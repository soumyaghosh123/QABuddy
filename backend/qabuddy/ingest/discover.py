"""Find the files to index, following config/sources.yaml."""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from qabuddy.config import Settings
from qabuddy.ingest.models import SourceFile

log = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    id: str
    label: str
    folder: str
    parser: str
    enabled: bool = True
    include: list[str] = field(default_factory=list)
    exclude_dirs: list[str] = field(default_factory=list)
    exclude_files: list[str] = field(default_factory=list)
    binary_ext: list[str] = field(default_factory=list)


def load_sources(path: Path) -> list[SourceConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults", {})
    sources = []
    for item in raw.get("sources", []):
        sources.append(SourceConfig(
            id=item["id"],
            label=item.get("label", item["id"]),
            folder=item["folder"],
            parser=item.get("parser", "none"),
            enabled=item.get("enabled", True),
            include=list(item.get("include", [])),
            exclude_dirs=list(defaults.get("exclude_dirs", [])) + list(item.get("exclude_dirs", [])),
            exclude_files=list(defaults.get("exclude_files", [])) + list(item.get("exclude_files", [])),
            binary_ext=[e.lower() for e in defaults.get("binary_ext", [])],
        ))
    return sources


def _matches(name: str, rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("**/"):
            if fnmatch.fnmatch(name, pattern[3:]):
                return True
        elif fnmatch.fnmatch(rel, pattern):
            return True
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def discover(settings: Settings, only: list[str] | None = None) -> list[SourceFile]:
    data_root = settings.data_path
    seen_hashes: set[str] = set()
    files: list[SourceFile] = []
    for source in load_sources(settings.config_path / "sources.yaml"):
        if not source.enabled or (only and source.id not in only):
            continue
        root = data_root / source.folder
        if not root.is_dir():
            log.warning("source %s: folder %s not found", source.id, root)
            continue
        found: list[SourceFile] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in source.exclude_dirs)
            for name in sorted(filenames):
                abs_path = Path(dirpath) / name
                rel_in_source = abs_path.relative_to(root).as_posix()
                if Path(name).suffix.lower() in source.binary_ext:
                    continue
                if any(fnmatch.fnmatch(name, p) for p in source.exclude_files):
                    continue
                if not _matches(name, rel_in_source, source.include):
                    continue
                file_hash = _sha256(abs_path)
                if file_hash in seen_hashes:
                    log.info("skipping duplicate file %s", abs_path)
                    continue
                seen_hashes.add(file_hash)
                found.append(SourceFile(
                    abs_path=abs_path,
                    rel_path=abs_path.relative_to(data_root).as_posix(),
                    source=source.id,
                    parser=source.parser,
                    file_hash=file_hash,
                ))
        files.extend(sorted(found, key=lambda f: f.rel_path))
    return files


def source_labels(settings: Settings) -> dict[str, str]:
    return {s.id: s.label for s in load_sources(settings.config_path / "sources.yaml")}
