# board/ — 看板源码（改造版）

本目录收录**看板本体源码**，使本仓库从「安装器 + 手册」变成**自带源码的自足仓库**：
安装器可直接从本仓库取代码，不再必须联网下载上游。

## 与上游的关系

| | 说明 |
|---|---|
| 上游 | [`chuspeeism/dashi-taskboard`](https://github.com/chuspeeism/dashi-taskboard)，`package.json` 版本 **1.1.22** |
| 许可 | **Apache License 2.0**（见 `upstream/LICENSE`），允许再分发与修改 |
| 本目录 | 上游代码 + 本机改造，**不是官方发行版** |

> **Apache-2.0 义务提示**：再分发须保留 `upstream/LICENSE` 与版权声明。本目录保留原样。
> 上游的 `NOTICE` 文件本次未随附（源目录中不存在）。

## 目录结构

```
board/
├── README.md            # 本文件
└── upstream/            # 上游代码 + 本机改造（路径与上游仓库同构）
    ├── LICENSE          # Apache-2.0（上游原样，勿删）
    ├── package.json     # 上游 1.1.22 + 本机新增 test:components 脚本
    ├── web/             # 前端（React + Vite）← 按天分组改造在此
    ├── server/          # 本地 HTTP 服务（node:sqlite）
    ├── shared/          # 前后端共享（任务输入契约、领域常量）
    ├── cli/             # taskctl CLI
    ├── cloud/           # Cloudflare Worker + D1 迁移
    ├── scripts/         # 构建 / 注入 / 迁移脚本
    ├── test/            # node --test 用例
    ├── inject/          # Codex 注入器
    ├── integrations/    # 外部集成（Jira 等）
    └── skills/          # 随看板分发的 Skill
```

## 本机改造：任务按天分组

修复的问题：每日推送的任务与历史任务混在同一栏，分不清哪条是当天推的。

**根因两层**：① 旧逻辑是「1 个 Hub 任务 + 每天追加评论」，而**看板卡片不渲染评论**，
用户永远看不见当天推送；② 看板组织维度只有 `status`，**没有「天」**。

**改动文件**：

| 文件 | 改动 |
|---|---|
| `web/src/boardGrouping.ts` | 新增：按天分组纯函数层 |
| `web/src/boardGrouping.test.tsx` | 新增：23 例单测 |
| `web/src/components/TodayPushPanel.tsx` | 「今日推送」板块（见下节：已改为看板内的竖向板块） |
| `web/src/components/BoardColumn.tsx` | 改动：列内按天分节渲染 |
| `web/src/components/BoardCardDisplayMenu.tsx` | 改动：新增开关 + `normalizeBoardDisplaySettings()` |
| `web/src/components/BoardCardDisplayMenu.test.tsx` | 新增：12 例单测 |
| `web/src/App.tsx` | 改动：接入分组、今日板块、设置归一化 |
| `web/src/styles.css` | 改动：分节标题 / 今日板块样式 / 布局填充 |

**两条不变式**（改这块代码前务必先读）：

1. **日期只能来自 `startDate`**。`shared/task-input.mjs` 的 `parseTaskCreate` **不接受
   `createdAt`**，由服务端 `now()` 生成。故回填历史卡片只能靠 `start_date`。
2. **不得重排传给 `BoardColumn` 的数组**。`moveTask()` 从 `tasks`（**不是渲染数组**）取
   `destination` 并 `findIndex(beforeTaskId)`，而该 id 来自按 DOM 序扫描的落点；
   重排会让两套下标错位。`groupTasksByDay` 只做**稳定分区**（组内序 = 传入序）。

另有一条：**「天」必须按本地时区算**。`created_at` 是 ISO UTC，`iso.slice(0,10)`
会把 00:00–07:59 的卡片算错一天。

**已知限制**：同列内**跨天**拖拽后，卡片会回到自己所属的天分组（天由日期决定），
视觉上像「拖拽没生效」。

## 布局：板块随屏幕自适应填充

**原来的问题**：在「更多显示设置」里显示/隐藏板块后，看板不随屏幕宽度重排 ——
`.board-scroll` 被 `max-width: N×400px` 锁死、`.board-column` 又被 `max-width: 400px`
限制，于是宽屏下留出大片空白。实测：1920px 屏只显示 2 个板块时看板仅 824px 宽（空 1060px）。

**现在的规则**：

| 规则 | 说明 |
|---|---|
| `.board-scroll` | `max-width: none`，填满 `.issue-board-layout` 的可用宽度 |
| `.board-column` | `max-width: none`，由 `.board` 的 `minmax(300px, 1fr)` 等分 |
| `.board` | 保留 `min-width: var(--main-board-min-width)`，窄屏横向滚动而非压扁 |
| 容器查询断点 | `threshold(N) = 324N + 335`，覆盖 N=1..9（N≥10 时他项面板不再收合） |

**实测填充率**（1920/2560 视口）：5 列 → 100%；2 列 → 100%（修复前 824px/空 1060px）；
1280/1600 视口 5 列时看板保持 1596px 最小宽度并横向滚动（预期行为）。

`--main-board-max-width` 已随之上线为**无用变量并从 `App.tsx` 移除** ——
若日后再想给「极宽屏 + 少列」加回一个上限，记得同时把居中一起做，否则空白会重新偏到一侧。

## 「今日推送」是看板里的一列

它是**看板内的普通列**，不是顶部独立专区：放在 `.board` 网格的**第一列**，
与状态列共用 `.board-column` / `.column-header` / `.column-heading` / `.column-list`，
因此列宽（参与等分）、表头高度（36px）、卡片样式、滚动行为全部一致。
唯一视觉差异是配色用 `--accent` 而非某个状态色。

要点：

- 卡片用**默认 `variant="main"`**。用 `sidebar` variant 会多出 `.sidebar-card-creator`
  底栏、少掉内联参与者，风格就不统一了。
- 表头的底色规则是**按列逐个列举**的（`.board-column.status-X .column-header`），
  新列必须一并列进那组选择器；漏了会表现为「表头没有底色」。
- 它**不是状态流转的一环**，故不画流向箭头：箭头画在
  `.board-column:not(:first-child) .column-header::before`，首列本就没有，
  再用 `.today-push-column + .board-column` 隐掉紧随其后那列的箭头。
- **只读、不进拖拽体系**：根节用 `onDragStartCapture` 在捕获阶段 `preventDefault()`
  取消整棵子树的原生拖拽（只传空 `onDragStart` 不够 —— 浏览器仍会发起拖拽）；
  也不注册 `onDragOver`/`onDrop`，因此它不是任何任务的落点。
- **计入列数**：`showTodayPanel` 为真时 `mainColumnCount` 要 +1，否则
  `.board` 网格少一列、右侧板块被挤窄。
- 折叠按钮复用其它板块 `.column-actions` 的图标按钮位（图标库无 `chevronUp`，
  用 `chevronDown` 旋转 -90°）。
- 显示与否由「更多显示设置」里的 **`showTodayPanel`** 开关控制。

## 构建

上游 tag 不含 `dist/`，**不构建就没有界面**：

```bash
cd board/upstream
npm install
npm run build          # vite build --config web/vite.config.ts
npm start              # node server/index.mjs  →  http://127.0.0.1:47823
```

测试：

```bash
npm run typecheck      # tsc --noEmit
npm test               # node --test + vitest（含按天分组的 35 例）
```

## 安全扫描说明（重要）

本目录用仓库自带的 `scan-secrets.py` 扫描时，**会有 23 处命中，全部是上游原样的假阳性**。
逐条核对过，分四类（下表刻意**不复写那些字面量** —— 复写会让扫描器报自己的文档，
噪音一大就没人再认真看它的输出了）：

| 规则 | 处数 | 实际性质 | 判定 |
|---|---|---|---|
| `absolute-user-path` | 9 | 上游测试里的**假想 Windows 用户名**（如 admin / alice 之类），不是真实账户 | 假阳性 |
| `secret-looking-literal` | 11 | **自述为测试值**的字符串（字面含 test / must-not 等），是上游测试夹具 | 假阳性 |
| `local-private-literal` | 2 | 上游 Jira 集成的固定项目 ID 常量，与本机私有前缀**命名撞车**（同名字符串，不同含义） | 假阳性 |
| `email` | 1 | 保留域名写法（`*.example.test`）的示例地址 | 假阳性 |

全部集中在 `upstream/test/**`（上游测试夹具）与 `upstream/shared/domain.mjs`、
`upstream/web/src/api.ts`（上游 Jira 常量）。**这两处源码文件是上游原样，未做改动。**

**本机真实信息（用户名 / 主机名 / tailnet 域名 / 课题关键词）一处都不在本目录内** ——
含这些内容的启动脚本与本机自检脚本已按设计排除（见仓库根 `.gitignore` 的 `board/` 段）。

> 另注：这些规则依赖**运行环境**（`CUR_USER` / `CUR_HOST` / `LOCAL_LITERALS`），
> 在 CI 等干净环境上不会命中。所以 CI 不会因本目录变红。
>
> ⚠️ **本目录每次改动后请重跑扫描器**，并确认命中数**不增加**：
> ```bash
> python workbuddy-taskboard-starter/references/setup/scan-secrets.py --root board
> ```
> 注意该脚本 `--root` 传**跨盘符**路径时，打印命中明细会因 `os.path.relpath` 抛
> `ValueError: path is on mount ...` 而**吞掉命中位置**（只报数量）。要定位是哪个文件，
> 用 `_build/locate_secret_hits.py`（复用官方 `scan_text()` 判据、另补行号）。

## 未纳入的内容（有意排除）

| 排除项 | 原因 |
|---|---|
| `node_modules/`、`dist/` | 依赖与构建产物，可重建 |
| `logs/`、`*.log`、`_il.err` | 运行态日志，且含本机绝对路径 |
| `*.cmd`、`*.pyw`、根目录 `*.py` | 装本机绝对路径的启动/自检脚本，含真实主机名 |
| `*.bak-*`、`*.removed-*`、`web/src.bak-*` | 本机备份残件 |
| `src-tauri/` | 桌面端打包（Tauri），本仓库不涉及 |
| `docs/` 与上游 README | 上游文档；含一张 1.7MB 截图。需要时直接看上游仓库 |
| `.data/`、`.wrangler/` | 本地数据库与 CF 状态 |
