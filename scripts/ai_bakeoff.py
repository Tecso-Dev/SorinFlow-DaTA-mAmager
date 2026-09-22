#!/usr/bin/env python3
"""
مسابقهٔ مدل‌ها — which model reads Divar listings the way the office does.

Given a labels file — the corrected facts for some listings — and one or
more model names, this runs the reader's own prompt through each model on
those listings and prints, per field, how often each model agreed with the
person. Same prompt pack, same schema, same gateway door (app/services/llm.py:
masked text, one ledger row per call under agent «bakeoff», the daily cap);
only the model differs. Nothing is stored on the listings.

    venv/bin/python scripts/ai_bakeoff.py labels.json z-ai/glm-5.3-flash openai/gpt-4.1-mini

labels.json, one entry per listing, only the fields the person checked:

    {"1042": {"kind": "shop", "floor": 1, "convertible": true, "document": null},
     "1043": {"kind": "house", "suitable_for": ["کافه"], "red_flags": ["سند قولنامه‌ای"]}}

A null label means «the text does not say» — a model that guesses a value
there is wrong. Lists compare as sets.
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONCURRENCY = 3
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _norm(v):
    if isinstance(v, list):
        return frozenset(_norm(x) for x in v)
    if isinstance(v, str):
        return v.translate(_FA_DIGITS).replace("‌", " ").strip()
    return v


def score(labels: dict, facts: dict) -> dict:
    """{field: hit} for the labelled fields of one listing."""
    return {k: _norm(facts.get(k)) == _norm(v) for k, v in labels.items()}


async def run_model(model: str, items, db_maker):
    from app.ai import listing_reader
    from app.services import llm
    hits, totals, failed, latencies, cost = {}, {}, 0, [], 0.0
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(prop, labels):
        nonlocal failed, cost
        async with sem:
            t0 = time.monotonic()
            try:
                async with db_maker() as db:
                    facts = await listing_reader.ask(db, prop, model=model, agent="bakeoff")
            except llm.LLMError as e:
                failed += 1
                print(f"  #{prop.id} {model}: {e}", file=sys.stderr)
                return
            latencies.append(time.monotonic() - t0)
            for k, ok in score(labels, facts).items():
                totals[k] = totals.get(k, 0) + 1
                hits[k] = hits.get(k, 0) + int(ok)

    await asyncio.gather(*(one(p, lb) for p, lb in items))
    return {"model": model, "hits": hits, "totals": totals, "failed": failed, "latencies": sorted(latencies)}


async def main(args) -> None:
    from sqlalchemy import select
    from app.database import async_session_maker
    from app.models.property import Property

    with open(args.labels, encoding="utf-8") as f:
        labels = {int(k): v for k, v in json.load(f).items()}
    async with async_session_maker() as db:
        props = (await db.execute(select(Property).where(Property.id.in_(labels)))).scalars().all()
    items = [(p, labels[p.id]) for p in props]
    if not items:
        sys.exit("none of the labelled ids is in the database")
    print(f"{len(items)} labelled listings · {len(args.models)} model(s)\n")

    results = [await run_model(m, items, async_session_maker) for m in args.models]
    fields = sorted({k for r in results for k in r["totals"]})
    w = max(len(m) for m in args.models) + 2
    print(f"{'field':14}" + "".join(f"{m:>{w}}" for m in args.models))
    for k in fields:
        print(f"{k:14}" + "".join(f"{r['hits'].get(k, 0):>{w - 4}}/{r['totals'].get(k, 0):<3}" for r in results))
    print(f"{'overall':14}" + "".join(
        f"{sum(r['hits'].values()):>{w - 4}}/{sum(r['totals'].values()):<3}" for r in results))
    print(f"{'failed':14}" + "".join(f"{r['failed']:>{w}}" for r in results))
    print(f"{'p50 s':14}" + "".join(
        f"{(r['latencies'][len(r['latencies']) // 2] if r['latencies'] else 0):>{w}.1f}" for r in results))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="دقت هر مدل در خواندن آگهی‌ها، فیلد به فیلد، در برابر برچسب‌های دستی")
    ap.add_argument("labels", help="JSON file: {property_id: {field: value}}")
    ap.add_argument("models", nargs="+", help="model names as the gateway lists them")
    asyncio.run(main(ap.parse_args()))
