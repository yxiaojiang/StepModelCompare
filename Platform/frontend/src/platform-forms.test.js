// 验证管理页面使用的真实角色映射、规则校验和批量审核请求。
import assert from "node:assert/strict";
import { test } from "node:test";

import { platformRequest } from "./api.js";
import { checkWeights, navigationFor, reviewPayload } from "./platform-forms.js";

test("七维权重仅在每项有效且合计 100% 时可以保存", () => {
  const weights = { D1: "10.1", D2: "14.9", D3: "20", D4: "0", D5: "50", D6: "5", D7: "0" };
  assert.deepEqual(checkWeights(weights), { total: 100, valid: true });
  assert.deepEqual(checkWeights({ ...weights, D1: "10" }), { total: 99.9, valid: false });
  assert.equal(checkWeights({ ...weights, D4: "" }).valid, false);
  assert.equal(checkWeights({ ...weights, D4: -5, D6: 10 }).valid, false);
  assert.equal(checkWeights({ ...weights, D4: Infinity }).valid, false);
});

test("导航、企业台账、管理后台和邀请码入口实施四角色门禁", () => {
  const views = (role) => navigationFor(role).map((item) => item.id);
  assert.deepEqual(views("SYS_ADMIN"), ["workbench", "admin"]);
  assert.deepEqual(views("ENT_ADMIN"), ["workbench", "ledger"]);
  assert.deepEqual(views("ENT_USER"), ["workbench", "ledger"]);
  assert.deepEqual(views("TRIAL_USER"), ["workbench", "bind"]);
  assert.deepEqual(views(undefined), ["workbench"]);
  assert.deepEqual(views("UNKNOWN"), ["workbench"]);
});

test("批量审核仅发送适用字段，完整传输 int64 编号与逐件结果", async (context) => {
  const max = "9223372036854775807";
  const ids = new Set(["2", max]);
  const passed = reviewPayload(ids, "approve", { publicDemo: true, reason: "不发送" });
  assert.deepEqual(passed, { model_ids: ["2", max], action: "approve", public_demo: true });
  const rejected = reviewPayload(ids, "reject", { publicDemo: true, reason: "  请重提  " });
  assert.deepEqual(rejected, { model_ids: ["2", max], action: "reject", reason: "请重提" });
  assert.throws(() => reviewPayload(ids, "reject", { reason: "   " }), /驳回原因/);
  assert.throws(() => reviewPayload([], "approve"), /待审模型/);
  assert.throws(() => reviewPayload(["2", "2"], "approve"), /待审模型/);
  assert.throws(() => reviewPayload(["9223372036854775808"], "approve"), /待审模型/);

  context.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/v1/admin/models/batch-review");
    assert.equal(options.method, "POST");
    assert.equal(options.headers.get("Authorization"), "Bearer local-test-token");
    assert.deepEqual(JSON.parse(options.body), rejected);
    return new Response(`{"results":[{"model_id":2,"status_code":200},{"model_id":${max},"status_code":409}]}`);
  });
  assert.deepEqual(await platformRequest("/admin/models/batch-review", "local-test-token", { data: rejected }),
    { results: [{ model_id: "2", status_code: 200 }, { model_id: max, status_code: 409 }] });
});
