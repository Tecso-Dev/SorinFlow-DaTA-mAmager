"""
A missing OpenCV must not take the whole application down.

captcha_solver already guards its import and falls back to «captcha
unsolvable» at run time — but one cv2 constant sat in a method signature,
where it is evaluated when the class is created. That turned an optional
vision dependency into a hard import-time requirement for every module
that transitively imports the scraper, which is most of the app.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_cap.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("LOGS_PATH", "/tmp")
os.environ.setdefault("IMAGES_PATH", "/tmp")


class TestCaptchaSolverImportsWithoutOpenCV:
    """The grader and everything else lives behind this import."""

    def test_no_cv2_attribute_is_read_at_class_creation(self):
        src = inspect.getsource
        from app.scraper import captcha_solver
        sig = inspect.signature(captcha_solver.PuzzleCaptchaSolver._template_match)
        assert sig.parameters["method"].default is None, (
            "a cv2.* default is evaluated when the class is created, so a "
            "missing OpenCV takes the whole app down at import"
        )
