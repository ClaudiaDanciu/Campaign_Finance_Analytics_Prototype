"""Benchmark for report Section 1.1 (theoretical concurrency redesign). NOT part of the prototype.

Compares three ways of generating one monthly report per candidate while a Tkinter event loop runs:
  1. Sequential   - the prototype's design: all reports run inside one Tk callback (blocks the loop).
  2. Threads      - ThreadPoolExecutor(4); the GUI thread submits tasks and polls futures with after(100).
  3. Processes    - ProcessPoolExecutor(2); same polling, but the snapshot is pickled into each task.

For each design and data scale it records:
  total_ms  - time from submitting the work to the last report finishing (future done-callback / loop end).
  freeze_ms - the longest gap between two ticks of a 10 ms after() ticker during the run. Because the ticker
              itself fires every ~10 ms, an idle loop already shows ~10-12 ms, so freeze can exceed total.

Usage (from the project folder, with the real dataset extracted OUTSIDE the project folder):
  python benchmark_concurrency.py --data "/path/to/AAP OL4 25 26 Reassessment data set"
Only the failure/memory checks:  python benchmark_concurrency.py --data "..." --checks-only
Options: --runs 10  --scales 1 10  --results benchmark_results.csv  --plot benchmark_gaps.png
Without --data it uses the fictional demo data (useful only to check the script works).
"""
from __future__ import annotations

import argparse
import csv
import platform
import statistics
import sys
import tempfile
import time
import tkinter as tk
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from campaign_finance import CampaignFinanceModel

TICK_MS = 10
POLL_MS = 100
SNAPSHOT_COLS = ["cand_nm", "month", "contribution_amount"]


def candidate_report(snap: pd.DataFrame, cand: str, out_dir: str) -> tuple[str, int]:
    """One task: filter one candidate, aggregate by month, write that candidate's own file (no shared state)."""
    rows = snap[snap["cand_nm"] == cand]
    monthly = rows.groupby("month")["contribution_amount"].agg(["size", "sum"])
    safe = "".join(c if c.isalnum() else "_" for c in cand)
    monthly.to_csv(Path(out_dir) / f"{safe}.csv")
    return cand, len(rows)


def failing_report(snap: pd.DataFrame, cand: str, out_dir: str) -> tuple[str, int]:
    """Same as candidate_report, but raises for one candidate: used to test failure isolation."""
    raise RuntimeError(f"simulated failure for {cand}")


def run_checks(base: pd.DataFrame, cands: list[str], threads: int) -> None:
    """Section 1.1 claims: (a) one failing task does not stop the others; (b) memory used by the snapshot."""
    print("\n--- Failure-isolation check (ThreadPoolExecutor) ---")
    log = []
    with tempfile.TemporaryDirectory() as out_dir, ThreadPoolExecutor(threads) as pool:
        futures = {pool.submit(failing_report if i == 0 else candidate_report, base, c, out_dir): c
                   for i, c in enumerate(cands)}
        done = 0
        for f, cand in futures.items():
            try:
                f.result(); done += 1
            except Exception as exc:
                log.append(f"report_failed {cand}: {exc}")
        written = len(list(Path(out_dir).glob("*.csv")))
    print(f"Tasks submitted: {len(cands)} | completed: {done} | files written: {written} | failures logged: {len(log)}")
    for line in log:
        print("  audit:", line)
    print("\n--- Snapshot memory (three columns, deep) ---")
    for scale in (1, 10):
        snap = pd.concat([base] * scale, ignore_index=True) if scale > 1 else base
        print(f"{scale:>3}x: {len(snap):,} rows, {snap.memory_usage(deep=True).sum() / 1e6:.1f} MB")


def _noop(x=None):
    return x


