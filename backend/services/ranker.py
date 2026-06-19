# backend/services/ranker.py

"""Ranking and diversification service.

Applies custom score weighting and Maximal Marginal Relevance (MMR)
for candidate diversification.
"""

from __future__ import annotations

import os
import math
import numpy as np
from typing import List, Dict, Any
from collections import Counter
import torch
import torch.nn as nn
import torch.nn.functional as F

from .signals import UserFeatures
from ..core.config import settings

def _recency_score(release_year: int | None) -> float:
    if release_year is None:
        return 0.0
    current_year = 2026
    age = max(0, current_year - release_year)
    # Exponential decay with half-life of ~10 years
    return math.exp(-age / 10.0)

def _popularity_score(vote_count: int | None) -> float:
    if not vote_count or vote_count <= 0:
        return 0.0
    # Log-scaled popularity normalized against 1,000,000 votes
    return math.log1p(vote_count) / math.log1p(1_000_000)

def _item_similarity(a: dict, b: dict) -> float:
    emb_a = a.get("embedding")
    emb_b = b.get("embedding")
    if emb_a is not None and emb_b is not None:
        try:
            arr_a = np.array(emb_a, dtype=np.float32)
            arr_b = np.array(emb_b, dtype=np.float32)
            norm_a = np.linalg.norm(arr_a)
            norm_b = np.linalg.norm(arr_b)
            if norm_a > 0 and norm_b > 0:
                return float(np.dot(arr_a, arr_b) / (norm_a * norm_b))
        except Exception:
            pass

    # Fallback to Jaccard similarity of genres
    genres_a = set(a.get("genres") or [])
    genres_b = set(b.get("genres") or [])
    if not genres_a or not genres_b:
        return 0.0
    return len(genres_a & genres_b) / len(genres_a | genres_b)

# ─────────────────────────────────────────────────────────
# DLRM Model Definition
# ─────────────────────────────────────────────────────────
class DLRM(nn.Module):
    def __init__(self, num_users, num_items, num_content_types=4, num_genres=50, embedding_dim=128, dense_dim=5):
        super().__init__()
        self.user_emb = nn.Embedding(num_users + 1, embedding_dim, padding_idx=0)
        self.item_emb = nn.Embedding(num_items + 1, embedding_dim, padding_idx=0)
        self.type_emb = nn.Embedding(num_content_types, embedding_dim)
        self.genre_emb = nn.Embedding(num_genres, embedding_dim)
        
        self.bottom_mlp = nn.Sequential(
            nn.Linear(dense_dim, HIDDEN_DIM := 256),
            nn.ReLU(),
            nn.Linear(HIDDEN_DIM, embedding_dim),
            nn.ReLU()
        )
        
        self.top_mlp = nn.Sequential(
            nn.Linear(embedding_dim + 10, HIDDEN_DIM),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(HIDDEN_DIM, 64),
            nn.ReLU(),
            nn.Linear(64, 1)
        )

    def forward(self, user_idx, item_idx, type_idx, genre_indices, dense_feats):
        u_e = self.user_emb(user_idx)
        i_e = self.item_emb(item_idx)
        t_e = self.type_emb(type_idx)
        
        genre_counts = genre_indices.sum(dim=1, keepdim=True)
        genre_counts = torch.clamp(genre_counts, min=1.0)
        g_e = torch.matmul(genre_indices, self.genre_emb.weight) / genre_counts
        
        dense_e = self.bottom_mlp(dense_feats)
        
        vectors = [dense_e, u_e, i_e, t_e, g_e]
        interactions = []
        for i in range(len(vectors)):
            for j in range(i + 1, len(vectors)):
                dot = torch.sum(vectors[i] * vectors[j], dim=1, keepdim=True)
                interactions.append(dot)
                
        inter_feats = torch.cat(interactions, dim=1)
        flat = torch.cat([dense_e, inter_feats], dim=1)
        logits = self.top_mlp(flat).squeeze(1)
        return logits

