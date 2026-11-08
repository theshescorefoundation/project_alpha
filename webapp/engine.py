"""Question engine: one question in, an answer plus the panels that 'unfold' below it.

Recognised questions get a deterministic answer straight from the data (fast, repeatable, cited).
Anything else goes to Gemini Flash through the scope filter (llm.py), grounded in the same data.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import views as V
from views import e

LOWER_IS_BETTER = ("M18", "M1242", "M2020", "M1400", "M1700", "M1710", "M1720")


@dataclass
class Result:
    title: str
    body: str
    panels: list = field(default_factory=list)
    followups: list = field(default_factory=list)
    srcs: list = field(default_factory=list)
    mode: str = "data"                      # data | ai | refused
    ctx: dict = field(default_factory=dict)  # {"pid":..., "branch":...}
    downloads: dict = field(default_factory=dict)  # label -> path
    note: str | None = None


INTENTS = [  # order matters: first match wins
    ("agent", r"\bagent\b|care gaps?|opportunit|\bscan|what did .{0,20}find|found this week|resolved"),
    ("network", r"pcp|primary care|referr|cw ?pco|cw ?rx|follow(s|ing)?\b.{0,30}\bafter|who follows|network|clinic"),
    ("discharge", r"discharg|ready|go(ing)? home|recert|readiness"),
    ("today", r"today|urgent|24 ?h|right now|priorit|first|need(s)? (action|attention)|act on|to.?do"),
    ("vitals", r"vital|weight|spo2|oxygen|o2 sat|pulse|heart rate|blood pressure|\bbp\b|trajector|fluid"),
    ("goals", r"goal"),
    ("meds", r"med(ication)?s?\b|drug|adheren|anticoag|apixaban|insulin|opioid|oxycodone|pharm(?!acy)"),
    ("wounds", r"wound|ulcer|incision|skin tear|pressure injur"),
    ("function", r"function|oasis|walk|ambulat|transfer|adl|mobility|improv|declin"),
    ("events", r"fall|\bed\b|emergency|hospital|adverse|incident|alert|readmi"),
    ("visits", r"visit|missed|schedul|book|staff|frequenc|deliver|ot\b|msw|therapy|coverage"),
    ("gaps", r"gap|missing data|unsure|data quality|uncertain|check first"),
    ("risk", r"\bwhy\b|risk|driver|on the list|score|flag"),
    ("actions", r"action|next step|what should|recommend|plan"),
    ("overview", r"how (is|are)|doing|overview|summar|branch|everyone|all patients|who('s| is) on|the list|conference|this week|tell me about|show me"),
]
PRONOUN = re.compile(r"\b(she|he|her|him|his|they|them|their|this patient|the patient)\b", re.I)


def detect(q):
    ql = q.lower()
    for name, rx in INTENTS:
        if re.search(rx, ql):
            return name
    return None


def find_pid(run, q):
    for m in re.findall(r"\b\d{7}\b", q):
        if m in run.patients:
            return m
    return None


def label(run, pid):
    return f"Patient {pid}"


# ======================================================================= readiness
def _code(ans):
    m = re.match(r"\s*(\d+)", str(ans or ""))
    return int(m.group(1)) if m else None


def oasis_changes(p):
    better, worse = [], []
    for o in (p.get("clinical") or {}).get("oasis") or []:
        if o.get("changed") and o["item"].startswith(LOWER_IS_BETTER):
            a, b = _code(o.get("first")), _code(o.get("latest"))
            if a is not None and b is not None and a != b:
                (better if b < a else worse).append(f"{o['item']} {a}→{b}")
    return better, worse


def readiness(p):
    """Booklet's discharge recommendation when the episode is in the 14-day window; otherwise the same signals as a snapshot."""
    pt = p["patient"]
    dr = p.get("discharge_recommendation")
    if dr:
        return {"recommendation": dr["recommendation"], "confidence": dr.get("confidence", ""),
                "basis": f"System recommendation · {pt['discharge_window']['days_remaining']} days to episode end",
                "discharge": dr.get("supports_discharge") or [], "recert": dr.get("supports_recert") or [],
                "caveats": dr.get("data_caveats") or [], "steps": dr.get("next_steps") or [], "in_window": True}
    dc, rc, cav = [], [], []
    goals = (p.get("goals") or {}).get("items") or []
    open_g = [g for g in goals if g["status"] != "Achieved"]
    if goals and not open_g:
        dc.append(f"All {len(goals)} care-plan goals met.")
    elif open_g:
        rc.append(f"{len(open_g)} of {len(goals)} goals not yet met.")
    else:
        cav.append("No care-plan goals on record")
    cls = (p["vitals"].get("trajectory") or {}).get("class")
    if cls == "deteriorating":
        rc.append("Vitals deteriorating: " + ((p["vitals"]["trajectory"].get("saw") or "").rstrip(".")) + ".")
    elif cls in ("stable", "improving"):
        dc.append(f"Vitals {cls}.")
    else:
        cav.append("No recent vitals")
    wounds = (p.get("clinical") or {}).get("wounds") or []
    open_w = [w for w in wounds if (w.get("healing_status") or "").lower() != "healed"]
    if open_w:
        rc.append(f"{len(open_w)} open wound(s).")
    elif wounds:
        dc.append("All wounds healed.")
    lvl = p["risk"]["outcome_risk_level"]
    (rc if lvl in ("high", "very_high") else dc).append(f"{lvl.replace('_', ' ').capitalize()} 60-day hospitalization risk ({p['risk']['score']:.2f}).")
    ev = (p.get("care_review") or {}).get("adverse_events") or []
    if ev:
        rc.append(f"{len(ev)} adverse event(s) this episode.")
    better, worse = oasis_changes(p)
    if better: dc.append("Function improved on OASIS: " + ", ".join(better) + ".")
    if worse: rc.append("Function declined on OASIS: " + ", ".join(worse) + ".")
    prog = ((pt.get("clinical_profile") or {}).get("prognosis") or "").lower()
    if prog in ("poor", "guarded"): rc.append(f"Prognosis {prog}.")
    elif prog in ("good", "excellent"): dc.append(f"Prognosis {prog}.")
    gaps = [x for x in re.split(r"[|\n]", (p.get("narratives") or {}).get("data_gaps") or "") if x.strip()]
    if gaps: cav.append(f"{len(gaps)} data gap(s) to resolve")
    r_, d_ = len(rc), len(dc)
    if r_ == 0 and d_ >= 2: rec, conf = "Leaning discharge", "Strong signal"
    elif d_ >= r_ + 2: rec, conf = "Leaning discharge", "Leaning"
    elif r_ >= d_ + 2 or (cls == "deteriorating" and r_ > d_): rec, conf = "Not ready", "Strong signal" if cls == "deteriorating" else "Leaning"
    else: rec, conf = "Mixed signals", "Clinical review"
    return {"recommendation": rec, "confidence": conf,
            "basis": f"Readiness snapshot · episode ends in {pt.get('days_to_episode_end')} days (not yet in the 14-day window)",
            "discharge": dc, "recert": rc, "caveats": cav, "steps": [], "in_window": False}


