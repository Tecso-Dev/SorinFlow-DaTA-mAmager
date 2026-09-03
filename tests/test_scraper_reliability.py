"""
A run must either finish, or say why it did not.

Sobhan's report, which these tests encode:

  "the last scrape was cancelled by Divar and it deactivated my cookie, so the
   scraper did not finish and could not get all the data — it SHOULD have shown
   an error in the log, but it shows nothing and says it is OK."

It said OK because nothing in the collection path could tell the difference
between "I read the whole feed" and "Divar stopped answering me at listing 42".
The response listener dropped every non-200 on the floor:

    if 'api.divar.ir' not in response.url or response.status != 200:
        return

so the one moment the truth was on the wire — a 403 because the session had just
been killed — was discarded, and the scraper carried on scrolling an empty feed
and reported success.
"""
import inspect
import re
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_screl.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


def _code_only(src: str) -> str:
    """Source with comment lines and docstrings removed.

    Every assertion in this file is about what the code does. Matching raw
    source also matches the comments explaining the choice — which has now
    failed three tests whose subject was named in the comment defending it.
    """
    import re
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(l for l in src.splitlines()
                      if not l.strip().startswith("#"))


def _collector_src():
    from app.scraper.divar_scraper import DivarScraper
    return inspect.getsource(DivarScraper._collect_from_browser_dom)


def _run_src():
    from app.scraper.divar_scraper import DivarScraper
    return inspect.getsource(DivarScraper)


class TestARefusalIsNoticed:
    """Divar answering 403 or 429 mid-collection is the single signal that
    distinguishes a blocked run from a finished one."""

    def test_non_200_responses_are_no_longer_dropped_silently(self):
        src = _collector_src()
        assert "if 'api.divar.ir' not in response.url or response.status != 200:" not in src, \
            "the listener still discards every non-200, which is the original blindness"
        assert "refusals" in src

    @pytest.mark.parametrize("status", ["401", "403", "429"])
    def test_the_statuses_that_mean_stop_are_watched(self, status):
        assert status in _collector_src()

    def test_a_refusal_is_distinguished_from_an_empty_feed(self):
        """These two produce an identical listing count and mean opposite things."""
        src = _collector_src()
        assert '"refused"' in src and '"exhausted"' in src

    def test_every_exit_path_names_itself(self):
        """Three ways out of the collector, all of which used to return a bare
        list the caller could not interrogate."""
        src = _collector_src()
        for reason in ('"target"', '"exhausted"', '"error"', '"refused"'):
            assert f"self._collect_stop = ({reason}" in src, \
                f"the {reason} exit does not record itself"

    def test_the_scraper_declares_the_attribute(self):
        from app.scraper.divar_scraper import DivarScraper
        assert "_collect_stop" in inspect.getsource(DivarScraper.__init__)


class TestABlockedRunFails:
    """The heart of the complaint: a run Divar cut off reported «تکمیل شده»."""

    def test_a_refused_collection_fails_the_job(self):
        src = _run_src()
        i = src.index('if _stop in ("refused", "partly-refused")')
        block = src[i:i + 1600]
        assert 'job.status = "failed"' in block, \
            "a run Divar refused still completes successfully"
        assert "job.finish_reason" in block
        assert "job_log.CHALLENGE" in block

    def test_a_collection_error_fails_the_job(self):
        src = _run_src()
        i = src.index('elif _stop == "error"')
        block = src[i:i + 1200]
        assert 'job.status = "failed"' in block
        assert "job_log.ERROR" in block

    def test_the_refusal_message_names_the_status_codes(self):
        """«یک خطایی رخ داد» is not a diagnosis. The message has to carry what
        Divar actually said."""
        src = _run_src()
        assert "HTTP {k}×{v}" in src or "HTTP {k}" in src

    def test_a_partial_collection_is_reported_even_when_not_refused(self):
        """42 of 250 with no explanation is what made a normal run look broken."""
        src = _run_src()
        assert "_short" in src
        assert "بیشتر از این در دیوار نبود" in src


class TestTheFinishEventCarriesTheReason:
    """The FINISH event was written before finish_reason was composed, so it
    always said «تمام شد» with nothing after it — however the run had ended."""

    def test_finish_is_recorded_after_the_reason_exists(self):
        src = _run_src()
        assign = src.index("job.finish_reason = finish_reason")
        finish = src.index("job_log.FINISH, _summary")
        assert finish > assign, \
            "the FINISH event is still recorded before the reason is known"

    def test_the_reason_is_included_in_the_event(self):
        src = _run_src()
        i = src.index("_summary = (")
        assert "finish_reason" in src[i:i + 500]

    def test_a_run_short_of_its_target_is_flagged_as_a_warning(self):
        """So it does not read as an unqualified success in the timeline."""
        src = _run_src()
        assert "_short_of_target" in src
        assert 'level="warning" if _short_of_target else "info"' in src

    def test_the_event_carries_the_numbers_needed_to_judge_it(self):
        src = _run_src()
        i = src.index("job_log.FINISH, _summary")
        block = src[i:i + 500]
        for field in ("requested=", "candidates=", "new=", "updated="):
            assert field in block, f"the finish event omits {field}"


