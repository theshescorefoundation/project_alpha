"""CQI booklet payload helpers (pure Python, no Spark).

compose(payload)   -> fills empty page-1 fields (headline, tiles, actions) with rule-based text from facts
validate(payload)  -> JSON Schema check (config/cqi_booklet_schema.json); returns a list of error strings
to_render(payload) -> converts a payload into the dict build_booklet.py draws
"""
import json, math, os, re
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "..", "config", "cqi_booklet_schema.json")
DEFAULT_LIMITS = {"sbp_high": 160, "pulse_high": 100, "pulse_low": 50, "spo2_low": 90, "weight_gain_lb": 5}
def _brand():
    try:
        import yaml
        return yaml.safe_load(open(os.environ.get("CQI_BRANDING", os.path.join(HERE, "..", "config", "branding.yaml")))) or {}
    except Exception:
        return {}
_B = _brand()
PRODUCED_BY = _B.get("produced_by") or "CW Data Science & Applied AI - HH Team"
PREPARED_FOR = _B.get("prepared_for") or "CWHH Clinical Quality Team"
URGENCY_RANK = {"Within 24 hrs": 0, "This week": 1, "Next SN visit": 2, "Next visit": 2, "Routine": 3}

# Page order of the booklet. Page numbers everywhere are derived from this list.
# Booklet sections. Same grouping as the Contents page; each section after "Start here" opens with a divider page.
#   (id, name, one-line description, divider page key or None, page keys in order)
SECTIONS = [
    ("start", "Start here", "Orientation and the one-page view", None,
     ["cover", "about", "guide", "glance", "summary"]),
    ("analysis", "CQI analysis", "What CQI found, concluded and recommends", "divA",
     ["narratives", "narratives2", "DC", "A2", "C", "D", "M", "F", "G"]),
    ("evidence", "Evidence", "The records behind the findings, and how CQI used them", "divE",
     ["A", "DX", "W", "OA", "B", "B2", "C2", "E", "methods"]),
    ("input", "Your input", "Fillable on screen: feedback, decision, notes", "divI",
     ["feedback", "notes"]),
]
PAGES = [k for _, _, _, div, keys in SECTIONS for k in ([div] if div else []) + keys]
PAGE_SECTION = {k: sid for sid, _, _, div, keys in SECTIONS for k in ([div] if div else []) + keys}
# Short customer version: cover, at a glance, vitals action window (no forms, no evidence pages)
BRIEF_PAGES = ["cover", "glance", "DC", "C"]
# Pages that only print when they apply (dropped otherwise; page numbers close up):
#   DC = nearing discharge, W = patient has wounds, M = medication data present
CONDITIONAL_PAGES = {"DC", "W", "M"}
PAGE_NO = {k: i + 1 for i, k in enumerate(PAGES)}
TILE_SECTION = {"risk": "A2", "care": "B", "vitals": "C", "goals": "D"}

# Clinician feedback form rows: (field key, label, page keys). Field names in the PDF are
# cqi__<patient_id>__<episode_id>__<key>_rating | _err_D/_err_I/_err_C/_err_N | _comment
FEEDBACK_FINDINGS = [
    ("r15", "At a glance + CQI flags", ["glance"]),
    ("r01", "Summary page + actions", ["summary"]),
    ("r02", "Profile & Risk Summary", ["narratives", "A"]),
    ("r03", "Risk tier and drivers", ["A2"]),
    ("r04", "Vitals + action window", ["narratives", "C"]),
    ("r05", "Care Delivery Summary", ["narratives2", "B"]),
    ("r06", "Missed Care Summary", ["narratives2", "B2"]),
    ("r07", "Delay Impact Explanation", ["narratives2", "B"]),
    ("r08", "Data Gaps Found", ["narratives2"]),
    ("r09", "Goals met / not met", ["D"]),
    ("r10", "Medications and supplies", ["F"]),
    ("r12", "Assessments + interventions", ["E"]),
    ("r13", "Adverse events + themes", ["E"]),
    ("r14", "Care plan gaps to add", ["F"]),
    ("r16", "Diagnoses, wounds, OASIS", ["DX", "W", "OA"]),
    ("r18", "Medication adherence", ["M"]),
    ("r17", "Care network + referrals", ["G"]),
    ("r11", "Discharge / recert recommendation", ["DC"]),   # only when flagged
]
ERROR_TYPES = {"D": "Data missing", "I": "Data incorrect", "C": "Clinical interpretation", "N": "Not relevant"}

# Values that print as alerts (plum) or reassuring (green) in status strips
ALERT_VALUES = {"no_future_schedule", "needs_change", "high", "very_high", "minor_shortfall", "behind",
                "significantly_behind", "significant_shortfall", "true", "yes", "moderate"}
OK_VALUES = {"on_track", "scheduled", "false", "no", "low", "not applicable"}


# ----------------------------------------------------------------- LLM text limits
# Recommended maxima so text prints at full size (no shrinking or truncation). Keep in sync with
# config/llm_output_spec.md. Schema maxLength values are hard limits; these are the design targets.
TEXT_LIMITS = {   # path: (max_chars, max_items or None)
    "summary.headline": (200, None),
    "summary.tiles.points": (70, 3),
    "summary.actions.action": (110, 4),
    "narratives.patient_goal": (250, None),
    "narratives.profile_risk": (1200, None),
    "narratives.vitals_trajectory": (900, None),
    "narratives.care_delivery": (700, None),
    "narratives.missed_care": (700, None),
    "narratives.delay_impact": (450, None),
    "narratives.data_gaps": (110, 6),          # per item, items separated by "|"
    "goals.interventions.text": (220, 4),
    "goals.items.evidence": (80, None),
    "vitals.findings.interpretation": (90, None),
    "risk.rationale": (600, None),
    "care_review.assessments_summary": (400, None),
    "care_review.interventions_summary": (400, None),
    "care_review.adverse_events_summary": (250, None),
    "care_review.visit_themes": (80, 6),
    "gap_analysis.missing_interventions": (110, 6),     # per intervention text
    "gap_analysis.missing_assessments": (90, 6),        # per reason text
    "gap_analysis.visit_goals": (100, 6),               # per visit goal text
    "at_a_glance.found": (110, None),
    "at_a_glance.risk": (160, None),
    "at_a_glance.care_delivery": (160, None),
    "at_a_glance.trajectory": (180, None),
    "at_a_glance.flags": (200, None),
    "vitals.trajectory.saw": (150, None),
    "vitals.trajectory.flags": (150, None),
    "vitals.trajectory.action_window": (50, None),
    "clinical.wound_summary": (400, None),
    "clinical.diagnosis_summary": (400, None),
    "clinical.oasis_summary": (400, None),
    "discharge_recommendation.rationale": (400, None),
}


