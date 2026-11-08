"""Build CQI booklet payloads (schema v1.1) from the dataset registry - pandas engine.

Same logic as steps 1-7 of the Databricks notebook, so you can develop and test locally on CSVs.

    python src/build_payloads.py --as-of 2026-08-15 --sample     # CSVs -> output/<as_of>/json, pdf, qc.csv
    python src/build_payloads.py --as-of 2026-08-15 --no-render  # JSON only

On Databricks with small cohorts you can also run this with Spark-loaded data:
    ds = Datasets(engine="spark", spark=spark, source="table")
    -> use the notebook instead for full runs (it stays distributed).
"""
import argparse, json, math, os, re, sys
from datetime import datetime, timezone
import pandas as pd

from cqi_datasets import Datasets

WEEKS = 9
RISK_LEVELS = {"high", "very_high"}
RISING_DELTA = 0.05
TRAJ_CLASSES = {"improving", "stable", "deteriorating", "insufficient_data"}
DISCHARGE_WINDOW_DAYS = 14          # flag active episodes within 14 days of episode end
PROFILE_FIELDS = ["comorbidity_adjustment", "functional_impairment", "functional_limitations", "activities_permitted",
                  "allergies", "mental_status", "nutritional_requirements", "prognosis", "rehab_potential"]
EPI_STATUSES = {"CURRENT", "RECERTIFIED", "ON-HOLD"}
DEFAULT_LIMITS = {"sbp_high": 160.0, "pulse_high": 100.0, "pulse_low": 50.0, "spo2_low": 90.0, "weight_gain_lb": 5.0}
COMPLETED, MISSED = {"COMPLETED", "COMPLETE"}, {"MISSED", "MISSED VISIT"}
CLIENT_LOG_TYPES = {"MISSED VISIT", "PATIENT NOT HOME", "PATIENT REFUSED", "RESCHEDULE", "HOSPITALIZED"}
DISC = {"SN": "SN", "RN": "SN", "LPN": "SN", "LVN": "SN", "PT": "PT", "PTA": "PT", "OT": "OT", "COTA": "OT",
        "ST": "ST", "SLP": "ST", "HHA": "HHA", "AIDE": "HHA", "MSW": "MSW", "MSS": "MSW"}
DISC_RE = re.compile(r"^\s*(COTA|PTA|LPN|LVN|SLP|AIDE|MSW|MSS|HHA|SN|RN|PT|OT|ST)")
DISC_ORDER = {"SN": 1, "PT": 2, "OT": 3, "ST": 4, "HHA": 5, "MSW": 6}
FREQ_RE = re.compile(r"(\d+)\s*(?:-\s*\d+\s*)?W(?:K|EEKS?)?\s*(\d+)", re.I)
GOAL_STATUS = {"achieved": "Achieved", "met": "Achieved", "in_progress": "In progress", "in progress": "In progress",
               "progressing": "In progress", "delayed": "Delayed", "behind": "Delayed", "missed": "Missed", "not_met": "Missed",
               "not started": "Not started", "not_started": "Not started"}
MEASURE_LABEL = {"weight": "Weight (lb)", "spo2": "SpO2 (%)", "pulse": "Pulse (bpm)", "sbp": "Systolic BP (mmHg)"}
KEY = ["patient_id", "episode_id"]


