"""
The forwarder app's APK, served from this site instead of from GitHub.

The panel's «دانلود برای اندروید» button points at sorinflow.com. It has to:
GitHub's release downloads come from objects.githubusercontent.com, which is
slow from Iran and blocked for some carriers, and a person on the phone that
needs the app is exactly the person who cannot fetch it from there.

So this keeps a copy. Every few hours it asks GitHub which release is the
latest, and when the tag moved it downloads the APK to the data volume and
swaps it into place atomically — the file the panel serves is never half
written. The version sits next to it in a small text file, which is what the
guide shows beside the button.

Never raises out of the loop: a mirror that cannot reach GitHub keeps serving
the copy it already has.
"""
import asyncio
import os
from pathlib import Path

import httpx
from loguru import logger

from app.config import get_settings

settings = get_settings()

REPO = "sobhanaz/sorinflow-sms-forwarder"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
APK_NAME = "sorinflow-forwarder.apk"
CHECK_SECONDS = 6 * 3600


def downloads_dir() -> Path:
    return Path(getattr(settings, "downloads_path", "/app/data/downloads"))


def apk_path() -> Path:
    return downloads_dir() / APK_NAME


def mirrored_version() -> str:
    """The tag of the copy on disk, or '' when there is none."""
    try:
        if apk_path().exists():
            return (downloads_dir() / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def pick_apk_asset(release: dict) -> tuple:
    """(tag, download url, size) of the APK in a GitHub release payload."""
    tag = str(release.get("tag_name") or "").strip()
    for a in release.get("assets") or []:
        name = str(a.get("name") or "")
        if name.endswith(".apk"):
            return tag, a.get("browser_download_url"), int(a.get("size") or 0)
    return tag, None, 0


async def refresh() -> dict:
    """One check. Downloads only when the release moved."""
    have = mirrored_version()
    async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "sorinflow-apk-mirror"}) as c:
        r = await c.get(LATEST_URL)
        r.raise_for_status()
        tag, url, size = pick_apk_asset(r.json())
        if not tag or not url:
            return {"changed": False, "version": have, "reason": "no apk asset in the latest release"}
        if tag == have:
            return {"changed": False, "version": have}

        d = downloads_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (APK_NAME + ".part")
        # Streamed to a side file and renamed at the end: the name the panel
        # serves either holds the previous complete APK or the new one.
        async with c.stream("GET", url, timeout=600) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as fh:
                async for chunk in resp.aiter_bytes(1 << 16):
                    fh.write(chunk)
        got = tmp.stat().st_size
        if size and got != size:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"short download: {got} of {size} bytes")
        os.replace(tmp, apk_path())
        (d / "VERSION").write_text(tag + "\n", encoding="utf-8")
        logger.info(f"[apk-mirror] now serving {tag} ({got // 1024} KB), was {have or 'nothing'}")
        return {"changed": True, "version": tag, "bytes": got}


async def mirror_loop() -> None:
    """Runs for the life of the process. APK_MIRROR_HOURS=0 disables."""
    every = float(getattr(settings, "apk_mirror_hours", 6) or 0)
    if every <= 0:
        logger.info("[apk-mirror] disabled")
        return
    await asyncio.sleep(120)         # let startup finish
    while True:
        try:
            await refresh()
        except Exception as e:
            logger.warning(f"[apk-mirror] could not refresh: {type(e).__name__}: {e}")
        await asyncio.sleep(every * 3600)
