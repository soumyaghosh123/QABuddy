"""Confluence Cloud connector: one JSON file per page under `confluence/<KEY>/pages/`, plus attachments."""
from __future__ import annotations

import base64
import json
import logging
import math
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from qabuddy.desktop.inputs import SpaceRef, parse_space_url
from qabuddy.desktop.sync import ConnectorResult, SyncContext, mark_local_source, read_local_sources
from qabuddy.ingest.connectors.http import ApiClient, AuthError, NotFound, ServiceError, Unreachable

log = logging.getLogger(__name__)

PAGE_LIMIT = 250
SEARCH_LIMIT = 100
OVERLAP_S = 600
DELETION_CHECK_S = 86400
MAX_DELETE_SHARE = 0.5  # a listing that would drop more than this share of cached pages is distrusted
ATTACHMENT_EXTS = {".pdf", ".docx", ".xlsx", ".pptx"}
JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
PAGE_ID = re.compile(r"^\d+$")
UNSAFE_NAME = re.compile(r'[<>:"/\\|?*]')


def atlassian_headers(email: str, token: str) -> dict:
    basic = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {basic}", "Accept": "application/json"}


def _rel(link: str) -> str:
    """`_links.next` / downloadLink are relative to /wiki (and may include it); the client base already has it."""
    return link[len("/wiki"):] if link.startswith("/wiki/") else link


def _results(client: ApiClient, path: str, params: dict | None):
    """Yield every result across `_links.next` pages."""
    data = client.get_json(path, params)
    while True:
        yield from data.get("results") or []
        nxt = (data.get("_links") or {}).get("next")
        if not nxt:
            return
        data = client.get_json(_rel(nxt))


def _space_id(client: ApiClient, key: str) -> str:
    results = client.get_json("/api/v2/spaces", {"keys": key}).get("results") or []
    if not results:
        raise ServiceError("Confluence", f"Space {key} not found")
    return results[0]["id"]


MAX_NAME = 100  # keeps attachment paths well under the Windows 260-character limit


def _shorten(name: str, limit: int = MAX_NAME) -> str:
    if len(name) <= limit:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or len(ext) > 10:
        return name[:limit].rstrip(" .")
    return stem[:limit - len(ext) - 1].rstrip(" .") + "." + ext


def _safe_name(title: str, att_id: str, used: set[str]) -> str:
    name = _shorten(UNSAFE_NAME.sub("_", title).strip(" .") or att_id)
    if name in used:
        name = _shorten(f"{att_id}_{name}")
    used.add(name)
    return name


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_if_changed(path: Path, record: dict) -> None:
    text = json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False)
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")


def _marker(ref: SpaceRef) -> str:
    return f"confluence:{ref.key}"


def _ancestors(parent_id: str | None, titles: dict[str, str], parents: dict[str, str | None]) -> list[str]:
    chain: list[str] = []
    seen: set[str] = set()
    while parent_id and parent_id in titles and parent_id not in seen:
        seen.add(parent_id)
        chain.append(titles[parent_id])
        parent_id = parents.get(parent_id)
    return list(reversed(chain))