def length_warnings(p):
    """Soft checks against TEXT_LIMITS -> list of strings for the QC file (never blocks rendering)."""
    out = []
    def chk(path, text, lim):
        if text and len(text) > lim:
            out.append(f"{path}: {len(text)} chars > {lim}")
    n, s = p.get("narratives") or {}, p.get("summary") or {}
    chk("summary.headline", s.get("headline"), TEXT_LIMITS["summary.headline"][0])
    for t in s.get("tiles") or []:
        for pt in t.get("points") or []:
            chk(f"summary.tiles.{t['key']}", pt, 70)
    acts = s.get("actions") or []
    if len(acts) > 4: out.append(f"summary.actions: {len(acts)} items > 4")
    for a in acts: chk("summary.actions.action", a.get("action"), 110)
    for k in ("patient_goal", "profile_risk", "vitals_trajectory", "care_delivery", "missed_care", "delay_impact"):
        chk(f"narratives.{k}", n.get(k), TEXT_LIMITS[f"narratives.{k}"][0])
    gaps = split_gaps(n.get("data_gaps"))
    if len(gaps) > 6: out.append(f"narratives.data_gaps: {len(gaps)} items > 6")
    for g in gaps: chk("narratives.data_gaps item", g, 110)
    g = p.get("goals") or {}
    iv = g.get("interventions") or []
    if len(iv) > 4: out.append(f"goals.interventions: {len(iv)} items > 4")
    for x in iv: chk("goals.interventions.text", x.get("text"), 220)
    for x in g.get("items") or []: chk("goals.items.evidence", x.get("evidence"), 80)
    for x in (p.get("vitals") or {}).get("findings") or []: chk("vitals.findings.interpretation", x.get("interpretation"), 90)
    chk("risk.rationale", (p.get("risk") or {}).get("rationale"), 600)
    cr, ga = p.get("care_review") or {}, p.get("gap_analysis") or {}
    for k in ("assessments_summary", "interventions_summary", "adverse_events_summary"):
        chk(f"care_review.{k}", cr.get(k), TEXT_LIMITS[f"care_review.{k}"][0])
    th = cr.get("visit_themes") or []
    if len(th) > 6: out.append(f"care_review.visit_themes: {len(th)} items > 6")
    for t in th: chk("care_review.visit_themes item", t, 80)
    for key, field in (("missing_interventions", "intervention"), ("missing_assessments", "reason"), ("visit_goals", "visit_goal")):
        rows = ga.get(key) or []
        lim, mx = TEXT_LIMITS[f"gap_analysis.{key}"]
        if len(rows) > mx: out.append(f"gap_analysis.{key}: {len(rows)} items > {mx}")
        for r_ in rows: chk(f"gap_analysis.{key}.{field}", r_.get(field), lim)
    for k_ in ("found", "risk", "care_delivery", "trajectory", "flags"):
        chk(f"at_a_glance.{k_}", (p.get("at_a_glance") or {}).get(k_), TEXT_LIMITS[f"at_a_glance.{k_}"][0])
    for k_ in ("wound_summary", "diagnosis_summary", "oasis_summary"):
        chk(f"clinical.{k_}", (p.get("clinical") or {}).get(k_), TEXT_LIMITS[f"clinical.{k_}"][0])
    for k_ in ("saw", "flags", "action_window"):
        chk(f"vitals.trajectory.{k_}", ((p.get("vitals") or {}).get("trajectory") or {}).get(k_), TEXT_LIMITS[f"vitals.trajectory.{k_}"][0])
    return out


# ----------------------------------------------------------------- validation
def validate(payload, schema_path=SCHEMA):
    import jsonschema
    schema = json.load(open(schema_path))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path))
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]


# ----------------------------------------------------------------- care metrics
def care_metrics(cd):
    cw = cd["current_week"]
    rem = sch = 0
    short = {}
    for d in cd["disciplines"]:
        s = 0
        for w in d["weeks"]:
            if w["week"] < cw:
                s += max(w["ordered"] - (w.get("delivered") or 0), 0)
            else:
                rem += w["ordered"]
                sch += min(w.get("scheduled") or 0, w["ordered"]) if w["ordered"] else 0
        if s:
            short[d["discipline"]] = s
    return rem, sch, short


# ----------------------------------------------------------------- composer
def _tile_care(p):
    cd = p["care_delivery"]
    rem, sch, short = care_metrics(cd)
    fs, ss = cd.get("frequency_status") or "", cd.get("schedule_status") or ""
    pts = [f"{rem} ordered visits remain; {sch} scheduled"]
    pts.append(("Shortfall to date: " + ", ".join(f"{k} {v}" for k, v in short.items())) if short else "No visit shortfall to date")
    if cd.get("compliance_window_missed"):
        pts.append("Acuity window for first visit was missed")
    elif fs:
        pts.append(f"Frequency status: {fs.replace('_', ' ')}")
    act = ss == "no_future_schedule" or fs in ("significantly_behind", "behind") or (rem > 0 and sch == 0)
    mon = bool(short) or ss == "needs_change" or fs == "minor_shortfall" or sch < rem
    return {"key": "care", "status": "Act now" if act else "Monitor" if mon else "On track", "points": pts[:3]}


def _risk_labels(r):
    pct = r.get("percentile")
    pct_lbl = f"Top {max(1, math.ceil(100 - pct))}%" if pct is not None else "n/a"
    prior = r.get("score_prior")
    if prior is None:
        trend = "No prior run"
    else:
        dlt = r["score"] - prior
        trend = f"{'Rising' if dlt > 0.005 else 'Falling' if dlt < -0.005 else 'Stable'} {dlt:+.2f} vs. prior run"
    return pct_lbl, trend


