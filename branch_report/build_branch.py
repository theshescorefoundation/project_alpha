"""Branch report PDF for Project Alpha: how the branch's patients are doing and what the branch must do this week.

Usage (from the project_alpha folder):
    .venv\\Scripts\\python branch_report\\build_branch.py --as-of 2026-08-15
Reads the patient payloads written by cqi_booklet (run build_payloads.py first) plus the CSVs.
Writes branch_report/output/<as_of>/<Branch>_report.pdf and <branch>_summary.json.
Spec: docs/branch_report_spec.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Flowable, Paragraph, Table, TableStyle

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aggregate  # noqa: E402
import brand  # noqa: E402

# ------------------------------------------------------------------ fonts + palette
_ff = brand.font_files()
if _ff:
    pdfmetrics.registerFont(TTFont("Arial", _ff[0])); pdfmetrics.registerFont(TTFont("Arial-Bold", _ff[1]))
    pdfmetrics.registerFont(TTFont("Arial-Italic", _ff[2]))
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic", boldItalic="Arial-Bold")
    F, FB, FI = "Arial", "Arial-Bold", "Arial-Italic"
else:
    F, FB, FI = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"

C = {k: HexColor(v) for k, v in brand.HEX.items()}
H_ = lambda h: HexColor(h)
W, H = letter
M = 46
CW = W - 2 * M
BRAND = brand.branding()
TIER_LABEL = aggregate.TIER_LABEL


def ps(size=9, color=None, font=None, lead=None, align=0):
    return ParagraphStyle("x", fontName=font or F, fontSize=size, textColor=color or C["dark_gray"],
                          leading=lead or size * 1.28, alignment=align)


CELL, CELLB, HEAD = ps(8.4), ps(8.4, font=FB), ps(8.2, white, FB)
T = lambda x: escape(str(x)) if x is not None else ""


def P(t, s=CELL, raw=False):
    return Paragraph(t if raw else T(t), s)


def colored(text, hexcol, bold=True):
    return f'<font color="{hexcol}">{"<b>" if bold else ""}{T(text)}{"</b>" if bold else ""}</font>'


def img(c, f, x, y, w=None, h=None):
    ir = ImageReader(brand.asset(f)); iw, ih = ir.getSize()
    if w and not h: h = w * ih / iw
    if h and not w: w = h * iw / ih
    c.drawImage(ir, x, y, w, h, mask="auto"); return w, h


def chip(c, x, y, text, color, size=7.2, right=False):
    tw = pdfmetrics.stringWidth(text.upper(), FB, size) + 12
    if right: x -= tw
    c.setFillColor(color); c.roundRect(x, y, tw, size + 7, (size + 7) / 2, fill=1, stroke=0)
    c.setFillColor(white); c.setFont(FB, size); c.drawString(x + 6, y + 4, text.upper())
    return tw


def table(c, data, x, ytop, widths, extra=None, zebra=True):
    t = Table(data, colWidths=widths)
    st = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
          ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
          ("LINEBELOW", (0, 0), (-1, -1), 0.4, C["light_gray"]), ("BACKGROUND", (0, 0), (-1, 0), C["dark_blue"])]
    if zebra:
        st += [("BACKGROUND", (0, r), (-1, r), C["panel"]) for r in range(2, len(data), 2)]
    t.setStyle(TableStyle(st + (extra or [])))
    _, h = t.wrap(sum(widths), 4000); t.drawOn(c, x, ytop - h); return h


def para(c, text, x, ytop, w, style, raw=False):
    p = Paragraph(text if raw else T(text), style); _, h = p.wrap(w, 4000); p.drawOn(c, x, ytop - h); return h


class PctBar(Flowable):
    """Small horizontal bar with the % written next to it (for table cells)."""
    def __init__(self, pct, w=70, h=7):
        super().__init__(); self.pct, self.w, self.h = pct, w, h
    def wrap(self, *_):
        return self.w + 30, self.h + 2
    def draw(self):
        c = self.canv
        c.setFillColor(C["light_gray"]); c.roundRect(0, 1, self.w, self.h, 3, fill=1, stroke=0)
        if self.pct is None:
            c.setFillColor(C["mid_gray"]); c.setFont(F, 7.5); c.drawString(self.w + 5, 1.5, "n/a"); return
        col = C["analysis_green"] if self.pct >= 90 else C["light_blue"] if self.pct >= 75 else C["plum"]
        if self.pct > 0:
            c.setFillColor(col); c.roundRect(0, 1, max(self.w * min(self.pct, 100) / 100, 6), self.h, 3, fill=1, stroke=0)
        c.setFillColor(col); c.setFont(FB, 8); c.drawString(self.w + 5, 1.5, f"{self.pct}%")


# ------------------------------------------------------------------ page furniture
def pattern_band(c, y0, height):
    c.setFillColor(C["dark_blue"]); c.rect(M, y0, CW, height, fill=1, stroke=0)
    c.saveState(); p = c.beginPath(); p.rect(M, y0, CW, height); c.clipPath(p, stroke=0, fill=0)
    split = M + CW * 0.535
    img(c, "pattern_left.png", M, y0, w=split - M + 1, h=height)
    img(c, "pattern_right.png", split, y0, w=M + CW - split, h=height)
    c.restoreState()
    c.setFillColor(C["light_blue"]); c.rect(M, y0 - 8, CW, 8, fill=1, stroke=0)
    c.setFillColor(C["green"]); c.rect(M + CW * 0.82, y0 - 8, CW * 0.18, 8, fill=1, stroke=0)
    return y0 - 8


def header(c, s, title, subtitle, icon, big=False):
    """Big header on page 1 (logo + prepared-for + tall band), compact band on the other pages."""
    if big:
        img(c, "logo_cwhh.png", M, H - 70, h=38)
        c.setFillColor(C["mid_gray"]); c.setFont(FB, 7.5); c.drawRightString(W - M, H - 44, "PREPARED FOR")
        c.setFillColor(C["dark_blue"]); c.setFont(FB, 9.5)
        c.drawRightString(W - M, H - 57, (BRAND.get("prepared_for") or "CWHH Clinical Quality Team").upper())
        top, hgt = 84, 96
    else:
        top, hgt = 30, 64
    y0 = H - top - hgt
    yb = pattern_band(c, y0, hgt)
    tx = M + 22
    chip_lab = "BRANCH REPORT  ·  CQI"
    tw = pdfmetrics.stringWidth(chip_lab, FB, 7) + 12
    c.setFillColor(white); c.roundRect(tx, H - top - 22, tw, 12, 6, fill=1, stroke=0)
    c.setFillColor(C["navy"]); c.setFont(FB, 7); c.drawString(tx + 6, H - top - 18.5, chip_lab)
    c.setFillColor(white); c.setFont(FB, 21 if big else 16.5); c.drawString(tx, H - top - (50 if big else 42), title)
    c.setFont(F, 9.5 if big else 8.6); c.drawString(tx, H - top - (68 if big else 56), subtitle)
    if big:
        c.setFont(FI, 8.6); c.drawString(tx, H - top - 83, f"{s['cohort_label']}  ·  {s['model_version']}")
    d = 54 if big else 40
    cx, cy = W - M - 22 - d / 2, y0 + hgt / 2
    c.setFillColor(C["light_blue"]); c.circle(cx, cy, d / 2 + 3, fill=1, stroke=0)
    c.setFillColor(white); c.circle(cx, cy, d / 2, fill=1, stroke=0)
    sz = d * 0.6; img(c, icon, cx - sz / 2, cy - sz / 2, w=sz, h=sz)
    return yb - 14


def footer(c, s, page, total):
    img(c, "logo_cwhh.png", M, 22, h=32)
    x = M + 150
    c.setFont(F, 7.4); c.setFillColor(C["mid_gray"])
    c.drawString(x, 42, BRAND.get("footer_note", "Internal use only  ·  Contains PHI  ·  AI-assisted drafts for clinical review"))
    stage = "  ·  ".join(t for t in [BRAND.get("stage_label", "Proof of concept"),
                                         BRAND.get("sample_label", "Sample data") if s.get("sample") else ""] if t)
    if stage:
        c.setFont(FB, 7.4); c.setFillColor(C["dark_blue"]); c.drawString(x, 31, stage.upper())
    c.setFont(FB, 8.5); c.setFillColor(C["light_blue"]); c.drawRightString(W - M, 42, f"Page {page} of {total}")
    c.setFont(F, 7.4); c.setFillColor(C["mid_gray"])
    c.drawRightString(W - M, 31, f"{s['branch_label']}  ·  Run {mdy(s['as_of_date'])}")
    from datetime import date
    c.setFont(F, 7.2)
    c.drawRightString(W - M, 20, f"Report generated {date.today():%m/%d/%Y} from "
                                 f"{BRAND.get('generated_by', 'Project Alpha – Clinical Quality Intelligence')}")


def subhead(c, text, y, note=None):
    c.setFillColor(C["dark_blue"]); c.setFont(FB, 12.5); c.drawString(M, y - 12.5, text)
    if note:
        c.setFillColor(C["mid_gray"]); c.setFont(F, 8); c.drawRightString(W - M, y - 11.5, note)
    c.setStrokeColor(C["light_blue"]); c.setLineWidth(1); c.line(M, y - 17, W - M, y - 17)
    return y - 26


def mdy(iso):
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}" if iso and len(iso) >= 10 else (iso or "")


def ids(lst):
    return ", ".join(lst) if lst else "-"


# ------------------------------------------------------------------ page 1: at a glance
def kpi_tiles(c, s, y):
    cs, c_, t = s["cohort_status"], s["census"], s["delivery_total"]
    tiles = [
        (c_["active_episodes"], "Active episodes", "in the branch", "neutral"),
        (c_["cohort"], "On the CQI list", "high / rising risk", "alert" if c_["cohort"] else "ok"),
        (c_["rising"], "Rising risk", "since last run", "alert" if c_["rising"] else "ok"),
        (cs["trajectory"]["deteriorating"], "Deteriorating", "vitals trajectory", "alert" if cs["trajectory"]["deteriorating"] else "ok"),
        (cs["near_discharge"], "Nearing discharge", "within 14 days", "neutral"),
        (t["unscheduled"], "Visits not booked", "ordered, unscheduled", "alert" if t["unscheduled"] else "ok"),
    ]
    gap = 8; tw = (CW - gap * (len(tiles) - 1)) / len(tiles); th = 66
    for i, (val, lab, sub, tone) in enumerate(tiles):
        x = M + i * (tw + gap)
        col = {"alert": C["plum"], "ok": C["analysis_green"], "neutral": C["dark_blue"]}[tone]
        c.setFillColor(C["panel"]); c.roundRect(x, y - th, tw, th, 6, fill=1, stroke=0)
        c.setFillColor(col); c.rect(x, y - 4, tw, 4, fill=1, stroke=0)
        c.setFillColor(col); c.setFont(FB, 24); c.drawCentredString(x + tw / 2, y - 34, str(val))
        c.setFillColor(C["dark_gray"]); c.setFont(FB, 8); c.drawCentredString(x + tw / 2, y - 48, lab)
        c.setFillColor(C["mid_gray"]); c.setFont(F, 7); c.drawCentredString(x + tw / 2, y - 58, sub)
    return y - th - 16


def findings_panel(c, s, y):
    items = s["findings"]
    st = ps(9, lead=12)
    paras = [Paragraph(T(f["text"]), st) for f in items]
    hs = [p.wrap(CW - 40, 400)[1] for p in paras]
    hgt = 30 + sum(hs) + 5 * len(items)
    c.setFillColor(white); c.setStrokeColor(C["light_gray"]); c.roundRect(M, y - hgt, CW, hgt, 6, fill=1, stroke=1)
    c.setFillColor(C["green"]); c.rect(M, y - hgt, 4, hgt, fill=1, stroke=0)
    c.setFillColor(C["dark_blue"]); c.setFont(FB, 11); c.drawString(M + 16, y - 17, "How the branch is doing")
    c.setFillColor(C["mid_gray"]); c.setFont(F, 7.6)
    c.drawRightString(W - M - 12, y - 16, "Rule-based from operational data and CQI outputs")
    yy = y - 30
    for f, p, h in zip(items, paras, hs):
        c.setFillColor(H_(brand.TONE_HEX[f["tone"]])); c.circle(M + 21, yy - 5.5, 3.2, fill=1, stroke=0)
        p.drawOn(c, M + 32, yy - h); yy -= h + 5
    return y - hgt - 16


ACT_W = [20, 270, 82, 72, 76]


def _action_rows(acts, start):
    rows = []
    for i, a in enumerate(acts, start):
        rows.append([P(str(i), CELLB), Paragraph(f"<b>{T(a['action'])}</b><br/><font size=7.4 color='{brand.HEX['mid_gray']}'>{T(a['why'])}</font>", CELL),
                     P(a["owner"]), P(colored(a["when"], brand.WHEN_HEX.get(a["when"], brand.HEX["navy"])), raw=True),
                     P(ids(a["patients"]))])
    return rows


ACT_HEAD = lambda: [P("#", HEAD), P("Branch action", HEAD), P("Owner", HEAD), P("When", HEAD), P("Patients", HEAD)]


def actions_table(c, s, y, bottom=84):
    """Draw as many actions as fit above `bottom`; return (new_y, number_drawn)."""
    acts = s["actions"]
    if not acts:
        return y - para(c, "No branch-level actions triggered this week.", M, y, CW, ps(9, C["mid_gray"], FI)), 0
    n = len(acts)
    while n > 1:
        t = Table([ACT_HEAD()] + _action_rows(acts[:n], 1), colWidths=ACT_W)
        t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]))
        if y - t.wrap(sum(ACT_W), 4000)[1] - (14 if n < len(acts) else 0) >= bottom:
            break
        n -= 1
    h = table(c, [ACT_HEAD()] + _action_rows(acts[:n], 1), M, y, ACT_W)
    if n < len(acts):
        c.setFont(FI, 7.8); c.setFillColor(C["mid_gray"])
        c.drawString(M, y - h - 11, f"+ {len(acts) - n} more action(s), lower urgency: continued on page 3.")
    return y - h - 14, n


def page_glance(c, s, total):
    org = s["branch_name"].split(" - ")[-1].title().replace("Centerwell", "CenterWell")
    sub = f"{org}  ·  {s['state']}  ·  CQI run {mdy(s['as_of_date'])}  ·  {s['conference_label']}"
    y = header(c, s, f"{s['branch_label']}: how our patients are doing", sub, "icon_monitor.png", big=True)
    y = kpi_tiles(c, s, y)
    y = findings_panel(c, s, y)
    y = subhead(c, "Branch actions for this week", y, note="Ordered by urgency  ·  details on pages 2-3")
    _, s["_actions_drawn"] = actions_table(c, s, y)
    footer(c, s, 1, total)


# ------------------------------------------------------------------ page 2: delivery + safety
def hbars(c, x, y, w, rows, max_v=None, label_w=150):
    """rows: [(label, value, hex)] -> simple horizontal bars. Returns height used."""
    if not rows:
        c.setFont(FI, 8.4); c.setFillColor(C["mid_gray"]); c.drawString(x, y - 10, "None recorded."); return 16
    mv = max_v or max(v for _, v, _ in rows) or 1
    bw = w - label_w - 24; yy = y
    for lab, v, col in rows:
        c.setFont(F, 8.2); c.setFillColor(C["dark_gray"]); c.drawString(x, yy - 9, lab)
        c.setFillColor(C["light_gray"]); c.roundRect(x + label_w, yy - 11, bw, 9, 3, fill=1, stroke=0)
        if v:
            c.setFillColor(H_(col)); c.roundRect(x + label_w, yy - 11, max(bw * v / mv, 6), 9, 3, fill=1, stroke=0)
        c.setFont(FB, 8.4); c.setFillColor(H_(col)); c.drawString(x + label_w + bw + 5, yy - 9.5, str(v))
        yy -= 16
    return y - yy


def stat_rows(c, x, y, w, rows):
    """rows: [(value, text, tone)] big number + text."""
    yy = y
    for val, text, tone in rows:
        col = H_(brand.TONE_HEX[tone])
        c.setFillColor(col); c.setFont(FB, 15); c.drawRightString(x + 26, yy - 14, str(val))
        h = para(c, text, x + 34, yy - 2, w - 34, ps(8.4, lead=10.4))
        yy -= max(h, 14) + 7
    return y - yy


def page_delivery(c, s, total):
    y = header(c, s, "Care delivery and safety", f"{s['branch_label']}  ·  all active episodes unless marked CQI list  ·  run {mdy(s['as_of_date'])}",
               "icon_checklist.png")
    y = subhead(c, "Visit delivery by discipline", y, note="Ordered to date = completed episode weeks; capped per week")
    data = [[P(h, HEAD) for h in ("Discipline", "Ordered to date", "Delivered", "Delivery", "Missed", "Remaining", "Scheduled", "Not booked")]]
    for d in s["delivery"] + [dict(s["delivery_total"], discipline="All")]:
        bold = d["discipline"] == "All"
        st = CELLB if bold else CELL
        data.append([P(d["discipline"], CELLB), P(d["ordered_to_date"], st), P(d["delivered_to_date"], st), PctBar(d["delivery_pct"]),
                     P(colored(d["missed"], brand.HEX["plum"]) if d["missed"] else "0", raw=True),
                     P(d["remaining"], st), P(d["scheduled"], st),
                     P(colored(d["unscheduled"], brand.HEX["plum"]) if d["unscheduled"] else "0", raw=True)])
    extra = [("LINEABOVE", (0, len(data) - 1), (-1, len(data) - 1), 1, C["dark_blue"])]
    y -= table(c, data, M, y, [62, 64, 54, 112, 46, 58, 58, 66], extra=extra) + 18

    colw = (CW - 20) / 2; xl, xr = M, M + colw + 20
    # left: missed by reason
    c.setFillColor(C["dark_blue"]); c.setFont(FB, 11); c.drawString(xl, y - 11, "Missed visits by reason")
    cat_col = {"Agency: staffing": brand.HEX["plum"], "Clinical: hospitalized": brand.HEX["navy"],
               "Patient: not home / reschedule / refused": brand.HEX["light_blue"]}
    rows = [(k.split(":")[0] + " - " + k.split(": ")[1], v, cat_col.get(k, brand.HEX["mid_gray"]))
            for k, v in sorted(s["missed_by_category"].items(), key=lambda x: -x[1])]
    hl = hbars(c, xl, y - 20, colw, rows, label_w=150)
    yy = y - 24 - hl
    for m in s["missed"][:5]:
        yy -= para(c, f"{m['date']}  {m['discipline']}  ·  {m['reason']}  ·  pt {m['patient_id']}", xl, yy, colw, ps(7.6, C["mid_gray"])) + 1
    left_bottom = yy
    # right: escalation + safety
    c.setFillColor(C["dark_blue"]); c.setFont(FB, 11); c.drawString(xr, y - 11, "Escalation and safety")
    e, m_, n = s["escalation"], s["medications"], s["care_network"]
    rows = [(e["not_notified"], f"vitals alert(s) with no documented physician notification (of {e['alerts']} alerts)",
             "alert" if e["not_notified"] else "ok"),
            (len(m_["high_risk_no_recent_check"]), f"patient(s) on high-risk meds with no understanding check in {aggregate.MED_CHECK_DAYS} days (CQI list)",
             "alert" if m_["high_risk_no_recent_check"] else "ok"),
            (len(m_["adherence_concern"]), "patient(s) with a documented medication adherence concern (CQI list)",
             "alert" if m_["adherence_concern"] else "ok"),
            (len(s["adverse_events"]), "adverse event(s) this episode, e.g. falls or ED visits (CQI list)",
             "alert" if s["adverse_events"] else "ok")]
    hr = stat_rows(c, xr, y - 22, colw, rows)
    y = min(left_bottom, y - 22 - hr) - 14

    # goals stacked bar
    y = subhead(c, "Care-plan goals (CQI list)", y, note="From the CQI goal inventory")
    g = s["goals"]; gt = sum(g.values())
    if gt:
        x = M; bh = 16
        for k in aggregate.GOAL_ORDER:
            if g[k]:
                wseg = CW * g[k] / gt
                c.setFillColor(H_(brand.GOAL_HEX[k])); c.rect(x, y - bh, wseg, bh, fill=1, stroke=0)
                if wseg > 22:
                    c.setFillColor(white); c.setFont(FB, 8.5); c.drawCentredString(x + wseg / 2, y - 11.5, str(g[k]))
                x += wseg
        lx = M; ly = y - bh - 13
        for k in aggregate.GOAL_ORDER:
            c.setFillColor(H_(brand.GOAL_HEX[k])); c.rect(lx, ly, 8, 8, fill=1, stroke=0)
            c.setFillColor(C["dark_gray"]); c.setFont(F, 8); lab = f"{k} ({g[k]})"; c.drawString(lx + 11, ly + 1, lab)
            lx += pdfmetrics.stringWidth(lab, F, 8) + 26
        y = ly - 18
    else:
        y -= para(c, "No care-plan goals on record for patients on the CQI list.", M, y, CW, ps(8.6, C["mid_gray"], FI)) + 10

    # care network
    y = subhead(c, "Care network and referral opportunities (CQI list)", y, note="CW PCO / CW Rx indicators from the booklet rules")
    tiles = [(len(n["refer_cwpco"]), "Refer to CW PCO"), (len(n["consider_cwpco"]), "Consider CW PCO"),
             (len(n["refer_cwrx"]), "Refer to CW Rx"), (len(n["no_pcp"]), "No PCP on record"),
             (len(n["cwpco_enrolled"]) + len(n["cwrx_enrolled"]), "Already enrolled")]
    gap = 8; tw = (CW - gap * 4) / 5
    for i, (v, lab) in enumerate(tiles):
        x = M + i * (tw + gap)
        c.setFillColor(C["panel"]); c.roundRect(x, y - 40, tw, 40, 5, fill=1, stroke=0)
        c.setFillColor(C["analysis_green"] if v and i < 3 else C["plum"] if v and i == 3 else C["dark_blue"])
        c.setFont(FB, 16); c.drawString(x + 10, y - 22, str(v))
        c.setFillColor(C["dark_gray"]); c.setFont(F, 8); c.drawString(x + 10, y - 34, lab)
    footer(c, s, 2, total)


# ------------------------------------------------------------------ page 3: roster
def page_patients(c, s, total):
    y = header(c, s, "Patients on this week's list", f"{s['branch_label']}  ·  {s['cohort_label']}  ·  highest risk first",
               "icon_phone.png")
    if not s["patients"]:
        para(c, "No patients on the CQI list for this branch this week.", M, y, CW, ps(10, C["mid_gray"], FI))
        footer(c, s, 3, total); return
    data = [[P(h, HEAD) for h in ("Patient", "Risk", "Vitals", "Episode", "Goals met", "Visits left / booked", "Data gaps", "Top action")]]
    for r in s["patients"]:
        tier = TIER_LABEL.get(r["tier"], r["tier"])
        delta = (f"<br/><font size=7 color='{brand.HEX['plum'] if (r['delta'] or 0) > 0 else brand.HEX['analysis_green']}'>"
                 f"{'+' if (r['delta'] or 0) > 0 else ''}{r['delta']:.2f} vs prior</font>") if r["delta"] is not None else ""
        risk = f"{colored(tier, brand.TIER_HEX.get(r['tier'], brand.HEX['navy']))} {r['score']:.2f}{delta}"
        traj = colored(brand.TRAJ_LABEL.get(r["trajectory"], r["trajectory"]), brand.TRAJ_HEX.get(r["trajectory"], brand.HEX["mid_gray"]))
        epi = f"Day {r['episode_day']} of {r['episode_length']}<br/><font size=7>{r['days_to_end']} days left</font>"
        if r["near_discharge"]:
            epi += f"<br/>{colored('Nearing discharge', brand.HEX['plum'])}"
        if r["recommendation"]:
            epi += f"<br/><font size=7>System: <b>{T(r['recommendation'])}</b></font>"
        act = (f"{T(r['top_action'])}<br/>{colored(r['top_action_when'], brand.WHEN_HEX.get(r['top_action_when'], brand.HEX['navy']))}"
               if r["top_action"] else f"<font color='{brand.HEX['mid_gray']}'><i>No CQI action text</i></font>")
        data.append([P(r["patient_id"], CELLB), P(risk, raw=True), P(traj, raw=True), P(epi, raw=True),
                     P(f"{r['goals_met']} of {r['goals_total']}" if r["goals_total"] else "-"),
                     P(f"{r['visits_remaining']} / {r['visits_scheduled']}"),
                     P(colored(r["data_gaps"], brand.HEX["plum"]) if r["data_gaps"] else "0", raw=True), P(act, raw=True)])
    y -= table(c, data, M, y, [52, 64, 74, 84, 40, 50, 38, 118], extra=[("VALIGN", (0, 1), (-1, -1), "TOP")]) + 18

    # branch actions that did not fit on page 1
    rest = s["actions"][s.get("_actions_drawn", len(s["actions"])):]
    if rest:
        y = subhead(c, "Branch actions (continued)", y, note="Lower urgency, from page 1")
        y -= table(c, [ACT_HEAD()] + _action_rows(rest, s["_actions_drawn"] + 1), M, y, ACT_W) + 18

    # in their own words
    voices = [r for r in s["patients"] if r["patient_goal"] or r["found"]]
    if voices:
        y = subhead(c, "In their own words", y, note="Patient-stated goal and what CQI found")
        colw = (CW - 12) / 2
        for i in range(0, len(voices), 2):
            row_h = 0
            for j, r in enumerate(voices[i:i + 2]):
                x = M + j * (colw + 12)
                q = f"“{T(r['patient_goal'])}”" if r["patient_goal"] else "<i>No patient-stated goal recorded.</i>"
                body = (f"<font color='{brand.HEX['dark_blue']}' size=10><b>{q}</b></font><br/>"
                        f"<font size=7.4 color='{brand.HEX['mid_gray']}'>PATIENT {r['patient_id']}  ·  "
                        f"{TIER_LABEL.get(r['tier'], r['tier']).upper()} RISK</font><br/>"
                        f"<b>What CQI found:</b> {T(r['found'] or 'No CQI summary for this run.')}")
                p = Paragraph(body, ps(8.4, lead=11)); _, h = p.wrap(colw - 22, 400)
                c.setFillColor(C["panel"]); c.roundRect(x, y - h - 16, colw, h + 16, 6, fill=1, stroke=0)
                c.setFillColor(C["light_blue"]); c.rect(x, y - h - 16, 4, h + 16, fill=1, stroke=0)
                p.drawOn(c, x + 14, y - 8 - h); row_h = max(row_h, h + 16)
            y -= row_h + 10
        y -= 6

    # discharge panels
    for d in s["discharge_panels"]:
        if y < 200:
            break
        y = subhead(c, f"Discharge or recertification: patient {d['patient_id']}", y,
                    note=f"{d.get('days_remaining')} days to episode end ({mdy(d.get('episode_end') or '')})")
        rec = d.get("recommendation") or "Clinical review"
        chip(c, M, y - 15, f"System: {rec}", C["analysis_green"] if rec.startswith("Discharge") else C["plum"], size=8)
        if d.get("confidence"):
            chip(c, M + pdfmetrics.stringWidth(f"SYSTEM: {rec}".upper(), FB, 8) + 22, y - 15, d["confidence"], C["navy"], size=7.2)
        y -= 24
        y -= para(c, d.get("rationale") or "", M, y, CW, ps(8.6, lead=11)) + 6
        colw = (CW - 16) / 2
        def lst(title, items, x, col):
            body = f"<font color='{col}'><b>{T(title)}</b></font><br/>" + "<br/>".join(f"• {T(i)}" for i in items or ["-"])
            return para(c, body, x, y, colw, ps(8.2, lead=10.6), raw=True)
        h1 = lst(f"Supports discharge ({len(d.get('supports_discharge') or [])})", d.get("supports_discharge"), M, brand.HEX["analysis_green"])
        h2 = lst(f"Supports recertification ({len(d.get('supports_recert') or [])})", d.get("supports_recert"), M + colw + 16, brand.HEX["plum"])
        y -= max(h1, h2) + 6
        steps = d.get("next_steps") or []
        if steps and y > 140:
            y -= para(c, f"<b>Next steps if the team agrees:</b> " + "; ".join(T(x.rstrip('.')) for x in steps) + ".", M, y, CW,
                      ps(8.2, lead=10.6), raw=True) + 8

    # methods line
    c.setFont(FI, 7.2); c.setFillColor(C["mid_gray"])
    meth = ("Branch counts use all active episodes from operational data; goals, vitals trajectory, data gaps and actions use the CQI list. "
            "Rules: docs/branch_report_spec.md. Rule-based summary for clinical review; clinicians make every decision.")
    para(c, meth, M, 92, CW, ps(7.2, C["mid_gray"], FI, lead=9))
    footer(c, s, 3, total)


# ------------------------------------------------------------------ build
def build_pdf(s, path):
    c = canvas.Canvas(path, pagesize=letter)
    c.setTitle(f"CQI Branch Report - {s['branch_label']} - {s['as_of_date']}")
    c.setAuthor(BRAND.get("produced_by") or "CW Data Science & Applied AI - HH Team")
    c.setSubject("Clinical Quality Intelligence (CQI) - Project Alpha")
    total = 3
    for fn in (page_glance, page_delivery, page_patients):
        fn(c, s, total); c.showPage()
    c.save()
    return path


def run(as_of, json_dir=None, out_dir=None):
    summaries = aggregate.build_branch_summaries(as_of, json_dir)
    out_dir = out_dir or os.path.join(aggregate.HERE, "output", as_of)
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for br, s in summaries.items():
        safe = re.sub(r"[^A-Za-z0-9]+", "_", s["branch_label"]).strip("_")
        with open(os.path.join(out_dir, f"{safe.lower()}_summary.json"), "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=1, default=str)
        made.append(build_pdf(s, os.path.join(out_dir, f"{safe}_report.pdf")))
    return made


def main():
    ap = argparse.ArgumentParser(description="Project Alpha branch report")
    ap.add_argument("--as-of", required=True, help="CQI run date, e.g. 2026-08-15")
    ap.add_argument("--json-dir", help="payload folder (default cqi_booklet/output/<as_of>/json)")
    ap.add_argument("--out", help="output folder (default branch_report/output/<as_of>)")
    a = ap.parse_args()
    for p in run(a.as_of, a.json_dir, a.out):
        print("wrote", p)


if __name__ == "__main__":
    main()
