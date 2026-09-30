"""
Ending a «تلاش دوباره» (#58) — one rule for every way it can end.

A retry puts a finished run back in the queue (config.retry, with the status
and finish line it had). However it ends — done, cancelled, its worker gone,
a day in the queue, a config that no longer reads — it is the retry that
ends, not the run: the row goes back to the status it had, a line saying
what happened goes in front of its old finish line (the original, kept once
in config.finish_before_retry, so retries never stack), and config.retry
comes off. Callers write these values in the same conditional UPDATE they
already make, so a cancel or the sweep that got there first still wins.
"""
from typing import Any, Dict, Optional

CANCELLED = "تلاش دوباره لغو شد"
ORPHANED = "تلاش دوباره نیمه‌کاره ماند: سرور در میانهٔ آن ری‌استارت شد"
STALE = "تلاش دوباره بیش از ۲۴ ساعت در صف ماند و اجرا نشد"
UNREADABLE = "تلاش دوباره اجرا نشد: تنظیمات ذخیره‌شدهٔ این اسکرپ خوانا نبود"


def retry_of(config: Any) -> Optional[Dict[str, Any]]:
    """The retry a row is in, or None."""
    retry = config.get("retry") if isinstance(config, dict) else None
    return retry if isinstance(retry, dict) else None


def ended(config: Any, line: str) -> Optional[Dict[str, Any]]:
    """{status, finish_reason, config} for a row whose retry ends with
    `line`; None for a row that is not in a retry."""
    retry = retry_of(config)
    if retry is None:
        return None
    cfg = dict(config)
    cfg.pop("retry", None)
    base = cfg.get("finish_before_retry", retry.get("prev_finish"))
    cfg["finish_before_retry"] = base
    reason = f"{line}؛ {base}" if base else line
    return {"status": retry.get("prev_status") or "completed",
            "finish_reason": reason[:300], "config": cfg}
