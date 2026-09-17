import { describe, expect, it } from "vitest";
import type { Task } from "./types";
import {
  DAILY_CARD_LABEL,
  UNDATED_KEY,
  dailyCardTasksForDay,
  dayGroupKey,
  dayGroupLabel,
  groupTasksByDay,
  isDailyCardTask,
  localDay,
  sortTasksByDay,
} from "./boardGrouping";

const TEXT = { today: "今天", yesterday: "昨天", undated: "未标注日期" };

/** 本机时区偏移（分钟，东正西负）。本机 UTC+8 → 480。 */
const OFFSET_MINUTES = -new Date().getTimezoneOffset();

/**
 * 由「本地日历时刻」反推 ISO 字符串 —— 这样断言的期望值与环境时区无关。
 * ⛔ 不要直接写 `new Date("2026-09-17T00:30:00Z")` 之类的字面量：那会把测试绑死在某个时区上。
 */
function isoForLocal(day: string, hour: number, minute = 0): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date, hour, minute).toISOString();
}

const BASE_TASK: Task = {
  id: "t",
  identifier: "T-1",
  projectId: "wb-demo",
  title: "t",
  description: "",
  status: "todo",
  priority: "none",
  labels: [],
  sortOrder: 0,
  threadId: null,
  threadBinding: null,
  legacyLocalThreadId: null,
  conversationRefs: [],
  participants: [],
  previewImage: null,
  activityKey: "",
  activityUpdatedAt: "2026-09-17T00:00:00.000Z",
  creatorType: "agent",
  creatorId: "codex-agent",
  creatorName: "Codex Agent",
  creatorAvatarUrl: null,
  assignee: { type: "agent", id: "codex-agent", name: "Codex Agent", avatarUrl: null },
  developmentContext: null,
  startDate: null,
  dueDate: null,
  recurrence: null,
  source: "local",
  externalOrigin: null,
  externalKey: null,
  externalUrl: null,
  archivedAt: null,
  relations: { parent: null, subIssues: [], blockedBy: [], blocks: [], related: [] },
  version: 1,
  createdAt: "2026-09-17T00:00:00.000Z",
  updatedAt: "2026-09-17T00:00:00.000Z",
};

function makeTask(
  id: string,
  createdAt: string,
  startDate: string | null = null,
  labels: string[] = [],
): Task {
  return { ...BASE_TASK, id, identifier: id.toUpperCase(), title: id, createdAt, startDate, labels };
}

const ids = (tasks: Task[]) => tasks.map((task) => task.id);

describe("本地时间判定（dayGroupKey / localDay）", () => {
  it("用本地 getter 取日，且与 UTC 日不同时以本地为准", () => {
    const createdAt = isoForLocal("2026-09-17", 0, 30);
    expect(dayGroupKey({ startDate: null, createdAt })).toBe("2026-09-17");
    expect(localDay(new Date(2026, 8, 17, 0, 30))).toBe("2026-09-17");
  });

  it("本地凌晨的卡片：iso.slice(0,10) 会判错一天（东半球时区普遍如此）", () => {
    // 本机 UTC+8 下，本地 09-17 00:30 的 ISO 是 2026-09-16T16:30:00.000Z
    const createdAt = isoForLocal("2026-09-17", 0, 30);
    expect(dayGroupKey({ startDate: null, createdAt })).toBe("2026-09-17");
    // 切片恰好在 offset ≤ 0 时正确；东半球（offset > 0）必然错 → 这就是不能用 slice 的原因
    expect(createdAt.slice(0, 10) === "2026-09-17").toBe(OFFSET_MINUTES <= 0);
  });

  it("本地零点整是跨日边界（正好是 16:00Z / 次日）", () => {
    const midnight = isoForLocal("2026-09-17", 0, 0);
    expect(dayGroupKey({ startDate: null, createdAt: midnight })).toBe("2026-09-17");
    expect(dayGroupKey({ startDate: null, createdAt: isoForLocal("2026-09-17", 23, 59) })).toBe("2026-09-17");
    expect(dayGroupKey({ startDate: null, createdAt: isoForLocal("2026-09-18", 0, 0) })).toBe("2026-09-18");
  });

  it("startDate 优先于 createdAt —— 回填场景（任务今天建、归属 09-14）", () => {
    const task = makeTask("WOR-8", isoForLocal("2026-09-17", 13, 36), "2026-09-14");
    expect(dayGroupKey(task)).toBe("2026-09-14");
  });

  it("脏 startDate（空串 / 未补零 / 非日期）一律回退 createdAt", () => {
    const createdAt = isoForLocal("2026-09-17", 13, 0);
    for (const dirty of ["", "2026-9-14", "20260914", "2026/09/14", " 2026-09-14 "]) {
      expect(dayGroupKey(makeTask("x", createdAt, dirty))).toBe("2026-09-17");
    }
  });

  it("形状合法但日期不存在的 startDate 也必须回退（Date 会把溢出静默归一化）", () => {
    const createdAt = isoForLocal("2026-09-17", 13, 0);
    // 2026-13-99 / 2026-02-30 都能通过「4-2-2 数字」的形状校验，但都不是真实日期：
    // new Date(2026, 12, 99) 不报错，而是变成 2027-04-09。
    for (const overflow of ["2026-13-99", "2026-02-30", "2026-00-10", "2026-01-00"]) {
      expect(dayGroupKey(makeTask("x", createdAt, overflow))).toBe("2026-09-17");
    }
  });

  it("createdAt 非法 → 归入 UNDATED_KEY", () => {
    expect(dayGroupKey({ startDate: null, createdAt: "not-a-date" })).toBe(UNDATED_KEY);
    expect(dayGroupKey({ startDate: null, createdAt: "" })).toBe(UNDATED_KEY);
  });
});

