"""
load_data.py — Suggestify V2 Data Loader
=========================================
Loads 406K+ items from all sources into PostgreSQL.

Sources:
  - TMDB Movies  (167,710 items) — from saved parquets
  - TMDB TV      ( 46,176 items) — from saved parquets
  - Jikan Anime  ( 19,868 items) — from saved parquets
  - Open Library ( 172,678 items) — from saved parquets

Usage:
  python scripts/load_data.py              # load all
  python scripts/load_data.py --drop       # drop tables first, then reload
  python scripts/load_data.py --source movies  # load only movies
  python scripts/load_data.py --limit 1000     # load first 1000 of each (testing)
"""

import asyncio
import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
import asyncpg
from dotenv import load_dotenv
from tqdm import tqdm
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn

load_dotenv()
console = Console()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://suggestify:suggestify_secret@localhost:5433/suggestify"
)

DATA_DIR = Path("./data")
PARQUET_DIR = DATA_DIR / "parquets"  # where your backed-up parquets live

BATCH_SIZE = 500  # rows per INSERT batch — safe for 16GB RAM

# ─────────────────────────────────────────────────────────
# Fix 2: TMDB Genre ID → Name mapping
# Some TMDB parquet rows store genre IDs as strings instead of names.
# ─────────────────────────────────────────────────────────

TMDB_GENRE_MAP = {
    "28":    "Action",
    "12":    "Adventure",
    "16":    "Animation",
    "35":    "Comedy",
    "80":    "Crime",
    "99":    "Documentary",
    "18":    "Drama",
    "10751": "Family",
    "14":    "Fantasy",
    "36":    "History",
    "27":    "Horror",
    "10402": "Music",
    "9648":  "Mystery",
    "10749": "Romance",
    "878":   "Science Fiction",
    "53":    "Thriller",
    "10752": "War",
    "37":    "Western",
    "10759": "Action",        # TV: Action & Adventure
    "10765": "Science Fiction", # TV: Sci-Fi & Fantasy
    "10762": "Kids",
    "10763": "News",
    "10764": "Reality",
    "10766": "Soap",
    "10767": "Talk",
    "10768": "War",           # TV: War & Politics
}


def _resolve_tmdb_genres(genres: list) -> list:
    """
    Convert any remaining numeric genre IDs (stored as strings) to names,
    then strip any that are still purely digits.
    Applied to both TMDB movie and TV normalizers.
    """
    genres = [TMDB_GENRE_MAP.get(str(g), str(g)) for g in genres]
    genres = [g for g in genres if not str(g).isdigit()]
    return genres


# ─────────────────────────────────────────────────────────
# Schema: normalize each source into a unified item dict
# ─────────────────────────────────────────────────────────

