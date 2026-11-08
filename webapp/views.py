"""HTML building blocks for Project Alpha (dark CenterWell theme from clinician_copilot)."""
from __future__ import annotations

from html import escape

LIME, SKY, TEAL, CORAL, AMBER = "#97D700", "#00B2E3", "#0095C8", "#FF8A80", "#FFB74D"
SOFT = "#b9c9f2"

TIER = {"very_high": ("Very high", CORAL), "high": ("High", CORAL), "moderate": ("Moderate", AMBER), "low": ("Low", LIME)}
TRAJ = {"deteriorating": ("Deteriorating", CORAL), "insufficient_data": ("No recent vitals", SOFT),
        "stable": ("Stable", SKY), "improving": ("Improving", LIME)}
GOAL = {"Achieved": LIME, "In progress": SKY, "Delayed": AMBER, "Missed": CORAL, "Not started": SOFT}
WHEN = {"Within 24 hrs": CORAL, "This conference": AMBER, "Before conference": AMBER, "This week": SKY,
        "Next SN visit": SKY, "Next visit": SKY, "Before discharge": LIME, "Routine": SOFT}


def e(x):
    return escape(str(x)) if x is not None else ""


def chip(text, color=SKY, solid=False):
    style = (f"background:{color};color:#00205B;border-color:{color}" if solid
             else f"color:{color};border-color:{color}")
    return f'<span class="pa-chip" style="{style}">{e(text)}</span>'


def tier_chip(level, score=None, delta=None):
    lab, col = TIER.get(level, (str(level).title(), SOFT))
    t = f"{lab} risk" + (f" {score:.2f}" if score is not None else "")
    if delta:
        t += f"  {'▲' if delta > 0 else '▼'}{abs(delta):.2f}"
    return chip(t, col)


def traj_chip(cls):
    lab, col = TRAJ.get(cls or "insufficient_data", (cls, SOFT))
    return chip(f"Vitals {lab.lower()}" if cls != "insufficient_data" else lab, col)


def sources(srcs):
    if not srcs:
        return ""
    return '<div class="pa-src">Sources ' + " ".join(f"<code>{e(s)}</code>" for s in dict.fromkeys(srcs)) + "</div>"


def answer_card(title, body_html, srcs=None, mode="data", note=None):
    badge = ('<span class="pa-badge ai">✦ Gemini Flash · grounded in CQI data</span>' if mode == "ai"
             else '<span class="pa-badge">● From the CQI data</span>' if mode == "data"
             else '<span class="pa-badge warn">Outside Project Alpha’s scope</span>')
    foot = f'<div class="pa-note">{e(note)}</div>' if note else ""
    return (f'<div class="pa-answer {"ai" if mode == "ai" else ""}">{badge}<div class="pa-title">{title}</div>'
            f'<div class="pa-body">{body_html}</div>{sources(srcs)}{foot}</div>')


def section(title, inner, sub=None):
    s = f'<span class="pa-sub">{e(sub)}</span>' if sub else ""
    return f'<div class="pa-card"><div class="pa-h">{e(title)}{s}</div>{inner}</div>'