describe("按天分组（sortTasksByDay / groupTasksByDay）", () => {
  const sep14 = makeTask("d14", isoForLocal("2026-09-14", 9, 0), "2026-09-14");
  const sep17a = makeTask("d17a", isoForLocal("2026-09-17", 9, 0), "2026-09-17");
  const sep17b = makeTask("d17b", isoForLocal("2026-09-17", 9, 5), "2026-09-17");
  const broken = makeTask("bad", "not-a-date");

  it("天按新→旧排序，UNDATED 恒排最后", () => {
    const groups = groupTasksByDay([sep14, broken, sep17a], "zh-CN", TEXT, "2026-09-17");
    expect(groups.map((group) => group.key)).toEqual(["2026-09-17", "2026-09-14", UNDATED_KEY]);
  });

  it("组内顺序严格等于传入顺序（稳定分区，不重排）", () => {
    const input = sortTasksByDay([sep14, sep17a, sep17b]);
    const groups = groupTasksByDay(input, "zh-CN", TEXT, "2026-09-17");
    const today = groups.find((group) => group.key === "2026-09-17")!;
    expect(ids(today.tasks)).toEqual(["d17a", "d17b"]);
    expect(today.count).toBe(2);
  });

  it("输入已按天排序时，扁平化结果与输入严格逐项相同 ← 拖拽不变式前提", () => {
    const input = sortTasksByDay([sep14, sep17a, sep17b, broken]);
    const groups = groupTasksByDay(input, "zh-CN", TEXT, "2026-09-17");
    expect(groups.flatMap((group) => group.tasks)).toEqual(input);
    expect(groups.flatMap((group) => ids(group.tasks))).toEqual(ids(input));
  });

  it("输入未排序时仍正确分组，且是同一多重集的一个排列（不强求相等）", () => {
    const input = [sep14, sep17a, sep17b];
    const groups = groupTasksByDay(input, "zh-CN", TEXT, "2026-09-17");
    const flat = groups.flatMap((group) => ids(group.tasks));
    expect(flat.slice().sort()).toEqual(ids(input).slice().sort());
    // 刻意不同：09-14 被排到了后面
    expect(flat).not.toEqual(ids(input));
  });

  it("空输入 → 空分组", () => {
    expect(groupTasksByDay([], "zh-CN", TEXT, "2026-09-17")).toEqual([]);
  });

  it("count 与组内条数一致，且各组条数之和 = 输入条数", () => {
    const input = sortTasksByDay([sep14, sep17a, sep17b, broken]);
    const groups = groupTasksByDay(input, "zh-CN", TEXT, "2026-09-17");
    for (const group of groups) expect(group.count).toBe(group.tasks.length);
    expect(groups.reduce((sum, group) => sum + group.count, 0)).toBe(input.length);
  });

  it("sortTasksByDay 不修改原数组", () => {
    const input = [sep14, sep17a];
    const before = ids(input);
    sortTasksByDay(input);
    expect(ids(input)).toEqual(before);
  });
});

