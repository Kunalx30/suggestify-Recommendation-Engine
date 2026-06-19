# backend/api/search.py

"""Search API endpoint.

Provides a two-stage search:
1. Text filtering in Qdrant payload.
2. Fallback to PostgreSQL ILIKE if Qdrant returns < 5 results.
"""

from __future__ import annotations

import os
from typing import List, Dict, Any
from fastapi import APIRouter, Query
from qdrant_client.models import Filter, FieldCondition, MatchText, MatchValue

from ..core.config import settings
from ..core.database import get_qdrant, get_pg_conn

router = APIRouter(prefix="/search", tags=["search"])

def _calculate_text_match_score(query: str, title: str) -> float:
    """Calculates text relevance score between query and title, mapped to [40, 95]%."""
    q_words = set(query.lower().split())
    t_words = set(title.lower().split())
    if not q_words or not t_words:
        return 40.0
    overlap = len(q_words & t_words)
    union = len(q_words | t_words)
    ratio = overlap / union
    # Map ratio [0, 1] to [40, 95] range
    return round(40.0 + ratio * 55.0, 1)

@router.get("")
async def search_items(
    q: str = Query(...),
    content_type: str | None = Query(None),
    limit: int = Query(50)
):
    results = []
    
    # PRIMARY: PostgreSQL with pg_trgm index (fast, already indexed in init_db.sql)
    try:
        if content_type:
            sql = """
                SELECT id, title, content_type, genres, release_year,
                       poster_url, rating, vote_count, popularity, imdb_id
                FROM items
                WHERE title ILIKE $1 AND content_type = $2
                ORDER BY popularity DESC, rating DESC
                LIMIT $3
            """
            args = (f"%{q}%", content_type.lower(), limit)
        else:
            sql = """
                SELECT id, title, content_type, genres, release_year,
                       poster_url, rating, vote_count, popularity, imdb_id
                FROM items
                WHERE title ILIKE $1
                ORDER BY popularity DESC, rating DESC
                LIMIT $2
            """
            args = (f"%{q}%", limit)
        
        async with get_pg_conn() as conn:
            rows = await conn.fetch(sql, *args)
        
        for row in rows:
            title = row["title"]
            title_lower = title.lower()
            q_lower = q.lower()
            
            if title_lower == q_lower:
                score = 95.0  # exact match
            elif title_lower.startswith(q_lower):
                score = 85.0  # starts with query
            elif q_lower in title_lower:
                score = 75.0  # contains query
            else:
                score = 50.0  # fuzzy match
            
            # Boost by popularity
            pop_boost = min(float(row["popularity"] or 0) / 1000.0, 10.0)
            score = round(min(score + pop_boost, 95.0), 1)
            
            results.append({
                "id": row["id"],
                "title": title,
                "content_type": row["content_type"],
                "genres": list(row["genres"] or []),
                "release_year": row["release_year"],
                "poster_url": row["poster_url"] or "",
                "rating": row["rating"] or 0.0,
                "vote_count": row["vote_count"] or 0,
                "imdb_id": row["imdb_id"] or "",
                "match_score": score,
            })
    except Exception as e:
        print(f"[WARN] PostgreSQL search failed: {e}")
    
    # FALLBACK: Qdrant search if PostgreSQL failed or returned no results
    if not results:
        try:
            collection_name = os.getenv("QDRANT_COLLECTION", settings.COLLECTION_NAME)
            qdrant = get_qdrant()
            must_conditions = [
                FieldCondition(key="title", match=MatchText(text=q))
            ]
            if content_type:
                must_conditions.append(
                    FieldCondition(key="content_type", match=MatchValue(value=content_type.lower()))
                )

            scroll_res, _ = qdrant.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=must_conditions),
                limit=limit,
                with_payload=True
            )
            
            for point in scroll_res:
                p = point.payload
                if not p:
                    continue
                item_id = p.get("item_id")
                if not item_id:
                    continue
                
                title = p.get("title") or ""
                title_lower = title.lower()
                q_lower = q.lower()
                
                if title_lower == q_lower:
                    score = 95.0  # exact match
                elif title_lower.startswith(q_lower):
                    score = 85.0  # starts with query
                elif q_lower in title_lower:
                    score = 75.0  # contains query
                else:
                    score = 50.0  # fuzzy match
                
                # Boost by popularity
                popularity = p.get("popularity") or 0.0
                pop_boost = min(float(popularity) / 1000.0, 10.0)
                score = round(min(score + pop_boost, 95.0), 1)

                results.append({
                    "id": item_id,
                    "title": title,
                    "content_type": p.get("content_type") or "movie",
                    "genres": list(p.get("genres") or []),
                    "release_year": p.get("release_year"),
                    "poster_url": p.get("poster_url") or "",
                    "rating": p.get("rating") or 0.0,
                    "vote_count": p.get("vote_count") or 0,
                    "imdb_id": p.get("imdb_id") or "",
                    "match_score": score
                })
        except Exception as e:
            print(f"[WARN] Qdrant fallback search failed: {e}")

    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results[:limit]
