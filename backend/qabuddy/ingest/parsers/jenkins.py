"""Jenkins results: JUnit/TestNG XML, console logs (TestNG + Playwright), and per-test history."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, fields

from qabuddy.ingest.chunking import estimate_tokens, split_by_size
from qabuddy.ingest.context import ParseContext, tag
from qabuddy.ingest.models import Chunk, SourceFile, make_chunk_id
from qabuddy.ingest.normalize import clean_text, mask_secrets, read_text

TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T[\d:.]+Z)\]\s?")
STAGE_RE = re.compile(r"^\[Pipeline\] \{ \((.+)\)\s*$")
TESTNG_RESULT_RE = re.compile(r"^(PASSED|FAILED|SKIPPED):\s+(\S+)\s*$")
RETRY_RE = re.compile(r"Retrying (\w+) \(attempt (\d+) of (\d+)\)")
EXCEPTION_RE = re.compile(r"^([\w.$]+(?:Exception|Error))(?::\s*(.*))?$")
STACK_LINE_RE = re.compile(r"^\s+(at\s.+|\.\.\. \d+ more)$")
STACK_INFO_RE = re.compile(r"^(Build info|System info|Driver info):")
PW_RESULT_RE = re.compile(
    r"^\s*(✓|✘|-)\s+(\d+)\s+\[([^\]]+)\]\s+›\s+(\S+?):(\d+):(\d+)\s+›\s+(.+?)"
    r"(?:\s+\(retry #(\d+)\))?\s+\(([\d.]+m?s)\)\s*$")
PW_FAILURE_HEADER_RE = re.compile(r"^\s*\d+\)\s+\[[^\]]+\]\s+›\s+(\S+?):\d+:\d+\s+›\s+(.+?)\s*$")
PW_SUMMARY_RE = re.compile(r"^\s*(\d+) (passed|failed|flaky|skipped|did not run)")
PW_ERROR_RE = re.compile(r"^\s*(\w*Error):?\s*(.*)$")
FRAME_EXCLUDE = ("java.", "jdk.", "sun.", "org.openqa.", "org.testng.", "org.junit.", "node:", "java.base/")
MAX_STACK_LINES = 40


@dataclass
class TestRun:
    __test__ = False  # not a pytest test class

    job: str
    build: int
    test_id: str
    class_name: str
    method: str
    title: str
    file: str | None
    status: str  # passed | failed | skipped
    attempt: int
    duration_s: float | None
    error_type: str | None
    error_message: str | None
    source_path: str


def project_frames(trace: str, limit: int = 5) -> list[str]:
    frames = []
    for line in trace.split("\n"):
        match = re.match(r"^\s*at\s+(.+)$", line)
        if not match:
            continue
        target = match.group(1)
        if target.startswith(FRAME_EXCLUDE) or "node_modules" in target:
            continue
        frames.append(f"at {target.strip()}")
        if len(frames) >= limit:
            break
    return frames


def _job_build_from_name(stem: str) -> tuple[str, int]:
    match = re.search(r"([A-Za-z0-9.\-]+)_(\d+)$", stem)
    if match:
        return match.group(1), int(match.group(2))
    return stem, 0


def _duration(value: str) -> float | None:
    try:
        return float(value[:-2]) / 1000 if value.endswith("ms") else float(value.rstrip("s"))
    except ValueError:
        return None


def _short_class(class_name: str) -> str:
    return class_name.rsplit(".", 1)[-1]


def _chunk(sf: SourceFile, ctx: ParseContext, kind: str, locator: str, citation: str, header: str, text: str,
           job: str, build: int, date: str, extra_ids: list[str], location: dict) -> Chunk:
    chunk = Chunk(
        chunk_id=make_chunk_id(sf.rel_path, locator),
        source=sf.source,
        kind=kind,
        path=sf.rel_path,
        citation=citation,
        text=mask_secrets(text, kv_lines=False),
        header=header,
        location=location,
        title=citation,
        parent_id=f"{job}#{build}",
        date=date,
    )
    return tag(chunk, ctx, [f"build-{build}", *extra_ids])


# ---------------------------------------------------------------- JUnit / TestNG XML

def parse_junit(sf: SourceFile, ctx: ParseContext) -> tuple[list[Chunk], list[TestRun]]:
    root = ET.fromstring(read_text(sf.abs_path).lstrip("﻿"))
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    file_job, file_build = _job_build_from_name(sf.abs_path.stem)
    chunks: list[Chunk] = []
    runs: list[TestRun] = []
    for suite in suites:
        props = {p.get("name"): p.get("value") for p in suite.findall("./properties/property")}
        job, build = file_job, file_build
        match = re.match(r"^(.+?)\s*#(\d+)$", props.get("jenkins.build", "") or "")
        if match:
            job, build = match.group(1), int(match.group(2))
        timestamp = suite.get("timestamp", "")
        date = timestamp[:10]
        context = " | ".join(x for x in [f"{job} #{build}", timestamp, suite.get("hostname", ""),
                                         f"browser={props['browser']}" if props.get("browser") else "",
                                         f"suite={props['surefire.suiteXmlFiles']}"
                                         if props.get("surefire.suiteXmlFiles") else ""] if x)
        attempts: dict[str, int] = defaultdict(int)
        for case in suite.findall("testcase"):
            class_name, method = case.get("classname", ""), case.get("name", "")
            test_id = f"{class_name}.{method}"
            attempts[test_id] += 1
            failure = case.find("failure")
            if failure is None:
                failure = case.find("error")
            status = "failed" if failure is not None else "skipped" if case.find("skipped") is not None else "passed"
            trace = (failure.text or "") if failure is not None else ""
            message = (failure.get("message") if failure is not None else None) or (trace.split("\n")[0] if trace else None)
            run = TestRun(job, build, test_id, class_name, method, method, None, status, attempts[test_id],
                          _duration(case.get("time", "")), failure.get("type") if failure is not None else None,
                          message, sf.rel_path)
            runs.append(run)
            if status != "failed":
                continue
            frames = project_frames(trace)
            text = "\n".join([
                f"Jenkins {job} #{build} — FAILED: {_short_class(class_name)}.{method}",
                f"Build: {context}",
                f"Test: {test_id} (attempt {run.attempt}, {run.duration_s} s)",
                f"Error: {run.error_type}: {message}",
                *(["Stack (project frames):", *(f"  {f}" for f in frames)] if frames else []),
            ])
            chunks.append(_chunk(sf, ctx, "test_failure", f"{test_id}#{run.attempt}",
                                 f"{job} #{build} › {method}",
                                 f"Jenkins › {job} #{build} › {_short_class(class_name)}.{method}",
                                 text, job, build, date, [method.lower()],
                                 {"build": build, "test": test_id}))
    return chunks, runs


# ---------------------------------------------------------------- console logs

def _strip_ts(line: str) -> tuple[str, str]:
    match = TIMESTAMP_RE.match(line)
    return (line[match.end():], match.group(1)) if match else (line, "")


def _selenium_error_blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    """Exception line + stack lines, with up to 3 lines of context before. Returns (start, end, text)."""
    blocks = []
    i = 0
    while i < len(lines):
        if EXCEPTION_RE.match(lines[i].strip()) and not lines[i].startswith((" ", "\t")):
            start = i
            j = i + 1
            while j < len(lines) and j - i < MAX_STACK_LINES and (
                    STACK_LINE_RE.match(lines[j]) or STACK_INFO_RE.match(lines[j])):
                j += 1
            context = [l for l in lines[max(0, start - 3):start] if not l.startswith("[Pipeline]")]
            blocks.append((start + 1, j, "\n".join([*context, *lines[start:j]])))
            i = j
        else:
            i += 1
    return blocks


def _playwright_failure_blocks(lines: list[str]) -> list[tuple[int, int, str, str]]:
    """Numbered failure sections: (start, end, test_key, text)."""
    blocks = []
    i = 0
    while i < len(lines):
        header = PW_FAILURE_HEADER_RE.match(lines[i])
        if header:
            start = i
            j = i + 1
            while j < len(lines) and not PW_FAILURE_HEADER_RE.match(lines[j]) and not PW_SUMMARY_RE.match(lines[j]):
                j += 1
            body = "\n".join(l for l in lines[start:j]).strip("\n")
            blocks.append((start + 1, j, f"{header.group(1)}::{header.group(2)}", body))
            i = j
        else:
            i += 1
    return blocks


def parse_console_log(sf: SourceFile, ctx: ParseContext) -> tuple[list[Chunk], list[TestRun]]:
    job, build = _job_build_from_name(sf.abs_path.stem)
    raw_lines = clean_text(read_text(sf.abs_path)).split("\n")
    lines: list[str] = []
    first_ts = ""
    for raw in raw_lines:
        line, ts = _strip_ts(raw)
        first_ts = first_ts or ts
        lines.append(line)
    date = first_ts[:10]

    started = agent = revision = commit = result = ""
    stages: list[str] = []
    commands: list[str] = []
    totals: list[str] = []
    runs: list[TestRun] = []
    attempts: dict[str, int] = defaultdict(int)
    pending_retry: set[str] = set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("Started by") and not started:
            started = stripped
        elif stripped.startswith("Building on") and not agent:
            agent = re.sub(r"^Building on\s+|\s+in workspace.*$", "", stripped)
        elif stripped.startswith("Checking out Revision") and not revision:
            revision = stripped.split()[3][:10]
        elif stripped.startswith("Commit message:") and not commit:
            commit = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Finished:"):
            result = stripped
        elif stripped.startswith("+ ") and len(commands) < 3:
            commands.append(stripped[2:])
        if stage := STAGE_RE.match(stripped):
            stages.append(stage.group(1))
        if re.search(r"Tests run: \d+, Failures: \d+", stripped) and not any("Tests run" in t for t in totals):
            totals.append(stripped.strip("[]WARNING INFO"))
        if stripped.startswith("allure-results:"):
            totals.append(stripped)
        if summary := PW_SUMMARY_RE.match(line):
            totals.append(f"{summary.group(1)} {summary.group(2)}")

        if retry := RETRY_RE.search(stripped):
            pending_retry.add(retry.group(1))
        if testng := TESTNG_RESULT_RE.match(stripped):
            status_word, fqcn = testng.groups()
            class_name, method = fqcn.rsplit(".", 1) if "." in fqcn else ("", fqcn)
            status = status_word.lower()
            if status == "skipped" and method in pending_retry:
                status = "failed"
                pending_retry.discard(method)
            attempts[fqcn] += 1
            error_type = error_message = None
            for follow in lines[index + 1: index + 4]:
                exc = EXCEPTION_RE.match(follow.strip())
                if exc:
                    error_type, error_message = exc.group(1), exc.group(2)
                    break
            if status == "passed":
                error_type = error_message = None
            runs.append(TestRun(job, build, fqcn, class_name, method, method, None, status, attempts[fqcn],
                                None, error_type, error_message, sf.rel_path))
        if pw := PW_RESULT_RE.match(line):
            mark, _, _, file, _, _, title, retry_no, duration = pw.groups()
            status = {"✓": "passed", "✘": "failed", "-": "skipped"}[mark]
            test_id = f"{file}::{title}"
            runs.append(TestRun(job, build, test_id, file, title.split(" › ")[-1], title, file, status,
                                int(retry_no or 0) + 1, _duration(duration), None, None, sf.rel_path))

    chunks: list[Chunk] = []
    pw_blocks = _playwright_failure_blocks(lines)
    for start, end, key, body in pw_blocks:
        error = next((PW_ERROR_RE.match(l) for l in body.split("\n")[1:] if PW_ERROR_RE.match(l)), None)
        for run in runs:
            if run.test_id == key and run.status == "failed" and error:
                run.error_type, run.error_message = error.group(1), error.group(2)
        title = key.split("::", 1)[-1].split(" › ")[-1]
        text = f"Jenkins {job} #{build} — Playwright failure: {title}\n{body}"
        for part, (piece, _, _) in enumerate(split_by_size(text, 600, 800, 0) if estimate_tokens(text) > 800
                                             else [(text, 1, 1)]):
            chunks.append(_chunk(sf, ctx, "error_block", f"pw:{start}:{part}",
                                 f"{job} #{build} › {title} (log line {start})",
                                 f"Jenkins › {job} #{build} › console log › {title}", piece, job, build, date,
                                 [], {"build": build, "line_start": start, "line_end": end}))
    if not pw_blocks:
        seen: dict[str, int] = {}
        blocks = _selenium_error_blocks(lines)
        for start, end, body in blocks:
            key = "\n".join(l.strip() for l in body.split("\n") if l.strip().startswith(("at ", "org.", "com.")))
            seen[key] = seen.get(key, 0) + 1
        emitted: set[str] = set()
        for start, end, body in blocks:
            key = "\n".join(l.strip() for l in body.split("\n") if l.strip().startswith(("at ", "org.", "com.")))
            if key in emitted:
                continue
            emitted.add(key)
            exc_line = next((l for l in body.split("\n") if EXCEPTION_RE.match(l.strip())), "error")
            exc_name = EXCEPTION_RE.match(exc_line.strip()).group(1).rsplit(".", 1)[-1] if EXCEPTION_RE.match(
                exc_line.strip()) else "error"
            count = f" (seen {seen[key]} times in this build)" if seen[key] > 1 else ""
            text = f"Jenkins {job} #{build} — {exc_name} in console log{count}\n{body}"
            chunks.append(_chunk(sf, ctx, "error_block", f"err:{start}",
                                 f"{job} #{build} › {exc_name} (log line {start})",
                                 f"Jenkins › {job} #{build} › console log › {exc_name}", text, job, build, date,
                                 [], {"build": build, "line_start": start, "line_end": end}))

    result_lines = []
    for run in runs[:40]:
        label = run.status.upper() + (" (retried)" if run.status == "failed" and any(
            r.test_id == run.test_id and r.attempt > run.attempt for r in runs) else "")
        result_lines.append(f"- {label}: {run.title if run.file else _short_class(run.class_name) + '.' + run.method}"
                            + (f" (attempt {run.attempt})" if run.attempt > 1 or label.endswith("(retried)") else ""))
    summary_text = "\n".join(x for x in [
        f"Jenkins build {job} #{build} — {result or 'result unknown'}",
        " | ".join(x for x in [started, f"Agent: {agent}" if agent else "", f"Date: {date}" if date else ""] if x),
        " | ".join(x for x in [f"Revision: {revision}" if revision else "",
                               f"Commit message: {commit}" if commit else ""] if x),
        f"Stages: {' → '.join(stages)}" if stages else "",
        *(f"Command: {c}" for c in commands),
        f"Totals: {' | '.join(totals)}" if totals else "",
        "Test results:" if result_lines else "",
        *result_lines,
    ] if x)
    chunks.insert(0, _chunk(sf, ctx, "build_summary", "summary", f"{job} #{build}",
                            f"Jenkins › {job} #{build} › build summary", summary_text, job, build, date, [],
                            {"build": build}))
    return chunks, runs


def merge_runs(runs: list[TestRun]) -> list[TestRun]:
    merged: dict[tuple, TestRun] = {}
    for run in runs:
        key = (run.job, run.build, run.test_id, run.attempt)
        if key not in merged:
            merged[key] = TestRun(**{f.name: getattr(run, f.name) for f in fields(TestRun)})
            continue
        existing = merged[key]
        for f in fields(TestRun):
            if getattr(existing, f.name) in (None, "") and getattr(run, f.name) not in (None, ""):
                setattr(existing, f.name, getattr(run, f.name))
        if run.status == "failed":
            existing.status = "failed"
    return sorted(merged.values(), key=lambda r: (r.job, r.test_id, r.build, r.attempt))


def build_test_history(runs: list[TestRun], ctx: ParseContext) -> list[Chunk]:
    groups: dict[tuple[str, str], list[TestRun]] = defaultdict(list)
    for run in runs:
        groups[(run.job, run.test_id)].append(run)
    chunks: list[Chunk] = []
    for (job, test_id), group in sorted(groups.items()):
        by_build: dict[int, list[TestRun]] = defaultdict(list)
        for run in group:
            by_build[run.build].append(run)
        finals: list[str] = []
        retried_builds = 0
        flaky = False
        lines = []
        for build in sorted(by_build):
            attempts = sorted(by_build[build], key=lambda r: r.attempt)
            final = attempts[-1].status
            finals.append(final)
            failed = [a for a in attempts if a.status == "failed"]
            error = next((f"{a.error_type}: {a.error_message}" for a in failed if a.error_message), "")
            error = (error[:180] + "…") if len(error) > 180 else error
            if len(attempts) > 1:
                retried_builds += 1
            if final == "passed" and failed:
                flaky = True
                line = f"- #{build}: passed after {len(failed)} failed attempt(s)"
            elif final == "failed":
                line = f"- #{build}: failed ({len(attempts)} attempt(s), all failed)" if len(failed) == len(attempts) \
                    else f"- #{build}: failed ({len(attempts)} attempt(s))"
            else:
                line = f"- #{build}: {final}"
            lines.append(line + (f" — {error}" if error else ""))
        changes = sum(1 for a, b in zip(finals, finals[1:]) if a != b)
        if changes and {"passed", "failed"} <= set(finals):
            flaky = True
        flip_rate = changes / (len(finals) - 1) if len(finals) > 1 else 0.0
        sample = group[0]
        name = sample.title if sample.file else f"{_short_class(sample.class_name)}.{sample.method}"
        text = "\n".join([
            f"Test history: {name} ({job})",
            f"Test id: {test_id}",
            f"Flaky: {'yes' if flaky else 'no'} | Builds: {len(finals)} | Final results: "
            f"passed {finals.count('passed')}, failed {finals.count('failed')}, skipped {finals.count('skipped')} | "
            f"Retried builds: {retried_builds} | Flip rate: {flip_rate:.2f}",
            *lines,
        ])
        folder = sample.source_path.rsplit("/", 1)[0] if "/" in sample.source_path else sample.source_path
        chunk = Chunk(
            chunk_id=make_chunk_id("jenkins-history", f"{job}:{test_id}"),
            source="jenkins",
            kind="test_history",
            path=folder,
            citation=f"Test history › {sample.method}",
            text=text,
            header=f"Jenkins › test history › {job} › {test_id}",
            location={"job": job, "test": test_id},
            title=name,
            parent_id=f"history:{job}:{test_id}",
        )
        chunks.append(tag(chunk, ctx, [*(f"build-{b}" for b in sorted(by_build)), sample.method.lower()]))
    return chunks


def parse_jenkins(sf: SourceFile, ctx: ParseContext) -> list[Chunk]:
    if sf.abs_path.suffix.lower() == ".xml":
        chunks, runs = parse_junit(sf, ctx)
    else:
        chunks, runs = parse_console_log(sf, ctx)
    ctx.test_runs.extend(runs)
    return chunks
