# local-patches — 本机改造快照

> **这不是安装器的一部分。** `main` 分支的定位是「安装器 + 运维手册」，本目录是**旁挂的源码快照**，
> 记录本机对看板做过的定制改造。放在这里只为拿到 **git 历史与回滚能力** ——
> 改造前 `dashi-taskboard` 目录不是 git 仓库，改错了没有 diff 可比、没有版本可退。

## 为什么单独放一个分支

| | `main` | `local-patches`（本分支） |
|---|---|---|
| 定位 | 安装器 + 手册，任何人可照它装一套 | 本机改造快照，**不对外承诺可用** |
| 来源 | 上游 `chuspeeism/dashi-taskboard` 固定 tag | 本机 `%USERPROFILE%\.workbuddy\apps\` |
| 影响 | 改动会影响安装流程与文档守卫 | **完全旁挂，不影响 `main` 任何人** |

## 目录结构

路径刻意与看板源码保持同构，便于日后对照或复制回原位：

```
local-patches/
├── web/src/                          # ← 对应 %USERPROFILE%\.workbuddy\apps\dashi-taskboard\web\src\
│   ├── boardGrouping.ts              #   新增：按天分组纯函数层
│   ├── boardGrouping.test.tsx        #   新增：23 例单测
│   ├── App.tsx                       #   改动：接入分组、今日专区、设置归一化
│   ├── styles.css                    #   改动：分节标题 / 今日专区样式
│   └── components/
│       ├── BoardColumn.tsx           #   改动：列内按天分节渲染
│       ├── BoardCardDisplayMenu.tsx  #   改动：新增开关 + normalizeBoardDisplaySettings()
│       ├── BoardCardDisplayMenu.test.tsx  # 新增：12 例单测
│       └── TodayPushPanel.tsx        #   新增：顶部「今日推送」专区
└── mail-bridge/                      # ← 对应 %USERPROFILE%\.workbuddy\apps\mail-bridge\
    ├── daily_card_task.py            #   新增：写侧唯一实现（一天一张卡片）
    └── backfill_daily_cards.py       #   新增：历史推送回填
```

**注意**：这里只是这 10 个文件的快照。看板其余文件（`vite.config.ts`、`package.json`、
上游原有组件等）**未纳入**，它们仍是上游原样。若要完整重建，需先按 `main` 的安装器装好，
再用本目录文件覆盖对应路径。

## 这批改造做了什么：任务按天分组

**解决的问题**：每日推送的任务与历史任务混在同一栏，看不出哪条是今天推的。

**根因有两层**：

1. 每日推送**从不创建任务** —— 旧逻辑是「1 个 Hub 任务 + 每天往评论里追加」，而看板卡片
   **不渲染评论**，所以用户永远看不见当天的推送；
2. 看板的组织维度只有 `status`，**没有「天」**，即便创建了任务也会混在一起。

**改动要点**：

- **一天一张独立任务卡片**，标签 `daily-card` + `daily-card-<kind>`，描述首行带
  `<!-- daily-card v1 kind=… date=… -->` marker；幂等判据用**静态标签 + marker**（绝不用标题，
  标题会变）。
- **分组日期只能来自 `startDate`** —— `parseTaskCreate`（上游 `shared/task-input.mjs`）**不接受
  `createdAt`**，由服务端 `now()` 生成。故回填历史卡片全靠 `--start-date`。
- **「天」按本地时区计算**（本机 UTC+8）。`created_at` 是 ISO UTC，
  `iso.slice(0,10)` 会把 00:00–07:59 的卡片算错一天。
- **列内分节 + 顶部「今日推送」专区** 两种展示形态，均可在显示设置里关闭。
- **刻意不重排传给 `BoardColumn` 的数组** —— 上游 `moveTask()` 从 `tasks`（而非渲染数组）取
  `destination` 并 `findIndex(beforeTaskId)`，而该 id 来自按 DOM 序扫描的落点。
  重排会让两套下标错位。`groupTasksByDay` 只做**稳定分区**。

## 已知限制

同列内**跨天**拖拽后，卡片会回到自己所属的天分组（天由日期决定），视觉上像「拖拽没生效」。

## 如何把改动同步回本机

```powershell
$app = "$env:USERPROFILE\.workbuddy\apps"
Copy-Item -Recurse -Force .\local-patches\web\src\*        "$app\dashi-taskboard\web\src\"
Copy-Item -Force          .\local-patches\mail-bridge\*.py "$app\mail-bridge\"
```

同步后需重建前端（上游 tag 不含 `dist/`，不构建就没有界面）。

## 脱敏

本目录文件已通过仓库自带的 `workbuddy-taskboard-starter/references/setup/scan-secrets.py`
扫描（11 条规则，`TOTAL HITS = 0`），并另经独立 regex 复核。**提交前如新增文件，务必重跑扫描器。**
