# backend/core/config.py

"""Application configuration using pydantic-settings.

All settings are loaded from environment variables or a ``.env`` file placed in the project root.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Any

class Settings(BaseSettings):
    # Core service URLs
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/suggestify"
    REDIS_URL: str = "redis://localhost:6379/0"
    QDRANT_URL: str = "http://localhost:6333"
    COLLECTION_NAME: str = Field(default="suggestify_items", validation_alias="QDRANT_COLLECTION")

    # Security / model parameters
    SECRET_KEY: str = "super-secret-key"
    EMBEDDING_DIM: int = 128
    RETRIEVAL_CANDIDATES: int = 200
    FINAL_RECS: int = 20
    BANDIT_EPSILON: float = 0.15
    MMR_LAMBDA: float = 0.7
    MAX_ITEMS_PER_GENRE: int = 4
    DLRM_MODEL_PATH: str = "ml/dlrm/model.pt"
    DLRM_WEIGHT: float = 0.5

    # Misc settings
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

# Export a singleton instance that can be imported throughout the project.
settings = Settings()
