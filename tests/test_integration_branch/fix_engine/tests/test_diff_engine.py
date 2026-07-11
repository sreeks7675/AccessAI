from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import unittest
from backend.fix_engine.diff_engine import HTMLDiffer, PreviewBuilder, _tokenize, _tokenize_tag


class TestTokenizer(unittest.TestCase):
    def test_tokenize_tag_splits_attributes(self):
        self.assertEqual(
            _tokenize_tag("<img src='a.jpg' alt=''>"),
            ["<img", "src='a.jpg'", "alt=''", ">"],
        )

    def test_tokenize_tag_closing_tag_untouched(self):
        self.assertEqual(_tokenize_tag("</div>"), ["</div>"])

    def test_tokenize_mixes_tags_and_text(self):
        tokens = _tokenize("<p>Hello</p>")
        self.assertIn("<p", tokens)
        self.assertIn("Hello", tokens)
        self.assertIn("</p>", tokens)


class TestHTMLDiffer(unittest.TestCase):
    def setUp(self):
        self.differ = HTMLDiffer()

    def test_diff_detects_added_alt_attribute_as_replace_or_insert(self):
        original = "<img src='hero.jpg'>"
        fixed = "<img src='hero.jpg' alt='Team photo'>"
        segments = self.differ.diff(original, fixed)
        ops = {s.op for s in segments}
        # adding a brand-new attribute token should show up as a non-equal op
        self.assertTrue(ops - {"equal"}, f"expected a change op, got only {ops}")

    def test_diff_identical_html_is_all_equal(self):
        html = "<img src='hero.jpg' alt='Team photo'>"
        segments = self.differ.diff(html, html)
        ops = {s.op for s in segments}
        self.assertEqual(ops, {"equal"})

    def test_to_unified_diff_has_diff2html_compatible_headers(self):
        original = "<img src='hero.jpg'>"
        fixed = "<img src='hero.jpg' alt='desc'>"
        udiff = self.differ.to_unified_diff(original, fixed)
        self.assertIn("--- original.html", udiff)
        self.assertIn("+++ fixed.html", udiff)
        self.assertTrue(any(line.startswith("+") and "alt=" in line for line in udiff.splitlines()))


class TestPreviewBuilder(unittest.TestCase):
    def setUp(self):
        self.builder = PreviewBuilder()

    def test_build_srcdoc_wraps_in_full_html_document(self):
        srcdoc = self.builder.build_srcdoc(
            patched_element_html="<img src='a.jpg' alt='desc'>",
            page_computed_css=".banner{color:red}",
            element_selector="img.banner",
        )
        self.assertTrue(srcdoc.startswith("<!DOCTYPE html>"))
        self.assertIn(".banner{color:red}", srcdoc)
        self.assertIn(".wcag-fix-highlight", srcdoc)
        self.assertIn("sandboxed iframe CSP", srcdoc)

    def test_mark_element_single_quoted_class(self):
        out = self.builder._mark_element("<div class='card'>x</div>")
        self.assertIn("class='wcag-fix-highlight card'", out)

    def test_mark_element_double_quoted_class(self):
        out = self.builder._mark_element('<div class="card">x</div>')
        self.assertIn('class="wcag-fix-highlight card"', out)

    def test_mark_element_no_existing_class(self):
        out = self.builder._mark_element("<img src='a.jpg' alt='desc'>")
        self.assertIn("class='wcag-fix-highlight'", out)
        # original attributes must survive untouched
        self.assertIn("src='a.jpg'", out)
        self.assertIn("alt='desc'", out)


if __name__ == "__main__":
    unittest.main()
