# Branch report

A 3-page CenterWell-branded PDF per branch showing how the branch's patients are doing and what the branch has to do this week. Spec and rules: [`docs/branch_report_spec.md`](../docs/branch_report_spec.md).

| File | Role |
|---|---|
| `aggregate.py` | Patient payloads + CSVs → one branch summary dict. **Shared with the Gradio app.** |
| `build_branch.py` | Summary → PDF (`<Branch>_report.pdf`) + `<branch>_summary.json` |
| `brand.py` | Booklet colours, assets, footer text and fonts. Reused by the app |

## Run (from the `project_alpha` folder)
```powershell
# 1. patient payloads + booklets (original pipeline)
cd cqi_booklet
..\.venv\Scripts\python src\build_payloads.py --as-of 2026-08-15 --sample
cd ..
# 2. branch reports
.venv\Scripts\python branch_report\build_branch.py --as-of 2026-08-15
```
Output goes to `branch_report\output\2026-08-15\`, which is git-ignored because it can contain PHI.

The code doesn't modify or import the original `cqi_booklet` code. It reads the pipeline's JSON output and the CSVs, and copies the booklet's constants, such as the cohort rule and the order-frequency parser, so the numbers match the booklet.
