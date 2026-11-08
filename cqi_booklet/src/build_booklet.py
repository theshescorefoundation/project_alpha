"""CQI Patient Conference Booklet renderer - CenterWell Home Health flyer-template styling.

    python src/build_booklet.py output/payloads/2026-08-15/9000123_7000456.json out.pdf

Accepts a payload (schema 1.0 / 1.1) or the internal render dict produced by cqi_payload.to_render().

Booklet structure (10 pages)
  1  Cover: what CQI is, how to use the booklet, contents
  2  Patient conference summary (one page the team discusses)
  CQI summaries: AI-generated text, unedited (same blocks as the CQI Power BI page)
  3  A  Patient context and risk drivers      \
  4  B  Care delivery                           |  evidence the team checks
  5  C  Vitals trajectory                       |  the summaries against
  6  D  Goals, interventions and medications   /
  11 Clinician feedback (fillable form)
  8  F  How CQI produced this summary
  9  G  Conference notes
"""
import json, sys, os, re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.platypus import Paragraph, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from xml.sax.saxutils import escape
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cqi_payload import FEEDBACK_FINDINGS, ERROR_TYPES

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "assets")
A = lambda f: os.path.join(ASSETS, f)

# ---- Brand tokens from the CenterWell flyer template (+ CQI dashboard header) ----
DARK_BLUE = HexColor("#003DA5"); LIGHT_BLUE = HexColor("#00A3D7"); GREEN = HexColor("#78BE20")
PANEL = HexColor("#DCF1F9"); DARK_GRAY = HexColor("#414042"); NAVY = HexColor("#003057")
PLUM = HexColor("#AF0061"); LIGHT_GRAY = HexColor("#D9D9D6"); MID_GRAY = HexColor("#6D6E71")
OK_GREEN = HexColor("#3E6B0E")
HEX = {"dark": "#003DA5", "light": "#00A3D7", "green": "#78BE20", "plum": "#AF0061",
       "gray": "#414042", "lgray": "#D9D9D6", "navy": "#003057"}
TL_COL = {"blue": DARK_BLUE, "plum": PLUM, "navy": NAVY, "gray": MID_GRAY, "green": HexColor("#4E7D14")}
TONE = {"alert": PLUM, "ok": OK_GREEN, "neutral": DARK_GRAY}


def _register_fonts():
    # Arial is the template font; Liberation Sans is metric-compatible on Linux / Databricks
    # [Project Alpha local-run fix] use the system's own Arial on Windows / macOS first.
    win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts") + os.sep
    for cand, names in ((win, ("arial.ttf", "arialbd.ttf", "ariali.ttf")),
                        ("/Library/Fonts/", ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf")),
                        ("/System/Library/Fonts/Supplemental/", ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf"))):
        if all(os.path.exists(cand + n) for n in names):
            pdfmetrics.registerFont(TTFont("Arial", cand + names[0]))
            pdfmetrics.registerFont(TTFont("Arial-Bold", cand + names[1]))
            pdfmetrics.registerFont(TTFont("Arial-Italic", cand + names[2]))
            registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic", boldItalic="Arial-Bold")
            plt.rcParams["font.family"] = "Arial"
            return
    for cand in ("/usr/share/fonts/liberation/", "/usr/share/fonts/truetype/liberation/"):
        if os.path.exists(cand + "LiberationSans-Regular.ttf"):
            reg, bold, ital = "LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf"
            plt.rcParams["font.family"] = "Liberation Sans"
            break
    else:
        for cand in ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/dejavu-sans-fonts/"):
            if os.path.exists(cand + "DejaVuSans.ttf"):
                reg, bold, ital = "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"
                break
        else:
            raise RuntimeError("No TTF fonts found - add fonts-liberation to the cluster init script")
    pdfmetrics.registerFont(TTFont("Arial", cand + reg))
    pdfmetrics.registerFont(TTFont("Arial-Bold", cand + bold))
    pdfmetrics.registerFont(TTFont("Arial-Italic", cand + ital))
    registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic", boldItalic="Arial-Bold")
_register_fonts()

W, H = letter
M = 46
CW = W - 2 * M
FOOT_TOP = 70
TOTAL = 10  # reset from d["pages"] in build()


def ps(size=10, color=DARK_GRAY, font="Arial", lead=None, align=0):
    return ParagraphStyle("x", fontName=font, fontSize=size, textColor=color, leading=lead or size * 1.25, alignment=align)

CELL = ps(8.8, lead=10.6); CELLB = ps(8.8, font="Arial-Bold", lead=10.6); HEADC = ps(8.8, white, "Arial-Bold", lead=10.6)
STATUS_COLOR = {"Act now": PLUM, "Monitor": LIGHT_BLUE, "On track": GREEN,
                "Achieved": HexColor("#4E7D14"), "In progress": DARK_BLUE, "Delayed": NAVY, "Missed": PLUM,
                "Not started": NAVY, "Unknown": MID_GRAY,
                "Within 24 hrs": PLUM, "This week": DARK_BLUE, "Next SN visit": NAVY, "Next visit": NAVY, "Routine": MID_GRAY}


# ---- Type scale (pt). One size per role; never pick a size from string length. ----
THERAPY = {"PT", "OT", "ST"}
def _branding():
    import yaml
    path = os.environ.get("CQI_BRANDING", os.path.join(HERE, "..", "config", "branding.yaml"))
    try:
        return yaml.safe_load(open(path)) or {}
    except FileNotFoundError:
        return {}
BRAND = _branding()
# Product vision (cover line + page 2 panel). Edit here only.
VISION_SHORT_UNUSED = ("Vision: an AI-assisted agent that scans operational data 24/7 on clinicians' behalf to find care gaps "
                "and opportunities to improve outcomes, proactively.")
_VISION_TODAY_DEFAULT = ("A weekly booklet and Power BI report built from operational data, reviewed by the clinical team in the "
                "patient conference. Clinician feedback shapes what comes next.")
_VISION_PROD_DEFAULT = ("An <b>AI-assisted agent</b> that scans operational data <b>24/7 on clinicians' behalf</b>, surfaces care gaps and "
               "opportunities to improve outcomes <b>as they emerge</b>, and brings the right patient to the right clinician "
               "while there is still time to act.")
TS = {"value_xl": 18, "value_l": 13, "value": 10, "label": 8, "body": 9.6, "chart": 8, "chart_title": 9.5}


def group_size(texts, font, width, max_size, min_size=8):
    """Largest size (0.5 pt steps) at which EVERY text in a group fits its width -> the whole group shares one size."""
    s = max_size
    while s > min_size and any(pdfmetrics.stringWidth(t, font, s) > width for t in texts):
        s -= 0.5
    return s


def T(x):
    return escape(str(x)) if x is not None else ""

def P(t, s=CELL, raw=False):
    return Paragraph(str(t) if raw else T(t), s)

def status_cell(text):
    col = STATUS_COLOR.get(text, PLUM)
    return Paragraph(f'<font color="#{col.hexval()[2:]}"><b>{T(text)}</b></font>', CELL)

def para(c, text, x, ytop, w, style, raw=False):
    p = Paragraph(text if raw else T(text), style); _, h = p.wrap(w, 4000); p.drawOn(c, x, ytop - h); return h

def fit_para(c, text, x, ytop, w, max_h, size=9.5, min_size=7.8, color=DARK_GRAY, font="Arial", note="see the CQI Power BI report"):
    """Draw text inside a fixed box: shrink the font first, then truncate at a word with a pointer."""
    if not text:
        return para(c, "No summary was generated for this run.", x, ytop, w, ps(9, MID_GRAY, "Arial-Italic"))
    s = size
    while s >= min_size - 1e-6:
        st = ps(s, color, font, lead=s * 1.27)
        p = Paragraph(T(text), st); _, h = p.wrap(w, 4000)
        if h <= max_h:
            p.drawOn(c, x, ytop - h); return h
        s -= 0.3
    st = ps(min_size, color, font, lead=min_size * 1.27)
    words = text.split(); lo, hi = 1, len(words)
    tail = f' ... <font color="#{MID_GRAY.hexval()[2:]}"><i>[truncated - {note}]</i></font>'
    while lo < hi:
        mid = (lo + hi + 1) // 2
        _, h = Paragraph(T(" ".join(words[:mid])) + tail, st).wrap(w, 4000)
        lo, hi = (mid, hi) if h <= max_h else (lo, mid - 1)
    p = Paragraph(T(" ".join(words[:lo])) + tail, st); _, h = p.wrap(w, 4000); p.drawOn(c, x, ytop - h)
    return h

def img(c, f, x, y, w=None, h=None):
    ir = ImageReader(A(f)); iw, ih = ir.getSize()
    if w and not h: h = w * ih / iw
    if h and not w: w = h * iw / ih
    c.drawImage(ir, x, y, w, h, mask="auto"); return w, h

def chip(c, x, y, text, color, size=7.5, right=False):
    tw = pdfmetrics.stringWidth(text.upper(), "Arial-Bold", size) + 12
    if right: x -= tw
    c.setFillColor(color); c.roundRect(x, y, tw, size + 7, (size + 7) / 2, fill=1, stroke=0)
    c.setFillColor(white); c.setFont("Arial-Bold", size); c.drawString(x + 6, y + 4.2, text.upper())
    return tw

def checkbox(c, x, y, label, size=9):
    c.setStrokeColor(DARK_BLUE); c.setLineWidth(0.9); c.setFillColor(white); c.rect(x, y, 9, 9, fill=1, stroke=1)
    c.setFillColor(DARK_GRAY); c.setFont("Arial", size); c.drawString(x + 13, y + 1, label)
    return 13 + pdfmetrics.stringWidth(label, "Arial", size) + 14

def line_field(c, x, y, label, x_end):
    c.setFillColor(DARK_GRAY); c.setFont("Arial", 9); c.drawString(x, y, label)
    lw = pdfmetrics.stringWidth(label, "Arial", 9) + (4 if label else 0)
    c.setStrokeColor(MID_GRAY); c.setLineWidth(0.6); c.line(x + lw, y - 2, x_end, y - 2)

def table(c, data, x, ytop, widths, header=True, zebra=True, extra=None, row_h=None):
    t = Table(data, colWidths=widths, rowHeights=row_h)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
          ("LINEBELOW", (0, 0), (-1, -1), 0.4, LIGHT_GRAY)]
    if header: st.append(("BACKGROUND", (0, 0), (-1, 0), DARK_BLUE))
    if zebra:
        for r in range(1 if header else 0, len(data)):
            if r % 2 == 0: st.append(("BACKGROUND", (0, r), (-1, r), PANEL))
    t.setStyle(TableStyle(st + (extra or [])))
    _, h = t.wrap(sum(widths), 4000); t.drawOn(c, x, ytop - h); return h

def fit(items, max_n):
    return (items[:max_n], len(items) - max_n) if len(items) > max_n else (items, 0)

def more_note(c, y, n, what):
    if n > 0:
        c.setFont("Arial-Italic", 8); c.setFillColor(MID_GRAY)
        c.drawString(M, y - 4, f"+ {n} more {what} not shown - see the CQI Power BI report.")
        return 12
    return 0

# ---------------- template furniture ----------------
def blue_bar_header(c, top, height, icon="icon_checklist.png", icon_d=None):
    y0 = H - top - height
    c.setFillColor(DARK_BLUE); c.rect(M, y0, CW, height, fill=1, stroke=0)
    c.saveState(); p = c.beginPath(); p.rect(M, y0, CW, height); c.clipPath(p, stroke=0, fill=0)
    split = M + CW * 0.535
    img(c, "pattern_left.png", M, y0, w=split - M + 1, h=height)
    img(c, "pattern_right.png", split, y0, w=M + CW - split, h=height)
    c.restoreState()
    c.setFillColor(LIGHT_BLUE); c.rect(M, y0 - 9, CW, 9, fill=1, stroke=0)
    c.setFillColor(GREEN); c.rect(M + CW * 0.82, y0 - 9, CW * 0.18, 9, fill=1, stroke=0)
    if icon:
        d = icon_d or min(height * 0.62, 78)
        cx, cy = M + 4 + d / 2 - 14, H - top - d / 2 + 14
        c.setFillColor(LIGHT_BLUE); c.circle(cx, cy, d / 2 + 3.5, fill=1, stroke=0)
        c.setFillColor(white); c.circle(cx, cy, d / 2, fill=1, stroke=0)
        s = d * 0.62; img(c, icon, cx - s / 2, cy - s / 2, w=s, h=s)
    return y0 - 9

def footer(c, page, d, logo=True, show_page=True):
    if logo:
        img(c, "logo_cwhh.png", M, 22, h=34)
    x = M + 158 if logo else M
    c.setFont("Arial", 7.6); c.setFillColor(MID_GRAY)
    c.drawString(x, 42, BRAND.get("footer_note", "Internal use only  \u00b7  Contains PHI  \u00b7  AI-assisted drafts for clinical review"))
    stage = "  \u00b7  ".join(t for t in [BRAND.get("stage_label", "Proof of concept"),
                                         BRAND.get("sample_label", "Sample data") if d.get("sample") else ""] if t)
    if stage:
        c.setFont("Arial-Bold", 7.6); c.setFillColor(DARK_BLUE); c.drawString(x, 31, stage.upper())
    if show_page:
        c.setFont("Arial-Bold", 8.5); c.setFillColor(LIGHT_BLUE); c.drawRightString(W - M, 42, f"Page {page} of {TOTAL}")
        c.setFont("Arial", 7.6); c.setFillColor(MID_GRAY)
        c.drawRightString(W - M, 31, f"Patient {d['header']['patient_id']}  \u00b7  Run {d['header']['run_date']}")

def ref_header(c, letter_, title, subtitle, d, icon, left_icon=None):
    y = blue_bar_header(c, 30, 62, icon=None)
    c.setFillColor(white); c.circle(M + 30, H - 61, 19, fill=1, stroke=0)
    img(c, left_icon or "icon_checklist.png", M + 30 - 12, H - 61 - 12, w=24, h=24)
    sec = section_of(d)
    lab = (sec["name"] if sec and sec["id"] != "start" else "Clinical Quality Intelligence").upper()
    col = SECTION_COLOR.get(sec["id"], NAVY) if sec else NAVY
    tw_ = pdfmetrics.stringWidth(lab, "Arial-Bold", 7.2) + 12
    c.setFillColor(white); c.roundRect(M + 60, H - 47, tw_, 12, 6, fill=1, stroke=0)
    c.setFillColor(col); c.setFont("Arial-Bold", 7.2); c.drawString(M + 66, H - 43.5, lab)
    c.setFillColor(white); c.setFont("Arial-Bold", 17); c.drawString(M + 60, H - 62, title)
    c.setFont("Arial", 9); c.drawString(M + 60, H - 77, subtitle)
    if icon:
        c.setFillColor(white); c.circle(W - M - 32, H - 61, 21, fill=1, stroke=0)
        img(c, icon, W - M - 46, H - 75, w=28, h=28)
    h = d["header"]
    c.setFont("Arial", 8); c.setFillColor(MID_GRAY)
    c.drawString(M, y - 14, f"Patient {h['patient_id']}  |  Episode {h['episode_id']}  |  {h['branch']}  |  Day {h['episode_day']} of {h['episode_length']}")
    return y - 30

def subhead(c, text, y, size=13):
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", size); c.drawString(M, y - size, text)
    return y - size - 7

def rule(c, y, color=LIGHT_BLUE, x0=M, x1=None, w=1):
    c.setStrokeColor(color); c.setLineWidth(w); c.line(x0, y, x1 or W - M, y)

def check_panel(c, y, items):
    hgt = 22 + 15 * len(items)
    c.setFillColor(PANEL); c.rect(M, y - hgt, CW, hgt, fill=1, stroke=0)
    c.setFillColor(GREEN); c.rect(M, y - hgt, 4, hgt, fill=1, stroke=0)
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 10); c.drawString(M + 14, y - 15, "Clinician check")
    c.setFont("Arial", 8.5); c.setFillColor(DARK_GRAY); c.drawRightString(W - M - 10, y - 15, "Initials: ________   Date: ________")
    yy = y - 32
    for it in items: checkbox(c, M + 14, yy, it, 9); yy -= 15
    return hgt

# ---------------- charts ----------------
# Charts are rendered at the exact size they occupy on the page (figsize = points / 72), so a
# matplotlib 8 pt label prints as 8 pt - no stretching, same scale as the rest of the booklet.
def _fig(w_pt, h_pt, rows=1, cols=1):
    return plt.subplots(rows, cols, figsize=(w_pt / 72, h_pt / 72), dpi=300)

def _style(ax, title=None):
    if title: ax.set_title(title, fontsize=TS["chart_title"], color=HEX["dark"], loc="left", fontweight="bold")
    ax.tick_params(labelsize=TS["chart"], colors=HEX["gray"])
    for s in ["top", "right"]: ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]: ax.spines[s].set_color(HEX["lgray"])

def chart_drivers(d, path, w_pt, h_pt):
    drv = d["risk"]["drivers"][:7][::-1]
    fig, ax = _fig(w_pt, h_pt)
    ax.barh([x[0][:48] for x in drv], [x[1] for x in drv], color=HEX["dark"], height=0.6, edgecolor="none")
    for i, (_, v) in enumerate(drv):
        ax.text(v + 0.0015, i, f"+{v:.3f}", va="center", fontsize=TS["chart"], color=HEX["gray"])
    ax.set_xlabel("Contribution to 60-day hospitalization risk score", fontsize=TS["chart"], color=HEX["gray"])
    ax.grid(axis="x", color=HEX["lgray"], lw=0.5); ax.set_axisbelow(True)
    _style(ax); ax.set_xlim(0, max([x[1] for x in drv] + [0.01]) * 1.18)
    plt.tight_layout(); fig.savefig(path, transparent=True); plt.close(fig)

