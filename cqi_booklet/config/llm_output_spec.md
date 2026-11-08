# CQI LLM output spec: text sizes for the Patient Conference Booklet

Text within these limits prints at full size (9–10 pt) and is never shrunk or cut off. Longer text is shrunk (down to 7.6 pt) and then truncated with "see the CQI Power BI report", and the QC file (`qc.csv` → `length_warnings`) flags it. The same limits live in `TEXT_LIMITS` in `src/cqi_payload.py`.

| Field (dataset column) | Booklet location | Max characters | ≈ Words | Shape |
|---|---|---|---|---|
| `headline` | Summary: "Why this patient is on today's list" | 200 | 30 | 1 sentence |
| tile points (`tiles_json`) | Summary: 4 status tiles | 70 each, 3 per tile | 10 | Fragments, numbers first |
| `actions_json` → action | Summary: recommended actions | 110 each, max 4 | 16 | Verb first; owner + urgency in their own fields |
| `care_plan_goal` | CQI summaries: profile, risk and vitals | 250 | 40 | Verbatim care-plan wording |
| `profile_risk_summary` | CQI summaries: profile, risk and vitals | 1,200 | 180 | 2 short paragraphs: profile, then "Risk Rationale:" |
| `vitals_summary` | CQI summaries: profile, risk and vitals | 900 | 130 | Trend → limit crossed → monitoring gap → urgency |
| `care_delivery_summary` + `schedule_summary` | CQI summaries: care delivery and data gaps | 700 combined | 100 | Delivered vs ordered, then schedule |
| `missed_care_summary` | CQI summaries: care delivery and data gaps | 700 | 100 | Start with "Missed Care Impact:" |
| `delay_impact_explanation` | CQI summaries: care delivery and data gaps | 450 | 65 | Acuity window, then why the impact rating was chosen |
| `data_gaps_summary` | CQI summaries: care delivery and data gaps: Data Gaps Found | 110 per item, max 6 | 16 per item | Items separated by `\|`, one gap per item |
| `recommended_interventions` | Goals page | 220 per item, max 4 | 32 | Each item starts with `[SN]`, `[PT]`, `[OT]`, `[ST]`, `[HHA]`, `[MSW]` or `[CM]` |
| goal `evidence` | Goals page | 80 | 12 | Fragment with a DAY+n reference |
| vitals interpretation (`vitals_interpretation_json`) | Vitals page table | 90 per measure | 14 | Keys: `weight`, `spo2`, `pulse`, `sbp` |
| `assessments_summary` | Care delivered: Assessments performed | 400 | 60 | What was assessed, how often, what was not repeated |
| `interventions_summary` | Care delivered: Interventions performed | 400 | 60 | Main interventions with counts; what no intervention addressed |
| `adverse_events_summary` | Care delivered: Adverse events | 250 | 35 | Event, day, severity; "No adverse events recorded" if none |
| `visit_themes` | Care delivered: Visit narrative themes | 80 per theme, max 6 | 12 | Themes separated by `\|`; patient / caregiver voice where possible |
| `missing_interventions_json` | Care plan gaps | 6 rows; intervention ≤ 110 | | JSON array: `[{"goal","intervention","discipline"}]`. `goal` must match a care-plan goal |
| `missing_assessments_json` | Care plan gaps | 6 rows; reason ≤ 90 | | JSON array: `[{"item","type":"Assessment"\|"Vitals","reason"}]` |
| `visit_goals_json` | Care plan gaps | 6 rows; visit_goal ≤ 100 | | JSON array: `[{"discipline","visit_goal","supports_goal"}]`. Measurable, one visit |
| `cqi_found_summary` → glance_found | Branch overview card: "What CQI found" | 110 | 16 | One sentence, the 1-2 facts that matter most |
| `glance_risk_summary` | At a glance: Risk (predictive model) | 160 | 24 | Why the model flags the patient, in clinical words |
| `glance_care_summary` | At a glance: Care delivery | 160 | 24 | Acuity window, shortfalls, missed evaluations, schedule |
| `glance_trajectory_summary` | At a glance: Clinical trajectory | 180 | 27 | Vitals direction with numbers; monitoring adequacy |
| `cqi_flags` | At a glance: CQI flags (dark blue bar) | 200 | 30 | 2-3 imperative actions, most urgent first |
| `vitals_trajectory_class` | Vitals action window | one value | | `improving`, `stable`, `deteriorating` or `insufficient_data` |
| `vitals_what_cqi_saw` | Vitals action window: What CQI saw | 150 | 22 | Readings with arrows, e.g. "SBP 117 → 107" |
| `vitals_what_cqi_flags` | Vitals action window: What CQI flags | 150 | 22 | Clinical meaning + monitoring adequacy |
| `action_window` | Vitals action window (green chevron) | 50 | 7 | e.g. "Clinician review within 48 hours" |
| `wound_summary` | Wounds page | 400 | 60 | Each open wound: type, location, trend; note any not re-measured |
| `diagnosis_summary` | Diagnoses page | 400 | 60 | Primary diagnosis, onset vs. exacerbation, key comorbidities |
| `oasis_summary` | OASIS page | 400 | 60 | Key risk items (M1033, function, meds, caregiver) and what changed |
| `discharge_recommendation` | Discharge or recertification page (only when within 14 days of episode end) | one value | | `Discharge`, `Discharge with referrals`, `Recertify` or `Clinical review` |
| `discharge_rationale` | Same page: recommendation bar | 400 | 60 | Why, from the data: goals, vitals, wounds, function, risk, events |
| `medication_summary` | Medication adherence page | 400 | 60 | High-risk meds and why; understanding, teaching and taking-as-prescribed findings; barriers (vision, cost, cognition, supply) |
| `risk_rationale` (fallback only) | Used if `profile_risk_summary` is empty | 600 | 90 | |

