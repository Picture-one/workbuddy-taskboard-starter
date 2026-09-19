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
    ├── web/             # 前端（React + Vite）← 按天分组 / 滚动条 / 缩放下限改造在此
    │   └── src/         #   boardZoom.ts（下限纯函数）· useBoardZoomGuard.ts（纠偏 hook）
    │                    #   boardZoom.test.tsx（14 项单测，需 --environment jsdom）
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

## 滚动条：能滚不等于能拖

**原来的问题**：浏览器缩放（Ctrl+滚轮）放大后右侧板块被推出视野，**只能用方向键，鼠标拖不动**。

排查发现这不是「滚动能力」缺失 —— 实测 2x 缩放下 `.board-scroll` 的横向可滚上限有 **832px**，
滚动能力一直在。真正的原因是 `styles.css` 把它的滚动条**彻底隐藏**了：

```css
/* 修复前 —— 有滚动能力，但没有抓手 */
.board-scroll { scrollbar-width: none; }
.board-scroll::-webkit-scrollbar { display: none; }
```

**判据**：`scrollWidth - clientWidth > 0` 只说明「能滚」；要判断「有没有抓手」，得看
**滚动条 gutter** = `offsetHeight - clientHeight`（横） / `offsetWidth - clientWidth`（纵）。
`> 0` 才是传统占位式滚动条（可抓），`= 0` 是 overlay 式或不存在。

**现在的规则**（三处滚动条各自由专属规则显式恢复，都用细滚动条常驻）：

| 容器 | 滚动条 | 对应体验 |
|---|---|---|
| `.board-scroll` | 横向 + 纵向均可见 | 右侧被遮挡的板块（横向拖动）、纵向内容超出（底部拖动） |
| `.board-column` / `.column-list` | 纵向可见 | 列内任务过多时的「底部滚动条」 |
| `.other-tasks-list` | 保持隐藏 | 它在自适应宽度的侧栏里，多一条滚动条会挤压卡片 |

同时 `.board-scroll` 的 `overflow-y` 从 `hidden` 改为 `auto` ——
祖先链（`.layout` / `.workspace` / `.app-shell` / `body`）全是 `overflow: hidden`，
纵向原本**根本没有任何滚动入口**，所以纵向是真的拖不动，不全是滚动条的问题。

⚠️ 改滚动条时**必须两处一起改**：`scrollbar-width`（标准属性）与
`::-webkit-scrollbar`（WebKit 伪元素）。只改一个，滚动条仍然不显示。

⚠️ 删除任何一处 `display: none` 前，先确认没有**另一个同优先级、位置更靠后**的规则
仍在把它盖回去 —— 我这次就踩到：把 `.board-scroll` 从隐藏组移除后，前面一段旧块
以同优先级 `display: none` 覆盖了新规则，表现为「规则写了却没生效」。

**实测**（`_build/verify_scrollbar_drag.mjs`，18 PASS / 0 FAIL）：

| 缩放 | 滚动条 gutter | 滚轮可滚 | 可达最右 |
|---|---|---|---|
| 1.25x | 10px, `display: block` | 0 → 352（满） | 352/352 ✓ |
| 1.5x | 10px, `display: block` | 0 → 400 | 565/565 ✓ |
| 2x | 10px, `display: block` | 0 → 400 | 832/832 ✓ |

几何实测（`.board` 有 `min-width`，缩放**不会**压窄板块，列宽恒 300px、总宽恒 1596px）：

| 缩放 | CSS 视口 | 容器宽 | 横向可滚上限 | 被推出视野的列数 |
|---|---|---|---|---|
| 1x | 1600×900 | 1564 | 32px | 1 |
| 1.25x | 1280×720 | 1244 | 352px | 2 |
| 1.5x | 1067×600 | 1031 | 565px | 2 |
| 2x | 800×450 | 764 | 832px | 3 |

### 再进一步：6px 与「`scrollbar-width` 复位定律」

滚动条找回来了，但 10px 占位偏宽，要求再减细到 6px。
第一版只把 `::-webkit-scrollbar { width }` 改成 `6px` —— **完全无效**：gutter 纹丝不动，
可见条反而因为 thumb 边框变窄从 4px 变粗到 8px。

实测（`_build/diag_phase3_gutter.mjs` / `diag_phase3_legacy.mjs`，Chrome 152）：

> **滚动条占位宽度只由标准属性 `scrollbar-width` 决定**（`thin`=10px / `auto`=15px / `none`=0）。
> 只要元素上声明了 `scrollbar-width` / `scrollbar-color`，legacy 的
> `::-webkit-scrollbar { width }` 对**布局**就完全无效 ——
> `getComputedStyle` 会如实回报 6px，但 gutter 恒为 10px。

