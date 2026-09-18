import { describe, expect, it } from "vitest";
import {
  ABSOLUTE_MIN_ZOOM,
  COLUMN_GAP,
  COMFORTABLE_COLUMN_COUNT,
  MAX_ZOOM,
  MIN_COLUMN_WIDTH,
  computeMinZoom,
  formatZoom,
  isAtOrBelowMinZoom,
  readZoom,
  requiredBoardWidth,
} from "./boardZoom";

describe("computeMinZoom", () => {
  it("板块数越多，下限越高（越不允许继续缩小）—— 单调不减", () => {
    const series = [1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => computeMinZoom({ columnCount: n }));
    for (let i = 1; i < series.length; i++) {
      expect(series[i]).toBeGreaterThanOrEqual(series[i - 1]);
    }
  });

  it("舒适列数以内保持绝对下限", () => {
    for (const n of [1, 2, 3, COMFORTABLE_COLUMN_COUNT]) {
      expect(computeMinZoom({ columnCount: n })).toBe(ABSOLUTE_MIN_ZOOM);
    }
  });

  it("超出舒适列数后逐级抬高，且严格大于绝对下限", () => {
    const over = computeMinZoom({ columnCount: COMFORTABLE_COLUMN_COUNT + 1 });
    expect(over).toBeGreaterThan(ABSOLUTE_MIN_ZOOM);
    expect(computeMinZoom({ columnCount: COMFORTABLE_COLUMN_COUNT + 2 })).toBeGreaterThan(over);
  });

  it("六个板块时下限高于绝对下限（对应本轮用户场景）", () => {
    const six = computeMinZoom({ columnCount: 6 });
    expect(six).toBeGreaterThan(ABSOLUTE_MIN_ZOOM);
    expect(six).toBeLessThanOrEqual(1);
  });

  it("下限永不超过 100%（绝不要求用户放大，放大反而会遮挡）", () => {
    for (const n of [1, 5, 6, 9, 20, 100]) {
      const limit = computeMinZoom({ columnCount: n });
      expect(limit).toBeLessThanOrEqual(MAX_ZOOM);
      expect(limit).toBeGreaterThanOrEqual(ABSOLUTE_MIN_ZOOM);
    }
  });

  it("非法入参不崩，退回绝对下限", () => {
    expect(computeMinZoom({ columnCount: 0 })).toBe(ABSOLUTE_MIN_ZOOM);
    expect(computeMinZoom({ columnCount: -3 })).toBe(ABSOLUTE_MIN_ZOOM);
    expect(computeMinZoom({ columnCount: NaN })).toBe(ABSOLUTE_MIN_ZOOM);
    expect(computeMinZoom({ columnCount: Infinity })).toBe(MAX_ZOOM);
  });
});

describe("requiredBoardWidth", () => {
  it("与 .board 的 min-width 公式同源", () => {
    for (const n of [1, 2, 3, 5, 8]) {
      expect(requiredBoardWidth(n)).toBe(n * MIN_COLUMN_WIDTH + (n - 1) * COLUMN_GAP);
    }
  });

  it("与 CSS 断点 threshold(N)=324N+335 口径一致（差值恒定）", () => {
    // 两个公式是对同一物理量的两种表达，差值恒为 23 即证明口径无漂移。
    const diffs = [1, 2, 3, 5, 8].map((n) => (324 * n + 335) - (requiredBoardWidth(n) + 300 + 36));
    expect(new Set(diffs).size).toBe(1);
    expect(diffs[0]).toBe(23);
  });

  it("非法入参按 1 列处理", () => {
    expect(requiredBoardWidth(0)).toBe(MIN_COLUMN_WIDTH);
    expect(requiredBoardWidth(NaN)).toBe(MIN_COLUMN_WIDTH);
  });
});

describe("readZoom", () => {
  it("相对基准 DPR 计算比例", () => {
    const original = window.devicePixelRatio;
    try {
      Object.defineProperty(window, "devicePixelRatio", { value: 2, configurable: true });
      expect(readZoom(1)).toBe(2);
      expect(readZoom(2)).toBe(1);
      expect(readZoom(4)).toBeCloseTo(0.5, 5);
    } finally {
      Object.defineProperty(window, "devicePixelRatio", { value: original, configurable: true });
    }
  });

  it("基准值非法时按 1 处理，不产生 NaN/Infinity", () => {
    const original = window.devicePixelRatio;
    try {
      Object.defineProperty(window, "devicePixelRatio", { value: 1, configurable: true });
      expect(readZoom(0)).toBe(1);
      expect(readZoom(-2)).toBe(1);
      expect(readZoom(NaN)).toBe(1);
    } finally {
      Object.defineProperty(window, "devicePixelRatio", { value: original, configurable: true });
    }
  });
});

describe("isAtOrBelowMinZoom", () => {
  it("含容差判定，吸收浮点与浏览器取整", () => {
    expect(isAtOrBelowMinZoom(0.7, 0.7)).toBe(true);
    expect(isAtOrBelowMinZoom(0.705, 0.7)).toBe(true);
    expect(isAtOrBelowMinZoom(0.8, 0.7)).toBe(false);
  });

  it("NaN 不误判为触底", () => {
    expect(isAtOrBelowMinZoom(NaN, 0.7)).toBe(false);
  });
});

describe("formatZoom", () => {
  it("格式化为百分比", () => {
    expect(formatZoom(1)).toBe("100%");
    expect(formatZoom(0.7)).toBe("70%");
    expect(formatZoom(0.85)).toBe("85%");
    expect(formatZoom(NaN)).toBe("100%");
  });
});
