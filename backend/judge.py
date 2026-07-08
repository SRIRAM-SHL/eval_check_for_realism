"""
judge.py - LLM-as-judge for candidate end-of-interview questions.

Judges ONE question at a time using the exact system prompt supplied by the
product owner (SYSTEM_PROMPT below - do not edit its wording). Each question is
sent as the user message; the model returns strict JSON which we parse and
normalise. Questions are judged concurrently to keep the UI responsive.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import litellm
from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(_ENV_PATH)
load_dotenv()

MODEL = os.getenv("LLM_MODEL", "openai/gpt-5.4-mini")
API_KEY = os.getenv("OPENAI_API_KEY")
API_BASE = os.getenv("OPENAI_ENDPOINT", "https://labs.shl.com/llm-internal/")

MAX_WORKERS = int(os.getenv("JUDGE_WORKERS", "6"))

# Valid intent ids (used to validate the model's best_intent).
INTENTS = [
    "CompanyGoals", "ProductsAndServices", "CompanyChallenges",
    "CompanyGrowthOpportunity", "Diversity", "JobRoleResponsibilites",
    "Technologies", "ModeOfWorking", "WorkLoad", "SalaryandBonus",
    "WorkingCulture", "EmployeeWellness", "EmployeeChallenges",
    "InterviewerExperience", "InterviewerMotivation", "InterviewFeedback",
    "NextSteps", "Other",
]
VALID_FLAGS = {"company_trivia", "document_quiz", "not_candidate"}


# --- EXACT system prompt (do not modify) -----------------------------------
SYSTEM_PROMPT = """You are an expert evaluator. You judge ONE question at a time.

CONTEXT
The question was produced by an AI system. It is meant to be a question that a
JOB CANDIDATE could ask the INTERVIEWER at the END of an interview. Your job is
to judge how realistic and appropriate that question is, and to label it.

RETURN ONLY valid JSON in exactly this shape:
{
  "realism": <number 0.0-1.0>,
  "reason": "<one short sentence>",
  "best_intent": "<one intent id from the list below, or 'Other'>",
  "intent_fit": <number 0.0-1.0>,
  "flags": [<zero or more of: "company_trivia", "document_quiz", "not_candidate">]
}

FIELD RULES
- realism = how likely a REAL candidate would actually ask this at the end of an
  interview. 1.0 = natural, human, commonly asked. 0.0 = odd, robotic, or something
  nobody would really ask. Judge the question ONLY, not any document.
- best_intent = the closest matching intent from the list. If nothing fits, use "Other".
  Do NOT force a fit. A good question that fits nothing should be "Other" with a low intent_fit.
- intent_fit = how well the question matches best_intent (1.0 = perfect, 0.0 = weak).
- flags = add any that apply:
    "company_trivia"  -> asks a company FACT such as headquarters, revenue, size/
                         workforce, stock ticker, founding date, or abstract
                         mission/purpose. (Facts about the company, not the job.)
    "document_quiz"   -> phrased like a test about a document, e.g. "Who must follow
                         the Code of Conduct?" or "What are the four core values?".
                         Real candidates do not talk like a quiz.
    "not_candidate"   -> something the INTERVIEWER would ask, not the candidate.

INTENT LIST (id -> meaning)
- CompanyGoals -> the company's vision, mission, long-term goals, strategy, priorities, how it measures success
- ProductsAndServices -> what the company makes or sells, product lines, clients, industries served, differentiation
- CompanyChallenges -> the biggest challenges or market/competitive pressures the company faces
- CompanyGrowthOpportunity -> growth of the company, and career growth or promotion opportunities from the role
- Diversity -> diversity, equity, inclusion and belonging
- JobRoleResponsibilites -> the day-to-day duties and responsibilities of this specific role
- Technologies -> tech stack, tools, programming languages, engineering practices, coding and testing standards
- ModeOfWorking -> remote, hybrid, or work-from-office; work location arrangements
- WorkLoad -> working hours, workload, pace, shifts, on-call expectations
- SalaryandBonus -> salary, pay range, bonus, incentives, compensation
- WorkingCulture -> the team or company work culture, environment, and values in daily practice
- EmployeeWellness -> mental-health support, wellness, work-life balance, employee benefits
- EmployeeChallenges -> common difficulties employees face in the role or company
- InterviewerExperience -> the interviewer's own personal experience working at the company
- InterviewerMotivation -> what personally motivates the interviewer to work there
- InterviewFeedback -> feedback on the interview or on the candidate's performance today
- NextSteps -> the next steps, timeline, or process after this interview

SCORING GUIDANCE
- Be consistent and strict. Prefer round, repeatable scores.
- A grounded, true fact is NOT automatically a good question. Ask: would a real
  candidate actually say this out loud at the end of an interview?

EXAMPLES
Question: "Is this role hybrid or fully remote?"
-> {"realism":0.95,"reason":"A very common, natural end-of-interview question.","best_intent":"ModeOfWorking","intent_fit":0.98,"flags":[]}

Question: "What are the coding and testing standards expected in this role?"
-> {"realism":0.9,"reason":"A natural role-focused question a candidate would ask.","best_intent":"Technologies","intent_fit":0.85,"flags":[]}

Question: "Where is the company headquartered?"
-> {"realism":0.35,"reason":"A company fact candidates rarely ask at interview end.","best_intent":"ProductsAndServices","intent_fit":0.3,"flags":["company_trivia"]}