| 组合 | gutter |
|---|---|
| `thin` + webkit `6px` | 10px ❌ |
| `thin` + webkit `4px` | 10px ❌ |
| `thin` + webkit `14px` | 10px ❌ |
| `auto` + `auto` + webkit `6px` | **6px ✓** |
| `auto` + `auto` + webkit `14px` | 14px ✓ |
| `none` | 0 |

所以要拿到 6px，必须**先把两个标准属性显式复位为 `auto`**，让 legacy 尺寸重新接管：

```css
.board-scroll {
  scrollbar-width: auto;   /* ⚠️ 不是笔误，别改回 thin */
  scrollbar-color: auto;
}
.board-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
.board-scroll::-webkit-scrollbar-thumb { border: 1px solid transparent; } /* 可见条 = 6−2 = 4px */
```

- **副作用已评估**：本文件由 `*` 规则给所有元素统一声明了 `scrollbar-width: thin`，
  这里改成 `auto` **不会**被后代继承放大 —— 后代仍各自命中 `*`。
- **风险与兜底**：若未来 Chromium 彻底移除 legacy 伪元素，这里会退化成 `auto` 的 15px。
  届时改回 `thin`（10px）即可 —— 比 15px 好，且仍然可抓。
- **防回归**：验收探针里有一条**前置条件断言**（`scrollbar-width === "auto"` 且
  `scrollbar-color === "auto"`）。只断言 `gutter === 6` 是不够的 ——
  将来有人改回 `thin` 时，gutter 会变成 10px，若探针只查「能否滚动」就会给出假绿灯。

### 三级可见度：静息很淡，靠近才清楚

6px 的条很难被鼠标发现，要求「保留但变淡，hover 变清楚」。三档变量：

| 变量 | 浅色 | 深色 | 触发时机 |
|---|---|---|---|
| `--scrollbar-thumb` | 0.15 | 0.15 | 静息 |
| `--scrollbar-thumb-soft` | 0.28 | 0.32 | **指针进入容器**（预热） |
| `--scrollbar-thumb-hover` | 0.46 | 0.50 | 指针压在条上 |

中间那档是关键：条只有 6px，**要求用户精确命中才给反馈等于没有反馈**。
所以指针进入 `.board-scroll` / 任一板块列就先提亮，真正压到条上再加深。

`.other-tasks-list` 保持 `scrollbar-width: none` —— 它在自适应宽度的侧栏里，
任何占位都会挤压卡片；而且 `none` 才能彻底去掉占位，**不能**改用这里的 `auto` + legacy 写法。

## 缩放下限：按板块数动态计算

**原来的问题**：显示 6 个板块时字号已经偏小，但继续缩小仍显示 6 个板块、字号继续变小，
没有任何下限。

**先说清一个语义**：缩放**缩小**会**增大**可用 CSS 宽度，所以「看不全」从来不是缩小的后果 ——
缩小的真正害处是**字号小到不可读**。所以下限按**可读性门槛**定，不按宽度定。

**规则**（`web/src/boardZoom.ts`，纯函数、可单测）：

| 常量 | 值 | 含义 |
|---|---|---|
| `ABSOLUTE_MIN_ZOOM` | `0.75` | 绝对下限：低于此值时 12px 正文只剩 <9px |
| `COMFORTABLE_COLUMN_COUNT` | `4` | 4 列以内允许缩到绝对下限 |
| `ZOOM_STEP_PER_EXTRA_COLUMN` | `0.05` | 每多 1 列抬高 5% |
| `MAX_ZOOM` | `1` | 上限 100%，**绝不要求用户放大**（那会反过来遮挡） |

下限表（板块数 1..9）：**75% → 75% → 75% → 75% → 80% → 85% → 90% → 95% → 100%**

⚠️ **字号与下限联动**（2026-09-18 由 `0.70` 上调至 `0.75`）：正文从 11px 抬到 12px 后，
「可读性门槛」对应的缩放必须同步抬高 —— 二者是同一个约束的两面：
```
下限 ≥ 最小可读字号 / 正文字号 = 9px / 12px = 0.75
```
改字号却不抬下限，等于把之前修好的问题重新放回来。`boardZoom.test.tsx` 里有这条断言的直接编码。

⚠️ `Infinity` 要单独拦：`Number.isFinite(Infinity)` 为 `false`，若直接落到「按 1 列处理」的
兜底，会得出「板块无穷多却允许缩到最小」的反直觉结果。

**浏览器缩放的固有约束**：`Ctrl+滚轮` / `Ctrl+±` 由宿主（WorkBuddy webview / Chrome）
直接处理，**不经过页面 JS，无法被 `preventDefault()` 拦下**。所以「不允许继续缩小」只能做成
**越界即纠偏 + 提示**，不能做成硬拦截。纠偏优先级（`web/src/useBoardZoomGuard.ts`）：

