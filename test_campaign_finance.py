import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from campaign_finance import REQUIRED, CampaignFinanceModel
from demo_data import create, frames, write


def real_dataset_path():
    """Locates the real assessment CSVs outside this project folder, if the marker/student has them
    available locally. Never bundled in the submission; the real-data test below skips cleanly when absent."""
    parent = Path(__file__).resolve().parent.parent
    # The dataset is distributed as a zip; the test needs the extracted folder (with or without ".zip" in its name).
    candidates = [os.environ.get("CAMPAIGN_FINANCE_REAL_DATA"), parent / "AAP OL4 25 26 Reassessment data set",
                  parent / "AAP OL4 25 26 Reassessment data set.zip"]
    for candidate in candidates:
        if not candidate:
            continue
        p = Path(candidate)
        if p.is_dir() and all((p / f"{name}.csv").is_file() for name in REQUIRED):
            return p
    return None


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name); create(self.root)
        self.m = CampaignFinanceModel(); self.m.load_csv_folder(self.root); self.diag = self.m.clean_and_prepare()

    def tearDown(self):
        self.tmp.cleanup()

    def test_cleaning_and_exclusion(self):
        self.assertEqual(self.diag["non_individual_excluded"], 2)
        self.assertEqual(self.diag["individual_rows"], 6)
        self.assertEqual(len(self.m.individual_data()), 6)
        self.assertIn("contribution_amount", self.m.tables["contributions"])
        self.assertNotIn("contb_receipt_amt", self.m.tables["contributions"])

    def test_no_rows_removed_for_clean_demo_data(self):
        self.assertEqual(self.diag["blank_identifier_removed"], 0)
        self.assertEqual(self.diag["invalid_amount_or_date_removed"], 0)
        self.assertEqual(self.diag["orphan_committee_removed"], 0)
        self.assertEqual(self.diag["unlinked_donor_or_candidate_removed"], 0)

    def test_statistics(self):
        s = self.m.statistics()
        self.assertAlmostEqual(s["mean"], 99.1666667, places=5)
        self.assertEqual(s["median"], 85)
        self.assertEqual(s["missing_excluded"], 0)

    def test_frequency_statistics(self):
        s = self.m.statistics("frequency")
        self.assertEqual(s["count"], 4)  # 4 distinct individual donors
        self.assertEqual(s["maximum"], 2)

    def test_pivot_and_categories(self):
        self.assertEqual(self.m.pivot().shape[1], 2)
        self.assertTrue({"contbr_st", "contbr_occupation", "cand_nm"}.issubset(self.m.categorical().columns))

    def test_committee_state_analysis(self):
        result = self.m.committee_state_analysis()
        self.assertTrue({"cand_nm", "cmte_id", "contbr_st", "transaction_count", "total_amount", "mean_amount", "median_amount"}.issubset(result.columns))
        row = result[(result["cand_nm"] == "Candidate Alpha") & (result["contbr_st"] == "TX")].iloc[0]
        self.assertEqual(row["total_amount"], 120)
        self.assertEqual(row["transaction_count"], 1)

    def test_non_individual_exclusion_reasons(self):
        reasons = dict(zip(self.m.prepared["donor_id"], self.m.prepared["exclusion_reason"]))
        self.assertEqual(reasons["D3"], "PAC")
        self.assertEqual(reasons["D5"], "UNION")
        self.assertTrue(pd.isna(reasons["D1"]))
        self.assertEqual(self.diag["exclusion_reason_counts"], {"PAC": 1, "UNION": 1})

    def test_backup_round_trip(self):
        db = self.root / "state.sqlite"
        self.m.save_sqlite(db)
        con = sqlite3.connect(db)
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        con.close()
        restored = CampaignFinanceModel(); restored.restore_sqlite(db)
        self.assertEqual(len(restored.individual_data()), 6)
        pd.testing.assert_series_equal(self.m.statistics(), restored.statistics())

    def test_repeated_cleaning_is_stable(self):
        diag2 = self.m.clean_and_prepare()
        self.assertEqual(self.diag["individual_rows"], diag2["individual_rows"])
        self.assertEqual(len(self.m.individual_data()), 6)

    def test_export_output_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.m.export_outputs(Path(tmp) / "out")
            names = {p.name for p in paths}
            expected = {"statistics.csv", "monthly_pivot.csv", "categorical_analysis.csv",
                        "category_amount_correlations.csv", "committee_state_analysis.csv", "audit.jsonl"}
            self.assertEqual(names, expected)

    def test_export_audit_includes_its_own_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self.m.export_outputs(out)
            lines = (out / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
            actions = [json.loads(line)["action"] for line in lines]
            self.assertIn("export", actions)
            self.assertEqual(actions[-1], "export")


class CleaningEdgeCaseTests(unittest.TestCase):
    def _model_for(self, **overrides) -> CampaignFinanceModel:
        data = frames(); data.update(overrides)
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, data)
            m = CampaignFinanceModel(); m.load_csv_folder(tmp)
            self.last_diag = m.clean_and_prepare()
            self.last_tmp = tmp
            return m

    def test_blank_donor_id_is_removed_not_saved(self):
        c = frames()["contributions"]; c.loc[0, "donor_id"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, {**frames(), "contributions": c})
            m = CampaignFinanceModel(); m.load_csv_folder(tmp)
            diag = m.clean_and_prepare()
            self.assertEqual(diag["blank_identifier_removed"], 1)
            self.assertNotIn("", m.tables["contributions"]["donor_id"].tolist())
            db = Path(tmp) / "state.sqlite"
            m.save_sqlite(db)  # must not raise sqlite3.IntegrityError
            con = sqlite3.connect(db)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            con.close()

    def test_blank_committee_id_is_removed_not_saved(self):
        c = frames()["contributions"]; c.loc[0, "cmte_id"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, {**frames(), "contributions": c})
            m = CampaignFinanceModel(); m.load_csv_folder(tmp)
            diag = m.clean_and_prepare()
            self.assertEqual(diag["blank_identifier_removed"], 1)
            db = Path(tmp) / "state.sqlite"
            m.save_sqlite(db)
            con = sqlite3.connect(db)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            con.close()

    def test_invalid_date_is_removed(self):
        c = frames()["contributions"]; c.loc[0, "contb_receipt_dt"] = "not-a-date"
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, {**frames(), "contributions": c})
            m = CampaignFinanceModel(); m.load_csv_folder(tmp)
            diag = m.clean_and_prepare()
            self.assertEqual(diag["invalid_amount_or_date_removed"], 1)
            self.assertEqual(diag["blank_identifier_removed"], 0)

    def test_non_numeric_amount_is_removed(self):
        c = frames()["contributions"]; c["contb_receipt_amt"] = c["contb_receipt_amt"].astype(object)
        c.loc[0, "contb_receipt_amt"] = "not-a-number"
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, {**frames(), "contributions": c})
            m = CampaignFinanceModel(); m.load_csv_folder(tmp)
            diag = m.clean_and_prepare()
            self.assertEqual(diag["invalid_amount_or_date_removed"], 1)

    def test_invalid_state_is_recoded_not_removed(self):
        d = frames()["donors"]; d.loc[d["donor_id"] == "D1", "contbr_st"] = "ZZ"
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, {**frames(), "donors": d})
            m = CampaignFinanceModel(); m.load_csv_folder(tmp)
            diag = m.clean_and_prepare()
            self.assertEqual(diag["unlinked_donor_or_candidate_removed"], 0)
            row = m.prepared[m.prepared["donor_id"] == "D1"]
            self.assertTrue((row["contbr_st"] == "UNKNOWN").all())

    def test_missing_occupation_is_recoded_not_excluded(self):
        d = frames()["donors"]; d.loc[d["donor_id"] == "D2", "contbr_occupation"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, {**frames(), "donors": d})
            m = CampaignFinanceModel(); m.load_csv_folder(tmp)
            m.clean_and_prepare()
            row = m.prepared[m.prepared["donor_id"] == "D2"]
            self.assertTrue((row["contbr_occupation"] == "UNKNOWN").all())
            self.assertTrue(row["is_individual"].all())

    def test_duplicate_keys_are_deduplicated(self):
        base = frames()
        donors = pd.concat([base["donors"], base["donors"].iloc[[0]]], ignore_index=True)
        candidates = pd.concat([base["candidates"], base["candidates"].iloc[[0]]], ignore_index=True)
        committees = pd.concat([base["committees"], base["committees"].iloc[[0]]], ignore_index=True)
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, {**base, "donors": donors, "candidates": candidates, "committees": committees})
            m = CampaignFinanceModel(); m.load_csv_folder(tmp)
            m.clean_and_prepare()
            self.assertEqual(len(m.tables["donors"]), len(base["donors"]))
            self.assertEqual(len(m.tables["candidates"]), len(base["candidates"]))
            self.assertEqual(len(m.tables["committees"]), len(base["committees"]))
            m.save_sqlite(Path(tmp) / "state.sqlite")  # must not raise a PRIMARY KEY violation

    def test_multiple_modes_reports_first_value_and_tie_count(self):
        c = frames()["contributions"]
        c["contb_receipt_amt"] = [50, 45, 80, 80, 50, 90, 500, 700]  # individual rows (D1,D2,D4,D6,D1,D4) -> 50 and 80 tie at 2
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, {**frames(), "contributions": c})
            m = CampaignFinanceModel(); m.load_csv_folder(tmp)
            m.clean_and_prepare()
            stats = m.statistics()
            self.assertEqual(stats["mode_count"], 2)
            self.assertEqual(stats["mode"], 50)

    def test_all_non_individual_yields_clear_error_not_crash(self):
        d = frames()["donors"]; d["contbr_employer"] = "SOME PAC"
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, {**frames(), "donors": d})
            m = CampaignFinanceModel(); m.load_csv_folder(tmp)
            m.clean_and_prepare()
            self.assertEqual(len(m.individual_data()), 0)
            with self.assertRaises(ValueError):
                m.statistics()
            self.assertTrue(m.committee_state_analysis().empty)
            self.assertTrue(m.categorical().empty)


