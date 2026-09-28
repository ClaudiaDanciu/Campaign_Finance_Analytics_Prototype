-- Structure-only dump of the database produced by CampaignFinanceModel.save_sqlite() in campaign_finance.py.
-- No data is included; this describes the schema that program creates and populates, at schema_version = 2.
PRAGMA foreign_keys = ON;

-- Raw/translated relational tables (populated after "Load CSV folder" -> "Clean and prepare").
CREATE TABLE donors (
  contbr_nm TEXT, contbr_city TEXT, contbr_st TEXT, contbr_zip TEXT,
  contbr_employer TEXT, contbr_occupation TEXT, donor_id TEXT PRIMARY KEY
);

CREATE TABLE candidates (cand_id TEXT PRIMARY KEY, cand_nm TEXT NOT NULL);

CREATE TABLE committees (
  cmte_id TEXT PRIMARY KEY,
  cand_id TEXT NOT NULL REFERENCES candidates(cand_id)
);

CREATE TABLE contributions (
  cmte_id TEXT NOT NULL REFERENCES committees(cmte_id),
  donor_id TEXT NOT NULL REFERENCES donors(donor_id),
  contribution_amount REAL NOT NULL,
  contb_receipt_dt TEXT NOT NULL,
  receipt_desc TEXT, memo_cd TEXT, memo_text TEXT,
  form_tp TEXT, file_num INTEGER, month TEXT
);

-- Denormalised analysis table: one row per contribution, joined with committee/candidate/donor
-- attributes, plus the non-individual exclusion flag and reason. This is also the audit trail for
-- exclusion decisions - excluded rows are kept here (is_individual = 0), never deleted.
CREATE TABLE prepared_analysis (
  cmte_id TEXT NOT NULL REFERENCES committees(cmte_id),
  donor_id TEXT NOT NULL REFERENCES donors(donor_id),
  contribution_amount REAL NOT NULL,
  contb_receipt_dt TEXT NOT NULL,
  receipt_desc TEXT, memo_cd TEXT, memo_text TEXT,
  form_tp TEXT, file_num INTEGER, month TEXT,
  cand_id TEXT NOT NULL REFERENCES candidates(cand_id),
  cand_nm TEXT NOT NULL,
  contbr_nm TEXT, contbr_city TEXT, contbr_st TEXT, contbr_zip TEXT,
  contbr_employer TEXT, contbr_occupation TEXT,
  is_individual INTEGER NOT NULL,        -- 0/1; false for PAC/COMMITTEE/UNION/CANDIDATE matches
  exclusion_reason TEXT                  -- NULL for individual rows, else the matched category
);

-- key/value store for backup metadata (schema_version, saved_utc), used by restore_sqlite() to
-- validate that a backup file is a compatible, well-formed snapshot before it is loaded.
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE INDEX idx_contributions_cmte ON contributions(cmte_id);
CREATE INDEX idx_contributions_donor ON contributions(donor_id);
CREATE INDEX idx_committees_cand ON committees(cand_id);
CREATE INDEX idx_prepared_cand_nm ON prepared_analysis(cand_nm);
CREATE INDEX idx_prepared_cmte_state ON prepared_analysis(cmte_id, contbr_st);
CREATE INDEX idx_prepared_individual ON prepared_analysis(is_individual);
