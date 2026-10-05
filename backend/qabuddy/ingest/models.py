"""Core records passed through the ingestion pipeline."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

CHUNK_NAMESPACE = uuid.UUID("6f1c2a52-9a1e-4c39-8a43-3b0f6a5d2e10")


def make_chunk_id(path: str, locator: str) -> str:
    return str(uuid.uuid5(CHUNK_NAMESPACE, f"{path}#{locator}"))


@dataclass
class SourceFile:
    abs_path: Path
    rel_path: str
    source: str
    parser: str
    file_hash: str


@dataclass
class Chunk:
    chunk_id: str
    source: str
    kind: str
    path: str
    citation: str
    text: str
    header: str = ""
    location: dict = field(default_factory=dict)
    title: str = ""
    breadcrumb: list[str] = field(default_factory=list)
    parent_id: str = ""
    module: str = ""
    ids_norm: list[str] = field(default_factory=list)
    date: str = ""

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.header}\n{self.text}".encode("utf-8")).hexdigest()

    def payload(self) -> dict:
        return asdict(self)
