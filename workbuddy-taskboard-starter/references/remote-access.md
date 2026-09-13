# 手机 / 外网访问（Tailscale serve）

目标：**在手机上打开本机的任务面板，同时不把服务暴露到公网、也不绑 `0.0.0.0`。**

做法：服务继续只监听 `127.0.0.1:47823`，由 `tailscale serve` 在你的**私有 tailnet** 内
做一层 HTTPS 反代。只有登录了你 tailnet 的设备能访问。

---

## 前置：三个后台开关，缺一不可

这三个都是 Tailscale 账号侧的设置，不开就是不通，而且报错信息不会直说。

| # | 开关 | 在哪开 | 不开的症状 |
|---|---|---|---|
| 1 | **Serve 功能** | `https://login.tailscale.com/f/serve?node=<你的节点ID>`（首次执行 `tailscale serve` 时终端会给出这个链接） | 命令卡住不返回；日志里出现 `Serve is not enabled on your tailnet` |
| 2 | **MagicDNS** | admin console → DNS | 拿不到 `*.ts.net` 域名 |
| 3 | **HTTPS Certificates** | admin console → DNS（在 MagicDNS 下方） | 证书签不出来，`https://` 打不开 |

**怎么只读地判断 HTTPS 证书到底开没开**（别用 `tailscale cert` 去试，它有副作用）：

```bash
tailscale status --json
```

看 `CertDomains` 字段：**为空 = 未开启**；有值（形如 `["<机器名>.<tailnet>.ts.net"]`）= 已开启。

---

## 配置

```bash
tailscale serve --bg --https=443 http://127.0.0.1:47823
tailscale serve status      # 复核
```

你的入口地址是：

```
https://<机器名>.<tailnet>.ts.net
```

> 注意：`tailscale status --json` 里的 `Self.DNSName` **结尾带一个点**
> （形如 `<机器名>.<tailnet>.ts.net.`）。拼 URL 和写白名单时**必须去掉那个点**。

---

## 关键一步：应用侧的来源白名单（最容易漏）

tailscale serve 会把请求反代到回环地址，但**透传的 `Origin` 是你那个 `https://...ts.net`**。
而看板服务端的来源校验**只信任本机与私有网段**（`127.*`、`10.*`、`172.16-31.*`、`192.168.*`、
`169.254.*`、fc00::/7、fe80::/10），**不含 `*.ts.net`**。

所以必须显式把这个 origin 加进白名单，否则**手机拿到 403，而本机完全正常**。

它要写**两处，并且逐字符一致**：

| 文件 | 变量 |
|---|---|
| `apps\dashi-taskboard\start-taskboard.cmd` | `set "CODEX_TASKBOARD_TRUSTED_ORIGINS=https://<机器名>.<tailnet>.ts.net"` |
| `apps\mail-bridge\common.py` | `TRUSTED_ORIGIN = 'https://<机器名>.<tailnet>.ts.net'` |

**格式约束（服务端会硬校验，写错服务直接起不来）**：

* 只能是**精确的 HTTPS origin**：不带路径、不带查询串、不带片段、不带用户名密码
* 多项用英文逗号分隔
* **不允许通配符 `*`**
* ⚠️ **变量「未设置」是安全的，但「设置成空字符串」会直接抛错**。所以不启用远程访问时，
  请把 `start-taskboard.cmd` 里那一行**整行注释掉或删掉**，不要留一个空的 `set`。

漂移的症状很好认：**手机 403，而且只在某一路实例上出现**（比如手动启动的常驻实例正常、
邮件桥按需拉起的临时实例却 403）。跑一次自检就会逐字符比对并指出不一致。

---

## 验证检查点（按顺序，别跳）

```bash
# 1) 本机回环可达
curl -s -o /dev/null -w "%{http_code}\n" --noproxy '*' http://127.0.0.1:47823/health
# 期望 200

# 2) Tailscale 侧配置在
tailscale serve status

# 3) HTTPS 证书已开（CertDomains 非空）
tailscale status --json

# 4) 走域名可达
curl -s -o /dev/null -w "%{http_code}\n" --noproxy '*' https://<机器名>.<tailnet>.ts.net/health
# 期望 200
```

⚠️ **探测本机端口一定要带 `--noproxy '*'`**（Python 侧是 `ProxyHandler({})`）。否则请求可能被
送进系统代理，你会拿到一个**假的 502**，然后误以为服务挂了。

---

## 一个反直觉但正常的现象

通过 `https://...ts.net` 访问时，**回环专属端点会返回 409 而不是 200**：

| 端点 | 远程访问的结果 |
|---|---|
| `/api/tasks`、`/api/projects` 等业务端点 | 200（正常） |
| `/api/local/*`（本地伴侣专用） | **409**（`LOCAL_COMPANION_REQUIRED`） |
| WebSocket `/api/events` | **409** |

这是**设计如此**：那批端点只对回环开放，反代不会放宽这个安全边界。看到 409 不是故障，
更不是漏洞被堵上了——它就是本该如此。

---

## 不用 Tailscale 行不行？

不要为了图省事把服务绑到 `0.0.0.0`。那等于把你的任务数据暴露给同网段（甚至公网，取决于
防火墙与路由器）。Tailscale 这条路的价值恰恰在于：**服务永远只听回环，外面那层是私有加密网络**。

其它方案（frp、ngrok、Cloudflare Tunnel、端口转发）也能通，但都要自己承担来源校验、
证书与暴露面管理，本仓库只对 Tailscale 这条路做过实测。
