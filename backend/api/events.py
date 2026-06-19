from __future__ import annotations

import os
import asyncio
import json
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from aiokafka import AIOKafkaProducer

from ..core.database import get_pg_conn
from ..services.signals import update_signals
from ..api.admin import log_ab_event

router = APIRouter(prefix="/events", tags=["events"])

class ClickEvent(BaseModel):
    user_id: str
    item_id: str
    session_id: str | None = None

class WatchEvent(BaseModel):
    user_id: str
    item_id: str
    progress: float | None = None

class SimpleEvent(BaseModel):
    user_id: str
    item_id: str

class RateEvent(BaseModel):
    user_id: str
    item_id: str
    rating: float  # 1-5 scale
    genres: list[str] | None = None

class SearchEvent(BaseModel):
    user_id: str
    query: str
    extracted_genres: list[str] | None = None

# Singleton Kafka Producer
_producer: AIOKafkaProducer | None = None
_producer_lock = asyncio.Lock()

async def get_kafka_producer() -> AIOKafkaProducer | None:
    global _producer
    if _producer is not None:
        return _producer
    
    async with _producer_lock:
        if _producer is not None:
            return _producer
            
        bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        try:
            producer = AIOKafkaProducer(
                bootstrap_servers=bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                request_timeout_ms=1000,
                api_version="auto"
            )
            await producer.start()
            _producer = producer
            print("[INFO] Kafka producer started successfully.")
        except Exception as e:
            print(f"[WARN] Failed to start Kafka producer: {e}. Will run in degraded mode.")
            _producer = None
    return _producer

async def _publish_kafka_task(event_type: str, payload: dict) -> None:
    producer = await get_kafka_producer()
    if producer is None:
        return
    topic = os.getenv("KAFKA_TOPIC_EVENTS", "user_events")
    try:
        await producer.send_and_wait(topic, {"event_type": event_type, "payload": payload})
    except Exception as e:
        print(f"[WARN] Failed to publish event to Kafka: {e}")

def _publish_kafka(event_type: str, payload: dict) -> None:
    # Fire and forget in the background to keep response time < 5ms
    asyncio.create_task(_publish_kafka_task(event_type, payload))

async def log_interaction_to_db(
    user_id: str,
    item_id: str | None,
    event_type: str,
    rating: float | None = None,
    watch_progress: float | None = None,
):
    """Write interaction to PostgreSQL for analytics. Non-blocking."""
    import uuid
    try:
        # Only log if user_id looks like a UUID (real users, not sim_user_*)
        uuid.UUID(user_id)
    except ValueError:
        return  # skip synthetic sim users
    
    try:
        async with get_pg_conn() as conn:
            await conn.execute("""
                INSERT INTO interactions 
                    (user_id, item_id, event_type, rating, watch_progress, created_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
            """, uuid.UUID(user_id), item_id, event_type, rating, watch_progress)
    except Exception as e:
        print(f"[WARN] Failed to log interaction: {e}")


async def log_ab_ctr_event(user_id: str, metric: str, value: float = 1.0) -> None:
    """Look up the user's active A/B assignment and write a CTR metric to ab_events."""
    import uuid
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return  # skip non-UUID (sim) users
    try:
        async with get_pg_conn() as conn:
            row = await conn.fetchrow("""
                SELECT aa.experiment_id, aa.variant
                FROM ab_assignments aa
                JOIN ab_experiments ae ON ae.id = aa.experiment_id
                WHERE aa.user_id = $1 AND ae.status = 'running'
                LIMIT 1
            """, uid)
            if row:
                await log_ab_event(user_id, row['experiment_id'], row['variant'], metric, value)
    except Exception as e:
        print(f"[WARN] log_ab_ctr_event failed: {e}")

@router.get("/rating")
async def get_user_rating(user_id: str, item_id: str):
    """Retrieve the user's saved rating for a specific item from Redis."""
    from ..core.database import get_redis
    redis = get_redis()
    rating = await redis.hget(f"user:{user_id}:ratings", item_id)
    if rating is not None:
        try:
            return {"rating": float(rating)}
        except ValueError:
            return {"rating": None}
    return {"rating": None}

@router.post("/click")
async def post_click(event: ClickEvent, background_tasks: BackgroundTasks):
    await update_signals(event.user_id, "click", event.item_id)
    _publish_kafka("click", event.model_dump())
    background_tasks.add_task(log_interaction_to_db, event.user_id, event.item_id, "click")
    background_tasks.add_task(log_ab_ctr_event, event.user_id, "ctr", 1.0)
    return {"ok": True}

@router.post("/watch")
async def post_watch(event: WatchEvent, background_tasks: BackgroundTasks):
    extra = {"progress": event.progress} if event.progress is not None else None
    await update_signals(event.user_id, "watch", event.item_id, extra)
    _publish_kafka("watch", event.model_dump())
    background_tasks.add_task(log_interaction_to_db, event.user_id, event.item_id, "watch", watch_progress=event.progress)
    return {"ok": True}

@router.post("/watch_now")
async def post_watch_now(event: SimpleEvent, background_tasks: BackgroundTasks):
    await update_signals(event.user_id, "watch_now", event.item_id)
    _publish_kafka("watch_now", event.model_dump())
    background_tasks.add_task(log_interaction_to_db, event.user_id, event.item_id, "watch_now")
    return {"ok": True}

@router.post("/watch_later")
async def post_watch_later(event: SimpleEvent, background_tasks: BackgroundTasks):
    await update_signals(event.user_id, "watch_later", event.item_id)
    _publish_kafka("watch_later", event.model_dump())
    background_tasks.add_task(log_interaction_to_db, event.user_id, event.item_id, "watch_later")
    return {"ok": True}

@router.post("/rate")
async def post_rate(event: RateEvent, background_tasks: BackgroundTasks):
    extra = {"rating": event.rating, "genres": event.genres or []}
    await update_signals(event.user_id, "rate", event.item_id, extra)
    _publish_kafka("rate", event.model_dump())
    background_tasks.add_task(log_interaction_to_db, event.user_id, event.item_id, "rate", rating=event.rating)
    background_tasks.add_task(log_ab_ctr_event, event.user_id, "rating", event.rating)
    return {"ok": True}

@router.post("/search")
async def post_search(event: SearchEvent, background_tasks: BackgroundTasks):
    extra = {"query": event.query, "extracted_genres": event.extracted_genres or []}
    await update_signals(event.user_id, "search", None, extra)
    _publish_kafka("search", event.model_dump())
    background_tasks.add_task(log_interaction_to_db, event.user_id, None, "search")
    return {"ok": True}

@router.post("/skip")
async def post_skip(event: SimpleEvent, background_tasks: BackgroundTasks):
    await update_signals(event.user_id, "skip", event.item_id)
    _publish_kafka("skip", event.model_dump())
    background_tasks.add_task(log_interaction_to_db, event.user_id, event.item_id, "skip")
    return {"ok": True}
