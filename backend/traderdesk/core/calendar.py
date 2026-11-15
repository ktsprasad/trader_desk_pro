"""Exchange session clocks. Each market's hours and holiday list come from data, so the
pre-market and post-market jobs and the risk window run per market."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import yaml

IST = timezone(timedelta(hours=5, minutes=30))
HOLIDAY_DIR = Path(__file__).resolve().parent.parent / "config" / "holidays"


@dataclass
class Session:
    market: str
    tz: timezone
    open: time
    close: time
    pre_open: time | None = None
    holidays: dict[str, str] = field(default_factory=dict)
    covered_years: set[int] = field(default_factory=set)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def is_trading_day(self, d: date) -> tuple[bool, str]:
        if d.weekday() >= 5:
            return False, "weekend"
        name = self.holidays.get(d.isoformat())
        if name:
            return False, f"{self.market} holiday: {name}"
        return True, ""

    def is_open(self, at: datetime) -> bool:
        local = at.astimezone(self.tz)
        ok, _ = self.is_trading_day(local.date())
        return ok and self.open <= local.time() < self.close

    def calendar_warning(self, d: date) -> str | None:
        if d.year not in self.covered_years:
            return (f"the {self.market} holiday list has no entries for {d.year}: holidays are not checked. "
                    f"Refresh it (07:30 job) or edit config/holidays/{self.market}.yaml")
        return None


def load_holidays(market: str) -> tuple[dict[str, str], set[int]]:
    p = HOLIDAY_DIR / f"{market}.yaml"
    if not p.exists():
        return {}, set()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    hol = {str(k): str(v) for k, v in (data.get("holidays") or {}).items()}
    years = {int(y) for y in data.get("covered_years") or []}
    return hol, years


def nse_session() -> Session:
    hol, years = load_holidays("NSE")
    return Session("NSE", IST, time(9, 15), time(15, 30), pre_open=time(9, 0), holidays=hol, covered_years=years)
