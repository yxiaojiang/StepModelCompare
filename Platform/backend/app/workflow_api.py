"""提供模型审核、规则后台和对比接口，依赖应用鉴权、仓储和调度状态。"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Path as RoutePath, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.auth.cache import AuthCacheValue
from app.compare.rule import ComparisonRule
from app.db.models import Model, SimilarityRule
from app.repository.compare_history import read_comparison, select_comparison_model
from app.repository.model_intake import accept_model_batch, review_model, upload_scope
from app.repository.quota import QuotaError, enqueue_compare
from app.repository.rules import RuleUnavailable, activate_rule, active_rule_snapshot, rule_record, save_rule
from app.uploads import UploadError, save_step, upload_form

_MAX_ID = 2**63 - 1
PathId = Annotated[int, RoutePath(gt=0, le=_MAX_ID)]
Cursor = Annotated[int, Query(ge=0, le=_MAX_ID)]
OptionalEnterprise = Annotated[int | None, Query(gt=0, le=_MAX_ID)]


class RuleInput(BaseModel):
    """保存已校验的完整规则模板，租户为空表示全局规则。"""

    name: str = Field(min_length=1, max_length=150)
    enterprise_id: int | None = Field(default=None, gt=0, le=_MAX_ID)
    rule: ComparisonRule


class RejectInput(BaseModel):
    """记录管理员驳回原因，作为同企重提时的反馈。"""

    reason: str = Field(min_length=1, max_length=512)


class ApproveInput(BaseModel):
    """审核通过时可由系统管理员将模型标记为正式公开演示来源。"""

    public_demo: bool = False


class SelectionInput(BaseModel):
    """输入本次对比中被用户选中的候选 ID。"""

    model_id: int = Field(gt=0, le=_MAX_ID)


class CompareOptions(BaseModel):
    """校验混合等待模式与候选数量。"""

    mode: Literal["sync", "async", "hybrid"]
    top_n: int = Field(gt=0)


def _multipart_docs(file_key, *, multiple=False, compare=False):
    file = {"type": "string", "format": "binary"}
    properties = {file_key: {"type": "array", "items": file} if multiple else file,
                  "username": {"type": "string"}, "password": {"type": "string", "format": "password"}}
    if compare:
        properties.update(mode={"type": "string", "enum": ["hybrid", "sync", "async"]}, top_n={"type": "integer"})
    return {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": [file_key], "properties": properties,
    }}}}}


def install_workflow_routes(router, current_user, require_admin, password_user) -> None:
    """在 router 注册业务端点，复用当前应用的 Header 和表单身份依赖。无返回值。"""
    @asynccontextmanager
    async def multipart_actor(request, max_files):
        actor = None
        if "authorization" in request.headers:
            actor = await run_in_threadpool(current_user, request)
            _limit_upload(request, actor)
        async with upload_form(request, max_files, request.app.state.settings.max_upload_request_bytes) as form:
            if actor is None:
                username, password = form.get("username"), form.get("password")
                if not isinstance(username, str) or not isinstance(password, str):
                    raise UploadError("AUTH_REQUIRED", 401)
                actor = await run_in_threadpool(password_user, request, username, password)
                _limit_upload(request, actor)
            yield actor, form

    async def upload_batch(request, enterprise_id, *, direct):
        settings = request.app.state.settings
        try:
            async with multipart_actor(request, settings.max_batch_files) as (actor, form):
                target = upload_scope(actor, enterprise_id, direct=direct)
                files = form.getlist("files[]") + form.getlist("files")
                if not files or any(not isinstance(file, UploadFile) for file in files):
                    raise UploadError("FILES_REQUIRED")
                result = await run_in_threadpool(
                    accept_model_batch, request.app.state.engine, _vault(request), actor, target, files,
                    direct=direct, file_limit=settings.max_file_size_bytes,
                )
                return JSONResponse(result, status_code=202)
        except UploadError as error:
            return _error(str(error), error.status)

    @router.post("/client/models/batch-upload", status_code=202, openapi_extra=_multipart_docs("files[]", multiple=True))
    async def upload_pending(request: Request, enterprise_id: OptionalEnterprise = None):
        """客户端批量上传 files[] 进入 PENDING；试用返回 403，非法件逐项反馈或请求超限返回 413。"""
        return await upload_batch(request, enterprise_id, direct=False)

    @router.post("/models/batch-upload", status_code=202, openapi_extra=_multipart_docs("files[]", multiple=True))
    async def upload_direct(request: Request, enterprise_id: OptionalEnterprise = None):
        """管理员选定企业直传进入 INDEXING，返回逐件台账；无权限返回 403。"""
        return await upload_batch(request, enterprise_id, direct=True)

    @router.get("/admin/models/pending", dependencies=[Depends(require_admin)])
    def pending_models(request: Request, after_id: Cursor = 0, enterprise_id: OptionalEnterprise = None) -> list[dict]:
        """分页列出管理员待审队列；游标超界返回 422。"""
        query = select(Model.id, Model.model_name, Model.enterprise_id, Model.md5, Model.uploader_id,
                       Model.uploaded_at, Model.status).where(Model.status == "PENDING", Model.id > after_id)
        if enterprise_id is not None:
            query = query.where(Model.enterprise_id == enterprise_id)
        with request.app.state.engine.connect() as connection:
            return [dict(row) for row in connection.execute(query.order_by(Model.id).limit(100)).mappings()]

    @router.post("/admin/models/{model_id}/approve")
    def approve(request: Request, model_id: PathId, body: ApproveInput | None = None,
                actor: AuthCacheValue = Depends(require_admin)):
        """通过 model_id 对应待审件并进入索引，可选公开标记；已处理或缺失返回 409。"""
        return _review(request, actor, model_id, approve=True, public_demo=bool(body and body.public_demo))

    @router.post("/admin/models/{model_id}/reject")
    def reject(request: Request, model_id: PathId, body: RejectInput,
               actor: AuthCacheValue = Depends(require_admin)):
        """驳回 model_id 对应待审件并保存原因；已处理或缺失返回 409。"""
        return _review(request, actor, model_id, approve=False, reason=body.reason)

    @router.get("/rules/active")
    def active_rules(request: Request, enterprise_id: OptionalEnterprise = None,
                     actor: AuthCacheValue = Depends(current_user)):
        """读取当前账号可用的生效规则及特征版本；未配置时返回 503。"""
        target = enterprise_id if actor.role == "SYS_ADMIN" else actor.enterprise_id
        try:
            with request.app.state.engine.connect() as connection:
                return active_rule_snapshot(connection, target)
        except RuleUnavailable as error:
            return _error(str(error), 503)

    @router.get("/admin/rules", dependencies=[Depends(require_admin)])
    def rules(request: Request, after_id: Cursor = 0):
        """分页读取规则版本、作用域和激活状态，每页最多 100 条。"""
        with request.app.state.engine.connect() as connection:
            rows = connection.execute(select(SimilarityRule).where(SimilarityRule.id > after_id)
                                      .order_by(SimilarityRule.id).limit(100)).all()
        records = []
        for row in rows:
            try:
                records.append(rule_record(row))
            except RuleUnavailable:
                records.append({"rule_id": row.id, "name": row.name, "is_active": row.is_active, "error_code": "RULE_INVALID"})
        return records

    @router.post("/admin/rules", status_code=201)
    def create_rule(request: Request, body: RuleInput, actor: AuthCacheValue = Depends(require_admin)):
        """存储一个不可变的新规则版本，用户以激活操作替换生效旧版本；企业不存在返回 404。"""
        try:
            return save_rule(request.app.state.engine, actor.user_id, body.name, body.enterprise_id, body.rule)
        except RuleUnavailable as error:
            return _error(str(error), 404)

    @router.post("/admin/rules/{rule_id}/activate")
    def activate(request: Request, rule_id: PathId, actor: AuthCacheValue = Depends(require_admin)):
        """原子切换同一作用域的生效版本，缺失返回 404，配置非法返回 422。"""
        try:
            result = activate_rule(request.app.state.engine, actor.user_id, rule_id)
        except RuleUnavailable as error:
            return _error(str(error), 422)
        return result if result is not None else _error("RULE_NOT_FOUND", 404)

    async def submit(request, enterprise_id, source):
        settings = request.app.state.settings
        engine = request.app.state.engine
        try:
            async with multipart_actor(request, 1) as (actor, form):
                file = form.get("file")
                if not isinstance(file, UploadFile):
                    raise UploadError("FILE_REQUIRED")
                try:
                    options = CompareOptions(
                        mode=form.get("mode", "async" if source == "WEB" else "hybrid"),
                        top_n=form.get("top_n", settings.default_top_n),
                    )
                except ValidationError:
                    raise UploadError("INVALID_COMPARE_OPTIONS") from None
                if options.top_n > settings.max_top_n:
                    raise UploadError("INVALID_COMPARE_OPTIONS")
                target = enterprise_id if actor.role == "SYS_ADMIN" else actor.enterprise_id
                folder = f"queries/enterprise-{target}" if target is not None else f"queries/user-{actor.user_id}"
                step = await run_in_threadpool(
                    save_step, _vault(request), folder, file.filename or "", file.file, settings.max_file_size_bytes,
                )
                try:
                    job_id = await run_in_threadpool(
                        enqueue_compare, engine, settings, actor, source=source, target_enterprise_id=enterprise_id,
                        query_filename=step.filename, query_md5=step.digest, query_storage_path=step.key,
                        auth_version=actor.auth_version, top_n=options.top_n,
                    )
                except (QuotaError, RuleUnavailable):
                    step.path.unlink(missing_ok=True)
                    raise
        except UploadError as error:
            return _error(str(error), error.status)
        except QuotaError as error:
            return _error(str(error), 503 if str(error) == "QUEUE_FULL" else 403)
        except RuleUnavailable as error:
            return _error(str(error), 503)
        poll_url = f"/api/v1/client/compare/{job_id}" if source == "CLIENT_API" else f"/api/v1/compare/jobs/{job_id}"
        # 响应超时只结束 HTTP 等待，计算任务继续持有原始预占，结果只从数据库读取。
        deadline = monotonic() + settings.sync_wait_timeout
        pending = {"job_id": job_id, "status": "QUEUED"}
        while options.mode != "async" and monotonic() < deadline:
            result = await run_in_threadpool(read_comparison, engine, actor, job_id, source)
            if result is None:
                return _error("JOB_NOT_FOUND", 404)
            if result["status"] in ("DONE", "FAILED"):
                return _response(result, poll_url)
            pending = result
            await asyncio.sleep(max(0, min(settings.queue_poll_seconds, deadline - monotonic())))
        return _response(pending, poll_url)

    @router.post("/client/compare", responses={202: {"description": "已接收，按 Location 轮询"}},
                 openapi_extra=_multipart_docs("file", compare=True))
    async def client_compare(request: Request, enterprise_id: OptionalEnterprise = None):
        """客户端提交 STEP，成功即时完成返回 200，异步或等候窗口结束返回 202。"""
        return await submit(request, enterprise_id, "CLIENT_API")

    @router.post("/compare/upload", responses={202: {"description": "已接收，按 Location 轮询"}},
                 openapi_extra=_multipart_docs("file", compare=True))
    async def web_compare(request: Request, enterprise_id: OptionalEnterprise = None):
        """网页提交 STEP，默认异步，也可用混合模式等待，返回 200 或 202。"""
        return await submit(request, enterprise_id, "WEB")

    @router.get("/client/compare/{job_id}")
    def poll_client(request: Request, job_id: PathId, actor: AuthCacheValue = Depends(current_user)):
        """按账号读取客户端任务结果，进行中返回 202，越权或任务不存在时返回 404。"""
        return _poll(request, actor, job_id, "CLIENT_API")

    @router.get("/compare/jobs/{job_id}")
    def poll_web(request: Request, job_id: PathId, actor: AuthCacheValue = Depends(current_user)):
        """按账号读取网页任务结果，进行中返回 202，越权或任务不存在时返回 404。"""
        return _poll(request, actor, job_id, "WEB")

    @router.post("/client/compare/{job_id}/select")
    def select_client(request: Request, job_id: PathId, body: SelectionInput,
                      actor: AuthCacheValue = Depends(current_user)):
        """客户端记录选择，同一候选最多累计一次，越权返回 404。"""
        return _select(request, actor, job_id, "CLIENT_API", body.model_id)

    @router.post("/compare/jobs/{job_id}/select")
    def select_web(request: Request, job_id: PathId, body: SelectionInput, actor: AuthCacheValue = Depends(current_user)):
        """网页记录选择，同一候选最多累计一次，越权返回 404。"""
        return _select(request, actor, job_id, "WEB", body.model_id)


def _vault(request):
    settings = request.app.state.settings
    if not settings.model_vault or request.app.state.engine.dialect.name != "postgresql":
        raise UploadError("STORAGE_UNAVAILABLE", 503)
    return Path(settings.model_vault)


def _limit_upload(request, actor):
    client = request.client.host if request.client else "unknown"
    if not request.app.state.upload_limiter.allow(f"ip:{client}", f"uid:{actor.user_id}"):
        raise UploadError("UPLOAD_RATE_LIMITED", 429)


def _review(request, actor, model_id, **values):
    directory = request.app.state.settings.model_vault
    result = review_model(request.app.state.engine, Path(directory) if directory else None, actor, model_id, **values)
    return result if result is not None else _error("MODEL_NOT_PENDING", 409)


def _poll(request, actor, job_id, source):
    result = read_comparison(request.app.state.engine, actor, job_id, source)
    return _response(result, request.url.path) if result is not None else _error("JOB_NOT_FOUND", 404)


def _select(request, actor, job_id, source, model_id):
    selected = select_comparison_model(request.app.state.engine, actor, job_id, source, model_id)
    return {"selected": selected} if selected is not None else _error("RESULT_NOT_FOUND", 404)


def _response(result, poll_url):
    headers = {"Cache-Control": "private, no-store"}
    if result["status"] in ("QUEUED", "RUNNING"):
        return JSONResponse(result, status_code=202, headers=dict(headers, Location=poll_url, **{"Retry-After": "1"}))
    if result["status"] == "FAILED":
        code = result["error_code"] or "COMPARE_FAILED"
        if code != "ERR_STEP_FILE_IO" and code.startswith(
            ("ERR_INVALID_STEP", "ERR_STEP_", "ERR_MODEL_TOO_COMPLEX", "ERR_COMPARE_QUERY_"),
        ):
            return JSONResponse(dict(result, error_code="INVALID_STEP_FILE"), status_code=422, headers=headers)
        if code in ("COMPARE_TIMEOUT", "QUEUE_WAIT_TIMEOUT", "ERR_COMPARE_TIMEOUT", "ERR_PARSE_TIMEOUT"):
            return JSONResponse(result, status_code=504, headers=headers)
        status = 409 if code in ("QUOTA_EXPIRED", "COMPARE_IDENTITY_CHANGED") else 500
        return JSONResponse(result, status_code=status, headers=headers)
    return JSONResponse(result, headers=headers)


def _error(code, status):
    return JSONResponse({"error_code": code}, status_code=status, headers={"Cache-Control": "private, no-store"})
