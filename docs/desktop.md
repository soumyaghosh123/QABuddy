# QABuddy Desktop

A Windows app that indexes your team's GitHub repos, Zephyr projects and Confluence space into the shared Qdrant Cloud index, then answers questions and builds traceability matrices from it. Everyone who installs it sees the same index; only one PC syncs at a time.

## Prerequisites (build machine)

- [uv](https://docs.astral.sh/uv/) and Node.js 20+
- Inno Setup 6: `winget install JRSoftware.InnoSetup`
- The Microsoft Edge WebView2 runtime (already present on Windows 11)

## Secrets file

Create `secrets.local.json` in the repo root (it is git-ignored; never commit or paste it in chat):

```json
{
  "GITHUB_TOKEN": "",
  "ZEPHYR_TOKEN": "",
  "ATLASSIAN_EMAIL": "",
  "ATLASSIAN_API_TOKEN": "",
  "QDRANT_URL": "",
  "QDRANT_API_KEY": "",
  "CLOUDFLARE_ACCOUNT_ID": "",
  "CLOUDFLARE_API_TOKEN": "",
  "DEEPSEEK_API_KEY": ""
}
```

All nine values are required. Keys not in this list are ignored. An environment variable of the same name takes precedence over the file.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-desktop.ps1
```

The script checks the secrets file (it names missing keys, never values), builds the frontend, runs PyInstaller (`dist\QABuddy\QABuddy.exe`) and then Inno Setup. The installer is `dist\QABuddy-Setup-<version>.exe`; the version comes from `backend\pyproject.toml`. Without Inno Setup the script stops after PyInstaller and tells you to install it.

## Install

Run the installer. It needs no admin rights, installs to `%LOCALAPPDATA%\Programs\QABuddy` and adds Start-menu and desktop shortcuts. Data, the local answer cache and logs live in `%LOCALAPPDATA%\QABuddy\` (`logs\sync.log` rotates at 5 x 1 MB, with secrets masked).

## First run

1. Open QABuddy. If a required secret is missing from the build, a window lists the missing key names and the app exits.
2. Open **Settings** and enter the GitHub repos, Zephyr projects and the Confluence space URL. They are saved in Qdrant, so every installation shares them.
3. A sync starts at every launch; a banner shows progress. Use the Sync button for a manual run.

## Measure

Settings has a **Measure** button that sizes the Confluence space (pages, attachments, estimated chunks, RAM and embedding calls) before you index it. The command line does the same:

```powershell
QABuddy.exe --measure
```

It prints one field per line and exits 0, or prints a message (for example "Set the Confluence space in Settings first") and exits 1.

## Sync

Sync downloads each source, then indexes only what changed. The sync state is mirrored in Qdrant, so a second PC continues incrementally. If another PC is syncing, you get a "Sync already running on <machine>" message.

## Traceability

The **Traceability** view maps a Jira story, a Zephyr folder or a whole project to its Zephyr test cases and automated scenarios, with an Excel export. Links in the match list open in your default browser.

## Accepted token risk

The owner's tokens are baked into `QABuddy.exe`, in plain text inside the installed folder. Anyone who can read the install can extract them, so share the installer only with trusted teammates and use tokens with the least access that works (read-only where possible). Rotate them if an installer leaks. A later version should ask each user for their own tokens instead of bundling the owner's.

Known gap: the packaged build does not include Docling, so Office attachments (docx, pptx) are not parsed and PDFs fall back to the plain-text reader.
