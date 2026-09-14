---
name: workbuddy-taskboard-starter
version: "1.0.0"
description: One-command installer and operations guide for a local-first Taskboard (upstream chuspeeism/dashi-taskboard) plus an optional IMAP/SMTP mail bridge, tailored for WorkBuddy on Windows. Use when the user pastes the workbuddy-taskboard-starter repo link and asks to install the task board, enable logon autostart, expose it to a phone over Tailscale, wire up the mail bridge, or asks how to use the board day to day for research or work (see references/usage-guide.md).
read_when:
  - 用户给出 workbuddy-taskboard-starter 仓库链接并要求安装任务面板
  - 要求在本机搭建任务看板 / taskboard / 任务面板并配置开机自启
  - 要求让手机或外网访问本机任务面板（Tailscale / tailnet / 内网穿透）
  - 要求配置邮件双向桥（回复邮件即回写看板 / QQ 邮箱 IMAP）
  - 需要运行或解读 selfcheck-taskboard.py 的自检结论与退出码
  - 用户问「日常怎么用这个看板 / 怎么用它提高效率 / 任务该拆到什么粒度」→ 读 references/usage-guide.md
agent_created: true
---

# workbuddy-taskboard-starter

把一套**本地优先的任务面板**装进 WorkBuddy（Windows）。本 skill 既是安装器，也是这套系统的
运维手册。

## 何时用

* 用户贴出 `workbuddy-taskboard-starter` 仓库链接，要求「装到本机 / 搭一套任务面板」
* 用户要给自己已有的面板配**开机自启**、**手机访问**、或**邮件提醒**
* 面板出问题需要**自检**（`selfcheck-taskboard.cmd`）并解读结论

## 安装

> **先看清楚**：本仓库**不含**看板本体源码。安装时安装器会从上游按固定 tag 下载
> `chuspeeism/dashi-taskboard`（Apache-2.0）—— 所以装的过程**需要联网**，且**必须跑一次
> 前端构建**（上游 tag 里没有 `dist/`，不构建就没有界面）。

安装器位置（随本 skill 一起被复制到本地）：

```
%USERPROFILE%\.workbuddy\skills\workbuddy-taskboard-starter\references\setup\install.py
```

标准调用：

```bat
python "%USERPROFILE%\.workbuddy\skills\workbuddy-taskboard-starter\references\setup\install.py"
```

常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--ref` | `v1.1.22` | 上游版本。要试新版就改这里（tag / 分支 / sha） |
| `--prefix` | `%USERPROFILE%\.workbuddy` | 安装根。**唯一的隔离旋钮**：同时决定 `apps/`、`taskboard-data/`、`skills/`、`tmp/` |
| `--port` | `47823` | 服务端口 |
| `--origin` | 自动探测 | Tailscale HTTPS origin。会自动从 `tailscale status --json` 读 `Self.DNSName`（**注意它带结尾的点，要去掉**）；拿不到就留空 |
| `--mail-bridge` | 关 | 一并安装邮件桥 |
| `--install-skill` | 开 | 把上游的 `manage-taskboard` CLI skill 装进 `skills/` |
| `--run-value-name` | `Taskboard` | 自启注册表值名。**做隔离验证时必须换名**，否则自检会去比对生产的那个值 |
| `--no-autostart` / `--no-build` / `--force-shell` / `--dry-run` / `--json` | — | 见 `install.py -h` |

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 参数错误 |
| 3 | 非 Windows（明确拒绝，不做半吊子安装） |
| 4 / 5 | 找不到 Node / Node 版本低于 22.5 |
| 6 / 7 | 下载失败 / 解包失败 |
| 8 / 9 | `npm ci` 失败 / 构建失败（没有 `dist\web\index.html`） |
| 10 | 自检报出配置或文件问题（透传） |
| 11 | 写自启注册表值失败（会附上可手工执行的 `reg add`） |
| 12 | 端口被占用（**绝不杀既有进程**，请改 `--port`） |

## 安装后的固定落点

| 内容 | 路径 |
|---|---|
| 看板本体 | `~\.workbuddy\apps\dashi-taskboard\` |
| 数据（SQLite） | `~\.workbuddy\taskboard-data\` |
| CLI skill | `~\.workbuddy\skills\manage-taskboard\` |
| 邮件桥（可选） | `~\.workbuddy\apps\mail-bridge\` |
| 自启项 | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的值 `Taskboard` |
| 安装清单 | `<app_dir>\.starter-manifest.json`（记录 ref / 路径 / 渲染文件哈希） |

## 启停与自检

```bat
start-taskboard.cmd     :: 前台启动（Ctrl+C 停止）
status-taskboard.cmd    :: 看服务是否在线
stop-taskboard.cmd      :: 停止
selfcheck-taskboard.cmd :: 一键自检（只读）
```

自检退出码：`0` 通过 / `1` 执行过但不健康 / `2` 本次登录未执行 / `3` 无法判定 / `10` 配置或文件问题。
详细判据见 [references/autostart.md](references/autostart.md)。

## 远程访问（手机 / 外网）

`tailscale serve` 反代回环地址，**不暴露公网、不绑 `0.0.0.0`**：

```bat
tailscale serve --bg --https=443 http://127.0.0.1:47823
```

然后必须把 `https://<机器名>.<tailnet>.ts.net` 加进服务端的来源白名单，**两处逐字符一致**：
`start-taskboard.cmd` 的 `CODEX_TASKBOARD_TRUSTED_ORIGINS` 与 `mail-bridge\common.py` 的
`TRUSTED_ORIGIN`。漂移的症状是「手机 403，而且只在某一路实例上出现」。