# ======================================================================= patient answers
def p_overview(run, pid):
    p = run.patients[pid]; pt = p["patient"]; s = readiness(p)
    found = (p.get("at_a_glance") or {}).get("found") or (p.get("summary") or {}).get("headline") or "No CQI summary for this run."
    acts = (p.get("summary") or {}).get("actions") or []
    body = (f"<p class='pa-lead'>{e(found)}</p>" + V.bullets([
        f"{V.TIER.get(p['risk']['outcome_risk_level'], ('',))[0]} 60-day hospitalization risk <b>{p['risk']['score']:.2f}</b>"
        f" (top {max(1, 100 - int(p['risk'].get('percentile') or 0))}% this run)",
        f"Day {pt['episode_day']} of {pt['episode_length']} · {pt.get('days_to_episode_end')} days to episode end",
        f"Discharge readiness: <b>{e(s['recommendation'])}</b> ({e(s['confidence'].lower())})",
    ] + ([f"First action: {e(acts[0]['action'])} — <b style='color:{V.WHEN.get(acts[0].get('when'), V.SKY)}'>{e(acts[0].get('when'))}</b>"] if acts else [])))
    panels = [V.patient_card(p, (run.decisions().get(pid) or {}).get("decision") and
                             _dec_label(run.decisions()[pid]["decision"])),
              V.section("Discharge readiness", V.readiness_card(p, s))]
    opps = [o for o in run.opportunities() if o["patient_id"] == pid]
    if opps:
        panels.append(V.section("What the CQI agent found", V.table(["When", "Patient", "Gap", "Finding", "Action", ""], _opp_rows(opps)),
                                sub=f"{len(opps)} care opportunities this run"))
    return Result(f"{label(run, pid)} · {e(pt['branch_name'].split(' - ')[0].title())}", body, panels,
                  [f"Are {pid}'s vitals stable?", f"Which goals aren't met for {pid}?", f"Why is {pid} on the list?", f"Any falls or ED visits for {pid}?"],
                  ["at_a_glance", "risk_scores", "episodes", "discharge_recommendation"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def _dec_label(code):
    import data
    return data.DECISIONS.get(code, code)


def p_discharge(run, pid):
    p = run.patients[pid]; s = readiness(p)
    if s["in_window"]:
        dr = p["discharge_recommendation"]
        lead = f"<b>{e(s['recommendation'])}</b> ({e(s['confidence'].lower())}). {e(dr.get('rationale'))}"
    else:
        lead = (f"Not in the 14-day discharge window yet ({p['patient'].get('days_to_episode_end')} days to episode end). "
                f"Today's signals: <b>{e(s['recommendation'])}</b> — {len(s['discharge'])} for discharge, {len(s['recert'])} against.")
    cn = p.get("care_network") or {}
    net = []
    if cn:
        net.append(f"PCP: {e(cn.get('pcp_name') or 'none on record')}")
        for k, n in (("refer_cwpco", "CW PCO"), ("refer_cwrx", "CW Rx")):
            if cn.get(k): net.append(f"{n}: <b>{e(cn[k]['indicator'])}</b> — {e(cn[k]['reason'])}")
    panels = [V.section("Discharge readiness", V.readiness_card(p, s), sub="Rule-based · the team and physician decide")]
    if net:
        panels.append(V.section("After discharge", V.bullets(net), sub="Care network"))
    return Result(f"Is {pid} ready for discharge?", f"<p class='pa-lead'>{lead}</p>", panels,
                  [f"Has function improved for {pid}?", f"Which goals aren't met for {pid}?", f"Who follows {pid} after discharge?", f"What data gaps exist for {pid}?"],
                  ["discharge_recommendation", "llm_goals", "vitals", "wounds", "oasis", "patient_profile", "care_network"],
                  ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_vitals(run, pid):
    p = run.patients[pid]; v = p["vitals"]; tr = v.get("trajectory") or {}
    lab, col = V.TRAJ.get(tr.get("class") or "insufficient_data")
    rows = [[e(f["measure"]), e(f["first"]), f"<b>{e(f['latest'])}</b>", e(f["change"]), e(f.get("interpretation") or ""),
             f"<b style='color:{V.WHEN.get(f.get('urgency'), V.SOFT)}'>{e(f.get('urgency') or '')}</b>"] for f in v.get("findings") or []]
    body = (f"<p class='pa-lead'><b style='color:{col}'>{e(lab)}</b>. {e(tr.get('saw') or v.get('adequacy') or '')}</p>"
            + (f"<p>{e(tr.get('flags'))} <b style='color:{col}'>{e(tr.get('action_window') or '')}</b></p>" if tr.get("flags") else ""))
    panels = [V.section("Trend against this patient's limits", V.vitals_chart(p))]
    if rows:
        panels.append(V.section("What CQI found", V.table(["Measure", "First", "Latest", "Change", "Interpretation", "Urgency"], rows)))
    return Result(f"Vitals · {pid}", body, panels,
                  [f"Were alerts escalated for {pid}?", f"Is {pid} ready for discharge?", f"What actions are due for {pid}?"],
                  ["vitals", "vs_alerts", "llm_summary.vitals_*"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_goals(run, pid):
    p = run.patients[pid]; g = p.get("goals") or {}; items = g.get("items") or []
    if not items:
        return Result(f"Goals · {pid}", "<p class='pa-lead'>No care-plan goals are on record for this patient in the CQI data.</p>",
                      followups=[f"What data gaps exist for {pid}?"], srcs=["llm_goals"], ctx={"pid": pid, "branch": run.branch_of(pid)})
    met = sum(1 for x in items if x["status"] == "Achieved")
    open_ = [x for x in items if x["status"] != "Achieved"]
    rows = [[e(x["goal"]), e(x.get("discipline")), V.chip(x["status"], V.GOAL.get(x["status"], V.SOFT)), e(x.get("evidence"))]
            for x in sorted(items, key=lambda x: ["Missed", "Not started", "Delayed", "In progress", "Achieved"].index(x["status"]) if x["status"] in V.GOAL else 9)]
    body = (f"<p class='pa-lead'><b>{met} of {len(items)}</b> goals achieved; {len(open_)} still open"
            + (f" — {', '.join(e(x['goal'][:60]) for x in open_[:2])}" if open_ else "") + ".</p>")
    if g.get("patient_stated_goal"):
        body += f"<div class='pa-quote'>“{e(g['patient_stated_goal'])}”</div>"
    panels = [V.section("Care-plan goals", V.table(["Goal", "Disc.", "Status", "Evidence"], rows))]
    if g.get("interventions"):
        panels.append(V.section("Recommended interventions (CQI draft)", V.bullets([f"<b>{e(i['discipline'])}</b> {e(i['text'])}" for i in g["interventions"]])))
    return Result(f"Goals · {pid}", body, panels, [f"Is {pid} ready for discharge?", f"Were visits missed for {pid}?"],
                  ["llm_goals", "llm_summary.patient_stated_goal", "llm_summary.interventions_text"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_visits(run, pid):
    p = run.patients[pid]; cd = p["care_delivery"]; cw = cd["current_week"]
    rows = []
    for d in cd["disciplines"]:
        o = sum(w["ordered"] or 0 for w in d["weeks"]); dl = sum(w["delivered"] or 0 for w in d["weeks"])
        rem = sum(w["ordered"] or 0 for w in d["weeks"] if w["week"] >= cw); sch = sum(w["scheduled"] or 0 for w in d["weeks"])
        rows.append([f"<b>{e(d['discipline'])}</b>", o, dl, rem, sch,
                     f"<b style='color:{V.CORAL}'>{max(rem - sch, 0)}</b>" if rem > sch else "0"])
    ml = cd.get("missed_log") or []
    body = f"<p class='pa-lead'>{e(cd.get('summary') or (p.get('at_a_glance') or {}).get('care_delivery') or '')}</p>"
    panels = [V.section("Ordered, delivered and booked", V.table(["Discipline", "Ordered", "Delivered", "Remaining", "Booked", "Not booked"], rows),
                        sub=f"Episode week {cw} of {cd['weeks']}")]
    if ml:
        panels.append(V.section("Missed or outstanding care", V.table(["Day", "Disc.", "Visit", "Reason", "Status"],
                      [[f"Day {e(m.get('episode_day'))}", e(m.get("discipline")), e(m.get("visit")), e(m.get("reason")), e(m.get("status"))] for m in ml])))
    return Result(f"Visits · {pid}", body, panels, [f"What actions are due for {pid}?", f"How is {run.branch_of(pid).title()} doing?"],
                  ["orders", "visits", "sched", "client_logs"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_meds(run, pid):
    p = run.patients[pid]; m = p.get("medications") or {}
    if not m or not m.get("active_count"):
        return Result(f"Medications · {pid}", "<p class='pa-lead'>No medication profile is in the CQI data for this patient.</p>",
                      srcs=["medications"], ctx={"pid": pid, "branch": run.branch_of(pid)})
    hr = m.get("high_risk") or []
    body = (f"<p class='pa-lead'>{e(m.get('summary') or '')}</p>" if m.get("summary") else "") + V.stat_tiles([
        (m.get("active_count"), "active medications", V.SKY), (len(hr), "high-risk", V.CORAL if hr else V.LIME),
        (f"{m.get('visits_with_check', 0)}/{m.get('visits_total', 0)}", "SN visits with understanding check", V.AMBER),
        (m.get("adherence_concerns") or 0, "visits with adherence concern", V.CORAL if m.get("adherence_concerns") else V.LIME)])
    panels = [V.section("High-risk medications", V.table(["Medication", "Class", "Why it matters"],
                        [[f"<b>{e(h['name'])}</b>", e(h.get("drug_class")), e(h.get("reason"))] for h in hr]) if hr else "<span class='pa-dim'>None</span>",
                        sub=e(m.get("flag") or ""))]
    return Result(f"Medications · {pid}", body, panels, [f"Is {pid} ready for discharge?", f"Who follows {pid} after discharge?"],
                  ["medications", "visits.med_*", "llm_summary.medication_summary"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_wounds(run, pid):
    p = run.patients[pid]; w = (p.get("clinical") or {}).get("wounds") or []
    if not w:
        return Result(f"Wounds · {pid}", "<p class='pa-lead'>No wound records for this patient.</p>", srcs=["wounds"], ctx={"pid": pid, "branch": run.branch_of(pid)})
    rows = [[f"<b>{e((x.get('location') or '').title())}</b>", e(x.get("description") or x.get("category")), e(x.get("first_size")), e(x.get("latest_size")),
             e(f"{x['area_change_pct']:+.0f}%" if x.get("area_change_pct") is not None else ""),
             V.chip(x.get("healing_status") or "?", V.LIME if (x.get("healing_status") or "").lower() in ("healed", "improving") else V.AMBER)] for x in w]
    body = f"<p class='pa-lead'>{e((p.get('clinical') or {}).get('wound_summary') or f'{len(w)} wound(s) on record.')}</p>"
    return Result(f"Wounds · {pid}", body, [V.section("Wound inventory", V.table(["Location", "Wound", "First", "Latest", "Area", "Status"], rows))],
                  [f"Is {pid} ready for discharge?"], ["wounds"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_function(run, pid):
    p = run.patients[pid]; oa = (p.get("clinical") or {}).get("oasis") or []
    if not oa:
        return Result(f"Function · {pid}", "<p class='pa-lead'>No OASIS items for this patient.</p>", srcs=["oasis"], ctx={"pid": pid, "branch": run.branch_of(pid)})
    better, worse = oasis_changes(p)
    rows = []
    for o in oa:
        a, b = _code(o.get("first")), _code(o.get("latest"))
        ch = ""
        if o.get("changed") and a is not None and b is not None and o["item"].startswith(LOWER_IS_BETTER):
            ch = f"<b style='color:{V.LIME}'>▼ Better</b>" if b < a else f"<b style='color:{V.CORAL}'>▲ Worse</b>"
        rows.append([f"<b>{e(o['item'])}</b> {e(o.get('description'))}", e(o.get("first")), e(o.get("latest")), ch])
    lead = ("Function improved: " + ", ".join(better) + "." if better else "No OASIS follow-up yet; start-of-care values only." if not any(o.get("changed") for o in oa) else "No improvement on key items.")
    if worse: lead += " Declined: " + ", ".join(worse) + "."
    return Result(f"Function since start of care · {pid}", f"<p class='pa-lead'>{e(lead)}</p>",
                  [V.section("OASIS: start of care → latest", V.table(["Item", "Start of care", "Latest", "Change"], rows), sub="Lower code = better")],
                  [f"Is {pid} ready for discharge?", f"Which goals aren't met for {pid}?"], ["oasis"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_network(run, pid):
    p = run.patients[pid]; cn = p.get("care_network") or {}
    if not cn:
        return Result(f"Care network · {pid}", "<p class='pa-lead'>No care network record for this patient.</p>", srcs=["care_network"], ctx={"pid": pid, "branch": run.branch_of(pid)})
    items = [f"PCP: <b>{e(cn.get('pcp_name') or 'None on record')}</b>" + (f" · last visit {cn['pcp_last_visit_days']} days ago" if cn.get("pcp_last_visit_days") is not None else ""),
             f"CW PCO: {'enrolled' if cn.get('cwpco_enrolled') else 'not enrolled'} · nearest clinic {e(cn.get('nearest_cwpco_clinic'))} ({e(cn.get('nearest_cwpco_miles'))} mi)",
             f"CW Rx: {'enrolled' if cn.get('cwrx_enrolled') else 'not enrolled'} · {e(cn.get('active_med_count'))} active medications"]
    refs = [[f"<b>{n}</b>", V.chip(cn[k]["indicator"], V.LIME if cn[k]["indicator"] in ("Refer", "Already enrolled") else V.AMBER if cn[k]["indicator"] in ("Consider", "Check eligibility") else V.SOFT), e(cn[k]["reason"])]
            for k, n in (("refer_cwpco", "CW PCO"), ("refer_cwrx", "CW Rx")) if cn.get(k)]
    return Result(f"Who follows {pid} after discharge?", V.bullets(items), [V.section("Referral indicators", V.table(["Program", "Indicator", "Why"], refs))],
                  [f"Is {pid} ready for discharge?"], ["care_network"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_events(run, pid):
    p = run.patients[pid]; ae = (p.get("care_review") or {}).get("adverse_events") or []; ev = p.get("events") or []
    rows = [[f"Day {e(x.get('episode_day'))}", e(x.get("type")), e(x.get("detail")), "⚑" if x.get("flag") else ""] for x in ev]
    rows += [[f"Day {e(x.get('episode_day'))}", e(x.get("type")), e(f"{x.get('severity')}: {x.get('detail')}"), "⚑"] for x in ae]
    lead = (p.get("care_review") or {}).get("adverse_events_summary") or (f"{len(ae)} adverse event(s) and {len(ev)} other event(s) on record." if (ae or ev) else "No adverse events or inpatient events on record this episode.")
    return Result(f"Falls, ED visits and events · {pid}", f"<p class='pa-lead'>{e(lead)}</p>",
                  [V.section("Episode events", V.table(["When", "Type", "Detail", ""], rows))] if rows else [],
                  [f"Are {pid}'s vitals stable?", f"Is {pid} ready for discharge?"], ["adverse_events", "ip_events", "vs_alerts", "client_logs"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_gaps(run, pid):
    p = run.patients[pid]
    gaps = [x.strip() for x in re.split(r"[|\n]", (p.get("narratives") or {}).get("data_gaps") or "") if x.strip()]
    body = (f"<p class='pa-lead'>CQI flagged <b>{len(gaps)}</b> data gap(s) to check before deciding.</p>" + V.bullets([e(g) for g in gaps])) if gaps \
        else "<p class='pa-lead'>No data gaps were flagged for this patient (or no CQI summary was generated this run).</p>"
    return Result(f"Data gaps · {pid}", body, [], [f"Is {pid} ready for discharge?"], ["llm_summary.data_gaps_summary"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_risk(run, pid):
    p = run.patients[pid]; rk = p["risk"]
    dr = rk.get("drivers") or []
    mx = max([d["contribution"] for d in dr] or [1])
    rows = "".join(f"<div class='pa-drv'><span>{e(d['label'])}</span><i style='width:{d['contribution'] / mx * 100:.0f}%'></i></div>" for d in dr)
    delta = (rk["score"] - rk["score_prior"]) if rk.get("score_prior") is not None else None
    body = (f"<p class='pa-lead'>{e((p.get('summary') or {}).get('headline') or rk.get('rationale') or '')}</p>"
            + V.stat_tiles([(f"{rk['score']:.2f}", f"{V.TIER.get(rk['outcome_risk_level'], ('',))[0]} 60-day risk", V.TIER.get(rk['outcome_risk_level'], ('', V.SKY))[1]),
                            (f"{delta:+.2f}" if delta is not None else "–", "vs. prior run", V.CORAL if (delta or 0) > 0 else V.LIME),
                            (f"Top {max(1, 100 - int(rk.get('percentile') or 0))}%", "of scored episodes", V.SKY)]))
    panels = [V.section("What drives the score", rows or "<span class='pa-dim'>No drivers</span>", sub="Model contributions")]
    if rk.get("rationale"):
        panels.append(V.section("CQI rationale", f"<p>{e(rk['rationale'])}</p>"))
    return Result(f"Why is {pid} on the list?", body, panels, [f"What actions are due for {pid}?", f"Are {pid}'s vitals stable?"],
                  ["risk_scores", "risk_drivers", "llm_summary.risk_rationale"], ctx={"pid": pid, "branch": run.branch_of(pid)})


def p_actions(run, pid):
    p = run.patients[pid]; acts = (p.get("summary") or {}).get("actions") or []
    rows = [[e(a["action"]), e(a.get("owner")), f"<b style='color:{V.WHEN.get(a.get('when'), V.SKY)}'>{e(a.get('when'))}</b>"] for a in acts]
    body = (f"<p class='pa-lead'>{len(acts)} recommended action(s); {sum(1 for a in acts if a.get('when') == 'Within 24 hrs')} within 24 hours.</p>"
            if acts else "<p class='pa-lead'>No CQI actions for this patient this run.</p>")
    return Result(f"Actions · {pid}", body, [V.section("Recommended actions (CQI draft)", V.table(["Action", "Owner", "When"], rows))] if rows else [],
                  [f"Is {pid} ready for discharge?", f"Why is {pid} on the list?"], ["llm_summary.actions_json"], ctx={"pid": pid, "branch": run.branch_of(pid)})


PATIENT = {"discharge": p_discharge, "vitals": p_vitals, "goals": p_goals, "visits": p_visits, "meds": p_meds, "wounds": p_wounds,
           "function": p_function, "network": p_network, "events": p_events, "gaps": p_gaps, "risk": p_risk, "actions": p_actions,
           "today": p_actions, "overview": p_overview}


# ======================================================================= branch / all answers
def _pids(run, branch):
    return [k for k in run.order if branch is None or run.branch_of(k) == branch]


def b_overview(run, branch):
    decs = run.decisions()
    brs = [branch] if branch else run.branch_ids()
    panels, lead = [], []
    for b in brs:
        s = run.branches[b]; c = s["census"]; cs = s["cohort_status"]; t = s["delivery_total"]
        tiles = V.stat_tiles([(c["active_episodes"], "active episodes", V.SKY), (c["cohort"], "on the CQI list", V.CORAL if c["cohort"] else V.LIME),
                              (cs["trajectory"]["deteriorating"], "deteriorating", V.CORAL if cs["trajectory"]["deteriorating"] else V.LIME),
                              (cs["near_discharge"], "nearing discharge", V.LIME), (f"{t['delivery_pct']}%" if t["delivery_pct"] is not None else "–", "visits delivered", V.LIME if (t["delivery_pct"] or 0) >= 90 else V.AMBER),
                              (t["unscheduled"], "visits not booked", V.CORAL if t["unscheduled"] else V.LIME)])
        tone = {"alert": V.CORAL, "ok": V.LIME, "neutral": V.SKY}
        finds = "".join(f"<li><i class='pa-dot' style='background:{tone[f['tone']]}'></i>{e(f['text'])}</li>" for f in s["findings"])
        panels.append(V.section(s["branch_label"], tiles + f"<ul class='pa-ul dots'>{finds}</ul>", sub=f"{s['state']} · {c['cohort']} on this week's list"))
        lead.append(f"<b>{e(s['branch_label'])}</b>: {len(s['actions'])} branch actions, {sum(1 for a in s['actions'] if a['when'] == 'Within 24 hrs')} due within 24 hours")
    cards = "".join(V.patient_card(run.patients[k], decs.get(k) and _dec_label(decs[k]["decision"])) for k in _pids(run, branch))
    panels.append(V.section("Patients on this week's list", f"<div class='pa-grid'>{cards}</div>", sub="Highest risk first"))
    first = _pids(run, branch)[:1]
    return Result(("How is " + run.branches[branch]["branch_label"] + " doing?") if branch else "This week's Patient Conference",
                  "<p class='pa-lead'>" + "<br>".join(lead) + "</p>", panels,
                  ["Who needs action today?", "Who is ready for discharge?"] + [f"Tell me about {k}" for k in first] + ([f"Show visit delivery for {run.branches[branch]['branch_label']}"] if branch else []),
                  ["episodes", "risk_scores", "orders/visits/sched", "vs_alerts", "patient payloads"], ctx={"branch": branch})


def b_today(run, branch):
    rows = []
    for b in ([branch] if branch else run.branch_ids()):
        for a in run.branches[b]["actions"]:
            if a["when"] in ("Within 24 hrs", "This conference", "Before conference"):
                rows.append((a["when"], [e(a["action"]), e(a["owner"]), f"<b style='color:{V.WHEN.get(a['when'], V.SKY)}'>{e(a['when'])}</b>",
                                         e(", ".join(a["patients"])), e(run.branches[b]["branch_label"])]))
    order = {"Within 24 hrs": 0, "This conference": 1, "Before conference": 2}
    rows.sort(key=lambda r: order.get(r[0], 9))
    pts = [k for k in _pids(run, branch) if any(a.get("when") == "Within 24 hrs" for a in (run.patients[k].get("summary") or {}).get("actions") or [])]
    n24 = sum(1 for w, _ in rows if w == "Within 24 hrs")
    body = (f"<p class='pa-lead'><b>{n24}</b> branch action(s) are due within 24 hours"
            + (f", centred on patient{'s' if len(pts) > 1 else ''} <b>{', '.join(pts)}</b>" if pts else "") + ".</p>")
    panels = [V.section("Due now", V.table(["Action", "Owner", "When", "Patients", "Branch"], [r for _, r in rows]))]
    if pts:
        panels.append(V.section("Patients with 24-hour actions", "<div class='pa-grid'>" + "".join(V.patient_card(run.patients[k]) for k in pts) + "</div>"))
    return Result("Who needs action today?", body, panels, [f"Tell me about {k}" for k in pts[:2]] + ["Who is ready for discharge?", "Why are visits being missed?"],
                  ["branch actions (rules)", "vs_alerts", "orders/sched", "llm_summary.actions_json"], ctx={"branch": branch})


def b_discharge(run, branch):
    pids = sorted(_pids(run, branch), key=lambda k: run.patients[k]["patient"].get("days_to_episode_end") or 999)
    rows = []
    for k in pids:
        p = run.patients[k]; s = readiness(p); pt = p["patient"]
        col = V.LIME if s["recommendation"].lower().startswith(("discharge", "leaning discharge")) else V.CORAL if s["recommendation"] in ("Recertify", "Not ready") else V.AMBER
        rows.append([f"<b>{k}</b>", f"{pt.get('days_to_episode_end')} days" + (" " + V.chip("in window", V.LIME, True) if s["in_window"] else ""),
                     f"<b style='color:{col}'>{e(s['recommendation'])}</b>", f"{len(s['discharge'])} / {len(s['recert'])}", e("; ".join(s["caveats"]) or "–")])
    near = [k for k in pids if (run.patients[k]["patient"].get("discharge_window") or {}).get("flag")]
    body = (f"<p class='pa-lead'><b>{len(near)}</b> patient(s) within 14 days of episode end"
            + (f": <b>{', '.join(near)}</b> — system says <b>{e(run.patients[near[0]]['discharge_recommendation']['recommendation'])}</b>" if near else "") + ".</p>")
    panels = [V.section("Discharge readiness across the list", V.table(["Patient", "Episode ends in", "Signals say", "For / against", "Check first"], rows), sub="Soonest first")]
    if near:
        panels.append(V.section(f"Patient {near[0]}", V.readiness_card(run.patients[near[0]], readiness(run.patients[near[0]]))))
    return Result("Who is ready for discharge?", body, panels, [f"Is {k} ready for discharge?" for k in near[:1]] + ["Who needs action today?"],
                  ["episodes.episode_end", "discharge_recommendation", "llm_goals", "vitals", "oasis", "wounds"], ctx={"branch": branch, "pid": near[0] if near else None})


def b_visits(run, branch):
    panels, lead = [], []
    for b in ([branch] if branch else run.branch_ids()):
        s = run.branches[b]
        rows = [[f"<b>{e(d['discipline'])}</b>", d["ordered_to_date"], d["delivered_to_date"], V.bar(d["delivery_pct"]),
                 f"<b style='color:{V.CORAL}'>{d['missed']}</b>" if d["missed"] else "0", d["remaining"], d["scheduled"],
                 f"<b style='color:{V.CORAL}'>{d['unscheduled']}</b>" if d["unscheduled"] else "0"] for d in s["delivery"]]
        miss = "".join(f"<li>{e(m['date'])} · {e(m['discipline'])} · {e(m['reason'])} <span class='pa-dim'>({e(m['category'].split(':')[0])}, pt {e(m['patient_id'])})</span></li>" for m in s["missed"])
        panels.append(V.section(f"{s['branch_label']} · visit delivery", V.table(["Disc.", "Ordered", "Delivered", "Delivery", "Missed", "Remaining", "Booked", "Not booked"], rows)
                                + (f"<div class='pa-k'>Missed visits</div><ul class='pa-ul'>{miss}</ul>" if miss else ""), sub="Ordered to date = completed weeks"))
        t = s["delivery_total"]; cats = s["missed_by_category"]
        lead.append(f"<b>{e(s['branch_label'])}</b>: {t['delivery_pct']}% delivered, {t['missed']} missed"
                    + (f" ({', '.join(f'{n} {k.split(':')[0].lower()}' for k, n in cats.items())})" if cats else "") + f", {t['unscheduled']} not booked")
    return Result("Visits and missed care", "<p class='pa-lead'>" + "<br>".join(lead) + "</p>", panels, ["Who needs action today?", "How are the branches doing?"],
                  ["orders", "visits", "sched"], ctx={"branch": branch})


def b_vitals(run, branch):
    rows = []
    for k in _pids(run, branch):
        tr = run.patients[k]["vitals"].get("trajectory") or {}
        rows.append([f"<b>{k}</b>", V.traj_chip(tr.get("class")), e(tr.get("saw") or ""), e(tr.get("action_window") or "")])
    det = [k for k in _pids(run, branch) if (run.patients[k]["vitals"].get("trajectory") or {}).get("class") == "deteriorating"]
    return Result("Vitals across the list", f"<p class='pa-lead'><b>{len(det)}</b> patient(s) with deteriorating vitals{': ' + ', '.join(det) if det else ''}.</p>",
                  [V.section("Vitals trajectory", V.table(["Patient", "Trajectory", "What CQI saw", "Action window"], rows))],
                  [f"Are {k}'s vitals stable?" for k in det[:1]] + ["Who needs action today?"], ["vitals", "llm_summary.vitals_*"], ctx={"branch": branch})


def b_gaps(run, branch):
    items = []
    for k in _pids(run, branch):
        for g in [x.strip() for x in re.split(r"[|\n]", (run.patients[k].get("narratives") or {}).get("data_gaps") or "") if x.strip()]:
            items.append(f"<b>{k}</b> · {e(g)}")
    return Result("Data gaps to check", f"<p class='pa-lead'><b>{len(items)}</b> data gap(s) flagged across the list.</p>" + V.bullets(items),
                  [], ["Who needs action today?"], ["llm_summary.data_gaps_summary"], ctx={"branch": branch})


AGENT_LABEL = {"vitals_outside_limits": "Vitals outside limits", "alert_not_escalated": "Alert not escalated", "visits_not_booked": "Visits not booked",
               "monitoring_gap": "Monitoring gap", "missed_visits": "Missed visits", "high_risk_meds": "High-risk meds",
               "wound_not_reassessed": "Wound not reassessed", "goals_behind": "Goals behind plan", "decision_window": "Discharge / recert window",
               "rising_risk": "Rising risk", "no_pcp": "No PCP"}


def _opp_rows(opps):
    return [[f"<b style='color:{V.WHEN.get(o['when'], V.SKY)}'>{e(o['when'])}</b>", f"<b>{e(o['patient_id'])}</b>",
             e(AGENT_LABEL.get(o["type"], o["type"])) + (f" · {e(o['discipline'])}" if o.get("discipline") else ""),
             e(o["finding"]), e(o["action"]) + f"<div class='pa-dim small'>{e(o['owner'])}</div>",
             V.chip("New", V.LIME) if o.get("status") == "new" else V.chip(f"Week {o.get('weeks_open', 2)}", V.AMBER)] for o in opps]


def b_agent(run, branch):
    st = run.agent_run()
    opps = [o for o in run.opportunities() if not branch or o["branch"] == branch]
    if not st:
        return Result("The CQI agent hasn't run for this date", "<p class='pa-lead'>Run <code>agent/cqi_agent.py --as-of "
                      f"{e(run.as_of)}</code> (or the weekly Databricks job) to scan for care opportunities.</p>",
                      followups=["Who needs action today?"], mode="data")
    n24 = [o for o in opps if o["when"] == "Within 24 hrs"]
    pts = sorted({o["patient_id"] for o in n24})
    body = (f"<p class='pa-lead'>The CQI agent scanned <b>{st['episodes_scanned']}</b> active episodes and found "
            f"<b>{len(opps)}</b> care opportunities — <b style='color:{V.CORAL}'>{len(n24)} within 24 hours</b>"
            + (f" for {', '.join(pts)}" if pts else "") + f". {st['new']} are new this week; {st['resolved']} closed since the last run.</p>")
    counts = Counter(o["type"] for o in opps)
    tiles = V.stat_tiles([(st["episodes_scanned"], "episodes scanned", V.SKY), (len(opps), "care opportunities", V.AMBER),
                          (len(n24), "within 24 hours", V.CORAL), (st["new"], "new this week", V.LIME), (st["resolved"], "resolved", V.LIME)])
    mix = "".join(V.chip(f"{AGENT_LABEL.get(k, k)} · {n}", V.SOFT) for k, n in counts.most_common())
    panels = [V.section("What the agent scanned", tiles + f"<div class='pa-chips'>{mix}</div>",
                        sub=f"Run {e(st['as_of'])} · explained by {e(st.get('explained_by', 'rules'))} · {e(st.get('seconds'))}s"),
              V.section("Care opportunities, highest priority first", V.table(["When", "Patient", "Gap", "Finding", "Action", ""], _opp_rows(opps[:14])),
                        sub=f"{len(opps)} total")]
    if st.get("resolved_items"):
        panels.append(V.section("Closed since the last run", V.bullets([f"<b>{e(r['patient_id'])}</b> · {e(r['finding'])}" for r in st["resolved_items"]])))
    return Result("What did the CQI agent find this week?", body, panels,
                  [f"Tell me about {p}" for p in pts[:2]] + ["Who is ready for discharge?", "How are the branches doing?"],
                  ["agent/opportunities.json"] + sorted({s for o in opps for s in o.get("sources", [])}), ctx={"branch": branch})


def p_agent(run, pid):
    opps = [o for o in run.opportunities() if o["patient_id"] == pid]
    body = (f"<p class='pa-lead'>The CQI agent flagged <b>{len(opps)}</b> care opportunit{'y' if len(opps) == 1 else 'ies'} for {pid}.</p>"
            if opps else f"<p class='pa-lead'>The CQI agent found no open care gaps for {pid} this run.</p>")
    return Result(f"CQI agent · {pid}", body, [V.section("Care opportunities", V.table(["When", "Patient", "Gap", "Finding", "Action", ""], _opp_rows(opps)))] if opps else [],
                  [f"Is {pid} ready for discharge?", f"Are {pid}'s vitals stable?"], ["agent/opportunities.json"], ctx={"pid": pid, "branch": run.branch_of(pid)})


PATIENT["agent"] = p_agent

BRANCH = {"agent": b_agent, "overview": b_overview, "today": b_today, "actions": b_today, "discharge": b_discharge, "visits": b_visits,
          "vitals": b_vitals, "gaps": b_gaps, "risk": b_overview}


# ======================================================================= entry point
def answer(run, q, ctx=None, llm=None, blocked=None):
    q = (q or "").strip()
    ctx = dict(ctx or {})
    if not q:
        return b_overview(run, None)
    if blocked and blocked(q):
        return refused()
    pid = find_pid(run, q)
    branch = run.find_branch(q)
    if pid is None and ctx.get("pid") and PRONOUN.search(q) and not branch:
        pid = ctx["pid"]
    intent = detect(q)
    if llm is not None and llm_needed(q):
        return llm(run, q, pid, branch)
    if pid:
        if intent in PATIENT:
            return PATIENT[intent](run, pid)
        if intent is None and re.fullmatch(r"\s*(patient\s*)?\d{7}\s*", q, re.I):
            return p_overview(run, pid)
    elif intent in BRANCH:
        return BRANCH[intent](run, branch)
    elif branch and intent is None:
        return b_overview(run, branch)
    # free text -> Gemini (scope-filtered), grounded in the patient or the list
    if llm is not None:
        return llm(run, q, pid or None, branch)
    return Result("Try asking it another way", "<p class='pa-lead'>I can answer questions about this week's patients and branches: "
                  "discharge readiness, vitals, goals, visits, medications, wounds, function, falls, referrals and data gaps.</p>",
                  followups=["Who needs action today?", "Who is ready for discharge?", "How are the branches doing?"], mode="refused")


def llm_needed(q):
    """Open, judgement-style questions go to the model even when a keyword matched (it sees the same data)."""
    return bool(re.search(r"\b(compare|explain|difference|versus|vs\.?|should (we|the team|i)|what if|summari[sz]e|worr(y|ied)|concern|"
                          r"most important|biggest|in plain|someone|anyone|help at home|caregiver|family|daughter|son|weekend|tell the|"
                          r"how would|what would|prepare|talking points)\b", q.lower()))


def refused(msg=None):
    return Result("Outside Project Alpha’s scope",
                  "<p class='pa-lead'>" + e(msg or "Project Alpha only answers questions about the patients and CQI data in this conference app.") + "</p>",
                  followups=["Who needs action today?", "Who is ready for discharge?", "How are the branches doing?"], mode="refused")
