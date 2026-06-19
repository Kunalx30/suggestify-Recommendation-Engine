# backend/services/two_tower.py

"""Two-tower user embedding service.

Loads the pretrained PyTorch user tower from model.pt and builds a 128-dim
user embedding on the fly using the user's features.
"""

from __future__ import annotations

import os
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

from .signals import UserFeatures

NUM_GENRES = 50
NUM_CONTENT_TYPES = 4
HIDDEN_DIM = 256
EMBEDDING_DIM = 128

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

# Singletons for model loading
_user_tower: Tower | None = None
_genre_to_idx: dict[str, int] | None = None
_content_type_map: dict[str, int] | None = None
_num_users: int | None = None

def load_user_tower() -> tuple[Tower, dict[str, int], dict[str, int], int]:
    """Load the Two-Tower model from disk and cache the user tower & metadata."""
    global _user_tower, _genre_to_idx, _content_type_map, _num_users
    if _user_tower is None:
        model_path = os.getenv("TWO_TOWER_MODEL_PATH", "ml/two_tower/model.pt")
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Two-tower model file not found at {model_path}")

        checkpoint = torch.load(model_path, map_location="cpu")
        _num_users = checkpoint["num_users"]
        num_train_items = checkpoint["num_train_items"]
        _genre_to_idx = checkpoint["genre_to_idx"]
        _content_type_map = checkpoint["content_type_map"]

        model = TwoTowerModel(_num_users, num_train_items)
        model.load_state_dict(checkpoint["model_state"])
        model.eval()
        _user_tower = model.user_tower
        
    return _user_tower, _genre_to_idx, _content_type_map, _num_users

def get_user_embedding(features: UserFeatures) -> np.ndarray:
    """Builds a user embedding on the fly using the user's features."""
    user_tower, genre_to_idx, content_type_map, num_users = load_user_tower()

    # 1. user_id -> hash to a training index (or 0 if unseen)
    user_idx = 0
    try:
        uid_int = int(features.user_id)
        if 0 <= uid_int < num_users:
            user_idx = uid_int + 1
    except ValueError:
        pass

    # 2. preferred content_type -> content_type_map integer (default to 0)
    type_idx = 0
    if features.content_type:
        type_idx = content_type_map.get(features.content_type.lower(), 0)

    # 3. preferred_genres -> 50-dim binary vector
    genre_vec = torch.zeros(NUM_GENRES, dtype=torch.float32)
    pref_genres = features.preferred_genres
    
    # Fallback to genre_boost if preferred_genres is empty
    if not pref_genres and features.genre_boost:
        sorted_boost = sorted(features.genre_boost.items(), key=lambda x: x[1], reverse=True)
        pref_genres = [g for g, score in sorted_boost if score > 0][:5]

    for g in (pref_genres or []):
        if g in genre_to_idx:
            genre_vec[genre_to_idx[g]] = 1.0

    # Convert to Tensors (batch size 1)
    ids_t = torch.tensor([user_idx], dtype=torch.long)
    types_t = torch.tensor([type_idx], dtype=torch.long)
    genres_t = genre_vec.unsqueeze(0)  # Shape [1, 50]

    with torch.no_grad():
        emb = user_tower(ids_t, types_t, genres_t)

    return emb.squeeze(0).numpy()
