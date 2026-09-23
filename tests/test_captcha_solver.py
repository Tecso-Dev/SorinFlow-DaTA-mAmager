"""
Tests for app/scraper/captcha_solver.py
Uses synthetic numpy images — no real captcha files needed.
"""
import os
import pytest
import tempfile

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

pytestmark = pytest.mark.skipif(not HAS_CV2, reason="OpenCV not installed")


def _make_bg(width=300, height=150, gap_x=120, gap_w=50):
    """Create a fake background image with a dark 'gap' region."""
    bg = np.ones((height, width, 3), dtype=np.uint8) * 180  # grey
    # Dark gap shadow at gap_x
    bg[:, gap_x:gap_x + gap_w] = 80
    return bg


def _make_gap_piece(width=50, height=150):
    """Create a fake puzzle piece (solid dark block with white background)."""
    img = np.ones((height, width, 3), dtype=np.uint8) * 255  # white bg
    img[10:height - 10, 5:width - 5] = 60   # dark puzzle piece
    return img


def _write_img(arr):
    """Write numpy array to a temp file, return path."""
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(f.name, arr)
    return f.name


@pytest.fixture
def captcha_files():
    bg_img   = _make_bg(gap_x=120, gap_w=50)
    gap_img  = _make_gap_piece(width=50, height=150)
    bg_path  = _write_img(bg_img)
    gap_path = _write_img(gap_img)
    out_path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    yield bg_path, gap_path, out_path
    for p in (bg_path, gap_path, out_path):
        try:
            os.unlink(p)
        except OSError:
            pass


class TestPuzzleCaptchaSolver:
    from app.scraper.captcha_solver import PuzzleCaptchaSolver

    def _solver(self, bg, gap, out):
        from app.scraper.captcha_solver import PuzzleCaptchaSolver
        return PuzzleCaptchaSolver(gap, bg, out)

    def test_returns_integer_position(self, captcha_files):
        bg, gap, out = captcha_files
        solver = self._solver(bg, gap, out)
        result = solver.discern()
        assert result is not None
        assert isinstance(result, int)

    def test_position_in_valid_range(self, captcha_files):
        bg, gap, out = captcha_files
        solver = self._solver(bg, gap, out)
        result = solver.discern()
        assert result is not None
        assert 0 <= result <= 300

    def test_detects_gap_approximately(self, captcha_files):
        bg, gap, out = captcha_files
        solver = self._solver(bg, gap, out)
        result = solver.discern()
        # Gap is at x=120 with width=50; allow ±30px tolerance
        assert result is not None
        assert abs(result - 120) <= 30, f"Expected ~120, got {result}"

    def test_missing_bg_returns_none(self, captcha_files):
        _, gap, out = captcha_files
        solver = self._solver("/nonexistent/bg.png", gap, out)
        assert solver.discern() is None

    def test_missing_gap_returns_none(self, captcha_files):
        bg, _, out = captcha_files
        solver = self._solver(bg, "/nonexistent/gap.png", out)
        assert solver.discern() is None

    def test_debug_image_created(self, captcha_files):
        bg, gap, out = captcha_files
        solver = self._solver(bg, gap, out)
        solver.discern()
        assert os.path.exists(out)

    def test_crop_nonwhite_all_white(self):
        from app.scraper.captcha_solver import PuzzleCaptchaSolver
        solver = PuzzleCaptchaSolver("a", "b", "c")
        white = np.ones((50, 50, 3), dtype=np.uint8) * 255
        result = solver._crop_nonwhite(white)
        # All-white image should return original
        assert result.shape == white.shape

    def test_crop_nonwhite_reduces_size(self):
        from app.scraper.captcha_solver import PuzzleCaptchaSolver
        solver = PuzzleCaptchaSolver("a", "b", "c")
        img = np.ones((100, 100, 3), dtype=np.uint8) * 255
        # Add a small dark patch in the middle
        img[40:60, 40:60] = 0
        result = solver._crop_nonwhite(img)
        assert result.shape[0] <= 100
        assert result.shape[1] <= 100

    def test_dark_column_strategy(self, captcha_files):
        bg, gap, out = captcha_files
        from app.scraper.captcha_solver import PuzzleCaptchaSolver
        solver = PuzzleCaptchaSolver(gap, bg, out)
        bg_img = cv2.imread(bg)
        x, conf = solver._strategy_dark_column(bg_img, 50)
        assert x is not None
        assert conf > 0
        assert abs(x - 120) <= 30, f"Dark column expected ~120, got {x}"

    def test_different_gap_positions(self):
        """Solver should track gap at multiple x positions."""
        from app.scraper.captcha_solver import PuzzleCaptchaSolver
        for gap_x in (60, 120, 180):
            bg_img  = _make_bg(gap_x=gap_x, gap_w=50)
            gap_img = _make_gap_piece()
            bg_path  = _write_img(bg_img)
            gap_path = _write_img(gap_img)
            out_path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
            try:
                solver = PuzzleCaptchaSolver(gap_path, bg_path, out_path)
                result = solver.discern()
                assert result is not None, f"No result for gap_x={gap_x}"
                assert abs(result - gap_x) <= 35, f"gap_x={gap_x}, got {result}"
            finally:
                for p in (bg_path, gap_path, out_path):
                    try: os.unlink(p)
                    except OSError: pass


