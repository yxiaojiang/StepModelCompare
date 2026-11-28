"""提供阶段 3 首切片的认证、企业和邀请码薄 API。

路由只做输入校验、身份解析和服务调用；跨企业模型查询仍必须调用 Repository 可见性守卫。
"""

from base64 import b64decode
from binascii import Error as Base64Error
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select

from app.auth.cache import AuthCacheValue
from app.auth.service import AuthenticationError, AuthenticationRateLimited
from app.db.model_features import active_feature_version, vault_path
from app.db.models import Enterprise, InviteCode, Model
from app.enterprise import EnterpriseError, EnterpriseService
from app.repository.visibility import VisibilityError, visible_glb_query, visible_model_query


class TokenRequest(BaseModel):
    """登录请求体。"""

    username: str = Field(min_length=1, max_length=150)
    password: SecretStr = Field(min_length=1, max_length=1024)


class EnterpriseRequest(BaseModel):
    """企业创建请求体。"""

    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=100)
    storage_quota_mb: int = Field(default=10240, gt=0, le=2**63-1)


class InviteRequest(BaseModel):
    """邀请码创建请求体。"""

    enterprise_id: int = Field(gt=0, le=2**63-1)
    code: SecretStr = Field(min_length=1, max_length=128)
    max_uses: int = Field(gt=0, le=2**31-1)
    expires_at: datetime


class BindInviteRequest(BaseModel):
    """邀请码绑定请求体。"""

    code: SecretStr = Field(min_length=1, max_length=128)


