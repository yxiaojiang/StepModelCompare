# 首轮断网彩排启动记录

冻结记录 [freeze.json](freeze.json)，版本 `60141de03b48dc1c177b1d90448e5e5444e7e29042d9582ce0140e2f59c8bdac`。

执行 `Demo/.venv/Scripts/python.exe -X utf8 Demo/loopback_server.py --port 8765`，服务成功启动且非回环 TCP 探针被拒绝。运行 `Demo/.venv/Scripts/python.exe -X utf8 Demo/rehearse.py` 时，Edge 的禁网 DNS 规则误拦回环 IP，打开页面报 `ERR_NAME_NOT_RESOLVED`。本轮没有上传和彩排数据。

仅在 Edge 启动参数中明确豁免 127.0.0.1，保留外连拒绝。程序比对、样例、SQLite 和阈值均沿用该轮。新的冻结记录见 [freeze-v2.json](freeze-v2.json)。
