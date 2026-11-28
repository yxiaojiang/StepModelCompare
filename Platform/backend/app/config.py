"""声明阶段一配置。依赖 Pydantic Settings 与 SQLAlchemy URL 解析，加载时校验，默认值待签认。"""

from typing import Self

from pydantic import Field, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.geometry_engine.step_input import ParseLimits


class Settings(BaseSettings):
    """集中读取 STEP_PLATFORM_ 环境变量。实例创建时校验，凭证用 SecretStr 包装。"""

    model_config = SettingsConfigDict(env_prefix="STEP_PLATFORM_", hide_input_in_errors=True)

    database_url: SecretStr = SecretStr("postgresql+psycopg://localhost/step_platform")
    max_refine_k: PositiveInt = 50
    compare_job_timeout: PositiveInt = 120
    queue_wait_timeout: PositiveInt = 60
    reaper_ttl: PositiveInt = 211
    sync_wait_timeout: float = Field(default=25, ge=0, le=30)
    default_top_n: PositiveInt = 5
    max_queue_depth: PositiveInt = 32
    max_file_size_bytes: PositiveInt = ParseLimits.max_file_size_bytes
    max_batch_files: PositiveInt = 50
    max_brep_faces: PositiveInt = ParseLimits.max_brep_faces
    max_mesh_triangles: PositiveInt = ParseLimits.max_mesh_triangles
    # 保留首版配置兼容。在线读取与切换以数据库的全局指针为准。
    active_feature_version: str = Field(default="1.0.0", min_length=1, max_length=32)
    occt_parse_timeout: float = Field(default=30, gt=0, le=60)
    occt_memory_limit_mb: PositiveInt = ParseLimits.occt_memory_limit_mb
    occt_pool_size: PositiveInt = 2
    occt_tasks_per_worker: PositiveInt = 50
    auth_pepper: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    auth_cache_ttl: PositiveInt = 300
    auth_cache_capacity: PositiveInt = 1024
    auth_rate_limit_window: PositiveInt = 60
    auth_rate_limit_max_attempts: PositiveInt = 5
    auth_rate_limit_capacity: int = Field(default=4096, ge=2)
    invite_bind_max_attempts: PositiveInt = 5
    upload_max_attempts: PositiveInt = 30
    jwt_ttl: PositiveInt = 3600
    model_vault: str | None = None
    max_upload_request_bytes: PositiveInt = 256_000_000
    max_top_n: PositiveInt = 50
    queue_poll_seconds: float = Field(default=0.25, gt=0, le=10)
    reaper_interval_seconds: float = Field(default=5, gt=0, le=60)
    run_queue_workers: bool = True

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        """校验 value 的数据库连接格式并返回原值，驱动或格式错误时抛 ValueError。"""
        try:
            driver = make_url(value.get_secret_value()).drivername
        except (ArgumentError, ValueError):
            raise ValueError("数据库连接地址格式无效") from None
        if driver not in {"postgresql+psycopg", "sqlite", "sqlite+pysqlite"}:
            raise ValueError("数据库需使用 psycopg，SQLite 仅用于基础自测")
        return value

    @model_validator(mode="after")
    def validate_reaper_ttl(self) -> Self:
        """校验预占回收时限并返回本实例，刚好等于下界或更小时抛 ValueError。"""
        if self.reaper_ttl <= self.queue_wait_timeout + self.compare_job_timeout + 30:
            raise ValueError("reaper_ttl 必须大于 queue_wait_timeout + compare_job_timeout + 30")
        return self

    @model_validator(mode="after")
    def validate_auth_secrets(self) -> Self:
        """同时校验两把独立密钥；未配置时关闭鉴权入口，配置不完整或强度不足时抛 ValueError。"""
        if self.auth_pepper is None and self.jwt_secret is None:
            return self
        values = [key.get_secret_value() for key in (self.auth_pepper, self.jwt_secret) if key is not None]
        if len(values) != 2 or len(set(values)) != 2 or min(len(key.encode()) for key in values) < 32:
            raise ValueError("需分别配置不少于 32 字节的鉴权 pepper 和 JWT 签名密钥")
        return self

    def parse_limits(self) -> ParseLimits:
        """提取解析子进程所需的上限，返回独立配置，无须将数据库凭证等配置传入子进程。"""
        return ParseLimits(
            max_file_size_bytes=self.max_file_size_bytes,
            max_brep_faces=self.max_brep_faces,
            max_mesh_triangles=self.max_mesh_triangles,
            occt_memory_limit_mb=self.occt_memory_limit_mb,
        )
