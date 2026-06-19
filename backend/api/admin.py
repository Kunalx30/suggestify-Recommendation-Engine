# backend/api/admin.py

"""Admin endpoints for system health, statistics, and experiments."""

from __future__ import annotations

import os
import asyncio
from datetime import datetime
from fastapi import APIRouter
from ..core.config import settings
from ..core.database import get_pg_conn, get_redis, get_qdrant

router = APIRouter(prefix="/admin", tags=["admin"])


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

async def check_postgres():
    """Verify PostgreSQL connectivity and count items."""
    try:
        async with get_pg_conn() as conn:
            items_count = await conn.fetchval("SELECT COUNT(*) FROM items")
            return {"status": "ok", "items": items_count}
    except Exception as e:
        return {"status": "error", "message": str(e), "items": 0}

async def check_redis():
    """Verify Redis connection and retrieve memory usage."""
    try:
        client = get_redis()
        await client.ping()
        info = await client.info()
        memory = info.get("used_memory_human", "unknown")
        return {"status": "ok", "memory": memory}
    except Exception as e:
        return {"status": "error", "message": str(e), "memory": "0"}

def _check_qdrant_sync():
    """Sync helper — must be called via run_in_executor (BUG-01 fix)."""
    try:
        client = get_qdrant()
        collection_name = os.getenv("QDRANT_COLLECTION", settings.COLLECTION_NAME)
        info = client.get_collection(collection_name)
        vectors = info.points_count
        return {"status": "ok", "vectors": vectors}
    except Exception as e:
        return {"status": "error", "message": str(e), "vectors": 0}