def chart_vitals(d, path, w_pt, h_pt):
    v = d["vitals"]; lim = v["limits"]; x = v["day"]
    fig, axs = _fig(w_pt, h_pt, 2, 2)
    def fin(ax, t):
        _style(ax, t); ax.grid(axis="y", color=HEX["lgray"], lw=0.5)
        ax.set_xlabel("Episode day", fontsize=TS["chart"], color=HEX["gray"])
    ax = axs[0, 0]
    ax.plot(x, v["weight"], "-o", color=HEX["plum"], ms=3.5, lw=1.8)
    wv = [(xi, w) for xi, w in zip(x, v["weight"]) if w == w]
    if len(wv) >= 2:
        ax.axhline(wv[0][1], color=HEX["gray"], ls=":", lw=1)
        ax.annotate(f"{wv[-1][1]-wv[0][1]:+.1f} lb", wv[-1], textcoords="offset points", xytext=(-40, 4),
                    fontsize=TS["chart"], color=HEX["plum"], fontweight="bold")
    fin(ax, "Weight (lb) - baseline dotted")
    ax = axs[0, 1]
    ax.plot(x, v["sbp"], "-o", color=HEX["dark"], ms=3, lw=1.6, label="Systolic")
    ax.plot(x, v["dbp"], "-o", color=HEX["light"], ms=3, lw=1.6, label="Diastolic")
    ax.axhline(lim["sbp_high"], color=HEX["plum"], ls="--", lw=1)
    ax.legend(fontsize=TS["chart"], frameon=False, loc="center right")
    fin(ax, f"Blood pressure (mmHg) - limit SBP {lim['sbp_high']:g}")
    ax = axs[1, 0]
    ax.plot(x, v["pulse"], "-o", color=HEX["navy"], ms=3, lw=1.6)
    ax.axhline(lim["pulse_high"], color=HEX["plum"], ls="--", lw=1)
    fin(ax, f"Pulse (bpm) - limit {lim['pulse_high']:g}")
    ax = axs[1, 1]
    ax.plot(x, v["spo2"], "-o", color=HEX["green"], ms=3, lw=1.8)
    ax.axhline(lim["spo2_low"], color=HEX["plum"], ls="--", lw=1)
    sp = [s_ for s_ in v["spo2"] if s_ == s_]
    ax.set_ylim(min([lim["spo2_low"] - 2] + sp), max([98] + [s_ + 1 for s_ in sp]))
    fin(ax, f"SpO2 (%) - limit {lim['spo2_low']:g}")
    plt.tight_layout(h_pad=1.0, w_pad=2); fig.savefig(path, transparent=True); plt.close(fig)



def discharge_banner(c, d, y, network=True):
    """Page 2 banner for active episodes within 14 days of episode end. Returns height used."""
    x = d["discharge"]; network = network and bool(x.get("network")); h = 54 if network else 40
    c.setFillColor(HexColor("#FBEAF2")); c.rect(M, y - h, CW, h, fill=1, stroke=0)
    c.setFillColor(PLUM); c.rect(M, y - h, 4, h, fill=1, stroke=0)
    cw_ = chip(c, M + 12, y - 17, "Nearing discharge decision", PLUM, 7.5)
    c.setFillColor(PLUM); c.setFont("Arial-Bold", 10.5)
    c.drawString(M + 22 + cw_, y - 14.5, f"{x['days_remaining']} days to episode end ({x['episode_end']})")
    c.setFillColor(DARK_GRAY); c.setFont("Arial-Bold", 8.6)
    rec = (d.get("dc_rec") or {}).get("recommendation")
    c.drawRightString(W - M - 10, y - 14.5, (f"System: {rec}" + (f"  (page {d['pages']['DC']})" if d["pages"].get("DC") and d["pages"].get("DC") != d.get("_cur") else ""))
                      if rec else "Decide: discharge, recert or transfer")
    c.setFont("Arial", 8.6)
    c.drawString(M + 12, y - 32, f"Prognosis: {x['prognosis']}   |   Rehab potential: {x['rehab_potential']}   |   "
                                 f"Goals: {x['goals']}   |   Visits left: {x['visits']}")
    if network:
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 8.6)
        c.drawString(M + 12, y - 46, "Care network:  " + x["network"] + (f"   (page {d['pages']['G']})" if d["pages"].get("G") else ""))
    return h


PROFILE_LABELS = [("comorbidity_adjustment", "Comorbidity adjustment"), ("functional_impairment", "Functional impairment"),
                  ("prognosis", "Prognosis"), ("rehab_potential", "Rehab potential"),
                  ("functional_limitations", "Functional limitations"), ("activities_permitted", "Activities permitted"),
                  ("mental_status", "Mental status"), ("allergies", "Allergies"),
                  ("nutritional_requirements", "Nutritional requirements")]


def clinical_profile_table(c, d, y):
    """Page A: 485 / OASIS clinical profile (dashboard 'Patient Profile' page), 4 columns."""
    cp = d.get("clinical_profile") or {}
    y = subhead(c, "Clinical profile (485 / OASIS)", y, 12)
    if not any(cp.values()):
        para(c, "No 485 / OASIS profile found for this episode.", M, y, CW, ps(9, MID_GRAY, "Arial-Italic"))
        return y - 22
    val = lambda k: (cp.get(k) or "Not documented").capitalize() if k not in ("allergies",) else (cp.get(k) or "Not documented")
    items = [(lab, val(k)) for k, lab in PROFILE_LABELS]
    small = ps(8.4, lead=10)
    rows, ext = [], []
    for i in range(0, len(items), 2):
        a = items[i]; b = items[i + 1] if i + 1 < len(items) else ("", "")
        rows.append([P(a[0], ps(8.2, font="Arial-Bold", lead=10)), P(a[1], small), P(b[0], ps(8.2, font="Arial-Bold", lead=10)), P(b[1], small)])
    for r_, (lab, v) in enumerate(items):
        if lab == "Allergies" and v.upper() not in ("NKA", "NKDA", "NOT DOCUMENTED"):
            col = 1 if r_ % 2 == 0 else 3
            ext.append(("TEXTCOLOR", (col, r_ // 2), (col, r_ // 2), PLUM))
    used = table(c, rows, M, y, [100, 160, 100, 160], header=False, extra=ext)
    return y - used - 10

# ---------------- COVER ----------------
def page_cover(c, d):
    """Clean cover: full-colour logo on white, one title band, patient card, generous white space."""
    h, pg, r = d["header"], d["pages"], d["risk"]
    top = H - 36
    # top strip on white: full-colour logo (brand-compliant), prepared-for label
    img(c, "logo_cwhh.png", M, top - 44, h=44)
    c.setFillColor(MID_GRAY); c.setFont("Arial-Bold", 8); c.drawRightString(W - M, top - 14, "PREPARED FOR")
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 11); c.drawRightString(W - M, top - 29, (h.get("prepared_for") or "").upper())

    # title band
    band_top = top - 118; band_h = 218; y0 = band_top - band_h
    c.setFillColor(DARK_BLUE); c.rect(M, y0, CW, band_h, fill=1, stroke=0)
    c.saveState(); p = c.beginPath(); p.rect(M, y0, CW, band_h); c.clipPath(p, stroke=0, fill=0)
    ph = band_h * 0.5; split = M + CW * 0.535
    img(c, "pattern_left.png", M, y0, w=split - M + 1, h=ph)
    img(c, "pattern_right.png", split, y0, w=M + CW - split, h=ph)
    c.restoreState()
    c.setFillColor(LIGHT_BLUE); c.rect(M, y0 - 10, CW, 10, fill=1, stroke=0)
    c.setFillColor(GREEN); c.rect(M + CW * 0.82, y0 - 10, CW * 0.18, 10, fill=1, stroke=0)
    x = M + 30
    c.setFillColor(GREEN); c.setFont("Arial-Bold", 34); c.drawString(x, band_top - 58, "Clinical Quality Intelligence")
    c.setFillColor(white); c.setFont("Arial-Italic", 13.5); c.drawString(x, band_top - 82, "Which patient needs care today, before it's too late?")
    c.setFont("Arial-Bold", 21); c.drawString(x, band_top - 124, "Weekly Patient Conference Summary")
    c.setFont("Arial", 11); c.drawString(x, band_top - 143, f"{h['cohort']}  |  {h['conference_date']}")

    # one-line definition of CQI (full explanation on page 2)
    dy = y0 - 34
    c.setFillColor(GREEN); c.rect(M + 30, dy - 38, 4, 38, fill=1, stroke=0)
    para(c, "<b>CQI is a framework for identifying risk, care gaps and opportunities to improve patient outcomes.</b> "
            "It is <b>proactive</b>: it shows which patient needs care this week, why, and what to do "
            "<b>while there is still time to change the outcome</b>.", M + 44, dy - 1, CW - 74, ps(11, DARK_BLUE, lead=14.5), raw=True)

    # patient card
    y = dy - 70
    cw_ = CW - 60; cx = M + 30; ch = 96
    c.setFillColor(white); c.setStrokeColor(LIGHT_GRAY); c.setLineWidth(0.7)
    c.roundRect(cx, y - ch, cw_, ch, 8, fill=1, stroke=1)
    c.setFillColor(GREEN); c.roundRect(cx, y - ch, 6, ch, 3, fill=1, stroke=0)
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 11); c.drawString(cx + 20, y - 20, "Patient")
    if d.get("discharge"):
        chip(c, cx + cw_ - 14, y - 24, f"Nearing discharge decision: {d['discharge']['days_remaining']} days", PLUM, 7.5, right=True)
    fields = [("Patient ID", h["patient_id"]), ("Episode", h["episode_id"]), ("Branch", h["branch"]),
              ("60-day risk", f"{r['tier']} {r['score']:.2f}"), ("Status", f"{h['episode_status'].title()}, day {h['episode_day']} of {h['episode_length']}"),
              ("CQI run", h["run_date"])]
    colw = (cw_ - 40) / 2
    for i, (k, v) in enumerate(fields):
        fx = cx + 20 + (i // 3) * colw; fy = y - 42 - (i % 3) * 17
        c.setFont("Arial", 8.6); c.setFillColor(MID_GRAY); c.drawString(fx, fy, k)
        c.setFont("Arial-Bold", 10); c.setFillColor(PLUM if k == "60-day risk" else DARK_BLUE); c.drawString(fx + 70, fy, str(v)[:36])

    # bottom: produced by + note (lots of white space above)
    yb = FOOT_TOP + 70
    c.setStrokeColor(LIGHT_GRAY); c.setLineWidth(0.6); c.line(M, yb + 34, W - M, yb + 34)
    c.setFillColor(MID_GRAY); c.setFont("Arial", 8.6); c.drawString(M, yb + 18, "Produced by")
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 10.5); c.drawString(M + 64, yb + 18, h.get("produced_by") or "")
    c.setFillColor(MID_GRAY); c.setFont("Arial", 8.6); c.drawRightString(W - M - 90, yb + 18, "Model")
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 9.5); c.drawRightString(W - M, yb + 18, h["model"])
    para(c, (f'What CQI is: page {pg["about"]}.  How to use this booklet: page {pg["guide"]}.' if pg.get("about")
             else "Short summary. Full evidence and charts are in the full booklet."),
         M, yb + 2, CW, ps(8.8, MID_GRAY, lead=11.4))
    c.setFont("Arial", 7.6); c.setFillColor(MID_GRAY)                      # cover: privacy line only
    c.drawString(M, 31, BRAND.get("footer_note", "Internal use only  \u00b7  Contains PHI"))


def page_about(c, d):
    """Page 2: what CQI is and its components (moved off the cover)."""
    pg = d["pages"]
    y = ref_header(c, None, "What is Clinical Quality Intelligence?", "Purpose, how it works and what's in this booklet",
                   d, "icon_monitor.png", left_icon="icon_checklist.png")
    y -= 4
    q = ('<font color="#4E7D14"><b>CQI answers:</b></font> Which patient needs care <b><i>today</i></b>, before it\'s too '
         '<b><i>late</i></b>? What <b><i>immediate actions</i></b> could a clinical manager take to <b><i>prevent</i></b> '
         'hospitalizations?')
    qs = ps(12, DARK_BLUE, lead=16); qh = Paragraph(q, qs).wrap(CW - 30, 999)[1] + 26
    c.setFillColor(PANEL); c.rect(M, y - qh, CW, qh, fill=1, stroke=0)
    c.setFillColor(GREEN); c.rect(M, y - qh, 4, qh, fill=1, stroke=0)
    para(c, q, M + 16, y - 13, CW - 30, qs, raw=True)
    y -= qh + 12
    y -= para(c, "Most quality measures are <b>retrospective</b>: they report what happened after the episode ends. "
                 "CQI is <b>proactive</b>: every week it connects risk, the care ordered and delivered, and the patient's trajectory, "
                 "so the team can act <b>while the patient is still in care</b>.", M, y, CW, ps(10.2, lead=13.6), raw=True) + 10
    c.setFillColor(NAVY); c.rect(M, y - 26, CW, 26, fill=1, stroke=0)
    c.setFillColor(GREEN); c.setFont("Arial-Bold", 10); c.drawString(M + 12, y - 17, "Purpose:")
    c.setFillColor(white); c.setFont("Arial-Bold", 10)
    c.drawString(M + 64, y - 17, "Weekly Patient Conference Preparation  |  Built for the CWHH Clinical Quality team")
    y -= 36
    cards = [("What CQI does",
              "Compares the care each high-risk patient <b>needs</b> with the care they <b>received</b>, so clinicians can close gaps early."),
             ("What it reads",
              "Visits, goals, vitals, OASIS, wounds, medications, events and notes. Co-created with CWHH Clinical Quality."),
             ("How AI is used",
              "<b>Predictive ML</b> risk model on LighthouseAI; <b>rules</b> for care gaps; <b>LLMs</b> summarize, checked by LLM judges.")]
    bw3 = (CW - 24) / 3; st = ps(9.4, lead=12.2)
    bh3 = max(Paragraph(t, st).wrap(bw3 - 20, 999)[1] for _, t in cards) + 40
    for i, (t, txt) in enumerate(cards):
        bx = M + i * (bw3 + 12)
        c.setFillColor(PANEL); c.rect(bx, y - bh3, bw3, bh3, fill=1, stroke=0)
        c.setFillColor(DARK_BLUE); c.rect(bx, y - 4, bw3, 4, fill=1, stroke=0)
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 11); c.drawString(bx + 10, y - 21, t)
        para(c, txt, bx + 10, y - 29, bw3 - 20, st, raw=True)
    y -= bh3 + 20
    y = subhead(c, "What's in this booklet", y, 14); y -= 4
    comps = [("Care gaps", "Missed, delayed or unscheduled visits", ["B", "B2"]),
             ("Risk drivers", "Who is highest risk this week, and why", ["A2"]),
             ("Clinical picture", "Diagnoses, wounds and OASIS", ["DX", "W", "OA"]),
             ("Vitals trajectory", "Trend, urgency and action window", ["C"]),
             ("Patient goals", "Met, not met, therapy goals", ["D"]),
             ("Medication adherence", "High-risk meds, understanding, teaching", ["M"]),
             ("Care plan gaps", "Interventions, assessments, visit goals to add", ["E", "F"]),
             ("Discharge & network", "Recommendation, PCP, CW PCO, CW Rx", ["DC", "G"])]
    comps = [(t, s_, [k for k in ks if k in pg]) for t, s_, ks in comps]
    comps = [x for x in comps if x[2]]
    cols = 4; bw4 = (CW - (cols - 1) * 10) / cols; st4 = ps(8.6, lead=10.8)
    bh = max(Paragraph(T(t_), st4).wrap(bw4 - 20, 999)[1] for _, t_, _ in comps) + 40
    for i, (t, txt, ks) in enumerate(comps):
        bx = M + (i % cols) * (bw4 + 10); by = y - (i // cols) * (bh + 8)
        c.setFillColor(PANEL); c.rect(bx, by - bh, bw4, bh, fill=1, stroke=0)
        c.setFillColor(GREEN); c.rect(bx, by - 3, bw4, 3, fill=1, stroke=0)
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 9.8); c.drawString(bx + 10, by - 16, t)
        para(c, txt, bx + 10, by - 22, bw4 - 20, st4)
        ns_ = sorted(pg[k] for k in ks)
        lab = f"Page {ns_[0]}" if len(ns_) == 1 else f"Pages {ns_[0]}-{ns_[-1]}" if ns_[-1] - ns_[0] == len(ns_) - 1 else "Pages " + ", ".join(map(str, ns_))
        c.setFillColor(LIGHT_BLUE); c.setFont("Arial-Bold", 8); c.drawRightString(bx + bw4 - 8, by - bh + 6, lab + "  >")
    vision_panel(c, FOOT_TOP + 6)
    footer(c, pg["about"], d)


VISION_TODAY = lambda: BRAND.get("vision_today") or _VISION_TODAY_DEFAULT
VISION_PROD = lambda: BRAND.get("vision_production") or _VISION_PROD_DEFAULT


def vision_panel(c, y0, h=118):
    """'Where CQI is going': today (prototype) -> in production (AI-assisted agent). Anchored at y0 (bottom)."""
    c.setFillColor(DARK_BLUE); c.roundRect(M, y0, CW, h, 8, fill=1, stroke=0)
    c.setFillColor(GREEN); c.setFont("Arial-Bold", 12); c.drawString(M + 16, y0 + h - 22, "Where CQI is going")
    c.setFillColor(white); c.setFont("Arial-Italic", 8.6)
    c.drawRightString(W - M - 16, y0 + h - 22, BRAND.get("vision_note", "Clinicians stay in charge of every decision."))
    lw = CW * 0.36; gap = 34; rx = M + 16 + lw + gap; rw = W - M - 16 - rx
    st = ps(9.4, white, lead=12.4)
    c.setFillColor(HexColor("#BFE6F5")); c.setFont("Arial-Bold", 8); c.drawString(M + 16, y0 + h - 42, "TODAY  |  PROTOTYPE")
    para(c, VISION_TODAY(), M + 16, y0 + h - 48, lw, st)
    c.setFillColor(GREEN); c.setFont("Arial-Bold", 8); c.drawString(rx, y0 + h - 42, "IN PRODUCTION  |  THE VISION")
    para(c, VISION_PROD(), rx, y0 + h - 48, rw, ps(10, white, lead=13), raw=True)
    ax = M + 16 + lw + 8; ay = y0 + h / 2 - 12                       # arrow
    p = c.beginPath(); p.moveTo(ax, ay + 7); p.lineTo(ax + 12, ay + 7); p.lineTo(ax + 12, ay + 13); p.lineTo(ax + 20, ay)
    p.lineTo(ax + 12, ay - 13); p.lineTo(ax + 12, ay - 7); p.lineTo(ax, ay - 7); p.close()
    c.setFillColor(GREEN); c.drawPath(p, fill=1, stroke=0)


