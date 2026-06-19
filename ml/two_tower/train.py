"""
two_tower/train.py — Suggestify V2 (CPU version)
"""

import argparse
import os
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
import asyncpg
import asyncio
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

load_dotenv()
console = Console()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://suggestify:suggestify_secret@localhost:5433/suggestify")
MODEL_DIR = Path("ml/two_tower")
DATA_DIR  = Path("data")

EMBEDDING_DIM     = 128
HIDDEN_DIM        = 256
NUM_GENRES        = 50
NUM_CONTENT_TYPES = 4

CONTENT_TYPE_MAP = {"movie": 0, "tv": 1, "anime": 2, "book": 3}

ALL_GENRES = [
    "Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary",
    "Drama", "Fantasy", "Horror", "Music", "Mystery", "Romance",
    "Science Fiction", "Thriller", "War", "Western", "Family", "History",
    "Biography", "Sport", "Sci-Fi", "Award Winning", "Suspense", "Supernatural",
    "Slice of Life", "Mecha", "Psychological", "Shounen", "Seinen", "Isekai",
    "Fiction", "Nonfiction", "Self-Help", "Literary Fiction", "Classic",
    "Graphic Novel", "Young Adult", "Children", "Poetry", "Short Stories",
    "Reality", "Talk", "News", "Game Show", "Variety", "Superhero",
    "Anime", "K-Drama", "Period", "Political"
]
GENRE_TO_IDX = {g: i for i, g in enumerate(ALL_GENRES)}


# ─────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────

async def fetch_data():
    console.print("Fetching items from PostgreSQL...")
    conn = await asyncpg.connect(DATABASE_URL)

    # ALL items — for embedding generation
    all_items = await conn.fetch(
        "SELECT id, content_type, genres FROM items ORDER BY id"
    )

    # High-quality subset — for training interactions
    train_items = await conn.fetch("""
        SELECT id, content_type, genres, rating, popularity, vote_count
        FROM items
        WHERE rating > 0 AND vote_count > 10
        ORDER BY popularity DESC
        LIMIT 100000
    """)
    await conn.close()
    console.print(f"   {len(all_items):,} total items | {len(train_items):,} for training")
    return list(all_items), list(train_items)


def generate_synthetic_interactions(train_items, num_users=5000, interactions_per_user=20):
    console.print(f"Generating synthetic interactions ({num_users} users x {interactions_per_user} items)...")

    t_ids    = [str(r["id"])           for r in train_items]
    t_types  = [str(r["content_type"]) for r in train_items]
    t_genres = [list(r["genres"] or []) for r in train_items]
    ratings  = np.array([float(r["rating"])     for r in train_items])
    pop      = np.array([float(r["popularity"]) for r in train_items])

    weights = (ratings / ratings.max()) * (np.log1p(pop) / np.log1p(pop.max()))
    weights = weights / weights.sum()

    interactions = []
    np.random.seed(42)

    for user_idx in range(num_users):
        n_prefs    = np.random.randint(2, 4)
        pref_genre = np.random.choice(ALL_GENRES, size=n_prefs, replace=False).tolist()
        pref_type  = np.random.choice(list(CONTENT_TYPE_MAP.keys())) if np.random.random() < 0.7 else None

        gb = np.array([1.5 if any(g in pref_genre for g in gs) else 1.0 for gs in t_genres])
        tb = np.array([1.3 if pref_type and tp == pref_type else 1.0 for tp in t_types])
        uw = weights * gb * tb
        uw = uw / uw.sum()

        for idx in np.random.choice(len(t_ids), size=min(interactions_per_user, len(t_ids)), replace=False, p=uw):
            interactions.append({
                "user_id":         user_idx,
                "item_idx":        int(idx),
                "preferred_genres": pref_genre,
                "preferred_type":  pref_type or "movie",
            })

    console.print(f"   Generated {len(interactions):,} interactions")
    return interactions, t_ids, t_types, t_genres


