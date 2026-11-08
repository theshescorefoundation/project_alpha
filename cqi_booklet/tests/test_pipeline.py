"""Smoke tests: python tests/test_pipeline.py  (or pytest)."""
import copy, glob, json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from cqi_datasets import Datasets
import build_payloads, cqi_payload, build_booklet


def test_registry_loads_all_csvs():
    ds = Datasets()
    for n in ds.names():
        assert ds.check(n)["status"] == "ok", n


def test_cohort_and_payloads_valid():
    ps = build_payloads.build_payloads(Datasets(), "2026-08-15", sample=True)
    ids = sorted(p["patient"]["patient_id"] for p in ps)
    assert ids == ["9000123", "9000231", "9000345"]          # 9000400 low/stable is excluded
    for p in ps:
        assert cqi_payload.validate(cqi_payload.compose(p)) == []
    p1 = next(p for p in ps if p["patient"]["patient_id"] == "9000123")
    sn = next(d for d in p1["care_delivery"]["disciplines"] if d["discipline"] == "SN")
    assert [w["ordered"] for w in sn["weeks"]] == [3, 2, 2, 1, 1, 1, 1, 1, 1]
    assert p1["narratives"]["missed_care"] and p1["kpis"]["missed_care"] == 3
    assert len(cqi_payload.to_render(cqi_payload.compose(p1))["data_gaps"]) == 6


def test_render_long_and_empty_summaries():
    p = next(x for x in build_payloads.build_payloads(Datasets(), "2026-08-15", sample=True)
             if x["patient"]["patient_id"] == "9000123")
    long_ = copy.deepcopy(p)
    for k, lim in [("profile_risk", 2500), ("vitals_trajectory", 1500), ("care_delivery", 1500), ("missed_care", 1500), ("delay_impact", 800)]:
        pass
    long_["narratives"]["data_gaps"] = "|".join(f"Data gap number {i}: a conflicting or missing record that needs to be confirmed in the chart." for i in range(25))
    for k, lim in [("profile_risk", 2500), ("vitals_trajectory", 1500), ("care_delivery", 1500), ("missed_care", 1500), ("delay_impact", 800)]:
        long_["narratives"][k] = ("Long CQI text for layout testing. " * 80)[:lim]
    empty = copy.deepcopy(p); empty["narratives"] = {k: None for k in p["narratives"]}
    with tempfile.TemporaryDirectory() as t:
        for name, q in [("long", long_), ("empty", empty)]:
            q = cqi_payload.compose(q)
            assert cqi_payload.validate(q) == []
            assert os.path.getsize(build_booklet.build(q, os.path.join(t, name + ".pdf"))) > 10000


def test_fillable_feedback_roundtrip():
    from pypdf import PdfReader, PdfWriter
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import collect_feedback
    p = next(x for x in build_payloads.build_payloads(Datasets(), "2026-08-15", sample=True)
             if x["patient"]["patient_id"] == "9000123")
    with tempfile.TemporaryDirectory() as t:
        pdf = build_booklet.build(cqi_payload.compose(p), os.path.join(t, "b.pdf"))
        r = PdfReader(pdf)
        assert len(r.pages) == len(cqi_payload.page_numbers(cqi_payload.applicable_pages(cqi_payload.compose(p))))
        names = r.get_fields().keys()
        assert "cqi__9000123__7000456__r05_rating" in names and "cqi__9000123__7000456__overall_comment" in names
        w = PdfWriter(clone_from=pdf)
        for pg in w.pages:
            w.update_page_form_field_values(pg, {"cqi__9000123__7000456__r05_rating": "/disagree"}, auto_regenerate=False)
        w.write(os.path.join(t, "filled.pdf"))
        got = collect_feedback.read_pdf(os.path.join(t, "filled.pdf"))[("9000123", "7000456")]
        assert got["r05_rating"] == "disagree"