# ---------------- GUIDE: how to use + contents ----------------
def page_guide(c, d):
    pg = d["pages"]
    y = ref_header(c, None, "Inside this booklet", "How to use it in the weekly patient conference, and where to find each section",
                   d, "icon_checklist.png", left_icon="icon_monitor.png")
    y = subhead(c, "How to use this booklet in conference", y, 14); y -= 6
    steps = [(f"Start with At a glance (page {pg['glance']}), then the one-page summary (page {pg['summary']}).", "Why the patient is on the list, four status tiles and the recommended actions."),
             (f"Review the CQI analysis (pages {section_range(d, 'analysis')}).", "What CQI found, concluded and recommends: CQI summaries, risk, vitals, goals, medications, care plan gaps."),
             (f"Check it against the evidence (pages {section_range(d, 'evidence')}).", "The operational records behind each finding. Each page ends with a feedback tip."),
             (f"Give your input on screen (pages {section_range(d, 'input')}).", "Rate each finding, record the conference decision, save and return.")]
    for i, (a, b) in enumerate(steps, 1):
        c.setFillColor(GREEN if i == 4 else DARK_BLUE); c.circle(M + 13, y - 12, 13, fill=1, stroke=0)
        c.setFillColor(white); c.setFont("Arial-Bold", 13); c.drawCentredString(M + 13, y - 16.5, str(i))
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 11); c.drawString(M + 36, y - 9, a)
        para(c, b, M + 36, y - 14, CW - 40, ps(9.6, lead=12))
        y -= 33
    y -= 6
    y = subhead(c, "Contents", y, 14)
    para(c, '<font color="#4E7D14"><b>CQI analysis</b></font> pages show what CQI worked out: predicted risk, rule-based checks and '
            'AI-written summaries. <font color="#003DA5"><b>Evidence</b></font> pages show the operational records behind them, '
            'so you can check CQI.', M, y, CW, ps(9.2, DARK_GRAY, lead=12), raw=True)
    y -= 34
    groups = [(x["name"], x["desc"], SECTION_COLOR[x["id"]],
               [(k, PAGE_TITLES[k][0]) for k in x["keys"] if k != "cover"], x["divider"]) for x in d["sections"]]

    gap = 14; colw = (CW - gap) / 2; row_h = 15
    layout = [[groups[0], groups[1]], [groups[2], groups[3]]]
    for ci, col_groups in enumerate(layout):
        x = M + ci * (colw + gap); yy = y
        for title, sub, col, items, div in col_groups:
            if not items: continue
            h = 34 + row_h * len(items) + 6
            c.setFillColor(PANEL if title != "Start here" else HexColor("#EEF2F8")); c.roundRect(x, yy - h, colw, h, 6, fill=1, stroke=0)
            c.setFillColor(col); c.rect(x, yy - h + 6, 4, h - 12, fill=1, stroke=0)
            c.setFillColor(col); c.setFont("Arial-Bold", 11); c.drawString(x + 14, yy - 16, title)
            if div and div in pg:
                c.setFillColor(MID_GRAY); c.setFont("Arial", 8); c.drawRightString(x + colw - 12, yy - 16, f"Section starts p. {pg[div]}")
            c.setFillColor(MID_GRAY); c.setFont("Arial-Italic", 8.2); c.drawString(x + 14, yy - 27, sub)
            ry = yy - 42
            for k, n in items:
                c.setFillColor(DARK_GRAY); c.setFont("Arial", 9.4); c.drawString(x + 14, ry, n)
                c.setFillColor(LIGHT_BLUE); c.setFont("Arial-Bold", 9.4); c.drawRightString(x + colw - 12, ry, str(pg[k]))
                ry -= row_h
            yy -= h + 10
        if ci == 0: left_bottom = yy
        else: right_bottom = yy
    y = min(left_bottom, right_bottom) - 4
    y -= 4
    y = subhead(c, "Colour key", y, 12); y -= 2
    keys = [(PLUM, "Act now / urgent, missed, or a data gap to resolve"), (DARK_BLUE, "In progress / this week"),
            (LIGHT_BLUE, "Monitor"), (GREEN, "On track / met")]
    x = M
    for col, lab in keys:
        c.setFillColor(col); c.roundRect(x, y - 12, 12, 12, 2, fill=1, stroke=0)
        c.setFillColor(DARK_GRAY); c.setFont("Arial", 8.8); c.drawString(x + 17, y - 10, lab)
        x += 17 + pdfmetrics.stringWidth(lab, "Arial", 8.8) + 18
    footer(c, pg["guide"], d)

# ---------------- sections: titles, colours, divider pages ----------------
SECTION_COLOR = {"start": NAVY, "analysis": HexColor("#4E7D14"), "evidence": DARK_BLUE, "input": PLUM}
PAGE_TITLES = {
    "about": ("What is Clinical Quality Intelligence?", "Purpose, how it works, the vision"),
    "guide": ("Inside this booklet", "How to use it and contents"),
    "glance": ("At a glance", "From stated goal to what needs attention now"),
    "summary": ("Patient conference summary", "Why this patient, status tiles, actions"),
    "narratives": ("CQI summaries: profile, risk, vitals", "AI-generated, unedited"),
    "narratives2": ("CQI summaries: care, missed care, data gaps", "AI-generated, unedited"),
    "DC": ("Discharge or recertification", "System recommendation and rationale"),
    "A2": ("Risk drivers and episode timeline", "Why the model flags this patient"),
    "C": ("Vitals trajectory and action window", "Trend, urgency and when to act"),
    "D": ("Goals met and not met", "Including therapy goals"),
    "M": ("Medication adherence", "High-risk meds, understanding, teaching"),
    "F": ("Care plan gaps: what to add", "Interventions, assessments, visit goals"),
    "G": ("Care network and referrals", "PCP, CW PCO, CW Rx at discharge"),
    "A": ("Patient context and 485 profile", "Demographics, living situation, profile"),
    "DX": ("Diagnoses and procedures", "Codes, onset and exacerbation"),
    "W": ("Wounds", "Type, size over time, healing"),
    "OA": ("OASIS", "Start of care vs. latest"),
    "B": ("Visits: ordered, delivered, scheduled", "Weekly grid by discipline"),
    "B2": ("Missed care and events", "Missed visits, alerts, logs, inpatient"),
    "C2": ("Vitals trends", "Charts against patient limits"),
    "E": ("Assessments, interventions, adverse events", "What was done at visits"),
    "feedback": ("Clinician feedback", "Rate each finding"),
    "notes": ("Conference decision and notes", "Decision, owner, follow-ups"),
    "methods": ("How CQI produced this", "Methods, data sources, limits"),
}
SECTION_INTRO = {
    "analysis": ("Everything in this section was <b>produced by CQI</b>: a predictive risk model, rule-based checks across visits, "
                 "vitals, goals and medications, and AI-written summaries checked by LLM judges. This is what operational "
                 "reports do not do: CQI <b>connects</b> the data, <b>explains</b> why the patient is at risk, and "
                 "<b>recommends</b> what to do next.", "Treat every finding as a draft for clinical review."),
    "evidence": ("These pages show the <b>operational records</b> CQI used, organized for this patient: the same data you would "
                 "find in the EMR and standard reports. Use them to <b>confirm or challenge</b> the findings in CQI analysis. "
                 "The last page explains <b>how CQI produced</b> those findings: methods, data sources and known limits.",
                 "Each page ends with a feedback tip that links back to the finding it supports."),
    "input": ("Your review makes CQI better. Rate each finding, record the conference decision and add notes <b>on screen</b>, "
              "then save and return the PDF. Answers are collected automatically.",
              "Open in Adobe Acrobat Reader, Microsoft Edge or Chrome to fill in."),
}

def section_of(d, key=None):
    sid = (d.get("page_section") or {}).get(key or d.get("_key"))
    return next((s_ for s_ in d.get("sections") or [] if s_["id"] == sid), None)

def section_range(d, sid):
    s_ = next((x for x in d.get("sections") or [] if x["id"] == sid), None)
    if not s_: return ""
    ns_ = [d["pages"][k] for k in ([s_["divider"]] if s_["divider"] else []) + s_["keys"] if k in d["pages"]]
    return f"{min(ns_)}-{max(ns_)}" if ns_ else ""

def page_divider(c, d, sid):
    pg = d["pages"]; s_ = next(x for x in d["sections"] if x["id"] == sid)
    col = SECTION_COLOR[sid]; num = [x["id"] for x in d["sections"]].index(sid) + 1
    top = H - 30; bh = 240; y0 = top - bh
    c.setFillColor(DARK_BLUE); c.rect(M, y0, CW, bh, fill=1, stroke=0)
    c.saveState(); p = c.beginPath(); p.rect(M, y0, CW, bh); c.clipPath(p, stroke=0, fill=0)
    split = M + CW * 0.535; ph = bh * 0.5
    img(c, "pattern_left.png", M, y0, w=split - M + 1, h=ph); img(c, "pattern_right.png", split, y0, w=M + CW - split, h=ph)
    c.restoreState()
    c.setFillColor(col if sid != "evidence" else LIGHT_BLUE); c.rect(M, y0 - 10, CW, 10, fill=1, stroke=0)
    c.setFillColor(GREEN); c.setFont("Arial-Bold", 11); c.drawString(M + 30, top - 40, f"SECTION {num - 1}" if num > 1 else "")
    c.setFillColor(white); c.setFont("Arial-Bold", 40); c.drawString(M + 30, top - 92, s_["name"])
    c.setFont("Arial", 14); c.drawString(M + 30, top - 118, s_["desc"])
    c.setFillColor(HexColor("#BFE6F5")); c.setFont("Arial-Bold", 10)
    c.drawString(M + 30, top - 150, f"Pages {section_range(d, sid)}")
    intro, note = SECTION_INTRO[sid]
    y = y0 - 34
    y -= para(c, intro, M, y, CW, ps(11.5, DARK_GRAY, lead=16), raw=True) + 8
    c.setFillColor(col); c.setFont("Arial-Italic", 10); c.drawString(M, y - 10, note)
    y -= 34
    c.setFillColor(col); c.setFont("Arial-Bold", 12.5); c.drawString(M, y - 12, "In this section"); y -= 24
    keys = s_["keys"]; ncol = 2 if len(keys) > 4 else 1; per = -(-len(keys) // ncol)
    gw = (CW - 12 * (ncol - 1)) / ncol; rh = 36
    for i, k in enumerate(keys):
        t, sub = PAGE_TITLES.get(k, (k, ""))
        x = M + (i // per) * (gw + 12); yy = y - (i % per) * (rh + 6)
        c.setFillColor(PANEL); c.roundRect(x, yy - rh, gw, rh, 5, fill=1, stroke=0)
        c.setFillColor(col); c.rect(x, yy - rh, 4, rh, fill=1, stroke=0)
        ts = group_size([t], "Arial-Bold", gw - 50, 10.5, 8.5)
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", ts); c.drawString(x + 14, yy - 15, t)
        c.setFillColor(MID_GRAY); c.setFont("Arial", 8.4); c.drawString(x + 14, yy - 28, sub)
        c.setFillColor(LIGHT_BLUE); c.setFont("Arial-Bold", 12); c.drawRightString(x + gw - 12, yy - 22, str(pg[k]))
    footer(c, pg[s_["divider"]], d)


# ---------------- PAGE 1: one-page summary ----------------
ACT_STYLE = [("TOPPADDING", (0, 1), (-1, -1), 6), ("BOTTOMPADDING", (0, 1), (-1, -1), 6), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]

def page_summary(c, d):
    h = d["header"]; r = d["risk"]; pg = d["pages"]
    top, bh = 30, 112
    y = blue_bar_header(c, top, bh, icon=None)
    # Header mirrors the CQI Power BI banner: CenterWell logo, green program name, white product line
    tx = M + 16
    c.setFillColor(white); c.setFont("Arial-Bold", 8); c.drawString(tx, H - top - 26, "CWHH  |  WEEKLY PATIENT CONFERENCE")
    c.setFillColor(GREEN); c.setFont("Arial-Bold", 19); c.drawString(tx, H - top - 58, "Clinical Quality Intelligence")
    c.setFillColor(white); c.setFont("Arial-Bold", 13.5); c.drawString(tx, H - top - 77, "Patient conference summary")
    c.setFont("Arial", 8.2)
    c.drawString(tx, H - top - 92, f"Proof of concept: {h['cohort']}")
    c.drawString(tx, H - top - 103, f"{h['model']}  |  CQI run {h['run_date']}  |  {h['conference_date']}")
    bx, bw = W - M - 162, 154  # ID card
    c.setFillColor(white); c.roundRect(bx, H - top - bh + 12, bw, bh - 24, 6, fill=1, stroke=0)
    rows = [("Patient ID", h["patient_id"]), ("Episode", h["episode_id"]), ("Branch", h["branch"][:22]),
            ("Status", h["episode_status"]), ("Admission", h["admission_type"]), ("Episode day", f"{h['episode_day']} of {h['episode_length']}")]
    yy = H - top - 27
    for k, val in rows:
        c.setFont("Arial", 8); c.setFillColor(MID_GRAY); c.drawString(bx + 10, yy, k)
        c.setFont("Arial-Bold", 8.5); c.setFillColor(DARK_BLUE); c.drawString(bx + 60, yy, str(val)[:17]); yy -= 12.8
    y -= 10
    c.setFillColor(DARK_GRAY); c.setFont("Arial", 8); c.drawString(M, y - 10, "60-DAY HOSPITALIZATION RISK")
    c.setFillColor(PLUM); c.setFont("Arial-Bold", 18); c.drawString(M, y - 30, f"{r['tier']}  {r['score']:.2f}")
    cols = [("Percentile", r["percentile"]), ("Trend", r["trend"]),
            ("Care gaps raise risk", "Yes" if r["risk_raised_by_care_gaps"] else "No"), ("Delay impact", r["delay_clinical_impact"])]
    xs = M + 134; widths_ = [64, 146, 100, CW - 134 - 310]      # columns end exactly at the right margin
    vs = min(group_size([v], "Arial-Bold", w_ - 8, TS["value"], 8) for (_, v), w_ in zip(cols, widths_))
    for (lab, val), wcol in zip(cols, widths_):
        c.setFillColor(DARK_GRAY); c.setFont("Arial", TS["label"]); c.drawString(xs, y - 10, lab.upper())
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", vs); c.drawString(xs, y - 26, val); xs += wcol
    y -= 40; rule(c, y)
    if d.get("discharge"):
        y -= discharge_banner(c, d, y - 6, network=False) + 6
    y -= 10
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 14); c.drawString(M, y - 14, "Why this patient is on today's list")
    hh = para(c, d["headline"], M, y - 20, CW * 0.6, ps(10.5, DARK_GRAY, lead=13.5)) + 20
    vx = M + CW * 0.64; vw = CW * 0.36
    c.setFillColor(GREEN); c.setFont("Arial-Bold", 8); c.drawString(vx + 12, y - 10, "IN THE PATIENT'S WORDS")
    goal = d["patient_goal"]; goal = goal if len(goal) < 90 else goal[:87] + "..."
    qh = para(c, f'"{goal}"', vx + 12, y - 16, vw - 14, ps(10.5, DARK_BLUE, "Arial-Italic", 13.5)) + 18
    blk = max(hh, qh, 40)
    c.setStrokeColor(GREEN); c.setLineWidth(1.2); c.line(vx, y - 2, vx, y - blk)
    y -= blk + 10
    tw = (CW - 14) / 2
    tile_need = lambda t: sum(Paragraph(T(p_), ps(9.4, lead=12.4)).wrap(tw - 36, 999)[1] + 5 for p_ in t["points"][:3])
    th = max(108, min(136, 32 + max(tile_need(t) for t in d["tiles"].values()) + 28))
    # leave room for every action row + the fixed decision box: tiles give up height first
    act_rows = [[P(i, CELLB), P(a, ps(9, lead=11)), P(o), status_cell(wh), P(pgn)] for i, (a, o, wh, pgn) in enumerate(d["actions"][:4], 1)]
    act_h = Table([[P("#", HEADC)] * 5] + (act_rows or [[P("-")] * 5]), colWidths=[20, 316, 80, 72, 32], style=ACT_STYLE).wrap(CW, 999)[1]
    room = y - (FOOT_TOP + 26) - act_h - 26 - 26 - 16
    th = max(min(th, room / 2), 32 + max(tile_need(t) for t in d["tiles"].values()) + 12)
    for i, (k, title) in enumerate([("care", "Care delivery gaps"), ("risk", "Clinical risk drivers"),
                                    ("vitals", "Vitals trajectory"), ("goals", "Patient goals")]):
        t = d["tiles"][k]; col = STATUS_COLOR[t["status"]]
        tx = M + (i % 2) * (tw + 14); ty = y - (i // 2) * (th + 14)
        c.setFillColor(PANEL); c.rect(tx, ty - th, tw, th, fill=1, stroke=0)
        c.setFillColor(col); c.rect(tx, ty - th, 4, th, fill=1, stroke=0)
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 12); c.drawString(tx + 14, ty - 18, title)
        chip(c, tx + tw - 10, ty - 22, t["status"], col, 7, right=True)
        yy = ty - 28
        for pt in t["points"][:3]:
            c.setFillColor(DARK_BLUE); c.circle(tx + 17, yy - 5.8, 1.9, fill=1, stroke=0)
            yy -= para(c, pt, tx + 24, yy, tw - 36, ps(9.4, DARK_GRAY, lead=12.4)) + 5
        c.setFillColor(LIGHT_BLUE); c.setFont("Arial-Bold", 8.5)
        c.drawRightString(tx + tw - 10, ty - th + 8, f"Summary p.{pg['narratives2'] if k == 'care' else pg['narratives']}  |  Evidence p.{t['page']}  >")
    y -= 2 * th + 14 + 22
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 14); c.drawString(M, y - 14, "Recommended actions for discussion")
    if d.get("data_gaps"):
        chip(c, W - M, y - 14, f"{len(d['data_gaps'])} data gaps - check page {pg['narratives2']} first", PLUM, 7, right=True)
    y -= 20
    table_fit(c, [P("#", HEADC), P("Action", HEADC), P("Owner", HEADC), P("When", HEADC), P("Page", HEADC)], act_rows,
              [20, 316, 80, 72, 32], M, y, FOOT_TOP + 26, [P("-"), P("No actions recommended for this run."), P(""), P(""), P("")], "actions",
              extra=ACT_STYLE)
    c.setFillColor(LIGHT_BLUE); c.setFont("Arial-Bold", 8.6)
    c.drawRightString(W - M, FOOT_TOP + 10, f"Record the conference decision on page {pg['notes']} and rate findings on page {pg['feedback']} (fillable)  >")
    footer(c, pg["summary"], d)

def data_gaps_box(c, d, x, y, w, h):
    """'Data Gaps Found' panel: one bullet per gap. Shrinks to fit, then lists '+ N more'.
    Plum accent = these are data problems to resolve, not clinical findings."""
    gaps = d.get("data_gaps") or []
    c.setFillColor(HexColor("#FBEAF2") if gaps else PANEL); c.rect(x, y, w, h, fill=1, stroke=0)
    c.setFillColor(PLUM if gaps else MID_GRAY); c.rect(x, y, 4, h, fill=1, stroke=0)
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 10); c.drawString(x + 12, y + h - 15, "Data Gaps Found")
    if gaps:
        chip(c, x + w - 8, y + h - 19, f"{len(gaps)} to resolve", PLUM, 6.5, right=True)
    c.setFillColor(MID_GRAY); c.setFont("Arial-Italic", 7.6)
    c.drawString(x + 12, y + h - 26, "Missing or conflicting data CQI noticed - confirm in the chart first.")
    top, avail, bw = y + h - 32, h - 40, w - 30
    if not gaps:
        para(c, "No data gaps were reported for this patient in this run.", x + 12, top, bw, ps(8.8, MID_GRAY, "Arial-Italic"))
        return
    def heights(items, size):
        st = ps(size, DARK_GRAY, lead=size * 1.22)
        return [Paragraph(T(g), st).wrap(bw, 4000)[1] + 2.5 for g in items]
    show, size = gaps, 8.8
    while size > 7.4 and sum(heights(show, size)) > avail:
        size -= 0.3
    if sum(heights(show, size)) > avail:            # still too long: keep what fits, leave room for "+ N more"
        while len(show) > 1 and sum(heights(show, size)) > avail - 11:
            show = show[:-1]
    yy = top
    for g, hh in zip(show, heights(show, size)):
        c.setFillColor(PLUM); c.circle(x + 15, yy - size * 0.55, 1.7, fill=1, stroke=0)
        para(c, g, x + 21, yy, bw, ps(size, DARK_GRAY, lead=size * 1.22)); yy -= hh
    if len(show) < len(gaps):
        c.setFillColor(MID_GRAY); c.setFont("Arial-Italic", 7.6)
        c.drawString(x + 21, yy - 8, f"+ {len(gaps) - len(show)} more data gaps - see the CQI Power BI report.")


