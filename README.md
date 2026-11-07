# CQA — Conversation Quality Analytics

AI scoring of support-operator dialogs (RU / KZ / EN, chats and calls) against a configurable checklist — with evidence links, N/A handling, weighted totals, deterministic pre-checks, appeals, reviewer decisions, hand-labeling and a live calibration report against ground truth.

Built for the Freedom Broker CQA Hackathon. Frontend is plain HTML/CSS/JS (ready to embed as a Dynamics 365 web resource); backend is FastAPI + SQLite; LLM is pluggable (cloud or local).

## Run in 60 seconds

```bash
git clone <repo> && cd cqa
cp .env.example .env            # choose the LLM provider (see below)
./run.sh                        # Windows: run.bat     |  Docker: docker compose up --build
# open http://localhost:8000
```

Tests: `pytest -q tests` (13 tests: scoring rules incl. steps / N/A / gross error, API flow, roles, import/export).

### Get the datasets

```bash
python tools/fetch_dataset.py "<GET link from the organizers' PDF, group_id=DEV>"     # -> data/dev.json, auto-imported on first start
```
Control set: paste the new link (new `group_id`) into the UI import field — the backend fetches it server-side (no CORS) — then **«Оценить все неоценённые»** → **«Экспорт CSV»**.

### Choose an AI backend (`.env`)

