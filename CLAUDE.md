# Trader Desk Pro: instructions for working in this repo

Local-first trading desk: FastAPI backend (`backend/traderdesk`), React + Vite UI (`frontend/`),
and the day-trader Claude skills (`skills/day-trader`). User-facing docs: [README.md](README.md)
(overview), [SETUP.md](SETUP.md) (install and use), [skills/README.md](skills/README.md) (skills).

## Safety rules (read first)

This app can place real orders with real money. These rules hold for every change and every session.

1. **Never place, modify or cancel a live order** yourself, and never switch the mode to live, tick
   "live orders allowed" or release the kill switch. Those are the owner's actions, in the UI.
2. **Never read, print, copy or commit secrets.** That covers Kite API secrets and access tokens,
   `.env`, `config.json`, `access_token.txt`, Windows Credential Manager entries (service
   `trader-desk-pro`), and anything in `D:\code\kite\kite` holding credentials. If you find one in a
   file, tell the user to rotate it; do not repeat it.
3. **Do not weaken the order desk.** The five gates in `orders/desk.py` (ticket, risk, typed
   confirm, idempotent send, track) stay in that order for every order, paper or live. No "skip
   confirm", no override of a BLOCK, no retry that can send twice. Exits must never be blocked by
   entry-only rules.
4. **Keep it local.** The server binds to 127.0.0.1. Do not add `0.0.0.0`, CORS `*`, or a telemetry
   call. Docker ports bind to 127.0.0.1.
5. **Every state change is audited.** New endpoints that change state call `audit.record(...)` and
   check a permission with `need("<perm>")`. Never write to `audit_log` other than through
   `core/audit.py`, and never edit or delete its rows.
6. **Money is data, not code.** Charges go in `config/charges.yaml` as a new dated entry; risk
   defaults in `config/risk.yaml`; holidays in `config/holidays/`. Never edit an old charges entry to
   change a rate; add one with a new `from:` date.
7. **Read-only connectors stay [READ]** until order placement is verified on a live account.
   **Signals and the AI assistant never trade.** A signal becomes a pre-filled ticket the user
   confirms; the assistant's tools are read-only. Do not add auto-execution or a write tool.
8. **No invented numbers on screens.** When a data source is missing, show what is needed (as the
   option chain, FII/DII and contract-note panels do), never placeholder figures.
9. Do not commit or push unless the user asks.

## Commands

```bat
start.bat                                              :: set up on first run, then serve on :8766
.venv\Scripts\python -m pytest tests                   :: backend tests (112, incl. tests/test_security.py)
.venv\Scripts\python -m pytest tests\test_desk.py -k oco
cd skills\day-trader && ..\..\.venv\Scripts\python -m pytest tests -p no:cacheprovider   :: skill tests (315)
cd frontend && npm run dev                             :: UI dev server :5173, proxies /api to :8766
cd frontend && npm run build                           :: UI into frontend\dist (served by the backend)
.venv\Scripts\traderdesk-import <folder>               :: import portfolio_*.json snapshots
```

Run the backend tests after any backend change and the skill tests after any change under
`skills/`. Set `PYTHONIOENCODING=utf-8` when a script prints ₹ on the Windows console.

To stop a server you started, stop it by its process id (find it with
`Get-NetTCPConnection -LocalPort <port>`). Never kill every `python.exe`.

## Layout