def normalize_tmdb_movie(row: dict) -> dict:
    """Normalize a TMDB movie row → unified item schema."""
    if "tmdb_id" in row and not pd.isna(row.get("tmdb_id")):
        import json
        genres = row.get("genre_ids", [])
        if isinstance(genres, str):
            try:
                genres = json.loads(genres)
            except Exception:
                genres = [genres]
        
        genres = _resolve_tmdb_genres(genres)
        
        trailer = ""
        t_key = row.get("trailer_key")
        if t_key and not pd.isna(t_key):
            trailer = f"https://www.youtube.com/watch?v={t_key}"

        return {
            "id": f"tmdb_movie_{row['tmdb_id']}",
            "title": str(row.get("title", "Unknown")),
            "content_type": "movie",
            "genres": genres[:10] if isinstance(genres, list) else [],
            "description": str(row.get("description", ""))[:2000],
            "release_year": int(row.get("year", 0) or 0),
            "language": row.get("language", "en"),
            "country": "",
            "poster_url": row.get("poster_url") or "",
            "backdrop_url": row.get("backdrop_url") or "",
            "trailer_url": trailer,
            "rating": float(row.get("rating", 0) or 0),
            "vote_count": int(row.get("vote_count", 0) or 0),
            "popularity": float(row.get("popularity", 0) or 0),
            "imdb_id": row.get("imdb_id", "") or "",
            "external_id": str(row["tmdb_id"]),
        }

    genres = row.get("genres", [])
    if isinstance(genres, str):
        import json
        try:
            genres = [g["name"] for g in json.loads(genres)]
        except Exception:
            genres = [genres]

    # Fix 2: resolve any numeric IDs that slipped through
    genres = _resolve_tmdb_genres(genres)

    poster = row.get("poster_path", "")
    if poster and not poster.startswith("http"):
        poster = f"https://image.tmdb.org/t/p/w500{poster}"

    backdrop = row.get("backdrop_path", "")
    if backdrop and not backdrop.startswith("http"):
        backdrop = f"https://image.tmdb.org/t/p/w1280{backdrop}"

    # Trailer: stored as YouTube key or full URL
    trailer = row.get("trailer_url") or row.get("trailer_key", "")
    if trailer and not trailer.startswith("http") and len(trailer) < 20:
        trailer = f"https://www.youtube.com/watch?v={trailer}"

    return {
        "id": f"tmdb_movie_{row.get('id', row.get('tmdb_id', ''))}",
        "title": str(row.get("title", row.get("original_title", "Unknown"))),
        "content_type": "movie",
        "genres": genres[:10],
        "description": str(row.get("overview", ""))[:2000],
        "release_year": _extract_year(row.get("release_date", "")),
        "language": row.get("original_language", "en"),
        "country": _first(row.get("production_countries", [])),
        "poster_url": poster,
        "backdrop_url": backdrop,
        "trailer_url": trailer,
        "rating": float(row.get("vote_average", 0) or 0),
        "vote_count": int(row.get("vote_count", 0) or 0),
        "popularity": float(row.get("popularity", 0) or 0),
        "imdb_id": row.get("imdb_id", ""),
        "external_id": str(row.get("id", "")),
    }


def normalize_tmdb_tv(row: dict) -> dict:
    """Normalize a TMDB TV row → unified item schema."""
    if "tmdb_id" in row and not pd.isna(row.get("tmdb_id")):
        import json
        genres = row.get("genre_ids", [])
        if isinstance(genres, str):
            try:
                genres = json.loads(genres)
            except Exception:
                genres = [genres]
        
        genres = _resolve_tmdb_genres(genres)
        
        trailer = ""
        t_key = row.get("trailer_key")
        if t_key and not pd.isna(t_key):
            trailer = f"https://www.youtube.com/watch?v={t_key}"

        return {
            "id": f"tmdb_tv_{row['tmdb_id']}",
            "title": str(row.get("title", "Unknown")),
            "content_type": "tv",
            "genres": genres[:10] if isinstance(genres, list) else [],
            "description": str(row.get("description", ""))[:2000],
            "release_year": int(row.get("year", 0) or 0),
            "language": row.get("language", "en"),
            "country": "",
            "poster_url": row.get("poster_url") or "",
            "backdrop_url": row.get("backdrop_url") or "",
            "trailer_url": trailer,
            "rating": float(row.get("rating", 0) or 0),
            "vote_count": int(row.get("vote_count", 0) or 0),
            "popularity": float(row.get("popularity", 0) or 0),
            "studio": "",
            "seasons": 0,
            "episodes": 0,
            "external_id": str(row["tmdb_id"]),
        }

    genres = row.get("genres", [])
    if isinstance(genres, str):
        import json
        try:
            genres = [g["name"] for g in json.loads(genres)]
        except Exception:
            genres = [genres]

    # Fix 2: resolve any numeric IDs that slipped through
    genres = _resolve_tmdb_genres(genres)

    poster = row.get("poster_path", "")
    if poster and not poster.startswith("http"):
        poster = f"https://image.tmdb.org/t/p/w500{poster}"

    backdrop = row.get("backdrop_path", "")
    if backdrop and not backdrop.startswith("http"):
        backdrop = f"https://image.tmdb.org/t/p/w1280{backdrop}"

    trailer = row.get("trailer_url") or row.get("trailer_key", "")
    if trailer and not trailer.startswith("http") and len(trailer) < 20:
        trailer = f"https://www.youtube.com/watch?v={trailer}"

    return {
        "id": f"tmdb_tv_{row.get('id', row.get('tmdb_id', ''))}",
        "title": str(row.get("name", row.get("original_name", "Unknown"))),
        "content_type": "tv",
        "genres": genres[:10],
        "description": str(row.get("overview", ""))[:2000],
        "release_year": _extract_year(row.get("first_air_date", "")),
        "language": row.get("original_language", "en"),
        "country": _first(row.get("origin_country", [])),
        "poster_url": poster,
        "backdrop_url": backdrop,
        "trailer_url": trailer,
        "rating": float(row.get("vote_average", 0) or 0),
        "vote_count": int(row.get("vote_count", 0) or 0),
        "popularity": float(row.get("popularity", 0) or 0),
        "studio": _first(row.get("networks", [])),
        "seasons": int(row.get("number_of_seasons", 0) or 0),
        "episodes": int(row.get("number_of_episodes", 0) or 0),
        "external_id": str(row.get("id", "")),
    }


