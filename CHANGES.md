# Changes in this revision

Tested with Python 3.12.3, pandas 2.3.3, Matplotlib 3.11 and Tk 8.6 (`python -m unittest -v`):
36 tests pass with the dataset; without it, 35 pass and 1 is skipped.

## campaign_finance.py
- **Restored backups can be re-cleaned.** ISO dates (as saved by save_sqlite) are now parsed; before, re-cleaning a restored backup left 0 rows.
- **Failed saves keep the previous backup.** Data is written to a temporary file, checked (foreign_key_check, integrity_check), then swapped in with os.replace().
- **Missing-value placeholders** ("INFORMATION REQUESTED", "REFUSED", "NONE", blank, etc.) in employer and occupation become UNKNOWN. On the real data, 1,992 individual contributions now have occupation UNKNOWN (1,663 of them "information requested" variants); before, the placeholder was the strongest positive correlation.
- **No CORPORATION exclusion.** The brief's keywords are PAC, COMM, UNION and CANDIDATE; a CORP rule wrongly removed 371 contributions from employees (e.g. MICROSOFT CORPORATION / ENGINEER). Now 76 exclusions and 32,694 individual rows (was 447 and 32,323).
- **Territory and military codes** (PR, VI, GU, AS, MP, AA, AE, AP) are kept (90 contributions).
- export_outputs() writes statistics.csv for the selected metric and logs it; format_statistics() is Tkinter-free and keeps two decimals for frequency (1.50, not 2). Smaller fixes: stable ordering of tied correlations, no pandas index in outputs, negative amounts shown as -$5,000.00.

## app.py
- **Session saved on close and restored on start** (brief, FR2), in `~/.campaign_finance_analytics/session.sqlite`, outside the project so no contributor data enters the zip.
- Results clear after a successful load, restore or clean; a failed action keeps them.
- The causation note appears only on Correlations.
- Layout: two toolbar rows (Data / Analysis), a "Summary metric" label with a note that tables and charts use amounts, a monospaced scrollable results table, a status bar that stays visible, chart titles giving the number of items shown, and confirmation dialogs for save and export.

## Tests
- New `test_revision.py`: 10 regression tests covering display precision, the export metric, failed saves, re-cleaning a restored backup, placeholders, corporate employees and territory codes, plus 3 tests that drive the real Tk window (stale results, causation note, session save and restore).
- `test_campaign_finance.py` finds the dataset folder with or without ".zip" in its name.

## New
- `benchmark_concurrency.py`: benchmark and failure/memory checks for report Section 1.1 (not part of the prototype), with its results `benchmark_results_20260928_002421.csv` and `benchmark_gaps_20260928_002421.png`.

## Unchanged
demo_data.py, requirements.txt and the database structure (one comment updated in schema.sql). demo_input/ and demo_output/ were regenerated with the fixed code.