Question: "Who must follow the Code of Conduct?"
-> {"realism":0.15,"reason":"Reads like a quiz about a document, not a real candidate question.","best_intent":"Other","intent_fit":0.1,"flags":["document_quiz"]}
"""


# Appended AFTER the verbatim SYSTEM_PROMPT (never edits it) to make the judge
# role-aware when a JD role is available.
ROLE_CONTEXT_TEMPLATE = """

ROLE CONTEXT (additional instruction - the rules, JSON shape, intent list and flags above still apply exactly):
These questions were generated for a candidate interviewing specifically for this role:
  Role: {role}{seniority}{focus}
When scoring `realism`, judge how likely a candidate FOR THIS SPECIFIC ROLE would ask the question
at the end of the interview. A question that is natural and relevant for this role should score
higher; a question clearly irrelevant to this role is less realistic for this candidate. Do not
change the JSON shape, the intent ids, or the flag rules.
"""


def build_system_prompt(role: dict | None) -> str:
    """Return the base prompt, plus a role-context block if a role was found."""
    if not role or not role.get("role"):
        return SYSTEM_PROMPT
    seniority = f" (seniority: {role['seniority']})" if role.get("seniority") else ""
    focus = f"\n  Focus: {role['focus']}" if role.get("focus") else ""
    return SYSTEM_PROMPT + ROLE_CONTEXT_TEMPLATE.format(
        role=role["role"], seniority=seniority, focus=focus
    )


class JudgeNotConfigured(RuntimeError):
    pass


def require_key() -> None:
    if not API_KEY:
        raise JudgeNotConfigured(
            "OPENAI_API_KEY is not set. Add it to evaluation_25/.env before evaluating."
        )


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"Model did not return JSON: {text[:160]}")
    return json.loads(text[start : end + 1])


def _clamp01(v: Any) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _normalize(obj: dict[str, Any]) -> dict[str, Any]:
    intent = str(obj.get("best_intent", "Other")).strip()
    if intent not in INTENTS:
        intent = "Other"
    flags = obj.get("flags", []) or []
    flags = [f for f in flags if f in VALID_FLAGS]
    return {
        "realism": _clamp01(obj.get("realism")),
        "reason": str(obj.get("reason", "")).strip(),
        "best_intent": intent,
        "intent_fit": _clamp01(obj.get("intent_fit")),
        "flags": flags,
    }


def judge_one(question: str, system_prompt: str | None = None) -> dict[str, Any]:
    """Judge a single question; returns the normalised verdict dict."""
    require_key()
    resp = litellm.completion(
        model=MODEL,
        api_key=API_KEY,
        api_base=API_BASE,
        messages=[
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            {"role": "user", "content": f"Question: \"{question}\""},
        ],
        temperature=0.0,
    )
    content = resp.choices[0].message.content or ""
    return _normalize(_parse_json(content))


def judge_all(
    questions: list[str],
    on_result: Callable[[int, dict[str, Any]], None] | None = None,
    role: dict | None = None,
) -> list[dict[str, Any]]:
    """Judge every question concurrently. Results preserve input order.

    `on_result(index, verdict)` is called as each finishes (for live progress).
    `role` (from role_extractor) makes the judge role-aware when present.
    """
    require_key()
    system_prompt = build_system_prompt(role)
    results: list[dict[str, Any] | None] = [None] * len(questions)

    def work(i: int) -> tuple[int, dict[str, Any]]:
        try:
            verdict = judge_one(questions[i], system_prompt=system_prompt)
        except Exception as exc:  # noqa: BLE001 - report per-question failure, keep going
            verdict = {
                "realism": None, "reason": f"evaluation failed: {exc}",
                "best_intent": "Other", "intent_fit": None, "flags": [], "error": True,
            }
        return i, verdict

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for i, verdict in pool.map(work, range(len(questions))):
            results[i] = verdict
            if on_result:
                on_result(i, verdict)
    return [r for r in results if r is not None]


def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll per-question verdicts up into summary statistics."""
    scored = [r for r in items if isinstance(r.get("realism"), (int, float))]
    fits = [r["intent_fit"] for r in items if isinstance(r.get("intent_fit"), (int, float))]

    def avg(xs):
        return round(sum(xs) / len(xs), 3) if xs else None

    intent_counts: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    for r in items:
        intent_counts[r.get("best_intent", "Other")] = intent_counts.get(r.get("best_intent", "Other"), 0) + 1
        for f in r.get("flags", []):
            flag_counts[f] = flag_counts.get(f, 0) + 1

    realism_vals = [r["realism"] for r in scored]
    high = sum(1 for v in realism_vals if v >= 0.7)
    mid = sum(1 for v in realism_vals if 0.4 <= v < 0.7)
    low = sum(1 for v in realism_vals if v < 0.4)

    return {
        "count": len(items),
        "avg_realism": avg(realism_vals),
        "avg_intent_fit": avg(fits),
        "realism_buckets": {"high_>=0.7": high, "mid_0.4-0.7": mid, "low_<0.4": low},
        "intent_distribution": dict(sorted(intent_counts.items(), key=lambda kv: -kv[1])),
        "flag_counts": flag_counts,
        "flagged_questions": sum(1 for r in items if r.get("flags")),
    }
