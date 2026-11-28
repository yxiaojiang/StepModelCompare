# STEP 模型对比工作台

Web 工作台使用 React 和 Three.js。运行需要支持 WebGL 2 的浏览器，以及具备 PostgreSQL、存储卷、鉴权密钥、常驻队列和已生效规则的 FastAPI 后端。

本地开发和回归使用 Node.js 22.12 或更高版本。在本目录执行 `npm ci` 和 `npm run dev`。开发代理默认指向 `http://127.0.0.1:8000`，可在启动前设置 `STEP_PLATFORM_API_TARGET`。打开 Vite 打印的本地地址，使用已有平台账号登录。提交 STEP 后可轮询任务、查看候选与分项、同步并排或在通过门禁后叠加，并记录选中。

`npm test` 执行前端回归。 `npm run build` 生成 `dist/`，`npm run preview` 可在本地验证产物。部署时静态页面与 `/api` 使用同源 HTTPS，`/api` 转发给后端。

新完成的 Web 任务会生成查询件 GLB，候选预览读取任务快照里的特征版本。早于此切片的已完成任务没有查询件 GLB，重新提交可生成预览。
