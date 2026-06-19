"""
fetch_new_tmdb.py — Suggestify V2
===================================
Fetches latest movies + TV shows from TMDB API and adds them to PostgreSQL.

Fetches:
  - Popular movies (page 1-20 = ~400 movies)
  - Now playing in theatres
  - Upcoming releases
  - Popular TV shows (page 1-20 = ~400 shows)
  - Currently airing TV shows
  - Top rated (movies + TV)

Usage:
  python scripts/fetch_new_tmdb.py
  python scripts/fetch_new_tmdb.py --pages 50   # fetch more
"""

import asyncio
import os
import time
from pathlib import Path

import httpx
import asyncpg
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
console = Console()

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE    = "https://api.themoviedb.org/3"
TMDB_IMG     = "https://image.tmdb.org/t/p"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://suggestify:suggestify_secret@localhost:5433/suggestify")

HEADERS = {
    "accept": "application/json",
}

# ─────────────────────────────────────────────────────────
# Genre ID → Name map (TMDB uses integer genre IDs)
# ─────────────────────────────────────────────────────────
MOVIE_GENRE_MAP = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
    80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
    14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
    9648: "Mystery", 10749: "Romance", 878: "Science Fiction",
    10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
}
TV_GENRE_MAP = {
    10759: "Action", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 10762: "Kids",
    9648: "Mystery", 10763: "News", 10764: "Reality", 10765: "Science Fiction",
    10766: "Soap", 10767: "Talk", 10768: "War", 37: "Western",
}

# ─────────────────────────────────────────────────────────
# TMDB endpoints to fetch
# ─────────────────────────────────────────────────────────
MOVIE_ENDPOINTS = [
    ("popular",     "/movie/popular"),
    ("now_playing", "/movie/now_playing"),
    ("upcoming",    "/movie/upcoming"),
    ("top_rated",   "/movie/top_rated"),
]

TV_ENDPOINTS = [
    ("popular",         "/tv/popular"),
    ("on_the_air",      "/tv/on_the_air"),
    ("airing_today",    "/tv/airing_today"),
    ("top_rated",       "/tv/top_rated"),
]

# ─────────────────────────────────────────────────────────
# Fetch pages from TMDB
# ─────────────────────────────────────────────────────────

async def fetch_pages(client: httpx.AsyncClient, endpoint: str, pages: int, label: str) -> list:
    results = []
    for page in range(1, pages + 1):
        try:
            r = await client.get(
                f"{TMDB_BASE}{endpoint}",
                params={"page": page, "language": "en-US", "api_key": TMDB_API_KEY},
                headers=HEADERS,
                timeout=10,
            )
            if r.status_code != 200:
                console.print(f"  [yellow]HTTP {r.status_code} on {endpoint} page {page}[/yellow]")
                break
            data = r.json()
            results.extend(data.get("results", []))
            total_pages = data.get("total_pages", 1)
            if page >= total_pages:
                break
        except Exception as e:
            console.print(f"  [red]Error fetching {label} page {page}: {e}[/red]")
            break
        await asyncio.sleep(0.1)  # be nice to TMDB rate limits

    console.print(f"  {label}: {len(results)} items fetched")
    return results


# ─────────────────────────────────────────────────────────
# Normalize TMDB movie result → items table row
# ─────────────────────────────────────────────────────────

def normalize_movie(r: dict) -> dict:
    genre_ids = r.get("genre_ids", [])
    genres    = [MOVIE_GENRE_MAP.get(gid, "") for gid in genre_ids if gid in MOVIE_GENRE_MAP]

    poster   = r.get("poster_path", "") or ""
    backdrop = r.get("backdrop_path", "") or ""

    return {
        "id":           f"tmdb_movie_{r['id']}",
        "title":        str(r.get("title", r.get("original_title", "Unknown"))),
        "content_type": "movie",
        "genres":       genres,
        "description":  str(r.get("overview", ""))[:2000],
        "release_year": int(str(r.get("release_date", "0"))[:4] or 0),
        "language":     r.get("original_language", "en"),
        "country":      "",
        "poster_url":   f"{TMDB_IMG}/w500{poster}"   if poster   else "",
        "backdrop_url": f"{TMDB_IMG}/w1280{backdrop}" if backdrop else "",
        "trailer_url":  "",
        "rating":       float(r.get("vote_average", 0) or 0),
        "vote_count":   int(r.get("vote_count", 0) or 0),
        "popularity":   float(r.get("popularity", 0) or 0),
        "external_id":  str(r["id"]),
    }


