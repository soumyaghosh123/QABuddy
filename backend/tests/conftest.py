from pathlib import Path

import pytest

from qabuddy.config import REPO_ROOT, load_settings


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def data_dir() -> Path:
    path = REPO_ROOT / "data"
    if not path.exists():
        pytest.skip("data/ folder not present")
    return path
