# Receiver report rent roll

A property management rent roll printed by suite, with vacant and occupied sections, additional-space lines folded into a lease, and a future rent increase series carrying a charge code per step.

## Recognising it

Matched on the strings the report prints about itself -- its title, report id and column headings -- hashed, never stored as text. The file name is not used: the sender chooses it and changes it.

## Reading it

- 13 columns, pinned once and carried across every page, because the headings are printed only on the first.
- 1 text column(s), found by left edge: numbers align right, words align left.
- Sections: VacantSuites, OccupiedSuites
- Repeating series ['Bump Date', 'Bump Monthly Amount', 'Bump PSF'] laid across the row, one group per step.
- Rows whose label contains ':' are the report summarising itself, not data. They are read as the printed totals to check against, and never added to them.

## Checking it

Extraction is accepted only when the figures read off the page reproduce the totals the report prints about itself. The reconciliation is the evidence; nothing is trusted because it looked right.

## Known confusions

Raised as a coloured cell and a written reason. Never silently corrected.

- **name_overflows_into_date** -- A long occupant name runs into the rent start column and interleaves with the date, so neither can be read with confidence. The name and the date must both be checked against the page.

## Notes

Learned from a four-page receiver report. The report states its own occupied, vacant and grand totals, and all six reconciled to the cent on the document it was learned from. Five rows raised the name-overflow flag. The occupied unit count read 24 against a printed 22, which is not yet explained -- three rows carry zero area (a garage income line and two structure lines) and may not be counted as units by the report.

---

Learned 2026-09-22 09:29; updated 2026-09-22 09:29.
