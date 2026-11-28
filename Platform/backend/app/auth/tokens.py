"""签发和校验短期 JWT 访问令牌。

令牌只承载用户身份与当前 `auth_version`；每次请求仍回查账号状态，避免禁用账号继续使用旧令牌。
"""

from datetime import datetime, timedelta, timezone

import jwt

from app.auth.cache import AuthCacheValue


class TokenService:
    """使用 HS256 签发访问令牌并校验签名、过期时间和载荷版本。"""

    def __init__(self, secret: str, ttl_seconds: int = 3600):
        """创建令牌服务；密钥为空或 TTL 非正数抛 ValueError。"""
        if not secret or ttl_seconds <= 0:
            raise ValueError("JWT 密钥不能为空且有效期必须为正数")
        self._secret = secret
        self._ttl_seconds = ttl_seconds

    def issue(self, user: AuthCacheValue) -> str:
        """为已成功鉴权用户签发令牌，载荷包含 uid、auth_version、角色和企业归属。"""
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user.user_id),
            "username": user.username,
            "auth_version": user.auth_version,
            "role": user.role,
            "enterprise_id": user.enterprise_id,
            "is_trial": user.is_trial,
            "iat": now,
            "exp": now + timedelta(seconds=self._ttl_seconds),
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def decode(self, token: str) -> dict:
        """校验并返回令牌载荷；格式、签名或有效期错误统一抛 ValueError。"""
        if not token:
            raise ValueError("访问令牌不能为空")
        try:
            return jwt.decode(token, self._secret, algorithms=["HS256"], options={"require": ["sub", "iat", "exp", "auth_version"]})
        except jwt.PyJWTError as error:
            raise ValueError("访问令牌无效或已过期") from error