1. `webFrame.setZoomFactor`（Electron / WorkBuddy 注入的接口）
2. `document.body.style.zoom`
3. 都不可用 → **仅提示，不静默失败**

UI 上给一个显式入口：`−  100%  ＋`（`.board-zoom-control`），到下限时 `−` 置灰、
比例文字高亮（`.is-clamped`），让「已经缩不动了」可见。

⚠️ `devicePixelRatio` 叠加了系统 DPI 缩放（125% / 150%），必须以**首次挂载时的 DPR 为基准**
算相对缩放，否则会把「系统 DPI」误判成「用户缩放」而误报下限。

## `body.style.zoom` 的布局补偿（底侧空白带）

兜底路径走 `document.body.style.zoom = z` 时，会出现一条只在特定操作序列下才现身、
**刷新即消失**的底侧空白带。这是三层叠加的结果。

**第一层 —— `zoom` 缩放矩形，但 `vh` 认的是未缩放视口**

`body.style.zoom = z` 会让该子树里**每个盒子的最终矩形 = 计算值 × z**，而 CSS 视口不变。
于是 `height: 100vh` 的 `.app-shell` 布局仍是 `100vh`，渲染出来却只有 `z × 视口高`，
底部空出 **`(1−z) × 视口高`**（545px 视口、`z=0.9` 时实测留白 54.5px）。

更糟的是方向反了：缩放的本意是「缩小内容、容纳更多板块」，而 shell 被压窄后，
可用的 **CSS 宽度反而变小** —— 与设计语义南辕北辙。

补偿：把布局尺寸写成 `100vh × (1/z)`，渲染后 = 恰好铺满视口，可用 CSS 宽度变成 `视口/z`，
与浏览器缩小的语义一致。用「乘 1/z」而不是「除 z」是因为 CSS `calc()` 对变量做除法的兼容性更弱。

```css
.app-shell, .workspace { height: calc(100vh * var(--app-zoom-layout, 1)); }
```

⚠️ **只补 `vh`，绝不补百分比**（这是第一版踩的坑）：百分比是在**被缩放元素自己的坐标系**里
解析的，等于已经自动除过 z 了；再乘一次 `1/z` 就是双重补偿 —— 实测右侧凭空多出 120px，
缩放按钮被挤出视口点不到。`diag_zoom_units.mjs` 的实测对照：

| 单位 | 计算值（z=0.9） | 渲染矩形 | 是否补偿 |
|---|---|---|---|
| `vh` | 545 | 490.5 | ✅ 必须补 1/z |
| `%` | 1333.33 | 1200 | ❌ 补了就双重补偿 |

**第二层 —— 内联样式不持久，导致「刷新即消失」**

`body.style.zoom` 是 JS 写的内联样式，刷新即清零；而 resize 不会清理它。
这正好解释了用户报的现象：**刷新后不出现、直接放大窗口不出现、
「缩到最小再放大」必然出现**（反复触发 resize，但内联 zoom 一直挂着）。

**第三层 —— `zoom` 不改 `devicePixelRatio`，状态与实际脱钩**

`readZoom()` 靠 DPR 读缩放，而 `body.style.zoom` 对 DPR **毫无影响**。
于是每次 resize 的 `sync()` 都会把 React state 冲回 `1`，
UI 显示「100%」而 body 上实际是 `0.9`；此后点「＋」又被 `next > 1` 判为越界拒绝执行，
整条状态彻底卡死到刷新为止。

修法：用单独的 ref 记录「真正落到 body 上的缩放值」，有效缩放 = `readZoom() × bodyZoomRef`；
复位到 100% 时必须 `removeProperty()` **而不是写 `"1"`** —— 留下内联痕迹同样清不掉。

`boardZoom.ts` 里把这些收成一个纯函数 `bodyZoomDeclaration(z)`，便于单测：

| 输入 | 返回 |
|---|---|
| `0.9` | `{ zoom: "0.9", layoutVar: "1.111…" }` |
| `1`（含 ≥0.9995） | `{ zoom: null, layoutVar: null }` ← 表示「移除属性」 |
| 越界值 | 钳制到 `[0.1, 5]` |

**验收**（`_build/verify_bottom_gap.mjs`，39 PASS / 0 FAIL）：完整复跑用户报告的操作序列
（1080×545 → 直接放大 → 420×300 → 放大回 → 抖动 → 刷新），全程底侧 / 右侧空白 = **0px**，
且复位后 `body.style.zoom` 与 `--app-zoom-layout` 均被移除。

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