# ---------------- fillable form helpers ----------------
FORM_BLUE = HexColor("#003DA5")

def fid(d, key):
    """Unique form-field name per patient-episode, so merged branch booklets keep separate answers."""
    h = d["header"]; clean = lambda v: re.sub(r"[^A-Za-z0-9]+", "", str(v))
    return f"cqi__{clean(h['patient_id'])}__{clean(h['episode_id'])}__{key}"

def f_check(c, d, key, x, y, size=10, tip=""):
    c.acroForm.checkbox(name=fid(d, key), tooltip=tip or key, x=x, y=y, size=size, buttonStyle="check",
                        borderColor=FORM_BLUE, fillColor=white, textColor=FORM_BLUE, borderWidth=0.8, forceBorder=True)

def f_radio(c, d, key, value, x, y, size=10, tip=""):
    c.acroForm.radio(name=fid(d, key), value=value, tooltip=tip or value, selected=False, x=x, y=y, size=size,
                     buttonStyle="circle", shape="square", borderColor=FORM_BLUE, fillColor=white,
                     textColor=FORM_BLUE, borderWidth=0.8, forceBorder=True)

def f_text(c, d, key, x, y, w, h, multiline=False, size=8.5, tip=""):
    c.acroForm.textfield(name=fid(d, key), tooltip=tip or key, x=x, y=y, width=w, height=h, fontName="Helvetica",
                         fontSize=size, borderColor=LIGHT_GRAY, fillColor=white, textColor=DARK_GRAY, borderWidth=0.6,
                         forceBorder=True, fieldFlags="multiline" if multiline else "")

def labelled_check(c, d, key, x, y, label, size=9):
    f_check(c, d, key, x, y - 1, 10, label)
    c.setFillColor(DARK_GRAY); c.setFont("Arial", size); c.drawString(x + 14, y + 1, label)
    return 14 + pdfmetrics.stringWidth(label, "Arial", size) + 14

def labelled_radio(c, d, key, value, x, y, label, size=9):
    f_radio(c, d, key, value, x, y - 1, 10, label)
    c.setFillColor(DARK_GRAY); c.setFont("Arial", size); c.drawString(x + 14, y + 1, label)
    return 14 + pdfmetrics.stringWidth(label, "Arial", size) + 14

def review_tip(c, d, keys, checks):
    """Bottom-of-page reminder replacing paper check boxes: what to verify, and where to record it."""
    pg = d["pages"]; names = {k: lab for k, lab, _ in FEEDBACK_FINDINGS}
    rated = ", ".join(f"<b>{names[k]}</b>" for k in keys)
    txt = (f'<font color="#{GREEN.hexval()[2:]}"><b>FEEDBACK TIP</b></font>&nbsp;&nbsp;Before rating {rated} on '
           f'<b>page {pg["feedback"]}</b> (fillable), check: ' + "; ".join(checks) + ".")
    st = ps(8.8, DARK_GRAY, lead=11.4)
    hh = Paragraph(txt, st).wrap(CW - 54, 999)[1] + 16
    y0 = FOOT_TOP + 4
    c.setFillColor(PANEL); c.rect(M, y0, CW, hh, fill=1, stroke=0)
    c.setFillColor(GREEN); c.rect(M, y0, 4, hh, fill=1, stroke=0)
    img(c, "icon_checklist.png", M + 12, y0 + hh / 2 - 11, w=22, h=22)
    para(c, txt, M + 42, y0 + hh - 8, CW - 54, st, raw=True)
    return y0 + hh + 10          # lowest y content above may use

def pointer(c, y, d, section, page_key="narratives"):
    c.setFillColor(PANEL); c.rect(M, y - 22, CW, 22, fill=1, stroke=0)
    c.setFillColor(LIGHT_BLUE); c.rect(M, y - 22, 4, 22, fill=1, stroke=0)
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 9)
    c.drawString(M + 12, y - 14.5, f"Evidence for the CQI {section} on page {d['pages'][page_key]}.")
    return 32

# ---------------- CQI summaries (AI-generated) ----------------
def section_band(c, x, y, w, title, ev=None):
    c.setFillColor(DARK_BLUE); c.rect(x, y - 20, w, 20, fill=1, stroke=0)
    c.setFillColor(white); c.setFont("Arial-Bold", 10.5); c.drawString(x + 10, y - 14, title)
    if ev:
        c.setFillColor(HexColor("#BFE6F5")); c.setFont("Arial-Bold", 8.5); c.drawRightString(x + w - 10, y - 13.5, ev)
    return 20

def stack_blocks(c, d, y, bottom, blocks, size=9.6):
    """Full-width narrative blocks. Natural height when it fits; otherwise share the space and let fit_para shrink/truncate."""
    need = [Paragraph(T(t or "No summary was generated for this run."), ps(size, lead=size * 1.27)).wrap(CW - 8, 4000)[1] for _, t, _ in blocks]
    overhead = len(blocks) * (20 + 8 + 16)
    avail = y - bottom - overhead
    share = [n for n in need] if sum(need) <= avail else [max(36, avail * n / sum(need)) for n in need]
    for (title, text, ev), h in zip(blocks, share):
        y -= section_band(c, M, y, CW, title, ev) + 8
        y -= fit_para(c, text, M + 4, y, CW - 8, h + 2, size=size, min_size=7.6) + 16
    return y

def kpi_and_flags(c, d, y):
    k = d["kpis"]; bw = (CW - 5 * 8) / 6
    for i, (lab, val, alert) in enumerate(k):
        bx = M + i * (bw + 8)
        c.setFillColor(PANEL); c.rect(bx, y - 40, bw, 40, fill=1, stroke=0)
        c.setFillColor(DARK_GRAY); c.setFont("Arial", 7.8); c.drawCentredString(bx + bw / 2, y - 13, lab)
        c.setFillColor(PLUM if alert else NAVY); c.setFont("Arial-Bold", 16); c.drawCentredString(bx + bw / 2, y - 33, val)
    y -= 50
    FLAG_LABEL = {"schedule_status": "Schedule status", "outcome_risk_level": "Risk level", "frequency_status": "Frequency status",
                  "risk_raised_by_care_gaps": "Care gaps raise risk", "delay_clinical_impact": "Delay impact",
                  "compliance_window_missed": "Acuity window missed", "EPI_STATUS": "Episode status",
                  "discharge_window": "Discharge decision"}
    hdr = [P(FLAG_LABEL.get(f, f), ps(8, white, "Arial-Bold", lead=9.6)) for f, _, _ in d["flags"]]
    vals = [P(v.replace("_", " "), ps(8.8, TONE[t], "Arial-Bold" if t == "alert" else "Arial", lead=10.6)) for _, v, t in d["flags"]]
    y -= table(c, [hdr, vals], M, y, [76, 54, 70, 70, 58, 70, 60, 62], zebra=False,
               extra=[("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 1), (-1, 1), 1.2, GREEN)]) + 14
    return y

def page_narratives(c, d):
    pg = d["pages"]; n = d["narratives"]
    y = ref_header(c, None, "CQI summaries: profile, risk and vitals",
                   "AI-generated text, printed unedited. Check each summary against its evidence page.",
                   d, "icon_monitor.png", left_icon="icon_checklist.png")
    y = kpi_and_flags(c, d, y)
    if not any(n.get(k) for k in ("profile_risk", "vitals_trajectory", "care_delivery", "missed_care", "delay_impact", "data_gaps")):
        c.setFillColor(HexColor("#FBEAF2")); c.rect(M, y - 34, CW, 34, fill=1, stroke=0)
        c.setFillColor(PLUM); c.rect(M, y - 34, 4, 34, fill=1, stroke=0)
        c.setFont("Arial-Bold", 10); c.drawString(M + 14, y - 14, "CQI summaries were not generated for this patient in this run.")
        c.setFillColor(DARK_GRAY); c.setFont("Arial", 8.8)
        c.drawString(M + 14, y - 27, f"The summary page was built from facts only. Review the evidence pages and note the gap on page {pg['feedback']}.")
        y -= 46
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 10); c.drawString(M, y - 10, "Patient goal (care plan)")
    y -= 15
    y -= fit_para(c, n.get("patient_goal") or "", M, y, CW, 40, size=9.8, min_size=8.5, color=DARK_BLUE, font="Arial-Bold") + 14
    bottom = review_tip(c, d, ["r02", "r04"], ["the profile, diagnoses and living situation match the chart (page %d)" % pg["A"],
                                               "the trends described match the vitals charts (page %d)" % pg["C"]])
    stack_blocks(c, d, y, bottom, [("Patient Profile & Risk Summary", n.get("profile_risk"), f"Evidence: pages {pg['A']}-{pg['A2']}"),
                                   ("Vitals Trajectory Summary", n.get("vitals_trajectory"), f"Evidence: page {pg['C']}")])
    footer(c, pg["narratives"], d)

def page_narratives2(c, d):
    pg = d["pages"]; n = d["narratives"]
    y = ref_header(c, None, "CQI summaries: care delivery and data gaps",
                   "Care delivery, missed care, delay impact and data gaps, printed unedited.",
                   d, "icon_monitor.png", left_icon="icon_checklist.png")
    bottom = review_tip(c, d, ["r05", "r06", "r07", "r08"],
                        ["visit counts match the weekly grid (page %d)" % pg["B"],
                         "missed visits and reasons match the log (page %d)" % pg["B2"],
                         "each data gap is real and not already resolved in the chart"])
    gaps = d.get("data_gaps") or []
    gneed = sum(Paragraph(T(g), ps(8.8, lead=10.8)).wrap(CW - 30, 999)[1] + 3 for g in gaps) + 50 if gaps else 64
    gh = max(64, min(gneed, 250))
    data_gaps_box(c, d, M, bottom, CW, gh)
    stack_blocks(c, d, y, bottom + gh + 14,
                 [("Care Delivery Summary", n.get("care_delivery"), f"Evidence: page {pg['B']}"),
                  ("Missed Care Summary", n.get("missed_care"), f"Evidence: page {pg['B2']}"),
                  ("Clinical Delay Impact Explanation", n.get("delay_impact"), f"Evidence: page {pg['B']}")])
    footer(c, pg["narratives2"], d)

# ---------------- CLINICAL PICTURE: diagnoses, wounds, OASIS ----------------
def summary_box(c, d, y, text, title="CQI summary"):
    """Light-blue AI summary box at the top of a page. Returns height used."""
    st = ps(TS["body"], DARK_GRAY, lead=TS["body"] * 1.3)
    txt = text or "No summary was generated for this run."
    h = min(Paragraph(T(txt), st).wrap(CW - 28, 999)[1], 70) + 30
    c.setFillColor(PANEL); c.rect(M, y - h, CW, h, fill=1, stroke=0)
    c.setFillColor(LIGHT_BLUE); c.rect(M, y - h, 4, h, fill=1, stroke=0)
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 10); c.drawString(M + 14, y - 15, title)
    fit_para(c, txt, M + 14, y - 21, CW - 28, h - 26, size=TS["body"], min_size=7.8,
             color=DARK_GRAY if text else MID_GRAY, font="Arial" if text else "Arial-Italic")
    return h + 12


def page_diagnoses(c, d):
    pg = d["pages"]; cl = d["clinical"]; dx = cl.get("diagnoses") or []
    y = ref_header(c, "A", "Diagnoses and procedures", "Primary and secondary diagnoses, onset or exacerbation, and procedures this episode",
                   d, "icon_checklist.png")
    y -= summary_box(c, d, y, cl.get("diagnosis_summary"))
    diag = [x for x in dx if (x.get("diag_or_proc") or "D").upper().startswith("D")]
    proc = [x for x in dx if (x.get("diag_or_proc") or "").upper().startswith("P")]
    cats = sorted({x.get("category") for x in diag if x.get("category")})
    y -= kpi_strip(c, y, [("Diagnoses", len(diag), NAVY), ("Procedures", len(proc), NAVY),
                          ("Onset / exacerbation", sum(1 for x in dx if x.get("onset_exacer")), PLUM if any(x.get("onset_exacer") == "EXACERBATION" for x in dx) else NAVY),
                          ("Body systems", len(cats), NAVY)]) + 6
    bottom = review_tip(c, d, ["r16"], ["diagnosis codes and the primary diagnosis match the 485 / plan of care",
                                        "onset vs. exacerbation is coded correctly", "the procedures listed belong to this episode"])
    oe = lambda x: (f"{x['onset_exacer'].title()}" + (f" ({'DAY' + format(x['onset_exacer_day'], '+d')})" if x.get("onset_exacer_day") is not None else "")) if x.get("onset_exacer") else ""
    y = subhead(c, "Diagnoses", y, 12)
    rows = [[P(x["code"], CELLB), P(("<b>Primary:</b> " if x.get("is_primary") else "") + T(x.get("description") or ""), CELL, raw=True),
             P((x.get("category") or "").title()), P(x.get("clinical_group") or ""),
             P(oe(x), ps(8.8, PLUM if x.get("onset_exacer") == "EXACERBATION" else DARK_GRAY, "Arial-Bold" if x.get("onset_exacer") else "Arial", 10.6))]
            for x in diag]
    hdr = [P(t, HEADC) for t in ["Code", "Description", "Body system", "Clinical group", "Onset / exacerbation"]]
    y -= table_fit(c, hdr, rows, [56, 200, 108, 84, CW - 448], M, y, bottom + 70, [P("None recorded"), P(""), P(""), P(""), P("")], "diagnoses",
                   extra=[("BACKGROUND", (0, 1), (-1, 1), HexColor("#EEF6E2"))] if diag and diag[0].get("is_primary") else None) + 12
    y = subhead(c, "Procedures", y, 12)
    rows = [[P(x["code"], CELLB), P(x.get("description") or ""), P(x.get("clinical_group") or ""), P(oe(x))] for x in proc]
    table_fit(c, [P(t, HEADC) for t in ["Code", "Description", "Clinical group", "Date"]], rows, [70, 280, 90, CW - 440], M, y, bottom,
              [P(""), P("No procedures recorded this episode"), P(""), P("")], "procedures")
    footer(c, pg["DX"], d)


WOUND_STATUS_COLOR = {"healed": OK_GREEN, "improving": OK_GREEN, "stable": DARK_BLUE, "worsening": PLUM, "deteriorating": PLUM,
                      "not reassessed": PLUM}

def page_wounds(c, d):
    pg = d["pages"]; cl = d["clinical"]; wd = cl.get("wounds") or []
    y = ref_header(c, "A", "Wounds", "Wound type, location, size over time and healing status", d, "icon_checklist.png")
    y -= summary_box(c, d, y, cl.get("wound_summary"))
    stale = [w for w in wd if w.get("last_day") is not None and d["header"]["episode_day"] - w["last_day"] > 7
             and (w.get("healing_status") or "").lower() != "healed"]
    y -= kpi_strip(c, y, [("Wounds", len(wd), NAVY),
                          ("Pressure ulcers", sum(1 for w in wd if "PRESSURE" in (w.get("category") or "").upper()), PLUM if any("PRESSURE" in (w.get("category") or "").upper() for w in wd) else NAVY),
                          ("Healed", sum(1 for w in wd if (w.get("healing_status") or "").lower() == "healed"), OK_GREEN),
                          ("Not measured in 7+ days", len(stale), PLUM if stale else NAVY)]) + 6
    bottom = review_tip(c, d, ["r16"], ["wound type, location and stage match the wound assessment",
                                        "measurements and healing status are current", "every open wound has a treatment order"])
    y = subhead(c, "Wound inventory", y, 12)
    def status_p(w):
        st = w.get("healing_status") or "Unknown"
        col = WOUND_STATUS_COLOR.get(st.lower(), DARK_GRAY)
        return Paragraph(f'<font color="#{col.hexval()[2:]}"><b>{T(st)}</b></font>', CELL)
    def change(w):
        if w.get("area_change_pct") is None or (w.get("assessments") or 0) < 2: return "-"
        v = w["area_change_pct"]
        return f"{'+' if v > 0 else ''}{v:.0f}% area"
    rows = [[P(w.get("location") or "", CELLB),
             P(T((w.get("category") or "").title()) + (f"<br/><font size=7.6 color='#6D6E71'>{T((w.get('extended_type') or '').title())}</font>" if w.get("extended_type") else ""), CELL, raw=True),
             P(w.get("stage") or "-"), P(f"{w.get('first_size') or '-'} &rarr; {w.get('latest_size') or '-'}", CELL, raw=True),
             P(change(w), ps(8.8, PLUM if (w.get("area_change_pct") or 0) > 0 else OK_GREEN, "Arial-Bold", 10.6)),
             status_p(w), P(f"{w.get('assessments')} / D+{w.get('last_day')}" if w.get("last_day") is not None else "")]
            for w in wd]
    hdr = [P(t, HEADC) for t in ["Location", "Type", "Stage", "Size: first \u2192 latest", "Change", "Status", "Count / last"]]
    y -= table_fit(c, hdr, rows, [78, 96, 44, 120, 54, 70, CW - 462], M, y, bottom + 90,
                   [P("No wounds recorded this episode"), P(""), P(""), P(""), P(""), P(""), P("")], "wounds") + 12
    if wd:
        y = subhead(c, "Wound descriptions", y, 12)
        for w in wd:
            if y < bottom + 16: break
            c.setFillColor(DARK_BLUE); c.circle(M + 4, y - 6, 2, fill=1, stroke=0)
            y -= para(c, f"<b>{T(w.get('location') or '')}:</b> {T((w.get('description') or '').capitalize())}", M + 12, y, CW - 12,
                      ps(9.2, lead=11.8), raw=True) + 4
    footer(c, pg["W"], d)


def page_oasis(c, d):
    pg = d["pages"]; cl = d["clinical"]; oa = cl.get("oasis") or []
    y = ref_header(c, "A", "OASIS", "Key OASIS items at start of care and the latest assessment, with changes highlighted", d, "icon_checklist.png")
    y -= summary_box(c, d, y, cl.get("oasis_summary"))
    tps = sorted({x.get("latest_timepoint") for x in oa if x.get("latest_timepoint")} | {x.get("first_timepoint") for x in oa if x.get("first_timepoint")})
    y -= kpi_strip(c, y, [("Items captured", len(oa), NAVY), ("Key risk items", sum(1 for x in oa if x.get("key_item")), NAVY),
                          ("Changed since SOC", sum(1 for x in oa if x.get("changed")), DARK_BLUE),
                          ("Assessments", len(tps), NAVY)]) + 6
    bottom = review_tip(c, d, ["r16"], ["answers match the OASIS in the EMR", "changed items reflect a real change in the patient",
                                        "M1033 risk factors are addressed in the care plan"])
    y = subhead(c, "OASIS items (key risk items first)", y, 12)
    multi = any(x.get("changed") or (x.get("latest_timepoint") and x.get("latest_timepoint") != x.get("first_timepoint")) for x in oa)
    if multi:
        hdr = [P(t, HEADC) for t in ["Item", "Description", "Start of care", "Latest"]]; widths = [50, 130, 170, CW - 350]
        rows = [[P(x["item"], CELLB), P(x.get("description") or ""), P(x.get("first") or ""),
                 P(x.get("latest") or "", ps(8.8, DARK_BLUE if x.get("changed") else DARK_GRAY, "Arial-Bold" if x.get("changed") else "Arial", 10.6))]
                for x in oa]
    else:
        hdr = [P(t, HEADC) for t in ["Item", "Section", "Description", "Answer"]]; widths = [50, 100, 140, CW - 290]
        rows = [[P(x["item"], CELLB), P(x.get("section") or ""), P(x.get("description") or ""), P(x.get("first") or "")] for x in oa]
    ext = [("BACKGROUND", (0, i + 1), (-1, i + 1), HexColor("#EEF6E2")) for i, x in enumerate(oa) if x.get("changed")]
    y -= table_fit(c, hdr, rows, widths, M, y, bottom, [P("No OASIS found for this episode")] + [P("")] * 3, "OASIS items", extra=ext) + 4
    if tps:
        c.setFont("Arial-Italic", 8); c.setFillColor(MID_GRAY)
        c.drawString(M, y - 6, "Assessments: " + "; ".join(tps) + (".  Green rows changed since start of care." if multi else "."))
    footer(c, pg["OA"], d)


# ---------------- DC: discharge or recertification recommendation ----------------
REC_COLOR = {"Discharge": OK_GREEN, "Discharge with referrals": OK_GREEN, "Recertify": DARK_BLUE, "Clinical review": PLUM}

def bullet_list(c, x, y, w, items, col, size=9.6, bottom=0):
    st = ps(size, DARK_GRAY, lead=size * 1.28)
    for it in items:
        h = Paragraph(T(it), st).wrap(w - 12, 999)[1]
        if y - h < bottom: break
        c.setFillColor(col); c.circle(x + 4, y - size * 0.55, 2.2, fill=1, stroke=0)
        para(c, it, x + 12, y, w - 12, st); y -= h + 5
    return y

def page_discharge(c, d):
    pg = d["pages"]; r = d.get("dc_rec") or {}
    y = ref_header(c, None, "Discharge or recertification", "System recommendation from this patient's data, with the rationale for each side",
                   d, "icon_checklist.png", left_icon="icon_monitor.png")
    if d.get("discharge"):
        d["_cur"] = pg["DC"]; y -= discharge_banner(c, d, y, network=False) + 14; d["_cur"] = None
    rec = r.get("recommendation") or "Clinical review"; col = REC_COLOR.get(rec, DARK_BLUE)
    # recommendation bar (same style as CQI flags)
    st = ps(11.5, white, lead=15); tx = M + 200; tw = W - M - 16 - tx
    rh = max(84, Paragraph(T(r.get("rationale") or ""), st).wrap(tw, 999)[1] + 30)
    c.setFillColor(DARK_BLUE); c.roundRect(M, y - rh, CW, rh, 6, fill=1, stroke=0)
    c.setFillColor(GREEN); c.setFont("Arial-Bold", 9); c.drawString(M + 18, y - 22, "SYSTEM RECOMMENDATION")
    rs = group_size([rec], "Arial-Bold", tx - M - 30, 20, 12)
    c.setFillColor(white); c.setFont("Arial-Bold", rs); c.drawString(M + 18, y - 46, rec)
    chip(c, M + 18, y - 70, r.get("confidence") or "", col if rec != "Recertify" else LIGHT_BLUE, 7.5)
    para(c, r.get("rationale") or "", tx, y - (rh - Paragraph(T(r.get("rationale") or ""), st).wrap(tw, 999)[1]) / 2, tw, st)
    y -= rh + 16
    bottom = FOOT_TOP + 8 if d.get("brief") else review_tip(c, d, ["r11"], ["each signal matches the evidence pages",
                                        "the recommendation fits the patient's goal and situation", "the physician agrees before the plan changes"])
    # two columns
    half = (CW - 14) / 2
    cols = [("Supports recertification", r.get("supports_recert") or ["No signals found."], DARK_BLUE),
            ("Supports discharge", r.get("supports_discharge") or ["No signals found."], OK_GREEN)]
    ch = max(sum(Paragraph(T(i), ps(9.6, lead=12.3)).wrap(half - 40, 999)[1] + 5 for i in items) for _, items, _ in cols) + 40
    ch = min(ch, (y - bottom) * 0.48)
    for i, (t, items, cc) in enumerate(cols):
        bx = M + i * (half + 14)
        c.setFillColor(PANEL); c.roundRect(bx, y - ch, half, ch, 8, fill=1, stroke=0)
        c.setFillColor(cc); c.rect(bx, y - ch + 8, 4, ch - 16, fill=1, stroke=0)
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 11.5); c.drawString(bx + 16, y - 20, f"{t} ({len(r.get('supports_recert' if i == 0 else 'supports_discharge') or [])})")
        bullet_list(c, bx + 16, y - 30, half - 28, items, cc, bottom=y - ch + 6)
    y -= ch + 14
    if r.get("data_caveats"):
        txt = "<b>Check first:</b> " + " ".join(T(x) for x in r["data_caveats"])
        hh = Paragraph(txt, ps(9.2, PLUM, lead=12)).wrap(CW - 24, 999)[1] + 14
        c.setFillColor(HexColor("#FBEAF2")); c.rect(M, y - hh, CW, hh, fill=1, stroke=0)
        para(c, txt, M + 12, y - 7, CW - 24, ps(9.2, PLUM, lead=12), raw=True); y -= hh + 14
    y = subhead(c, "Next steps if the team agrees", y, 12)
    bullet_list(c, M, y, CW, r.get("next_steps") or [], DARK_BLUE, size=9.6, bottom=bottom + 30)
    c.setFillColor(MID_GRAY); c.setFont("Arial-Italic", 8)
    c.drawString(M, bottom + 12, f"{'AI-generated' if r.get('source') == 'llm' else 'Rule-based'} recommendation for clinical review. "
                                 "The clinical team and physician make the discharge or recertification decision.")
    footer(c, pg["DC"], d)


