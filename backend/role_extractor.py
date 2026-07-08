"""
role_extractor.py - A small agent that reads a Job Description and returns the
role it is hiring for.

The extracted role (title + seniority + one-line focus) is later injected as extra
CONTEXT into the judge, so realism is scored for a candidate interviewing for THAT
specific role rather than a generic candidate.
"""
from __future__ import annotations

import json
import os

import litellm
from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(_ENV_PATH)
load_dotenv()

MODEL = os.getenv("LLM_MODEL", "openai/gpt-5.4-mini")
API_KEY = os.getenv("OPENAI_API_KEY")
API_BASE = os.getenv("OPENAI_ENDPOINT", "https://labs.shl.com/llm-internal/")

MAX_JD_CHARS = 12000

ROLE_SYSTEM = """You read a JOB DESCRIPTION (JD) and identify the single role it is hiring for.
If the text is clearly NOT a job description, set role to null.
Return ONLY valid JSON in exactly this shape:
{
  "role": "<concise role title, e.g. 'Senior Backend Engineer', or null>",
  "seniority": "<e.g. 'Early-career', 'Senior', 'Lead', or null>",
  "focus": "<one short phrase: the core focus / domain of the role, or null>"
}
Rules:
- role = the job title a candidate is interviewing for. Keep it concise (no company name, no boilerplate).
- Only use what the JD states. Do not invent a role if none is present.
"""


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON in role extractor reply")
    return json.loads(text[start : end + 1])


def extract_role(jd_text: str) -> dict:
    """Return {"role", "seniority", "focus"}. role is None if not found."""
    empty = {"role": None, "seniority": None, "focus": None}
    if not API_KEY or not (jd_text or "").strip():
        return empty
    try:
        resp = litellm.completion(
            model=MODEL,
            api_key=API_KEY,
            api_base=API_BASE,
            messages=[
                {"role": "system", "content": ROLE_SYSTEM},
                {"role": "user", "content": f"JOB DESCRIPTION:\n\"\"\"{jd_text[:MAX_JD_CHARS]}\"\"\""},
            ],
            temperature=0.0,
        )
        obj = _parse_json(resp.choices[0].message.content or "")
    except Exception:  # noqa: BLE001 - role context is best-effort
        return empty

    role = obj.get("role")
    role = str(role).strip() if role else None
    if role and role.lower() in ("null", "none", "n/a", ""):
        role = None
    return {
        "role": role,
        "seniority": (str(obj.get("seniority")).strip() if obj.get("seniority") else None),
        "focus": (str(obj.get("focus")).strip() if obj.get("focus") else None),
    }
