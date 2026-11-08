"""Splash screen + CenterWell header for Project Alpha (same pattern as clinician_copilot: leaf pops in,
lines type in one by one, a closing thought, then the splash fades; click anywhere to skip).

Story = the CQI vision from the booklet (cqi_booklet/config/branding.yaml): an AI-assisted agent that scans
operational data on clinicians' behalf, surfaces care gaps as they emerge, and brings the right patient to the
right clinician while there is still time to act. Clinicians stay in charge of every decision.
"""
from __future__ import annotations

import os
import re

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIME, SKY, GREEN = "#97D700", "#00B2E3", "#78BE20"

# Monochrome line icons (inline SVG, stroke = currentColor) - render the same on every machine.
_ICON = {
    "radar": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><path d="M12 12 19 5"/>',
    "gap": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><circle cx="12" cy="17" r=".6" fill="currentColor"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/><path d="M12 3V1M12 23v-2M3 12H1M23 12h-2"/>',
    "home": '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h5v-6h4v6h5V10"/>',
    "chat": '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4.4A8 8 0 1 1 21 12z"/><path d="M8.5 11h7M8.5 14h4"/>',
    "shield": '<path d="M12 3 4.5 6v6c0 4.4 3.2 7.9 7.5 9 4.3-1.1 7.5-4.6 7.5-9V6z"/><path d="m9 12 2 2 4-4"/>',
    "bulb": '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
    "close": '<path d="M18 6 6 18M6 6l12 12"/>',
}


def icon(name, size=18):
    return (f'<svg class="pa-ico" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_ICON[name]}</svg>')


LINES = [
    ("radar", "<b>CQI Agent</b> — scans operational data every week on clinicians’ behalf: visits, vitals, OASIS, goals, meds"),
    ("gap", "<b>Care gaps</b> — unbooked orders, missed visits, unescalated alerts, goals falling behind"),
    ("target", "<b>Prioritised</b> — the right patient to the right clinician, while there is still time to act"),
    ("home", "<b>Discharge readiness</b> — discharge, refer or recertify, with the evidence on both sides"),
    ("chat", "<b>Ask Alpha</b> — one question, the answer unfolds, every fact cited"),
]
SLOT = 2.4          # seconds per typed line
START = 1.5
STACK_END = START + len(LINES) * SLOT
FINAL = 4.4
TOTAL = STACK_END + FINAL


def _vision_note():
    try:
        with open(os.path.join(ROOT, "cqi_booklet", "config", "branding.yaml"), encoding="utf-8") as fh:
            return (yaml.safe_load(fh) or {}).get("vision_note") or "Clinicians stay in charge of every decision."
    except OSError:
        return "Clinicians stay in charge of every decision."


def splash(leaf_src, agent_stats=None):
    spans = "".join(f'<span style="animation-delay:{START + i * SLOT:.1f}s"><em class="pa-hl-e">{icon(ic, 17)}</em>'
                    f'<i class="pa-hl-t">{txt}</i></span>' for i, (ic, txt) in enumerate(LINES))
    note = re.sub(r"(every decision)", r"<u>\1</u>", _vision_note())
    live = ""
    if agent_stats:
        live = (f'<div class="pa-final-live" style="animation-delay:{STACK_END + 2.2:.1f}s">This week the agent scanned '
                f'<b>{agent_stats["episodes_scanned"]}</b> episodes and found <b>{agent_stats["opportunities"]}</b> care opportunities '
                f'— <b>{agent_stats["within_24h"]}</b> need action within 24 hours.</div>')
    return f"""
<div class="pa-splash" style="animation-delay:{TOTAL:.1f}s" onclick="this.classList.add('pa-skip')">
  <img src="{leaf_src}" alt="CenterWell">
  <div class="pa-splash-title">CenterWell <span>Project Alpha</span></div>
  <div class="pa-splash-tag">Clinical Quality Intelligence for the weekly Patient Conference</div>
  <div class="pa-splash-app">powered by the CQI agent &ndash; Alpha for CWHH Clinical Quality Team</div>
  <div class="pa-splash-story">
    <div class="pa-splash-hl" style="animation-delay:{STACK_END:.1f}s">{spans}</div>
    <div class="pa-splash-final" style="animation-delay:{STACK_END + 0.3:.1f}s">
      <div>Which patient needs care <b>today</b> — before it’s too late?</div>
      <div class="pa-final-sub" style="animation-delay:{STACK_END + 1.1:.1f}s">{note}</div>
      {live}
    </div>
  </div>
  <div class="pa-splash-bar"><span style="animation-duration:{TOTAL:.1f}s"></span></div>
  <div class="pa-splash-dots"><span></span><span></span><span></span></div>
  <div class="pa-splash-skip">click anywhere to skip</div>
</div>"""