def normalize_jikan_anime(row: dict) -> dict:
    """Normalize a Jikan (MyAnimeList) anime row → unified item schema."""
    if "tmdb_id" in row and str(row["tmdb_id"]).startswith("anime_"):
        import json
        genres = row.get("genre_ids", [])
        if isinstance(genres, str):
            try:
                genres = json.loads(genres)
            except Exception:
                genres = [genres]
        return {
            "id": row["tmdb_id"],
            "title": str(row.get("title", "Unknown")),
            "content_type": "anime",
            "genres": genres[:10] if isinstance(genres, list) else [],
            "description": str(row.get("description", ""))[:2000],
            "release_year": int(row.get("year", 0) or 0),
            "language": row.get("language", "ja"),
            "poster_url": row.get("poster_url", ""),
            "rating": float(row.get("rating", 0.0) or 0.0),
            "vote_count": int(row.get("vote_count", 0) or 0),
            "popularity": float(row.get("popularity", 0.0) or 0.0),
            "external_id": row["tmdb_id"].replace("anime_", ""),
        }

    genres = row.get("genres", [])
    if isinstance(genres, str):
        import json
        try:
            genres = [g["name"] for g in json.loads(genres)]
        except Exception:
            genres = [genres]
    elif isinstance(genres, list) and genres and isinstance(genres[0], dict):
        genres = [g.get("name", "") for g in genres]

    trailer = row.get("trailer_url", "") or row.get("trailer", {})
    if isinstance(trailer, dict):
        trailer = trailer.get("url", "")

    return {
        "id": f"anime_{row.get('mal_id', row.get('id', ''))}",
        "title": str(row.get("title", row.get("title_english", "Unknown"))),
        "content_type": "anime",
        "genres": genres[:10],
        "description": str(row.get("synopsis", ""))[:2000],
        "release_year": int(row.get("year", 0) or 0),
        "language": "ja",
        "poster_url": str(row.get("images", {}).get("jpg", {}).get("large_image_url", "")
                         if isinstance(row.get("images"), dict) else row.get("poster_url", "")),
        "trailer_url": str(trailer),
        "rating": float(row.get("score", 0) or 0),
        "vote_count": int(row.get("scored_by", 0) or 0),
        "popularity": float(row.get("popularity", 0) or 0),
        "studio": _first(row.get("studios", [])),
        "episodes": int(row.get("episodes", 0) or 0),
        "external_id": str(row.get("mal_id", "")),
    }


