# Project Alpha

CQI Patient Conference tools:
- **`cqi_booklet/`**: the original CQI booklet pipeline (CSVs → payload JSON → fillable PDF booklets). Copied from `cqi_booklet (1).zip` and treated as the reference implementation. It has **one local-run fix**: `_register_fonts()` in `src/build_booklet.py` now also finds Arial on Windows and macOS. The original only looked in Linux / Databricks font folders. The fix is marked `[Project Alpha local-run fix]`.
- **`branch_report/`**: a branch-level roll-up. It produces a 3-page branded PDF per branch on how its patients are doing and what the branch must do this week, plus a summary JSON for the app. See `branch_report/README.md` and `docs/branch_report_spec.md`.
- **`webapp/`**: the Project Alpha Gradio app. It is search-first: ask one question and the answer opens below it. Run it with `.venvScriptspython webapppp.py` and open http://localhost:7871. See `webapp/README.md`.
- **`docs/cqi_review_and_webapp_plan.md`**: the review of the zip and PDF, plus the web app plan.

## Generate the PDF booklets (original pipeline)

One-time setup. A `.venv` is already created and the requirements are installed. To recreate it:
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r cqi_booklet\requirements.txt
```

Build the booklets from the sample CSVs (run from the `cqi_booklet` folder):
```powershell
cd cqi_booklet
..\.venv\Scripts\python src\build_payloads.py --as-of 2026-08-15 --sample
```

Output goes to `cqi_booklet\output\2026-08-15\`:
| Path | What |
|---|---|
| `pdf\<patient>.pdf` | Full fillable booklet per patient (9000123, 9000231, 9000345) |
| `pdf\_BRANCH_<branch>.pdf` | Branch overview + every booklet |
| `pdf_brief\` | 3-page short summaries |
| `json\*.json` | Payloads: the shared data layer the web app will read |
| `qc.csv` | Validation and text-length warnings |

Options:
- `--format full|brief|both`
- Leave out `--sample` for real data. This also removes the "Sample data" footer.

Branch reports (after the booklets, from the `project_alpha` folder):
```powershell
.venv\Scripts\python branch_report\build_branch.py --as-of 2026-08-15
```
Output: `branch_report\output\2026-08-15\Branch_<nnnn>_report.pdf` and `branch_<nnnn>_summary.json`.

Checks:
```powershell
..\.venv\Scripts\python tests\test_pipeline.py
..\.venv\Scripts\python tools\check_sync.py
```

Collect returned feedback forms:
```powershell
..\.venv\Scripts\python tools\collect_feedback.py <folder of returned PDFs> --out feedback\2026-08-15
```

See `cqi_booklet\README.md` for the full pipeline documentation (pages, rules, Databricks notebooks).

## Notes
- **Data:** the CSVs in `cqi_booklet\data\csv` are synthetic. Generated PDFs, payloads and AI logs can contain PHI on real data, and `output/` is git-ignored.
- **Environment:** this was installed on Python 3.14 with pandas 3.1 and ReportLab 5.1. The original code only requires `pandas>=2.0` and `reportlab>=4.0`. If the build fails on a pandas 3 change, pin `pandas<3` and reinstall.