def _tile_risk(p):
    r = p["risk"]
    pct_lbl, trend = _risk_labels(r)
    pts = [f"60-day risk {r['score']:.2f}, {pct_lbl.lower()}; {trend.split(' vs')[0].lower()}"]
    if r.get("drivers"):
        short = lambda t: t.split(" (")[0][:30]
        pts.append("Top drivers: " + "; ".join(short(d["label"]) for d in r["drivers"][:2]))
    if r.get("risk_raised_by_care_gaps") is not None:
        pts.append("Care gaps flagged as raising risk" if r["risk_raised_by_care_gaps"] else "Care gaps not raising risk")
    rising = r.get("score_prior") is not None and r["score"] - r["score_prior"] > 0.005
    act = r["outcome_risk_level"] == "very_high" or (r["outcome_risk_level"] == "high" and (rising or r.get("risk_raised_by_care_gaps")))
    return {"key": "risk", "status": "Act now" if act else "Monitor", "points": pts[:3]}


def _tile_vitals(p):
    v = p.get("vitals") or {}
    if not v.get("readings"):
        return {"key": "vitals", "status": "Monitor", "points": ["No vitals found for this episode", "Confirm monitoring plan with SN"]}
    f = sorted(v.get("findings") or [], key=lambda x: URGENCY_RANK.get(x["urgency"], 9))
    pts = [f"{x['measure'].split(' (')[0]}: {x.get('change') or 'n/a'} ({x.get('first')} to {x.get('latest')})" for x in f[:2]]
    last_day = max(x["episode_day"] for x in v["readings"])
    gap = p["patient"]["episode_day"] - last_day
    pts.append(f"Last vitals day {last_day}" + (f" ({gap} days ago)" if gap > 0 else ""))
    top = f[0]["urgency"] if f else "Routine"
    return {"key": "vitals", "status": "Act now" if top == "Within 24 hrs" else "Monitor" if top == "This week" else "On track",
            "points": pts[:3]}


def _tile_goals(p):
    g = (p.get("goals") or {}).get("items") or []
    if not g:
        return {"key": "goals", "status": "Monitor", "points": ["No care-plan goals found", "May be a documentation gap"]}
    cnt = {}
    for x in g:
        cnt[x["status"]] = cnt.get(x["status"], 0) + 1
    pts = [f"{len(g)} care-plan goals: {cnt.get('Achieved', 0)} achieved, {cnt.get('In progress', 0)} in progress"]
    if cnt.get("Delayed"):
        pts.append(f"{cnt['Delayed']} delayed")
    if cnt.get("Missed"):
        pts.append(f"{cnt['Missed']} missed")
    return {"key": "goals", "status": "Monitor" if cnt.get("Delayed") or cnt.get("Missed") else "On track", "points": pts[:3]}


# ----------------------------------------------------------------- medication adherence
HIGH_RISK_CLASSES = ("anticoagulant", "antiplatelet", "insulin", "sulfonylurea", "opioid", "digoxin", "antiarrhythmic",
                     "chemotherapy", "immunosuppress", "benzodiazepine", "antipsychotic")
MED_CHECK_DAYS = 14        # high-risk meds need an understanding check at least this often


def medication_flag(high_risk, last_check_day, episode_day, adherence_concerns=None):
    if adherence_concerns:
        return f"Adherence concern documented on {adherence_concerns} visit(s)."
    if not high_risk:
        return None
    if last_check_day is None:
        return "High-risk medications and no medication-understanding check documented this episode."
    if episode_day - last_check_day > MED_CHECK_DAYS:
        return f"High-risk medications and no medication-understanding check in {episode_day - last_check_day} days."
    return None


# ----------------------------------------------------------------- care network / referral rules
PCO_NEAR_MI, PCO_FAR_MI, RX_MIN_MEDS = 10, 20, 5
OASIS_KEY_ITEMS = ["M1033", "M1400", "M1800", "M1810", "M1820", "M1830", "M1840", "M1850", "M1860", "M2020",
                   "M1242", "M1700", "M1710", "M1720", "M1100", "M2102", "M1311", "M1324", "M1330", "M2001"]


def referral_indicators(cn, near_discharge=False):
    """CW PCO / CW Rx referral indicator from enrollment, eligibility, distance and med count.
    Returns (refer_cwpco, refer_cwrx) dicts {indicator, reason}. Rules are deliberately simple and listed on the page."""
    def ind(i, r): return {"indicator": i, "reason": r}
    elig = cn.get("ma_eligible")
    # CW PCO
    miles = cn.get("nearest_cwpco_miles")
    if cn.get("cwpco_enrolled"):
        pco = ind("Already enrolled", "Patient is a CW PCO patient; send the discharge summary to the CW PCO team.")
    elif elig is False:
        pco = ind("Not indicated", "Not eligible for CW PCO based on plan.")
    elif miles is None or (cn.get("cwpco_clinics_20mi") or 0) == 0 and (miles is None or miles > PCO_FAR_MI):
        pco = ind("Not indicated", f"No CW PCO clinic within {PCO_FAR_MI} miles.")
    elif miles <= PCO_NEAR_MI:
        why = "no PCP on record" if cn.get("has_pcp") is False else "clinic close by"
        pco = ind("Refer", f"CW PCO clinic {miles:.1f} mi away; {why}.")
    else:
        pco = ind("Consider", f"Nearest CW PCO clinic {miles:.1f} mi away (within {PCO_FAR_MI} mi); confirm travel / transport.")
    if pco["indicator"] in ("Refer", "Consider") and elig is None:
        pco = ind("Check eligibility", pco["reason"] + " Eligibility unknown.")
    # CW Rx
    meds = cn.get("active_med_count")
    if cn.get("cwrx_enrolled"):
        rx = ind("Already enrolled", "Patient uses CW Rx; confirm discharge medication list is shared.")
    elif elig is False:
        rx = ind("Not indicated", "Not eligible for CW Rx based on plan.")
    elif meds is not None and meds >= RX_MIN_MEDS:
        rx = ind("Refer" if elig else "Check eligibility", f"{meds} active medications; home delivery and pharmacist review support adherence.")
    elif meds is None:
        rx = ind("Check eligibility", "Medication count unknown.")
    else:
        rx = ind("Not indicated", f"{meds} active medications (below {RX_MIN_MEDS}).")
    return pco, rx


