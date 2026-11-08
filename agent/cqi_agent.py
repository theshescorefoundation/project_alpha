"""CQI care-gap agent - scans operational data on clinicians' behalf and finds care opportunities.

One weekly run = five steps:
  1 SENSE       load the operational datasets for every active episode (CSV on a laptop; Unity Catalog
                tables or parquet/delta paths on Databricks - same registry as the booklet pipeline)
  2 DETECT      run the care-gap detectors below over every episode
  3 PRIORITISE  score each finding (urgency + risk tier + rising risk) and rank
  4 EXPLAIN     a short brief per branch - rule-based, or by the configured LLM (agent.explain_with_llm)
  5 PUBLISH     opportunities.json / .csv, agent_brief.md and agent_run.json, plus MEMORY: each finding is
                marked new / ongoing (weeks open) against the previous run, and last week's findings that
                no longer fire are listed as resolved

Run locally:   .venv\\Scripts\\python agent\\cqi_agent.py --as-of 2026-08-15
Databricks:    cqi_booklet/notebooks/02_cqi_agent_weekly.py (scheduled weekly by databricks.yml)
Config:        config/project_alpha.yaml -> data, agent, llm
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "branch_report"))
import aggregate as A  # noqa: E402
import alpha_config  # noqa: E402

HIGH_RISK_CLASSES = ("anticoagulant", "antiplatelet", "insulin", "sulfonylurea", "opioid", "digoxin", "antiarrhythmic")
TRUE = {"true", "1", "yes", "y", "t"}
TIER_BONUS = {"very_high": 20, "high": 15, "moderate": 5, "low": 0}
DATASETS = ["episodes", "risk_scores", "orders", "visits", "sched", "vs_alerts", "vitals", "llm_goals",
            "medications", "wounds", "care_network"]


def truthy(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return True if s in TRUE else False if s in {"false", "0", "no", "n", "f"} else None


def _d(x):
    return pd.to_datetime(x, errors="coerce")


# =============================================================================== 1 SENSE
def sense(load, as_of=None):
    data = {n: load(n) for n in DATASETS}
    for n, cols in {"episodes": ["episode_start", "episode_end"], "risk_scores": ["as_of_date"], "orders": ["effective_dt"],
                    "visits": ["visit_dt"], "sched": ["sched_dt"], "vs_alerts": ["alert_dt"], "vitals": ["visit_dt"],
                    "llm_goals": ["as_of_date"], "medications": ["start_dt"], "wounds": ["assess_dt"],
                    "care_network": ["pcp_last_visit_dt"]}.items():
        df = data.get(n)
        if df is not None:
            for c in cols:
                if c in df:
                    df[c] = _d(df[c])
            for k in ("patient_id", "episode_id"):
                if k in df:
                    df[k] = df[k].astype(str)
    rs = data["risk_scores"]
    asof = pd.Timestamp(as_of) if as_of else rs["as_of_date"].max()
    ep = data["episodes"].copy()
    ep["episode_status"] = ep["episode_status"].astype(str).str.upper().str.strip().str.replace(r"[ _]+", "-", regex=True)
    ep["episode_end"] = ep["episode_end"].fillna(ep["episode_start"] + pd.to_timedelta(59, unit="D"))
    ep["branch"] = ep["branch_name"].map(A.branch_key)
    act = ep[ep["episode_status"].isin(A.EPI_STATUSES) & (ep["episode_start"] <= asof) & (ep["episode_end"] >= asof)].copy()
    act["episode_day"] = (asof - act["episode_start"]).dt.days
    act["current_week"] = (act["episode_day"] // 7 + 1).clip(upper=A.WEEKS + 1)
    act["days_to_end"] = (act["episode_end"] - asof).dt.days
    rs = rs.sort_values(["patient_id", "episode_id", "as_of_date"]).copy()
    rs["score_prior"] = rs.groupby(["patient_id", "episode_id"])["score"].shift(1)
    rs = rs[rs["as_of_date"] == asof].copy()
    rs["outcome_risk_level"] = rs["outcome_risk_level"].astype(str).str.lower().str.strip().str.replace(r"[ -]+", "_", regex=True)
    act = act.merge(rs[["patient_id", "episode_id", "score", "score_prior", "outcome_risk_level"]], on=["patient_id", "episode_id"], how="left")
    return asof, act, data


# =============================================================================== 2 DETECT
def _rows(df, k):
    if df is None or not len(df):
        return df.iloc[0:0] if df is not None else None
    m = (df["patient_id"] == k[0]) & ((df["episode_id"] == k[1]) if "episode_id" in df else True)
    return df[m]


def _vitals_series(v):
    """Normalise both vitals layouts (WEIGHT/BP 'x/y'/PULSE/SPO2 and SYSTOLIC BP/HEART RATE/O2 SAT) to columns."""
    out = defaultdict(dict)
    for _, r in v.iterrows():
        name, val, day = str(r["vital_name"]).upper().strip(), str(r["vital_value"]).strip(), r["visit_dt"]
        try:
            if name == "BP" and "/" in val:
                out[day]["sbp"] = float(val.split("/")[0])
            elif name in ("SYSTOLIC BP", "SBP"):
                out[day]["sbp"] = float(val)
            elif name in ("PULSE", "HEART RATE", "HR"):
                out[day]["pulse"] = float(val)
            elif name in ("SPO2", "O2 SAT", "SPO2 %"):
                out[day]["spo2"] = float(val)
            elif name == "WEIGHT":
                out[day]["weight"] = float(val)
        except ValueError:
            continue
    return pd.DataFrame.from_dict(out, orient="index").sort_index()


def detect(asof, act, data, cfg):
    lim = cfg.get("limits") or {}
    look = int(cfg.get("lookback_days", 14))
    found = []

    def add(e, kind, finding, action, owner, when, evidence, sources, discipline=""):
        found.append({"type": kind, "patient_id": e.patient_id, "episode_id": e.episode_id, "branch": e.branch,
                      "discipline": discipline, "finding": finding, "action": action, "owner": owner, "when": when,
                      "evidence": evidence, "sources": sources, "risk_level": e.outcome_risk_level if isinstance(e.outcome_risk_level, str) else "unscored",
                      "risk_score": None if pd.isna(e.score) else round(float(e.score), 2),
                      "rising": bool(pd.notna(e.score) and pd.notna(e.score_prior) and e.score - e.score_prior >= A.RISING_DELTA),
                      "episode_day": int(e.episode_day), "days_to_end": int(e.days_to_end)})

    for e in act.itertuples():
        k = (e.patient_id, e.episode_id)
        risky = e.outcome_risk_level in ("high", "very_high")
        # ---- vitals: limit breach, weight gain, monitoring gap
        deteriorating = False
        v = _rows(data.get("vitals"), k)
        vs = _vitals_series(v) if v is not None and len(v) else pd.DataFrame()
        if len(vs):
            last_day = vs.index.max()
            lastrow = vs.loc[last_day]
            breaches = []
            if "spo2" in vs and pd.notna(lastrow.get("spo2")) and lastrow["spo2"] < lim.get("spo2_low", 90):
                breaches.append(f"SpO2 {lastrow['spo2']:.0f}% (< {lim.get('spo2_low', 90):.0f})")
            if "pulse" in vs and pd.notna(lastrow.get("pulse")) and (lastrow["pulse"] > lim.get("pulse_high", 100) or lastrow["pulse"] < lim.get("pulse_low", 50)):
                breaches.append(f"pulse {lastrow['pulse']:.0f}")
            if "sbp" in vs and pd.notna(lastrow.get("sbp")) and lastrow["sbp"] > lim.get("sbp_high", 160):
                breaches.append(f"SBP {lastrow['sbp']:.0f}")
            if "weight" in vs and vs["weight"].notna().sum() >= 2:
                w = vs["weight"].dropna()
                gain = w.iloc[-1] - w.iloc[0]
                if gain >= lim.get("weight_gain_lb", 5):
                    breaches.append(f"weight +{gain:.1f} lb since {w.index[0]:%m/%d}")
            if breaches:
                deteriorating = True
                add(e, "vitals_outside_limits", "Vitals outside limits: " + "; ".join(breaches) + f" (last reading {last_day:%m/%d}).",
                    "Same-day clinician review; call the patient and notify the MD per parameters.", "SN / Clinical manager",
                    "Within 24 hrs", {"last_reading": f"{last_day:%Y-%m-%d}", "breaches": breaches}, ["vitals"])
        if not len(vs):
            od0 = _rows(data.get("orders"), k)
            if od0 is not None and any(A.discipline_of(x) == "SN" for x in od0["discipline"]) and e.episode_day > 3:
                add(e, "monitoring_gap", f"No vitals recorded this episode (day {int(e.episode_day)}) although SN visits are ordered.",
                    "Capture vitals at the next SN visit and check documentation.", "SN / Clinical manager",
                    "Within 24 hrs" if risky else "This week", {"days_since_vitals": None}, ["vitals", "orders"])
        else:
            gap = (asof - last_day).days
            if gap > 7:
                add(e, "monitoring_gap", f"No vitals for {gap} days (last {last_day:%m/%d}).",
                    "Schedule an SN visit or phone check to capture vitals.", "Scheduling / SN",
                    "Within 24 hrs" if deteriorating or risky else "This week", {"days_since_vitals": gap}, ["vitals", "sched"])
        # ---- vitals alerts with no physician notification
        al = _rows(data.get("vs_alerts"), k)
        if al is not None and len(al):
            nn = al[[truthy(x) is False for x in al["physician_notified"]]]
            nn = nn[nn["alert_dt"] <= asof]
            if len(nn):
                add(e, "alert_not_escalated", f"{len(nn)} vitals alert(s) with no documented physician notification: "
                    + "; ".join(f"{r.alert_dt:%m/%d} {r.alert_text}" for r in nn.itertuples()),
                    "Notify the physician and document the follow-up.", "Clinical manager", "Within 24 hrs",
                    {"alerts": [str(x) for x in nn["alert_text"]]}, ["vs_alerts"])
        # ---- ordered visits not booked (per discipline, booklet definition of remaining)
        od = _rows(data.get("orders"), k)
        sc = _rows(data.get("sched"), k)
        if od is not None and len(od):
            rem = Counter()
            for r in od.itertuples():
                disc = A.discipline_of(r.discipline)
                if not disc:
                    continue
                eff = r.effective_dt if pd.notna(r.effective_dt) else e.episode_start
                for wk, n in A.expand_frequency(r.frequency, max(1, (eff - e.episode_start).days // 7 + 1)):
                    if wk >= e.current_week:
                        rem[disc] += n
            booked = Counter(A.discipline_of(x) for x in (sc[sc["sched_dt"] > asof]["visit_type"] if sc is not None and len(sc) else []))
            for disc, n in rem.items():
                gap = n - booked.get(disc, 0)
                if gap > 0:
                    add(e, "visits_not_booked", f"{gap} of {n} remaining ordered {disc} visit(s) have nothing scheduled.",
                        f"Book the remaining {disc} visits.", "Scheduling",
                        "Within 24 hrs" if (deteriorating or risky) and disc in ("SN", "PT") else "This week",
                        {"remaining": n, "scheduled": booked.get(disc, 0)}, ["orders", "sched"], disc)
        # ---- missed visits in the lookback window
        vi = _rows(data.get("visits"), k)
        if vi is not None and len(vi):
            st = vi["visit_status"].astype(str).str.upper().str.strip()
            ms = vi[st.isin(A.MISSED) & (vi["visit_dt"] <= asof) & (vi["visit_dt"] >= asof - pd.to_timedelta(look, unit="D"))]
            if len(ms):
                cats = Counter(A.missed_category(r if isinstance(r, str) else "") for r in ms["missed_reason"])
                staffing = cats.get("Agency: staffing", 0)
                add(e, "missed_visits", f"{len(ms)} missed visit(s) in the last {look} days: "
                    + "; ".join(f"{r.visit_dt:%m/%d} {A.discipline_of(r.visit_type) or ''} ({r.missed_reason if isinstance(r.missed_reason, str) else 'no reason'})" for r in ms.itertuples()),
                    "Make up the missed visits" + ("; review staffing coverage." if staffing else "."),
                    "Branch director" if staffing else "Clinical manager", "This week",
                    {"by_reason": dict(cats)}, ["visits", "client_logs"])
            # ---- high-risk medication understanding
            meds = _rows(data.get("medications"), k)
            if meds is not None and len(meds):
                hr = [r.med_name for r in meds.itertuples()
                      if truthy(r.high_risk) or (truthy(r.high_risk) is None and any(c in str(r.drug_class).lower() for c in HIGH_RISK_CLASSES))]
                if hr:
                    done = vi[st.isin(A.COMPLETED)]
                    chk = done[[truthy(x) is True for x in done.get("med_understanding", pd.Series(dtype=object))]] if "med_understanding" in done else done.iloc[0:0]
                    last = chk["visit_dt"].max() if len(chk) else None
                    nonadh = done[[truthy(x) is False for x in done["med_adherent"]]] if "med_adherent" in done else done.iloc[0:0]
                    if last is None or (asof - last).days > look or len(nonadh):
                        why = (f"no understanding check since {last:%m/%d}" if last is not None else "no understanding check documented")
                        if len(nonadh):
                            why += f"; adherence concern on {len(nonadh)} visit(s)"
                        add(e, "high_risk_meds", f"High-risk medication(s) {', '.join(hr)}: {why}.",
                            "Teach-back on high-risk medications at the next SN visit.", "SN", "Next visit",
                            {"high_risk": hr, "last_check": None if last is None else f"{last:%Y-%m-%d}", "adherence_concerns": len(nonadh)},
                            ["medications", "visits"])
        # ---- wounds not reassessed
        wd = _rows(data.get("wounds"), k)
        if wd is not None and len(wd):
            for wid, g in wd.groupby("wound_id"):
                lastw = g.sort_values("assess_dt").iloc[-1]
                if str(lastw["healing_status"]).lower() != "healed" and (asof - lastw["assess_dt"]).days > 7:
                    add(e, "wound_not_reassessed", f"Wound {wid} ({str(lastw['location']).title()}) last assessed {lastw['assess_dt']:%m/%d}; status {lastw['healing_status']}.",
                        "Reassess and measure the wound at the next SN visit.", "SN", "This week",
                        {"wound_id": wid, "days_since": int((asof - lastw["assess_dt"]).days)}, ["wounds"])
        # ---- goals behind plan (CQI goal inventory)
        gl = _rows(data.get("llm_goals"), k)
        if gl is not None and len(gl):
            gl = gl[gl["as_of_date"] == gl["as_of_date"].max()]
            lag = gl[gl["status"].astype(str).str.lower().isin(["missed", "not_started", "delayed"])]
            if len(lag):
                add(e, "goals_behind", f"{len(lag)} care-plan goal(s) delayed, missed or not started: "
                    + "; ".join(f"{r.goal} ({r.discipline}, {str(r.status).replace('_', ' ')})" for r in lag.itertuples()),
                    "Confirm orders and staffing for these disciplines; update the plan of care.", "Clinical manager", "This week",
                    {"goals": [str(x) for x in lag["goal"]]}, ["llm_goals"])
        # ---- discharge / recert decision window
        if 0 <= e.days_to_end <= 14:
            add(e, "decision_window", f"Episode ends in {e.days_to_end} days ({e.episode_end:%m/%d}).",
                "Discharge or recertification decision in conference; physician to confirm.", "IDT conference", "This conference",
                {"episode_end": f"{e.episode_end:%Y-%m-%d}"}, ["episodes"])
        # ---- rising risk
        if pd.notna(e.score) and pd.notna(e.score_prior) and e.score - e.score_prior >= A.RISING_DELTA:
            add(e, "rising_risk", f"60-day hospitalization risk rose {e.score_prior:.2f} → {e.score:.2f}.",
                "Review drivers and the care plan in conference.", "IDT conference", "This conference",
                {"score": float(e.score), "prior": float(e.score_prior)}, ["risk_scores", "risk_drivers"])
        # ---- care network
        cn = data.get("care_network")
        cn = cn[cn["patient_id"] == e.patient_id] if cn is not None else None
        if cn is not None and len(cn):
            c = cn.iloc[0]
            if truthy(c.get("has_pcp")) is False:
                add(e, "no_pcp", "No primary care provider on record.", "Arrange primary care before discharge (consider CW PCO).",
                    "Clinical manager", "Before discharge", {"nearest_cwpco_miles": c.get("nearest_cwpco_miles")}, ["care_network"])
    return found


# =============================================================================== 3 PRIORITISE + memory
def prioritise(found, cfg):
    w = cfg.get("urgency_weight") or {}
    for f in found:
        f["priority"] = int(w.get(f["when"], 20) + TIER_BONUS.get(f["risk_level"], 0) + (5 if f["rising"] else 0))
        f["id"] = f"{f['patient_id']}-{f['episode_id']}-{f['type']}" + (f"-{f['discipline']}" if f["discipline"] else "")
    return sorted(found, key=lambda f: (-f["priority"], f["branch"], f["patient_id"]))


def remember(found, agent_root, as_of):
    """Compare with the most recent earlier run: new / ongoing (weeks open) / resolved."""
    prev_dirs = sorted(d for d in glob.glob(os.path.join(agent_root, "*", "opportunities.json"))
                       if os.path.basename(os.path.dirname(d)) < as_of)
    prev = {}
    if prev_dirs:
        with open(prev_dirs[-1], encoding="utf-8") as fh:
            prev = {o["id"]: o for o in json.load(fh)}
    ids = set()
    for f in found:
        ids.add(f["id"])
        p = prev.get(f["id"])
        f["status"] = "ongoing" if p else "new"
        f["weeks_open"] = (p.get("weeks_open", 1) + 1) if p else 1
        f["first_seen"] = p.get("first_seen", p.get("as_of")) if p else as_of
        f["as_of"] = as_of
    resolved = [{"id": k, "patient_id": v["patient_id"], "type": v["type"], "finding": v["finding"]} for k, v in prev.items() if k not in ids]
    return found, resolved, (os.path.basename(os.path.dirname(prev_dirs[-1])) if prev_dirs else None)


DECISION_LABEL = {"agree": "Agree with CQI", "agree_changes": "Agree with changes", "disagree": "Disagree", "escalate": "Escalate to MD"}


def local_decisions(as_of):
    """Laptop: every decisions.csv the app saved (webapp/output/<run>/decisions.csv) up to as_of."""
    rows = []
    for path in sorted(glob.glob(os.path.join(ROOT, "webapp", "output", "*", "decisions.csv"))):
        if os.path.basename(os.path.dirname(path)) <= as_of:
            with open(path, encoding="utf-8") as fh:
                rows += list(csv.DictReader(fh))
    return rows


def attach_decisions(found, decisions):
    """Give each finding the patient's most recent conference decision (from the app / the decisions table)."""
    last = {}
    for d in decisions or []:
        k = (str(d.get("patient_id")), str(d.get("episode_id")))
        if k not in last or str(d.get("saved_ts") or "") > str(last[k].get("saved_ts") or ""):
            last[k] = d
    for f in found:
        d = last.get((f["patient_id"], f["episode_id"]))
        f["last_decision"] = None if not d else {
            "decision": DECISION_LABEL.get(d.get("decision"), d.get("decision")), "conference": d.get("as_of_date"),
            "action_owner": d.get("action_owner") or None, "due_date": d.get("due_date") or None,
            "notes": d.get("notes_discussion") or None}
    return len(last)


