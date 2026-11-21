"""Parsing and validation of user-entered sources (repos, Zephyr projects, spaces)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError


class InputError(ValueError):
    """User-facing validation error."""


@dataclass(frozen=True)
class RepoRef:
    host: str
    owner: str
    repo: str

    @property
    def slug(self) -> str:
        return "gh_" + re.sub(r"[^a-z0-9]+", "_", self.repo.lower()).strip("_")

    @property
    def api_base(self) -> str:
        return f"https://{self.host}/api/v3"

    @property
    def web_url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.repo}"


@dataclass(frozen=True)
class SpaceRef:
    site: str
    key: str

    @property
    def base(self) -> str:
        return f"https://{self.site}/wiki"

    @property
    def web_url(self) -> str:
        return f"{self.base}/spaces/{self.key}"


_REPO_HINT = "Enter a repository link like https://<host>/<owner>/<repo>"
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")


def parse_repo_url(url: str) -> RepoRef:
    p = urlparse((url or "").strip())
    parts = [s for s in p.path.split("/") if s]
    if p.scheme not in ("http", "https") or not p.hostname or len(parts) < 2:
        raise InputError(_REPO_HINT)
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not repo:
        raise InputError(_REPO_HINT)
    return RepoRef(host=p.hostname, owner=parts[0], repo=repo)


def parse_zephyr_keys(text: str) -> list[str]:
    keys: list[str] = []
    for raw in (text or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        key = raw.upper()
        if not _KEY_RE.match(key):
            raise InputError(f"Use the project key (e.g. AUSWEB), not the name ({raw})")
        if key not in keys:
            keys.append(key)
    return keys


def parse_space_url(url: str) -> SpaceRef:
    p = urlparse((url or "").strip())
    parts = [s for s in p.path.split("/") if s]
    hint = "Enter a Confluence space link like https://<site>/wiki/spaces/<KEY>"
    if p.scheme not in ("http", "https") or not p.hostname or "spaces" not in parts:
        raise InputError(hint)
    i = parts.index("spaces")
    if i + 1 >= len(parts):
        raise InputError(hint)
    return SpaceRef(site=p.hostname, key=parts[i + 1])


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


class Inputs(BaseModel):
    github_repos: list[str] = []
    zephyr_projects: list[str] = []
    confluence_spaces: list[str] = []
    updated_by: str = ""
    updated_at: float = 0

    @classmethod
    def validated(cls, raw: dict) -> "Inputs":
        raw = dict(raw)
        zp = raw.get("zephyr_projects")
        if isinstance(zp, str):
            raw["zephyr_projects"] = [zp]
        try:
            m = cls(**raw)
        except ValidationError as e:
            fields = ", ".join(sorted({str(err["loc"][0]) for err in e.errors() if err["loc"]}))
            raise InputError(f"Invalid value for: {fields or 'inputs'}") from None
        m.github_repos = _dedupe([parse_repo_url(u).web_url for u in m.github_repos])
        zk: list[str] = []
        for entry in m.zephyr_projects:
            zk.extend(parse_zephyr_keys(entry))
        m.zephyr_projects = _dedupe(zk)
        m.confluence_spaces = _dedupe([parse_space_url(u).web_url for u in m.confluence_spaces])
        m.updated_by = m.updated_by.strip()
        return m
