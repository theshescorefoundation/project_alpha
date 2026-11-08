"""Branch-level roll-up for Project Alpha.

Reads ONLY what the original pipeline already has:
  - patient payload JSON written by cqi_booklet/src/build_payloads.py  (CQI cohort detail)
  - the canonical CSVs in cqi_booklet/data/csv                         (all active episodes)
and returns one summary dict per branch. The branch PDF (build_branch.py) and the Gradio app
both read this, so the handout and the screen always show the same numbers.

Rules are documented in docs/branch_report_spec.md.
"""
from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter, defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CQI = os.path.join(ROOT, "cqi_booklet")
CSV_DIR = os.path.join(CQI, "data", "csv")

# Same constants as cqi_booklet/src/build_payloads.py, so numbers agree with the booklet.
WEEKS = 9
EPI_STATUSES = {"CURRENT", "RECERTIFIED", "ON-HOLD"}
RISING_DELTA = 0.05
COMPLETED, MISSED = {"COMPLETED", "COMPLETE"}, {"MISSED", "MISSED VISIT"}
DISC = {"SN": "SN", "RN": "SN", "LPN": "SN", "LVN": "SN", "PT": "PT", "PTA": "PT", "OT": "OT", "COTA": "OT",
        "ST": "ST", "SLP": "ST", "HHA": "HHA", "AIDE": "HHA", "MSW": "MSW", "MSS": "MSW"}
DISC_RE = re.compile(r"^\s*(COTA|PTA|LPN|LVN|SLP|AIDE|MSW|MSS|HHA|SN|RN|PT|OT|ST)")
DISC_ORDER = ["SN", "PT", "OT", "ST", "HHA", "MSW"]
FREQ_RE = re.compile(r"(\d+)\s*(?:-\s*\d+\s*)?W(?:K|EEKS?)?\s*(\d+)", re.I)
MED_CHECK_DAYS = 14
TIER_ORDER = ["very_high", "high", "moderate", "low"]
TIER_LABEL = {"very_high": "Very high", "high": "High", "moderate": "Moderate", "low": "Low"}
GOAL_ORDER = ["Achieved", "In progress", "Delayed", "Missed", "Not started"]
URGENCY_ORDER = ["Within 24 hrs", "This conference", "Before conference", "This week", "Next SN visit", "Before discharge"]


# ------------------------------------------------------------------ helpers
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


def branch_key(name: str) -> str:
    """'BRANCH 0412 - CENTERWELL HOME HEALTH' -> 'BRANCH 0412'."""
    return (name or "UNKNOWN").split(" - ")[0].strip().upper()


def branch_label(key: str) -> str:
    return key.title()


def missed_category(reason: str) -> str:
    r = (reason or "").lower()
    if "staff" in r or "availab" in r or "coverage" in r:
        return "Agency: staffing"
    if "hospital" in r or "ed visit" in r:
        return "Clinical: hospitalized"
    return "Patient: not home / reschedule / refused"


def _csv(name, csv_dir):
    path = os.path.join(csv_dir, f"{name}.csv")
    return pd.read_csv(path, dtype={"patient_id": str, "episode_id": str}) if os.path.exists(path) else None


def _dates(df, *cols):
    for c in cols:
        if df is not None and c in df:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def _pct(a, b):
    return round(100.0 * a / b) if b else None


def _plural(n, word, plural=None):
    return f"{n} {word if n == 1 else (plural or word + 's')}"


