import pytest

from qabuddy.ingest.context import ParseContext
from qabuddy.ingest.discover import discover
from qabuddy.ingest.parsers.jenkins import (
    build_test_history,
    merge_runs,
    parse_console_log,
    parse_jenkins,
    parse_junit,
    project_frames,
)

XML_142 = "TEST-TestSuite_vwo-selenium-regression_142.xml"


@pytest.fixture(scope="module")
def files(settings, data_dir):
    return {f.rel_path.rsplit("/", 1)[-1]: f for f in discover(settings) if f.source == "jenkins"}


@pytest.fixture()
def ctx(settings):
    return ParseContext(settings=settings, source_label="Jenkins", known_entities=set(), glossary={})


def test_junit_142(files, ctx):
    chunks, runs = parse_junit(files[XML_142], ctx)
    assert len(runs) == 4
    assert sum(r.status == "failed" for r in runs) == 2
    assert all(r.job == "vwo-selenium-regression" and r.build == 142 for r in runs)
    failures = [c for c in chunks if c.kind == "test_failure"]
    assert len(failures) == 2
    text = failures[0].text
    assert "DashBoardPage.java:20" in text or "WaitHelpers.java:76" in text
    assert "at org.openqa" not in text
    assert failures[0].citation.startswith("vwo-selenium-regression #142 › ")
    assert "build-142" in failures[0].ids_norm


def test_selenium_log_143_retry(files, ctx):
    _, runs = parse_console_log(files["vwo-selenium-regression_143.log"], ctx)
    login = sorted((r for r in runs if r.method == "testLoginPositiveVWO"), key=lambda r: r.attempt)
    assert [(r.attempt, r.status) for r in login] == [(1, "failed"), (2, "passed")]
    assert "TimeoutException" in (login[0].error_type or "")


def test_playwright_88(files, ctx):
    chunks, runs = parse_console_log(files["playwright-e2e_88.log"], ctx)
    checkout = sorted((r for r in runs if r.title.endswith("should complete checkout successfully")), key=lambda r: r.attempt)
    assert [r.status for r in checkout] == ["failed", "failed", "failed"]
    assert checkout[0].file == "src/tests/e2e/e2e-checkout.spec.ts"
    booking = sorted((r for r in runs if r.title.endswith("create a booking")), key=lambda r: r.attempt)
    assert [(r.attempt, r.status) for r in booking] == [(1, "failed"), (2, "passed")]
    blocks = [c for c in chunks if c.kind == "error_block"]
    assert any("> 56 |" in c.text and "Expected: 1" in c.text for c in blocks)


def test_build_summary_chunk(files, ctx):
    chunks, _ = parse_console_log(files["vwo-selenium-regression_143.log"], ctx)
    [summary] = [c for c in chunks if c.kind == "build_summary"]
    assert "Started by user Rahul Verma" in summary.text
    assert "Enable RetryAnalyzer + ScreenshotListener" in summary.text
    assert "Finished: SUCCESS" in summary.text
    assert "Checkout → Build → Selenium Regression → Publish Allure" in summary.text
    assert summary.citation == "vwo-selenium-regression #143"
    assert "[Pipeline]" not in summary.text


def test_merge_runs_dedupes_xml_and_log_142(files, ctx):
    _, xml_runs = parse_junit(files[XML_142], ctx)
    _, log_runs = parse_console_log(files["vwo-selenium-regression_142.log"], ctx)
    merged = merge_runs(xml_runs + log_runs)
    target = [r for r in merged if r.class_name.endswith("TestVWOLogin_03_Prop_Improved_POM") and r.method == "testLoginPositiveVWO"]
    assert len(target) == 1
    assert target[0].duration_s == pytest.approx(25.34)
    assert target[0].error_message


def test_history_flaky(files, ctx):
    for name in files:
        parse_jenkins(files[name], ctx)
    history = build_test_history(merge_runs(ctx.test_runs), ctx)
    retry_login = [c for c in history if "TestVWOLogin_05" in c.header and "testLoginPositiveVWO" in c.header]
    assert retry_login and "Flaky: yes" in retry_login[0].text
    checkout = [c for c in history if "should complete checkout successfully" in c.header]
    assert checkout and "Flaky: no" in checkout[0].text and "failed" in checkout[0].text
    booking = [c for c in history if "create a booking" in c.header]
    assert booking and "Flaky: yes" in booking[0].text
    assert all(c.kind == "test_history" for c in history)


def test_project_frames_filters_framework_frames():
    trace = (
        "org.openqa.selenium.TimeoutException: boom\n"
        "\tat org.openqa.selenium.support.ui.WebDriverWait.timeoutException(WebDriverWait.java:84)\n"
        "\tat com.thetestingacademy.utils.WaitHelpers.visibilityOfElement(WaitHelpers.java:76)\n"
        "\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)\n"
    )
    assert project_frames(trace) == ["at com.thetestingacademy.utils.WaitHelpers.visibilityOfElement(WaitHelpers.java:76)"]
