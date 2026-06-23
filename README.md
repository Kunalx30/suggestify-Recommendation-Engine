<div align="center">

<br/>

# ⚡ SUGGESTIFY

### Multimodal · Learning-Based · Real-Time Recommendation Engine

<br/>

[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.x-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io)
[![Kafka](https://img.shields.io/badge/Kafka-3.x-231F20?style=for-the-badge&logo=apachekafka&logoColor=white)](https://kafka.apache.org)
[![Qdrant](https://img.shields.io/badge/Qdrant-Vector_DB-6C63FF?style=for-the-badge)](https://qdrant.tech)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

<br/>

> **A full-stack, AI-powered content recommendation engine built from scratch.**
> Recommends movies, TV shows, anime & books using a Two-Tower neural network,
> DLRM ranking model, real-time Kafka event pipeline, and diversity-aware reranking — all in one unified platform.

<br/>

**Project By [Kunal Chandelkar](https://github.com/kunalchandelkar) · Personal Engineering Project · June 2026**

<br/>

---

</div>

## 📸 Screenshots

<table>
<tr>
<td width="60%">

**🏠 Home — Hero Banner + Carousels**

![Home Page](Screenshots/Homescreen.png)

</td>
<td width="40%">

**📊 Admin Panel — System Health**

![Admin Panel](Screenshots/Admin_Panel.png)

</td>
</tr>
<tr>
<td width="60%">

**📺 TV Shows — Personalised Recommendations**

![Anime Page](Screenshots/TV_shows.png)

</td>
<td width="40%">

**📚 Books — Personalised Recommendations**

![Model Quality](Screenshots/books_Screen.png)

</td>
</tr>
</table>

---

## 📋 Table of Contents

- [What is SUGGESTIFY?](#-what-is-suggestify)
- [Key Features](#-key-features)
- [System Architecture](#-system-architecture)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Quick Start](#-quick-start)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Data Sources & Catalogue](#-data-sources--catalogue)
- [AI Models](#-ai-models)
- [REST API Reference](#-rest-api-reference)
- [Admin Panel](#-admin-panel)
- [Deployment](#-deployment)
- [Known Issues & Engineering Notes](#-known-issues--engineering-notes)
- [Future Roadmap](#-future-roadmap)
- [License](#-license)

---

## 🎯 What is SUGGESTIFY?

SUGGESTIFY is a **production-grade, full-stack recommendation engine** built entirely from the ground up as a personal engineering project. It unifies movies, television series, anime, and books into a single 406,000+ item catalogue and serves personalised, real-time recommendations through a two-stage AI pipeline.

Unlike wrappers around existing APIs, every layer here is custom-built:

- **Two custom neural models** — a Two-Tower retrieval network and a DLRM ranking network, both trained from scratch in PyTorch
- **Real-time personalisation** — every click, watch, and rating is captured by Kafka and reflected in the next recommendation within milliseconds
- **Diversity enforcement** — a Maximal Marginal Relevance reranker prevents single-genre echo chambers with a configurable per-genre cap
- **Full observability** — a live admin panel tracks system health, model quality, A/B experiments, fairness metrics, and user simulation

```
User opens app
    → Redis assembles taste features
    → Two-Tower model generates embedding
    → Qdrant finds nearest 50 candidates in < 30ms
    → DLRM scores & ranks each candidate
    → MMR diversity filter applies genre cap
    → Final match-scored list returned
    → Every interaction published to Kafka → Redis updated → next request already smarter
```

---

## ✨ Key Features

| Feature | Detail |
|---|---|
| 🎬 **Unified Catalogue** | 406,432+ items — movies, TV, anime, books in one schema |
| 🧠 **Two-Stage AI Pipeline** | Two-Tower retrieval + DLRM ranking, both trained from scratch |
| ⚡ **Real-Time Learning** | Kafka → Redis pipeline updates taste profile in < 1ms |
| 🎯 **Match Percentages** | 40–95% display band, min-max scaled from real relevance scores |
| 🌈 **Diversity Reranking** | MMR with configurable per-genre cap (default: max 4 per genre) |
| 🧊 **Cold Start Handling** | New users get genre-diversified recs from onboarding preferences |
| 🔍 **Hybrid Search** | Text + vector search across all 4 content verticals |
| 📈 **Trending Engine** | Hourly trending calculator based on recent interaction volume |
| 🛠 **Admin Observability** | System health, A/B experiments, fairness dashboard, model quality |
| 🔐 **JWT Authentication** | Signup, login, onboarding, role-based admin access |

---

## 🏗 System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│           React 18 + TypeScript SPA (Frontend)              │
│   Hero Banner · Genre Carousels · Item Modal · Onboarding   │
└────────────────────────┬────────────────────────────────────┘
                         │  HTTPS / REST (JWT-authenticated)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              FastAPI Gateway (ASGI Framework)                │
└──────────┬──────────────────────────────────────┬───────────┘
           │ Interaction Events                    │ Candidate Retrieval
           ▼                                       ▼
┌──────────────────────┐             ┌─────────────────────────┐
│   Kafka Event Stream │             │   Qdrant Vector Engine   │
│  (Zookeeper-managed) │             │  549K embeddings, < 30ms │
└──────────┬───────────┘             └──────────────┬──────────┘
           │ Real-Time Aggregation                   │ ANN Candidates
           ▼                                         │
┌──────────────────────────────────────┐             │
│          Redis Feature Store         │             │
│  clicks · watched · genre_boost      │             │
│  ratings · session_ctr · last_active │             │
└──────────────────────┬───────────────┘             │
                       │ Feature Read                 │
                       ▼                             ▼
┌─────────────────────────────────────────────────────────────┐
│              Ranking Service (ranker.py)                     │
│  1. Two-Tower user embedding → Qdrant ANN candidates        │
│  2. Heuristic + DLRM score blend                            │
│  3. MMR diversity rerank + per-genre cap                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 🛠 Tech Stack

### Frontend
| Technology | Purpose |
|---|---|
| React 18 + TypeScript | Component-based SPA |
| Tailwind CSS | Utility-first styling |
| Vite | Build toolchain |

### Backend
| Technology | Purpose |
|---|---|
| FastAPI (ASGI) | Async REST API gateway |
| asyncpg | Async PostgreSQL driver |
| python-jose | JWT authentication |
| Pydantic | Settings & validation |

### AI / ML
| Technology | Purpose |
|---|---|
| PyTorch | Two-Tower & DLRM training |
| MLflow | Experiment tracking & checkpoints |
| Qdrant | 549K-vector ANN index, < 30ms search |

### Data & Streaming
| Technology | Purpose |
|---|---|
| PostgreSQL 15 | 406K+ item catalogue & interactions |
| Redis 7 | Real-time user feature store |
| Apache Kafka + Zookeeper | Event streaming & decoupling |

### Observability
| Technology | Purpose |
|---|---|
| Prometheus | Metrics collection |
| Grafana | Live dashboards |
| Custom Admin Panel | A/B tests, fairness, model quality |

---

## 📁 Project Structure

```
suggestify/
│
├── backend/
│   ├── core/
│   │   └── config.py                # Centralised Pydantic settings
│   ├── services/
│   │   ├── two_tower.py             # Two-Tower inference (user tower only at runtime)
│   │   ├── ranker.py                # DLRM + heuristic blend + MMR reranking
│   │   └── signals.py               # Real-time Redis feature aggregator
│   └── api/
│       ├── auth.py                  # Signup / login / onboarding (JWT)
│       ├── recommendations.py       # Recommendation orchestration + score normalisation
│       ├── search.py                # Qdrant hybrid text + vector search
│       ├── trending.py              # Hourly trending calculator
│       ├── events.py                # Real-time signal endpoints (click/watch/rate)
│       └── admin.py                 # Admin panel telemetry
│
├── ml/
│   ├── two_tower/
│   │   ├── train.py                 # Two-Tower training (InfoNCE contrastive loss)
│   │   └── model.pt                 # Trained retrieval checkpoint
│   └── dlrm/
│       ├── train.py                 # DLRM training (MLflow-instrumented)
│       └── model.pt                 # Trained ranking checkpoint
│
├── frontend/
│   └── src/
│       ├── AuthPage.tsx             # Login / signup
│       ├── OnboardingPage.tsx       # Genre preferences + seed ratings
│       ├── HomePage.tsx             # Main browsing surface + carousels
│       ├── ItemModal.tsx            # Item detail modal
│       └── AdminPage.tsx            # Observability dashboard
│
├── scripts/
│   ├── load_data.py                 # Ingests 406K+ items into PostgreSQL
│   └── index_qdrant.py             # Builds Qdrant vector index from embeddings
│
├── docker-compose.yml               # 8-service orchestration
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker Desktop (required for backing services)
- CUDA GPU *(optional — recommended for training, not required for inference)*

### 1 — Clone the repo

```bash
git clone https://github.com/kunalchandelkar/suggestify.git
cd suggestify
```

### 2 — Start all backing services

```bash
docker-compose up -d
# Starts: PostgreSQL · Redis · Qdrant · Kafka · Zookeeper · Prometheus · Grafana · MLflow
```

### 3 — Set up Python environment

```bash
python -m venv venv

# Windows
.\\venv\\Scripts\\Activate.ps1

# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 4 — Configure environment

```bash
cp .env.example .env
# Edit .env with your values (see Configuration section)
```

### 5 — Load the catalogue

```bash
python scripts/load_data.py
# Ingests 406,432+ items from TMDB, Jikan, and Open Library into PostgreSQL
# Takes 10–20 minutes depending on your connection
```

### 6 — Train the models

```bash
# Train Two-Tower retrieval model
python ml/two_tower/train.py

# Train DLRM ranking model
python ml/dlrm/train.py --epochs 5

# Monitor training in MLflow UI → http://localhost:5000
```

### 7 — Build the Qdrant vector index

```bash
python scripts/index_qdrant.py
# Indexes 549K+ item embeddings into Qdrant
```

### 8 — Start the API

```bash
python -m uvicorn backend.main:app --reload
# API available at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
```

### 9 — Start the frontend

```bash
cd frontend
npm install
npm run dev
# App available at http://localhost:5173
```

---

## ⚙️ Configuration

All settings are centralised in `backend/core/config.py` and loaded from `.env`.

```bash
# .env.example

# ── Database ─────────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5433/suggestify

# ── Redis ────────────────────────────────────────────
REDIS_URL=redis://localhost:6379

# ── Qdrant ───────────────────────────────────────────
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=suggestify_items

# ── Kafka ────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_TOPIC=user-events

# ── ML Models ────────────────────────────────────────
DLRM_MODEL_PATH=ml/dlrm/model.pt
TWO_TOWER_MODEL_PATH=ml/two_tower/model.pt

# ── MLflow ───────────────────────────────────────────
MLFLOW_TRACKING_URI=http://localhost:5000

# ── Auth ─────────────────────────────────────────────
JWT_SECRET_KEY=your-secret-key-here
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# ── Recommendation Tuning ────────────────────────────
MMR_LAMBDA=0.7               # Relevance vs diversity trade-off (0.0–1.0)
MAX_ITEMS_PER_GENRE=4        # Hard genre cap in MMR reranking
BANDIT_EPSILON=0.15          # Cold-start exploration rate
DLRM_WEIGHT=0.5              # DLRM vs heuristic blend weight
```

---

## 📊 Data Sources & Catalogue

The catalogue is assembled from three public APIs, normalised into a single schema:

| Source | Items | Content Type | Key Attributes |
|---|---|---|---|
| TMDB | 167,710 | Movies | Poster, backdrop, trailer, synopsis, genres, rating |
| TMDB | 46,176 | TV Series | Poster, backdrop, trailer, synopsis, genres, rating |
| Jikan (MyAnimeList) | 19,868 | Anime | Poster, trailer, synopsis, genres, rating |
| Open Library | 172,678 | Books | Cover, author, genre, publication metadata |
| **Total** | **406,432+** | All four types | Unified schema |

---

## 🤖 AI Models

### Two-Tower Retrieval Network

A dual-encoder model that learns a joint embedding space for users and items:

```
User Tower Input                    Item Tower Input
────────────────                    ────────────────
Embedding(user_id, 64)              Embedding(item_id, 64)
Embedding(content_type, 16)         Embedding(content_type, 16)
genre_vector(50)                    genre_vector(50)
→ Linear(130, 256)                  → Linear(130, 256)
→ LayerNorm + GELU + Dropout(0.1)   → LayerNorm + GELU + Dropout(0.1)
→ Linear(256, 128)                  → Linear(256, 128)
      ↓ 128-dim embedding                 ↓ 128-dim embedding
                    cosine similarity
```

- **Loss**: InfoNCE contrastive loss
- **Training**: 100K items · 5,000 synthetic users
- **Inference**: user tower only (item embeddings pre-computed in Qdrant)
- **Speed**: < 30ms p99 ANN search over 549K vectors

### DLRM Ranking Model

A sparse-and-dense feature interaction network for fine-grained ranking:

**Dense features (5):** cosine similarity · normalised rating · genre match · recency · popularity

**Sparse features (4, embedded):** user_id · item_id · content_type · genre

**Training:** negative samples drawn via vectorised rejection sampling against the user's positive interaction set.

### Diversity Reranking (MMR)

```
Score(i) = λ · Relevance(i) − (1 − λ) · max_{j ∈ Selected} Sim(i, j)
```

subject to: no genre of item `i` may exceed `MAX_ITEMS_PER_GENRE` in the selected set.

A two-pass greedy loop ensures the list never truncates below the requested size.

---

## 🌐 REST API Reference

Base URL: `http://localhost:8000/api`

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/signup` | ❌ | Register new user, returns JWT |
| `POST` | `/auth/login` | ❌ | Authenticate user, returns JWT |
| `POST` | `/users/onboarding` | ✅ | Save genre preferences + seed ratings |
| `GET` | `/recommendations` | ✅ | Ranked, diversity-filtered recommendation list |
| `GET` | `/search?q=&limit=&content_type=` | ✅ | Hybrid text + vector search |
| `GET` | `/trending` | ✅ | Trending items by recent interaction volume |
| `POST` | `/events/click` | ✅ | Record click event → Kafka |
| `POST` | `/events/watch` | ✅ | Record watch event → Kafka |
| `POST` | `/events/rate` | ✅ | Record rating event → Kafka |
| `POST` | `/events/search` | ✅ | Record search event → Kafka |
| `GET` | `/admin/stats` | ✅ Admin | Live telemetry for admin panel |

Full interactive docs available at `http://localhost:8000/docs`

---

## 🛡 Admin Panel

Accessible at `/admin` (requires admin role). Tabs:

| Tab | What it shows |
|---|---|
| **System Health** | PostgreSQL · Redis · Qdrant · Kafka — all UP/DOWN with live stats |
| **A/B Experiments** | Experiment outcomes and variant performance |
| **Fairness** | Per-genre diversity metrics across all recommendation lists |
| **User Simulation** | Synthetic user stress-testing without live traffic |
| **Model Quality** | Embedding dim · training stats · Qdrant vector count · ANN speed |

---

## ☁️ Deployment

### Free-Tier Cloud Deployment (Zero Cost)

| Service | Platform | Free Limit |
|---|---|---|
| React Frontend | Firebase Hosting | 10GB storage, 360MB/day |
| FastAPI Backend | Google Cloud Run | 2M requests/month |
| PostgreSQL | Supabase | 500MB / 2 projects |
| Redis | Upstash | 10K commands/day, 256MB |
| Qdrant | Qdrant Cloud | 1GB cluster |
| Kafka | Confluent Cloud | 5GB/month |
| ML Models | Google Cloud Storage | 5GB |
| Monitoring | Grafana Cloud | Free forever |

See the full step-by-step deployment guide in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)

### Docker Compose (Local)

```bash
# Start all 8 services
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f
```

### Services & Ports

| Service | Port |
|---|---|
| FastAPI API | `8000` |
| React Frontend | `5173` |
| PostgreSQL | `5433` |
| Redis | `6379` |
| Qdrant | `6333` |
| Kafka | `9092` |
| MLflow | `5000` |
| Prometheus | `9090` |
| Grafana | `3000` |

---

## 🐛 Known Issues & Engineering Notes

These issues were discovered during integration testing, root-caused, and resolved. Documented here for engineering transparency.

### 1. Hardcoded Diversity Config in Admin Fallback
**Symptom:** Admin panel showed diversity config values that didn't match what the ranker actually enforced.
**Root Cause:** Exception-handling branch of the admin stats endpoint returned hardcoded literal values instead of reading from the live settings object.
**Fix:** Refactored fallback to read from the same `config` object used by the primary path.

### 2. Rank-Based Match Score Distortion
**Symptom:** Match percentages were evenly spaced at a fixed interval regardless of actual score differences.
**Root Cause:** Match % was computed from the item's position in the result list, not its blended relevance score.
**Fix:** Replaced rank-based scaling with min-max normalisation over the actual post-reranking relevance scores.

### 3. DLRM Validation Loss Divergence
**Symptom:** Training loss improved steadily while validation loss degraded after epoch 2.
**Root Cause:** Embedding table capacity was large relative to the 300K-interaction training set — classic overfitting.
**Fix:** Adopted early checkpointing at best validation epoch. Long-term fix: reduce embedding dimensions and expand training data.

### 4. Cold-Start UUID Embedding Collapse
**Symptom:** All real (UUID-identified) users appeared to share the same learned user embedding.
**Root Cause:** DLRM user embedding table was trained on synthetic integer IDs. UUID strings can't be parsed to a valid index and default to the padding index.
**Fix:** Confirmed personalisation is maintained via user-specific dense features (Two-Tower cosine similarity + genre boosts). Full fix tracked in roadmap — see UUID personalisation item below.

---

## 🗺 Future Roadmap

| Phase | Item | Status |
|---|---|---|
| ✅ Done | 406K catalogue, Two-Tower, DLRM, Kafka pipeline, Admin panel | **Shipped** |
| 🔵 Next | IMDB dataset merge → 1M+ items | Planned |
| 🔵 Next | UUID-aware personalisation (hashed bucket scheme) | Planned |
| 🔵 Next | Embedding dimensionality reduction to fix overfitting | Planned |
| 🟣 Future | Kubernetes containerisation + horizontal autoscaling | Planned |
| 🟣 Future | SHAP explainability per recommendation | Planned |
| 🟣 Future | Multi-language metadata + language-preference signals | Planned |
| 🟣 Future | Refresh-token rotation + role-based access control | Planned |

---

## 📄 License

```
MIT License

Copyright (c) 2026 Kunal Chandelkar

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
```

---

<div align="center">

<br/>

**Built from scratch. Designed to learn.**

⭐ If you found this project interesting, consider giving it a star!

<br/>

[![GitHub](https://img.shields.io/badge/GitHub-kunalchandelkar-181717?style=for-the-badge&logo=github)](https://github.com/kunalx30)

<br/>

*SUGGESTIFY — Personal Engineering Project by Kunal Chandelkar · June 2026*

</div>
