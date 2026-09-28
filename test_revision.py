"""Regression tests for the reassessment revision: display precision, export metric, safe backups,
cleaning a restored backup, stale results, causation messaging and the close/reopen session lifecycle."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from campaign_finance import CampaignFinanceModel, format_statistics
from demo_data import create

try:
    import tkinter as tk
    _root = tk.Tk(); _root.destroy()
    GUI_AVAILABLE = True
except Exception:  # no Tkinter or no display (e.g. headless CI)
    GUI_AVAILABLE = False


def prepared_model(folder: Path) -> CampaignFinanceModel:
    create(folder)
    m = CampaignFinanceModel(); m.load_csv_folder(folder); m.clean_and_prepare()
    return m


class ModelRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.m = prepared_model(self.root / "in")

    def tearDown(self):
        self.tmp.cleanup()

    def test_frequency_mean_and_median_keep_two_decimals(self):
        text = format_statistics(self.m.statistics("frequency"), "frequency")
        self.assertIn("Mean: 1.50", text)
        self.assertIn("Median: 1.50", text)
        self.assertIn("Minimum: 1", text)
        self.assertIn("Maximum: 2", text)
        self.assertIn("Mean: $99.17", format_statistics(self.m.statistics(), "contribution_amount"))
        refund = self.m.statistics().copy(); refund["minimum"] = -5000
        self.assertIn("Minimum: -$5,000.00", format_statistics(refund, "contribution_amount"))

    def test_export_uses_selected_summary_metric(self):
        out = self.root / "out"
        self.m.export_outputs(out, metric="frequency")
        stats = pd.read_csv(out / "statistics.csv", index_col=0)["value"]
        self.assertEqual(stats["count"], 4)          # four individual donors, not six transactions
        self.assertAlmostEqual(stats["mean"], 1.5)
        last = json.loads((out / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertEqual(last["action"], "export")
        self.assertIn("summary metric=frequency", last["detail"])
        with self.assertRaises(ValueError):
            self.m.export_outputs(out, metric="not_a_metric")

    def test_failed_save_leaves_previous_backup_intact(self):
        db = self.root / "state.sqlite"
        self.m.save_sqlite(db)
        broken = prepared_model(self.root / "in2")
        broken.tables["contributions"].loc[broken.tables["contributions"].index[0], "donor_id"] = "NO_SUCH_DONOR"
        with self.assertRaises(Exception):
            broken.save_sqlite(db)
        con = sqlite3.connect(db)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM contributions").fetchone()[0], 8)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM prepared_analysis").fetchone()[0], 8)
        con.close()
        self.assertEqual(sorted(p.name for p in self.root.iterdir() if p.suffix == ".tmp"), [])  # no temp debris
        restored = CampaignFinanceModel(); restored.restore_sqlite(db)
        self.assertEqual(len(restored.individual_data()), 6)

    def test_cleaning_a_restored_backup_keeps_all_rows(self):
        db = self.root / "state.sqlite"
        self.m.save_sqlite(db)
        restored = CampaignFinanceModel(); restored.restore_sqlite(db)
        diag = restored.clean_and_prepare()
        self.assertEqual(diag["invalid_amount_or_date_removed"], 0)
        self.assertEqual(diag["prepared_rows"], 8)
        self.assertEqual(diag["individual_rows"], 6)
        pd.testing.assert_series_equal(self.m.statistics(), restored.statistics())
        pd.testing.assert_frame_equal(self.m.pivot(), restored.pivot())

    def test_missing_value_placeholders_become_unknown(self):
        from demo_data import frames, write
        d = frames()["donors"]
        d.loc[d["donor_id"] == "D1", "contbr_occupation"] = "Information Requested Per Best Efforts"
        d.loc[d["donor_id"] == "D2", "contbr_employer"] = "INFORMATION REQUESTED"
        d.loc[d["donor_id"] == "D4", "contbr_occupation"] = "refused"
        write(self.root / "ph", {**frames(), "donors": d})
        m = CampaignFinanceModel(); m.load_csv_folder(self.root / "ph"); m.clean_and_prepare()
        by_donor = m.prepared.drop_duplicates("donor_id").set_index("donor_id")
        self.assertEqual(by_donor.loc["D1", "contbr_occupation"], "UNKNOWN")
        self.assertEqual(by_donor.loc["D2", "contbr_employer"], "UNKNOWN")
        self.assertEqual(by_donor.loc["D4", "contbr_occupation"], "UNKNOWN")
        self.assertTrue(by_donor.loc[["D1", "D2", "D4"], "is_individual"].all())  # still individuals, not excluded

    def test_territory_and_military_codes_are_kept(self):
        from demo_data import frames, write
        d = frames()["donors"]; d.loc[d["donor_id"] == "D1", "contbr_st"] = "pr"; d.loc[d["donor_id"] == "D2", "contbr_st"] = "AE"
        d.loc[d["donor_id"] == "D4", "contbr_st"] = "XX"
        write(self.root / "terr", {**frames(), "donors": d})
        m = CampaignFinanceModel(); m.load_csv_folder(self.root / "terr"); m.clean_and_prepare()
        st = m.prepared.drop_duplicates("donor_id").set_index("donor_id")["contbr_st"]
        self.assertEqual((st["D1"], st["D2"], st["D4"]), ("PR", "AE", "UNKNOWN"))

    def test_corporate_employer_does_not_exclude_individual(self):
        from demo_data import frames, write
        d = frames()["donors"]; d.loc[d["donor_id"] == "D1", "contbr_employer"] = "MICROSOFT CORPORATION"
        write(self.root / "corp", {**frames(), "donors": d})
        m = CampaignFinanceModel(); m.load_csv_folder(self.root / "corp"); diag = m.clean_and_prepare()
        self.assertEqual(diag["individual_rows"], 6)
        self.assertNotIn("CORPORATION", diag["exclusion_reason_counts"])


@unittest.skipUnless(GUI_AVAILABLE, "Tkinter display not available; GUI controller tests skipped.")
class GuiRevisionTests(unittest.TestCase):
    """Drives the real App with the file dialogs and message boxes patched out."""

    def setUp(self):
        from app import App
        self.App = App
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        create(self.root / "in")
        self.session = self.root / "session.sqlite"
        self.patches = [mock.patch("app.messagebox.showerror"), mock.patch("app.messagebox.showinfo"),
                        mock.patch("app.messagebox.askyesno", return_value=True),
                        mock.patch("app.filedialog.askdirectory", return_value=str(self.root / "in"))]
        for p in self.patches: p.start()
        self.app = self.App(session_path=self.session, restore_session=False); self.app.withdraw()

    def tearDown(self):
        try: self.app.destroy()
        except Exception: pass
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def _prepare_and_run(self, view="Committee-state analysis", metric="contribution_amount"):
        self.app.load(); self.app._task(self.app._clean, clear=True)
        self.app.metric.set(metric); self.app.view.set(view)
        self.assertTrue(self.app._task(lambda: self.app._analyse(metric, view)))

    def test_new_data_clears_stale_results(self):
        self._prepare_and_run()
        self.assertTrue(hasattr(self.app, "_view"))
        self.assertIn("COMMITTEE-STATE", self.app.text.get("1.0", "end"))
        self.app.load()                                   # new source data
        self.assertFalse(hasattr(self.app, "_view"))
        self.assertNotIn("COMMITTEE-STATE", self.app.text.get("1.0", "end"))
        self.assertNotIn("Observations", self.app.text.get("1.0", "end"))
        self.assertEqual(len(self.app.fig.axes), 0)
        self.assertEqual(self.app.state_name, "LOADED")
        self.assertEqual(str(self.app.run_btn["state"]), "disabled")

    def test_causation_note_only_on_correlation_view(self):
        self._prepare_and_run("Monthly candidate activity", "frequency")
        self.assertNotIn("causation", self.app.status.get())
        self.assertIn("Mean: 1.50", self.app.text.get("1.0", "end"))
        self._prepare_and_run("Correlations")
        self.assertIn("association, not causation", self.app.status.get())

    def test_session_saved_on_close_and_restored_on_start(self):
        self._prepare_and_run()
        self.app.on_close()                               # destroys the window after saving
        self.assertTrue(self.session.is_file())
        self.app = self.App(session_path=self.session, restore_session=False); self.app.withdraw()
        self.app.restore_session()
        self.assertEqual(self.app.state_name, "PREPARED")
        self.assertIn("Previous session restored", self.app.status.get())
        self.assertEqual(len(self.app.model.individual_data()), 6)
        self.assertEqual(str(self.app.run_btn["state"]), "normal")


if __name__ == "__main__":
    unittest.main()
