"""Field search, against the transcript a real reader actually produced."""
import unittest

import field_search as fs

# Verbatim from a DeepSeek-OCR run on a scanned, annotated utility bill --
# markdown, HTML tables, escaped dollar signs, handwriting and all.
BILL = """<!-- ===== page 1 ===== -->

**Daniel K. Okafor**

1450 Birch Street

**Service Address:**

1450 Birch Street

**ext.to 10/09**

**TOTAL AMOUNT DUE:** &#36;184.61

**Meter Reading Information**

<table><tr><td>Meter No.</td><td>Read Date</td><td>Previous Read</td><td>Current Read</td><td>Multiplier</td><td>kWh Used</td></tr><tr><td>ML-0093812</td><td>09/05/2026</td><td>70,118</td><td>71,322</td><td>1</td><td>1,204</td></tr></table>

![](images/0.jpg)

**Metro Light & Power** Account No: **ML-4471-2093** Amount Enclosed: &#36;

Due Date: **09/29/2026**

PAID 9/20 -chk #1042 (&#36;198.47 only!)
"""


class Normalising(unittest.TestCase):
    def test_entities_and_markup_are_resolved(self):
        out = fs.normalise(BILL)
        self.assertIn("$184.61", out)
        self.assertNotIn("&#36;", out)
        self.assertNotIn("<td>", out)
        self.assertNotIn("**", out)

    def test_page_markers_and_images_are_dropped(self):
        out = fs.normalise(BILL)
        self.assertNotIn("=====", out)
        self.assertNotIn("images/0.jpg", out)


class Finding(unittest.TestCase):
    def test_a_label_printed_differently_is_still_found(self):
        """The bill says "Account No:", the field is called "Account number"."""
        got = fs.find_field(BILL, "Account number")
        self.assertEqual(got[0], "ML-4471-2093")
        self.assertEqual(got[2], "account no")

    def test_value_after_a_colon(self):
        self.assertEqual(fs.find_field(BILL, "Due date")[0], "09/29/2026")

    def test_value_under_a_column_heading(self):
        """"Meter No." heads a column; the value is the cell beneath it."""
        value, evidence, _ = fs.find_field(BILL, "Meter reading")
        self.assertEqual(value, "ML-0093812")
        self.assertIn("Meter No.", evidence)

    def test_longest_label_wins(self):
        """"Total amount due" must beat the bare synonym "total"."""
        value, _, label = fs.find_field(BILL, "Total charges")
        self.assertEqual(value, "$184.61")
        self.assertEqual(label, "total amount due")

    def test_a_field_that_is_not_printed_is_reported_missing(self):
        value, evidence, _ = fs.find_field(BILL, "Cap rate")
        self.assertEqual(value, "")
        self.assertEqual(evidence, "")

    def test_evidence_is_the_line_the_value_came_from(self):
        _, evidence, _ = fs.find_field(BILL, "Due date")
        self.assertIn("09/29/2026", evidence)

    def test_caller_supplied_synonyms_are_used(self):
        """A field nobody has heard of, told which label to look for."""
        value, _, label = fs.find_field(BILL, "Site address", extra=["service address"])
        self.assertEqual(value, "1450 Birch Street")
        self.assertEqual(label, "service address")

    def test_prose_after_a_label_is_not_mistaken_for_a_value(self):
        """"Meter Reading Information" is a heading, not "Meter reading: Information"."""
        self.assertNotEqual(fs.find_field(BILL, "Meter reading")[0], "Information")


class Reporting(unittest.TestCase):
    def test_every_requested_field_comes_back_found_or_not(self):
        rows = fs.find_fields(BILL, ["Account number", "Due date", "Cap rate"])
        self.assertEqual([r["field"] for r in rows],
                         ["Account number", "Due date", "Cap rate"])
        self.assertEqual([r["found"] for r in rows], [True, True, False])

    def test_nothing_is_invented_for_a_missing_field(self):
        row = fs.find_fields(BILL, ["Net operating income"])[0]
        self.assertEqual(row["value"], "")
        self.assertFalse(row["found"])




# Layouts where the value has drifted away from its label. All three turn up
# on real scans, and the first two are what "label and value are offset" means.
SHIFTED_DOWN = """ACCOUNT SUMMARY
Previous Balance
Payment Received
Balance Forward
Total Due
141.35
-141.35
0.00
184.61
"""

SHIFTED_RIGHT = """Account No.\t\tML-4471-2093
Bill Date\t\t09/08/2026
Due Date\t\t09/28/2026
"""

# A charges table that has slipped one row: every label sits beside its
# neighbour's amount. Arithmetic proves the real pairing --
# 12.50+52.10+97.64+7.34+2.15 = 171.73, the printed Subtotal.
SLIPPED_TABLE = """Customer Charge\tQuantity\tRate\tAmount
Energy Charge Tier 1\t1 month\t$12.50/mo\t12.50
Subtotal\t\t\t2.15
Due Date\t\t\t171.73
"""


