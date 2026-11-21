"""Jira Cloud connector: fetches the issues linked from Confluence pages and Zephyr test cases into `jira/<KEY>.json`."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from qabuddy.desktop.inputs import parse_space_url
from qabuddy.desktop.sync import ConnectorResult, SyncContext
from qabuddy.ingest.connectors.confluence import atlassian_headers
from qabuddy.ingest.connectors.http import ApiClient, ServiceError
from qabuddy.ingest.normalize import clean_text

log = logging.getLogger(__name__)

BATCH = 100
OVERLAP_S = 600
BASE_FIELDS = ["summary", "issuetype", "status", "priority", "description", "labels", "fixVersions",
               "components", "issuelinks"]


def _children(node: dict, sep: str) -> str:
    return sep.join(adf_to_text_raw(c) for c in node.get("content") or [])


def adf_to_text_raw(node) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    attrs = node.get("attrs") or {}
    if kind == "text":
        return node.get("text") or ""
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        return attrs.get("text") or ""
    if kind == "inlineCard":
        return attrs.get("url") or ""
    if kind in ("doc", "blockquote", "panel", "listItem"):
        return _children(node, "\n")
    if kind == "bulletList":
        return "\n".join("- " + adf_to_text_raw(c) for c in node.get("content") or [])
    if kind == "orderedList":
        return "\n".join(f"{i}. {adf_to_text_raw(c)}" for i, c in enumerate(node.get("content") or [], 1))
    if kind == "table":
        return _children(node, "\n")
    if kind == "tableRow":
        return _children(node, " | ")
    if kind in ("tableCell", "tableHeader"):
        return _children(node, "\n").replace("\n", " ")
    return _children(node, "")  # paragraph, heading, codeBlock and unknown nodes


def adf_to_text(doc) -> str:
    """Atlassian Document Format (or an already-plain string) to cleaned text."""
    return clean_text(adf_to_text_raw(doc)).strip()


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


def _batches(items: list, size: int = BATCH):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _is_bad_request(e: ServiceError) -> bool:
    return type(e) is ServiceError and "HTTP 400" in e.message


class JiraConnector:
    name = "jira"

    def __init__(self, email: str, token: str, site: str = "ausiex.atlassian.net", transport=None):
        self._headers = atlassian_headers(email, token)
        self._site = site
        self._transport = transport

    def _site_for(self, ctx: SyncContext) -> str:
        for url in ctx.inputs.confluence_spaces:
            try:
                return parse_space_url(url).site
            except ValueError:
                continue
        return self._site

    def _search(self, client: ApiClient, jql: str, fields: list[str]) -> list[dict]:
        issues: list[dict] = []
        token = None
        while True:
            body = {"jql": jql, "fields": fields, "maxResults": BATCH}
            if token:
                body["nextPageToken"] = token
            data = client.post_json("/rest/api/3/search/jql", body)
            issues.extend(data.get("issues") or [])
            token = data.get("nextPageToken")
            if data.get("isLast", True) or not token:
                return issues

    def _search_ids(self, client: ApiClient, field: str, values: list[str], suffix: str,
                    fields: list[str]) -> list[dict]:
        """`<field> in (...)` search (field is `key` or `id`); a 400 (some value does not exist) bisects the
        batch so valid values still load."""
        jql = f"{field} in ({','.join(values)}){suffix}"
        try:
            return self._search(client, jql, fields)
        except ServiceError as e:
            if not _is_bad_request(e):
                raise
            if len(values) == 1:
                log.warning("Jira %s %s unreadable, skipped", field, values[0])
                return []
            mid = len(values) // 2
            return (self._search_ids(client, field, values[:mid], suffix, fields)
                    + self._search_ids(client, field, values[mid:], suffix, fields))

    def _project_keys(self, client: ApiClient) -> set[str]:
        out: set[str] = set()
        start = 0
        while True:
            data = client.get_json("/rest/api/3/project/search", {"maxResults": 100, "startAt": start})
            values = data.get("values") or []
            out.update(v["key"] for v in values)
            if data.get("isLast", True) or not values:
                return out
            start += len(values)

    def _acceptance_field(self, client: ApiClient) -> str | None:
        for f in client.get_json("/rest/api/3/field"):
            if (f.get("name") or "").strip().lower() == "acceptance criteria":
                return f["id"]
        return None

    def _resolve_ids(self, client: ApiClient, ids: list[int], id_map: dict[str, str]) -> None:
        todo = [str(i) for i in sorted(ids) if str(i) not in id_map]
        for batch in _batches(todo):
            for issue in self._search_ids(client, "id", batch, "", ["key"]):
                id_map[str(issue["id"])] = issue["key"]

    def run(self, ctx: SyncContext) -> ConnectorResult:
        wanted = ctx.state.get("jira_wanted")
        if wanted is None:
            return ConnectorResult(ok=True)
        state = ctx.state.setdefault("jira", {})
        dest = ctx.data_dir / "jira"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            client = ApiClient("Jira", f"https://{self._site_for(ctx)}", self._headers, transport=self._transport)
            # A failed Confluence page / Zephyr case may be the only link to an issue, and its keys are unknown
            # this run: keep every jira/ file (local and shared) rather than prune what may still be wanted.
            feeder_failed = any(p.startswith(("confluence/", "zephyr/")) for p in ctx.failed)
            self._sync(client, ctx, wanted, state, dest, prune=not feeder_failed)
        except Exception as e:
            log.warning("Jira sync failed: %s", e)
            return ConnectorResult(ok=False, error=str(e) or type(e).__name__)
        # the wanted keys come from the local Confluence/Zephyr copies: complete only if all of those are
        feeders = ({"confluence"} if ctx.inputs.confluence_spaces else set()) | {
            f"zephyr_{k.lower()}" for k in ctx.inputs.zephyr_projects}
        return ConnectorResult(ok=True, ready_sources={"jira_linked"} if feeders and feeders <= ctx.ready else set(),
                               failed_paths={"jira/"} if feeder_failed else set())

    def _sync(self, client: ApiClient, ctx: SyncContext, wanted: dict, state: dict, dest: Path,
              prune: bool = True) -> None:
        id_map: dict[str, str] = dict(state.get("id_to_key") or {})
        self._resolve_ids(client, wanted.get("issue_ids") or [], id_map)
        state["id_to_key"] = id_map
        projects = self._project_keys(client)
        keys: set[str] = set()
        for key in wanted.get("keys") or []:
            if key.rsplit("-", 1)[0] in projects:
                keys.add(key)
            else:
                log.debug("Jira key %s dropped: no such project", key)
        keys.update(id_map[str(i)] for i in wanted.get("issue_ids") or [] if str(i) in id_map)

        acceptance = self._acceptance_field(client)
        fields = BASE_FIELDS + ([acceptance] if acceptance else [])
        fresh = state.get("acceptance_field") == acceptance and "last_sync" in state
        on_disk = {k for k in keys if fresh and (dest / f"{k}.json").exists()}
        # UTC, but JQL reads date literals in the Jira user's profile timezone. The AUSIEX account is in
        # Australia (east of UTC), so this only widens the window; an account west of UTC would miss updates.
        since = time.strftime("%Y-%m-%d %H:%M", time.gmtime(state.get("last_sync", 0) - OVERLAP_S))
        todo = sorted(keys - on_disk)
        done = 0
        for keyset, suffix in ((sorted(on_disk), f' AND updated >= "{since}"'), (todo, "")):
            for batch in _batches(keyset):
                for issue in self._search_ids(client, "key", batch, suffix, fields):
                    id_map[str(issue["id"])] = issue["key"]
                    _write_if_changed(dest / f"{issue['key']}.json", {
                        "key": issue["key"], "id": issue["id"], "fields": issue.get("fields") or {},
                        "acceptance_field": acceptance})
                    done += 1
                ctx.progress(f"jira: {done} issues")

        if prune:
            for f in dest.glob("*.json"):
                if f.stem not in keys:
                    f.unlink()
        state["acceptance_field"] = acceptance
        state["last_sync"] = ctx.now
