"""A page whose text is already in the PDF is read from it, not OCR'd.

PDFs are drawn with reportlab, so the text layers are real: a digital page
with a ruled table, a page that is only an image (a scan), and a table whose
columns run together (better left to OCR's layout model).
"""
import io
import os
import tempfile
import unittest

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import native_text


def ruled_table(c, x, y, rows, widths, h=18):
    for r, row in enumerate(rows):
        cx = x
        for text, w in zip(row, widths):
            c.rect(cx, y - (r + 1) * h, w, h)
            c.drawString(cx + 4, y - (r + 1) * h + 5, text)
            cx += w


def make(path):
    c = canvas.Canvas(path, pagesize=letter)
    # 1: a digital bill with a ruled table
    c.drawString(72, 740, "Bright Valley Electric - ELECTRICITY BILL")
    c.drawString(72, 720, "TOTAL AMOUNT DUE: $102.28 DUE BY 09/26/2026")
    ruled_table(c, 72, 700, [["Account Number", "EL-88342710"], ["Bill Date", "09/05/2026"]], [160, 160])
    c.drawString(72, 600, "Previous Balance 118.62  Payment Received -118.62  Balance Forward 0.00")
    c.showPage()
    # 2: a scan -- the page is one picture
    img = Image.new("RGB", (1200, 1500), "white")
    buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    c.drawImage(ImageReader(buf), 0, 0, width=letter[0], height=letter[1])
    c.showPage()
    # 3: a "table" whose values ended up crammed into one ruled cell
    c.drawString(72, 740, "Value Conclusions for the subject property, as of the date of value.")
    ruled_table(c, 72, 720, [["Market Value As Is Leased Fee 3/18/2026 $16,700,000 $9,156,454", ""],
                             ["Insurable Replacement Cost", ""]], [360, 60])
    c.showPage()
    c.save()


class NativeTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.pdf = os.path.join(cls.dir.name, "doc.pdf")
        make(cls.pdf)
        cls.pages = native_text.read(cls.pdf)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_a_digital_page_is_read_from_its_text(self):
        self.assertIn(1, self.pages)
        text = self.pages[1]
        self.assertIn("TOTAL AMOUNT DUE: $102.28", text)
        self.assertIn("Previous Balance 118.62", text)

    def test_its_table_comes_out_as_html_like_the_ocr(self):
        text = self.pages[1]
        self.assertIn("<table", text)
        self.assertIn("<td>Account Number</td><td>EL-88342710</td>", text)
        # Reading order: the heading line comes before the table.
        self.assertLess(text.index("ELECTRICITY BILL"), text.index("<table"))

    def test_a_scanned_page_is_left_for_ocr(self):
        self.assertNotIn(2, self.pages)

    def test_a_table_with_merged_columns_is_left_for_ocr(self):
        self.assertNotIn(3, self.pages)

    def test_only_the_pages_asked_for(self):
        self.assertEqual(list(native_text.read(self.pdf, [1, 2])), [1])


if __name__ == "__main__":
    unittest.main()
