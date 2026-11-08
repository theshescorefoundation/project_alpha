"""Project Alpha: one question, and the Patient Conference unfolds.

Run (from the project_alpha folder):
    .venv\\Scripts\\python webapp\\app.py            -> http://localhost:7871
Needs the patient payloads from the original pipeline (cqi_booklet/src/build_payloads.py).
Typed questions use the AI model set in config/project_alpha.yaml (llm.provider);
everything else is answered straight from the data.
"""
from __future__ import annotations

import base64
import os
import sys

import gradio as gr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import data  # noqa: E402
import engine  # noqa: E402
import llm  # noqa: E402
import brand_ui  # noqa: E402
from views import e  # noqa: E402

RUNS = data.runs()
if not RUNS:
    raise SystemExit("No CQI payloads found. Run: cd cqi_booklet && ..\\.venv\\Scripts\\python src\\build_payloads.py --as-of 2026-08-15 --sample")


def _b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


LEAF = f"data:image/png;base64,{_b64(os.path.join(HERE, 'assets', 'centerwell_leaf_t.png'))}"
INTER = _b64(os.path.join(HERE, "assets", "InterVariable.woff2"))
SUGGEST = ["What did the CQI agent find this week?", "Who needs action today?", "Who is ready for discharge?", "Tell me about 9000123"]
N_FU = 4


def mdy(iso):
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}"


def hero_html(as_of):
    r = data.load(as_of)
    return (f"<div class='pa-agent-wrap'>{brand_ui.agent_pill(r.agent_run())}</div>"
            "<div class='pa-q'>Which patient needs care <span>today</span>?</div>"
            "<div class='pa-q-sub'>Ask anything about this week’s Patient Conference</div>")


def meta_html(as_of):
    r = data.load(as_of)
    sample = "<span class='pa-pill'>Sample data</span>" if r.sample else ""
    return (f"<div class='pa-meta'>CQI run {mdy(as_of)} · {e(r.conference_label)} · {len(r.patients)} patients on the list · "
            f"{len(r.branches)} branches {sample}<br><span class='pa-ai'>✦ AI-assisted · answers only from CQI data · every answer cites its source</span></div>")


def header_html(as_of):
    r = data.load(as_of)
    return brand_ui.header(LEAF, mdy(as_of), r.sample)


def splash_html(as_of):
    return brand_ui.splash(LEAF, data.load(as_of).agent_run())


FOOT = ("<div class='pa-foot'>CenterWell Home Health · Clinical Quality Intelligence · Proof of concept · "
        "AI-assisted drafts for clinical review — clinicians make every decision</div>")


# ------------------------------------------------------------------ handlers
def run_query(q, ctx, as_of):
    q = (q or "").strip()
    run = data.load(as_of)
    if not q:
        return [gr.update()] * 20
    r = engine.answer(run, q, ctx or {}, llm.ask, llm.blocked)
    if r.mode == "refused" and "scope" in r.title.lower():
        r.body = f"<p class='pa-lead'>{e(llm.refusal() or '')}</p>" if llm.refusal() else r.body
    from views import answer_card
    ans = answer_card(r.title, r.body, r.srcs, r.mode, r.note)
    panels = "".join(r.panels)
    new_ctx = {k: v for k, v in (r.ctx or {}).items() if v} or (ctx or {})
    fus = (r.followups or [])[:N_FU]
    fu_updates = [gr.update(value=fus[i], visible=True) if i < len(fus) else gr.update(visible=False) for i in range(N_FU)]
    pid, branch = new_ctx.get("pid"), new_ctx.get("branch")
    dls = []
    if pid and pid in run.patients:
        dls += [("⬇  Full booklet (PDF)", run.booklet(pid)), ("⬇  3-page brief", run.brief(pid))]
    if branch and branch in run.branches and r.mode != "refused":
        dls.append((f"⬇  {run.branches[branch]['branch_label']} report", run.branch_pdf(branch)))
    dls = [d for d in dls if d[1]][:3]
    dl_updates = [gr.update(value=dls[i][1], label=dls[i][0], visible=True) if i < len(dls) else gr.update(visible=False) for i in range(3)]
    show_dec = bool(pid) and r.mode != "refused"
    prev = run.decisions().get(pid) if pid else None
    dec_title = (f"Conference decision · patient {pid}" + (f" · saved: {data.DECISIONS.get(prev['decision'], prev['decision'])}" if prev else "")) if pid else ""
    return ([gr.update(visible=False), gr.update(visible=True), gr.update(value=q), ans, panels] + fu_updates + dl_updates
            + [gr.update(visible=show_dec, label=dec_title, open=False), new_ctx, gr.update(value=None), "", "", "", "", ""])