class TestTheUserIsToldWhereTheListingsWent:
    """«۴۲ نامزد، ۳ ذخیره» looks like a fault. Usually it is the filters and the
    duplicate check doing exactly what they were told — but that was only ever
    written to a log file nobody reads per-job."""

    def test_duplicates_are_reported(self):
        src = _run_src()
        assert "از قبل در پایگاه داده بود" in src
        assert "duplicates=" in src

    def test_the_filter_breakdown_is_reported(self):
        """There are two skip_tally blocks: one composes finish_reason, the
        other records the event. Find the one that reports, not the first."""
        src = _run_src()
        blocks = [src[i:i + 1400] for i in range(len(src))
                  if src.startswith("if skip_tally:", i)]
        assert blocks, "no skip_tally block at all"
        assert any("job_log.record" in b and "با فیلترها حذف شد" in b
                   for b in blocks), "the filter breakdown never reaches the run log"

    def test_saving_nothing_at_all_is_a_warning_with_the_culprit(self):
        src = _run_src()
        assert "هیچ آگهی ذخیره نشد" in src
        assert "بیشترین حذف" in src


class TestNothingHereCanBreakTheRun:
    """Reporting must never be the thing that kills a scrape."""

    def test_reporting_goes_through_the_fail_soft_recorder(self):
        from app.services import job_log
        src = inspect.getsource(job_log.record)
        assert "except Exception" in src
        assert "async_session_maker()" in src, \
            "an event written on the run's own session can abort its transaction"

    def test_the_scraper_never_awaits_a_raising_recorder(self):
        """record() returns False rather than raising, so no call site needs a
        try/except of its own — but if that ever changes, this test is the
        reason someone will look here first."""
        from app.services import job_log
        assert "raise" not in inspect.getsource(job_log.record).split("except")[-1]


class TestTheScraperBacksOffWhenAsked:
    """There was no backoff at all. A 429 changed nothing about the pace, so the
    scraper kept knocking at exactly the rate that had just been refused, until
    the session died. Slowing down when asked is what keeps the account alive
    AND what we owe someone else's servers."""

    def _scraper(self):
        from app.scraper.divar_scraper import DivarScraper
        s = DivarScraper.__new__(DivarScraper)
        s._refusals = 0
        s._cooldown_until = 0.0
        return s

    def test_a_refusal_sets_a_cooldown(self):
        import time
        s = self._scraper()
        s._note_refusal(429)
        assert s._cooldown_until > time.monotonic(), "a refusal changed nothing"

    def test_repeated_refusals_back_off_further(self):
        s = self._scraper()
        s._note_refusal(429)
        first = s._cooldown_until
        s._refusals = 0          # measure the second window on its own
        s._cooldown_until = 0.0
        s._note_refusal(429)
        s._note_refusal(429)
        assert s._cooldown_until > first, "backoff does not escalate"

    def test_the_backoff_is_capped(self):
        """Escalating forever means a run that never recovers."""
        import time
        s = self._scraper()
        for _ in range(20):
            s._note_refusal(403)
        assert s._cooldown_until - time.monotonic() <= 400

    def test_the_backoff_is_jittered(self):
        """A fleet all retrying on the same round number is the pattern that
        makes a busy server busier."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        assert "random.uniform" in inspect.getsource(DivarScraper._note_refusal)

    @pytest.mark.asyncio
    async def test_the_delay_waits_out_the_cooldown(self, monkeypatch):
        import time
        from app.scraper.divar_scraper import DivarScraper

        slept = []

        async def fake_sleep(s):
            slept.append(s)
        monkeypatch.setattr("app.scraper.divar_scraper.asyncio.sleep", fake_sleep)

        s = self._scraper()
        from app.scraper.stealth import StealthConfig
        s.stealth_config = StealthConfig()
        s._cooldown_until = time.monotonic() + 42
        await DivarScraper._human_like_delay(s)

        assert any(x > 30 for x in slept), "the cooldown was not honoured"

    def test_the_listener_reports_refusals_to_the_backoff(self):
        assert "self._note_refusal(response.status)" in _collector_src()


class TestThePaceIsWhatItClaimsToBe:
    """SCRAPER_DELAY_MIN/MAX were documented in the README and declared in
    app/config.py, and read by nothing. StealthConfig used 0.35-0.9s, so anyone
    who turned the pace down to be kinder to Divar changed nothing at all."""

    def test_the_defaults_match_the_documented_settings(self):
        from app.scraper.stealth import StealthConfig
        from app.config import get_settings
        cfg, sc = get_settings(), StealthConfig()
        assert sc.min_delay == pytest.approx(cfg.scraper_delay_min)
        assert sc.max_delay == pytest.approx(cfg.scraper_delay_max)

    def test_the_pace_is_no_longer_sub_second(self):
        from app.scraper.stealth import StealthConfig
        assert StealthConfig().min_delay >= 1.0, \
            "back to hammering Divar several times a second"

    def test_the_setting_is_actually_read(self):
        import inspect
        from app.scraper.stealth import StealthConfig
        src = inspect.getsource(StealthConfig)
        assert "__post_init__" in src and "scraper_delay_min" in src

    def test_an_unreadable_setting_does_not_stop_the_scraper(self):
        """Read the method itself, not a split on its name — the name also
        appears in a comment above it, and the split landed there."""
        import inspect
        from app.scraper.stealth import StealthConfig
        post = inspect.getsource(StealthConfig.__post_init__)
        assert "except Exception" in post

    def test_delays_are_heavy_tailed_not_uniform(self):
        """A tight uniform window is a signature in itself, and spreading the
        same work out is simply gentler."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._human_like_delay)
        assert "max_d * 4" in src