class Harness:
    """Runs one design inside a live Tk event loop and measures total time and the longest loop gap."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.ticks: list[float] = []
        self._tick()

    def _tick(self):
        self.ticks.append(time.perf_counter())
        self.root.after(TICK_MS, self._tick)

    def idle(self, ms: int):
        end = time.perf_counter() + ms / 1000
        while time.perf_counter() < end:
            self.root.update()
            time.sleep(0.001)

    def run(self, design: str, snap: pd.DataFrame, cands: list[str], out_dir: str, pool) -> dict:
        state = {"t0": None, "last_done": None, "finished": False, "errors": 0}

        def start():
            state["t0"] = time.perf_counter()
            if design == "sequential":
                for c in cands:
                    candidate_report(snap, c, out_dir)
                state["last_done"] = time.perf_counter()
                state["finished"] = True
                return
            futures = []
            for c in cands:
                f = pool.submit(candidate_report, snap, c, out_dir)
                f.add_done_callback(lambda _f: state.__setitem__("last_done", time.perf_counter()))
                futures.append(f)

            def poll():
                pending = [f for f in futures if not f.done()]
                for f in futures:
                    if f.done() and f.exception() is not None:
                        state["errors"] += 1
                if pending:
                    self.root.after(POLL_MS, poll)
                else:
                    state["finished"] = True
            self.root.after(POLL_MS, poll)

        self.idle(150)                        # settle, so the loop is ticking normally before the run
        first_tick = len(self.ticks)
        self.root.after(0, start)
        while not state["finished"]:
            self.root.update()
            time.sleep(0.0005)
        self.idle(50)                         # capture the tick that ends the last gap
        # include the last tick before the work started, so a gap caused by blocking the loop is measured
        before = [t for t in self.ticks[:first_tick] if t <= state["t0"]]
        window = before[-1:] + [t for t in self.ticks[first_tick:] if t > (before[-1] if before else 0)]
        gaps = [b - a for a, b in zip(window, window[1:])]
        return {"total_ms": (state["last_done"] - state["t0"]) * 1000,
                "freeze_ms": max(gaps) * 1000 if gaps else 0.0,
                "errors": state["errors"],
                "gap_trace": [((a - state["t0"]) * 1000, (b - a) * 1000) for a, b in zip(window, window[1:])]}


def load_snapshot(data: str | None) -> pd.DataFrame:
    m = CampaignFinanceModel()
    if data:
        m.load_csv_folder(data)
    else:
        from demo_data import write
        tmp = tempfile.mkdtemp()
        write(tmp)
        m.load_csv_folder(tmp)
    m.clean_and_prepare()
    return m.individual_data()[SNAPSHOT_COLS].copy()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="folder with the four CSV files (omit to use fictional demo data)")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--scales", type=int, nargs="+", default=[1, 10])
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--checks-only", action="store_true", help="run only the failure and memory checks")
    ap.add_argument("--processes", type=int, default=2)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    ap.add_argument("--results", default=f"benchmark_results_{stamp}.csv")
    ap.add_argument("--plot", default=f"benchmark_gaps_{stamp}.png", help="event-loop gap plot at the largest scale ('' to skip)")
    args = ap.parse_args()

    base = load_snapshot(args.data)
    cands = sorted(base["cand_nm"].unique())
    print(f"Machine: {platform.platform()} | CPUs: {__import__('os').cpu_count()} | Python {platform.python_version()} "
          f"| pandas {pd.__version__} | Tk {tk.TkVersion}")
    print(f"Snapshot: {len(base):,} individual rows, {len(cands)} candidates, {args.runs} runs per cell"
          + ("" if args.data else "  [DEMO DATA - not for the report]"))

    run_checks(base, cands, args.threads)
    if args.checks_only:
        return
    print()
    root = tk.Tk(); root.title("benchmark"); root.geometry("200x50")
    h = Harness(root)
    rows, median_traces = [], {}
    with tempfile.TemporaryDirectory() as out_dir, \
            ThreadPoolExecutor(args.threads) as tpool, ProcessPoolExecutor(args.processes) as ppool:
        # Persistent pools are warmed up once (the design keeps them alive), so start-up cost is not timed.
        list(tpool.map(_noop, range(args.threads))); list(ppool.map(_noop, range(args.processes)))
        designs = [("Sequential (prototype)", "sequential", None),
                   (f"ThreadPoolExecutor({args.threads})", "threads", tpool),
                   (f"ProcessPoolExecutor({args.processes})", "processes", ppool)]
        for scale in args.scales:
            snap = pd.concat([base] * scale, ignore_index=True) if scale > 1 else base
            for label, design, pool in designs:
                results = [h.run(design, snap, cands, out_dir, pool) for _ in range(args.runs)]
                tot = statistics.median(r["total_ms"] for r in results)
                frz = statistics.median(r["freeze_ms"] for r in results)
                errs = sum(r["errors"] for r in results)
                rows.append({"design": label, "scale": f"{scale}x", "rows": len(snap),
                             "median_total_ms": round(tot, 1), "median_freeze_ms": round(frz, 1), "errors": errs})
                print(f"{label:26} {scale:>3}x  total {tot:7.1f} ms   freeze {frz:7.1f} ms" + (f"  errors {errs}" if errs else ""))
                # keep the run whose freeze is closest to the median, for the plot
                median_traces[(design, scale)] = min(results, key=lambda r: abs(r["freeze_ms"] - frz))["gap_trace"]
    root.destroy()

    with open(args.results, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"Results written to {Path(args.results).resolve()}")
    print("\nCopy this table (same run as the plot):")
    print(Path(args.results).read_text())

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        scale = max(args.scales)
        fig, ax = plt.subplots(figsize=(8, 3.2))
        for design, label in [("sequential", "Sequential (prototype)"), ("threads", f"ThreadPoolExecutor({args.threads})")]:
            xs, ys = zip(*median_traces[(design, scale)])
            ax.step(xs, ys, where="post", label=label)
        ax.axhline(100, linestyle="--", linewidth=0.8, color="grey"); ax.text(0, 102, "100 ms perception limit", fontsize=7)
        ax.set_xlabel("Time since reports started (ms)"); ax.set_ylabel("Event-loop gap (ms)")
        ax.set_title(f"Event-loop gaps at {scale}x the data (median run)", fontsize=10); ax.legend(fontsize=8)
        ax.set_ylim(0, max(110, ax.get_ylim()[1]))
        fig.text(0.99, 0.01, f"run {Path(args.results).stem.replace('benchmark_results_', '')}", ha="right", fontsize=6, color="grey")
        fig.tight_layout(); fig.savefig(args.plot, dpi=200)
        print(f"Plot written to {Path(args.plot).resolve()}")


if __name__ == "__main__":
    sys.exit(main())
