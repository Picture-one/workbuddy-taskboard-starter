import { describe, expect, it } from "vitest";
import {
  ABSOLUTE_MIN_ZOOM,
  APP_ZOOM_LAYOUT_VAR,
  COLUMN_GAP,
  COMFORTABLE_COLUMN_COUNT,
  MAX_ZOOM,
  MIN_COLUMN_WIDTH,
  bodyZoomDeclaration,
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
    expect(formatZoom(0.75)).toBe("75%");
    expect(formatZoom(0.85)).toBe("85%");
    expect(formatZoom(NaN)).toBe("100%");
  });
});

/**
 * 字号 × 缩放下限联动守卫。
 *
 * 背景：下限的语义是「可读性门槛」，它由正文号与最小可读字号共同决定：
 *     下限 ≥ 最小可读字号 / 正文字号
 * 因此**抬高正文字号却不抬高下限**，等于把「缩到最小时看不清」的问题
 * 重新放回来。这组断言把两者的耦合固化下来，防止将来单边改动。
 */
describe("字号 × 缩放下限联动", () => {
  /** 与 styles.css 的 .task-card-description font-size 同源（单位 px）。 */
  const BODY_FONT_PX = 12;

  /** 最小可读字号：低于此值时中文正文在常规显示器上已难辨认。 */
  const MIN_READABLE_PX = 9;

  it("正文字号与 styles.css 保持一致（改字号必须同步改这里）", () => {
    // 这是一个「契约锚点」：若样式表字号变了而这里没变，说明有人漏改了联动。
    expect(BODY_FONT_PX).toBe(12);
  });

  it("绝对下限保证最小板块字号不低于可读阈值", () => {
    const renderedPx = BODY_FONT_PX * ABSOLUTE_MIN_ZOOM;
    expect(renderedPx).toBeGreaterThanOrEqual(MIN_READABLE_PX);
  });

  it("绝对下限恰好等于「可读阈值 / 正文字号」（无多余余量，也不欠缺）", () => {
    expect(ABSOLUTE_MIN_ZOOM).toBeCloseTo(MIN_READABLE_PX / BODY_FONT_PX, 2);
  });

  it("任何板块数下，最小板块字号都不低于可读阈值", () => {
    for (const n of [1, 2, 3, 4, 5, 6, 7, 8, 9]) {
      const renderedPx = BODY_FONT_PX * computeMinZoom({ columnCount: n });
      expect(renderedPx).toBeGreaterThanOrEqual(MIN_READABLE_PX);
    }
  });

  it("各板块数对应的下限符合既定阶梯 75/75/75/75/80/85/90/95/100", () => {
    const expected = [0.75, 0.75, 0.75, 0.75, 0.8, 0.85, 0.9, 0.95, 1];
    const actual = [1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => computeMinZoom({ columnCount: n }));
    expect(actual).toEqual(expected);
  });
});

/**
 * 步进 × 下限的「跨过」问题（2026-09-18 实测发现）。
 *
 * `zoomOut` 的步进是 0.10（Chrome 一档），而下限阶梯是 0.05 的倍数。
 * 两者不是整数倍关系 → 缩小会**跨过**下限：例如 minZoom=0.85、当前 0.90，
 * 下一档 next=0.80 已越界，若直接拒绝，则当前 zoom 仍 > 下限 →
 * `atMinZoom=false` → 缩小键永不禁用，用户点击无反应也无提示。
 *
 * 修复：跨过时吸附到下限。本组断言把该行为固化。
 */
describe("缩小步进与下限的跨过", () => {
  const ZOOM_STEP = 0.1;
  const TOL = 0.001;

  /** 复刻修复后的 zoomOut 决策：返回 {zoom, acted}。 */
  function zoomOutOnce(zoom: number, minZoom: number) {
    const next = Math.round((zoom - ZOOM_STEP) * 100) / 100;
    if (isAtOrBelowMinZoom(next, minZoom)) {
      if (minZoom < zoom - TOL) return { zoom: minZoom, acted: true };
      return { zoom, acted: false };
    }
    return { zoom: next, acted: true };
  }

  it("从 100% 一路缩小，每一档都必须能真正到达下限（不允许卡在半路）", () => {
    for (const n of [1, 2, 3, 4, 5, 6, 7, 8, 9]) {
      const min = computeMinZoom({ columnCount: n });
      let z = 1;
      let guard = 0;
      // 反复缩小直到不再变化
      for (;;) {
        const r = zoomOutOnce(z, min);
        if (!r.acted) break;
        z = r.zoom;
        expect(++guard).toBeLessThan(30);
      }
      // 最终必须停在下限，而不是下限之上
      expect(z).toBeCloseTo(min, 5);
      // 且此时 atMinZoom 为真 → 按钮会被禁用
      expect(isAtOrBelowMinZoom(z, min)).toBe(true);
    }
  });

  it("跨过场景：minZoom=0.85 时从 0.90 缩小应吸附到 0.85，而非原地不动", () => {
    const r = zoomOutOnce(0.9, 0.85);
    expect(r.acted).toBe(true);
    expect(r.zoom).toBeCloseTo(0.85, 5);
  });

  it("已在下限时再缩小：不改变 zoom，且返回未执行（用于标 clamped）", () => {
    const r = zoomOutOnce(0.85, 0.85);
    expect(r.acted).toBe(false);
    expect(r.zoom).toBeCloseTo(0.85, 5);
  });

  it("复位到 100% 后，仍允许一路重新缩小到下限（不残留 clamped 死锁）", () => {
    for (const n of [1, 4, 6, 9]) {
      const min = computeMinZoom({ columnCount: n });
      let z = 1;
      for (;;) {
        const r = zoomOutOnce(z, min);
        if (!r.acted) break;
        z = r.zoom;
      }
      expect(z).toBeCloseTo(min, 5);
      // 复位后重新走一遍，必须得到完全相同的结果（不依赖残留状态）
      let z2 = 1;
      for (;;) {
        const r = zoomOutOnce(z2, min);
        if (!r.acted) break;
        z2 = r.zoom;
      }
      expect(z2).toBeCloseTo(z, 5);
    }
  });

  it("不会因为吸附而反向放大：吸附目标必须严格小于当前值", () => {
    for (const n of [1, 2, 3, 4, 5, 6, 7, 8, 9]) {
      const min = computeMinZoom({ columnCount: n });
      let z = 1;
      let prev = 1;
      for (;;) {
        const r = zoomOutOnce(z, min);
        if (!r.acted) break;
        expect(r.zoom).toBeLessThanOrEqual(prev + 1e-9);
        prev = r.zoom;
        z = r.zoom;
      }
      expect(z).toBeGreaterThanOrEqual(min - 1e-9);
    }
  });
});

