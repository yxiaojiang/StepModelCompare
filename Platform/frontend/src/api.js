// 工作台的同源 API 客户端。仅在请求头传递内存中的 Bearer 令牌。
const root = "/api/v1";
const int64Fields = new Set([
  "id", "actor_id", "enterprise_id", "existing_model_id", "invite_id", "job_id",
  "model_id", "rule_id", "storage_quota_mb", "target_id", "uploader_id",
]);
const failures = {
  AUTH_REQUIRED: "请先登录。",
  INVALID_STEP_FILE: "无法解析此 STEP 文件。请确认文件可正常打开后重试。",
  FILE_TOO_LARGE: "文件超过服务端大小上限。",
  UPLOAD_TOO_LARGE: "上传请求超过服务端大小上限。",
  UPLOAD_RATE_LIMITED: "上传过于频繁，请稍后重试。",
  TRIAL_QUOTA_EXCEEDED: "试用对比次数已用完。",
  QUEUE_FULL: "对比队列已满，请稍后重试。",
  QUEUE_WAIT_TIMEOUT: "任务排队超时，请重新提交。",
  COMPARE_TIMEOUT: "计算已超时，请重新提交。",
  ERR_COMPARE_TIMEOUT: "计算已超时，请重新提交。",
  ERR_PARSE_TIMEOUT: "STEP 解析超时。",
  QUOTA_EXPIRED: "试用配额预占已过期，请重新提交。",
  JOB_NOT_FOUND: "任务已不可访问。",
  PREVIEW_NOT_FOUND: "此预览已不可访问或尚未生成。",
  RESULT_NOT_FOUND: "此结果已不可访问。",
};

function readBody(response) {
  // 保留 PostgreSQL int64 的十进制原文，避免编号和容量发生舍入。
  return response.text().then((body) => JSON.parse(body, (key, value, context) => {
    if (!int64Fields.has(key)) return value;
    if (typeof value === "number") {
      if (context?.source) return context.source;
      if (!Number.isSafeInteger(value)) throw new Error("当前浏览器无法准确读取记录编号。");
      return String(value);
    }
    return value;
  }));
}

async function request(path, token, options) {
  const headers = new Headers(options?.headers);
  if (token) headers.set("Authorization", "Bearer " + token);
  let response;
  try {
    response = await fetch(root + path, { ...options, headers, cache: "no-store", redirect: "error" });
  } catch (error) {
    if (options?.signal?.aborted) throw error;
    throw new Error("服务连接中断，请稍后重试。", { cause: error });
  }
  if (response.ok) return response;
  const data = await readBody(response).catch(() => null);
  const message = failures[data?.error_code] ?? ({
    401: token ? "登录已失效，请重新登录。" : "用户名或密码错误。",
    403: "当前账号没有此操作权限。",
    404: "请求的内容已不可访问。",
    422: "请求格式无效，请检查输入后重试。",
    429: "操作过于频繁，请稍后重试。",
    503: "服务暂不可用，请稍后重试。",
  })[response.status] ?? "操作失败，请稍后重试。";
  const error = new Error(message);
  error.status = response.status;
  error.terminal = data?.status === "FAILED" || response.status === 404;
  throw error;
}

function json(body) {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

/** 访问台账或管理接口。查询为公开筛选条件，凭证仅经请求头或 JSON 请求体传递。 */
export async function platformRequest(path, token, { query, data, body, method, signal } = {}) {
  const params = new URLSearchParams(Object.entries(query ?? {}).filter(([, value]) => value != null && value !== ""));
  const suffix = params.size ? "?" + params : "";
  return readBody(await request(path + suffix, token, {
    ...(data === undefined ? { body } : json(data)),
    method: method ?? (data === undefined ? "GET" : "POST"),
    signal,
  }));
}

function compareResponse(response) {
  return readBody(response).then((data) => {
    const retry = Number(response.headers.get("Retry-After"));
    const delay = retry > 0 ? Math.min(retry, 30) * 1000 : 1000;
    return { ...data, delay };
  });
}

/** 用账号密码登录，返回后端短期令牌与身份；拒绝无效响应。 */
export async function login(username, password, signal) {
  const data = await readBody(await request("/auth/token", null, { ...json({ username, password }), signal }));
  if (!data.access_token) throw new Error("服务未返回登录令牌。");
  return data;
}

/** 上传一个 STEP 并返回任务。服务端或网络异常会中止此 Promise。 */
export async function uploadStep(token, file, signal) {
  const body = new FormData();
  body.append("file", file);
  return compareResponse(await request("/compare/upload", token, { method: "POST", body, signal }));
}

/** 按已有 job 轮询，返回包含推荐间隔的任务状态或结果。失败结果转为错误。 */
export async function pollJob(token, job, signal) {
  return compareResponse(await request("/compare/jobs/" + job, token, { signal }));
}

/** 为任务记录候选选择，返回服务端结果；重复选择由服务端作幂等处理。 */
export async function selectModel(token, job, model, signal) {
  return readBody(await request("/compare/jobs/" + job + "/select", token, { ...json({ model_id: model }), signal }));
}

/** 取任务绑定的二进制预览，model 为空时取查询件。无权限或未生成时抛出错误。 */
export async function fetchPreview(token, job, model, signal) {
  const target = model == null ? "query" : "models/" + model;
  const response = await request("/compare/jobs/" + job + "/" + target + "/glb", token, { signal });
  return response.arrayBuffer();
}
