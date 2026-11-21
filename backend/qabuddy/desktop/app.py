"""Desktop entry point: loads bundled secrets, serves the app on localhost and shows it in a pywebview window.

`QABuddy.exe --measure` prints the size of the shared Confluence space instead of opening a window.
pywebview is imported lazily so the rest of the package (and the test suite) works without the desktop group.
"""
from __future__ import annotations

import dataclasses
import html
import logging
import os
import socket
import sys
import threading
import time
from collections.abc import MutableMapping
from logging.handlers import RotatingFileHandler
from pathlib import Path

from qabuddy.config import REPO_ROOT, Settings, load_settings
from qabuddy.desktop import paths
from qabuddy.desktop.secrets import SecretsFileError, load_secrets
from qabuddy.ingest.normalize import mask_secrets

SECRETS_FILE = "secrets.local.json"
TITLE = "QABuddy"


class MeasureError(Exception):
    pass


def bundle_dir() -> Path:
    """Folder holding the bundled files (secrets.local.json, config/, frontend/dist)."""
    return REPO_ROOT


def prepare_environment(env: MutableMapping[str, str], bundle: Path) -> list[str]:
    """Load bundled secrets and point the desktop profile at the per-user folders; return missing secret names."""
    missing = load_secrets(Path(bundle) / SECRETS_FILE, env)
    storage = paths.storage_dir(env)
    env["QABUDDY_PROFILE"] = "desktop"
    env["QABUDDY_DATA_DIR"] = str(paths.data_dir(env))
    env["QABUDDY_STORAGE_DIR"] = str(storage)
    env["QABUDDY_SOURCES_FILE"] = str(storage / "sources.desktop.yaml")
    env["QABUDDY_CACHE__DB_PATH"] = str(storage / "cache.db")
    return missing


class _MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask_secrets(super().format(record))


