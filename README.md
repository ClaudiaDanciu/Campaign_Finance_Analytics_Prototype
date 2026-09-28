# Campaign Finance Analytics Prototype

This project is a desktop application for cleaning and analysing campaign finance data. It was developed in Python using Tkinter for the interface, Pandas and NumPy for data processing, Matplotlib for charts, and SQLite for saving and restoring data.

The application is single-threaded, as required by the assessment brief.

## Requirements

- Python 3.12-3.14
- NumPy 1.26 or later
- Pandas 2.1 or later
- Matplotlib 3.8 or later
- Tkinter and SQLite, which are normally included with Python

Install the required packages from the project folder:

```bash
python -m pip install -r requirements.txt
```

To run the program in Anaconda:

```bash
conda activate <environment-name>
python -m pip install -r requirements.txt
python app.py
```

## Project files

- `app.py` contains the Tkinter interface and charts.
- `campaign_finance.py` contains the cleaning, analysis, audit, backup and restore code.
- `demo_data.py` creates a small fictional dataset for testing the interface.
- `test_campaign_finance.py` contains the automated model tests.
- `test_revision.py` contains regression tests for the reassessment fixes, including GUI controller tests that drive the real window (skipped automatically if no display is available).
- `schema.sql` documents the SQLite table structure without including data.
- `requirements.txt` lists the third-party packages.
- `benchmark_concurrency.py` benchmarks the theoretical threaded redesign in report Section 1.1 and checks failure handling and snapshot memory. It is not part of the prototype. `benchmark_results_20260928_002421.csv` and `benchmark_gaps_20260928_002421.png` are the results reported in Table 1 and Figure 2.

## Input data

The program expects these four files in the same folder:

- `donors.csv`
- `candidates.csv`
- `committees.csv`
- `contributions.csv`

The required columns are checked when a folder is loaded. Extra columns are ignored.

The main relationships are:

```text
candidates.cand_id  -> committees.cand_id
committees.cmte_id  -> contributions.cmte_id
donors.donor_id     -> contributions.donor_id
```

## Using the program

### 1. Load the CSV files

Select **Load CSV folder** and choose the folder containing the four input files. The program checks that each file and its required columns are present.

### 2. Clean and prepare the data

Select **Clean and prepare**. This step:

- removes surrounding whitespace;
- converts state abbreviations to uppercase;
- replaces missing occupations and employers, including FEC placeholders such as `INFORMATION REQUESTED` and `REFUSED`, with `UNKNOWN`;
- keeps US territory and military postal codes (PR, VI, GU, AS, MP, AA, AE, AP) and replaces other invalid states with `UNKNOWN`;
- converts contribution amounts to numbers;
- parses contribution dates;
- removes rows with blank donor or committee IDs;
- removes unreadable amounts and dates;
- removes records that cannot be linked to a donor, committee or candidate;
- removes duplicate donor, candidate and committee keys;
- renames `contb_receipt_amt` to `contribution_amount`; and
- merges the four datasets for analysis.

Contributions whose employer or occupation matches the brief's non-individual keywords (PAC, COMM/COMMITTEE, UNION, CANDIDATE) are kept in the prepared data but excluded from individual-donor analysis. The reason for each exclusion is recorded.

### 3. Run an analysis

Choose a **Summary metric**: `contribution_amount` (per transaction) or `frequency` (contributions per individual donor). The GUI displays the count, mean, median, mode, minimum and maximum. Mean and median keep two decimals, so donor counts of 2, 1, 2, 1 show as 1.50 rather than a rounded 2.

The summary metric changes the statistics only. The tables and charts in every analysis view are always based on contribution amounts; a note under the toolbar says so.

Four analysis views are available:

- **Correlations** compares state and occupation indicators with contribution amount. These values show association, not causation.
- **Monthly candidate activity** uses a pivot table to show contribution totals by month and candidate.
- **State-occupation-candidate analysis** compares three categorical variables using transaction count, total amount and median amount.
- **Committee-state analysis** reports count, total, mean and median by candidate, committee and contributor state.

