"""Generate SYNTHETIC sample CSVs for the CQI booklet (no real patients).

Four patients on run date 2026-08-15:
  9000123  high + rising, full LLM output          -> booklet (matches the sample booklet)
  9000231  very_high, NO LLM narrative, no vitals   -> booklet (tests rule-based composer + empty states)
  9000345  moderate but rising +0.08, partial LLM  -> booklet (tests 'rising' cohort rule)
  9000400  low and stable                           -> excluded from cohort
Run:  python tools/make_sample_csvs.py   (writes data/csv/)
"""
import csv, json, os
from datetime import date, timedelta

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "csv")
os.makedirs(OUT, exist_ok=True)
RUN, PRIOR = "2026-08-15", "2026-08-08"
B1, B2 = "BRANCH 0412 - CENTERWELL HOME HEALTH", "BRANCH 0518 - CENTERWELL HOME HEALTH"
P = {"p1": ("9000123", "7000456", date(2026, 7, 10)), "p2": ("9000231", "7000512", date(2026, 7, 27)),
     "p3": ("9000345", "7000588", date(2026, 7, 1)), "p4": ("9000400", "7000601", date(2026, 7, 20))}
d = lambda k, day: (P[k][2] + timedelta(days=day)).isoformat()
ids = lambda k: {"patient_id": P[k][0], "episode_id": P[k][1]}

