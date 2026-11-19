# Studio 0.3.1 评分详情核验

2026-11-19。使用 `Demo/src/loopback_server.py` 启动本地服务，运行 `Demo/src/rehearse.py` 验证分数及明细。包由 `Demo/Tools/Package.bat` 构建，产物为 `Demo/release/installer/StepModelCompare-Studio-Setup-0.3.1-x64.exe`。其 SHA-256 为 `559BD3AD6B52E5DAF232CC73F56987576AEEE66BA408514FB631A337241CAE64`。

- 用孔位变化查询验证 SHOW-H01 和 SHOW-H02 的可点击评分。明细逐列与各自查询结果 JSON 对照。SHOW-H01 显示总分 81.6，表面 RMS 为 0.588 mm、得 28.24／40 分，零分阈值为 2 mm。
- 在 SHOW-H01 已选中时打开 SHOW-H02 的详情，关闭后仍显示原三维视图，键盘焦点返回详情按钮。
- 对称件待复核时仍可查看评分及对齐原因，三维视图仅提供并排。同件、孔位、对齐歧义、损坏文件及重试的原有回归通过。
- 使用 0.3.1 安装程序展开到独立中文目录，启动安装后的 Studio.exe，通过 WebView2 验证孔位查询以及两条候选的分项。关闭 Studio 后本机服务正常退出。

[评分详情截图](score-details.png)
