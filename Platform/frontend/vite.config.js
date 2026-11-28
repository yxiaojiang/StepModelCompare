import { defineConfig } from "vite";

// 开发与本地预览走同源代理。正式发布时由部署入口将 /api 转发至 FastAPI。
const proxy = { "/api": process.env.STEP_PLATFORM_API_TARGET || "http://127.0.0.1:8000" };

export default defineConfig({
  server: { host: "127.0.0.1", proxy },
  preview: { host: "127.0.0.1", proxy },
});
