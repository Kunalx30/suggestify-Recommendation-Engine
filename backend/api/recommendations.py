# backend/api/recommendations.py

"""Recommendation endpoints for the FastAPI backend.

Implements:
- GET /recommendations
- GET /recommendations/rows
"""

from __future__ import annotations

import os
import time
import uuid
import random
import asyncio
import functools
import json
import httpx
import numpy as np
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Query, HTTPException, BackgroundTasks
from qdrant_client.models import Filter, FieldCondition, MatchValue

from ..core.config import settings
from ..core.database import get_pg_conn, get_qdrant, get_redis
from ..services.signals import load_user_features, UserFeatures, is_valid_uuid
from ..services.two_tower import get_user_embedding
from ..services.ranker import rerank
from ..api.admin import log_ab_event

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

TMDB_API_KEY = "be06ccdec5d44b100a46f6cd22df2ea3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
TMDB_BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280"

async def _fetch_tmdb_media(imdb_id: str, client: httpx.AsyncClient) -> Dict[str, str]:
    """Fetch poster_url, backdrop_url, trailer_url from TMDB for a given IMDb ID."""
    try:
        find = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY},
            timeout=4,
        )
        find.raise_for_status()
        data = find.json()
        movie_res = data.get("movie_results", [])
        tv_res = data.get("tv_results", [])
        item = movie_res[0] if movie_res else (tv_res[0] if tv_res else None)
        if not item:
            return {}
        media_type = "movie" if movie_res else "tv"
        tmdb_id = item["id"]

        result: Dict[str, str] = {}
        if item.get("poster_path"):
            result["poster_url"] = f"{TMDB_IMAGE_BASE}{item['poster_path']}"
        if item.get("backdrop_path"):
            result["backdrop_url"] = f"{TMDB_BACKDROP_BASE}{item['backdrop_path']}"

        # Fetch trailer
        vids = await client.get(
            f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/videos",
            params={"api_key": TMDB_API_KEY},
            timeout=4,
        )
        vids.raise_for_status()
        trailer = next(
            (v for v in vids.json().get("results", []) if v["type"] == "Trailer" and v["site"] == "YouTube"),
            None,
        )
        if trailer:
            result["trailer_url"] = f"https://www.youtube.com/watch?v={trailer['key']}"
        return result
    except Exception:
        return {}

async def enrich_missing_posters(items: List[Dict[str, Any]]) -> None:
    """Batch-enrich items with missing poster_url by fetching from TMDB in parallel.
    Mutates the dicts in-place and persists to Postgres."""
    to_enrich = [
        item for item in items
        if not item.get("poster_url") and item.get("imdb_id")
    ]
    if not to_enrich:
        return

    sem = asyncio.Semaphore(5)
    async def sem_fetch(imdb_id: str, cl: httpx.AsyncClient):
        async with sem:
            return await _fetch_tmdb_media(imdb_id, cl)

    async with httpx.AsyncClient() as client:
        tasks = [sem_fetch(item["imdb_id"], client) for item in to_enrich]
        results = await asyncio.gather(*tasks)

    # Persist enrichment back to Postgres
    updates: List[tuple] = []
    for item, media in zip(to_enrich, results):
        if not media:
            continue
        item["poster_url"] = media.get("poster_url", item.get("poster_url", ""))
        item["backdrop_url"] = media.get("backdrop_url", item.get("backdrop_url", ""))
        item["trailer_url"] = media.get("trailer_url", item.get("trailer_url", ""))
        updates.append((
            item["poster_url"],
            item["backdrop_url"],
            item["trailer_url"],
            item["id"],
        ))

    if updates:
        try:
            async with get_pg_conn() as conn:
                await conn.executemany(
                    """UPDATE items
                       SET poster_url = $1, backdrop_url = $2, trailer_url = $3
                       WHERE id = $4""",
                    updates
                )
        except Exception as e:
            print(f"[WARN] Failed to persist TMDB enrichment: {e}")


