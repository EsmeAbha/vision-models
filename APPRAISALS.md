# Rating guide extraction

Upload a zip of appraisals; every PDF inside is searched for the manufactured-
housing rating guide — the Class A / B / C / Unratable grid — and only that
table is taken.

It is part of the chat OCR server: start that one and open
**http://127.0.0.1:7862/appraisals/**, or click **Appraisal rating guides**
in its sidebar.

```powershell
.\.venv\Scripts\python.exe vision_server.py
```

`appraisal_server.py` still runs on its own (on 7885) — that is how
`serve_appraisals.ps1` shares just this page on the network, with its
password, without exposing the rest of the chat server. The page uses
relative URLs, so the same files work both ways.

## Serving it to the rest of the network

```powershell
.\serve_appraisals.ps1 -Password "something-long"
```

Binds `0.0.0.0` and prints the LAN address. Open the firewall once, from an
**admin** PowerShell:

```powershell
New-NetFirewallRule -DisplayName "appraisal rating guide" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 7885 -Profile Private
```

`-Profile Private` is deliberate: this machine's Ethernet is on a private
profile, and the rule stays off if it is ever joined to a public network.

A password is **required** the moment the server listens anywhere but
loopback — `appraisal_server.py` refuses to start without
`APPRAISAL_PASSWORD` and says why. On loopback it stays optional, because
anyone who can reach 127.0.0.1 is already this user. The session is an
HttpOnly `SameSite=strict` cookie, good for twelve hours; wrong guesses get
three tries then a doubling lockout capped at five minutes.

This is **plain HTTP on a local network**. The password crosses the wifi in
clear, so do not reuse one that opens anything else, and do not put this on
the public internet in this form — it would need TLS in front of it, which a
tunnel supplies.

### What an exposed endpoint is protected against

Uploads are streamed to disk in 1 MB chunks rather than read into memory, and
capped at 3 GB. Before anything is unpacked the archive is inspected: more
than 20,000 members, more than 12 GB declared, or an expansion ratio past 200x
is refused outright — a zip is free to claim a megabyte holds a petabyte of
zeroes, and `zips.extract` checks paths but not sizes. Appraisals are
already-compressed PDFs and come nowhere near that ratio. Three archives may
be read at once; a fourth is turned away rather than queued.

Note that an upload rejected before its body has been read closes the
connection, which a browser reports as a generic network error rather than the
reason. The page says as much when that happens.

## What it looks for

The table is matched on its **shape, not its heading**. Firms re-type the
title — the sample this was built from reads "Manufacture Housing Communites
Rating Guide", two typos in six words — so a title is the first thing to drift
between files. What does not drift is four class columns across the top and a
known set of row labels down the side: Density, Age, Amenities,
Quality/Layout, Roads, Utilities, Parking, Homes (Quality), Homes (Age), Homes
(Mix), and the two Star Rating rows under "Comparison to Star Rating".

Every candidate table is scored against that shape. To be taken it must carry
at least 5 of the 10 core rows **and** score 0.45 overall. Both gates matter:
four class headers alone score 0.30, so three stray row labels would clear a
score floor by themselves — and an appraisal's amenity comparison really does
have rows called Age, Parking and Utilities under Class A/B/C headings. A file
whose closest table falls short is reported as not having the guide, with the
score and row count in the note, rather than having the nearest grid returned
in its place.

Row labels are compared with punctuation and spacing removed, so "Homes
(Quality)", "Homes(Quality)" and "HOMES  (QUALITY)" all match. Rows the guide
does not define are kept and flagged rather than dropped, so a firm that adds
its own row does not lose it silently.

## How the table is read off the page

Three routes are tried and the best-scoring result wins, with the character
rebuild preferred when two tie.

The first two are pdfplumber's own: its ruled reader, and its whole-page text
reader. On a real archive of fifty-three appraisals, fifteen defeated both.
The guide is drawn without ruling that the PDF layer exposes, so the ruled
reader returns nothing at all for that page; the text reader then treats the
entire page as one grid and smears the prose above the table through the same
columns, which breaks the header row apart.

The third route rebuilds the table from character positions. The header labels
give the column centres, the row labels anchor the rows, and each row owns
every line between the midpoints to its neighbours. That last part matters:
the row label is centred in its row while a wrapped value starts at the top of
it, so the first line of "Medium (10 sites/acre or less)" is printed **above**
the word "Density". Treating each line as its own row, or folding a label-less
line into the row above, puts those values one row out.

It reads characters rather than words on purpose. Some of these files carry a
text matrix about 5e-08 off true, which is enough for pdfplumber to mark every
character in the table as not upright and read the whole thing as vertical
text, one letter to a line. The characters themselves are exact; only the
orientation verdict is wrong.

The rebuild is also preferred over the ruled reader on a tie, because the
ruled reader decides where a row ends from the ruling, and on five of these
files a rule sits a point above the last line of a wrapped cell -- dropping
the "Dirt)" of "(Some Gravel & Dirt)" into the row beneath. Both score a
perfect 1.0 there; only one is right.

## What you get

A workbook with:

- **Index** — every PDF in the archive, what happened to it, which page the
  guide was on, and the match score. Files that produced nothing are shaded.
- **Combined** — one row per (appraisal, category), so the same row can be
  compared across every appraisal at once. This is the sheet the exercise is
  for.
- **One sheet per appraisal** — the table as it was printed, title band and
  star-rating band included, with the source file, page and score beneath it.

The page also previews any matched table in the browser before you download.

## Limits

Values are read from the **PDF text layer**, so they are exact rather than
retyped or transcribed. A scanned appraisal has no text layer: it is reported
as `scan` and skipped, not guessed at. Routing those through the OCR server is
not connected here.

A guide split across a page break keeps only the part on the page it was found
on; the Index note then lists the missing rows, so it shows up rather than
passing silently. Nested zips are not unpacked. Unpacked archives accumulate
under `uploads/appraisals/` and workbooks under `outputs/appraisals/`; nothing
is cleaned up on a schedule.

Measured on a real archive: 53 appraisals of 120–150 pages each took 90
seconds, about 1.7 s per file. The scan stops early once a table plainly is
the guide, so a file whose guide sits on page 43 of 134 is not read to the
end.

**Every extracted table needs review.** A shape match is not proof that the
right table was found, and a value landing in the right column is not proof it
was read correctly.

## Verification

```powershell
.\.venv\Scripts\python.exe test_mhc_rating_table.py    # locator + workbook
.\.venv\Scripts\python.exe test_appraisal_server.py    # the API, server running
node test_appraisal_ui.cjs                             # the page, server running
.\.venv\Scripts\python.exe test_appraisal_public.py    # the guards; starts its own
```

The API and page tests default to a standalone server on 7885. To test the
copy inside the chat server, point them at it first:
`$env:APPRAISAL_BASE = "http://127.0.0.1:7862/appraisals"`.

`test_appraisal_public.py` runs its own password-protected server on port 7899
so it does not disturb the one on 7885. It covers the login gate, the lockout,
the cross-origin refusal, the zip-bomb rejection, and that the server exits
rather than listening beyond this machine without a password.

The fixtures are generated, not real: a PDF carrying the guide exactly as the
sample prints it, one carrying a decoy that borrows three of its rows, one
with no guide at all, and one with no text layer. They prove the locator picks
the right table, refuses the wrong ones, and reports a scan instead of
guessing. They prove nothing about how much real appraisals vary — run a real
archive through the page for that.
