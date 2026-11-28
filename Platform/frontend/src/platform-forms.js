// 维护管理表单与角色导航的纯数据契约，供页面和测试共用。
const links = [
  { id: "workbench", label: "三维对比工作台" },
  { id: "ledger", label: "模型上传与台账", roles: ["ENT_ADMIN", "ENT_USER"] },
  { id: "admin", label: "管理后台", roles: ["SYS_ADMIN"] },
  { id: "bind", label: "绑定企业", roles: ["TRIAL_USER"] },
];

/** 返回 role 可进入的视图。未登录时可浏览工作台。 */
export function navigationFor(role) {
  return links.filter((link) => !link.roles || link.roles.includes(role));
}

/** 构建单一操作的批量审核 JSON。编号按 int64 字符串传递，输入无效时抛错。 */
export function reviewPayload(ids, action, { publicDemo = false, reason = "" } = {}) {
  const modelIds = Array.from(ids);
  if (!modelIds.length || modelIds.length > 100 || new Set(modelIds).size !== modelIds.length ||
      modelIds.some((id) => typeof id !== "string" || !/^[1-9]\d{0,18}$/.test(id) || BigInt(id) > 9223372036854775807n)) {
    throw new Error("请选择 1 至 100 件不同的待审模型。");
  }
  if (action === "approve") return { model_ids: modelIds, action, public_demo: Boolean(publicDemo) };
  const explanation = reason.trim();
  if (action === "reject" && explanation && explanation.length <= 512) {
    return { model_ids: modelIds, action, reason: explanation };
  }
  throw new Error("请输入 1 至 512 字的驳回原因。");
}

export const dimensions = ["D1", "D2", "D3", "D4", "D5", "D6", "D7"];

/** 读取七维权重的校验结果。允许小数及与后端一致的浮点精度误差。 */
export function checkWeights(weights) {
  const values = dimensions.map((key) => {
    const raw = weights[key];
    return (typeof raw === "number" || typeof raw === "string" && raw.trim()) ? Number(raw) : NaN;
  });
  const total = values.reduce((sum, value) => sum + value, 0);
  const valid = values.every((value) => Number.isFinite(value) && value >= 0 && value <= 100) &&
    Math.abs(total - 100) <= 1e-8;
  return { total, valid };
}