# =============================================================================== 4 EXPLAIN
def rule_brief(found, branch, n=8):
    mine = [f for f in found if f["branch"] == branch]
    u24 = [f for f in mine if f["when"] == "Within 24 hrs"]
    pts = Counter(f["patient_id"] for f in mine)
    lines = [f"**{branch.title()}**: {len(mine)} care opportunities across {len(pts)} patient(s); "
             f"{len(u24)} need action within 24 hours ({sum(1 for f in mine if f['status'] == 'new')} new this week)."]
    lines += [f"- [{f['when']}] {f['patient_id']}: {f['finding']} → {f['action']} ({f['owner']})"
              + (f" · last conference: {f['last_decision']['decision']} ({f['last_decision']['conference']})" if f.get("last_decision") else "")
              for f in mine[:n]]
    return "\n".join(lines)


def llm_brief(found, branch, n=8):
    sys.path.insert(0, os.path.join(ROOT, "webapp"))
    import llm  # webapp/llm.py - same provider switch as the app
    items = [{k: f[k] for k in ("patient_id", "type", "finding", "action", "owner", "when", "status", "weeks_open")}
             for f in found if f["branch"] == branch][:40]
    system = ("You write a 3-sentence weekly brief for a home health branch's clinical manager from the CQI agent's findings. "
              "Use ONLY the findings given; mention patient IDs; lead with what must happen in the next 24 hours; no new facts.")
    return llm.call(system, json.dumps(items, default=str), 600)