def network_line(cn):
    """One-line care network status for At a glance / discharge banner."""
    if not cn:
        return None
    pcp = "PCP on record" if cn.get("has_pcp") else ("No PCP on record" if cn.get("has_pcp") is False else "PCP unknown")
    return (f"{pcp}  |  CW PCO: {(cn.get('refer_cwpco') or {}).get('indicator', 'n/a')}"
            f"  |  CW Rx: {(cn.get('refer_cwrx') or {}).get('indicator', 'n/a')}")


def page_numbers(include=(), keys=None):
    """include: conditional page keys that apply to this patient (see applicable_pages)."""
    include = set(include or ())
    keys = [k for k in (keys or PAGES) if k not in CONDITIONAL_PAGES or k in include]
    return {k: i + 1 for i, k in enumerate(keys)}


def applicable_pages(p):
    """Which conditional pages this payload needs."""
    inc = set()
    if (p["patient"].get("discharge_window") or {}).get("flag"): inc.add("DC")
    if ((p.get("clinical") or {}).get("wounds")): inc.add("W")
    m = p.get("medications") or {}
    documented = any(x.get(k) is not None for x in m.get("checks") or [] for k in ("understanding", "teaching", "adherent"))
    if m.get("high_risk") or m.get("active_count") or documented: inc.add("M")
    return inc


DC_OPTIONS = ("Discharge", "Discharge with referrals", "Recertify", "Clinical review")
LOWER_IS_BETTER = ("M18", "M1242", "M2020", "M1400", "M1700", "M1710", "M1720")


def _code(ans):
    m = re.match(r"\s*(\d+)", str(ans or ""))
    return int(m.group(1)) if m else None


def discharge_recommendation(p):
    """System recommendation for episodes nearing their end: Discharge / Discharge with referrals / Recertify / Clinical review.
    Rule-based from facts in the payload; LLM text (recommendation, rationale) is kept when present.
    Returns None when the episode is not in the discharge window."""
    pt = p["patient"]
    if not (pt.get("discharge_window") or {}).get("flag"):
        return None
    llm = p.get("discharge_recommendation") or {}
    recert, dc, data = [], [], []
    goals = (p.get("goals") or {}).get("items") or []
    open_g = [g for g in goals if g["status"] != "Achieved"]
    if goals and not open_g:
        dc.append("Care-plan goal met." if len(goals) == 1 else f"All {len(goals)} care-plan goals met.")
    elif open_g:
        names = "; ".join(g["goal"][:48] for g in open_g[:2])
        lag = [g for g in open_g if g["status"] in ("Delayed", "Missed", "Not started")]
        recert.append((f"Goal not yet met ({names})" if len(goals) == 1 else f"{len(open_g)} of {len(goals)} goals not yet met ({names})") + (f"; {len(lag)} delayed or missed." if lag else "."))
    else:
        data.append("No care-plan goals found: goal attainment cannot be confirmed.")
    tr = (p.get("vitals") or {}).get("trajectory") or {}
    cls = tr.get("class")
    if cls == "deteriorating":
        recert.append(f"Vitals deteriorating: {(tr.get('saw') or '').rstrip('.')}.")
    elif cls in ("stable", "improving"):
        dc.append(f"Vitals {cls}.")
    else:
        data.append("No recent vitals: stability cannot be confirmed.")
    wounds = (p.get("clinical") or {}).get("wounds") or []
    open_w = [w for w in wounds if (w.get("healing_status") or "").lower() != "healed"]
    if open_w:
        recert.append(f"{len(open_w)} open wound(s) still need skilled care ({', '.join((w.get('location') or '').title() for w in open_w[:2])}).")
    elif wounds:
        dc.append("Wound healed." if len(wounds) == 1 else f"All {len(wounds)} wounds healed.")
    lvl = p["risk"]["outcome_risk_level"]
    (recert if lvl in ("high", "very_high") else dc).append(
        f"{lvl.replace('_', ' ').capitalize()} 60-day hospitalization risk ({p['risk']['score']:.2f}).")
    ev = [e for e in p.get("events") or [] if e.get("flag")] + ((p.get("care_review") or {}).get("adverse_events") or [])
    if ev:
        recert.append(f"{len(ev)} adverse event(s) or unresolved alert(s) this episode.")
    better, worse = [], []
    for o in (p.get("clinical") or {}).get("oasis") or []:
        if o.get("changed") and o["item"].startswith(LOWER_IS_BETTER):
            a, b_ = _code(o.get("first")), _code(o.get("latest"))
            if a is not None and b_ is not None:
                (better if b_ < a else worse if b_ > a else []).append(f"{o['item']} {a}->{b_}")
    if better:
        dc.append("Function improved on OASIS: " + ", ".join(better[:3]) + ".")
    if worse:
        recert.append("Function declined on OASIS: " + ", ".join(worse[:3]) + ".")
    cp = pt.get("clinical_profile") or {}
    prog, rehab = (cp.get("prognosis") or "").lower(), (cp.get("rehab_potential") or "").lower()
    if prog in ("poor", "guarded"):
        recert.append(f"Prognosis {prog}.")
    elif prog in ("good", "excellent"):
        dc.append(f"Prognosis {prog}.")
    if rehab in ("good", "excellent") and open_g:
        recert.append(f"Rehab potential {rehab} with goals still progressing.")
    gaps = (p.get("narratives") or {}).get("data_gaps")
    if gaps:
        data.append(f"{len([x for x in re.split(r'[|\n]', gaps) if x.strip()])} data gap(s) to resolve before deciding.")
    # decision
    r_, d_ = len(recert), len(dc)
    strong_recert = cls == "deteriorating" or lvl == "very_high" or any("Function declined" in x for x in recert)
    if r_ == 0 and d_ >= 2:
        rec, conf = "Discharge", "Strong signal"
    elif d_ >= r_ + 2:
        rec, conf = "Discharge", "Leaning"
    elif r_ >= d_ + 2 or (strong_recert and r_ > d_):
        rec, conf = "Recertify", "Strong signal" if strong_recert else "Leaning"
    else:
        rec, conf = "Clinical review", "Mixed signals"
    cn = p.get("care_network") or {}
    refs = [n_ for n_, k_ in (("CW PCO", "refer_cwpco"), ("CW Rx", "refer_cwrx")) if (cn.get(k_) or {}).get("indicator") in ("Refer", "Consider")]
    if rec == "Discharge" and refs:
        rec = "Discharge with referrals"
    # next steps
    steps = []
    if rec.startswith("Discharge"):
        for n_ in refs:
            steps.append(f"Offer a referral to {n_} ({(cn.get('refer_cwpco' if n_ == 'CW PCO' else 'refer_cwrx') or {}).get('reason', '')})")
        if cn.get("cwpco_enrolled"): steps.append("Send the discharge summary to the patient's CW PCO team.")
        if cn.get("cwrx_enrolled"): steps.append("Share the discharge medication list with CW Rx.")
        steps.append("Confirm a PCP follow-up within 7 days of discharge." if cn.get("has_pcp") is not False
                     else "No PCP on record: arrange primary care before discharge.")
        steps.append("Reconcile medications and teach back the discharge plan with the patient / caregiver.")
        for g in open_g[:2]: steps.append(f"Document the outcome of the unmet goal: {g['goal'][:60]}.")
        if open_w: steps.append("Wound care orders and supplies in place for after discharge.")
        steps.append("Complete the discharge OASIS.")
    else:
        steps.append("Complete the recertification OASIS in the last 5 days of the episode.")
        if open_g: steps.append(f"Carry over {len(open_g)} unmet goal(s) with updated, measurable targets.")
        steps.append("Update the plan of care and visit frequencies for the next 60-day period.")
        if cls == "deteriorating": steps.append(f"Act on the vitals action window: {tr.get('action_window') or 'clinician review'}.")
        if rec == "Clinical review": steps.append("Discuss mixed signals in conference; physician to confirm discharge or recert.")
    trim = lambda x: x.rstrip(".")
    if rec.startswith("Discharge"):
        auto = (f"Most signals support discharge ({len(dc)} vs. {len(recert)}): " + "; ".join(trim(x) for x in dc[:3]) + "."
                + (f" Still open: {trim(recert[0])}." if recert else ""))
    elif rec == "Recertify":
        auto = (f"Most signals support recertification ({len(recert)} vs. {len(dc)}): " + "; ".join(trim(x) for x in recert[:3]) + ".")
    else:
        auto = (f"Signals are mixed ({len(recert)} for recertification, {len(dc)} for discharge). "
                + (f"For recert: {trim(recert[0])}. " if recert else "") + (f"For discharge: {trim(dc[0])}." if dc else ""))
    rationale = llm.get("rationale") or auto
    return {"recommendation": llm.get("recommendation") if llm.get("recommendation") in DC_OPTIONS else rec,
            "confidence": conf, "rationale": rationale[:400], "supports_recert": recert, "supports_discharge": dc,
            "data_caveats": data, "next_steps": steps[:7], "source": "llm" if llm.get("recommendation") in DC_OPTIONS else "rules",
            "days_remaining": pt["discharge_window"].get("days_remaining")}