def test_brief_overview_and_glance_limits():
    from pypdf import PdfReader
    ps_ = [cqi_payload.compose(x) for x in build_payloads.build_payloads(Datasets(), "2026-08-15", sample=True)]
    p = copy.deepcopy(next(x for x in ps_ if x["patient"]["patient_id"] == "9000123"))
    w = "Clinical words fill this sentence to test sizing in the booklet layout. " * 10
    for k_, n in [("found", 110), ("risk", 160), ("care_delivery", 160), ("trajectory", 180), ("flags", 200)]:
        p["at_a_glance"][k_] = w[:n]
    for k_, n in [("saw", 150), ("flags", 150), ("action_window", 50)]:
        p["vitals"]["trajectory"][k_] = w[:n]
    assert cqi_payload.validate(p) == []
    assert not [x for x in cqi_payload.length_warnings(p) if x.startswith(("at_a_glance", "vitals.trajectory"))]
    with tempfile.TemporaryDirectory() as t:
        b = build_booklet.build(p, os.path.join(t, "brief.pdf"), brief=True)
        assert len(PdfReader(b).pages) == len(cqi_payload.page_numbers(cqi_payload.applicable_pages(p), cqi_payload.BRIEF_PAGES))
        assert not PdfReader(b).get_fields()                       # short version has no form fields
        ov = build_booklet.build_overview([cqi_payload.to_render(x) for x in ps_], os.path.join(t, "ov.pdf"), "Branch 0412", "08/15/2026")
        assert len(PdfReader(ov).pages) == 2


def test_discharge_recommendation():
    ps_ = {x["patient"]["patient_id"]: cqi_payload.compose(x) for x in build_payloads.build_payloads(Datasets(), "2026-08-15", sample=True)}
    near, far = ps_["9000345"], ps_["9000123"]
    dr = near["discharge_recommendation"]
    assert dr["recommendation"] in cqi_payload.DC_OPTIONS and dr["supports_discharge"] and dr["next_steps"]
    assert far.get("discharge_recommendation") is None
    assert "DC" in cqi_payload.to_render(near)["pages"] and "DC" not in cqi_payload.to_render(far)["pages"]


def test_conditional_pages_and_meds():
    ps_ = {x["patient"]["patient_id"]: cqi_payload.compose(x) for x in build_payloads.build_payloads(Datasets(), "2026-08-15", sample=True)}
    assert cqi_payload.applicable_pages(ps_["9000231"]) == set()            # no wounds, no meds, not near discharge
    assert cqi_payload.applicable_pages(ps_["9000123"]) == {"W", "M"}
    assert cqi_payload.applicable_pages(ps_["9000345"]) == {"DC", "W", "M"}
    m = ps_["9000123"]["medications"]
    assert len(m["high_risk"]) == 2 and m["flag"] and m["visits_with_check"] == 2 and m["visits_total"] == 7
    assert any(a["evidence_section"] == "M" for a in ps_["9000345"]["summary"]["actions"])


def test_referral_indicators():
    ri = cqi_payload.referral_indicators
    base = {"ma_eligible": True, "cwpco_enrolled": False, "cwrx_enrolled": False, "has_pcp": True,
            "nearest_cwpco_miles": 4.0, "cwpco_clinics_10mi": 1, "cwpco_clinics_20mi": 2, "active_med_count": 8}
    pco, rx = ri(base); assert pco["indicator"] == "Refer" and rx["indicator"] == "Refer"
    pco, _ = ri({**base, "nearest_cwpco_miles": 15.0, "cwpco_clinics_10mi": 0}); assert pco["indicator"] == "Consider"
    pco, _ = ri({**base, "nearest_cwpco_miles": 32.0, "cwpco_clinics_10mi": 0, "cwpco_clinics_20mi": 0}); assert pco["indicator"] == "Not indicated"
    pco, rx = ri({**base, "cwpco_enrolled": True, "cwrx_enrolled": True}); assert pco["indicator"] == rx["indicator"] == "Already enrolled"
    _, rx = ri({**base, "active_med_count": 3}); assert rx["indicator"] == "Not indicated"
    pco, _ = ri({**base, "ma_eligible": None}); assert pco["indicator"] == "Check eligibility"


def test_everything_in_sync():
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "check_sync.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


if __name__ == "__main__":
    for f in [test_registry_loads_all_csvs, test_cohort_and_payloads_valid, test_render_long_and_empty_summaries,
              test_fillable_feedback_roundtrip, test_brief_overview_and_glance_limits, test_referral_indicators, test_discharge_recommendation, test_conditional_pages_and_meds, test_everything_in_sync]:
        f(); print("PASS", f.__name__)
