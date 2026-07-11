"""
Requires: pydantic, httpx, python-dotenv (base_agent.py's import chain).
Run with: pytest backend/agents/tests/test_cognitive_agent.py -v

Syllable counts below were hand-traced against _count_syllables' logic
before being written here (vowel-group counting + trailing-e / -le
corrections), so these assert exact values rather than just "> 1".
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from backend.agents.cognitive_agent import _count_syllables, compute_flesch_kincaid_grade


class TestCountSyllables:
    def test_single_syllable_words(self):
        assert _count_syllables("the") == 1
        assert _count_syllables("like") == 1
        assert _count_syllables("cat") == 1

    def test_multi_syllable_words(self):
        assert _count_syllables("banana") == 3
        assert _count_syllables("apple") == 2

    def test_empty_string_returns_zero(self):
        assert _count_syllables("") == 0

    def test_strips_punctuation(self):
        assert _count_syllables("cat,") == _count_syllables("cat")


class TestFleschKincaidGrade:
    def test_returns_none_for_too_short_text(self):
        assert compute_flesch_kincaid_grade("Hi there.") is None

    def test_simple_text_scores_lower_than_complex_text(self):
        simple = (
            "The cat sat on the mat. It was a sunny day. The dog ran fast. "
            "We had fun in the park. The sun was warm."
        )
        complex_text = (
            "The utilization of asymmetric cryptographic methodologies necessitates "
            "the implementation of certificate authority hierarchies. Endpoint "
            "communications require authentication through hierarchical validation "
            "procedures established by internationally recognized standards bodies."
        )
        simple_grade = compute_flesch_kincaid_grade(simple)
        complex_grade = compute_flesch_kincaid_grade(complex_text)
        assert simple_grade is not None and complex_grade is not None
        assert simple_grade < complex_grade

    def test_strips_html_tags_before_scoring(self):
        html = "<p>The cat sat on the mat.</p> <p>It was a sunny day. We had fun.</p>"
        plain = "The cat sat on the mat. It was a sunny day. We had fun."
        assert compute_flesch_kincaid_grade(html) == compute_flesch_kincaid_grade(plain)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
