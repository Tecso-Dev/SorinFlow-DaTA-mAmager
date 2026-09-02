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
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_test_screl.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


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
