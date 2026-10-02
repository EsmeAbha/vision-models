"""Filling a spreadsheet you already have with fields read off a document."""
import json
import os
import shutil
import tempfile
import unittest

import openpyxl

import fill_template as ft


def blank_form(path, labels, sheet="Summary"):
    book = openpyxl.Workbook()
    page = book.active
    page.title = sheet
    page["A1"] = "Summary"
    for i, label in enumerate(labels, start=4):
        page[f"A{i}"] = label
    book.save(path)
    return {label: f"B{i}" for i, label in enumerate(labels, start=4)}


class Numbers(unittest.TestCase):
    """A spreadsheet wants numbers, but only where the text really is one."""

    def test_money_and_plain_numbers_convert(self):
        self.assertEqual(ft.as_number("1,204.90"), 1204.90)
        self.assertEqual(ft.as_number("$5,940.20"), 5940.20)
        self.assertEqual(ft.as_number("675"), 675)
        self.assertEqual(ft.as_number("+ $5,569.77"), 5569.77)

    def test_a_minus_and_accounting_brackets_stay_negative(self):
        self.assertEqual(ft.as_number("- $3,880.32"), -3880.32)
        self.assertEqual(ft.as_number("(1,250.00)"), -1250.0)

    def test_anything_not_cleanly_a_number_is_refused(self):
        # A date in a number cell is worse than the text it came from.
        for text in ("08/31/2026", "EL-88342710", "", None,
                     "08/01/2026 - 08/31/2026", "see attached"):
            self.assertIsNone(ft.as_number(text), repr(text))


class Loading(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tpl_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.cells = blank_form(os.path.join(self.dir, "form.xlsx"),
                                ["Account number", "Ending balance"])

    def write(self, name, cfg):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def good(self, **over):
        cfg = {"name": "Form", "doc_type": "Bank statement",
               "workbook": "form.xlsx", "sheet": "Summary",
               "cells": self.cells}
        cfg.update(over)
        return cfg

    def test_a_good_mapping_loads(self):
        self.write("01-form.json", self.good())
        loaded = ft.load_templates(self.dir)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "01-form")
        self.assertTrue(os.path.isfile(loaded[0]["path"]))

    def test_a_broken_mapping_costs_only_itself(self):
        self.write("01-form.json", self.good())
        self.write("02-nocells.json", self.good(cells={}))
        self.write("03-missing.json", self.good(workbook="gone.xlsx"))
        self.write("04-badcell.json",
                   self.good(cells={"Account number": "not a cell"}))
        with open(os.path.join(self.dir, "05-broken.json"), "w") as fh:
            fh.write("{ not json")
        self.assertEqual([t["id"] for t in ft.load_templates(self.dir)],
                         ["01-form"])

    def test_a_workbook_outside_the_folder_is_refused(self):
        self.write("01-form.json", self.good(workbook="../secrets.xlsx"))
        self.assertEqual(ft.load_templates(self.dir), [])

    def test_filtering_by_document_type(self):
        self.write("01-form.json", self.good())
        self.assertEqual(len(ft.for_doc_type("Bank statement", self.dir)), 1)
        self.assertEqual(ft.for_doc_type("Lease document", self.dir), [])


class Filling(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tpl_")
        self.out = tempfile.mkdtemp(prefix="out_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        self.cells = blank_form(
            os.path.join(self.dir, "form.xlsx"),
            ["Account number", "Statement date", "Ending balance"])
        with open(os.path.join(self.dir, "01-form.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"name": "Form", "doc_type": "Bank statement",
                       "workbook": "form.xlsx", "sheet": "Summary",
                       "cells": self.cells,
                       "as_number": ["Ending balance"]}, fh)
        self.template = ft.load_templates(self.dir)[0]

    def read_back(self, path):
        book = openpyxl.load_workbook(path)
        page = book["Summary"]
        try:
            return {label: page[ref].value for label, ref in self.cells.items()}
        finally:
            book.close()

    def test_values_land_in_the_mapped_cells(self):
        path, written, skipped = ft.fill(
            self.template,
            {"Account number": "4021587390", "Statement date": "08/31/2026",
             "Ending balance": "$5,940.20"},
            out_dir=self.out)
        got = self.read_back(path)
        self.assertEqual(got["Account number"], "4021587390")
        self.assertEqual(got["Statement date"], "08/31/2026")
        # Declared as_number, so it lands as a number a formula can use.
        self.assertEqual(got["Ending balance"], 5940.20)
        self.assertEqual(len(written), 3)
        self.assertEqual(skipped, [])

    def test_the_blank_form_is_never_written_to(self):
        with open(self.template["path"], "rb") as fh:
            before = fh.read()
        ft.fill(self.template, {"Account number": "X-1"}, out_dir=self.out)
        with open(self.template["path"], "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_a_field_that_was_not_found_leaves_its_cell_empty(self):
        path, written, skipped = ft.fill(
            self.template, {"Account number": "4021587390"}, out_dir=self.out)
        got = self.read_back(path)
        self.assertIsNone(got["Statement date"])
        self.assertIsNone(got["Ending balance"])
        self.assertEqual([s["field"] for s in written], ["Account number"])
        self.assertEqual(sorted(s["field"] for s in skipped),
                         ["Ending balance", "Statement date"])
        self.assertTrue(all(s["why"] == "not found" for s in skipped))

    def test_a_number_cell_refuses_text_rather_than_mangling_it(self):
        path, written, skipped = ft.fill(
            self.template, {"Ending balance": "see attached"}, out_dir=self.out)
        self.assertIsNone(self.read_back(path)["Ending balance"])
        self.assertEqual(written, [])
        why = {s["field"]: s["why"] for s in skipped}
        self.assertIn("not cleanly a number", why["Ending balance"])
        # The other two are a different kind of blank, and say so.
        self.assertEqual(why["Account number"], "not found")

    def test_each_fill_is_its_own_file(self):
        a, _, _ = ft.fill(self.template, {"Account number": "A"}, out_dir=self.out)
        b, _, _ = ft.fill(self.template, {"Account number": "B"}, out_dir=self.out,
                          stem="other")
        self.assertNotEqual(a, b)
        self.assertEqual(self.read_back(a)["Account number"], "A")
        self.assertEqual(self.read_back(b)["Account number"], "B")

    def test_a_named_sheet_that_is_missing_is_reported(self):
        self.template["sheet"] = "Nope"
        with self.assertRaises(RuntimeError) as caught:
            ft.fill(self.template, {"Account number": "A"}, out_dir=self.out)
        self.assertIn("no sheet", str(caught.exception))
        # The failed attempt leaves nothing behind.
        self.assertEqual(os.listdir(self.out), [])


class ShippedTemplates(unittest.TestCase):
    """The examples in fill_templates/ have to actually work."""

    def test_they_load_and_name_real_document_types(self):
        import app as vision
        types = {d["name"] for d in vision.load_doc_types()}
        shipped = ft.load_templates()
        self.assertTrue(shipped, "no templates in fill_templates/")
        for t in shipped:
            self.assertIn(t["doc_type"], types,
                          f"{t['id']} names an unknown document type")


if __name__ == "__main__":
    unittest.main(verbosity=2)
