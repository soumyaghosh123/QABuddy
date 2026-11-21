# PyInstaller spec: one-folder build dist/QABuddy/QABuddy.exe
#   uv run pyinstaller packaging/qabuddy.spec   (run from the repo root; scripts/build-desktop.ps1 does this)
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "config"), "config"),
    (str(ROOT / "backend" / "qabuddy" / "index" / "bm25_english.txt"), "qabuddy/index"),
    (str(ROOT / "secrets.local.json"), "."),
]

hiddenimports = (
    ["tree_sitter", "tree_sitter_java", "tree_sitter_typescript", "tree_sitter_javascript", "fastembed"]
    + collect_submodules("qabuddy")  # desktop modules are imported lazily by api.py
    + collect_submodules("uvicorn")
)
binaries = []

# Docling (PDF/Word/Excel/PowerPoint attachments) is imported lazily, so collect it whole
for package in ("docling", "docling_core", "docling_parse", "docling_ibm_models"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [str(ROOT / "backend" / "qabuddy" / "desktop" / "app.py")],
    pathex=[str(ROOT / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["ollama"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="QABuddy", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="QABuddy")
