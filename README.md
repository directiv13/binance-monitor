# binance-monitor

Real-time monitor for one or more **Binance USD-M Futures** accounts. It

- posts to a **topic of a Telegram supergroup** whenever an order is created, updated or removed, or a position is opened or closed;
- keeps **one pinned message** in that topic with all open positions and open orders of all accounts;
- sends a **Pushover** push to every subscriber when a position opens or closes;
- lets admins **add / remove Binance accounts at runtime** from a private chat with the bot (no restart).

Python 3.12, asyncio, aiogram 3, httpx, websockets, SQLite (aiosqlite), Fernet-encrypted API keys. The Binance client is written in-house (no ccxt / python-binance).

---

## Quick start

```bash
cp .env.example .env          # then edit it (see "Configuration")
docker compose up --build
```

Then DM the bot: `/add_account main <api_key> <secret>`.

### Generate the encryption key

`ENCRYPTION_KEY` encrypts the Binance API keys/secrets stored in SQLite. Generate it once and keep it safe (losing it means re-adding the accounts):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Telegram setup

1. **Create the bot** with [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token into `TELEGRAM_BOT_TOKEN`.
2. **Create a supergroup with topics**: group settings → *Topics* → enable. Create (or pick) the topic the bot should post in.
3. **Add the bot to the group and make it an administrator** with at least:
   - **Pin messages** (required for the pinned summary),
   - **Delete messages** (lets it remove credentials if someone pastes `/add_account` into the group by mistake),
   - **Manage topics is not needed**.
4. **`TELEGRAM_CHAT_ID`**: for a supergroup it is `-100` followed by the internal id. Open any message of the group in Telegram Web/Desktop and *Copy message link*; a link like `https://t.me/c/1234567890/2/15` means chat id `-1001234567890`.
5. **`TELEGRAM_TOPIC_ID`** (`message_thread_id`): in the same kind of link the **second number** is the topic id (`.../c/1234567890/`**`2`**`/15` → `2`). For the topic's first message / the *General* topic the id is `1`. Alternative: send a message in the topic, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `message_thread_id` (stop the bot first, `getUpdates` conflicts with polling).
6. **`ADMIN_IDS`**: Telegram user ids (numbers) of the people allowed to manage accounts, e.g. from [@userinfobot](https://t.me/userinfobot).

The bot uses long polling; no inbound ports are required.

---

## Configuration (`.env`)

Only `.env.example` is committed. The app validates the config at startup and exits with a list of problems if something is missing or malformed.

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | – | Bot token |
| `TELEGRAM_CHAT_ID` | – | Target supergroup, e.g. `-100…` |
| `TELEGRAM_TOPIC_ID` | – | `message_thread_id` of the topic |
| `ADMIN_IDS` | – | Comma-separated Telegram user ids allowed to run admin commands |
| `PUSHOVER_APP_TOKEN` | – | Pushover application token |
| `PUSHOVER_PRIORITY` | `2` | Pushover priority: `2` = emergency (repeats until acknowledged), `-2`…`1` = quieter levels |
| `PUSHOVER_RETRY_SECONDS` | `60` | Emergency only: re-alert interval (Pushover minimum 30) |
| `PUSHOVER_EXPIRE_SECONDS` | `3600` | Emergency only: stop re-alerting after this long (Pushover maximum 10800) |
| `ENCRYPTION_KEY` | – | Fernet key for API keys/secrets (see above) |
| `DB_PATH` | `/data/monitor.db` | SQLite file (WAL mode) |
| `BINANCE_FAPI_URL` | `https://fapi.binance.com` | REST base URL |
| `BINANCE_FSTREAM_URL` | `wss://fstream.binance.com` | WebSocket base URL |
| `PUSHOVER_API_URL` | `https://api.pushover.net` | Pushover base URL |
| `TELEGRAM_API_URL` | `https://api.telegram.org` | Telegram Bot API base URL |
| `PINNED_REFRESH_SECONDS` | `60` | Periodic refresh of mark price / PnL in the pinned message |
| `RECONCILE_SECONDS` | `300` | Periodic REST reconciliation |
| `LOG_LEVEL` | `INFO` | Log level |
| `HEARTBEAT_PATH` | `/data/heartbeat` | Heartbeat file used by the Docker healthcheck |

---

## Commands

All commands work **in private chat only**. In the group the bot only posts; if someone tries a private-only command there, it replies "Please DM me" (and deletes `/add_account` messages to protect the credentials).

### Admin (user id must be in `ADMIN_IDS`)

| Command | What it does |
|---|---|
| `/add_account <name> <api_key> <secret>` (also `/add-account …`) | Deletes your message immediately, validates the name (`[A-Za-z0-9_-]{1,32}`, unique), checks the credentials with a signed Binance call, stores them Fernet-encrypted and starts monitoring. If Binance rejects them, you get the Binance error and nothing is stored. The reply never contains the key or secret. |
| `/accounts` | Account names, status (`connected` / `error` / `connecting`) and when each was added |
| `/remove_account <name>` | Stops the monitor and deletes the account's stored data |

Non-admins get "not allowed" and the attempt is logged.

### Users

| Command | What it does |
|---|---|
| `/subscribe <pushover_user_key>` | Validates the key with Pushover, then stores it (re-subscribing replaces the key) |
| `/unsubscribe` | Removes the subscription (fine if there was none) |
| `/start`, `/help` | Usage |

---

## What gets posted

```
🟢 CLOSE LONG ORDER · main-acc

SKHYNIXUSDT · MARKET

💲Price: market (~740.34000)
🤑Size: 0.06 SKHYNIX
💵Size in USDT: 44.42 USDT

🟢 CLOSE LONG ORDER FILLED · main-acc

SKHYNIXUSDT · MARKET

💲Price: market (~741.70000)
🤑Size: 0.06 SKHYNIX
💵Size in USDT: 44.50 USDT
✅Filled: 0.06

📈 POSITION OPENED · main-acc

SKHYNIXUSDT · LONG · 5x

💲Entry price: 735.71000
🤑Size: 0.15 SKHYNIX
💵Size in USDT: 109.93 USDT
```

In order and position messages the symbol is sent as `<code>`, so tapping it copies it. Conditional orders add a `🛑Stop:` line.

Pushover (emergency priority by default; 🟢 = long, 🔴 = short):

```
Title:   🟢 OPEN LONG · SKHYNIXUSDT · main-acc
Message: LONG · 5x
         Entry price: 735.71000
         Size: 0.15 SKHYNIX
         Size in USDT: 109.93

Title:   🟢 CLOSE LONG · SKHYNIXUSDT · main-acc
Message: LONG · 5x
         Entry price: 735.71000
         Size: 0.15 SKHYNIX
         Size in USDT: 110.56

         Realized PnL: +0.62 USDT
```

- **Orders:** `NEW` → *new order*; `AMENDMENT` → *updated*; final status `FILLED / CANCELED / EXPIRED / EXPIRED_IN_MATCH / REJECTED` → *removed*, the title ends with the outcome (`… ORDER FILLED`, `… ORDER CANCELED`, …). `❌` is used for canceled orders only; filled and triggered orders get `🟢`, expired / rejected / no longer open (found on resync) get `⚠️`. **Partial fills are not posted**; they only update `filled_qty` and the pinned message.
- **Direction** (Open/Close Long/Short, Flip in one-way mode) is computed once, when the order is created, from the side / positionSide / reduceOnly / closePosition fields and the stored position, and reused for later messages.
- **Positions** (from `ACCOUNT_UPDATE`): 0→non-zero = opened, non-zero→0 = closed, a one-way sign flip = closed then opened. Pure size changes only update the DB and the pinned message. Pushover is sent on open/close only. Realized PnL on close is summed from the closing trades' `rp` when available.
- **Reconciliation** on every (re)connect and every `RECONCILE_SECONDS`: a fresh REST snapshot is diffed against the DB and anything missed is posted with `(detected on resync)`.
- **Restarts do not re-post** existing orders/positions: state is persisted, and the very first snapshot of a newly added account is stored silently.
- **One account failing never affects the others.** It is marked `error`, retried with exponential backoff + jitter, and the topic gets one warning when it goes down and one when it recovers.
- **Pinned summary:** edits are debounced (≥ 3 s apart), skipped when unchanged, and if the text would exceed Telegram's 4096 characters it degrades gracefully (orders per symbol → compact positions → counts → whole accounts omitted) instead of cutting a line.

---

## Binance API notes

Verified against the current developer docs while building this (see the "Verification" section for what could not be checked):

- **User data stream URL** is `wss://fstream.binance.com/private/ws/<listenKey>`. The legacy `/ws/<listenKey>` and `/stream` paths were announced to stop working on 2026-04-23.
- A listenKey lives **60 minutes** (the monitor sends `PUT` keepalives every 30 min); a connection lives at most **24 h** (the monitor reconnects at 23 h). `listenKeyExpired` and a keepalive `-1125` trigger a planned reconnect.
- **Conditional orders** (`STOP`, `STOP_MARKET`, `TAKE_PROFIT`, `TAKE_PROFIT_MARKET`, `TRAILING_STOP_MARKET`) live in the **Algo Service**: separate REST endpoints (`/fapi/v1/algoOrder`, open ones via `/fapi/v1/openAlgoOrders`) and the `ALGO_UPDATE` stream event (order object under `ao`, trigger price `tp`, statuses `NEW / TRIGGERING / TRIGGERED / FINISHED / CANCELED / REJECTED / EXPIRED`). They are stored with `is_algo = 1` and handled like orders; a `TRIGGERED`/`FINISHED` algo order is reported as *removed (triggered)* and the resulting matching-engine order follows as a normal order.
- Positions use `GET /fapi/v3/positionRisk`; credentials are validated with `GET /fapi/v3/account`; position mode with `GET /fapi/v1/positionSide/dual`. Leverage is taken from `positionRisk` when present and from `GET /fapi/v1/symbolConfig` otherwise.
- Signing: HMAC-SHA256 over the query string, `X-MBX-APIKEY` header, `recvWindow=5000`; the server-time offset is tracked and a `-1021` is retried once after resyncing; `429`/`418` honour `Retry-After`.
- USDT size: `price × qty`; for market orders / orders without a price: average fill price → last fill price → trigger price → mark price.

---

## Security

- **Use read-only Binance API keys** (enable *Reading* only; never trading or withdrawals) **restricted to your VPS IP**. This service never places orders.
- API keys and secrets are stored Fernet-encrypted; they are never logged (only masked as `abcd…wxyz` where shown at all), and the `/add_account` message is deleted before anything else happens.
- Admin commands only work in private chat and only for `ADMIN_IDS`.
- `.env` and `*.db` are in `.gitignore` and `.dockerignore`. The container runs as a non-root user.

---

## Development

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

Tests use no real network: Binance and Pushover REST are mocked with `respx`, Telegram calls are recorded, and the WebSocket connector is tested against a local server. Payload fixtures live in `tests/fixtures/samples.json`.

Layout: `binance/` (client, stream parsing, models) · `monitor/` (pure `direction`, `lifecycle`, `diff`; I/O in `account_monitor`, `manager`) · `notify/` (formatting, pinned summary, Pushover, Telegram) · `bot/` (aiogram handlers) · `db/` (connection, numbered SQL migrations, repositories).

---

## Deployment (GitHub Actions → GHCR → VPS)

`.github/workflows/ci-cd.yml`:

1. **PR and push:** `ruff check`, `ruff format --check`, `mypy`, `pytest`.
2. **Push to `main`** (after tests): build the image and push `ghcr.io/<owner>/binance-monitor:latest` and `:<sha>` (layer cache via GitHub Actions cache).
3. **Deploy job** (`environment: production`, one deploy at a time): SSH to the VPS and run `cd /opt/binance-monitor && docker compose pull && docker compose up -d && docker image prune -f`. **The `.env` lives only on the VPS; CI never sees it.**

### Repository secrets (environment `production`)

| Secret | |
|---|---|
| `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` | required (private key of the deploy user) |
| `VPS_PORT` | optional, default 22 |
| `VPS_KNOWN_HOSTS` | optional; pin the server's host key (otherwise `ssh-keyscan` trust-on-first-use) |

### One-time VPS setup

```bash
# 1. Docker (Debian/Ubuntu)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker <deploy-user>

# 2. Project directory
sudo mkdir -p /opt/binance-monitor && sudo chown <deploy-user>: /opt/binance-monitor
cd /opt/binance-monitor

# 3. Compose file and environment (from this repo)
cp <repo>/deploy/docker-compose.prod.yml docker-compose.yml
cp <repo>/.env.example .env && chmod 600 .env && $EDITOR .env
echo 'IMAGE=ghcr.io/<owner>/binance-monitor:latest' >> .env   # only needed for manual runs

# 4. Private repo/package only: log in to GHCR with a read-only PAT (scope read:packages)
echo <PAT> | docker login ghcr.io -u <github-user> --password-stdin

# 5. Deploy key: add the PUBLIC half of VPS_SSH_KEY to ~/.ssh/authorized_keys of <deploy-user>
```

Then push to `main`. Add the VPS IP to the Binance API key's IP allow-list.

### Container behaviour

Multi-stage build on `python:3.12-slim`, non-root user, `PYTHONUNBUFFERED=1`, `STOPSIGNAL SIGTERM`. On SIGTERM the bot stops polling, monitors are cancelled (their WebSockets close and listenKeys are released), and the DB is checkpointed and closed. The healthcheck verifies that `/data/heartbeat` (rewritten every 15 s) is younger than 60 s. State lives in the named volume `monitor-data` mounted at `/data` (if you switch to a `./data` bind mount, `chown 10001:10001 data` first).

---

## Verification status

Checked in this repository: `ruff check`, `ruff format --check`, `mypy` (strict) and 134 `pytest` tests all pass; the config fail-fast output, the compose files' syntax and the wheel contents (SQL migrations included) were checked.

**Not verified here:**

- Nothing has been run against **live Binance, Telegram or Pushover**. Test with a small read-only key and a throwaway group before relying on it.
- The **Docker image build / `docker compose up --build`** was not run (the Docker daemon was not available while developing); the GitHub workflow has not run either.
- Parts of the current Binance docs could not be read in full: the exact `positionRisk` v3 field list, the `ORDER_TRADE_UPDATE` / `ACCOUNT_UPDATE` pages (field keys follow the long-standing payloads and the task description), and the path of the open-algo-orders endpoint (`/fapi/v1/openAlgoOrders` is assumed; if it answers 404 the monitor logs a warning, skips algo reconciliation and keeps the stored algo orders instead of reporting them removed). Parsers are tolerant to missing fields.
