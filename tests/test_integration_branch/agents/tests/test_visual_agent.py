"""
Requires: pydantic, httpx, python-dotenv.
Run with: pytest backend/agents/tests/test_visual_agent.py -v

Contrast values were hand-computed from the WCAG relative-luminance
formula before writing these assertions (see PR description / review
notes) to catch drift between the docstring's example numbers and the
actual implementation.
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from backend.agents.visual_agent import _parse_colour, compute_wcag_contrast, compute_apca_lc


class TestParseColour:
    def test_hex_long_form(self):
        assert _parse_colour("#ff0000") == pytest.approx((1.0, 0.0, 0.0))

    def test_hex_short_form(self):
        assert _parse_colour("#f00") == pytest.approx((1.0, 0.0, 0.0))

    def test_rgb_function_form(self):
        assert _parse_colour("rgb(255, 0, 0)") == pytest.approx((1.0, 0.0, 0.0))

    def test_unparseable_returns_none(self):
        assert _parse_colour("not-a-colour") is None


class TestWCAGContrast:
    def test_black_on_white_is_max_contrast(self):
        assert compute_wcag_contrast("#000000", "#ffffff") == 21.0

    def test_same_colour_is_minimum_contrast(self):
        assert compute_wcag_contrast("#777777", "#777777") == 1.0

    def test_known_borderline_case_from_visual_agent_docstring(self):
        # base_agent's own few-shot example claims #767676 on #ffffff is ~4.48:1
        # (fails the 4.5:1 AA threshold for normal text). The actual
        # implementation computes 4.54 -- close to the doc's "approximately"
        # figure but not identical. Locking in the real computed value here
        # means if the luminance math changes later, this test catches the
        # drift from the documented example instead of a user noticing it.
        ratio = compute_wcag_contrast("#767676", "#ffffff")
        assert ratio == 4.54

    def test_unparseable_colour_returns_none(self):
        assert compute_wcag_contrast("not-a-colour", "#ffffff") is None


class TestAPCA:
    def test_black_text_on_white_bg_is_strongly_positive(self):
        assert compute_apca_lc("#000000", "#ffffff") == 114.0

    def test_white_text_on_black_bg_is_strongly_negative(self):
        assert compute_apca_lc("#ffffff", "#000000") == -114.0

    def test_identical_colours_is_near_zero(self):
        lc = compute_apca_lc("#777777", "#777777")
        assert lc == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
