# backend/api/trending.py

"""Trending service API endpoints.

Exposes:
- GET /trending?content_type=movie
- POST /trending/refresh (re-computes trending scores using decay formula)
"""

from __future__ import annotations

from fastapi import APIRouter, Query, BackgroundTasks
from ..core.database import get_pg_conn

router = APIRouter(prefix="/trending", tags=["trending"])

@router.get("")
async def get_trending_items(
    content_type: str | None = Query(None, description="Filter by content type"),
    limit: int = Query(20, description="Max trending items to return")
):
    """Retrieve top trending items from trending_items table."""
    try:
        async with get_pg_conn() as conn:
            if content_type:
                query = """
                    SELECT i.id, i.title, i.content_type, i.genres, i.release_year, 
                           i.poster_url, i.backdrop_url, i.rating, i.vote_count, 
                           i.imdb_id, t.trend_score
                    FROM trending_items t
                    JOIN items i ON t.item_id = i.id
                    WHERE i.content_type = $1 AND i.poster_url IS NOT NULL AND i.poster_url != ''
                    ORDER BY t.trend_score DESC
                    LIMIT $2
                """
                rows = await conn.fetch(query, content_type.lower(), limit)
            else:
                query = """
                    SELECT i.id, i.title, i.content_type, i.genres, i.release_year, 
                           i.poster_url, i.backdrop_url, i.rating, i.vote_count, 
                           i.imdb_id, t.trend_score
                    FROM trending_items t
                    JOIN items i ON t.item_id = i.id
                    WHERE i.poster_url IS NOT NULL AND i.poster_url != ''
                    ORDER BY t.trend_score DESC
                    LIMIT $1
                """
                rows = await conn.fetch(query, limit)
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"[WARN] Failed to fetch trending items: {e}")
        return []

async def refresh_trending_scores_task():
    """Background task to recalculate trending scores based on the decay formula,
    segmented by content type to prevent subset starvation (like cold books or anime).
    """
    try:
        print("[INFO] Starting trending scores recalculation (per-type)...")
        async with get_pg_conn() as conn:
            all_trending_insert_rows = []
            
            for c_type in ["movie", "tv", "anime", "book"]:
                # Fetch interactions for this content type in the last 24h
                compute_query = """
                    SELECT int.item_id,
                           SUM(
                             CASE int.event_type
                               WHEN 'watch_now' THEN 5.0
                               WHEN 'watch' THEN 3.0
                               WHEN 'rate' THEN 4.0
                               WHEN 'save' THEN 2.0
                               WHEN 'click' THEN 1.0
                               WHEN 'search' THEN 1.0
                               WHEN 'skip' THEN -1.0
                               ELSE 1.0
                             END * EXP(-0.1 * EXTRACT(EPOCH FROM (NOW() - int.created_at)) / 3600.0)
                           ) AS computed_score
                    FROM interactions int
                    JOIN items i ON int.item_id = i.id
                    WHERE i.content_type = $1 AND int.created_at >= NOW() - INTERVAL '24 hours'
                    GROUP BY int.item_id
                """
                rows = await conn.fetch(compute_query, c_type)
                
                type_scores = []
                for r in rows:
                    type_scores.append((r["item_id"], float(r["computed_score"])))
                
                # Fallback to popularity of this content type if interaction data is sparse
                if len(type_scores) < 40:
                    fallback_rows = await conn.fetch(
                        """SELECT id, popularity
                           FROM items
                           WHERE content_type = $1 AND poster_url IS NOT NULL AND poster_url != ''
                           ORDER BY popularity DESC, rating DESC
                           LIMIT 100""",
                        c_type
                    )
                    for r in fallback_rows:
                        pop_score = float(r["popularity"] or 0.0) * 0.1
                        type_scores.append((r["id"], pop_score))
                
                # Sort and take top 100 for this content type
                type_scores.sort(key=lambda x: x[1], reverse=True)
                top_type_scores = type_scores[:100]
                
                for item_id, score in top_type_scores:
                    all_trending_insert_rows.append((item_id, c_type, score, 24))
            
            # Re-populate the trending_items table inside a transaction
            async with conn.transaction():
                # Delete existing records
                await conn.execute("DELETE FROM trending_items")
                
                await conn.executemany(
                    """INSERT INTO trending_items (item_id, content_type, trend_score, window_hours)
                       VALUES ($1, $2, $3, $4)""",
                    all_trending_insert_rows
                )
                
        print(f"[INFO] Trending scores refreshed successfully. Total items across all types: {len(all_trending_insert_rows)}")
    except Exception as e:
        print(f"[ERROR] Failed to refresh trending scores: {e}")

@router.post("/refresh")
async def refresh_trending(background_tasks: BackgroundTasks):
    """Triggers recalculation of trending scores in a background task."""
    background_tasks.add_task(refresh_trending_scores_task)
    return {"status": "refresh triggered in background"}
