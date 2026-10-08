"""Vercel entrypoint: the QABuddy API with the cloud profile (spec §7)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QABUDDY_PROFILE", "cloud")
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from qabuddy.api import create_app  # noqa: E402
from qabuddy.config import load_settings  # noqa: E402

settings = load_settings()
app = create_app(settings)