class Drifted(unittest.TestCase):
    def test_a_run_of_labels_then_a_run_of_values(self):
        """Labels printed first, values beneath: pair past the other labels."""
        value, evidence, _ = fs.find_field(SHIFTED_DOWN, "Starting balance")
        self.assertEqual(value, "141.35")
        self.assertIn("other label", evidence)

    def test_the_next_label_is_never_taken_as_the_value(self):
        for field in ("Starting balance", "Ending balance"):
            value, _, _ = fs.find_field(SHIFTED_DOWN, field)
            self.assertNotIn("Balance", value)
            self.assertNotIn("Payment", value)

    def test_value_several_columns_right(self):
        self.assertEqual(fs.find_field(SHIFTED_RIGHT, "Account number")[0], "ML-4471-2093")
        self.assertEqual(fs.find_field(SHIFTED_RIGHT, "Due date")[0], "09/28/2026")

    def test_shape_beats_proximity(self):
        """A date field skips the amount beside it for the date further along."""
        value, _, _ = fs.find_field("Due Date	171.73	09/28/2026", "Due date")
        self.assertEqual(value, "09/28/2026")

    def test_only_a_wrong_shaped_value_nearby_is_returned_but_flagged(self):
        """The slipped table has no date at all: report it, and mark it."""
        row = fs.find_fields(SLIPPED_TABLE, ["Due date"])[0]
        self.assertEqual(row["value"], "171.73")
        self.assertFalse(row["shape_ok"])

    def test_a_wrong_shaped_value_is_flagged_not_hidden(self):
        """When only a bad-shaped value exists, say so rather than drop it."""
        row = fs.find_fields("Cap rate\tnot applicable", ["Cap rate"])[0]
        self.assertTrue(row["found"])
        self.assertFalse(row["shape_ok"])

    def test_shapes_are_inferred_for_unknown_fields(self):
        self.assertEqual(fs.shape_of("Completion date"), "date")
        self.assertEqual(fs.shape_of("Insurance amount"), "money")

    def test_evidence_says_where_the_value_was_found(self):
        _, evidence, _ = fs.find_field(SHIFTED_RIGHT, "Account number")
        self.assertRegex(evidence, r"\[(beside|same row|under|below|after)")



class Unlabelled(unittest.TestCase):
    """Some values carry no label at all -- the issuer is the letterhead."""

    STUB = ("Daniel K. Okafor\n1450 Birch Street\n"
            "PLEASE DETACH AND RETURN THIS PORTION\n"
            "Metro Light & Power\nPO Box 1800, Columbus, OH 43216-1800\n")

    def test_the_issuer_is_found_without_a_label(self):
        value, _, how = fs.find_unlabelled(self.STUB, "Provider name")
        self.assertEqual(value, "Metro Light & Power")
        self.assertIn("no label", how)

    def test_the_customer_is_not_mistaken_for_the_issuer(self):
        """The first line of the page is the customer; position alone is a trap."""
        self.assertFalse(fs.looks_like_org("Daniel K. Okafor"))
        self.assertNotEqual(fs.find_unlabelled(self.STUB, "Provider name")[0],
                            "Daniel K. Okafor")

    def test_a_field_label_is_never_returned_as_a_name(self):
        for label in ("Service Period", "Service Address:", "Account Number"):
            self.assertFalse(fs.looks_like_org(label), label)

    def test_only_fields_that_have_no_label_are_guessed(self):
        self.assertEqual(fs.find_unlabelled(self.STUB, "Due date"), ("", "", ""))

    def test_a_guess_is_reported_as_a_guess(self):
        row = fs.find_fields(self.STUB, ["Provider name"])[0]
        self.assertTrue(row["found"])
        self.assertTrue(row["guessed"])
        self.assertEqual(row["matched_label"], "")

    def test_a_labelled_value_is_not_marked_as_guessed(self):
        row = fs.find_fields("Provider: Northlake Energy", ["Provider name"])[0]
        self.assertTrue(row["found"])
        self.assertFalse(row["guessed"])



class PropertyIsTheServiceAddress(unittest.TestCase):
    """A bill prints "Service Address"; the property is that same place."""

    # chr(92) is a backslash: the fixture holds the literal characters
    # "\" and "n", which is how the reader writes a break inside a cell.
    BILL = ("Service Address:" + chr(92) + "n1450 Birch Street"
            + chr(92) + "n" + "Columbus, OH 43215\tBill Date\t09/08/2026\n")

    def test_property_name_matches_the_service_address_label(self):
        value, _, label = fs.find_field(self.BILL, "Property name")
        self.assertIn("1450 Birch Street", value)
        self.assertEqual(label, "service address")

    def test_both_names_give_the_same_answer(self):
        self.assertEqual(fs.find_field(self.BILL, "Property name")[0],
                         fs.find_field(self.BILL, "Service address")[0])

    def test_a_line_break_written_as_backslash_n_is_flattened(self):
        """PaddleOCR writes a break inside a cell as a backslash then an n."""
        value, _, _ = fs.find_field(self.BILL, "Service address")
        self.assertNotIn("\\", value)
        self.assertEqual(value, "1450 Birch Street Columbus, OH 43215")