# =============================================================================== 5 PUBLISH
def publish(out_dir, as_of, found, resolved, briefs, stats):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "opportunities.json"), "w", encoding="utf-8") as fh:
        json.dump(found, fh, indent=1, default=str)
    cols = ["priority", "when", "branch", "patient_id", "episode_id", "type", "discipline", "finding", "action", "owner",
            "status", "weeks_open", "first_seen", "risk_level", "risk_score", "rising", "days_to_end", "last_decision", "sources", "id"]
    with open(os.path.join(out_dir, "opportunities.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for f in found:
            w.writerow({**f, "sources": ", ".join(f["sources"]), "last_decision": (f.get("last_decision") or {}).get("decision") or ""})
    with open(os.path.join(out_dir, "agent_brief.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# CQI agent brief · run {as_of}\n\n" + "\n\n".join(briefs.values())
                 + ("\n\n## Resolved since last run\n" + "\n".join(f"- {r['patient_id']}: {r['finding']}" for r in resolved) if resolved else "") + "\n")
    with open(os.path.join(out_dir, "agent_run.json"), "w", encoding="utf-8") as fh:
        json.dump({**stats, "briefs": briefs, "resolved_items": resolved}, fh, indent=1, default=str)


def run(as_of=None, load=None, out_root=None, explain=None, cfg=None, decisions=None):
    """decisions: list of conference-decision dicts (Databricks: from the decisions table). Default: the app's local CSVs."""
    t0 = time.time()
    cfg = cfg or alpha_config.cfg().get("agent") or {}
    load = load or A.csv_loader()
    out_root = out_root or alpha_config.path(alpha_config.data("agent_root", "agent/output"))
    asof, act, data = sense(load, as_of)                                          # 1
    as_of = asof.strftime("%Y-%m-%d")
    found = prioritise(detect(asof, act, data, cfg), cfg)                         # 2 + 3
    found, resolved, prev_run = remember(found, out_root, as_of)
    n_decided = attach_decisions(found, local_decisions(as_of) if decisions is None else decisions)
    explain = cfg.get("explain_with_llm", False) if explain is None else explain  # 4
    briefs = {}
    for b in sorted({f["branch"] for f in found}):
        try:
            briefs[b] = llm_brief(found, b) if explain else rule_brief(found, b, cfg.get("max_brief_items", 8))
        except Exception as ex:
            briefs[b] = rule_brief(found, b, cfg.get("max_brief_items", 8)) + f"\n_(LLM brief unavailable: {type(ex).__name__})_"
    stats = {"as_of": as_of, "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "episodes_scanned": int(len(act)), "patients_scanned": int(act["patient_id"].nunique()),
             "branches": sorted(act["branch"].unique().tolist()), "opportunities": len(found),
             "within_24h": sum(1 for f in found if f["when"] == "Within 24 hrs"),
             "new": sum(1 for f in found if f["status"] == "new"), "resolved": len(resolved), "previous_run": prev_run,
             "patients_with_decision": n_decided,
             "by_type": dict(Counter(f["type"] for f in found)), "datasets": DATASETS,
             "explained_by": "llm" if explain else "rules", "seconds": round(time.time() - t0, 2)}
    publish(os.path.join(out_root, as_of), as_of, found, resolved, briefs, stats)  # 5
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Project Alpha CQI care-gap agent (weekly)")
    ap.add_argument("--as-of", help="CQI run date (default: latest risk score date)")
    ap.add_argument("--explain-with-llm", action="store_true", help="write branch briefs with the configured LLM")
    a = ap.parse_args()
    s = run(a.as_of, explain=True if a.explain_with_llm else None)
    print(json.dumps({k: s[k] for k in ("as_of", "episodes_scanned", "opportunities", "within_24h", "new", "resolved", "by_type", "seconds")}, indent=1))
