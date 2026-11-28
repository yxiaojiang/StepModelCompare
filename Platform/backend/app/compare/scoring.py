"""用已版本化特征计算 D1–D7 子分及上界。依赖有效规则与 SciPy 分布距离，无数据库或 OCCT 调用。"""

from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from math import fsum, isfinite
import re
import unicodedata

import numpy as np
from scipy.stats import wasserstein_distance

from app.compare.rule import ComparisonRule, DIMENSIONS

_ONE_MISSING = "ONE_SIDE_MISSING_UNVERIFIED"
_BOTH_MISSING = "BOTH_MISSING_REALLOCATED"


@dataclass
class ScoreCard:
    """保存一个候选的维度行与警告。D5 测量填入前，分值只包含其他已知项。"""

    rows: dict[str, dict]
    warnings: list[str]

    def total(self) -> float | None:
        """汇总当前已知带权分，缺少全部有效维度时返回 None，无参数。"""
        if not any(row["effective_weight"] for row in self.rows.values()):
            return None
        return min(100., fsum(row["weighted_score"] for row in self.rows.values()))

    def upper_bound(self) -> float | None:
        """将待测 D5 按 100 分计算乐观上界，缺项全部移除时返回 None，无参数。"""
        if not any(row["effective_weight"] for row in self.rows.values()):
            return None
        # 与最终分使用相同的逐项求和路径。D5 实得分始终不超过代入的满分贡献。
        return min(100., fsum(
            row["effective_weight"] if row["status"] == "PENDING_D5" else row["weighted_score"]
            for row in self.rows.values()
        ))

    def apply_surface(self, measurements: Mapping, rule: ComparisonRule) -> None:
        """按 measurements 的双向较大 RMS/P95/Max 更新 D5 分，所需字段缺失时抛 KeyError。"""
        row = self.rows["D5"]
        if row["status"] != "PENDING_D5":
            return
        names = ("rms_mm", "p95_mm", "max_mm")
        parts = {name: max(d[name] for d in measurements["overall"].values()) for name in names}
        score = min(100., fsum(
            fraction * _linear(parts[name], rule.thresholds[name])
            for name, fraction in zip(names, (4 / 7, 2 / 7, 1 / 7))
        ))
        row.update(status="OK", subscore=score, weighted_score=row["effective_weight"] * (score / 100),
                   measurements_mm=parts, thresholds_mm={name: rule.thresholds[name] for name in names})


def score_features(query: Mapping, candidate: Mapping, rule: ComparisonRule) -> ScoreCard:
    """根据 query、candidate 特征与 rule 计算各维有效权重、当前得分和缺项诊断。

    空有效集安全返回。结构损坏或版本不一致时抛 ValueError 或 TypeError。
    """
    if not query.get("feature_version") or query["feature_version"] != candidate.get("feature_version"):
        raise ValueError("禁止混用不同版本特征")

    weights = dict(rule.weights)
    warnings = []
    rows = {}
    for dim in DIMENSIONS:
        base = rule.weights[dim]
        row = dict(raw_weight=base, effective_weight=0., status="DISABLED", subscore=None, weighted_score=0.)
        rows[dim] = row
        if not weights[dim]:
            continue
        if dim == "D5":
            row["status"] = "PENDING_D5"
            continue
        status, score, detail = _dimension(dim, query, candidate, rule)
        row.update(status=status, subscore=score, **detail)
        if status == _ONE_MISSING or detail.get("unverified"):
            warnings.append(f"{dim}:{_ONE_MISSING if status == _ONE_MISSING else 'PARTIAL_UNVERIFIED'}")
        if status == _BOTH_MISSING:
            if (dim == "D3" and rule.both_missing == "D3_TO_D7" and _can_transfer(query, candidate)):
                weights["D7"] += weights[dim]
                row["status"] = "BOTH_MISSING_TRANSFERRED_TO_D7"
            weights[dim] = 0.
        elif status == _ONE_MISSING and score is None:
            weights[dim] = 0.

    total_weight = fsum(weights.values())
    if total_weight:
        for dim, row in rows.items():
            weight = 100 * weights[dim] / total_weight
            row.update(effective_weight=weight, weighted_score=weight * ((row["subscore"] or 0.) / 100))
    return ScoreCard(rows, warnings)


def _linear(error, threshold):
    return 100 * max(0., 1. - error / threshold)


def _missing_pair(left, right, rule):
    if left is not None and right is not None:
        return "OK", None
    if left is None and right is None:
        return _BOTH_MISSING, None
    scores = {"ZERO": 0., "NEUTRAL": rule.neutral_score, "REALLOCATE": None}
    return _ONE_MISSING, scores[rule.one_side_missing]


