"""
import_imdb.py — Suggestify V2
================================
Downloads IMDB public datasets and merges them into PostgreSQL.

IMDB provides free TSV dumps at:
  https://datasets.imdbws.com/

Files used:
  title.basics.tsv.gz   — title, type, year, genres (9M+ titles)
  title.ratings.tsv.gz  — rating, vote_count (1.4M titles)

Strategy:
  - Download both files (~200MB total)
  - Filter to movies + TV only, min 100 votes, year >= 2000
  - Merge ratings onto basics
  - Insert as new items (tmdb_movie_* / tmdb_tv_* IDs won't clash since IMDB uses tt* IDs)
  - Skip titles already in DB via ON CONFLICT

Usage:
  python scripts/import_imdb.py
  python scripts/import_imdb.py --min-votes 1000 --min-year 2010
  python scripts/import_imdb.py --limit 50000
"""

import asyncio
import argparse
import os
import gzip
import time
import shutil
from pathlib import Path

import httpx
import asyncpg
import pandas as pd
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.table import Table

load_dotenv()
console = Console()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://suggestify:suggestify_secret@localhost:5433/suggestify")
DATA_DIR     = Path("./data")
IMDB_DIR     = DATA_DIR / "imdb"

IMDB_BASICS  = "https://datasets.imdbws.com/title.basics.tsv.gz"
IMDB_RATINGS = "https://datasets.imdbws.com/title.ratings.tsv.gz"

IMDB_GENRE_MAP = {
    "Action": "Action", "Adventure": "Adventure", "Animation": "Animation",
    "Biography": "Biography", "Comedy": "Comedy", "Crime": "Crime",
    "Documentary": "Documentary", "Drama": "Drama", "Family": "Family",
    "Fantasy": "Fantasy", "Film-Noir": "Crime", "History": "History",
    "Horror": "Horror", "Music": "Music", "Musical": "Music",
    "Mystery": "Mystery", "Romance": "Romance", "Sci-Fi": "Science Fiction",
    "Sport": "Sport", "Thriller": "Thriller", "War": "War", "Western": "Western",
    "Talk-Show": "Talk", "Reality-TV": "Reality", "Game-Show": "Game Show",
    "News": "News", "Short": "Drama",
}

TITLE_TYPE_MAP = {
    "movie":       "movie",
    "tvMovie":     "movie",
    "tvSeries":    "tv",
    "tvMiniSeries":"tv",
    "tvSpecial":   "tv",
}

# ─────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────

def download_file(url: str, dest: Path):
    if dest.exists():
        console.print(f"  Already downloaded: {dest.name}")
        return

    console.print(f"  Downloading {dest.name} ...")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        total = int(r.headers.get("content-length", 0))
        with open(dest, "wb") as f:
            downloaded = 0
            for chunk in r.iter_bytes(chunk_size=1024*1024):
                f.write(chunk)
                downloaded += len(chunk)
                mb = downloaded / 1024 / 1024
                if total:
                    pct = downloaded / total * 100
                    print(f"\r  {mb:.1f}MB / {total/1024/1024:.1f}MB ({pct:.0f}%)", end="", flush=True)
    print()
    console.print(f"  Saved: {dest}")


# ─────────────────────────────────────────────────────────
# Load and filter IMDB data
# ─────────────────────────────────────────────────────────

def load_imdb_data(min_votes: int, min_year: int, limit: int) -> pd.DataFrame:
    console.print("\nLoading title.basics.tsv.gz ...")
    basics = pd.read_csv(
        IMDB_DIR / "title.basics.tsv.gz",
        sep="\t",
        na_values=["\\N"],
        dtype=str,
        compression="gzip",
        usecols=["tconst", "titleType", "primaryTitle", "startYear", "genres"],
    )
    console.print(f"  Basics rows: {len(basics):,}")

    # Filter to supported types
    basics = basics[basics["titleType"].isin(TITLE_TYPE_MAP.keys())]
    console.print(f"  After type filter: {len(basics):,}")

    # Filter by year
    basics["startYear"] = pd.to_numeric(basics["startYear"], errors="coerce")
    basics = basics[basics["startYear"] >= min_year]
    console.print(f"  After year>={min_year} filter: {len(basics):,}")

    console.print("\nLoading title.ratings.tsv.gz ...")
    ratings = pd.read_csv(
        IMDB_DIR / "title.ratings.tsv.gz",
        sep="\t",
        na_values=["\\N"],
        dtype={"tconst": str, "averageRating": float, "numVotes": int},
        compression="gzip",
    )
    console.print(f"  Ratings rows: {len(ratings):,}")

    # Filter by vote count
    ratings = ratings[ratings["numVotes"] >= min_votes]
    console.print(f"  After min_votes>={min_votes} filter: {len(ratings):,}")

    # Merge
    df = basics.merge(ratings, on="tconst", how="inner")
    console.print(f"\nAfter merge: {len(df):,} items")

    if limit:
        df = df.sort_values("numVotes", ascending=False).head(limit)
        console.print(f"Limited to top {limit:,} by vote count")

    return df


# ─────────────────────────────────────────────────────────
# Normalize row
# ─────────────────────────────────────────────────────────

