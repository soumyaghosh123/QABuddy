# QABuddy on Vercel

Production: **https://qabuddy-mauve.vercel.app** (Vercel team `qa-explorer`, project `qabuddy`)

```
Browser → Vercel: React UI (frontend/) + one Python function (vercel_app.py, FastAPI)
                    ├─ Qdrant Cloud (free, AWS us-east-1) — vectors + index manifest
                    ├─ Cloudflare Workers AI — qwen3-embedding-0.6b (embeddings), bge-reranker-base (reranking)
                    └─ DeepSeek — writes the answer
Laptop: ingest (Docling + Ollama + local Qdrant) → `qabuddy publish` → Qdrant Cloud
```

The app is **public**: anyone with the link can ask questions. There is no login.

## Accounts and keys

| Service | What you need | Where to get it |
|---|---|---|
| Qdrant Cloud | Free cluster on AWS `us-east-1`, its endpoint and an API key | cloud.qdrant.io → your cluster → Endpoint / API Keys |
| Cloudflare | Account ID and an API token with Workers AI permission | dash.cloudflare.com → AI → Workers AI → Use REST API |
| DeepSeek | API key | platform.deepseek.com |

Keep the Cloudflare account on the **Workers Free** plan. On that plan, Workers AI stops at 10,000 neurons a day instead of billing you.

## Environment variables

They go in two places, and never in a file in this repo.

| Name | Laptop (`setx NAME "value"`, then restart VS Code/terminals) | Vercel (Production) |
|---|---|---|
| `QDRANT_URL` | yes, for `publish` and the cloud eval. Include `:6333` | yes |
| `QDRANT_API_KEY` | yes | yes |
| `CLOUDFLARE_ACCOUNT_ID` | yes | yes |
| `CLOUDFLARE_API_TOKEN` | yes | yes |
| `DEEPSEEK_API_KEY` | yes (already set for the local app) | yes |

To add the values on Vercel, copy them from your laptop's settings to the clipboard:

```powershell
'DEEPSEEK_API_KEY','QDRANT_URL','QDRANT_API_KEY','CLOUDFLARE_ACCOUNT_ID','CLOUDFLARE_API_TOKEN' | ForEach-Object { "$_=$([Environment]::GetEnvironmentVariable($_,'User'))" } | Set-Clipboard
```

Then open https://vercel.com/qa-explorer/qabuddy/settings/environment-variables, click **Add Environment Variable**, paste into the first **Key** box (Vercel splits the five lines into rows), tick **Production**, and click **Save**. A changed value takes effect only after the next deploy.

## After every ingest: publish

The cloud index is a mirror of the local one. After `qabuddy ingest` changes anything, run:

```powershell
cd backend
uv run qabuddy publish --dry-run   # shows new / changed / deleted chunk counts, writes nothing
uv run qabuddy publish             # re-embeds only new or changed chunks via Cloudflare
```

There's no redeploy needed. The app picks up the new manifest within 5 minutes.

`publish` refuses to run if the local index is empty, so a fresh or broken local Qdrant can never wipe the cloud index. If `publish` fails part-way, run it again: it is safe to repeat.

## Check retrieval quality

```powershell
cd backend
$env:QABUDDY_PROFILE = 'cloud'; uv run qabuddy eval; Remove-Item Env:QABUDDY_PROFILE
```

Add `--write` to save a re-calibrated "no answer" cutoff to `config/settings.cloud.yaml`. The 2026-10-08 baseline was hit@6 95.65 % (22/23) and MRR 0.913 at cutoff `0.0194`.

The reranker cannot separate "What is the salary band for an SDET II?" from real questions. That question reaches DeepSeek, which answers "could not find it in the knowledge base", as it is instructed to.

## Deploy

From the repo root:

```powershell
vercel deploy --prod --scope qa-explorer   # production
vercel deploy --scope qa-explorer          # preview, needs the variables ticked for Preview too
```

`vercel.json` uses Vercel Services: `frontend/` is served at `/` and `vercel_app.py` at `/api/*`. `.vercelignore` keeps `data/`, `storage/`, `tools/` and `.env*` out of every upload.

The Vercel project is also connected to the GitHub repo `soumyaghosh123/QABuddy`, so a push to `main` triggers a production build. Disconnect it under Project → Settings → Git if you'd rather deploy only from the CLI.

To check a deployment:

```powershell
curl https://qabuddy-mauve.vercel.app/api/health
```

The response should have `status.level` set to `ok` and four `ok` pills.

## Keep-alive and limits

- A Vercel cron calls `/api/health` daily. Each call queries Qdrant, so the free cluster never reaches its one-week inactivity suspension (it is deleted after four weeks idle).
- Costs:
  - Cloudflare: re-embedding the whole index uses about 550 neurons; a question uses a few.
  - DeepSeek: about 3K tokens per answered question. Keep a small prepaid balance.
- The answer cache lives in each Vercel instance's `/tmp`, so it is lost on cold starts.
