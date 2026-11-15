# Trader Desk Pro

One local-first app that merges `D:\Personal\trader-desk` (the day-trader skills) and `D:\code\kite`
(the Kite scripts). It tracks holdings across accounts and puts every order through the same five
gates: ticket, risk check, typed confirmation, send, track. It starts in paper mode.

Built from the plan "Trader Desk Pro — Unified Trading Platform Plan" and its UI designs. It has the
foundation (login, roles, audit log, secrets, broker plugins, the order desk) and every screen in the
designs:

| Area | Screens |
| --- | --- |
| Trading | Desk, Charts, Algo signals, Trade, Order book, Options (chain + strategy builder), Briefs |
| Wealth | Portfolio, Digital metals, Tax centre, Charges |
| Money | Expenses, Bills and reminders, Budgets |
| Workspace | Dashboards, AI assistant |
| Settings | Connections (with paper/live and risk rules), Analysis providers, Users and audit |

Market data for charts, briefs and signals is Yahoo Finance's free delayed feed (Kite's personal plan
has no quote API). The option chain needs Kite's paid Connect plan. Where a screen needs a source that
isn't connected, it says so instead of showing made-up numbers.

## Docs

| File | For |
| --- | --- |
| [SETUP.md](SETUP.md) | Install, connect Kite, daily routine, placing an order, paper and live, troubleshooting |
| [skills/README.md](skills/README.md) | The Claude skills: what they do, how they fit with the app, installing them |
| [CLAUDE.md](CLAUDE.md) | Working on the code: safety rules, commands, layout, conventions |

## Run it

```bat
start.bat
```

