# workbuddy-taskboard-starter

> One-command setup for a **local-first task board** (kanban + CLI) inside WorkBuddy on Windows,
> with optional **phone access over Tailscale** and an optional **two-way email bridge** that lets
> you tick off tasks by replying to an email.
>
> 给你的 WorkBuddy 装一套本地任务面板：看板 + 命令行 + 开机自启，可选手机访问与「回邮件即完成任务」。

本仓库**不包含**看板本体的源码。安装时它会从上游按**固定版本**拉取
[`chuspeeism/dashi-taskboard`](https://github.com/chuspeeism/dashi-taskboard)（Apache-2.0），
再叠加上一套可配置的 Windows 外壳脚本、自检工具与邮件桥。详见 [NOTICE](NOTICE)。

---

## 30 秒看懂

```
手机浏览器 ──(Tailscale HTTPS)──┐
本机浏览器 ─────────────────────┼──> 127.0.0.1:47823 (Node) ──> SQLite
taskctl CLI / 邮件桥 ───────────┘
```

三条设计要点：

| 要点 | 说明 |
|---|---|
| **local-first** | 数据全在本机 SQLite，无账号、无云端、无遥测 |
| **只绑回环** | 服务只监听 `127.0.0.1`，**从不**绑 `0.0.0.0`；要给手机看就走 Tailscale 反代 |
| **零 pip 依赖** | 所有 Python 脚本只用标准库，不需要 `pip install` 任何东西 |

---

## 前置要求

| 项 | 要求 | 备注 |
|---|---|---|
| 系统 | **Windows 10 / 11** | 只支持 Windows（自启依赖注册表 Run 键、外壳用 `.cmd`）。macOS / Linux 会明确拒绝安装 |
| Node.js | **≥ 22.5** | 用于跑看板服务与构建前端 |
| Python | 3.10+ | 用于安装器、自检与启动器 |
| Tailscale | 可选 | 只有想用手机访问才需要 |
| QQ 邮箱 | 可选 | 只有想用邮件桥才需要（要一个 16 位授权码） |

---

## 安装

### 方式 A：让 WorkBuddy 自己装（推荐）

1. 把本仓库地址贴给 WorkBuddy，说一句：
   > 把这个 starter 装到本机
2. WorkBuddy 会先把本仓库作为 skill 装进 `~/.workbuddy/skills/workbuddy-taskboard-starter/`，
   然后读取其中的 `SKILL.md`，按里面的步骤调用自带的安装器。
3. 安装完成后按提示双击 `start-taskboard.cmd` 即可看到界面。

### 方式 B：手动跑安装器

```bat
python workbuddy-taskboard-starter\references\setup\install.py
```

> 先 `git clone` 本仓库，或把仓库作为 skill 装好后到
> `%USERPROFILE%\.workbuddy\skills\workbuddy-taskboard-starter\references\setup\` 下执行。

常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--ref` | `v1.1.22` | 上游版本（tag / 分支 / commit sha） |
| `--prefix` | `%USERPROFILE%\.workbuddy` | 安装根目录。**改它即可把整套东西装到隔离位置** |
| `--port` | `47823` | 服务端口 |
| `--origin` | 自动探测 | Tailscale 的 HTTPS origin（形如 `https://<机器名>.<tailnet>.ts.net`）。留空则不启用远程访问。裸域名也能给，会自动补 `https://` |
| `--mail-bridge` | 关 | 一并安装邮件桥 |
| `--no-autostart` | — | 不写开机自启项 |
| `--no-smoke` | — | 跳过「起一次服务验证配置」这一步 |
| `--dry-run` | — | 只打印计划，不落盘 |

安装器是**幂等**的：重复执行只会修复缺失文件，**不会覆盖你手改过的**
`start-taskboard.cmd`，也**从不删除数据目录**。

**装完会当场起一次服务**（阶段 3b），把 `/health` 打通再关掉。这一步是为了让「配置写错导致根本起不来」
在安装当场就暴露，而不是等你下次登录才发现 502。它失败时安装器**不会**写自启项，退出码 `13`。
冒烟日志在 `<prefix>\tmp\smoke.log`，与 `logs\autostart.log` 严格分开 —— 后者是判断
「登录触发器是否执行过」的证据，不能被污染。

### 安装器退出码

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `2` | 参数不对（含 `--origin` 格式非法） |
| `3` | 非 Windows |
| `4` | 没找到 node |
| `5` | node 版本低于 22.5 |
| `6` / `7` | 下载 / 解包上游失败 |
| `8` / `9` | `npm ci` / 构建前端失败 |
| `10` | 自检报出配置或文件问题（安装器写出来的不一致状态） |
| `11` | 写自启项失败 |
| `12` | 端口已被占用（安装器**不会**去杀那个进程） |
| `13` | **服务起不来** —— 见上面阶段 3b 的冒烟 |

### 安装后都落在哪

| 内容 | 路径 |
|---|---|
| 看板本体（上游代码） | `~\.workbuddy\apps\dashi-taskboard\` |
| 数据（SQLite） | `~\.workbuddy\taskboard-data\` |
| CLI skill | `~\.workbuddy\skills\manage-taskboard\` |
| 邮件桥（可选） | `~\.workbuddy\apps\mail-bridge\` |
| 自启项 | 注册表 `HKCU\...\CurrentVersion\Run` 的值 `Taskboard` |

---

## 日常使用

看板目录（`~\.workbuddy\apps\dashi-taskboard\`）下四个命令：

| 命令 | 作用 |
|---|---|
| `start-taskboard.cmd` | 启动服务（前台，Ctrl+C 停止）。**它同时是配置的单一真源** |
| `status-taskboard.cmd` | 查看服务是否在线 |
| `stop-taskboard.cmd` | 停止服务 |
| `selfcheck-taskboard.cmd` | **一键自检**（只读，十多项检查）——出问题先跑它 |

### 自检退出码怎么读

| 码 | 含义 | 该怎么办 |
|---|---|---|
| `0` | 通过 | 一切正常 |
| `1` | 执行过，但服务不健康 | 看自检报出的失败项 |
| `2` | 本次登录未执行自启 | 首次安装后属正常（需登录一次），否则查自启项 |
| `3` | 无法判定 | 时间戳对不上，自检会说明原因 |
| `10` | 配置或文件有问题 | 看自检报出的失败项 |

---

## 可选：让手机也能打开

用 Tailscale 组一个私有网络，**不暴露公网**。三个后台开关缺一不可（Serve、MagicDNS、HTTPS
Certificates），配置与实测坑位见 [`references/remote-access.md`](workbuddy-taskboard-starter/references/remote-access.md)。

```bat
tailscale serve --bg --https=443 http://127.0.0.1:47823
```

⚠️ 关键：服务端的来源校验只信任本机与私有网段，**不含 `*.ts.net`**，所以必须把
`https://<机器名>.<tailnet>.ts.net` 显式加进白名单，否则手机拿到 403。
**注意**：这个值必须同时写进 `start-taskboard.cmd` 与 `mail-bridge\common.py`，**逐字符一致**。
自检会自动比对这一项。

---

## 可选：回邮件完成任务

配好之后，每天定时给你发一封当日清单邮件，你在邮件里回复「把 1、2 项标记为已完成」，
下次轮询就会把结果写回看板。

需要：一个 QQ 邮箱 + 16 位授权码。完整配置、指令语法与踩坑见
[`references/mail-bridge.md`](workbuddy-taskboard-starter/references/mail-bridge.md)。

> 提醒：授权码**等同于邮箱密码**，只填在本机的 `config.json` 里。本仓库的 `.gitignore`
> 已把 `config.json` 排除，只提交 `config.example.json`。

---

## 常见问题

| 症状 | 先查 |
|---|---|
| 打不开页面 / 502 | 服务没起来。跑 `status-taskboard.cmd`，再跑 `selfcheck-taskboard.cmd` |
| 手机 403，本机正常 | 白名单漂移 —— `start-taskboard.cmd` 与 `mail-bridge\common.py` 的 origin 不一致 |
| 开机后没自动起 | 跑 `selfcheck-taskboard.cmd`，看结论与退出码；自启项是否被系统禁用 |
| 端口被占 | 换个端口：`start-taskboard.cmd` 里的 `CODEX_TASKBOARD_PORT` |
| 其它 | [`references/troubleshooting.md`](workbuddy-taskboard-starter/references/troubleshooting.md) |

---

## 卸载

1. 删掉自启项：
   ```bat
   reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v Taskboard /f
   ```
2. 删掉这些目录（**想保留任务数据就别删第二个**）：
   ```
   %USERPROFILE%\.workbuddy\apps\dashi-taskboard
   %USERPROFILE%\.workbuddy\taskboard-data
   %USERPROFILE%\.workbuddy\apps\mail-bridge
   %USERPROFILE%\.workbuddy\skills\workbuddy-taskboard-starter
   %USERPROFILE%\.workbuddy\skills\manage-taskboard
   ```

不涉及系统服务、不写 Program Files、不改系统 PATH。

---

## 仓库结构

```
workbuddy-taskboard-starter/
├── README.md                 ← 你正在看的
├── LICENSE                   ← MIT（仅覆盖本仓库自有代码）
├── NOTICE                    ← 上游致谢与「不重分发」声明
└── workbuddy-taskboard-starter/
    ├── SKILL.md              ← WorkBuddy 读取的入口
    └── references/
        ├── setup/            ← 安装器（install.py / render.py / scan-secrets.py + 模板）
        ├── remote-access.md  ← 手机 / 外网访问
        ├── autostart.md      ← 开机自启与判定方法
        ├── mail-bridge.md    ← 邮件双向桥
        ├── local-layout.md   ← 路径 / 端口 / 命令总表
        └── troubleshooting.md
```

---

## Credits & License

* 看板本体：[`chuspeeism/dashi-taskboard`](https://github.com/chuspeeism/dashi-taskboard)（Apache-2.0），
  **安装时下载、不打包分发**，详见 [NOTICE](NOTICE)。
* 本仓库自有代码（外壳脚本、自检、邮件桥、skill）：**MIT**，见 [LICENSE](LICENSE)。

与上游作者无隶属或背书关系。
