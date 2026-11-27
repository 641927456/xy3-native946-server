# 新大话 3 本地服务端（3.0.946）

服务端入口是 `server.py`。`native946_*.py` 实现登录、存档和玩法，配套 JSON 是服务端运行数据。账号数据库和连接日志在本机运行时生成。仓库所附 SQLite 3.51.3 DLL 在加载时会校验版本和哈希。

## 本地开发

使用 Windows x64 和 64 位 Python 3.13。在仓库根目录执行以下命令。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-test.txt
.\.venv\Scripts\python.exe -B -X utf8 native946_run.py -m unittest -q test_native946_login test_native946_durable_writer
```

启动服务及测试地图、移动和原生协议进场，需要自行在仓库根目录放入与当前服务端匹配的 `client-946` 本地客户端副本。其中要有 `xymain.bin`、版本为 `3.0.946` 的 `update.ini`、`bin_script.wdf`、`resource/configs.wdf` 和 `map/`。部分原版行为的对照测试还依赖本地分析资料和客户端工具。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 native946_run.py -m unittest -q test_native946_growth_battle test_native946_characters test_native946_party_concurrency
.\.venv\Scripts\python.exe -B -X utf8 native946_run.py server.py --client client-946 --native-local-login
```

服务监听 `127.0.0.1:18127`（HTTP）和 `127.0.0.1:19127`（游戏 TCP/UDP）。

## 提交修改

获得私有仓库访问权限后，克隆仓库并为每项修改建一个分支。

```powershell
git clone https://github.com/641927456/xy3-native946-server.git
cd xy3-native946-server
git switch -c fix/your-change
# 修改服务端代码并跑相关回归
git add -- 文件路径
git commit -m "说明修改内容"
git push -u origin fix/your-change
```

在 GitHub 向 `main` 开 Pull Request，填写修改内容及测试结果。合并后，维护者在本地 `main` 上运行 `git pull --ff-only`。玩法修改与验收按 [AGENTS.md](AGENTS.md) 执行。