def normalize_openlib_book(row: dict) -> dict:
    """Normalize an Open Library book row → unified item schema."""
    if "tmdb_id" in row and str(row["tmdb_id"]).startswith("book_"):
        import json
        genres = row.get("genre_ids", [])
        if isinstance(genres, str):
            try:
                genres = json.loads(genres)
            except Exception:
                genres = [genres]
        return {
            "id": row["tmdb_id"],
            "title": str(row.get("title", "Unknown")),
            "content_type": "book",
            "genres": genres[:10] if isinstance(genres, list) else [],
            "description": str(row.get("description", ""))[:2000],
            "release_year": int(row.get("year", 0) or 0),
            "language": row.get("language", "en"),
            "poster_url": row.get("poster_url", ""),
            "rating": float(row.get("rating", 0.0) or 0.0),
            "vote_count": int(row.get("vote_count", 0) or 0),
            "popularity": float(row.get("popularity", 0.0) or 0.0),
            "external_id": row["tmdb_id"].replace("book_", ""),
        }

    genres = row.get("subjects", row.get("genres", []))
    if isinstance(genres, str):
        import json
        try:
            genres = json.loads(genres)
        except Exception:
            genres = [genres]
    genres = [str(g) for g in (genres or [])][:10]

    cover_id = row.get("cover_id") or row.get("covers", [None])[0] if isinstance(row.get("covers"), list) else row.get("cover_id")
    poster_url = f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else ""

    return {
        "id": f"book_{row.get('key', row.get('work_key', '')).replace('/', '_').strip('_')}",
        "title": str(row.get("title", "Unknown")),
        "content_type": "book",
        "genres": genres,
        "description": str(row.get("description", row.get("first_sentence", "")))[:2000],
        "release_year": int(row.get("first_publish_year", 0) or 0),
        "language": _first(row.get("language", ["en"])),
        "poster_url": poster_url,
        "rating": float(row.get("ratings_average", 0) or 0),
        "vote_count": int(row.get("ratings_count", 0) or 0),
        "popularity": float(row.get("want_to_read_count", 0) or 0),
        "author": _first(row.get("author_names", row.get("authors", []))),
        "page_count": int(row.get("number_of_pages_median", 0) or 0),
        "external_id": str(row.get("key", "")),
    }


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _extract_year(date_str) -> int:
    if not date_str:
        return 0
    try:
        return int(str(date_str)[:4])
    except Exception:
        return 0


def _first(lst) -> str:
    if not lst:
        return ""
    if isinstance(lst, list):
        item = lst[0]
        if isinstance(item, dict):
            return str(item.get("name", item.get("iso_3166_1", "")))
        return str(item)
    return str(lst)


# ─────────────────────────────────────────────────────────
# Database operations
# ─────────────────────────────────────────────────────────

INSERT_SQL = """
    INSERT INTO items (
        id, title, content_type, genres, description, release_year,
        language, country, poster_url, backdrop_url, trailer_url,
        rating, vote_count, popularity, author, studio,
        seasons, episodes, page_count, imdb_id, external_id
    ) VALUES (
        $1, $2, $3, $4, $5, $6,
        $7, $8, $9, $10, $11,
        $12, $13, $14, $15, $16,
        $17, $18, $19, $20, $21
    )
    ON CONFLICT (id) DO UPDATE SET
        title        = EXCLUDED.title,
        genres       = EXCLUDED.genres,
        description  = EXCLUDED.description,
        poster_url   = EXCLUDED.poster_url,
        backdrop_url = EXCLUDED.backdrop_url,
        trailer_url  = EXCLUDED.trailer_url,
        rating       = EXCLUDED.rating,
        vote_count   = EXCLUDED.vote_count,
        popularity   = EXCLUDED.popularity,
        updated_at   = NOW()
"""


