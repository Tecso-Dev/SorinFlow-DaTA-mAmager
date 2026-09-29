"""
A saved Divar page, served to the real scraper. No browser, no network.

`FixturePage` answers the calls `DivarScraper.scrape_property_detail` makes on
a Playwright page from one saved HTML string. Most are plain (`goto`,
`content`, `query_selector`, `title`); four are JavaScript the scraper runs in
the page — the description, the photos, what the advertiser check harvests and
the publish line — and each of those is answered here by reading the same HTML
with BeautifulSoup, one small port per script, kept next to the calls that need
it. They are ports, not the scripts: what they return is what the script
returns on a page like the fixture, and no more.

The pages under tests/fixtures/divar are hand-built from the structure the
parsers read. When real captured pages replace them, nothing here changes.

Not a test module (the leading underscore keeps pytest away); imported bare, the
way tests/_fake_redis.py is.
"""
import json
import os
import re
import uuid
from typing import Dict, Optional

from bs4 import BeautifulSoup

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "divar")


def manifest() -> dict:
    """What each fixture page must read as — see expected.json."""
    with open(os.path.join(FIXTURE_DIR, "expected.json"), encoding="utf-8") as fh:
        return json.load(fh)


def load(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def url_of(token: str) -> str:
    return f"https://divar.ir/v/{token}"


def _clean(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:200]


class _Response:
    def __init__(self, status: int = 200):
        self.status = status


class FixturePage:
    """`pages` maps a listing token to its HTML. `goto(url)` opens the page whose
    token is in the URL and every later call answers from it."""

    def __init__(self, pages: Dict[str, str], title: str = ""):
        self.pages = pages
        self.tab_title = title
        self.url = "about:blank"
        self.html = ""
        self.soup = BeautifulSoup("", "lxml")
        self.opened: list = []

    # ── the calls the scrape makes ──────────────────────────────────────────

    async def goto(self, url, **_):
        token = re.search(r"/v/(?:[^/?#]+/)?([A-Za-z0-9_-]+)", url).group(1)
        self.html = self.pages[token]
        self.soup = BeautifulSoup(self.html, "lxml")
        self.url = url
        self.opened.append(token)
        return _Response(200)

    async def wait_for_selector(self, *_a, **_k):
        return None

    async def query_selector(self, selector):
        return object() if selector == "h1" and self.soup.select_one("h1") else None

    async def content(self):
        return self.html

    async def title(self):
        return self.tab_title

    async def evaluate(self, script, *_a):
        if "description-row__text--primary" in script and "BAD" in script:
            return self._description()
        if "const rows = []" in script:
            return self._harvest()
        if "startsWith('انتشار آگهی')" in script:
            return self._published()
        if "document.querySelectorAll('img')" in script:
            return self._photos()
        if "out.push(el.tagName" in script:
            return []
        return None

    # ── the page scripts, read off the same HTML ────────────────────────────

    def _description(self):
        """The first primary paragraph that is not the small publish line."""
        for p in self.soup.select('p[class*="description-row__text--primary"]'):
            if "description-row__text--small" in " ".join(p.get("class", [])):
                continue
            text = p.get_text().strip()
            if len(text) < 10 or any(b in text for b in ("موردی برای نمایش", "انتشار آگهی")):
                continue
            return text
        return None

    def _harvest(self):
        """What _extract_advertiser_type collects — rows, the contact block and
        every short standalone line — with no judgement, exactly as the script
        does it. decide_advertiser_type() makes the judgement."""
        rows = []
        for row in self.soup.select(".kt-base-row, .kt-unexpandable-row"):
            title = row.select_one('[class*="__title"]')
            if title is None:
                continue
            value = row.select_one('[class*="__value"], [class*="__end"]')
            rows.append([_clean(title.get_text(" ")), _clean(value.get_text(" ") if value else "")])
        contact = self.soup.select_one('[class*="contact"], [class*="seller"], [class*="advertiser"]')
        seen: Dict[str, None] = {}

        def push(text):
            text = _clean(text)
            if text and len(text) <= 80:
                seen[text] = None

        for a in self.soup.select("a"):
            push(a.get_text())
        for el in self.soup.select("p, span, h1, h2, h3, h4, div"):
            if el.find(True):          # leaf nodes only
                continue
            push(el.get_text())
        return {"rows": rows, "contact": _clean(contact.get_text(" ")) if contact else "",
                "panel": list(seen)[:400]}

    def _published(self):
        for el in self.soup.select("p, span"):
            text = el.get_text().strip()
            if text.startswith("انتشار آگهی"):
                return text
        return None

    def _photos(self):
        return [img.get("src") for img in self.soup.select("img")
                if "divarcdn.com" in (img.get("src") or "")]


class FakeSession:
    """The run's own session: it hands back the job row and holds nothing."""

    def __init__(self, job):
        self.job = job

    async def execute(self, stmt):
        row = self.job if "scraping_jobs" in str(stmt) else None

        class Result:
            def scalar_one_or_none(self):
                return row
        return Result()

    def add(self, _):
        pass

    async def commit(self):
        pass

    async def refresh(self, _):
        pass

    async def rollback(self):
        pass


async def nothing(*_a, **_k):
    return None


def make_job():
    from app.models.scraping_job import ScrapingJob
    return ScrapingJob(job_id=uuid.uuid4(), status="pending", new_items=0, updated_items=0,
                       failed_items=0, scraped_items=0, total_items=0, scraped_pages=0)


def make_scraper(page, spent=None):
    """A DivarScraper with nothing wired up but what a detail scrape touches.

    `spent` (a list) hears about everything a listing can cost — the contact
    reveal, the account's reveal budget, the photos — so a test can say a
    listing cost nothing."""
    from app.scraper.divar_scraper import DivarScraper
    from app.scraper.stealth import StealthConfig
    spent = spent if spent is not None else []
    s = DivarScraper.__new__(DivarScraper)
    s.stealth_config = StealthConfig()
    s.page, s.current_job, s.active_phone = page, None, None
    s._reveals_since_rotation, s.images_dir = 0, None
    s._phone_required = False
    s._check_rate_limit = nothing
    s._dwell_like_a_reader = nothing
    s._space_out_reveal = nothing
    s.maybe_rotate_account = nothing
    s._human_like_delay = nothing

    async def charge():
        spent.append("reveal")
        return 1

    async def download(*_a, **_k):
        spent.append("photos")
        return []

    async def one_account():
        return 1

    s._charge_reveal, s.download_images = charge, download
    s._usable_account_count = one_account
    return s


def patch_reveal(monkeypatch, spent):
    """The contact reveal, replaced by one that only writes down which listing
    it ran for — «contact:<token>» — and finds no number: the fixtures have
    none to give."""
    from app.scraper import divar_scraper as ds

    class NoContact:
        contact_channel, needs_identity = None, False

        def __init__(self, *_a, otp_key="", **_k):
            spent.append("contact:" + str(otp_key).rsplit(":", 1)[-1])

        async def get_phone_number(self):
            return None

    monkeypatch.setattr(ds, "ContactExtractor", NoContact)
    monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep", nothing)


class RunResult:
    """What a run over saved pages left behind."""

    def __init__(self, job, skipped, log, stored, spent, tokens):
        self.job, self.skipped, self.log = job, skipped, log
        self.stored, self.spent, self.tokens = stored, spent, tokens

    @property
    def saved(self):
        """Tokens of the listings that were saved, in order."""
        return [p["divar_id"] for p in self.stored]

    @property
    def revealed(self):
        """Tokens a contact reveal was spent on."""
        return [e.split(":", 1)[1] for e in self.spent if e.startswith("contact:")]

    @property
    def dropped(self):
        """{token: the filter bucket that dropped it}"""
        return {r["divar_id"]: r["reason"] for r in self.skipped}

    def lines(self, stage=None):
        return [e["message"] for e in self.log if stage is None or e["stage"] == stage]

    def events(self, needle):
        return [e for e in self.log if needle in e["message"]]


def make_runner(monkeypatch):
    """`await run(names, category, **filters)` — a run over saved pages, the way a
    list of URLs is run: the real start_scraping_job (its filters, its pre-reveal
    check, its annotation, its counters and its report), with the browser, the
    database session and the reveal replaced. `names` are fixture file names."""
    from app.scraper import otp_store
    from app.services import job_log, skipped_listings
    from _fake_redis import patch_redis

    async def run(names, category, *, edit=None, **filters):
        """`edit` maps a fixture name to a function that changes its HTML before it
        is served — a page with its photos taken out, say — so one fixture can
        stand for a second, slightly different ad."""
        skipped, log, stored, spent = [], [], [], []
        infos = {n: manifest()[n] for n in names}
        tokens = [infos[n]["token"] for n in names]

        async def record(_job_id, **row):
            skipped.append(row)
            return True

        async def event(_job_id, stage, message, level="info", **details):
            log.append({"stage": stage, "message": message, "level": level, **details})
            return True

        monkeypatch.setattr(skipped_listings, "record", record)
        monkeypatch.setattr(skipped_listings, "prune", nothing)
        monkeypatch.setattr(job_log, "record", event)
        monkeypatch.setattr(job_log, "prune", nothing)
        patch_redis(monkeypatch, otp_store)
        patch_reveal(monkeypatch, spent)

        edit = edit or {}
        page = FixturePage({infos[n]["token"]: edit.get(n, lambda h: h)(load(n)) for n in names})
        job = make_job()
        s = make_scraper(page, spent)
        s.db_session = FakeSession(job)

        async def save(property_data):
            stored.append(dict(property_data))
            s._last_save_created = True
            return object()

        s.save_property = save
        await s.start_scraping_job(
            city="—", category=category, max_items=len(names) + 10, download_images=False,
            job_id=str(job.job_id), urls=[url_of(t) for t in tokens], **filters)
        return RunResult(job, skipped, log, stored, spent, tokens)

    return run