def bullets(items):
    return "<ul class='pa-ul'>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def table(head, rows, cls=""):
    th = "".join(f"<th>{e(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="pa-t {cls}"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def stat_tiles(items):
    """items: [(value, label, color)]"""
    return '<div class="pa-stats">' + "".join(
        f'<div class="pa-stat"><b style="color:{c}">{e(v)}</b><span>{e(l)}</span></div>' for v, l, c in items) + "</div>"


def bar(pct, color=None):
    if pct is None:
        return '<span class="pa-dim">n/a</span>'
    col = color or (LIME if pct >= 90 else SKY if pct >= 75 else CORAL)
    return (f'<span class="pa-bar"><i style="width:{max(min(pct, 100), 3)}%;background:{col}"></i></span>'
            f'<b style="color:{col}">{pct}%</b>')


# ---------------------------------------------------------------- patient pieces
def patient_card(p, decision=None):
    pt, rk = p["patient"], p["risk"]
    delta = round(rk["score"] - rk["score_prior"], 2) if rk.get("score_prior") is not None else None
    cls = (p["vitals"].get("trajectory") or {}).get("class") or "insufficient_data"
    chips = [tier_chip(rk["outcome_risk_level"], rk["score"], delta), traj_chip(cls),
             chip(f"Day {pt['episode_day']} of {pt['episode_length']}", SOFT)]
    if (pt.get("discharge_window") or {}).get("flag"):
        chips.append(chip(f"Nearing discharge · {pt['discharge_window']['days_remaining']} days", LIME, solid=True))
    goal = (p.get("goals") or {}).get("patient_stated_goal")
    found = (p.get("at_a_glance") or {}).get("found") or (p.get("summary") or {}).get("headline") or ""
    dec = f'<div class="pa-dec">✓ {e(decision)}</div>' if decision else ""
    q = f'<div class="pa-quote">“{e(goal)}”</div>' if goal else '<div class="pa-quote dim">No patient-stated goal recorded</div>'
    return (f'<div class="pa-pt"><div class="pa-pt-top"><span class="pa-pid">{e(pt["patient_id"])}</span>'
            f'<span class="pa-dim">{e(pt["branch_name"].split(" - ")[0].title())}</span></div>'
            f'<div class="pa-chips">{"".join(chips)}</div>{q}<div class="pa-found">{e(found)}</div>{dec}</div>')


def readiness_card(p, snap):
    """snap: dict from engine.readiness(p)."""
    rec, conf = snap["recommendation"], snap["confidence"]
    col = LIME if rec.startswith("Discharge") else CORAL if rec == "Recertify" else AMBER
    head = (f'<div class="pa-ready-rec" style="color:{col}">{e(rec)}</div>'
            f'<div class="pa-dim">{e(conf)} · {e(snap["basis"])}</div>')
    dc = "".join(f"<li>{e(x)}</li>" for x in snap["discharge"]) or "<li class='pa-dim'>None</li>"
    rc = "".join(f"<li>{e(x)}</li>" for x in snap["recert"]) or "<li class='pa-dim'>None</li>"
    chk = (f'<div class="pa-check">Check first · {e("; ".join(snap["caveats"]))}</div>' if snap["caveats"] else "")
    steps = (f'<div class="pa-steps"><b>Next steps if the team agrees</b>{bullets([e(s) for s in snap["steps"]])}</div>'
             if snap["steps"] else "")
    return (f'<div class="pa-ready" style="border-color:{col}">{head}'
            f'<div class="pa-two"><div><div class="pa-k" style="color:{LIME}">Supports discharge ({len(snap["discharge"])})</div><ul class="pa-ul">{dc}</ul></div>'
            f'<div><div class="pa-k" style="color:{CORAL}">Supports recertification ({len(snap["recert"])})</div><ul class="pa-ul">{rc}</ul></div></div>'
            f'{chk}{steps}</div>')


def vitals_chart(p):
    """Small-multiple sparklines (weight, SpO2, pulse, SBP) with patient limits; inline SVG."""
    rd = p["vitals"].get("readings") or []
    lim = p["vitals"].get("limits") or {}
    if not rd:
        return '<div class="pa-dim">No vitals readings for this episode.</div>'
    spec = [("weight", "Weight", "lb", None, None), ("spo2", "SpO₂", "%", lim.get("spo2_low"), None),
            ("pulse", "Pulse", "bpm", lim.get("pulse_low"), lim.get("pulse_high")),
            ("sbp", "Systolic BP", "mmHg", None, lim.get("sbp_high"))]
    out = []
    for key, lab, unit, lo, hi in spec:
        pts = [(r["episode_day"], r[key]) for r in rd if r.get(key) is not None]
        if len(pts) < 2:
            continue
        W, H, pad = 250, 84, 10
        xs = [x for x, _ in pts]; ys = [y for _, y in pts] + [v for v in (lo, hi) if v is not None]
        x0, x1 = min(xs), max(xs) or 1
        y0, y1 = min(ys), max(ys)
        if y1 - y0 < 1e-6: y0, y1 = y0 - 1, y1 + 1
        sx = lambda x: pad + (x - x0) / max(x1 - x0, 1) * (W - 2 * pad)
        sy = lambda y: H - pad - (y - y0) / (y1 - y0) * (H - 2 * pad)
        first, last = pts[0][1], pts[-1][1]
        breach = (lo is not None and last < lo) or (hi is not None and last > hi)
        trend_bad = (key == "weight" and last - first >= (lim.get("weight_gain_lb") or 5)) or breach
        col = CORAL if trend_bad else SKY
        lines = "".join(f'<line x1="{pad}" x2="{W - pad}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="lim"/>' for v in (lo, hi) if v is not None)
        path = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in pts)
        dots = "".join(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="2.6" fill="{col}"/>' for x, y in pts)
        fmt = lambda v: f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"
        out.append(f'<div class="pa-spark"><div class="pa-spark-h"><span>{lab}</span><b style="color:{col}">{fmt(last)} {unit}</b></div>'
                   f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none">{lines}<polyline points="{path}" fill="none" stroke="{col}" stroke-width="2.4"/>{dots}</svg>'
                   f'<div class="pa-dim small">Day {pts[0][0]}: {fmt(first)} → Day {pts[-1][0]}: {fmt(last)}</div></div>')
    return '<div class="pa-sparks">' + "".join(out) + "</div>"
