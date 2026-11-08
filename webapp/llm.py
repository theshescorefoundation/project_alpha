"""Gemini Flash (or the company gateway) answers for free-text questions, behind the Project Alpha scope filter.

Config: webapp/project_alpha_ai_gateway.yaml (backend switch, model, key env var names, filter, prompts, logging).
Grounding: the selected patient's payload, or the week's branch summaries + roster. Nothing else is sent.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime

import yaml

import engine
import views as V
from views import e

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.environ.get("ALPHA_AI_CONFIG", os.path.join(HERE, "project_alpha_ai_gateway.yaml"))


def cfg():
    with open(CFG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def backend_label():
    c = cfg(); b = c.get("backend", "rules")
    if b == "gemini":
        ok = bool(os.environ.get(c["gemini"].get("api_key_env", "GEMINI_API_KEY")) or os.environ.get("GOOGLE_API_KEY"))
        return f"Gemini Flash · {c['gemini']['model']}" if ok else "Rules only · no Gemini key set"
    if b == "gateway":
        return (c.get("gateway") or {}).get("provider_label") or "Company AI gateway"
    return "Rules only"


# ------------------------------------------------------------------ grounding context
DROP = {"meta", "provenance"}


def patient_context(run, pid):
    p = run.patients[pid]
    ctx = {k: v for k, v in p.items() if k not in DROP}
    meds = dict(ctx.get("medications") or {}); meds.pop("checks", None); ctx["medications"] = meds
    ctx["readiness_snapshot"] = engine.readiness(p)
    ctx["conference_decision"] = run.decisions().get(pid)
    return json.dumps(ctx, default=str, separators=(",", ":"))


def list_context(run, branch=None):
    brs = [branch] if branch else run.branch_ids()
    out = {"branches": {}, "patients": []}
    for b in brs:
        s = dict(run.branches[b]); s.pop("patients", None)
        out["branches"][b] = s
    for k in run.order:
        if branch and run.branch_of(k) != branch:
            continue
        p = run.patients[k]
        out["patients"].append({"patient_id": k, "branch": run.branch_of(k), "risk": {x: p["risk"].get(x) for x in ("outcome_risk_level", "score", "score_prior", "percentile")},
                                "episode_day": p["patient"]["episode_day"], "days_to_episode_end": p["patient"].get("days_to_episode_end"),
                                "vitals_trajectory": (p["vitals"].get("trajectory") or {}).get("class"),
                                "at_a_glance": p.get("at_a_glance"), "headline": (p.get("summary") or {}).get("headline"),
                                "actions": (p.get("summary") or {}).get("actions"), "readiness": engine.readiness(p)["recommendation"],
                                "goals": [(g["goal"], g["status"]) for g in (p.get("goals") or {}).get("items") or []],
                                "care_network": {x: (p.get("care_network") or {}).get(x) for x in
                                                 ("has_pcp", "pcp_name", "cwpco_enrolled", "cwrx_enrolled", "refer_cwpco", "refer_cwrx", "active_med_count")},                                "patient_stated_goal": (p.get("goals") or {}).get("patient_stated_goal")})
    return json.dumps(out, default=str, separators=(",", ":"))


# ------------------------------------------------------------------ model calls
def _gemini(c, system, prompt, max_tokens):
    from google import genai
    from google.genai import types
    g = c["gemini"]
    key = os.environ.get(g.get("api_key_env", "GEMINI_API_KEY")) or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("no Gemini API key in the environment")
    client = genai.Client(api_key=key, http_options=types.HttpOptions(api_version="v1beta"))  # system_instruction needs v1beta
    conf = types.GenerateContentConfig(system_instruction=system, temperature=g.get("request", {}).get("temperature", 0.1),
                                       max_output_tokens=max_tokens, thinking_config=types.ThinkingConfig(thinking_budget=0))
    tries = int(g.get("request", {}).get("max_retries", 2)) + 1
    for i in range(tries):
        try:
            r = client.models.generate_content(model=g["model"], contents=prompt, config=conf)
            return (r.text or "").strip()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))   # rate limit / transient error


def _gateway(c, system, prompt, max_tokens):
    import urllib.request
    gw = c["gateway"]; auth = gw.get("auth") or {}
    headers = {"Content-Type": "application/json", **(gw.get("extra_headers") or {})}
    tok = os.environ.get(auth.get("token_env") or "")
    if auth.get("type") == "bearer" and tok:
        headers[auth.get("header_name") or "Authorization"] = f"Bearer {tok}"
    elif auth.get("type") == "api_key_header" and tok:
        headers[auth.get("header_name") or "api-key"] = tok
    body = {"model": gw["model"], "temperature": gw.get("request", {}).get("temperature", 0.1), "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    req = urllib.request.Request(gw["base_url"].rstrip("/") + gw.get("chat_path", "/chat/completions"), json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=gw.get("request", {}).get("timeout_seconds", 30)) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"].strip()


def call(system, prompt, max_tokens):
    c = cfg(); b = c.get("backend", "rules")
    if b == "gemini":
        return _gemini(c, system, prompt, max_tokens)
    if b == "gateway":
        return _gateway(c, system, prompt, max_tokens)
    raise RuntimeError("AI backend is set to rules only")


# ------------------------------------------------------------------ scope filter
def blocked(q):
    """Layer 1 only (no model call): applied to every question, including ones the rules could answer."""
    sf = cfg().get("scope_filter") or {}
    if not sf.get("enabled", True):
        return False
    pf = sf.get("pre_filter") or {}
    ql = q.lower()
    return len(q) > pf.get("max_question_chars", 600) or any(re.search(p, ql) for p in pf.get("block_patterns") or [])


def refusal():
    return (cfg().get("scope_filter") or {}).get("refusal_message")


def scope_check(q):
    """Return (ok, how). Layer 1 keywords; layer 2 guard prompt on the model."""
    c = cfg(); sf = c.get("scope_filter") or {}
    if not sf.get("enabled", True):
        return True, "filter off"
    pf = sf.get("pre_filter") or {}
    if len(q) > pf.get("max_question_chars", 600):
        return False, "too long"
    ql = q.lower()
    for pat in pf.get("block_patterns") or []:
        if re.search(pat, ql):
            return False, "blocked pattern"
    if any(k in ql for k in pf.get("allow_keywords") or []) or re.search(r"\b\d{7}\b", q):
        return True, "allow keyword"
    g = sf.get("guard") or {}
    if not g.get("enabled", True):
        return True, "guard off"
    verdict = call(g["prompt"], f"Question: {q}", g.get("max_tokens", 5) + 3).upper()
    return ("IN_SCOPE" in verdict and "OUT_OF_SCOPE" not in verdict), f"guard: {verdict[:20]}"


# ------------------------------------------------------------------ render model text
def to_html(text):
    srcs, out, in_ul = [], [], False
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^\**sources?\**\s*:\s*(.+)$", s, re.I)
        if m:
            srcs += [x.strip(" `[]*") for x in re.split(r"[,;]", m.group(1)) if x.strip(" `[]*")]
            continue
        if not s:
            if in_ul: out.append("</ul>"); in_ul = False
            continue
        s = e(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"\[([a-z_.*/ ]{2,40})\]", r"<code>\1</code>", s)
        if re.match(r"^[-*•]\s+", s):
            if not in_ul: out.append("<ul class='pa-ul'>"); in_ul = True
            out.append("<li>" + re.sub(r"^[-*•]\s+", "", s) + "</li>")
        else:
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<p>{s}</p>")
    if in_ul: out.append("</ul>")
    return "".join(out), srcs


def log(row):
    c = cfg().get("logging") or {}
    if not c.get("enabled", True):
        return
    path = os.path.join(HERE, c.get("path", "output/ai_log.csv"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    fields = ["timestamp", "patient_id", "branch", "question", "scope_result", "model", "latency_ms", "context_hash", "answer", "feedback"]
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, restval="", extrasaction="ignore"); new and w.writeheader(); w.writerow(row)


# ------------------------------------------------------------------ entry point used by engine.answer
def ask(run, q, pid=None, branch=None):
    c = cfg(); t0 = time.time()
    refusal = (c.get("scope_filter") or {}).get("refusal_message", "Project Alpha only answers questions about this conference's patients and data.")
    if run.sample is False and not (c.get(c.get("backend", ""), {}) or {}).get("approved_for_phi", False):
        return engine.Result("AI answers are off for real data", "<p class='pa-lead'>This model is not approved for PHI. "
                             "Use the suggested questions, or switch the backend to an approved gateway.</p>", mode="refused")
    try:
        ok, how = scope_check(q)
    except Exception as ex:  # model unreachable -> rules
        return _unavailable(q, ex)
    if not ok:
        log({"timestamp": datetime.now().isoformat(timespec="seconds"), "patient_id": pid or "", "branch": branch or "", "question": q,
             "scope_result": how, "model": backend_label(), "latency_ms": int((time.time() - t0) * 1000), "answer": "REFUSED"})
        return engine.Result("Outside Project Alpha’s scope", f"<p class='pa-lead'>{e(refusal)}</p>",
                             followups=["Who needs action today?", "Who is ready for discharge?", "How are the branches doing?"], mode="refused")
    context = patient_context(run, pid) if pid else list_context(run, branch)
    system = (c.get("answer") or {}).get("system_prompt", "").replace("{as_of_date}", run.as_of).replace("{patient_context}", context)
    system += ("\n\nFormat: a one-sentence direct answer first, then up to 5 short bullet lines starting with '- '. "
               "Cite the top-level data section in square brackets after each fact, e.g. [vitals], [goals], [care_delivery], [branches]. "
               "End with one line 'SOURCES: ' listing the sections you used.")
    try:
        txt = call(system, q, (c.get("gemini") or {}).get("request", {}).get("max_output_tokens", 600))
    except Exception as ex:
        return _unavailable(q, ex)
    html_, srcs = to_html(txt)
    log({"timestamp": datetime.now().isoformat(timespec="seconds"), "patient_id": pid or "", "branch": branch or "", "question": q,
         "scope_result": how, "model": backend_label(), "latency_ms": int((time.time() - t0) * 1000),
         "context_hash": hashlib.sha1(context.encode()).hexdigest()[:10], "answer": txt[:2000]})
    title = (f"{q.rstrip('?')}?" if len(q) < 90 else q[:87] + "…")
    fu = ([f"Is {pid} ready for discharge?", f"Why is {pid} on the list?"] if pid else ["Who needs action today?", "Who is ready for discharge?"])
    return engine.Result(e(title), html_, [], fu, srcs, mode="ai", ctx={"pid": pid, "branch": branch},
                         note=(c.get("answer") or {}).get("disclaimer"))


def _unavailable(q, ex):
    return engine.Result("AI answer unavailable", f"<p class='pa-lead'>The AI model could not be reached ({e(type(ex).__name__)}). "
                         "The suggested questions below are answered straight from the data.</p>",
                         followups=["Who needs action today?", "Who is ready for discharge?", "How are the branches doing?"], mode="refused")