def write(name, rows):
    with open(os.path.join(OUT, name), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(f"{name:<22}{len(rows):>4} rows")

# ---------------------------------------------------------------- episodes
write("episodes.csv", [
    {**ids("p1"), "episode_start": d("p1", 0), "episode_end": d("p1", 59), "episode_status": "CURRENT", "admit_type": "READMISSION", "branch_name": B1,
     "branch_state": "NC", "clin_group": "MMTA_CARDIAC", "acuity_level": "2", "age": 82, "sex": "F", "primary_dx": "Heart failure, unspecified (I50.9)", "wound_ind": "N", "inpat_ind": "Y", "death_ind": "N", "transfers_ind": "N"},
    {**ids("p2"), "episode_start": d("p2", 0), "episode_end": d("p2", 59), "episode_status": "CURRENT", "admit_type": "NEW ADMISSION", "branch_name": B1,
     "branch_state": "NC", "clin_group": "MMTA_RESPIRATORY", "acuity_level": "1", "age": 77, "sex": "M", "primary_dx": "COPD with acute exacerbation (J44.1)", "wound_ind": "N", "inpat_ind": "Y", "death_ind": "N", "transfers_ind": "N"},
    {**ids("p3"), "episode_start": d("p3", 0), "episode_end": d("p3", 59), "episode_status": "RECERTIFIED", "admit_type": "NEW ADMISSION", "branch_name": B2,
     "branch_state": "SC", "clin_group": "MS_REHAB", "acuity_level": "3", "age": 71, "sex": "F", "primary_dx": "Presence of right artificial knee joint (Z96.651)", "wound_ind": "Y", "inpat_ind": "N", "death_ind": "N", "transfers_ind": "N"},
    {**ids("p4"), "episode_start": d("p4", 0), "episode_end": d("p4", 59), "episode_status": "CURRENT", "admit_type": "NEW ADMISSION", "branch_name": B2,
     "branch_state": "SC", "clin_group": "MS_REHAB", "acuity_level": "3", "age": 68, "sex": "M", "primary_dx": "Aftercare following joint replacement (Z47.1)", "wound_ind": "N", "inpat_ind": "N", "death_ind": "N", "transfers_ind": "N"},
])

# ---------------------------------------------------------------- 485 / OASIS clinical profile
write("patient_profile.csv", [
    {**ids("p1"), "comorbidity_adjustment": "High", "functional_impairment": "Medium",
     "functional_limitations": "AMBULATION, ENDURANCE, DYSPNEA WITH MINIMAL EXERTION, DECREASED VISION",
     "activities_permitted": "UP AS TOLERATED, WALKER, TRANSFER BED/CHAIR", "allergies": "PENICILLIN, SULFA",
     "mental_status": "ORIENTED, FORGETFUL", "nutritional_requirements": "2 GM SODIUM, 1500 ML FLUID RESTRICTION",
     "prognosis": "FAIR", "rehab_potential": "FAIR"},
    {**ids("p2"), "comorbidity_adjustment": "Low", "functional_impairment": "High",
     "functional_limitations": "ENDURANCE, DYSPNEA WITH MINIMAL EXERTION", "activities_permitted": "UP AS TOLERATED, +1 ADL ASSIST",
     "allergies": "NKA", "mental_status": "ORIENTED", "nutritional_requirements": "REGULAR DIET", "prognosis": "GUARDED",
     "rehab_potential": "FAIR"},
    {**ids("p3"), "comorbidity_adjustment": "None", "functional_impairment": "Medium",
     "functional_limitations": "AMBULATION, PAIN INTERFERES WITH ACTIVITY", "activities_permitted": "PARTIAL WEIGHT BEARING, CANE, UP AS TOLERATED",
     "allergies": "ADHESIVE", "mental_status": "ORIENTED", "nutritional_requirements": "REGULAR DIET", "prognosis": "GOOD",
     "rehab_potential": "GOOD"},
])

# ---------------------------------------------------------------- assessments, interventions, adverse events
AS = []
for day in [1, 3, 5, 8, 10, 15, 22]:
    AS += [{**ids("p1"), "assess_dt": d("p1", day), "assessment": "Cardiopulmonary assessment", "discipline": "SN"},
           {**ids("p1"), "assess_dt": d("p1", day), "assessment": "Edema assessment", "discipline": "SN"}]
AS += [{**ids("p1"), "assess_dt": d("p1", 1), "assessment": a, "discipline": "SN"}
       for a in ("Fall-risk assessment (MAHC-10)", "Pain assessment", "Depression screen (PHQ-2)")]
AS += [{**ids("p1"), "assess_dt": d("p1", day), "assessment": "Medication review", "discipline": "SN"} for day in (1, 15)]
AS += [{**ids("p1"), "assess_dt": d("p1", day), "assessment": "Gait and balance assessment", "discipline": "PT"} for day in (2, 16, 29)]
AS += [{**ids("p3"), "assess_dt": d("p3", day), "assessment": "Pain assessment", "discipline": "PT"} for day in (1, 8, 15, 22, 29, 36)]
AS += [{**ids("p3"), "assess_dt": d("p3", day), "assessment": "Knee range of motion", "discipline": "PT"} for day in (1, 15, 29, 43)]
write("assessments.csv", AS)
IV = []
for day, iv, disc, goal in [(1, "HF teaching", "SN", "Verbalize 3 signs of HF exacerbation within 2 weeks"),
                            (3, "HF teaching", "SN", "Verbalize 3 signs of HF exacerbation within 2 weeks"),
                            (5, "HF teaching", "SN", "Verbalize 3 signs of HF exacerbation within 2 weeks"),
                            (8, "HF teaching", "SN", "Verbalize 3 signs of HF exacerbation within 2 weeks"),
                            (10, "HF teaching", "SN", "Verbalize 3 signs of HF exacerbation within 2 weeks"),
                            (1, "Daily-weight teaching", "SN", "Weigh daily and keep a log"),
                            (8, "Daily-weight teaching", "SN", "Weigh daily and keep a log"),
                            (15, "Daily-weight teaching", "SN", "Weigh daily and keep a log"),
                            (1, "Medication teaching", "SN", ""), (10, "Medication teaching", "SN", ""), (22, "Medication teaching", "SN", ""),
                            (15, "Anticoagulant bleeding-precaution teaching", "SN", "Verbalize anticoagulant bleeding precautions within 3 weeks")] + \
                           [(dd, "Gait and transfer training", "PT", "Safe transfers with walker within 4 weeks") for dd in (2, 9, 11, 16, 18, 24)]:
    IV.append({**ids("p1"), "intervention_dt": d("p1", day), "intervention": iv, "discipline": disc, "goal": goal})
IV += [{**ids("p3"), "intervention_dt": d("p3", day), "intervention": "Gait training with cane", "discipline": "PT",
        "goal": "Ambulate 300 ft with cane within 6 weeks"} for day in (3, 10, 17, 24, 31, 38)]
write("interventions.csv", IV)
write("adverse_events.csv", [
    {**ids("p1"), "event_dt": d("p1", 19), "event_type": "Near-fall", "severity": "No injury", "detail": "Lost balance getting up from chair; caught by daughter"},
    {**ids("p2"), "event_dt": d("p2", 13), "event_type": "ED visit", "severity": "Treat and release", "detail": "COPD exacerbation"},
])

# ---------------------------------------------------------------- wounds, diagnoses, OASIS, care network
WD = []
for day, l_, w_, d_, st in [(1, 3.0, 2.0, 0.3, "Stable"), (8, 2.8, 1.9, 0.3, "Improving"), (15, 2.6, 1.8, 0.2, "Improving"),
                            (22, 2.5, 1.6, 0.2, "Improving")]:
    WD.append({**ids("p1"), "wound_id": "W1", "assess_dt": d("p1", day), "wound_category": "PRESSURE ULCER", "wound_class": "P",
               "extended_type": "", "location": "SACRAL, MID", "description": "STAGE 2 PRESSURE ULCER - PARTIAL THICKNESS SKIN LOSS",
               "stage": "Stage 2", "length_cm": l_, "width_cm": w_, "depth_cm": d_, "healing_status": st})
WD.append({**ids("p1"), "wound_id": "W2", "assess_dt": d("p1", 19), "wound_category": "TRAUMA", "wound_class": "T",
           "extended_type": "SKIN TEAR", "location": "FOREARM, LT", "description": "SKIN TEAR FROM NEAR-FALL",
           "stage": "", "length_cm": 2.0, "width_cm": 0.5, "depth_cm": "", "healing_status": "Not reassessed"})
for day, l_, w_, st in [(1, 6.0, 0.4, "Stable"), (15, 5.0, 0.2, "Improving"), (29, 0.0, 0.0, "Healed")]:
    WD.append({**ids("p3"), "wound_id": "W1", "assess_dt": d("p3", day), "wound_category": "SURGICAL", "wound_class": "S",
               "extended_type": "SURGICAL WOUND", "location": "KNEE, RT", "description": "SURGICAL INCISION - RIGHT TKA",
               "stage": "", "length_cm": l_, "width_cm": w_, "depth_cm": "", "healing_status": st})
write("wounds.csv", WD)
write("diagnoses.csv", [{**ids(k), "code": c_, "description": de, "category": ca, "clinical_group": cg, "diag_or_proc": dp,
                         "onset_exacer": oe, "onset_exacer_dt": d(k, od) if od is not None else "", "is_primary": pr}
    for k, c_, de, ca, cg, dp, oe, od, pr in [
        ("p1", "I50.9", "Heart failure, unspecified", "CIRCULATORY SYSTEM", "MMTA_CARDIAC", "D", "EXACERBATION", -6, "Y"),
        ("p1", "I48.91", "Atrial fibrillation, unspecified", "CIRCULATORY SYSTEM", "MMTA_CARDIAC", "D", "ONSET", -400, "N"),
        ("p1", "E11.9", "Type 2 diabetes without complications", "ENDOCRINE", "MMTA_ENDO", "D", "", None, "N"),
        ("p1", "N18.30", "Chronic kidney disease, stage 3 unspecified", "GENITOURINARY", "MMTA_OTHER", "D", "", None, "N"),
        ("p1", "L89.152", "Pressure ulcer of sacral region, stage 2", "SKIN", "WOUND", "D", "ONSET", -3, "N"),
        ("p1", "Z79.01", "Long term use of anticoagulants", "FACTORS INFLUENCING HEALTH", "", "D", "", None, "N"),
        ("p2", "J44.1", "COPD with acute exacerbation", "RESPIRATORY SYSTEM", "MMTA_RESPIRATORY", "D", "EXACERBATION", -2, "Y"),
        ("p2", "Z99.81", "Dependence on supplemental oxygen", "FACTORS INFLUENCING HEALTH", "", "D", "", None, "N"),
        ("p3", "Z96.651", "Presence of right artificial knee joint", "FACTORS INFLUENCING HEALTH", "MS_REHAB", "D", "ONSET", -3, "Y"),
        ("p3", "0SRC0J9", "Replacement of right knee joint", "PROCEDURE", "MS_REHAB", "P", "", -3, "N")]])
OA = []
def oasis(k, aid, day, tp, rows):
    for it, sec, de, an in rows:
        OA.append({**ids(k), "assessment_id": aid, "assess_dt": d(k, day), "timepoint": tp, "item": it, "section": sec,
                   "description": de, "answer": an})
oasis("p1", "OA1", 1, "01 - Start of care", [
    ("M1033", "Risk for hospitalization", "Risk for hospitalization", "1 - History of falls; 3 - 2+ hospitalizations in 6 months; 5 - Currently taking 5+ medications"),
    ("M1400", "Respiratory status", "When is the patient dyspneic", "2 - With moderate exertion"),
    ("M1850", "ADL / IADL", "Transferring", "1 - Transfers with minimal human assistance or device"),
    ("M1860", "ADL / IADL", "Ambulation / locomotion", "2 - Requires use of a two-handed device (walker)"),
    ("M2020", "Medications", "Management of oral medications", "2 - Able if another person prepares"),
    ("M1242", "Pain", "Frequency of pain interfering with activity", "1 - Less often than daily"),
    ("M1700", "Neuro / cognitive", "Cognitive functioning", "1 - Requires prompting under stressful conditions"),
    ("M1100", "Living arrangements", "Patient living situation", "Lives alone; occasional assistance"),
    ("M2102", "Care management", "Supervision and safety", "2 - Non-agency caregiver(s) need training / supportive services"),
    ("M1311", "Integumentary", "Number of stage 2 pressure ulcers", "1"),
    ("M1200", "Sensory", "Vision", "1 - Partially impaired")])
oasis("p3", "OA3", 1, "01 - Start of care", [
    ("M1860", "ADL / IADL", "Ambulation / locomotion", "2 - Requires use of a two-handed device (walker)"),
    ("M1242", "Pain", "Frequency of pain interfering with activity", "3 - Daily but not constantly"),
    ("M1850", "ADL / IADL", "Transferring", "1 - Transfers with minimal human assistance or device")])
oasis("p3", "OA3R", 30, "05 - Other follow-up", [
    ("M1860", "ADL / IADL", "Ambulation / locomotion", "1 - With a one-handed device (cane)"),
    ("M1242", "Pain", "Frequency of pain interfering with activity", "1 - Less often than daily"),
    ("M1850", "ADL / IADL", "Transferring", "0 - Able to independently transfer")])
write("oasis.csv", [r_ for r_ in OA if r_["assess_dt"] <= RUN])
write("care_network.csv", [
    {"patient_id": P["p1"][0], "has_pcp": "Y", "pcp_name": "Community Internal Medicine", "pcp_last_visit_dt": "2026-03-02",
     "ma_eligible": "Y", "cwpco_enrolled": "N", "cwrx_enrolled": "N", "nearest_cwpco_clinic": "CenterWell Senior Primary Care - Hickory",
     "nearest_cwpco_miles": 4.2, "cwpco_clinics_10mi": 2, "cwpco_clinics_20mi": 3, "active_med_count": 11},
    {"patient_id": P["p2"][0], "has_pcp": "N", "pcp_name": "", "pcp_last_visit_dt": "", "ma_eligible": "Y",
     "cwpco_enrolled": "N", "cwrx_enrolled": "Y", "nearest_cwpco_clinic": "CenterWell Senior Primary Care - Lenoir",
     "nearest_cwpco_miles": 14.8, "cwpco_clinics_10mi": 0, "cwpco_clinics_20mi": 1, "active_med_count": 8},
    {"patient_id": P["p3"][0], "has_pcp": "Y", "pcp_name": "CenterWell Senior Primary Care - Rock Hill", "pcp_last_visit_dt": "2026-07-20",
     "ma_eligible": "Y", "cwpco_enrolled": "Y", "cwrx_enrolled": "N", "nearest_cwpco_clinic": "CenterWell Senior Primary Care - Rock Hill",
     "nearest_cwpco_miles": 3.1, "cwpco_clinics_10mi": 1, "cwpco_clinics_20mi": 2, "active_med_count": 4},
])

# ---------------------------------------------------------------- medications
write("medications.csv", [{**ids(k), "med_name": m, "drug_class": c_, "high_risk": h, "start_dt": d(k, 0)} for k, m, c_, h in [
    ("p1", "Apixaban 5 mg BID", "Anticoagulant", "Y"), ("p1", "Insulin glargine 20 units nightly", "Insulin", "Y"),
    ("p1", "Furosemide 40 mg daily", "Loop diuretic", "N"), ("p1", "Metoprolol succinate 50 mg daily", "Beta blocker", "N"),
    ("p1", "Lisinopril 10 mg daily", "ACE inhibitor", "N"), ("p1", "Atorvastatin 40 mg", "Statin", "N"),
    ("p1", "Metformin 500 mg BID", "Biguanide", "N"), ("p1", "Potassium chloride 20 mEq", "Electrolyte", "N"),
    ("p1", "Pantoprazole 40 mg", "PPI", "N"), ("p1", "Acetaminophen PRN", "Analgesic", "N"), ("p1", "Docusate PRN", "Laxative", "N"),
    ("p3", "Oxycodone 5 mg PRN", "Opioid", ""), ("p3", "Aspirin 81 mg", "Antiplatelet", "N"),
    ("p3", "Celecoxib 200 mg", "NSAID", "N"), ("p3", "Senna PRN", "Laxative", "N")]])

# ---------------------------------------------------------------- risk scores (two runs -> trend)
rs = []
for k, prior, cur, lvl in [("p1", .31, .38, "high"), ("p2", .52, .55, "very_high"), ("p3", .12, .20, "moderate"), ("p4", .06, .06, "low")]:
    rs += [{**ids(k), "as_of_date": PRIOR, "score": prior, "outcome_risk_level": lvl},
           {**ids(k), "as_of_date": RUN, "score": cur, "outcome_risk_level": lvl}]
write("risk_scores.csv", rs)

DRV = {"p1": [("ip_stay_14d_pre_soc", "Inpatient stay within 14 days before SOC", .092), ("m1400_dyspnea", "Dyspnea with moderate exertion (OASIS M1400)", .071),
              ("dx_hf_primary", "Heart failure primary diagnosis", .058), ("med_count_10p_anticoag", "10+ medications incl. anticoagulant", .044),
              ("fall_3m", "Fall in the past 3 months", .037), ("lives_alone", "Lives alone, limited caregiver support", .029),
              ("m1200_vision", "Impaired vision (OASIS M1200)", .018), ("age_80p", "Age 80+", -0.004)],
       "p2": [("m1400_dyspnea_rest", "Dyspnea at rest (OASIS M1400)", .121), ("dx_copd_primary", "COPD primary diagnosis", .083),
              ("home_o2", "Home oxygen", .052), ("ed_visit_6m", "2+ ED visits in 6 months", .047), ("smoker", "Current smoker", .021)],
       "p3": [("m1860_ambulation", "Ambulation needs assistance (OASIS M1860)", .041), ("pain_daily", "Daily pain interfering with activity", .028),
              ("dx_knee_tka", "Recent knee replacement", .019)],
       "p4": [("m1860_ambulation", "Ambulation needs assistance (OASIS M1860)", .012)]}
write("risk_drivers.csv", [{**ids(k), "as_of_date": RUN, "feature": f, "label": l, "contribution": c} for k, v in DRV.items() for f, l, c in v])

# ---------------------------------------------------------------- orders (485 frequencies)
write("orders.csv", [
    {**ids("p1"), "discipline": "SN", "frequency": "3W1, 2W2, 1W6", "effective_dt": d("p1", 0)},
    {**ids("p1"), "discipline": "PT", "frequency": "1W1, 2W4, 1W3", "effective_dt": d("p1", 0)},
    {**ids("p1"), "discipline": "OT", "frequency": "1W1", "effective_dt": d("p1", 7)},
    {**ids("p1"), "discipline": "HHA", "frequency": "1W8", "effective_dt": d("p1", 7)},
    {**ids("p1"), "discipline": "MSW", "frequency": "1W1", "effective_dt": d("p1", 14)},
    {**ids("p2"), "discipline": "SN", "frequency": "3W2, 2W3, 1W4", "effective_dt": d("p2", 0)},
    {**ids("p2"), "discipline": "PT", "frequency": "2W4", "effective_dt": d("p2", 0)},
    {**ids("p3"), "discipline": "PT", "frequency": "3W3, 2W3, 1W3", "effective_dt": d("p3", 0)},
    {**ids("p3"), "discipline": "SN", "frequency": "1W2", "effective_dt": d("p3", 0)},
    {**ids("p4"), "discipline": "PT", "frequency": "2W4", "effective_dt": d("p4", 0)},
])

# ---------------------------------------------------------------- visits (completed + missed)
V, n = [], [0]
MED_CHECK = {("p1", 1): ("Y", "Y", "Y"), ("p1", 10): ("Y", "Y", "Y"), ("p1", 15): ("N", "Y", "N"), ("p1", 3): ("N", "N", ""),
             ("p1", 5): ("N", "N", ""), ("p1", 8): ("N", "Y", "Y"), ("p1", 22): ("N", "N", "N"), ("p3", 2): ("Y", "Y", "Y"),
             ("p3", 9): ("Y", "N", "Y")}
def visit(k, day, vt, status="COMPLETED", reason=""):
    n[0] += 1
    mu, mt, ma = MED_CHECK.get((k, day), ("", "", "")) if vt.startswith(("SN", "RN")) and status == "COMPLETED" else ("", "", "")
    V.append({**ids(k), "visit_id": f"V{n[0]:05d}", "visit_dt": d(k, day), "visit_type": vt, "visit_status": status, "missed_reason": reason,
              "med_understanding": mu, "med_teaching": mt, "med_adherent": ma})
SN, PT, OT, HHA, MSW = "SN11 - SN SUBSEQUENT VISIT", "PT11 - PT SUBSEQUENT VISIT", "OT10 - OT EVALUATION", "HHA01 - HHA VISIT", "MSW10 - MSW CONSULT"
for day in [1, 3, 5, 8, 10, 15, 22]: visit("p1", day, SN)
visit("p1", 17, SN, "MISSED", "Patient not home"); visit("p1", 31, SN, "MISSED", "Patient requested reschedule")
for day in [2, 9, 11, 16, 18, 24, 29]: visit("p1", day, PT)
visit("p1", 26, PT, "MISSED", "Staff availability")
for day in [10, 17, 24, 31]: visit("p1", day, HHA)
for day in [1, 2, 4, 8, 10, 12, 15, 17]: visit("p2", day, SN)
for day in [3, 9, 11, 16]: visit("p2", day, PT)
visit("p2", 14, PT, "MISSED", "Patient hospitalized")
for day in [1, 3, 5, 8, 10, 12, 15, 17, 19, 22, 24, 29, 31, 36, 38, 43]: visit("p3", day, PT)
visit("p3", 2, "RN10 - SN EVALUATION"); visit("p3", 9, SN)
for day in [1, 3, 8, 10, 15, 17, 22]: visit("p4", day, PT)
write("visits.csv", V)

# ---------------------------------------------------------------- future schedule
S = [{**ids("p1"), "sched_dt": d("p1", day), "visit_type": HHA} for day in (38, 45, 52, 59)]
S += [{**ids("p2"), "sched_dt": d("p2", day), "visit_type": t} for day, t in ((20, SN), (21, PT), (23, PT), (27, SN), (34, SN))]
S += [{**ids("p3"), "sched_dt": d("p3", day), "visit_type": PT} for day in (46, 50, 57)]
S += [{**ids("p4"), "sched_dt": d("p4", day), "visit_type": PT} for day in (29, 31)]
write("sched.csv", S)

# ---------------------------------------------------------------- vitals (long; BP as "sys/dia")
VT = []
for day, w, s, b, pl, o in zip([1, 3, 5, 8, 10, 15, 17, 22, 29, 33], [168.0, 167.6, 168.2, 168.8, 169.5, 170.4, 171.0, 172.6, 173.8, 174.3],
                               [132, 128, 136, 134, 138, 142, 140, 146, 150, 152], [78, 76, 80, 78, 82, 84, 82, 86, 88, 86],
                               [82, 86, 79, 88, 92, 90, 96, 94, 102, 98], [95, 96, 95, 94, 94, 93, 93, 92, 92, 91]):
    for nm, val in [("WEIGHT", w), ("BP", f"{s}/{b}"), ("PULSE", pl), ("SPO2", o)]:
        VT.append({**ids("p1"), "visit_dt": d("p1", day), "vital_name": nm, "vital_value": val})
for day, s, b, pl in [(1, 128, 74, 76), (8, 126, 76, 78), (15, 130, 78, 74), (22, 124, 72, 72), (36, 126, 74, 70)]:
    for nm, val in [("SYSTOLIC BP", s), ("DIASTOLIC BP", b), ("HEART RATE", pl), ("O2 SAT", 97)]:
        VT.append({**ids("p3"), "visit_dt": d("p3", day), "vital_name": nm, "vital_value": val})
write("vitals.csv", VT)   # p2 intentionally has no vitals

write("vs_alerts.csv", [
    {**ids("p1"), "alert_dt": d("p1", 29), "alert_text": "Weight above parameter (+5.4 lb from baseline)", "physician_notified": "N"},
    {**ids("p1"), "alert_dt": d("p1", 29), "alert_text": "Pulse 102 above parameter (100)", "physician_notified": "Y"},
    {**ids("p2"), "alert_dt": d("p2", 12), "alert_text": "SpO2 87% on 2L below parameter (88%)", "physician_notified": "N"},
])
write("client_logs.csv", [
    {**ids("p1"), "log_dt": d("p1", 17), "log_type": "PATIENT NOT HOME", "log_note": "Patient not home for SN visit; voicemail left"},
    {**ids("p1"), "log_dt": d("p1", 31), "log_type": "RESCHEDULE", "log_note": "Patient requested reschedule; not rebooked"},
    {**ids("p2"), "log_dt": d("p2", 14), "log_type": "HOSPITALIZED", "log_note": "Daughter called: patient in ED overnight, discharged home"},
    {**ids("p3"), "log_dt": d("p3", 20), "log_type": "GENERAL", "log_note": "Equipment delivered"},
])
write("ip_events.csv", [
    {"patient_id": P["p1"][0], "admit_dt": d("p1", -6), "event_desc": "HF exacerbation; discharged 2 days before SOC"},
    {"patient_id": P["p2"][0], "admit_dt": d("p2", 13), "event_desc": "ED visit, COPD exacerbation; treat and release"},
    {"patient_id": P["p2"][0], "admit_dt": "2025-11-02", "event_desc": "Old admission - outside window, must be excluded"},
])

# ---------------------------------------------------------------- LLM outputs
p1_tiles = [{"key": "care", "status": "Act now", "points": ["7 ordered visits remain (4 SN, 3 PT); 0 scheduled", "2 SN and 1 PT visits missed in weeks 3-5", "OT evaluation and MSW consult not completed"]},
            {"key": "risk", "status": "Act now", "points": ["60-day risk 0.38; rising +0.07 vs. prior week", "Top drivers: recent inpatient stay, dyspnea, HF", "Care gaps flagged as raising risk"]},
            {"key": "vitals", "status": "Act now", "points": ["Weight +6.3 lb over 3 weeks (168.0 to 174.3)", "SpO2 drifting down 95% to 91%; pulse up to 102", "No vitals since day 33; no SN visit scheduled"]},
            {"key": "goals", "status": "Monitor", "points": ["6 care-plan goals: 1 achieved, 2 in progress", "2 delayed (daily weights, safe transfers)", "1 missed (MSW consult within 3 weeks)"]}]
p1_actions = [{"action": "Call patient and notify MD of weight gain; review diuretic order and HF zone plan", "owner": "SN / CM", "when": "Within 24 hrs", "evidence_section": "C"},
              {"action": "Schedule all remaining ordered visits (SN wks 6-9, PT wks 6-8)", "owner": "Scheduling", "when": "Within 24 hrs", "evidence_section": "B"},
              {"action": "Obtain order and complete OT home-safety evaluation; MSW consult for living-alone support", "owner": "CM / OT / MSW", "when": "This week", "evidence_section": "D"},
              {"action": "Anticoagulant-focused med reconciliation with large-print list and teach-back", "owner": "SN", "when": "Next SN visit", "evidence_section": "D"}]
base = {"frequency_status": "", "schedule_status": "", "risk_raised_by_care_gaps": "", "delay_clinical_impact": "", "compliance_window_missed": "",
        "outcomes_at_risk": "", "risk_rationale": "", "care_delivery_summary": "", "schedule_summary": "", "vitals_summary": "",
        "patient_stated_goal": "", "interventions_text": "", "comorbidities": "", "living_situation": "", "headline": "", "tiles_json": "",
        "actions_json": "", "vitals_interp_json": "", "judge_status": "", "llm_model": "",
        "profile_risk_summary": "", "missed_care_summary": "", "delay_impact_explanation": "", "care_plan_goal": "", "data_gaps_summary": "",
        "assessments_summary": "", "interventions_summary": "", "adverse_events_summary": "", "visit_themes": "",
        "missing_interventions_json": "", "missing_assessments_json": "", "visit_goals_json": "",
        "glance_found": "", "glance_risk": "", "glance_care_delivery": "", "glance_trajectory": "", "cqi_flags": "",
        "vitals_trajectory_class": "", "vitals_saw": "", "vitals_flags": "", "action_window": "",
        "wound_summary": "", "diagnosis_summary": "", "oasis_summary": "",
        "discharge_recommendation": "", "discharge_rationale": "", "medication_summary": ""}
write("llm_summary.csv", [
    {**ids("p1"), "as_of_date": RUN, **base, "frequency_status": "minor_shortfall", "schedule_status": "no_future_schedule",
     "risk_raised_by_care_gaps": "true", "delay_clinical_impact": "moderate", "compliance_window_missed": "false",
     "outcomes_at_risk": "hospitalization,fall,medication_adverse_event",
     "risk_rationale": "Baseline risk is high: recent HF hospitalization, exertional dyspnea, anticoagulant use and a recent fall in a patient who lives alone with impaired vision. Risk has risen because SN and PT visits fell behind in weeks 3-5, the OT evaluation was not completed, and no future visits are on the schedule while weight is trending up.",
     "care_delivery_summary": "Week 1 front-loading was met for SN and PT. SN fell short in weeks 3 and 5 and PT in weeks 4-5; neither shortfall was made up. The OT evaluation ordered for week 2 and the PRN MSW consult have no visit record.",
     "schedule_summary": "Seven ordered visits remain in weeks 6-9 (4 SN, 3 PT) and none are scheduled; only HHA visits are on the calendar.",
     "vitals_summary": "Ten vitals days in 33 days. No readings since DAY+33 and no SN visit scheduled. Weight alert on DAY+29 has no documented physician notification. CQI recommends a same-day phone check and MD notification.",
     "patient_stated_goal": "I want to stay at home and live as long as I can.",
     "interventions_text": "[SN] Anticoagulant-focused medication reconciliation with a large-print list for impaired vision. [SN] HF zone teaching with teach-back; reinforce daily weights and when to call. [CM] Obtain or confirm orders for OT home-safety evaluation and MSW consult. [PT] Resume gait and transfer training toward the walker goal.",
     "comorbidities": "Type 2 diabetes; CKD stage 3; atrial fibrillation on anticoagulant; impaired vision", "living_situation": "Lives alone; daughter visits weekly",
     "headline": "High and rising hospitalization risk, weight up 6.3 lb in 3 weeks, and no future visits scheduled for the 7 ordered visits that remain.",
     "tiles_json": json.dumps(p1_tiles), "actions_json": json.dumps(p1_actions),
     "vitals_interp_json": json.dumps({"weight": "Steady gain with edema noted DAY+33; possible fluid retention", "spo2": "Gradual decline, still above limit; consistent with weight trend",
                                       "pulse": "Crossed 100 on DAY+29; AFib - confirm apical rate and rhythm", "sbp": "Rising but below limit; recheck with HF review"}),
     "care_plan_goal": "PATIENT WILL VERBALIZE AND DEMONSTRATE DAILY WEIGHT MONITORING AND 3 SIGNS OF HEART FAILURE EXACERBATION AND WHEN TO CALL THE AGENCY WITHIN 2 WEEKS",
     "profile_risk_summary": "82-year-old woman readmitted after a heart failure hospitalization that ended 2 days before start of care, with atrial fibrillation on an anticoagulant, type 2 diabetes, CKD stage 3, impaired vision and a fall in the past 3 months. She lives alone; her daughter visits weekly. SN fell short in weeks 3 and 5 and PT in weeks 4-5, and no SN or PT visits are scheduled for the rest of the period. Risk Rationale: Baseline risk is high due to the recent HF hospitalization, exertional dyspnea, polypharmacy with an anticoagulant and fall history in a patient who lives alone. Care gaps - missed SN visits during a period of weight gain and zero future skilled visits - raise hospitalization risk by reducing clinical surveillance of fluid status and medication safety.",
     "missed_care_summary": "Missed Care Impact: Two SN visits (DAY+17 patient not home; DAY+31 patient requested reschedule) and one PT visit (DAY+26 staff availability) were missed and not made up. The DAY+31 SN miss came two days after a weight alert with no documented physician notification, so a key chance to assess fluid status was lost. The OT evaluation and PRN MSW consult have no visit record. For a patient who lives alone with impaired vision, these gaps matter more than the visit count suggests.",
     "delay_impact_explanation": "ACUITYSTATUS: Acuity level 2 requires the first visit within 48 hours; the first SN visit occurred on DAY+1, within the window. Delay impact is rated moderate because later SN gaps coincided with a rising weight trend rather than because of the start-of-care timing.",
     "data_gaps_summary": "OT evaluation ordered for week 2 has no visit, missed-visit record or cancellation.|Weight alert on DAY+29 has no physician-notification record and no follow-up note.|Pulse oximeter is listed as a needed supply but no delivery or in-home documentation was found.|Edema noted on DAY+33 has no measurement or comparison to baseline.|Living situation says the daughter visits weekly but no caregiver contact or availability is recorded.|Apixaban teaching is marked partial on DAY+15 with no later update.",
     "assessments_summary": "SN completed cardiopulmonary and edema assessments at every SN visit and medication review twice. Fall-risk (MAHC-10) was done at SOC only. Pain and depression screens were done at SOC and not repeated. No home-safety assessment has been documented because the OT evaluation was not delivered.",
     "interventions_summary": "Most interventions targeted HF self-management: HF teaching (5 visits), daily-weight teaching (3), medication teaching (3) and gait/transfer training by PT (6). Anticoagulant bleeding-precaution teaching was documented once. No intervention addressed the living-alone risk, and no diuretic or weight-escalation intervention was documented after the DAY+29 weight alert.",
     "adverse_events_summary": "One near-fall on DAY+19 (caught by daughter, no injury) and a weight alert on DAY+29 without documented physician notification. No ED visit or hospitalization since SOC.",
     "visit_themes": "Shortness of breath when walking to the bathroom|Forgets to weigh on days the daughter does not visit|Worried about bleeding on apixaban|Wants to stay at home and avoid another hospital stay|Difficulty reading medication labels",
     "missing_interventions_json": json.dumps([
        {"goal": "Weigh daily and keep a log", "intervention": "Large-print weight log and daily reminder call; escalate gain >3 lb/2 days to MD", "discipline": "SN"},
        {"goal": "Safe transfers with walker within 4 weeks", "intervention": "Home exercise program for sit-to-stand with written large-print copy", "discipline": "PT"},
        {"goal": "Home-safety evaluation completed", "intervention": "OT home-safety evaluation (grab bars, rugs, lighting)", "discipline": "OT"},
        {"goal": "MSW consult for social and community resources within 3 weeks", "intervention": "MSW visit for meal delivery and personal emergency response system", "discipline": "MSW"}]),
     "missing_assessments_json": json.dumps([
        {"item": "Orthostatic blood pressure", "type": "Vitals", "reason": "Near-fall on DAY+19 while on diuretic"},
        {"item": "Repeat fall-risk (MAHC-10)", "type": "Assessment", "reason": "Last done at SOC; near-fall since"},
        {"item": "Lung sounds / edema grading each SN visit", "type": "Assessment", "reason": "Weight trend up; edema noted DAY+33 without grade"},
        {"item": "Daily home weights reported by phone", "type": "Vitals", "reason": "No SN visit scheduled; weight is the earliest HF warning sign"}]),
     "visit_goals_json": json.dumps([
        {"discipline": "SN", "visit_goal": "Patient states HF zones and when to call using the large-print zone sheet", "supports_goal": "Stay at home; HF self-management"},
        {"discipline": "SN", "visit_goal": "Weight log reviewed every visit; MD notified for gain >3 lb/2 days", "supports_goal": "Weigh daily and keep a log"},
        {"discipline": "PT", "visit_goal": "Sit-to-stand x5 with walker without loss of balance", "supports_goal": "Safe transfers with walker"},
        {"discipline": "MSW", "visit_goal": "Community resources and emergency response set up", "supports_goal": "Stay at home living alone"}]),
     "glance_found": "Weight up 6.3 lb in 3 weeks, possible fluid retention; 7 ordered visits remain and none are scheduled.",
     "glance_risk": "Readmission after HF hospitalization; AFib on anticoagulant, recent fall, lives alone with impaired vision.",
     "glance_care_delivery": "Acuity window met. SN short in weeks 3 and 5, PT in weeks 4-5; OT evaluation and MSW consult not done.",
     "glance_trajectory": "Weight up 6.3 lb over 3 weeks with new ankle edema; SpO2 drifting 95% to 91%. No vitals since DAY+33.",
     "cqi_flags": "Call the patient and notify MD of the weight gain today. Schedule all remaining SN and PT visits and escalate to the clinical manager.",
     "vitals_trajectory_class": "deteriorating",
     "vitals_saw": "Weight up 6.3 lb over 3 weeks; SpO2 95% to 91%; pulse crossed 100 on DAY+29; no readings since DAY+33.",
     "vitals_flags": "Possible fluid retention. Clinician review within 24 hours; no SN visit scheduled is inadequate monitoring.",
     "action_window": "Clinician review within 24 hours",
     "medication_summary": "11 active medications including two high-risk: apixaban (bleeding risk) and insulin glargine (hypoglycemia). Understanding checked on 2 of 7 SN visits, last DAY+10. Missed doses reported DAY+15 and DAY+22; impaired vision makes labels hard to read.",
     "wound_summary": "One stage 2 sacral pressure injury, area down 33% since SOC and assessed weekly. A skin tear on the left forearm from the DAY+19 near-fall has no follow-up measurement.",
     "diagnosis_summary": "Primary HF (I50.9) with exacerbation onset before SOC. Comorbid AFib on apixaban, type 2 diabetes and CKD stage 3. No procedure codes this episode.",
     "oasis_summary": "SOC OASIS shows dyspnea with moderate exertion, ambulation with a device, and 5+ medications. Fall-risk and caregiver items (M2102) suggest the patient needs supervision support that is not in the plan.",
     "judge_status": "adjudicated", "llm_model": "<llm-endpoint>"},
    # p2: LLM step has not run -> only key ids; composer must build page 1
    {**ids("p2"), "as_of_date": RUN, **base},
    {**ids("p3"), "as_of_date": RUN, **base, "frequency_status": "on_track", "schedule_status": "needs_change", "risk_raised_by_care_gaps": "false",
     "delay_clinical_impact": "low", "compliance_window_missed": "false",
     "risk_rationale": "Moderate risk driven by ambulation assistance and daily pain after knee replacement; risk rose this week as pain scores increased.",
     "patient_stated_goal": "Walk to church by myself again.", "interventions_text": "[PT] Progress gait training and reassess pain plan with MD.",
     "data_gaps_summary": "Recertification on DAY+60 is not reflected in the scheduled-visit data.|Pain scores are referenced in the risk drivers but no pain assessment values are in the payload.",
     "care_plan_goal": "PATIENT WILL AMBULATE 300 FT WITH CANE ON LEVEL SURFACES WITH MODIFIED INDEPENDENCE WITHIN 6 WEEKS",
     "vitals_summary": "Vitals are stable across five readings: SBP 124-130, pulse 70-78, SpO2 97%. No escalation is needed; continue routine monitoring at PT visits.",
     "care_delivery_summary": "PT met or exceeded ordered frequency in every completed week, and the SN evaluation and follow-up were completed. Schedule: three PT visits are scheduled for weeks 7-9 against three ordered, but the week 7 visit falls on DAY+46 and needs confirmation after the recertification.",
     "judge_status": "passed", "llm_model": "<llm-endpoint>"},
])
write("llm_goals.csv", [{**ids("p1"), "as_of_date": RUN, "goal": g, "discipline": dsc, "status": s, "evidence": e} for g, dsc, s, e in [
    ("Verbalize 3 signs of HF exacerbation within 2 weeks", "SN", "achieved", "Teach-back documented DAY+10"),
    ("Weigh daily and keep a log", "SN", "delayed", "Log incomplete on 3 of 5 SN visits"),
    ("Verbalize anticoagulant bleeding precautions within 3 weeks", "SN", "in_progress", "Partially documented DAY+15"),
    ("Safe transfers with walker within 4 weeks", "PT", "delayed", "PT shortfall weeks 4-5"),
    ("Home-safety evaluation completed", "OT", "in_progress", "OT evaluation not yet delivered"),
    ("MSW consult for social and community resources within 3 weeks", "MSW", "missed", "No MSW visit record"),
    ("Ambulate 150 ft with walker, supervision, within 4 weeks", "PT", "achieved", "150 ft with walker DAY+24"),
    ("Independent with lower-body dressing using adaptive equipment", "OT", "not_started", "OT evaluation not delivered")]] +
  [{**ids("p3"), "as_of_date": RUN, "goal": "Ambulate 300 ft with cane within 6 weeks", "discipline": "PT", "status": "in_progress", "evidence": "Ambulating 150 ft DAY+36"}])
write("alignment.csv", [{**ids("p1"), "as_of_date": RUN, "item": i, "type": t, "supports_goal": g, "check": c, "flag": f} for i, t, g, c, f in [
    ("Furosemide 40 mg daily", "Medication", "Daily weights / HF goal", "Order aligned; weight trend not escalated", "Y"),
    ("Apixaban 5 mg BID", "Medication", "Bleeding precautions", "Teaching in progress", "N"),
    ("Digital scale", "Supply", "Daily weights", "Present, documented DAY+1", "N"),
    ("Pulse oximeter", "Supply", "Dyspnea monitoring", "Not documented", "Y"),
    ("Front-wheeled walker", "DME", "Safe transfers", "Present", "N")]])
