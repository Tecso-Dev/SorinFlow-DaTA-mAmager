from loguru import logger

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _cv2_import_error = None
except Exception as e:
    cv2 = None   # type: ignore
    np = None    # type: ignore
    _cv2_import_error = e

# Confidence threshold — below this every strategy is considered unreliable
_CONF_THRESHOLD = 0.30


class PuzzleCaptchaSolver:
    def __init__(self, gap_image_path: str, bg_image_path: str, output_image_path: str):
        self.gap_image_path = gap_image_path
        self.bg_image_path = bg_image_path
        self.output_image_path = output_image_path

    # ── Image helpers ────────────────────────────────────────────────────────

    def _crop_nonwhite(self, img):
        """Crop to bounding box of non-white pixels (fast numpy path)."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mask = gray < 250
        coords = np.argwhere(mask)
        if coords.size == 0:
            return img
        y0, x0 = coords.min(axis=0)
        y1, x1 = coords.max(axis=0) + 1
        cropped = img[y0:y1, x0:x1]
        return img if cropped.size == 0 else cropped

    def _to_gray(self, img):
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    def _edges(self, img):
        """Canny edge map as 3-channel image."""
        gray = self._to_gray(img)
        edges = cv2.Canny(gray, 50, 150)
        return cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)

    def _clahe(self, img):
        """CLAHE contrast enhancement — makes gap shadow more visible."""
        gray = self._to_gray(img)
        eq = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
        return cv2.cvtColor(eq, cv2.COLOR_GRAY2BGR)

    # ── Detection strategies ─────────────────────────────────────────────────

    def _template_match(self, bg, template, method=cv2.TM_CCOEFF_NORMED):
        """Run matchTemplate; return (x_pos, confidence)."""
        result = cv2.matchTemplate(bg, template, method)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        return max_loc[0], float(max_val)

    def _strategy_edge(self, bg, gap_cropped):
        """Strategy 1 — edge maps (original approach, improved threshold)."""
        bg_e  = self._edges(bg)
        gap_e = self._edges(gap_cropped)
        return self._template_match(bg_e, gap_e)

    def _strategy_clahe(self, bg, gap_cropped):
        """Strategy 2 — CLAHE-enhanced images; captures shadow contrast better."""
        bg_c  = self._clahe(bg)
        gap_c = self._clahe(gap_cropped)
        return self._template_match(bg_c, gap_c)

    def _strategy_raw(self, bg, gap_cropped):
        """Strategy 3 — raw pixel matching (no preprocessing)."""
        return self._template_match(bg, gap_cropped)

    def _strategy_dark_column(self, bg, gap_w):
        """Strategy 4 — scan background columns for the darkest gap-shadow band.

        ARCaptcha leaves a dark shadow where the piece was removed.  We slide
        a window of width `gap_w` and find the column whose mean darkness is
        lowest.  Entirely independent of the gap template image.
        """
        gray = self._to_gray(bg).astype(np.float32)
        bg_h, bg_w = gray.shape

        if gap_w >= bg_w:
            return None, 0.0

        # Build column-sum integral image for O(1) window sums
        col_means = np.array([
            gray[:, x:x + gap_w].mean()
            for x in range(bg_w - gap_w)
        ])

        # Ignore first 10% and last 10% of background (slider track borders)
        margin = int(bg_w * 0.10)
        col_means[:margin] = np.inf
        col_means[-(margin + gap_w):] = np.inf

        best_x = int(np.argmin(col_means))
        # Confidence: how much darker the best column is vs the overall mean
        valid = col_means[col_means != np.inf]
        if valid.size == 0:
            return None, 0.0
        overall_mean = float(valid.mean())
        best_val = float(gray[:, best_x:best_x + gap_w].mean())
        contrast = (overall_mean - best_val) / (overall_mean + 1e-6)
        # Map to ~[0, 1] — a contrast of 0.08+ is typically a real gap
        confidence = min(contrast / 0.12, 1.0)
        return best_x, confidence

    # ── Multi-scale helper ───────────────────────────────────────────────────

    def _multiscale_edge(self, bg, gap_cropped):
        """Strategy 5 — run edge matching at ×0.75, ×1.0, ×1.25 scales.

        Accounts for device-pixel-ratio mismatches between screenshots.
        """
        bg_e = self._edges(bg)
        best_x, best_conf = None, 0.0
        for scale in (0.75, 1.0, 1.25):
            gh, gw = gap_cropped.shape[:2]
            new_w, new_h = max(1, int(gw * scale)), max(1, int(gh * scale))
            resized = cv2.resize(gap_cropped, (new_w, new_h))
            if new_w >= bg.shape[1] or new_h >= bg.shape[0]:
                continue
            gap_e = self._edges(resized)
            try:
                x, conf = self._template_match(bg_e, gap_e)
            except cv2.error:
                continue
            if conf > best_conf:
                best_conf = conf
                best_x = x
        return best_x, best_conf

    # ── Main entry point ─────────────────────────────────────────────────────

    def discern(self):
        """Return x-offset (pixels) for the slider, or None on failure.

        Runs up to 5 strategies.  Picks the result with highest confidence
        that exceeds _CONF_THRESHOLD.  Falls back to dark-column scan if all
        template strategies fail.
        """
        if cv2 is None or np is None:
            logger.warning(f"OpenCV/numpy not available: {_cv2_import_error}")
            return None

        bg  = cv2.imread(self.bg_image_path)
        gap = cv2.imread(self.gap_image_path)
        if bg is None or gap is None:
            logger.warning("Could not read captcha images")
            return None

        bg_h, bg_w = bg.shape[:2]
        gap_cropped = self._crop_nonwhite(gap)
        gc_h, gc_w  = gap_cropped.shape[:2]
        logger.info(f"Captcha — bg: {bg_w}×{bg_h}  gap: {gc_w}×{gc_h}")

        if gc_w >= bg_w or gc_h >= bg_h:
            logger.warning("Gap image is same size or larger than background")
            return None

        # ── Primary: dark-column scan ────────────────────────────────────────
        # ARCaptcha background has a dark hole/shadow at the gap position.
        # Scanning for the darkest band is more reliable than template matching
        # the puzzle piece (which does NOT appear in the background as-is).
        dc_x, dc_conf = self._strategy_dark_column(bg, gc_w)
        logger.info(f"  [dark_column] x={dc_x}  conf={dc_conf:.3f}")

        if dc_x is not None and dc_conf >= _CONF_THRESHOLD:
            logger.info(f"Dark-column selected: x={dc_x}  conf={dc_conf:.3f}")
            self._save_debug(bg, dc_x, gc_w, bg_h)
            return dc_x

        # ── Secondary: template strategies (cross-check only) ────────────────
        gap_std = float(gap_cropped.astype(np.float32).std())
        candidates = []
        if gap_std >= 8.0:
            for name, fn in [
                ("edge",       lambda: self._strategy_edge(bg, gap_cropped)),
                ("clahe",      lambda: self._strategy_clahe(bg, gap_cropped)),
                ("raw",        lambda: self._strategy_raw(bg, gap_cropped)),
                ("multiscale", lambda: self._multiscale_edge(bg, gap_cropped)),
            ]:
                try:
                    x, conf = fn()
                    candidates.append((name, x, conf))
                    logger.info(f"  [{name}] x={x}  conf={conf:.3f}")
                except Exception as e:
                    logger.debug(f"  [{name}] failed: {e}")
        else:
            logger.warning(f"Gap piece appears uniform (std={gap_std:.1f}) — skipping template strategies")

        best = max(candidates, key=lambda c: c[2], default=None)
        if best and best[2] >= _CONF_THRESHOLD:
            name, x_pos, conf = best
            # Reject template result if it's suspiciously close to left edge (likely false positive)
            if x_pos <= bg_w * 0.05:
                logger.warning(f"Template '{name}' x={x_pos} is at left edge — discarding as false positive")
            else:
                logger.info(f"Template strategy '{name}' selected: x={x_pos}  conf={conf:.3f}")
                self._save_debug(bg, x_pos, gc_w, gc_h)
                return x_pos

        # ── Last resort: dark-column regardless of confidence ─────────────────
        if dc_x is not None:
            logger.warning(f"Using dark-column with low confidence: x={dc_x}  conf={dc_conf:.3f}")
            self._save_debug(bg, dc_x, gc_w, bg_h)
            return dc_x

        return None

    def _save_debug(self, bg, x_pos, w, h):
        try:
            debug = bg.copy()
            cv2.rectangle(debug, (x_pos, 0), (x_pos + w, h), (0, 0, 255), 2)
            cv2.imwrite(self.output_image_path, debug)
        except Exception:
            pass
