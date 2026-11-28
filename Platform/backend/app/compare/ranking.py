"""按有效权重上界执行精排。依赖同版本特征、任务规则与几何回调，无数据库写入。"""

from collections.abc import Callable, Iterable, Mapping

from app.compare.rule import ComparisonRule
from app.compare.scoring import score_features


def rank_candidates(
    query: Mapping, candidates: Iterable[dict], rule: ComparisonRule, *,
    top_n: int, budget: int, refine: Callable[[dict], dict],
) -> dict:
    """比较 query 与已授权 candidates，按 rule、top_n、budget 返回可靠榜、待复核和截断诊断。

    refine 接收一个候选并返回几何门禁与测量。参数非法抛 ValueError，任务异常向上传播。
    """
    if min(top_n, budget) < 1:
        raise ValueError("可靠榜名额和精排预算须为正数")
    work, review, best = [], [], []
    weighted_d5 = False
    for candidate in candidates:
        model_id = candidate["model_id"]
        payload = candidate.get("feature_payload")
        if not isinstance(payload, Mapping) or payload.get("feature_version") != query.get("feature_version"):
            review.append(_review(model_id, "CANDIDATE_FEATURE_UNAVAILABLE"))
            continue
        card = score_features(query, payload, rule)
        upper = card.upper_bound()
        if upper is None:
            review.append(_review(model_id, "INSUFFICIENT_FEATURES", card))
            continue
        weighted_d5 |= card.rows["D5"]["effective_weight"] > 0
        # 上界相同先取快速几何得分，再以模型 ID 固定顺序，避免输入顺序影响预算截断。
        geo = sum(card.rows[dim]["weighted_score"] for dim in ("D2", "D4", "D6", "D7"))
        work.append((upper, geo, model_id, candidate, card))
    work.sort(key=lambda row: (-row[0], -row[1], row[2]))

    termination = "exact_pruned" if weighted_d5 else "exact_no_d5"
    max_untested_upper = None
    skipped, pruned, attempts = [], [], 0
    for i, (upper, _, model_id, candidate, card) in enumerate(work):
        if len(best) == top_n:
            floor = best[-1]
            if upper < floor["similarity_score"]:
                pruned.extend(item[2] for item in work[i:])
                break
            # 同分需按最终排序键判断。之后上界相同的较小 ID 仍可能入榜。
            if upper == floor["similarity_score"] and model_id > floor["model_id"]:
                pruned.append(model_id)
                continue
        if attempts == min(budget, rule.max_refine_k):
            termination = "truncated_by_k"
            max_untested_upper = upper
            skipped = [item[2] for item in work[i:]]
            break

        # 几何门禁独立于 D5 权重。零权重候选仍要验证后才可成为可靠榜或剪枝依据。
        attempts += 1
        alignment = refine(candidate)
        if alignment.get("overall"):
            card.apply_surface(alignment, rule)
        if alignment["needs_review"]:
            review.append(_review(model_id, alignment["review_reason"], card, alignment))
            continue
        best.append(_row(model_id, card, alignment))
        best.sort(key=lambda item: (-item["similarity_score"], item["model_id"]))
        del best[top_n:]

    for position, row in enumerate(best, 1):
        row["rank"] = position
    return {
        "feature_version": query["feature_version"], "results": best,
        "needs_review": sorted(review, key=lambda row: row["model_id"]),
        "termination": termination, "max_untested_upper": max_untested_upper,
        "refined_count": attempts, "pruned_model_ids": pruned, "truncated_model_ids": skipped,
    }


def _row(model_id, card, alignment):
    return {
        "model_id": model_id, "status": "OK", "needs_review": False, "review_reason": None,
        "similarity_score": card.total(), "breakdown": card.rows, "warnings": card.warnings,
        "alignment": alignment,
    }


def _review(model_id, reason, card=None, alignment=None):
    value = _row(model_id, card, alignment) if card is not None else {
        "model_id": model_id, "breakdown": {}, "warnings": [], "alignment": alignment,
    }
    if card is None or card.rows["D5"]["status"] == "PENDING_D5":
        value["similarity_score"] = None
    value.update(status="NEEDS_REVIEW", needs_review=True, review_reason=reason)
    return value