def header(leaf_src, run_label, sample):
    badge = '<span class="pa-demo-badge">PROOF OF CONCEPT · SAMPLE DATA</span>' if sample else '<span class="pa-demo-badge">PROOF OF CONCEPT</span>'
    return f"""
<div class="pa-header">
  <img class="pa-logo-img" src="{leaf_src}" alt="CenterWell">
  <div style="flex:1">
    <div class="pa-wordmark">CenterWell <span class="pa-lh">Project Alpha</span> {badge}</div>
    <div class="pa-app">Clinical Quality Intelligence · Weekly Patient Conference</div>
    <div class="pa-powered">powered by the CQI agent &ndash; Alpha for CWHH Clinical Quality Team</div>
  </div>
  <button class="pa-guide-btn" title="User guide" aria-label="Open the user guide"
          onclick="document.getElementById('pa-guide').classList.add('open')">{icon('bulb', 20)}<span>Guide</span></button>
  <div class="pa-run">{icon('shield', 15)} CQI run {run_label}</div>
</div>"""


def guide_overlay(md_path):
    """docs/USER_GUIDE.md rendered as an in-app overlay (opened by the lightbulb in the header).
    The coordinator setup section is left out - it is for whoever runs the app, not for conference users."""
    try:
        from markdown_it import MarkdownIt
        with open(md_path, encoding="utf-8") as fh:
            md = fh.read()
        md = md.split("### For the coordinator")[0].rstrip().rstrip("-").rstrip()
        body = MarkdownIt("commonmark").enable("table").render(md)
    except Exception as ex:  # guide missing or renderer unavailable - keep the app working
        body = f"<p>The user guide could not be loaded ({type(ex).__name__}). See docs/USER_GUIDE.md.</p>"
    close = "document.getElementById('pa-guide').classList.remove('open')"
    return f"""
<div id="pa-guide" class="pa-guide" onclick="if(event.target===this){{{close}}}">
  <div class="pa-guide-card">
    <button class="pa-guide-close" title="Close" aria-label="Close the user guide" onclick="{close}">&#10005;</button>
    <div class="pa-guide-head">{icon('bulb', 22)} <span>How to use Project Alpha</span></div>
    <div class="pa-guide-body">{body}</div>
  </div>
</div>"""


def agent_pill(st):
    if not st:
        return "<div class='pa-agent off'>● CQI agent has not run for this date</div>"
    return (f"<div class='pa-agent'><span class='pa-pulse'></span>{icon('radar', 15)} CQI agent scanned <b>{st['episodes_scanned']}</b> episodes · "
            f"<b>{st['opportunities']}</b> care opportunities · <b class='hot'>{st['within_24h']}</b> within 24 h · {st['new']} new</div>")


