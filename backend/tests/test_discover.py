import pytest

from qabuddy.ingest.discover import discover, load_sources


@pytest.fixture(scope="module")
def files(settings, data_dir):
    return discover(settings)


def test_load_sources_order_and_figma_disabled(settings):
    sources = load_sources(settings.config_path / "sources.yaml")
    assert [s.id for s in sources][:3] == ["selenium", "playwright", "testcases"]
    figma = next(s for s in sources if s.id == "figma")
    assert figma.enabled is False


def test_discover_skips_readme_and_binaries(files):
    names = [f.rel_path.rsplit("/", 1)[-1] for f in files]
    assert not any(n.startswith("_README") for n in names)
    assert not any(n.endswith((".zip", ".webm", ".png", ".html")) for n in names)


def test_discover_skips_excluded_dirs(files):
    for f in files:
        parts = f.rel_path.split("/")
        assert not {".idea", "tta-report", "node_modules"} & set(parts), f.rel_path


def test_discover_no_duplicate_hashes(files):
    hashes = [f.file_hash for f in files]
    assert len(hashes) == len(set(hashes))


def test_discover_only_configured_code_copies(files):
    assert not any(f.rel_path.startswith("07_Source_Codes/Advance-Playwright-Framework-main") for f in files)
    assert any(f.source == "selenium" and f.rel_path.endswith("DashBoardPage.java") for f in files)
    assert any(f.source == "playwright" and f.rel_path.endswith(".spec.ts") for f in files)


def test_discover_handles_spaces_in_names(files):
    prd = [f for f in files if f.rel_path.endswith("Product Requirements Document (PRD) VWO.com (1).pdf")]
    assert len(prd) == 1
    assert prd[0].source == "requirements" and prd[0].parser == "documents"
    assert prd[0].abs_path.exists()


def test_discover_figma_disabled(files):
    assert not any(f.source == "figma" for f in files)


def test_discover_paths_use_forward_slashes(files):
    assert all("\\" not in f.rel_path for f in files)
