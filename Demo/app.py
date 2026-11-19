"""Loopback-only FastAPI demo and persistent per-query evidence."""

import hashlib
import json
import logging
import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter, sleep
from uuid import UUID, uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from geometry import ROOT, SETTINGS, export_glb, read_step
from library import LIBRARY as DEFAULT_LIBRARY, catalog, glb_blob
from make_samples import DEMO_SOURCE, SAMPLES, digest, write_json
from search import retrieve, worker_pool

LIBRARY = Path(os.environ.get("STEP_DEMO_LIBRARY", DEFAULT_LIBRARY)).resolve()
QUERY_DIR = Path(os.environ.get("STEP_DEMO_QUERIES", ROOT / "data/queries")).resolve()
MAX_UPLOAD = 32 * 1024 * 1024
logger = logging.getLogger("uvicorn.error")

# The Windows registry may advertise JavaScript as text/plain.
mimetypes.init()
mimetypes.add_type("text/javascript", ".js")


def set_status(directory, record):
    temporary = directory / "status.tmp"
    write_json(temporary, record)
    # Windows can briefly lock an open status file across replace().
    for attempt in range(5):
        try:
            temporary.replace(directory / "status.json")
            return
        except PermissionError:
            if attempt == 4:
                raise
            sleep(0.02)


def read_status(directory):
    status = directory / "status.json"
    if not status.is_file():
        raise HTTPException(404, "查询不存在")
    return json.loads(status.read_text(encoding="utf-8"))


def process(directory, record, annotation, pool, started):
    try:
        parse_started = perf_counter()
        source = (directory / "source.step").read_bytes()
        mesh = read_step(source, record["query"]["file_name"])
        (directory / "query.glb").write_bytes(export_glb(mesh))
        parsing = perf_counter() - parse_started
        record["query"]["info"] = mesh.info
        record["query"]["glb"] = f"/api/queries/{record['id']}/model.glb"
        record["state"] = "comparing"
        set_status(directory, record)
        retrieve_started = perf_counter()
        outcome = retrieve(LIBRARY, mesh, pool, SETTINGS["coarse_k"], annotation,
                           directory / "hole-samples.npz")
        for entry in [*outcome["candidates"], *outcome["review"]]:
            entry["glb"] = f"/api/queries/{record['id']}/models/{entry['id']}.glb"
        result = {**record, **outcome, "state": "complete",
                  "timing": {"parse_seconds": parsing, "compare_seconds": perf_counter() - retrieve_started,
                             "total_seconds": perf_counter() - started}}
        set_status(directory, result)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "本地几何处理失败，详细原因见服务日志"
        if not isinstance(exc, ValueError):
            logger.exception("Query %s failed", record["id"])
        set_status(directory, {**record, "state": "failed", "reason": message})


@asynccontextmanager
async def lifespan(app):
    if not LIBRARY.is_file():
        raise RuntimeError("缺少 SQLite 历史库，请先执行 library.py 导入清单")
    QUERY_DIR.mkdir(parents=True, exist_ok=True)
    for directory in QUERY_DIR.iterdir():
        if not (directory / "status.json").is_file():
            continue
        previous = read_status(directory)
        if previous["state"] in ("parsing", "comparing"):
            set_status(directory, {**previous, "state": "failed", "reason": "服务重启，请重新上传"})
    app.state.annotations = {}
    registration = SAMPLES / "rehearsal/cases.json"
    if registration.is_file():
        cases = json.loads(registration.read_text(encoding="utf-8"))
        app.state.annotations = {item["sha256"]: item for item in cases if "hole" in item}
    app.state.demo_hashes = {digest(path) for path in SAMPLES.rglob("*")
                             if path.is_file() and path.suffix.lower() in (".step", ".stp")}
    with worker_pool() as pool, ThreadPoolExecutor(max_workers=1) as queue:
        app.state.pool, app.state.queue = pool, queue
        yield


app = FastAPI(title="STEP 相似检索", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def local_resources(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; frame-ancestors 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/library")
def get_library():
    return catalog(LIBRARY)


@app.post("/api/queries", status_code=202)
async def upload(file: UploadFile = File(...)):
    started = perf_counter()
    name = Path((file.filename or "").replace("\\", "/")).name
    if Path(name).suffix.lower() not in (".step", ".stp"):
        raise HTTPException(415, "请选择 .step 或 .stp 文件")
    if file.size is not None and file.size > MAX_UPLOAD:
        raise HTTPException(413, "STEP 文件超过 32 MB")
    query_id = uuid4().hex
    directory = QUERY_DIR / query_id
    directory.mkdir()
    size, sha256 = 0, hashlib.sha256()
    try:
        with (directory / "source.step").open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "STEP 文件超过 32 MB")
                sha256.update(chunk)
                output.write(chunk)
        source_hash = sha256.hexdigest()
        record = {"id": query_id, "state": "parsing", "query": {
            "file_name": name, "sha256": source_hash,
            "source": DEMO_SOURCE if source_hash in app.state.demo_hashes else "上传文件",
        }}
        set_status(directory, record)
    except Exception:
        # Clean up only the two files created by this request before enqueueing it.
        (directory / "source.step").unlink(missing_ok=True)
        (directory / "status.tmp").unlink(missing_ok=True)
        directory.rmdir()
        raise
    finally:
        await file.close()
    app.state.queue.submit(process, directory, record, app.state.annotations.get(source_hash),
                           app.state.pool, started)
    return {"id": query_id, "state": "parsing"}


@app.get("/api/queries/{query_id}")
def get_query(query_id: UUID):
    return read_status(QUERY_DIR / query_id.hex)


@app.get("/api/queries/{query_id}/model.glb")
def get_query_model(query_id: UUID):
    directory = QUERY_DIR / query_id.hex
    if not read_status(directory)["state"] == "complete":
        raise HTTPException(404, "查询模型尚不可查看")
    return FileResponse(directory / "query.glb", media_type="model/gltf-binary")


@app.get("/api/queries/{query_id}/models/{model_id}.glb")
def get_candidate_model(query_id: UUID, model_id: str):
    result = read_status(QUERY_DIR / query_id.hex)
    if result["state"] != "complete" or not any(
        entry["id"] == model_id for entry in [*result["candidates"], *result["review"]]
    ):
        raise HTTPException(404, "此查询没有该候选模型")
    glb = glb_blob(LIBRARY, model_id)
    if glb is None:
        raise HTTPException(404, "历史模型不存在")
    return Response(glb, media_type="model/gltf-binary")


app.mount("/", StaticFiles(directory=ROOT / "dist", html=True), name="static")
