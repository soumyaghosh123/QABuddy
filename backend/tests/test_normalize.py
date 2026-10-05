import re

from qabuddy.ingest.normalize import (
    SECRET_KEY_RE,
    clean_text,
    expand_with_glossary,
    extract_ids,
    mask_secrets,
    read_text,
    split_identifiers,
)

PROPS = "07_Source_Codes/01_selenium_framework/ATB13xSeleniumAdvanceFramework-main/src/main/resources/data.properties"
DOTENV = "07_Source_Codes/02_playwright_framework/Advance-Playwright-Framework-main/.env"


def _sensitive_pairs(text: str) -> list[tuple[str, str]]:
    pairs = []
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*[=:]\s*(.+?)\s*$", line)
        if m and not line.lstrip().startswith("#") and SECRET_KEY_RE.search(m.group(1)):
            pairs.append((m.group(1), m.group(2)))
    return pairs


def _assert_masked(original: str):
    masked = mask_secrets(original)
    pairs = _sensitive_pairs(original)
    assert pairs, "expected sensitive keys in the fixture file"
    for key, value in pairs:
        assert re.search(rf"^\s*{re.escape(key)}\s*[=:]\s*\*\*\*\s*$", masked, re.M), key
        if len(value) >= 6:
            assert value not in masked, f"value of {key} survived masking"


def test_mask_secrets_properties_file(data_dir):
    _assert_masked(read_text(data_dir / PROPS))


def test_mask_secrets_env_file(data_dir):
    _assert_masked(read_text(data_dir / DOTENV))


def test_mask_secrets_json_and_yaml_forms():
    text = '{"password": "hunter22", "url": "https://x"}\napi_key: abc123xyz\nuser_name = bob'
    masked = mask_secrets(text)
    assert "hunter22" not in masked and "abc123xyz" not in masked and "bob" not in masked
    assert "https://x" in masked


def test_split_identifiers():
    out = split_identifiers("testLoginPositiveVWO DashBoardPage.loggedInUserName")
    assert "test login positive vwo" in out
    assert "dash board page logged in user name" in out


def test_extract_ids():
    ids = extract_ids("VWO-26 and QAB-101 in build #142", known=set())
    assert set(ids) == {"vwo-26", "qab-101", "build-142"}


def test_extract_ids_known_identifiers():
    ids = extract_ids("WaitHelpers.visibilityOfElement timed out", known={"waithelpers", "visibilityofelement"})
    assert {"waithelpers", "visibilityofelement"} <= set(ids)


def test_read_text_falls_back_to_cp1252(tmp_path):
    path = tmp_path / "x.csv"
    path.write_bytes(b"caf\xe9")
    assert read_text(path) == "café"


def test_clean_text_strips_ansi_and_bom():
    raw = "﻿hello \x1b[31mred\x1b[0m  \r\n\r\n\r\n\r\nworld   "
    assert clean_text(raw) == "hello red\n\n\nworld"


def test_expand_with_glossary():
    out = expand_with_glossary("Which TC covers login?", {"tc": ["test case"]})
    assert out.startswith("Which TC covers login?")
    assert "test case" in out
