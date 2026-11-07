"""
Deterministic pre-checks run before the LLM. Two purposes:
  1. Hard facts the model can't miss: they are injected into the prompt as HINTS (model still decides).
  2. Shown in the UI as "Автопроверки" / "Статистика звонка" — unique, explainable, zero-cost signals.

Everything here is regex + timestamps. RU / KZ / EN patterns.
"""
from __future__ import annotations
import re
from datetime import datetime

PATTERNS = {
    "asks_password": re.compile(r"\b(парол|пароль|password|құпия ?сөз|пин[- ]?код|pin ?code|cvv|cvc)\b", re.I),
    "asks_full_card": re.compile(r"(номер карты|card number|карта нөмір|полный номер|все 16|16 цифр|16 digits)", re.I),
    "guaranteed_return": re.compile(r"(точно вырастут|гарантирован\w* (доход|прибыл|рост)|guaranteed (profit|return)|100% (вырастет|прибыль)|міндетті түрде өседі|кепілд\w* табыс)", re.I),
    "investment_advice": re.compile(r"(купите (лучше |обязательно )?(акции|облигации)|советую купить|советую продать|рекомендую купить|you should buy|buy .{0,20} stock|сатып ал\w* кеңес)", re.I),
    "money_to_operator": re.compile(r"(перевед\w+ (мне|на мою|на мой (номер|счет|счёт|карту))|скин\w+ (мне )?на (карту|каспи|kaspi)|на мой каспи|мои реквизиты|лично мне|send (it )?to my (card|account|kaspi)|маған аудар\w*|менің картама)", re.I),
    "asks_personal_number": re.compile(r"(ваш (личный |мобильный )?номер (телефона )?(дайте|скиньте|напишите)|напишите мне (в )?(whatsapp|ватсап|telegram|телеграм)|мой (личный )?номер\b|(whatsapp|ватсап|telegram|телеграм)\s*\+?\d|жеке нөмір|give me your (personal )?(phone )?number|my personal number)", re.I),
    "rude": re.compile(r"(как хотите|ваши проблемы|не мои проблемы|сами виноваты|not my problem|whatever you want|өз проблема)", re.I),
    "greeting": re.compile(r"(здравствуй|добрый (день|вечер|утро)|доброе утро|приветству|сәлем|қайырлы|hello|good (morning|afternoon|evening)|hi\b)", re.I),
    "introduction": re.compile(r"(меня зовут|freedom|фридом|менің атым|my name is|this is \w+)", re.I),
    "identification": re.compile(r"(последние (4|четыре) цифр|кодов\w+ слов|дату рождения|фио|назовите (ваш[еи]? )?(фамилию|имя)|last (4|four) digits|date of birth|code ?word|соңғы (4|төрт) сан|туған күн)", re.I),
    "closing": re.compile(r"(до свидан|всего добр|хорошего (дня|вечера)|остались (ли )?(ещё |еще )?вопрос|сау бол|жақсы күн|goodbye|have a (good|nice)|anything else)", re.I),
}

PII_LIKE = re.compile(r"\b\d{16}\b|\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b")  # full card number in text (by anyone)


def _parse_ts(ts: str | None) -> float | None:
    if not ts:
        return None
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", ts):  # mm:ss or hh:mm:ss
        parts = [int(p) for p in ts.split(":")]
        return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def run(conversation: dict) -> dict:
    transcript = conversation.get("transcript", [])
    flags: list[dict] = []
    op_turns = [(i, t) for i, t in enumerate(transcript, start=1) if t.get("speaker") == "operator"]  # 1-based
    for i, t in op_turns:
        text = str(t.get("text", ""))
        for name in ("money_to_operator", "asks_personal_number", "asks_password", "asks_full_card", "guaranteed_return", "investment_advice", "rude"):
            if PATTERNS[name].search(text):
                flags.append({"flag": name, "turn": i, "severity": "medium" if name in ("rude", "investment_advice") else "high"})
    for i, t in enumerate(transcript, start=1):
        if PII_LIKE.search(str(t.get("text", ""))):
            flags.append({"flag": "card_number_in_chat", "turn": i, "severity": "high"})

    found = {
        "greeting_turns": [i for i, t in op_turns if PATTERNS["greeting"].search(str(t.get("text", "")))],
        "introduction_turns": [i for i, t in op_turns if PATTERNS["introduction"].search(str(t.get("text", "")))],
        "closing_turns": [i for i, t in op_turns if PATTERNS["closing"].search(str(t.get("text", "")))],
        "identification_turns": [i for i, t in op_turns if PATTERNS["identification"].search(str(t.get("text", "")))],
        "first_operator_has_greeting": bool(op_turns) and bool(PATTERNS["greeting"].search(str(op_turns[0][1].get("text", "")))),
        "first_operator_has_introduction": bool(op_turns) and bool(PATTERNS["introduction"].search(str(op_turns[0][1].get("text", "")))),
        "first_operator_turn": op_turns[0][0] if op_turns else None,
        "last_operator_turn": op_turns[-1][0] if op_turns else None,
        "operator_turns": len(op_turns),
        "client_turns": sum(1 for t in transcript if t.get("speaker") == "client"),
    }

    # timing: operator response latency, longest silence, talk share
    times = [_parse_ts(t.get("timestamp")) for t in transcript]
    latencies: list[float] = []
    longest_gap = 0.0
    for i in range(1, len(transcript)):
        a, b = times[i - 1], times[i]
        if a is None or b is None:
            continue
        gap = max(0.0, b - a)
        longest_gap = max(longest_gap, gap)
        if transcript[i].get("speaker") == "operator" and transcript[i - 1].get("speaker") == "client":
            latencies.append(gap)
    words = lambda sp: sum(len(str(t.get("text", "")).split()) for t in transcript if t.get("speaker") == sp)  # noqa: E731
    ow, cw = words("operator"), words("client")
    stats = {
        "duration_sec": (max(x for x in times if x is not None) - min(x for x in times if x is not None)) if any(x is not None for x in times) else None,
        "avg_operator_latency_sec": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "max_operator_latency_sec": round(max(latencies), 1) if latencies else None,
        "longest_gap_sec": round(longest_gap, 1),
        "operator_talk_share": round(ow / (ow + cw), 2) if ow + cw else None,
        "ended_by": transcript[-1].get("speaker") if transcript else None,
    }
    return {"flags": flags, "found": found, "stats": stats}


def as_hints(pre: dict) -> str:
    """Compact text block for the prompt."""
    f, s = pre["found"], pre["stats"]
    lines = [
        f"- operator turns: {f['operator_turns']} (first #{f['first_operator_turn']}, last #{f['last_operator_turn']})",
        f"- FIRST operator turn #{f['first_operator_turn']}: greeting={f['first_operator_has_greeting']}, introduction={f['first_operator_has_introduction']}",
        f"- greeting keywords in operator turns: {f['greeting_turns'] or 'none'}; introduction (name/company): {f['introduction_turns'] or 'none'}; identification request: {f['identification_turns'] or 'none'}; closing keywords: {f['closing_turns'] or 'none'}",
        f"- conversation ended by: {s['ended_by']}",
    ]
    if s["avg_operator_latency_sec"] is not None:
        lines.append(f"- avg operator response latency: {s['avg_operator_latency_sec']}s, max {s['max_operator_latency_sec']}s")
    if pre["flags"]:
        lines.append("- RISK FLAGS (regex, verify in context): " + "; ".join(f"{x['flag']} at #{x['turn']}" for x in pre["flags"]))
    return "\n".join(lines)
