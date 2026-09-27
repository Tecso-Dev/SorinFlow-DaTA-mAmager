"""
The Divar login's debug screenshots and page dump go to the data volume.
They were written to /app, which is read-only in the container, so every
login logged «Could not take screenshot» and kept nothing (1405/07/05).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_ldf.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")

from app.scraper import auth  # noqa: E402

AUTH_SRC = Path(auth.__file__).read_text(encoding="utf-8")


def test_debug_files_land_beside_the_images(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.settings, "images_path", str(tmp_path / "images"))
    target = auth._debug_file("debug_before_login_click.png")
    assert target == tmp_path / "debug" / "debug_before_login_click.png"
    assert target.parent.is_dir(), "the folder must exist before the browser writes"
    target.write_bytes(b"png")


def test_nothing_is_written_to_the_read_only_app_folder():
    assert '"/app/debug' not in AUTH_SRC
    assert AUTH_SRC.count("_debug_file(") >= 4   # the helper and its three users