# ---------------- M: medication adherence ----------------
def yes_no_cell(v):
    if v is None: return P("-", ps(8.8, MID_GRAY, align=1))
    return Paragraph(f'<font color="#{(OK_GREEN if v else PLUM).hexval()[2:]}"><b>{"Yes" if v else "No"}</b></font>', ps(8.8, align=1))

def page_meds(c, d):
    pg = d["pages"]; m = d.get("meds") or {}
    y = ref_header(c, "E", "Medication adherence", "Does the patient understand, have and take their medications as prescribed?",
                   d, "icon_checklist.png")
    y -= summary_box(c, d, y, m.get("summary"))
    since = (d["header"]["episode_day"] - m["last_check_day"]) if m.get("last_check_day") is not None else None
    vt = m.get("visits_total") or 0
    y -= kpi_strip(c, y, [("Active meds", m.get("active_count") if m.get("active_count") is not None else "-", NAVY),
                          ("High-risk meds", len(m.get("high_risk") or []), PLUM if m.get("high_risk") else NAVY),
                          ("Understanding", f"{m.get('visits_with_check') or 0} of {vt} visits" if vt else "Not documented",
                           PLUM if vt and (m.get("visits_with_check") or 0) * 2 < vt else NAVY),
                          ("Adherence issues", m.get("adherence_concerns") if m.get("adherence_concerns") is not None else "-",
                           PLUM if m.get("adherence_concerns") else NAVY),
                          ("Days since check", since if since is not None else "Never", PLUM if since is None or since > 14 else NAVY)]) + 6
    if m.get("flag"):
        txt = f"<b>Flag:</b> {T(m['flag'])} Check understanding with teach-back and confirm the patient is taking each medication as prescribed."
        hh = Paragraph(txt, ps(9.6, PLUM, lead=12.4)).wrap(CW - 24, 999)[1] + 14
        c.setFillColor(HexColor("#FBEAF2")); c.rect(M, y - hh, CW, hh, fill=1, stroke=0)
        c.setFillColor(PLUM); c.rect(M, y - hh, 4, hh, fill=1, stroke=0)
        para(c, txt, M + 12, y - 7, CW - 24, ps(9.6, PLUM, lead=12.4), raw=True); y -= hh + 12
    bottom = review_tip(c, d, ["r18"], ["the high-risk list matches the medication profile",
                                        "understanding, teaching and adherence are documented on the visits shown",
                                        "adherence barriers (vision, cost, cognition, supply) are addressed in the plan"])
    y = subhead(c, "High-risk medications", y, 12)
    rows = [[P(x["name"], CELLB), P(x.get("drug_class") or ""), P(x.get("reason") or "")] for x in m.get("high_risk") or []]
    y -= table_fit(c, [P(t, HEADC) for t in ["Medication", "Class", "Why it matters"]], rows, [190, 110, CW - 300], M, y, bottom + 90,
                   [P("No high-risk medications on the profile")] + [P("")] * 2, "high-risk medications") + 12
    y = subhead(c, "Understanding, teaching and adherence at visits", y, 12)
    rows = [[P(f"DAY+{x['episode_day']}"), P(x.get("discipline") or "", CELLB), yes_no_cell(x.get("understanding")),
             yes_no_cell(x.get("teaching")), yes_no_cell(x.get("adherent"))]
            for x in (m.get("checks") or []) if any(x.get(k) is not None for k in ("understanding", "teaching", "adherent"))]
    w3 = (CW - 150) / 3
    table_fit(c, [P(t, HEADC) for t in ["Visit", "Discipline", "Understanding assessed", "Teaching done", "Taking as prescribed"]], rows,
              [75, 75, w3, w3, w3], M, y, bottom, [P("Not documented on any visit")] + [P("")] * 4, "visits")
    footer(c, pg["M"], d)


# ---------------- conference decision + notes (all fillable sections together) ----------------
def page_notes(c, d):
    pg = d["pages"]
    y = ref_header(c, None, "Conference decision and notes (fillable)", "Record the team's decision here. Saved with the feedback form.",
                   d, "icon_monitor.png", left_icon="icon_checklist.png")
    # decision panel
    BOX_H = 92
    c.setFillColor(PANEL); c.rect(M, y - BOX_H, CW, BOX_H, fill=1, stroke=0)
    c.setFillColor(GREEN); c.rect(M, y - BOX_H, 4, BOX_H, fill=1, stroke=0)
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 12); c.drawString(M + 16, y - 20, "Conference decision")
    rec = (d.get("dc_rec") or {}).get("recommendation")
    if rec:
        c.setFillColor(MID_GRAY); c.setFont("Arial-Italic", 8.4)
        c.drawRightString(W - M - 12, y - 20, f"System discharge recommendation: {rec} (page {pg['DC']})")
    xx = M + 16; yy = y - 40
    for v, lab in [("agree", "Agree with CQI"), ("agree_changes", "Agree with changes"), ("disagree", "Disagree"), ("escalate", "Escalate to MD")]:
        xx += labelled_radio(c, d, "decision", v, xx, yy, lab, 9.4)
    fy = y - BOX_H + 12
    for x_, w_, k, lab in [(M + 16, 180, "action_owner", "Action owner"), (M + 206, 110, "due_date", "Due date"),
                           (M + 326, CW - 340, "reviewed_by", "Reviewed by")]:
        c.setFillColor(DARK_GRAY); c.setFont("Arial", 8); c.drawString(x_, fy + 19, lab)
        f_text(c, d, k, x_, fy, w_, 15, tip=lab)
    y -= BOX_H + 18
    blocks = [("Discussion notes", "notes_discussion", 0.45), ("Follow-ups and owners", "notes_followups", 0.30),
              ("Anything else for the CQI team", "notes_cqi", 0.25)]
    avail = y - FOOT_TOP - 6 - len(blocks) * 34
    for title, key, share in blocks:
        y = subhead(c, title, y); y -= 2
        h = avail * share
        f_text(c, d, key, M, y - h, CW, h, multiline=True, size=9.5, tip=title)
        y -= h + 14
    footer(c, pg["notes"], d)


# ---------------- G: care network and discharge referrals ----------------
IND_COLOR = {"Refer": OK_GREEN, "Consider": DARK_BLUE, "Already enrolled": NAVY, "Not indicated": MID_GRAY, "Check eligibility": PLUM}

def yn(v, yes="Yes", no="No"):
    return yes if v is True else no if v is False else "Unknown"

def page_network(c, d):
    pg = d["pages"]; cn = d.get("network") or {}
    y = ref_header(c, "G", "Care network and discharge referrals", "Primary care, CenterWell Primary Care (CW PCO) and CenterWell Pharmacy (CW Rx)",
                   d, "icon_phone.png")
    if d.get("discharge"):
        y -= discharge_banner(c, d, y) + 12
    if not cn:
        c.setFillColor(PANEL); c.rect(M, y - 50, CW, 50, fill=1, stroke=0)
        c.setFillColor(PLUM); c.setFont("Arial-Bold", 11); c.drawString(M + 14, y - 22, "No care network data for this patient")
        c.setFillColor(DARK_GRAY); c.setFont("Arial", 9.2); c.drawString(M + 14, y - 38, "Check PCP and CenterWell enrollment before discharge.")
        footer(c, pg["G"], d); return
    # referral indicator cards
    half = (CW - 14) / 2; st = ps(10, DARK_GRAY, lead=13)
    cards = [("Refer to CenterWell Primary Care (CW PCO)?", cn.get("refer_cwpco") or {}), ("Refer to CenterWell Pharmacy (CW Rx)?", cn.get("refer_cwrx") or {})]
    ch = max(Paragraph(T(r_.get("reason") or ""), st).wrap(half - 28, 999)[1] for _, r_ in cards) + 70
    for i, (t, r_) in enumerate(cards):
        bx = M + i * (half + 14); ind = r_.get("indicator") or "n/a"; col = IND_COLOR.get(ind, DARK_GRAY)
        c.setFillColor(PANEL); c.roundRect(bx, y - ch, half, ch, 8, fill=1, stroke=0)
        c.setFillColor(col); c.rect(bx, y - ch + 8, 5, ch - 16, fill=1, stroke=0)
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 10.5); c.drawString(bx + 16, y - 18, t)
        c.setFillColor(col); c.setFont("Arial-Bold", 20); c.drawString(bx + 16, y - 44, ind)
        para(c, r_.get("reason") or "", bx + 16, y - 54, half - 28, st)
    y -= ch + 16
    # facts table
    miles = cn.get("nearest_cwpco_miles")
    lv = cn.get("pcp_last_visit_days")
    facts = [("Primary care provider (PCP)", yn(cn.get("has_pcp"), "Yes", "No PCP on record"), cn.get("pcp_name") or ""),
             ("Last PCP visit", f"{lv} days ago" if lv is not None else "Unknown", "Over 6 months ago" if (lv or 0) > 180 else ""),
             ("Enrolled in CW PCO", yn(cn.get("cwpco_enrolled")), ""),
             ("Enrolled in CW Rx", yn(cn.get("cwrx_enrolled")), ""),
             ("Plan eligible (MA)", yn(cn.get("ma_eligible")), ""),
             ("Nearest CW PCO clinic", f"{miles:.1f} mi" if miles is not None else "Unknown", cn.get("nearest_cwpco_clinic") or ""),
             ("CW PCO clinic within 10 miles", yn((cn.get("cwpco_clinics_10mi") or 0) > 0 if cn.get("cwpco_clinics_10mi") is not None else None),
              f"{cn.get('cwpco_clinics_10mi')} clinic(s)" if cn.get("cwpco_clinics_10mi") is not None else ""),
             ("CW PCO clinic within 20 miles", yn((cn.get("cwpco_clinics_20mi") or 0) > 0 if cn.get("cwpco_clinics_20mi") is not None else None),
              f"{cn.get('cwpco_clinics_20mi')} clinic(s)" if cn.get("cwpco_clinics_20mi") is not None else ""),
             ("Active medications", str(cn.get("active_med_count")) if cn.get("active_med_count") is not None else "Unknown", "")]
    y = subhead(c, "Care network", y, 12)
    bad = lambda k, v: (k.startswith("Primary") and v != "Yes") or (k.startswith("Last PCP") and (lv or 0) > 180)
    rows = [[P(k, CELLB), P(v, ps(9, PLUM if bad(k, v) else DARK_BLUE, "Arial-Bold", 11)), P(n_)] for k, v, n_ in facts]
    y -= table(c, [[P(t, HEADC) for t in ["Item", "Status", "Detail"]]] + rows, M, y, [190, 120, CW - 310],
               extra=[("TOPPADDING", (0, 1), (-1, -1), 5), ("BOTTOMPADDING", (0, 1), (-1, -1), 5)]) + 14
    y = subhead(c, "How the referral indicator is set", y, 12)
    rules = ["<b>CW PCO:</b> Refer if not enrolled, plan-eligible and a clinic is within 10 miles (stronger when there is no PCP). "
             "Consider if the nearest clinic is 10-20 miles away. Not indicated if none within 20 miles or not eligible.",
             "<b>CW Rx:</b> Refer if not enrolled, plan-eligible and taking 5 or more active medications. "
             "Already enrolled: share the discharge medication list.",
             "<b>Check eligibility</b> appears when plan eligibility is unknown. Indicators are prompts for the discharge conversation, not orders."]
    for r_ in rules:
        c.setFillColor(DARK_BLUE); c.circle(M + 4, y - 6, 2, fill=1, stroke=0)
        y -= para(c, r_, M + 12, y, CW - 12, ps(9.2, lead=12), raw=True) + 4
    review_tip(c, d, ["r17"], ["PCP and enrollment match the member record", "the referral indicator makes sense for this patient",
                               "the patient or caregiver agrees before any referral"])
    footer(c, pg["G"], d)


