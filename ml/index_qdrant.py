"""
index_qdrant.py — Suggestify V2
=================================
Uploads all 549K item embeddings into Qdrant for ANN search.

Steps:
  1. Load embeddings.npy (549553 x 128) + item_ids.npy
  2. Fetch item metadata from PostgreSQL (content_type, genres, language, rating, popularity)
  3. Create Qdrant collection with HNSW index, cosine distance
  4. Upload all vectors with payload (enables filtered search)
  5. Verify ANN speed < 10ms

Usage:
  python ml/index_qdrant.py
  python ml/index_qdrant.py --batch-size 2000
"""

import asyncio
import argparse
import os
import time
from pathlib import Path

import numpy as np
import asyncpg
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    HnswConfigDiff, OptimizersConfigDiff,
)
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn

load_dotenv()
console = Console()

DATABASE_URL     = os.getenv("DATABASE_URL", "postgresql://suggestify:suggestify_secret@localhost:5433/suggestify")
QDRANT_URL       = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME  = os.getenv("QDRANT_COLLECTION", "suggestify_items")
EMBEDDING_DIM    = 128

DATA_DIR  = Path("data")
EMBS_PATH = DATA_DIR / "embeddings.npy"
IDS_PATH  = DATA_DIR / "item_ids.npy"


# ─────────────────────────────────────────────────────────
# Fetch metadata from PostgreSQL
# ─────────────────────────────────────────────────────────

async def fetch_metadata(item_ids: list[str]) -> dict:
    """Fetch item metadata for payload. Returns dict: item_id -> metadata."""
    console.print("Fetching item metadata from PostgreSQL...")
    conn = await asyncpg.connect(DATABASE_URL)

    # Fetch in chunks to avoid huge IN clause
    metadata = {}
    chunk_size = 5000

    for i in range(0, len(item_ids), chunk_size):
        chunk = item_ids[i:i + chunk_size]
        rows = await conn.fetch("""
            SELECT id, content_type, genres, language, rating, popularity, vote_count,
                   release_year, poster_url, title
            FROM items
            WHERE id = ANY($1::text[])
        """, chunk)
        for r in rows:
            metadata[r["id"]] = {
                "item_id":      r["id"],
                "title":        str(r["title"] or ""),
                "content_type": str(r["content_type"] or "movie"),
                "genres":       list(r["genres"] or []),
                "language":     str(r["language"] or "en"),
                "rating":       float(r["rating"] or 0),
                "popularity":   float(r["popularity"] or 0),
                "vote_count":   int(r["vote_count"] or 0),
                "release_year": int(r["release_year"] or 0),
                "poster_url":   str(r["poster_url"] or ""),
            }

        if i % 50000 == 0 and i > 0:
            console.print(f"  ... fetched metadata for {i:,}/{len(item_ids):,}")

    await conn.close()
    console.print(f"  Got metadata for {len(metadata):,} items")
    return metadata


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────

