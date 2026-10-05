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
    "documents": ("qabuddy.ingest.parsers.documents", "parse_document"),
    "meetings": ("qabuddy.ingest.parsers.meetings", "parse_meeting"),
    "lucid": ("qabuddy.ingest.parsers.lucid", "parse_lucid"),
    "jenkins": ("qabuddy.ingest.parsers.jenkins", "parse_jenkins"),
}


def get_parser(name: str) -> ParseFn:
    if name not in _REGISTRY:
        raise KeyError(f"unknown parser {name!r}")
    module_name, attr = _REGISTRY[name]
    return getattr(importlib.import_module(module_name), attr)


PARSER_NAMES = tuple(_REGISTRY)