/**
 * body 缩放的布局补偿（2026-09-18 底侧空白带）。
 *
 * 用户现象：底侧出现一条多余空白 —— 刷新后消失，直接放大窗口不出现，
 * 但「缩到最小再放大」在刷新前一直存在。
 *
 * 根因是两层叠加：
 *   1. `document.body.style.zoom = z` 会把 body 的渲染尺寸乘 z，而 CSS 视口不变，
 *      于是 `height: 100%` / `100vh` 的外壳渲染出来只有 `z × 视口` → 底部空出
 *      `(1−z)×视口`（与右侧同理）。
 *   2. 该内联 zoom 刷新才清，resize 不碰它 →「刷新后消失、缩到最小再放大仍在」。
 *
 * 补偿：给布局尺寸乘 `1/z`，渲染回来正好等于视口。
 */
describe("bodyZoomDeclaration", () => {
  it("100% 时返回 null —— 必须移除属性，而不是写 \"1\"", () => {
    // 写 "1" 会留下清不掉的内联痕迹：残留 zoom 让空白带复活，且刷新前看不出原因。
    expect(bodyZoomDeclaration(1).zoom).toBeNull();
    expect(bodyZoomDeclaration(1).layoutVar).toBeNull();
  });

  it("足够接近 100%（浮点误差内）也按复位处理", () => {
    expect(bodyZoomDeclaration(0.9999).zoom).toBeNull();
    expect(bodyZoomDeclaration(1.0001).zoom).toBeNull();
    // 而真正缩小时必须给出值
    expect(bodyZoomDeclaration(0.99).zoom).not.toBeNull();
  });

  it("缩小时给出 1/z 的补偿值（渲染后恰好铺满视口）", () => {
    for (const z of [0.9, 0.85, 0.8, 0.75]) {
      const { zoom, layoutVar } = bodyZoomDeclaration(z);
      expect(Number(zoom)).toBeCloseTo(z, 6);
      expect(Number(layoutVar)).toBeCloseTo(1 / z, 6);
      // 核心不变量：布局尺寸 × zoom = 视口尺寸
      expect(Number(zoom) * Number(layoutVar)).toBeCloseTo(1, 6);
    }
  });

  it("补偿后可用 CSS 尺寸随缩小而变大（语义与浏览器缩放一致）", () => {
    // 设计本意是「缩小内容以容纳更多板块」。不补偿时 body zoom 反而让可用宽度
    // 变小（方向相反）；补偿后 视口/z 才是正确方向。
    const viewport = 1600;
    const layoutAt90 = viewport * Number(bodyZoomDeclaration(0.9).layoutVar);
    const layoutAt75 = viewport * Number(bodyZoomDeclaration(0.75).layoutVar);
    expect(layoutAt90).toBeGreaterThan(viewport);
    expect(layoutAt75).toBeGreaterThan(layoutAt90);
  });

  it("下限档位也都给出合法补偿（覆盖实际会用到的所有档）", () => {
    for (const n of [1, 2, 3, 4, 5, 6, 7, 8, 9]) {
      const min = computeMinZoom({ columnCount: n });
      const { zoom, layoutVar } = bodyZoomDeclaration(min);
      if (min >= 0.9995) {
        expect(zoom).toBeNull();
        continue;
      }
      expect(Number(zoom)).toBeCloseTo(min, 6);
      expect(Number(zoom) * Number(layoutVar)).toBeCloseTo(1, 6);
    }
  });

  it("越界入参被钳制，不产生 NaN / Infinity / 除以零", () => {
    for (const bad of [NaN, Infinity, -Infinity, 0, -1]) {
      const { zoom, layoutVar } = bodyZoomDeclaration(bad);
      if (zoom === null) continue;
      const z = Number(zoom);
      const v = Number(layoutVar);
      expect(Number.isFinite(z)).toBe(true);
      expect(Number.isFinite(v)).toBe(true);
      expect(z).toBeGreaterThan(0);
      expect(v).toBeGreaterThan(0);
    }
    // NaN 走复位分支
    expect(bodyZoomDeclaration(NaN).zoom).toBeNull();
    // 极小值被钳到 0.1，补偿值 10 仍有限
    expect(Number(bodyZoomDeclaration(0.0001).zoom)).toBeCloseTo(0.1, 6);
    expect(Number(bodyZoomDeclaration(0.0001).layoutVar)).toBeCloseTo(10, 6);
  });

  it("变量名与 styles.css 的补偿项同名（改一处必须改另一处）", () => {
    // 契约锚点：CSS 里写的是 `var(--app-zoom-layout, 1)`。
    expect(APP_ZOOM_LAYOUT_VAR).toBe("--app-zoom-layout");
  });
});