def _make_arcaptcha(hole=(190, 80, 30, 30), decoy=(127, 37, 19, 19), size=(260, 161)):
    """A background the way ARCaptcha actually draws one.

    The old fixtures are a dark band down the full height of a flat grey
    picture — which is nothing like the real thing, and why a solver that
    could not read a real one passed them. ARCaptcha paints bright colourful
    artwork, cuts one solid dark square out of it, and draws small dark puzzle
    icons into the artwork that look exactly like the cut-out.
    """
    w, h = size
    bg = np.full((h, w, 3), (174, 198, 99), dtype=np.uint8)      # the green
    for cy in (20, 70, 130):                                      # swirls
        cv2.circle(bg, (40, cy), 22, (150, 90, 60), 7)
        cv2.circle(bg, (215, cy), 26, (150, 90, 60), 7)
    cv2.ellipse(bg, (95, 60), (40, 22), 20, 0, 360, (245, 255, 245), -1)   # a leaf
    for x, y, bw, bh in (hole, decoy):
        cv2.rectangle(bg, (x, y), (x + bw, y + bh), (40, 50, 20), -1)
    return bg


class TestTheHoleIsAShapeNotADarkColumn:
    """Why the solver slid to 26px when the hole was at 191.

    Averaging darkness down a whole column drowns a cut-out that covers a
    quarter of the picture's height, and the search window fenced off the
    right third of the background besides — so a hole on the right could not
    be found at all, whatever the artwork looked like.
    """

    def _solve(self, bg_img, piece_box=None, gap_img=None):
        from app.scraper.captcha_solver import PuzzleCaptchaSolver
        bg_path = _write_img(bg_img)
        gap_path = _write_img(gap_img if gap_img is not None else _make_gap_piece(50, 44))
        out_path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        try:
            s = PuzzleCaptchaSolver(gap_path, bg_path, out_path, piece_box=piece_box)
            return s, s.discern()
        finally:
            for p in (bg_path, gap_path, out_path):
                try: os.unlink(p)
                except OSError: pass

    def test_a_hole_on_the_right_is_found(self):
        """The old window arithmetic could never return past 133 here."""
        _, x = self._solve(_make_arcaptcha(), piece_box=(0, 70, 50, 44))
        assert x is not None and abs(x - 190) <= 4, f"expected the hole at ~190, got {x}"

    def test_the_slide_leaves_out_the_inset_the_piece_is_drawn_with(self):
        """The piece sits inside its element, so its element's left edge is
        not where the piece starts. The hole's own y says by how much."""
        s, _ = self._solve(_make_arcaptcha(), piece_box=(0, 70, 50, 44))
        assert abs(s.slide_distance() - 180) <= 4, \
            f"element at x=0 drawn 10px in, hole at 190 — expected ~180, got {s.slide_distance()}"

    def test_a_puzzle_icon_in_the_artwork_is_not_the_hole(self):
        """Even when the decoy is the bigger of the two, it is on the wrong
        row — the piece only ever moves sideways."""
        s, x = self._solve(_make_arcaptcha(hole=(190, 80, 26, 26), decoy=(120, 20, 40, 40)),
                           piece_box=(0, 70, 50, 44))
        assert x is not None and abs(x - 190) <= 4, f"the decoy won: got {x}"

    def test_without_the_pieces_row_the_solid_dark_square_still_wins(self):
        _, x = self._solve(_make_arcaptcha())
        assert x is not None and abs(x - 190) <= 4, f"expected ~190, got {x}"

    def test_the_column_fallback_can_reach_the_right_of_the_picture(self):
        from app.scraper.captcha_solver import PuzzleCaptchaSolver
        bg = np.full((161, 260, 3), 200, dtype=np.uint8)
        bg[70:114, 195:235] = 60                      # the only dark thing
        s = PuzzleCaptchaSolver("a", "b", "c", piece_box=(0, 70, 50, 44))
        x, conf = s._strategy_dark_column(bg, 40)
        assert x is not None and abs(x - 195) <= 10, \
            f"the tail margin used to fence off everything past 133; got {x}"
