"""
app.py - FastAPI backend for the 25-Question LLM-as-Judge evaluator.

Endpoints:
  GET  /                    -> the UI
  GET  /api/health          -> config / readiness
  POST /api/evaluate        -> upload a .txt of questions, stream per-question verdicts (SSE)
  POST /api/export          -> download the verdicts as JSON or CSV

Run:  uvicorn backend.app:app --reload  (from the evaluation_25 folder)
"""
from __future__ import annotations

import csv
import io
import json
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import judge, role_extractor
from .extractor import UnsupportedFileError, extract_text
from .parser import parse_questions

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB is plenty for a question list

app = FastAPI(title="25-Question Evaluator (LLM-as-Judge)", version="1.0.0")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": judge.MODEL,
        "llm_configured": bool(judge.API_KEY),
        "workers": judge.MAX_WORKERS,
    }


async def _read_questions(file: UploadFile) -> list[str]:
    name = (file.filename or "").lower()
    if not name.endswith((".txt", ".md", ".csv")):
        raise HTTPException(status_code=400, detail="Please upload a .txt (or .md/.csv) file of questions.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 2 MB).")
    text = data.decode("utf-8", errors="replace")
    questions = parse_questions(text)
    if not questions:
        raise HTTPException(status_code=422, detail="No questions found in the file.")
    return questions


async def _read_jd_role(jd_file: UploadFile | None) -> dict | None:
    """Extract text from an optional JD file and return the detected role dict."""
    if jd_file is None or not (jd_file.filename or "").strip():
        return None
    data = await jd_file.read()
    if not data:
        return None
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="JD file too large (max 8 MB).")
    try:
        text = extract_text(jd_file.filename, data)
    except UnsupportedFileError as exc:
        raise HTTPException(status_code=400, detail=f"JD file: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read JD: {exc}") from exc
    return role_extractor.extract_role(text)


@app.post("/api/extract_role")
async def extract_role_endpoint(jd_file: UploadFile = File(...)) -> dict:
    """Preview the role detected from an uploaded JD (used by the UI)."""
    role = await _read_jd_role(jd_file)
    return {"role": role or {"role": None, "seniority": None, "focus": None}}


@app.post("/api/evaluate")
async def evaluate(
    file: UploadFile = File(...),
    jd_file: UploadFile | None = File(None),
) -> StreamingResponse:
    questions = await _read_questions(file)
    if not judge.API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not set in evaluation_25/.env.")
    role = await _read_jd_role(jd_file)

    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    def event_stream():
        yield sse({"stage": "start", "total": len(questions), "role": role,
                   "questions": [{"index": i, "question": q} for i, q in enumerate(questions)]})

        # Bridge the threaded judge callbacks into this generator via a queue.
        q: "queue.Queue[dict | None]" = queue.Queue()
        results: list[dict] = [None] * len(questions)  # type: ignore[list-item]

        def on_result(i: int, verdict: dict) -> None:
            results[i] = verdict
            q.put({"stage": "result", "index": i, "question": questions[i], "verdict": verdict})

        def run() -> None:
            try:
                judge.judge_all(questions, on_result=on_result, role=role)
            except Exception as exc:  # noqa: BLE001
                q.put({"stage": "error", "detail": str(exc)})
            finally:
                q.put(None)  # sentinel -> stream complete

        threading.Thread(target=run, daemon=True).start()

        done = 0
        while True:
            item = q.get()
            if item is None:
                break
            if item.get("stage") == "error":
                yield sse(item)
                return
            done += 1
            item["done"] = done
            yield sse(item)

        clean = [r for r in results if r is not None]
        yield sse({
            "stage": "done",
            "count": len(clean),
            "role": role,
            "results": [
                {"index": i, "question": questions[i], **clean[i]} for i in range(len(clean))
            ],
            "aggregate": judge.aggregate(clean),
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ExportRequest(BaseModel):
    filename: str = "evaluation_25"
    format: str = "json"  # "json" | "csv"
    results: list[dict]
    aggregate: dict | None = None


@app.post("/api/export")
def export(req: ExportRequest) -> StreamingResponse:
    if not req.results:
        raise HTTPException(status_code=400, detail="Nothing to export.")
    safe = "".join(c for c in req.filename if c.isalnum() or c in ("-", "_", " ")).strip()
    safe = safe.replace(" ", "_") or "evaluation_25"

    if req.format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["#", "question", "realism", "best_intent", "intent_fit", "flags", "reason"])
        for i, r in enumerate(req.results, 1):
            writer.writerow([
                i, r.get("question", ""), r.get("realism", ""),
                r.get("best_intent", ""), r.get("intent_fit", ""),
                "|".join(r.get("flags", [])), r.get("reason", ""),
            ])
        data = buf.getvalue().encode("utf-8")
        media, ext = "text/csv", "csv"
    else:
        payload = {"aggregate": req.aggregate, "results": req.results}
        data = json.dumps(payload, indent=2).encode("utf-8")
        media, ext = "application/json", "json"

    return StreamingResponse(
        io.BytesIO(data),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{safe}.{ext}"'},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
