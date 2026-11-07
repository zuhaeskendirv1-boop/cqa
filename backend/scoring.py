"""
Scoring engine: weights, step scales, N/A redistribution, sections, gross error, rounding.

Criterion types
  yes_no       yes = 1.0, no = 0.0
  scale        value snapped to the nearest allowed step (scale.steps, e.g. [0,5,10,15,20] or [0,4,7,10]),
               then normalised (v - min) / (max - min)
  gross_error  separate criterion WITHOUT points: "yes" = gross error happened. Never enters the weighted sum.
  N/A          excluded; its weight is redistributed proportionally among applicable criteria
               (== weighted mean over applicable criteria).

If no criterion is applicable the total is None (not 0).
"""
from __future__ import annotations
from typing import Any

YES = ("yes", "да", "true", "1", "иә", "y")


def is_na(answer: Any) -> bool:
    return answer is None or (isinstance(answer, str) and answer.strip().upper() in ("N/A", "NA"))


def is_yes(answer: Any) -> bool:
    if isinstance(answer, bool):
        return answer
    return str(answer).strip().lower() in YES


def snap_to_steps(criterion: dict, value: Any) -> float:
    """Clamp to [min,max] and snap to the nearest allowed step (ties round up)."""
    sc = criterion.get("scale") or {"min": 0, "max": 1}
    lo, hi = float(sc.get("min", 0)), float(sc.get("max", 1))
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    v = max(lo, min(hi, v))
    steps = sc.get("steps")
    if steps:
        return float(min(sorted(steps, reverse=True), key=lambda s: abs(float(s) - v)))
    return float(round(v))


def normalize(criterion: dict, answer: Any) -> float | None:
    """Score in [0,1], or None for N/A."""
    if is_na(answer):
        return None
    t = criterion.get("type", "yes_no")
    if t in ("yes_no", "gross_error"):
        return 1.0 if is_yes(answer) else 0.0
    if t == "scale":
        sc = criterion.get("scale") or {"min": 0, "max": 1}
        lo, hi = float(sc.get("min", 0)), float(sc.get("max", 1))
        v = snap_to_steps(criterion, answer)
        return 0.0 if hi == lo else (v - lo) / (hi - lo)
    return 0.0


def compute_totals(checklist: dict, answers: dict[str, Any]) -> dict:
    rules = checklist.get("rules", {})
    total_scale = float(rules.get("total_scale", 100))
    rounding = int(rules.get("rounding", 2))
    gross_policy = rules.get("gross_error_policy", "flag")  # flag | cap | zero
    gross_cap = float(rules.get("gross_error_cap", 0))

    per: dict[str, dict] = {}
    applicable_weight = 0.0
    weighted_sum = 0.0
    gross_criteria: list[str] = []

    for c in checklist["criteria"]:
        cid = c["id"]
        raw = answers.get(cid, "N/A")
        w = float(c.get("weight", 0))
        if c.get("type") == "gross_error":
            hit = (not is_na(raw)) and is_yes(raw)
            per[cid] = {"score": None, "weight": 0.0, "effective_weight": 0.0, "points": None, "na": False, "gross": hit}
            if hit:
                gross_criteria.append(cid)
            continue
        s = normalize(c, raw)
        if s is None:
            per[cid] = {"score": None, "weight": w, "effective_weight": 0.0, "points": None, "na": True}
            continue
        applicable_weight += w
        weighted_sum += w * s
        per[cid] = {"score": s, "weight": w, "effective_weight": None, "points": None, "na": False}

    has_applicable = applicable_weight > 0
    for d in per.values():
        if d["na"] or d.get("gross") is not None:
            continue
        ew = d["weight"] / applicable_weight * 100 if has_applicable else 0.0
        d["effective_weight"] = round(ew, 2)
        d["points"] = round(d["score"] * ew * total_scale / 100, rounding)

    gross_error = bool(gross_criteria)
    total: float | None
    if not has_applicable:
        total = None
    else:
        total = weighted_sum / applicable_weight * total_scale
        if gross_error and gross_policy == "zero":
            total = 0.0
        elif gross_error and gross_policy == "cap":
            total = min(total, gross_cap)
        total = round(total, rounding)

    sections: dict[str, dict] = {}
    for c in checklist["criteria"]:
        if c.get("type") == "gross_error":
            continue
        sec = c.get("section") or "Общее"
        d = per[c["id"]]
        s = sections.setdefault(sec, {"weight": 0.0, "sum": 0.0, "applicable": 0, "na": 0})
        if d["na"]:
            s["na"] += 1
            continue
        s["weight"] += d["weight"]
        s["sum"] += d["weight"] * d["score"]
        s["applicable"] += 1
    section_scores = {
        name: {"score": (round(s["sum"] / s["weight"] * total_scale, rounding) if s["weight"] else None), "applicable": s["applicable"], "na": s["na"]}
        for name, s in sections.items()
    }

    return {
        "total": total,
        "total_scale": total_scale,
        "applicable_weight": applicable_weight,
        "criteria": per,
        "sections": section_scores,
        "gross_error": gross_error,
        "gross_error_criteria": gross_criteria,
    }