# ---------------- PAGE A: context + clinical profile ----------------
def page_context(c, d):
    pg = d["pages"]
    y = ref_header(c, "A", "Patient context", "Who the patient is: demographics, diagnoses, living situation and 485 / OASIS profile",
                   d, "icon_checklist.png")
    y -= pointer(c, y, d, "Patient Profile & Risk Summary")
    y = subhead(c, "Patient context", y)
    prof = list(d["profile"].items())[:10] or [("Profile", "Not found")]
    lab_s, val_s = ps(8.8, font="Arial-Bold", lead=11), ps(9, lead=11)
    rows = []
    for i in range(0, len(prof), 2):
        a = prof[i]; b = prof[i + 1] if i + 1 < len(prof) else ("", "")
        rows.append([P(a[0], lab_s), P(a[1], val_s), P(b[0], lab_s), P(b[1], val_s)])
    y -= table(c, rows, M, y, [100, 160, 100, 160], header=False, extra=[("TOPPADDING", (0, 0), (-1, -1), 5),
                                                                          ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]) + 18
    y = clinical_profile_table(c, d, y)
    if d.get("discharge"):
        y -= 8; discharge_banner(c, d, y, network=False)
    review_tip(c, d, ["r02"], ["diagnoses, comorbidities and living situation match the chart",
                               "allergies, mental status and activities permitted are current on the 485"])
    footer(c, pg["A"], d)

# ---------------- PAGE A2: timeline + risk drivers ----------------
def page_timeline_risk(c, d, tmp):
    pg = d["pages"]; hd = d["header"]
    y = ref_header(c, "A", "Episode timeline and risk drivers", "Where the patient is in the episode, and why the model flags them",
                   d, "icon_checklist.png")
    y -= pointer(c, y, d, "Patient Profile & Risk Summary and risk tile")
    y = subhead(c, "Episode timeline", y)
    L = max(hd["episode_length"], 1); x0 = M + 10; x1 = W - M - 10
    ty = y - 48
    c.setStrokeColor(LIGHT_GRAY); c.setLineWidth(7); c.line(x0, ty, x1, ty)
    cur_x = x0 + (x1 - x0) * min(hd["episode_day"], L) / L
    c.setStrokeColor(DARK_BLUE); c.line(x0, ty, cur_x, ty)
    win = next((m[0] for m in d["timeline"] if m[1] == "Discharge window"), None)
    if win is not None:
        wx = x0 + (x1 - x0) * win / L
        c.setFillColor(HexColor("#E6F2D3")); c.rect(wx, ty - 8, x1 - wx, 16, fill=1, stroke=0)
        c.setStrokeColor(HexColor("#4E7D14")); c.setLineWidth(7); c.line(wx, ty, min(x1, max(wx, cur_x)), ty)
    occupied, marks = {}, []
    for day, lab, col, _ in sorted(d["timeline"], key=lambda m: (m[1] != "Run date", m[0])):
        mx = x0 + (x1 - x0) * day / L
        tw_ = pdfmetrics.stringWidth(f"{lab} (D{day})", "Arial-Bold", 8.2) + 6
        right = day == L or mx + tw_ > W - M
        span = (mx - tw_, mx) if right else (mx, mx + tw_)
        for lvl in (-1, 1, -2, 2, -3, 3):
            if all(span[1] < a0 or span[0] > a1 for a0, a1 in occupied.get(lvl, [])):
                occupied.setdefault(lvl, []).append(span); marks.append([day, lab, col, lvl, right]); break
    pos = lambda day, lvl: (x0 + (x1 - x0) * day / L, ty + 5 + 12 * lvl if lvl > 0 else ty - 7 + 12 * lvl)
    for day, lab, col, lvl, right in marks:
        mx, ly = pos(day, lvl); c.setStrokeColor(TL_COL[col]); c.setLineWidth(0.5); c.line(mx, ty, mx, ly + (0 if lvl > 0 else 8))
    for day, lab, col, lvl, right in marks:
        mx, ly = pos(day, lvl); cc = TL_COL[col]; c.setFillColor(cc); c.circle(mx, ty, 5, fill=1, stroke=0)
        txt = f"{lab} (D{day})"; f = "Arial-Bold" if lab == "Run date" else "Arial"
        c.setFont(f, 8.2); tw_ = pdfmetrics.stringWidth(txt, f, 8.2)
        c.setFillColor(white); c.rect((mx - tw_ - 1) if right else mx + 2, ly - 2, tw_ + 2, 10.5, fill=1, stroke=0)
        c.setFillColor(cc)
        if right: c.drawRightString(mx, ly, txt)
        else: c.drawString(mx + 3, ly, txt)
    y = ty - 56
    if d.get("discharge"):
        discharge_banner(c, d, y, network=False); y -= 52
    y = subhead(c, "Top drivers of 60-day hospitalization risk", y)
    bottom = review_tip(c, d, ["r03", "r11"] if d.get("discharge") else ["r03"],
                        ["the top drivers are clinically plausible for this patient",
                         "the prior inpatient stay and OASIS items match the chart"])
    p = os.path.join(tmp, f"_drivers_{hd['patient_id']}.png")
    ih = max(120, min(200, y - bottom - 6))
    chart_drivers(d, p, CW, ih)
    c.drawImage(ImageReader(p), M, y - ih, CW, ih, mask="auto"); os.remove(p)
    footer(c, pg["A2"], d)

# ---------------- PAGE B: care delivery grid ----------------
def page_care(c, d):
    pg = d["pages"]
    y = ref_header(c, "B", "Care delivery", "Ordered vs. delivered vs. scheduled visits by discipline and week", d, "icon_checklist.png")
    y -= pointer(c, y, d, "Care Delivery Summary and Delay Impact", "narratives2")
    g = d["care_grid"]; nW = g["weeks"]; cwk = g["current_week"]
    y = subhead(c, "Weekly visit frequency", y)
    ctr = lambda col=white, f="Arial-Bold", s=9: ps(s, col, f, align=1)
    hdr = [P("Discipline", HEADC)] + [P(f"Wk {i}", ctr()) for i in range(1, nW + 1)] + [P("Total", ctr()), P("Left / sched.", ctr())]
    data = [hdr]; ext = []
    for ri, (disc, v) in enumerate(g["rows"].items(), 1):
        row = [P(disc, CELLB)]; dt = ot = rem = sch = 0
        for wi in range(nW):
            o, dl, s = v["ordered"][wi], v["delivered"][wi], v["scheduled"][wi]
            if wi + 1 < cwk:
                ot += o; dt += dl or 0
                if o == 0 and not dl: row.append(P("-", ctr(MID_GRAY, "Arial")))
                else:
                    met = (dl or 0) >= o
                    row.append(P(f"{dl or 0}/{o}", ctr(OK_GREEN if met else PLUM)))
                    ext.append(("BACKGROUND", (wi + 1, ri), (wi + 1, ri), HexColor("#E6F2D3") if met else HexColor("#F7DCE8")))
            else:
                rem += o; sch += min(s, o)
                if o == 0: row.append(P("-", ctr(MID_GRAY, "Arial")))
                else:
                    short = s < o
                    row.append(P(f"{s} of {o}", ctr(PLUM if short else MID_GRAY, "Arial-Bold" if short else "Arial", 8.2)))
                    if short: ext.append(("BACKGROUND", (wi + 1, ri), (wi + 1, ri), HexColor("#FBEAF2")))
        row += [P(f"{dt}/{ot}", ctr(DARK_BLUE)), P(f"{rem} / {sch}", ctr(PLUM if sch < rem else OK_GREEN))]
        data.append(row)
    if not g["rows"]:
        data.append([P("-")] + [P("")] * (nW + 2))
    if 1 <= cwk - 1 <= nW: ext.append(("LINEAFTER", (cwk - 1, 0), (cwk - 1, -1), 1.8, LIGHT_BLUE))
    ext += [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 1), (-1, -1), 7), ("BOTTOMPADDING", (0, 1), (-1, -1), 7)]
    y -= table(c, data, M, y, [60] + [43 * 9 / nW] * nW + [44, 49], zebra=False, extra=ext) + 8
    c.setFont("Arial", 8.2); c.setFillColor(MID_GRAY)
    c.drawString(M, y - 4, f"Weeks 1-{cwk-1}: delivered/ordered (green = met, plum = short).  Weeks {cwk}-{nW}: scheduled of ordered.")
    c.drawString(M, y - 15, "Light blue rule = CQI run date.  Left / sched. = ordered visits remaining vs. visits on the calendar.")
    y -= 36
    st = g["status"]
    alert = lambda v: v not in ("on_track", "unknown", "scheduled", "No", "0")
    items = [("Frequency status", st["frequency_status"]), ("Schedule status", st["schedule_status"]),
             ("Acuity window missed", st["compliance_window_missed"]), ("Missed-care records", str(st["missed_care_records"]))]
    bw = (CW - 30) / 4
    txts = [v.replace("_", " ").capitalize() for _, v in items]
    vs = group_size(txts, "Arial-Bold", bw - 18, TS["value_l"], 9)
    for i, ((k, v), txt) in enumerate(zip(items, txts)):
        bx = M + i * (bw + 10)
        c.setFillColor(PANEL); c.rect(bx, y - 50, bw, 50, fill=1, stroke=0)
        c.setFillColor(DARK_GRAY); c.setFont("Arial", TS["label"]); c.drawString(bx + 9, y - 16, k.upper())
        c.setFillColor(PLUM if alert(v) else DARK_BLUE); c.setFont("Arial-Bold", vs); c.drawString(bx + 9, y - 37, txt)
    review_tip(c, d, ["r05", "r07"], ["ordered frequencies match the 485 / plan of care",
                                      "delivered counts match the visit record",
                                      "the future schedule is confirmed in the scheduling system today"])
    footer(c, pg["B"], d)

# ---------------- PAGE B2: missed care + events ----------------
def page_missed_events(c, d):
    pg = d["pages"]
    y = ref_header(c, "B", "Missed care and events", "Missed or outstanding visits, inpatient events, vitals alerts and client log notes",
                   d, "icon_phone.png")
    y -= pointer(c, y, d, "Missed Care Summary", "narratives2")
    y = subhead(c, "Missed and outstanding care", y)
    rows_, more = fit(d["missed_log"], 10)
    data = [[P(x, HEADC) for x in ["When", "Discipline", "Visit", "Reason / evidence", "Status"]]]
    for r in rows_: data.append([P(r[0]), P(r[1], CELLB), P(r[2]), P(r[3]), status_cell(r[4])])
    if not rows_: data.append([P("-"), P(""), P("No missed or outstanding care found"), P(""), P("")])
    y -= table(c, data, M, y, [70, 62, 140, 160, 88], extra=[("TOPPADDING", (0, 1), (-1, -1), 5), ("BOTTOMPADDING", (0, 1), (-1, -1), 5)]) + 4
    y -= more_note(c, y, more, "missed-care records") + 22
    y = subhead(c, "Inpatient events, vitals alerts and client log notes", y)
    rows_, more = fit(d["events"], 10)
    data = [[P(x, HEADC) for x in ["When", "Type", "Detail", "Source / follow-up"]]]
    for e in rows_:
        flag = e[4]
        data.append([P(e[0]), P(e[1], CELLB), P(e[2]), P(e[3], ps(8.8, PLUM if flag else DARK_GRAY, "Arial-Bold" if flag else "Arial", 10.6))])
    if not rows_: data.append([P("-"), P(""), P("No events, alerts or log notes found"), P("")])
    y -= table(c, data, M, y, [58, 96, 216, 150], extra=[("TOPPADDING", (0, 1), (-1, -1), 5), ("BOTTOMPADDING", (0, 1), (-1, -1), 5)]) + 4
    more_note(c, y, more, "events")
    review_tip(c, d, ["r06"], ["missed visits and reasons match the visit record and client logs",
                               "alerts flagged in plum had no documented physician notification"])
    footer(c, pg["B2"], d)

# ---------------- PAGE C: vitals ----------------
# ---------------- vector icons (drawn, so they stay crisp and on-brand) ----------------
def icon_star(c, cx, cy, r):
    import math as _m
    pts = []
    for i in range(10):
        a = _m.pi / 2 + i * _m.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * _m.cos(a), cy + rr * _m.sin(a)))
    p = c.beginPath(); p.moveTo(*pts[0])
    for q in pts[1:]: p.lineTo(*q)
    p.close()
    c.setFillColor(HexColor("#E6F2D3")); c.setStrokeColor(DARK_BLUE); c.setLineWidth(1.6); c.drawPath(p, fill=1, stroke=1)

def icon_warning(c, cx, cy, r):
    p = c.beginPath(); p.moveTo(cx, cy + r); p.lineTo(cx + r, cy - r * 0.8); p.lineTo(cx - r, cy - r * 0.8); p.close()
    c.setFillColor(PANEL); c.setStrokeColor(DARK_BLUE); c.setLineWidth(1.6); c.drawPath(p, fill=1, stroke=1)
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", r * 1.05); c.drawCentredString(cx, cy - r * 0.55, "!")

def icon_calendar(c, cx, cy, r):
    c.setFillColor(white); c.setStrokeColor(DARK_BLUE); c.setLineWidth(1.6)
    c.roundRect(cx - r, cy - r * 0.9, 2 * r, 1.8 * r, 2, fill=1, stroke=1)
    c.setFillColor(DARK_BLUE); c.rect(cx - r, cy + r * 0.45, 2 * r, r * 0.45, fill=1, stroke=0)
    for i in (-0.5, 0.5):
        c.line(cx + i * r, cy + r * 0.75, cx + i * r, cy + r * 1.1)
    c.setFillColor(GREEN)
    for row in range(2):
        for col in range(3):
            c.rect(cx - r * 0.7 + col * r * 0.55, cy - r * 0.15 - row * r * 0.45, r * 0.3, r * 0.25, fill=1, stroke=0)

def icon_heart(c, cx, cy, r):
    p = c.beginPath()
    p.moveTo(cx, cy - r * 0.9)
    p.curveTo(cx - r * 1.4, cy, cx - r * 0.9, cy + r * 1.1, cx, cy + r * 0.45)
    p.curveTo(cx + r * 0.9, cy + r * 1.1, cx + r * 1.4, cy, cx, cy - r * 0.9)
    c.setFillColor(PANEL); c.setStrokeColor(DARK_BLUE); c.setLineWidth(1.6); c.drawPath(p, fill=1, stroke=1)
    c.setStrokeColor(GREEN); c.setLineWidth(1.5)
    q = c.beginPath(); q.moveTo(cx - r * 1.1, cy); q.lineTo(cx - r * 0.35, cy); q.lineTo(cx - r * 0.15, cy + r * 0.45)
    q.lineTo(cx + r * 0.1, cy - r * 0.45); q.lineTo(cx + r * 0.3, cy); q.lineTo(cx + r * 1.1, cy)
    c.drawPath(q, fill=0, stroke=1)

def pref(d, key, fmt="page {}"):
    """Page reference that disappears in the short (brief) version, where most pages don't exist."""
    n = d["pages"].get(key)
    return fmt.format(n) if n else ""

TRAJ_LABEL = {"deteriorating": "Deteriorating", "stable": "Stable", "improving": "Improving", "insufficient_data": "Insufficient data"}
TRAJ_COLOR = {"deteriorating": PLUM, "stable": DARK_BLUE, "improving": OK_GREEN, "insufficient_data": MID_GRAY}


# ---------------- AT A GLANCE: from stated goal to what needs attention now ----------------
def page_glance(c, d):
    pg = d["pages"]; g = d["glance"]; tr = d["trajectory"]
    y = ref_header(c, None, "At a glance", "From the patient's stated goal to what needs attention now",
                   d, "icon_monitor.png", left_icon="icon_checklist.png")
    y -= 6
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 18)
    c.drawString(M, y - 18, "From stated goal to what needs attention now")
    y -= 34
    no_goal = d["patient_goal"].startswith("No patient-stated goal")
    rows = [("Patient goal", "No patient-stated goal recorded in the care plan" if no_goal else f'\u201c{d["patient_goal"]}\u201d',
             icon_star, HexColor("#EEF6E2"), ps(13, MID_GRAY if no_goal else NAVY, "Arial-Italic", 17)),
            ("Risk", g.get("risk"), icon_warning, PANEL, ps(11.5, DARK_GRAY, lead=15)),
            ("Care delivery", g.get("care_delivery"), icon_calendar, PANEL, ps(11.5, DARK_GRAY, lead=15)),
            ("Clinical trajectory", g.get("trajectory"), icon_heart, PANEL, ps(11.5, DARK_GRAY, lead=15))]
    lx, tx = M + 62, M + 222; tw = W - M - 16 - tx
    for lab, txt, ic, bg, st in rows:
        h = max(70, Paragraph(T(txt or ""), st).wrap(tw, 999)[1] + 30)
        c.setFillColor(bg); c.roundRect(M, y - h, CW, h, 6, fill=1, stroke=0)
        ic(c, M + 32, y - h / 2, 15)
        c.setFillColor(NAVY); c.setFont("Arial-Bold", 12); c.drawString(lx, y - h / 2 - 4, lab)
        para(c, txt or "Not available", tx, y - (h - Paragraph(T(txt or ""), st).wrap(tw, 999)[1]) / 2, tw, st)
        y -= h + 9
    # CQI flags bar
    fst = ps(12.5, white, "Arial-Bold", 16.5)
    fh = max(76, Paragraph(T(g.get("flags") or ""), fst).wrap(tw, 999)[1] + 28)
    c.setFillColor(DARK_BLUE); c.roundRect(M, y - fh, CW, fh, 6, fill=1, stroke=0)
    c.setFillColor(GREEN); c.setFont("Arial-Bold", 15); c.drawString(M + 22, y - fh / 2 - 5, "CQI flags")
    para(c, g.get("flags") or "", tx, y - (fh - Paragraph(T(g.get("flags") or ""), fst).wrap(tw, 999)[1]) / 2, tw, fst)
    y -= fh + 22
    # status strip: action window, trajectory, discharge, data gaps
    cls = tr.get("class") or "insufficient_data"
    items = [("Action window", tr.get("action_window") or "-", GREEN if cls != "deteriorating" else PLUM),
             ("Vitals trajectory", TRAJ_LABEL.get(cls, cls), TRAJ_COLOR.get(cls, DARK_BLUE)),
             ("Data gaps", str(len(d.get("data_gaps") or [])), PLUM if d.get("data_gaps") else DARK_BLUE),
             ("Discharge", (f"{(d.get('dc_rec') or {}).get('recommendation', 'Due')} \u00b7 {d['discharge']['days_remaining']}d") if d.get("discharge") else "Not due", PLUM if d.get("discharge") else DARK_BLUE)]
    bws = [CW * 0.36, CW * 0.2, CW * 0.2, CW - 30 - CW * 0.76]
    vs = min(group_size([v], "Arial-Bold", w_ - 20, 12, 8.5) for (_, v, _), w_ in zip(items, bws))
    bx = M
    for (lab, val, col), bw in zip(items, bws):
        c.setFillColor(PANEL); c.rect(bx, y - 46, bw, 46, fill=1, stroke=0)
        c.setFillColor(col); c.rect(bx, y - 46, 4, 46, fill=1, stroke=0)
        c.setFillColor(DARK_GRAY); c.setFont("Arial", TS["label"]); c.drawString(bx + 12, y - 15, lab.upper())
        c.setFillColor(col); c.setFont("Arial-Bold", vs); c.drawString(bx + 12, y - 34, val)
        bx += bw + 10
    y -= 60
    if d.get("network_line"):
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 9.5)
        c.drawString(M, y - 4, "Care network for discharge:  " + d["network_line"])
        y -= 18
    if pg.get("feedback"):
        review_tip(c, d, ["r15"], ["the goal is the patient's own wording", "each row matches the evidence pages",
                                   "the CQI flags are the right next steps"])
    footer(c, pg["glance"], d)


