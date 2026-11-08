"""Check that the data model, sample data, pipeline, renderer, LLM spec and docs agree.

    python tools/check_sync.py        # exit code 0 = in sync

Checks
 1. datasets.yaml  <-> data/csv           every dataset has a CSV with exactly the canonical columns
 2. llm_summary    <-> build_payloads.py  every LLM column is read by the pipeline, and every g("...") is declared
 3. llm_summary    <-> llm_output_spec.md every LLM text column (or its source name) is documented
 4. payload        <-> schema             payload top-level keys == schema properties; all sample payloads validate
 5. pages          <-> renderer           every page key renders and sits in one section; links point at real pages
 6. form fields    <-> collect_feedback   every feedback row has fillable fields in the PDF and is read back
 7. branding.yaml  <-> renderer           every branding key is used
"""
import csv, json, os, re, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, os.path.join(ROOT, "tools"))
import yaml
import cqi_payload, build_payloads, build_booklet
from cqi_datasets import Datasets

problems, ok = [], []
def check(name, cond, detail=""):
    (ok if cond else problems).append(name + ("" if cond else f": {detail}"))

reg = yaml.safe_load(open(os.path.join(ROOT, "config", "datasets.yaml")))["datasets"]
csv_dir = os.path.join(ROOT, "data", "csv")
# 1
for n, spec in reg.items():
    path = os.path.join(csv_dir, spec.get("csv", f"{n}.csv"))
    if not os.path.exists(path):
        check(f"csv exists: {n}", False, "missing " + os.path.relpath(path, ROOT)); continue
    cols = next(csv.reader(open(path)))
    want = list(spec["columns"])
    check(f"csv columns: {n}", set(cols) == set(want), f"extra={sorted(set(cols) - set(want))} missing={sorted(set(want) - set(cols))}")
    for k in spec.get("rename", {}):
        check(f"rename key is a column: {n}.{k}", k in spec["columns"], "rename for unknown column")
# 2
src = open(os.path.join(ROOT, "src", "build_payloads.py")).read()
read = set(re.findall(r'\bg\("([a-z_0-9]+)"\)', src))
llm_cols = set(reg["llm_summary"]["columns"]) - {"patient_id", "episode_id", "as_of_date"}
check("every g(...) column is declared in llm_summary", read <= llm_cols, sorted(read - llm_cols))
check("every llm_summary column is used by the pipeline", llm_cols <= read, sorted(llm_cols - read))
# 3
spec_md = open(os.path.join(ROOT, "config", "llm_output_spec.md")).read()
ren = reg["llm_summary"].get("rename", {})
NOT_TEXT = {"judge_status", "llm_model", "frequency_status", "schedule_status", "risk_raised_by_care_gaps", "delay_clinical_impact",
            "compliance_window_missed", "outcomes_at_risk", "comorbidities", "living_situation", "patient_stated_goal", "risk_rationale"}
undoc = sorted(c for c in llm_cols - NOT_TEXT if f"`{c}`" not in spec_md and f"`{ren.get(c, '-')}`" not in spec_md
               and c.replace("_json", "") not in spec_md)
check("LLM text columns documented in llm_output_spec.md", not undoc, undoc)
# 4
schema = json.load(open(os.path.join(ROOT, "config", "cqi_booklet_schema.json")))
payloads = [cqi_payload.compose(p) for p in build_payloads.build_payloads(Datasets(), "2026-08-15", sample=True)]
for p in payloads:
    pid = p["patient"]["patient_id"]
    check(f"payload keys == schema ({pid})", set(p) <= set(schema["properties"]), sorted(set(p) - set(schema["properties"])))
    check(f"payload validates ({pid})", not cqi_payload.validate(p), cqi_payload.validate(p)[:3])
    check(f"schema_version allowed ({pid})", p["schema_version"] in schema["properties"]["schema_version"]["enum"], p["schema_version"])
    lw = cqi_payload.length_warnings(p)
    check(f"sample text within LLM limits ({pid})", not lw, lw[:3])
