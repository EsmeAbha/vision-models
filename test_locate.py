"""Clicking a figure in an answer shows it on its page: review.locate finds
where it is printed, and ranks the copy the answer meant first.

A two-page PDF is made with reportlab so the text layer is real; a scanned
page is stood in for by OCR layout blocks with no PDF at all.
"""
import os
import tempfile
import unittest

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import review


def make_pdf(path):
    c = canvas.Canvas(path, pagesize=letter)
    c.drawString(72, 720, "Market overview: regional cap rates range 6.75% to 9.00%.")
    c.drawString(72, 690, "Total Amount Due: $1,204.10")
    c.showPage()
    c.drawString(72, 720, "The subject property was built in 1985.")
    c.drawString(72, 690, "Concluded capitalization rate for the subject: 6.75 %")
    c.showPage()
    c.save()


class LocateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.pdf = os.path.join(self.dir.name, "doc.pdf")
        make_pdf(self.pdf)

    def test_a_figure_is_found_with_a_box_on_its_page(self):
        hits = review.locate(self.pdf, "1985")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["page"], 2)
        self.assertTrue(hits[0]["precise"])
        x0, y0, x1, y1 = hits[0]["box"]
        self.assertTrue(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, hits[0]["box"])
        self.assertLess(y1, 0.3, "the line is near the top of the page")

    def test_printed_forms_differ_from_the_answer(self):
        # The answer writes 1204.10 or 6.75%; the page prints $1,204.10 and 6.75 %.
        self.assertEqual([h["page"] for h in review.locate(self.pdf, "1204.10")], [1])
        self.assertIn(2, [h["page"] for h in review.locate(self.pdf, "6.75%")])

    def test_the_words_around_it_pick_the_right_copy(self):
        hits = review.locate(self.pdf, "6.75%",
                             near="The concluded capitalization rate for the subject is 6.75%.")
        self.assertEqual(hits[0]["page"], 2, "ranked the market range above the concluded rate")
        hits = review.locate(self.pdf, "6.75%", near="Regional market overview cap rates range")
        self.assertEqual(hits[0]["page"], 1)

    def test_not_printed_is_an_empty_list(self):
        self.assertEqual(review.locate(self.pdf, "12% higher"), [])

    def test_a_scanned_page_falls_back_to_the_ocr_region(self):
        layout = [{"page": 1, "blocks": [
            {"label": "text", "box": [0.1, 0.1, 0.9, 0.2], "text": "Invoice date 03/04/2026"},
            {"label": "text", "box": [0.1, 0.5, 0.9, 0.6], "text": "Balance due $88.00"}]}]
        hits = review.locate(None, "$88.00", layout)
        self.assertEqual(hits, [{"page": 1, "box": [0.1, 0.5, 0.9, 0.6], "precise": False}])


if __name__ == "__main__":
    unittest.main()
