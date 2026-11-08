# CQI booklet: review of the zip and the PDF, plus a plan for the Patient Conference web app

Reviewed 2026-11-07. Sources:
- `Downloads\cqi_booklet (1).zip`, extracted to the scratchpad `zip_extract\cqi_booklet\`
- `Downloads\CQI_Booklet_Sample_9000123_fillable 4.pdf`

The code was read and checked for risky calls. None were found: no network or exec calls, and the only `subprocess` call is in the tests, where it runs the zip's own `tools/check_sync.py`. The pipeline was **not run**, because auto mode blocked running code from the download.

---

## 1. Headline finding

**The zip already contains a finished PDF generator, so goal 1 is mostly built.** `src/build_payloads.py` reads the CSVs, works out the cohort and its metrics, writes one payload JSON per patient (schema v1.8) and renders PDFs through `src/build_booklet.py` (ReportLab). The PDFs have real AcroForm fields. `tools/collect_feedback.py` reads the returned forms back into CSV and XLSX files.

**The downloaded PDF is an older version than the code in the zip.** Comparison:

| | Downloaded PDF | Zip `samples/CQI_Booklet_Sample_9000123.pdf` |
|---|---|---|
| Pages | 15 | 27 (25-28 depending on the patient) |
| Form fields | 77 | 119 |
| Feedback rows | r01-r10 | r01-r18 (adds glance, assessments, gaps, diagnoses/wounds/OASIS, meds, network, discharge) |
| Goals listed for 9000123 | 6 | 8 (the CSV now has 8) |
| Creator / Producer | "M365 Copilot" / PDFsharp re-save of ReportLab | ReportLab |

The downloaded copy was re-saved through another tool, and the CSVs have changed since it was made. **Treat the zip's code and samples as the current spec**, not the download. Both use the same field-name pattern, so `collect_feedback.py` reads either one.

## 2. What's in the zip

| Path | What it is |
|---|---|
| `config/datasets.yaml` | Dataset registry. Each of the 22 datasets has its grain, columns and types, a csv / table / path source, and source-column renames for Databricks (many marked VERIFY) |
| `config/cqi_booklet_schema.json` | JSON Schema for the per-patient payload (v1_8). This is the contract between the data and the renderer |
| `config/llm_output_spec.md` | Character limits for each LLM text field, plus a prompt block |
| `config/branding.yaml` | Footer, stage label and vision text |
| `src/cqi_datasets.py` | `Datasets.load()/check()`: loads each dataset by its canonical name and type-casts it. Has a Spark view for Databricks |
| `src/build_payloads.py` | Cohort, care grid, vitals, meds, network and discharge window → payload JSON → PDF. CLI and `run()` |
| `src/cqi_payload.py` | Rule-based composer: tiles, actions, glance, trajectory, `discharge_recommendation()`, `referral_indicators()`, conditional pages, validation, length QC |
| `src/build_booklet.py` | 130 KB ReportLab renderer with CenterWell styling, charts and form widgets |
| `notebooks/00,01` | Databricks setup and the weekly build |
| `tools/` | `make_sample_csvs.py` (generates the synthetic data), `collect_feedback.py`, `check_sync.py` (148 consistency checks) |
| `tests/test_pipeline.py` | 9 smoke tests |
| `samples/` | 3 patient PDFs, a branch PDF in full and short form, 2 payload JSONs |
| `_drivers_*.json` | Chart.js-style bar configs for the risk drivers (looks like a leftover dev artifact) |
| `assets/` | Logo, patterns and icons |

### CSV schemas (`data/csv/`, all synthetic; join key `patient_id` + `episode_id`)

| CSV | Grain | Columns | Notes |
|---|---|---|---|
| episodes | patient-episode | episode_start, episode_end, episode_status, admit_type, branch_name, branch_state, clin_group, acuity_level, age, sex, primary_dx, wound_ind, inpat_ind, death_ind, transfers_ind | 4 episodes: 9000123, 9000231, 9000345, 9000400. This is the hub table |
| risk_scores | episode × as_of_date | score, outcome_risk_level | 2 runs (08-08, 08-15), used for the trend and the "rising" test |
| risk_drivers | episode × run × feature | feature, label, contribution | SHAP-style drivers |
| llm_summary | episode × run | 47 columns: status flags (frequency_status, schedule_status, risk_raised_by_care_gaps, delay_clinical_impact, outcomes_at_risk), narratives (risk_rationale, care_delivery, vitals, missed_care, delay_impact, data_gaps `\|`-list, assessments/interventions/adverse summaries, visit_themes), glance_*, vitals_trajectory_class/saw/flags/action_window, wound/diagnosis/oasis/medication summaries, discharge_recommendation/rationale, patient_stated_goal, care_plan_goal, comorbidities, living_situation, headline, tiles/actions/vitals_interp/missing_*/visit_goals JSON | 9000123 is complete. **9000231 is empty**, which tests the "no AI summaries" path. 9000345 is partial. discharge_* is blank for everyone, so the rules decide |
| llm_goals | episode × goal | goal, discipline, status (achieved / in_progress / delayed / missed / not_started), evidence | 8 goals for 123, 1 for 345 |
| alignment | episode × item | item, type (Medication/Supply/DME), supports_goal, check, flag | 123 only |
| patient_profile | episode | comorbidity_adjustment, functional_impairment, functional_limitations, activities_permitted, allergies, mental_status, nutritional_requirements, prognosis, rehab_potential | 485 / OASIS profile. 9000400 is missing |
| diagnoses | episode × code | code, description, category, clinical_group, diag_or_proc, onset_exacer, onset_exacer_dt, is_primary | |
| oasis | episode × assessment × item | assessment_id, assess_dt, timepoint, item, section, description, answer | 345 has SOC and follow-up, so change can be measured. 123 has SOC only |
| wounds | episode × wound × assess_dt | wound_id, category, class, extended_type, location, description, stage, length/width/depth_cm, healing_status | |
| medications | episode × med | med_name, drug_class, high_risk (Y/N/blank → class fallback), start_dt | |
| care_network | **patient** (no episode_id) | has_pcp, pcp_name, pcp_last_visit_dt, ma_eligible, cwpco_enrolled, cwrx_enrolled, nearest clinic + miles, clinics within 10/20 mi, active_med_count | |
| orders | episode × discipline | frequency (e.g. "3W1, 2W2, 1W6"), effective_dt | Expanded into ordered visits per week |
| visits | visit | visit_dt, visit_type, visit_status, missed_reason, med_understanding, med_teaching, med_adherent | Delivered and missed visits, plus med checks |
| sched | future visit | sched_dt, visit_type | |
| vitals | episode × date × measure | vital_name, vital_value | **Naming varies**: 123 uses WEIGHT / BP "132/78" / PULSE / SPO2; 345 uses SYSTOLIC BP / DIASTOLIC BP / HEART RATE / O2 SAT. The code normalizes them |
| vs_alerts | alert | alert_dt, alert_text, physician_notified | |
| assessments / interventions | event | date, name, discipline (+ goal for interventions) | |
| adverse_events | event | event_dt, event_type, severity, detail | |
| client_logs | log | log_dt, log_type, log_note | |
| ip_events | **patient** (no episode_id) | admit_dt, event_desc | Includes a deliberate out-of-window row that must be excluded |

**Cohort rule** (`build_payloads`): an episode is in the cohort if its status is CURRENT, RECERTIFIED or ON-HOLD, and it is either high / very_high risk or its score rose by at least 0.05 since the prior run. With as_of 2026-08-15:
- 9000123: in (high, rising)
- 9000231: in (very high)
- 9000345: in (moderate, but rising by 0.08)
- 9000400: out (low, flat)

**Discharge window:** an episode is in the window when it is not discharged and is 0-14 days from `episode_end`. Only **9000345** is in the window (14 days to 08-29). 9000123 has 23 days left and 9000231 has 40.

## 3. How the CSVs map onto the PDF (current 25-28 page booklet)

| Page | Main sources |
|---|---|
| Cover / At a glance | episodes, risk_scores, llm_summary.glance_*, care_network |
| Conference summary (fillable decision) | llm_summary.headline/tiles_json/actions_json, or the rule-based composer when those are empty; risk_scores (tier, percentile, trend) |
| CQI summaries ×2 | llm_summary narratives + data_gaps_summary |
| Discharge or recert (conditional) | `discharge_recommendation()` over llm_goals, vitals trajectory, wounds, risk, adverse_events / vs_alerts, OASIS change, patient_profile prognosis and rehab, data gaps, care_network |
| Risk drivers / timeline | risk_drivers, visits, vs_alerts, ip_events |
| Vitals action window / trends | vitals, vs_alerts, llm_summary.vitals_*; default limits SBP 160, pulse 50-100, SpO2 90, weight +5 lb |
| Goals | llm_goals, patient_stated_goal, interventions_text, alignment |
| Medication adherence (conditional) | medications, visits.med_* |
| Care plan gaps | missing_interventions/assessments_json, visit_goals_json |
| Care network | care_network → referral indicators |
| Context / Diagnoses / Wounds (conditional) / OASIS | episodes, patient_profile, diagnoses, wounds, oasis |
| Care delivery / Missed care | orders × visits × sched (9-week grid), client_logs, ip_events |
| Care delivered | assessments, interventions, adverse_events, visit_themes |
| Feedback / Decision + notes (fillable) | Form fields `cqi__<pid>__<eid>__<key>`: decision (agree / agree_changes / disagree / escalate), action_owner, due_date, reviewed_by, rNN_rating + rNN_err_D/I/C/N + rNN_comment, out_* outcomes, usefulness 1-5, overall_comment, reviewer_*, notes_* |

---

## 4. Patient Conference web app: a deliberately simple vision

### Principle
**Share one data layer.** The web app reads the same per-patient payload JSON that `build_payloads.py` already produces, which is the same object the PDF renders from. Every number on screen then matches the booklet, no clinical logic is copied, and the app uses only fields from the zip.

### Screens (3 in total)
1. **This week's conference list.** One row per cohort patient, sorted by risk. Columns: risk tier, score and trend; days to episode end with a "Nearing discharge" chip; system recommendation; vitals trajectory class; goals met (x of y); visits remaining vs. scheduled; data-gap count. Filters: branch, nearing discharge, deteriorating vitals.
2. **Patient page.** A **discharge readiness card** sits at the top: the recommendation and its confidence, "supports discharge" and "supports recert" lists, "check first" caveats and next steps. Below it, compact tabs: Goals · Vitals (small chart) · Visits (ordered / delivered / scheduled grid) · Function (OASIS start of care → latest) · Wounds · Meds · Care network · Events. Each item notes which data it came from.
3. **Ask panel** (on the patient page and the list page). A fixed set of question chips, each answered from known fields, plus a search box that matches questions to chips. **No free-form LLM in v1**, so every answer traces back to a field.

### Example questions and the data that answers them
| Question | Answered from |
|---|---|
| Is this patient ready for discharge? | `discharge_recommendation` (rules over the items below) + `discharge_rationale` when the LLM has filled it |
| Are care-plan goals met? Which aren't? | llm_goals.status / evidence / discipline |
| Are vitals stable? | vitals (first → latest, limit crossings), vitals_trajectory_class, vs_alerts.physician_notified |
| Has function improved since start of care? | oasis M18xx / M1242 / M2020 / M1400 / M1700, SOC vs. latest (lower code is better) |
| Are wounds healed? | wounds.healing_status and size over time |
| How much skilled care is left, and is it scheduled? | orders × visits × sched |
| Were visits missed, and why? | visits.missed_reason, client_logs |
| Any falls, ED visits or hospitalizations this episode? | adverse_events, ip_events (filtered to the episode window), episodes.inpat_ind |
| Is the patient safe on their medications? | medications.high_risk, visits.med_understanding / teaching / adherent, medication_summary |
| Who follows the patient after discharge? Refer to CW PCO / CW Rx? | care_network → `referral_indicators()` |
| Prognosis, rehab potential, home situation? | patient_profile, llm_summary.living_situation, oasis M1100 / M2102 |
| What does the patient want? | patient_stated_goal, care_plan_goal |
| What is CQI unsure about? | data_gaps_summary |
| Across the branch: who is nearing discharge, and who is deteriorating? | payload list: discharge_window, vitals trajectory |

### Discharge-readiness signals (all from existing data, reusing `discharge_recommendation()`)
- **Supports discharge:** all goals achieved; vitals stable or improving; wounds healed; risk low or moderate; OASIS function improved; prognosis good or excellent.
- **Supports recert:** goals open, delayed or missed; vitals deteriorating; open wounds; high or very high risk; adverse events or unresolved alerts; OASIS decline; prognosis poor or guarded; good rehab potential with goals still progressing.
- **Check first:** no goals on record, no recent vitals, open data gaps.
- **Referrals and next steps:** from care_network.

Worked example, 9000345: **Discharge (Leaning), 5 vs. 2.** Vitals are stable, the wound is healed, risk is moderate, OASIS improved (M1850 1→0, M1860 2→1, M1242 3→1) and prognosis is good. The cane goal is still open.

### Gaps: what the data cannot answer
- **Discharge logistics:** destination, caregiver availability and ability, and home safety outcome. There is only free text (living_situation) and OASIS M1100 / M2102.
- **Objective therapy measures:** for example, ambulation distance appears only inside goal evidence text. Pain scores are absent, which 9000345's own data_gaps note points out.
- **No labs**, no physician discharge order, no PHQ or depression scores, no orthostatic BP.
- **No patient names.** IDs only, which is good for PHI.
- **Little history:** 2 risk runs, 1 as-of date and 4 patients, so trends are thin.
- **Missing AI text:** 9000231 has no LLM text, and LLM `discharge_*` is blank for everyone, so the app must fall back to rules, as the PDF already does.
- **No episode_id** on `care_network` and `ip_events`, so they join by patient (and ip_events is filtered by date).
- **Everything is synthetic.** Source mappings marked VERIFY are unconfirmed.

### Stack: Gradio (user's choice, 2026-11-07, matching their last app)
- **Data layer:** the existing `cqi_datasets.Datasets` + `build_payloads.build_payloads()`, called with `render=False` (or reading the JSON from `output/<date>/json`). Payloads are cached in memory per as-of date.
- **UI:** a single `gr.Blocks` app, run with `python app.py`. Screens map onto Gradio components as follows:
  - **Conference list:** `gr.Dataframe` of the cohort, with `gr.Dropdown` filters for branch and nearing-discharge. Selecting a row (`.select`) opens the patient.
  - **Patient page:** a `gr.HTML` or `gr.Markdown` discharge-readiness card, then `gr.Tabs` for Goals, Vitals (`gr.LinePlot` or a matplotlib `gr.Plot`), Visits grid (`gr.Dataframe`), Function, Wounds, Meds, Care network and Events.
  - **Ask:** a `gr.Chatbot` with `gr.Examples` / button chips for the fixed questions. A small router maps each question to a function over the payload, and every answer ends with a "Source:" line naming the CSV fields it used.
  - **Download booklet:** `gr.DownloadButton` / `gr.File`, which calls the existing renderer for the selected patient.
- **Styling:** `gr.themes.Soft` with CenterWell colours (#003DA5, #00A3D7, #78BE20, #AF0061) and a little custom CSS for the readiness card.
- **Optional v2:** a decision radio and notes on the patient page, saved under the same `cqi__…` keys so `collect_feedback` output and web decisions land in one table.

### AI answers in the Ask panel (user's choice, 2026-11-07)
This copies the pattern already used in clinician_copilot's `src/llm.py`, scaled down.
- **Two kinds of answer, one chat:**
  - The fixed question chips stay **rule-based**, so they are instant, give the same answer every time and cite their fields.
  - Free-typed questions go to the LLM.
  - If no model is configured or the call fails, the app falls back to the closest rule-based answer, so a demo never breaks. This is the same behaviour as clinician_copilot's "Rules Engine (offline)".
- **Grounding, strict:**
  - The prompt holds only a compact text rendering of that patient's payload: profile, risk and drivers, goals, vitals table, visit grid, OASIS SOC→latest, wounds, meds, network, events, the discharge recommendation and its signals, and the data gaps.
  - For list-level questions, it holds a one-line-per-patient cohort table instead.
  - No outside knowledge base and no other data sources.
- **System prompt rules:**
  1. Answer only from the context.
  2. Cite the section / field for each claim (for example "llm_goals", "vitals").
  3. If the data doesn't contain the answer, say "Not in the CQI data". Then name the gap, using the gaps list (for example caregiver ability, pain scores, labs).
  4. Never invent values, dates or recommendations.
  5. Treat this as decision support: the clinician and physician decide discharge.
  6. Keep it short and use the conference's language.
- **Backends (pluggable, from config):**
  - Gemini or any **OpenAI-compatible endpoint**: a company-hosted or Databricks serving endpoint, which matches the zip's `<llm-endpoint>` placeholder.
  - Optionally Claude.
  - Rules-only as the default and fallback.
  - Model choice and keys are read from env/config, never hard-coded.
- **PHI:** the CSVs are synthetic and use IDs only, with no names. For real data the LLM must be an **approved internal endpoint**. The app shows the active backend and an "AI-generated, check against the evidence tabs" label under every AI answer.
- **Logging:** each AI answer is logged with its question, patient, model and the context hash to a local CSV for later QC. This goes alongside `collect_feedback`. Optionally add a thumbs up/down per answer.
- **Out of scope for v1:** tool calling, multi-patient retrieval beyond the cohort table, and writing anything back to the data.

### Proposed layout
```
cqi_booklet/            (existing, unchanged)
webapp/
  app.py                # gr.Blocks UI: list, patient tabs, ask chatbot, booklet download
  data.py               # imports cqi_booklet/src; load_payloads(as_of) -> list[dict]
  questions.py          # fixed questions -> function(payload) -> answer + source fields (also the AI fallback)
  llm.py                # payload -> context text; backends (rules / Gemini / OpenAI-compatible); system prompt
  config.yaml           # as_of date, data source
  project_alpha_ai_gateway.yaml  # Project Alpha: backend switch (Gemini Flash default / company gateway / rules),
                                 # gateway URL + auth env var, scope filter (keywords + guard prompt), answer prompt, logging
```
Draft of the AI config: `project_alpha_ai_gateway.yaml` in this notes folder. It moves into `webapp/` once the project location is chosen. **Decided:** the app is named **Project Alpha**, and it uses **Gemini Flash** (`gemini-2.5-flash`, `GEMINI_API_KEY`, google-genai, as in clinician_copilot).

### Decisions to confirm with the user before building
1. Should the zip be the base for the PDF generator (it's finished), or does the user want a rewrite?
2. ~~Stack~~: decided, Gradio.
3. ~~Ask panel~~: decided, fixed questions plus grounded AI answers with a rules fallback.
4. Where should the new project live? It must not go in clinician_copilot.
5. ~~LLM backend~~: decided, Gemini Flash, with the company gateway as a switchable option in the YAML.
