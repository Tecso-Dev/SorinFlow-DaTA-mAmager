"""
The container has no tz database. Anything that needs one dies at import.

On 2026-09-18 two modules did ZoneInfo("Asia/Tehran") at import time. It
passed every test on a Mac, which has tzdata, and crash-looped the only pod
in production, which does not. This runs the app's imports with the tz
database hidden, the way the Playwright image sees the world.
"""
import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_the_app_imports_without_a_tz_database():
    code = r"""
import sys, zoneinfo
# no tzdata: neither the system's nor the PyPI package
zoneinfo.reset_tzpath(to=[])
sys.modules['tzdata'] = None
import os
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///./_tz.db')
os.environ.setdefault('SECRET_KEY', '0123456789abcdef0123456789abcdef')
os.environ.setdefault('LOGS_PATH', '/tmp'); os.environ.setdefault('IMAGES_PATH', '/tmp')
import app.main
import app.services.scrape_scheduler, app.crm.call_queue, app.services.backup_service
print('ok')
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-2000:]


def test_a_rollout_that_never_comes_up_rolls_itself_back():
    """Recreate strategy: the old pod is gone before the new one is tried, so
    a new pod that never becomes ready is the site being down. The deploy
    job must put the previous image back on its own."""
    wf = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           ".github/workflows/deploy.yml"), encoding="utf-8").read()
    step = wf[wf.index("Roll out new image"):wf.index("Why the rollout failed")]
    assert "if ! kubectl rollout status" in step
    assert "kubectl rollout undo deployment/backend" in step
    assert "exit 1" in step, "a rolled-back deploy must still be reported as failed"
