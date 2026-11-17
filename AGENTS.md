# AGENTS.md

## Project

Personal Hyperliquid copy-trading site: several self-owned wallets follow a target
address's perpetual positions by ratio, started with `docker compose`. A FastAPI
backend (also serving the built React app) runs one serial engine loop.

Current stage: design only, no implementation yet. `docs/PLAN.md` is the
authoritative design (architecture, decision table, data model, AC mapping,
trade-offs). Read it before changing behavior.

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Feature docs

Pre-PRD discussions live at `docs/feature/<release>/<feature-slug>/discussion.md`. See `docs/agents/feature-docs.md`.

### Worktrees

Implementation tasks use isolated worktrees under `.worktrees/<task-slug>`. See `docs/agents/worktrees.md`.

## Repository engineering

### Layout

- `backend/hlcopy/` — `main.py` (app factory, static mount, engine startup), `config.py`, `crypto.py` (Fernet), `db.py` (schema + repositories), `hl/` (adapter), `engine/`, `api/`
- `backend/tests/` — pytest
- `frontend/` — React 18 + Vite + TS, build output statically mounted by FastAPI
- `docs/PLAN.md` — authoritative design
- `scripts/probe.py` — planned: capture real HL error strings (needs testnet credentials)

### Commands

Run from the repo root (Makefile at the root):

- `make test` — pytest pure-logic tests, no network (`cd backend && uv run --frozen pytest`)
- `make dev` — uvicorn on `127.0.0.1:8080` plus Vite on `127.0.0.1:5173` (Vite proxies `/api`)
- `make up` — `docker compose up -d --build`; `make logs`, `make down`

First run: `cp .env.example .env` and set `HL_SECRET_ENC_KEY` (missing key is a fatal startup error). Local dev uses `DATA_DIR=../data`, which the Makefile sets.

Toolchain: backend dependencies via `uv` (`backend/pyproject.toml` + `uv.lock`, `uv sync --frozen` in the image); frontend dependencies via `npm` (`frontend/package.json` + `package-lock.json`, `npm ci` in the image).

### Module boundaries

- `engine/planner.py` is pure: no `hyperliquid` import, no DB, no network, so tests assert it directly.
- `hl/` owns all SDK/network/signing; signatures are built only in `hl/exchange.py`.
- `engine/loop.py` is the only order placer; its serial loop is the mutual exclusion for `(task, coin)`.
- The `WsListener` thread hands `userFills` to the engine queue — that queue is the only async/sync boundary.
- `db.py` is the single SQLite writer; one app replica, one uvicorn worker.

### Numeric contract

- Quantities, prices, ratios, leverage use `decimal.Decimal` end to end; `float` never reaches an order parameter, comparison, or DB write.
- Rounding uses `Decimal.quantize(..., ROUND_DOWN)` (toward zero), never `round()`.
- SQLite numeric columns are `TEXT` holding canonical Decimal strings; only ids, millisecond timestamps, booleans and counters are `INTEGER`.
- API numeric fields serialize as strings; the frontend displays and compares, never accumulates.
- `szDecimals` and max leverage come from `/info meta` (loaded at startup, refreshed every 60s).

### Security contract

- Private keys are Fernet-encrypted in `wallets.secret_enc`, never returned by an API, never logged.
- Decryption happens only in `hl/exchange.py`, just before building a short-lived signer.
- Missing `HL_SECRET_ENC_KEY` at startup is fatal; there is no plaintext fallback.
- Logs and `exec_logs.raw` mask anything matching 64-hex key material.
- Only API-wallet (agent) keys are imported; a master-account key is never accepted.
- `wallets.address` (master account) is `UNIQUE`: one follower per master account.

### Behavioral invariants

- Events are hints, not commands: every round recomputes from fresh full leader and follower state.
- A failed leader-state read is never treated as a flat position.
- Reversal is two orders: reduce-only close, then open at the new target.
- `cloid = blake2b(f"{task_id}|{coin}|{target}|{reverse_leg}")[:16]`; retries reuse it.
- A per-task exception skips only that task's current round.