def build_api_router() -> APIRouter:
    """构造认证和企业管理路由；数据库引擎从 request.app.state.engine 读取。"""
    router = APIRouter(prefix="/api/v1")

    def services(request: Request):
        if request.app.state.auth_service is None:
            raise HTTPException(503, "鉴权服务尚未配置")
        return request.app.state.auth_service, request.app.state.token_service

    def current_user(request: Request) -> AuthCacheValue:
        """解析 Basic 或 Bearer 凭证并复核当前账号；缺失、失效或超限返回 401/429。"""
        auth, tokens = services(request)
        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        try:
            if scheme.lower() == "basic" and len(credential) <= 8192:
                username, password = b64decode(credential, validate=True).decode("utf-8").split(":", 1)
                return auth.authenticate(username, password, _client_ip(request))
            if scheme.lower() == "bearer" and len(credential) <= 8192:
                payload = tokens.decode(credential)
                user_id = int(payload["sub"])
                version = payload["auth_version"]
                if 0 < user_id < 2**63 and type(version) is int and 0 < version < 2**31:
                    return auth.resolve_token_user(user_id, version)
        except AuthenticationRateLimited:
            raise HTTPException(429, "登录尝试过于频繁") from None
        except (KeyError, TypeError, ValueError, Base64Error):
            pass
        raise HTTPException(401, "凭证无效或已失效", headers={"WWW-Authenticate": "Bearer"})

    def require_admin(user: AuthCacheValue = Depends(current_user)) -> AuthCacheValue:
        if user.role != "SYS_ADMIN":
            raise HTTPException(403, "需要系统管理员权限")
        return user

    @router.post("/auth/token")
    @router.post("/auth/login")
    def issue_token(request: Request, body: TokenRequest) -> dict:
        """校验用户名密码并返回短期 Bearer 令牌。"""
        auth, tokens = services(request)
        try:
            user = auth.authenticate(body.username, body.password.get_secret_value(), _client_ip(request))
        except AuthenticationRateLimited:
            raise HTTPException(429, "登录尝试过于频繁") from None
        except AuthenticationError:
            raise HTTPException(401, "用户名或密码错误") from None
        return {"access_token": tokens.issue(user), "token_type": "bearer", "expires_in": request.app.state.settings.jwt_ttl,
                "role": user.role, "enterprise_id": user.enterprise_id}

    @router.post("/admin/enterprises")
    def create_enterprise(request: Request, body: EnterpriseRequest, actor: AuthCacheValue = Depends(require_admin)) -> dict:
        """创建企业并返回企业 ID。"""
        try:
            enterprise_id = EnterpriseService(request.app.state.engine).create_enterprise(
                body.name, body.code, body.storage_quota_mb, actor_id=actor.user_id,
            )
        except EnterpriseError as error:
            raise HTTPException(400, str(error)) from error
        return {"enterprise_id": enterprise_id}

    @router.post("/admin/invite-codes")
    def create_invite(request: Request, body: InviteRequest, actor: AuthCacheValue = Depends(require_admin)) -> dict:
        """由系统管理员为企业创建邀请码。"""
        try:
            invite_id = EnterpriseService(request.app.state.engine).create_invite_code(
                body.enterprise_id, body.code.get_secret_value(), body.max_uses, body.expires_at, actor.user_id,
            )
        except EnterpriseError as error:
            raise HTTPException(400, str(error)) from error
        return {"invite_id": invite_id}

    @router.post("/auth/bind-invite")
    def bind_invite(request: Request, body: BindInviteRequest, actor: AuthCacheValue = Depends(current_user)) -> dict:
        """核销邀请码并绑定当前试用用户的企业归属。"""
        try:
            enterprise_id = EnterpriseService(request.app.state.engine).bind_invite(
                actor.user_id, body.code.get_secret_value(), auth_version=actor.auth_version,
            )
        except EnterpriseError as error:
            raise HTTPException(400, str(error)) from error
        return {"enterprise_id": enterprise_id}

    @router.get("/admin/enterprises", dependencies=[Depends(require_admin)])
    def list_enterprises(request: Request, after_id: int = 0) -> list[dict]:
        """分页列出企业摘要；仅系统管理员可访问，每次最多 100 条。"""
        return _list_records(request, select(Enterprise.id, Enterprise.name, Enterprise.code, Enterprise.status)
                             .where(Enterprise.id > after_id).order_by(Enterprise.id))

    @router.get("/admin/invite-codes", dependencies=[Depends(require_admin)])
    def list_invites(request: Request, after_id: int = 0) -> list[dict]:
        """分页列出邀请码状态，不返回可用于绑定的秘密值；仅系统管理员可访问。"""
        return _list_records(request, select(
            InviteCode.id, InviteCode.enterprise_id, InviteCode.max_uses, InviteCode.used_count,
            InviteCode.expires_at, InviteCode.is_active,
        ).where(InviteCode.id > after_id).order_by(InviteCode.id))

    @router.delete("/admin/invite-codes/{invite_id}")
    def revoke_invite(request: Request, invite_id: int, actor: AuthCacheValue = Depends(require_admin)) -> dict:
        """停用指定邀请码，返回本次是否改变状态；权限不足返回 403。"""
        return {"revoked": EnterpriseService(request.app.state.engine).revoke_invite(invite_id, actor.user_id)}

    @router.get("/models")
    def list_models(request: Request, enterprise_id: int | None = None, after_id: int = 0,
                    actor: AuthCacheValue = Depends(current_user)) -> list[dict]:
        """分页返回模型摘要；企业参数只可收窄系统管理员范围。"""
        return _list_records(request, _model_summary(actor, enterprise_id=enterprise_id)
                             .where(Model.id > after_id).order_by(Model.id))

    @router.get("/models/{model_id}")
    def model_detail(request: Request, model_id: int, actor: AuthCacheValue = Depends(current_user)) -> dict:
        """返回单个可见模型摘要；无权限或不存在返回 404。"""
        rows = _list_records(request, _model_summary(actor, model_id=model_id))
        if not rows:
            raise HTTPException(404, "模型不存在")
        return rows[0]

    @router.get("/models/{model_id}/glb", response_class=FileResponse)
    def model_glb(request: Request, model_id: int, actor: AuthCacheValue = Depends(current_user)) -> FileResponse:
        """流式返回当前版本的可见模型预览；模型或文件不可见或不存在均返回 404。"""
        try:
            with request.app.state.engine.connect() as connection:
                version = active_feature_version(connection)
                reference = connection.execute(visible_glb_query(actor, model_id, version)).scalar_one_or_none()
            vault = request.app.state.settings.model_vault
            path = vault_path(Path(vault), reference) if reference is not None and vault is not None else None
            if path is not None and path.is_file():
                return FileResponse(path, media_type="model/gltf-binary", headers={"Cache-Control": "private, no-store"})
        except (VisibilityError, OSError, ValueError):
            pass
        raise HTTPException(404, "模型预览不存在")

    return router


def _client_ip(request):
    return request.client.host if request.client else "unknown"


def _list_records(request, statement):
    with request.app.state.engine.connect() as connection:
        return [dict(row) for row in connection.execute(statement.limit(100)).mappings()]


def _model_summary(actor, *, model_id=None, enterprise_id=None):
    try:
        return visible_model_query(actor, model_id, enterprise_id).with_only_columns(
            Model.id, Model.enterprise_id, Model.model_name, Model.status,
        )
    except VisibilityError:
        raise HTTPException(403, "账号没有可用企业范围") from None
