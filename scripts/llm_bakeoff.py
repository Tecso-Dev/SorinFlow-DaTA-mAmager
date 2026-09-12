#!/usr/bin/env python3
"""
Which model reads Persian ads best — measured on THIS project's listings.

No published benchmark covers the one thing this codebase would ask a model
to do: read a Divar listing in Persian, say whether an agency wrote it, and
return JSON. So this asks each candidate model the same question about the
same real listings and reports who agreed with whom, how fast, and at what
cost. Nothing here writes to the database.

Run inside the backend pod, where the database and LLM settings already are:

    kubectl exec -n sorinflow deploy/backend -- python scripts/llm_bakeoff.py \
        openai/gpt-4.1-mini deepseek/deepseek-v4-pro google/gemini-2.5-flash

Model identifiers are whatever the gateway lists; they are passed through
verbatim. LLM_BASE_URL and LLM_API_KEY come from the environment (or the
panel) exactly as match_service reads them.

The comparison has three columns because there is no ground truth:

  divar   what the poster declared (شخصی / املاکی). Known to be unreliable —
          the reason advertiser_signals.py exists.
  regex   what app/services/advertiser_signals.py says from the words.
  model   what the model says, with the phrase it points to.

Where the model disagrees with BOTH, the row is printed in full so a person can
read the ad and decide who was right. That reading is the actual benchmark.
"""
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings                      # noqa: E402
from app.database import async_session_maker             # noqa: E402
from app.models.property import Property                 # noqa: E402
from app.services import advertiser_signals              # noqa: E402

settings = get_settings()

N = int(os.environ.get("BAKEOFF_N", "40"))
CONCURRENCY = 4

PROMPT = """این متن یک آگهی ملک از دیوار است. فقط بر اساس متن بگو آگهی را «مشاور/آژانس املاک» نوشته یا «مالک/شخصی».
به نفی دقت کن: «بدون واسطه» و «بدون کمیسیون» یعنی شخصی، نه املاکی.
فقط JSON برگردان، بدون توضیح:
{{"agency": true یا false, "evidence": "عبارتی از متن که تصمیم را می‌سازد یا null", "confidence": 0 تا 1}}

عنوان: {title}
متن:
{description}"""


@dataclass
class Result:
    model: str
    agree_divar: int = 0
    agree_regex: int = 0
    disagree_both: list = field(default_factory=list)
    failed: int = 0
    latencies: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


async def ask(client: httpx.AsyncClient, base: str, key: str, model: str,
              title: str, description: str):
    t0 = time.monotonic()
    resp = await client.post(
        f"{base.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT.format(
                title=title or "", description=(description or "")[:2500])}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": 200,
        },
    )
    dt = time.monotonic() - t0
    resp.raise_for_status()
    body = resp.json()
    content = body["choices"][0]["message"]["content"]
    usage = body.get("usage") or {}
    return json.loads(content), dt, usage


async def run_model(model: str, rows, base: str, key: str) -> Result:
    r = Result(model=model)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(p: Property):
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    data, dt, usage = await ask(client, base, key, model,
                                                p.title, p.description)
            except Exception as e:
                r.failed += 1
                return
            r.latencies.append(dt)
            r.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            r.completion_tokens += int(usage.get("completion_tokens") or 0)

            model_says = bool(data.get("agency"))
            divar_says = (p.advertiser_type or "").lower() == "agency"
            regex_says, regex_why = advertiser_signals.detect(p.title, p.description)

            if model_says == divar_says:
                r.agree_divar += 1
            if model_says == regex_says:
                r.agree_regex += 1
            if model_says != divar_says and model_says != regex_says:
                r.disagree_both.append({
                    "id": p.id,
                    "model": model_says, "divar": divar_says, "regex": regex_says,
                    "model_evidence": data.get("evidence"),
                    "regex_evidence": regex_why,
                    "title": p.title,
                    "description": (p.description or "")[:300],
                })

    await asyncio.gather(*(one(p) for p in rows))
    return r


async def main(models):
    base = (settings.llm_base_url or "").strip()
    key = (settings.llm_api_key or "").strip()
    if not base or not key:
        sys.exit("LLM_BASE_URL / LLM_API_KEY are not set — nothing to test against")

    async with async_session_maker() as db:
        rows = (await db.execute(
            select(Property)
            .where(Property.description.isnot(None))
            .order_by(Property.id.desc())
            .limit(N)
        )).scalars().all()
    if not rows:
        sys.exit("no listings with a description in the database")

    print(f"{len(rows)} listings · {len(models)} model(s) · gateway {base}\n")
    results = [await run_model(m, rows, base, key) for m in models]

    n = len(rows)
    print(f"{'model':38} {'vs divar':>9} {'vs regex':>9} {'neither':>8} {'failed':>7} "
          f"{'p50 s':>6} {'p95 s':>6} {'tokens in/out':>15}")
    for r in results:
        lat = sorted(r.latencies) or [0]
        p50 = lat[len(lat) // 2]
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        print(f"{r.model:38} {r.agree_divar:>5}/{n:<3} {r.agree_regex:>5}/{n:<3} "
              f"{len(r.disagree_both):>8} {r.failed:>7} {p50:>6.1f} {p95:>6.1f} "
              f"{r.prompt_tokens:>7}/{r.completion_tokens:<7}")

    # The rows worth a human's eyes: the model stood alone. Either it saw
    # something both Divar and the regex missed, or it invented something.
    for r in results:
        if not r.disagree_both:
            continue
        print(f"\n── {r.model}: disagreed with both divar and regex on "
              f"{len(r.disagree_both)} listing(s) ──")
        for d in r.disagree_both[:12]:
            print(f"\n#{d['id']}  model={'agency' if d['model'] else 'personal'}  "
                  f"divar={'agency' if d['divar'] else 'personal'}  "
                  f"regex={'agency' if d['regex'] else 'personal'}")
            print(f"  model evidence: {d['model_evidence']!r}")
            print(f"  regex evidence: {d['regex_evidence']!r}")
            print(f"  {d['title']}")
            print(f"  {d['description']}")


if __name__ == "__main__":
    models = sys.argv[1:]
    if not models:
        sys.exit(__doc__)
    asyncio.run(main(models))
