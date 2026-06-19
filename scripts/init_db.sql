-- ============================================================
-- Suggestify V2 — Database Schema
-- PostgreSQL 15 | Run once on container first start
-- ============================================================

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- for fast text search

-- ─────────────────────────────────────────────────────────
-- USERS
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    username        VARCHAR(100) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    country         VARCHAR(10) DEFAULT 'IN',
    language        VARCHAR(10) DEFAULT 'en',
    preferred_genres TEXT[] DEFAULT '{}',
    preferred_content_types TEXT[] DEFAULT '{"movie","tv","anime","book"}',
    is_active       BOOLEAN DEFAULT TRUE,
    is_admin        BOOLEAN DEFAULT FALSE,
    onboarding_done BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_username ON users(username);

-- ─────────────────────────────────────────────────────────
-- ITEMS (406K+ movies, TV, anime, books)
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS items (
    id              VARCHAR(50) PRIMARY KEY,   -- e.g. "tmdb_movie_550", "book_OL123W"
    title           TEXT NOT NULL,
    content_type    VARCHAR(20) NOT NULL,       -- movie | tv | anime | book
    genres          TEXT[] DEFAULT '{}',
    description     TEXT,
    release_year    INTEGER,
    language        VARCHAR(20) DEFAULT 'en',
    country         VARCHAR(20),
    poster_url      TEXT,
    backdrop_url    TEXT,
    trailer_url     TEXT,
    rating          FLOAT DEFAULT 0.0,
    vote_count      INTEGER DEFAULT 0,
    popularity      FLOAT DEFAULT 0.0,
    author          VARCHAR(255),               -- books
    studio          VARCHAR(255),               -- anime/TV
    seasons         INTEGER,                    -- TV
    episodes        INTEGER,                    -- anime/TV
    page_count      INTEGER,                    -- books
    imdb_id         VARCHAR(20),
    external_id     VARCHAR(50),
    embedding_index INTEGER,                    -- row index in embeddings.npy
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_items_content_type ON items(content_type);
CREATE INDEX idx_items_genres ON items USING GIN(genres);
CREATE INDEX idx_items_language ON items(language);
CREATE INDEX idx_items_rating ON items(rating DESC);
CREATE INDEX idx_items_popularity ON items(popularity DESC);
CREATE INDEX idx_items_title_trgm ON items USING GIN(title gin_trgm_ops);
CREATE INDEX idx_items_release_year ON items(release_year DESC);

-- ─────────────────────────────────────────────────────────
-- INTERACTIONS (click, watch, skip, rate, save, search)
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS interactions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id         VARCHAR(50) NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    event_type      VARCHAR(30) NOT NULL,   -- click | watch | skip | rate | save | search | remove_save
    rating          FLOAT,                  -- for 'rate' events (1-5)
    watch_progress  FLOAT,                  -- 0-1, for 'watch' events
    session_id      VARCHAR(100),
    context         JSONB DEFAULT '{}',     -- device, time_of_day, etc.
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_interactions_user_id ON interactions(user_id);
CREATE INDEX idx_interactions_item_id ON interactions(item_id);
CREATE INDEX idx_interactions_event_type ON interactions(event_type);
CREATE INDEX idx_interactions_created_at ON interactions(created_at DESC);
CREATE INDEX idx_interactions_user_event ON interactions(user_id, event_type);

-- ─────────────────────────────────────────────────────────
-- AB EXPERIMENTS
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ab_experiments (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) UNIQUE NOT NULL,
    description     TEXT,
    variant_a       JSONB NOT NULL DEFAULT '{"name": "control"}',
    variant_b       JSONB NOT NULL DEFAULT '{"name": "treatment"}',
    traffic_split   FLOAT DEFAULT 0.5,      -- fraction going to B
    status          VARCHAR(20) DEFAULT 'running',  -- running | paused | completed
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    winner          VARCHAR(10)             -- 'A' | 'B' | null
);

CREATE TABLE IF NOT EXISTS ab_assignments (
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    experiment_id   INTEGER NOT NULL REFERENCES ab_experiments(id) ON DELETE CASCADE,
    variant         VARCHAR(10) NOT NULL,   -- 'A' | 'B'
    assigned_at     TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, experiment_id)
);

CREATE TABLE IF NOT EXISTS ab_events (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL,
    experiment_id   INTEGER NOT NULL,
    variant         VARCHAR(10) NOT NULL,
    metric          VARCHAR(50) NOT NULL,   -- ctr | watch_time | rating
    value           FLOAT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ab_events_exp ON ab_events(experiment_id, variant);

-- ─────────────────────────────────────────────────────────
-- RECOMMENDATION LOGS (for quality analysis)
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recommendation_logs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL,
    request_id      VARCHAR(100),
    row_name        VARCHAR(100),           -- "Because you liked X", "Trending Now"
    item_ids        TEXT[] NOT NULL,
    scores          FLOAT[] NOT NULL,
    strategy        VARCHAR(50),            -- two_tower | bandit | trending | cold_start
    latency_ms      INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_rec_logs_user ON recommendation_logs(user_id, created_at DESC);

-- ─────────────────────────────────────────────────────────
-- TRENDING (pre-computed hourly)
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trending_items (
    id              SERIAL PRIMARY KEY,
    item_id         VARCHAR(50) NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    content_type    VARCHAR(20),
    trend_score     FLOAT NOT NULL,
    window_hours    INTEGER DEFAULT 24,
    computed_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_trending_type ON trending_items(content_type, trend_score DESC);

-- ─────────────────────────────────────────────────────────
-- SEED DATA — Default A/B experiment
-- ─────────────────────────────────────────────────────────
INSERT INTO ab_experiments (name, description, variant_a, variant_b, traffic_split, status)
VALUES (
    'two_tower_vs_popularity',
    'Compare Two-Tower neural recs vs popularity baseline',
    '{"name": "control", "strategy": "popularity"}',
    '{"name": "treatment", "strategy": "two_tower"}',
    0.5,
    'running'
) ON CONFLICT (name) DO NOTHING;

-- ─────────────────────────────────────────────────────────
-- Auto-update updated_at timestamps
-- ─────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_items_updated_at
    BEFORE UPDATE ON items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