# ─────────────────────────────────────────────────────────
# Dataset
# ─────────────────────────────────────────────────────────

class InteractionDataset(Dataset):
    def __init__(self, interactions, t_types, t_genres):
        self.data     = interactions
        self.t_types  = t_types
        self.t_genres = t_genres

    def __len__(self):
        return len(self.data)

    def _gvec(self, genres):
        v = torch.zeros(NUM_GENRES, dtype=torch.float32)
        for g in (genres or []):
            if g in GENRE_TO_IDX:
                v[GENRE_TO_IDX[g]] = 1.0
        return v

    def __getitem__(self, idx):
        d       = self.data[idx]
        iidx    = d["item_idx"]
        return {
            "user_id":     torch.tensor(d["user_id"],                              dtype=torch.long),
            "user_type":   torch.tensor(CONTENT_TYPE_MAP.get(d["preferred_type"],0), dtype=torch.long),
            "user_genres": self._gvec(d["preferred_genres"]),
            "item_id":     torch.tensor(iidx,                                      dtype=torch.long),
            "item_type":   torch.tensor(CONTENT_TYPE_MAP.get(self.t_types[iidx],0), dtype=torch.long),
            "item_genres": self._gvec(self.t_genres[iidx]),
        }


# ─────────────────────────────────────────────────────────
# Model
# ─────────────────────────────────────────────────────────

class Tower(nn.Module):
    def __init__(self, num_ids, id_dim=64):
        super().__init__()
        self.id_emb   = nn.Embedding(num_ids, id_dim, padding_idx=0)
        self.type_emb = nn.Embedding(NUM_CONTENT_TYPES, 16)
        in_dim = id_dim + 16 + NUM_GENRES
        self.net = nn.Sequential(
            nn.Linear(in_dim, HIDDEN_DIM),
            nn.LayerNorm(HIDDEN_DIM),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(HIDDEN_DIM, EMBEDDING_DIM),
        )

    def forward(self, ids, types, genres):
        x = torch.cat([self.id_emb(ids), self.type_emb(types), genres], dim=-1)
        return F.normalize(self.net(x), dim=-1)


class TwoTowerModel(nn.Module):
    def __init__(self, num_users, num_train_items):
        super().__init__()
        self.user_tower  = Tower(num_users + 1)
        self.item_tower  = Tower(num_train_items + 1)
        self.temperature = nn.Parameter(torch.tensor(0.07))

    def forward(self, uid, utype, ugenre, iid, itype, igenre):
        return self.user_tower(uid, utype, ugenre), self.item_tower(iid, itype, igenre)

    def loss(self, ue, ie):
        t      = torch.clamp(self.temperature, 0.01, 1.0)
        logits = torch.matmul(ue, ie.T) / t
        labels = torch.arange(len(ue))
        return (F.cross_entropy(logits, labels) + F.cross_entropy(logits.T, labels)) / 2


# ─────────────────────────────────────────────────────────
# Train
# ─────────────────────────────────────────────────────────