class BackupValidationTests(unittest.TestCase):
    def test_restore_rejects_missing_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "broken.sqlite"
            con = sqlite3.connect(db); con.execute("CREATE TABLE donors (donor_id TEXT PRIMARY KEY)"); con.commit(); con.close()
            with self.assertRaises(ValueError):
                CampaignFinanceModel().restore_sqlite(db)

    def test_restore_rejects_missing_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "broken.sqlite"
            con = sqlite3.connect(db)
            for name, columns in REQUIRED.items():
                cols_sql = ", ".join(f'"{c}" TEXT' for c in columns[1:]) or "dummy TEXT"  # drop first required column
                con.execute(f"CREATE TABLE {name} ({cols_sql})")
            con.commit(); con.close()
            with self.assertRaises(ValueError):
                CampaignFinanceModel().restore_sqlite(db)

    def test_restore_rejects_nonexistent_file(self):
        with self.assertRaises(ValueError):
            CampaignFinanceModel().restore_sqlite("/no/such/backup.sqlite")

    def test_restore_rejects_non_sqlite_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not_a_database.sqlite"
            path.write_text("this is plain text, not a sqlite file", encoding="utf-8")
            with self.assertRaises(ValueError):
                CampaignFinanceModel().restore_sqlite(path)

    def test_restore_rejects_unreadable_contribution_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "in"; write(root)
            m = CampaignFinanceModel(); m.load_csv_folder(root); m.clean_and_prepare()
            db = Path(tmp) / "state.sqlite"; m.save_sqlite(db)
            con = sqlite3.connect(db)
            con.execute("UPDATE contributions SET contribution_amount = 'not-a-number' WHERE rowid = 1")
            con.commit(); con.close()
            with self.assertRaises(ValueError):
                CampaignFinanceModel().restore_sqlite(db)


@unittest.skipUnless(real_dataset_path(), "Real assessment dataset not found locally (set CAMPAIGN_FINANCE_REAL_DATA or place it as a sibling folder); skipping real-data-shaped test.")
class RealDatasetShapeTests(unittest.TestCase):
    def test_real_dataset_clean_save_restore(self):
        m = CampaignFinanceModel()
        m.load_csv_folder(real_dataset_path())
        diag = m.clean_and_prepare()
        self.assertEqual(diag["blank_identifier_removed"], 7299)
        self.assertEqual(diag["individual_rows"], 32694)
        self.assertEqual(diag["exclusion_reason_counts"], {"UNION": 41, "COMMITTEE": 30, "CANDIDATE": 3, "PAC": 2})
        self.assertFalse(m.prepared["contbr_occupation"].str.contains("REQUESTED").any())
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite"
            m.save_sqlite(db)
            con = sqlite3.connect(db)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            con.close()
            restored = CampaignFinanceModel(); restored.restore_sqlite(db)
            self.assertEqual(len(restored.individual_data()), 32694)
            pd.testing.assert_series_equal(m.statistics(), restored.statistics())


if __name__ == "__main__":
    unittest.main()
