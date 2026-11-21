"""Parser registry: parser name (from sources.yaml) -> parse(SourceFile, ParseContext) -> list[Chunk].

Modules are imported lazily so heavy dependencies (Docling, tree-sitter) load only when needed.
"""

from __future__ import annotations

import importlib
from typing import Callable

from qabuddy.ingest.context import ParseContext
from qabuddy.ingest.models import Chunk, SourceFile

ParseFn = Callable[[SourceFile, ParseContext], list[Chunk]]

_REGISTRY: dict[str, tuple[str, str]] = {
    "code": ("qabuddy.ingest.parsers.code", "parse_code"),
    "testcases": ("qabuddy.ingest.parsers.testcases", "parse_testcases"),
    "jira": ("qabuddy.ingest.parsers.jira", "parse_jira_markdown"),
    "jira_api": ("qabuddy.ingest.parsers.jira_api", "parse_jira_json"),
    "documents": ("qabuddy.ingest.parsers.documents", "parse_document"),
    "meetings": ("qabuddy.ingest.parsers.meetings", "parse_meeting"),
    "lucid": ("qabuddy.ingest.parsers.lucid", "parse_lucid"),
    "gherkin": ("qabuddy.ingest.parsers.gherkin", "parse_gherkin"),
    "jenkins": ("qabuddy.ingest.parsers.jenkins", "parse_jenkins"),
    "zephyr": ("qabuddy.ingest.parsers.zephyr", "parse_zephyr"),
    "confluence": ("qabuddy.ingest.parsers.confluence", "parse_confluence_page"),
    "confluence_attachment": ("qabuddy.ingest.parsers.confluence", "parse_confluence_attachment"),
}


def get_parser(name: str) -> ParseFn:
    if name not in _REGISTRY:
        raise KeyError(f"unknown parser {name!r}")
    module_name, attr = _REGISTRY[name]
    return getattr(importlib.import_module(module_name), attr)


PARSER_NAMES = tuple(_REGISTRY)