_dlrm_model: DLRM | None = None
_dlrm_mappings: dict | None = None

def load_dlrm_model() -> tuple[DLRM | None, dict | None]:
    global _dlrm_model, _dlrm_mappings
    if _dlrm_model is None:
        model_path = settings.DLRM_MODEL_PATH
        if not os.path.exists(model_path):
            print(f"[WARN] DLRM checkpoint not found at {model_path}. Fallback to heuristic-only.")
            return None, None
        try:
            checkpoint = torch.load(model_path, map_location="cpu")
            num_users = checkpoint["num_users"]
            num_items = checkpoint["num_items"]
            model = DLRM(num_users=num_users, num_items=num_items)
            model.load_state_dict(checkpoint["model_state"])
            model.eval()
            
            _dlrm_model = model
            _dlrm_mappings = {
                "genre_to_idx": checkpoint["genre_to_idx"],
                "content_type_map": checkpoint["content_type_map"],
                "t_id_to_idx": {}
            }
            # Load t_ids from Two-Tower model checkpoint metadata
            tt_path = "ml/two_tower/model.pt"
            if os.path.exists(tt_path):
                tt_checkpoint = torch.load(tt_path, map_location="cpu")
                t_ids = tt_checkpoint.get("t_ids") or []
                _dlrm_mappings["t_id_to_idx"] = {iid: idx for idx, iid in enumerate(t_ids)}
        except Exception as e:
            print(f"[WARN] Failed to load DLRM checkpoint: {e}. Fallback to heuristic-only.")
            _dlrm_model = None
            _dlrm_mappings = None
    return _dlrm_model, _dlrm_mappings