def _dimension(dim, query, candidate, rule):
    key, prepare, measure = _METRICS[dim]
    left, left_notes = prepare(query.get(key))
    right, right_notes = prepare(candidate.get(key))
    status, subscore = _missing_pair(left, right, rule)
    detail = {}
    if left_notes or right_notes:
        detail["data_notes"] = {"query": left_notes, "candidate": right_notes}
        detail["unverified"] = True
    if status == "OK":
        error, measured = measure(left, right, rule)
        if error is None:
            return _BOTH_MISSING, None, {**detail, **measured}
        detail.update(error=error, zero_at=rule.thresholds[dim], **measured)
        subscore = _linear(error, rule.thresholds[dim])
    return status, subscore, detail


def _relative(a, b):
    scale = max(abs(a), abs(b))
    return abs(a / scale - b / scale) if scale else 0.


def _distribution(a, b):
    if not len(a) or not len(b):
        return float(bool(len(a)) != bool(len(b)))
    scale = max(a[-1], b[-1])
    if not scale:
        return 0.
    # 在共同量纲尺度上作一维最优搬运，长度和角度各自独立。
    return float(wasserstein_distance(np.asarray(a) / scale, np.asarray(b) / scale))


def _histogram(a, b):
    union = set(a) | set(b)
    denominator = fsum(max(a.get(k, 0), b.get(k, 0)) for k in union)
    return fsum(abs(a.get(k, 0) - b.get(k, 0)) for k in union) / denominator if denominator else 0.


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0


def _vector(value):
    return sorted(float(x) for x in value if _number(x)) if isinstance(value, (list, tuple)) else []


def _set_strings(value):
    return {_normalize(s) for s in value if isinstance(s, str) and s.strip()} if isinstance(value, (list, tuple)) else set()


def _normalize(value):
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _jaccard(a, b):
    return 1 - len(a & b) / len(a | b) if (a or b) else 0.


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _d1_values(value):
    if not isinstance(value, Mapping):
        return None, []
    stem = value.get("filename_stem")
    names = _set_strings(value.get("product_names"))
    if isinstance(stem, str) and stem.strip():
        names.add(_normalize(stem))
    appearance = _mapping(value.get("colors_layers"))
    props = {f"color:{s}" for s in _set_strings(appearance.get("rgb_palette"))}
    props.update(f"layer:{s}" for s in _set_strings(appearance.get("layers")))
    params = _mapping(value.get("derived_params"))
    dims = _vector(params.get("obb_mm"))
    values = (names or None, _set_strings(value.get("materials")) or None, props or None,
              (dims, _vector(params.get("hole_diameters_mm"))) if len(dims) == 3 else None)
    return (values if any(values) else None), []


def _names(a, b):
    tokens_a = {token for name in a for token in re.findall(r"[^\W_]+", name)}
    tokens_b = {token for name in b for token in re.findall(r"[^\W_]+", name)}
    # 限制编辑比较在确定性名称集合的首个字符串内，避免大型装配的两两名称比较。
    left, right = min(a), min(b)
    edit = 1 - (SequenceMatcher(None, left, right, autojunk=False).ratio()
                + SequenceMatcher(None, right, left, autojunk=False).ratio()) / 2
    return (_jaccard(tokens_a, tokens_b) + edit) / 2


def _parameters(a, b):
    return (_max_relative(a[0], b[0]) + _distribution(a[1], b[1])) / 2


def _basic(a, b, rule):
    parts, values, weights = {}, [], []
    for name, left, right, metric, weight in zip(
        ("name", "material", "attributes", "parameters"), a, b,
        (_names, _jaccard, _jaccard, _parameters), (35, 25, 15, 25),
    ):
        status, missing_score = _missing_pair(left, right, rule)
        error = metric(left, right) if status == "OK" else None
        score = _linear(error, rule.thresholds["D1"]) if error is not None else missing_score
        parts[name] = {"status": status, "subscore": score, "error": error}
        if score is not None:
            values.append(score * weight)
            weights.append(weight)
    details = {"components": parts, "unverified": any(part["status"] == _ONE_MISSING for part in parts.values())}
    if not weights:
        return None, details
    # 统一还原到维度阈值尺度，使材料单边缺项可以按策略折算且不重复扣分。
    return (1 - fsum(values) / (100 * fsum(weights))) * rule.thresholds["D1"], details


def _volume(value):
    if isinstance(value, Mapping) and value.get("volume_available") and _number(value.get("volume_mm3")):
        return value["volume_mm3"], []
    return None, []


