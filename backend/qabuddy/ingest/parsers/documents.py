"""Company docs and requirement documents: Markdown and PDF, split by heading then size."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from qabuddy.ingest.chunking import Section, estimate_tokens, merge_small, split_by_size, split_markdown
from qabuddy.ingest.context import ParseContext, tag
from qabuddy.ingest.models import Chunk, SourceFile, make_chunk_id
from qabuddy.ingest.normalize import clean_text, mask_secrets, read_text

log = logging.getLogger(__name__)
SKIP_LABELS = {"page_header", "page_footer", "picture", "caption"}


def title_from_path(path: Path) -> str:
    stem = re.sub(r"\s*\(\d+\)$", "", path.stem)
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}[_ -]+", "", stem)
    return re.sub(r"[_]+", " ", stem).strip()


def _crumb_text(title: str, breadcrumb: list[str]) -> str:
    crumbs = breadcrumb[1:] if breadcrumb and breadcrumb[0] == title else breadcrumb
    return " › ".join([title, *crumbs])


def sections_to_chunks(sf: SourceFile, ctx: ParseContext, sections: list[Section], title: str, kind: str,
                       page_mode: bool = False, preamble: str = "", date: str = "",
                       line_pages: list[int] | None = None) -> list[Chunk]:
    """Turn sections into size-limited chunks with breadcrumb headers.

    page_mode: line numbers are PDF line indexes; `line_pages` maps each 1-based line to its page.
    """
    settings = ctx.settings.chunking
    sections = merge_small(sections, settings.min_tokens, settings.target_tokens)
    label = ctx.source_label or sf.source
    chunks: list[Chunk] = []
    for section in sections:
        pieces = split_by_size(section.text, settings.target_tokens, settings.max_tokens,
                               settings.prose_overlap_tokens)
        for piece, start, end in pieces:
            abs_start, abs_end = section.line_start + start - 1, section.line_start + end - 1
            crumb = _crumb_text(title, section.breadcrumb)
            last = section.breadcrumb[-1] if section.breadcrumb else title
            if page_mode:
                page = line_pages[abs_start - 1] if line_pages else abs_start
                location = {"page": page}
                citation = f"{title} p.{page}" + (f" › {last}" if last != title else "")
                locator = f"p{page}:{abs_start}"
            else:
                location = {"line_start": abs_start, "line_end": abs_end}
                citation = f"{title} › {last}" if last != title else title
                locator = f"{abs_start}-{abs_end}"
            text = f"{preamble}\n{piece}" if preamble else piece
            chunk = Chunk(
                chunk_id=make_chunk_id(sf.rel_path, locator),
                source=sf.source,
                kind=kind,
                path=sf.rel_path,
                citation=citation,
                text=text,
                header=f"{label} › {crumb}",
                location=location,
                title=title,
                breadcrumb=section.breadcrumb,
                parent_id=sf.rel_path,
                date=date,
            )
            chunks.append(tag(chunk, ctx))
    return chunks


def markdown_chunks(sf: SourceFile, ctx: ParseContext, kind: str, title: str | None = None,
                    preamble: str = "", date: str = "") -> list[Chunk]:
    text = mask_secrets(clean_text(read_text(sf.abs_path)), kv_lines=False)
    title = title or title_from_path(sf.abs_path)
    return sections_to_chunks(sf, ctx, split_markdown(text, title), title, kind, preamble=preamble, date=date)


@lru_cache(maxsize=1)
def _docling_converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = False
    options.do_table_structure = True
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


def _docling_lines(path: Path) -> tuple[list[str], list[int]]:
    """Return Markdown-ish lines and the page number of each line."""
    document = _docling_converter().convert(str(path)).document
    lines: list[str] = []
    pages: list[int] = []
    for item, _level in document.iterate_items():
        label = str(getattr(item, "label", "")).split(".")[-1].lower()
        if label in SKIP_LABELS:
            continue
        prov = getattr(item, "prov", None) or []
        page = prov[0].page_no if prov else (pages[-1] if pages else 1)
        if label == "table":
            try:
                text = item.export_to_markdown(doc=document)
            except TypeError:
                text = item.export_to_markdown()
        else:
            text = (getattr(item, "text", "") or "").strip()
            if not text:
                continue
            if label == "title":
                text = f"# {text}"
            elif label == "section_header":
                level = min(max(int(getattr(item, "level", 1)) + 1, 2), 3)
                text = f"{'#' * level} {text}"
            elif label == "list_item":
                text = f"- {text}"
        for line in text.split("\n"):
            lines.append(line)
            pages.append(page)
        lines.append("")
        pages.append(page)
    return lines, pages


def _pypdf_lines(path: Path) -> tuple[list[str], list[int]]:
    from pypdf import PdfReader

    lines: list[str] = []
    pages: list[int] = []
    for number, page in enumerate(PdfReader(str(path)).pages, 1):
        for line in (page.extract_text() or "").split("\n"):
            lines.append(line)
            pages.append(number)
        lines.append("")
        pages.append(number)
    return lines, pages


def pdf_to_sections(path: Path, title: str) -> tuple[list[Section], list[int]]:
    try:
        lines, pages = _docling_lines(path)
        if not any(line.strip() for line in lines):
            raise ValueError("Docling returned no text")
    except Exception as exc:  # noqa: BLE001 - any Docling failure falls back to pypdf
        log.warning("Docling failed on %s (%s); falling back to pypdf", path.name, exc)
        lines, pages = _pypdf_lines(path)
    text = mask_secrets(clean_text("\n".join(lines)), kv_lines=False)
    lines = text.split("\n")
    pages = pages[: len(lines)] + [pages[-1] if pages else 1] * max(0, len(lines) - len(pages))
    return split_markdown(text, title), pages


def parse_document(sf: SourceFile, ctx: ParseContext) -> list[Chunk]:
    title = title_from_path(sf.abs_path)
    if sf.abs_path.suffix.lower() == ".pdf":
        sections, pages = pdf_to_sections(sf.abs_path, title)
        return sections_to_chunks(sf, ctx, sections, title, "doc_section", page_mode=True, line_pages=pages)
    return markdown_chunks(sf, ctx, "doc_section", title=title)


def estimate_document_tokens(chunks: list[Chunk]) -> int:
    return sum(estimate_tokens(c.text) for c in chunks)
