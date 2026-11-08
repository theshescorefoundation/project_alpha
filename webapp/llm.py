"""AI answers for free-text questions, behind the Project Alpha scope filter.

Config: config/project_alpha.yaml -> llm.provider (gemini | anthropic | gateway | rules), plus scope_filter, answer, logging.
Grounding: the selected patient's payload, or the week's branch summaries + roster + agent findings. Nothing else is sent.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime

import engine
import views as V
from views import e

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import alpha_config  # noqa: E402

PROVIDER_LABEL = {"gemini": "Gemini Flash", "anthropic": "Claude Opus", "gateway": "AI gateway", "rules": "Rules only"}


def cfg():
    return alpha_config.cfg()


def llm_cfg():
    return cfg().get("llm") or {}


def provider():
    return (llm_cfg().get("provider") or "rules").lower()


def _key(env_name, *fallbacks):
    for n in (env_name, *fallbacks):
        if n and os.environ.get(n):
            return os.environ[n]
    return None


def _url(section):
    return (os.environ.get(section.get("base_url_env") or "") or section.get("base_url") or "").strip()


def backend_label():
    p = provider(); c = llm_cfg().get(p) or {}
    if p == "gemini":
        return f"Gemini Flash · {c.get('model')}" if _key(c.get("api_key_env", "GEMINI_API_KEY"), "GOOGLE_API_KEY") else "Rules only · no Gemini key set"
    if p == "anthropic":
        via = " via gateway" if _url(c) else ""
        return f"Claude Opus · {c.get('model')}{via}"
    if p == "gateway":
        return f"{c.get('provider_label') or 'AI gateway'} · {c.get('model')}"
    return "Rules only"


def approved_for_phi():
    return bool((llm_cfg().get(provider()) or {}).get("approved_for_phi", False))


def max_answer_tokens():
    c = llm_cfg().get(provider()) or {}
    return int(c.get("max_output_tokens") or c.get("max_tokens") or 600)


# ------------------------------------------------------------------ grounding context
DROP = {"meta", "provenance"}


def patient_context(run, pid):
    p = run.patients[pid]
    ctx = {k: v for k, v in p.items() if k not in DROP}
    meds = dict(ctx.get("medications") or {}); meds.pop("checks", None); ctx["medications"] = meds
    ctx["readiness_snapshot"] = engine.readiness(p)
    ctx["conference_decision"] = run.decisions().get(pid)
    ctx["agent_opportunities"] = [o for o in run.opportunities() if o.get("patient_id") == pid]
    return json.dumps(ctx, default=str, separators=(",", ":"))


def list_context(run, branch=None):
    brs = [branch] if branch else run.branch_ids()
    out = {"branches": {}, "patients": [], "agent_opportunities": [o for o in run.opportunities()
                                                                   if not branch or o.get("branch") == branch][:60]}
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
                                                 ("has_pcp", "pcp_name", "cwpco_enrolled", "cwrx_enrolled", "refer_cwpco", "refer_cwrx", "active_med_count")},
                                "patient_stated_goal": (p.get("goals") or {}).get("patient_stated_goal")})
    return json.dumps(out, default=str, separators=(",", ":"))


# ------------------------------------------------------------------ model calls
def _gemini(c, system, prompt, max_tokens):
    from google import genai
    from google.genai import types
    key = _key(c.get("api_key_env", "GEMINI_API_KEY"), "GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("no Gemini API key in the environment")
    client = genai.Client(api_key=key, http_options=types.HttpOptions(api_version="v1beta"))  # system_instruction needs v1beta
    conf = types.GenerateContentConfig(system_instruction=system, temperature=0.1, max_output_tokens=max_tokens,
                                       thinking_config=types.ThinkingConfig(thinking_budget=0))
    tries = int(c.get("max_retries", 2)) + 1
    for i in range(tries):
        try:
            r = client.models.generate_content(model=c["model"], contents=prompt, config=conf)
            return (r.text or "").strip()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))   # rate limit / transient error


def _anthropic(c, system, prompt, max_tokens):
    """Claude via the official Anthropic SDK - direct, or through a gateway that proxies the Messages API (base_url)."""
    import anthropic
    kw = {"timeout": float(c.get("timeout_seconds", 60)), "max_retries": int(c.get("max_retries", 2))}
    key = _key(c.get("api_key_env", "ANTHROPIC_API_KEY"))
    if key:
        kw["api_key"] = key                       # otherwise the SDK uses ANTHROPIC_AUTH_TOKEN / an `ant auth login` profile
    if _url(c):
        kw["base_url"] = _url(c)
    if c.get("extra_headers"):
        kw["default_headers"] = {str(k): str(v) for k, v in c["extra_headers"].items()}
    client = anthropic.Anthropic(**kw)
    # Opus 5.5 always thinks (adaptive); thinking counts toward max_tokens, so never send a tiny budget.
    req = dict(model=c.get("model", "claude-opus-5-5"), max_tokens=max(int(max_tokens), int(c.get("max_tokens", 4000))),
               system=system, messages=[{"role": "user", "content": prompt}],
               output_config={"effort": c.get("effort", "low")})
    if c.get("refusal_fallback", True) and not _url(c):
        r = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **req)
    else:
        r = client.messages.create(**req)
    if r.stop_reason == "refusal":
        cat = getattr(getattr(r, "stop_details", None), "category", None)
        raise RuntimeError(f"model declined the request ({cat or 'refusal'})")
    return "".join(b.text for b in r.content if b.type == "text").strip()


def _gateway(c, system, prompt, max_tokens):
    """Any OpenAI-compatible chat endpoint: company AI gateway, Databricks Model Serving, vLLM..."""
    import ssl
    import urllib.request
    auth = c.get("auth") or {}
    headers = {"Content-Type": "application/json", **{str(k): str(v) for k, v in (c.get("extra_headers") or {}).items()}}
    tok = _key(auth.get("token_env"))
    if auth.get("type") == "bearer" and tok:
        headers[auth.get("header_name") or "Authorization"] = f"Bearer {tok}"
    elif auth.get("type") == "api_key_header" and tok:
        headers[auth.get("header_name") or "api-key"] = tok
    body = {"model": c["model"], "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    url = _url(c)
    if not url or url.startswith("<"):
        raise RuntimeError("gateway base_url is not set in config/project_alpha.yaml")
    ca = os.environ.get(c.get("ca_bundle_env") or "")
    ctx = ssl.create_default_context(cafile=ca) if ca else None
    req = urllib.request.Request(url.rstrip("/") + c.get("chat_path", "/chat/completions"), json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=c.get("timeout_seconds", 30), context=ctx) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"].strip()


def call(system, prompt, max_tokens):
    p = provider(); c = llm_cfg().get(p) or {}
    if p == "gemini":
        return _gemini(c, system, prompt, max_tokens)
    if p == "anthropic":
        return _anthropic(c, system, prompt, max_tokens)
    if p == "gateway":
        return _gateway(c, system, prompt, max_tokens)
    raise RuntimeError("llm.provider is set to rules")


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
    # a question that names an MRN-like identifier (has digits, 4+ characters) is about a patient - skip the guard call
    if any(k in ql for k in pf.get("allow_keywords") or []) or re.search(r"\b(?=[\w-]*\d)[\w-]{4,}\b", q):
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
    path = alpha_config.path(c.get("path", "webapp/output/ai_log.csv"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    fields = ["timestamp", "patient_id", "branch", "question", "scope_result", "model", "latency_ms", "context_hash", "answer", "feedback"]
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, restval="", extrasaction="ignore"); new and w.writeheader(); w.writerow(row)


# ------------------------------------------------------------------ entry point used by engine.answer
def ask(run, q, pid=None, branch=None):
    c = cfg(); t0 = time.time()
    refusal = (c.get("scope_filter") or {}).get("refusal_message", "Project Alpha only answers questions about this conference's patients and data.")
    if run.sample is False and not approved_for_phi():
        return engine.Result("AI answers are off for real data", "<p class='pa-lead'>This model is not approved for PHI. "
                             "Use the suggested questions, or set llm.provider to an approved endpoint.</p>", mode="refused")
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
        txt = call(system, q, max_answer_tokens())
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