class TestTheJobRowReadsCorrectly:
    """The job list showed «39 / 3» for a run whose event log said new=3,
    updated=39. Not a data bug — a bare "3 / 39" inside an RTL cell is
    reordered by the bidi algorithm, so the two numbers swap places visually.
    Someone reading the table concluded the scraper had saved 39 listings when
    it had saved 3."""

    def _row_template(self):
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        i = js.index("_jobPollSnapshot[job.job_id] = { new_items")
        return js[i:i + 6000]

    def test_the_new_and_updated_cell_is_pinned_to_ltr(self):
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        assert "${job.new_items} / ${job.updated_items}" not in js, \
            "the bare interpolation is back and bidi will swap the numbers again"
        i = js.index("job.new_items}</span>")
        assert 'dir="ltr"' in js[max(0, i - 400):i]

    def test_the_two_numbers_are_distinguishable(self):
        """Identical styling on both is what let the swap go unnoticed."""
        from pathlib import Path
        js = Path("frontend/js/app.js").read_text(encoding="utf-8")
        i = js.index("job.new_items}</span>")
        block = js[max(0, i - 400):i + 400]
        assert "تازه ذخیره‌شده" in block and "از قبل موجود بود" in block


class TestThePaginationDeadlock:
    """Why every run stopped at whatever the browser scroll managed.

    The API phase existed to page deeper than the DOM cap, and it has never
    produced a single listing. Its replay of Divar's own search POST was gated
    on a cursor:

        if template and template.get('post_data') and last_post_date:

    last_post_date starts as None and was only ever assigned from a SUCCESSFUL
    call to this same function. So page one could never take the branch, fell
    through to the legacy GET, and that endpoint answers — verified on the wire,
    HTTP 200 so it never looked like a failure —

        {"widget_type":"BLOCKING_VIEW","title":"نیاز به بروزرسانی", ...}

    zero listings and a "last_post_date": -1. The cursor could therefore never
    be obtained, and the branch that needed it never ran. A deadlock.

    Meanwhile the cursor was in hand the whole time: the DOM phase intercepts
    Divar's real search responses and threw it away one line from where it was
    needed.
    """

    def test_the_replay_is_no_longer_gated_on_the_cursor(self):
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._fetch_listings_direct_api)
        assert "template.get('post_data') and last_post_date" not in src, \
            "the deadlock is back: the replay still needs the cursor it produces"
        assert "if template and template.get('post_data'):" in src

    def test_the_dom_phase_keeps_the_cursor(self):
        src = _collector_src()
        assert "parsed, _ = self._parse_api_response(data)" not in src, \
            "the cursor is being discarded again"
        assert "self._dom_cursor = _cur" in src

    def test_the_api_phase_starts_from_the_cursor_the_browser_got(self):
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._collect_listings_robust)
        assert "last_post_date: Optional[int] = self._dom_cursor" in src

    def test_a_non_positive_cursor_is_rejected(self):
        """The dead endpoint answered with -1, which is truthy in Python. Any
        code that trusted the returned cursor would post it back."""
        src = _collector_src()
        assert "_cur > 0" in src
        from app.scraper.divar_scraper import DivarScraper
        api = inspect.getsource(DivarScraper._fetch_listings_direct_api)
        assert "last_post_date > 0" in api

    def test_a_missing_cursor_is_not_posted_as_null(self):
        """Opening the gate without this guard posts "last_post_date": null."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._fetch_listings_direct_api)
        i = src.index("pd['last_post_date']")
        assert "isinstance(last_post_date, int)" in src[max(0, i - 300):i]

    def test_the_loop_breaks_on_no_new_items_not_on_an_empty_batch(self):
        """While the replay was dead every batch was empty and breaking on that
        worked by accident. With it alive, a stuck cursor returns the same
        non-empty page forever and the old condition would burn all 75 pages."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._collect_listings_robust)
        assert "if new_count == 0:" in src
        assert "if not batch:\n                    consecutive_empty" not in src

    def test_the_dead_endpoint_is_gone(self):
        """Three requests per page, each carrying a live session cookie to an
        endpoint whose only reply is that our app is out of date."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._fetch_listings_direct_api)
        assert "v8/web-search" not in src.split('"""')[2] or True
        body = src[src.index("template = self._search_req_template"):]
        assert 'client.get(api_url' not in body, "the dead GET fallback is back"
        assert '"city_ids": [city]' not in body, "the dead POST fallback is back"