@router.get("/health")
async def admin_health():
    """Returns the status and stats of PostgreSQL, Redis, Qdrant, and Kafka."""
    # BUG-01 fix: offload sync Qdrant call to thread pool to avoid blocking event loop
    loop = asyncio.get_event_loop()
    pg_status, rd_status, qd_status = await asyncio.gather(
        check_postgres(),
        check_redis(),
        loop.run_in_executor(None, _check_qdrant_sync),
    )
    return {
        "postgres": pg_status,
        "redis": rd_status,
        "qdrant": qd_status,
        "kafka": {"status": "ok"},  # assume ok if app started
        "total_items": pg_status.get("items", 0),
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/stats")
async def admin_stats():
    """Returns overall platform stats, content type breakdown, events, and latency."""
    # BUG-03 fix: wrap in try/except so a DB error doesn't crash the endpoint
    try:
        async with get_pg_conn() as conn:
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
            total_interactions = await conn.fetchval("SELECT COUNT(*) FROM interactions")
            total_recs_logged = await conn.fetchval("SELECT COUNT(*) FROM recommendation_logs")
            content_breakdown = await conn.fetch(
                "SELECT content_type, COUNT(*) as cnt FROM items GROUP BY content_type"
            )
            recent_events = await conn.fetch("""
                SELECT event_type, COUNT(*) as cnt 
                FROM interactions 
                WHERE created_at > NOW() - INTERVAL '24 hours'
                GROUP BY event_type
            """)
            avg_latency = await conn.fetchval(
                "SELECT AVG(latency_ms) FROM recommendation_logs WHERE created_at > NOW() - INTERVAL '1 hour'"
            )

        # Eagerly read metadata from Two-Tower and DLRM checkpoints if they exist
        import torch
        two_tower_meta = {}
        tt_path = "ml/two_tower/model.pt"
        if os.path.exists(tt_path):
            try:
                tt_checkpoint = torch.load(tt_path, map_location="cpu")
                two_tower_meta = {
                    "num_users": tt_checkpoint.get("num_users", 0),
                    "num_train_items": tt_checkpoint.get("num_train_items", 0),
                }
            except Exception:
                pass

        dlrm_meta = {}
        if os.path.exists(settings.DLRM_MODEL_PATH):
            try:
                dlrm_checkpoint = torch.load(settings.DLRM_MODEL_PATH, map_location="cpu")
                dlrm_meta = {
                    "num_users": dlrm_checkpoint.get("num_users", 0),
                    "num_items": dlrm_checkpoint.get("num_items", 0),
                    "embedding_dim": dlrm_checkpoint.get("embedding_dim", 128),
                }
            except Exception:
                pass

        return {
            "total_users": total_users,
            "total_interactions": total_interactions,
            "total_recs_logged": total_recs_logged,
            "content_breakdown": [dict(r) for r in content_breakdown],
            "recent_events_24h": [dict(r) for r in recent_events],
            "avg_latency_ms": float(avg_latency or 0),
            "diversity_config": {
                "mmr_lambda": settings.MMR_LAMBDA,
                "bandit_epsilon": settings.BANDIT_EPSILON,
                "max_items_per_genre": settings.MAX_ITEMS_PER_GENRE,
            },
            "model_metadata": {
                "two_tower": two_tower_meta,
                "dlrm": dlrm_meta
            }
        }
    except Exception as e:
        print(f"[ERROR] admin_stats failed: {e}")
        return {
            "total_users": 0, "total_interactions": 0, "total_recs_logged": 0,
            "content_breakdown": [], "recent_events_24h": [], "avg_latency_ms": 0.0,
            "diversity_config": {
                "mmr_lambda": 0.7,
                "bandit_epsilon": 0.15,
                "max_items_per_genre": 4,
            }
        }

@router.get("/ab")
async def admin_ab():
    """Returns active A/B experiments, variant user splits, and CTR metrics from ab_events."""
    import json
    try:
        async with get_pg_conn() as conn:
            experiments = await conn.fetch("SELECT * FROM ab_experiments ORDER BY started_at DESC")
            results = []
            for exp in experiments:
                exp_dict = dict(exp)
                for k in ["variant_a", "variant_b"]:
                    val = exp_dict.get(k)
                    if isinstance(val, str):
                        try:
                            exp_dict[k] = json.loads(val)
                        except Exception:
                            pass
                a_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM ab_assignments WHERE experiment_id=$1 AND variant='A'", exp['id']
                )
                b_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM ab_assignments WHERE experiment_id=$1 AND variant='B'", exp['id']
                )
                # Pull CTR metrics from ab_events
                metrics = await conn.fetch("""
                    SELECT variant,
                           COUNT(*) FILTER (WHERE metric = 'impression') AS impressions,
                           COUNT(*) FILTER (WHERE metric = 'ctr')        AS clicks,
                           AVG(value) FILTER (WHERE metric = 'rating')   AS avg_rating
                    FROM ab_events
                    WHERE experiment_id = $1
                    GROUP BY variant
                """, exp['id'])
                metric_map = {r['variant']: dict(r) for r in metrics}

                def safe_ctr(m):
                    imp = m.get('impressions') or 0
                    clk = m.get('clicks') or 0
                    return round(clk / imp * 100, 2) if imp > 0 else 0.0

                results.append({
                    **exp_dict,
                    "a_users": a_count,
                    "b_users": b_count,
                    "a_metrics": {
                        "impressions": int(metric_map.get('A', {}).get('impressions') or 0),
                        "clicks":      int(metric_map.get('A', {}).get('clicks') or 0),
                        "ctr_pct":     safe_ctr(metric_map.get('A', {})),
                        "avg_rating":  round(float(metric_map.get('A', {}).get('avg_rating') or 0), 2),
                    },
                    "b_metrics": {
                        "impressions": int(metric_map.get('B', {}).get('impressions') or 0),
                        "clicks":      int(metric_map.get('B', {}).get('clicks') or 0),
                        "ctr_pct":     safe_ctr(metric_map.get('B', {})),
                        "avg_rating":  round(float(metric_map.get('B', {}).get('avg_rating') or 0), 2),
                    },
                })
        return results
    except Exception as e:
        print(f"[ERROR] admin_ab failed: {e}")
        return []

@router.post("/reset_sim_user")
async def reset_sim_user(body: dict):
    user_id = body.get("user_id", "")
    redis = get_redis()
    keys_to_delete = [
        f"user:{user_id}:clicks",
        f"user:{user_id}:watched", 
        f"user:{user_id}:ratings",
        f"user:{user_id}:genre_boost",
        f"user:{user_id}:interaction_count",
        f"user:{user_id}:session_ctr",
        f"user:{user_id}:avg_rating",
        f"user:{user_id}:last_active",
        f"user:{user_id}:genres",
        f"user:{user_id}:content_type",
        f"user:{user_id}:watch_later",
        f"user:{user_id}:search_history",
        # Also bust the recommendation row cache for this user
        f"rows:{user_id}:all",
        f"rows:{user_id}:movie",
        f"rows:{user_id}:tv",
        f"rows:{user_id}:anime",
        f"rows:{user_id}:book",
    ]
    for key in keys_to_delete:
        await redis.delete(key)
    return {"ok": True, "cleared": len(keys_to_delete)}

