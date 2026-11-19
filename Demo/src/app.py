"""Loopback-only FastAPI demo and persistent per-query evidence."""

import hashlib
import json
import logging
import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from time import perf_counter, sleep
from uuid import UUID, uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles

from geometry import SETTINGS, export_glb, read_step
from library import LIBRARY as DEFAULT_LIBRARY
from library import MAX_STEP_BYTES, STEP_SUFFIXES, glb_blob
from make_samples import DEMO_SOURCE, SAMPLES, digest, write_json
from paths import ROOT, WEB
from search import retrieve, worker_pool
from similarity import similarity_score
from library_loader import LibraryLoader
from studio_data import StudioData

STUDIO = StudioData(os.environ["STEP_STUDIO_DATA"]) if "STEP_STUDIO_DATA" in os.environ else None
LIBRARY = STUDIO.library if STUDIO else Path(os.environ.get("STEP_DEMO_LIBRARY", DEFAULT_LIBRARY)).resolve()
QUERY_DIR = STUDIO.pending if STUDIO else Path(os.environ.get("STEP_DEMO_QUERIES", ROOT / "data/queries")).resolve()
library_lock = Lock()
archive_lock = Lock()
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


def process(directory, source_name, record, annotation, pool, started):
    def progress(percent, text):
        record.update(progress=percent, progress_text=text)
        set_status(directory, record)

    def indexing(done, total, current=""):
        if total:
            current_hint = f" · {current}" if current else ""
            progress(17 + 9 * done // total, f"正在更新基础模型库（{done}/{total}）{current_hint}")
        else:
            found = f"（已找到 {done} 个）" if done else ""
            progress(16, f"正在扫描基础模型库{found}")

    def comparison(done, total):
        if total:
            progress(30 + 60 * done // total, f"已完成 {done}/{total} 个候选模型的比对")
        else:
            progress(90, "基础模型库中暂无可比对的 STEP")

    try:
        parse_started = perf_counter()
        progress(8, "正在解析 STEP 并生成三维网格")
        source = (directory / source_name).read_bytes()
        mesh = read_step(source, record["query"]["file_name"])
        (directory / "query.glb").write_bytes(export_glb(mesh))
        parsing = perf_counter() - parse_started
        record["query"]["info"] = mesh.info
        record["query"]["glb"] = f"/api/queries/{record['id']}/model.glb"
        record["state"] = "comparing"
        retrieve_started = perf_counter()
        progress(16, "正在准备基础模型库")
        with library_lock:
            if STUDIO:
                STUDIO.refresh_base(indexing)
            progress(27, "正在筛选相似的基础模型")
            outcome = retrieve(LIBRARY, mesh, pool, SETTINGS["coarse_k"], annotation,
                               directory / "hole-samples.npz", comparison)
            progress(95, "正在保存比对结果")
            entries = [*outcome["candidates"], *outcome["review"]]
            if STUDIO and entries:
                (directory / "models").mkdir()
            for entry in entries:
                entry["glb"] = f"/api/queries/{record['id']}/models/{entry['id']}.glb"
                if STUDIO:
                    (directory / "models" / f"{entry['id']}.glb").write_bytes(glb_blob(LIBRARY, entry["id"]))
        result = {**record, **outcome, "state": "complete", "progress": 100, "progress_text": "比对完成",
                  "timing": {"parse_seconds": parsing, "compare_seconds": perf_counter() - retrieve_started,
                             "total_seconds": perf_counter() - started}}
        with archive_lock:
            set_status(directory, result)
            if STUDIO:
                try:
                    directory.rename(STUDIO.history / record["id"])
                except OSError as exc:
                    message = "无法将结果写入 Data/History"
                    set_status(directory, {**record, "state": "failed", "reason": message})
                    raise ValueError(message) from exc
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "本地几何处理失败，详细原因见服务日志"
        if not isinstance(exc, ValueError):
            logger.exception("Query %s failed", record["id"])
        set_status(directory, {**record, "state": "failed", "reason": message})


@asynccontextmanager
async def lifespan(app):
    if STUDIO:
        STUDIO.initialize()
    elif not LIBRARY.is_file():
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
    loader = LibraryLoader(studio=STUDIO, database=LIBRARY, work_lock=library_lock)
    app.state.library_loader = loader
    try:
        with worker_pool() as pool, ThreadPoolExecutor(max_workers=1) as queue:
            app.state.pool, app.state.queue = pool, queue
            yield
    finally:
        loader.close()


app = FastAPI(title="STEP 相似检索", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.exception_handler(Exception)
async def unhandled_error(request, exc):
    if isinstance(exc, (HTTPException, StarletteHTTPException, RequestValidationError)):
        raise exc
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "服务器处理请求失败，请重试。"}, status_code=500)


@app.middleware("http")
async def local_resources(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; frame-ancestors 'none'"
    )
    if request.url.path.startswith("/api/") or request.url.path.endswith((".html", ".js", ".css")) or request.url.path in ("", "/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/library")
def get_library():
    return app.state.library_loader.current(start_if_idle=True)


@app.post("/api/library/refresh")
def refresh_library():
    return app.state.library_loader.start()


@app.get("/api/library-folder")
def get_library_folder_status():
    available = STUDIO is not None and os.name == "nt" and hasattr(os, "startfile")
    return {"available": available}


@app.post("/api/library-folder")
def open_library_folder():
    if not STUDIO:
        raise HTTPException(409, "当前运行模式没有独立的基础模型库文件夹")
    if os.name != "nt" or not hasattr(os, "startfile"):
        raise HTTPException(501, "只能在 Windows Studio 中打开基础模型库文件夹")
    folder = STUDIO.base.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(folder))
    except OSError as exc:
        logger.exception("Could not open the Studio base library folder")
        raise HTTPException(500, "Windows 无法打开基础模型库文件夹") from exc
    return {"opened": True}


