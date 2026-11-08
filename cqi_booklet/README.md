# CQI Patient Conference Booklet
**Clinical Quality Intelligence (CQI) - CenterWell Home Health**

This folder turns CQI outputs and Lighthouse data into a printable booklet for each high-risk or rising-risk patient. It reads CSV files today and tables or lake paths later. You switch sources in config, not code.

```
cqi_booklet/
├── config/
│   ├── datasets.yaml              # dataset registry: columns, types, csv / table / path for each dataset
│   └── cqi_booklet_schema.json    # payload contract (schema 1.1)
├── src/
│   ├── cqi_datasets.py            # Datasets.load() / check() / snapshot_to_csv(); CohortPandasView for Spark
│   ├── build_payloads.py          # datasets -> payload JSON -> PDF (CLI + run() used by the notebook)
│   ├── cqi_payload.py             # compose (rule-based page-1 text), validate, to_render
│   └── build_booklet.py           # PDF renderer (CenterWell flyer template styling)
├── assets/                        # logo, leaf pattern, swoosh, icons from the CenterWell flyer template
├── data/csv/                      # SYNTHETIC sample data (canonical column names)
├── notebooks/
│   ├── 00_datasets_setup.py       # Databricks: check sources, mix csv/table/path, snapshot tables -> CSV
│   └── 01_build_booklets.py       # Databricks: weekly build -> Volume PDFs + Delta payload/QC tables
├── tools/make_sample_csvs.py      # regenerates data/csv
├── tools/collect_feedback.py      # returned fillable PDFs -> feedback CSV / Excel
├── tools/check_sync.py            # data model / pipeline / renderer / LLM spec / docs consistency (148 checks)
├── tests/test_pipeline.py         # smoke tests incl. long / empty summaries
├── samples/                       # sample booklet PDF + payload JSON
└── output/                        # generated (git-ignored, may contain PHI)
```

## Run locally
```bash
pip install -r requirements.txt
python src/cqi_datasets.py                              # where each dataset comes from + quality checks
python src/build_payloads.py --as-of 2026-08-15 --sample
#  -> output/2026-08-15/json/*.json, pdf/*.pdf, pdf/_BRANCH_*.pdf, qc.csv
python tests/test_pipeline.py
python tools/check_sync.py        # run after any change to datasets.yaml, schema, LLM spec or pages
```

## Run on Databricks
1. Put the folder in a Repo or Workspace files and set `ROOT` in both notebooks.
2. Run `00_datasets_setup` to check every source. Fix anything marked `VERIFY` in `datasets.yaml`.
3. Run `01_build_booklets` with these widgets:
   - **as_of_date:** the CQI run date.
   - **source:** `auto` reads each dataset's table if one is configured, otherwise its lake path.
   - **branches:** an optional pilot list.

   The cohort and percentile are calculated in Spark. Only cohort rows go to pandas, and they go through the same `build_payloads.run()` as the local run, so there is one set of logic to maintain.

## Outputs per run
| File | What it is | For |
|---|---|---|
| `pdf/_BRANCH_<branch>.pdf` | "In their own words" + vitals action-window overview, then every full booklet (highest risk first) | Clinical Quality conference |
| `pdf_brief/_BRANCH_<branch>_brief.pdf` | The same overview, then a **3-page short summary** per patient | Customers / leadership |
| `pdf/<patient>.pdf` | Full booklet, 23 pages, fillable feedback | Clinical review |
| `pdf_brief/<patient>_brief.pdf` | Cover, At a glance, Vitals action window (no forms) | Short summary |

CLI: `python src/build_payloads.py --sample --format both|full|brief`. In Databricks, use the `format` widget.

## Branch overview (front of each branch PDF)
1. **In their own words:** one card per patient with the patient-stated goal (care-plan wording, unchanged), the risk tier, and "What CQI found" (`glance_found`). 8 cards per page.
2. **Vitals trajectory turns weekly readings into an action window:** a 4-step chevron (context → trajectory → urgency → action window), then one card per patient with *What CQI saw*, *What CQI flags* and an action-window chip. Ordered deteriorating → insufficient data → stable → improving. 9 per page.

