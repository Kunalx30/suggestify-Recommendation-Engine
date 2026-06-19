# backend/api/auth.py

"""Authentication API router.

Provides user registration and JWT-based authentication using database table schemas.
"""

from __future__ import annotations

import uuid
import datetime
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from jose import jwt
from passlib.context import CryptContext

from ..core.database import get_pg_conn, get_redis
from ..core.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class RegisterSchema(BaseModel):
    email: EmailStr
    username: str
    password: str
    country: str = "IN"
    language: str = "en"

class LoginSchema(BaseModel):
    username: str
    password: str

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: datetime.timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + datetime.timedelta(days=7)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")
    return encoded_jwt

@router.post("/register")
async def register_user(user_data: RegisterSchema):
    """Register a new user and return their UUID user_id."""
    hashed = get_password_hash(user_data.password)
    try:
        async with get_pg_conn() as conn:
            # Check if email or username exists
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM users WHERE email = $1 OR username = $2)",
                user_data.email, user_data.username
            )
            if exists:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email or username already registered"
                )
                
            # Insert user
            user_id = await conn.fetchval(
                """INSERT INTO users (email, username, hashed_password, country, language)
                   VALUES ($1, $2, $3, $4, $5)
                   RETURNING id""",
                user_data.email, user_data.username, hashed, user_data.country, user_data.language
            )
            return {"ok": True, "user_id": str(user_id)}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Registration failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration failed: {str(e)}"
        )

@router.post("/login")
async def login_user(user_data: LoginSchema):
    """Authenticate a user and return a JWT access token and user_id."""
    try:
        async with get_pg_conn() as conn:
            row = await conn.fetchrow(
                "SELECT id, username, hashed_password FROM users WHERE username = $1",
                user_data.username
            )
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect username or password"
                )
            if not verify_password(user_data.password, row["hashed_password"]):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect username or password"
                )
                
            access_token = create_access_token(data={"sub": str(row["id"])})
            return {
                "access_token": access_token,
                "token_type": "bearer",
                "user_id": str(row["id"]),
                "username": row["username"]  # canonical casing from DB
            }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Login failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login failed: {str(e)}"
        )

class SeedRatingSchema(BaseModel):
    item_id: str
    rating: float

class OnboardingSchema(BaseModel):
    user_id: str
    preferred_genres: list[str]
    preferred_content_types: list[str]
    seed_ratings: list[SeedRatingSchema]

@router.post("/onboarding")
async def user_onboarding(data: OnboardingSchema):
    """Save user genre, content type preferences, and seed ratings during onboarding."""
    try:
        user_uuid = uuid.UUID(data.user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id format"
        )
        
    try:
        # 1. Update PostgreSQL users table
        async with get_pg_conn() as conn:
            exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM users WHERE id = $1)", user_uuid)
            if not exists:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )
                
            await conn.execute(
                """UPDATE users SET
                     preferred_genres = $1,
                     preferred_content_types = $2,
                     onboarding_done = TRUE
                   WHERE id = $3""",
                data.preferred_genres,
                data.preferred_content_types,
                user_uuid
            )

        # 2. Write to Redis
        redis = get_redis()
        
        # LPUSH user:{user_id}:genres → preferred_genres  (TTL 30 days)
        if data.preferred_genres:
            await redis.delete(f"user:{data.user_id}:genres")
            await redis.lpush(f"user:{data.user_id}:genres", *data.preferred_genres)
            await redis.expire(f"user:{data.user_id}:genres", 30 * 86400)
            
        # SET   user:{user_id}:content_type → preferred_content_types[0]  (TTL 30 days)
        if data.preferred_content_types:
            await redis.set(f"user:{data.user_id}:content_type", data.preferred_content_types[0], ex=30 * 86400)
            
        # SET   user:{user_id}:country → "IN"  (TTL 24hr)
        await redis.set(f"user:{data.user_id}:country", "IN", ex=24 * 3600)
        
        # SET   user:{user_id}:language → "en"  (TTL 24hr)
        await redis.set(f"user:{data.user_id}:language", "en", ex=24 * 3600)

        # For each seed_rating:
        if data.seed_ratings:
            async with get_pg_conn() as conn:
                for seed in data.seed_ratings:
                    # HSET user:{user_id}:ratings {item_id} {rating} (TTL 30 days)
                    await redis.hset(f"user:{data.user_id}:ratings", seed.item_id, seed.rating)
                    
                    # Fetch item genres from PostgreSQL
                    genres = await conn.fetchval("SELECT genres FROM items WHERE id = $1", seed.item_id)
                    
                    # For each genre: HINCRBYFLOAT user:{user_id}:genre_boost {genre} {rating - 3.0} (TTL 7 days)
                    if genres:
                        for genre in genres:
                            await redis.hincrbyfloat(f"user:{data.user_id}:genre_boost", genre, seed.rating - 3.0)
            
            await redis.expire(f"user:{data.user_id}:ratings", 30 * 86400)
            await redis.expire(f"user:{data.user_id}:genre_boost", 7 * 86400)
            
        # Recalculate avg_rating:
        ratings_dict = await redis.hgetall(f"user:{data.user_id}:ratings")
        if ratings_dict:
            total = sum(float(r) for r in ratings_dict.values())
            count = len(ratings_dict)
            avg = total / count if count > 0 else 0.0
            await redis.set(f"user:{data.user_id}:avg_rating", avg, ex=30 * 86400)

        return {"ok": True, "user_id": data.user_id}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Onboarding failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Onboarding failed: {str(e)}"
        )