# ------------------------------------------------------------------------ helpers
def nn(v):
    """pandas NA/NaN/NaT/'' -> None; numpy scalars -> python."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, str) and not v.strip():
        return None
    return v.item() if hasattr(v, "item") else v


def discipline_of(s):
    s = (s or "").upper().strip()
    if s in DISC:
        return DISC[s]
    m = DISC_RE.match(s)
    return DISC.get(m.group(1)) if m else None


def expand_frequency(freq, start_week):
    out, wk = [], int(start_week or 1)
    for n, k in FREQ_RE.findall(freq or ""):
        for _ in range(int(k)):
            if 1 <= wk <= WEEKS:
                out.append((wk, int(n)))
            wk += 1
    return out


def visit_label(vt):
    """'SN11 - SN SUBSEQUENT VISIT' -> 'SN subsequent visit' (discipline codes stay upper case)."""
    words = re.sub(r"^[A-Z0-9]+\s*-\s*", "", vt or "").strip().lower().split()
    out = [w.upper() if w.upper() in DISC else w for w in words]
    if out and out[0].islower():
        out[0] = out[0].capitalize()
    return " ".join(out)


def fmt_num(x):
    return f"{x:.1f}".rstrip("0").rstrip(".") if x is not None else None


def parse_json(s, default=None):
    try:
        return json.loads(s) if nn(s) else default
    except json.JSONDecodeError:
        return default


def med_block(m, checks, episode_day, summary):
    import cqi_payload as _cp
    done_ = [x for x in checks if x.get("understanding") or x.get("teaching")]
    last = max((x["episode_day"] for x in checks if x.get("understanding")), default=None)
    hr = m.get("high_risk") or []
    adh = [x for x in checks if x.get("adherent") is not None]
    return {"summary": summary, "active_count": m.get("active_count"), "high_risk": hr, "checks": checks,
            "adherence_concerns": sum(1 for x in adh if x["adherent"] is False) if adh else None,
            "visits_with_check": len([x for x in checks if x.get("understanding")]), "visits_total": len([x for x in checks if x.get("understanding") is not None]), "last_check_day": last,
            "flag": _cp.medication_flag(hr, last, episode_day, sum(1 for x in adh if x["adherent"] is False))}


def care_network(row, asof, near_discharge):
    """Care network block + CW PCO / CW Rx referral indicators. Empty dict when no data."""
    import cqi_payload as _cp
    if row is None:
        return {}
    v = lambda c_: nn(row[c_]) if c_ in row else None
    lv = v("pcp_last_visit_dt")
    cn = {"has_pcp": v("has_pcp"), "pcp_name": v("pcp_name"),
          "pcp_last_visit_days": int((asof - lv).days) if lv is not None else None,
          "ma_eligible": v("ma_eligible"), "cwpco_enrolled": v("cwpco_enrolled"), "cwrx_enrolled": v("cwrx_enrolled"),
          "nearest_cwpco_clinic": v("nearest_cwpco_clinic"),
          "nearest_cwpco_miles": float(v("nearest_cwpco_miles")) if v("nearest_cwpco_miles") is not None else None,
          "cwpco_clinics_10mi": int(v("cwpco_clinics_10mi")) if v("cwpco_clinics_10mi") is not None else None,
          "cwpco_clinics_20mi": int(v("cwpco_clinics_20mi")) if v("cwpco_clinics_20mi") is not None else None,
          "active_med_count": int(v("active_med_count")) if v("active_med_count") is not None else None}
    for b_ in ("has_pcp", "ma_eligible", "cwpco_enrolled", "cwrx_enrolled"):
        if cn[b_] is not None: cn[b_] = bool(cn[b_])
    cn["refer_cwpco"], cn["refer_cwrx"] = _cp.referral_indicators(cn, near_discharge)
    return cn


def json_list(text, required):
    """LLM JSON array -> list of dicts that have every required key (bad rows are dropped, not fatal)."""
    rows = parse_json(text, []) or []
    if not isinstance(rows, list):
        return []
    out = []
    for r_ in rows:
        if isinstance(r_, dict) and all(r_.get(k) for k in required):
            out.append({k: (str(v) if v is not None else None) for k, v in r_.items()})
    return out


def split_interventions(text):
    parts = [p.strip() for p in re.split(r"(?=\[[A-Z/]{2,6}\])", text or "") if p.strip()]
    out = []
    for p in parts:
        m = re.match(r"^\[([A-Z/]{2,6})\]\s*(.*)$", p, re.S)
        out.append({"discipline": m.group(1), "text": m.group(2).strip()} if m else {"discipline": None, "text": p})
    return out


def by_key(df):
    return {k: g for k, g in df.groupby(KEY, sort=False)} if df is not None and len(df) else {}


# ------------------------------------------------------------------------ build
def build_payloads(ds, as_of, branches=None, model_version="cwhh-tif-60day-v1", sample=False,
                   cohort_label="High-Risk & Rising-Risk Patients", percentiles=None):
    """percentiles: optional {(patient_id, episode_id): 0-100} computed over ALL scored episodes on the run date.
    Pass it when `ds` was pre-filtered to the cohort (Databricks notebook); otherwise it is computed here."""
    asof = pd.Timestamp(as_of)
    run_id = f"cqi_{as_of.replace('-', '')}_{datetime.now(timezone.utc).strftime('%H%M%S')}"

    # 1. cohort --------------------------------------------------------------
    rs = ds.load("risk_scores").sort_values(KEY + ["as_of_date"])
    rs["score_prior"] = rs.groupby(KEY)["score"].shift(1)
    run = rs[rs["as_of_date"] == asof].copy()
    if run.empty:
        raise SystemExit(f"no risk scores for as_of_date {as_of}")
    run["percentile"] = (run["score"].rank(method="min") - 1) / max(len(run) - 1, 1) * 100   # = Spark percent_rank
    if percentiles:
        run["percentile"] = [percentiles.get((a, b), p) for a, b, p in zip(run["patient_id"], run["episode_id"], run["percentile"])]
    ep = ds.load("episodes")
    ep["episode_status"] = ep["episode_status"].str.upper().str.strip().str.replace(r"[ _]+", "-", regex=True)
    c = run.merge(ep, on=KEY, how="inner")
    c["outcome_risk_level"] = c["outcome_risk_level"].str.lower().str.strip().str.replace(r"[ -]+", "_", regex=True)
    rising = (c["score"] - c["score_prior"]) >= RISING_DELTA
    c = c[c["episode_status"].isin(EPI_STATUSES) & (c["outcome_risk_level"].isin(RISK_LEVELS) | rising)]
    if branches:
        c = c[c["branch_name"].isin(branches)]
    c = c.copy()
    c["episode_day"] = (asof - c["episode_start"]).dt.days.clip(lower=0)
    c["current_week"] = (c["episode_day"] // 7 + 1).clip(upper=WEEKS + 1).astype(int)
    c["episode_end"] = c["episode_end"].fillna(c["episode_start"] + pd.Timedelta(days=59))   # 60-day period default
    c["episode_length"] = ((c["episode_end"] - c["episode_start"]).dt.days + 1).clip(lower=1)
    c["days_to_end"] = (c["episode_end"] - asof).dt.days
    c["near_discharge"] = (c["episode_status"] != "DISCHARGED") & c["days_to_end"].between(0, DISCHARGE_WINDOW_DAYS)
    keys = c[KEY + ["episode_start", "episode_day", "current_week"]]
    cohort_ids = set(map(tuple, c[KEY].values))
    def in_cohort(df):
        if df is None or not len(df):
            return df
        return df[pd.Series([tuple(x) in cohort_ids for x in df[KEY].values], index=df.index)]
    with_keys = lambda df: in_cohort(df).merge(keys, on=KEY) if df is not None and len(df) else None

    # 2. drivers + LLM --------------------------------------------------------
    dr = ds.load("risk_drivers")
    dr = in_cohort(dr[(dr["as_of_date"] == asof) & (dr["contribution"] > 0)])
    dr = dr.sort_values("contribution", ascending=False).groupby(KEY).head(7)
    drivers = by_key(dr)
    llm_df = ds.load("llm_summary")
    llm = {}
    if llm_df is not None:
        llm_df = in_cohort(llm_df[llm_df["as_of_date"] == asof]).drop_duplicates(KEY)
        llm = {tuple(r[KEY]): r for _, r in llm_df.iterrows()}

    # 3. care delivery --------------------------------------------------------
    od = with_keys(ds.load("orders"))
    grid = {}   # (pid, eid) -> disc -> week -> dict
    def cell(k, disc, wk):
        return grid.setdefault(k, {}).setdefault(disc, {}).setdefault(wk, {"ordered": 0, "delivered": 0, "scheduled": 0})
    if od is not None:
        for _, r in od.iterrows():
            disc = discipline_of(r["discipline"])
            if not disc:
                continue
            eff = r["effective_dt"] if nn(r["effective_dt"]) is not None else r["episode_start"]
            start = max(1, (eff - r["episode_start"]).days // 7 + 1)
            for wk, n in expand_frequency(r["frequency"], start):
                cell((r.patient_id, r.episode_id), disc, wk)["ordered"] += n
    vs = with_keys(ds.load("visits"))
    vs = vs[vs["visit_dt"] <= asof].copy()
    vs["discipline"] = vs["visit_type"].map(discipline_of)
    vs["week"] = (vs["visit_dt"] - vs["episode_start"]).dt.days // 7 + 1
    vs["st"] = vs["visit_status"].str.upper().str.strip()
    vs = vs[vs["discipline"].notna()]
    done = vs[vs["st"].isin(COMPLETED)]
    # medication understanding / teaching documented at completed visits
    med_checks = {}
    for k, g_ in done.groupby(KEY):
        rows_ = [{"episode_day": int((r_.visit_dt - r_.episode_start).days), "discipline": r_.discipline,
                  "understanding": nn(r_.med_understanding), "teaching": nn(r_.med_teaching), "adherent": nn(r_.med_adherent)}
                 for _, r_ in g_.sort_values("visit_dt").iterrows()]
        for x in rows_:
            for f_ in ("understanding", "teaching", "adherent"):
                if x[f_] is not None: x[f_] = bool(x[f_])
        med_checks[k] = rows_
    for _, r in done.iterrows():
        if 1 <= r.week <= WEEKS:
            cell((r.patient_id, r.episode_id), r.discipline, int(r.week))["delivered"] += 1
    sc = with_keys(ds.load("sched"))
    if sc is not None:
        sc = sc[sc["sched_dt"] > asof]
        for _, r in sc.iterrows():
            disc, wk = discipline_of(r["visit_type"]), (r["sched_dt"] - r["episode_start"]).days // 7 + 1
            if disc and 1 <= wk <= WEEKS:
                cell((r.patient_id, r.episode_id), disc, int(wk))["scheduled"] += 1
    missed = by_key(vs[vs["st"].isin(MISSED)])
    n_done = done.groupby(KEY).size().to_dict()
    n_sched = sc.groupby(KEY).size().to_dict() if sc is not None and len(sc) else {}
    first_visit = done.assign(d=(done["visit_dt"] - done["episode_start"]).dt.days).groupby(KEY)["d"].min().to_dict()

    # 4. vitals ---------------------------------------------------------------
    vt = with_keys(ds.load("vitals"))
    readings, findings = {}, {}
    if vt is not None:
        vt = vt[vt["visit_dt"] <= asof].copy()
        nm = vt["vital_name"].str.upper()
        vt["measure"] = None
        for pat, m in [("WEIGHT|WT", "weight"), ("SYSTOLIC|SBP", "sbp"), ("DIASTOLIC|DBP", "dbp"), (r"^BP$|BLOOD PRESSURE", "bp"),
                       ("PULSE|HEART RATE|HR", "pulse"), ("SPO2|O2 SAT|OXYGEN SAT", "spo2"), ("RESP", "resp"), ("TEMP", "temp")]:
            vt.loc[vt["measure"].isna() & nm.str.contains(pat, regex=True), "measure"] = m
        bp = vt[vt["measure"] == "bp"]
        rest = vt[vt["measure"].notna() & (vt["measure"] != "bp")].assign(val=lambda x: x["vital_value"].str.extract(r"(\d+\.?\d*)")[0])
        sbp = bp.assign(measure="sbp", val=bp["vital_value"].str.extract(r"(\d+)\s*/")[0])
        dbp = bp.assign(measure="dbp", val=bp["vital_value"].str.extract(r"/\s*(\d+)")[0])
        v = pd.concat([rest, sbp, dbp])
        v["val"] = pd.to_numeric(v["val"], errors="coerce")
        v = v[v["val"] > 0]
        v["episode_day"] = (v["visit_dt"] - v["episode_start"]).dt.days
        daily = v.groupby(KEY + ["episode_day", "measure"])["val"].mean().round(1).unstack("measure").reset_index()
        for m in ["weight", "sbp", "dbp", "pulse", "spo2", "resp", "temp"]:
            if m not in daily:
                daily[m] = None
        for k, g in daily.groupby(KEY):
            readings[k] = [{"episode_day": int(r.episode_day), **{m: nn(r[m]) for m in ["weight", "sbp", "dbp", "pulse", "spo2", "resp", "temp"]}}
                           for _, r in g.sort_values("episode_day").iterrows()]
        L = DEFAULT_LIMITS
        for k, g in v[v["measure"].isin(MEASURE_LABEL)].groupby(KEY):
            fs = []
            for m, gm in g.sort_values("episode_day").groupby("measure"):
                f, l = gm["val"].iloc[0], gm["val"].iloc[-1]
                d0, d1 = gm["episode_day"].iloc[0], gm["episode_day"].iloc[-1]
                urg = ("Within 24 hrs" if (m == "weight" and l - f >= L["weight_gain_lb"]) or (m == "spo2" and (l < L["spo2_low"] or f - l >= 3))
                       else "This week" if (m == "pulse" and (l > L["pulse_high"] or l < L["pulse_low"])) or (m == "sbp" and (l > L["sbp_high"] or l - f >= 20))
                       else "Routine")
                wks = max(round((d1 - d0) / 7), 1)
                fs.append({"measure": MEASURE_LABEL[m], "first": fmt_num(f), "latest": fmt_num(l),
                           "change": f"{'+' if l >= f else ''}{fmt_num(l - f)} in {wks} wks", "_m": m, "urgency": urg})
            findings[k] = sorted(fs, key=lambda x: {"Within 24 hrs": 0, "This week": 1}.get(x["urgency"], 2))

    # 5. goals + alignment ----------------------------------------------------
    gl = ds.load("llm_goals")
    goals = {}
    if gl is not None:
        gl = in_cohort(gl[gl["as_of_date"] == asof]).copy()
        gl["status"] = gl["status"].str.lower().str.strip().map(GOAL_STATUS).fillna("Unknown")
        gl["rk"] = gl["status"].map({"Missed": 0, "Delayed": 1, "In progress": 2}).fillna(3)
        goals = by_key(gl.sort_values("rk", kind="stable"))
    al = ds.load("alignment")
    align = {}
    if al is not None:
        al = in_cohort(al[al["as_of_date"] == asof]).copy()
        t = al["type"].str.lower()
        al["type"] = t.map(lambda x: "Medication" if "med" in str(x) else "DME" if "dme" in str(x) else "Supply")
        align = by_key(al)

    # 5b. care delivered: assessments, interventions, adverse events -----------
    def day_of(df, col):
        return (df[col] - df["episode_start"]).dt.days
    assess, interv, adverse = {}, {}, {}
    a_ = with_keys(ds.load("assessments"))
    if a_ is not None:
        a_ = a_[a_["assess_dt"] <= asof].assign(day=lambda x: day_of(x, "assess_dt"))
        for k, g_ in a_.groupby(KEY):
            agg = g_.groupby("assessment").agg(count=("day", "size"), last_day=("day", "max"),
                                               discipline=("discipline", lambda s_: "/".join(sorted(set(s_.dropna())))))
            assess[k] = [{"assessment": i, "discipline": r_.discipline or None, "count": int(r_["count"]), "last_day": int(r_.last_day)}
                         for i, r_ in agg.sort_values(["count", "last_day"], ascending=False).iterrows()]
    i_ = with_keys(ds.load("interventions"))
    if i_ is not None:
        i_ = i_[i_["intervention_dt"] <= asof].assign(day=lambda x: day_of(x, "intervention_dt"))
        i_["goal"] = i_["goal"].fillna("")
        for k, g_ in i_.groupby(KEY):
            agg = g_.groupby(["intervention", "goal"]).agg(count=("day", "size"), last_day=("day", "max"),
                                                           discipline=("discipline", lambda s_: "/".join(sorted(set(s_.dropna())))))
            interv[k] = [{"intervention": i[0], "goal": i[1] or None, "discipline": r_.discipline or None, "count": int(r_["count"]),
                          "last_day": int(r_.last_day)} for i, r_ in agg.sort_values("count", ascending=False).iterrows()]
    e_ = with_keys(ds.load("adverse_events"))
    if e_ is not None:
        e_ = e_[e_["event_dt"] <= asof].assign(day=lambda x: day_of(x, "event_dt")).sort_values("day")
        for k, g_ in e_.groupby(KEY):
            adverse[k] = [{"episode_day": int(r_.day), "type": r_.event_type, "severity": nn(r_.severity), "detail": nn(r_.detail)}
                          for _, r_ in g_.iterrows()]

    # 6. events ---------------------------------------------------------------
    events = {}
    def add_ev(k, e):
        events.setdefault(k, []).append(e)
    a = with_keys(ds.load("vs_alerts"))
    if a is not None:
        for _, r in a[a["alert_dt"] <= asof].iterrows():
            ok = bool(nn(r["physician_notified"]))
            add_ev((r.patient_id, r.episode_id), {"episode_day": int((r.alert_dt - r.episode_start).days), "type": "Vitals alert",
                   "detail": r["alert_text"], "source": "Physician notified" if ok else "No physician notification found", "flag": not ok})
    cl = with_keys(ds.load("client_logs"))
    if cl is not None:
        cl = cl[(cl["log_dt"] <= asof) & cl["log_type"].str.upper().isin(CLIENT_LOG_TYPES)]
        for _, r in cl.iterrows():
            add_ev((r.patient_id, r.episode_id), {"episode_day": int((r.log_dt - r.episode_start).days), "type": "Client log note",
                   "detail": (nn(r["log_note"]) or r["log_type"])[:140], "source": "Source: client logs", "flag": False})
    ip = ds.load("ip_events")
    if ip is not None:
        ip = ip.merge(keys, on="patient_id")
        ip = ip[(ip["admit_dt"] >= ip["episode_start"] - pd.Timedelta(days=14)) & (ip["admit_dt"] <= asof)]
        for _, r in ip.iterrows():
            day = int((r.admit_dt - r.episode_start).days)
            add_ev((r.patient_id, r.episode_id), {"episode_day": day, "type": "Inpatient event", "detail": r["event_desc"],
                   "source": "Source: inpatient events", "flag": day >= 0})

    # 5c. clinical picture: wounds, diagnoses, OASIS ---------------------------
    wounds, dx, oasis = {}, {}, {}
    w_ = with_keys(ds.load("wounds"))
    if w_ is not None:
        w_ = w_[w_["assess_dt"] <= asof].assign(day=lambda x: (x["assess_dt"] - x["episode_start"]).dt.days).sort_values("day")
        size = lambda r_: ("x".join(fmt_num(v) for v in (r_.length_cm, r_.width_cm, r_.depth_cm) if nn(v) is not None) + " cm") \
            if nn(r_.length_cm) is not None else None
        area = lambda r_: (r_.length_cm * r_.width_cm) if nn(r_.length_cm) is not None and nn(r_.width_cm) is not None else None
        for k, g_ in w_.groupby(KEY):
            rows_ = []
            for wid, gw in g_.groupby("wound_id", sort=False):
                f0, f1 = gw.iloc[0], gw.iloc[-1]
                a0, a1 = area(f0), area(f1)
                rows_.append({"wound_id": str(wid), "category": nn(f1.wound_category), "wound_class": nn(f1.wound_class),
                              "extended_type": nn(f1.extended_type), "location": nn(f1.location), "description": nn(f1.description),
                              "stage": nn(f1.stage), "first_size": size(f0), "latest_size": size(f1),
                              "area_change_pct": round((a1 - a0) / a0 * 100, 0) if a0 and a1 is not None else None,
                              "healing_status": nn(f1.healing_status), "assessments": int(len(gw)),
                              "first_day": int(f0.day), "last_day": int(f1.day)})
            wounds[k] = rows_
    x_ = with_keys(ds.load("diagnoses"))
    if x_ is not None:
        for k, g_ in x_.groupby(KEY):
            g_ = g_.assign(prim=g_["is_primary"].fillna(False).astype(bool)).sort_values(["prim", "diag_or_proc"], ascending=[False, True])
            dx[k] = [{"code": r_.code, "description": nn(r_.description), "category": nn(r_.category), "clinical_group": nn(r_.clinical_group),
                      "diag_or_proc": nn(r_.diag_or_proc), "onset_exacer": nn(r_.onset_exacer),
                      "onset_exacer_day": int((r_.onset_exacer_dt - r_.episode_start).days) if nn(r_.onset_exacer_dt) is not None else None,
                      "is_primary": bool(r_.prim)} for _, r_ in g_.drop_duplicates("code").iterrows()]
    o_ = with_keys(ds.load("oasis"))
    if o_ is not None:
        o_ = o_[o_["assess_dt"] <= asof].sort_values("assess_dt")
        import cqi_payload as _cp
        key_rank = {it: i for i, it in enumerate(_cp.OASIS_KEY_ITEMS)}
        for k, g_ in o_.groupby(KEY):
            rows_ = []
            for it, gi in g_.groupby("item", sort=False):
                f0, f1 = gi.iloc[0], gi.iloc[-1]
                rows_.append({"item": it, "section": nn(f1.section), "description": nn(f1.description), "first": nn(f0.answer),
                              "latest": nn(f1.answer), "first_timepoint": nn(f0.timepoint), "latest_timepoint": nn(f1.timepoint),
                              "changed": len(gi) > 1 and str(f0.answer) != str(f1.answer),
                              "key_item": it.split("_")[0][:5] in key_rank})
            oasis[k] = sorted(rows_, key=lambda r_: (not r_["key_item"], key_rank.get(r_["item"][:5], 99), r_["item"]))
    meds = {}
    md_ = with_keys(ds.load("medications"))
    if md_ is not None:
        import cqi_payload as _cp
        for k, g_ in md_.groupby(KEY):
            hr = []
            for _, r_ in g_.drop_duplicates("med_name").iterrows():
                cls = (nn(r_.drug_class) or "").lower()
                flag = nn(r_.high_risk)
                hit = next((h for h in _cp.HIGH_RISK_CLASSES if h in cls), None)
                if flag is True or (flag is None and hit):
                    hr.append({"name": r_.med_name, "drug_class": nn(r_.drug_class),
                               "reason": f"{(hit or cls or 'high-risk').capitalize()}: needs teaching and monitoring"})
            meds[k] = {"active_count": int(g_["med_name"].nunique()), "high_risk": hr}
    cnet = {}
    cn_ = ds.load("care_network")
    if cn_ is not None and len(cn_):
        cnet = {r_.patient_id: r_ for _, r_ in cn_.drop_duplicates("patient_id").iterrows()}

    pf = ds.load("patient_profile")
    profiles = {}
    if pf is not None and len(pf):
        profiles = {tuple(x[KEY]): x for _, x in in_cohort(pf).drop_duplicates(KEY).iterrows()}

    # 7. assemble -------------------------------------------------------------
    payloads = []
    for _, r in c.sort_values("score", ascending=False).iterrows():
        k = (r.patient_id, r.episode_id)
        L_ = llm.get(k)
        g = lambda col: nn(L_[col]) if L_ is not None else None
        cw = int(r.current_week)
        discs = []
        for disc in sorted(grid.get(k, {}), key=lambda x: DISC_ORDER.get(x, 9)):
            wk = grid[k][disc]
            discs.append({"discipline": disc, "weeks": [{"week": w, "ordered": wk.get(w, {}).get("ordered", 0),
                                                          "delivered": wk.get(w, {}).get("delivered", 0) if w < cw else None,
                                                          "scheduled": wk.get(w, {}).get("scheduled", 0)} for w in range(1, WEEKS + 1)]})
        interp = parse_json(g("vitals_interp_json"), {}) or {}
        fnd = [{**{x: f[x] for x in ("measure", "first", "latest", "change", "urgency")}, "interpretation": interp.get(f["_m"])}
               for f in findings.get(k, [])]
        age = nn(r.age)
        age_band = f"{age // 5 * 5}-{age // 5 * 5 + 4}" if age is not None else None
        fv = first_visit.get(k)
        profile = [("Age band / sex", " / ".join(x for x in [age_band, (nn(r.sex) or "").title() or None] if x)),
                   ("Clinical group", nn(r.clin_group)), ("Primary diagnosis", nn(r.primary_dx)),
                   ("Comorbidities", g("comorbidities")), ("Living situation", g("living_situation")),
                   ("Admission type", (nn(r.admit_type) or "").title() or None),
                   ("Acuity level / first visit", "; ".join(x for x in [f"Level {r.acuity_level}" if nn(r.acuity_level) else None,
                                                                         f"first visit DAY+{int(fv)}" if fv is not None else None] if x) or None)]
        sched_txt = g("schedule_summary")
        care_txt = " ".join(x for x in [g("care_delivery_summary"), f"Schedule: {sched_txt}" if sched_txt else None] if x) or None
        dci = (g("delay_clinical_impact") or "").lower().replace("_", " ")
        judge = (g("judge_status") or "").lower()
        payloads.append({
            "schema_version": "1.8",
            "meta": {"as_of_date": as_of, "model_version": model_version,
                     "generated_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "run_id": run_id,
                     "conference_label": "Week of " + (asof + pd.offsets.Week(weekday=0)).strftime("%m/%d/%Y"),
                     "cohort_label": cohort_label, "sample": sample, "llm_model": g("llm_model"),
                     "judge_status": judge if judge in ("passed", "adjudicated", "failed", "not_run") else None},
            "patient": {"patient_id": r.patient_id, "episode_id": r.episode_id, "branch_name": nn(r.branch_name),
                        "branch_state": nn(r.branch_state), "episode_status": r.episode_status, "admit_type": nn(r.admit_type),
                        "episode_start": r.episode_start.strftime("%Y-%m-%d"), "episode_day": int(r.episode_day), "episode_length": int(r.episode_length),
                        "episode_end": r.episode_end.strftime("%Y-%m-%d"), "days_to_episode_end": int(r.days_to_end),
                        "discharge_window": {"flag": bool(r.near_discharge), "days_remaining": int(r.days_to_end),
                                             "window_days": DISCHARGE_WINDOW_DAYS,
                                             "label": "Nearing discharge decision" if r.near_discharge else None},
                        "clinical_profile": {f: (nn(profiles[k][f]) if k in profiles else None) for f in PROFILE_FIELDS},
                        "clin_group": nn(r.clin_group), "profile": [{"label": a_, "value": b_} for a_, b_ in profile],
                        "indicators": {"wound": nn(r.wound_ind), "inpatient": nn(r.inpat_ind), "death": nn(r.death_ind),
                                       "transfer": nn(r.transfers_ind)}},
            "risk": {"outcome_risk_level": r.outcome_risk_level, "score": round(float(r.score), 3),
                     "percentile": round(float(r.percentile), 1), "score_prior": None if nn(r.score_prior) is None else round(float(r.score_prior), 3),
                     "risk_raised_by_care_gaps": g("risk_raised_by_care_gaps"),
                     "delay_clinical_impact": dci if dci in ("low", "moderate", "high", "not applicable") else None,
                     "outcomes_at_risk": [x.strip() for x in (g("outcomes_at_risk") or "").split(",") if x.strip()],
                     "drivers": [{"feature": d_.feature, "label": nn(d_.label) or d_.feature, "contribution": round(float(d_.contribution), 4)}
                                 for _, d_ in drivers.get(k, pd.DataFrame(columns=["feature", "label", "contribution"])).iterrows()],
                     "rationale": g("risk_rationale")},
            "summary": {"headline": g("headline"), "tiles": parse_json(g("tiles_json")), "actions": parse_json(g("actions_json"))},
            "care_delivery": {"weeks": WEEKS, "current_week": min(cw, WEEKS), "disciplines": discs,
                              "frequency_status": g("frequency_status"), "schedule_status": g("schedule_status"),
                              "compliance_window_missed": g("compliance_window_missed"),
                              "acuity_level": nn(r.acuity_level), "first_visit_day": None if fv is None else int(fv),
                              "summary": care_txt,
                              "missed_log": [{"episode_day": int((m.visit_dt - m.episode_start).days), "when_label": None,
                                              "discipline": m.discipline, "visit": visit_label(m.visit_type),
                                              "reason": nn(m.missed_reason), "status": "Missed"}
                                             for _, m in missed.get(k, pd.DataFrame()).sort_values("visit_dt").head(12).iterrows()] if k in missed else []},
            "vitals": {"readings": readings.get(k, []), "limits": dict(DEFAULT_LIMITS), "findings": fnd, "adequacy": g("vitals_summary"),
                       "trajectory": {"class": tclass if (tclass := (g("vitals_trajectory_class") or "").lower().strip()) in TRAJ_CLASSES else None,
                                      "saw": g("vitals_saw"), "flags": g("vitals_flags"), "action_window": g("action_window")}},
            "at_a_glance": {"found": g("glance_found"), "risk": g("glance_risk"), "care_delivery": g("glance_care_delivery"),
                            "trajectory": g("glance_trajectory"), "flags": g("cqi_flags")},
            "goals": {"patient_stated_goal": g("patient_stated_goal"),
                      "items": [{"goal": x.goal, "discipline": nn(x.discipline), "status": x.status, "evidence": nn(x.evidence)}
                                for _, x in goals[k].iterrows()] if k in goals else [],
                      "interventions": split_interventions(g("interventions_text")),
                      "alignment": [{"item": x["item"], "type": x["type"], "supports_goal": nn(x["supports_goal"]), "check": nn(x["check"]),
                                     "flag": bool(nn(x["flag"]))} for _, x in align[k].iterrows()] if k in align else []},
            "narratives": {"patient_goal": g("care_plan_goal"),
                           "profile_risk": g("profile_risk_summary") or g("risk_rationale"),
                           "vitals_trajectory": g("vitals_summary"), "care_delivery": care_txt,
                           "missed_care": g("missed_care_summary"), "delay_impact": g("delay_impact_explanation"),
                           "data_gaps": g("data_gaps_summary")},
            "clinical": {"wound_summary": g("wound_summary"), "wounds": wounds.get(k, []),
                         "diagnosis_summary": g("diagnosis_summary"), "diagnoses": dx.get(k, []),
                         "oasis_summary": g("oasis_summary"), "oasis": oasis.get(k, [])},
            "discharge_recommendation": {"recommendation": g("discharge_recommendation"), "rationale": g("discharge_rationale")}
                                        if bool(r.near_discharge) and (g("discharge_recommendation") or g("discharge_rationale")) else None,
            "care_network": care_network(cnet.get(r.patient_id), asof, bool(r.near_discharge)),
            "medications": med_block(meds.get(k, {}), med_checks.get(k, []), int(r.episode_day), g("medication_summary")),
            "care_review": {"assessments_summary": g("assessments_summary"), "assessments": assess.get(k, []),
                            "interventions_summary": g("interventions_summary"), "interventions": interv.get(k, []),
                            "adverse_events_summary": g("adverse_events_summary"), "adverse_events": adverse.get(k, []),
                            "visit_themes": [t_ for t_ in re.split(r"\s*\|\s*", g("visit_themes") or "") if t_][:10]},
            "gap_analysis": {"missing_interventions": json_list(g("missing_interventions_json"), ["goal", "intervention"]),
                             "missing_assessments": [{**x, "type": x.get("type") if x.get("type") in ("Assessment", "Vitals") else None}
                                                     for x in json_list(g("missing_assessments_json"), ["item"])],
                             "visit_goals": json_list(g("visit_goals_json"), ["visit_goal"])},
            "kpis": {"visits_completed": int(n_done.get(k, 0)), "scheduled_visits": int(n_sched.get(k, 0)),
                     "missed_care": int(len(missed[k])) if k in missed else 0,
                     "ip_events": sum(1 for e in events.get(k, []) if e["type"] == "Inpatient event"),
                     "vitals_days": len(readings.get(k, []))},
            "events": sorted(events.get(k, []), key=lambda e: e["episode_day"])[:12],
            "provenance": {"sources": {n: ds.resolve_source(n) for n in ds.names()}, "vitals_days": len(readings.get(k, [])),
                           "goal_count": len(goals[k]) if k in goals else 0},
        })
    return payloads


def run(ds, as_of, out_dir, render=True, branches=None, sample=False, tmp=None, formats=("full", "brief"), **build_kw):
    """Build, compose, validate and (optionally) render. Returns QC rows. Used by the CLI and the Databricks notebook.

    Outputs (out_dir):
      json/<branch>_<patient>_<episode>.json     payload (schema 1.4)
      pdf/<...>.pdf                              full booklet (19 pages, fillable feedback)
      pdf_brief/<...>.pdf                        short customer summary (cover, at a glance, vitals action window)
      pdf/_BRANCH_<branch>.pdf                   "In their own words" overview + every full booklet, highest risk first
      pdf_brief/_BRANCH_<branch>_brief.pdf       overview + every short summary
      qc.csv
    """
    import cqi_payload, build_booklet
    payloads = build_payloads(ds, as_of, branches, sample=sample, **build_kw)
    jdir, pdir, bdir = (os.path.join(out_dir, x) for x in ("json", "pdf", "pdf_brief"))
    for x in (jdir, pdir, bdir):
        os.makedirs(x, exist_ok=True)
    qc, by_branch = [], {}
    for p in payloads:
        branch = re.sub(r"\s*-\s*CENTERWELL HOME HEALTH$", "", p["patient"].get("branch_name") or "unknown", flags=re.I)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{branch}_{p['patient']['patient_id']}_{p['patient']['episode_id']}")
        p = cqi_payload.compose(p)
        errs = cqi_payload.validate(p)
        with open(os.path.join(jdir, stem + ".json"), "w") as fh:
            json.dump(p, fh, indent=1, default=str)
        pdf = brief = None
        if render and not errs:
            try:
                rd = cqi_payload.to_render(p)
                if "full" in formats:
                    pdf = build_booklet.build(rd, os.path.join(pdir, stem + ".pdf"), tmp=tmp or pdir)
                if "brief" in formats:
                    brief = build_booklet.build(rd, os.path.join(bdir, stem + "_brief.pdf"), tmp=tmp or bdir, brief=True)
                by_branch.setdefault(branch, []).append((rd, pdf, brief))
            except Exception as e:
                errs.append(f"render: {e.__class__.__name__}: {e}")
        qc.append({"as_of_date": as_of, "patient_id": p["patient"]["patient_id"], "episode_id": p["patient"]["episode_id"],
                   "branch": branch, "risk": p["risk"]["outcome_risk_level"], "score": p["risk"]["score"],
                   "valid": not errs, "errors": "; ".join(errs)[:2000],
                   "length_warnings": "; ".join(cqi_payload.length_warnings(p))[:2000], "pdf": pdf, "pdf_brief": brief})
    if render and by_branch:
        try:
            from pypdf import PdfWriter
        except ImportError:
            PdfWriter = None
            print("[info] pypdf not installed - skipping merged branch booklets")
        for br, rows in by_branch.items():           # payloads arrive highest risk first
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", br)
            for fmt, folder, idx, suffix in (("full", pdir, 1, ""), ("brief", bdir, 2, "_brief")):
                files = [r_[idx] for r_ in rows if r_[idx]]
                if fmt not in formats or not files:
                    continue
                ov = build_booklet.build_overview([r_[0] for r_ in rows if r_[idx]], os.path.join(folder, f"_overview_{safe}{suffix}.pdf"),
                                                  br.title(), cqi_payload._mdy(as_of), sample=sample, brief=fmt == "brief")
                if PdfWriter:
                    w = PdfWriter()
                    for f in [ov] + files:
                        w.append(f)
                    w.write(os.path.join(folder, f"_BRANCH_{safe}{suffix}.pdf")); w.close()
                    os.remove(ov)
    pd.DataFrame(qc).to_csv(os.path.join(out_dir, "qc.csv"), index=False)
    return qc


def main():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default="2026-08-15")
    ap.add_argument("--config", default=os.path.join(root, "config", "datasets.yaml"))
    ap.add_argument("--source", default=None, help="override every dataset: csv | auto | table | path")
    ap.add_argument("--branches", default="", help="comma list of branch_name values")
    ap.add_argument("--out", default=None, help="default: output/<as_of>")
    ap.add_argument("--sample", action="store_true", help="mark payloads as synthetic (prints SAMPLE DATA)")
    ap.add_argument("--no-render", action="store_true", help="JSON only")
    ap.add_argument("--format", default="both", choices=["full", "brief", "both"], help="full booklet, short summary, or both")
    a = ap.parse_args()
    ds = Datasets(a.config, source=a.source)
    out = a.out or os.path.join(root, "output", a.as_of)
    qc = run(ds, a.as_of, out, render=not a.no_render, branches=[b.strip() for b in a.branches.split(",") if b.strip()], sample=a.sample,
             formats=("full", "brief") if a.format == "both" else (a.format,))
    for q in qc:
        print(f"{q['patient_id']:<10} {q['risk']:<10} {q['score']:.2f}  {'OK ' if q['valid'] else 'INVALID'}  {q['errors'][:120]}")
    for line in ds.log:
        print(line)
    print(f"{len(qc)} patients -> {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