def normalize_tv(r: dict) -> dict:
    genre_ids = r.get("genre_ids", [])
    genres    = [TV_GENRE_MAP.get(gid, "") for gid in genre_ids if gid in TV_GENRE_MAP]

    poster   = r.get("poster_path", "") or ""
    backdrop = r.get("backdrop_path", "") or ""

    return {
        "id":           f"tmdb_tv_{r['id']}",
        "title":        str(r.get("name", r.get("original_name", "Unknown"))),
        "content_type": "tv",
        "genres":       genres,
        "description":  str(r.get("overview", ""))[:2000],
        "release_year": int(str(r.get("first_air_date", "0"))[:4] or 0),
        "language":     r.get("original_language", "en"),
        "country":      "",
        "poster_url":   f"{TMDB_IMG}/w500{poster}"   if poster   else "",
        "backdrop_url": f"{TMDB_IMG}/w1280{backdrop}" if backdrop else "",
        "trailer_url":  "",
        "rating":       float(r.get("vote_average", 0) or 0),
        "vote_count":   int(r.get("vote_count", 0) or 0),
        "popularity":   float(r.get("popularity", 0) or 0),
        "external_id":  str(r["id"]),
    }


# ─────────────────────────────────────────────────────────
# Upsert into PostgreSQL
# ─────────────────────────────────────────────────────────

INSERT_SQL = """
    INSERT INTO items (
        id, title, content_type, genres, description, release_year,
        language, country, poster_url, backdrop_url, trailer_url,
        rating, vote_count, popularity, external_id
    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
    ON CONFLICT (id) DO UPDATE SET
        title        = EXCLUDED.title,
        genres       = EXCLUDED.genres,
        description  = EXCLUDED.description,
        poster_url   = EXCLUDED.poster_url,
        backdrop_url = EXCLUDED.backdrop_url,
        rating       = EXCLUDED.rating,
        vote_count   = EXCLUDED.vote_count,
        popularity   = EXCLUDED.popularity,
        updated_at   = NOW()
"""

async def upsert_items(conn, items: list[dict]) -> int:
    rows = [(
        i["id"], i["title"], i["content_type"], i["genres"],
        i["description"], i["release_year"], i["language"], i["country"],
        i["poster_url"], i["backdrop_url"], i["trailer_url"],
        i["rating"], i["vote_count"], i["popularity"], i["external_id"],
    ) for i in items]
    await conn.executemany(INSERT_SQL, rows)
    return len(rows)


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────

async def main(pages: int):
    if not TMDB_API_KEY or TMDB_API_KEY == "your_tmdb_api_key_here":
        console.print("[red]TMDB_API_KEY not set in .env![/red]")
        console.print("Add: TMDB_API_KEY=your_key_here")
        return

    console.rule("[bold cyan]TMDB Fresh Data Fetch[/bold cyan]")
    console.print(f"Fetching {pages} pages per endpoint (~{pages*20} items each)\n")

    conn = await asyncpg.connect(DATABASE_URL)
    before = await conn.fetchval("SELECT COUNT(*) FROM items")
    console.print(f"Items in DB before: [cyan]{before:,}[/cyan]\n")

    new_movies = 0
    new_tv     = 0

    async with httpx.AsyncClient() as client:

        # ── Movies ───────────────────────────────────────
        console.print("[bold]Movies:[/bold]")
        all_movie_results = {}
        for label, endpoint in MOVIE_ENDPOINTS:
            results = await fetch_pages(client, endpoint, pages, label)
            for r in results:
                all_movie_results[r["id"]] = r  # dedupe by TMDB id

        movies = [normalize_movie(r) for r in all_movie_results.values()]
        movies = [m for m in movies if m["title"] and m["title"] != "Unknown"]
        new_movies = await upsert_items(conn, movies)
        console.print(f"  Upserted [green]{new_movies:,}[/green] movies\n")

        # ── TV Shows ─────────────────────────────────────
        console.print("[bold]TV Shows:[/bold]")
        all_tv_results = {}
        for label, endpoint in TV_ENDPOINTS:
            results = await fetch_pages(client, endpoint, pages, label)
            for r in results:
                all_tv_results[r["id"]] = r

        shows = [normalize_tv(r) for r in all_tv_results.values()]
        shows = [s for s in shows if s["title"] and s["title"] != "Unknown"]
        new_tv = await upsert_items(conn, shows)
        console.print(f"  Upserted [green]{new_tv:,}[/green] TV shows\n")

    after = await conn.fetchval("SELECT COUNT(*) FROM items")
    net_new = after - before

    # ── Summary ───────────────────────────────────────────
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")
    table.add_row("Movies upserted",   f"{new_movies:,}")
    table.add_row("TV shows upserted", f"{new_tv:,}")
    table.add_row("Items before",      f"{before:,}")
    table.add_row("Items after",       f"{after:,}")
    table.add_row("Net NEW items",     f"{net_new:,}")
    console.print(table)

    console.print(f"\n[bold green]Done! {net_new:,} new items added to your catalog.[/bold green]")
    console.print("Re-run Two-Tower training to embed the new items:")
    console.print("  python ml/two_tower/train.py --epochs 10")

    await conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=20,
                        help="Pages per endpoint (20 items/page). Default=20 → ~400 per endpoint")
    args = parser.parse_args()
    asyncio.run(main(args.pages))