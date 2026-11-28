"""定义单任务规则快照。依赖 Pydantic 校验配置，供评分与独立几何门禁共用。"""

from math import fsum, isclose
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, PositiveInt, model_validator

DIMENSIONS = ("D1", "D2", "D3", "D4", "D5", "D6", "D7")


class AlignmentLimits(BaseModel):
    """约束右手系配准的拟合、去重、歧义和镜像诊断，仅在任务生命周期内使用。"""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    fit_rms_mm: float = Field(default=0.95, gt=0)
    fit_p95_mm: float = Field(default=0.65, gt=0)
    dedup_angle_deg: float = Field(default=0.25, gt=0, lt=180)
    dedup_center_mm: float = Field(default=0.12, gt=0)
    ambiguity_angle_deg: float = Field(default=6.0, gt=0, le=180)
    ambiguity_center_mm: float = Field(default=0.85, gt=0)
    mirror_margin_mm: float = Field(default=0.05, gt=0)
    mirror_rms_ratio: float = Field(default=0.5, gt=0, lt=1)

    @model_validator(mode="after")
    def validate_pose_tolerances(self) -> Self:
        """校验本实例的去重阈值严格紧于歧义阈值，返回本实例，冲突时抛 ValueError。"""
        if self.dedup_angle_deg >= self.ambiguity_angle_deg or self.dedup_center_mm >= self.ambiguity_center_mm:
            raise ValueError("姿态去重容差必须严于歧义阈值")
        return self


class ComparisonRule(BaseModel):
    """校验七维百分比和缺项规则。默认数值为开发建议，规则快照由调用方管理。"""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    weights: dict[str, FiniteFloat]
    thresholds: dict[str, FiniteFloat] = Field(default_factory=lambda: {
        "D1": 1.0, "D2": 0.15, "D3": 1.0, "D4": 0.15, "D6": 0.10, "D7": 1.0,
        "rms_mm": 2.0, "p95_mm": 2.0, "max_mm": 8.0,
    })
    d4_weights: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (60, 30, 10)
    both_missing: Literal["REDISTRIBUTE", "D3_TO_D7"] = "REDISTRIBUTE"
    one_side_missing: Literal["ZERO", "NEUTRAL", "REALLOCATE"] = "ZERO"
    neutral_score: float = Field(default=50, ge=0, le=100)
    max_refine_k: PositiveInt = 50
    safety: AlignmentLimits = Field(default_factory=AlignmentLimits)

    @model_validator(mode="after")
    def validate_percentages(self) -> Self:
        """校验维度集合、权重和正数阈值，返回本实例，非法配置抛 ValueError。"""
        for weights in (tuple(self.weights.values()), self.d4_weights):
            if any(not 0 <= x <= 100 for x in weights) or not isclose(fsum(weights), 100, rel_tol=0, abs_tol=1e-8):
                raise ValueError("权重范围须为 0 至 100 且总和为 100")
        if set(self.weights) != set(DIMENSIONS):
            raise ValueError("规则必须明确列出 D1 至 D7 的权重")
        required = (set(DIMENSIONS) - {"D5"}) | {"rms_mm", "p95_mm", "max_mm"}
        if set(self.thresholds) != required or any(value <= 0 for value in self.thresholds.values()):
            raise ValueError("非 D5 维度和三项 D5 距离均需要正数零分阈值")
        return self
