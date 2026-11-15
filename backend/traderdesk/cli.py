"""Command-line helpers.

    traderdesk-import D:\\code\\kite\\kite\\portfolio_data      # load dated snapshots as history
    traderdesk-import <folder> --account kite
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .db.models import make_session_factory
from .portfolio.importer import import_snapshots


def import_main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import Kite portfolio_*.json snapshots into the database")
    ap.add_argument("folder")
    ap.add_argument("--account", default="kite")
    ap.add_argument("--db", default=os.environ.get("TDP_DATABASE_URL"))
    a = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    url = a.db or f"sqlite:///{(root / 'data' / 'traderdesk.db').as_posix()}"
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    Db = make_session_factory(url)
    with Db() as db, db.begin():
        res = import_snapshots(db, a.folder, a.account)
    print(f"imported {res['imported']}, already there {res['skipped']}, failed {res['failed']}")
    for e in res["errors"]:
        print("  " + e)
    return 0 if not res["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(import_main())
