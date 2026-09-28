from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

REQUIRED = {
    "donors": ["contbr_nm", "contbr_city", "contbr_st", "contbr_zip", "contbr_employer", "contbr_occupation", "donor_id"],
    "candidates": ["cand_id", "cand_nm"],
    "committees": ["cmte_id", "cand_id"],
    "contributions": ["cmte_id", "donor_id", "contb_receipt_amt", "contb_receipt_dt", "receipt_desc", "memo_cd", "memo_text", "form_tp", "file_num"],
}
# Checked in priority order; the first pattern that matches wins and is recorded as the exclusion reason.
NON_INDIVIDUAL_PATTERNS = [
    ("PAC", re.compile(r"\bPAC\b", re.I)),
    ("COMMITTEE", re.compile(r"\bCOMM(?:ITTEE)?\b", re.I)),
    ("UNION", re.compile(r"\bUNION\b", re.I)),
    ("CANDIDATE", re.compile(r"\bCANDIDATE\b", re.I)),
    # No CORPORATION pattern: the brief's examples are PAC, COMM, UNION and CANDIDATE, and on the data
    # a CORP rule mostly removed individual employees (e.g. "MICROSOFT CORPORATION" / "ENGINEER").
]
# FEC filers write these when a donor gives no employer or occupation; they are missing values, not categories.
MISSING_PLACEHOLDER = re.compile(r"^(?:INFO(?:RMATION)?\s+REQUESTED.*|REQUESTED|REFUSED|N/?A|NONE|)$")
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
# US territories and military (APO/FPO) postal codes are valid contributor addresses, not errors.
US_STATES |= set("PR VI GU AS MP AA AE AP".split())
SCHEMA_VERSION = "2"
SUPPORTED_SCHEMA_VERSIONS = {"1", "2"}
# Tried in order. The first two are the FEC/CSV input formats; the ISO formats are how save_sqlite() stores
# cleaned dates, so a restored backup can be cleaned again without every row being rejected as a bad date.
DATE_FORMATS = ["%d-%b-%y", "%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]
SUMMARY_METRICS = ["contribution_amount", "frequency"]


def format_statistics(stats: pd.Series, metric: str) -> str:
    """Plain-text summary shown in the GUI. Kept free of Tkinter so it can be unit-tested.

    Amounts use currency with two decimals. Frequency counts are whole numbers for count, mode, minimum and
    maximum, but mean and median keep two decimals: donor counts 2, 1, 2, 1 give 1.50, not a rounded 2.
    """
    currency = metric != "frequency"
    def money(v): return f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}"
    def whole_or_money(v): return money(v) if currency else f"{v:,.0f}"
    def average(v): return money(v) if currency else f"{v:,.2f}"
    unit = "contribution amount per transaction" if currency else "contributions per individual donor"
    lines = ["INDIVIDUAL-DONOR STATISTICS", f"Summary metric: {metric}", f"({unit})", "",
             f"Observations: {int(stats['count']):,}",
             f"Missing/excluded values: {int(stats['missing_excluded']):,}",
             f"Mean: {average(stats['mean'])}", f"Median: {average(stats['median'])}",
             f"Mode: {whole_or_money(stats['mode'])}"
             + (f" (first of {int(stats['mode_count'])} tied mode values)" if int(stats["mode_count"]) > 1 else ""),
             f"Minimum: {whole_or_money(stats['minimum'])}", f"Maximum: {whole_or_money(stats['maximum'])}"]
    if currency and stats["minimum"] < 0:
        lines.append("Note: negative amounts may represent refunds or corrections, not new contributions.")
    return "\n".join(lines)