def rerank(
    candidates: List[Dict[str, Any]],
    features: UserFeatures,
    limit: int = 20,
    lambda_mmr: float | None = None
) -> List[Dict[str, Any]]:
    """Rerank candidates based on scoring formula and apply MMR diversification.

    Scoring formula:
    score = 0.4 * cosine_similarity + 0.2 * normalized_rating + 0.2 * genre_match_score
            + 0.1 * recency_score + 0.1 * popularity_score
    """
    # Load DLRM model and batch compute inference scores if available
    dlrm_scores = None
    model, mappings = load_dlrm_model()
    if model is not None and mappings is not None and len(candidates) > 0:
        try:
            user_idx = 0
            try:
                uid_int = int(features.user_id)
                num_users = model.user_emb.num_embeddings - 1
                if 0 <= uid_int < num_users:
                    user_idx = uid_int + 1
            except ValueError:
                pass
                
            B = len(candidates)
            user_indices = torch.tensor([user_idx] * B, dtype=torch.long)
            
            t_id_to_idx = mappings.get("t_id_to_idx", {})
            item_indices = torch.tensor([t_id_to_idx.get(c["item_id"], -1) + 1 for c in candidates], dtype=torch.long)
            
            content_type_map = mappings.get("content_type_map", {})
            type_indices = torch.tensor([content_type_map.get(c.get("content_type", "").lower(), 0) for c in candidates], dtype=torch.long)
            
            genre_to_idx = mappings.get("genre_to_idx", {})
            genre_tensors = torch.zeros(B, 50, dtype=torch.float32)
            for i, c in enumerate(candidates):
                for g in c.get("genres") or []:
                    if g in genre_to_idx:
                        genre_tensors[i, genre_to_idx[g]] = 1.0
                        
            dense_list = []
            for c in candidates:
                cos_sim = float(c.get("qdrant_score", 0.0))
                rating = float(c.get("rating") or 0.0)
                genres = c.get("genres") or []
                release_year = c.get("release_year")
                vote_count = int(c.get("vote_count") or 0)
                
                norm_rating = rating / 10.0
                match_score = sum(features.genre_boost.get(g, 0.0) for g in genres)
                norm_genre_match = min(1.0, match_score / 5.0) if match_score > 0 else 0.0
                r_score = _recency_score(release_year)
                p_score = _popularity_score(vote_count)
                
                dense_list.append([cos_sim, norm_rating, norm_genre_match, r_score, p_score])
                
            dense_tensors = torch.tensor(dense_list, dtype=torch.float32)
            
            with torch.no_grad():
                logits = model(user_indices, item_indices, type_indices, genre_tensors, dense_tensors)
                dlrm_scores = torch.sigmoid(logits).tolist()
        except Exception as e:
            print(f"[WARN] DLRM inference failed: {e}. Falling back to heuristic-only.")
            dlrm_scores = None

    scored_candidates = []
    for idx, cand in enumerate(candidates):
        cos_sim = float(cand.get("qdrant_score", 0.0))
        rating = float(cand.get("rating") or 0.0)
        genres = cand.get("genres") or []
        release_year = cand.get("release_year")
        vote_count = int(cand.get("vote_count") or 0)

        # Compute heuristic parameters
        norm_rating = rating / 10.0
        match_score = sum(features.genre_boost.get(g, 0.0) for g in genres)
        norm_genre_match = min(1.0, match_score / 5.0) if match_score > 0 else 0.0
        r_score = _recency_score(release_year)
        p_score = _popularity_score(vote_count)

        heuristic_score = (
            0.4 * cos_sim
            + 0.2 * norm_rating
            + 0.2 * norm_genre_match
            + 0.1 * r_score
            + 0.1 * p_score
        )
        
        # Blended score with DLRM
        if dlrm_scores is not None:
            dlrm_score = dlrm_scores[idx]
            alpha = settings.DLRM_WEIGHT
            score = alpha * dlrm_score + (1.0 - alpha) * heuristic_score
        else:
            dlrm_score = None
            score = heuristic_score
            
        cand_copy = dict(cand)
        cand_copy["rerank_score"] = score
        cand_copy["heuristic_score"] = heuristic_score
        if dlrm_score is not None:
            cand_copy["dlrm_score"] = dlrm_score
            
        scored_candidates.append(cand_copy)

    # Sort by score descending
    scored_candidates.sort(key=lambda x: x["rerank_score"], reverse=True)

    if not scored_candidates:
        return []

    if lambda_mmr is None:
        lambda_mmr = settings.MMR_LAMBDA

    max_genre_cap = settings.MAX_ITEMS_PER_GENRE

    # Track how many items of each genre we've selected so far
    genre_counts: Counter[str] = Counter()
    first_item = scored_candidates[0]
    for g in first_item.get("genres") or []:
        genre_counts[g] += 1

    # MMR Selection Loop
    selected = [first_item]
    remaining = scored_candidates[1:]

    while len(selected) < limit and remaining:
        best_mmr = -99999.0
        best_cand = None

        # Pass 1: Try to pick the best candidate that respects the genre cap
        for cand in remaining:
            cand_genres = cand.get("genres") or []
            if any(genre_counts[g] >= max_genre_cap for g in cand_genres):
                continue

            relevance = cand["rerank_score"]
            max_sim = 0.0
            for sel in selected:
                sim = _item_similarity(cand, sel)
                if sim > max_sim:
                    max_sim = sim

            mmr_val = lambda_mmr * relevance - (1.0 - lambda_mmr) * max_sim
            if mmr_val > best_mmr:
                best_mmr = mmr_val
                best_cand = cand

        # Pass 2: If all candidates violate the genre cap, fall back to general MMR selection
        if best_cand is None:
            for cand in remaining:
                relevance = cand["rerank_score"]
                max_sim = 0.0
                for sel in selected:
                    sim = _item_similarity(cand, sel)
                    if sim > max_sim:
                        max_sim = sim

                mmr_val = lambda_mmr * relevance - (1.0 - lambda_mmr) * max_sim
                if mmr_val > best_mmr:
                    best_mmr = mmr_val
                    best_cand = cand

        if best_cand is None:
            break

        selected.append(best_cand)
        for g in best_cand.get("genres") or []:
            genre_counts[g] += 1
        remaining.remove(best_cand)

    return selected
