"""
backend/fix_engine/diff_engine.py

"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# 1. Token-level HTML diff
# ---------------------------------------------------------------------------

_TOKEN_PATTERN = re.compile(r"""(<[^>]+>)|([^<]+)""")  # group 1: full tag, group 2: text run


def _tokenize(html_str: str) -> list[str]:
    """Tokenizes into tags (each attribute as its own sub-token so
    attribute-level changes are isolated) and text runs."""
    tokens: list[str] = []
    for tag_match, text_match in _TOKEN_PATTERN.findall(html_str):
        if tag_match:
            tokens.extend(_tokenize_tag(tag_match))
        elif text_match.strip():
            tokens.append(text_match)
    return tokens


def _tokenize_tag(tag: str) -> list[str]:
    """Breaks a single tag like `<img src='a.jpg' alt=''>` into
    `['<img', "src='a.jpg'", "alt=''", '>']` so the differ can see exactly
    which attribute changed."""
    inner = tag.strip("<>")
    if inner.startswith("/"):
        return [tag]

    parts = inner.split()
    if not parts:
        return [tag]

    return [f"<{parts[0]}", *parts[1:], ">"]


@dataclass
class DiffSegment:
    op: str  # "equal" | "insert" | "delete" | "replace"
    original_tokens: list[str]
    fixed_tokens: list[str]


class HTMLDiffer:
    """Token-level HTML diff producing diff2html-compatible unified-diff
    text. diff2html on Anirudh's side accepts a standard unified diff
    string, so we render one -- but computed over HTML tokens rather than
    file lines, per Design Doc 4.3."""

    def diff(self, original_html: str, fixed_html: str) -> list[DiffSegment]:
        import difflib

        original_tokens = _tokenize(original_html)
        fixed_tokens = _tokenize(fixed_html)

        matcher = difflib.SequenceMatcher(a=original_tokens, b=fixed_tokens, autojunk=False)
        segments: list[DiffSegment] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            segments.append(
                DiffSegment(op=tag, original_tokens=original_tokens[i1:i2], fixed_tokens=fixed_tokens[j1:j2])
            )
        return segments

    def to_unified_diff(self, original_html: str, fixed_html: str, *, context_lines: int = 3) -> str:
        """Renders a standard unified-diff string (the format diff2html
        consumes) using difflib.unified_diff over the *token* sequences
        joined one-per-line, so token-level granularity survives into the
        line-oriented unified diff format."""
        import difflib

        diff_lines = difflib.unified_diff(
            _tokenize(original_html),
            _tokenize(fixed_html),
            fromfile="original.html",
            tofile="fixed.html",
            lineterm="",
            n=context_lines,
        )
        return "\n".join(diff_lines)


# ---------------------------------------------------------------------------
# 2. Sandboxed preview builder
# ---------------------------------------------------------------------------

_HIGHLIGHT_CSS = """
.wcag-fix-highlight {
  outline: 3px solid #2e7d32 !important;
  outline-offset: 2px;
  box-shadow: 0 0 0 4px rgba(46, 125, 50, 0.15);
}
"""


class PreviewBuilder:
    """Builds the `preview_srcdoc` string for the Fix Studio iframe
    (Design Doc 4.4). Fully self-contained -- no external fetches --
    because the sandboxed iframe's CSP won't allow them anyway."""

    def build_srcdoc(self, *, patched_element_html: str, page_computed_css: str, element_selector: str) -> str:
        """`page_computed_css` is the <style> block Anirudh's content
        script extracts from the page's computed stylesheets. We inject it
        verbatim, add our highlight rule, mark the patched element with a
        class so it visibly stands out, and wrap everything in a minimal,
        self-contained HTML document."""
        marked_html = self._mark_element(patched_element_html)

        return (
            "<!DOCTYPE html>"
            "<html><head>"
            "<meta charset='utf-8'>"
            f"<style>{page_computed_css}\n{_HIGHLIGHT_CSS}</style>"
            "</head><body>"
            f"{marked_html}"
            "<!-- NOTE: external resources (images, fonts, CDN scripts) will "
            "not load here due to sandboxed iframe CSP restrictions. This is "
            "a visual approximation, not a pixel-perfect replica -- disclose "
            "this in the UI rather than trying to bypass the sandbox. -->"
            "</body></html>"
        )

    @staticmethod
    def _mark_element(element_html: str) -> str:
        """Adds the highlight class onto the (already patched) element's
        opening tag without disturbing existing attributes."""
        if "class='" in element_html:
            return element_html.replace("class='", "class='wcag-fix-highlight ", 1)
        if 'class="' in element_html:
            return element_html.replace('class="', 'class="wcag-fix-highlight ', 1)
        return re.sub(r"^(<[a-zA-Z0-9]+)", r"\1 class='wcag-fix-highlight'", element_html, count=1)
