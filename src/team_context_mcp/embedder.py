"""
Wrapper around sentence-transformers.
Lazy-loads the model on first use so CLI startup is instant.
"""

from __future__ import annotations
from functools import lru_cache
from typing import Union


MODEL_NAME = "all-MiniLM-L6-v2"


class Embedder:
    _model = None

    @classmethod
    def _get_model(cls):
        if cls._model is None:
            import logging
            import os

            # Silenciar los logs HTTP de HuggingFace Hub — el modelo ya está cacheado
            os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
            logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
            logging.getLogger("transformers").setLevel(logging.ERROR)
            logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
            logging.getLogger("httpx").setLevel(logging.ERROR)
            logging.getLogger("httpcore").setLevel(logging.ERROR)

            from sentence_transformers import SentenceTransformer  # lazy import

            cls._model = SentenceTransformer(MODEL_NAME, device="cpu")
        return cls._model

    @classmethod
    def embed(cls, text: str) -> list[float]:
        model = cls._get_model()
        return model.encode(text, normalize_embeddings=True).tolist()

    @classmethod
    def embed_batch(cls, texts: list[str]) -> list[list[float]]:
        model = cls._get_model()
        return model.encode(texts, normalize_embeddings=True).tolist()