describe("天文案（dayGroupLabel）", () => {
  it("今天 / 昨天", () => {
    expect(dayGroupLabel("2026-09-17", "zh-CN", TEXT, "2026-09-17")).toBe("今天");
    expect(dayGroupLabel("2026-09-16", "zh-CN", TEXT, "2026-09-17")).toBe("昨天");
  });

  it("昨天跨月正确回退", () => {
    expect(dayGroupLabel("2026-08-31", "zh-CN", TEXT, "2026-09-01")).toBe("昨天");
    expect(dayGroupLabel("2026-02-28", "zh-CN", TEXT, "2026-03-01")).toBe("昨天");
  });

  it("同年：`9月15日 周二`（zh-CN 下 month+day+weekday 连排无空格，必须显式补）", () => {
    expect(dayGroupLabel("2026-09-15", "zh-CN", TEXT, "2026-09-20")).toBe("9月15日 周二");
  });

  it("跨年补年份", () => {
    expect(dayGroupLabel("2026-09-15", "zh-CN", TEXT, "2027-01-05")).toBe("2026年9月15日 周二");
  });

  it("en：周几在前", () => {
    expect(dayGroupLabel("2026-09-15", "en", TEXT, "2026-09-20")).toBe("Tue, Sep 15");
    expect(dayGroupLabel("2026-09-15", "en", TEXT, "2027-01-05")).toBe("Tue, 2026 Sep 15");
  });

  it("UNDATED 用兜底文案；非法 key 原样返回（不编造一个溢出后的日期）", () => {
    expect(dayGroupLabel(UNDATED_KEY, "zh-CN", TEXT, "2026-09-17")).toBe("未标注日期");
    // 曾经会输出「4月9日 周五」——那是 2026-13-99 被 Date 静默归一化成的 2027-04-09，
    // 会与分组键 2026-13-99 自相矛盾。必须原样返回 key。
    expect(dayGroupLabel("2026-13-99", "zh-CN", TEXT, "2026-09-17")).toBe("2026-13-99");
    expect(dayGroupLabel("2026-02-30", "zh-CN", TEXT, "2026-09-17")).toBe("2026-02-30");
  });
});

describe("每日推送卡片识别（isDailyCardTask）", () => {
  it("认得核心标签与任意 kind 前缀", () => {
    expect(DAILY_CARD_LABEL).toBe("daily-card");
    expect(isDailyCardTask(makeTask("a", BASE_TASK.createdAt, null, ["daily-card"]))).toBe(true);
    expect(isDailyCardTask(makeTask("b", BASE_TASK.createdAt, null, ["daily-card", "daily-card-paper", "paper-reading"]))).toBe(true);
    expect(isDailyCardTask(makeTask("c", BASE_TASK.createdAt, null, ["daily-card-db"]))).toBe(true);
    expect(isDailyCardTask(makeTask("d", BASE_TASK.createdAt, null, ["x", "daily-card-newkind"]))).toBe(true);
  });

  it("不误匹配相似标签，也不匹配普通任务", () => {
    expect(isDailyCardTask(makeTask("a", BASE_TASK.createdAt, null, ["daily-cards"]))).toBe(false);
    expect(isDailyCardTask(makeTask("b", BASE_TASK.createdAt, null, ["mydaily-card"]))).toBe(false);
    expect(isDailyCardTask(makeTask("c", BASE_TASK.createdAt, null, []))).toBe(false);
    expect(isDailyCardTask(makeTask("d", BASE_TASK.createdAt, null, ["paper-reading", "database"]))).toBe(false);
  });
});

describe("今日专区数据源（dailyCardTasksForDay）", () => {
  it("只取指定日的每日推送卡片，普通任务不混入", () => {
    const tasks = [
      makeTask("card17", isoForLocal("2026-09-17", 9, 0), null, ["daily-card", "daily-card-paper"]),
      makeTask("card14", isoForLocal("2026-09-14", 9, 0), null, ["daily-card", "daily-card-db"]),
      makeTask("plain17", isoForLocal("2026-09-17", 10, 0), null, ["paper-reading"]),
      makeTask("backfilled", isoForLocal("2026-09-17", 11, 0), "2026-09-16", ["daily-card-paper"]),
    ];
    expect(ids(dailyCardTasksForDay(tasks, "2026-09-17"))).toEqual(["card17"]);
    expect(ids(dailyCardTasksForDay(tasks, "2026-09-14"))).toEqual(["card14"]);
    expect(ids(dailyCardTasksForDay(tasks, "2026-09-16"))).toEqual(["backfilled"]);
    expect(dailyCardTasksForDay(tasks, "2026-09-13")).toEqual([]);
  });
});