class ColumnHeadings(unittest.TestCase):
    """A heading row must not be read sideways for a value.

    On a real bank statement, asking for "credits" matched the heading row
    "Date | Description | Debits | Credits | Balance" and returned the next
    heading along, "Balance", graded as a good answer.
    """

    STATEMENT = (
        "STATEMENT OF ACCOUNT\n"
        "Account Number  BK-7741209\n"
        "Statement Date  08/31/2026\n"
        "Date\tDescription\tDebits\tCredits\tBalance\n"
        "08/02/2026\tCard payment\t45.10\t\t1,204.90\n"
        "08/09/2026\tSalary\t\t2,300.00\t3,504.90\n"
        "Total Debits  1,245.10\n"
        "Total Credits  2,300.00\n"
    )

    def test_a_heading_is_never_the_value_beside_another_heading(self):
        for field in ("credits", "debits"):
            value = fs.find_field(self.STATEMENT, field)[0]
            self.assertNotIn(value, ("Balance", "Credits", "Debits",
                                     "Description", "Date"),
                             f"{field} picked up a column heading")

    def test_bare_credits_and_debits_are_money(self):
        self.assertEqual(fs.shape_of("credits"), "money")
        self.assertEqual(fs.shape_of("debits"), "money")
        self.assertEqual(fs.shape_of("Total charges"), "money")

    def test_the_totals_are_found_either_way_you_name_them(self):
        for field in ("credits", "Total credits"):
            self.assertEqual(fs.find_field(self.STATEMENT, field)[0], "2,300.00")
        for field in ("debits", "Total debits"):
            self.assertEqual(fs.find_field(self.STATEMENT, field)[0], "1,245.10")

    def test_two_cells_still_read_sideways(self):
        """The guard is for heading rows, not for every wordy line.

        "Appraiser  Jane Okafor" has no digits either, and its value is the
        cell beside the label.
        """
        value, _, _ = fs.find_field("Appraiser  Jane Okafor\n", "Appraiser")
        self.assertEqual(value, "Jane Okafor")

    DETAIL = (
        "TRANSACTION DETAIL\n"
        "Date\tDescription\tCheck/Ref\tDebits\tCredits\tBalance\n"
        "08/01\tACH DEBIT MAPLE RENT\t\t1,450.00\t\t2,800.75\n"
        "08/31\tENDING BALANCE\t\t3,880.32\t5,569.77\t5,940.20\n"
    )

    def test_a_labelled_row_is_read_under_the_column_that_names_it(self):
        """The row reads 3,880.32 | 5,569.77 | 5,940.20 under Debits,
        Credits, Balance. The balance is the last of those, not the first
        number after the label."""
        value, evidence, _ = fs.find_field(self.DETAIL, "Ending balance")
        self.assertEqual(value, "5,940.20")
        self.assertIn("Balance column", evidence)

    def test_empty_cells_do_not_shift_the_columns(self):
        """Check/Ref is blank on that row, so counting from the left lands a
        column early. Counting from the right is what keeps it honest."""
        self.assertNotEqual(fs.find_field(self.DETAIL, "Ending balance")[0],
                            "3,880.32")

    SUMMARY = (
        "ACCOUNT SUMMARY\n"
        "Beginning Balance on 08/01/2026\t$4,250.75\n"
        "Deposits and Other Credits (6)\t+ $5,569.77\n"
        "Withdrawals and Other Debits (29)\t- $3,880.32\n"
        "Ending Balance on 08/31/2026\t$5,940.20\n"
    )

    def test_a_count_in_brackets_does_not_hide_the_amount(self):
        """"Deposits and Other Credits (6)" put a bracket where the
        separator was expected, so the amount beside it was never reached."""
        self.assertIn("5,569.77", fs.find_field(self.SUMMARY, "Total credits")[0])
        self.assertIn("3,880.32", fs.find_field(self.SUMMARY, "Total debits")[0])

    def test_the_separator_rule_still_rejects_a_heading(self):
        """Stepping over a bracket must not weaken the rule it sits in:
        "Meter Reading Information" is a heading, not a value."""
        text = "Meter Reading Information\nMeter No  MTR-5520918\n"
        self.assertEqual(fs.find_field(text, "Meter reading")[0], "MTR-5520918")

    def test_a_heading_row_is_still_read_downwards(self):
        """Dropping the sideways move must not lose the column below it."""
        text = ("Meter\tReading\tUsage\n"
                "MTR-5520918\t41230\t675\n")
        self.assertEqual(fs.find_field(text, "Usage")[0], "675")


if __name__ == "__main__":
    unittest.main(verbosity=2)