```
backend/traderdesk/
  app/main.py          create_app(): auth, accounts, orders, rules, audit routes; auth deps (need); SPA
  app/trading_api.py   register(app, ctx): market data, desk overview, positions, options, charges,
                       signals, briefs, providers, the scheduler thread (off when a clock is injected)
  app/wealth_api.py    register(app, ctx): portfolio overview, metals, tax, money, dashboards, users,
                       sessions, AI assistant (read-only tools, claude-sonnet-5-5 via the anthropic SDK)
  market/yahoo.py      MarketData(fetch=...): delayed bars/quotes; tests pass a fake fetch
  analysis/            strategies.py (ORB, VWAP, backtest, regime, pivots), brief.py (pre/post market)
  tax/engine.py        compute() from config/tax.yaml; orders/pnl.py FIFO realised P/L
  core/models.py       domain dataclasses + enums (OrderRequest.fingerprint() binds the confirm)
  core/auth.py         Argon2, TOTP, ROLES -> permissions, SessionStore
  core/audit.py        hash-chained append-only log: record(), verify()
  core/secrets.py      KeyringStore / MemoryStore; keys look like "<account>:<name>"
  core/calendar.py     IST session, holidays, nse_session()
  core/ratelimit.py    token buckets per plugin limits
  brokers/base.py      BrokerAdapter Protocol, Cap capabilities, PluginManifest
  brokers/registry.py  discovers plugins/*/plugin.yaml + entry point group traderdesk.brokers
  plugins/kite/        Kite Connect adapter (client injectable for tests)
  plugins/paper/       simulator: set_price(), GTT/OCO, AMO, failure hooks for tests
  plugins/_rest.py     read-only REST base for dhan/, upstox/, indstocks/; plus csv_import/, cas/, ccxt_exchange/
  plugins/email_imap/  any-provider IMAP source: EXAMINE + BODY.PEEK only (never STORE/MOVE/EXPUNGE);
                       parse_alert() for bank/card/UPI mails; imap_factory injectable for tests
  app/connectors_api.py Upstox callback, PIN+TOTP token login, CSV/CAS upload, AMFI NAV refresh, email
                       test/scan/import/attachment (scan imports nothing; EmailSeen stops doubles), disconnect
  orders/desk.py       OrderDesk: the five gates, modify/cancel/cancel_all/flatten/kill
  orders/risk.py       check(order, rules, ctx) -> RiskVerdict
  orders/charges.py    ChargesEngine, round_to_tick, round_to_lot
  portfolio/           snapshot importer, net worth
  db/models.py         SQLAlchemy tables
  config/              charges.yaml, risk.yaml, tax.yaml, holidays/NSE.yaml
frontend/src/          App.jsx (shell, hash routes #/screen?params, role-filtered nav, top bar),
                       ui.jsx (Card, Kpi, Tabs, Verdict, Confirm, TicketResult), PriceChart.jsx
                       (lightweight-charts), api.js (fetch + CSRF, formatters), screens/*.jsx
skills/day-trader/     vendored Claude skills (see "The skills folder" below)
tests/                 backend tests; conftest.py pins the clock to 2026-11-16 10:30 IST
```

## Conventions

- **Python 3.12**, type hints, `from __future__ import annotations`. Dataclasses for domain
  objects; pydantic only at the HTTP edge.
- **Time:** store UTC; show and reason in IST (`core.calendar.IST`). Anything time-dependent takes
  the injected `clock` (see `OrderDesk._utcnow()`), never `datetime.now()` directly, so tests can
  pin it.
- **Brokers:** a new broker is a plugin folder with `plugin.yaml`, `settings.schema.json` and an
  adapter class built as `cls(account_id=, settings=, secrets=, manifest=)`. Declare capabilities
  honestly; the desk calls `require(adapter, Cap.X)` before using one. Secrets in the schema get
  `"secret": true` and go to the keychain; the API rejects secret fields sent as settings. Map broker
  errors to `BrokerError(ErrorKind...)` and statuses to `OrderStatus`.
- **Orders:** client tags are `TDP-<plan[:8]>-<id>`, 20 characters at most (Kite's limit). Any new
  path that sends must look up by tag before a retry.
- **API:** routes under `/api`, permission via `Depends(need("area:action"))`, state-changing
  requests need the `X-CSRF` header (the UI's `api()` adds it). Add new permissions to `ROLES` in
  `core/auth.py`.
- **UI:** plain JSX, React Query for data, colours from the CSS tokens in `styles.css` (light and
  dark). Keep layouts working at phone width.
- **Tests:** use the paper adapter and `MemoryStore`; never hit a real broker or network in tests.
  The Kite adapter takes a fake client. Add a test with every behaviour change in `orders/`.

## The skills folder

`skills/day-trader` is a copy of the published Day Trader skill pack (US markets, Yahoo data,
Alpaca/IBKR for orders). Its own tests check the repo shape, so:

- do not add files or folders at `skills/day-trader/` root (only `.claude-plugin`, `LICENSE`,
  `README.md`, `tests` and the eleven `trader-*` folders are allowed);
- keep each `SKILL.md` frontmatter (`name`, a description over 200 characters) and the
  "not financial advice" and "yes" wording in the order-path skills;
- no em dash characters in any text file there, and tools must stay Python 3.8 compatible
  with no third-party imports;
- the README must keep starting with `# The Day Trader skill` and keep its install lines.

Notes about how the skills fit Trader Desk Pro go in `skills/README.md`.

## Not built yet

Order placement through the read-only connectors, contract-note parsing, live tick streaming, PDF
import for metals, Gmail API OAuth (email is IMAP + app password), FII/DII and OI data, the LLM step in the brief pipeline, non-app reminders, passkeys. The
full list is in README.md → "Not built yet".