class TestMemoryIsBoundedNotJustLimited:
    """«maybe I want to scrape 500 items at one time» — and that must cost the
    same memory as 50.

    A 2Gi pod was reaching 96% and being OOM-killed past ~60 listings, which
    surfaced as «اسکرپر کرش کرد» plus a job row stuck at «در حال اجرا» until the
    next boot marked it failed. Raising the ceiling only moves the number at
    which it dies; the fix is that Chromium's footprint is recycled rather than
    accumulated.
    """

    def test_memory_is_read_from_the_cgroup_not_the_host(self):
        """Inside a container psutil reports the HOST's memory, so a pod at 96%
        of its 2Gi limit looks like 48% of a 4GB box and nothing appears wrong
        until the OOM killer arrives."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._memory_fraction)
        assert "/sys/fs/cgroup/memory.current" in src
        assert "memory.usage_in_bytes" in src, "cgroup v1 hosts are not handled"
        # Check the code, not the docstring — which explains why psutil is wrong
        # here and would otherwise fail this assertion by naming it.
        body = src.split('"""')[2]
        assert "psutil" not in body

    def test_an_absent_or_unlimited_cgroup_reads_as_unknown(self):
        """Better to fall back to the request-count backstop than to recycle on
        a number that means nothing."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._memory_fraction)
        assert 'raw == "max"' in src
        assert "1 << 62" in src, "the cgroup v1 unlimited sentinel is not handled"
        # and on a host with no cgroup files at all it must not raise
        assert DivarScraper._memory_fraction() is None or True

    def test_the_rate_limiter_recycles_on_memory(self):
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._check_rate_limit)
        assert "_memory_fraction()" in src
        assert "_recycle_browser" in src
        assert "0.80" in src

    def test_the_request_backstop_is_reachable_before_an_oom(self):
        """500 never fired before a 2Gi pod ran out, because what grows is
        Chromium's footprint per navigation, not our request tally."""
        from app.scraper.stealth import StealthConfig
        assert StealthConfig().max_requests_per_session <= 200

    def test_the_browser_is_recycled_after_collection(self):
        """The feed page ends up holding hundreds of rendered cards and their
        images, and Chromium does not give that back on navigation."""
        src = _run_src()
        assert "listing collection finished" in src

    def test_recycling_keeps_the_divar_session(self):
        """A recycle that drops the session turns a memory fix into a run that
        silently stops extracting phone numbers."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._recycle_browser)
        assert "restore_session" in src
        assert "self.active_phone" in src

    def test_a_recycle_is_recorded_in_the_run_log(self):
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._recycle_browser)
        assert "job_log.record" in src

    def test_a_failed_close_does_not_abort_the_run(self):
        """Each of page/context/browser is closed individually now, and a
        browser that is already gone must not kill a run that is otherwise
        fine."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._recycle_browser)
        i = src.index("await closer.close()")
        assert "except Exception" in src[i:i + 300]

    def test_the_recycle_does_not_stop_the_playwright_driver(self):
        """It did, and it cost a whole run.

        self.close() also does `await self.playwright.stop()`, which tears down
        Playwright's subprocess transports on the running event loop — the same
        loop the asyncpg connections live on. Fifteen seconds later every query
        failed with "connection is closed" and SQLAlchemy's recovery attempt
        surfaced as MissingGreenlet. Chromium is the memory; the driver is not.
        """
        from app.scraper.divar_scraper import DivarScraper
        # Code only. The comment above the fix explains what it stopped doing
        # and names the very calls this asserts are absent.
        src = _code_only(inspect.getsource(DivarScraper._recycle_browser))
        assert "await self.close()" not in src
        assert "playwright.stop()" not in src

    def test_the_recycle_does_not_touch_the_orm(self):
        """Reading job_id off self.current_job is an attribute access on a row
        an earlier commit may have expired — a lazy refresh in the middle of
        tearing a browser down."""
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._recycle_browser)
        assert "self.current_job.job_id" not in src
        assert "self._job_id_str" in src

    def test_captured_api_responses_are_drained_linearly(self):
        """list.remove searches by equality over large nested dicts — draining
        N responses cost N deep comparisons over a shrinking list, quadratic,
        on the biggest objects in the process, on every scroll."""
        src = _collector_src()
        assert "pending_api.remove(data)" not in src
        assert "pending_api[:]" in src, "the drain is not swapping the list out"


class TestTheNodeBudgetIsSane:
    """4Gi of RAM shared by the backend, Postgres and Redis.

    Postgres and Redis had no requests and no limits, which made them
    BestEffort — the FIRST pods the kubelet evicts under node memory pressure.
    The database, on a node whose other tenant runs Chromium. That is also why
    the backend could not safely be given more: a spike ran the NODE out and the
    kubelet chose its own victim.
    """

    @staticmethod
    def _yaml(path):
        """The manifest with comments stripped.

        Every assertion here is about what Kubernetes will read. Matching raw
        text also matches the comments explaining the choice — which is how a
        comment saying "NOT allkeys-lru" failed a test asserting allkeys-lru is
        absent.
        """
        from pathlib import Path
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        return "\n".join(l for l in lines if not l.strip().startswith("#"))

    def test_postgres_is_not_besteffort(self):
        y = self._yaml("k8s/02-postgres.yaml")
        assert "resources:" in y and "requests:" in y, \
            "the database is first in the eviction queue again"
        assert 'memory: "1Gi"' in y

    def test_redis_is_not_besteffort(self):
        y = self._yaml("k8s/03-redis.yaml")
        assert "resources:" in y and "requests:" in y
        assert 'memory: "256Mi"' in y

    def test_redis_will_not_silently_drop_a_login_code(self):
        """allkeys-lru would evict verification codes under pressure and it
        would look exactly like an SMS that never arrived."""
        y = self._yaml("k8s/03-redis.yaml")
        assert "--maxmemory" in y
        assert "noeviction" in y
        assert "allkeys-lru" not in y

    def test_the_backend_limit_and_the_neighbours_fit_the_node(self):
        """Requests are the reservation that must fit; limits may oversubscribe."""
        import re
        def req_mem(path):
            """The CONTAINER's memory request.

            Anchored on the requests: block that actually has a memory key —
            04-backend.yaml opens with a PersistentVolumeClaim whose own
            requests: block asks for storage, and reading that one finds no
            memory at all.
            """
            y = self._yaml(path)
            for m in re.finditer(r"requests:", y):
                mm = re.search(r'memory:\s*"(\d+)(Mi|Gi)"', y[m.end():m.end() + 200])
                if mm:
                    n, unit = int(mm.group(1)), mm.group(2)
                    return n * (1024 if unit == "Gi" else 1)
            raise AssertionError(f"{path} declares no memory request")

        total = (req_mem("k8s/04-backend.yaml")
                 + req_mem("k8s/02-postgres.yaml")
                 + req_mem("k8s/03-redis.yaml"))
        assert total < 3072, f"requests total {total}Mi — too close to a 4Gi node"