That creates `.venv`, builds the UI and serves http://127.0.0.1:8766 (localhost only). On first
open you create the owner account. For UI development, run `npm run dev` in `frontend/`
(http://127.0.0.1:5173, proxying `/api` to the backend).

Tests:

```bat
.venv\Scripts\python -m pytest tests                    :: 139 tests
cd skills\day-trader && ..\..\.venv\Scripts\python -m pytest tests -p no:cacheprovider   :: the 315 day-trader tests
```

## First steps

The short version; [SETUP.md](SETUP.md) has every step in full.

1. **Rotate the Kite API secret.** The current one has been in plain text in `D:\code\kite\kite`
   (`.env`, `config.json`, `access_token.txt`). Make a new one in the Kite developer console, then
   delete those three files.
2. **Connect Kite:** Settings → Connect a broker → Zerodha Kite. The API key is stored as a setting;
   the secret goes to Windows Credential Manager. In the Kite developer console set the redirect
   URL to `http://127.0.0.1:8766/api/brokers/kite/callback`.
3. **Daily login:** the light in the top bar turns red when the token has expired (around 06:00 IST).
   Click "Log in (daily)". No password or TOTP is stored for auto-login.
4. **Import history:** Settings → Import loads the `portfolio_*.json` snapshots once (23 files, Dec 2025 →
   Nov 2026). Or from the command line: `traderdesk-import D:\code\kite\kite\portfolio_data`.
5. **NSE holidays:** add the year's list to `backend/traderdesk/config/holidays/NSE.yaml`. Until
   then the risk check warns that holidays are not being checked.
6. **Going live:** set your risk rules, allow live orders on the account, then switch the mode to
   live (owner only). Live API orders need a static IP whitelisted with the broker (SEBI retail
   algo rules, mandatory from 1 April 2026).

## The order desk

| Gate | What happens |
| --- | --- |
| 1. Ticket | Quantity rounded down to the lot size and prices to the tick size; option market orders become limits 5% through the last price. Shows charges for entry and exit, breakeven, charges as % of expected profit, and margin. |
| 2. Risk | PASS or BLOCK with reasons (`config/risk.yaml`, versioned in the database): stop required and on the right side, max loss per trade, reward:risk, daily loss, trades and orders per day, position size %, F&O exposure, margin, entry window, no new MIS after 15:00, expiry-day cut-off, blocked list. Exits are never blocked. A BLOCK cannot be overridden; changing a rule makes a new version and is logged. |
| 3. Confirm | You type the order back ("BUY 10 RELIANCE") within 5 minutes. The confirm is bound to the order's fingerprint and the risk check runs again. A password re-check comes before the first live order of the day. |
| 4. Send | Client tag `TDP-<plan>-<n>`. On a timeout the order book is re-read before any retry, so an order is never sent twice. |
| 5. Track | Order book sync. A filled entry with stop + target gets an OCO GTT at the broker; its stop leg is a limit 1% past the trigger. |

Also: modify (a bigger quantity goes back through risk), cancel, cancel all (per symbol or account),
flatten (short legs first, hedges last) and the kill switch (cancels everything; only exits are
allowed for the rest of the day).

Charges come from `config/charges.yaml`, one entry per date, so the April 2026 STT change is data.
The rates were filled in from published schedules, not a live check; confirm the exchange
transaction rates and the DP charge against Zerodha's charges page before relying on them to the
rupee.

## Layout

```
backend/traderdesk/
  app/main.py            FastAPI: auth, accounts, portfolio, orders, rules, audit, top bar, serves the UI
  app/trading_api.py     market data, desk overview, positions, alerts, options, charges, signals,
                         briefs, analysis providers, the scheduler
  app/wealth_api.py      portfolio overview, metals, tax, expenses, bills, dashboards, users, AI assistant
  market/yahoo.py        free delayed bars and quotes (NSE, indices, global), cached
  analysis/              ORB / VWAP strategies, back-test, regime score, pre-market brief, review
  tax/engine.py          tax from config/tax.yaml (dated rules)
  orders/pnl.py          realised P/L, FIFO
  brokers/base.py        BrokerAdapter contract + plugin manifest
  brokers/registry.py    loads plugins/*/plugin.yaml and `traderdesk.brokers` entry points
  plugins/kite/          Kite Connect v3 adapter (ported from D:\code\kite)
  plugins/paper/         simulator with the same interface
  orders/desk.py         the five gates, cancel/modify/flatten/kill
  orders/risk.py         INR/NSE risk rules
  orders/charges.py      brokerage + statutory charges
  portfolio/             snapshot import, net worth
  core/                  domain models, errors, auth/roles, hash-chained audit, keychain secrets,
                         rate limiter, session calendars
  config/                charges.yaml, risk.yaml, tax.yaml, holidays/
frontend/src/            React + Vite: App.jsx (shell), ui.jsx (shared parts), PriceChart.jsx, screens/*.jsx
skills/day-trader/       the existing Claude skills + their 315 tests, unchanged
tests/                   backend tests
```

SQLite (`data/traderdesk.db`) is the default database. Set `TDP_DATABASE_URL` to use Postgres
(`docker-compose.yml`).

## Market data

Real time from Kite's quote API while the day's Kite login is valid (prices, today's candle, screeners,
futures and option OI). Otherwise delayed: Yahoo prices and NSE's end-of-day F&O file. The top bar shows
**Live · Kite** or **Delayed data**, and each panel names its source.

## Connectors

| Connector | How | Trades |
| --- | --- | --- |
| Zerodha Kite | OAuth daily login | yes (five gates) |
| Dhan, INDstocks | daily access token, or PIN + TOTP | read-only |
| Upstox | OAuth daily login | read-only |
| INDmoney US / any broker | holdings CSV upload | import |
| CAS + AMFI NAV | CAMS/KFintech PDF upload, NAV refresh | import |
| Crypto (CCXT) | read-only API key (`pip install -e "backend[crypto]"`) | read-only |
| Email inbox (Gmail, Outlook, Yahoo, Zoho, iCloud, any IMAP) | app password; reads bank, card and UPI alerts into Money and CAS/CSV attachments into Portfolio | read-only |

Read-only connectors stay read-only until order placement is verified on a live account.

## Testing

`.venv\Scripts\python -m pytest tests` runs 139 tests: unit, API, connectors and security (sign-in on
every route, roles, CSRF, origin, secrets never returned, path traversal, login throttling, hostile
input). Load (50 concurrent users) and browser end-to-end runs are in SETUP.md, "Testing".

## Not built yet

- Order placement through Dhan, Upstox, INDstocks and crypto (they connect read-only).
- Kite tick streaming and Kite intraday candles. Prices are real time from Kite quotes when logged in; 1m to 15m candles stay delayed (Yahoo).
- Bill detection from email, PDF statement parsing for metals, contract-note parsing and matching.
- Gmail API sign-in with Google's read-only scope (email uses IMAP with an app password today).
- FII/DII flows, intraday OI and GIFT Nifty (no free source). End-of-day PCR, OI walls and OI flow are on
  the Market pulse page, from NSE's free F&O bhavcopy.
- An LLM step inside the brief pipeline (the AI assistant itself works with an Anthropic API key).
- Telegram, email and calendar reminders (bills remind in the app only).
- Passkeys and Google sign-in; the strategy research lab; automatic order execution (by design, never).

Education and tooling, not investment advice.
