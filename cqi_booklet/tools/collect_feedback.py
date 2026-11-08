"""Collect clinician feedback from returned (filled) CQI booklet PDFs into CSV / Excel.

    python tools/collect_feedback.py returned_pdfs/ --out feedback/2026-08-15

Reads every *.pdf in the folder (per-patient or merged branch booklets), finds the form fields
named cqi__<patient_id>__<episode_id>__<key>, and writes:
  feedback_findings.csv  one row per patient-episode x finding (rating, error types, comment)
  feedback_overall.csv   one row per patient-episode (decision, outcome, usefulness, reviewer, notes)
  feedback.xlsx          both sheets, if openpyxl is installed
Blank forms are skipped, so you can drop every returned file in one folder.
"""
import argparse, glob, os, re, sys
import pandas as pd
from pypdf import PdfReader

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from cqi_payload import FEEDBACK_FINDINGS, ERROR_TYPES

NAME = re.compile(r"^cqi__(?P<pid>[^_]+)__(?P<eid>[^_]+)__(?P<key>.+)$")
LABEL = {k: lab for k, lab, _ in FEEDBACK_FINDINGS}
OUTCOMES = {"out_plan": "Plan updated", "out_visits": "Visits rescheduled", "out_md": "MD notified",
            "out_nochange": "No change needed", "out_ds": "Refer to Data Science"}


def value(v):
    if v is None:
        return None
    v = str(v)
    if v in ("/Off", "Off", ""):
        return None
    return v[1:] if v.startswith("/") else v


def read_pdf(path):
    out = {}
    for name, f in (PdfReader(path).get_fields() or {}).items():
        m = NAME.match(name)
        if m:
            out.setdefault((m["pid"], m["eid"]), {})[m["key"]] = value(f.get("/V"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default="feedback")
    a = ap.parse_args()
    findings, overall = [], []
    for path in sorted(glob.glob(os.path.join(a.folder, "**", "*.pdf"), recursive=True)):
        for (pid, eid), f in read_pdf(path).items():
            if not any(f.values()):
                continue                                   # untouched form
            src = os.path.basename(path)
            for k, lab in LABEL.items():
                rating = f.get(f"{k}_rating")
                errs = [ERROR_TYPES[e] for e in "DICN" if f.get(f"{k}_err_{e}")]
                comment = f.get(f"{k}_comment")
                if rating or errs or comment:
                    findings.append({"patient_id": pid, "episode_id": eid, "finding_key": k, "finding": lab,
                                     "rating": rating, "error_types": "; ".join(errs), "comment": comment, "source_file": src})
            overall.append({"patient_id": pid, "episode_id": eid, "decision": f.get("decision"),
                            "action_owner": f.get("action_owner"), "due_date": f.get("due_date"), "reviewed_by": f.get("reviewed_by"),
                            "conference_outcome": "; ".join(lab for k, lab in OUTCOMES.items() if f.get(k)),
                            "usefulness_1to5": f.get("usefulness"), "overall_comment": f.get("overall_comment"),
                            "decision_notes": f.get("decision_notes"), "notes_discussion": f.get("notes_discussion"),
                            "notes_followups": f.get("notes_followups"), "notes_cqi": f.get("notes_cqi"),
                            "reviewer_name": f.get("reviewer_name"), "reviewer_role": f.get("reviewer_role"),
                            "review_date": f.get("review_date"), "source_file": src})
    os.makedirs(a.out, exist_ok=True)
    fdf, odf = pd.DataFrame(findings), pd.DataFrame(overall)
    fdf.to_csv(os.path.join(a.out, "feedback_findings.csv"), index=False)
    odf.to_csv(os.path.join(a.out, "feedback_overall.csv"), index=False)
    try:
        with pd.ExcelWriter(os.path.join(a.out, "feedback.xlsx")) as xw:
            fdf.to_excel(xw, sheet_name="findings", index=False); odf.to_excel(xw, sheet_name="overall", index=False)
    except ImportError:
        pass
    print(f"{len(odf)} completed forms, {len(fdf)} finding ratings -> {os.path.abspath(a.out)}")
    if len(fdf):
        print(fdf.groupby(["finding", "rating"]).size().unstack(fill_value=0).to_string())


if __name__ == "__main__":
    main()
