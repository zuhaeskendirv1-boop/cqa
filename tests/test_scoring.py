import json
from pathlib import Path
from backend.scoring import compute_totals, normalize, snap_to_steps

CHK = json.loads((Path(__file__).resolve().parent.parent / "backend" / "default_checklist.json").read_text(encoding="utf-8"))
FULL = {"contact": "yes", "need": "yes", "identification": "yes", "consultation": 20, "empathy": "yes", "dialog_management": 10, "closing": "yes", "gross_error": "no"}


def test_official_weights_sum_to_100():
    assert sum(c["weight"] for c in CHK["criteria"] if c["type"] != "gross_error") == 100


def test_perfect_is_100():
    r = compute_totals(CHK, FULL)
    assert r["total"] == 100 and r["gross_error"] is False


def test_na_redistributes_weight():
    r = compute_totals(CHK, dict(FULL, need="N/A", closing="N/A"))
    assert r["total"] == 100
    assert r["criteria"]["need"]["na"] is True
    assert r["criteria"]["identification"]["effective_weight"] == 25.0  # 20 / 80


def test_no_applicable_criteria_gives_none():
    r = compute_totals(CHK, {c["id"]: "N/A" for c in CHK["criteria"]})
    assert r["total"] is None


def test_step_scales_snap():
    cons = next(c for c in CHK["criteria"] if c["id"] == "consultation")
    assert snap_to_steps(cons, 13) == 15 and snap_to_steps(cons, 12) == 10 and snap_to_steps(cons, 99) == 20
    dm = next(c for c in CHK["criteria"] if c["id"] == "dialog_management")
    assert snap_to_steps(dm, 5) == 4 and snap_to_steps(dm, 6) == 7
    assert normalize(dm, 7) == 0.7


def test_gross_error_is_separate_without_points():
    r = compute_totals(CHK, dict(FULL, gross_error="yes"))
    assert r["gross_error"] and r["gross_error_criteria"] == ["gross_error"]
    assert r["total"] == 100  # policy flag: points untouched
    assert r["criteria"]["gross_error"]["points"] is None
    chk = json.loads(json.dumps(CHK)); chk["rules"]["gross_error_policy"] = "zero"
    assert compute_totals(chk, dict(FULL, gross_error="yes"))["total"] == 0
    chk["rules"].update(gross_error_policy="cap", gross_error_cap=50)
    assert compute_totals(chk, dict(FULL, gross_error="yes"))["total"] == 50


def test_rounding_two_decimals():
    r = compute_totals(CHK, dict(FULL, consultation=15, dialog_management=7))
    # 100 - 20*0.25 - 15*0.3 = 100 - 5 - 4.5 = 90.5
    assert r["total"] == 90.5


def test_rounding_math_explicit():
    # applicable weight 85 (need N/A); points: all full except consultation 15/20 -> 20*0.75=15
    r = compute_totals(CHK, dict(FULL, need="N/A", consultation=15))
    assert r["total"] == round((10 + 20 + 15 + 15 + 15 + 5) / 85 * 100, 2) == 94.12


def test_sections():
    r = compute_totals(CHK, dict(FULL, empathy="no", dialog_management=0))
    assert r["sections"]["Коммуникация"]["score"] == 0
    assert r["sections"]["Начало диалога"]["score"] == 100
    assert "Грубые ошибки" not in r["sections"]
