#!/usr/bin/env python3
"""
Semantic Embedding Pipeline — Multi-Backend + SHA256 Cache
===========================================================
Semantic Foundation Phase 1 (Day 1-2).
Provides a singleton embedding encoder with SHA256 text caching.

Backends:
    - FlagEmbedding (default): BAAI/bge-m3 (1024-dim, multilingual, high quality)
    - sentence-transformers (fallback): BAAI/bge-small-zh-v1.5 (512-dim, Chinese)

Architecture:
    - Singleton model (lazy init on first encode)
    - SHA256 → vector cache dict (session lifetime)
    - encode_batch() for bulk operations
    - Graceful degradation: if model fails, returns zero vectors
    - Auto-detects embedding dimension from model

Usage:
    from ..semantic.embeddings import get_embedder, EMBEDDING_DIM
    embedder = get_embedder()
    vec = embedder.encode("你好世界")  # returns np.ndarray (512,) or (1024,)
"""

import hashlib
import logging
import os
import time
import numpy as np
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Model Configuration ───────────────────────────────────────
# Default model (fast download, Chinese-optimized)
_DEFAULT_MODEL = "BAAI/bge-m3"
# Fallback model (smaller, faster download, Chinese-optimized)
_FALLBACK_MODEL = "BAAI/bge-small-zh-v1.5"
# Override via env var: EMBEDDING_MODEL=BAAI/bge-m3
_MODEL_NAME = os.environ.get("EMBEDDING_MODEL", _DEFAULT_MODEL)

CACHE_SIZE_LIMIT = 10000  # max cached texts before LRU eviction
MODEL_INIT_TIMEOUT = 120  # seconds for first model load

# Dynamic — set after model loads
EMBEDDING_DIM = 1024  # default for bge-m3; updated on model load


class _Embedder:
    """Singleton embedding engine with SHA256 text cache.

    Auto-selects backend based on model name:
    - BGE-M3 → FlagEmbedding (BGEM3FlagModel, 1024-dim)
    - Others → sentence-transformers (SentenceTransformer, variable dim)
    """

    def __init__(self, model_name: Optional[str] = None):
        self._model = None
        self._cache: dict[str, np.ndarray] = {}
        self._model_name = model_name or _MODEL_NAME
        self._loaded = False
        self._load_error: Optional[str] = None
        self._backend: str = "auto"  # "flagembedding" or "sentence_transformers"
        global EMBEDDING_DIM
        EMBEDDING_DIM = self.dim  # sync global constant

    def _ensure_model(self) -> bool:
        """Lazy-load model. Returns True if loaded, False on failure."""
        if self._loaded:
            return True
        if self._load_error:
            return False

        # Detect backend from model name
        is_bge_m3 = "bge-m3" in self._model_name.lower()

        if is_bge_m3:
            return self._load_flagembedding()
        else:
            return self._load_sentence_transformers()

    def _load_sentence_transformers(self) -> bool:
        """Load model via sentence-transformers (preferred for most models)."""
        logger.info(f"Loading {self._model_name} via sentence-transformers...")
        start = time.time()
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name, device="cpu")
            self._backend = "sentence_transformers"
            self._loaded = True
            elapsed = time.time() - start
            # Update global dimension
            global EMBEDDING_DIM
            EMBEDDING_DIM = self._model.get_sentence_embedding_dimension()
            logger.info(
                f"{self._model_name} loaded in {elapsed:.1f}s "
                f"(dim={EMBEDDING_DIM}, backend=sentence_transformers)"
            )
            return True
        except Exception as e:
            self._load_error = str(e)
            logger.error(f"Failed to load via sentence-transformers: {e}")
            # Fallback: try FlagEmbedding if sentence-transformers fails
            return self._load_flagembedding()

    def _load_flagembedding(self) -> bool:
        """Load model via FlagEmbedding (required for BGE-M3)."""
        logger.info(f"Loading {self._model_name} via FlagEmbedding...")
        start = time.time()
        try:
            from FlagEmbedding import BGEM3FlagModel
            self._model = BGEM3FlagModel(
                self._model_name,
                use_fp16=True,
                device="cpu",
            )
            self._backend = "flagembedding"
            self._loaded = True
            elapsed = time.time() - start
            global EMBEDDING_DIM
            EMBEDDING_DIM = 1024  # BGE-M3 dense dim
            logger.info(
                f"{self._model_name} loaded in {elapsed:.1f}s "
                f"(dim={EMBEDDING_DIM}, backend=flagembedding)"
            )
            return True
        except Exception as e:
            self._load_error = str(e)
            logger.error(f"Failed to load via FlagEmbedding: {e}")
            return False

    def encode(self, text: str) -> np.ndarray:
        """Encode a single text to a dense vector.

        Uses SHA256 cache. Returns zero vector on failure.
        """
        if not text or not text.strip():
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)

        text = text.strip()
        cache_key = hashlib.sha256(text.encode("utf-8")).hexdigest()

        if cache_key in self._cache:
            return self._cache[cache_key]

        if len(self._cache) >= CACHE_SIZE_LIMIT:
            oldest = next(iter(self._cache))
            del self._cache[oldest]

        if not self._ensure_model():
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)

        try:
            if self._backend == "flagembedding":
                output = self._model.encode(
                    [text],
                    return_dense=True,
                    return_sparse=False,
                    return_colbert_vecs=False,
                )
                vec = output["dense_vecs"][0].astype(np.float32)
            else:
                # sentence-transformers
                vec = self._model.encode(text, normalize_embeddings=False)
                vec = np.array(vec, dtype=np.float32)

            self._cache[cache_key] = vec
            return vec
        except Exception as e:
            logger.warning(f"Encode failed for text '{text[:50]}...': {e}")
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        """Encode multiple texts, with cache for previously seen texts.

        Returns:
            np.ndarray of shape (N, EMBEDDING_DIM).
        """
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

        uncached_texts = []
        uncached_indices = []
        results = [None] * len(texts)

        for i, text in enumerate(texts):
            if not text or not text.strip():
                results[i] = np.zeros(EMBEDDING_DIM, dtype=np.float32)
                continue
            text_s = text.strip()
            cache_key = hashlib.sha256(text_s.encode("utf-8")).hexdigest()
            if cache_key in self._cache:
                results[i] = self._cache[cache_key]
            else:
                uncached_texts.append(text_s)
                uncached_indices.append(i)

        if uncached_texts:
            if not self._ensure_model():
                for idx in uncached_indices:
                    results[idx] = np.zeros(EMBEDDING_DIM, dtype=np.float32)
            else:
                try:
                    if self._backend == "flagembedding":
                        output = self._model.encode(
                            uncached_texts,
                            return_dense=True,
                            return_sparse=False,
                            return_colbert_vecs=False,
                        )
                        vecs = output["dense_vecs"].astype(np.float32)
                    else:
                        vecs = self._model.encode(
                            uncached_texts, normalize_embeddings=False
                        )
                        vecs = np.array(vecs, dtype=np.float32)

                    for j, idx in enumerate(uncached_indices):
                        results[idx] = vecs[j]
                        ck = hashlib.sha256(
                            uncached_texts[j].encode("utf-8")
                        ).hexdigest()
                        self._cache[ck] = vecs[j]
                except Exception as e:
                    logger.warning(f"Batch encode failed: {e}")
                    for idx in uncached_indices:
                        results[idx] = np.zeros(EMBEDDING_DIM, dtype=np.float32)

        return np.array(results, dtype=np.float32)

    def cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two vectors. Returns 0.0 on zero vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    # ── Properties ────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    @property
    def dim(self) -> int:
        """Get embedding dimension (from global or by trying to load model)."""
        global EMBEDDING_DIM
        if self._loaded:
            return EMBEDDING_DIM
        # Try to load to get real dimension
        if self._ensure_model():
            return EMBEDDING_DIM
        return EMBEDDING_DIM  # fallback to current value

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def backend(self) -> str:
        return self._backend


