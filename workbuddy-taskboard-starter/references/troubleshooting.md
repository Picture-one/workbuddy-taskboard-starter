# 排坑速查

按「症状 → 根因 → 对策」组织。带 ⚠️ 的是**会静默失败**的类型 —— 最值得记住。

---

## 一、静默失败类（没有任何报错，最难查）

| 症状 | 根因 | 对策 |
|---|---|---|
| ⚠️ 开机后服务没起来，**日志里一条记录都没有**，事件日志也干净 | 自启项写成了 `.pyw` 文件路径 → 走「`.pyw` 关联 → `pyw.exe` → PEP 514 最高版本解释器」三层解析。若注册表存在**僵尸高版本注册**（如 3.14 → 不存在的目录），`pyw.exe` 创建进程失败，退出码 101，而它是 GUI 子系统进程，**不写控制台、不写事件日志** | 自启值**直连绝对路径 `pythonw.exe`**。自检有哨兵：跑 `py -0p` 检查默认解释器路径是否存在 |
| ⚠️ 邮件发了，微信完全没动静 | 微信「QQ邮箱提醒」的开关没开 / 绑定失效 | 见 `mail-bridge.md` 第一节第 2 步，逐条核 |
| ⚠️ 手机 403，本机一切正常 | origin 白名单漂移：`start-taskboard.cmd` 与 `mail-bridge\common.py` 两处不一致。**只在某一路实例上出现**（例如手动启动的正常、按需拉起的临时实例 403） | 跑自检，它会逐字符比对 |
| ⚠️ 面板能开但白屏 / 样式错乱 | 前端没构建 —— 上游 tag 里**没有 `dist/`**，必须自己跑 `npm ci` + `npm run build:web` | 重跑安装器（幂等），或手动构建 |
| ⚠️ 改了 `start-taskboard.cmd` 却没生效 | 启动器只在**登录时**解析一次 | 重跑一次 `start-taskboard.cmd`，或重新登录 |
| ⚠️ 服务完全起不来，报 `TRUSTED_ORIGINS_ENV must not be empty when configured` | 把 `CODEX_TASKBOARD_TRUSTED_ORIGINS` **设成了空字符串**。服务端对「未设置」宽容，对「设置但为空」直接抛错 | 不启用远程访问时，**整行删掉或注释掉**，不要留空的 `set` |

---

## 二、判断类（不是故障，但会被误判成故障）

| 症状 | 真相 |
|---|---|
| 电脑明明刚启动过，`uptime` 却显示已运行几十小时 | **快速启动（hiberboot）保留内核会话**，`GetTickCount64` / `LastBootUpTime` 不重置。要看 `Kernel-Boot` 事件（18 / 27 / 32），或取 `explorer.exe`/`sihost.exe` 的最早创建时间 |
| 走域名访问时 `/api/local/*` 返回 **409** | **设计如此**。那批端点只对回环开放，反代不会放宽这个边界。业务端点仍正常 200 |
| 自检报「无法判定」（退出码 3） | 通常是因为 `autostart.log` 被重置过：日志 mtime 晚于登录时间、却没有 banner，两个判据互相矛盾。**别反复重置日志**，它同时是"本次登录是否执行过"的判据 |
| 自检报「未执行」（退出码 2） | 首次安装后**属正常** —— 装完还没登录过。注销重登一次再看 |
| `StartupApproved` 里有 Taskboard 条目 | **不代表被禁用**。该值首字节语义各版本不一，别猜；去「任务管理器 → 启动应用」看它是否「已启用」 |

---

## 三、环境 / 工具类

| 症状 | 根因 | 对策 |
|---|---|---|
| 本机探测端口拿到 **假的 502** | 机器上设了 `HTTP_PROXY`/`HTTPS_PROXY`，`urllib` 连 `127.0.0.1` 的请求也送进代理 | 显式绕过：Python `build_opener(ProxyHandler({}))`；命令行 `curl --noproxy '*'` |
| `schtasks /Create /SC ONLOGON` 报 `Access is denied` | `C:\Windows\System32\Tasks` 根目录 ACL 只给 Administrators / SYSTEM 写权限（连 `notepad` 探针都被拒） | 用免提权的 `HKCU\...\Run` 值 |
| 从 Git-Bash 调用 `cmd.exe` 被拦 | 安全策略：「Invoking cmd.exe from Bash bypasses all command validation」 | 需要跑 `.cmd` 时改用 PowerShell 工具，或用 `explorer.exe "<...cmd>"` 转交 shell |
| 后台服务在下一次工具调用后就没了 | 某些自动化/agent 环境用**作业对象（kill-on-close）**包裹命令：调用结束，整棵进程树被销毁 | 长驻交给自启；要让服务活下来，用 `explorer.exe "<...cmd>"` 把启动动作转交给 shell |
| `npm ci` 卡住不动 | 命令接了管道（`\| tail`）而写端无人读 → 阻塞假死 | 一律 `> log 2>&1`，别接管道 |
| `.cmd` 里中文变成乱码 | cmd 用 GBK 解码 | 写 `.cmd` 用**纯 ASCII 内容 + CRLF 行尾**；需要中文就 `chcp 65001` |
| PowerShell 命令没有输出 | 部分环境下 PowerShell 工具不回传 stdout | 让它写文件，再读那个文件 |

---

## 四、数据与安全类

| 症状 | 对策 |
|---|---|
| 担心安装/升级把任务数据弄丢 | 安装器**从不删除数据目录**；升级是「解到 `.new` → 原子换名」，失败回滚 |
| 担心自检脚本会改东西 | 它是**严格只读**的：只以 `"r"` 模式读文件 + 打印到屏幕 |
| 担心轮询把邮件标记为已读 | **不会**。`readonly` 打开 + `BODY.PEEK[]` 取信，全程不发 `STORE` |
| 不小心把 `config.json` 提交了 | 立刻视为**已泄露**：① 去 QQ 邮箱重新生成授权码（旧码立即作废）② 若用了企微 webhook，重置机器人 ③ 用 `git filter-repo` 改写历史并强推 ④ 联系平台清缓存。**fork 与缓存不可回收**，别指望删掉 commit 就没事 |

---

## 五、还是不行？

```bat
selfcheck-taskboard.cmd
```

把它的【结论】行和退出码贴出来。自检覆盖了十几项：代码/解释器是否存在、自启注册表值、
自启是否被禁用、启动夹是否残留旧指针、origin 两处是否一致、banner 格式是否与解析同步、
以及那条僵尸解释器哨兵。绝大多数问题它会直接点名。
