# Project Alpha

CQI Patient Conference tools:
- **`config/project_alpha.yaml`**: **the one file to edit**. It sets where the data and outputs live (laptop or Databricks Volume), which AI model answers questions (`llm.provider: gemini | anthropic | gateway | rules`, plus the gateway URL, model and key env vars), the CQI agent's thresholds, and the scope filter and prompts.
- **`agent/cqi_agent.py`**: the weekly **CQI care-gap agent**. It scans every active episode (sense, detect, prioritise, explain, publish) and remembers last week's findings. See `docs/databricks.md`.
- **`databricks.yml` + `databricks/`**: the Databricks Asset Bundle. It defines two weekly jobs, both reading Unity Catalog tables or parquet through the dataset registry:
  - **full job:** the original booklet notebook, then the agent and branch reports;
  - **agent-only job:** pandas + pyyaml only, no PDF or AI libraries.

  Both first load the conference decisions uploaded from the app into `hsa.cqi_conference_decisions`, so the agent knows each patient's last decision.
- **`cqi_booklet/`**: the original CQI booklet pipeline (CSVs → payload JSON → fillable PDF booklets). Copied from `cqi_booklet (1).zip` and treated as the reference implementation. It has **two small changes**, both in `src/build_booklet.py`:
  - `_register_fonts()` also finds Arial on Windows and macOS, marked `[Project Alpha local-run fix]`. The original only looked in Linux / Databricks font folders.
  - `generated_footer()` adds "Report generated <date> from Project Alpha – Clinical Quality Intelligence" to every page, marked `[Project Alpha]`. The text is `generated_by` in `config/branding.yaml`.
- **`branch_report/`**: a branch-level roll-up. It produces a 3-page branded PDF per branch on how its patients are doing and what the branch must do this week, plus a summary JSON for the app. See `branch_report/README.md` and `docs/branch_report_spec.md`.
- **`webapp/`**: the Project Alpha Gradio app. It is search-first: ask one question and the answer opens below it. Run it with `.venv\Scripts\python webapp\app.py` and open http://localhost:7871. See `webapp/README.md`.
- **`docs/USER_GUIDE.md`**: the **user guide** for clinicians and coordinators: asking questions, reading answers, discharge readiness, recording decisions, downloads, a suggested conference flow, and troubleshooting.
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

CQI agent (the app's splash and home screen show its results):
```powershell
.venv\Scripts\python agent\cqi_agent.py --as-of 2026-08-15
```
Output: `agent\output\2026-08-15\opportunities.json|.csv`, `agent_brief.md` and `agent_run.json`. Run it for an earlier date first (`--as-of 2026-08-08`) to see new, ongoing and resolved findings.

Databricks: see **`docs/databricks.md`**. It covers the weekly job, switching to Unity Catalog tables or parquet, demoing on your laptop from synced outputs, and changing the AI model.

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
