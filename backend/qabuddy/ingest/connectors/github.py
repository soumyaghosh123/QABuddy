"""GitHub Enterprise connector: downloads each repo's `develop` branch tarball into the data folder."""
from __future__ import annotations

import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from qabuddy.desktop.inputs import RepoRef, parse_repo_url
from qabuddy.desktop.sync import ConnectorResult, SyncContext
from qabuddy.ingest.connectors.http import ApiClient, NotFound, ServiceError

log = logging.getLogger(__name__)

BRANCH = "develop"
MAX_PATH = 259  # Windows path limit (260 incl. the terminator) without long-path support


def _populated(folder: Path) -> bool:
    return folder.is_dir() and any(folder.iterdir())


def _common_top(members: list[tarfile.TarInfo]) -> str | None:
    """GitHub tarballs wrap everything in one `<owner>-<repo>-<sha>/` folder; return it if present."""
    tops = {PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts}
    deeper = any(len(PurePosixPath(m.name).parts) > 1 for m in members)
    return tops.pop() if len(tops) == 1 and deeper else None


def _extract(archive: Path, target: Path, final: Path) -> int:
    """Extract into target without the archive's top folder; returns how many files were skipped.

    Members that escape the tree (absolute, `..`, links leaving it) are refused. Files whose path under
    `final` (where the folder ends up) would exceed MAX_PATH are skipped rather than failing the repo.
    """
    skipped = 0
    try:
        with tarfile.open(archive) as tf:
            members = tf.getmembers()
            for member in members:
                p = PurePosixPath(member.name)
                if p.is_absolute() or ".." in p.parts:
                    raise ServiceError("github", f"Unsafe path in repo archive: {member.name}")
            top = _common_top(members)
            kept = []
            for member in members:
                parts = PurePosixPath(member.name).parts
                if top:
                    parts = parts[1:]
                    if not parts:
                        continue  # the top folder itself
                    if member.islnk() and member.linkname.startswith(top + "/"):
                        member.linkname = member.linkname[len(top) + 1:]
                member.name = "/".join(parts)
                if len(str(final.joinpath(*parts))) > MAX_PATH:
                    if member.isfile():
                        skipped += 1
                        log.warning("Skipping %s: path longer than %d characters", member.name, MAX_PATH)
                    continue
                kept.append(member)
            tf.extractall(target, members=kept, filter="data")
    except tarfile.TarError as e:  # includes FilterError
        raise ServiceError("github", f"Unsafe or corrupt repo archive ({e})") from e
    return skipped


def _replace(new: Path, dest: Path) -> None:
    """Swap new into dest: rename the old folder away, move the new one in, delete the old."""
    old = None
    if dest.exists():
        old = dest.with_name(f"{dest.name}.old.{uuid.uuid4().hex[:8]}")  # must not exist: Windows
        os.replace(dest, old)
    try:
        os.replace(new, dest)
    except OSError:
        if old is not None:
            os.replace(old, dest)
        raise
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


class GitHubConnector:
    name = "github"

    def __init__(self, token: str, transport=None):
        self._token = token
        self._transport = transport
        self._skipped = 0  # files skipped by the last sync_repo (path too long)

    def client_for(self, ref: RepoRef) -> ApiClient:
        return ApiClient("GitHub", ref.api_base, {"Authorization": f"Bearer {self._token}",
                                                  "Accept": "application/vnd.github+json"},
                         transport=self._transport)

    def sync_repo(self, client: ApiClient, ref: RepoRef, dest: Path, last_sha: str | None) -> str:
        """Bring dest up to the head of `develop`; returns the current SHA."""
        try:
            sha = client.get_json(f"/repos/{ref.owner}/{ref.repo}/branches/{BRANCH}")["commit"]["sha"]
        except NotFound:
            raise ServiceError("github", f"Repo {ref.repo} has no {BRANCH} branch") from None
        self._skipped = 0
        if sha == last_sha:
            return sha
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Keep the work folder name short: Windows limits paths to 260 characters.
        work = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".t"))
        try:
            archive = work / "r.tgz"
            client.download(f"/repos/{ref.owner}/{ref.repo}/tarball/{BRANCH}", archive, None)
            root = work / "x"
            root.mkdir()
            self._skipped = _extract(archive, root, dest)
            if not any(p.is_file() for p in root.rglob("*")):  # never swap a good folder for an empty one
                raise ServiceError("github", f"Repo {ref.repo} archive has no files")
            _replace(root, dest)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return sha

    def run(self, ctx: SyncContext) -> ConnectorResult:
        state = ctx.state.setdefault("github", {})
        errors: list[str] = []
        ready: set[str] = set()
        refs: list[RepoRef] = []
        for url in ctx.inputs.github_repos:
            try:
                refs.append(parse_repo_url(url))
            except Exception as e:
                errors.append(f"{url}: {e}")
        for stale in set(state) - {r.slug for r in refs}:
            del state[stale]
        for ref in refs:
            dest = ctx.data_dir / "github" / f"{ref.owner}__{ref.repo}"
            # a stale SHA with no folder (repo removed, then re-added) must not skip the download
            last = state.get(ref.slug) if _populated(dest) else None
            try:
                sha = self.sync_repo(self.client_for(ref), ref, dest, last)
            except Exception as e:
                log.warning("GitHub sync of %s failed: %s", ref.repo, e)
                errors.append(str(e) or type(e).__name__)
                continue
            state[ref.slug] = sha
            ready.add(ref.slug)
            line = (f"github: {ref.repo} up to date" if sha == last
                    else f"github: {ref.repo} downloaded {sha[:7]}")
            if self._skipped:
                line += (f" ({self._skipped} file{'s' if self._skipped != 1 else ''} skipped: "
                         f"path longer than {MAX_PATH} characters)")
            ctx.progress(line)
        if errors:
            return ConnectorResult(ok=False, error="; ".join(errors), ready_sources=ready)
        return ConnectorResult(ok=True, ready_sources=ready)
