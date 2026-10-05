"""Source code (Java, TypeScript, JavaScript) split along the syntax tree, plus repo Markdown and config files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from qabuddy.ingest.chunking import estimate_tokens, split_by_size
from qabuddy.ingest.context import ParseContext, tag
from qabuddy.ingest.models import Chunk, SourceFile, make_chunk_id
from qabuddy.ingest.normalize import clean_text, mask_secrets, read_text
from qabuddy.ingest.parsers.documents import markdown_chunks

CONFIG_EXT = {".xml", ".json", ".yml", ".yaml", ".properties", ".env"}
MARKDOWN_EXT = {".md", ".mdx"}
TEST_CALLS = {"test", "it", "test.only", "test.skip", "test.fixme", "test.fail", "test.slow", "it.only", "it.skip"}
DESCRIBE_CALLS = {"describe", "test.describe", "test.describe.serial", "test.describe.parallel",
                  "test.describe.only", "test.describe.skip", "describe.only", "describe.skip"}
HOOK_CALLS = {"test.beforeEach", "test.afterEach", "test.beforeAll", "test.afterAll",
              "beforeEach", "afterEach", "beforeAll", "afterAll"}
JAVA_CLASS_TYPES = {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
JAVA_MEMBER_TYPES = {"method_declaration", "constructor_declaration"}
TS_CLASS_TYPES = {"class_declaration", "abstract_class_declaration"}
TS_FUNCTION_TYPES = {"function_declaration", "generator_function_declaration"}
SPLIT_OVERLAP_TOKENS = 40  # ≈ 5 lines of code when a long method is split


@lru_cache(maxsize=None)
def _parser(kind: str):
    from tree_sitter import Language, Parser

    if kind == "java":
        import tree_sitter_java as grammar
        language = Language(grammar.language())
    elif kind == "ts":
        import tree_sitter_typescript as grammar
        language = Language(grammar.language_typescript())
    elif kind == "tsx":
        import tree_sitter_typescript as grammar
        language = Language(grammar.language_tsx())
    else:
        import tree_sitter_javascript as grammar
        language = Language(grammar.language())
    return Parser(language)


@dataclass
class Unit:
    kind: str                       # method | test | class_context | module_context
    start: int                      # 1-based line numbers
    end: int
    scope: list[str]                # e.g. ["class DashBoardPage"] or describe titles
    names: list[str]                # signatures / titles shown in the header
    symbols: list[str] = field(default_factory=list)  # identifiers defined here
    text: str | None = None         # explicit text (context units); else source lines


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace")


def _row(node) -> int:
    return node.start_point[0] + 1


def _end_row(node) -> int:
    return node.end_point[0] + 1


def _leading_comment_start(node) -> int:
    start = _row(node)
    prev = node.prev_named_sibling
    while prev is not None and "comment" in prev.type and _end_row(prev) >= start - 1:
        start = _row(prev)
        prev = prev.prev_named_sibling
    return start


# ---------------------------------------------------------------- Java

def _java_units(root) -> list[Unit]:
    package = next((_text(n) for n in root.named_children if n.type == "package_declaration"), "")
    imports = [_text(n) for n in root.named_children if n.type == "import_declaration"]
    units: list[Unit] = []

    def visit_class(node, outer: list[str]) -> None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node) if name_node else "?"
        scope = [*outer, f"{node.type.split('_')[0]} {name}"]
        body = node.child_by_field_name("body")
        header_end = _row(body) if body else _end_row(node)
        header = "\n".join(_text(node).split("\n")[: header_end - _row(node) + 1])
        context_lines = [package, *imports, header] if not outer else [header]
        if body is not None:
            for member in body.named_children:
                if member.type in JAVA_MEMBER_TYPES:
                    member_name = _text(member.child_by_field_name("name"))
                    params = member.child_by_field_name("parameters")
                    signature = f"{member_name}{_text(params) if params else '()'}"
                    units.append(Unit("method", _leading_comment_start(member), _end_row(member), scope,
                                      [signature], [member_name]))
                elif member.type in JAVA_CLASS_TYPES:
                    visit_class(member, scope)
                elif member.type in ("field_declaration", "constant_declaration", "enum_constant",
                                     "static_initializer", "annotation_type_declaration"):
                    context_lines.append("    " + _text(member))
        context_lines.append("}")
        units.append(Unit("class_context", _row(node), _end_row(node), scope, ["class context"], [name],
                          text="\n".join(l for l in context_lines if l)))

    for child in root.named_children:
        if child.type in JAVA_CLASS_TYPES:
            visit_class(child, [])
    return units


# ---------------------------------------------------------------- TypeScript / JavaScript

def _string_value(node) -> str:
    raw = _text(node)
    return raw[1:-1] if len(raw) >= 2 and raw[0] in "'\"`" else raw


def _call_parts(stmt):
    expr = stmt.named_children[0] if stmt.named_children else None
    if expr is not None and expr.type == "await_expression" and expr.named_children:
        expr = expr.named_children[0]
    if expr is None or expr.type != "call_expression":
        return None
    function = expr.child_by_field_name("function")
    args = expr.child_by_field_name("arguments")
    if function is None or args is None:
        return None
    name = re.sub(r"\s+", "", _text(function))
    arg_nodes = list(args.named_children)
    title = _string_value(arg_nodes[0]) if arg_nodes and arg_nodes[0].type in ("string", "template_string") else ""
    callback = next((a for a in reversed(arg_nodes) if a.type in ("arrow_function", "function_expression", "function")), None)
    return name, title, callback


def _ts_units(root) -> list[Unit]:
    units: list[Unit] = []
    module_lines: list[tuple[int, int, str]] = []

    def add_context(node) -> None:
        module_lines.append((_row(node), _end_row(node), _text(node)))

    def visit_class(node) -> None:
        name_node = node.child_by_field_name("name")
        name = _text(name_node) if name_node else "?"
        scope = [f"class {name}"]
        body = node.child_by_field_name("body")
        header_end = _row(body) if body else _end_row(node)
        lines = ["\n".join(_text(node).split("\n")[: header_end - _row(node) + 1])]
        if body is not None:
            for member in body.named_children:
                if member.type in ("method_definition", "method_signature", "abstract_method_signature"):
                    member_name = _text(member.child_by_field_name("name"))
                    params = member.child_by_field_name("parameters")
                    signature = f"{member_name}{_text(params) if params else '()'}"
                    units.append(Unit("method", _leading_comment_start(member), _end_row(member), scope,
                                      [signature], [member_name]))
                elif "comment" not in member.type:
                    lines.append("    " + _text(member))
        lines.append("}")
        units.append(Unit("class_context", _row(node), _end_row(node), scope, ["class context"], [name],
                          text="\n".join(lines)))

    def visit_statement(node, describe: list[str]) -> None:
        if node.type == "export_statement":
            inner = node.child_by_field_name("declaration") or next(
                (c for c in node.named_children if c.type in TS_CLASS_TYPES | TS_FUNCTION_TYPES
                 | {"lexical_declaration", "variable_declaration"}), None)
            if inner is None:
                add_context(node)
            else:
                visit_statement(inner, describe)
            return
        if node.type in TS_CLASS_TYPES:
            visit_class(node)
        elif node.type in TS_FUNCTION_TYPES:
            name = _text(node.child_by_field_name("name"))
            params = node.child_by_field_name("parameters")
            units.append(Unit("method", _leading_comment_start(node), _end_row(node), [],
                              [f"{name}{_text(params) if params else '()'}"], [name]))
        elif node.type in ("lexical_declaration", "variable_declaration"):
            declarators = [c for c in node.named_children if c.type == "variable_declarator"]
            fn = [d for d in declarators if d.child_by_field_name("value") is not None
                  and d.child_by_field_name("value").type in ("arrow_function", "function_expression", "function")]
            if fn and not describe:
                name = _text(fn[0].child_by_field_name("name"))
                params = fn[0].child_by_field_name("value").child_by_field_name("parameters")
                units.append(Unit("method", _leading_comment_start(node), _end_row(node), [],
                                  [f"{name}{_text(params) if params else '()'}"], [name]))
            else:
                add_context(node)
        elif node.type == "expression_statement" and (parts := _call_parts(node)):
            name, title, callback = parts
            body = callback.child_by_field_name("body") if callback is not None else None
            if name in DESCRIBE_CALLS and body is not None:
                for stmt in body.named_children:
                    visit_statement(stmt, [*describe, title])
            elif name in TEST_CALLS:
                units.append(Unit("test", _row(node), _end_row(node), describe, [f"test: {title}"]))
            elif name in HOOK_CALLS:
                units.append(Unit("method", _row(node), _end_row(node), describe, [name.split(".")[-1]]))
            else:
                add_context(node)
        elif "comment" in node.type:
            return
        else:
            add_context(node)

    for child in root.named_children:
        visit_statement(child, [])
    if module_lines:
        units.append(Unit("module_context", module_lines[0][0], module_lines[-1][1], [], ["module context"],
                          text="\n".join(t for _, _, t in module_lines)))
    return units


# ---------------------------------------------------------------- chunk assembly

def _short_path(rel_path: str, source_folder_depth: int = 3) -> str:
    parts = rel_path.split("/")[source_folder_depth:]  # drop 07_Source_Codes/<source>/<repo>/
    short = "/".join(parts) if parts else rel_path
    return re.sub(r"^src/(main|test)/java/", "", short)


def _merge_small(units: list[Unit], lines: list[str], min_tokens: int, target: int) -> list[Unit]:
    merged: list[Unit] = []
    for unit in units:
        size = estimate_tokens("\n".join(lines[unit.start - 1: unit.end]))
        if merged:
            prev = merged[-1]
            prev_size = estimate_tokens("\n".join(lines[prev.start - 1: prev.end]))
            same_scope = prev.kind == unit.kind == "method" and prev.scope == unit.scope
            if same_scope and (prev_size < min_tokens or size < min_tokens) and \
                    estimate_tokens("\n".join(lines[prev.start - 1: unit.end])) <= target:
                merged[-1] = Unit("method", prev.start, unit.end, prev.scope, prev.names + unit.names,
                                  prev.symbols + unit.symbols)
                continue
        merged.append(unit)
    return merged


def _code_chunks(sf: SourceFile, ctx: ParseContext, source: str, units: list[Unit]) -> list[Chunk]:
    settings = ctx.settings.chunking
    lines = source.split("\n")
    file_name = sf.rel_path.rsplit("/", 1)[-1]
    label = ctx.source_label or sf.source
    short = _short_path(sf.rel_path)
    method_units = sorted((u for u in units if u.text is None), key=lambda u: u.start)
    context_units = [u for u in units if u.text is not None]
    chunks: list[Chunk] = []
    for unit in [*context_units, *_merge_small(method_units, lines, settings.min_tokens, settings.target_tokens)]:
        body = unit.text if unit.text is not None else "\n".join(lines[unit.start - 1: unit.end])
        body = mask_secrets(body, kv_lines=False)
        if estimate_tokens(body) <= settings.max_tokens:
            pieces = [(body, unit.start, unit.end)]
        else:
            offset = unit.start - 1 if unit.text is None else 0
            pieces = [(p, s + offset, e + offset) for p, s, e in
                      split_by_size(body, settings.max_tokens - 100, settings.max_tokens, SPLIT_OVERLAP_TOKENS)]
            if unit.text is not None:
                pieces = [(p, unit.start, unit.end) for p, _, _ in pieces]
        for index, (piece, start, end) in enumerate(pieces):
            names = ", ".join(unit.names) + (f" (part {index + 1})" if len(pieces) > 1 else "")
            header = " › ".join([label, short, *unit.scope, names])
            chunk = Chunk(
                chunk_id=make_chunk_id(sf.rel_path, f"{unit.kind}:{unit.start}-{unit.end}:{index}"),
                source=sf.source,
                kind=unit.kind,
                path=sf.rel_path,
                citation=f"{file_name}:{start}-{end}",
                text=piece,
                header=header,
                location={"line_start": start, "line_end": end},
                title=names,
                breadcrumb=[*(s.split(" ", 1)[-1] for s in unit.scope), *unit.symbols]
                if unit.kind != "test" else [*unit.scope, names],
                parent_id=sf.rel_path,
            )
            chunks.append(tag(chunk, ctx, unit.symbols))
    return chunks


def _config_chunks(sf: SourceFile, ctx: ParseContext) -> list[Chunk]:
    settings = ctx.settings.chunking
    text = mask_secrets(clean_text(read_text(sf.abs_path)), kv_lines=True)
    if not text.strip():
        return []
    file_name = sf.rel_path.rsplit("/", 1)[-1]
    pieces = [(text, 1, text.count("\n") + 1)] if estimate_tokens(text) <= settings.max_tokens else \
        split_by_size(text, settings.target_tokens, settings.max_tokens, SPLIT_OVERLAP_TOKENS)
    chunks = []
    for start_end_index, (piece, start, end) in enumerate(pieces):
        chunk = Chunk(
            chunk_id=make_chunk_id(sf.rel_path, f"config:{start}-{end}:{start_end_index}"),
            source=sf.source,
            kind="config",
            path=sf.rel_path,
            citation=f"{file_name}:{start}-{end}",
            text=piece,
            header=f"{ctx.source_label or sf.source} › {_short_path(sf.rel_path)}",
            location={"line_start": start, "line_end": end},
            title=file_name,
            parent_id=sf.rel_path,
        )
        chunks.append(tag(chunk, ctx))
    return chunks


def parse_code(sf: SourceFile, ctx: ParseContext) -> list[Chunk]:
    suffix = sf.abs_path.suffix.lower()
    name = sf.abs_path.name.lower()
    if suffix in MARKDOWN_EXT:
        return markdown_chunks(sf, ctx, "md_section")
    if suffix in CONFIG_EXT or name == ".env" or name.endswith(".env"):
        return _config_chunks(sf, ctx)
    kind = {".java": "java", ".ts": "ts", ".tsx": "tsx", ".js": "js", ".jsx": "js", ".mjs": "js"}.get(suffix)
    if kind is None:
        return _config_chunks(sf, ctx)
    source = clean_text(read_text(sf.abs_path))
    tree = _parser(kind).parse(source.encode("utf-8"))
    units = _java_units(tree.root_node) if kind == "java" else _ts_units(tree.root_node)
    if not units:
        return _config_chunks(sf, ctx)
    return _code_chunks(sf, ctx, source, units)


def _is_distinctive(name: str) -> bool:
    return len(name) >= 5 and bool(re.search(r"[a-z][A-Z]|_|\d", name) or re.match(r"^[A-Z][a-z]+[A-Z]", name))


def code_entities(chunks: list[Chunk]) -> set[str]:
    names: set[str] = set()
    for chunk in chunks:
        if chunk.kind in ("method", "class_context"):
            names.update(b.lower() for b in chunk.breadcrumb if _is_distinctive(b))
    return names
