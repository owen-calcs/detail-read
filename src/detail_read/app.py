"""FastAPI backend: upload a PDF, OCR it, detect connection details, solve them on connectionshub."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
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
    return {k: v for k, v in job.items() if k != "path" and not k.startswith("_")}


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

# Each user's own connectionshub key, entered in the UI and sent with every request that calls connectionshub.
KEY_HEADER = "X-Connectionshub-Key"


def _leaf_errors(e: BaseException) -> list[BaseException]:
    """The MCP transport wraps HTTP failures in (nested) exception groups; dig out the real causes."""
    if isinstance(e, BaseExceptionGroup):
        return [leaf for sub in e.exceptions for leaf in _leaf_errors(sub)]
    return [e]


def _api_error(e: Exception) -> HTTPException:
    if isinstance(e, HTTPException):
        return e
    leaves = _leaf_errors(e)
    if any(isinstance(x, mcp_client.MissingKeyError) for x in leaves):
        return HTTPException(401, str(next(x for x in leaves if isinstance(x, mcp_client.MissingKeyError))))
    for x in leaves:
        if isinstance(x, mcp_client.KeyRejectedError):
            return HTTPException(401, "connectionshub rejected the API key. Check it in the API key box.")
        status = getattr(getattr(x, "response", None), "status_code", None)
        if status in (401, 403) or re.search(r"\b(401|403)\b|Unauthori[sz]ed|Forbidden", str(x)):
            return HTTPException(401, "connectionshub rejected the API key. Check it in the API key box.")
    leaf = leaves[0]
    if isinstance(leaf, mcp_client.McpError):
        return HTTPException(502, str(leaf))
    return HTTPException(502, f"connectionshub call failed: {type(leaf).__name__}: {leaf}")


@app.get("/api/config")
def config():
    """Lets the UI say whether the user must enter a key or the server already has one."""
    return {"serverKey": bool(mcp_client.server_key())}


async def _definition(s, code: str) -> dict:
    path = CALC_CACHE / f"{code}.json"
    if path.exists():
        return json.loads(path.read_text())
    d = await mcp_client.call(s, "get_calculator", code=code)
    path.write_text(json.dumps(d))
    return d


@app.get("/api/calculators")
async def calculators(api_key: str | None = Header(None, alias=KEY_HEADER)):
    path = CALC_CACHE / "_list.json"
    if not path.exists():
        try:
            async with mcp_client.session(api_key) as s:
                data = await mcp_client.call(s, "list_calculators", limit=200)
        except Exception as e:
            raise _api_error(e)
        path.write_text(json.dumps(data))
    data = json.loads(path.read_text())
    return [t for t in data["templates"] if t.get("category") and t.get("hasApiOutput")]


@app.get("/api/calculators/{code}")
async def calculator(code: str, api_key: str | None = Header(None, alias=KEY_HEADER)):
    try:
        async with mcp_client.session(api_key) as s:
            d = await _definition(s, code)
    except Exception as e:
        raise _api_error(e)
    return {
        "code": d["code"], "name": d["name"],
        "presets": d.get("presets") or [],
        "sectionFields": [{"referenceId": f["referenceId"], "label": f["label"]} for f in calc._section_fields(d)],
        "defaultPreset": calc.pick_preset(d, None),
    }


def _account(api_key: str | None) -> str:
    """Stable, non-reversible id for the effective key, so each account gets its own project."""
    key = (api_key or "").strip() or mcp_client.server_key() or ""
    key = key.removeprefix("Bearer ").removeprefix("bearer ").strip()
    return hashlib.sha256(key.encode()).hexdigest()[:16]


async def _project_id(s, api_key: str | None) -> str:
    account = _account(api_key)
    async with _project_lock:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        if "projectId" in state:
            # Single-account format from before per-user keys. It belonged to the server key, so it can
            # only be filed once that key is known; until then keep it aside.
            legacy = state
            state = {"projects": {_account(None): legacy}} if mcp_client.server_key() else {"projects": {}, "legacy": legacy}
        projects = state.setdefault("projects", {})
        if account in projects:
            return projects[account]["projectId"]
        p = await mcp_client.call(s, "create_project", name="Detail Read (PDF OCR)", buildingStandard="US", unitSystem="FPS")
        projects[account] = {"projectId": p["projectId"], "projectUrl": p.get("projectUrl")}
        STATE.write_text(json.dumps(state))
        return p["projectId"]


class SolveRequest(BaseModel):
    job_id: str
    page: int
    detail_id: str
    code: str
    preset: str | None = None
    roles: dict[str, str] | None = None        # overrides, imperial section names
    loads_kn: dict[str, float] | None = None   # overrides: shear, tension, compression, moment, horizontal


async def _solve_detail(job: dict, page_index: int, detail: dict, code: str, api_key: str | None,
                        preset: str | None = None, role_overrides: dict[str, str] | None = None,
                        loads_kn: dict[str, float] | None = None) -> dict:
    """Solve one detail on connectionshub and record the result on the job. Raises HTTPException on failure."""
    cand = next((c for c in detail["candidates"] if c["code"] == code), None)
    roles = dict(cand["roles"]) if cand else {}
    roles.update({k: v for k, v in (role_overrides or {}).items() if v})
    loads = calc.loads_from_detail(detail["loads"]) if loads_kn is None else {k: v for k, v in loads_kn.items() if v}
    try:
        async with mcp_client.session(api_key) as s:
            definition = await _definition(s, code)
            preset = preset or calc.pick_preset(definition, cand["preset_hint"] if cand else None)
            attrs, notes = calc.build_inputs(definition, roles, detail["plates"], detail["bolts"], loads)
            pid = await _project_id(s, api_key)
            name = f"{Path(job['filename'] or 'drawing').stem} p{page_index + 1} {detail['title'][:30]}"
            result = await calc.validate_and_solve(s, definition, pid, preset, attrs, name, notes)
    except Exception as e:
        raise _api_error(e)

    out = {
        "calculator": {"code": definition["code"], "name": definition["name"], "preset": preset},
        "roles": roles,
        "loadsKn": loads,
        "attributes": attrs,
        "notes": notes,
        "summary": calc.summarise(definition, result),
    }
    job.setdefault("results", {})[f"{page_index}:{detail['id']}"] = out
    return out


def _find_detail(job: dict, page_index: int, detail_id: str) -> dict:
    page = next((p for p in job["pages"] if p["index"] == page_index), None)
    detail = next((d for d in (page or {}).get("details", []) if d["id"] == detail_id), None)
    if not detail:
        raise HTTPException(404, "Unknown page/detail.")
    return detail


@app.post("/api/solve")
async def solve(req: SolveRequest, api_key: str | None = Header(None, alias=KEY_HEADER)):
    job = _job(req.job_id)
    if job["status"] != "done":
        raise HTTPException(409, "Analysis is still running.")
    detail = _find_detail(job, req.page, req.detail_id)
    return await _solve_detail(job, req.page, detail, req.code, api_key, req.preset, req.roles, req.loads_kn)


# Each solve takes 10-60 s on connectionshub; a few at a time keeps the batch quick without flooding it.
SOLVE_ALL_CONCURRENCY = 4


def _solvable(job: dict) -> list[tuple[int, dict]]:
    return [(p["index"], d) for p in job["pages"] for d in p["details"] if d["candidates"]]


async def _run_solve_all(job: dict, targets: list[tuple[int, dict]], api_key: str | None) -> None:
    run = job["solveAll"]
    sem = asyncio.Semaphore(SOLVE_ALL_CONCURRENCY)

    async def one(page_index: int, detail: dict) -> None:
        key = f"{page_index}:{detail['id']}"
        async with sem:
            if run["cancel"]:
                return
            run["running"].append(key)
            try:
                await _solve_detail(job, page_index, detail, detail["candidates"][0]["code"], api_key)
            except HTTPException as e:
                run["errors"][key] = e.detail
                if e.status_code == 401:  # a bad key fails every solve; stop rather than repeat it
                    run["cancel"] = True
                    run["fatal"] = e.detail
            finally:
                run["running"].remove(key)
                run["done"] += 1

    await asyncio.gather(*(one(p, d) for p, d in targets))
    run["status"] = "cancelled" if run["cancel"] else "done"


@app.post("/api/jobs/{job_id}/solve-all")
async def solve_all(job_id: str, skip_solved: bool = True, api_key: str | None = Header(None, alias=KEY_HEADER)):
    """Solve every detail that has a detected calculator, using its detected values."""
    job = _job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "Analysis is still running.")
    if (job.get("solveAll") or {}).get("status") == "running":
        raise HTTPException(409, "Solve all is already running.")
    if not ((api_key or "").strip() or mcp_client.server_key()):
        raise _api_error(mcp_client.MissingKeyError("No connectionshub API key: enter yours in the app first."))
    solved = set(job.get("results", {})) if skip_solved else set()
    targets = [(p, d) for p, d in _solvable(job) if f"{p}:{d['id']}" not in solved]
    job["solveAll"] = {"status": "running", "total": len(targets), "done": 0, "running": [], "errors": {},
                       "cancel": False, "fatal": None}
    # Keep a reference so the task isn't garbage-collected mid-run. The key lives only in this task's memory.
    job["_solveAllTask"] = asyncio.create_task(_run_solve_all(job, targets, api_key))
    return job["solveAll"]


@app.post("/api/jobs/{job_id}/solve-all/cancel")
def cancel_solve_all(job_id: str):
    run = _job(job_id).get("solveAll")
    if not run or run["status"] != "running":
        raise HTTPException(409, "Solve all is not running.")
    run["cancel"] = True  # solves already in flight finish; queued ones are skipped
    return run


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main() -> None:
    import uvicorn
    uvicorn.run("detail_read.app:app", host="127.0.0.1", port=8000)