async def fetch_postgres_item_details(item_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Helper to fetch item metadata from PostgreSQL.

    Two-pass strategy:
    1. Look up by primary key (works for tmdb_* items).
    2. For unresolved imdb_* IDs, look up by imdb_id column (bridges the gap
       between Qdrant's imdb_ prefix and Postgres's tmdb_ primary keys).
    """
    if not item_ids:
        return {}

    SELECT = """SELECT id, title, content_type, genres, description, release_year,
                       language, country, poster_url, backdrop_url, trailer_url,
                       rating, vote_count, imdb_id
                FROM items"""
    result: Dict[str, Dict[str, Any]] = {}

    try:
        async with get_pg_conn() as conn:
            # Pass 1 — exact primary key match
            rows = await conn.fetch(f"{SELECT} WHERE id = ANY($1)", item_ids)
            for r in rows:
                result[r["id"]] = dict(r)

            # Pass 2 — for unresolved imdb_* IDs, try matching by imdb_id column
            unresolved = [
                iid for iid in item_ids
                if iid not in result and iid.startswith("imdb_")
            ]
            if unresolved:
                bare_ids = [iid.replace("imdb_", "", 1) for iid in unresolved]
                rows2 = await conn.fetch(f"{SELECT} WHERE imdb_id = ANY($1)", bare_ids)
                for r in rows2:
                    # Key the result by the original imdb_ prefixed ID so callers match correctly
                    original_key = f"imdb_{r['imdb_id']}"
                    result[original_key] = dict(r)

    except Exception as e:
        print(f"[WARN] Failed to fetch item details from PostgreSQL: {e}")

    return result

async def log_recommendations(
    user_id: str,
    row_name: str,
    item_ids: List[str],
    scores: List[float],
    strategy: str,
    latency_ms: int
) -> None:
    """Helper to write log records to recommendation_logs table."""
    if not is_valid_uuid(user_id) or not item_ids:
        return
    try:
        async with get_pg_conn() as conn:
            await conn.execute(
                """INSERT INTO recommendation_logs (user_id, row_name, item_ids, scores, strategy, latency_ms)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                uuid.UUID(user_id),
                row_name,
                item_ids,
                scores,
                strategy,
                latency_ms
            )
    except Exception as e:
        print(f"[WARN] Failed to write recommendation log to PostgreSQL: {e}")

async def get_ab_variant(user_id: str) -> tuple[str | None, int | None]:
    """Return (variant, experiment_id) for the running experiment, or (None, None)."""
    import uuid
    try:
        uuid.UUID(user_id)
    except ValueError:
        return None, None
    try:
        async with get_pg_conn() as conn:
            exp = await conn.fetchrow(
                "SELECT id, traffic_split FROM ab_experiments WHERE status='running' LIMIT 1"
            )
            if not exp:
                return None, None
            existing = await conn.fetchval(
                "SELECT variant FROM ab_assignments WHERE user_id=$1 AND experiment_id=$2",
                uuid.UUID(user_id), exp['id']
            )
            if existing:
                return existing, exp['id']
            # Assign fresh using the actual traffic_split from the DB
            variant = 'B' if random.random() < float(exp['traffic_split']) else 'A'
            await conn.execute("""
                INSERT INTO ab_assignments (user_id, experiment_id, variant)
                VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
            """, uuid.UUID(user_id), exp['id'], variant)
            return variant, exp['id']
    except Exception as e:
        print(f"[WARN] A/B variant lookup failed: {e}")
        return None, None


async def log_ab_event(user_id: str, experiment_id: int, variant: str, metric: str, value: float) -> None:
    """Write a metric event to ab_events for experiment analysis."""
    import uuid
    try:
        async with get_pg_conn() as conn:
            await conn.execute("""
                INSERT INTO ab_events (user_id, experiment_id, variant, metric, value)
                VALUES ($1, $2, $3, $4, $5)
            """, uuid.UUID(user_id), experiment_id, variant, metric, value)
    except Exception as e:
        print(f"[WARN] Failed to log ab_event: {e}")