# ---------------- C: vitals action window ----------------
def chevrons(c, y, labels, h=58):
    """Process arrow like the CQI slide: blue chevrons, final one green. labels: [(title, value, value_color)]."""
    n = len(labels); gap = 6; tip = 16
    w = (CW - (n - 1) * gap + tip * 0) / n
    for i, (t, v, vc) in enumerate(labels):
        x0 = M + i * (w + gap); last = i == n - 1
        p = c.beginPath()
        p.moveTo(x0, y); p.lineTo(x0 + w - tip, y); p.lineTo(x0 + w, y - h / 2); p.lineTo(x0 + w - tip, y - h); p.lineTo(x0, y - h)
        if i: p.lineTo(x0 + tip, y - h / 2)
        p.close()
        c.setFillColor(GREEN if last else DARK_BLUE); c.drawPath(p, fill=1, stroke=0)
        tx = x0 + (tip + 6 if i else 10)
        st = ps(9.4, NAVY if last else white, "Arial-Bold", 11)
        vw = w - tip - (tip + 8 if i else 14)
        if t:
            c.setFillColor(NAVY if last else white); c.setFont("Arial-Bold", 8.6); c.drawString(tx, y - 18, t)
            para(c, v, tx, y - 23, vw, st)
        else:
            vh = Paragraph(T(v), st).wrap(vw, 999)[1]
            para(c, v, tx, y - (h - vh) / 2, vw, st)
    return h

def page_vitals(c, d, tmp=None):
    pg = d["pages"]; tr = d["trajectory"]; vm = d["vitals_meta"]; brief = d.get("brief")
    y = ref_header(c, "C", "Vitals trajectory: action window", "Weekly readings turned into a trajectory, an urgency and an action window",
                   d, "icon_phone.png")
    if not brief:
        y -= pointer(c, y, d, "Vitals Trajectory Summary")
    cls = tr.get("class") or "insufficient_data"
    urg = sorted([f[5] for f in d["vitals"]["findings"]], key=lambda u: {"Within 24 hrs": 0, "This week": 1}.get(u, 2))
    ctx = f"{vm['days']} vitals days" + (f", last DAY+{vm['last_day']}" if vm["last_day"] is not None else "")
    y -= chevrons(c, y, [("WEEKLY VITALS", ctx, None), ("TRAJECTORY", TRAJ_LABEL.get(cls, cls), None),
                         ("URGENCY", urg[0] if urg else "No vitals", None),
                         ("ACTION WINDOW", tr.get("action_window") or "-", None)]) + 16
    # What CQI saw / What CQI flags (slide format)
    st = ps(11.5, DARK_GRAY, lead=15); half = (CW - 14) / 2
    hh = max(Paragraph(T(tr.get(k) or ""), st).wrap(half - 28, 999)[1] for k in ("saw", "flags")) + 46
    for i, (lab, key) in enumerate((("What CQI saw", "saw"), ("What CQI flags", "flags"))):
        bx = M + i * (half + 14)
        c.setFillColor(PANEL); c.roundRect(bx, y - hh, half, hh, 8, fill=1, stroke=0)
        c.setFillColor(DARK_BLUE if i == 0 else (PLUM if cls == "deteriorating" else DARK_BLUE)); c.rect(bx, y - hh + 8, 4, hh - 16, fill=1, stroke=0)
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 12.5); c.drawString(bx + 14, y - 22, lab)
        para(c, tr.get(key) or "", bx + 14, y - 30, half - 28, st)
    y -= hh + 16
    y -= kpi_strip(c, y, [("Vitals days", vm["days"], NAVY),
                          ("Days since last reading", vm["since"] if vm["since"] is not None else "-", PLUM if (vm["since"] or 0) > 3 else NAVY),
                          ("Needing review", vm["urgent"], PLUM if vm["urgent"] else NAVY),
                          ("Trajectory", TRAJ_LABEL.get(cls, cls), TRAJ_COLOR.get(cls, NAVY))]) + 10
    y = subhead(c, "What CQI found, by measure", y, 12)
    data = [[P(x, HEADC) for x in ["Measure", "First", "Latest", "Change", "CQI interpretation", "Urgency"]]]
    for r in d["vitals"]["findings"][:6]:
        data.append([P(r[0], CELLB), P(r[1]), P(r[2]), P(r[3], CELLB), P(r[4]), status_cell(r[5])])
    if len(data) == 1: data.append([P("-"), P(""), P(""), P(""), P("No vitals findings"), P("")])
    table(c, data, M, y, [78, 40, 44, 72, 210, CW - 444], extra=[("TOPPADDING", (0, 1), (-1, -1), 5), ("BOTTOMPADDING", (0, 1), (-1, -1), 5)])
    if not brief:
        review_tip(c, d, ["r04"], ["readings match the visit documentation (units, dates, values)",
                                   "the trajectory and action window are clinically appropriate",
                                   f"the trend charts on page {pg['C2']} support what CQI saw"])
    else:
        para(c, "AI-assisted findings are drafts for clinical review. Full evidence and charts are in the full booklet.",
             M, FOOT_TOP + 22, CW, ps(8.6, MID_GRAY, "Arial-Italic", 11))
    footer(c, pg["C"], d)

def page_vitals_charts(c, d, tmp):
    pg = d["pages"]
    y = ref_header(c, "C", "Vitals trends", "Each measure against the patient's limits, by episode day", d, "icon_phone.png")
    y -= pointer(c, y, d, "vitals action window", "C")
    bottom = review_tip(c, d, ["r04"], ["readings match the visit documentation (units, dates, values)",
                                        "limits match the patient's ordered call parameters"])
    if d["vitals"]["day"]:
        p = os.path.join(tmp, f"_vitals_{d['header']['patient_id']}.png"); ih = min(430, y - bottom - 10)
        chart_vitals(d, p, CW, ih)
        c.drawImage(ImageReader(p), M, y - ih, CW, ih, mask="auto"); os.remove(p)
    else:
        c.setFillColor(PANEL); c.rect(M, y - 60, CW, 60, fill=1, stroke=0)
        c.setFillColor(PLUM); c.setFont("Arial-Bold", 12); c.drawString(M + 14, y - 26, "No vitals found for this episode")
        c.setFillColor(DARK_GRAY); c.setFont("Arial", 9.5)
        c.drawString(M + 14, y - 44, "This may be a monitoring or documentation gap. Confirm with the SN before conference.")
    footer(c, pg["C2"], d)


def _overview_vitals_page(c, items, branch, as_of, sample, page_label):
    """Second overview section (slide format): vitals trajectory -> action window, 3 cards per row."""
    img(c, "logo_cwhh.png", M, H - 78, h=40)
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 18); c.drawString(M, H - 122, "Vitals trajectory turns weekly readings into an action window")
    c.setFillColor(DARK_GRAY); c.setFont("Arial", 10)
    c.drawString(M, H - 140, f"{branch}  |  CQI run {as_of}  |  patients ordered by urgency")
    y = H - 156
    chevrons(c, y, [("", "Context + weekly vitals", None), ("", "Trajectory", None),
                    ("", "Urgency + escalation", None), ("", "Action window", None)], h=42)
    y -= 66
    cw3 = (CW - 20) / 3; st = ps(9.4, DARK_GRAY, lead=12); ch = 196
    for j, d in enumerate(items):
        col, row = j % 3, j // 3
        x = M + col * (cw3 + 10); top = y - row * (ch + 12)
        tr = d["trajectory"]; cls = tr.get("class") or "insufficient_data"
        c.setFillColor(HexColor("#E6F4FA")); c.roundRect(x, top - ch, cw3, ch, 10, fill=1, stroke=0)
        c.setFillColor(NAVY); c.setFont("Arial-Bold", 11); c.drawString(x + 12, top - 18, f"Patient {d['header']['patient_id']}")
        c.setFillColor(TRAJ_COLOR.get(cls, NAVY)); c.setFont("Arial-Bold", 8.4); c.drawString(x + 12, top - 31, TRAJ_LABEL.get(cls, cls).upper())
        yy = top - 46
        for lab, key in (("What CQI saw", "saw"), ("What CQI flags", "flags")):
            c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 9.6); c.drawString(x + 12, yy, lab)
            yy -= 5
            yy -= fit_para(c, tr.get(key) or "", x + 12, yy, cw3 - 24, 54, size=9.4, min_size=7.8) + 9
        chip(c, x + 12, top - ch + 10, tr.get("action_window") or "-", GREEN if cls != "deteriorating" else PLUM, 7)
    c.setFillColor(MID_GRAY); c.setFont("Arial-Italic", 8)
    c.drawString(M, 58, "AI-assisted findings are drafts for clinical review.")
    _ov_footer(c, sample)
    c.setFillColor(LIGHT_BLUE); c.setFont("Arial-Bold", 8.5); c.drawRightString(W - M, 46, page_label)
    c.showPage()

def _ov_footer(c, sample):
    c.setFont("Arial", 7.6); c.setFillColor(MID_GRAY); c.drawString(M, 42, BRAND.get("footer_note", ""))
    stage = "  \u00b7  ".join(t for t in [BRAND.get("stage_label", "Proof of concept"), BRAND.get("sample_label", "Sample data") if sample else ""] if t)
    if stage:
        c.setFont("Arial-Bold", 7.6); c.setFillColor(DARK_BLUE); c.drawString(M, 31, stage.upper())