class ConfluenceConnector:
    name = "confluence"

    def __init__(self, email: str, token: str, transport=None):
        self._headers = atlassian_headers(email, token)
        self._transport = transport

    def client(self, ref: SpaceRef) -> ApiClient:
        return ApiClient("Confluence", ref.base, self._headers, transport=self._transport)

    def _changed_pages(self, client: ApiClient, ref: SpaceRef, space_id: str, sstate: dict, first_load: bool,
                       errors: list[str], failed: list[str]) -> list[dict]:
        """Pages to (re)write: everything on first load, else CQL hits plus pages that failed last run."""
        if first_load:
            return list(_results(client, f"/api/v2/spaces/{space_id}/pages",
                                 {"body-format": "storage", "status": "current", "limit": PAGE_LIMIT}))
        since = time.strftime("%Y-%m-%d %H:%M", time.gmtime(sstate["last_sync"] - OVERLAP_S))
        cql = f'space="{ref.key}" and type=page and lastmodified >= "{since}"'
        hits = [h["id"] for h in _results(client, "/rest/api/content/search", {"cql": cql, "limit": SEARCH_LIMIT})]
        retry = [i for i in sstate.get("failed", []) if i not in hits]
        pages = []
        for page_id in hits + retry:
            try:
                pages.append(client.get_json(f"/api/v2/pages/{page_id}", {"body-format": "storage"}))
            except NotFound as e:  # trashed or moved since the search: nothing to retry
                if page_id in hits:
                    errors.append(f"{page_id}: {e.message}")
            except (AuthError, Unreachable):
                raise
            except ServiceError as e:
                log.warning("Confluence page %s failed: %s", page_id, e)
                errors.append(f"{page_id}: {e.message}")
                failed.append(page_id)
        return pages

    def _comments(self, client: ApiClient, page_id: str) -> list[dict]:
        params = {"expand": "body.storage,history", "depth": "all", "location": ["footer", "inline"],
                  "limit": 100}
        out = []
        for c in _results(client, f"/rest/api/content/{page_id}/child/comment", params):
            history = c.get("history") or {}
            out.append({"author": (history.get("createdBy") or {}).get("displayName") or "",
                        "created": history.get("createdDate") or "",
                        "body": ((c.get("body") or {}).get("storage") or {}).get("value") or ""})
        return out

    def _attachments(self, client: ApiClient, ctx: SyncContext, dest: Path, page_id: str,
                     old: dict | None) -> list[dict]:
        max_bytes = ctx.settings.desktop.attachment_max_mb * 1024 * 1024
        cached = {a["id"]: a for a in (old or {}).get("attachments") or []}
        kept: list[dict] = []
        used: set[str] = set()
        for att in _results(client, f"/api/v2/pages/{page_id}/attachments", {"limit": PAGE_LIMIT}):
            title = att.get("title") or ""
            if Path(title).suffix.lower() not in ATTACHMENT_EXTS or (att.get("fileSize") or 0) > max_bytes:
                continue
            version = (att.get("version") or {}).get("number")
            rel = f"attachments/{page_id}/{_safe_name(title, att['id'], used)}"
            prev = cached.get(att["id"])
            if not (prev and prev.get("version") == version and prev.get("file") == rel and (dest / rel).exists()):
                if not client.download(_rel(att["downloadLink"]), dest / rel, max_bytes):
                    continue
            kept.append({"id": att["id"], "title": title, "version": version, "file": rel})
        keep_files = {a["file"] for a in kept}
        for a in cached.values():
            if a.get("file") not in keep_files:
                (dest / a["file"]).unlink(missing_ok=True)
        return kept

    def _delete_missing(self, client: ApiClient, dest: Path, space_id: str, listed: set[str] | None) -> bool:
        """Remove cached pages no longer in the space; returns False (nothing removed) on a suspicious listing."""
        if listed is None:
            listed = {p["id"] for p in _results(client, f"/api/v2/spaces/{space_id}/pages",
                                                {"status": "current", "limit": PAGE_LIMIT})}
        cached = list((dest / "pages").glob("*.json"))
        gone = [f for f in cached if f.stem not in listed]
        if gone and (not listed or len(gone) > MAX_DELETE_SHARE * len(cached)):
            log.warning("Confluence %s: listing would remove %d of %d cached pages; skipping deletions",
                        dest.name, len(gone), len(cached))
            return False
        for f in gone:
            f.unlink()
        att_root = dest / "attachments"
        if att_root.exists():
            for d in att_root.iterdir():
                if d.name not in listed:
                    shutil.rmtree(d, ignore_errors=True)
        return True

    def sync_space(self, client: ApiClient, ctx: SyncContext, ref: SpaceRef, sstate: dict,
                   failed_paths: set[str]) -> list[str]:
        """Write changed pages; returns per-page errors. Adds the data-dir-relative page file and attachment
        folder of each page whose fetch failed to `failed_paths`."""
        dest = ctx.data_dir / "confluence" / ref.key
        (dest / "pages").mkdir(parents=True, exist_ok=True)
        started = ctx.now
        space_id = _space_id(client, ref.key)
        # The shared last_sync says some PC loaded the space; only a local marker plus cached pages prove THIS
        # PC's copy is complete. Without them, list everything.
        first_load = ("last_sync" not in sstate or not read_local_sources(ctx.settings).get(_marker(ref))
                      or not any((dest / "pages").glob("*.json")))
        errors: list[str] = []
        failed: list[str] = []
        pages = self._changed_pages(client, ref, space_id, sstate, first_load, errors, failed)

        titles: dict[str, str] = {}
        parents: dict[str, str | None] = {}
        for f in (dest / "pages").glob("*.json"):
            cached = _load(f)
            if cached:
                titles[cached["id"]] = cached.get("title", "")
                parents[cached["id"]] = cached.get("parent_id")
        for p in pages:
            titles[p["id"]] = p.get("title", "")
            parents[p["id"]] = p.get("parentId")

        for i, p in enumerate(pages, 1):
            page_id = p["id"]
            if not PAGE_ID.match(page_id):
                errors.append(f"Skipped page with unsafe id {page_id!r}")
                continue
            file = dest / "pages" / f"{page_id}.json"
            body = ((p.get("body") or {}).get("storage") or {}).get("value") or ""
            try:
                comments = self._comments(client, page_id)
                attachments = self._attachments(client, ctx, dest, page_id, _load(file))
            except (AuthError, Unreachable):
                raise
            except ServiceError as e:
                log.warning("Confluence page %s failed: %s", page_id, e)
                errors.append(f"{page_id}: {e.message}")
                failed.append(page_id)
                continue
            _write_if_changed(file, {
                "id": page_id, "title": p.get("title", ""), "version": (p.get("version") or {}).get("number"),
                "parent_id": p.get("parentId"),
                "ancestors_titles": _ancestors(p.get("parentId"), titles, parents),
                "web_url": ref.base + ((p.get("_links") or {}).get("webui") or ""),
                "body": body, "comments": comments, "jira_keys": sorted(set(JIRA_KEY.findall(body))),
                "attachments": attachments})
            if i % 25 == 0:
                ctx.progress(f"confluence: {ref.key} {i}/{len(pages)} pages")

        if ctx.now - sstate.get("deletion_checked_at", 0) >= DELETION_CHECK_S:
            if self._delete_missing(client, dest, space_id, {p["id"] for p in pages} if first_load else None):
                sstate["deletion_checked_at"] = ctx.now
        sstate["last_sync"] = started  # always advance; failed pages are re-fetched by id next run
        sstate["failed"] = failed
        for page_id in failed:
            root = f"confluence/{ref.key}"
            failed_paths |= {f"{root}/pages/{page_id}.json", f"{root}/attachments/{page_id}/"}
        # A first load with failed pages is not complete: the next run lists everything again (the shared retry
        # list can be cleared by another PC meanwhile), and the space stays not-ready until a clean first load.
        if first_load and not failed:
            mark_local_source(ctx.settings, _marker(ref), ctx.now)
        ctx.progress(f"confluence: {ref.key} {len(pages)} pages")
        return errors

    def run(self, ctx: SyncContext) -> ConnectorResult:
        state = ctx.state.setdefault("confluence", {})
        refs: list[SpaceRef] = []
        errors: list[str] = []
        for url in ctx.inputs.confluence_spaces:
            try:
                refs.append(parse_space_url(url))
            except ValueError as e:
                errors.append(str(e))
        for stale in set(state) - {r.key for r in refs}:
            del state[stale]
        jira_keys: set[str] = set()
        failed: set[str] = set()
        complete = bool(refs) and len(refs) == len(ctx.inputs.confluence_spaces)
        for ref in refs:
            try:
                page_errors = self.sync_space(self.client(ref), ctx, ref, state.setdefault(ref.key, {}), failed)
            except Exception as e:
                log.warning("Confluence sync of %s failed: %s", ref.key, e)
                msg = str(e) or type(e).__name__
                errors.append(msg if msg.startswith("Space ") else f"{ref.key}: {msg}")
                complete = False
            else:
                if page_errors:
                    errors.append(f"{ref.key}: {len(page_errors)} page(s) failed ({page_errors[0]})")
            for f in (ctx.data_dir / "confluence" / ref.key / "pages").glob("*.json"):
                cached = _load(f)
                if cached:
                    jira_keys.update(cached.get("jira_keys") or [])
        marks = read_local_sources(ctx.settings) if complete else {}
        ready = {"confluence"} if complete and all(marks.get(_marker(r)) for r in refs) else set()
        if errors:
            return ConnectorResult(ok=False, error="; ".join(errors), jira_keys=jira_keys, ready_sources=ready,
                                   failed_paths=failed)
        return ConnectorResult(ok=True, jira_keys=jira_keys, ready_sources=ready)