@dataclass
class AuditLog:
    events: list[dict] = field(default_factory=list)

    def add(self, action: str, detail: str, rows: int | None = None) -> None:
        self.events.append({"time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "action": action, "detail": detail, "rows": rows})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(x, sort_keys=True) for x in self.events) + "\n", encoding="utf-8")


def _audited(action: str) -> Callable:
    """Logs `<action>_failed` with the error message before re-raising, so recoverable errors are still auditable."""
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception as exc:
                self.audit.add(f"{action}_failed", str(exc))
                raise
        return wrapper
    return decorator


class CampaignFinanceModel:

    def __init__(self) -> None:
        self.tables: dict[str, pd.DataFrame] = {}
        self.prepared: pd.DataFrame | None = None
        self.audit = AuditLog()

    @_audited("load")
    def load_csv_folder(self, folder: str | Path) -> None:
        folder = Path(folder)
        loaded = {}
        for name, columns in REQUIRED.items():
            path = folder / f"{name}.csv"
            if not path.is_file():
                raise FileNotFoundError(f"Required file is missing: {path.name}")
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
            missing = sorted(set(columns) - set(frame.columns))
            if missing:
                raise ValueError(f"{path.name} missing columns: {', '.join(missing)}")
            loaded[name] = frame[columns].copy()
        self.tables = loaded
        self.prepared = None
        self.audit.add("load", f"Loaded validated CSV files from {folder}", sum(map(len, loaded.values())))

    @_audited("clean")
    def clean_and_prepare(self) -> dict[str, int]:
        if not self.tables:
            raise RuntimeError("Load or restore data before cleaning.")
        t = {k: v.copy() for k, v in self.tables.items()}
        for name, frame in t.items():
            for col in frame.select_dtypes(include=["object", "string"]):
                frame[col] = frame[col].astype("string").str.strip()
        d = t["donors"]
        d["contbr_st"] = d["contbr_st"].str.upper()
        invalid_state = ~d["contbr_st"].isin(US_STATES)
        d.loc[invalid_state, "contbr_st"] = "UNKNOWN"
        d["contbr_city"] = d["contbr_city"].replace({"": "UNKNOWN", "N/A": "UNKNOWN", "NONE": "UNKNOWN"}).fillna("UNKNOWN")
        for col in ["contbr_employer", "contbr_occupation"]:
            text = d[col].fillna("").str.upper().str.replace(r"\s+", " ", regex=True)
            d[col] = text.mask(text.str.fullmatch(MISSING_PLACEHOLDER), "UNKNOWN")
        c = t["contributions"]
        amount_source = c.pop("contb_receipt_amt") if "contb_receipt_amt" in c else c["contribution_amount"]
        # .astype("float64") deliberately downgrades from pandas' nullable Float64 (pd.NA) to plain numpy
        # float64 (np.nan): pd.NA propagates as itself (not True/False) through np.isfinite()/boolean "|"/
        # .sum() below, which would silently under-count or miss unparseable amounts.
        c["contribution_amount"] = pd.to_numeric(amount_source, errors="coerce").astype("float64")
        raw_date = c["contb_receipt_dt"].astype("string")
        parsed_date = pd.Series(pd.NaT, index=c.index, dtype="datetime64[ns]")
        for fmt in DATE_FORMATS:
            parsed_date = parsed_date.fillna(pd.to_datetime(raw_date, format=fmt, errors="coerce"))
        c["contb_receipt_dt"] = parsed_date
        c["file_num"] = pd.to_numeric(c["file_num"], errors="coerce").astype("Int64")

        # Blank donor_id/cmte_id strings are structurally missing keys, not valid foreign keys - they must be
        # treated as NA here, or they survive into the saved contributions table and violate the SQLite FK
        # constraint on save (a blank id essentially never matches a real donor/committee row).
        c["cmte_id"] = c["cmte_id"].replace("", pd.NA)
        c["donor_id"] = c["donor_id"].replace("", pd.NA)
        blank_identifier = c["cmte_id"].isna() | c["donor_id"].isna()
        bad_amount = ~np.isfinite(c["contribution_amount"])
        bad_date = c["contb_receipt_dt"].isna()
        invalid_amount_or_date = ~blank_identifier & (bad_amount | bad_date)
        blank_identifier_removed = int(blank_identifier.sum())
        invalid_amount_or_date_removed = int(invalid_amount_or_date.sum())
        c = c.loc[~(blank_identifier | bad_amount | bad_date)].copy()
        c["month"] = c["contb_receipt_dt"].dt.to_period("M").astype(str)
        t["contributions"] = c
        t["donors"] = d.drop_duplicates("donor_id", keep="first")
        t["candidates"] = t["candidates"].drop_duplicates("cand_id", keep="first")
        t["committees"] = t["committees"].drop_duplicates("cmte_id", keep="first")

        merged = c.merge(t["committees"], on="cmte_id", how="left", validate="many_to_one", indicator=True)
        orphan_committee_removed = int((merged["_merge"] != "both").sum())
        merged = merged.loc[merged["_merge"] == "both"].drop(columns="_merge")
        merged = merged.merge(t["candidates"], on="cand_id", how="left", validate="many_to_one")
        merged = merged.merge(t["donors"], on="donor_id", how="left", validate="many_to_one")
        unlinked_donor_or_candidate_removed = int(merged[["cand_nm", "contbr_st"]].isna().any(axis=1).sum())
        merged = merged.dropna(subset=["cand_nm", "contbr_st"]).copy()

        source_text = (merged["contbr_employer"].fillna("") + " " + merged["contbr_occupation"].fillna("")).astype(str)
        reason = pd.Series(pd.array([pd.NA] * len(merged), dtype="string"), index=merged.index)
        for name, pattern in NON_INDIVIDUAL_PATTERNS:
            hit = reason.isna() & source_text.str.contains(pattern)
            reason.loc[hit] = name
        merged["exclusion_reason"] = reason
        merged["is_individual"] = merged["exclusion_reason"].isna()
        exclusion_reason_counts = merged.loc[~merged["is_individual"], "exclusion_reason"].value_counts().to_dict()
        non_individual_excluded = int((~merged["is_individual"]).sum())

        self.tables, self.prepared = t, merged
        diag = {
            "blank_identifier_removed": blank_identifier_removed,
            "invalid_amount_or_date_removed": invalid_amount_or_date_removed,
            "orphan_committee_removed": orphan_committee_removed,
            "unlinked_donor_or_candidate_removed": unlinked_donor_or_candidate_removed,
            "prepared_rows": len(merged),
            "non_individual_excluded": non_individual_excluded,
            "individual_rows": int(merged["is_individual"].sum()),
            "exclusion_reason_counts": exclusion_reason_counts,
        }
        self.audit.add("clean", json.dumps(diag, sort_keys=True), len(merged))
        self.audit.add("invalid_removal",
                        f"blank_identifier={blank_identifier_removed}; invalid_amount_or_date={invalid_amount_or_date_removed}; "
                        f"orphan_committee={orphan_committee_removed}; unlinked_donor_or_candidate={unlinked_donor_or_candidate_removed}",
                        blank_identifier_removed + invalid_amount_or_date_removed + orphan_committee_removed + unlinked_donor_or_candidate_removed)
        self.audit.add("non_individual_exclusion", json.dumps(exclusion_reason_counts, sort_keys=True), non_individual_excluded)
        self.audit.add("rename", "contb_receipt_amt -> contribution_amount", len(c))
        self.audit.add("merge", "contributions -> committees -> candidates -> donors; many-to-one validation", len(merged))
        return diag

    def individual_data(self) -> pd.DataFrame:
        if self.prepared is None:
            raise RuntimeError("Clean and prepare data before analysis.")
        return self.prepared.loc[self.prepared["is_individual"]].copy()

    @_audited("statistics")
    def statistics(self, metric: str = "contribution_amount") -> pd.Series:
        df = self.individual_data()
        if metric == "frequency":
            raw = df.groupby("donor_id").size().astype(float)
            missing_excluded = 0
        elif metric in df and pd.api.types.is_numeric_dtype(df[metric]):
            raw = df[metric].astype(float)
            missing_excluded = int(raw.isna().sum())
            raw = raw.dropna()
        else:
            raise ValueError(f"Unsupported numeric metric: {metric}")
        if raw.empty:
            raise ValueError("No values remain for the selected analysis.")
        modes = raw.mode()
        mode_value = modes.iloc[0] if not modes.empty else np.nan
        result = pd.Series({"count": raw.count(), "mean": raw.mean(), "median": raw.median(),
                            "mode": mode_value, "mode_count": len(modes), "minimum": raw.min(), "maximum": raw.max(),
                            "missing_excluded": missing_excluded})
        self.audit.add("statistics", f"metric={metric}; individual-only; first mode of {len(modes)} shown", len(raw))
        return result

    @_audited("pivot")
    def pivot(self) -> pd.DataFrame:
        df = self.individual_data()
        result = pd.pivot_table(df, index="month", columns="cand_nm", values="contribution_amount", aggfunc="sum", fill_value=0)
        self.audit.add("pivot", "monthly contribution sum by candidate", len(result))
        return result

    @_audited("categorical")
    def categorical(self, top_occupations: int = 8) -> pd.DataFrame:
        df = self.individual_data()
        top = df["contbr_occupation"].value_counts().head(top_occupations).index
        result = (df[df["contbr_occupation"].isin(top)]
                  .groupby(["contbr_st", "contbr_occupation", "cand_nm"], observed=True)
                  .agg(transaction_count=("contribution_amount", "size"), total_amount=("contribution_amount", "sum"),
                       median_amount=("contribution_amount", "median")).reset_index())
        self.audit.add("categorical", f"state x occupation x candidate; top occupations={top_occupations}", len(result))
        return result.sort_values("total_amount", ascending=False, kind="stable").reset_index(drop=True)

    @_audited("committee_state")
    def committee_state_analysis(self) -> pd.DataFrame:
        df = self.individual_data()
        result = (df.groupby(["cand_nm", "cmte_id", "contbr_st"], observed=True)
                  .agg(transaction_count=("contribution_amount", "size"), total_amount=("contribution_amount", "sum"),
                       mean_amount=("contribution_amount", "mean"), median_amount=("contribution_amount", "median"))
                  .reset_index())
        self.audit.add("committee_state", "candidate x committee x state; count, total, mean, median", len(result))
        return result.sort_values("total_amount", ascending=False, kind="stable").reset_index(drop=True)

    @_audited("correlation")
    def correlation_matrix(self, max_categories: int = 12) -> pd.DataFrame:
        df = self.individual_data()
        keep_states = df["contbr_st"].value_counts().head(max_categories).index
        keep_jobs = df["contbr_occupation"].value_counts().head(max_categories).index
        subset = df[df["contbr_st"].isin(keep_states) & df["contbr_occupation"].isin(keep_jobs)]
        encoded = pd.get_dummies(subset[["contbr_st", "contbr_occupation"]], prefix=["state", "occupation"], dtype=float)
        encoded["contribution_amount"] = subset["contribution_amount"].astype(float).values
        corr = encoded.corr(numeric_only=True)[["contribution_amount"]].drop("contribution_amount")
        self.audit.add("correlation", "Pearson correlation of one-hot categories with amount; descriptive only", len(subset))
        # Sort by label first, then stably by r, so tied coefficients (e.g. a state and an occupation that pick out
        # the same donors) always come out in the same order whatever the pandas version.
        return corr.sort_index().sort_values("contribution_amount", kind="stable")

    @_audited("backup")
    def save_sqlite(self, path: str | Path) -> None:
        if not self.tables:
            raise RuntimeError("Nothing to save.")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write a complete new database beside the destination, validate it, then swap it in with os.replace().
        # If anything fails, the temporary file is deleted and the previous backup at `path` is left untouched.
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            self._write_sqlite(tmp_path)
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        self.audit.add("backup", f"SQLite relational snapshot (schema v{SCHEMA_VERSION}) saved to {path}")

    def _write_sqlite(self, path: Path) -> None:
        """Creates and populates a fresh database at `path` and checks it before the caller replaces the backup."""
        con = sqlite3.connect(path)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            con.executescript("""
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
                    is_individual INTEGER NOT NULL,
                    exclusion_reason TEXT
                );
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);

                CREATE INDEX idx_contributions_cmte ON contributions(cmte_id);
                CREATE INDEX idx_contributions_donor ON contributions(donor_id);
                CREATE INDEX idx_committees_cand ON committees(cand_id);
                CREATE INDEX idx_prepared_cand_nm ON prepared_analysis(cand_nm);
                CREATE INDEX idx_prepared_cmte_state ON prepared_analysis(cmte_id, contbr_st);
                CREATE INDEX idx_prepared_individual ON prepared_analysis(is_individual);
            """)
            for name in ["donors", "candidates", "committees", "contributions"]:
                serial = self.tables[name].copy()
                for col in serial.select_dtypes(include=["datetime64[ns]"]):
                    serial[col] = serial[col].astype(str)
                serial.to_sql(name, con, if_exists="append", index=False)
            if self.prepared is not None:
                serial = self.prepared.copy()
                for col in serial.select_dtypes(include=["datetime64[ns]"]):
                    serial[col] = serial[col].astype(str)
                serial["is_individual"] = serial["is_individual"].astype(int)
                serial.to_sql("prepared_analysis", con, if_exists="append", index=False)
            con.execute("INSERT OR REPLACE INTO metadata VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
            con.execute("INSERT OR REPLACE INTO metadata VALUES ('saved_utc', ?)", (datetime.now(timezone.utc).isoformat(),))
            violations = con.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(f"Backup would violate foreign-key integrity: {len(violations)} row(s).")
            con.commit()
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("New backup failed SQLite integrity_check.")
        finally:
            con.close()

    @_audited("restore")
    def restore_sqlite(self, path: str | Path) -> None:
        path = Path(path)
        if not path.is_file():
            raise ValueError(f"Backup file not found: {path}")
        try:
            con = sqlite3.connect(path)
        except sqlite3.Error as exc:
            raise ValueError(f"Backup could not be opened as a SQLite database: {exc}") from exc
        try:
            con.execute("PRAGMA foreign_keys=ON")
            try:
                names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            except sqlite3.DatabaseError as exc:
                raise ValueError(f"Backup file is not a valid SQLite database: {exc}") from exc
            missing_tables = set(REQUIRED) - names
            if missing_tables:
                raise ValueError(f"Backup missing required table(s): {', '.join(sorted(missing_tables))}")
            for name, columns in REQUIRED.items():
                existing_cols = {row[1] for row in con.execute(f'PRAGMA table_info("{name}")')}
                missing_cols = sorted(c for c in columns if c not in existing_cols and not (name == "contributions" and c == "contb_receipt_amt" and "contribution_amount" in existing_cols))
                if missing_cols:
                    raise ValueError(f"Backup table '{name}' missing required column(s): {', '.join(missing_cols)}")
            if "metadata" in names:
                row = con.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
                version = row[0] if row else None
                if version is not None and version not in SUPPORTED_SCHEMA_VERSIONS:
                    raise ValueError(f"Unsupported backup schema version: {version!r}")
            violations = con.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ValueError(f"Backup fails foreign-key integrity: {len(violations)} violation(s) found.")
            tables = {name: pd.read_sql_query(f'SELECT * FROM "{name}"', con) for name in REQUIRED}
            prepared = pd.read_sql_query('SELECT * FROM "prepared_analysis"', con) if "prepared_analysis" in names else None
        finally:
            con.close()

        if "contribution_amount" not in tables["contributions"].columns:
            raise ValueError("Backup's contributions table is missing the contribution_amount column.")
        numeric_amount = pd.to_numeric(tables["contributions"]["contribution_amount"], errors="coerce")
        if numeric_amount.isna().any():
            raise ValueError("Backup contains contribution amounts that cannot be read as numbers.")
        if pd.to_datetime(tables["contributions"]["contb_receipt_dt"], errors="coerce").isna().any():
            raise ValueError("Backup contains contribution dates that cannot be parsed.")
        if prepared is not None:
            if "is_individual" not in prepared.columns:
                raise ValueError("Backup's prepared analysis is missing the is_individual column.")
            prepared["contb_receipt_dt"] = pd.to_datetime(prepared["contb_receipt_dt"], errors="coerce")
            prepared["is_individual"] = prepared["is_individual"].astype(bool)

        self.tables, self.prepared = tables, prepared
        self.audit.add("restore", f"Restored and validated SQLite snapshot from {path}")

    @_audited("export")
    def export_outputs(self, folder: str | Path, metric: str = "contribution_amount") -> list[Path]:
        """Writes the analysis files. statistics.csv uses the summary metric selected in the GUI; the grouped
        tables and correlations are always based on contribution amounts."""
        if metric not in SUMMARY_METRICS:
            raise ValueError(f"Unsupported summary metric: {metric}")
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        stats = self.statistics(metric).rename("value").to_frame()
        stats.index.name = f"statistic ({metric})"
        outputs = {"statistics.csv": stats, "monthly_pivot.csv": self.pivot(),
                   "categorical_analysis.csv": self.categorical(), "category_amount_correlations.csv": self.correlation_matrix(),
                   "committee_state_analysis.csv": self.committee_state_analysis()}
        paths = []
        for filename, frame in outputs.items():
            path = folder / filename
            keep_index = filename in {"statistics.csv", "monthly_pivot.csv", "category_amount_correlations.csv"}
            frame.to_csv(path, index=keep_index)
            paths.append(path)
        self.audit.add("export", f"Exported {len(paths)} analysis file(s) to {folder}; summary metric={metric}; "
                                 "grouped outputs and correlations use contribution_amount", len(paths))
        audit_path = folder / "audit.jsonl"
        self.audit.save(audit_path)
        paths.append(audit_path)
        return paths