// 验证客户端保留 int64 路由编号，且令牌只发送在请求头中。
import assert from "node:assert/strict";
import { test } from "node:test";

import { fetchPreview, pollJob, selectModel } from "./api.js";

test("轮询、选中和授权预览完整传输 int64 编号", async (context) => {
  const large = "9223372036854775807";
  const mock = context.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(options.headers.get("Authorization"), "Bearer local-test-token");
    assert.equal(options.redirect, "error");
    assert.ok(url.startsWith("/api/v1/compare/jobs/" + large));
    assert.ok(!url.includes("token"));
    if (url.endsWith("/select")) {
      assert.deepEqual(JSON.parse(options.body), { model_id: large });
      return new Response('{"selected":true}');
    }
    if (url.endsWith("/glb")) {
      assert.ok(url.endsWith("/models/" + large + "/glb"));
      return new Response("glTF");
    }
    return new Response('{"job_id":' + large + ',"status":"DONE","candidates":[{"model_id":' + large + '}]}');
  });
  const result = await pollJob("local-test-token", large);
  assert.equal(result.job_id, large);
  assert.equal(result.candidates[0].model_id, large);
  assert.deepEqual(await selectModel("local-test-token", large, large), { selected: true });
  assert.equal(new TextDecoder().decode(await fetchPreview("local-test-token", large, large)), "glTF");
  assert.equal(mock.mock.callCount(), 3);
});