def _pmi(value):
    if not isinstance(value, Mapping) or not value.get("has_pmi"):
        return None, []
    raw = _mapping(value.get("type_histogram"))
    known, notes = {}, []
    unknown_tolerance = False
    for name, count in raw.items():
        if not isinstance(name, str) or not _number(count) or not count:
            continue
        part, _, kind = name.partition(".")
        if (name == "datum" or (part == "dimensions" and kind.startswith(("Size_", "Location_"))
                                and kind != "Location_None")
                or (part == "geometric_tolerances" and kind and kind.lower() not in {"none", "unknown"})):
            known[name] = count
        else:
            notes.append("PMI_UNKNOWN_TYPE")
            unknown_tolerance |= part == "geometric_tolerances"
    if not known:
        return None, sorted(set(notes))
    lengths = _vector(value.get("numeric_values_mm"))
    if unknown_tolerance:
        # 旧版长度多重集没有单值类型出处，不能把未知公差的正值误认成可信尺寸。
        lengths = []
        notes.append("PMI_NUMERIC_UNVERIFIED")
    elif any(name.startswith("geometric_tolerances.") for name in known) and lengths and not lengths[0]:
        # OCCT 默认公差值也为零，保留语义类型，只剔除无法区分来源的零长度证据。
        lengths = [x for x in lengths if x > 0]
        notes.append("PMI_ZERO_UNVERIFIED")
    return (known, lengths, _vector(value.get("numeric_values_deg"))), sorted(set(notes))


def _measure_pmi(a, b, _rule):
    terms = {"count": _relative(fsum(a[0].values()), fsum(b[0].values())), "type": _histogram(a[0], b[0])}
    for i, label in ((1, "mm_distribution"), (2, "degree_distribution")):
        if a[i] or b[i]:
            terms[label] = _distribution(a[i], b[i])
    numeric = [v for k, v in terms.items() if k.endswith("distribution")]
    error = 0.25 * terms["count"] + 0.35 * terms["type"]
    if numeric:
        error += 0.4 * fsum(numeric) / len(numeric)
    else:
        error /= 0.6
    return error, {"components": terms}


def _max_relative(a, b):
    return max((_relative(x, y) for x, y in zip(a, b)), default=0.)


def _surface(value):
    fields = ("total_area_mm2", "merged_face_count")
    if isinstance(value, Mapping) and all(_number(value.get(key)) for key in fields):
        ratios = value.get("type_area_ratio") or {}
        if isinstance(ratios, Mapping) and ratios and all(_number(x) for x in ratios.values()):
            return (value[fields[0]], ratios, value[fields[1]]), []
    return None, []


def _surface_error(a, b, rule):
    l1 = .5 * fsum(abs(a[1].get(key, 0) - b[1].get(key, 0)) for key in (set(a[1]) | set(b[1])))
    terms = {"area": _relative(a[0], b[0]), "type_area": l1, "merged_face_count": _relative(a[2], b[2])}
    return fsum(x * w / 100 for x, w in zip(terms.values(), rule.d4_weights)), {"components": terms}


def _obb(value):
    dims = _vector(value.get("dims_sorted_mm")) if isinstance(value, Mapping) else []
    return (dims if len(dims) == 3 and dims[-1] > 0 else None), []


def _holes(value):
    if isinstance(value, Mapping) and _number(value.get("hole_count")):
        count = value["hole_count"]
        diameters, distances = (_vector(value.get(key)) for key in ("diameters_sorted_mm", "center_distances_sorted_mm"))
        if count == len(diameters) and len(distances) == count * (count - 1) / 2:
            return (count, diameters, distances), []
    return None, []


def _can_transfer(a, b):
    left, right = (_holes(value.get("d7_holes"))[0] for value in (a, b))
    return left is not None and right is not None and bool(left[0] or right[0])


def _hole_error(a, b, _rule):
    terms = {"count": _relative(a[0], b[0]), "diameters": _distribution(a[1], b[1]),
             "distances": _distribution(a[2], b[2])}
    return fsum(terms.values()) / len(terms), {"components": terms}


_METRICS = {
    "D1": ("d1_basic", _d1_values, _basic),
    "D2": ("d2_volume", _volume, lambda a, b, _: (_relative(a, b), {})),
    "D3": ("d3_pmi", _pmi, _measure_pmi),
    "D4": ("d4_surface", _surface, _surface_error),
    "D6": ("d6_obb", _obb, lambda a, b, _: (_max_relative(a, b), {})),
    "D7": ("d7_holes", _holes, _hole_error),
}
