# 安装后总表：路径 / 端口 / 命令 / 退出码

一页查完，不用再翻别处。

---

## 目录

| 内容 | 默认路径 | 备注 |
|---|---|---|
| 安装根（`--prefix`） | `%USERPROFILE%\.workbuddy` | 改它即可整体隔离安装 |
| 看板本体（上游代码） | `<prefix>\apps\dashi-taskboard\` | 安装时从上游 tag 下载，**仓库里没有** |
| 数据（SQLite） | `<prefix>\taskboard-data\` | `taskboard.sqlite` + `-shm` + `-wal`。**任何安装/升级都不会删它** |
| 本 skill | `<prefix>\skills\workbuddy-taskboard-starter\` | |
| CLI skill | `<prefix>\skills\manage-taskboard\` | 由安装器从上游 tarball 就地生成 |
| 邮件桥（可选） | `<prefix>\apps\mail-bridge\` | |
| 临时目录 | `<prefix>\tmp\` | 下载/解包中转，安装完可清 |
| 安装清单 | `<prefix>\apps\dashi-taskboard\.starter-manifest.json` | 记录 ref / 路径 / 各渲染文件哈希 |
| 启动器日志 | `<prefix>\apps\dashi-taskboard\logs\autostart.log` | 同时是 Node 的 stdout/stderr 落地点 |
| 启动器状态 | `<prefix>\apps\dashi-taskboard\logs\last-logon.json` | 机器可读：`result` / `port` / `node_pid` / `boot` |

---

## 注册表

| 位置 | 值 | 说明 |
|---|---|---|
| `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` | 值名 `Taskboard`（可用 `--run-value-name` 改） | 自启项。内容是 `"<绝对路径 pythonw.exe>" "<绝对路径 autostart-taskboard.pyw>"` |
| `HKCU\...\Explorer\StartupApproved\Run` | 同名条目（若有） | 系统用来记录「启动应用」里是否被禁用。**有条目不代表被禁用**，语义各版本不一，别猜 |

---

## 环境变量（都由 `start-taskboard.cmd` 设置）

| 变量 | 默认 | 说明 |
|---|---|---|
| `CODEX_TASKBOARD_HOST` | `127.0.0.1` | **只允许回环**。不要改成 `0.0.0.0` |
| `CODEX_TASKBOARD_PORT` | `47823` | 服务端口 |
| `CODEX_TASKBOARD_DATA_DIR` | `<prefix>\taskboard-data` | SQLite 位置 |
| `CODEX_TASKBOARD_URL` | `http://127.0.0.1:47823` | CLI 与自检用 |
| `CODEX_TASKBOARD_TRUSTED_ORIGINS` | （不设） | 远程访问的 HTTPS origin。⚠️ **不启用就整行省略**，设成空串会让服务起不来 |

---

## 命令

在 `<prefix>\apps\dashi-taskboard\` 下：

| 命令 | 作用 |
|---|---|
| `start-taskboard.cmd` | 前台启动服务。**配置的单一真源** —— 改端口/origin 只改这里 |
| `status-taskboard.cmd` | 查看服务是否在线 |
| `stop-taskboard.cmd` | 停止服务 |
| `selfcheck-taskboard.cmd` | 一键自检（只读） |
| `taskctl.cmd ...` | 看板 CLI 的 Windows 包装 |

在 `<prefix>\apps\mail-bridge\` 下（装了才有）：

| 命令 | 作用 |
|---|---|
| `send-test.cmd` | 发测试邮件 |
| `send-daily.cmd` | 发今日清单 |
| `poll-once.cmd` | 跑一轮轮询（测试用） |
| `start-poller.cmd` | 常驻轮询 |

---

## 退出码

### 自检 `selfcheck-taskboard.py`

| 码 | 含义 |
|---|---|
| `0` | 通过 |
| `1` | 执行过，但服务不健康 |
| `2` | 本次登录未执行自启（首次安装后正常，需登录一次） |
| `3` | 无法判定（自检会说明原因） |
| `10` | 配置或文件问题 |

### 安装器 `install.py`

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `2` | 参数错误 |
| `3` | 非 Windows |
| `4` / `5` | 找不到 Node / Node 版本低于 22.5 |
| `6` / `7` | 下载失败 / 解包失败 |
| `8` / `9` | `npm ci` 失败 / 构建失败 |
| `10` | 自检报出配置或文件问题（透传） |
| `11` | 写自启注册表值失败 |
| `12` | 端口被占用（**绝不杀既有进程**） |

---

## 三个入口地址

| 场景 | 地址 |
|---|---|
| 本机浏览器 / CLI | `http://127.0.0.1:47823` |
| 健康检查 | `http://127.0.0.1:47823/health` |
| 手机（配了 Tailscale 后） | `https://<机器名>.<tailnet>.ts.net` |

> 走域名访问时，回环专属端点（`/api/local/*`、WebSocket `/api/events`）会返回 **409** ——
> 这是设计如此，不是故障。详见 `remote-access.md`。

---

## 装完之后的一次性动作

| # | 动作 | 必需？ |
|---|---|---|
| 1 | 双击 `start-taskboard.cmd`，确认能看到界面 | ✅ 必做 |
| 2 | `selfcheck-taskboard.cmd` → 期望退出码 `0` | ✅ 必做 |
| 3 | **注销 → 重新登录**一次，再跑自检，验证自启触发点真的有效 | ✅ 必做（唯一能证明触发器有效的路径） |
| 4 | 配 Tailscale 并设置 origin 白名单（两处一致） | 可选 |
| 5 | `copy config.example.json config.json` 并填授权码，跑通邮件桥三步 | 可选 |
