import os
import re
import subprocess
import sys

from qabuddy.config import REPO_ROOT

HEAVY = ("docling", "fastembed", "onnxruntime", "ollama", "tree_sitter", "openpyxl", "pypdf", "torch")
CODE = f"""
import sys
for name in {HEAVY!r}:
    sys.modules[name] = None  # any import of these now fails
import vercel_app
print(vercel_app.settings.profile, vercel_app.app.title)
"""


def test_cloud_entrypoint_imports_without_heavy_packages(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "QABUDDY_PROFILE"}
    env["QABUDDY_CACHE__DB_PATH"] = str(tmp_path / "cache.db")
    result = subprocess.run([sys.executable, "-c", CODE], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "cloud QABuddy.ai"


def test_vercelignore_excludes_private_folders():
    lines = set((REPO_ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines())
    assert {"data/", "storage/", "tools/", ".env", "*.env"} <= lines


def test_requirements_are_slim():
    names = set()
    for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            names.add(re.split(r"[<>=\[ ]", line.strip(), maxsplit=1)[0].lower())
    heavy = {"docling", "fastembed", "onnxruntime", "ollama", "tree-sitter", "openpyxl", "pypdf", "torch"}
    assert not names & heavy
    assert {"mmh3", "py-rust-stemmers", "fastapi", "qdrant-client"} <= names