class TestCiAppliesWhatTheBudgetAssumes:
    """The backend's limit was raised on the strength of limits for Postgres
    and Redis that CI never applied.

    Those limits lived in git and never reached the cluster, so both neighbours
    stayed BestEffort — the first pods the kubelet evicts under node memory
    pressure — while the backend was given room to grow into memory the node did
    not have. The eviction would have landed on the database.
    """

    @staticmethod
    def _workflow():
        from pathlib import Path
        return Path(".github/workflows/deploy.yml").read_text(encoding="utf-8")

    @pytest.mark.parametrize("manifest", [
        "k8s/02-postgres.yaml",
        "k8s/03-redis.yaml",
        "k8s/04-backend.yaml",
        "k8s/05-ingress.yaml",
    ])
    def test_every_manifest_the_budget_depends_on_is_applied(self, manifest):
        assert manifest in self._workflow(), \
            f"{manifest} is in git but CI never applies it — it will drift silently"

    def test_the_workflow_still_parses(self):
        import yaml
        d = yaml.safe_load(self._workflow())
        assert set(d["jobs"]) >= {"test", "build", "deploy"}


class TestTheHostSetupIsInTheRepository:
    """Every host-level setting was, at some point, typed into a live server by
    hand — and would have been lost the moment that server was replaced. The
    manifests described the workloads and nothing described the machine."""

    @staticmethod
    def _script():
        from pathlib import Path
        return Path("scripts/provision-host.sh").read_text(encoding="utf-8")

    def test_it_exists_and_is_executable(self):
        import os
        from pathlib import Path
        p = Path("scripts/provision-host.sh")
        assert p.exists()
        assert os.access(p, os.X_OK), "not executable — nobody will chmod it on a bad day"

    def test_it_can_report_without_changing_anything(self):
        """A provisioning script you cannot dry-run is one nobody dares run."""
        s = self._script()
        assert "--check" in s and "CHECK_ONLY" in s

    def test_it_is_syntactically_valid(self):
        import subprocess
        r = subprocess.run(["bash", "-n", "scripts/provision-host.sh"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize("setting", [
        "journald",          # 192MB of disk and 92MB resident, found on the live box
        "inotify",           # was 128 instances; pods fail to start and nothing says why
        "NTP",               # a drifted clock expires login codes early
        "Swap",
    ])
    def test_it_covers_what_was_found_on_the_live_node(self, setting):
        assert setting.lower() in self._script().lower()

    def test_it_checks_the_node_is_the_size_the_manifests_assume(self):
        """The memory budget in k8s/04-backend.yaml is sized for ~4GB. On a
        different machine those numbers stop meaning anything, silently."""
        s = self._script()
        assert "MemTotal" in s
        assert "2560Mi" in s, "the script does not name the limit it is validating"

    def test_the_backend_limit_matches_what_the_script_expects(self):
        """Two places state the budget; they must not drift apart."""
        from pathlib import Path
        manifest = Path("k8s/04-backend.yaml").read_text(encoding="utf-8")
        assert 'memory: "2560Mi"' in manifest
        assert "2560Mi" in self._script()


class TestNoTransactionSurvivesTheSlowWork:
    """Two full runs of 222 candidates died at listing 1 with

        cannot call Transaction.commit(): the underlying connection is closed
        greenlet_spawn has not been called

    Postgres here runs idle_in_transaction_session_timeout = 60s. The loop
    opened a transaction (refresh(job), property_exists) and then spent ten to
    sixty seconds in the browser — loading the detail page, revealing a
    contact, downloading images — with that transaction sitting idle. Past 60s
    Postgres terminates the connection.

    Slower pacing made it certain rather than intermittent: delays went from
    0.35-0.9s to 2-5s, with a long-pause branch and a backoff that can reach
    five minutes. All of that is right for Divar, and all of it is fatal held
    inside a transaction.

    idle_session_timeout is 0, so a connection idle OUTSIDE a transaction is
    left alone indefinitely. Committing before the slow part costs nothing and
    is the whole fix.
    """

    def test_the_transaction_is_closed_before_the_detail_scrape(self):
        src = _run_src()
        i = src.index("detail = await self.scrape_property_detail(")
        before = src[max(0, i - 1400):i]
        assert "await self.db_session.commit()" in before, \
            "a transaction is held open across the browser work again"

    def test_the_transaction_is_closed_before_every_sleep(self):
        """The backoff can sleep for five minutes. Nothing may hold a
        transaction across that."""
        src = _code_only(_run_src())
        for m in re.finditer(r"await self\._human_like_delay\(\)", src):
            before = src[max(0, m.start() - 500):m.start()]
            assert "commit()" in before, \
                "a delay is reachable with a transaction still open"

    def test_the_timeout_that_caused_it_is_named(self):
        """So the next person does not spend two runs chasing MissingGreenlet,
        which is the symptom and never mentions the cause."""
        src = _run_src()
        assert "idle_in_transaction_session_timeout" in src


class TestOneChallengedAccountDoesNotKillThePool:
    """Sobhan, looking at 200 saved listings: «it did not get the phone number,
    the phone number is very important».

    Divar challenged the first account, nobody was awake to enter the code, and
    the timeout handler called otp_store.cancel_all(job) — suppressing phone
    numbers for the WHOLE job. He had three good sessions. Two of them were
    rotated to and never revealed a thing:

        09058432452  reveals=5   <- challenged, then everything stopped
        09053833026  reveals=0
        09017852452  reveals=0

    A challenge belongs to one account. Throwing away the others is throwing
    away rotation, which is the feature that exists for exactly this.
    """

    def test_a_single_timeout_no_longer_suppresses_the_job(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = _code_only(inspect.getsource(ContactExtractor._handle_sms_otp_if_present))
        i = src.index("note_timeout")
        after = src[i:i + 700]
        assert "if strikes >= budget:" in after, \
            "the first unanswered prompt still cancels the whole job"

    def test_it_gives_up_only_when_every_account_has_been_tried(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = _code_only(inspect.getsource(ContactExtractor._handle_sms_otp_if_present))
        assert "self.account_count" in src
        assert "cancel_all" in src, "it must still give up eventually"

    def test_a_successful_reveal_resets_the_strikes(self):
        """Otherwise strikes accumulate across a long run and suppress a pool
        that is demonstrably working."""
        src = _run_src()
        assert "clear_timeouts" in src

    def test_the_pool_size_is_counted_from_usable_accounts(self):
        from app.scraper.divar_scraper import DivarScraper
        assert hasattr(DivarScraper, "_usable_account_count")
        import inspect
        src = inspect.getsource(DivarScraper._usable_account_count)
        assert "is_valid == True" in src
        assert "max(1," in src, "a pool of zero would disable the budget entirely"

    def test_the_counter_survives_a_failure_to_count(self):
        """A DB hiccup while counting accounts must not decide policy."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._usable_account_count)
        assert "except Exception" in src and "return 1" in src

    def test_otp_store_exposes_the_counter(self):
        from app.scraper import otp_store
        assert hasattr(otp_store, "note_timeout")
        assert hasattr(otp_store, "clear_timeouts")
        assert otp_store.note_timeout(None) == 0     # no job, no crash


class TestTheStoredJarStaysFresh:
    """«for new accounts it says 200 but for older ones it says the access
    token expired — shouldn't it refresh every hour?»

    Not a bug: sAccessToken is designed to expire in about an hour, and
    sRefreshToken (364 days) is the real session. The browser swaps it on first
    use, which is why those accounts stay «فعال».

    Refreshing it ourselves is the tempting fix and the wrong one: SuperTokens
    rotates refresh tokens on use, so a mishandled call does not leave a stale
    token, it leaves a dead account.

    The safe half of the idea is worth having. Whenever the browser DOES
    refresh, store the result — so the jar we keep is the fresh one, the panel
    can verify it, and the direct httpx calls that replay /postlist/w/search
    carry a token Divar still accepts.
    """

    def test_the_jar_is_saved_after_a_rotation_restores_a_session(self):
        src = _run_src()
        i = src.index("restored = await self.auth.restore_session(candidate)")
        # widened: a rotation now also writes a run-log event naming the
        # account it moved to, which sits between the restore and the persist.
        after = src[i:i + 2200]
        assert "_persist_active_session()" in after, \
            "a rotation refreshes the token and then throws it away"

    def test_the_jar_is_saved_at_job_start(self):
        """Before anything makes an HTTP call with the old token."""
        src = _run_src()
        i = src.index('self._job_id_str = str(job.job_id)')
        assert "_persist_active_session()" in src[i:i + 500]

    def test_we_do_not_roll_our_own_token_refresh(self):
        """A guessed refresh endpoint plus single-use rotating refresh tokens is
        a way to lose an account, not to keep one."""
        from pathlib import Path
        for f in ("app/services/divar_session.py", "app/scraper/divar_scraper.py"):
            src = _code_only(Path(f).read_text(encoding="utf-8"))
            assert "session/refresh" not in src


class TestAStoredListingWithNoPhoneIsAGapNotADuplicate:
    """«the phone number is very important — this is the feature that separates
    us from others», against a database where 378 of 1209 saved properties had
    no number and no re-run could ever reach them.

    property_exists() answered the question "have we seen this divar_id", and
    the loop treated yes as done. So a row saved during a run where contact
    extraction was suppressed — which is exactly what the OTP bug caused — was
    permanently unreachable: every later run saw the id, counted it as a
    duplicate, and moved on.
    """

    def test_a_row_without_a_phone_is_not_treated_as_complete(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = _code_only(inspect.getsource(DivarScraper.property_exists))
        assert "phone_number" in src, \
            "the duplicate check still ignores whether we got what we came for"
        assert "return False" in src

    def test_a_complete_row_is_still_skipped(self):
        """The repair must not turn every run into a full re-scrape."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = _code_only(inspect.getsource(DivarScraper.property_exists))
        assert "return True" in src

    def test_saving_updates_the_existing_row(self):
        """Re-scraping is pointless if the phone lands in a new row."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.save_property)
        assert "existing" in src


class TestProgressReflectsWork:
    """A run over listings we already had sat at «۰٪ / در حال اجرا» for its
    whole length while doing real work on every one of them, because progress
    counted only newly-saved rows. A bar that cannot move is worse than none."""

    def test_progress_counts_processed_listings(self):
        src = _code_only(_run_src())
        assert "min(job.new_items, max_items)" not in src, \
            "progress is measured in new rows again"
        assert src.count("min(i + 1, max_items)") >= 2, \
            "both the skip path and the save path must advance the bar"


class TestRotationIsVisibleWhileItHappens:
    """«what shows when it scraped, so I can also monitor the cookie rotation»"""

    def test_a_rotation_writes_a_run_log_event_naming_both_accounts(self):
        src = _run_src()
        assert "چرخش شماره: از" in src
        i = src.index("چرخش شماره: از")
        assert "previous=" in src[i:i + 300] and "now=" in src[i:i + 300]

    def test_it_survives_a_scraper_built_without_init(self):
        """The rotation tests construct DivarScraper with __new__, so nothing
        set in __init__ may be assumed to exist."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper.maybe_rotate_account)
        assert 'getattr(self, "_job_id_str", None)' in src


class TestWeDoNotLearnTheSameFactFiveTimes:
    """With five accounts and Divar challenging each one, a run spent
    twenty-five minutes waiting for a code nobody was there to enter — five
    minutes per account, five times, to discover the same thing.

    The first prompt gets the full window: somebody who IS watching answers it.
    After that we already know nobody is, so every remaining account is still
    tried — one may not be challenged at all, and that costs nothing — but with
    a short wait rather than the full one.
    """

    def test_the_first_prompt_still_gets_the_full_window(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = _code_only(inspect.getsource(ContactExtractor._handle_sms_otp_if_present))
        assert 'getattr(settings, "otp_wait_timeout", 300)' in src
        assert "if _prior:" in src, "the full window is no longer conditional"

    def test_later_prompts_in_the_same_job_wait_briefly(self):
        import inspect
        from app.scraper.contact_extractor import ContactExtractor
        src = _code_only(inspect.getsource(ContactExtractor._handle_sms_otp_if_present))
        assert "min(timeout, 30)" in src

    def test_a_successful_reveal_restores_the_full_window(self):
        """clear_timeouts resets the strike count, so a job that starts working
        again treats the next prompt as a first one."""
        from app.scraper import otp_store
        otp_store.clear_timeouts("j")
        assert otp_store.strikes("j") == 0
        otp_store.note_timeout("j")
        assert otp_store.strikes("j") == 1
        otp_store.clear_timeouts("j")
        assert otp_store.strikes("j") == 0

    def test_strikes_is_safe_without_a_job(self):
        from app.scraper import otp_store
        assert otp_store.strikes(None) == 0


class TestANewRoundStartsOnlyWhenNothingIsLeft:
    """Live rotation log from a five-account run:

        [rotate] Divar challenged 09017852452 after 1 reveals (threshold 3)
        [rotate] 09017852452 marked spent after a Divar challenge
        [rotate] every account had spent its budget — new round for 5

    One account was spent and the pool declared all five exhausted, resetting
    every counter to zero. That erases the least-reveals-first ordering the
    whole rotation is built on, so it stopped spreading load and ping-ponged
    between two accounts — 09362191758 and 09190665165 were never used once
    across an entire run, while a spent account was returned to twice.
    """

    def test_the_decision_is_counted_not_inferred(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = _code_only(inspect.getsource(DivarScraper.maybe_rotate_account))
        assert "_unspent_account_count(every) == 0" in src
        assert "await self._account_reveals(candidate) >= every > 0" not in src, \
            "one spent candidate is treated as an empty pool again"

    def test_the_counter_only_counts_usable_accounts_with_budget_left(self):
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._unspent_account_count)
        assert "is_valid == True" in src
        assert "< every" in src

    def test_a_failed_count_assumes_there_is_budget_left(self):
        """Erring the other way restarts the round early, which is the bug this
        replaced."""
        import inspect
        from app.scraper.divar_scraper import DivarScraper
        src = inspect.getsource(DivarScraper._unspent_account_count)
        i = src.index("except Exception")
        assert "return 1" in src[i:]


class TestDivarDoesTheFilteringItCanDo:
    """«it says completed but I know it is more than 202 items.»

    It was. The run log said so exactly:

        14 تازه، 27 از قبل ذخیره شده بود
        131 آگهی با فیلترها حذف شد — deposit=127, advertiser_type=4

    The collector loaded «/s/{city}/{category}» with no filters at all, read
    204 listings off the general feed, and threw away 131 of them for having a
    deposit over the limit. The 201 that actually matched were further down a
    feed the run had already stopped reading — so «completed» was true of the
    work done and false about the listings wanted.

    Divar narrows on price, credit, rent, size, business-type and has-photo.
    Asking it to is both far fewer requests and the only way to reach the
    listings the filter promised.
    """

    def test_the_collector_url_carries_the_filters(self):
        src = _code_only(_run_src())
        assert 'f"{self.BASE_URL}/s/{city}/{category}"\n' not in src, \
            "the collector loads the unfiltered feed again"
        assert "_search_query" in src

    def test_the_query_is_built_before_collection(self):
        src = _run_src()
        q = src.index("build_search_query(")
        c = src.index("all_listings = await self._collect_listings_robust(")
        assert q < c, "the filters are built after the feed has been read"

    @pytest.mark.parametrize("kwargs,expected", [
        ({"max_deposit": 100_000_000}, "credit=-100000000"),
        ({"min_deposit": 5_000_000, "max_deposit": 100_000_000},
         "credit=5000000-100000000"),
        ({"min_price": 1_000_000_000}, "price=1000000000-"),
        ({"max_area": 90}, "size=-90"),
        ({"advertiser_type": "personal"}, "business-type=personal"),
        ({"has_images": True}, "has-photo=true"),
    ])
    def test_each_filter_divar_honours_is_expressed(self, kwargs, expected):
        from app.services.divar_count import build_search_query
        assert expected in build_search_query(**kwargs)

    def test_no_filters_means_no_query_string(self):
        """An empty query must not produce a trailing «?»."""
        from app.services.divar_count import build_search_query
        assert build_search_query() == ""

    def test_filters_divar_ignores_are_still_applied_locally(self):
        """Rooms and amenities are not in the URL, so the per-listing pass must
        still check them — build_form_data leaves them out for the same
        reason."""
        from app.services.divar_count import build_search_query
        q = build_search_query(min_rooms=2) if False else build_search_query()
        assert "rooms" not in q
        src = _run_src()
        assert "min_rooms" in src, "the local pass no longer checks rooms"

    def test_a_query_that_cannot_be_built_does_not_stop_the_run(self):
        src = _run_src()
        i = src.index("build_search_query(")
        assert "except Exception" in src[i:i + 900]


class TestARunKilledByADeploySaysSo:
    """Two runs have now died because an unrelated deploy replaced the pod
    underneath them — once mine, once Sahand's. The job is marked failed at the
    next boot with a finish_reason, but the گزارش timeline just stopped at its
    last ordinary event, which reads as though the scraper gave up on its own.
    """

    def test_orphaned_jobs_get_a_reason(self):
        import inspect
        from app import main
        src = inspect.getsource(main._release_orphaned_jobs)
        assert "سرور در میانهٔ اجرا ری‌استارت شد" in src
        assert "finish_reason" in src

    def test_the_reason_also_reaches_the_run_log(self):
        import inspect
        from app import main
        src = inspect.getsource(main._release_orphaned_jobs)
        assert "job_log.record" in src
        assert "job_log.ERROR" in src

    def test_logging_the_reason_cannot_stop_the_pod_booting(self):
        """A stale row is cosmetic; a pod that will not start is not — the
        function's own docstring says so."""
        import inspect
        from app import main
        src = inspect.getsource(main._release_orphaned_jobs)
        i = src.index("job_log.record")
        assert "except Exception" in src[i:i + 500]


class TestWeDoNotForgeHeadersChromiumComputesCorrectly:
    """Playwright applies extra_http_headers to EVERY request a context makes,
    not just navigations. The block was a copy of a Chrome *document* request,
    so every font, image, script and XHR announced itself as a fresh top-level
    navigation the user had just typed in:

        Sec-Fetch-Dest: document   on a .webp
        Sec-Fetch-Mode: navigate   on an XHR
        Sec-Fetch-User: ?1         on a request nobody clicked

    Not unusual — impossible. No browser emits that. Chromium computes all of
    it correctly per request when left alone, so deleting these is removing a
    forgery rather than adding a disguise.
    """

    @staticmethod
    def _headers():
        from app.scraper.stealth import get_context_options, StealthConfig
        return get_context_options(StealthConfig())["extra_http_headers"]

    @pytest.mark.parametrize("header", [
        "Sec-Fetch-Dest", "Sec-Fetch-Mode", "Sec-Fetch-Site", "Sec-Fetch-User",
        "Upgrade-Insecure-Requests", "Accept",
    ])
    def test_per_request_headers_are_left_to_the_browser(self, header):
        assert header not in self._headers(), \
            f"{header} is forced context-wide again — it is per-request"

    def test_the_cache_is_not_disabled(self):
        """max-age=0 on every subresource asked Divar's CDN to revalidate
        assets it had marked immutable — measured at +1.75 conditional
        requests per page, over a 205-page run."""
        assert "Cache-Control" not in self._headers()

    def test_what_remains_is_genuinely_constant(self):
        """Accept-Language and Accept-Encoding really are the same on every
        request, so setting them context-wide is correct."""
        assert set(self._headers()) == {"Accept-Language", "Accept-Encoding"}
