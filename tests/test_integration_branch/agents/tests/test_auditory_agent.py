"""
Requires: pydantic, httpx, python-dotenv.
Run with: pytest backend/agents/tests/test_auditory_agent.py -v
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from backend.agents.auditory_agent import detect_auto_caption


class TestDetectAutoCaption:
    @pytest.mark.parametrize("src", [
        "captions/auto_generated_en.vtt",
        "captions/machine_generated.vtt",
        "captions/yt_auto_en.vtt",
        "captions/asr_output.vtt",
        "AUTO-CAPTION-en.vtt",  # case-insensitive
    ])
    def test_flags_auto_generated_filenames(self, src):
        assert detect_auto_caption(src) is True

    @pytest.mark.parametrize("src", [
        "captions/en.vtt",
        "captions/english-professional.vtt",
        "subtitles/movie_en_US.srt",
    ])
    def test_does_not_flag_clean_filenames(self, src):
        assert detect_auto_caption(src) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
