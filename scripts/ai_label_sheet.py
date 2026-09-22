#!/usr/bin/env python3
"""
برگهٔ برچسب — N listings as a Markdown table for a person to correct.

The reader has no ground truth until someone reads the same ads and says
what they actually say. This prints the listings (id, serial, title, the
first 200 characters of the description) with the reader's current facts
beside them, so a consultant can correct the facts column by hand. The
corrected rows become the labels file ai_bakeoff.py scores models against.

    venv/bin/python scripts/ai_label_sheet.py -n 30 > labels.md
    venv/bin/python scripts/ai_label_sheet.py --ids 1042 1043

DATABASE_URL and the rest come from the environment, as for the app.
Nothing here writes to the database.
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DESCRIPTION_CHARS = 200
FACT_FIELDS = ("kind", "floor", "total_floors", "year_built", "document", "condition",
               "has_elevator", "has_parking", "has_storage", "has_balcony", "district",
               "convertible", "exchange", "vacant", "negotiable", "suitable_for", "red_flags")


def _cell(text) -> str:
    """One Markdown cell: no pipes, no line breaks."""
    return str(text if text is not None else "").replace("|", "／").replace("\r", "").replace("\n", " ").strip()


def facts_cell(facts) -> str:
    """The facts a person corrects, compact: only the fields that were set."""
    if not facts:
        return "—"
    parts = [f"{k}={json.dumps(facts[k], ensure_ascii=False)}" for k in FACT_FIELDS
             if facts.get(k) not in (None, [], "")]
    return _cell("; ".join(parts) or "—")


def build_table(rows) -> str:
    """rows: anything with id, serial_no, title, description, ai_facts."""
    out = ["| id | serial | title | description | facts |", "|---|---|---|---|---|"]
    for p in rows:
        desc = (p.description or "")[:DESCRIPTION_CHARS]
        out.append(f"| {p.id} | {p.serial_no or ''} | {_cell(p.title)} | {_cell(desc)} | {facts_cell(p.ai_facts)} |")
    return "\n".join(out)


async def main(args) -> None:
    from sqlalchemy import select
    from app.database import async_session_maker
    from app.models.property import Property

    q = select(Property).where(Property.is_active == True)      # noqa: E712
    if args.ids:
        q = q.where(Property.id.in_(args.ids))
    else:
        q = q.order_by(Property.id.desc()).limit(args.count)
    async with async_session_maker() as db:
        rows = (await db.execute(q)).scalars().all()
    print(build_table(sorted(rows, key=lambda p: p.id)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="آگهی‌ها به‌صورت جدول Markdown، با برداشت فعلی خواننده، برای تصحیح دستی")
    ap.add_argument("-n", "--count", type=int, default=30, help="how many of the newest listings (default 30)")
    ap.add_argument("--ids", type=int, nargs="*", help="specific property ids instead")
    asyncio.run(main(ap.parse_args()))
