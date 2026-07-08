"""
parser.py - Pull the individual questions out of an uploaded text file.

Handles the common shapes we see:
  - Numbered lists:   "1. How is the team structured?"  /  "1) ..."
  - Bulleted lists:   "- ..."  /  "* ..."  /  bullet chars
  - Plain one-per-line questions.

It ignores blank lines and any "Evidence:" lines (the V3 export writes an
`Evidence:` line under each question), so a raw V3 export drops straight in.
"""
from __future__ import annotations

import re

_NUM_PREFIX = re.compile(r"^\s*\d+\s*[\.\):\-]\s+")
_BULLET_PREFIX = re.compile(r"^\s*[\-\*\u2022\u2023\u25E6\u2043]\s+")
_EVIDENCE_LINE = re.compile(r"^\s*(evidence|source|answer)\s*[:\-]", re.IGNORECASE)


def parse_questions(text: str) -> list[str]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    # Pass 1: prefer explicitly enumerated / bulleted lines.
    enumerated: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or _EVIDENCE_LINE.match(line):
            continue
        if _NUM_PREFIX.match(line):
            enumerated.append(_NUM_PREFIX.sub("", line).strip())
        elif _BULLET_PREFIX.match(line):
            enumerated.append(_BULLET_PREFIX.sub("", line).strip())

    if enumerated:
        return _dedupe_keep_order([q for q in enumerated if q])

    # Pass 2: no markers -> every non-evidence, non-empty line is a question.
    plain = [
        line.strip()
        for line in lines
        if line.strip() and not _EVIDENCE_LINE.match(line.strip())
    ]
    return _dedupe_keep_order(plain)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out