Results are shown as a scrollable table and as a chart within the application. Loading, restoring or cleaning data clears any results that belonged to the previous data. The status bar at the bottom confirms or rejects every action, and errors are also shown in a dialog.

### 4. Save or restore a backup

Clean the data before selecting **Save backup**. The current tables and prepared analysis are saved in a local SQLite file.

Backups are written to a temporary file, checked, and only then swapped in, so a failed save leaves any previous backup at that location unchanged.

Select **Restore backup** to reopen a saved state. A restored backup can be cleaned again without losing rows. The program checks the tables, columns, schema version, contribution values and foreign-key relationships before accepting the file. A prepared backup can be analysed without loading the original CSV files again.

### 5. Automatic session save and restore

When the window is closed, the prepared state is saved automatically to `~/.campaign_finance_analytics/session.sqlite` and restored the next time the program opens. If the save fails, you are asked whether to close anyway. The file is kept in your home folder rather than the project folder, so contributor data is never included in the submitted program. Set the `CAMPAIGN_FINANCE_SESSION` environment variable to use another location.

### 6. Export the results

Select **Export outputs** to create:

- `statistics.csv`
- `monthly_pivot.csv`
- `categorical_analysis.csv`
- `category_amount_correlations.csv`
- `committee_state_analysis.csv`
- `audit.jsonl`

`statistics.csv` uses the summary metric currently selected; the other files are based on contribution amounts. The audit file records the metric used, and also records loads, cleaning decisions, analyses, errors, backups, restores and exports with timestamps.

## Demonstration data

Generate a small fictional dataset with:

```bash
python demo_data.py
```

This creates a `demo_input` folder containing the four required CSV files. It can be used to demonstrate the workflow without using real contributor information.

## Tests

Run the tests from the project folder:

```bash
python -m unittest -v
```

The tests cover data cleaning, non-individual exclusions, missing and duplicate identifiers, invalid values, statistics, analysis outputs, SQLite integrity, backup and restore, audit logging, and output filenames.

Result on the fictional data: 36 tests, 35 passed and 1 skipped (the real-dataset test). Without a display, the three GUI tests are skipped as well.

To run the additional test with the assessment dataset, extract it and set `CAMPAIGN_FINANCE_REAL_DATA` to the folder containing the four CSV files (or place the extracted `AAP OL4 25 26 Reassessment data set` folder next to the project folder). Do not include the dataset in the submitted zip.

macOS or Linux:

```bash
export CAMPAIGN_FINANCE_REAL_DATA=/path/to/dataset
python -m unittest -v
```

Windows PowerShell:

```powershell
$env:CAMPAIGN_FINANCE_REAL_DATA = "C:\path\to\dataset"
python -m unittest -v
```

If the dataset is not available, this test is skipped and the fictional-data tests still run.

## Concurrency benchmark (report Section 1.1)

The prototype itself is single-threaded. To reproduce Table 1 and Figure 2 with the assessment dataset:

```bash
python benchmark_concurrency.py --data "/path/to/dataset"
```

Add `--checks-only` to run just the failure-handling and memory checks. Each run writes a timestamped CSV and chart to the project folder.

## Database

The project uses SQLite 3, which is included with Python. The database is created and populated by `CampaignFinanceModel.save_sqlite()` in `campaign_finance.py`.

`schema.sql` is a structure-only copy of the database schema. It describes the `donors`, `candidates`, `committees`, `contributions`, `prepared_analysis` and `metadata` tables, together with their keys and indexes.

SQLite is an embedded database, so there is no localhost address or port to configure. The official SQLite download page is <https://www.sqlite.org/download.html>.

## Limitations and responsible use

Contributor records may contain personal information. Real data and exported results should only be used for legitimate education and analysis. They should not be used for advertising, solicitation or targeting individuals.

Employer and occupation values are self-reported and may be inconsistent. Contributions without a donor ID are removed because they cannot be linked to a donor; in the 2012 assessment data this removes every contribution to six candidates, so candidate-level results cover seven of the thirteen candidates. Rule-based exclusions may not classify every record correctly. Negative amounts may represent refunds or corrections. Correlation results describe patterns in this dataset but do not explain why those patterns exist.