def _discharge(p):
    """Facts the team needs for the discharge / recertification decision (None when not in the window)."""
    pt = p["patient"]; dw = pt.get("discharge_window") or {}
    if not dw.get("flag"):
        return None
    cp = pt.get("clinical_profile") or {}
    goals = (p.get("goals") or {}).get("items") or []
    met = sum(1 for g in goals if g["status"] == "Achieved")
    rem, sch, _ = care_metrics(p["care_delivery"])
    return {"days_remaining": dw.get("days_remaining"), "episode_end": _mdy(pt.get("episode_end") or ""),
            "prognosis": (cp.get("prognosis") or "n/a").title(), "rehab_potential": (cp.get("rehab_potential") or "n/a").title(),
            "goals": f"{met} of {len(goals)} achieved" if goals else "No goals found",
            "visits": f"{rem} ordered, {sch} scheduled",
            "network": network_line(p.get("care_network")),
            "recommendation": (p.get("discharge_recommendation") or {}).get("recommendation")}


def _actions(p):
    acts = []
    if (p["patient"].get("discharge_window") or {}).get("flag"):
        dd = p["patient"]["discharge_window"].get("days_remaining")
        cn = p.get("care_network") or {}
        refs = [n_ for n_, k_ in (("CW PCO", "refer_cwpco"), ("CW Rx", "refer_cwrx")) if (cn.get(k_) or {}).get("indicator") in ("Refer", "Consider")]
        rec = (p.get("discharge_recommendation") or {}).get("recommendation") or "review"
        acts.append({"action": (f"Discharge decision due in {dd} days: system suggests {rec.lower()}"
                                + (f"; refer to {' and '.join(refs)}" if refs and rec.startswith("Discharge") else "; confirm as a team"))[:110],
                     "owner": "CM / Clinician",
                     "when": "This week", "evidence_section": "DC"})
    rem, sch, _ = care_metrics(p["care_delivery"])
    med = p.get("medications") or {}
    if med.get("flag"):
        names = ", ".join(x["name"].split()[0] for x in (med.get("high_risk") or [])[:2])
        acts.append({"action": f"Check medication understanding with teach-back on high-risk meds ({names}) at the next SN visit"[:110],
                     "owner": "SN", "when": "Next SN visit", "evidence_section": "M"})
    f = (p.get("vitals") or {}).get("findings") or []
    urgent = [x["measure"].split(" (")[0] for x in f if x["urgency"] == "Within 24 hrs"]
    if urgent:
        acts.append({"action": f"Review {', '.join(urgent).lower()} trend with patient and notify MD", "owner": "SN / CM",
                     "when": "Within 24 hrs", "evidence_section": "C"})
    if rem > sch:
        acts.append({"action": f"Schedule remaining ordered visits ({rem - sch} unscheduled)", "owner": "Scheduling",
                     "when": "Within 24 hrs" if sch == 0 else "This week", "evidence_section": "B"})
    goals = (p.get("goals") or {}).get("items") or []
    if any(x["status"] in ("Missed", "Delayed") for x in goals):
        acts.append({"action": "Address missed and delayed care-plan goals with the discipline owners", "owner": "CM",
                     "when": "This week", "evidence_section": "D"})
    for iv in ((p.get("goals") or {}).get("interventions") or [])[:1]:
        t = iv["text"]
        acts.append({"action": t if len(t) <= 120 else t[:116].rstrip() + "...", "owner": iv.get("discipline") or "SN",
                     "when": "Next visit", "evidence_section": "D"})
    acts.sort(key=lambda a: URGENCY_RANK.get(a["when"], 9))   # most urgent first
    return acts[:4]


