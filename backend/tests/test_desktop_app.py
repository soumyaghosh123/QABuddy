import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from qabuddy.desktop import app as desktop_app
from qabuddy.desktop.inputs import Inputs
from qabuddy.desktop.secrets import REQUIRED


def write_secrets(bundle: Path, **overrides) -> None:
    data = {k: f"value-{k}" for k in REQUIRED}
    data.update(overrides)
    (bundle / "secrets.local.json").write_text(json.dumps(data), encoding="utf-8")


def test_prepare_environment_sets_profile_and_paths(tmp_path):
    write_secrets(tmp_path, EXTRA_KEY="not-allowed")
    env = {"LOCALAPPDATA": str(tmp_path / "local")}
    missing = desktop_app.prepare_environment(env, tmp_path)
    assert missing == []
    assert env["QABUDDY_PROFILE"] == "desktop"
    root = tmp_path / "local" / "QABuddy"
    assert env["QABUDDY_DATA_DIR"] == str(root / "data")
    assert env["QABUDDY_STORAGE_DIR"] == str(root / "storage")
    assert env["QABUDDY_SOURCES_FILE"] == str(root / "storage" / "sources.desktop.yaml")
    assert env["QABUDDY_CACHE__DB_PATH"] == str(root / "storage" / "cache.db")
    assert env["GITHUB_TOKEN"] == "value-GITHUB_TOKEN"
    assert "EXTRA_KEY" not in env  # only REQUIRED keys are injected


def test_prepare_environment_reports_missing_secrets(tmp_path):
    write_secrets(tmp_path, ZEPHYR_TOKEN="")
    env = {"LOCALAPPDATA": str(tmp_path / "local")}
    assert desktop_app.prepare_environment(env, tmp_path) == ["ZEPHYR_TOKEN"]
    env2 = {"LOCALAPPDATA": str(tmp_path / "local")}
    assert desktop_app.prepare_environment(env2, tmp_path / "nowhere") == list(REQUIRED)


@dataclass
class FakeMeasurement:
    pages: int = 12
    attachments: int = 3


class FakeMeta:
    def __init__(self, spaces):
        self.spaces = spaces

    def read_inputs(self):
        return Inputs(confluence_spaces=self.spaces)


def clean_env(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_app, "bundle_dir", lambda: tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in REQUIRED}
    env["LOCALAPPDATA"] = str(tmp_path / "local")
    monkeypatch.setattr(os, "environ", env)  # main() mutates the environment; keep that out of other tests


def test_measure_flag_prints_numbers(tmp_path, capsys, monkeypatch):
    write_secrets(tmp_path)
    clean_env(monkeypatch, tmp_path)
    monkeypatch.setattr(desktop_app, "run_measure", lambda settings: FakeMeasurement())
    assert desktop_app.main(["--measure"]) == 0
    assert capsys.readouterr().out.splitlines() == ["pages: 12", "attachments: 3"]


def test_measure_without_space_asks_for_settings():
    services = type("S", (), {"meta": FakeMeta([])})()
    with pytest.raises(desktop_app.MeasureError, match="Set the Confluence space in Settings first"):
        desktop_app.run_measure(None, services=services)


def test_measure_error_exits_one_with_message(tmp_path, capsys, monkeypatch):
    write_secrets(tmp_path)
    clean_env(monkeypatch, tmp_path)

    def boom(settings):
        raise desktop_app.MeasureError("Set the Confluence space in Settings first")

    monkeypatch.setattr(desktop_app, "run_measure", boom)
    assert desktop_app.main(["--measure"]) == 1
    assert "Set the Confluence space in Settings first" in capsys.readouterr().err


def test_measure_reports_missing_secret_names_only(tmp_path, capsys, monkeypatch):
    write_secrets(tmp_path, DEEPSEEK_API_KEY="")
    clean_env(monkeypatch, tmp_path)
    assert desktop_app.main(["--measure"]) == 1
    err = capsys.readouterr().err
    assert "DEEPSEEK_API_KEY" in err and "value-" not in err


def test_malformed_secrets_file_is_a_message_not_a_traceback(tmp_path, capsys, monkeypatch):
    (tmp_path / "secrets.local.json").write_text("{nope", encoding="utf-8")
    clean_env(monkeypatch, tmp_path)
    assert desktop_app.main(["--measure"]) == 1
    err = capsys.readouterr().err
    assert "not valid JSON" in err and "Traceback" not in err


def test_overridden_keys_are_names_only(tmp_path):
    write_secrets(tmp_path)
    env = {"GITHUB_TOKEN": "live-secret-value", "QDRANT_URL": ""}
    assert desktop_app.overridden_keys(env, tmp_path) == ["GITHUB_TOKEN"]


def test_main_logs_overridden_key_names_not_values(tmp_path, monkeypatch, caplog):
    write_secrets(tmp_path)
    clean_env(monkeypatch, tmp_path)
    os.environ["GITHUB_TOKEN"] = "live-secret-value"
    monkeypatch.setattr(desktop_app, "configure_logging", lambda: None)
    monkeypatch.setattr(desktop_app, "_run_window", lambda settings: 0)
    with caplog.at_level("INFO"):
        assert desktop_app.main([]) == 0
    assert "GITHUB_TOKEN" in caplog.text
    assert "live-secret-value" not in caplog.text and "value-GITHUB_TOKEN" not in caplog.text


def test_self_check_prints_section_and_character_counts(tmp_path, capsys, monkeypatch):
    doc = tmp_path / "a.docx"
    doc.write_bytes(b"x")
    monkeypatch.setattr(desktop_app, "parse_document", lambda path: ["abc", "de"])
    assert desktop_app.main(["--self-check", str(doc)]) == 0
    assert capsys.readouterr().out.splitlines() == ["sections: 2", "characters: 5"]


def test_self_check_failure_is_a_message(tmp_path, capsys):
    assert desktop_app.main(["--self-check", str(tmp_path / "missing.docx")]) == 1
    assert "not found" in capsys.readouterr().err


def test_error_page_lists_names_and_escapes():
    page = desktop_app.error_page("Missing", ["A_KEY", "<b>"])
    assert "A_KEY" in page and "&lt;b&gt;" in page and "<b>" not in page