@dataclass
class Measurement:
    pages: int
    attachments: int
    indexable_attachments: int
    attachment_mb: float
    est_chunks: int
    est_ram_mb: float
    est_embed_calls: int


def measure_space(client: ApiClient, ref: SpaceRef, max_mb: float) -> Measurement:
    """Size a space (no downloads) so the user can judge laptop RAM and embedding cost before indexing it."""
    space_id = _space_id(client, ref.key)
    pages = sum(1 for _ in _results(client, f"/api/v2/spaces/{space_id}/pages",
                                    {"status": "current", "limit": PAGE_LIMIT}))
    attachments = indexable = indexable_bytes = 0
    params = {"cql": f'space="{ref.key}" and type=attachment', "expand": "extensions", "limit": SEARCH_LIMIT}
    for att in _results(client, "/rest/api/content/search", params):
        attachments += 1
        size = (att.get("extensions") or {}).get("fileSize") or 0
        if Path(att.get("title") or "").suffix.lower() in ATTACHMENT_EXTS and size <= max_mb * 1024 * 1024:
            indexable += 1
            indexable_bytes += size
    chunks = pages * 6 + indexable * 20
    return Measurement(pages=pages, attachments=attachments, indexable_attachments=indexable,
                       attachment_mb=round(indexable_bytes / (1024 * 1024), 2), est_chunks=chunks,
                       est_ram_mb=chunks * 1.4 / 1024, est_embed_calls=math.ceil(chunks / 32))