def item_to_row(item: dict) -> tuple:
    """Convert normalized item dict → tuple for asyncpg executemany."""
    return (
        item.get("id", ""),
        item.get("title", ""),
        item.get("content_type", ""),
        item.get("genres", []),
        item.get("description", ""),
        item.get("release_year", 0) or 0,
        item.get("language", "en") or "en",
        item.get("country", "") or "",
        item.get("poster_url", "") or "",
        item.get("backdrop_url", "") or "",
        item.get("trailer_url", "") or "",
        item.get("rating", 0.0) or 0.0,
        item.get("vote_count", 0) or 0,
        item.get("popularity", 0.0) or 0.0,
        item.get("author", "") or "",
        item.get("studio", "") or "",
        item.get("seasons", 0) or 0,
        item.get("episodes", 0) or 0,
        item.get("page_count", 0) or 0,
        item.get("imdb_id", "") or "",
        item.get("external_id", "") or "",
    )


async def batch_insert(conn: asyncpg.Connection, items: list[dict], source_name: str) -> int:
    """Insert a batch of items, return count of successful inserts."""
    rows = []
    for item in items:
        try:
            rows.append(item_to_row(item))
        except Exception as e:
            console.print(f"[yellow]⚠ Skip malformed item: {e}[/yellow]")

    if not rows:
        return 0

    try:
        await conn.executemany(INSERT_SQL, rows)
        return len(rows)
    except Exception as e:
        console.print(f"[red]✗ Batch insert error ({source_name}): {e}[/red]")
        # Try one by one to isolate bad rows
        success = 0
        for row in rows:
            try:
                await conn.execute(INSERT_SQL, *row)
                success += 1
            except Exception:
                pass
        return success


async def load_source(
    pool: asyncpg.Pool,
    df: pd.DataFrame,
    normalizer,
    source_name: str,
    limit: Optional[int] = None,
) -> int:
    """Load a DataFrame source into PostgreSQL."""
    if limit:
        df = df.head(limit)

    total = len(df)
    loaded = 0

    async with pool.acquire() as conn:
        with Progress(
            SpinnerColumn(),
            TextColumn(f"[cyan]{source_name}[/cyan]"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.completed}/{task.total}"),
        ) as progress:
            task = progress.add_task(source_name, total=total)

            batch = []
            for _, row in df.iterrows():
                try:
                    item = normalizer(row.to_dict())
                    if item.get("id") and item.get("title"):
                        batch.append(item)
                except Exception as e:
                    pass  # skip malformed rows silently

                if len(batch) >= BATCH_SIZE:
                    loaded += await batch_insert(conn, batch, source_name)
                    progress.update(task, advance=len(batch))
                    batch = []

            if batch:
                loaded += await batch_insert(conn, batch, source_name)
                progress.update(task, advance=len(batch))

    return loaded


# ─────────────────────────────────────────────────────────
# Parquet file discovery
# ─────────────────────────────────────────────────────────

PARQUET_MAP = {
    "movies": {
        "paths": [
            PARQUET_DIR / "tmdb_movies.parquet",
            PARQUET_DIR / "movies.parquet",
            DATA_DIR / "tmdb_movies.parquet",
        ],
        "normalizer": normalize_tmdb_movie,
    },
    "tv": {
        "paths": [
            PARQUET_DIR / "tmdb_tv.parquet",
            PARQUET_DIR / "tv_shows.parquet",
            DATA_DIR / "tmdb_tv.parquet",
        ],
        "normalizer": normalize_tmdb_tv,
    },
    "anime": {
        "paths": [
            PARQUET_DIR / "jikan_anime.parquet",
            PARQUET_DIR / "anime.parquet",
            DATA_DIR / "jikan_anime.parquet",
        ],
        "normalizer": normalize_jikan_anime,
    },
    "books": {
        "paths": [
            PARQUET_DIR / "openlib_books.parquet",
            PARQUET_DIR / "books.parquet",
            DATA_DIR / "openlib_books.parquet",
        ],
        "normalizer": normalize_openlib_book,
    },
}


def find_parquet(source: str) -> Optional[Path]:
    """Find the first existing parquet file for a source."""
    for path in PARQUET_MAP[source]["paths"]:
        if path.exists():
            return path
    return None


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────

