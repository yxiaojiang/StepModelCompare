"""提供账号注册、密码鉴权和 auth_version 失效控制。

职责边界：本服务只管理账号凭证与状态，不负责 JWT 请求依赖或企业业务授权。
依赖 SQLAlchemy Engine、Argon2id 和进程内成功缓存；数据库事务由本模块短事务完成。
"""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import case, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.auth.cache import AuthCacheValue, AuthSuccessCache
from app.auth.rate_limit import AuthRateLimiter
from app.db.models import User


class AuthenticationError(ValueError):
    """表示凭证错误、账号不可用或鉴权限流。"""


class AuthenticationRateLimited(AuthenticationError):
    """表示请求超出当前进程的密码校验窗口。"""


class AuthService:
    """封装 Argon2id 鉴权、HMAC 缓存键、双限流和 auth_version 原子失效。"""

    def __init__(
        self,
        engine: Engine,
        pepper: str,
        cache: AuthSuccessCache | None = None,
        limiter: AuthRateLimiter | None = None,
        password_hasher: PasswordHasher | None = None,
    ):
        """创建服务；pepper 为空抛 ValueError，依赖对象可替换为测试实现。"""
        if not pepper:
            raise ValueError("鉴权 pepper 不能为空")
        self.engine = engine
        self._pepper = pepper.encode("utf-8")
        self.cache = cache if cache is not None else AuthSuccessCache()
        self.limiter = limiter or AuthRateLimiter()
        self.password_hasher = password_hasher or PasswordHasher()
        self._dummy_hash = self.password_hasher.hash("不存在的账号校验占位")

    def authenticate(self, username: str, password: str, client_ip: str) -> AuthCacheValue:
        """校验账号密码并返回摘要；失败不入缓存，限流或凭证错误抛 AuthenticationError。"""
        if not username or len(username) > 150 or not password or len(password) > 1024 or not client_ip:
            raise AuthenticationError("用户名或密码错误")
        with self.engine.connect() as connection:
            user = connection.execute(select(User).where(User.username == username)).one_or_none()
        available = user is not None and user.is_active and not self._is_locked(user.locked_until)
        key = self._cache_key(user.id, user.auth_version, password) if available else None
        cached = self.cache.get(key) if key is not None else None
        if cached is not None and user.failed_login_count == 0:
            return cached
        verified = cached is not None
        if not verified:
            if not self.limiter.allow(f"ip:{client_ip}", f"user:{username.casefold()}"):
                raise AuthenticationRateLimited("登录尝试过于频繁")
            try:
                verified = self.password_hasher.verify(user.password_hash if available else self._dummy_hash, password)
            except (InvalidHashError, VerificationError):
                verified = False
        if not available:
            raise AuthenticationError("用户名或密码错误")
        # 慢哈希期间不持数据库锁。写入时按版本、锁定时间和状态复核，防止旧请求覆盖较新的权限或锁定。
        now = datetime.now(timezone.utc)
        current = update(User).where(
            User.id == user.id, User.auth_version == user.auth_version, User.is_active.is_(True),
            (User.locked_until.is_(None) | (User.locked_until <= now)),
        )
        with self.engine.begin() as connection:
            if verified:
                accepted = connection.execute(current.values(failed_login_count=0, locked_until=None)).rowcount == 1
            else:
                threshold = User.failed_login_count >= 4
                connection.execute(current.values(
                    failed_login_count=case((threshold, 0), else_=User.failed_login_count + 1),
                    locked_until=case((threshold, now + timedelta(minutes=15)), else_=None),
                    auth_version=case((threshold, User.auth_version + 1), else_=User.auth_version),
                ))
                accepted = False
        # 在事务正常提交后才报告失败，避免异常回滚锁定和计数。
        if not accepted:
            raise AuthenticationError("用户名或密码错误")
        value = self._value(user)
        self.cache.put(key, value)
        return value

    def resolve_token_user(self, user_id: int, auth_version: int) -> AuthCacheValue:
        """按令牌账号 ID 与版本重查当前身份；账号不可用或版本变更时抛 AuthenticationError。"""
        with self.engine.connect() as connection:
            user = connection.execute(select(User).where(
                User.id == user_id, User.auth_version == auth_version, User.is_active.is_(True),
            )).one_or_none()
        if user is None or self._is_locked(user.locked_until):
            raise AuthenticationError("访问令牌无效或已失效")
        return self._value(user)

    def register(self, username: str, password: str, *, is_trial: bool = True) -> AuthCacheValue:
        """创建账号并返回摘要；用户名冲突或密码不合要求时抛 AuthenticationError。"""
        if not username or len(username) > 150 or not 8 <= len(password) <= 1024:
            raise AuthenticationError("用户名不能为空且密码至少 8 位")
        role = "TRIAL_USER" if is_trial else "ENT_USER"
        password_hash = self.password_hasher.hash(password)
        try:
            with self.engine.begin() as connection:
                user = connection.execute(User.__table__.insert().values(
                    username=username, password_hash=password_hash, role=role, is_trial=is_trial,
                ).returning(*User.__table__.c)).one()
        except IntegrityError:
            raise AuthenticationError("用户名已存在") from None
        return self._value(user)

    def set_password(self, user_id: int, password: str) -> None:
        """修改密码并递增 auth_version，使旧的成功缓存和令牌立即失效。"""
        if not 8 <= len(password) <= 1024:
            raise AuthenticationError("密码至少 8 位")
        password_hash = self.password_hasher.hash(password)
        with self.engine.begin() as connection:
            self._bump_auth_version(connection, user_id, password_hash=password_hash)

    def set_active(self, user_id: int, is_active: bool) -> None:
        """启用或禁用账号并递增 auth_version；账号不存在抛 AuthenticationError。"""
        with self.engine.begin() as connection:
            self._bump_auth_version(connection, user_id, is_active=is_active)

    def set_role_or_enterprise(self, user_id: int, *, role: str, enterprise_id: int | None) -> None:
        """修改角色或企业归属并递增 auth_version，防止旧权限继续复用。"""
        with self.engine.begin() as connection:
            if (
                role not in {"TRIAL_USER", "SYS_ADMIN", "ENT_USER", "ENT_ADMIN"}
                or (enterprise_id is not None) != (role in {"ENT_USER", "ENT_ADMIN"})
            ):
                raise AuthenticationError("角色与企业归属不匹配")
            self._bump_auth_version(connection, user_id, role=role, enterprise_id=enterprise_id, is_trial=role == "TRIAL_USER")

    def _cache_key(self, user_id: int, auth_version: int, password: str) -> str:
        # 版本进入 HMAC 键，旧版本成功记录自然失效，无需广播清理。
        message = f"{user_id}:{auth_version}:{password}".encode("utf-8")
        return hmac.new(self._pepper, message, hashlib.sha256).hexdigest()

    @staticmethod
    def _is_locked(locked_until: datetime | None) -> bool:
        if locked_until is None:
            return False
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        return locked_until > datetime.now(timezone.utc)

    def _value(self, user) -> AuthCacheValue:
        return AuthCacheValue(
            user_id=user.id, username=user.username, auth_version=user.auth_version,
            role=user.role, enterprise_id=user.enterprise_id, is_trial=user.is_trial,
        )

    def _bump_auth_version(self, connection, user_id: int, **values) -> None:
        statement = update(User).where(User.id == user_id).values(**values, auth_version=User.auth_version + 1)
        if connection.execute(statement).rowcount != 1:
            raise AuthenticationError("账号不存在")