def save_decision(ctx, as_of, decision, owner, due, notes, reviewer):
    pid = (ctx or {}).get("pid")
    if not pid:
        return "<span class='pa-dim'>Open a patient first.</span>"
    if not decision:
        return "<span style='color:#FFB74D'>Choose a decision first.</span>"
    code = {v: k for k, v in data.DECISIONS.items()}[decision]
    data.load(as_of).save_decision(pid, code, owner, due, notes, reviewer)
    return f"<span style='color:#97D700'>✓ Saved for patient {e(pid)}: {e(decision)}</span>"


def go_home(as_of):
    return gr.update(visible=True), gr.update(visible=False), gr.update(value=""), {}, hero_html(as_of), meta_html(as_of)


# ------------------------------------------------------------------ CSS (clinician_copilot palette + Inter)
CSS = brand_ui.CSS + f"""
@font-face {{ font-family:'Inter'; src:url(data:font/woff2;base64,{INTER}) format('woff2'); font-weight:100 900; font-display:swap; }}
:root {{ --cw:#0033A1; --cw2:#00257F; --lime:#97D700; --sky:#00B2E3; --teal:#0095C8; --coral:#FF8A80; --amber:#FFB74D; --soft:#b9c9f2; }}
html, body, gradio-app, .gradio-container {{ background: radial-gradient(1200px 700px at 50% -10%, #0a47c4 0%, var(--cw) 45%, var(--cw2) 100%) fixed !important; }}
.gradio-container {{ --font:'Inter','Segoe UI',system-ui,sans-serif; max-width: 1080px !important; margin: 0 auto !important; }}
.gradio-container, .gradio-container *:not(code) {{ font-family: var(--font); }}
.gradio-container, .gradio-container .prose, .gradio-container .prose * {{ color:#fff; }}
footer {{ display:none !important; }}
.block, .form, .gr-group {{ background: transparent !important; border: none !important; box-shadow:none !important; }}
/* hero */
.pa-hero {{ text-align:center; padding: 4vh 0 6px; }}
.pa-leaf {{ display:block; margin: 0 auto; height: 92px; animation: pop 1s cubic-bezier(.2,1.4,.4,1) both; filter: drop-shadow(0 6px 22px rgba(0,178,227,.35)); }}
@keyframes pop {{ from {{ opacity:0; transform: scale(.6) rotate(-12deg); }} }}
.pa-word {{ font-size: 54px; font-weight: 750; letter-spacing:-1.5px; margin-top: 8px; animation: rise .7s ease .2s both; }}
.pa-word span {{ color: var(--lime); }}
.pa-tag {{ font-size: 17px; color: var(--soft) !important; margin-top: 2px; animation: rise .7s ease .35s both; }}
@keyframes rise {{ from {{ opacity:0; transform: translateY(12px); }} }}
/* search pill */
.pa-searchrow {{ max-width: 720px; margin: 22px auto 6px !important; background:#fff; border-radius: 999px !important; padding: 6px 6px 6px 22px !important;
  box-shadow: 0 14px 40px rgba(0,16,64,.45); align-items:center !important; flex-wrap: nowrap !important; gap: 4px !important; animation: rise .7s ease .45s both; }}
.pa-searchrow.top {{ max-width: none; margin: 0 !important; box-shadow: 0 6px 20px rgba(0,16,64,.35); animation:none; }}
.pa-search textarea, .pa-search input {{ background: transparent !important; border: none !important; box-shadow: none !important; color:#13213f !important;
  font-size: 17px !important; padding: 10px 0 !important; }}
.pa-search textarea::placeholder {{ color:#7f8aa6 !important; }}
.pa-search, .pa-search > label, .pa-search .wrap {{ background: transparent !important; border:none !important; box-shadow:none !important; padding:0 !important; }}
button.pa-go {{ min-width: 46px !important; width:46px !important; height: 46px !important; border-radius: 50% !important; background: var(--cw) !important;
  color:#fff !important; font-size: 20px !important; border:none !important; padding:0 !important; flex: 0 0 46px !important; }}
button.pa-go:hover {{ background: var(--teal) !important; }}
/* chips */
.pa-sugg {{ justify-content:center !important; flex-wrap: wrap !important; gap: 8px !important; margin-top: 10px !important; animation: rise .7s ease .55s both; }}
.pa-sugg button, .pa-fu button, button.pa-chipbtn {{ flex: 0 0 auto !important; min-width: 0 !important; width:auto !important; background: rgba(255,255,255,.07) !important;
  border: 1px solid rgba(255,255,255,.28) !important; color:#fff !important; border-radius: 999px !important; padding: 7px 15px !important;
  font-size: 13.5px !important; font-weight: 500 !important; box-shadow:none !important; }}
.pa-sugg button:hover, .pa-fu button:hover {{ border-color: var(--lime) !important; background: rgba(151,215,0,.12) !important; }}
.pa-meta {{ text-align:center; color: var(--soft) !important; font-size: 13px; margin-top: 18px; line-height:1.8; animation: rise .7s ease .65s both; }}
.pa-meta * {{ color: var(--soft) !important; }} .pa-meta .pa-ai {{ color: var(--sky) !important; }}
.pa-pill {{ border:1px solid var(--amber); color: var(--amber) !important; border-radius: 999px; padding: 1px 8px; font-size: 11px; margin-left: 4px; }}
/* results */
.pa-topbar {{ align-items:center !important; gap: 16px !important; padding-top: 18px !important; flex-wrap: nowrap !important; }}
.pa-top {{ display:flex; align-items:center; gap:8px; font-size: 20px; white-space:nowrap; }} .pa-top img {{ height: 34px; }} .pa-top b {{ color: var(--lime); }}
button.pa-home {{ background: transparent !important; border: none !important; box-shadow:none !important; padding:0 !important; min-width:0 !important; flex: 0 0 auto !important; }}
.pa-answer {{ background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.16); border-left: 4px solid var(--sky); border-radius: 18px;
  padding: 18px 22px; margin-top: 14px; animation: rise .45s ease both; }}
.pa-answer.ai {{ border-left-color: var(--lime); }}
.pa-badge {{ font-size: 11.5px; font-weight: 700; letter-spacing:.4px; color: var(--sky) !important; text-transform: uppercase; }}
.pa-badge.ai {{ color: var(--lime) !important; }} .pa-badge.warn {{ color: var(--amber) !important; }}
.pa-title {{ font-size: 24px; font-weight: 700; margin: 6px 0 8px; letter-spacing:-.3px; }}
.pa-lead {{ font-size: 16.5px; line-height: 1.55; margin: 0 0 8px; }}
.pa-body p {{ margin: 0 0 8px; line-height:1.55; }}
.pa-src {{ margin-top: 10px; font-size: 12px; color: var(--soft) !important; }}
.pa-src code, .pa-body code {{ background: rgba(255,255,255,.1); border:1px solid rgba(255,255,255,.18); color: var(--soft) !important; border-radius: 6px;
  padding: 1px 6px; font-size: 11.5px; margin-left: 3px; font-family: ui-monospace, Consolas, monospace; }}
.pa-note {{ margin-top: 6px; font-size: 12px; color: var(--soft) !important; font-style: italic; }}
.pa-fu {{ gap: 8px !important; flex-wrap: wrap !important; margin: 12px 0 4px !important; }}
.pa-card {{ background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.14); border-radius: 18px; padding: 16px 20px; margin-top: 14px;
  animation: rise .5s ease both; }}
.pa-card:nth-child(2) {{ animation-delay: .08s; }} .pa-card:nth-child(3) {{ animation-delay: .16s; }}
.pa-h {{ font-size: 15px; font-weight: 700; margin-bottom: 12px; display:flex; justify-content:space-between; gap: 12px; }}
.pa-sub {{ font-weight: 400; font-size: 12.5px; color: var(--soft) !important; }}
.pa-chip {{ display:inline-block; border:1px solid; border-radius: 999px; padding: 2px 10px; font-size: 12px; font-weight: 600; margin: 0 6px 6px 0; white-space:nowrap; }}
.pa-chips {{ margin: 8px 0 4px; }}
.pa-grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }}
.pa-pt {{ background: rgba(0,20,80,.35); border: 1px solid rgba(255,255,255,.14); border-radius: 14px; padding: 14px 16px; }}
.pa-pt-top {{ display:flex; justify-content:space-between; align-items: baseline; }}
.pa-pid {{ font-size: 19px; font-weight: 750; letter-spacing:.3px; }}
.pa-quote {{ color: var(--lime) !important; font-size: 15px; font-weight: 600; margin: 6px 0; }}
.pa-quote.dim {{ color: var(--soft) !important; font-weight: 400; font-style: italic; }}
.pa-found {{ font-size: 13.5px; line-height: 1.5; color: #dfe7ff !important; }}
.pa-dec {{ margin-top: 8px; color: var(--lime) !important; font-weight: 700; font-size: 13px; }}
.pa-dim {{ color: var(--soft) !important; }} .small {{ font-size: 11.5px; }}
.pa-ul {{ margin: 4px 0 0; padding-left: 18px; }} .pa-ul li {{ margin: 4px 0; line-height: 1.5; }}
.pa-ul.dots {{ list-style: none; padding-left: 0; }} .pa-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right: 9px; }}
.pa-stats {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin: 6px 0 12px; }}
.pa-stat {{ background: rgba(0,20,80,.35); border-radius: 12px; padding: 10px 12px; }}
.pa-stat b {{ display:block; font-size: 26px; font-weight: 750; letter-spacing:-.5px; }} .pa-stat span {{ font-size: 12px; color: var(--soft) !important; }}
table.pa-t {{ width:100%; border-collapse: collapse; font-size: 13.5px; font-variant-numeric: tabular-nums; }}
.pa-t th {{ text-align:left; font-size: 11.5px; text-transform: uppercase; letter-spacing:.5px; color: var(--soft) !important; font-weight: 600;
  padding: 6px 8px; border-bottom: 1px solid rgba(255,255,255,.2); background: transparent !important; }}
.pa-t td {{ padding: 8px 8px; border-bottom: 1px solid rgba(255,255,255,.08); vertical-align: top; background: transparent !important; }}
.pa-t th, .pa-t td {{ border-left: none !important; border-right: none !important; border-top: none !important; }}
table.pa-t {{ border: none !important; }}
.pa-t tr:hover td {{ background: rgba(255,255,255,.04) !important; }}
.pa-bar {{ display:inline-block; width: 80px; height: 7px; border-radius: 4px; background: rgba(255,255,255,.15); margin-right: 8px; vertical-align: middle; overflow:hidden; }}
.pa-bar i {{ display:block; height:100%; border-radius: 4px; }}
.pa-ready {{ border: 1.5px solid; border-radius: 16px; padding: 14px 18px; background: rgba(0,20,80,.3); }}
.pa-ready-rec {{ font-size: 26px; font-weight: 750; letter-spacing:-.4px; }}
.pa-two {{ display:grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 12px; }}
.pa-k {{ font-size: 12px; text-transform: uppercase; letter-spacing: .5px; font-weight: 700; margin-top: 8px; }}
.pa-check {{ margin-top: 12px; border-left: 3px solid var(--amber); padding: 6px 12px; background: rgba(255,183,77,.08); border-radius: 0 8px 8px 0; font-size: 13.5px; }}
.pa-steps {{ margin-top: 12px; font-size: 13.5px; }}
.pa-sparks {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
.pa-spark {{ background: rgba(0,20,80,.35); border-radius: 12px; padding: 10px 12px; }}
.pa-spark svg {{ width:100%; height: 84px; }} .pa-spark line.lim {{ stroke: var(--coral); stroke-dasharray: 4 4; stroke-width: 1; opacity:.8; }}
.pa-spark-h {{ display:flex; justify-content: space-between; font-size: 13px; color: var(--soft) !important; }} .pa-spark-h b {{ font-size: 15px; }}
.pa-drv {{ display:grid; grid-template-columns: 1fr 1.2fr; align-items:center; gap: 12px; margin: 6px 0; font-size: 13.5px; }}
.pa-drv i {{ display:block; height: 9px; border-radius: 5px; background: linear-gradient(90deg, var(--sky), var(--lime)); }}
/* downloads + decision */
.pa-dl {{ gap: 8px !important; margin-top: 14px !important; flex-wrap: wrap !important; }}
.pa-dl button, .pa-dl a {{ flex: 0 0 auto !important; width:auto !important; min-width:0 !important; background: transparent !important; border: 1px solid var(--sky) !important;
  color: var(--sky) !important; border-radius: 999px !important; padding: 7px 16px !important; font-size: 13px !important; }}
.pa-decbox {{ margin-top: 14px !important; background: rgba(255,255,255,.05) !important; border: 1px solid rgba(151,215,0,.45) !important; border-radius: 16px !important; }}
.pa-decbox .label-wrap span, .pa-decbox button.label-wrap {{ color: var(--lime) !important; font-weight: 700 !important; }}
.pa-decbox input, .pa-decbox textarea {{ background: rgba(0,20,80,.45) !important; color:#fff !important; border: 1px solid rgba(255,255,255,.2) !important; }}
.pa-decbox label span, .pa-decbox .wrap label {{ color:#fff !important; }}
.pa-decbox .wrap label {{ background: rgba(255,255,255,.06) !important; border: 1px solid rgba(255,255,255,.2) !important; border-radius: 999px !important; }}
.pa-decbox .wrap label.selected {{ border-color: var(--lime) !important; background: rgba(151,215,0,.15) !important; }}
button.pa-save {{ background: var(--lime) !important; color: #00205B !important; font-weight: 700 !important; border:none !important; border-radius: 999px !important; }}
.pa-foot {{ text-align:center; font-size: 11.5px; color: rgba(185,201,242,.7) !important; margin: 40px 0 16px; }}
@media (max-width: 760px) {{ .pa-two {{ grid-template-columns: 1fr; }} .pa-word {{ font-size: 40px; }} }}
"""