def _headline(p):
    r = p["risk"]
    rising = r.get("score_prior") is not None and r["score"] - r["score_prior"] > 0.005
    parts = [f"{r['outcome_risk_level'].replace('_', ' ').capitalize()}{' and rising' if rising else ''} hospitalization risk"]
    f = [x for x in (p.get("vitals") or {}).get("findings") or [] if x["urgency"] == "Within 24 hrs"]
    if f:
        parts.append(f"{f[0]['measure'].split(' (')[0].lower()} {f[0].get('change')}")
    rem, sch, _ = care_metrics(p["care_delivery"])
    if rem and sch == 0:
        parts.append(f"no future visits scheduled for the {rem} ordered visits that remain")
    elif rem > sch:
        parts.append(f"{rem - sch} of {rem} remaining ordered visits not scheduled")
    txt = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]
    return (txt + ".")[:220]


ACTION_WINDOW = {"Within 24 hrs": "Clinician review within 24 hours", "This week": "Clinician review this week",
                 "Next SN visit": "Review at the next SN visit", "Next visit": "Review at the next visit",
                 "Routine": "Continue routine monitoring"}


def _short(t, n):
    t = (t or "").strip()
    return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0].rstrip(",;") + "..."


def compose_trajectory(p):
    """Fill vitals.trajectory {class, saw, flags, action_window} from rule-based findings when the LLM left them empty."""
    v = p.setdefault("vitals", {})
    t = v.setdefault("trajectory", {}) or {}
    v["trajectory"] = t
    f = sorted(v.get("findings") or [], key=lambda x: URGENCY_RANK.get(x["urgency"], 9))
    if not v.get("readings"):
        t.setdefault("class", None); t["class"] = t.get("class") or "insufficient_data"
        t["saw"] = t.get("saw") or "No vitals recorded for this episode."
        t["flags"] = t.get("flags") or "Monitoring gap: confirm the vitals plan with the SN."
        t["action_window"] = t.get("action_window") or "Obtain vitals at the next visit"
        return p
    top = f[0]["urgency"] if f else "Routine"
    if not t.get("class"):
        t["class"] = "deteriorating" if top in ("Within 24 hrs", "This week") else "stable"
    if not t.get("saw"):
        parts = [f"{x['measure'].split(' (')[0]} {x.get('first')} -> {x.get('latest')}" for x in f[:3]]
        t["saw"] = _short("; ".join(parts) + ".", 150)
    if not t.get("flags"):
        hits = [x for x in f if x["urgency"] in ("Within 24 hrs", "This week")]
        t["flags"] = _short(("; ".join(x.get("interpretation") or f"{x['measure'].split(' (')[0]} trend needs review" for x in hits[:2]) + ".")
                            if hits else "No vitals outside expected range; continue monitoring.", 150)
    t["action_window"] = t.get("action_window") or ACTION_WINDOW.get(top, "Continue routine monitoring")
    return p


def compose_glance(p):
    """Fill the at-a-glance story rows from facts already in the payload (never overwrites LLM text)."""
    g = p.setdefault("at_a_glance", {}) or {}
    p["at_a_glance"] = g
    tiles = {t["key"]: t for t in (p.get("summary") or {}).get("tiles") or []}
    r = p["risk"]; tr = (p.get("vitals") or {}).get("trajectory") or {}
    if not g.get("risk"):
        lc = lambda t: t if t[:2].isupper() else t[:1].lower() + t[1:]          # keep acronyms (COPD, HF, SOC)
        drv = ", ".join(lc(d["label"].split(" (")[0]) for d in (r.get("drivers") or [])[:3])
        g["risk"] = _short(f"{r['outcome_risk_level'].replace('_', ' ').capitalize()} 60-day hospitalization risk ({r['score']:.2f})"
                           + (f"; driven by {drv}." if drv else "."), 160)
    if not g.get("care_delivery"):
        g["care_delivery"] = _short("; ".join((tiles.get("care") or {}).get("points") or []) + ".", 160)
    if not g.get("trajectory"):
        g["trajectory"] = _short(f"{tr.get('saw') or ''} {tr.get('flags') or ''}".strip(), 180)
    if not g.get("flags"):
        acts = [a["action"].rstrip(".") for a in (p.get("summary") or {}).get("actions") or []][:2]
        g["flags"] = _short(". ".join(acts) + "." if acts else "No immediate actions recommended; continue the current plan.", 200)
    if not g.get("found"):
        pts = ((tiles.get("care") or {}).get("points") or [])[:1] + ((tiles.get("vitals") or {}).get("points") or [])[:1]
        g["found"] = _short("; ".join(pts) + "." if pts else (p["summary"].get("headline") or ""), 110)
    return p


def compose(p):
    """Fill page-1 fields the LLM step left empty. Never overwrites LLM text."""
    s = p.setdefault("summary", {})
    have = {t["key"]: t for t in (s.get("tiles") or [])}
    builders = {"care": _tile_care, "risk": _tile_risk, "vitals": _tile_vitals, "goals": _tile_goals}
    clip = lambda t: {**t, "points": [x if len(x) <= 70 else x[:67].rstrip() + "..." for x in t["points"]]}
    s["tiles"] = [have.get(k) or clip(fn(p)) for k, fn in builders.items()]
    compose_trajectory(p)            # discharge rules need the vitals trajectory
    dr = discharge_recommendation(p)
    if dr is not None:
        p["discharge_recommendation"] = dr
    if not s.get("actions"):
        s["actions"] = _actions(p)
    if not s.get("headline"):
        s["headline"] = _headline(p)
    compose_trajectory(p)
    compose_glance(p)
    return p