@router.get("")
async def get_recommendations(
    user_id: str = Query(..., description="User ID"),
    content_type: str | None = None,
    limit: int = 20,
    background_tasks: BackgroundTasks = None,
):
    """The main recommendation pipeline endpoint. Returns personalized recommendations.
    
    A/B experiment: Variant A receives popularity-sorted candidates (control),
    Variant B receives Two-Tower neural ranking (treatment).
    """
    start_time = time.time()

    # ── A/B: Get (or assign) the user's variant for the running experiment ──
    ab_variant, ab_exp_id = await get_ab_variant(user_id)
    # Use Two-Tower for Variant B (treatment) or unassigned users;
    # use popularity baseline for Variant A (control).
    use_two_tower = (ab_variant != 'A')
    strategy_name = "two_tower" if use_two_tower else "popularity"
    print(f"[A/B] user={user_id} variant={ab_variant} strategy={strategy_name}")

    # 1. Load UserFeatures from Redis (with Postgres fallback)
    features: UserFeatures = await load_user_features(user_id)

    # ── VARIANT A — Popularity baseline ──────────────────────────────────────
    if not use_two_tower:
        try:
            async with get_pg_conn() as conn:
                ct_filter = "AND content_type = $1" if content_type else ""
                params = [content_type] if content_type else []
                rows = await conn.fetch(
                    f"""SELECT id, title, content_type, genres, release_year,
                               poster_url, backdrop_url, rating, vote_count, imdb_id
                        FROM items
                        WHERE poster_url IS NOT NULL AND poster_url != ''
                        {ct_filter}
                        ORDER BY vote_count DESC, rating DESC
                        LIMIT ${ len(params)+1 }""",
                    *params, limit
                )
            final_list = [dict(r) for r in rows]
            # Assign simple match scores so UI renders correctly
            for i, item in enumerate(final_list):
                item["match_score"] = round(95.0 - i * (55.0 / max(len(final_list) - 1, 1)), 1)
        except Exception as e:
            print(f"[WARN] Popularity fallback failed: {e}")
            final_list = []

        latency = int((time.time() - start_time) * 1000)
        await log_recommendations(
            user_id=user_id, row_name="Personalized For You",
            item_ids=[i["id"] for i in final_list],
            scores=[i["match_score"] for i in final_list],
            strategy=strategy_name, latency_ms=latency
        )
        # Log A/B impression metric
        if ab_exp_id:
            asyncio.create_task(log_ab_event(user_id, ab_exp_id, ab_variant, "impression", 1.0))

        return {"user_id": user_id, "recommendations": final_list, "ab_variant": ab_variant}

    # ── VARIANT B — Two-Tower neural ranking ─────────────────────────────────
    # 2. Compute user embedding on the fly using Two-Tower
    try:
        user_emb = get_user_embedding(features)
    except Exception as e:
        print(f"[WARN] Error computing user embedding: {e}. Defaulting to random vector.")
        user_emb = np.random.normal(0, 0.1, settings.EMBEDDING_DIM)
        norm = np.linalg.norm(user_emb)
        if norm > 0:
            user_emb = user_emb / norm

    # 3. Query Qdrant for top candidates
    qdrant = get_qdrant()
    collection_name = os.getenv("QDRANT_COLLECTION", settings.COLLECTION_NAME)
    
    try:
        query_filter = None
        if content_type:
            query_filter = Filter(
                must=[FieldCondition(key="content_type", match=MatchValue(value=content_type))]
            )
        loop = asyncio.get_event_loop()
        search_result = await loop.run_in_executor(
            None,
            functools.partial(
                qdrant.search,
                collection_name=collection_name,
                query_vector=user_emb.tolist(),
                query_filter=query_filter,
                limit=500,
                with_vectors=True,
                with_payload=True,
            )
        )
        print(f"[DEBUG] Qdrant search found {len(search_result)} hits")
    except Exception as e:
        print(f"[WARN] Qdrant search failed: {e}. Falling back to random Postgres retrieval.")
        search_result = []

    # Parse and initial-filter candidates
    candidates = []
    for hit in search_result:
        p = hit.payload
        if not p:
            continue
        item_id = p.get("item_id")
        if not item_id:
            continue
            
        # Filter by content_type if specified
        if content_type and p.get("content_type") != content_type:
            continue
            
        # Filter out already watched items
        if item_id in features.watched_ids:
            continue
            
        candidates.append({
            "item_id": item_id,
            "qdrant_score": hit.score,
            "rating": p.get("rating") or 0.0,
            "genres": p.get("genres") or [],
            "release_year": p.get("release_year"),
            "vote_count": p.get("vote_count") or 0,
            "embedding": hit.vector,
            "title": p.get("title") or "",
            "content_type": p.get("content_type"),
            "poster_url": p.get("poster_url") or "",
        })
    print(f"[DEBUG] Candidates count: {len(candidates)}")

    # 4. Re-rank remaining candidates
    # Retrieve a larger set of ranked items so we can prioritize posters and slice to the requested limit
    rank_limit = max(limit, 40) if content_type == "movie" else limit
    ranked = rerank(candidates, features, limit=rank_limit)
    print(f"[DEBUG] Ranked count: {len(ranked)}")

    # If filtering by movie, prioritize items with poster_url
    if content_type == "movie":
        ranked.sort(key=lambda x: (0 if x.get('poster_url') else 1, -x.get('rerank_score', 0)))
        ranked = ranked[:limit]

    # 5. Apply bandit: replace epsilon fraction with random exploration items from other retrieved candidates
    explore_slots = int(limit * settings.BANDIT_EPSILON)
    if explore_slots > 0 and len(ranked) > explore_slots:
        # Get candidates that weren't selected in top ranked slots
        ranked_ids = {r["item_id"] for r in ranked}
        explore_pool = [c for c in candidates if c["item_id"] not in ranked_ids]
        if explore_pool:
            explore_items = random.sample(explore_pool, min(explore_slots, len(explore_pool)))
            # Replace the tail of ranked recommendations
            ranked = ranked[:-explore_slots] + explore_items
    print(f"[DEBUG] Ranked after bandit count: {len(ranked)}")

    # 6. Fetch full details from PostgreSQL for the selected items
    item_ids = [r["item_id"] for r in ranked]
    db_items = await fetch_postgres_item_details(item_ids)
    print(f"[DEBUG] Postgres items count: {len(db_items)}")

    # Reconstruct final response keeping order and computing match_score
    final_list = []
    for rank_item in ranked:
        iid = rank_item["item_id"]
        details = db_items.get(iid)
        if not details:
            # Fallback to payload metadata if Postgres row is missing (ID mismatch)
            details = {
                "id": iid,
                "title": rank_item.get("title") or "",
                "content_type": rank_item.get("content_type") or "movie",
                "genres": rank_item.get("genres") or [],
                "release_year": rank_item.get("release_year"),
                "rating": rank_item.get("rating") or 0.0,
                "vote_count": rank_item.get("vote_count") or 0,
                "poster_url": rank_item.get("poster_url") or "",
                "backdrop_url": rank_item.get("backdrop_url") or "",
                "trailer_url": rank_item.get("trailer_url") or "",
                "imdb_id": iid.replace("imdb_", "") if iid.startswith("imdb_") else "",
            }
        else:
            # Patch empty media URLs from Qdrant payload (handles imdb_/tmdb_ ID mismatch)
            if not details.get("poster_url") and rank_item.get("poster_url"):
                details["poster_url"] = rank_item["poster_url"]
            if not details.get("backdrop_url") and rank_item.get("backdrop_url"):
                details["backdrop_url"] = rank_item["backdrop_url"]
            if not details.get("trailer_url") and rank_item.get("trailer_url"):
                details["trailer_url"] = rank_item["trailer_url"]

        # Store raw score for value-based scaling
        details["rerank_score"] = rank_item.get("rerank_score", 0.0)
        if "dlrm_score" in rank_item:
            details["dlrm_score"] = rank_item["dlrm_score"]
        if "heuristic_score" in rank_item:
            details["heuristic_score"] = rank_item["heuristic_score"]
        final_list.append(details)

    # Sort final recommendations by raw score descending to guarantee monotonic order
    final_list.sort(key=lambda x: x.get("rerank_score", 0.0), reverse=True)

    # ── Scale match scores to 40-95% range based on actual score values ──────
    raw_scores = [x.get("rerank_score", 0.0) for x in final_list]
    s_min = min(raw_scores) if raw_scores else 0.0
    s_max = max(raw_scores) if raw_scores else 1.0
    score_range = s_max - s_min if s_max != s_min else 1.0

    for details in final_list:
        raw = details.get("rerank_score", 0.0)
        normalised = (raw - s_min) / score_range if score_range > 0 else 1.0
        match_percentage = round(40.0 + normalised * 55.0, 1)
        details["match_score"] = match_percentage

    # Enrich missing poster/backdrop/trailer from TMDB in the background
    if background_tasks:
        background_tasks.add_task(enrich_missing_posters, final_list)
    else:
        asyncio.create_task(enrich_missing_posters(final_list))

    # 7. Log to recommendation_logs table asynchronously
    latency = int((time.time() - start_time) * 1000)
    await log_recommendations(
        user_id=user_id,
        row_name="Personalized For You",
        item_ids=[item["id"] for item in final_list],
        scores=[item["match_score"] for item in final_list],
        strategy=strategy_name,
        latency_ms=latency
    )

    # Log A/B impression metric
    if ab_exp_id:
        asyncio.create_task(log_ab_event(user_id, ab_exp_id, ab_variant, "impression", 1.0))

    return {"user_id": user_id, "recommendations": final_list, "ab_variant": ab_variant}

