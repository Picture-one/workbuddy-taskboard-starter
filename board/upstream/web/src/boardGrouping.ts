import type { Task } from "./types";

/**
 * 「任务按天分组」的纯函数层。
 *
 * 设计要点（与实施计划 P2 对应）：
 * 1. **天的判定一律走本地时区**。服务端存的是 ISO UTC（`createdAt`），本机 UTC+8，
 *    所以 `"2026-09-16T23:30:00.000Z"` 的本地日是 **09-17**；用 `iso.slice(0, 10)` 会把
 *    本地 00:00–07:59 的卡片全算到前一天。见 `localDay()`。
 * 2. **分组键优先 `startDate`**。服务端 `parseTaskCreate`（`shared/task-input.mjs:116-142`）
 *    的允许字段集里**没有 `createdAt`**，由服务端 `now()` 生成 —— 所以历史回填只能靠
 *    `startDate` 落回原推送日。见 `dayGroupKey()`。
 * 3. **`groupTasksByDay` 是稳定分区，不重排组内任务**。拖拽不变式
 *    （`BoardColumn.findDropBefore` 按真实 DOM 序算落点、`moveTask` 按数组下标找落点）
 *    要求「卡片 DOM 序 = 数组序」。因此调用方必须**先用 `sortTasksByDay()` 排序**，
 *    再分组；此时 `flatten(groups)` 与排序后的数组**严格逐项相同**。
 */

/** 每日推送卡片的静态标签核心段。具体 kind 用 `daily-card-<kind>` 前缀扩展（见 `mail-bridge/daily_card_task.py`）。 */
export const DAILY_CARD_LABEL = "daily-card";

/** 无可用日期时的兜底分组键；排序恒排最后。 */
export const UNDATED_KEY = "__undated__";

export interface DayGroup {
  /** 分组键：`YYYY-MM-DD`，或 `UNDATED_KEY`。 */
  key: string;
  /** 展示文案：`今天` / `昨天` / `9月15日 周二`。 */
  label: string;
  count: number;
  /** 组内顺序 = 传入顺序（稳定分区）；拖拽不变式依赖这一点。 */
  tasks: Task[];
}

/** 与 i18n 解耦的文案入参，避免本模块依赖 React context。 */
export interface DayGroupingText {
  today: string;
  yesterday: string;
  undated: string;
}

const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/**
 * 是否为**真实存在的**日历日（严格校验）。
 *
 * ⛔ `DAY_PATTERN` 只验形状，拦不住 `"2026-13-99"` —— 而 `new Date(2026, 12, 99)`
 * **不会报错，会把溢出静默归一化**成 2027-04-09。那样分组键是 `2026-13-99`、
 * 天文案却是「4月9日」，键与文案互相矛盾，且卡片会落进一个凭空的组。
 * 因此这里用「本地格式化后回写比对」来识别溢出，而不是靠 `NaN` 判断。
 */