## Discharge or recertification page (conditional)
Episodes within 14 days of their end date get an extra page right after the summary, so the full booklet is 24 pages for them and 23 for everyone else. The page is also added to the short summary for those episodes. It shows:
- **System recommendation:** Discharge, Discharge with referrals, Recertify, or Clinical review (mixed signals), with a confidence label and a rationale.
- **Supports recertification / Supports discharge:** the signals on each side, from goals met or not met, vitals trajectory, open or healed wounds, risk tier, adverse events, OASIS function change (M18xx, M1242, M2020 and others: lower code = better), prognosis and rehab potential.
- **Check first:** data caveats such as no recent vitals or open data gaps.
- **Next steps:** a discharge checklist (CW PCO / CW Rx referrals, PCP follow-up within 7 days, med reconciliation, discharge OASIS) or a recert checklist (recert OASIS in the last 5 days, carry over unmet goals, update frequencies).

Rules live in `cqi_payload.discharge_recommendation()`. An LLM `discharge_recommendation` / `discharge_rationale` replaces the rule-based label and rationale when present. The summary banner, At a glance and the discharge action all show the recommendation.

## Pages that only print when they apply
`CONDITIONAL_PAGES` in `src/cqi_payload.py` and `applicable_pages()` decide them. Page numbers, contents, cross-references and feedback rows all close up automatically.

| Page | Prints when |
|---|---|
| Discharge or recertification | Active episode within 14 days of its end date |
| Wounds | The patient has at least one wound record |
| Medication adherence | Medication profile or understanding / teaching / adherence data exists |

The sample booklets are 22 pages (no wounds, meds or discharge), 24 pages, and 25 pages (all three).

## Medication adherence page
Adherence is the umbrella: does the patient **understand**, **get taught about**, and **take** their medications as prescribed, with extra attention on high-risk drugs.
- **Data:**
  - `medications` dataset: name, class, high-risk flag. A blank flag falls back to `HIGH_RISK_CLASSES`.
  - Three per-visit columns: `med_understanding`, `med_teaching` and `med_adherent`, mapped to `MED_UNDERSTANDING_PERFORMED`, `MED_TEACHING_PERFORMED` and `MED_TAKEN_AS_PRESCRIBED`. All three are marked VERIFY.
- **Shows:**
  - Counts: active meds, high-risk meds, visits with an understanding check, and visits with an adherence concern.
  - The high-risk medication list.
  - A visit table: understanding assessed / teaching done / taking as prescribed.
- **Flag:** any visit documenting an adherence concern, or high-risk meds with no understanding check in 14 days (`MED_CHECK_DAYS`). The flag adds a teach-back action on the summary page.

## Conference decision
The decision box moved off the summary page and onto the **Conference decision and notes (fillable)** page. It now sits next to the feedback form, so all the fillable pages are together. Field names are unchanged, so `collect_feedback.py` still works.

## Product vision
Edit `VISION_SHORT`, `VISION_TODAY` and `VISION_PROD` at the top of `src/build_booklet.py`. They appear on the cover (one line) and in the "Where CQI is going" panel on page 2.

## Labels and footer text: no code changes needed
Edit `config/branding.yaml`, or point `CQI_BRANDING` at another file:
- `stage_label`: "Proof of concept" on every page footer. Set it to `""` after go-live.
- `sample_label`: "Sample data" appears **only** when you run with `--sample` (the synthetic CSVs). **Real runs don't pass `--sample`, so the label never shows.** The Databricks notebook never sets it.
- `footer_note`, `prepared_for`, `produced_by`, and the vision text on page 2.

The cover shows only the privacy line, with no page number and no stage label.