missing_in_payload = set(schema["properties"]) - set(payloads[0]) - {"provenance"}
check("every schema section is produced by the pipeline", not missing_in_payload, sorted(missing_in_payload))
# 5
bsrc = open(os.path.join(ROOT, "src", "build_booklet.py")).read()
rendered = set(re.findall(r'"([A-Za-z0-9]+)": lambda: page_', bsrc))
check("every page key has a renderer", set(cqi_payload.PAGES) <= rendered, sorted(set(cqi_payload.PAGES) - rendered))
check("conditional pages are real pages", cqi_payload.CONDITIONAL_PAGES <= set(cqi_payload.PAGES))
from collections import Counter
cnt = Counter(k for _, _, _, dv, ks in cqi_payload.SECTIONS for k in ([dv] if dv else []) + ks)
check("every page belongs to exactly one section", set(cnt) == set(cqi_payload.PAGES) and max(cnt.values()) == 1,
      [k for k, v in cnt.items() if v > 1])
check("every content page has a Contents / divider title",
      {k for k in cqi_payload.PAGES if not k.startswith("div") and k != "cover"} <= set(build_booklet.PAGE_TITLES),
      sorted({k for k in cqi_payload.PAGES if not k.startswith("div") and k != "cover"} - set(build_booklet.PAGE_TITLES)))
check("brief pages are real pages", set(cqi_payload.BRIEF_PAGES) <= set(cqi_payload.PAGES))
check("tile evidence pages exist", set(cqi_payload.TILE_SECTION.values()) <= set(cqi_payload.PAGES))
fb_pages = {k for _, _, ks in cqi_payload.FEEDBACK_FINDINGS for k in ks}
check("feedback rows point at real pages", fb_pages <= set(cqi_payload.PAGES), sorted(fb_pages - set(cqi_payload.PAGES)))
ev = set(schema["properties"]["summary"]["properties"]["actions"]["items"]["properties"]["evidence_section"]["enum"])
check("action evidence sections are pages", ev <= set(cqi_payload.PAGES), sorted(ev - set(cqi_payload.PAGES)))
tips = set(re.findall(r'review_tip\(c, d, \[([^\]]*)\]', bsrc))
tip_keys = {k.strip().strip('"') for t in tips for k in t.split(",") if k.strip()}
fb_keys = {k for k, _, _ in cqi_payload.FEEDBACK_FINDINGS}
check("feedback tips reference real feedback rows", tip_keys <= fb_keys, sorted(tip_keys - fb_keys))
# 6
from pypdf import PdfReader
import collect_feedback
with tempfile.TemporaryDirectory() as t:
    for p in payloads:
        rd = cqi_payload.to_render(p)
        pdf = build_booklet.build(rd, os.path.join(t, "b.pdf"))
        r = PdfReader(pdf); names = set(r.get_fields() or {})
        pid, eid = p["patient"]["patient_id"], p["patient"]["episode_id"]
        expected = [k for k, _, ks in cqi_payload.FEEDBACK_FINDINGS if any(x in rd["pages"] for x in ks)]
        miss = [k for k in expected if f"cqi__{pid}__{eid}__{k}_rating" not in names]
        check(f"feedback form fields present ({pid})", not miss, miss)
        check(f"page count matches page plan ({pid})", len(r.pages) == len(rd["pages"]), f"{len(r.pages)} vs {len(rd['pages'])}")
        check(f"collect_feedback reads the form ({pid})", (pid, eid) in collect_feedback.read_pdf(pdf))
# 7
brand = yaml.safe_load(open(os.path.join(ROOT, "config", "branding.yaml")))
psrc = open(os.path.join(ROOT, "src", "cqi_payload.py")).read()
unused = [k for k in brand if f'"{k}"' not in bsrc and f'"{k}"' not in psrc]
check("every branding.yaml key is used", not unused, unused)

print(f"{len(ok)} checks passed")
for p_ in problems:
    print("  FAIL", p_)
sys.exit(1 if problems else 0)
