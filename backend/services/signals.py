# backend/services/signals.py

"""User feature aggregation service.

Aggregates user interaction signals from Redis and fallbacks to PostgreSQL for cold start.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Set, List, Dict, Any
import redis.asyncio as aioredis

from ..core.database import get_redis, get_pg_conn

# List of all genres used in Two-Tower model
ALL_GENRES = [
    "Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary",
    "Drama", "Fantasy", "Horror", "Music", "Mystery", "Romance",
    "Science Fiction", "Thriller", "War", "Western", "Family", "History",
    "Biography", "Sport", "Sci-Fi", "Award Winning", "Suspense", "Supernatural",
    "Slice of Life", "Mecha", "Psychological", "Shounen", "Seinen", "Isekai",
    "Fiction", "Nonfiction", "Self-Help", "Literary Fiction", "Classic",
    "Graphic Novel", "Young Adult", "Children", "Poetry", "Short Stories",
    "Reality", "Talk", "News", "Game Show", "Variety", "Superhero",
    "Anime", "K-Drama", "Period", "Political"
]

@dataclass
class UserFeatures:
    user_id: str
    watched_ids: Set[str] = field(default_factory=set)
    click_history: List[str] = field(default_factory=list)
    watch_later: List[str] = field(default_factory=list)
    genre_boost: Dict[str, float] = field(default_factory=dict)
    preferred_genres: List[str] = field(default_factory=list)
    content_type: str | None = None
    avg_rating: float = 0.0
    session_ctr: float = 0.0
    interaction_count: int = 0
    last_active: int = 0
    country: str | None = None
    language: str | None = None

def is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False

async def load_user_features(user_id: str) -> UserFeatures:
    """Load all feature keys for a user from Redis.

    Uses pipeline to fetch efficiently. If missing, attempts to query Postgres
    to load user profile (cold start).
    """
    redis = get_redis()
    
    # Run pipeline to get all keys at once
    async with redis.pipeline() as pipe:
        pipe.smembers(f"user:{user_id}:watched")
        pipe.lrange(f"user:{user_id}:clicks", 0, -1)
        pipe.lrange(f"user:{user_id}:watch_later", 0, -1)
        pipe.hgetall(f"user:{user_id}:genre_boost")
        pipe.lrange(f"user:{user_id}:genres", 0, -1)
        pipe.get(f"user:{user_id}:content_type")
        pipe.get(f"user:{user_id}:avg_rating")
        pipe.get(f"user:{user_id}:session_ctr")
        pipe.get(f"user:{user_id}:interaction_count")
        pipe.get(f"user:{user_id}:last_active")
        pipe.get(f"user:{user_id}:country")
        pipe.get(f"user:{user_id}:language")
        res = await pipe.execute()

    watched_ids = set(res[0]) if res[0] else set()
    click_history = list(res[1]) if res[1] else []
    watch_later = list(res[2]) if res[2] else []
    
    # Convert genre boost hash map values to float
    genre_boost = {}
    if res[3]:
        for k, v in res[3].items():
            try:
                genre_boost[k] = float(v)
            except (ValueError, TypeError):
                genre_boost[k] = 0.0

    preferred_genres = list(res[4]) if res[4] else []
    content_type = res[5] if res[5] else None
    
    def _to_float(v, default=0.0):
        try:
            return float(v) if v is not None else default
        except (ValueError, TypeError):
            return default

    def _to_int(v, default=0):
        try:
            return int(v) if v is not None else default
        except (ValueError, TypeError):
            return default

    avg_rating = _to_float(res[6])
    session_ctr = _to_float(res[7])
    interaction_count = _to_int(res[8])
    last_active = _to_int(res[9])
    country = res[10] if res[10] else None
    language = res[11] if res[11] else None

    # Check for Cold Start fallback to PostgreSQL
    is_cold = not watched_ids and not click_history and not preferred_genres and not content_type
    if is_cold and is_valid_uuid(user_id):
        try:
            async with get_pg_conn() as conn:
                row = await conn.fetchrow(
                    "SELECT country, language, preferred_genres, preferred_content_types FROM users WHERE id = $1",
                    uuid.UUID(user_id)
                )
                if row:
                    country = row["country"] or "IN"
                    language = row["language"] or "en"
                    preferred_genres = row["preferred_genres"] or []
                    pref_types = row["preferred_content_types"] or []
                    content_type = pref_types[0] if pref_types else "movie"

                    # Save populated cold start features back to Redis with 24h TTL
                    async with redis.pipeline() as write_pipe:
                        if preferred_genres:
                            write_pipe.lpush(f"user:{user_id}:genres", *preferred_genres)
                            write_pipe.expire(f"user:{user_id}:genres", 86400)
                        write_pipe.set(f"user:{user_id}:content_type", content_type, ex=86400)
                        write_pipe.set(f"user:{user_id}:country", country, ex=86400)
                        write_pipe.set(f"user:{user_id}:language", language, ex=86400)
                        write_pipe.set(f"user:{user_id}:last_active", int(time_stamp_now()), ex=86400)
                        await write_pipe.execute()
        except Exception as e:
            print(f"[WARN] Failed to load cold start user features from PostgreSQL: {e}")

    return UserFeatures(
        user_id=user_id,
        watched_ids=watched_ids,
        click_history=click_history,
        watch_later=watch_later,
        genre_boost=genre_boost,
        preferred_genres=preferred_genres,
        content_type=content_type,
        avg_rating=avg_rating,
        session_ctr=session_ctr,
        interaction_count=interaction_count,
        last_active=last_active,
        country=country,
        language=language,
    )

def time_stamp_now() -> int:
    import time
    return int(time.time())

async def update_signals(
    user_id: str,
    event_type: str,
    item_id: str | None = None,
    extra: Dict[str, Any] | None = None,
) -> None:
    """Update user feature keys in Redis after each event with correct TTLs."""
    redis = get_redis()
    TTL = 86400  # 24 hours
    now = time_stamp_now()

    async with redis.pipeline() as pipe:
        if event_type == "click" and item_id:
            pipe.lpush(f"user:{user_id}:clicks", item_id)
            pipe.ltrim(f"user:{user_id}:clicks", 0, 99)  # Keep last 100 clicks
            pipe.expire(f"user:{user_id}:clicks", TTL)
            pipe.incr(f"user:{user_id}:interaction_count")
            pipe.expire(f"user:{user_id}:interaction_count", TTL)
            pipe.set(f"user:{user_id}:last_active", now, ex=TTL)
            pipe.incrbyfloat(f"user:{user_id}:session_ctr", 0.05)
            pipe.expire(f"user:{user_id}:session_ctr", TTL)

        elif event_type == "watch" and item_id:
            pipe.sadd(f"user:{user_id}:watched", item_id)
            pipe.expire(f"user:{user_id}:watched", TTL)
            if extra and "progress" in extra:
                progress = float(extra["progress"])
                pipe.hset(f"user:{user_id}:watch_progress", item_id, str(progress))
                pipe.expire(f"user:{user_id}:watch_progress", TTL)
            pipe.incr(f"user:{user_id}:interaction_count")
            pipe.expire(f"user:{user_id}:interaction_count", TTL)
            pipe.set(f"user:{user_id}:last_active", now, ex=TTL)
            # If user has rated it already, update avg_rating
            rating = extra.get("rating") if extra else None
            if rating is not None:
                pipe.hset(f"user:{user_id}:ratings", item_id, str(rating))
                pipe.expire(f"user:{user_id}:ratings", TTL)

        elif event_type == "watch_now" and item_id:
            pipe.sadd(f"user:{user_id}:watched", item_id)
            pipe.expire(f"user:{user_id}:watched", TTL)
            pipe.set(f"user:{user_id}:last_active", now, ex=TTL)

        elif event_type == "watch_later" and item_id:
            pipe.lrem(f"user:{user_id}:watch_later", 0, item_id)
            pipe.lpush(f"user:{user_id}:watch_later", item_id)
            pipe.ltrim(f"user:{user_id}:watch_later", 0, 99)
            pipe.expire(f"user:{user_id}:watch_later", TTL)

        elif event_type == "rate" and item_id and extra and "rating" in extra:
            rating = float(extra["rating"])
            pipe.hset(f"user:{user_id}:ratings", item_id, str(rating))
            pipe.expire(f"user:{user_id}:ratings", TTL)
            
            # Recalculate average rating
            # We can't do this synchronously inside pipeline since we need ratings hash,
            # so we'll run a quick get outside the pipeline, or handle it locally.
            # Let's fetch all ratings inside a separate block below, or fetch genres.

        elif event_type == "search" and extra and "query" in extra:
            query = extra["query"]
            pipe.lpush(f"user:{user_id}:search_history", query)
            pipe.ltrim(f"user:{user_id}:search_history", 0, 49)
            pipe.expire(f"user:{user_id}:search_history", TTL)
            
            # Extract genre keywords matching query
            query_lower = query.lower()
            for genre in ALL_GENRES:
                if genre.lower() in query_lower:
                    pipe.hincrbyfloat(f"user:{user_id}:genre_boost", genre, 1.0)
            pipe.expire(f"user:{user_id}:genre_boost", TTL)

        elif event_type == "skip" and item_id:
            pipe.incr(f"user:{user_id}:interaction_count")
            pipe.expire(f"user:{user_id}:interaction_count", TTL)
            pipe.incrbyfloat(f"user:{user_id}:session_ctr", -0.05)
            pipe.expire(f"user:{user_id}:session_ctr", TTL)

        await pipe.execute()

    # Click genre boost updating
    if event_type == "click" and item_id:
        try:
            genres = None
            async with get_pg_conn() as conn:
                genres = await conn.fetchval("SELECT genres FROM items WHERE id = $1", item_id)
            if genres:
                async with redis.pipeline() as pipe_genres:
                    for genre in genres:
                        # Give a small positive boost of +0.2 for clicks
                        pipe_genres.hincrbyfloat(f"user:{user_id}:genre_boost", genre, 0.2)
                    pipe_genres.expire(f"user:{user_id}:genre_boost", TTL)
                    await pipe_genres.execute()
        except Exception as e:
            print(f"[WARN] Failed to fetch/update genres for click boost: {e}")

    # Rate recalculation or genre fetching
    if event_type == "rate" and item_id and extra and "rating" in extra:
        rating = float(extra["rating"])
        # Recalculate avg rating
        ratings_dict = await redis.hgetall(f"user:{user_id}:ratings")
        if ratings_dict:
            try:
                avg = sum(float(v) for v in ratings_dict.values()) / len(ratings_dict)
                await redis.set(f"user:{user_id}:avg_rating", str(avg), ex=TTL)
            except Exception:
                pass
        
        # Fetch genres of item to update user genre boosts
        genres = extra.get("genres")
        if not genres:
            try:
                async with get_pg_conn() as conn:
                    genres = await conn.fetchval("SELECT genres FROM items WHERE id = $1", item_id)
            except Exception as e:
                print(f"[WARN] Failed to fetch genres for rating update: {e}")
        
        if genres:
            async with redis.pipeline() as pipe_genres:
                for genre in genres:
                    # Boost: if rated 4-5, positive boost. If 1-2, negative.
                    # Or just add (rating - 3)
                    boost = rating - 3.0
                    pipe_genres.hincrbyfloat(f"user:{user_id}:genre_boost", genre, boost)
                pipe_genres.expire(f"user:{user_id}:genre_boost", TTL)
                await pipe_genres.execute()
