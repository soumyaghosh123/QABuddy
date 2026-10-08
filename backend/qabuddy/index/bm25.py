"""BM25 query vectors identical to FastEmbed's `Qdrant/bm25` model, without importing FastEmbed.

FastEmbed pulls in onnxruntime, which the Vercel function must not carry (spec §6.2). This mirrors
FastEmbed 0.8.1 `Bm25.query_embed`; tests/test_bm25.py checks the two agree exactly.
`bm25_english.txt` is the English stop-word list from the Qdrant/bm25 model repo (Apache-2.0).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import mmh3
from py_rust_stemmers import SnowballStemmer
from qdrant_client import models

DEFAULT_STOPWORDS = Path(__file__).with_name("bm25_english.txt")


class Bm25QueryEncoder:
    def __init__(self, stopwords_path: Path = DEFAULT_STOPWORDS, token_max_length: int = 40):
        self.stopwords = set(stopwords_path.read_text(encoding="utf-8").splitlines())
        self.token_max_length = token_max_length
        self.stemmer = SnowballStemmer("english")

    def tokens(self, text: str) -> list[str]:
        text = re.sub(r"[^\w\s]", " ", text)
        raw = re.sub(r"\s+", " ", re.sub(r"[^\w]", " ", text.lower())).strip().split()
        stems = []
        for token in raw:
            if len(token) == 1 and unicodedata.category(token).startswith("P"):  # FastEmbed's punctuation set
                continue
            if token in self.stopwords or len(token) > self.token_max_length:
                continue
            stem = self.stemmer.stem_word(token)
            if stem:
                stems.append(stem)
        return stems

    def embed_query(self, text: str) -> models.SparseVector:
        indices = sorted({abs(mmh3.hash(stem)) for stem in self.tokens(text)})
        return models.SparseVector(indices=indices, values=[1.0] * len(indices))