# ------------------------------------------------------------------ layout
with gr.Blocks(title="Project Alpha · CQI Patient Conference") as demo:
    ctx = gr.State({})
    as_of = gr.State(RUNS[0])

    gr.HTML(splash_html(RUNS[0]))
    gr.HTML(header_html(RUNS[0]))

    with gr.Column(visible=True) as hero:
        hero_h = gr.HTML(hero_html(RUNS[0]))
        with gr.Row(elem_classes="pa-searchrow"):
            q1 = gr.Textbox(placeholder="Ask about a patient, a branch, discharge readiness…", show_label=False, container=False,
                            elem_classes="pa-search", scale=10, lines=1, max_lines=1, autofocus=True)
            go1 = gr.Button("➜", elem_classes="pa-go", scale=0)
        with gr.Row(elem_classes="pa-sugg"):
            sugg = [gr.Button(s, size="sm") for s in SUGGEST]
        meta_h = gr.HTML(meta_html(RUNS[0]))

    with gr.Column(visible=False) as results:
        with gr.Row(elem_classes="pa-topbar"):
            with gr.Row(elem_classes="pa-searchrow top"):
                q2 = gr.Textbox(show_label=False, container=False, elem_classes="pa-search", scale=10, lines=1, max_lines=1,
                                placeholder="Ask a follow-up…")
                go2 = gr.Button("➜", elem_classes="pa-go", scale=0)
        ans = gr.HTML()
        with gr.Row(elem_classes="pa-fu"):
            fu = [gr.Button("", size="sm", visible=False) for _ in range(N_FU)]
            back = gr.Button("⌂  Start over", size="sm", elem_classes="pa-chipbtn")
        panels = gr.HTML()
        with gr.Row(elem_classes="pa-dl"):
            dls = [gr.DownloadButton("", visible=False, size="sm") for _ in range(3)]
        with gr.Accordion("Conference decision", open=False, visible=False, elem_classes="pa-decbox") as decbox:
            dec = gr.Radio(list(data.DECISIONS.values()), show_label=False)
            with gr.Row():
                owner = gr.Textbox(label="Action owner", placeholder="e.g. SN / CM")
                due = gr.Textbox(label="Due date", placeholder="MM/DD/YYYY")
                reviewer = gr.Textbox(label="Reviewed by")
            notes = gr.Textbox(label="Notes", lines=2, placeholder="What the team agreed")
            with gr.Row():
                save = gr.Button("Save decision", elem_classes="pa-save", scale=0)
                saved = gr.HTML()
    gr.HTML(FOOT)

    outs = [hero, results, q2, ans, panels] + fu + dls + [decbox, ctx, dec, owner, due, notes, reviewer, saved]
    for box, btn in ((q1, go1), (q2, go2)):
        box.submit(run_query, [box, ctx, as_of], outs, show_progress="minimal")
        btn.click(run_query, [box, ctx, as_of], outs, show_progress="minimal")
    for b in sugg + fu:
        b.click(run_query, [b, ctx, as_of], outs, show_progress="minimal")
    back.click(go_home, [as_of], [hero, results, q1, ctx, hero_h, meta_h])
    save.click(save_decision, [ctx, as_of, dec, owner, due, notes, reviewer], saved)


if __name__ == "__main__":
    demo.launch(css=CSS, theme=gr.themes.Base(), server_name="127.0.0.1",
                server_port=int(os.environ.get("ALPHA_PORT", 7871)))