def configure_logging(env: MutableMapping[str, str] = os.environ) -> None:
    handler = RotatingFileHandler(paths.logs_dir(env) / "sync.log", maxBytes=1_000_000, backupCount=4,
                                  encoding="utf-8")
    handler.setFormatter(_MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def error_page(title: str, lines: list[str]) -> str:
    items = "".join(f"<li><code>{html.escape(line)}</code></li>" for line in lines)
    return (f"<html><body style='font-family:Segoe UI,sans-serif;margin:40px'><h2>{html.escape(title)}</h2>"
            f"<ul>{items}</ul></body></html>")


def run_measure(settings: Settings, services=None):
    from qabuddy.desktop.inputs import parse_space_url
    from qabuddy.ingest.connectors.confluence import atlassian_headers, measure_space
    from qabuddy.ingest.connectors.http import ApiClient, ServiceError

    if services is None:
        from qabuddy.desktop.wiring import build_desktop

        services = build_desktop(settings)
    spaces = services.meta.read_inputs().confluence_spaces
    if not spaces:
        raise MeasureError("Set the Confluence space in Settings first")
    ref = parse_space_url(spaces[0])
    env = getattr(services, "env", os.environ)
    client = ApiClient("confluence", ref.base, atlassian_headers(env.get("ATLASSIAN_EMAIL", ""),
                                                                 env.get("ATLASSIAN_API_TOKEN", "")))
    try:
        return measure_space(client, ref, settings.desktop.attachment_max_mb)
    except ServiceError as e:
        raise MeasureError(str(e)) from None


def overridden_keys(env: MutableMapping[str, str], bundle: Path) -> list[str]:
    """Names of bundled secrets that a same-named environment variable already overrides."""
    bundled: dict[str, str] = {}
    try:
        load_secrets(Path(bundle) / SECRETS_FILE, bundled)
    except SecretsFileError:
        return []
    return [k for k in bundled if env.get(k)]


def parse_document(path: Path) -> list[str]:
    """Parse an attachment through the same Docling paths the sync uses; returns the section texts."""
    from qabuddy.ingest.parsers.documents import office_to_sections, pdf_to_sections

    if path.suffix.lower() == ".pdf":
        return [s.text for s in pdf_to_sections(path, path.stem)[0]]
    return [s.text for s in office_to_sections(path, path.stem)]


def self_check(argv: list[str]) -> int:
    """`--self-check <file>`: network-free proof that the bundled document parsers work."""
    index = argv.index("--self-check") + 1
    path = Path(argv[index]) if index < len(argv) else None
    if path is None:
        return _fail("--self-check needs a file path", [], window=False)
    if not path.is_file():
        return _fail(f"File not found: {path}", [], window=False)
    try:
        texts = parse_document(path)
    except Exception as e:  # noqa: BLE001 - report the reason, not a traceback
        return _fail(f"Could not parse {path.name}: {type(e).__name__}: {mask_secrets(str(e))}", [], window=False)
    print(f"sections: {len(texts)}")
    print(f"characters: {sum(len(t) for t in texts)}")
    return 0


def _attach_console() -> None:
    """The windowed exe has no stdout/stderr; reuse the console (or redirected handles) of whoever launched it."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        import ctypes
        import msvcrt

        ctypes.windll.kernel32.AttachConsole(-1)  # ATTACH_PARENT_PROCESS
        for std_id, name in ((-11, "stdout"), (-12, "stderr")):
            handle = ctypes.windll.kernel32.GetStdHandle(std_id)
            if handle and handle != -1:
                fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_TEXT)
                setattr(sys, name, os.fdopen(fd, "w", encoding="utf-8", closefd=False))
            else:
                setattr(sys, name, open("CONOUT$", "w", encoding="utf-8"))
    except Exception:  # noqa: BLE001 - no console available; messages are simply lost
        for name in ("stdout", "stderr"):
            if getattr(sys, name) is None:
                setattr(sys, name, open(os.devnull, "w"))


def _fail(message: str, lines: list[str], window: bool) -> int:
    if window:
        _show_page(error_page(message, lines))
    else:
        print("\n".join([message, *lines]), file=sys.stderr)
    return 1


def _show_page(page: str) -> None:
    import webview

    webview.create_window(TITLE, html=page, width=700, height=420)
    webview.start()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ensure_sources_file(settings: Settings) -> None:
    """create_app reads the sources file at start-up; the first run has no inputs yet, so seed an empty one."""
    if not settings.sources_path.exists():
        from qabuddy.desktop.inputs import Inputs
        from qabuddy.desktop.sources import write_sources_file

        write_sources_file(Inputs(), settings.sources_path)


def _run_window(settings: Settings) -> int:
    import uvicorn
    import webview

    from qabuddy.api import create_app
    from qabuddy.desktop.routes import start_startup_sync

    _ensure_sources_file(settings)
    app = create_app(settings)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("The local server did not start")
    start_startup_sync(app)
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # target=_blank links open in the default browser
    webview.create_window(TITLE, f"http://127.0.0.1:{port}/", width=1400, height=900)
    webview.start()
    server.should_exit = True
    return 0


def main(argv: list[str]) -> int:
    measure = "--measure" in argv
    if measure or "--self-check" in argv:
        _attach_console()
    if "--self-check" in argv:
        return self_check(argv)
    overridden = overridden_keys(os.environ, bundle_dir())
    try:
        missing = prepare_environment(os.environ, bundle_dir())
    except SecretsFileError as e:
        return _fail(str(e), [], window=not measure)
    if missing:
        return _fail("Missing required secrets in secrets.local.json:", missing, window=not measure)
    if measure:
        try:
            result = run_measure(load_settings())
        except MeasureError as e:
            return _fail(str(e), [], window=False)
        for key, value in dataclasses.asdict(result).items():
            print(f"{key}: {value}")
        return 0
    configure_logging()
    for key in overridden:
        logging.getLogger(__name__).info("Environment variable %s overrides the bundled value", key)
    try:
        return _run_window(load_settings())
    except Exception as e:  # noqa: BLE001 - show the reason in the window instead of a silent exit
        logging.getLogger(__name__).exception("Start-up failed")
        return _fail("QABuddy could not start", [mask_secrets(str(e) or type(e).__name__)], window=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