# ------------------------------------------------------------------ loading
def load_payloads(json_dir):
    out = []
    for f in sorted(glob.glob(os.path.join(json_dir, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def default_json_dir(as_of):
    return os.path.join(CQI, "output", as_of, "json")


# ------------------------------------------------------------------ main roll-up
def build_branch_summaries(as_of: str, json_dir: str | None = None, csv_dir: str = CSV_DIR) -> dict[str, dict]:
    asof = pd.Timestamp(as_of)
    payloads = load_payloads(json_dir or default_json_dir(as_of))
    if not payloads:
        raise SystemExit(f"No payload JSON found for {as_of}. Run cqi_booklet/src/build_payloads.py --as-of {as_of} first.")

    ep = _dates(_csv("episodes", csv_dir), "episode_start", "episode_end")
    ep["episode_status"] = ep["episode_status"].str.upper().str.strip().str.replace(r"[ _]+", "-", regex=True)
    ep["episode_end"] = ep["episode_end"].fillna(ep["episode_start"] + pd.to_timedelta(59, unit="D"))
    ep["branch"] = ep["branch_name"].map(branch_key)
    active = ep[ep["episode_status"].isin(EPI_STATUSES) & (ep["episode_start"] <= asof) & (ep["episode_end"] >= asof)].copy()
    active["current_week"] = ((asof - active["episode_start"]).dt.days // 7 + 1).clip(upper=WEEKS + 1)

    rs = _dates(_csv("risk_scores", csv_dir), "as_of_date")
    rs = rs.sort_values(["patient_id", "episode_id", "as_of_date"])
    rs["score_prior"] = rs.groupby(["patient_id", "episode_id"])["score"].shift(1)
    rs = rs[rs["as_of_date"] == asof]
    rs["outcome_risk_level"] = rs["outcome_risk_level"].str.lower().str.strip().str.replace(r"[ -]+", "_", regex=True)
    active = active.merge(rs[["patient_id", "episode_id", "score", "score_prior", "outcome_risk_level"]],
                          on=["patient_id", "episode_id"], how="left")

    orders = _dates(_csv("orders", csv_dir), "effective_dt")
    visits = _dates(_csv("visits", csv_dir), "visit_dt")
    sched = _dates(_csv("sched", csv_dir), "sched_dt")
    alerts = _dates(_csv("vs_alerts", csv_dir), "alert_dt")

    by_branch_payloads = defaultdict(list)
    for p in payloads:
        by_branch_payloads[branch_key(p["patient"]["branch_name"])].append(p)

    meta = payloads[0]["meta"]
    out = {}
    for br in sorted(set(active["branch"]) | set(by_branch_payloads)):
        a = active[active["branch"] == br]
        ps = sorted(by_branch_payloads.get(br, []), key=lambda p: -(p["risk"]["score"] or 0))
        out[br] = _summarize_branch(br, asof, meta, a, ps, orders, visits, sched, alerts, ep)
    return out


def _summarize_branch(br, asof, meta, a, ps, orders, visits, sched, alerts, ep):
    keys = set(zip(a["patient_id"], a["episode_id"]))
    in_branch = lambda df: df[[k in keys for k in zip(df["patient_id"], df["episode_id"])]] if df is not None else None
    state = (a["branch_state"].dropna().iloc[0] if len(a) else
             (ps[0]["patient"].get("branch_state") if ps else ""))
    full_name = (a["branch_name"].iloc[0] if len(a) else ps[0]["patient"]["branch_name"]) if (len(a) or ps) else br

    # ---- census + risk (all active episodes) ----
    tiers = Counter(a["outcome_risk_level"].fillna("unscored"))
    rising = a[(a["score"] - a["score_prior"]) >= RISING_DELTA]
    census = {
        "active_episodes": int(len(a)),
        "cohort": len(ps),
        "by_tier": {t: int(tiers.get(t, 0)) for t in TIER_ORDER},
        "unscored": int(tiers.get("unscored", 0)),
        "rising": int(len(rising)),
        "rising_ids": sorted(rising["patient_id"]),
        "avg_score": round(float(a["score"].mean()), 2) if a["score"].notna().any() else None,
        "avg_score_prior": round(float(a["score_prior"].mean()), 2) if a["score_prior"].notna().any() else None,
    }

    # ---- visit delivery (all active episodes) ----
    start = dict(zip(zip(a["patient_id"], a["episode_id"]), a["episode_start"]))
    cur_wk = dict(zip(zip(a["patient_id"], a["episode_id"]), a["current_week"]))
    grid = defaultdict(lambda: defaultdict(lambda: {"ordered": 0, "delivered": 0}))  # (k, disc) -> week -> cell
    for _, r in in_branch(orders).iterrows():
        k = (r.patient_id, r.episode_id); disc = discipline_of(r.discipline)
        if not disc:
            continue
        eff = r.effective_dt if pd.notna(r.effective_dt) else start[k]
        for wk, n in expand_frequency(r.frequency, max(1, (eff - start[k]).days // 7 + 1)):
            grid[(k, disc)][wk]["ordered"] += n
    v = in_branch(visits)
    v = v[v["visit_dt"] <= asof].copy()
    v["disc"] = v["visit_type"].map(discipline_of)
    v["st"] = v["visit_status"].str.upper().str.strip()
    v["k"] = list(zip(v["patient_id"], v["episode_id"]))
    v["week"] = [(d - start[k]).days // 7 + 1 for d, k in zip(v["visit_dt"], v["k"])]
    done, missed = v[v["st"].isin(COMPLETED)], v[v["st"].isin(MISSED)]
    for _, r in done.iterrows():
        if r.disc:
            grid[(r.k, r.disc)][r.week]["delivered"] += 1
    s = in_branch(sched)
    s = s[s["sched_dt"] > asof].copy()
    s["disc"] = s["visit_type"].map(discipline_of)

    disc_rows, per_patient = {}, {}
    for (k, disc), weeks in grid.items():
        d = disc_rows.setdefault(disc, {"discipline": disc, "ordered_to_date": 0, "delivered_to_date": 0, "missed": 0,
                                        "remaining": 0, "scheduled": 0, "unscheduled": 0, "patients_unscheduled": set()})
        for wk, c in weeks.items():
            if wk < cur_wk[k]:
                d["ordered_to_date"] += c["ordered"]
                d["delivered_to_date"] += min(c["delivered"], c["ordered"]) if c["ordered"] else 0
        # remaining = visits ordered from the current week on (booklet definition, e.g. "7 ordered visits remain")
        rem = sum(c["ordered"] for wk, c in weeks.items() if wk >= cur_wk[k])
        sch = int(((s["patient_id"] == k[0]) & (s["episode_id"] == k[1]) & (s["disc"] == disc)).sum())
        d["remaining"] += rem; d["scheduled"] += sch
        pp = per_patient.setdefault(k[0], {"remaining": 0, "scheduled": 0})
        pp["remaining"] += rem; pp["scheduled"] += sch
        uns = max(rem - sch, 0); d["unscheduled"] += uns
        if uns:
            d["patients_unscheduled"].add(k[0])
    for disc, n in Counter(missed["disc"].dropna()).items():
        disc_rows.setdefault(disc, {"discipline": disc, "ordered_to_date": 0, "delivered_to_date": 0, "missed": 0, "remaining": 0,
                                    "scheduled": 0, "unscheduled": 0, "patients_unscheduled": set()})["missed"] = int(n)
    delivery = []
    for disc in sorted(disc_rows, key=lambda x: DISC_ORDER.index(x) if x in DISC_ORDER else 99):
        d = disc_rows[disc]
        d["delivery_pct"] = _pct(d["delivered_to_date"], d["ordered_to_date"])
        d["patients_unscheduled"] = sorted(d["patients_unscheduled"])
        delivery.append(d)
    tot = {f: sum(d[f] for d in delivery) for f in ("ordered_to_date", "delivered_to_date", "missed", "remaining", "scheduled", "unscheduled")}
    tot["delivery_pct"] = _pct(tot["delivered_to_date"], tot["ordered_to_date"])

    missed_list = [{"patient_id": r.patient_id, "date": r.visit_dt.strftime("%m/%d"), "discipline": r.disc or "",
                    "reason": r.missed_reason if isinstance(r.missed_reason, str) else "Not documented",
                    "category": missed_category(r.missed_reason if isinstance(r.missed_reason, str) else "")}
                   for _, r in missed.sort_values("visit_dt").iterrows()]
    missed_by_cat = Counter(m["category"] for m in missed_list)

    # ---- escalation ----
    al = in_branch(alerts)
    al = al[al["alert_dt"] <= asof] if al is not None else al
    not_notified = al[al["physician_notified"].astype(str).str.upper().str.strip().isin({"N", "NO", "FALSE", "0"})] if al is not None else al
    escalation = {"alerts": int(len(al)) if al is not None else 0,
                  "not_notified": int(len(not_notified)) if not_notified is not None else 0,
                  "not_notified_list": [{"patient_id": r.patient_id, "date": r.alert_dt.strftime("%m/%d"), "alert": r.alert_text}
                                        for _, r in (not_notified.iterrows() if not_notified is not None else [])]}

    # ---- cohort detail from payloads ----
    traj = Counter((p["vitals"].get("trajectory") or {}).get("class") or "insufficient_data" for p in ps)
    deteriorating = [p["patient"]["patient_id"] for p in ps if (p["vitals"].get("trajectory") or {}).get("class") == "deteriorating"]
    near = [p for p in ps if (p["patient"].get("discharge_window") or {}).get("flag")]
    goals = Counter(); goal_lag_disc = Counter(); goal_lag_ids = set()
    for p in ps:
        for g in (p.get("goals") or {}).get("items") or []:
            goals[g["status"]] += 1
            if g["status"] in ("Missed", "Not started"):
                goal_lag_disc[g.get("discipline") or "?"] += 1; goal_lag_ids.add(p["patient"]["patient_id"])
    meds_flag, adherence_ids, hr_ids = [], [], []
    for p in ps:
        m = p.get("medications") or {}
        if m.get("high_risk"):
            hr_ids.append(p["patient"]["patient_id"])
            last, day = m.get("last_check_day"), p["patient"]["episode_day"]
            if last is None or day - last > MED_CHECK_DAYS:
                meds_flag.append(p["patient"]["patient_id"])
        if (m.get("adherence_concerns") or 0) > 0:
            adherence_ids.append(p["patient"]["patient_id"])
    net = {"no_pcp": [], "refer_cwpco": [], "consider_cwpco": [], "refer_cwrx": [], "cwpco_enrolled": [], "cwrx_enrolled": []}
    for p in ps:
        cn, pid = p.get("care_network") or {}, p["patient"]["patient_id"]
        if cn.get("has_pcp") is False: net["no_pcp"].append(pid)
        if (cn.get("refer_cwpco") or {}).get("indicator") == "Refer": net["refer_cwpco"].append(pid)
        if (cn.get("refer_cwpco") or {}).get("indicator") == "Consider": net["consider_cwpco"].append(pid)
        if (cn.get("refer_cwrx") or {}).get("indicator") == "Refer": net["refer_cwrx"].append(pid)
        if cn.get("cwpco_enrolled"): net["cwpco_enrolled"].append(pid)
        if cn.get("cwrx_enrolled"): net["cwrx_enrolled"].append(pid)
    gaps = {p["patient"]["patient_id"]: [x.strip() for x in re.split(r"[|\n]", (p.get("narratives") or {}).get("data_gaps") or "") if x.strip()]
            for p in ps}
    adverse = [{"patient_id": p["patient"]["patient_id"], **e} for p in ps for e in (p.get("care_review") or {}).get("adverse_events") or []]
    urgent_patient_actions = [{"patient_id": p["patient"]["patient_id"], **x} for p in ps
                              for x in (p.get("summary") or {}).get("actions") or [] if x.get("when") == "Within 24 hrs"]

    roster = []
    for p in ps:
        pt, rk = p["patient"], p["risk"]
        gi = (p.get("goals") or {}).get("items") or []
        pv = per_patient.get(pt["patient_id"], {"remaining": 0, "scheduled": 0})   # same numbers as the delivery table
        rem, sch = pv["remaining"], pv["scheduled"]
        dr = p.get("discharge_recommendation") or {}
        acts = (p.get("summary") or {}).get("actions") or []
        roster.append({
            "patient_id": pt["patient_id"], "episode_id": pt["episode_id"],
            "tier": rk["outcome_risk_level"], "score": rk["score"],
            "delta": round(rk["score"] - rk["score_prior"], 2) if rk.get("score_prior") is not None else None,
            "percentile": rk.get("percentile"),
            "trajectory": (p["vitals"].get("trajectory") or {}).get("class") or "insufficient_data",
            "action_window": (p["vitals"].get("trajectory") or {}).get("action_window"),
            "episode_day": pt["episode_day"], "episode_length": pt["episode_length"],
            "days_to_end": pt.get("days_to_episode_end"),
            "near_discharge": bool((pt.get("discharge_window") or {}).get("flag")),
            "recommendation": dr.get("recommendation"), "confidence": dr.get("confidence"),
            "goals_met": sum(1 for g in gi if g["status"] == "Achieved"), "goals_total": len(gi),
            "visits_remaining": rem, "visits_scheduled": sch,
            "data_gaps": len(gaps.get(pt["patient_id"], [])),
            "found": (p.get("at_a_glance") or {}).get("found") or (p.get("summary") or {}).get("headline"),
            "patient_goal": (p.get("goals") or {}).get("patient_stated_goal"),
            "top_action": acts[0]["action"] if acts else None, "top_action_when": acts[0].get("when") if acts else None,
        })
    discharge_panels = [{"patient_id": p["patient"]["patient_id"], "days_remaining": p["patient"]["discharge_window"].get("days_remaining"),
                         "episode_end": p["patient"].get("episode_end"), **(p.get("discharge_recommendation") or {})} for p in near]

    summary = {
        "branch": br, "branch_label": branch_label(br), "branch_name": full_name, "state": state,
        "as_of_date": asof.strftime("%Y-%m-%d"), "conference_label": meta.get("conference_label"),
        "cohort_label": meta.get("cohort_label"), "model_version": meta.get("model_version"), "sample": bool(meta.get("sample")),
        "census": census,
        "cohort_status": {"trajectory": {k: int(traj.get(k, 0)) for k in ("deteriorating", "insufficient_data", "stable", "improving")},
                          "deteriorating_ids": deteriorating, "near_discharge": len(near),
                          "recommendations": dict(Counter((p.get("discharge_recommendation") or {}).get("recommendation") or "n/a" for p in near))},
        "delivery": delivery, "delivery_total": tot,
        "missed": missed_list, "missed_by_category": dict(missed_by_cat),
        "escalation": escalation,
        "goals": {g: int(goals.get(g, 0)) for g in GOAL_ORDER}, "goals_lagging_by_discipline": dict(goal_lag_disc),
        "goals_lagging_ids": sorted(goal_lag_ids),
        "medications": {"patients_high_risk": hr_ids, "high_risk_no_recent_check": meds_flag, "adherence_concern": adherence_ids},
        "care_network": net,
        "data_gaps": {"total": sum(len(x) for x in gaps.values()), "by_patient": {k: len(x) for k, x in gaps.items() if x}},
        "adverse_events": adverse,
        "urgent_patient_actions": urgent_patient_actions,
        "patients": roster,
        "discharge_panels": discharge_panels,
    }
    summary["findings"] = branch_findings(summary)
    summary["actions"] = branch_actions(summary)
    return summary


# ------------------------------------------------------------------ narrative (rule-based, numbers only)
def branch_findings(s):
    """Short 'how the branch is doing' statements. tone: alert | ok | neutral."""
    f, c, cs, t = [], s["census"], s["cohort_status"], s["delivery_total"]
    hv = c["by_tier"]["very_high"] + c["by_tier"]["high"]
    trend = ""
    if c["avg_score"] is not None and c["avg_score_prior"] is not None:
        d = c["avg_score"] - c["avg_score_prior"]
        trend = f"; average risk score {c['avg_score_prior']:.2f} → {c['avg_score']:.2f}"
    f.append(("alert" if hv or c["rising"] else "ok",
              f"{_plural(c['active_episodes'], 'active episode')}: {hv} high or very high risk, {c['rising']} rising since the last run{trend}."))
    if cs["trajectory"]["deteriorating"]:
        f.append(("alert", f"{_plural(cs['trajectory']['deteriorating'], 'patient')} on the CQI list with deteriorating vitals "
                           f"({', '.join(cs['deteriorating_ids'])})."))
    if t["delivery_pct"] is not None:
        parts = ", ".join(f"{d['discipline']} {d['delivery_pct']}%" for d in s["delivery"] if d["delivery_pct"] is not None)
        f.append(("ok" if t["delivery_pct"] >= 90 else "alert",
                  f"Visit delivery to date: {t['delivery_pct']}% of ordered visits ({parts})."))
    if s["missed"]:
        cats = "; ".join(f"{n} {k.split(':')[0].lower()}" for k, n in sorted(s["missed_by_category"].items(), key=lambda x: -x[1]))
        f.append(("alert", f"{_plural(len(s['missed']), 'missed visit')} this period ({cats})."))
    if t["unscheduled"]:
        f.append(("alert", f"{_plural(t['unscheduled'], 'ordered visit')} still to deliver with nothing booked."))
    if s["escalation"]["not_notified"]:
        f.append(("alert", f"{_plural(s['escalation']['not_notified'], 'vitals alert')} with no documented physician notification."))
    g = s["goals"]; gt = sum(g.values())
    if gt:
        f.append(("alert" if g["Delayed"] + g["Missed"] + g["Not started"] else "neutral",
                  f"Care-plan goals (CQI list): {g['Achieved']} of {gt} achieved, {g['In progress']} in progress, {g['Delayed']} delayed, "
                  f"{g['Missed'] + g['Not started']} missed or not started."))
    if cs["near_discharge"]:
        recs = ", ".join(f"{k} ×{v}" for k, v in cs["recommendations"].items())
        f.append(("neutral", f"{_plural(cs['near_discharge'], 'patient')} within 14 days of episode end (system: {recs})."))
    if s["data_gaps"]["total"]:
        f.append(("neutral", f"{_plural(s['data_gaps']['total'], 'data gap')} flagged by CQI across {_plural(len(s['data_gaps']['by_patient']), 'patient')}."))
    return [{"tone": a, "text": b} for a, b in f]


def branch_actions(s):
    """Branch-level to-do list. Each item: action, owner, when, patients, why."""
    acts, cs, t = [], s["cohort_status"], s["delivery_total"]
    if cs["deteriorating_ids"]:
        acts.append(("Same-day clinician review of deteriorating vitals; confirm the vitals action window is met",
                     "Clinical manager / SN", "Within 24 hrs", cs["deteriorating_ids"], "Vitals trajectory: deteriorating"))
    if s["escalation"]["not_notified"]:
        ids = sorted({x["patient_id"] for x in s["escalation"]["not_notified_list"]})
        acts.append(("Notify the physician and document follow-up for vitals alerts with no MD notification",
                     "Clinical manager", "Within 24 hrs", ids, f"{s['escalation']['not_notified']} alert(s), MD not notified"))
    if t["unscheduled"]:
        ids = sorted({i for d in s["delivery"] for i in d["patients_unscheduled"]})
        by = ", ".join(f"{d['unscheduled']} {d['discipline']}" for d in s["delivery"] if d["unscheduled"])
        urgent = any(i in cs["deteriorating_ids"] for i in ids)
        acts.append((f"Book the remaining ordered visits ({by})", "Scheduling", "Within 24 hrs" if urgent else "This week", ids,
                     f"{t['unscheduled']} ordered visit(s) with nothing scheduled"))
    staff = [m for m in s["missed"] if m["category"].startswith("Agency")]
    if staff:
        by = ", ".join(f"{n} {d}" for d, n in Counter(m["discipline"] for m in staff).items())
        acts.append((f"Review staffing and coverage: visits missed for staff availability ({by})", "Branch director", "This week",
                     sorted({m["patient_id"] for m in staff}), "Missed-visit reason: staffing"))
    if s["goals_lagging_ids"]:
        by = ", ".join(f"{n} {d}" for d, n in s["goals_lagging_by_discipline"].items())
        acts.append((f"Confirm orders and staffing for missed or not-started goals ({by})", "Clinical manager", "This week",
                     s["goals_lagging_ids"], "Goal status: missed / not started"))
    m = s["medications"]
    med_ids = sorted(set(m["high_risk_no_recent_check"]) | set(m["adherence_concern"]))
    if med_ids:
        acts.append(("Medication teach-back on high-risk meds; check understanding and adherence", "SN", "Next SN visit", med_ids,
                     f"No understanding check in {MED_CHECK_DAYS} days or adherence concern documented"))
    if cs["near_discharge"]:
        ids = [x["patient_id"] for x in s["discharge_panels"]]
        acts.append(("Make the discharge or recertification decision; physician to confirm", "IDT conference", "This conference", ids,
                     "Episode ends within 14 days"))
    n = s["care_network"]
    ref = sorted(set(n["refer_cwpco"] + n["consider_cwpco"] + n["refer_cwrx"] + n["no_pcp"]))
    if ref:
        bits = [x for x in (f"CW PCO {len(n['refer_cwpco'])}" if n["refer_cwpco"] else "",
                            f"CW Rx {len(n['refer_cwrx'])}" if n["refer_cwrx"] else "",
                            f"no PCP {len(n['no_pcp'])}" if n["no_pcp"] else "") if x]
        acts.append(("Offer CenterWell referrals and arrange primary care where missing", "Clinical manager", "Before discharge", ref,
                     "Referral indicators: " + ", ".join(bits)))
    if s["data_gaps"]["total"]:
        acts.append(("Resolve CQI data gaps before conference decisions", "Clinical quality", "Before conference",
                     sorted(s["data_gaps"]["by_patient"]), f"{s['data_gaps']['total']} gap(s)"))
    rank = {u: i for i, u in enumerate(URGENCY_ORDER)}
    acts.sort(key=lambda x: rank.get(x[2], 99))
    return [{"action": a, "owner": o, "when": w, "patients": p, "why": y} for a, o, w, p, y in acts]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Print branch summaries as JSON")
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--json-dir")
    a = ap.parse_args()
    print(json.dumps(build_branch_summaries(a.as_of, a.json_dir), indent=1, default=str))
