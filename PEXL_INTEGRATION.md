# Pexl integration (port 7862)

Each result card has **Send to Pexl**. Pick the document type (it is
pre-selected from what FinAi read the document as) and press **Create Excel
file**. FinAi sends the extracted values to Pexl, Pexl builds its standard
workbook for that type, the same one its own Export button makes, and FinAi
downloads it and keeps it against the run. There's no template and no column mapping.

For a custom layout, **Advanced: use your own template instead** still uploads
a template, matches columns and fills it (see "Custom templates" below).

## Turning it on

FinAi needs one secret from Pexl. Set it in the environment of the process that
starts `vision_server.py`, never in the page:

```powershell
$env:PEXL_API_KEY = '<the key Pexl issued to FinAi>'
# Only if Pexl is not at the default https://pexlbackend.bulkscraper.cloud:
# $env:PEXL_API_URL = 'https://...'
.\.venv\Scripts\python.exe vision_server.py
```

Until `PEXL_API_KEY` is set the dialog says Pexl is not connected and sends
nothing. The key is never sent to the browser or written to a log.

Pexl's side must be deployed first: `curl -H "Authorization: Bearer <key>"
https://pexlbackend.bulkscraper.cloud/api/v1/doc-types` should return six types.
A 503 there means Pexl's key config or export runtime is missing, not FinAi.

## What is sent

`POST /api/v1/exports` with `doc_type`, `external_reference` (the FinAi run
id), one `files` entry for the run's document and one row per extracted value:
`page`, `field`, `value`, plus `confidence` ("high" when the value was found
beside its label, else "low"). Values that weren't found go as `null`. Fields
whose name contains "date" are converted to MM/DD/YYYY, which is what Pexl buckets
months by. A date that can't be parsed is sent as it was read. Utility bills
also send `options.date_field` (auto / billing_date / date), chosen in the
dialog.

FinAi document type → Pexl type: appraisal → `appraisal`, bank statement →
`bank_statement`, utility bill → `utility_bill`, lease document →
`lease_contract`, tax bill → `tax`, rent roll → `others` (each field becomes a
column). The mapping is the `pexl` key in `doc_types/*.json`. FinAi's field
names are Pexl's own keys. At startup the server checks them against Pexl's
`/api/v1/doc-types` and logs a warning for any that don't match. A
non-empty `unknown_fields` in Pexl's answer is logged with the run id and shown
in the dialog: those values only reach Pexl's "Plain Data" sheet.

## Errors and retries

| Pexl says | FinAi does |
| --- | --- |
| 401 bad key | fails, no retry |
| 404 | fails |
| 413 too large | fails; split the documents |
| 422 | fails, no retry; Pexl's `detail` is shown |
| 503 | retries with backoff, 3 tries in all |
| 500 | retries once |
| timeout / connection lost on the POST | fails, **never resent**: Pexl may have made the workbook, and another POST makes another |

The timeout is 150s (Pexl may take 120s to build a workbook). GETs (download,
doc-types) retry transient failures up to three times.

The handoff runs on a background thread. The dialog polls its local stage:
uploading → building workbook → downloading → completed / failed. Jobs,
Pexl's export id, unknown fields and the downloaded file are kept under
`outputs/pexl` (SQLite plus the .xlsx), listed per run at
`GET /api/pexl/runs/{run_id}/jobs`, and download at
`/api/pexl/jobs/{job_id}/outputs/0` under Pexl's file name. A handoff still
running when the server restarts is marked failed and not replayed.

## Custom templates

Under Advanced: upload an .xlsx template (kept per FinAi document type, so it
is offered again next time), **Match columns** proposes destination ← source
column matches (the local chat model, checked against the real sheet names,
falling back to exact name matches), or map them by hand. **Create from
template** uploads the Fields/Tables workbook as a source and fills up to ten
templates through `/api/v1/files` and `/api/v1/jobs`. Headers are row 1, output
starts at row 2. Mapped template cells must not be merged or hold formulas.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest test_pexl_exports test_pexl_integration -v
```

No GPU or live Pexl needed: Pexl is mocked at the HTTP layer. Covered: post then
download, unknown fields warning, 401/422 not retried, 503 retried then
given up, 500 retried once, a timed-out POST not resent, the date and
`null` rules, refusing before anything is sent, and custom templates.
`test_finai_fields_exist_in_pexl` checks every FinAi field against
`test_files/pexl/doc_types.json`, which is Pexl's real `/doc-types` output
from its export runtime. Regenerate it after Pexl changes its fields:

```powershell
node E:\odin\pexl\pexl\dist-server\export-workbook.mjs doc-types > test_files\pexl\doc_types.json
```

The live round trip (`LiveTests`) runs only when `PEXL_API_KEY` is set. It hasn't
been run yet: no key has been issued to this machine.
