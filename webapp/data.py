"""Data layer for the Project Alpha app.

Everything comes from what the original pipeline already produced plus the branch roll-up:
  cqi_booklet/output/<run>/json/*.json   patient payloads (same objects the booklet PDFs render)
  branch_report.aggregate               branch summaries (same numbers as the branch PDF)
No other data sources.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys
from datetime import datetime
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CQI_OUT = os.path.join(ROOT, "cqi_booklet", "output")
sys.path.insert(0, os.path.join(ROOT, "branch_report"))
import aggregate  # noqa: E402

DECISIONS = {"agree": "Agree with CQI", "agree_changes": "Agree with changes", "disagree": "Disagree", "escalate": "Escalate to MD"}


def runs() -> list[str]:
    """CQI run dates that have patient payloads, newest first."""
    ds = [os.path.basename(os.path.dirname(d)) for d in glob.glob(os.path.join(CQI_OUT, "*", "json"))
          if glob.glob(os.path.join(d, "*.json"))]
    return sorted(ds, reverse=True)


class Run:
    def __init__(self, as_of: str):
        self.as_of = as_of
        self.json_dir = os.path.join(CQI_OUT, as_of, "json")
        self.patients: dict[str, dict] = {}
        self.stems: dict[str, str] = {}
        for f in sorted(glob.glob(os.path.join(self.json_dir, "*.json"))):
            with open(f, encoding="utf-8") as fh:
                p = json.load(fh)
            pid = p["patient"]["patient_id"]
            self.patients[pid] = p
            self.stems[pid] = os.path.splitext(os.path.basename(f))[0]
        self.branches = aggregate.build_branch_summaries(as_of, self.json_dir)
        # highest risk first, as in the booklet
        self.order = sorted(self.patients, key=lambda k: -(self.patients[k]["risk"]["score"] or 0))
        meta = next(iter(self.patients.values()))["meta"] if self.patients else {}
        self.conference_label = meta.get("conference_label", "")
        self.sample = bool(meta.get("sample"))
        self.model_version = meta.get("model_version", "")

    # ---------------- lookups
    def branch_of(self, pid):
        return aggregate.branch_key(self.patients[pid]["patient"]["branch_name"])

    def branch_ids(self):
        return sorted(self.branches)

    def find_branch(self, text):
        """'0412', 'branch 0412' -> 'BRANCH 0412'."""
        import re
        m = re.search(r"\b(\d{4})\b", text or "")
        if m:
            key = f"BRANCH {m.group(1)}"
            if key in self.branches:
                return key
        return None

    # ---------------- files
    def booklet(self, pid):
        p = os.path.join(CQI_OUT, self.as_of, "pdf", self.stems[pid] + ".pdf")
        return p if os.path.exists(p) else None

    def brief(self, pid):
        p = os.path.join(CQI_OUT, self.as_of, "pdf_brief", self.stems[pid] + "_brief.pdf")
        return p if os.path.exists(p) else None

    def branch_pdf(self, branch):
        import build_branch
        s = self.branches[branch]
        out = os.path.join(ROOT, "branch_report", "output", self.as_of)
        path = os.path.join(out, f"{s['branch_label'].replace(' ', '_')}_report.pdf")
        if not os.path.exists(path):
            build_branch.run(self.as_of, self.json_dir, out)
        return path if os.path.exists(path) else None

    # ---------------- conference decisions (booklet form field names)
    def decisions_path(self):
        return os.path.join(HERE, "output", self.as_of, "decisions.csv")

    def save_decision(self, pid, decision, action_owner, due_date, notes, reviewed_by):
        path = self.decisions_path(); os.makedirs(os.path.dirname(path), exist_ok=True)
        p = self.patients[pid]["patient"]
        row = {"saved_ts": datetime.now().isoformat(timespec="seconds"), "as_of_date": self.as_of, "branch": self.branch_of(pid),
               "patient_id": pid, "episode_id": p["episode_id"], "decision": decision, "action_owner": action_owner or "",
               "due_date": due_date or "", "notes_discussion": notes or "", "reviewed_by": reviewed_by or ""}
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row)); new and w.writeheader(); w.writerow(row)
        return row

    def decisions(self):
        path = self.decisions_path()
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        return {r["patient_id"]: r for r in rows}   # latest per patient wins


@lru_cache(maxsize=8)
def load(as_of: str) -> Run:
    return Run(as_of)
