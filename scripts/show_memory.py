"""What she holds about you, as it is in the database, for you to read.

    .venv/Scripts/python.exe scripts/show_memory.py            what she would say she knows
    .venv/Scripts/python.exe scripts/show_memory.py --said     with your own sentence behind each one
    .venv/Scripts/python.exe scripts/show_memory.py --waiting  sentences written down and not yet made into anything

Reads only; nothing is changed. Each line starts with the memory's number, the
day it was kept and its kind. A memory written before 2026-10-13 has no
sentence behind it: nothing recorded what was said then, so its wording was
never checked against it. To be rid of one, tell her ("forget that I ...").
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.paths import MEMORY_DATABASE_PATH  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--said", action="store_true", help="show the sentence each memory came from")
    parser.add_argument("--waiting", action="store_true", help="show what is written down and not yet kept")
    parser.add_argument("--forgotten", action="store_true", help="include what she was told to forget")
    args = parser.parse_args()
    if not MEMORY_DATABASE_PATH.exists():
        raise SystemExit(f"no memory database at {MEMORY_DATABASE_PATH}")
    db = sqlite3.connect(f"file:{MEMORY_DATABASE_PATH.as_posix()}?mode=ro", uri=True)
    has_said = bool(db.execute("select 1 from sqlite_master where type='table' and name='said'").fetchone())

    if args.waiting:
        rows = db.execute("select id, said_at, tries, text from said where state = 'waiting' order by id").fetchall() \
            if has_said else []
        print(f"{len(rows)} sentence(s) written down and not yet made into anything")
        for said_id, when, tries, text in rows:
            print(f"  [{said_id}] {str(when)[:16]}  tried {tries}x  {text}")
        return

    where = "category != 'research_evidence'" + ("" if args.forgotten else " and is_active = 1")
    rows = db.execute(f"select id, created_at, category, is_active, content from memories where {where} order by id").fetchall()
    print(f"{len(rows)} thing(s) she holds about you ({MEMORY_DATABASE_PATH})")
    for memory_id, when, category, active, content in rows:
        mark = "" if active else "  (forgotten)"
        print(f"  [{memory_id}] {str(when)[:10]}  {category}{mark}\n      {content}")
        if args.said and has_said:
            behind = db.execute("select text from said where memory_id = ? order by id", (memory_id,)).fetchall()
            for (text,) in behind:
                print(f"        you said: {text}")
            if not behind:
                print("        (kept before sentences were written down)")


if __name__ == "__main__":
    main()
