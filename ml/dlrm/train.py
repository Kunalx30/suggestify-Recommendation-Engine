import argparse
import os
import time
import sys
import random
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
import asyncpg
import asyncio
from dotenv import load_dotenv
import mlflow
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

# Ensure the root path is in PYTHONPATH
sys.path.append(str(Path(__file__).resolve().parents[2]))

load_dotenv()
console = Console()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://suggestify:suggestify_secret@localhost:5433/suggestify")
DLRM_DIR = Path("ml/dlrm")
TWO_TOWER_PATH = Path("ml/two_tower/model.pt")
EMBEDDINGS_PATH = Path("data/embeddings.npy")
ITEM_IDS_PATH = Path("data/item_ids.npy")

EMBEDDING_DIM = 128
HIDDEN_DIM = 256
NUM_GENRES = 50
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
# Two-Tower Model Definition for user embeddings
# ─────────────────────────────────────────────────────────
class TwoTower_Tower(nn.Module):
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
        self.user_tower  = TwoTower_Tower(num_users + 1)
        self.item_tower  = TwoTower_Tower(num_train_items + 1)
        self.temperature = nn.Parameter(torch.tensor(0.07))

    def forward(self, uid, utype, ugenre, iid, itype, igenre):
        return self.user_tower(uid, utype, ugenre), self.item_tower(iid, itype, igenre)

# ─────────────────────────────────────────────────────────
# DLRM Model Definition
# ─────────────────────────────────────────────────────────
class DLRM(nn.Module):
    def __init__(self, num_users, num_items, num_content_types=4, num_genres=50, embedding_dim=128, dense_dim=5):
        super().__init__()
        # Sparse Feature Embeddings
        self.user_emb = nn.Embedding(num_users + 1, embedding_dim, padding_idx=0)
        self.item_emb = nn.Embedding(num_items + 1, embedding_dim, padding_idx=0)
        self.type_emb = nn.Embedding(num_content_types, embedding_dim)
        self.genre_emb = nn.Embedding(num_genres, embedding_dim)
        
        # Bottom MLP for dense features
        self.bottom_mlp = nn.Sequential(
            nn.Linear(dense_dim, HIDDEN_DIM),
            nn.ReLU(),
            nn.Linear(HIDDEN_DIM, embedding_dim),
            nn.ReLU()
        )
        
        # Interaction layer: dot product between all pairs of 5 vectors:
        # bottom_mlp output, user embedding, item embedding, type embedding, genre embedding.
        # Number of vectors = 5
        # Interacting pairs = 5 * 4 / 2 = 10
        # Concatenated with bottom_mlp output (128) -> 138 features
        
        # Top MLP to output binary classification score (logit)
        self.top_mlp = nn.Sequential(
            nn.Linear(embedding_dim + 10, HIDDEN_DIM),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(HIDDEN_DIM, 64),
            nn.ReLU(),
            nn.Linear(64, 1)
        )

    def forward(self, user_idx, item_idx, type_idx, genre_indices, dense_feats):
        # Embed sparse inputs
        u_e = self.user_emb(user_idx)       # [B, D]
        i_e = self.item_emb(item_idx)       # [B, D]
        t_e = self.type_emb(type_idx)       # [B, D]
        
        # Mean pool active genre embeddings
        genre_counts = genre_indices.sum(dim=1, keepdim=True)
        genre_counts = torch.clamp(genre_counts, min=1.0)
        g_e = torch.matmul(genre_indices, self.genre_emb.weight) / genre_counts # [B, D]
        
        # Dense input through bottom MLP
        dense_e = self.bottom_mlp(dense_feats) # [B, D]
        
        # Pairwise dot product interactions
        vectors = [dense_e, u_e, i_e, t_e, g_e]
        interactions = []
        for i in range(len(vectors)):
            for j in range(i + 1, len(vectors)):
                dot = torch.sum(vectors[i] * vectors[j], dim=1, keepdim=True) # [B, 1]
                interactions.append(dot)
                
        inter_feats = torch.cat(interactions, dim=1) # [B, 10]
        
        # Concatenate dense features with interaction features
        flat = torch.cat([dense_e, inter_feats], dim=1) # [B, D + 10]
        
        # Output binary logit
        logits = self.top_mlp(flat).squeeze(1) # [B]
        return logits

