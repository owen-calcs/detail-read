"""FastAPI backend: upload a PDF, OCR it, detect connection details, solve them on connectionshub."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import calc, mcp_client, ocr
from .detect import find_details
from .extract import extract_page

ROOT = Path.cwd()
WORK = ROOT / ".detail-read"
UPLOADS = WORK / "uploads"
CALC_CACHE = WORK / "calculators"
STATE = WORK / "state.json"
STATIC = Path(__file__).parent / "static"
for d in (UPLOADS, CALC_CACHE):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Detail Read")
JOBS: dict[str, dict[str, Any]] = {}
_project_lock = asyncio.Lock()


# ---------- analysis ----------

def _analyse(job_id: str) -> None:
    job = JOBS[job_id]
    try:
        def progress(done: int, total: int) -> None:
            job.update(progress=done, total=total)
        job["total"] = ocr.page_count(job["path"])
        pages = []
        for res in ocr.read_pdf(job["path"], progress):
            entities = extract_page(res["lines"])
            details = find_details(entities, res["lines"], tuple(res["size"]))
            pages.append({
                "index": res["index"],
                "size": res["size"],
                "entities": entities,
                "details": [asdict(d) for d in details],
                "ocrLines": len(res["lines"]),
            })
        job["pages"] = pages
        job["best"] = _best_detail(pages)
        job["status"] = "done"
    except Exception as e:  # surfaced to the UI
        job.update(status="error", error=f"{type(e).__name__}: {e}")


def _best_detail(pages: list[dict]) -> dict | None:
    best, key = None, None
    for p in pages:
        for d in p["details"]:
            if not d["candidates"]:
                continue
            k = (d["candidates"][0]["score"], len(d["loads"]) > 0, len(d["members"]))
            if key is None or k > key:
                best, key = {"page": p["index"], "detail": d["id"]}, k
    return best


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf") and file.content_type != "application/pdf":
        raise HTTPException(400, "Please upload a PDF.")
    job_id = uuid.uuid4().hex[:12]
    path = UPLOADS / f"{job_id}.pdf"
    path.write_bytes(await file.read())
    try:
        ocr.page_count(str(path))
    except Exception:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "That file could not be opened as a PDF.")
    JOBS[job_id] = {"id": job_id, "filename": file.filename, "path": str(path), "status": "running",
                    "progress": 0, "total": 0, "pages": None}
    threading.Thread(target=_analyse, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = _job(job_id)
    return {k: v for k, v in job.items() if k != "path"}


@app.get("/api/jobs/{job_id}/pages/{index}.png")
def page_image(job_id: str, index: int, width: int = 1400):
    job = _job(job_id)
    cache = UPLOADS / f"{job_id}-{index}-{width}.png"
    if not cache.exists():
        cache.write_bytes(ocr.render_preview(job["path"], index, min(width, 3000)))
    return FileResponse(cache, media_type="image/png")


def _job(job_id: str) -> dict:
    if job_id not in JOBS:
        raise HTTPException(404, "Unknown job (the server may have restarted; upload again).")
    return JOBS[job_id]


# ---------- calculators ----------

async def _definition(s, code: str) -> dict:
    path = CALC_CACHE / f"{code}.json"
    if path.exists():
        return json.loads(path.read_text())
    d = await mcp_client.call(s, "get_calculator", code=code)
    path.write_text(json.dumps(d))
    return d


@app.get("/api/calculators")
async def calculators():
    path = CALC_CACHE / "_list.json"
    if not path.exists():
        async with mcp_client.session() as s:
            data = await mcp_client.call(s, "list_calculators", limit=200)
        path.write_text(json.dumps(data))
    data = json.loads(path.read_text())
    return [t for t in data["templates"] if t.get("category") and t.get("hasApiOutput")]


@app.get("/api/calculators/{code}")
async def calculator(code: str):
    async with mcp_client.session() as s:
        d = await _definition(s, code)
    return {
        "code": d["code"], "name": d["name"],
        "presets": d.get("presets") or [],
        "sectionFields": [{"referenceId": f["referenceId"], "label": f["label"]} for f in calc._section_fields(d)],
        "defaultPreset": calc.pick_preset(d, None),
    }


async def _project_id(s) -> str:
    async with _project_lock:
        if STATE.exists():
            pid = json.loads(STATE.read_text()).get("projectId")
            if pid:
                return pid
        p = await mcp_client.call(s, "create_project", name="Detail Read (PDF OCR)", buildingStandard="US", unitSystem="FPS")
        STATE.write_text(json.dumps({"projectId": p["projectId"], "projectUrl": p.get("projectUrl")}))
        return p["projectId"]


class SolveRequest(BaseModel):
    job_id: str
    page: int
    detail_id: str
    code: str
    preset: str | None = None
    roles: dict[str, str] | None = None        # overrides, imperial section names
    loads_kn: dict[str, float] | None = None   # overrides: shear, tension, compression, moment, horizontal


@app.post("/api/solve")
async def solve(req: SolveRequest):
    job = _job(req.job_id)
    if job["status"] != "done":
        raise HTTPException(409, "Analysis is still running.")
    page = next((p for p in job["pages"] if p["index"] == req.page), None)
    detail = next((d for d in (page or {}).get("details", []) if d["id"] == req.detail_id), None)
    if not detail:
        raise HTTPException(404, "Unknown page/detail.")
    cand = next((c for c in detail["candidates"] if c["code"] == req.code), None)
    roles = dict(cand["roles"]) if cand else {}
    roles.update({k: v for k, v in (req.roles or {}).items() if v})
    loads = calc.loads_from_detail(detail["loads"]) if req.loads_kn is None else {k: v for k, v in req.loads_kn.items() if v}

    try:
        async with mcp_client.session() as s:
            definition = await _definition(s, req.code)
            preset = req.preset or calc.pick_preset(definition, cand["preset_hint"] if cand else None)
            attrs, notes = calc.build_inputs(definition, roles, detail["plates"], detail["bolts"], loads)
            pid = await _project_id(s)
            name = f"{Path(job['filename'] or 'drawing').stem} p{req.page + 1} {detail['title'][:30]}"
            result = await calc.validate_and_solve(s, definition, pid, preset, attrs, name, notes)
    except mcp_client.McpError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        raise HTTPException(502, f"connectionshub call failed: {type(e).__name__}: {e}")

    return {
        "calculator": {"code": definition["code"], "name": definition["name"], "preset": preset},
        "roles": roles,
        "loadsKn": loads,
        "attributes": attrs,
        "notes": notes,
        "summary": calc.summarise(definition, result),
    }


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main() -> None:
    import uvicorn
    uvicorn.run("detail_read.app:app", host="127.0.0.1", port=8000)