def train(args):
    console.print("\nDevice: CPU")

    all_items, train_items = asyncio.run(fetch_data())

    interactions, t_ids, t_types, t_genres = generate_synthetic_interactions(
        train_items, args.num_users, args.interactions_per_user
    )

    num_users       = args.num_users
    num_train_items = len(t_ids)

    # ── All items data (for embedding generation) ─────────
    all_ids    = [str(r["id"])            for r in all_items]
    all_types  = [str(r["content_type"])  for r in all_items]
    all_genres = [list(r["genres"] or []) for r in all_items]
    num_all    = len(all_ids)

    console.print(f"\nTraining: {num_users} users | {num_train_items} train items | {len(interactions)} interactions")
    console.print(f"Embedding generation: {num_all} items")

    dataset = InteractionDataset(interactions, t_types, t_genres)
    loader  = DataLoader(dataset, batch_size=args.batch_size, shuffle=True, num_workers=0, pin_memory=False)

    model     = TwoTowerModel(num_users, num_train_items)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    console.print(f"Parameters: {sum(p.numel() for p in model.parameters()):,}\n")

    best_loss = float("inf")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        t0 = time.time()

        with Progress(SpinnerColumn(), TextColumn(f"Epoch {epoch}/{args.epochs}"),
                      BarColumn(), TextColumn("{task.percentage:>3.0f}%"),
                      TextColumn("loss={task.fields[loss]:.4f}"), TimeElapsedColumn()) as prog:
            task = prog.add_task("t", total=len(loader), loss=0.0)
            for b in loader:
                optimizer.zero_grad()
                ue, ie = model(b["user_id"], b["user_type"], b["user_genres"],
                               b["item_id"],  b["item_type"],  b["item_genres"])
                l = model.loss(ue, ie)
                l.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                total_loss += l.item()
                prog.update(task, advance=1, loss=l.item())

        scheduler.step()
        avg = total_loss / len(loader)
        console.print(f"  Epoch {epoch:2d} | Loss: {avg:.4f} | {time.time()-t0:.1f}s | LR: {scheduler.get_last_lr()[0]:.6f}")

        if avg < best_loss:
            best_loss = avg
            torch.save({
                "model_state": model.state_dict(),
                "num_users": num_users, "num_train_items": num_train_items,
                "t_ids": t_ids, "t_types": t_types,
                "embedding_dim": EMBEDDING_DIM,
                "genre_to_idx": GENRE_TO_IDX, "content_type_map": CONTENT_TYPE_MAP,
            }, MODEL_DIR / "model.pt")
            console.print(f"  Saved model (loss={best_loss:.4f})")

    # ── Generate embeddings for ALL items ─────────────────
    console.print(f"\nGenerating embeddings for {num_all:,} items...")
    model.eval()

    # Build t_id → training index lookup
    t_id_to_idx = {iid: i for i, iid in enumerate(t_ids)}

    # Build tensors from all_ids/all_types/all_genres directly
    id_indices  = torch.tensor([t_id_to_idx.get(iid, 0) for iid in all_ids], dtype=torch.long)
    type_tensor = torch.tensor([CONTENT_TYPE_MAP.get(t, 0) for t in all_types], dtype=torch.long)

    genre_rows = []
    for g in all_genres:
        row = torch.zeros(NUM_GENRES, dtype=torch.float32)
        for genre in g:
            if genre in GENRE_TO_IDX:
                row[GENRE_TO_IDX[genre]] = 1.0
        genre_rows.append(row)
    genre_tensor = torch.stack(genre_rows)

    console.print(f"  id_indices={id_indices.shape} type={type_tensor.shape} genre={genre_tensor.shape}")

    embeddings = []
    BS = 1024
    with torch.no_grad():
        for s in range(0, num_all, BS):
            e = min(s + BS, num_all)
            emb = model.item_tower(id_indices[s:e], type_tensor[s:e], genre_tensor[s:e])
            embeddings.append(emb.numpy())
            if s % 100000 == 0 and s > 0:
                console.print(f"  ... {s:,}/{num_all:,}")

    embeddings = np.vstack(embeddings)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    np.save(DATA_DIR / "embeddings.npy", embeddings)
    np.save(DATA_DIR / "item_ids.npy",   np.array(all_ids))

    console.print(f"  Saved embeddings: {embeddings.shape} -> data/embeddings.npy")
    console.print(f"  Saved item IDs   -> data/item_ids.npy")
    console.print(f"\nDay 3 complete! Next: python ml/index_qdrant.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs",                type=int,   default=10)
    parser.add_argument("--batch-size",            type=int,   default=512)
    parser.add_argument("--lr",                    type=float, default=1e-3)
    parser.add_argument("--num-users",             type=int,   default=5000)
    parser.add_argument("--interactions-per-user", type=int,   default=20)
    train(parser.parse_args())
    