function isRealDay(value: string): boolean {
  if (!DAY_PATTERN.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return !Number.isNaN(date.getTime()) && localDay(date) === value;
}

/**
 * 本地时区日，形如 `2026-09-17`。
 *
 * ⛔ 绝不要退回 `date.toISOString().slice(0, 10)` —— 那是 UTC 日。
 */
export function localDay(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/**
 * 分组键：`startDate` 优先（回填依赖它），缺失或脏数据回退「本地日(createdAt)」。
 *
 * 脏数据一律走回退，包括：`""`、`"2026-9-14"`（未补零）、`"2026/09/14"`（分隔符错）、
 * 以及 `"2026-13-99"` / `"2026-02-30"` 这类**形状合法但日期不存在**的值（见 `isRealDay`）。
 */
export function dayGroupKey(task: Pick<Task, "startDate" | "createdAt">): string {
  const start = task.startDate;
  if (typeof start === "string" && isRealDay(start)) return start;
  const created = new Date(task.createdAt);
  return Number.isNaN(created.getTime()) ? UNDATED_KEY : localDay(created);
}

/**
 * 把 `YYYY-MM-DD` 解析为**本地**零点的 Date。
 *
 * ⛔ 不能写 `new Date("2026-09-17")`：那是 UTC 零点，在东半球会落到前一天。
 */
function parseLocalDay(key: string): Date | null {
  if (!isRealDay(key)) return null;
  const [year, month, day] = key.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** 按天平移，正确处理月/年进位与夏令时。 */
function shiftDay(key: string, delta: number): string {
  const date = parseLocalDay(key);
  if (!date) return "";
  date.setDate(date.getDate() + delta);
  return localDay(date);
}

function formatDayLabel(date: Date, locale: string, sameYear: boolean): string {
  const weekday = new Intl.DateTimeFormat(locale, { weekday: "short" }).format(date);
  if (locale.startsWith("zh")) {
    // zh-CN 下 month+day+weekday 连排无空格（实测 `2026年9月15日周二`），故显式补一个空格。
    const monthDay = new Intl.DateTimeFormat(locale, { month: "long", day: "numeric" }).format(date);
    return `${sameYear ? "" : `${date.getFullYear()}年`}${monthDay} ${weekday}`;
  }
  // 英文习惯周几在前。
  const monthDay = new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" }).format(date);
  return `${weekday}, ${sameYear ? "" : `${date.getFullYear()} `}${monthDay}`;
}

/** 天的展示文案。`todayKey` 可注入，便于单测固定「今天」。 */
export function dayGroupLabel(
  key: string,
  locale: string,
  text: DayGroupingText,
  todayKey: string = localDay(new Date()),
): string {
  if (key === UNDATED_KEY) return text.undated;
  if (key === todayKey) return text.today;
  if (key === shiftDay(todayKey, -1)) return text.yesterday;
  const date = parseLocalDay(key);
  if (!date) return key;
  return formatDayLabel(date, locale, key.slice(0, 4) === todayKey.slice(0, 4));
}

/** 天降序（新 → 旧），`UNDATED_KEY` 恒排最后。 */
export function compareDayGroupKeys(a: string, b: string): number {
  if (a === b) return 0;
  if (a === UNDATED_KEY) return 1;
  if (b === UNDATED_KEY) return -1;
  return b.localeCompare(a);
}

/** 同 `compareDayGroupKeys` 的任务级比较器；`sortTasksByDay` 与 `groupTasksByDay` 共用它，避免两份拷贝漂移。 */
export function compareTaskDayDesc(a: Task, b: Task): number {
  return compareDayGroupKeys(dayGroupKey(a), dayGroupKey(b));
}

/**
 * 按天降序排序（不改原数组）。
 *
 * `Array.prototype.sort` 自 ES2019 起保证**稳定**，所以同一天内任务保持原有相对顺序
 * （原有顺序来自 `sortTasks`：优先级 / sortOrder 等）。
 */
export function sortTasksByDay(tasks: Task[]): Task[] {
  return tasks.slice().sort(compareTaskDayDesc);
}

/**
 * 按天分组。**稳定分区**：组内顺序严格等于传入顺序，绝不重排。
 *
 * 前置条件（拖拽不变式）：传入的 `tasks` 应已用 `sortTasksByDay()` 排好。
 * 满足时 `groups.flatMap(g => g.tasks)` 与入参**严格逐项相同**；
 * 不满足时仍能正确分组，但扁平化结果是入参的一个排列。
 */
export function groupTasksByDay(
  tasks: Task[],
  locale: string,
  text: DayGroupingText,
  todayKey: string = localDay(new Date()),
): DayGroup[] {
  const buckets = new Map<string, Task[]>();
  for (const task of tasks) {
    const key = dayGroupKey(task);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(task);
    else buckets.set(key, [task]);
  }
  return [...buckets.keys()].sort(compareDayGroupKeys).map((key) => {
    // 上面循环保证了 key 必有值。
    const grouped = buckets.get(key) ?? [];
    return { key, label: dayGroupLabel(key, locale, text, todayKey), count: grouped.length, tasks: grouped };
  });
}

/** 是否为「每日推送」卡片。用**前缀**匹配，将来加 kind（`daily-card-<kind>`）前端无需改动。 */
export function isDailyCardTask(task: Pick<Task, "labels">): boolean {
  const labels = task.labels || [];
  return labels.some((label) => label === DAILY_CARD_LABEL || label.startsWith(`${DAILY_CARD_LABEL}-`));
}

/** 顶部「今日」专区的数据源：**只取指定日的每日推送卡片**，普通任务不混入。 */
export function dailyCardTasksForDay(tasks: Task[], dayKey: string): Task[] {
  return tasks.filter((task) => isDailyCardTask(task) && dayGroupKey(task) === dayKey);
}