# ---------------- BRANCH OVERVIEW: In their own words ----------------
def build_overview(items, out_path, branch, as_of, sample=False, brief=False):
    """Front pages for a merged branch booklet: one card per patient, slide format the customers liked.
    items: render dicts (cqi_payload.to_render), ordered as they appear in the booklet."""
    c = canvas.Canvas(out_path, pagesize=letter)
    c.setTitle(f"CQI - In their own words - {branch}")
    per = 8; qpages = max(1, -(-len(items) // per)); cw2 = (CW - 14) / 2
    URG = {"deteriorating": 0, "insufficient_data": 1, "stable": 2, "improving": 3}
    vitems = sorted(items, key=lambda d: URG.get(d["trajectory"].get("class") or "insufficient_data", 9))
    vper = 9; vpages = max(1, -(-len(vitems) // vper)); pages = qpages + vpages
    for pi in range(qpages):
        img(c, "logo_cwhh.png", M, H - 78, h=40)
        c.setFillColor(MID_GRAY); c.setFont("Arial-Bold", 8); c.drawRightString(W - M, H - 50, "PREPARED FOR")
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 10); c.drawRightString(W - M, H - 63, "CWHH CLINICAL QUALITY TEAM")
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 20); c.drawString(M, H - 122, "In their own words: what patients told us they want")
        c.setFillColor(DARK_GRAY); c.setFont("Arial", 10)
        c.drawString(M, H - 140, f"{branch}  |  CQI run {as_of}  |  {len(items)} high or rising-risk patients  |  "
                                 f"{'Short summaries' if brief else 'Full booklets'} follow in this order")
        y = H - 158
        for j, d in enumerate(items[pi * per:(pi + 1) * per]):
            col, row = j % 2, j // 2
            x = M + col * (cw2 + 14); top = y - row * 136; h = 126
            c.setFillColor(HexColor("#E6F4FA")); c.roundRect(x, top - h, cw2, h, 10, fill=1, stroke=0)
            c.setFillColor(GREEN); c.rect(x, top - h + 8, 5, h - 16, fill=1, stroke=0)
            hd, r = d["header"], d["risk"]
            c.setFillColor(DARK_GRAY); c.setFont("Arial", 8.6)
            c.drawString(x + 16, top - 16, f"Patient {hd['patient_id']}  |  {r['tier']} risk {r['score']:.2f}")
            if d.get("discharge"):
                chip(c, x + cw2 - 10, top - 20, "Discharge decision due", PLUM, 6.5, right=True)
            goal = d["patient_goal"]
            missing = goal.startswith("No patient-stated goal")
            q = f"\u201c{goal}\u201d" if not missing else "No patient-stated goal recorded"
            para(c, q, x + 16, top - 26, cw2 - 32, ps(12, NAVY if not missing else MID_GRAY, "Arial-Italic", 15))
            f = d["glance"].get("found") or ""
            para(c, f'<font color="#{DARK_BLUE.hexval()[2:]}"><b>What CQI found:</b></font> {T(f)}', x + 16, top - 76, cw2 - 32,
                 ps(9.6, DARK_GRAY, lead=12.4), raw=True)
        c.setFillColor(MID_GRAY); c.setFont("Arial-Italic", 8)
        c.drawString(M, 58, "Patient-stated goals from the care plan, wording unchanged. AI-assisted findings are drafts for clinical review.")
        _ov_footer(c, sample)
        c.setFillColor(LIGHT_BLUE); c.setFont("Arial-Bold", 8.5); c.drawRightString(W - M, 46, f"Overview {pi + 1} of {pages}")
        c.showPage()
    for vi in range(vpages):
        _overview_vitals_page(c, vitems[vi * vper:(vi + 1) * vper], branch, as_of, sample, f"Overview {qpages + vi + 1} of {pages}")
    c.save()
    return out_path

# ---------------- PAGE D: goals ----------------
def table_fit(c, header, rows, widths, x, ytop, bottom, empty_row, what, extra=None):
    """Draw as many rows as fit above `bottom`; note how many were left out. Returns height used."""
    n = len(rows)
    while n > 1:
        t = Table([header] + rows[:n], colWidths=widths); _, h = t.wrap(sum(widths), 4000)
        if ytop - h - (12 if n < len(rows) else 0) >= bottom:
            break
        n -= 1
    data = [header] + (rows[:n] if rows else [empty_row])
    if not rows:     # empty-state message spans the full table width
        data[1] = [Paragraph(T(next((e.text for e in empty_row if getattr(e, "text", "")), "None")), ps(8.8, MID_GRAY, "Arial-Italic", 10.6))] + [""] * (len(widths) - 1)
        extra = (extra or []) + [("SPAN", (0, 1), (-1, 1))]
    used = table(c, data, x, ytop, widths, extra=extra) + 4
    if rows and n < len(rows):
        used += more_note(c, ytop - used, len(rows) - n, what)
    return used


def kpi_strip(c, y, items):
    bw = (CW - (len(items) - 1) * 10) / len(items)
    vs = group_size([str(v) for _, v, _ in items], "Arial-Bold", bw - 20, TS["value_xl"], 10)
    for i, (lab, val, col) in enumerate(items):
        bx = M + i * (bw + 10)
        c.setFillColor(PANEL); c.rect(bx, y - 42, bw, 42, fill=1, stroke=0)
        c.setFillColor(DARK_GRAY); c.setFont("Arial", TS["label"]); c.drawString(bx + 10, y - 14, lab.upper())
        c.setFillColor(col); c.setFont("Arial-Bold", vs); c.drawString(bx + 10, y - 35, str(val))
    return 52


GOAL_HDR = lambda: [P(x, HEADC) for x in ["Goal", "Disc.", "CQI status", "Evidence found"]]
GOAL_W = [236, 42, 72, CW - 350]
goal_row = lambda g: [P(g[0]), P(g[1], CELLB), status_cell(g[2]), P(g[3])]


# ---------------- PAGE D: goals met / not met / therapy ----------------
def page_goals(c, d):
    pg = d["pages"]; goals = d["goals"]
    y = ref_header(c, "D", "Patient goals", "Goals met, goals not yet met, and therapy goals", d, "icon_checklist.png")
    gst = ps(11, DARK_BLUE, "Arial-Italic", 14)
    gq = f'"{d["patient_goal"]}"'; gh = Paragraph(T(gq), gst).wrap(CW - 28, 999)[1] + 30
    c.setFillColor(PANEL); c.rect(M, y - gh, CW, gh, fill=1, stroke=0)
    c.setFillColor(GREEN); c.rect(M, y - gh, 4, gh, fill=1, stroke=0)
    c.setFillColor(DARK_GRAY); c.setFont("Arial", TS["label"]); c.drawString(M + 14, y - 14, "PATIENT-STATED GOAL (care plan wording, unchanged)")
    para(c, gq, M + 14, y - 20, CW - 28, gst)
    y -= gh + 14
    met = [g for g in goals if g[2] == "Achieved"]
    not_met = [g for g in goals if g[2] != "Achieved"]
    therapy = [g for g in goals if g[1] in THERAPY]
    y -= kpi_strip(c, y, [("Goals met", len(met), OK_GREEN), ("Not yet met", len(not_met), PLUM if not_met else NAVY),
                          ("Delayed / missed", sum(g[2] in ("Delayed", "Missed") for g in goals), PLUM),
                          ("Therapy goals (PT/OT/ST)", len(therapy), NAVY)]) + 6
    bottom = review_tip(c, d, ["r09"], ["goal wording and status match the care plan",
                                        "'met' goals have documented evidence", "therapy goals match PT / OT / ST plans"])
    room = y - bottom
    blocks = [("Goals not yet met", not_met, "goals"), ("Goals met", met, "goals"), ("Therapy goals (PT / OT / ST)", therapy, "therapy goals")]
    for i, (title, rows, what) in enumerate(blocks):
        y = subhead(c, title, y, 12)
        # share remaining space so later blocks always get at least their header + 1 row
        reserve = sum(62 for _ in blocks[i + 1:])
        y -= table_fit(c, GOAL_HDR(), [goal_row(g) for g in rows], GOAL_W, M, y, bottom + reserve,
                       [P("None"), P(""), P(""), P("")], what) + 10
    footer(c, pg["D"], d)


# ---------------- PAGE E: care delivered ----------------
def page_care_delivered(c, d):
    pg = d["pages"]; cr = d["care_review"]
    y = ref_header(c, "E", "Care delivered", "Assessments and interventions performed, adverse events and visit narrative themes",
                   d, "icon_checklist.png")
    bottom = review_tip(c, d, ["r12", "r13"], ["counts match the visit documentation",
                                               "the summaries describe what was actually done, not what was ordered",
                                               "every adverse event is listed and correctly classified"])
    # bottom row: adverse events | visit themes (fixed block)
    bh = 150; by = bottom
    half = (CW - 14) / 2
    for i, title in enumerate(("Adverse event summary", "Visit narrative themes")):
        bx = M + i * (half + 14)
        c.setFillColor(HexColor("#FBEAF2") if i == 0 and cr["adverse_events"] else PANEL); c.rect(bx, by, half, bh, fill=1, stroke=0)
        c.setFillColor(PLUM if i == 0 else LIGHT_BLUE); c.rect(bx, by, 4, bh, fill=1, stroke=0)
        c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 10.5); c.drawString(bx + 12, by + bh - 16, title)
    ax, aw = M + 12, half - 22; ay = by + bh - 24
    used = fit_para(c, cr["adverse_events_summary"] or ("No adverse events recorded." if not cr["adverse_events"] else ""),
                    ax, ay, aw, 46, size=TS["body"] - 0.6, min_size=7.6) if (cr["adverse_events_summary"] or not cr["adverse_events"]) else 0
    ay -= used + 6
    for e in cr["adverse_events"][:4]:
        if ay < by + 14: break
        txt = f"<b>{T(e[0])}</b>  {T(e[1])}" + (f" ({T(e[2])})" if e[2] else "") + (f": {T(e[3])}" if e[3] else "")
        ay -= para(c, txt, ax, ay, aw, ps(8.4, lead=10.4), raw=True) + 3
    tx = M + half + 14 + 12; ty = by + bh - 24
    themes = cr["visit_themes"] or []
    if not themes:
        para(c, "No visit narrative themes were generated for this run.", tx, ty, aw, ps(8.8, MID_GRAY, "Arial-Italic"))
    for t_ in themes[:6]:
        if ty < by + 14: break
        c.setFillColor(LIGHT_BLUE); c.circle(tx + 3, ty - 5.5, 2, fill=1, stroke=0)
        ty -= para(c, f'"{t_}"', tx + 10, ty, aw - 10, ps(9, DARK_GRAY, "Arial-Italic", 11.2)) + 4
    top_bottom = by + bh + 14
    # assessments + interventions share the space above
    space = y - top_bottom
    for i, (title, summary, rows, hdr, widths, what) in enumerate([
        ("Assessments performed", cr["assessments_summary"],
         [[P(a[0], CELLB), P(a[1]), P(a[2], ps(8.8, align=1)), P(a[3], ps(8.8, align=1))] for a in cr["assessments"]],
         ["Assessment", "Disc.", "Count", "Last day"], [250, 90, 70, CW - 410], "assessments"),
        ("Interventions performed", cr["interventions_summary"],
         [[P(a[0], CELLB), P(a[1]), P(a[4] or "Not linked to a goal", ps(8.8, MID_GRAY if not a[4] else DARK_GRAY, lead=10.6)),
           P(a[2], ps(8.8, align=1)), P(a[3], ps(8.8, align=1))] for a in cr["interventions"]],
         ["Intervention", "Disc.", "Linked care-plan goal", "Count", "Last day"], [150, 50, 220, 50, CW - 470], "interventions")]):
        blk_bottom = y - space / 2 if i == 0 else top_bottom
        y -= section_band(c, M, y, CW, title) + 6
        y -= fit_para(c, summary, M + 2, y, CW - 4, 52, size=TS["body"] - 0.4, min_size=7.6) + 8
        y -= table_fit(c, [P(x, HEADC) for x in hdr], rows, widths, M, y, blk_bottom, [P("None recorded")] + [P("")] * (len(hdr) - 1), what) + 12
    footer(c, pg["E"], d)


# ---------------- PAGE F: care plan gaps ----------------
def page_care_gaps(c, d):
    pg = d["pages"]; ga = d["gap_analysis"]
    y = ref_header(c, "F", "Care plan gaps: what to add", "Interventions missing per goal, assessments or vitals to add, and visit goals to add",
                   d, "icon_checklist.png")
    bottom = review_tip(c, d, ["r14", "r10"], ["each missing item is truly missing (not just undocumented)",
                                               "suggested visit goals are measurable and fit the patient's goal",
                                               "medications and supplies match the current med profile"])
    sections = [
        ("Interventions missing per goal",
         [P(x, HEADC) for x in ["Care-plan goal", "Missing intervention", "Disc."]], [190, CW - 240, 50],
         [[P(r_["goal"]), P(r_["intervention"], CELLB), P(r_.get("discipline") or "", CELLB)] for r_ in ga["missing_interventions"]],
         "missing interventions"),
        ("Assessments or vitals to add",
         [P(x, HEADC) for x in ["Assessment / vital", "Type", "Why it is needed"]], [170, 70, CW - 240],
         [[P(r_["item"], CELLB), P(r_.get("type") or ""), P(r_.get("reason") or "")] for r_ in ga["missing_assessments"]],
         "assessments"),
        ("Visit goals to add to meet patient goals",
         [P(x, HEADC) for x in ["Disc.", "Visit goal", "Supports patient goal"]], [50, CW - 230, 180],
         [[P(r_.get("discipline") or "", CELLB), P(r_["visit_goal"]), P(r_.get("supports_goal") or "")] for r_ in ga["visit_goals"]],
         "visit goals"),
        ("Medications, supplies and equipment linked to goals",
         [P(x, HEADC) for x in ["Item", "Type", "Supports goal", "CQI alignment check"]], [150, 70, 140, CW - 360],
         [[P(a[0], CELLB), P(a[1]), P(a[2]), P(a[3], ps(8.8, PLUM if a[4] else DARK_GRAY, "Arial-Bold" if a[4] else "Arial", 10.6))]
          for a in d["alignment"]], "items"),
    ]
    for i, (title, hdr, widths, rows, what) in enumerate(sections):
        y = subhead(c, title, y, 12)
        if rows:
            chip(c, M + pdfmetrics.stringWidth(title, "Arial-Bold", 12) + 10, y + 5, f"{len(rows)}", PLUM if i < 3 else NAVY, 7.5)
        reserve = 50 * (len(sections) - i - 1)
        empty = [P("Not yet compared" if i == 3 else "None identified")] + [P("")] * (len(hdr) - 1)
        y -= table_fit(c, hdr, rows, widths, M, y, bottom + reserve, empty, what) + 8
    footer(c, pg["F"], d)

# ---------------- FEEDBACK (fillable) ----------------
def page_feedback(c, d):
    pg = d["pages"]
    y = ref_header(c, None, "Clinician feedback (fillable)", "Complete on screen, save and return. One form per patient; answers are collected automatically.",
                   d, "icon_monitor.png", left_icon="icon_checklist.png")
    # how-to
    txt = ("<b>How to complete:</b> open this PDF in Adobe Acrobat Reader, Microsoft Edge or Chrome, click the circles and boxes, "
           "type comments, then save (Ctrl+S) and return it to the " + T(d["header"].get("produced_by") or "CQI team") + ". "
           "<b>Tip:</b> rate at least the findings you discussed. For <i>Partly</i> or <i>Disagree</i>, tick an error type and add one line "
           "saying what was wrong - that is what improves CQI.")
    st = ps(8.8, lead=11.4); th = Paragraph(txt, st).wrap(CW - 24, 999)[1] + 14
    c.setFillColor(PANEL); c.rect(M, y - th, CW, th, fill=1, stroke=0)
    c.setFillColor(GREEN); c.rect(M, y - th, 4, th, fill=1, stroke=0)
    para(c, txt, M + 14, y - 7, CW - 24, st, raw=True)
    y -= th + 14
    # ratings table (drawn grid, then form fields on top)
    def pg_list(keys):
        ns_ = sorted(pg[p] for p in keys if p in pg)
        return f"{ns_[0]}-{ns_[-1]}" if len(ns_) > 2 and ns_[-1] - ns_[0] == len(ns_) - 1 else ", ".join(map(str, ns_))
    rows = [(k, lab, pg_list(pages)) for k, lab, pages in FEEDBACK_FINDINGS if any(p in pg for p in pages)]
    widths = [134, 36, 36, 36, 46, 72, CW - 360]
    hdr = [P(x, ps(8.2, white, "Arial-Bold", lead=9.6, align=0 if i in (0, 6) else 1), raw=True) for i, x in
           enumerate(["CQI finding", "Pages", "Agree", "Partly", "Disagree", "Error type<br/>D&nbsp;&nbsp;&nbsp;&nbsp;I&nbsp;&nbsp;&nbsp;&nbsp;C&nbsp;&nbsp;&nbsp;&nbsp;N", "Comment (what was wrong or missing)"])]
    RH, HH = 16.5, 24
    data = [hdr] + [[P(lab, ps(8.6, font="Arial-Bold", lead=10)), P(pgs, ps(8.4, align=1)), "", "", "", "", ""] for _, lab, pgs in rows]
    t = Table(data, colWidths=widths, rowHeights=[HH] + [RH] * len(rows))
    sty = [("BACKGROUND", (0, 0), (-1, 0), DARK_BLUE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("GRID", (0, 0), (-1, -1), 0.4, LIGHT_GRAY), ("LEFTPADDING", (0, 0), (-1, -1), 5)]
    for r in range(2, len(data), 2): sty.append(("BACKGROUND", (0, r), (-1, r), HexColor("#F2F9FC")))
    t.setStyle(TableStyle(sty))
    _, tht = t.wrap(CW, 4000); t.drawOn(c, M, y - tht)
    xcol = [M + sum(widths[:i]) for i in range(len(widths) + 1)]
    for i, (k, lab, _) in enumerate(rows):
        cy = y - HH - RH * i - RH / 2
        for j, v in zip((2, 3, 4), ("agree", "partly", "disagree")):
            f_radio(c, d, f"{k}_rating", v, (xcol[j] + xcol[j + 1]) / 2 - 5.5, cy - 5.5, 11, f"{lab}: {v}")
        for m_, e in enumerate("DICN"):
            f_check(c, d, f"{k}_err_{e}", xcol[5] + 7 + m_ * 16, cy - 5, 10, f"{lab}: {ERROR_TYPES[e]}")
        f_text(c, d, f"{k}_comment", xcol[6] + 3, cy - 6.5, widths[6] - 6, 13, tip=f"{lab}: comment")
    y -= tht + 6
    c.setFont("Arial", 7.8); c.setFillColor(MID_GRAY)
    c.drawString(M, y - 6, "Error type:  D = data missing   I = data incorrect   C = clinical interpretation   N = not relevant for this patient")
    y -= 24
    # outcome
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 11); c.drawString(M, y - 10, "Conference outcome (tick all that apply)")
    y -= 24; xx = M
    for k, lab in [("out_plan", "Plan updated"), ("out_visits", "Visits rescheduled"), ("out_md", "MD notified"),
                   ("out_nochange", "No change needed"), ("out_ds", "Refer to Data Science")]:
        xx += labelled_check(c, d, k, xx, y, lab, 8.8)
    y -= 26
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 11); c.drawString(M, y - 10, "How useful was this booklet for conference?")
    y -= 28; xx = M
    for v, lab in [("1", "1  Not useful"), ("2", "2"), ("3", "3"), ("4", "4"), ("5", "5  Very useful")]:
        xx += labelled_radio(c, d, "usefulness", v, xx, y, lab, 8.8)
    y -= 26
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 11); c.drawString(M, y - 10, "What should CQI add, remove or change?")
    y -= 16
    ch = max(26, y - FOOT_TOP - 50)
    f_text(c, d, "overall_comment", M, y - ch, CW, ch, multiline=True, size=9, tip="Overall comments")
    y -= ch + 22
    third = (CW - 20) / 3
    for i, (k, lab) in enumerate([("reviewer_name", "Reviewer name"), ("reviewer_role", "Role / discipline"), ("review_date", "Date")]):
        x = M + i * (third + 10)
        c.setFillColor(DARK_GRAY); c.setFont("Arial", 8); c.drawString(x, y + 3, lab)
        f_text(c, d, k, x, y - 18, third, 17, tip=lab)
    footer(c, pg["feedback"], d)

# ---------------- PAGE F ----------------
def page_methods(c, d):
    y = ref_header(c, None, "How CQI produced this summary", "Methods, data sources and definitions: what to trust and what to check", d, "icon_monitor.png", left_icon="icon_checklist.png")
    y = subhead(c, "From data to findings", y)
    steps = [("1  Predict", "Production 60-day hospitalization risk model scores high and rising-risk patients each week and explains top drivers."),
             ("2  Compare", "Rules compare ordered, scheduled and delivered visits by discipline and week, timing windows and vitals limits."),
             ("3  Read", f"Language models read goals, care plans, notes and logs to draft the summaries on pages {d['pages']['narratives']}-{d['pages']['narratives2']}."),
             ("4  Check", "AI drafts are checked by a panel of LLM judges and adjudicated. Clinician feedback from this pilot will guide improvements.")]
    sw = (CW - 30) / 4
    for i, (t, txt) in enumerate(steps):
        sx = M + i * (sw + 10)
        c.setFillColor(GREEN if i == 3 else DARK_BLUE); c.rect(sx, y - 26, sw, 26, fill=1, stroke=0)
        c.setFillColor(white); c.setFont("Arial-Bold", 11); c.drawString(sx + 10, y - 17, t)
        c.setFillColor(PANEL); c.rect(sx, y - 118, sw, 92, fill=1, stroke=0)
        para(c, txt, sx + 8, y - 34, sw - 16, ps(9.2, lead=11.8))
    y -= 138
    y = subhead(c, "Data sources", y)
    for s in ["Home Health operational data on the CW Lighthouse AI environment: admissions and episodes, visits, 485 plan of care and orders, "
              "vitals, assessments, interventions, OASIS, inpatient events, events, client logs, goals, notes, diagnoses and procedures, "
              "referrals, PDGM periods, supplies, medications, care-plan tasks, wounds.",
              "Model outputs: CWHH 60-day transfer-to-inpatient risk scores and risk drivers."]:
        y -= para(c, s, M, y, CW, ps(10, lead=13.2)) + 6
    y -= 10
    y = subhead(c, "Definitions", y)
    defs = [("High / rising risk", "High: top 25% of weekly risk scores. Rising: weekly score increasing versus prior runs."),
            ("Frequency status", "on_track, minor_shortfall, behind or significantly_behind versus ordered visits to date."),
            ("Schedule status", "Whether remaining ordered visits are on the calendar (e.g. no_future_schedule, needs_change)."),
            ("Risk raised by care gaps", "CQI judgment that missed or delayed care materially increased risk."),
            ("Delay clinical impact", "Estimated clinical impact of timing delays: low, moderate or high."),
            ("Nearing discharge decision", "Active episode within 14 days of the episode end date."),
            ("Urgency", "Within 24 hrs, this week, or next visit - a recommendation for the team, not an order.")]
    y -= table(c, [[P(k, CELLB), P(v)] for k, v in defs], M, y, [150, CW - 150], header=False,
               extra=[("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]) + 16
    y = subhead(c, "Known limits", y)
    for lt in ["Findings reflect data available at the run date; documentation lag can look like a care gap.",
               "Missing goals or vitals may be a documentation gap rather than a care gap - check the chart.",
               "Recommendations are drafts for clinical review."]:
        c.setFillColor(DARK_BLUE); c.circle(M + 4, y - 6, 2, fill=1, stroke=0)
        y -= para(c, lt, M + 12, y, CW - 12, ps(10, lead=13)) + 4
    ph = 50; y0 = FOOT_TOP + 6
    c.setFillColor(PANEL); c.rect(M, y0, CW, ph, fill=1, stroke=0)
    img(c, "icon_phone.png", M + 12, y0 + 9, w=32, h=32)
    c.setFillColor(DARK_BLUE); c.setFont("Arial-Bold", 12); c.drawString(M + 54, y0 + 30, "Questions or corrections")
    c.setFont("Arial", 10); c.setFillColor(DARK_GRAY)
    c.drawString(M + 54, y0 + 14, f"{d['header'].get('produced_by') or ''}  |  For more information visit go/lighthouse")
    footer(c, d["pages"]["methods"], d)

# ---------------- PAGE G (fillable notes) ----------------
def build(payload, out_path, tmp=None, brief=False):
    """payload: path to JSON, or a dict (payload v1.x or internal render dict).
    brief=True -> short customer version: cover, at a glance, vitals action window (3 pages, no forms)."""
    d = json.load(open(payload)) if isinstance(payload, str) else payload
    if "schema_version" in d:
        sys.path.insert(0, HERE)
        import cqi_payload
        d = cqi_payload.to_render(cqi_payload.compose(d))
    d = dict(d)
    if brief:
        from cqi_payload import BRIEF_PAGES, page_numbers
        d["pages"] = {k: i + 1 for i, k in enumerate(k_ for k_ in BRIEF_PAGES if k_ in d["pages"])}; d["brief"] = True; d["total"] = len(d["pages"])
    tmp = tmp or os.path.dirname(os.path.abspath(out_path))
    c = canvas.Canvas(out_path, pagesize=letter)
    c.setTitle(f"CQI Patient Conference {'Summary' if brief else 'Booklet'} - Patient {d['header']['patient_id']}")
    c.setAuthor(d["header"].get("produced_by") or "CW Data Science & Applied AI - HH Team")
    c.setSubject("Clinical Quality Intelligence (CQI)")
    global TOTAL
    TOTAL = d.get("total") or len(d["pages"])
    pages = {"cover": lambda: page_cover(c, d), "about": lambda: page_about(c, d), "guide": lambda: page_guide(c, d),
             "glance": lambda: page_glance(c, d), "summary": lambda: page_summary(c, d),
             "narratives": lambda: page_narratives(c, d), "narratives2": lambda: page_narratives2(c, d),
             "A": lambda: page_context(c, d), "A2": lambda: page_timeline_risk(c, d, tmp), "B": lambda: page_care(c, d),
             "B2": lambda: page_missed_events(c, d), "C": lambda: page_vitals(c, d, tmp), "C2": lambda: page_vitals_charts(c, d, tmp),
             "D": lambda: page_goals(c, d), "E": lambda: page_care_delivered(c, d), "F": lambda: page_care_gaps(c, d),
             "DX": lambda: page_diagnoses(c, d), "W": lambda: page_wounds(c, d), "OA": lambda: page_oasis(c, d),
             "G": lambda: page_network(c, d), "DC": lambda: page_discharge(c, d), "M": lambda: page_meds(c, d),
             "feedback": lambda: page_feedback(c, d), "methods": lambda: page_methods(c, d), "notes": lambda: page_notes(c, d)}
    pages.update({"divA": lambda: page_divider(c, d, "analysis"), "divE": lambda: page_divider(c, d, "evidence"),
                  "divI": lambda: page_divider(c, d, "input")})
    for key in sorted(d["pages"], key=d["pages"].get):
        d["_key"] = key; pages[key](); c.showPage()
    c.save()
    return out_path


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "CQI_Patient_Conference_Booklet.pdf")
