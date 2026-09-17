import { describe, expect, it } from "vitest";
import {
  DEFAULT_BOARD_DISPLAY_SETTINGS,
  normalizeBoardDisplaySettings,
} from "./BoardCardDisplayMenu";

/**
 * 看板上可能出现的全部列。**故意硬编码**而不从 `MAIN_STATUSES` 复用 ——
 * 若将来新增状态，这里会失败，从而强迫做一次「新状态默认放哪」的显式决定。
 */
const ALL_STATUSES = [
  "backlog",
  "todo",
  "in_progress",
  "in_review",
  "blocked",
  "done",
  "canceled",
  "archived",
] as const;

/**
 * **真实**的旧版 v3 存储值（取自 `%APPDATA%/Codex Taskboard/client-storage.json`）。
 * 它没有 `groupByDay` / `showTodayPanel` —— 正是「老用户升级后新字段为 undefined」的现场。
 */
const LEGACY_STORED_VALUE = {
  cover: true,
  body: true,
  mainStatuses: ["todo", "in_progress", "blocked", "in_review"],
  sidebarStatuses: ["backlog", "done", "canceled", "archived"],
  hiddenStatuses: [],
};

function allPlaced(settings: ReturnType<typeof normalizeBoardDisplaySettings>) {
  return [...settings.mainStatuses, ...settings.sidebarStatuses, ...settings.hiddenStatuses];
}

describe("旧配置回归：缺新字段时必须落到「默认开启」", () => {
  it("真实旧 v3 值不含新字段 → 两个开关都为 true（而不是 undefined 被当成关闭）", () => {
    const settings = normalizeBoardDisplaySettings(LEGACY_STORED_VALUE);
    expect(settings.groupByDay).toBe(true);
    expect(settings.showTodayPanel).toBe(true);
    // guard：确认这个 fixture 真的没有新字段，否则本测试就是同义反复
    expect("groupByDay" in LEGACY_STORED_VALUE).toBe(false);
    expect("showTodayPanel" in LEGACY_STORED_VALUE).toBe(false);
  });

  it("旧配置其余字段原样保留（不因归一化而改变既有行为）", () => {
    const settings = normalizeBoardDisplaySettings(LEGACY_STORED_VALUE);
    expect(settings.cover).toBe(true);
    expect(settings.body).toBe(true);
    expect(settings.mainStatuses).toEqual(["todo", "in_progress", "blocked", "in_review"]);
    expect(settings.sidebarStatuses).toEqual(["backlog", "done", "canceled", "archived"]);
    expect(settings.hiddenStatuses).toEqual([]);
  });

  it("用户显式关闭必须被尊重（不能被默认值覆盖）", () => {
    const settings = normalizeBoardDisplaySettings({ ...LEGACY_STORED_VALUE, groupByDay: false, showTodayPanel: false });
    expect(settings.groupByDay).toBe(false);
    expect(settings.showTodayPanel).toBe(false);
  });

  it("值为非布尔时回退默认，而不是把字符串当真值", () => {
    const settings = normalizeBoardDisplaySettings({ groupByDay: "false", showTodayPanel: 0 });
    expect(settings.groupByDay).toBe(true);
    expect(settings.showTodayPanel).toBe(true);
  });
});

describe("分区完整性（既有隐患修复）", () => {
  it("每个状态恰好出现在一个分区，一个不多一个不少", () => {
    const settings = normalizeBoardDisplaySettings(LEGACY_STORED_VALUE);
    const placed = allPlaced(settings);
    expect(placed.slice().sort()).toEqual([...ALL_STATUSES].sort());
    expect(new Set(placed).size).toBe(placed.length);
  });

  it("对任意残留配置都保持完整性（含缺数组、空对象）", () => {
    for (const raw of [{}, { mainStatuses: [] }, { sidebarStatuses: ["done"] }, { hiddenStatuses: ALL_STATUSES.slice() }]) {
      const placed = allPlaced(normalizeBoardDisplaySettings(raw));
      expect(placed.slice().sort()).toEqual([...ALL_STATUSES].sort());
      expect(new Set(placed).size).toBe(placed.length);
    }
  });

  it("孤立状态回到「默认归属」，而不是一律塞进主看板", () => {
    const settings = normalizeBoardDisplaySettings({
      cover: true,
      body: false,
      mainStatuses: ["todo"],
      sidebarStatuses: ["done"],
      hiddenStatuses: [],
    });
    // in_progress / blocked / in_review 默认在主看板；backlog / canceled / archived 默认在侧栏
    expect(settings.mainStatuses).toEqual(["todo", "in_progress", "blocked", "in_review"]);
    expect(settings.sidebarStatuses).toEqual(["done", "backlog", "canceled", "archived"]);
    expect(settings.hiddenStatuses).toEqual([]);
  });

  it("重复状态被去重（保留首次出现的位置）", () => {
    const settings = normalizeBoardDisplaySettings({
      mainStatuses: ["todo", "todo"],
      sidebarStatuses: ["todo", "done", "done"],
      hiddenStatuses: ["archived", "archived"],
    });
    expect(settings.mainStatuses.filter((status) => status === "todo")).toHaveLength(1);
    expect(settings.sidebarStatuses.filter((status) => status === "done")).toHaveLength(1);
    expect(settings.sidebarStatuses).not.toContain("todo");
    expect(settings.hiddenStatuses).toEqual(["archived"]);
  });

  it("非法状态值与非字符串项被丢弃", () => {
    const settings = normalizeBoardDisplaySettings({
      mainStatuses: ["todo", "nope", null, 42, {}, "blocked"],
      sidebarStatuses: "done",
      hiddenStatuses: undefined,
    });
    expect(settings.mainStatuses).toEqual(["todo", "blocked", "in_progress", "in_review"]);
    // sidebarStatuses 传的是字符串而非数组 → 视为空，随后由孤立状态补全
    expect(settings.sidebarStatuses).toEqual(["backlog", "done", "canceled", "archived"]);
  });
});

describe("健壮性", () => {
  it("非对象输入一律回落到全默认", () => {
    for (const raw of [null, undefined, [], "x", 42, true]) {
      expect(normalizeBoardDisplaySettings(raw)).toEqual(DEFAULT_BOARD_DISPLAY_SETTINGS);
    }
  });

  it("默认值本身是完整的（避免默认值漏字段）", () => {
    const placed = allPlaced(DEFAULT_BOARD_DISPLAY_SETTINGS);
    expect(placed.slice().sort()).toEqual([...ALL_STATUSES].sort());
    expect(DEFAULT_BOARD_DISPLAY_SETTINGS.groupByDay).toBe(true);
    expect(DEFAULT_BOARD_DISPLAY_SETTINGS.showTodayPanel).toBe(true);
  });

  it("不修改传入对象", () => {
    const raw = { mainStatuses: ["todo"], sidebarStatuses: [], hiddenStatuses: [] };
    const snapshot = JSON.stringify(raw);
    normalizeBoardDisplaySettings(raw);
    expect(JSON.stringify(raw)).toBe(snapshot);
  });
});
