"""
What the site tells a crawler — and what it told one before.

/robots.txt and /sitemap.xml did not exist, so the API-key middleware answered
both with 401: a search engine asking for the crawl rules was turned away, and
with no rules to read, nothing said the panel was off limits. The landing page
carried a title and a description and nothing else — no canonical, no preview
card, and not one machine-readable fact about what the product does, on a page
built almost entirely out of gradients and animation.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_seo.db")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

import app.main as m  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LANDING = (ROOT / "frontend/landing.html").read_text(encoding="utf-8")


def _client():
    from fastapi.testclient import TestClient
    return TestClient(m.app)      # no lifespan: startup wants Postgres


class TestWhatCrawlersAreTold:

    def test_they_are_reachable_with_the_api_key_gate_on(self, monkeypatch):
        """The trap this file's own comments keep describing: locally API_KEY is
        empty so the gate never runs, and in production a crawler asking for the
        rules got 401 JSON. Every one of these is fetched with no credential."""
        monkeypatch.setattr(m.settings, "api_key", "a-key-is-configured", raising=False)
        for path in ("/robots.txt", "/sitemap.xml", "/llms.txt", "/og.png"):
            r = _client().get(path)
            assert r.status_code != 401, f"{path} is behind the API key"


    def test_robots_is_reachable_at_all(self):
        r = _client().get("/robots.txt")
        assert r.status_code == 200, "it used to 401 — no rules could be read"
        ct = r.headers["content-type"]
        assert "text/plain" in ct
        assert ct.count("charset") == 1, f"Starlette adds charset itself: {ct}"

    def test_robots_closes_the_panel_and_the_api(self):
        body = _client().get("/robots.txt").text
        for shut in ("/dashboard/", "/api/", "/images/", "/downloads/"):
            assert f"Disallow: {shut}" in body

    def test_robots_names_the_sitemap(self):
        body = _client().get("/robots.txt").text
        assert re.search(r"^Sitemap: https?://[^/]+/sitemap\.xml$", body, re.M)

    def test_the_sitemap_lists_the_public_pages_and_nothing_else(self):
        body = _client().get("/sitemap.xml").text
        assert body.startswith("<?xml")
        locs = re.findall(r"<loc>(.*?)</loc>", body)
        assert len(locs) == 2 and any(l.endswith("/portal/") for l in locs)
        assert not any("/dashboard" in l for l in locs)

    def test_the_links_follow_the_host_the_request_came_on(self):
        """Hard-coding one hostname breaks the moment the site answers on
        another — a staging host, or the bare domain beside the www one."""
        r = _client().get("/robots.txt", headers={"host": "example.test"})
        assert "example.test/sitemap.xml" in r.text

    def test_the_panel_says_noindex_whatever_robots_says(self):
        """A panel URL pasted in a chat is still not something to index."""
        r = _client().get("/dashboard/index.html")
        if r.status_code == 200:
            assert "noindex" in r.headers.get("x-robots-tag", "")

    def test_llms_txt_describes_the_product_in_words(self):
        r = _client().get("/llms.txt")
        assert r.status_code == 200
        for fact in ("اسکرپر", "CRM", "/portal", "sorinflow.com"):
            assert fact in r.text


class TestWhatTheLandingPageSays:

    def test_it_declares_one_canonical_address(self):
        assert '<link rel="canonical" href="https://sorinflow.com/">' in LANDING

    def test_the_link_preview_is_complete(self):
        """A card missing any one of these renders as a bare grey link."""
        for tag in ("og:title", "og:description", "og:image", "og:url", "og:type",
                    "twitter:card", "twitter:image"):
            assert tag in LANDING, f"{tag} is missing"
        assert 'content="1200"' in LANDING and 'content="630"' in LANDING

    def test_the_preview_image_exists_and_is_the_size_it_claims(self):
        png = ROOT / "frontend/og.png"
        assert png.exists()
        head = png.read_bytes()[:33]
        assert head[:8] == b"\x89PNG\r\n\x1a\n"
        w = int.from_bytes(head[16:20], "big")
        h = int.from_bytes(head[20:24], "big")
        assert (w, h) == (1200, 630), f"og:image claims 1200×630, file is {w}×{h}"

    def test_the_preview_image_is_actually_served(self):
        r = _client().get("/og.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"

    def test_the_structured_data_parses(self):
        blob = re.search(r'<script type="application/ld\+json">(.*?)</script>', LANDING, re.S)
        assert blob, "no structured data at all"
        graph = json.loads(blob.group(1))["@graph"]
        assert {n["@type"] for n in graph} >= {"SoftwareApplication", "Organization", "WebSite", "FAQPage"}

    def test_the_questions_are_answered_not_just_asked(self):
        """An answer engine quotes the answer; a question with an empty one is
        worse than no FAQ at all."""
        blob = re.search(r'<script type="application/ld\+json">(.*?)</script>', LANDING, re.S)
        faq = next(n for n in json.loads(blob.group(1))["@graph"] if n["@type"] == "FAQPage")
        assert len(faq["mainEntity"]) >= 4
        for q in faq["mainEntity"]:
            assert q["name"].endswith("؟")
            assert len(q["acceptedAnswer"]["text"]) > 60, f"«{q['name']}» is answered in a phrase"

    def test_the_facts_match_the_page(self):
        """Structured data that disagrees with the page is worse than none."""
        blob = re.search(r'<script type="application/ld\+json">(.*?)</script>', LANDING, re.S)
        org = next(n for n in json.loads(blob.group(1))["@graph"] if n["@type"] == "Organization")
        assert org["email"] in LANDING
        assert org["telephone"].replace("+98", "0") == "09125005495"
        assert "Tecso" in LANDING