async def main(args):
    console.rule("[bold cyan]Day 4 — Qdrant Vector Indexing[/bold cyan]")

    # ── Load embeddings ───────────────────────────────────
    console.print(f"\nLoading embeddings from {EMBS_PATH}...")
    if not EMBS_PATH.exists():
        console.print(f"[red]embeddings.npy not found! Run Day 3 first: python ml/two_tower/train.py[/red]")
        return
    if not IDS_PATH.exists():
        console.print(f"[red]item_ids.npy not found! Run Day 3 first.[/red]")
        return

    embeddings = np.load(EMBS_PATH).astype(np.float32)
    item_ids   = np.load(IDS_PATH, allow_pickle=True).tolist()
    num_items  = len(item_ids)

    console.print(f"  Embeddings shape: {embeddings.shape}")
    console.print(f"  Item IDs: {num_items:,}")
    assert embeddings.shape[0] == num_items, "Mismatch between embeddings and item_ids!"
    assert embeddings.shape[1] == EMBEDDING_DIM, f"Expected dim {EMBEDDING_DIM}, got {embeddings.shape[1]}"

    # ── Fetch metadata ────────────────────────────────────
    metadata = await fetch_metadata(item_ids)

    # ── Connect to Qdrant ─────────────────────────────────
    console.print(f"\nConnecting to Qdrant at {QDRANT_URL}...")
    client = QdrantClient(url=QDRANT_URL, timeout=60)
    try:
        info = client.get_collections()
        console.print(f"  Connected! Existing collections: {[c.name for c in info.collections]}")
    except Exception as e:
        console.print(f"[red]Cannot connect to Qdrant: {e}[/red]")
        console.print("Make sure Docker is running: docker compose up -d qdrant")
        return

    # ── Create / recreate collection ──────────────────────
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in existing:
        console.print(f"\n  Collection '{COLLECTION_NAME}' exists.")
        if args.recreate:
            console.print("  --recreate flag: deleting and recreating...")
            client.delete_collection(COLLECTION_NAME)
        else:
            console.print("  Skipping creation (use --recreate to rebuild from scratch)")

    if COLLECTION_NAME not in [c.name for c in client.get_collections().collections]:
        console.print(f"\nCreating collection '{COLLECTION_NAME}'...")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=EMBEDDING_DIM,
                distance=Distance.COSINE,
            ),
            hnsw_config=HnswConfigDiff(
                m=16,               # number of edges per node (higher = better recall, more RAM)
                ef_construct=100,   # construction time accuracy
                full_scan_threshold=10000,
            ),
            optimizers_config=OptimizersConfigDiff(
                indexing_threshold=20000,   # start indexing after 20K vectors
            ),
        )
        console.print(f"  Collection created with HNSW (m=16, ef_construct=100, cosine distance)")

    # ── Upload vectors ────────────────────────────────────
    console.print(f"\nUploading {num_items:,} vectors to Qdrant...")
    console.print(f"  Batch size: {args.batch_size}")

    t0 = time.time()
    uploaded = 0
    skipped  = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("Uploading"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
    ) as progress:
        task = progress.add_task("upload", total=num_items)

        for start in range(0, num_items, args.batch_size):
            end   = min(start + args.batch_size, num_items)
            batch_ids   = item_ids[start:end]
            batch_vecs  = embeddings[start:end]

            points = []
            for i, (iid, vec) in enumerate(zip(batch_ids, batch_vecs)):
                meta = metadata.get(iid)
                if meta is None:
                    skipped += 1
                    continue

                points.append(PointStruct(
                    id=start + i,          # integer ID for Qdrant
                    vector=vec.tolist(),
                    payload=meta,          # searchable metadata
                ))

            if points:
                client.upsert(
                    collection_name=COLLECTION_NAME,
                    points=points,
                    wait=False,            # async upload — faster
                )
                uploaded += len(points)

            progress.update(task, advance=end - start)

    elapsed = time.time() - t0
    console.print(f"\n  Uploaded: [green]{uploaded:,}[/green] vectors")
    console.print(f"  Skipped (no metadata): [yellow]{skipped:,}[/yellow]")
    console.print(f"  Time: {elapsed:.1f}s ({uploaded/elapsed:.0f} vectors/sec)")

    # ── Wait for indexing ─────────────────────────────────
    console.print("\nWaiting for HNSW index to build...")
    client.update_collection(
        collection_name=COLLECTION_NAME,
        optimizer_config=OptimizersConfigDiff(indexing_threshold=0),
    )
    time.sleep(3)

    # ── Verify collection ─────────────────────────────────
    info = client.get_collection(COLLECTION_NAME)
    console.print(f"  Vectors indexed: [green]{info.vectors_count:,}[/green]")
    console.print(f"  Points count:    [green]{info.points_count:,}[/green]")
    console.print(f"  Status:          {info.status}")

    # ── Speed test ────────────────────────────────────────
    console.print("\nRunning ANN speed test (10 queries)...")
    test_vec = embeddings[0].tolist()
    latencies = []

    for _ in range(10):
        t = time.time()
        results = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=test_vec,
            limit=20,
            with_payload=True,
        )
        latencies.append((time.time() - t) * 1000)

    avg_ms = sum(latencies) / len(latencies)
    p99_ms = sorted(latencies)[-1]

    console.print(f"  Avg latency: [green]{avg_ms:.1f}ms[/green]")
    console.print(f"  P99 latency: [green]{p99_ms:.1f}ms[/green]")
    console.print(f"  Target:      <10ms  {'✅' if avg_ms < 10 else '⚠ above target but ok for now'}")

    # ── Sample results ────────────────────────────────────
    console.print("\nSample ANN results for first item:")
    first_item = metadata.get(item_ids[0], {})
    console.print(f"  Query: [cyan]{first_item.get('title', '?')}[/cyan] ({first_item.get('content_type', '?')})")
    for r in results[:5]:
        p = r.payload
        console.print(f"  Score {r.score:.3f} | {p.get('title','?'):40s} | {p.get('content_type','?'):6s} | {p.get('genres',[][:2])}")

    # ── Summary table ─────────────────────────────────────
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")
    table.add_row("Total vectors",    f"{uploaded:,}")
    table.add_row("Collection",       COLLECTION_NAME)
    table.add_row("Dimensions",       str(EMBEDDING_DIM))
    table.add_row("Distance metric",  "Cosine")
    table.add_row("HNSW m",           "16")
    table.add_row("Avg ANN latency",  f"{avg_ms:.1f}ms")
    table.add_row("P99 ANN latency",  f"{p99_ms:.1f}ms")
    console.print(table)

    console.print(f"\n[bold green]Day 4 complete! Qdrant is ready for ANN search.[/bold green]")
    console.print("Next: build backend API — Day 5")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qdrant Vector Indexing")
    parser.add_argument("--batch-size", type=int, default=1000,
                        help="Vectors per upload batch (default: 1000)")
    parser.add_argument("--recreate",   action="store_true",
                        help="Delete and recreate collection from scratch")
    asyncio.run(main(parser.parse_args()))