# ----------------------------------------------------------------- adapter helpers
def _mdy(iso):
    try:
        y, m, d = iso[:10].split("-")
        return f"{m}/{d}/{y}"
    except Exception:
        return iso or ""


def _day_lbl(d):
    if d is None:
        return "n/a"
    return f"DAY{d:+d}" if d else "DAY 0"


def split_gaps(text):
    """'a.|b.|c.' (LLM format) or one item per line -> ['a.', 'b.', 'c.']; empty -> []."""
    if not text:
        return []
    return [x.strip(" -\u2022\t") for x in re.split(r"\s*\|\s*|\n+", text) if x.strip(" -\u2022\t")]


def _tone(v):
    s = str(v).strip().lower() if v is not None else ""
    return "alert" if s in ALERT_VALUES else "ok" if s in OK_VALUES else "neutral"


def _fmt_flag(v):
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return str(v)


def _timeline(p):
    pt = p["patient"]; L = pt["episode_length"]; cur = min(pt["episode_day"], L)
    pts = [(0, "SOC", "blue")]
    for m in p["care_delivery"].get("missed_log") or []:
        if m.get("episode_day") is not None and 0 < m["episode_day"] < L:
            pts.append((m["episode_day"], f"Missed {m['discipline']}", "plum"))
    for e in p.get("events") or []:
        if e.get("flag") and e.get("episode_day") is not None and 0 < e["episode_day"] < L:
            pts.append((e["episode_day"], e["type"][:16], "plum"))
    pts = sorted(set(pts))[:8]
    out = [[d, lab, col, 0] for d, lab, col in pts]
    win = (pt.get("discharge_window") or {}).get("window_days") or 14
    if L - win > 0:
        out.append([L - win, "Discharge window", "green", 0])
    out.append([cur, "Run date", "navy", 0])
    out.append([L, "End of period", "gray", 0])
    return out


def _kpis(p):
    k = dict(p.get("kpis") or {})
    cd = p["care_delivery"]; cw = cd["current_week"]
    if k.get("visits_completed") is None:
        k["visits_completed"] = sum((w.get("delivered") or 0) for d in cd["disciplines"] for w in d["weeks"] if w["week"] < cw)
    if k.get("scheduled_visits") is None:
        k["scheduled_visits"] = sum((w.get("scheduled") or 0) for d in cd["disciplines"] for w in d["weeks"] if w["week"] >= cw)
    if k.get("missed_care") is None:
        k["missed_care"] = len(cd.get("missed_log") or [])
    if k.get("ip_events") is None:
        k["ip_events"] = sum(1 for e in p.get("events") or [] if e["type"].lower().startswith("inpatient"))
    if k.get("vitals_days") is None:
        k["vitals_days"] = len((p.get("vitals") or {}).get("readings") or [])
    rem, sch, _ = care_metrics(cd)
    wound = ((p["patient"].get("indicators") or {}).get("wound"))
    return [("Visits completed", str(k["visits_completed"]), False),
            ("Scheduled visits", str(k["scheduled_visits"]) if k["scheduled_visits"] else "--", rem > sch),
            ("Missed care", str(k["missed_care"]), k["missed_care"] > 0),
            ("IP events", str(k["ip_events"]), k["ip_events"] > 0),
            ("Vitals days", str(k["vitals_days"]), k["vitals_days"] == 0),
            ("Wound patient", "n/a" if wound is None else "Yes" if wound else "No", bool(wound))]


