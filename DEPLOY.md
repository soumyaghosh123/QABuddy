# Deploy QABuddy on a DigitalOcean droplet

This guide runs QABuddy on your own Ubuntu droplet. Search runs entirely on the server: Qdrant, the qwen3 embedding model and the reranker. Answers are written by DeepSeek, and you can switch to a local model (see [Keeping real data private](#keeping-real-data-private)).

You keep ingesting on your laptop as today, then copy the finished index to the droplet with a few commands. The droplet never needs `data/` or the PDF parser.

> **Status:** written 2026-10-10 and not yet run end to end on a live droplet. If a step fails, check [Troubleshooting](#troubleshooting) and fix this file.
> For the Vercel setup, see [docs/deploy-vercel.md](docs/deploy-vercel.md).

```
Browser ──HTTPS──► Caddy (:443) ──► QABuddy API + web UI (127.0.0.1:8000)
                                       ├─ Qdrant (127.0.0.1:6333)  ── index copied from the laptop
                                       ├─ Ollama (127.0.0.1:11434) ── qwen3-embedding:0.6b for questions
                                       ├─ Reranker (in-process)    ── ms-marco-MiniLM-L-12-v2
                                       └─ DeepSeek API             ── writes the answer

Laptop: qabuddy ingest ──► local Qdrant + storage/qabuddy.db ──(snapshot + scp)──► droplet
```

Only Caddy is reachable from the internet. Qdrant, Ollama and the app listen on `127.0.0.1` only.

## Before you start

- **A DigitalOcean account** and an SSH key added to it.
- **Your laptop running QABuddy locally**, with an up-to-date index: `scripts\start.ps1` works, and `cd backend; uv run qabuddy ingest` has finished.
- **A DeepSeek API key.**
- **Optional: a domain name** you can point at the droplet. Without one, you can use a free `sslip.io` hostname (step 9).

## 1. Create the droplet

In the DigitalOcean control panel, choose **Create → Droplets**:

| Setting | Value |
|---|---|
| Image | **Ubuntu 24.04 (LTS) x64** |
| Plan | Basic, Regular, **4 GB RAM / 2 vCPU / 80 GB disk** (about $24/month) |
| Region | the one closest to your team |
| Authentication | your SSH key |

Note the droplet's public IP address. The rest of this guide calls it `<droplet-ip>`.

2 GB of RAM is too tight. The embedding model, reranker, Qdrant and the app together need about 2 GB, plus room for updates.

## 2. Prepare the server

Log in from your laptop:

```bash
ssh root@<droplet-ip>
```

Update the system, add swap (it helps the first dependency install and UI build), and turn on the firewall:

```bash
apt update && apt -y upgrade
apt install -y git curl

fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

ufw allow OpenSSH
ufw allow 80,443/tcp
ufw --force enable
```

Create a dedicated user that owns everything QABuddy runs:

```bash
useradd --system --create-home --home-dir /srv/qabuddy --shell /bin/bash qabuddy
install -d -o qabuddy -g qabuddy /srv/qabuddy/transfer
```

## 3. Install Qdrant

Use **the same Qdrant version as your laptop**, so the index snapshot restores cleanly. `scripts/setup.ps1` installs `v1.19.1`.

```bash
QDRANT_VERSION=v1.19.1
install -d -o qabuddy -g qabuddy /srv/qabuddy/qdrant
curl -fsSL "https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/qdrant-x86_64-unknown-linux-gnu.tar.gz" \
  | tar -xz -C /srv/qabuddy/qdrant
chown -R qabuddy:qabuddy /srv/qabuddy/qdrant

tee /etc/systemd/system/qdrant.service > /dev/null <<'EOF'
[Unit]
Description=Qdrant vector database
After=network.target

[Service]
User=qabuddy
WorkingDirectory=/srv/qabuddy/qdrant
Environment=QDRANT__SERVICE__HOST=127.0.0.1
Environment=QDRANT__STORAGE__STORAGE_PATH=/srv/qabuddy/qdrant/storage
Environment=QDRANT__STORAGE__SNAPSHOTS_PATH=/srv/qabuddy/qdrant/snapshots
Environment=QDRANT__TELEMETRY_DISABLED=true
ExecStart=/srv/qabuddy/qdrant/qdrant
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now qdrant
curl -s http://127.0.0.1:6333/     # prints Qdrant's name and version
```

## 4. Install Ollama and the embedding model

Questions must be embedded with **the same model your laptop used for ingest**, `qwen3-embedding:0.6b`.

```bash
curl -fsSL https://ollama.com/install.sh | sh     # installs and starts the "ollama" service on 127.0.0.1:11434
ollama pull qwen3-embedding:0.6b
ollama list                                       # should list qwen3-embedding:0.6b
```

## 5. Install QABuddy

Install `uv`, which manages Python, and Node.js 20, which builds the web UI:

```bash
sudo -u qabuddy -H bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
```

Get the code, install the Python dependencies, and build the web UI:

```bash
sudo -u qabuddy git clone https://github.com/soumyaghosh123/QABuddy.git /srv/qabuddy/app
sudo -u qabuddy mkdir -p /srv/qabuddy/app/storage

cd /srv/qabuddy/app/backend
sudo -u qabuddy -H /srv/qabuddy/.local/bin/uv sync --no-dev

cd /srv/qabuddy/app/frontend
sudo -u qabuddy -H npm ci --no-audit --no-fund
sudo -u qabuddy -H npm run build
```

`uv sync` downloads several GB the first time, mostly PyTorch for the PDF parser. The server never loads it, but it's part of the same package. Expect 5–15 minutes.

## 6. Add the DeepSeek key

The key lives in a root-owned file that only the `qabuddy` group can read. Never put it in the repo.

```bash
install -m 640 -o root -g qabuddy /dev/null /etc/qabuddy.env
nano /etc/qabuddy.env
```

Add this line, save, and exit:

```
DEEPSEEK_API_KEY=<your DeepSeek key>
```

## 7. Run QABuddy as a service

```bash
tee /etc/systemd/system/qabuddy.service > /dev/null <<'EOF'
[Unit]
Description=QABuddy web app and API
After=network.target qdrant.service ollama.service
Wants=qdrant.service ollama.service

[Service]
User=qabuddy
WorkingDirectory=/srv/qabuddy/app/backend
EnvironmentFile=/etc/qabuddy.env
Environment=FASTEMBED_CACHE_PATH=/srv/qabuddy/.cache/fastembed
ExecStart=/srv/qabuddy/app/backend/.venv/bin/qabuddy serve --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now qabuddy
curl -s http://127.0.0.1:8000/api/health
```

The health check answers, but reports the index as empty or missing (status "Degraded") until step 8.

## 8. Copy the index from your laptop

Repeat this step whenever you re-ingest on the laptop.

**On the laptop** (PowerShell, in the repo folder, with `scripts\start.ps1` running so the local Qdrant is up):

```powershell
# 1. Snapshot the Qdrant collection and download it
$snap = (Invoke-RestMethod -Method Post http://127.0.0.1:6333/collections/qabuddy_chunks/snapshots).result.name
Invoke-WebRequest "http://127.0.0.1:6333/collections/qabuddy_chunks/snapshots/$snap" -OutFile storage\qabuddy_chunks.snapshot
Invoke-RestMethod -Method Delete "http://127.0.0.1:6333/collections/qabuddy_chunks/snapshots/$snap" | Out-Null

# 2. Make a consistent copy of the SQLite catalog (index version, entities, stats)
cd backend
uv run python -c "import sqlite3; s=sqlite3.connect('../storage/qabuddy.db'); d=sqlite3.connect('../storage/qabuddy-transfer.db'); s.backup(d); d.close()"
cd ..

# 3. Send both files to the droplet (scp is built into Windows 10/11)
scp storage\qabuddy_chunks.snapshot storage\qabuddy-transfer.db root@<droplet-ip>:/srv/qabuddy/transfer/
```

**On the droplet:**

```bash
systemctl stop qabuddy

curl -fsS -X POST "http://127.0.0.1:6333/collections/qabuddy_chunks/snapshots/upload?priority=snapshot&wait=true" \
  -F "snapshot=@/srv/qabuddy/transfer/qabuddy_chunks.snapshot"

install -o qabuddy -g qabuddy -m 640 /srv/qabuddy/transfer/qabuddy-transfer.db /srv/qabuddy/app/storage/qabuddy.db
rm /srv/qabuddy/transfer/*

systemctl start qabuddy
curl -s http://127.0.0.1:8000/api/health      # the status should now say "Ready" with your chunk count
```

The upload replaces the droplet's collection completely. Copying the catalog also clears the answer cache, so no stale answers survive.

## 9. Put it on HTTPS with Caddy

First point a DNS **A record** for your hostname at `<droplet-ip>`, for example `qabuddy.example.com`, and wait until it resolves.

- **No domain?** Use `<droplet-ip-with-dashes>.sslip.io` as the hostname, for example `203-0-113-10.sslip.io`. It resolves to your IP automatically.

```bash
apt install -y caddy

tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
qabuddy.example.com {
	reverse_proxy 127.0.0.1:8000 {
		flush_interval -1
	}
}
EOF

systemctl reload caddy
```

- **Replace `qabuddy.example.com`** with your hostname. Caddy fetches and renews the HTTPS certificate on its own.
- **Keep `flush_interval -1`:** it streams answers word by word instead of holding them back until the end.

### Optional: put a password on it

The app has no login of its own. To keep it internal, add basic auth. Create a password hash:

```bash
caddy hash-password       # type the password twice and copy the hash it prints
```

Then add a `basicauth` block inside the site block of `/etc/caddy/Caddyfile`, and run `systemctl reload caddy`:

```
qabuddy.example.com {
	basicauth {
		qa <paste-the-hash-here>
	}
	reverse_proxy 127.0.0.1:8000 {
		flush_interval -1
	}
}
```

Everyone signs in with the user `qa` and that password. Another option is to allow ports 80/443 only from your office IP range in `ufw`, or in a DigitalOcean Cloud Firewall.

## 10. Check it works

1. Open `https://<your-hostname>`.
2. **Status strip:** the top-left pill shows **✓ Ready · N chunks**, and the Vector DB, Embeddings, Reranker and LLM pills have green dots.
3. **Ask a question:** click one of the questions on the spider web. Source cards appear first, then the answer streams in with `[n]` citations.
4. **Open a source:** click a citation chip. The source viewer opens with the full text.

The first question can take a few extra seconds while the reranker model (about 120 MB) downloads.

## Day-to-day

| Task | How |
|---|---|
| **New data** | On the laptop: `cd backend; uv run qabuddy ingest`, then repeat [step 8](#8-copy-the-index-from-your-laptop) |
| **New app version** | See the commands below |
| **Logs** | `journalctl -u qabuddy -f` (also `-u qdrant`, `-u ollama`, `-u caddy`) |
| **Restart** | `systemctl restart qabuddy` |
| **Backups** | Nothing on the droplet is unique except `/etc/qabuddy.env`. The index can always be copied again from the laptop. |

To install a new app version:

```bash
cd /srv/qabuddy/app && sudo -u qabuddy git pull
cd backend  && sudo -u qabuddy -H /srv/qabuddy/.local/bin/uv sync --no-dev
cd ../frontend && sudo -u qabuddy -H npm ci --no-audit --no-fund && sudo -u qabuddy -H npm run build
systemctl restart qabuddy
```

## Keeping real data private

With DeepSeek, each question and the retrieved source snippets are sent to DeepSeek's API. That's fine for the current sample data.

For confidential company data, write answers on the droplet instead:

1. Resize the droplet to at least **8 GB RAM**. CPU-Optimized is best, because answers are generated on the CPU.
2. Pull the local model:

   ```bash
   ollama pull qwen3:4b-instruct-2507-q4_K_M
   ```

3. Add this line to `/etc/qabuddy.env`:

   ```
   QABUDDY_LLM__PROVIDER=ollama
   ```

4. Run `systemctl restart qabuddy`.

Expect noticeably slower answers than with DeepSeek, as on the laptop. With this switch, no data leaves the server.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Caddy shows **502 Bad Gateway** | The app isn't running. Check `systemctl status qabuddy` and `journalctl -u qabuddy -n 50`. |
| **Vector DB** pill is red, or the chat says *Knowledge base offline* | Check `systemctl status qdrant`, and that `curl -s http://127.0.0.1:6333/` answers. |
| Status pill says **Degraded**, and its tooltip mentions an empty index | The index hasn't been copied yet. Do [step 8](#8-copy-the-index-from-your-laptop). |
| Snapshot upload fails | The Qdrant versions differ. Install the laptop's version in step 3 (check `tools\qdrant` or `scripts\setup.ps1`). |
| **Embeddings** pill says `Run: ollama pull …` | Run that exact command, then `systemctl restart qabuddy`. |
| **LLM** pill is red | `DEEPSEEK_API_KEY` is missing or wrong in `/etc/qabuddy.env`. Fix it, then `systemctl restart qabuddy`. |
| Answers arrive all at once instead of streaming | `flush_interval -1` is missing from the Caddyfile. |
| No HTTPS certificate | The DNS record doesn't point at the droplet yet, or ports 80/443 are blocked. Check `journalctl -u caddy -n 50`. |
| Results look unrelated to the question | The droplet's embedding model differs from the laptop's. Both must use `qwen3-embedding:0.6b`. |