# ── Singleton ─────────────────────────────────────────────────

_embedder: Optional[_Embedder] = None


def get_embedder(model_name: Optional[str] = None) -> _Embedder:
    """Get or create the singleton embedder instance.

    Args:
        model_name: Override default model (e.g. "BAAI/bge-m3").
                    Only used on first call; subsequent calls return existing instance.
    """
    global _embedder
    if _embedder is None:
        _embedder = _Embedder(model_name=model_name)
    return _embedder


# ── CLI for testing ──────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Multi-Backend Embedding CLI")
    parser.add_argument("--encode", type=str, help="Encode a single text")
    parser.add_argument("--batch", type=str, nargs="+", help="Encode multiple texts")
    parser.add_argument(
        "--similarity", type=str, nargs=2,
        help="Compute cosine similarity between two texts"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--model", type=str, default=None,
                        help=f"Model name (default: {_DEFAULT_MODEL})")

    args = parser.parse_args()

    embedder = get_embedder(model_name=args.model)

    if args.encode:
        vec = embedder.encode(args.encode)
        dim = embedder.dim
        if args.json:
            print(json.dumps({
                "text": args.encode,
                "dim": dim,
                "model": embedder.model_name,
                "backend": embedder.backend,
                "norm": round(float(np.linalg.norm(vec)), 4),
                "sample": [round(float(x), 6) for x in vec[:5].tolist()],
            }, ensure_ascii=False, indent=2))
        else:
            print(f"Model: {embedder.model_name} ({embedder.backend})")
            print(f"Text: {args.encode}")
            print(f"Dim: {dim}, Norm: {np.linalg.norm(vec):.4f}")
            print(f"Sample: {vec[:5].tolist()}")
            print(f"Cache size: {embedder.cache_size}")

    elif args.batch:
        vecs = embedder.encode_batch(args.batch)
        dim = embedder.dim
        if args.json:
            print(json.dumps({
                "count": len(args.batch),
                "dim": dim,
                "model": embedder.model_name,
                "backend": embedder.backend,
                "norms": [round(float(np.linalg.norm(v)), 4) for v in vecs],
            }, ensure_ascii=False, indent=2))
        else:
            print(f"Model: {embedder.model_name} ({embedder.backend})")
            print(f"Batch: {len(args.batch)} texts → {vecs.shape}")
            for i, text in enumerate(args.batch):
                print(f"  [{i}] norm={np.linalg.norm(vecs[i]):.4f}: {text[:50]}")

    elif args.similarity:
        a, b = args.similarity
        va = embedder.encode(a)
        vb = embedder.encode(b)
        sim = embedder.cosine_similarity(va, vb)
        if args.json:
            print(json.dumps({
                "text_a": a, "text_b": b,
                "similarity": round(sim, 4),
                "model": embedder.model_name,
                "dim": embedder.dim,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"Model: {embedder.model_name} ({embedder.backend})")
            print(f"Cosine similarity: {sim:.4f}")
            print(f"  A: {a}")
            print(f"  B: {b}")

    else:
        parser.print_help()