# ----------------------------------------------------------------- adapter
def to_render(p):
    m, pt, r, s, cd = p["meta"], p["patient"], p["risk"], p["summary"], p["care_delivery"]
    cr, ga = p.get("care_review") or {}, p.get("gap_analysis") or {}
    pages = page_numbers(applicable_pages(p))
    v, g = p.get("vitals") or {}, p.get("goals") or {}
    n = dict(p.get("narratives") or {})
    # v1.0 payloads: fall back to the older per-section summary fields
    n.setdefault("profile_risk", None); n.setdefault("vitals_trajectory", None); n.setdefault("care_delivery", None)
    n["profile_risk"] = n["profile_risk"] or r.get("rationale")
    n["vitals_trajectory"] = n["vitals_trajectory"] or v.get("adequacy")
    n["care_delivery"] = n["care_delivery"] or cd.get("summary")
    pct_lbl, trend = _risk_labels(r)
    weeks = cd.get("weeks", 9); cw = cd["current_week"]
    rows = {}
    for d in cd["disciplines"]:
        wk = {w["week"]: w for w in d["weeks"]}
        get = lambda i, k: (wk.get(i) or {}).get(k) or 0
        rows[d["discipline"]] = {"ordered": [get(i, "ordered") for i in range(1, weeks + 1)],
                                 "delivered": [get(i, "delivered") if i < cw else None for i in range(1, weeks + 1)],
                                 "scheduled": [get(i, "scheduled") for i in range(1, weeks + 1)]}
    rd = sorted(v.get("readings") or [], key=lambda x: x["episode_day"])
    col = lambda k: [x.get(k) if x.get(k) is not None else float("nan") for x in rd]
    fmt_disc = lambda t, dsc: f"<b>[{escape(dsc)}]</b> {escape(t)}" if dsc else escape(t)
    branch = re.sub(r"\s*-\s*CENTERWELL HOME HEALTH$", "", pt.get("branch_name") or "", flags=re.I).title()
    epi_status = pt["episode_status"]
    dw = pt.get("discharge_window") or {"flag": False}
    flags = [("schedule_status", cd.get("schedule_status")), ("outcome_risk_level", r["outcome_risk_level"]),
             ("frequency_status", cd.get("frequency_status")), ("risk_raised_by_care_gaps", r.get("risk_raised_by_care_gaps")),
             ("delay_clinical_impact", r.get("delay_clinical_impact")), ("compliance_window_missed", cd.get("compliance_window_missed")),
             ("EPI_STATUS", epi_status),
             ("discharge_window", f"Yes, {dw['days_remaining']} days" if dw.get("flag") else "No")]
    return {
        "sample": m.get("sample", False),
        "pages": pages,
        "sections": [{"id": sid, "name": nm, "desc": ds, "divider": dv, "keys": [k for k in ks if k in pages]}
                     for sid, nm, ds, dv, ks in SECTIONS],
        "page_section": PAGE_SECTION,
        "total": len(pages),
        "header": {"patient_id": pt["patient_id"], "episode_id": pt["episode_id"], "branch": branch,
                   "episode_status": epi_status, "admission_type": (pt.get("admit_type") or "").title(),
                   "episode_day": pt["episode_day"], "episode_length": pt["episode_length"], "run_date": _mdy(m["as_of_date"]),
                   "model": m["model_version"], "conference_date": m.get("conference_label") or f"CQI run {_mdy(m['as_of_date'])}",
                   "cohort": m.get("cohort_label") or "High-Risk & Rising-Risk Patients",
                   "produced_by": m.get("produced_by") or m.get("prepared_by") or PRODUCED_BY,
                   "episode_end": _mdy(pt.get("episode_end") or ""),
                   "prepared_for": m.get("prepared_for") or PREPARED_FOR},
        "risk": {"tier": r["outcome_risk_level"].replace("_", " ").capitalize(), "score": r["score"],
                 "percentile": pct_lbl, "trend": trend,
                 "risk_raised_by_care_gaps": bool(r.get("risk_raised_by_care_gaps")),
                 "delay_clinical_impact": (r.get("delay_clinical_impact") or "n/a").capitalize(),
                 "drivers": [[d["label"], d["contribution"]] for d in r.get("drivers") or []] or [["No drivers available", 0.0]]},
        "headline": s["headline"],
        "patient_goal": g.get("patient_stated_goal") or "No patient-stated goal found in the care plan.",
        "narratives": n,
        "discharge": _discharge(p),
        "dc_rec": p.get("discharge_recommendation") if (pt.get("discharge_window") or {}).get("flag") else None,
        "clinical_profile": pt.get("clinical_profile") or {},
        "data_gaps": split_gaps(n.get("data_gaps")),
        "flags": [(k, _fmt_flag(val), "alert" if k == "discharge_window" and dw.get("flag") else _tone(val)) for k, val in flags],
        "kpis": _kpis(p),
        "tiles": {t["key"]: {"status": t["status"], "points": t["points"], "page": pages[TILE_SECTION[t["key"]]]} for t in s["tiles"]},
        "actions": [[a["action"], a["owner"], a["when"], pages.get(a.get("evidence_section"), pages["summary"])] for a in s["actions"]],
        "profile": {x["label"]: x.get("value") or "Not found" for x in pt.get("profile") or []},
        "timeline": _timeline(p),
        "care_grid": {"weeks": weeks, "current_week": cw, "rows": rows,
                      "status": {"frequency_status": cd.get("frequency_status") or "unknown",
                                 "schedule_status": cd.get("schedule_status") or "unknown",
                                 "compliance_window_missed": "Yes" if cd.get("compliance_window_missed") else "No",
                                 "missed_care_records": len(cd.get("missed_log") or [])}},
        "missed_log": [[x.get("when_label") or _day_lbl(x.get("episode_day")), x["discipline"], x["visit"],
                        x.get("reason") or "Not documented", x.get("status") or "Missed"] for x in cd.get("missed_log") or []],
        "vitals": {"day": [x["episode_day"] for x in rd], "weight": col("weight"), "sbp": col("sbp"), "dbp": col("dbp"),
                   "pulse": col("pulse"), "spo2": col("spo2"), "limits": {**DEFAULT_LIMITS, **(v.get("limits") or {})},
                   "findings": [[x["measure"], x.get("first") or "-", x.get("latest") or "-", x.get("change") or "-",
                                 x.get("interpretation") or "", x["urgency"]] for x in v.get("findings") or []]},
        "goals": [[x["goal"], x.get("discipline") or "", x["status"], x.get("evidence") or ""] for x in g.get("items") or []],
        "interventions": [fmt_disc(x["text"], x.get("discipline")) for x in g.get("interventions") or []],
        "alignment": [[x["item"], x["type"], x.get("supports_goal") or "", x.get("check") or "", bool(x.get("flag"))]
                      for x in g.get("alignment") or []],
        "care_review": {
            "assessments_summary": cr.get("assessments_summary"),
            "assessments": [[a["assessment"], a.get("discipline") or "", a["count"], _day_lbl(a.get("last_day"))] for a in cr.get("assessments") or []],
            "interventions_summary": cr.get("interventions_summary"),
            "interventions": [[a["intervention"], a.get("discipline") or "", a["count"], _day_lbl(a.get("last_day")), a.get("goal")]
                              for a in cr.get("interventions") or []],
            "adverse_events_summary": cr.get("adverse_events_summary"),
            "adverse_events": [[_day_lbl(e.get("episode_day")), e["type"], e.get("severity"), e.get("detail")] for e in cr.get("adverse_events") or []],
            "visit_themes": cr.get("visit_themes") or []},
        "gap_analysis": {k: ga.get(k) or [] for k in ("missing_interventions", "missing_assessments", "visit_goals")},
        "glance": dict(p.get("at_a_glance") or {}),
        "clinical": p.get("clinical") or {"wounds": [], "diagnoses": [], "oasis": []},
        "network": p.get("care_network") or {},
        "meds": p.get("medications") or {},
        "network_line": network_line(p.get("care_network")),
        "trajectory": dict((v.get("trajectory") or {})),
        "vitals_meta": {"days": len(rd), "last_day": rd[-1]["episode_day"] if rd else None,
                        "since": (pt["episode_day"] - rd[-1]["episode_day"]) if rd else None,
                        "urgent": sum(1 for x in v.get("findings") or [] if x["urgency"] in ("Within 24 hrs", "This week"))},
        "events": [[_day_lbl(e.get("episode_day")), e["type"], e["detail"], e.get("source") or "", bool(e.get("flag"))]
                   for e in p.get("events") or []],
    }