def normalize_imdb_row(row) -> dict:
    tconst      = str(row["tconst"])
    title_type  = str(row["titleType"])
    content_type = TITLE_TYPE_MAP.get(title_type, "movie")

    raw_genres = str(row.get("genres", "") or "")
    genres = []
    if raw_genres and raw_genres != "nan":
        for g in raw_genres.split(","):
            mapped = IMDB_GENRE_MAP.get(g.strip())
            if mapped:
                genres.append(mapped)

    return {
        "id":           f"imdb_{tconst}",
        "title":        str(row.get("primaryTitle", "Unknown")),
        "content_type": content_type,
        "genres":       genres[:10],
        "description":  "",
        "release_year": int(row.get("startYear", 0) or 0),
        "language":     "en",
        "country":      "",
        "poster_url":   "",
        "backdrop_url": "",
        "trailer_url":  "",
        "rating":       float(row.get("averageRating", 0) or 0),
        "vote_count":   int(row.get("numVotes", 0) or 0),
        "popularity":   float(row.get("numVotes", 0) or 0) / 1000.0,
        "imdb_id":      tconst,
        "external_id":  tconst,
    }


# ─────────────────────────────────────────────────────────
# Insert
# ─────────────────────────────────────────────────────────

INSERT_SQL = """
    INSERT INTO items (
        id, title, content_type, genres, description, release_year,
        language, country, poster_url, backdrop_url, trailer_url,
        rating, vote_count, popularity, imdb_id, external_id
    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
    ON CONFLICT (id) DO UPDATE SET
        rating     = EXCLUDED.rating,
        vote_count = EXCLUDED.vote_count,
        popularity = EXCLUDED.popularity,
        updated_at = NOW()
"""

async def insert_batch(conn, items: list) -> int:
    rows = [(
        i["id"], i["title"], i["content_type"], i["genres"],
        i["description"], i["release_year"], i["language"], i["country"],
        i["poster_url"], i["backdrop_url"], i["trailer_url"],
        i["rating"], i["vote_count"], i["popularity"],
        i["imdb_id"], i["external_id"],
    ) for i in items]
    await conn.executemany(INSERT_SQL, rows)
    return len(rows)


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────

async def main(args):
    console.rule("[bold cyan]IMDB Data Import — Suggestify V2[/bold cyan]")

    # ── Download ──────────────────────────────────────────
    IMDB_DIR.mkdir(parents=True, exist_ok=True)
    console.print("\n[bold]Step 1: Download IMDB dumps[/bold]")
    download_file(IMDB_BASICS,  IMDB_DIR / "title.basics.tsv.gz")
    download_file(IMDB_RATINGS, IMDB_DIR / "title.ratings.tsv.gz")

    # ── Load + filter ─────────────────────────────────────
    console.print("\n[bold]Step 2: Load and filter[/bold]")
    df = load_imdb_data(args.min_votes, args.min_year, args.limit)

    # ── Connect ───────────────────────────────────────────
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)
    async with pool.acquire() as conn:
        before = await conn.fetchval("SELECT COUNT(*) FROM items")
    console.print(f"\nDB items before: [cyan]{before:,}[/cyan]")

    # ── Insert ────────────────────────────────────────────
    console.print(f"\n[bold]Step 3: Insert {len(df):,} items into PostgreSQL[/bold]")
    BATCH = 500
    total_inserted = 0
    t0 = time.time()

    rows = df.to_dict("records")
    items_to_insert = []
    for row in rows:
        try:
            items_to_insert.append(normalize_imdb_row(row))
        except Exception:
            pass

    with Progress(SpinnerColumn(), TextColumn("Inserting"),
                  BarColumn(), TextColumn("{task.percentage:>3.0f}%"),
                  TextColumn("{task.completed}/{task.total}")) as prog:
        task = prog.add_task("insert", total=len(items_to_insert))
        async with pool.acquire() as conn:
            for i in range(0, len(items_to_insert), BATCH):
                batch = items_to_insert[i:i+BATCH]
                total_inserted += await insert_batch(conn, batch)
                prog.update(task, advance=len(batch))

    async with pool.acquire() as conn:
        after = await conn.fetchval("SELECT COUNT(*) FROM items")
        ct = await conn.fetch(
            "SELECT content_type, COUNT(*) cnt FROM items GROUP BY content_type ORDER BY cnt DESC"
        )

    # ── Summary ───────────────────────────────────────────
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")
    table.add_row("IMDB rows processed", f"{len(df):,}")
    table.add_row("Items upserted",      f"{total_inserted:,}")
    table.add_row("DB before",           f"{before:,}")
    table.add_row("DB after",            f"{after:,}")
    table.add_row("Net NEW items",       f"{after - before:,}")
    table.add_row("Time",                f"{time.time()-t0:.1f}s")
    console.print(table)

    console.print("\nBy content type:")
    for row in ct:
        console.print(f"  {row['content_type']:10s}: {row['cnt']:,}")

    console.print(f"\n[bold green]Done! {after:,} total items in DB.[/bold green]")
    console.print("Next: python ml/two_tower/train.py --epochs 10")
    await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-votes", type=int, default=100,
                        help="Minimum IMDB vote count (default: 100)")
    parser.add_argument("--min-year",  type=int, default=2000,
                        help="Minimum release year (default: 2000)")
    parser.add_argument("--limit",     type=int, default=None,
                        help="Max items to import (default: all)")
    asyncio.run(main(parser.parse_args()))