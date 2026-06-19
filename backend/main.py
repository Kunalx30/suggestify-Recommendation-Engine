# backend/main.py

"""FastAPI entry point for the Suggestify backend.

Includes all route controllers, handles CORS, startup hooks, Prometheus instrumentation,
and service health checks.
"""

from __future__ import annotations

import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

# Core database connection helpers
from .core.database import get_pg_conn, get_redis, get_qdrant, init_pg_pool, close_pg_pool

# Routers
from .api.events import router as events_router
from .api.recommendations import router as recommendations_router
from .api.search import router as search_router
from .api.trending import router as trending_router
from .api.auth import router as auth_router
from .api.admin import router as admin_router

app = FastAPI(
    title="Suggestify Backend API",
    description="FastAPI recommender engine backend utilizing Two-Tower embeddings, Redis, and Qdrant.",
    version="2.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite development server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Route registrations
app.include_router(auth_router)
app.include_router(events_router)
app.include_router(recommendations_router)
app.include_router(search_router)
app.include_router(trending_router)
app.include_router(admin_router)

# Prometheus instrumentation middleware (automatically exposes /metrics)
Instrumentator().instrument(app).expose(app)

@app.on_event("shutdown")
async def shutdown_event():
    """Gracefully close the PostgreSQL connection pool on shutdown."""
    await close_pg_pool()

@app.on_event("startup")
async def startup_event():
    """Startup initialization: pre-load models and verify service connectivity."""
    print("[INFO] Starting Suggestify FastAPI application...")

    # 0. Initialize PostgreSQL connection pool (eliminates per-request TCP handshakes)
    try:
        await init_pg_pool()
        print("[INFO] PostgreSQL connection pool initialized.")
    except Exception as e:
        print(f"[ERROR] Failed to initialize PostgreSQL pool: {e}")
    
    # 1. Load Two-Tower PyTorch model
    try:
        from .services.two_tower import load_user_tower
        load_user_tower()
        print("[INFO] Two-Tower user embedding model loaded successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to load Two-Tower model on startup: {e}")

    # 1.5. Load DLRM PyTorch ranking model
    try:
        from .services.ranker import load_dlrm_model
        load_dlrm_model()
        print("[INFO] DLRM ranking model loaded successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to load DLRM model on startup: {e}")
        
    # 2. Test Redis connection
    try:
        redis_client = get_redis()
        await redis_client.ping()
        print("[INFO] Redis connection successful.")
    except Exception as e:
        print(f"[ERROR] Redis connection check failed on startup: {e}")
        
    # 3. Test Qdrant connection
    try:
        qdrant_client = get_qdrant()
        qdrant_client.get_collections()
        print("[INFO] Qdrant connection successful.")
    except Exception as e:
        print(f"[ERROR] Qdrant connection check failed on startup: {e}")

    # 4. Initialize trending scores on startup
    try:
        from .api.trending import refresh_trending_scores_task
        await refresh_trending_scores_task()
        print("[INFO] Trending scores initialized on startup.")
    except Exception as e:
        print(f"[WARN] Trending init failed (non-fatal): {e}")

@app.get("/health")
async def health_check():
    """Health check endpoint. Checks status of PostgreSQL, Redis, and Qdrant."""
    health_status = {
        "status": "healthy",
        "postgres": "unhealthy",
        "redis": "unhealthy",
        "qdrant": "unhealthy"
    }

    # Check PostgreSQL
    try:
        async with get_pg_conn() as conn:
            await conn.execute("SELECT 1")
        health_status["postgres"] = "healthy"
    except Exception as e:
        health_status["postgres"] = f"unhealthy: {e}"
        health_status["status"] = "degraded"

    # Check Redis
    try:
        redis_client = get_redis()
        await redis_client.ping()
        health_status["redis"] = "healthy"
    except Exception as e:
        health_status["redis"] = f"unhealthy: {e}"
        health_status["status"] = "degraded"

    # Check Qdrant
    try:
        qdrant_client = get_qdrant()
        qdrant_client.get_collections()
        health_status["qdrant"] = "healthy"
    except Exception as e:
        health_status["qdrant"] = f"unhealthy: {e}"
        health_status["status"] = "degraded"

    return health_status

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Observability middleware to track response times."""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.4f}s"
    return response