## Prompt block to paste into each CQI summary prompt

```
OUTPUT LENGTH AND FORMAT (strict - text is printed in a fixed-size PDF booklet):
- Return plain text only: no markdown, no bullets, no headings, no line breaks unless asked.
- Respect the character limit for each field; it is a hard maximum, including spaces:
  headline <= 200 | each tile point <= 70 (exactly 3 per tile) | each action <= 110 (max 4)
  profile_risk_summary <= 1200 | vitals_summary <= 900 | care_delivery_summary + schedule_summary <= 700 combined
  missed_care_summary <= 700 | delay_impact_explanation <= 450
  data_gaps_summary: max 6 items, each <= 110, separated by "|" with no other separators
  recommended_interventions: max 4 items, each <= 220, each starting with [SN], [PT], [OT], [ST], [HHA], [MSW] or [CM]
  vitals interpretation per measure <= 90 | goal evidence <= 80
  assessments_summary <= 400 | interventions_summary <= 400 | adverse_events_summary <= 250
  visit_themes: max 6 themes, each <= 80, separated by "|"
  missing_interventions_json, missing_assessments_json, visit_goals_json: valid JSON arrays only, max 6 rows each,
    text fields <= 110 / 90 / 100 characters; use the exact care-plan goal wording in "goal" and "supports_goal"
  AT A GLANCE (customer summary): cqi_found_summary <= 110 | glance_risk_summary <= 160 | glance_care_summary <= 160
    glance_trajectory_summary <= 180 | cqi_flags <= 200 (2-3 actions, imperative, most urgent first)
  DISCHARGE (only if episode ends within 14 days): discharge_recommendation = one of Discharge | Discharge with referrals |
    Recertify | Clinical review; discharge_rationale <= 400, citing the data on both sides
  MEDICATION ADHERENCE: medication_summary <= 400
  CLINICAL PICTURE: wound_summary <= 400 | diagnosis_summary <= 400 | oasis_summary <= 400
  VITALS ACTION WINDOW: vitals_trajectory_class = one of improving|stable|deteriorating|insufficient_data
    vitals_what_cqi_saw <= 150 (numbers with arrows) | vitals_what_cqi_flags <= 150 | action_window <= 50
    ("Clinician review within 24 hours", "... within 48 hours", "... this week", "Continue routine monitoring")
- If you have more to say than fits, keep the facts that change what a clinician does this week and drop the rest.
- Write for a Home Health clinical manager: short sentences, active voice, standard clinical abbreviations
  (SN, PT, OT, MSW, HHA, SOC, HF, CKD, SpO2). Refer to days as DAY+n. Put numbers before words ("3 SN visits missed").
- Do not repeat the patient ID, branch or dates that are already printed elsewhere.
- Only state facts present in the input data. If a fact is missing, say so in data_gaps_summary instead of guessing.
```

## Tips
- Ask for the character limit, not just a word count. LLMs keep to character caps more reliably when the number is stated per field.
- Add a post-processing check: if a field is over its limit, send one re-prompt ("Shorten to N characters, keep all numbers") before you write it to `hsa.cqi_patient_summary`.
- Keep the `|` separator for data gaps. The renderer splits on `|` or new lines, so don't use `|` inside an item.

## If these fields are empty
The pipeline writes them from facts: the trajectory class and action window come from the vitals urgency rules, and the at-a-glance rows from the status tiles, risk drivers and actions. LLM text always wins when it's present. The rule-based text is accurate but plainer, so the customer-facing pages read best with the LLM fields filled.
