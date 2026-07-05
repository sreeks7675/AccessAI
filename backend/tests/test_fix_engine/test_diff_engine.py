"""
tests/test_fix_engine/test_diff_engine.py

"""

from backend.fix_engine.diff_engine import HTMLDiffer, PreviewBuilder


def test_diff_isolates_attribute_level_change():
    differ = HTMLDiffer()
    original = "<img src='hero.jpg' class='banner'>"
    fixed = "<img src='hero.jpg' class='banner' alt='Team photo'>"

    segments = differ.diff(original, fixed)

    # There should be an 'insert' or 'replace' segment introducing the new
    # alt='...' token, and the surrounding tokens (src, class) should show
    # up as 'equal' -- proving the diff is attribute-granular, not a
    # whole-line replacement.
    ops = {s.op for s in segments}
    assert "equal" in ops
    assert any(s.op in ("insert", "replace") for s in segments)

    inserted_tokens = [tok for s in segments if s.op in ("insert", "replace") for tok in s.fixed_tokens]
    assert any("alt=" in tok for tok in inserted_tokens)


def test_diff_no_whitespace_noise_for_identical_html():
    differ = HTMLDiffer()
    html_str = "<p style='color:#000'>Hello world</p>"

    segments = differ.diff(html_str, html_str)

    assert all(s.op == "equal" for s in segments)


def test_unified_diff_format_is_diff2html_compatible():
    differ = HTMLDiffer()
    original = "<input type='email' name='email'>"
    fixed = "<input type='email' name='email' aria-label='Email address'>"

    unified = differ.to_unified_diff(original, fixed)

    assert "--- original.html" in unified
    assert "+++ fixed.html" in unified
    assert any(line.startswith("+") and "aria-label" in line for line in unified.splitlines())


def test_preview_srcdoc_is_self_contained_and_highlights_element():
    builder = PreviewBuilder()
    srcdoc = builder.build_srcdoc(
        patched_element_html="<img src='hero.jpg' alt='Team photo'>",
        page_computed_css="body { font-family: sans-serif; }",
        element_selector="img.hero-banner",
    )

    assert "<!DOCTYPE html>" in srcdoc
    assert "wcag-fix-highlight" in srcdoc
    assert "font-family: sans-serif" in srcdoc
    # No external script tags should ever be injected -- CSP in the
    # sandboxed iframe would block them anyway (Design Doc 2.2.2 risk note).
    assert "<script src=" not in srcdoc


def test_preview_marks_element_without_existing_class():
    builder = PreviewBuilder()
    marked = builder._mark_element("<input type='email'>")
    assert "wcag-fix-highlight" in marked
    assert marked.startswith("<input class='wcag-fix-highlight'")