完整前置开关（Serve 授权、MagicDNS、HTTPS Certificates）与实测坑位见
[references/remote-access.md](references/remote-access.md)。

## 邮件桥（可选）

上行：SMTP 把当日清单发到你的邮箱 → 微信「QQ邮箱提醒」推送到手机。
下行：IMAP **只读**拉取你的回复，解析后经 `taskctl` 回写看板。

配置与指令语法见 [references/mail-bridge.md](references/mail-bridge.md)。

## 不可用能力清单（诚实说明）

这套东西是「看板本体 + 外壳」，以下上游能力在本宿主下**不可用**，别去试：

| 能力 | 为什么不可用 |
|---|---|
| Codex / Claude 的浏览器注入脚本 | 上游为其它宿主写的，本宿主不加载 |
| Tauri 桌面壳 | 需要 Rust 工具链与打包，本方案走的是「Node 服务 + 浏览器 UI」 |
| 云端同步（Cloudflare Workers / D1） | 上游可选组件，本方案刻意不用（保持 local-first、无账号） |

## 关键约束（改动前必读）

1. **`start-taskboard.cmd` 是配置的单一真源**。启动器（`autostart-taskboard.pyw`）用正则解析它来
   取 `CODEX_TASKBOARD_*` 环境变量。**改端口 / origin 只改这一个文件**，别在别处再写一份。
2. **没配远程访问时，`CODEX_TASKBOARD_TRUSTED_ORIGINS` 必须整行省略**，不能留一个空的
   `set`。服务端（`server/app.mjs`，`parseTrustedOrigins`）对「未设置」是宽容的，
   对「设置但为空」会**直接抛错、服务起不来**。
3. **自检脚本是严格只读的**（只以 `"r"` 模式读文件 + 写 stdout）。改它时别引入任何写操作。
4. **自启值必须直连绝对路径 `pythonw.exe`**，不要写成 `.pyw` 文件路径 —— 见下面这条坑。
5. **安装器默认不覆盖 `start-taskboard.cmd`**（它含用户配置）。要覆盖得显式加 `--force-shell`。

## 排坑速查

| 症状 | 根因 | 对策 |
|---|---|---|
| 开机后服务没起来，**日志里一条记录都没有** | 自启项写成了 `.pyw` 文件路径 → 走「`.pyw` 关联 → `pyw.exe` → PEP 514 最高版本解释器」三层解析。若注册表里存在**僵尸高版本注册**（如 3.14 → 一个不存在的目录），`pyw.exe` 会**静默失败：零日志、零事件、退出码 101** | 自启值直连绝对路径 `pythonw.exe`。自检里有一条哨兵专门探测这个（跑 `py.exe -0p` 看默认解释器的路径是否存在） |
| 判「今天到底有没有开机/登录过」时被 uptime 骗 | 快速启动（hiberboot）保留内核会话，`GetTickCount64` / `LastBootUpTime` **不重置** | 看 `Kernel-Boot` 事件 18/27/32；或取 `explorer.exe` / `sihost.exe` 的**最早创建时间**当登录时间（别用 `winlogon.exe`，它是开机就启动的） |
| `schtasks /Create /SC ONLOGON` 报 `Access is denied` | `C:\Windows\System32\Tasks` 根目录 ACL 只给 Administrators / SYSTEM 写权限（实测连 `notepad` 探针都被拒） | 用免提权的 `HKCU\...\Run` 值 |
| 手改脚本后「改了没生效」 | 启动器在**登录时**只解析一次 | 改完重跑一次 `start-taskboard.cmd` 或重新登录 |
| 手机 403、本机正常 | origin 白名单漂移（两处不一致） | 跑自检，它会逐字符比对并指出不一致 |
| 面板能开但样式错乱 / 白屏 | 前端没构建（`dist\web` 缺失） | 重跑 `npm ci` + `npm run build:web`，或重跑安装器（幂等） |

更多见 [references/troubleshooting.md](references/troubleshooting.md)。