## Booklet (23 pages; 24 when nearing discharge)
| Page | Content |
|---|---|
| 1-3 | Cover, What is CQI?, Inside this booklet |
| 4 | At a glance (adds a care network line for discharge) |
| 5 | Patient conference summary (**fillable** decision) |
| 6-7 | CQI summaries: summaries + data gaps |
| 8-9 | A: Patient context and profile; episode timeline and risk drivers |
| 10 | **A: Diagnoses and procedures**: primary diagnosis, codes, body system, clinical group, onset vs. exacerbation; procedures |
| 11 | **A: Wounds**: wound inventory (location, type, stage, size first → latest, % area change, healing status, last measured), descriptions |
| 12 | **A: OASIS**: key risk items first (M1033, M1400, M18xx, M2020, M1242, M17xx, M1100, M2102...), start of care vs. latest, changes highlighted |
| 13-14 | B: Visit grid; missed care and events |
| 15-16 | C: Vitals action window; vitals trends |
| 17-19 | D: Patient goals; E: Care delivered; F: Care plan gaps |
| 20 | **G: Care network and discharge referrals**: PCP, CW PCO / CW Rx enrollment, nearest CW PCO clinic, clinics within 10 and 20 miles, **Refer to CW PCO / CW Rx indicators** |
| 21 | Clinician feedback (fillable) |
| 22-23 | How CQI works; conference notes (fillable) |

### Referral indicators (`cqi_payload.referral_indicators`)
| Indicator | CW PCO | CW Rx |
|---|---|---|
| **Refer** | Not enrolled, plan-eligible, clinic within 10 mi | Not enrolled, plan-eligible, 5+ active medications |
| **Consider** | Nearest clinic 10-20 mi | - |
| **Already enrolled** | Send discharge summary to CW PCO | Share discharge medication list |
| **Not indicated** | No clinic within 20 mi, or not eligible | Under 5 medications, or not eligible |
| **Check eligibility** | Eligibility unknown | Eligibility or med count unknown |

Thresholds are `PCO_NEAR_MI`, `PCO_FAR_MI` and `RX_MIN_MEDS`. The indicators appear on page 20, in the discharge banner (when the episode is within 14 days of its end), on At a glance, and in the discharge action.

### New data (schema 1.5)
| Dataset | Source (VERIFY) | Payload |
|---|---|---|
| `wounds` | WCI_NAME, WCI_WOUNDCLASS, CW_EXTENDEDWOUNDTYPE, ALI_SHORTNAME, WTI_DESCRIPTION + measurements | `clinical.wounds` |
| `diagnoses` | diag_prod_cd, diag_category, clinical_group, diag_or_proc, onset_exacer_flg/dt, primary_diag_ind | `clinical.diagnoses` |
| `oasis` | `hsa.cqi_oasis_items`, flattened from `oasis_event_desc` JSON in notebook 00 | `clinical.oasis` |
| `care_network` | PCP, MA eligibility, CW PCO / CW Rx enrollment, clinic distance, active med count | `care_network` |
| `llm_summary` | `wound_summary`, `diagnosis_summary`, `oasis_summary` | `clinical.*_summary` |

### New data for At a glance and the vitals action window (schema 1.4)
| Payload | LLM column (`llm_summary`) | Fallback when empty |
|---|---|---|
| `at_a_glance.found` | `glance_found` | First care and vitals tile points |
| `at_a_glance.risk` / `care_delivery` / `trajectory` / `flags` | `glance_risk`, `glance_care_delivery`, `glance_trajectory`, `cqi_flags` | Risk drivers / care tile / vitals saw+flags / top 2 actions |
| `vitals.trajectory.class` | `vitals_trajectory_class` | `deteriorating` if any measure is urgent this week or sooner, else `stable`; `insufficient_data` with no readings |
| `vitals.trajectory.saw` / `flags` / `action_window` | `vitals_saw`, `vitals_flags`, `action_window` | Findings first → latest; interpretations; urgency → action window |

