# 25-Question Evaluator (LLM-as-Judge)

A small dashboard that takes a text file of candidate questions and judges each
one with an LLM, using the exact evaluation rubric in `backend/judge.py`
(`SYSTEM_PROMPT`). Every question is judged **one at a time** and scored for:

- **realism** (0–1) — how likely a real candidate would ask it at the end of an interview
- **best_intent** — the closest intent from a fixed list (or `Other`)
- **intent_fit** (0–1) — how well it matches that intent
- **flags** — `company_trivia`, `document_quiz`, `not_candidate`

## Input format
Upload a `.txt` (or `.md`/`.csv`). The parser accepts:
- numbered lists (`1. ...`, `1) ...`), bulleted lists (`- `, `* `, `• `), or one question per line.
- It ignores `Evidence:` lines, so a raw **V3 dashboard export drops straight in**.

## Run

```bash
cd evaluation_25
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn backend.app:app --host 0.0.0.0 --port 8020
```

Open http://localhost:8020 (or your server IP on port 8020).

## API
- `GET /api/health`
- `POST /api/evaluate` — multipart `file`; streams per-question verdicts (SSE) + a final aggregate.
- `POST /api/export` — download results as `json` or `csv`.

## Config (`.env`)
Uses the SHL OpenAI-compatible endpoint:
```
OPENAI_API_KEY=...
OPENAI_ENDPOINT="https://labs.shl.com/llm-internal/"
LLM_MODEL="openai/gpt-5.4-mini"
# JUDGE_WORKERS=6   # concurrent question evaluations
```