CSS = f"""
/* ---------- header (as clinician_copilot) ---------- */
.pa-header {{ display:flex; align-items:center; gap:16px; padding: 14px 2px 4px; }}
.pa-logo-img {{ height: 56px; width:auto; }}
.pa-wordmark {{ font-size: 28px; font-weight: 700; color:#fff !important; letter-spacing:.3px; font-family:'Segoe UI','Trebuchet MS',sans-serif !important; }}
.pa-wordmark .pa-lh {{ color: {LIME} !important; font-family: inherit !important; }}
.pa-demo-badge {{ font-size: 10.5px; font-weight: 700; letter-spacing: 1px; vertical-align: middle; margin-left: 8px; padding: 3px 9px;
  border-radius: 999px; border: 1px solid #FFB74D; color:#FFB74D !important; font-family: var(--font) !important; }}
.pa-app {{ font-size: 13.5px; color: #c9d7ff !important; }}
.pa-powered {{ font-size: 10.5px; letter-spacing: .4px; color: #9fb4e8 !important; }}
.pa-run {{ display:flex; align-items:center; gap:6px; font-size: 12.5px; color:#c9d7ff !important; border:1px solid rgba(255,255,255,.25);
  border-radius: 999px; padding: 6px 12px; white-space: nowrap; }}
.pa-ico {{ vertical-align: -3px; }}
/* ---------- user guide (lightbulb, top right) ---------- */
.pa-guide-btn {{ display:flex; align-items:center; gap:6px; cursor:pointer; background: rgba(151,215,0,.12); border:1px solid rgba(151,215,0,.6);
  color:{LIME} !important; border-radius: 999px; padding: 6px 13px 6px 10px; font-size: 13px; font-weight: 600; white-space: nowrap; }}
.pa-guide-btn span {{ color:{LIME} !important; }}
.pa-guide-btn:hover {{ background: rgba(151,215,0,.25); box-shadow: 0 0 14px rgba(151,215,0,.35); }}
.pa-guide {{ position: fixed; inset: 0; z-index: 9000; background: rgba(0,12,45,.72); display:none; align-items:flex-start; justify-content:center;
  padding: 4vh 16px; backdrop-filter: blur(3px); }}
.pa-guide.open {{ display:flex; animation: pa-rise .25s ease both; }}
.pa-guide-card {{ position: relative; width: min(920px, 100%); max-height: 92vh; overflow-y: auto; background: #0A3BA8; border: 1px solid rgba(255,255,255,.3);
  border-radius: 18px; padding: 22px 30px 26px; box-shadow: 0 20px 60px rgba(0,0,0,.45); }}
.pa-guide-close {{ position: sticky; top: 0; float: right; cursor:pointer; background: rgba(255,255,255,.1); border:1px solid rgba(255,255,255,.3);
  color:#fff !important; border-radius: 50%; width: 34px; height: 34px; display:flex; align-items:center; justify-content:center; font-size: 16px; line-height: 1; }}
.pa-guide-close:hover {{ border-color: {LIME}; }}
.pa-guide-head {{ display:flex; align-items:center; gap:10px; font-size: 22px; font-weight: 700; color:{LIME} !important; margin-bottom: 6px; }}
.pa-guide-head span {{ color:#fff !important; }}
.pa-guide-body {{ font-size: 14.5px; line-height: 1.6; color:#e8efff !important; }}
.pa-guide-body * {{ color:#e8efff !important; }}
.pa-guide-body h1 {{ display:none; }}
.pa-guide-body h2 {{ font-size: 18px; margin: 22px 0 8px; padding-top: 12px; border-top: 1px solid rgba(255,255,255,.15); color:{LIME} !important; }}
.pa-guide-body h3 {{ font-size: 15.5px; margin: 16px 0 6px; color:{SKY} !important; }}
.pa-guide-body strong, .pa-guide-body b {{ color:#fff !important; }}
.pa-guide-body em {{ color:#cfe0ff !important; }}
.pa-guide-body blockquote {{ margin: 10px 0; padding: 8px 14px; border-left: 3px solid #FFB74D; background: rgba(255,183,77,.08); border-radius: 0 8px 8px 0; }}
.pa-guide-body table {{ border: none !important; width:100%; border-collapse: collapse; margin: 8px 0 12px; font-size: 13.5px; }}
.pa-guide-body th {{ text-align:left; color:#b9c9f2 !important; font-size: 12px; text-transform: uppercase; letter-spacing:.4px; border-bottom: 1px solid rgba(255,255,255,.25);
  padding: 6px 8px; background: transparent !important; border-left:none !important; border-right:none !important; border-top:none !important; }}
.pa-guide-body td {{ padding: 7px 8px; border-bottom: 1px solid rgba(255,255,255,.08); vertical-align: top; background: transparent !important; border-left:none !important; border-right:none !important; }}
.pa-guide-body code {{ background: rgba(255,255,255,.1); border-radius: 5px; padding: 1px 5px; font-size: 12.5px; }}
.pa-guide-body ul, .pa-guide-body ol {{ padding-left: 22px; }} .pa-guide-body li {{ margin: 4px 0; }}
.pa-guide-body hr {{ border: none; border-top: 1px solid rgba(255,255,255,.15); }}
.pa-agent {{ display:inline-flex; align-items:center; gap:8px; margin: 0 auto; padding: 7px 16px; border-radius: 999px; font-size: 13.5px;
  background: rgba(151,215,0,.10); border: 1px solid rgba(151,215,0,.45); color:#e8efff !important; animation: rise .7s ease .15s both; }}
.pa-agent b {{ color:#fff !important; }} .pa-agent b.hot {{ color:#FF8A80 !important; }} .pa-agent.off {{ border-color: rgba(255,255,255,.25); background: transparent; }}
.pa-agent-wrap {{ text-align:center; margin-top: 7vh; }}
.pa-pulse {{ width:8px; height:8px; border-radius:50%; background:{LIME}; box-shadow: 0 0 0 0 rgba(151,215,0,.7); animation: pulse 1.8s infinite; }}
@keyframes pulse {{ 70% {{ box-shadow: 0 0 0 9px rgba(151,215,0,0); }} 100% {{ box-shadow: 0 0 0 0 rgba(151,215,0,0); }} }}
.pa-q {{ text-align:center; font-size: 44px; font-weight: 750; letter-spacing: -1.2px; margin-top: 18px; line-height:1.1; animation: rise .7s ease .25s both; }}
.pa-q span {{ color: {LIME} !important; }}
.pa-q-sub {{ text-align:center; font-size: 16px; color: #b9c9f2 !important; margin-top: 8px; animation: rise .7s ease .35s both; }}
/* ---------- splash ---------- */
.pa-splash {{ position: fixed; inset: 0; z-index: 99999; background: radial-gradient(1200px 700px at 50% -10%, #0a47c4 0%, #0033A1 45%, #00257F 100%);
  display:flex; flex-direction:column; align-items:center; justify-content:center; gap: 6px; animation: pa-out .9s ease forwards; cursor: pointer; }}
@keyframes pa-out {{ to {{ opacity:0; visibility:hidden; }} }}
.pa-splash.pa-skip {{ animation-delay: 0s !important; animation-duration: .45s !important; pointer-events: none; }}
.pa-splash img {{ height: 116px; width:auto; animation: pa-pop 1.1s cubic-bezier(.2,1.4,.4,1) both; filter: drop-shadow(0 8px 28px rgba(0,178,227,.4)); }}
@keyframes pa-pop {{ from {{ opacity:0; transform: scale(.5) rotate(-12deg); }} }}
.pa-splash-title {{ font-size: 40px; font-weight: 700; color:#fff !important; font-family:'Segoe UI','Trebuchet MS',sans-serif !important; margin-top: 14px; animation: pa-rise .8s ease .35s both; }}
.pa-splash-title span {{ color: {LIME} !important; font-family: inherit !important; }}
.pa-splash-tag {{ font-size: 16px; color:#fff !important; letter-spacing:.4px; animation: pa-rise .8s ease .6s both; }}
.pa-splash-app {{ font-size: 11.5px; font-weight: 600; color:{SKY} !important; letter-spacing: 1.6px; text-transform: uppercase; animation: pa-rise .8s ease .85s both; }}
@keyframes pa-rise {{ from {{ opacity:0; transform: translateY(16px); }} }}
.pa-splash-story {{ position: relative; margin-top: 22px; width: min(780px, 94vw); min-height: 210px; }}
.pa-splash-hl {{ display:flex; flex-direction:column; gap: 10px; align-items:flex-start; width: fit-content; margin: 0 auto; animation: pa-away .6s ease both; }}
.pa-splash-hl span {{ white-space: nowrap; font-size: 15.5px; color:#e8efff !important; opacity:0; animation: pa-show .1s linear both; display:flex; align-items:center; }}
@keyframes pa-show {{ to {{ opacity:1; }} }}
.pa-splash-hl span b {{ color:{LIME} !important; }}
.pa-hl-e {{ font-style: normal; display:inline-flex; width: 28px; height: 28px; align-items:center; justify-content:center; margin-right: 10px;
  border-radius: 50%; background: rgba(0,178,227,.18); border: 1px solid rgba(0,178,227,.6); color:#fff !important; opacity:0;
  animation: pa-show .4s ease both; animation-delay: inherit; }}
.pa-hl-t {{ font-style: normal; display:inline-block; color:#e8efff !important; clip-path: inset(0 102% 0 0); animation: pa-type {SLOT}s linear both; animation-delay: inherit; }}
.pa-hl-t::after {{ content:"▌"; color:{LIME}; margin-left: 3px; opacity:0; animation: pa-caret {SLOT}s step-end both; animation-delay: inherit; }}
@keyframes pa-type {{ 0% {{ clip-path: inset(0 102% 0 0); opacity:0; }} 1% {{ opacity:1; }} 70% {{ clip-path: inset(0 0 0 0); }} 100% {{ clip-path: inset(0 0 0 0); opacity:1; }} }}
@keyframes pa-caret {{ 0% {{opacity:1}} 15% {{opacity:0}} 30% {{opacity:1}} 45% {{opacity:0}} 60% {{opacity:1}} 75% {{opacity:0}} 100% {{opacity:0}} }}
@keyframes pa-away {{ to {{ opacity:0; visibility:hidden; }} }}
.pa-splash-final {{ position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap: 12px;
  text-align:center; opacity:0; padding-bottom: 60px; animation: pa-final 1s cubic-bezier(.2,1.2,.4,1) both; }}
.pa-splash-final > div:first-child {{ font-size: 30px; font-weight: 700; color:#fff !important; font-family:'Segoe UI','Trebuchet MS',sans-serif !important; }}
.pa-splash-final b {{ color:{LIME} !important; }}
.pa-final-sub {{ font-size: 16.5px; color:#d7e2ff !important; letter-spacing:.4px; white-space: nowrap; clip-path: inset(0 100% 0 0); width: fit-content;
  margin: 0 auto; animation: pa-type 2s linear both; }}
.pa-final-sub u {{ color:{LIME} !important; text-decoration-color:{LIME}; text-underline-offset: 3px; font-weight: 600; }}
.pa-final-live {{ font-size: 14px; color:#b9c9f2 !important; opacity:0; animation: pa-show .6s ease both; }}
.pa-final-live b {{ color:#fff !important; }}
@keyframes pa-final {{ from {{ opacity:0; transform: scale(.9) translateY(14px); }} to {{ opacity:1; }} }}
.pa-splash-bar {{ width: 280px; height: 4px; border-radius: 4px; background: rgba(255,255,255,.18); margin-top: 14px; overflow:hidden; animation: pa-rise .8s ease 1.1s both; }}
.pa-splash-bar span {{ display:block; height:100%; width:0; background: linear-gradient(90deg, {SKY}, {LIME}); animation-name: pa-bar; animation-timing-function: linear; animation-fill-mode: forwards; }}
@keyframes pa-bar {{ to {{ width:100%; }} }}
.pa-splash-dots {{ display:flex; gap: 8px; margin-top: 12px; animation: pa-rise .8s ease 1.1s both; }}
.pa-splash-dots span {{ width:9px; height:9px; border-radius:50%; background:{LIME}; animation: pa-dot 1s ease infinite; }}
.pa-splash-dots span:nth-child(2) {{ background:{SKY}; animation-delay:.18s; }} .pa-splash-dots span:nth-child(3) {{ background:{GREEN}; animation-delay:.36s; }}
@keyframes pa-dot {{ 50% {{ transform: translateY(-7px); opacity:.5; }} }}
.pa-splash {{ overflow: hidden; }}
@media (max-width: 980px) {{
  .pa-splash img {{ height: 80px; }} .pa-splash-title {{ font-size: 30px; }} .pa-splash-tag {{ font-size: 14px; text-align:center; padding: 0 16px; }}
  .pa-splash-hl {{ width: auto; padding: 0 20px; }} .pa-splash-hl span {{ white-space: normal; font-size: 13.5px; align-items: flex-start; }}
  .pa-hl-t {{ display:inline; }} .pa-splash-final > div:first-child {{ font-size: 22px; padding: 0 16px; }}
  .pa-final-sub {{ white-space: normal; font-size: 14px; }} .pa-header {{ flex-wrap: wrap; }} .pa-q {{ font-size: 32px; }}
}}
.pa-splash-skip {{ position:absolute; bottom: 26px; font-size: 12.5px; color: rgba(255,255,255,.55) !important; letter-spacing: 1px; animation: pa-rise .8s ease 2.5s both; }}
"""