@router.get("/rows")
async def get_recommendation_rows(
    user_id: str = Query(...),
    content_type: str | None = None,
    cache_bust: bool = Query(False),  # BUG-08 fix: allow caller to skip Redis cache
    background_tasks: BackgroundTasks = None,
):
    """Returns multiple named carousels of recommended/trending/saved content.
    
    When content_type is supplied, the personalized row and extra genre rows
    are filtered to that content type only.
    Set cache_bust=true to bypass the 60s Redis cache (used after user interactions).
    """
    # ── Redis cache check (60-second TTL) ────────────────────────────────────
    redis = get_redis()
    cache_key = f"rows:{user_id}:{content_type or 'all'}"
    if not cache_bust:  # BUG-08: skip cache read when busting
        try:
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass  # Cache miss or Redis error — proceed normally

    # Run personalized recs + trending + top rated all at once in parallel
    recs_task = get_recommendations(user_id=user_id, content_type=content_type, limit=40, background_tasks=background_tasks)
    
    async def fetch_trending():
        try:
            async with get_pg_conn() as conn:
                # BUG-10 fix: use parameterized query instead of f-string injection
                if content_type:
                    rows = await conn.fetch(
                        """SELECT i.id, i.title, i.content_type, i.genres, i.release_year,
                                  i.poster_url, i.backdrop_url, i.rating, i.vote_count, i.imdb_id,
                                  t.trend_score
                           FROM trending_items t
                           JOIN items i ON t.item_id = i.id
                           WHERE i.poster_url IS NOT NULL AND i.poster_url != ''
                           AND i.content_type = $1
                           ORDER BY t.trend_score DESC
                           LIMIT 40""",
                        content_type
                    )
                else:
                    rows = await conn.fetch(
                        """SELECT i.id, i.title, i.content_type, i.genres, i.release_year,
                                  i.poster_url, i.backdrop_url, i.rating, i.vote_count, i.imdb_id,
                                  t.trend_score
                           FROM trending_items t
                           JOIN items i ON t.item_id = i.id
                           WHERE i.poster_url IS NOT NULL AND i.poster_url != ''
                           ORDER BY t.trend_score DESC
                           LIMIT 40"""
                    )
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"[WARN] Error fetching trending rows: {e}")
            return []

    async def fetch_top_rated():
        try:
            async with get_pg_conn() as conn:
                if content_type:
                    rows = await conn.fetch(
                        """SELECT id, title, content_type, genres, release_year, poster_url,
                                  backdrop_url, rating, vote_count, imdb_id
                           FROM items
                           WHERE content_type = $1
                           AND poster_url IS NOT NULL AND poster_url != ''
                           ORDER BY rating DESC, vote_count DESC
                           LIMIT 40""",
                        content_type
                    )
                else:
                    rows = await conn.fetch(
                        """SELECT id, title, content_type, genres, release_year, poster_url,
                                  backdrop_url, rating, vote_count, imdb_id
                           FROM items
                           WHERE content_type IN ('book', 'anime')
                           AND poster_url IS NOT NULL AND poster_url != ''
                           ORDER BY rating DESC, vote_count DESC
                           LIMIT 40"""
                    )
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"[WARN] Error fetching top rated: {e}")
            return []

    recs_result, trending, top_rated = await asyncio.gather(
        recs_task, fetch_trending(), fetch_top_rated()
    )
    personalized = recs_result.get("recommendations", []) if isinstance(recs_result, dict) else []

    # 3. Because You Liked X — based on most recent click
    because_liked = []
    redis = get_redis()
    most_recent_click = await redis.lindex(f"user:{user_id}:clicks", 0)
    if most_recent_click:
        qdrant = get_qdrant()
        collection_name = os.getenv("QDRANT_COLLECTION", settings.COLLECTION_NAME)
        try:
            loop = asyncio.get_event_loop()
            scroll_res = await loop.run_in_executor(
                None,
                functools.partial(
                    qdrant.scroll,
                    collection_name=collection_name,
                    scroll_filter=Filter(
                        must=[FieldCondition(key="item_id", match=MatchValue(value=most_recent_click))]
                    ),
                    limit=1,
                    with_vectors=True,
                )
            )
            if scroll_res and scroll_res[0]:
                vector = scroll_res[0][0].vector
                sim_filter = None
                if content_type:
                    sim_filter = Filter(
                        must=[FieldCondition(key="content_type", match=MatchValue(value=content_type))]
                    )
                sim_res = await loop.run_in_executor(
                    None,
                    functools.partial(
                        qdrant.search,
                        collection_name=collection_name,
                        query_vector=vector,
                        query_filter=sim_filter,
                        limit=41,
                        with_payload=True,
                    )
                )
                similar_ids = [
                    hit.payload["item_id"] for hit in sim_res
                    if hit.payload and hit.payload.get("item_id") != most_recent_click
                ][:40]
                db_details = await fetch_postgres_item_details(similar_ids)
                for sid in similar_ids:
                    if sid in db_details:
                        because_liked.append(db_details[sid])
                if background_tasks:
                    background_tasks.add_task(enrich_missing_posters, because_liked)
                else:
                    asyncio.create_task(enrich_missing_posters(because_liked))
        except Exception as e:
            print(f"[WARN] Error in Because You Liked: {e}")

    # 4. Watch Later
    watch_later = []
    watch_later_ids = await redis.lrange(f"user:{user_id}:watch_later", 0, 39)
    if watch_later_ids:
        wl_details = await fetch_postgres_item_details(watch_later_ids)
        for wlid in watch_later_ids:
            if wlid in wl_details:
                watch_later.append(wl_details[wlid])
        if background_tasks:
            background_tasks.add_task(enrich_missing_posters, watch_later)
        else:
            asyncio.create_task(enrich_missing_posters(watch_later))

    response: dict = {
        "Trending Now": trending,
        "Personalized For You": personalized,
        "Because You Liked X": because_liked,
        "Watch Later": watch_later,
        "Top Rated": top_rated,
    }

    # ── Movies-tab extra rows ─────────────────────────────────────────────────
    if content_type == "movie":
        try:
            async with get_pg_conn() as conn:
                new_releases_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'movie'
                       AND release_year >= 2024
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC
                       LIMIT 40"""
                )
                response["New Releases"] = [dict(r) for r in new_releases_rows]

                action_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'movie'
                       AND genres && ARRAY['Action', 'Adventure']
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["Action & Adventure"] = [dict(r) for r in action_rows]

                drama_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'movie'
                       AND genres && ARRAY['Drama', 'Romance']
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["Drama & Romance"] = [dict(r) for r in drama_rows]

                comedy_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'movie'
                       AND genres && ARRAY['Comedy', 'Family']
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["Comedy & Family"] = [dict(r) for r in comedy_rows]
        except Exception as e:
            print(f"[WARN] Error fetching movie extra rows: {e}")

    # ── TV Shows-tab extra rows ───────────────────────────────────────────────
    elif content_type == "tv":
        try:
            async with get_pg_conn() as conn:
                drama_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'tv'
                       AND genres && ARRAY['Drama', 'Crime', 'Thriller']
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["Drama & Crime Series"] = [dict(r) for r in drama_rows]

                scifi_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'tv'
                       AND genres && ARRAY['Science Fiction', 'Fantasy', 'Sci-Fi']
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["Sci-Fi & Fantasy"] = [dict(r) for r in scifi_rows]

                comedy_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'tv'
                       AND genres && ARRAY['Comedy', 'Animation', 'Family']
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["Comedy & Animation"] = [dict(r) for r in comedy_rows]

                new_series_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'tv'
                       AND release_year >= 2022
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["New Series"] = [dict(r) for r in new_series_rows]
        except Exception as e:
            print(f"[WARN] Error fetching TV extra rows: {e}")

    # ── Anime-tab extra rows ──────────────────────────────────────────────────
    elif content_type == "anime":
        try:
            async with get_pg_conn() as conn:
                action_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'anime'
                       AND genres && ARRAY['Action', 'Adventure', 'Shounen']
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["Action & Shounen"] = [dict(r) for r in action_rows]

                romance_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'anime'
                       AND genres && ARRAY['Romance', 'Slice of Life', 'Drama']
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["Romance & Slice of Life"] = [dict(r) for r in romance_rows]

                fantasy_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'anime'
                       AND genres && ARRAY['Fantasy', 'Isekai', 'Supernatural', 'Mecha']
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["Fantasy & Isekai"] = [dict(r) for r in fantasy_rows]

                psychological_rows = await conn.fetch(
                    """SELECT id, title, content_type, genres, release_year, poster_url,
                              backdrop_url, rating, vote_count, imdb_id
                       FROM items
                       WHERE content_type = 'anime'
                       AND genres && ARRAY['Psychological', 'Mystery', 'Thriller', 'Seinen']
                       AND poster_url IS NOT NULL AND poster_url != ''
                       ORDER BY rating DESC, vote_count DESC
                       LIMIT 40"""
                )
                response["Psychological & Mystery"] = [dict(r) for r in psychological_rows]
        except Exception as e:
            print(f"[WARN] Error fetching anime extra rows: {e}")

    # ── Save to Redis cache (60s TTL) ────────────────────────────────────────
    try:
        await redis.set(cache_key, json.dumps(response, default=str), ex=60)
    except Exception:
        pass  # Non-fatal if cache write fails

    return response
