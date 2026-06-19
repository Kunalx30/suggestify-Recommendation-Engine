# Suggestify V2 — Day 1 Checklist

## What's in this folder right now

```
suggestify/
├── .env                          ✅ All service configs
├── .gitignore                    ✅
├── docker-compose.yml            ✅ 8 services
├── requirements.txt              ✅ All Python deps
├── scripts/
│   ├── init_db.sql               ✅ PostgreSQL schema (auto-runs on first start)
│   ├── load_data.py              ✅ Loads 406K+ items from parquets
│   └── verify_services.py        ✅ Health check for all 8 services
└── monitoring/
    ├── prometheus/prometheus.yml  ✅
    └── grafana/datasources/       ✅
```

---

## Step-by-step Day 1 commands

### 1. Copy this folder to your machine
```
C:\Users\kunal\suggestify\
```

### 2. Copy your backed-up parquets into:
```
C:\Users\kunal\suggestify\data\parquets\
  ├── tmdb_movies.parquet
  ├── tmdb_tv.parquet
  ├── jikan_anime.parquet
  └── openlib_books.parquet
```

### 3. Create Python virtual environment
```bash
cd C:\Users\kunal\suggestify
python -m venv venv
venv\Scripts\activate

pip install --upgrade pip
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt
```

### 4. Start all 8 Docker services
```bash
docker compose up -d
```
Wait ~60 seconds for Kafka + Zookeeper to fully start.

### 5. Verify all services are healthy
```bash
python scripts/verify_services.py
```
Expected output: all 8 services GREEN ✅

### 6. Load 406K items into PostgreSQL
```bash
python scripts/load_data.py
```
Expected: ~10-20 minutes. Final line: "406,432 items loaded"

### 7. Quick smoke test
```bash
# Check DB directly
docker exec -it suggestify_postgres psql -U suggestify -d suggestify -c "SELECT content_type, COUNT(*) FROM items GROUP BY content_type;"
```

---

## Service URLs (bookmark these)

| Service    | URL                          | Credentials |
|------------|------------------------------|-------------|
| Grafana    | http://localhost:3001         | admin / suggestify_admin |
| MLflow     | http://localhost:5000         | no auth |
| Prometheus | http://localhost:9090         | no auth |
| Qdrant     | http://localhost:6333/dashboard | no auth |

---

## Troubleshooting

**Docker services not starting?**
```bash
docker compose logs postgres    # check postgres logs
docker compose logs kafka       # kafka takes 30s to start
docker compose restart kafka    # if kafka is unhealthy
```

**Parquet not found?**
- Make sure files are in `data/parquets/` with exact names: `tmdb_movies.parquet`, `tmdb_tv.parquet`, `jikan_anime.parquet`, `openlib_books.parquet`
- Or edit `PARQUET_MAP` in `scripts/load_data.py` to point to your actual paths

**Test with small data first:**
```bash
python scripts/load_data.py --limit 100
```

---

## Day 2 Preview
```bash
python scripts/import_imdb.py   # merge IMDB → 500K-1M items
```
