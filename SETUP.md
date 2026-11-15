# Trader Desk Pro: setup and user guide

This guide covers installing the app, connecting Zerodha Kite, the daily routine, and placing an
order. It starts in **paper mode**: no real order can go out until you turn live mode on yourself.

- [1. What you need](#1-what-you-need)
- [2. Install and first run](#2-install-and-first-run)
- [3. Create the owner account](#3-create-the-owner-account)
- [4. Connect Zerodha Kite](#4-connect-zerodha-kite)
- [5. Import your portfolio history](#5-import-your-portfolio-history)
- [6. Set your risk rules](#6-set-your-risk-rules)
- [7. Daily routine](#7-daily-routine)
- [8. Placing an order](#8-placing-an-order)
- [9. Practising in paper mode](#9-practising-in-paper-mode)
- [10. Going live](#10-going-live)
- [11. Users and roles](#11-users-and-roles)
- [12. Configuration reference](#12-configuration-reference)
- [13. Backup and reset](#13-backup-and-reset)
- [14. Troubleshooting](#14-troubleshooting)

---

## 1. What you need

| | Version | Check with |
| --- | --- | --- |
| Windows 10/11 (macOS and Linux also work, see below) | | |
| Python | 3.12 or newer | `python --version` |
| Node.js + npm | Node 18 or newer | `node --version` |
| A Zerodha Kite Connect app | for live data and orders | developers.kite.trade |
| Docker | optional, only for Postgres | `docker --version` |

You can use the app without Kite: the paper simulator and imported snapshots work on their own.

## 2. Install and first run

### The quick way (Windows)

Double-click `start.bat`, or from a terminal in the project folder:

```bat
start.bat
```

The first run:
1. creates a Python virtual environment in `.venv`;
2. installs the backend (`backend/`) with its dependencies;
3. installs the UI packages and builds the UI into `frontend/dist`;
4. starts the server on **http://127.0.0.1:8766**.

Later runs skip the steps already done and start in a few seconds. Open
http://127.0.0.1:8766 in your browser. The server only listens on this computer (127.0.0.1); other
machines on your network cannot reach it.

Stop the server with `Ctrl+C` in its window.

### By hand (any OS)

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate        macOS/Linux:  source .venv/bin/activate
pip install -e "backend[dev]"

cd frontend
npm install
npm run build
cd ..

python -m traderdesk.app.main            # or: traderdesk
```

### After you pull new code

If the backend's dependencies changed, run `.venv\Scripts\python -m pip install -e "backend[dev]"`.
If the UI changed, delete `frontend\dist` (or run `npm run build` in `frontend`) so `start.bat`
builds it again.

### Check the install

```bat
.venv\Scripts\python -m pytest tests
```

You should see `139 passed`.

## 3. Create the owner account

On the first visit the app asks you to create the **owner** account:

- **Username:** anything you like.
- **Password:** at least 10 characters. It is stored as an Argon2 hash in the local database.
- **Two-factor (TOTP), recommended:** **Settings → Users and audit → Users → Turn on two-factor
  sign-in**. Scan the QR code with an authenticator app (or type the key) and enter the 6-digit code.
  From then on, sign-in asks for the code.

Sessions end after 12 hours without activity, and when the server restarts.

## 4. Connect Zerodha Kite

### 4a. Make (or update) the Kite Connect app

1. Sign in at https://developers.kite.trade and open your app (or create one).
2. Set **Redirect URL** to exactly:
   ```
   http://127.0.0.1:8766/api/brokers/kite/callback
   ```
3. **Rotate the API secret** if it has ever been saved in a file (it was in plain text in
   `D:\code\kite\kite`). Then delete `.env`, `config.json` and `access_token.txt` in that folder.
4. Keep the **API key** and the new **API secret** at hand for the next step.

### 4b. Add the account in the app

1. Go to **Settings → Connections → Connect a broker**.
2. Plugin: **Zerodha Kite**.
3. Account id: a short id such as `kite-main` (lower case, digits, `-` or `_`).
4. Display name: anything, for example "Zerodha".
5. API key and API secret: paste them in. The key is saved as a setting; the **secret goes into
   Windows Credential Manager** (service name `trader-desk-pro`), never into the database or a file.
6. Click **Connect**.

### 4c. Log in (once a day)

Kite tokens expire every morning around 06:00 IST. In **Connections**, click **Log in (daily)**,
sign in on Zerodha's page, and you are sent back to the app with the account connected. The light in
the top bar is green while the token is valid and red when you need to log in again.

The app does not store your Zerodha password or TOTP, and it does not log in automatically.

**Real-time data comes from this login.** While the day's Kite login is valid, the top bar shows
**Live · Kite**. These then come from Kite's quote API in real time:

- every price and day change, and today's candle on daily charts;
- the Market pulse screeners (refreshed every 20 s);
- futures OI flow and the option chain OI (refreshed every 30 s).

Today's OI change is the live OI minus NSE's last end-of-day figure.

Without the login, the badge reads **Delayed data**. Prices then come from Yahoo (delayed), and OI comes
from NSE's file of the last trading day; each panel says which source it used.

Kite's quote API needs market data enabled on your Kite Connect app. If Kite refuses, the badge says
why and the app stays on delayed data. Intraday candles (1m to 15m) stay on Yahoo even when prices are
live: Kite's historical candles and the live tick stream are not wired in yet.

### 4d. Optional: the Kite MCP server (Kite inside Claude)

Zerodha's Kite MCP server lets Claude Desktop or Claude Code read your Kite account (holdings,
positions, orders, quotes). It is separate from Trader Desk Pro and not needed to run the app.
Orders still belong in the app, where the risk check and typed confirm apply, so run the MCP server
**read-only**.

1. Keep its `.env` in the MCP server's own folder, never in this repo or a chat. Start from its
   `.env.example`:

   ```ini
   # Kite Connect credentials: type them in this file only
   KITE_API_KEY=<your API key>
   KITE_API_SECRET=<your API secret>
   # KITE_ACCESS_TOKEN=          # leave unset; it expires daily at ~06:00 IST

   APP_MODE=stdio                # stdio for Claude Desktop / Claude Code; http, sse or hybrid for web clients
   APP_PORT=8080                 # only for http / sse / hybrid; does not clash with the app's 8766
   APP_HOST=localhost            # keep it on this computer

   # Read-only: Claude cannot place, change or cancel orders or GTTs
   EXCLUDED_TOOLS=place_order,modify_order,cancel_order,place_gtt_order,modify_gtt_order,delete_gtt_order

   LOG_LEVEL=info                # debug, info, warn or error
   ```

2. **One redirect URL per Kite app.** The Trader Desk Pro login needs the redirect URL from step 4a.
   If the MCP server's own login needs a different redirect, make a second Kite Connect app for it
   instead of changing the first one.
3. In Claude, set any order tools you leave enabled to **Needs approval**, never "Always allow".

| Setting | Values | Notes |
| --- | --- | --- |
| `KITE_API_KEY`, `KITE_API_SECRET` | from developers.kite.trade | required; secret only in `.env` |
| `KITE_ACCESS_TOKEN` | daily token | optional; stale after ~06:00 IST, so prefer the server's login |
| `APP_MODE` | `stdio`, `http` (default), `sse`, `hybrid` | `stdio` for Claude Desktop |
| `APP_PORT`, `APP_HOST` | `8080`, `localhost` | only for `http`, `sse`, `hybrid` |
| `EXCLUDED_TOOLS` | comma-separated tool names | empty enables every tool, including orders |
| `LOG_LEVEL` | `debug`, `info`, `warn`, `error` | `debug` can log request details; avoid it day to day |

### 4e. Other connectors

**Settings → Connections → Add a connection**, pick one, fill the form, **Connect**. Then, on the account:

| Connector | Daily step | Notes |
| --- | --- | --- |
| Dhan | **PIN + TOTP** (or **Paste token** from web.dhan.co) | read-only; token lasts 24 h |
| INDstocks | **PIN + TOTP** with your API client ID (or **Paste token** from indstocks.com, API trading) | read-only; one token per 60 s |
| Upstox | **Log in (daily)**; redirect URL `http://127.0.0.1:8766/api/brokers/upstox/callback` | read-only; token ends 03:30 IST |
| INDmoney US / any CSV | **Upload CSV** of holdings (choose USD for US stocks) | converted at USD/INR |
| CAS + AMFI NAV | **Upload CAS PDF** (password used once, not stored), then **Refresh NAVs** | all mutual funds |
| Crypto (CCXT) | none; use a read-only API key | `pip install -e "backend[crypto]"` |

PINs and TOTP codes are sent once to the broker for the day's token and never stored. Tokens and secrets
live in Windows Credential Manager. **Disconnect** removes an account and its keys (accounts with order
history are kept).

### 4f. Email inbox (Gmail, Outlook, Yahoo, Zoho, iCloud or any IMAP mailbox, read-only)

The email connection reads bank, card and UPI alert emails into **Money → Expenses**. It also finds CAS
PDFs and holdings CSVs and imports them into **Portfolio**. It works with any provider that offers IMAP.

**Connect Gmail (read-only):**

1. Turn on 2-Step Verification: myaccount.google.com → **Security** → **2-Step Verification**.
2. Create an app password: myaccount.google.com/apppasswords. Name it `Trader Desk` and copy the 16
   letters. This password works only for mail apps, and you can revoke it there at any time.
3. In the app: **Connections → Add a connection → Email inbox (any provider) → Connect**.
   - Provider: **Gmail**.
   - Email address: your Gmail address.
   - App password: the 16 letters. Spaces are fine.
   - Optional: under **Only these senders**, list your banks, e.g.
     `hdfcbank.net, icicibank.com, axisbank.com, camsonline.com`.
4. On the new account, click **Test connection**, then **Scan inbox** → **Scan**.
5. Check the alerts it found. Fix any category, untick anything you don't want, then click
   **Import N selected**. Each alert is imported once; rescanning never adds duplicates.
6. A CAS PDF or holdings CSV found in the inbox can be imported into your CAS or CSV connection from the
   same window.

Gmail has had IMAP switched on for every account since early 2025. If Google says "app passwords aren't
available", your account is a work or school account where the admin blocks them, or it uses Advanced
Protection.

**What "read-only" means here:** the app opens the folder with IMAP `EXAMINE` and reads mail with
`BODY.PEEK`, so nothing is marked read, moved, deleted or sent; the tests check this. Google, however,
has no read-only app password. The password itself could do anything a mail app can, so the protection
comes from this app's code and from the password living only in Windows Credential Manager. Revoke it at
myaccount.google.com/apppasswords whenever you like.

**Other providers:** pick the provider and use its app password.

| Provider | IMAP host (filled in for you) | Where to get the app password |
| --- | --- | --- |
| Outlook / Hotmail / Microsoft 365 | outlook.office365.com | account.microsoft.com → Security → Advanced security options → App passwords (needs two-step verification); work accounts need IMAP allowed by the admin |
| Yahoo | imap.mail.yahoo.com | Account security → Generate app password |
| Zoho / Zoho (India) | imap.zoho.com / imap.zoho.in | Mail settings → IMAP access on; Security → App passwords |
| iCloud | imap.mail.me.com | appleid.apple.com → App-specific passwords |
| Rediffmail | imap.rediffmail.com | enable IMAP in settings; mail password |
| AOL | imap.aol.com | Account security → Generate app password |
| Other | enter the host (port 993, SSL) | your provider's IMAP settings |

The alert reader understands common Indian bank wording ("Rs 1,249 debited ... towards SWIGGY", "INR
85,000 credited ... by NEFT from ..."). It skips OTPs and offers. Nothing is added until you pick it.
Contract-note PDFs are listed but not parsed yet.

## 5. Import your portfolio history

**Settings → Connections → Import portfolio history**: the folder defaults to
`D:\code\kite\kite\portfolio_data`. Click **Import**. Each dated `portfolio_*.json` snapshot is loaded
once; running it again skips files already in. `portfolio_latest.json` is skipped because it repeats
the newest dated file.

From the command line instead:

```bat
.venv\Scripts\traderdesk-import D:\code\kite\kite\portfolio_data
```

The **Portfolio** screen then shows the latest value and history. When a Kite account is connected
and logged in, it shows live holdings; otherwise it falls back to the newest snapshot.

## 6. Set your risk rules

**Settings → Connections → Risk rules**. The defaults come from `backend/traderdesk/config/risk.yaml`:

| Rule | Default | Meaning |
| --- | --- | --- |
| capital | 500000 | INR that the position-size % is measured against |
| max loss per trade | 2500 | INR lost if the stop fills |
| max daily loss | 7500 | INR: today's entries' max losses plus this one |
| max trades per day | 5 | entries |
| max orders per day | 20 | all orders |
| max position pct | 20 | % of capital in one instrument after the order |
| fno exposure cap | 300000 | INR notional across today's F&O entries |
| min reward risk | 1.5 | target distance ÷ stop distance |
| require stop | on | an entry must have a stop, on the right side of the price |
| allow fo / allow short intraday | on | F&O entries; MIS shorts (CNC shorts are never allowed) |
| no entries before / after | 09:20 / 15:10 | IST entry window |
| no new MIS after | 15:00 | IST |
| expiry day no new fo after | 13:30 | IST |
| blocked | empty | symbols you never want to trade |

Change a value and click **Save new version**. Each save makes a new rules version and an audit
entry, with the reason you type in "Why". Only the owner can change rules.

Exits (orders that reduce a position you hold) are never blocked by these rules.

### NSE holidays

Add the year's trading holidays to `backend/traderdesk/config/holidays/NSE.yaml` (NSE publishes the
list each December), and add the year to `covered_years`:

```yaml
covered_years: [2026]
holidays:
  - 2026-01-26   # Republic Day
  # ...
```

Until the current year is covered, every ticket carries a warning that holidays are not checked.
Restart the server after editing.

## 7. Daily routine

| When (IST) | What |
| --- | --- |
| Before 09:15 | Start the app, sign in, click **Log in (daily)** for Kite. Check the top bar: mode, Kite light, risk budget left. |
| 09:15 to 09:20 | The opening minutes: no new entries until 09:20 by default. |
| 09:20 to 15:10 | Entries allowed. Every order goes through the ticket (section 8). |
| 15:00 | No new MIS entries. |
| Before 15:20 | Square off MIS positions yourself (**Desk → Flatten intraday**) rather than leaving it to the broker. |
| After the close | **Order book → Sync** to pull final statuses. Review the **Audit log** if anything looked odd. |

The **top bar** shows the IST clock and whether NSE is open, the New York clock, paper or live mode,
the risk budget left today, trades used against the limit, a light per broker account (green when
connected), the theme switch, sign out, and the red **Kill switch**.

## 8. Placing an order

Every order, paper or live, goes through the same five steps.

1. **Ticket** (**Trade** screen): pick the account, exchange, symbol, side, quantity, order
   type, product, price, and your **stop** and **target**. The app rounds the quantity down to the
   lot size and prices to the tick size, turns an option market order into a limit 5% through the
   last price, and shows the charges for entry and exit, the breakeven and the margin needed.
2. **Risk check**: PASS or BLOCK, with each reason. A BLOCK cannot be overridden from the ticket;
   change the order or (as owner) the rules.
3. **Confirm**: type the order back exactly as shown, for example `BUY 10 RELIANCE`, within 5
   minutes. If anything about the order changed, or the rules changed and it no longer passes, it
   stops. Before the first **live** order of the day you re-enter your password.
4. **Send**: the order goes out with a tag like `TDP-orb1-42`. If the broker times out, the app
   checks the order book for that tag before retrying, so the order is never placed twice.
5. **Track**: the **Order book** shows its status. When an entry with a stop and a target fills,
   the app places a two-leg **OCO GTT** at the broker (one leg at the stop, one at the target). The
   stop leg is a limit 1% past the trigger so it still fills on a gap.

From the **Order book** you can also **Modify** an open order's price, **Cancel** it, **Cancel
all** open orders, **Flatten** an account (squares off intraday and F&O positions; short legs first,
hedges last) and **Sync** statuses from the broker. Through the API, a modify can also change the
quantity (a bigger one goes back through the risk check) and cancel-all can be limited to a symbol.

### The kill switch

The red button in the top bar cancels every open order and blocks new entries for the rest of the
day. Exits still work. Only the owner can release it (**Settings → Connections → Paper / live → Release kill
switch**).

## 9. Practising in paper mode

The app starts in paper mode. Every account then routes to a simulator with the same interface, so
everything above works without real money.

For NSE and BSE stocks the simulator takes its first price from the free delayed feed when you build a
ticket. For F&O, or to move a price yourself, use **Settings → Connections → Paper simulator price**,
for example `NSE:RELIANCE` = `2950`. Resting orders and GTTs fill against the price you set:

- a buy limit at or above the price fills at once; below it, it rests until you lower the price;
- move the price through the stop or target to watch the OCO fire;
- an after-market order (AMO) waits until the simulated session opens.

## 10. Going live

Only the owner can go live, and it takes three separate steps:

1. **Settings → Connections**: tick **allowed** in the account's **Live orders** column.
2. **Settings → Connections → Paper / live → Go live**, and confirm.
3. On your first live order of the day, re-enter your password at the confirm step.

An account without live orders allowed stays on paper even in live mode. Tickets show a **PAPER** or
**LIVE** label so you always know which one you are on.

**Static IP.** Since 1 April 2026, SEBI's retail algo rules require API orders to come from a static
IP whitelisted with the broker. Register your IP in the Kite developer console before sending live
orders from the app, or Kite will reject them.

## 11. Users and roles

The owner manages people in **Settings → Users and audit → Users**: add a user with a temporary
password and a role, change a role, or disable someone (their sessions end at once). **Sessions**
lists who is signed in and lets you sign a session out.

| Role | Sees |
| --- | --- |
| owner | everything: users, brokers, rules, live mode, audit, release the kill switch |
| trader | trading screens, portfolio, charges, dashboards, AI assistant; can send, cancel, flatten, kill |
| viewer | desk, charts, briefs, portfolio, dashboards, connections (read only) |
| accountant | portfolio, tax centre (with export), charges, dashboards, AI assistant |
| family | Expenses, Bills and dashboards |

## 11a. The screens

| Screen | What it does | Needs |
| --- | --- | --- |
| **Desk** | Today's brief, index quotes, armed plans, accounts (cash, margin), positions across brokers with stop/target/R and Exit, open orders, alerts. On a phone: Flatten intraday and Kill switch at the bottom. | — |
| **Charts** | Candles / line / Heikin-Ashi, 1m to 1W, VWAP, opening range, EMA, signal markers. **Buy / exit signals** by style: Intraday (ORB or VWAP, 5m, exit at 2R target, stop or 15:15), Swing (pullback to 20-DMA, daily, 2R / stop / 10 days), Long-term (50/200-DMA trend, daily over 10 years, exit on two closes below the 200-DMA) and Options (the intraday signal as an at-the-money CE or PE, strike step from NSE). Each shows entries and exits on the chart, what the rule says now, its back-test (trades, win %, total vs buy and hold) and the rules; an active signal opens a pre-filled ticket you confirm. Templates: Intraday ORB, Swing, Index dashboard, Relative strength, Portfolio, Metals. | delayed feed |
| **Market pulse** | Nine tabs. **Money Flow**: a sector money-flow monitor (about 130 stocks in 20 sectors: ₹ cr flowing in or out per sector, top inflow and outflow stocks, breadth, a one-line headline and, with Kite live data, surge rates in ₹ cr per minute), then MFI and turnover vs the 20-day average per stock. **Crypto & Gold**: gold, silver, USD/INR, gold and silver ETFs, Bitcoin, Ether, Solana and Brent with 1-month and 1-year returns, RSI and gold/silver parity in ₹. **Option Pulse**: PCR, max pain, call/put OI walls, OI by strike, most active contracts, for any F&O symbol and expiry. **Stock Radar**: 52-week-high breakouts, pullbacks, volume surges, squeezes, gaps. **Calm Zone**: India VIX, regime checklist, volatility-squeeze stocks. **Liquidity Map**: untaken swing highs/lows, previous day/week levels, 52-week range and option OI walls for one symbol. **OI Flow**: long/short buildup, short covering, long unwinding across all F&O futures. **RSI Screener**: daily and weekly RSI(14) with filters. **F&O Academy**: short lessons. Edit the stock list in `backend/traderdesk/config/universe.yaml` (your watchlist is always added). Analysis only. | Yahoo daily bars; NSE's free end-of-day F&O file (published each evening, so options and OI are as of the last trading day) |
| **Algo signals** | Market regime score, opening-range breakout and VWAP strategies scanning your watchlist every 5 minutes (09:20 to 14:35), back-test odds, a 5-check discipline gate and **Open pre-filled ticket**. Never sends orders by itself. | delayed feed |
| **Trade** | Chart with entry/stop/target lines next to the order ticket: product, order type, trigger, OCO stop and target, live max loss and reward:risk, then the risk check and typed confirm. | — |
| **Options** | Option chain (Kite Connect plan) and a strategy builder with presets, payoff at expiry, max profit/loss and breakevens. **Review and place** makes one ticket per leg, buys first. | chain: Kite Connect plan |
| **Briefs** | Pre-market brief at 08:45 IST: bias, global cues, VIX, regime, NIFTY pivots, setups from your watchlist with their historical hit rate, no-trade conditions. Post-market review at 15:45 grades the call. | delayed feed |
| **Portfolio** | Net worth (holdings + metals + broker cash), allocation, holdings with how long each was held and STCG/LTCG, GTT ladders, lots turning long-term. | Kite or snapshots |
| **Digital metals** | Gold/silver/platinum holdings by app and vault. You enter the provider's sell rate per gram; value and P/L follow. | — |
| **Tax centre** | Tax by income type from `config/tax.yaml`, advance-tax schedule and payments, LTCG exemption used, harvesting ideas, F&O turnover, CSV export. Counts live fills automatically (never paper) plus entries you add from Kite Console's tax P&L. | — |
| **Charges** | Calculator for any trade, the month's charges by type, the same trades priced at other brokers. | — |
| **Expenses / Bills** | Manual expenses and income, budgets, monthly chart, CSV export; bills with due dates, recurring bills, mark paid, snooze, calendar. | — |
| **Dashboards** | Your own layouts of widgets (net worth, day P/L, tax, bills, equity curve, NIFTY, positions, spending, signals). Saved per user. | — |
| **AI assistant** | Ask questions about your own data. Claude (`claude-sonnet-5-5`) calls read-only tools; it cannot place orders or change settings. Optional local Ollama for money and tax questions. Every tool call is in the audit log. | Anthropic API key (or Ollama) |
| **Analysis providers** | Which providers are on, the installed Claude skills, the brief/scan schedule, the scorecard. | — |

**AI assistant key:** in **AI assistant → Claude API key**, paste a key from console.anthropic.com. It
goes to Windows Credential Manager. The assistant sends Claude only the tool results it asks for.
Using it costs API credit on your Anthropic account.

**Delayed data:** charts, briefs and signals use Yahoo Finance's free feed, which is unofficial and
delayed. Check prices against your broker before acting.

## 12. Configuration reference

### Environment variables

| Variable | Default | Use |
| --- | --- | --- |
| `TDP_PORT` | `8766` | server port. If you change it, change the Kite redirect URL too. |
| `TDP_DATABASE_URL` | `sqlite:///data/traderdesk.db` | database. Postgres: `postgresql+psycopg://traderdesk:traderdesk@127.0.0.1:5432/traderdesk` |
| `TDP_FRONTEND_DIST` | `frontend/dist` | where the built UI is served from |
| `TDP_SECRETS` | keychain | `memory` keeps secrets in memory only (tests and demos; lost on restart) |
| `TDP_SCHEDULER` | `on` | `off` stops the background jobs (08:45 brief, 15:45 review, signal scans) |
| `ANTHROPIC_API_KEY` | — | used by the AI assistant when no key is stored in the app |

### Files

| File | What |
| --- | --- |
| `backend/traderdesk/config/risk.yaml` | default risk rules (version 1; later versions live in the database) |
| `backend/traderdesk/config/charges.yaml` | brokerage and statutory charges, one entry per effective date |
| `backend/traderdesk/config/holidays/NSE.yaml` | NSE trading holidays |
| `backend/traderdesk/plugins/*/plugin.yaml` | broker plugins: capabilities, rate limits, settings |
| `data/traderdesk.db` | the SQLite database (created on first run, not in git) |

### Charges

`charges.yaml` holds the rates by effective date (the STT change of 1 April 2026 is its own entry).
Check the exchange transaction rates and the DP charge against Zerodha's charges page before relying
on them to the rupee. To add a rate change, add an entry with a new `from:` date; the old one stays
for orders before that date.

### Postgres instead of SQLite (optional)

```bat
docker compose up -d
.venv\Scripts\python -m pip install -e "backend[postgres]"
set TDP_DATABASE_URL=postgresql+psycopg://traderdesk:traderdesk@127.0.0.1:5432/traderdesk
start.bat
```

The containers bind to 127.0.0.1 only. Change the password in `docker-compose.yml` if anyone else
uses this computer.

## 13. Backup and reset

- **Back up:** stop the server and copy `data\traderdesk.db`. Broker secrets are in Windows
  Credential Manager (search "trader-desk-pro"), not in the database.
- **Check the audit log:** the **Audit log** screen recomputes the hash chain each time it opens
  and shows "hash chain intact" or the first row that was changed. **Export CSV** saves a copy.
- **Start over:** stop the server, delete `data\traderdesk.db`, and remove the `trader-desk-pro`
  entries from Credential Manager. The next start asks for a new owner.

## 14. Troubleshooting

| Problem | Fix |
| --- | --- |
| `python` not found, or the wrong version | Install Python 3.12+ from python.org and tick "Add to PATH". Delete `.venv` and run `start.bat` again. |
| `npm` not found | Install Node.js LTS from nodejs.org, open a new terminal, run `start.bat`. |
| Port 8766 already in use | Another copy is running. Close it, or `set TDP_PORT=8767` (and update the Kite redirect URL). |
| Kite login ends on "sign in as the owner" | Sign in to the app first, in the same browser, at http://127.0.0.1:8766 (not `localhost`, and not the 5173 dev server), then click **Log in (daily)** again. |
| Kite "invalid redirect URL" | The redirect URL in the Kite console must match section 4a exactly. |
| After Kite login the browser goes to `localhost:8080/...` and "refused to connect" | The Kite app's redirect URL points at the Kite MCP server. Set it to the URL in section 4a (one redirect URL per Kite app; use a second app for the MCP server). |
| Kite login fails with "Invalid checksum" | The API secret stored in the app does not match the API key, usually because the secret was regenerated. **Settings → Connections → Replace secret**, paste the current secret from the Kite console, then **Log in (daily)** again. |
| Kite light stays red | The token expired (around 06:00 IST) or the secret was rotated. Log in again; if the secret changed, use **Replace secret** first. |
| Every ticket is BLOCKED by the window rule | Outside 09:20 to 15:10 IST, or a weekend or holiday. Use an AMO order (delivery) or wait for the session. |
| Warning "holidays not checked" | Fill in `config/holidays/NSE.yaml` (section 6). |
| Live order rejected for IP | Whitelist your static IP in the Kite developer console (section 10). |
| Signed out after a restart | Sessions are held in memory; sign in again. |
| The ₹ sign shows as `?` in a terminal | `set PYTHONIOENCODING=utf-8` before running command-line tools. |

## Testing

| Kind | How | Last result |
| --- | --- | --- |
| Unit, API, connectors, security | `.venv\Scripts\python -m pytest tests` | 139 passed |
| Skills | in `skills\day-trader`: `..\..\.venv\Scripts\python -m pytest tests -p no:cacheprovider` | 315 passed |
| Dependencies | `npm audit` (frontend), `pip-audit` (venv) | UI 0; backend: autobahn via kiteconnect (unused ticker code) |
| Load | 50 signed-in users for 45 s on a test server | about 110 req/s, 0 server errors |
| Browser end-to-end | 25 journeys in headless Edge | 25 passed |

Run load and browser tests against a throwaway server, never your real database: set
`TDP_DATABASE_URL=sqlite:///C:/temp/test.db`, `TDP_SECRETS=memory` and `TDP_PORT=8799` first.

## Developing the UI

```bat
:: terminal 1
start.bat
:: terminal 2
cd frontend
npm run dev
```

Open http://127.0.0.1:5173. Changes reload at once and `/api` is proxied to 8766. Do the Kite daily
login on 8766, not 5173.

---

Education and tooling, not investment advice. You are responsible for every order you send.
