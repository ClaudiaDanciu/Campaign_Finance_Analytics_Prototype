from __future__ import annotations
from pathlib import Path
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from campaign_finance import SUMMARY_METRICS, CampaignFinanceModel, format_statistics

ANALYSIS_VIEWS = ["Correlations", "Monthly candidate activity", "State-occupation-candidate analysis", "Committee-state analysis"]
# Session file used to keep the prepared state when the window closes and bring it back at the next start.
# It lives in the user's home folder (not the project folder) so contributor data never ends up in the
# submitted program zip. Override with the CAMPAIGN_FINANCE_SESSION environment variable if needed.
SESSION_PATH = Path(os.environ.get("CAMPAIGN_FINANCE_SESSION", Path.home() / ".campaign_finance_analytics" / "session.sqlite"))
NO_RESULTS = "No results yet for this data.\nChoose a summary metric and an analysis view, then select Run analysis."
METRIC_NOTE = "Summary metric changes the statistics only; tables and charts always show contribution amounts ($)."


class App(tk.Tk):
    def __init__(self, session_path: Path | None = SESSION_PATH, restore_session: bool = True):
        super().__init__(); self.title("Campaign Finance Analytics"); self.geometry("1120x720")
        self.model=CampaignFinanceModel(); self.state_name="EMPTY"; self.session_path=session_path
        self.metric=tk.StringVar(value=SUMMARY_METRICS[0]); self.view=tk.StringVar(value=ANALYSIS_VIEWS[0])
        self.status=tk.StringVar(value="Ready - load four CSV files or restore a backup.")
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if restore_session and session_path is not None:
            self.after(50, self.restore_session)

    # ---------- layout ----------
    def _build(self):
        # Row 1: data source and state. Row 2: analysis choices. Splitting the toolbar keeps every control
        # visible at 1120 px wide and groups controls by task (prepare the data, then analyse it).
        source=ttk.Frame(self,padding=(8,8,8,2)); source.pack(fill="x")
        ttk.Label(source,text="Data:",width=9).pack(side="left")
        self.load_btn=ttk.Button(source,text="Load CSV folder",command=self.load); self.load_btn.pack(side="left",padx=3)
        self.restore_btn=ttk.Button(source,text="Restore backup",command=self.restore); self.restore_btn.pack(side="left",padx=3)
        self.clean_btn=ttk.Button(source,text="Clean and prepare",command=lambda:self._task(self._clean,clear=True)); self.clean_btn.pack(side="left",padx=3)
        self.save_btn=ttk.Button(source,text="Save backup",command=self.save); self.save_btn.pack(side="left",padx=3)

        analysis=ttk.Frame(self,padding=(8,2,8,2)); analysis.pack(fill="x")
        ttk.Label(analysis,text="Analysis:",width=9).pack(side="left")
        ttk.Label(analysis,text="Summary metric:").pack(side="left",padx=(3,3))
        ttk.Combobox(analysis,textvariable=self.metric,values=SUMMARY_METRICS,state="readonly",width=20).pack(side="left")
        ttk.Label(analysis,text="Analysis view:").pack(side="left",padx=(12,3))
        ttk.Combobox(analysis,textvariable=self.view,values=ANALYSIS_VIEWS,state="readonly",width=32).pack(side="left")
        self.run_btn=ttk.Button(analysis,text="Run analysis",command=lambda:self._task(lambda m=self.metric.get(),v=self.view.get():self._analyse(m,v))); self.run_btn.pack(side="left",padx=(12,3))
        self.export_btn=ttk.Button(analysis,text="Export outputs",command=self.export); self.export_btn.pack(side="left",padx=3)
        ttk.Label(self,text=METRIC_NOTE,padding=(8,0,8,4),foreground="#444").pack(fill="x")
        # Packed before the results area so the feedback line always stays visible, however small the window.
        ttk.Label(self,textvariable=self.status,anchor="w",padding=8,wraplength=1080,relief="groove").pack(side="bottom",fill="x")

        body=ttk.Panedwindow(self,orient="horizontal"); body.pack(fill="both",expand=True,padx=8,pady=4)
        left=ttk.Frame(body); right=ttk.Frame(body); body.add(left,weight=2); body.add(right,weight=3)
        # "TkFixedFont" must be passed as the named font itself; a ("TkFixedFont", 10) tuple is read as an
        # unknown family and falls back to a proportional font, which misaligns the table columns.
        self.text=tk.Text(left,wrap="none",font="TkFixedFont")
        yscroll=ttk.Scrollbar(left,orient="vertical",command=self.text.yview)
        xscroll=ttk.Scrollbar(left,orient="horizontal",command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set,xscrollcommand=xscroll.set)
        self.text.grid(row=0,column=0,sticky="nsew"); yscroll.grid(row=0,column=1,sticky="ns"); xscroll.grid(row=1,column=0,sticky="ew")
        left.rowconfigure(0,weight=1); left.columnconfigure(0,weight=1)
        self.fig=Figure(figsize=(7,5)); self.canvas=FigureCanvasTkAgg(self.fig,master=right); self.canvas.get_tk_widget().pack(fill="both",expand=True)
        self._sync()

    # ---------- state handling ----------
    def _sync(self):
        busy=self.state_name=="BUSY"; prepared=self.state_name=="PREPARED"; loaded=self.state_name in {"LOADED","PREPARED"}
        for b in [self.load_btn,self.restore_btn]: b.configure(state="disabled" if busy else "normal")
        self.clean_btn.configure(state="normal" if loaded and not busy else "disabled")
        for b in [self.run_btn,self.export_btn,self.save_btn]: b.configure(state="normal" if prepared and not busy else "disabled")

    def _settled_state(self):
        return "PREPARED" if self.model.prepared is not None else ("LOADED" if self.model.tables else "EMPTY")

    def _task(self, fn, clear=False):
        """Runs one action synchronously (the prototype is single-threaded by design).
        On success, `clear=True` removes results that belonged to the previous data; on failure the
        existing usable state and results are kept and the error is reported."""
        self.state_name="BUSY"; self.status.set("Working..."); self._sync(); self.update_idletasks()
        try:
            payload=fn()
        except Exception as exc:
            self.state_name=self._settled_state()
            self.status.set(f"Action rejected: {exc}"); self._sync()
            messagebox.showerror("Campaign Finance Analytics",str(exc)); return False
        self.state_name=self._settled_state(); self.status.set(str(payload)); self._sync()
        if clear: self._clear_results()
        else: self._render()
        return True

    def _clear_results(self):
        """Old statistics and charts must not stay on screen after the underlying data changes."""
        if hasattr(self,"_view"): del self._view
        self.text.delete("1.0","end"); self.fig.clf(); self.canvas.draw()
        if self.state_name=="PREPARED": self.text.insert("end",NO_RESULTS)

    # ---------- actions ----------
    def load(self):
        folder=filedialog.askdirectory(title="Select folder containing four CSV files")
        if folder:
            def action():
                self.model.load_csv_folder(folder)
                rows=sum(len(v) for v in self.model.tables.values())
                return f"Loaded {rows:,} source rows across 4 files from {folder}. Next: Clean and prepare."
            self._task(action,clear=True)

    def restore(self):
        path=filedialog.askopenfilename(filetypes=[("SQLite backup","*.sqlite *.db")])
        if path: self._task(lambda:self._restore_from(path),clear=True)

    def _restore_from(self, path):
        self.model.restore_sqlite(path)
        if self.model.prepared is not None:
            return f"Backup restored from {path}: {len(self.model.prepared):,} prepared rows ({int(self.model.prepared['is_individual'].sum()):,} individual)."
        return f"Backup restored from {path}: relational tables only - select Clean and prepare before analysis."

    def restore_session(self):
        """Brings back the state saved when the program was last closed, if there is one."""
        if self.session_path is None or not Path(self.session_path).is_file(): return
        def action(): return "Previous session restored. " + self._restore_from(self.session_path)
        if not self._task(action,clear=True):
            self.status.set(f"Previous session could not be restored ({self.session_path}); load CSV files or restore a backup.")

    def on_close(self):
        """Saves the current state before closing so it can be restored at the next start."""
        if self.session_path is not None and self.model.prepared is not None:
            try:
                self.model.save_sqlite(self.session_path)
            except Exception as exc:
                if not messagebox.askyesno("Session not saved", f"The current state could not be saved:\n{exc}\n\nClose anyway?"):
                    self.status.set(f"Close cancelled: session save failed ({exc})."); return
        elif self.model.tables:
            # Raw, uncleaned tables do not fit the relational schema yet, so they are not saved; say so first.
            if not messagebox.askyesno("Data not cleaned", "The loaded files have not been cleaned, so this state will not be saved.\n\nClose anyway?"):
                self.status.set("Close cancelled - select Clean and prepare so the session can be saved."); return
        self.destroy()

    def _clean(self):
        diag=self.model.clean_and_prepare()
        removed=diag["blank_identifier_removed"]+diag["invalid_amount_or_date_removed"]+diag["orphan_committee_removed"]+diag["unlinked_donor_or_candidate_removed"]
        reasons=", ".join(f"{k} {v:,}" for k,v in diag["exclusion_reason_counts"].items()) or "none"
        return (f"Cleaned {diag['prepared_rows']+removed:,} source rows. Removed {removed:,} invalid/unlinked "
                f"({diag['blank_identifier_removed']:,} blank IDs, {diag['invalid_amount_or_date_removed']:,} bad amount/date, "
                f"{diag['orphan_committee_removed']:,} unmatched committees, {diag['unlinked_donor_or_candidate_removed']:,} unlinked donor/candidate). "
                f"Excluded {diag['non_individual_excluded']:,} non-individual transactions ({reasons}). "
                f"{diag['individual_rows']:,} individual-donor rows ready for analysis.")

    def _analyse(self, metric, view):
        stats=self.model.statistics(metric)
        if view=="Correlations": data=self.model.correlation_matrix()
        elif view=="Monthly candidate activity": data=self.model.pivot()
        elif view=="State-occupation-candidate analysis": data=self.model.categorical()
        elif view=="Committee-state analysis": data=self.model.committee_state_analysis()
        else: raise ValueError(f"Unknown analysis view: {view}")
        self._view=(view,metric,stats,data)
        message=f"{view} complete. Summary statistics use '{metric}'; the table and chart use contribution amounts."
        if view=="Correlations":
            message+=" Correlations describe association, not causation."
            undefined=int(data["contribution_amount"].isna().sum())
            if undefined: message+=f" {undefined} constant categor{'y' if undefined==1 else 'ies'} with undefined r not charted."
        return message

    def _format_stats(self, stats, metric):
        return format_statistics(stats, metric)

    def _shorten(self, label, width=30):
        text=str(label).replace("state_","State: ").replace("occupation_","Occ: ")
        return text if len(text)<=width else text[:width-1]+"…"

    def _render(self):
        if not hasattr(self,"_view"): return
        view,metric,stats,data=self._view
        table=data.to_string() if view in {"Correlations","Monthly candidate activity"} else data.to_string(index=False)
        self.text.delete("1.0","end")
        self.text.insert("end", self._format_stats(stats,metric)+f"\n\n{view.upper()} (contribution amounts)\n\n"+table)
        self.fig.clf()
        ax=self.fig.add_subplot(111)
        if view=="Correlations": self._render_correlations(ax,data)
        elif view=="Monthly candidate activity": self._render_monthly(ax,data)
        elif view=="State-occupation-candidate analysis":
            self._render_top_bar(ax,data,["contbr_st","contbr_occupation","cand_nm"],"total_amount","State x occupation x candidate totals")
        elif view=="Committee-state analysis":
            self._render_top_bar(ax,data,["cand_nm","cmte_id","contbr_st"],"total_amount","Candidate x committee x state totals")
        self.fig.tight_layout(); self.canvas.draw()

    def _render_correlations(self, ax, corr, top_n=15):
        values=corr["contribution_amount"].dropna()
        top=values.abs().sort_values(ascending=False,kind="stable").head(top_n).index
        ordered=values.loc[top].sort_values(kind="stable")
        labels=[self._shorten(i) for i in ordered.index]
        colors=["#c0392b" if v<0 else "#2471a3" for v in ordered.values]
        ax.barh(labels,ordered.values,color=colors)
        ax.axvline(0,color="black",linewidth=0.8)
        ax.set_xlabel("Pearson r with amount\n(association, not causation)",fontsize=9)
        ax.set_title(f"Category associations with amount\n{len(ordered)} shown (up to {top_n} strongest by |r|)",fontsize=10)
        ax.tick_params(axis="y",labelsize=8)

    def _render_monthly(self, ax, pivot, top_n=6):
        top=pivot.sum().sort_values(ascending=False).head(top_n).index
        for cand in sorted(top):
            ax.plot(pivot.index,pivot[cand],marker="o",label=self._shorten(cand,20))
        ax.set_title(f"Monthly contribution totals by candidate\n{len(top)} shown (up to {top_n} largest)",fontsize=10)
        ax.set_xlabel("Month (0 = no included contribution that month)"); ax.set_ylabel("Total contribution amount ($)")
        ax.tick_params(axis="x",rotation=45,labelsize=8); ax.legend(fontsize=7)

    def _render_top_bar(self, ax, df, label_cols, value_col, title, top_n=15):
        top=df.sort_values(value_col,ascending=False,kind="stable").head(top_n).iloc[::-1]
        labels=[self._shorten(" | ".join(str(row[c]) for c in label_cols),34) for _,row in top.iterrows()]
        ax.barh(labels,top[value_col],color="#2471a3")
        ax.set_xlabel(f"{value_col.replace('_',' ').title()} ($)")
        ax.set_title(f"{title}\n{len(top)} shown (up to {top_n} largest)",fontsize=10); ax.tick_params(axis="y",labelsize=7)

    def save(self):
        path=filedialog.asksaveasfilename(defaultextension=".sqlite",filetypes=[("SQLite backup","*.sqlite")])
        if path:
            try:
                self.model.save_sqlite(path)
            except Exception as exc:
                self.status.set(f"Save rejected: {exc}. Any previous backup at this location was left unchanged.")
                messagebox.showerror("Save failed",f"{exc}\n\nAny previous backup at this location was left unchanged."); return
            self.status.set(f"Backup saved to {path}; the current state can be restored later.")
            messagebox.showinfo("Backup saved",f"Backup saved to:\n{path}")

    def export(self):
        folder=filedialog.askdirectory(title="Select output folder")
        if folder:
            metric=self.metric.get()
            try:
                paths=self.model.export_outputs(folder,metric=metric)
            except Exception as exc:
                self.status.set(f"Export rejected: {exc}")
                messagebox.showerror("Export failed",str(exc)); return
            self.status.set(f"Exported {len(paths)} files to {folder} (statistics use '{metric}').")
            messagebox.showinfo("Export complete", f"Created {len(paths)} files in {folder}\n(statistics.csv uses '{metric}'):\n"+"\n".join(p.name for p in paths))

if __name__=="__main__": App().mainloop()