| Provider | `.env` | Notes |
|---|---|---|
| Cloud, OpenAI-compatible | `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, `LLM_MODEL=gpt-4o-mini` | OpenRouter / DeepSeek / Groq via `OPENAI_BASE_URL` |
| Local | `LLM_PROVIDER=ollama`, `LLM_MODEL=qwen2.5:14b` | `ollama pull qwen2.5:14b`; Qwen is decent at RU/KZ |
| Demo without a model | `LLM_PROVIDER=mock` | keyword heuristics, UI only — the top bar shows **MOCK** |

Per-language overrides: `LLM_MODEL_KZ=gpt-4o` (stronger model only for Kazakh), `EVAL_SAMPLES_KZ=3` (voting only for Kazakh).
Cost readout (calls, tokens, ≈USD) in the top bar / `GET /api/stats`. gpt-4o-mini, per-criterion, 1 sample ≈ 8 calls, ≈ 20k tokens, ≈ $0.005 per dialog.

## Features (mapped to the hackathon checklist)

| Requirement | Where |
|---|---|
| Checklist editor: criteria, type (yes/no, scale with labels), weights, N/A rule, critical flag, rounding, gross-error policy, **domain knowledge block** | tab **Чек-лист**; every save = new version; each evaluation freezes a snapshot |
| Import from the organizers' API (server-side) or JSON file; batch scoring with progress (persisted job) | tab **Диалоги** |
| Card: numbered transcript, score per criterion, explanation (RU), **clickable evidence turn**, "action missing → checked range", N/A with reason, sections, total, gross error | **Диалоги → Открыть** |
| Calls: audio player, ▶ jump to a turn by timestamp, call stats (duration, operator latency, talk share) | card, voice channel |
| Deterministic pre-checks: money-to-operator requests, personal phone number / private messenger, password/PIN/CVV, full card number, guaranteed returns, investment advice, rude phrases, greeting+introduction in the operator's first turn, identification requests → ⚠ on turns and hints to the LLM | `backend/prechecks.py` |
| Appeal per criterion with a comment → reviewer queue (disputed score, comment, dialog fragment) → confirm / change with a reason → total recomputed; card shows AI vs final | tab **Апелляции** |
| **Proof of live analysis**: full LLM trace (system prompt, prompt, raw response) per evaluation | card → «Показать трассу LLM» |
| **Ground-truth labeling + calibration report** (MAE, RMSE, bias, ±5/±10 hit-rate, per-language, per-criterion agreement, worst dialogs) | tab **Калибровка** |
| Roles: operator / reviewer / admin (header `X-Role`; optional tokens in `.env`) | top-right selector |
| Export CSV / JSON of final scores | «Экспорт CSV», `/api/export.json` |

## Official checklist (default) and scoring rules

| # | Criterion | Type | Weight |
|---|---|---|---|
| 1 | Установление контакта | Да/Нет | 10 |
| 2 | Выявление потребности | Да/Нет/N/A | 15 |
| 3 | Идентификация | Да/Нет/N/A | 20 |
| 4 | Консультация | 0/5/10/15/20 | 20 |
| 5 | Эмпатия | Да/Нет | 15 |
| 6 | Управление диалогом | 0/4/7/10 | 15 |
| 7 | Завершение | Да/Нет/N/A | 5 |
| 8 | Грубые ошибки | Да/Нет, **без баллов** | — |

`backend/default_checklist.json` carries this structure; **paste the exact criterion wording and N/A conditions from the organizers' PDF into the editor** (field `description` / `na_hint`) — the model reads that text.

* Answer → 0..1 (yes/no → 1/0; step scale → snapped to the nearest allowed step, e.g. 13 → 15, then (v−min)/(max−min)).
* **Total** = Σ(w·s) / Σ(w over applicable criteria) × 100, rounded to **2 decimals**. N/A criteria drop out and their weight is redistributed proportionally (effective weight shown in the card). **No applicable criteria → total is not computed (null).**
* **Sections**: same formula inside each section.
* **Gross error**: a separate criterion type without points; «Да» = violation (money to the operator personally, asking for a personal phone number / private messenger, password / full card number, knowingly false info, insults). Shown as a flag next to the total; policy `flag` (default) / `cap` / `zero` is configurable.
* Reviewer decision overrides an answer (validated against the type / steps) → total recomputed from the frozen checklist snapshot.

## How scoring works — and how to hit Ground Truth

1. Transcript is numbered `[i] speaker: text`; only `operator` turns are scored.
2. Prompt = strict system prompt (lower-of-two rule, independent criteria) + channel/language + **domain notes** from the checklist + **regex hints** + criterion definition, answer schema, N/A rule + **calibration examples** (`data/calibration.json`).
3. One call per criterion (`EVAL_MODE=per_criterion`) or one per dialog (`batch`). Optional self-consistency voting (`EVAL_SAMPLES=3`: majority / median).
4. Answers are validated server-side (no disallowed N/A, scale clamped, turn indices in range).

**Tuning loop (this is where the points are):**

1. Paste the organizers' real 8 criteria into the editor — wording is what the model reads. Fill *domain notes* with the facts auditors use.
2. **Калибровка** tab: label 10–15 DEV dialogs as a team (see `docs/LABELING_GUIDE.md`).
3. Score them → read MAE / bias / worst dialogs. `bias > +3` = AI too generous → tighten wording, add middle-value examples to `calibration.json`.
4. Re-score (`python tools/compare_gt.py --reeval`) until MAE stops dropping. Try `--samples 3`, another model, `LLM_MODEL_KZ`.
5. Before the control stage: `EVAL_SAMPLES=3`, per-criterion mode. Stability beats speed on 10 dialogs.

## Layout

```
backend/   main.py (API, roles) · evaluator.py (prompts, voting, trace) · scoring.py · prechecks.py · llm.py (openai/ollama/mock) · db.py (SQLite) · default_checklist.json
frontend/  index.html · css/app.css · js/api.js · js/util.js · js/app.js (router) · js/components/{dialogs,card,checklist,review,calibration}.js
tools/     fetch_dataset.py · compare_gt.py
tests/     test_scoring.py · test_api.py
data/      sample_dev.json (4 demo dialogs) · calibration.example.json · cqa.sqlite3 (created at runtime)
docs/      LABELING_GUIDE.md · DEMO_SCRIPT.md
```

## API (short)

| Method | Path | |
|---|---|---|
| GET / POST / DELETE | `/api/checklists[/{id}]` | POST/DELETE need `X-Role: admin` |
| POST | `/api/conversations/import` | `{url}` or `{payload}` |
| GET | `/api/conversations[/{id}]` | `{id}` includes pre-checks |
| POST | `/api/evaluate` · `/api/evaluate/batch` · GET `/api/evaluate/batch/status` | |
| GET | `/api/evaluations?conversation_id=` · `/api/evaluations/{id}` · `/api/evaluations/{id}/trace` | |
| POST | `/api/evaluations/{id}/appeals` · `/api/appeals/{id}/decision` (reviewer/admin) | |
| GET / POST / DELETE | `/api/ground_truth` · GET `/api/calibration/report` | |
| GET | `/api/export.csv` · `/api/export.json` · `/api/stats` · `/api/health` | |

## Known limits (said out loud)

* Roles are a header, not a login — per the hackathon guide. Tokens in `.env` make reviewer/admin require a secret.
* Calls are judged from the transcript; audio is for the human (player + per-turn seek), not transcribed again.
* SQLite is single-node; fine for hundreds of dialogs, swap `db.py` for Postgres beyond that.
* `mock` provider is for UI demos only and is clearly labelled in the top bar.