async def main(args):
    console.rule("[bold cyan]🎬 Suggestify V2 — Data Loader[/bold cyan]")

    # ── Connect to PostgreSQL ─────────────────────────────
    console.print(f"\n📡 Connecting to PostgreSQL: [cyan]{DATABASE_URL}[/cyan]")
    try:
        pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
        console.print("[green]✅ Connected![/green]")
    except Exception as e:
        console.print(f"[red]✗ Cannot connect to PostgreSQL: {e}[/red]")
        console.print("[yellow]Make sure Docker is running: docker compose up -d postgres[/yellow]")
        sys.exit(1)

    # ── Drop & recreate if --drop flag ───────────────────
    if args.drop:
        console.print("\n[yellow]⚠  --drop flag: truncating items table...[/yellow]")
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE TABLE interactions, trending_items, recommendation_logs CASCADE")
            await conn.execute("TRUNCATE TABLE items CASCADE")
        console.print("[green]✅ Tables cleared[/green]")

    # ── Determine which sources to load ──────────────────
    sources_to_load = (
        [args.source] if args.source
        else ["movies", "tv", "anime", "books"]
    )

    results = {}
    start_total = time.time()

    for source in sources_to_load:
        console.print(f"\n{'─'*50}")
        parquet_path = find_parquet(source)

        if not parquet_path:
            console.print(f"[yellow]⚠  No parquet found for '{source}'[/yellow]")
            console.print(f"   Searched: {[str(p) for p in PARQUET_MAP[source]['paths']]}")
            results[source] = 0
            continue

        console.print(f"📂 Loading [bold]{source}[/bold] from: [dim]{parquet_path}[/dim]")
        t0 = time.time()

        try:
            df = pd.read_parquet(parquet_path)
            console.print(f"   Rows: [cyan]{len(df):,}[/cyan]  |  Columns: {list(df.columns[:6])}...")
        except Exception as e:
            console.print(f"[red]✗ Failed to read parquet: {e}[/red]")
            results[source] = 0
            continue

        normalizer = PARQUET_MAP[source]["normalizer"]
        count = await load_source(pool, df, normalizer, source, limit=args.limit)
        elapsed = time.time() - t0

        results[source] = count
        console.print(f"   ✅ Loaded [green]{count:,}[/green] items in {elapsed:.1f}s")

    # ── Summary table ─────────────────────────────────────
    total_elapsed = time.time() - start_total
    console.print(f"\n{'─'*50}")
    console.rule("[bold green]📊 Summary[/bold green]")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Source", style="cyan")
    table.add_column("Items Loaded", justify="right", style="green")

    grand_total = 0
    for source, count in results.items():
        table.add_row(source.title(), f"{count:,}")
        grand_total += count

    table.add_row("[bold]TOTAL[/bold]", f"[bold]{grand_total:,}[/bold]")
    console.print(table)

    # ── Verify DB count ───────────────────────────────────
    async with pool.acquire() as conn:
        db_count = await conn.fetchval("SELECT COUNT(*) FROM items")
        ct_counts = await conn.fetch(
            "SELECT content_type, COUNT(*) as cnt FROM items GROUP BY content_type ORDER BY cnt DESC"
        )

    console.print(f"\n🗄️  PostgreSQL total items: [bold green]{db_count:,}[/bold green]")
    for row in ct_counts:
        console.print(f"   {row['content_type']:12s}: {row['cnt']:,}")

    console.print(f"\n⏱️  Total time: {total_elapsed:.1f}s")
    console.print("\n[bold green]✅ Day 1 complete! Run Day 2 next: python scripts/import_imdb.py[/bold green]")

    await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Suggestify V2 — Data Loader")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Truncate items table before loading (for fresh reload)"
    )
    parser.add_argument(
        "--source",
        choices=["movies", "tv", "anime", "books"],
        help="Load only this source (default: all)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max rows per source (for testing)"
    )
    args = parser.parse_args()
    asyncio.run(main(args))