# ─────────────────────────────────────────────────────────
# Dataset
# ─────────────────────────────────────────────────────────
class DLRMDataset(Dataset):
    def __init__(self, samples):
        self.samples = samples

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s = self.samples[idx]
        return {
            "user_idx": torch.tensor(s["user_idx"], dtype=torch.long),
            "item_idx": torch.tensor(s["item_idx"], dtype=torch.long),
            "type_idx": torch.tensor(s["type_idx"], dtype=torch.long),
            "genre_vec": torch.tensor(s["genre_vec"], dtype=torch.float32),
            "dense_feats": torch.tensor(s["dense_feats"], dtype=torch.float32),
            "label": torch.tensor(s["label"], dtype=torch.float32)
        }

# Helpers for dense features
def _recency_score(release_year):
    if release_year is None:
        return 0.0
    current_year = 2026
    age = max(0, current_year - release_year)
    return np.exp(-age / 10.0)

def _popularity_score(vote_count):
    if not vote_count or vote_count <= 0:
        return 0.0
    return np.log1p(vote_count) / np.log1p(1_000_000)

async def main(args):
    console.print("[bold green]Starting DLRM training run...[/bold green]")
    
    # Load Two-Tower checkpoint
    if not TWO_TOWER_PATH.exists():
        console.print(f"[bold red]Two-Tower checkpoint not found at {TWO_TOWER_PATH}. Train it first.[/bold red]")
        sys.exit(1)
    
    tt_checkpoint = torch.load(TWO_TOWER_PATH, map_location="cpu")
    num_users = tt_checkpoint["num_users"]
    num_train_items = tt_checkpoint["num_train_items"]
    t_ids = tt_checkpoint["t_ids"]
    t_types = tt_checkpoint["t_types"]
    
    # Load embeddings to compute cos_sim
    if not EMBEDDINGS_PATH.exists() or not ITEM_IDS_PATH.exists():
        console.print("[bold red]Embeddings or item IDs files not found in data/. Run Two-Tower index generation first.[/bold red]")
        sys.exit(1)
        
    embeddings = np.load(EMBEDDINGS_PATH)
    item_ids_arr = np.load(ITEM_IDS_PATH)
    t_id_to_idx = {iid: idx for idx, iid in enumerate(t_ids)}
    db_id_to_arr_idx = {iid: idx for idx, iid in enumerate(item_ids_arr)}
    
    # Instantiate Two-Tower Model and load state dict
    tt_model = TwoTowerModel(num_users, num_train_items)
    tt_model.load_state_dict(tt_checkpoint["model_state"])
    tt_model.eval()
    
    # Connect to PostgreSQL to load items data
    console.print("Fetching items from PostgreSQL...")
    conn = await asyncpg.connect(DATABASE_URL)
    items_rows = await conn.fetch("SELECT id, genres, rating, vote_count, release_year, content_type FROM items")
    await conn.close()
    
    # Map items details
    item_details = {}
    for r in items_rows:
        item_details[r["id"]] = {
            "genres": r["genres"] or [],
            "rating": r["rating"] or 0.0,
            "vote_count": r["vote_count"] or 0,
            "release_year": r["release_year"],
            "content_type": r["content_type"] or "movie"
        }
        
    # Generate synthetic interactions matching Two-Tower structure
    console.print(f"Generating synthetic interactions for {args.num_users} users...")
    np.random.seed(42)
    random.seed(42)
    
    # We will compute user embeddings for all 5000 users
    user_embs = []
    user_prefs = []
    
    with torch.no_grad():
        for user_idx in range(args.num_users):
            n_prefs = np.random.randint(2, 4)
            pref_genres = np.random.choice(ALL_GENRES, size=n_prefs, replace=False).tolist()
            pref_type = np.random.choice(list(CONTENT_TYPE_MAP.keys())) if np.random.random() < 0.7 else "movie"
            
            # Map genres to vector
            genre_vec = torch.zeros(NUM_GENRES, dtype=torch.float32)
            for g in pref_genres:
                if g in GENRE_TO_IDX:
                    genre_vec[GENRE_TO_IDX[g]] = 1.0
                    
            ids_t = torch.tensor([user_idx + 1], dtype=torch.long)
            types_t = torch.tensor([CONTENT_TYPE_MAP.get(pref_type, 0)], dtype=torch.long)
            genres_t = genre_vec.unsqueeze(0)
            
            u_emb = tt_model.user_tower(ids_t, types_t, genres_t).squeeze(0).numpy()
            user_embs.append(u_emb)
            user_prefs.append((pref_genres, pref_type))
            
    # Sample interactions
    samples = []
    # Fetch subset of items that were mapped in Two-Tower model
    valid_train_items = [iid for iid in t_ids if iid in item_details and iid in db_id_to_arr_idx]
    N_items = len(valid_train_items)
    
    # Pre-extract attributes for vectorised weight computation
    ratings = np.array([item_details[iid]["rating"] for iid in valid_train_items], dtype=np.float32)
    vote_counts = np.array([item_details[iid]["vote_count"] for iid in valid_train_items], dtype=np.float32)
    content_types = np.array([item_details[iid]["content_type"] for iid in valid_train_items])
    total_genres_count = np.array([len(item_details[iid]["genres"]) for iid in valid_train_items], dtype=np.float32)
    
    # Base weights: rating and popularity scores
    base_weights = (ratings / 10.0) * (np.log1p(vote_counts) / np.log1p(100_000))
    
    # Build genres binary matrix: shape [N_items, 50]
    genres_matrix = np.zeros((N_items, NUM_GENRES), dtype=np.float32)
    for idx, iid in enumerate(valid_train_items):
        for g in item_details[iid]["genres"]:
            if g in GENRE_TO_IDX:
                genres_matrix[idx, GENRE_TO_IDX[g]] = 1.0
                
    console.print(f"Sampling positive and negative pairs ({args.num_users} users x {args.interactions_per_user} positive/negative)...")
    for user_idx in range(args.num_users):
        u_emb = user_embs[user_idx]
        pref_genres, pref_type = user_prefs[user_idx]
        user_pref_set = set(pref_genres)
        
        # Build preferred genres vector
        pref_genres_vec = np.zeros(NUM_GENRES, dtype=np.float32)
        for g in pref_genres:
            if g in GENRE_TO_IDX:
                pref_genres_vec[GENRE_TO_IDX[g]] = 1.0
                
        # Count genre match
        genre_matches = np.dot(genres_matrix, pref_genres_vec)
        # weight multiplier: 1.5 for matches, 1.0 for others
        g_match_arr = 0.5 * genre_matches + total_genres_count
        
        # content type match multiplier
        t_match_arr = np.where(content_types == pref_type, 1.3, 1.0)
        
        # Compute final sampling weights
        item_scores = g_match_arr * t_match_arr * base_weights
        item_scores = item_scores / (item_scores.sum() + 1e-9)
        
        # Sample positive items
        pos_sampled = np.random.choice(valid_train_items, size=args.interactions_per_user, replace=False, p=item_scores)
        pos_set = set(pos_sampled)
        
        # Sample negative items efficiently
        neg_sampled = []
        while len(neg_sampled) < args.interactions_per_user:
            candidate_neg = random.choice(valid_train_items)
            if candidate_neg not in pos_set:
                neg_sampled.append(candidate_neg)
        
        for pos_id, neg_id in zip(pos_sampled, neg_sampled):
            # Pos Sample
            det_p = item_details[pos_id]
            idx_p = t_id_to_idx[pos_id]
            arr_idx_p = db_id_to_arr_idx[pos_id]
            cos_p = float(np.dot(u_emb, embeddings[arr_idx_p]))
            genre_match_p = sum(1.0 for g in det_p["genres"] if g in user_pref_set)
            norm_genre_p = min(1.0, genre_match_p / 5.0)
            
            genre_vec_p = np.zeros(NUM_GENRES, dtype=np.float32)
            for g in det_p["genres"]:
                if g in GENRE_TO_IDX:
                    genre_vec_p[GENRE_TO_IDX[g]] = 1.0
                    
            samples.append({
                "user_idx": user_idx + 1,
                "item_idx": idx_p + 1,
                "type_idx": CONTENT_TYPE_MAP.get(det_p["content_type"], 0),
                "genre_vec": genre_vec_p,
                "dense_feats": [cos_p, det_p["rating"]/10.0, norm_genre_p, _recency_score(det_p["release_year"]), _popularity_score(det_p["vote_count"])],
                "label": 1.0
            })
            
            # Neg Sample
            det_n = item_details[neg_id]
            idx_n = t_id_to_idx[neg_id]
            arr_idx_n = db_id_to_arr_idx[neg_id]
            cos_n = float(np.dot(u_emb, embeddings[arr_idx_n]))
            genre_match_n = sum(1.0 for g in det_n["genres"] if g in user_pref_set)
            norm_genre_n = min(1.0, genre_match_n / 5.0)
            
            genre_vec_n = np.zeros(NUM_GENRES, dtype=np.float32)
            for g in det_n["genres"]:
                if g in GENRE_TO_IDX:
                    genre_vec_n[GENRE_TO_IDX[g]] = 1.0
                    
            samples.append({
                "user_idx": user_idx + 1,
                "item_idx": idx_n + 1,
                "type_idx": CONTENT_TYPE_MAP.get(det_n["content_type"], 0),
                "genre_vec": genre_vec_n,
                "dense_feats": [cos_n, det_n["rating"]/10.0, norm_genre_n, _recency_score(det_n["release_year"]), _popularity_score(det_n["vote_count"])],
                "label": 0.0
            })
            
    # Train / Val Split
    random.shuffle(samples)
    split = int(len(samples) * 0.9)
    train_samples = samples[:split]
    val_samples = samples[split:]
    
    console.print(f"Total samples: {len(samples):,} | Train: {len(train_samples):,} | Val: {len(val_samples):,}")
    
    train_dataset = DLRMDataset(train_samples)
    val_dataset = DLRMDataset(val_samples)
    
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False)
    
    # Initialize MLflow
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"))
    mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", "suggestify_v2"))
    
    with mlflow.start_run() as run:
        mlflow.log_params({
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "learning_rate": args.lr,
            "embedding_dim": EMBEDDING_DIM,
            "num_users": num_users,
            "num_train_items": num_train_items,
            "num_samples": len(samples)
        })
        
        # Instantiate Model
        model = DLRM(num_users=num_users, num_items=num_train_items)
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
        criterion = nn.BCEWithLogitsLoss()
        
        best_val_loss = float("inf")
        DLRM_DIR.mkdir(parents=True, exist_ok=True)
        
        t_start = time.time()
        for epoch in range(1, args.epochs + 1):
            model.train()
            total_loss = 0.0
            correct = 0
            total = 0
            
            with Progress(SpinnerColumn(), TextColumn(f"Epoch {epoch}/{args.epochs}"),
                          BarColumn(), TextColumn("{task.percentage:>3.0f}%"),
                          TextColumn("loss={task.fields[loss]:.4f}"), TimeElapsedColumn()) as prog:
                task = prog.add_task("t", total=len(train_loader), loss=0.0)
                for b in train_loader:
                    optimizer.zero_grad()
                    logits = model(b["user_idx"], b["item_idx"], b["type_idx"], b["genre_vec"], b["dense_feats"])
                    loss = criterion(logits, b["label"])
                    loss.backward()
                    optimizer.step()
                    
                    total_loss += loss.item()
                    
                    # Compute train accuracy
                    preds = (torch.sigmoid(logits) >= 0.5).float()
                    correct += (preds == b["label"]).sum().item()
                    total += b["label"].size(0)
                    
                    prog.update(task, advance=1, loss=loss.item())
                    
            train_avg_loss = total_loss / len(train_loader)
            train_acc = correct / total
            
            # Validation
            model.eval()
            val_loss = 0.0
            val_correct = 0
            val_total = 0
            with torch.no_grad():
                for b in val_loader:
                    logits = model(b["user_idx"], b["item_idx"], b["type_idx"], b["genre_vec"], b["dense_feats"])
                    loss = criterion(logits, b["label"])
                    val_loss += loss.item()
                    preds = (torch.sigmoid(logits) >= 0.5).float()
                    val_correct += (preds == b["label"]).sum().item()
                    val_total += b["label"].size(0)
                    
            val_avg_loss = val_loss / len(val_loader)
            val_acc = val_correct / val_total
            
            console.print(f"  Epoch {epoch:2d} | Train Loss: {train_avg_loss:.4f} | Train Acc: {train_acc:.4f} | Val Loss: {val_avg_loss:.4f} | Val Acc: {val_acc:.4f}")
            
            # Log metrics to MLflow
            mlflow.log_metrics({
                "train_loss": train_avg_loss,
                "train_accuracy": train_acc,
                "val_loss": val_avg_loss,
                "val_accuracy": val_acc
            }, step=epoch)
            
            if val_avg_loss < best_val_loss:
                best_val_loss = val_avg_loss
                torch.save({
                    "model_state": model.state_dict(),
                    "num_users": num_users,
                    "num_items": num_train_items,
                    "embedding_dim": EMBEDDING_DIM,
                    "genre_to_idx": GENRE_TO_IDX,
                    "content_type_map": CONTENT_TYPE_MAP
                }, DLRM_DIR / "model.pt")
                console.print(f"  Saved best model checkpoint (val_loss={best_val_loss:.4f})")
                
        t_duration = time.time() - t_start
        console.print(f"\nTraining completed in {t_duration:.1f}s. Best Val Loss: {best_val_loss:.4f}")
        mlflow.log_metric("training_duration_seconds", t_duration)
        mlflow.log_metric("best_val_loss", best_val_loss)
        
        # Log artifacts/weights in MLflow
        mlflow.log_artifact(str(DLRM_DIR / "model.pt"))
        print(f"MLflow Run ID: {run.info.run_id}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--num-users", type=int, default=5000)
    parser.add_argument("--interactions-per-user", type=int, default=30) # 5000 x 30 x 2 = 300,000 samples
    asyncio.run(main(parser.parse_args()))