### New data for pages 14-16
| Source | Dataset / column | Used for |
|---|---|---|
| Lighthouse (VERIFY paths) | `assessments`, `interventions`, `adverse_events` | Counts and last day per assessment / intervention; adverse event list |
| CQI LLM (`llm_summary`) | `assessments_summary`, `interventions_summary`, `adverse_events_summary`, `visit_themes` | Narrative summaries on page 15 |
| CQI LLM (`llm_summary`) | `missing_interventions_json`, `missing_assessments_json`, `visit_goals_json` | Gap tables on page 16 |
| `llm_goals` | status + discipline | Met / not met split; therapy goals = PT, OT, ST |

Payload schema 1.3 adds `care_review` and `gap_analysis`. Feedback rows r12-r14 cover the new pages.

Brand: the CenterWell logo appears only in full colour, on white (top of the cover, and the page footers). No white or reversed logo is used on blue headers.

## LLM text sizes
See `config/llm_output_spec.md` for per-field character limits and a prompt block you can paste into the CQI prompts. Text within those limits prints at full size. Longer text is shrunk and then truncated, and `qc.csv` lists it under `length_warnings`.

This is a proof of concept. The wording describes findings as AI-assisted drafts for clinical review. They are not described as clinician-validated anywhere.

Each evidence page (8-20) ends with a **Feedback tip**: what to check, and which rows to rate on page 21. The paper check boxes are gone. Page order comes from `PAGES` in `src/cqi_payload.py`, and all page references update automatically. The full booklet is 23 pages.

## Clinician feedback: fillable PDF
Page 21 is a real PDF form, as are the decision box on page 5 and the notes on page 23. Clinicians fill it in on screen in Adobe Acrobat Reader, Microsoft Edge or Chrome, save it, and send it back. Nothing has to be printed.

| Part | Fields |
|---|---|
| One row per CQI finding (10, plus "Discharge decision readiness" when flagged) | Agree / Partly / Disagree, error type checkboxes (D data missing, I data incorrect, C clinical interpretation, N not relevant), comment |
| Conference outcome | Plan updated, visits rescheduled, MD notified, no change, refer to Data Science |
| Usefulness | 1-5 |
| Free text | What should CQI add, remove or change; reviewer name, role, date |

Field names are `cqi__<patient_id>__<episode_id>__<key>`, so merged branch booklets keep each patient's answers separate.

**Collecting the answers:**
```bash
python tools/collect_feedback.py <folder of returned PDFs> --out feedback/2026-08-15
#  -> feedback_findings.csv (patient x finding), feedback_overall.csv (patient), feedback.xlsx
```
Blank or unopened forms are skipped. The finding-level file is what you need to track AI quality by section, e.g. % Agree for the Missed Care Summary, or the most common error type.

## Where the summaries go
Summaries come from `llm_summary` (`hsa.cqi_patient_summary`) and appear on pages 6-7:

| Payload `narratives` field | Dataset column | Power BI block |
|---|---|---|
| patient_goal | care_plan_goal | Patient Goal |
| profile_risk | profile_risk_summary (falls back to risk_rationale) | Patient Profile & Risk Summary |
| vitals_trajectory | vitals_summary | Vitals Trajectory Summary |
| care_delivery | care_delivery_summary + schedule_summary | Care Delivery Summary |
| missed_care | missed_care_summary | Missed Care Summary |
| delay_impact | delay_impact_explanation | Clinical Delay Impact Explanation |
| data_gaps | data_gaps_summary (items separated by `\|`) | Data Gaps Found |

**Data Gaps Found** prints one bullet per `|`-separated item in a plum panel on page 7, with a count chip. Page 5 shows "N data gaps - check page 7 first" above the actions, and page 21 has a feedback row for the gaps. Gaps that don't fit after shrinking show as "+ N more". Pages 6-7 give each summary its natural height and shrink only when the text is very long.

Long text shrinks to fit, down to 7.6 pt. If it still doesn't fit, it's cut at a word and labelled as truncated, pointing to the CQI Power BI report. Empty summaries show "No summary was generated". If every summary is missing, the page shows a banner.

