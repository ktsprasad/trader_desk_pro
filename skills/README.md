# Skills in Trader Desk Pro

This folder holds Claude skills used alongside Trader Desk Pro. Each skill is a folder with a
`SKILL.md` (the instructions Claude follows) and, usually, a small Python tool.

| Folder | What | Markets |
| --- | --- | --- |
| [day-trader/](day-trader/README.md) | 11 day-trading skills: scanner, news, entry timer, exit planner, Time Machine, risk, budget, broker, alerts, earnings analyst, congressional trades tracker | US stocks |

## How they fit together

The skills and the app are two layers with one rule in common: **no order without a risk PASS and
your explicit yes for that exact order.**

| Step | Skill (planning, in Claude) | Trader Desk Pro (execution, in the app) |
| --- | --- | --- |
| Find a setup | `/trader-scanner`, `/trader-news` | not built yet (planner screens) |
| Time the entry | `/trader-entry` | not built yet |
| Plan stop and target | `/trader-exit`, `/trader-timemachine` | you enter stop and target on the ticket |
| Size the position | `/trader-budget` (USD) | ticket rounds to lot and tick size, shows charges and margin (INR) |
| Risk check | `/trader-risk` | gate 2: INR/NSE rules, versioned, BLOCK cannot be overridden |
| Confirm and send | `/trader-broker` (Alpaca / IBKR) | gates 3 to 5: typed confirm, idempotent send, OCO GTT |
| Watch | `/trader-alerts` | Order book, kill switch, flatten |

Today the skills work on **US stocks** only. Their tools read Yahoo Finance in New York time and
place orders through Alpaca or Interactive Brokers. They do not know NSE symbols, IST sessions,
lot sizes or Indian charges. For **Indian markets**, do the planning however you like and place the
order through Trader Desk Pro's **Trade** screen, so the app's risk check and confirm apply.

For NSE, the app has its own rules engine: the Briefs and Algo signals screens run opening-range and
VWAP strategies, pivots and a regime score on delayed NSE data. Wrapping these skills in the app is not built.

## Install the skills in Claude Code

From this repo (project-local, available when Claude Code runs in this folder):

```bat
mkdir .claude\skills
xcopy /E /I skills\day-trader\trader-* .claude\skills\
```

Then run `/trader-scanner` and the others in Claude Code. Or install for every project by copying
the same `trader-*` folders into `%USERPROFILE%\.claude\skills\`. The upstream install
instructions (plugin marketplace, paste a URL) are in [day-trader/README.md](day-trader/README.md).

The tools need Python 3.8 or newer and nothing else. Their files go in a `trader/` folder in the
project you run them from. Copy `trader-budget/config.example.json` to `trader/config.json` and set
your budget, limits and `"paper": true`. The SEC tools (news filings, the analyst) need
`"sec_user_agent"` (your name and email) in that file or the `SEC_USER_AGENT` environment variable,
because SEC requires it; that value stays on your computer.

## Run their tests

```bat
cd skills\day-trader
..\..\.venv\Scripts\python -m pytest tests -p no:cacheprovider
```

315 tests, offline, on fixtures. They also check the folder shape, so read "The skills folder" in
[CLAUDE.md](../CLAUDE.md) before you add or rename anything under `day-trader/`.

## Adding a skill

Make a new folder here (not inside `day-trader/`), for example `skills/nse-planner/`, with:

```
SKILL.md      frontmatter: name, description (what it does + when to use it), then the steps
tool.py       optional; standard library only if Claude runs it directly
tests/        offline tests on saved fixtures
```

If the skill can lead to an order, it must hand the order to Trader Desk Pro's ticket (or a
broker connector set to "Needs approval") and never place it on its own.

Education and tooling, not financial advice.
