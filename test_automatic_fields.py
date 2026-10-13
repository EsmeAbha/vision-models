import unittest
from automatic_fields import plan_fields

CATALOGUE = [
    {'id':'utility-bill','name':'Utility bill','fields':['provider_name','account_number','total_electricity_bill']},
    {'id':'bank-statement','name':'Bank statement','fields':['account_number','beginning_balance','ending_balance']},
    {'id':'rent-roll','name':'Rent roll','fields':[]},
]


class AutomaticFieldsTests(unittest.TestCase):
    def test_all_utility_data_keeps_full_schema(self):
        plan = plan_fields('Extract all data from these utility bills', 'OCR text', 'doc.pdf', CATALOGUE)
        self.assertEqual(plan['fields'], CATALOGUE[0]['fields'])

    def test_mixed_bank_and_utility_preserves_both(self):
        plan = plan_fields('Extract everything from utility and bank statement files', '', '', CATALOGUE)
        self.assertEqual(plan['types'], ['utility-bill','bank-statement'])
        self.assertIn('ending_balance',plan['fields'])
        self.assertIn('total_electricity_bill',plan['fields'])
        self.assertEqual(plan['fields'].count('account_number'),1)

    def test_blank_prompt_detects_from_document(self):
        plan = plan_fields('', 'BANK STATEMENT\nOpening balance', 'scan.pdf', CATALOGUE)
        self.assertEqual(plan['fields'], CATALOGUE[1]['fields'])

    def test_ai_adds_labels_never_values(self):
        plan = plan_fields('Find the meter serial number', 'utility', '', CATALOGUE,
            suggest=lambda _: '{"types":["utility-bill"],"extra_fields":["meter serial number"]}')
        self.assertIn('meter serial number',plan['fields'])
        self.assertIn('provider_name',plan['fields'])

    def test_invalid_ai_output_falls_back_without_losing_extraction(self):
        plan = plan_fields('Extract data', 'Unrecognized', '', CATALOGUE,
            suggest=lambda _: '{"types":["made-up"],"extra_fields":[]}')
        self.assertTrue(plan['fallback'])
        self.assertIn('ending_balance',plan['fields'])

    def test_table_based_document_does_not_invent_fields(self):
        plan = plan_fields('Extract all rent roll data', '', '', CATALOGUE)
        self.assertEqual(plan['fields'],[])
        self.assertFalse(plan['fallback'])


if __name__ == '__main__': unittest.main()
