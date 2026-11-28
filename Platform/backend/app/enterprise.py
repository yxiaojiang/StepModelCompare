"""提供企业、邀请码及用户绑定的事务服务。

职责是管理租户归属和邀请码核销，不负责模型可见性查询；邀请码核销与用户权限变更在同一事务完成，
并递增 `auth_version` 使旧令牌和鉴权缓存立即失效。
"""

from datetime import datetime, timezone

from sqlalchemy import exists, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.db.models import AuditLog, Enterprise, InviteCode, User


class EnterpriseError(ValueError):
    """表示企业或邀请码业务约束不满足。"""


class EnterpriseService:
    """封装企业创建、邀请码签发和试用用户绑定。"""

    def __init__(self, engine: Engine):
        """创建企业服务并绑定数据库引擎。"""
        self.engine = engine

    def create_enterprise(self, name: str, code: str, storage_quota_mb: int = 10240, *, actor_id=None) -> int:
        """创建启用企业并返回 ID；编码冲突或配额非法抛 EnterpriseError。"""
        if not 1 <= len(name.strip()) <= 200 or not 1 <= len(code.strip()) <= 100 or storage_quota_mb <= 0:
            raise EnterpriseError("企业名称、编码和存储配额必须有效")
        with self.engine.begin() as connection:
            try:
                enterprise_id = connection.execute(Enterprise.__table__.insert().values(
                    name=name, code=code, storage_quota_mb=storage_quota_mb, status="ACTIVE",
                ).returning(Enterprise.id)).scalar_one()
                self._audit(connection, actor_id, "ENTERPRISE_CREATED", "enterprise", enterprise_id)
                return enterprise_id
            except IntegrityError:
                raise EnterpriseError("无法创建企业，请核对企业编码") from None

    def create_invite_code(
        self,
        enterprise_id: int,
        code: str,
        max_uses: int,
        expires_at: datetime,
        created_by: int,
    ) -> int:
        """为企业创建可限次邀请码并返回 ID；过期、重复或非法次数抛 EnterpriseError。"""
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if not 1 <= len(code) <= 128 or not 0 < max_uses <= 2_147_483_647 or expires_at <= datetime.now(timezone.utc):
            raise EnterpriseError("邀请码参数无效或已过期")
        with self.engine.begin() as connection:
            try:
                active = connection.execute(select(Enterprise.id).where(
                    Enterprise.id == enterprise_id, Enterprise.status == "ACTIVE",
                )).scalar_one_or_none()
                if active is None:
                    raise EnterpriseError("企业不可用")
                invite_id = connection.execute(InviteCode.__table__.insert().values(
                    enterprise_id=enterprise_id, code=code, max_uses=max_uses,
                    expires_at=expires_at, created_by=created_by, is_active=True,
                ).returning(InviteCode.id)).scalar_one()
                self._audit(connection, created_by, "INVITE_CREATED", "invite", invite_id)
                return invite_id
            except IntegrityError:
                raise EnterpriseError("无法创建邀请码，请核对企业及邀请码") from None

    def revoke_invite(self, invite_id: int, actor_id: int) -> bool:
        """停用 invite_id 并记录操作者；已撤销或不存在返回 False。数据库异常向上传播。"""
        with self.engine.begin() as connection:
            changed = connection.execute(update(InviteCode).where(
                InviteCode.id == invite_id, InviteCode.is_active.is_(True),
            ).values(is_active=False)).rowcount
            if changed == 1:
                self._audit(connection, actor_id, "INVITE_REVOKED", "invite", invite_id)
            return changed == 1

    def bind_invite(self, user_id: int, code: str, now: datetime | None = None, *, auth_version: int | None = None) -> int:
        """核销邀请码并将试用用户绑定企业，返回企业 ID；失败抛 EnterpriseError。"""
        now = now or datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            invite = connection.execute(
                update(InviteCode)
                .where(InviteCode.code == code, InviteCode.is_active.is_(True),
                       InviteCode.used_count < InviteCode.max_uses, InviteCode.expires_at > now,
                       exists(select(Enterprise.id).where(
                           Enterprise.id == InviteCode.enterprise_id, Enterprise.status == "ACTIVE",
                       )))
                .values(used_count=InviteCode.used_count + 1)
                .returning(InviteCode.id, InviteCode.enterprise_id)
            ).one_or_none()
            if invite is None:
                raise EnterpriseError("邀请码无效、已过期或已用尽")
            # 并发绑定不同邀请码时只有一个能改变账号，另一事务回滚已核销的用量。
            eligible = update(User).where(
                User.id == user_id, User.enterprise_id.is_(None), User.role == "TRIAL_USER",
                User.is_trial.is_(True), User.is_active.is_(True),
                (User.locked_until.is_(None) | (User.locked_until <= now)),
            )
            if auth_version is not None:
                eligible = eligible.where(User.auth_version == auth_version)
            changed = connection.execute(eligible.values(
                enterprise_id=invite.enterprise_id, role="ENT_USER", is_trial=False,
                auth_version=User.auth_version + 1,
            )).rowcount
            if changed != 1:
                raise EnterpriseError("当前账号不允许绑定邀请码")
            self._audit(connection, user_id, "INVITE_BOUND", "invite", invite.id)
            return invite.enterprise_id

    @staticmethod
    def _audit(connection, actor_id, action, target_type, target_id):
        connection.execute(AuditLog.__table__.insert().values(
            actor_id=actor_id, action=action, target_type=target_type, target_id=target_id, detail_json={},
        ))