@app.post("/api/queries", status_code=202)
async def upload(file: UploadFile = File(...)):
    started = perf_counter()
    name = Path((file.filename or "").replace("\\", "/")).name
    if Path(name).suffix.lower() not in STEP_SUFFIXES:
        raise HTTPException(415, "请选择 .step 或 .stp 文件")
    if file.size is not None and file.size > MAX_STEP_BYTES:
        raise HTTPException(413, "STEP 文件超过 32 MB")
    query_id = uuid4().hex
    directory = QUERY_DIR / query_id
    directory.mkdir()
    source_name = name if STUDIO and not os.path.isreserved(name) else "source.step"
    incoming = directory / source_name
    size, sha256 = 0, hashlib.sha256()
    try:
        with incoming.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_STEP_BYTES:
                    raise HTTPException(413, "STEP 文件超过 32 MB")
                sha256.update(chunk)
                output.write(chunk)
        source_hash = sha256.hexdigest()
        record = {"id": query_id, "state": "parsing", "progress": 0,
                  "progress_text": "等待处理上传的 STEP", "query": {
            "file_name": name, "sha256": source_hash,
            "source": DEMO_SOURCE if source_hash in app.state.demo_hashes else "上传文件",
        }}
        set_status(directory, record)
    except Exception:
        # Clean up only the two files created by this request before enqueueing it.
        incoming.unlink(missing_ok=True)
        (directory / "status.tmp").unlink(missing_ok=True)
        directory.rmdir()
        raise
    finally:
        await file.close()
    app.state.queue.submit(process, directory, source_name, record, app.state.annotations.get(source_hash),
                           app.state.pool, started)
    return {"id": query_id, "state": "parsing", "progress": 0}


def query_record(query_id):
    with archive_lock:
        directory = STUDIO.query_directory(query_id.hex) if STUDIO else QUERY_DIR / query_id.hex
        return directory, read_status(directory)


@app.get("/api/queries/{query_id}")
def get_query(query_id: UUID):
    _, record = query_record(query_id)
    if record["state"] == "complete":
        for entry in [*record["candidates"], *record["review"]]:
            if "similarity" not in entry:
                entry["similarity"] = similarity_score(record["query"]["info"], entry["info"], entry["alignment"])
    return record


@app.get("/api/queries/{query_id}/model.glb")
def get_query_model(query_id: UUID):
    directory, result = query_record(query_id)
    if result["state"] != "complete":
        raise HTTPException(404, "查询模型尚不可查看")
    return FileResponse(directory / "query.glb", media_type="model/gltf-binary")


@app.get("/api/queries/{query_id}/models/{model_id}.glb")
def get_candidate_model(query_id: UUID, model_id: str):
    directory, result = query_record(query_id)
    if result["state"] != "complete" or not any(
        entry["id"] == model_id for entry in [*result["candidates"], *result["review"]]
    ):
        raise HTTPException(404, "此查询没有该候选模型")
    if STUDIO:
        return FileResponse(directory / "models" / f"{model_id}.glb", media_type="model/gltf-binary")
    glb = glb_blob(LIBRARY, model_id)
    if glb is None:
        raise HTTPException(404, "历史模型不存在")
    return Response(glb, media_type="model/gltf-binary")


app.mount("/", StaticFiles(directory=WEB, html=True), name="static")