## Notes
- Data in `data/csv` and `samples/` is synthetic. Percentiles in the sample come from 4 patients; real runs rank against every scored episode.
- Generated files can contain PHI. Write them only to approved Volumes; `output/` is git-ignored.

Cover text: `meta.prepared_for` (default "CWHH Clinical Quality Team") and `meta.produced_by` (default "CW Data Science & Applied AI - HH Team", shown as "Produced by") are optional payload fields.

## Nearing discharge decision flag
- **Rule:** an active episode (not DISCHARGED) that is 0-14 days from `episode_end` is flagged. Set `DISCHARGE_WINDOW_DAYS` in `src/build_payloads.py` to change the window.
- **Data:** `episodes.episode_end` maps to `EPI_END` (marked VERIFY in `datasets.yaml`). If it's missing, the episode end is the start date + 59 days.
- **Payload:** `patient.episode_end`, `patient.days_to_episode_end` and `patient.discharge_window {flag, days_remaining, window_days, label}`.
- **PDF:**
  - Cover: a chip on the patient card.
  - Summary page: a plum banner with days left, end date, prognosis, rehab potential, goals achieved and visits left. When CQI doesn't supply actions, a "discharge decision" action is added.
  - "CQI summaries" page: a "Discharge decision" column in the status strip.
  - Page A: a green discharge-window band on the timeline.
  - Feedback page: a "Discharge decision readiness" row.

Power BI calculated columns, to keep the dashboard in line with the PDF:
```DAX
days_to_episode_end = DATEDIFF ( [AsOfDate], [EPI_END], DAY )
nearing_discharge_decision =
    IF ( [EPI_STATUS] <> "DISCHARGED" && [days_to_episode_end] >= 0 && [days_to_episode_end] <= 14, TRUE (), FALSE () )
```

## Clinical profile (485 / OASIS)
The new optional dataset `patient_profile` has the fields from the dashboard Patient Profile page: comorbidity adjustment, functional impairment, functional limitations, activities permitted, allergies, mental status, nutritional requirements, prognosis and rehab potential.
- **Page A:** shown in a 4-column table. Allergies other than NKA print in plum.
- **Discharge banner:** prognosis and rehab potential appear there.
- **Payload:** stored under `patient.clinical_profile`.

## Contents page
Contents on page 3 is grouped so readers can tell CQI's own work apart from operational reporting:
- **Start here:** what CQI is, At a glance, the one-page summary.
- **CQI analysis:** what CQI found, concluded and recommends. This covers the CQI summaries, the discharge recommendation, risk drivers, the vitals action window, goals, medication adherence, care plan gaps and referrals.
- **Evidence:** the operational records behind the findings, and how CQI used them. This covers context, diagnoses, wounds, OASIS, visits, missed care, vitals trends, assessments / interventions and the methods page.
- **Your input:** feedback and the conference decision and notes.

Pages that don't apply to a patient are left out of their group automatically.

## Sections and divider pages
The booklet follows the same four groups as the Contents page (`SECTIONS` in `src/cqi_payload.py`):

| Section | Opens with | Pages |
|---|---|---|
| Start here | (cover) | Cover, What is CQI, Inside this booklet, At a glance, Patient conference summary |
| **CQI analysis** | Divider page | CQI summaries, Discharge or recertification, Risk drivers, Vitals action window, Goals, Medication adherence, Care plan gaps, Care network |
| **Evidence** | Divider page | Patient context, Diagnoses, Wounds, OASIS, Visits, Missed care and events, Vitals trends, Assessments / interventions, How CQI produced this |
| **Your input** | Divider page | Clinician feedback, Conference decision and notes |

- **Divider pages:** each shows the section name, its page range, what makes it different (CQI's own work vs. operational records), and a tile for every page in it.
- **Page headers:** every page header carries its section label (CQI ANALYSIS / EVIDENCE / YOUR INPUT).
- **Pages that don't apply:** pages such as wounds, discharge and medication adherence drop out of their section and the numbering closes up.
- **Short summary:** has no dividers.

Full booklets are 25-28 pages depending on the patient.
