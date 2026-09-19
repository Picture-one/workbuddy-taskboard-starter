/**
 * 浏览器缩放的最小尺度守卫。
 *
 * ## 需要先厘清一件事
 *
 * 浏览器缩放（Ctrl+滚轮 / Ctrl+加减号）改变的是「1 个 CSS 像素占多少屏幕物理像素」。
 * 设物理宽度为 `W`、缩放为 `z`，则可用 CSS 宽度是 `W / z`：
 *
 * - **放大（z 变大）** → 可用 CSS 宽度**变小** → N 个板块放不下 → 出现横向滚动，
 *   右侧板块被推到视野外（用户说的「放大后遮挡右侧面板」）。
 * - **缩小（z 变小）** → 可用 CSS 宽度**变大** → 一定能放下，且会空出更多宽度。
 *
 * 所以「缩到某档就看全不了」在**宽度**方向上不成立。缩小真正的害处是
 * **字号小到不可读**（用户原话「字体持续缩小」）。
 *
 * ## 因此下限的语义
 *
 * 下限 = **可读性门槛**：保证最小板块宽度下的字号不低于可读阈值。
 * 它随「需要显示的板块数」变化 —— 板块越多，你越需要靠放大来让每列可读，
 * 于是允许的下限越**高**（越不允许继续缩小）。这与用户的要求
 * 「最小尺度以能够完整展示所需显示的模块为标准」一致：
 * 板块越多，要求的尺度越大。
 *
 * ## 为什么不用 CSS
 *
 * `@container` / `@media` 断点是被动的：它们能在窄容器下改布局，
 * 但拦不住用户把页面缩到 50%。必须读回真实缩放并在越界时纠偏。
 */

/** 板块的最小可读宽度（CSS px）。与 `.board` 的 `minmax(300px, 1fr)` 同源。 */
export const MIN_COLUMN_WIDTH = 300;

/** 板块间距（CSS px）。与 `.board` 的 `gap` 同源。 */
export const COLUMN_GAP = 24;

/**
 * 绝对下限：任何情况下都不允许缩到 75% 以下。
 * 低于此值时 12px 的正文字号只剩 9px 以下，肉眼已难辨认。
 *
 * 2026-09-18：由 0.70 上调至 0.75。原因是正文字号从 11px 抬到 12px 后，
 * 「可读性门槛」对应的缩放也必须同步抬高 —— 二者是同一个约束的两面：
 * 改动字号却不动下限，等于把之前修好的问题重新放回来。
 * 详见 boardZoom.test.tsx 的「字号 × 缩放下限联动」断言。
 */
export const ABSOLUTE_MIN_ZOOM = 0.75;

/** 缩放上限（=100%）。保留放大余量，绝不阻止用户放大。 */
export const MAX_ZOOM = 1;

/**
 * 「可读性基准」：在**这几个**板块数以内，允许缩到 ABSOLUTE_MIN_ZOOM。
 * 超过之后，每多一个板块就把下限抬高一级。
 *
 * 取 4 是因为 4 列在 1920 屏上仍有 ~460px/列，是最舒服的密度；
 * 5 列起就必须靠放大换取可读性。
 */
export const COMFORTABLE_COLUMN_COUNT = 4;

/** 每超出 1 个板块，下限抬高多少（缩放比例）。 */
const ZOOM_STEP_PER_EXTRA_COLUMN = 0.05;

export interface ZoomLimitInput {
  /** 当前显示的板块总数（含「今日推送」列）。 */
  columnCount: number;
}

/**
 * 计算当前板块数对应的最小缩放比例（1 = 100%）。
 *
 * 单调不减：板块数增加时返回值只会变大或持平，绝不会变小 ——
 * 「显示的模块越多，要求的尺度越大」。
 */
export function computeMinZoom({ columnCount }: ZoomLimitInput): number {
  // Infinity 要单独拦：`Number.isFinite(Infinity)` 为 false，若直接落到
  // 「按 1 列处理」的兜底，会得出「板块无穷多却允许缩到最小」的反直觉结果。
  const columns = columnCount === Infinity
    ? Number.MAX_SAFE_INTEGER
    : Math.max(1, Number.isFinite(columnCount) ? Math.floor(columnCount) : 1);

  const extra = Math.max(0, columns - COMFORTABLE_COLUMN_COUNT);
  const limit = ABSOLUTE_MIN_ZOOM + extra * ZOOM_STEP_PER_EXTRA_COLUMN;

  // 上限 100%：板块极多时最多要求用户保持 100%，绝不要求放大（那会反过来遮挡）。
  return Math.min(Math.max(limit, ABSOLUTE_MIN_ZOOM), MAX_ZOOM);
}

/**
 * 当前板块数在**不出现横向滚动**的前提下所需的 CSS 宽度。
 * 与 `App.tsx` 的 `mainBoardMinWidth` 同源，供提示文案与断言复用。
 */
export function requiredBoardWidth(columnCount: number): number {
  const n = Number.isFinite(columnCount) ? Math.max(1, Math.floor(columnCount)) : 1;
  return n * MIN_COLUMN_WIDTH + (n - 1) * COLUMN_GAP;
}

/**
 * 读取当前页面的缩放比例。
 *
 * 浏览器缩放会同时改变 `devicePixelRatio` 与布局视口宽度，而
 * `devicePixelRatio` 还叠加了系统 DPI 缩放 —— 因此必须以**首次加载时**的
 * `devicePixelRatio` 为基准算相对缩放，否则在 125%/150% 系统缩放的机器上
 * 会把「系统 DPI」误判成「用户缩放」。
 */
export function readZoom(baselineDpr: number): number {
  const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
  const base = baselineDpr > 0 && Number.isFinite(baselineDpr) ? baselineDpr : 1;
  const ratio = dpr / base;
  if (!Number.isFinite(ratio) || ratio <= 0) return 1;
  return Math.min(Math.max(ratio, 0.1), 5);
}

/**
 * 判断给定缩放是否已触及下限。
 * `tolerance` 用于吸收浮点误差与浏览器自身的取整（Chrome 按 10% 步进）。
 */
export function isAtOrBelowMinZoom(currentZoom: number, minZoom: number, tolerance = 0.01): boolean {
  if (!Number.isFinite(currentZoom)) return false;
  return currentZoom <= minZoom + tolerance;
}

/** 供 UI 展示：`0.75` → `"75%"`。 */
export function formatZoom(zoom: number): string {
  const pct = Math.round((Number.isFinite(zoom) ? zoom : 1) * 100);
  return `${pct}%`;
}

/**
 * body 路径缩放时写入 `<html>` 的布局补偿变量名。
 *
 * ## 为什么需要补偿（2026-09-18 底部空白带的根因）
 *
 * `document.body.style.zoom = z` 会把**整个 body 的渲染尺寸**乘以 z，
 * 但 CSS 视口不会跟着变 —— 于是 `height: 100vh` 的 `.app-shell` 渲染后只有
 * `z × 100vh`，底部留下 `(1−z)×视口高` 的空白带（右侧同理）。
 * 更糟的是方向反了：缩放的本意是「缩小内容、容纳更多板块」，
 * 而 shell 变窄反而让可用 CSS 宽度**变小**。
 *
 * 补偿：shell 的布局尺寸改为 `100vh × (1/z)`，渲染后 = z × 100vh/z = 恰好铺满视口。
 * 布局可用 CSS 尺寸变为 `视口/z` —— 与浏览器缩小的语义完全一致。
 *
 * 用「乘以 1/z」而不是「除以 z」：CSS calc 对变量做除法的兼容性更弱，乘法无歧义。
 */
export const APP_ZOOM_LAYOUT_VAR = "--app-zoom-layout";

/**
 * 计算body 路径缩放要写的两个值：body 的内联 zoom 与补偿变量。
 * 返回 `null` 表示「移除该属性」（复位到 100% 时必须移除而不是写 "1"，
 * 否则会留下永远清不掉的内联痕迹 —— 2026-09-18 的残留空白带正源于此）。
 */
export function bodyZoomDeclaration(target: number): {
  zoom: string | null;
  layoutVar: string | null;
} {
  if (!Number.isFinite(target) || target >= 0.9995) {
    return { zoom: null, layoutVar: null };
  }
  const clamped = Math.min(Math.max(target, 0.1), 5);
  return { zoom: String(clamped), layoutVar: String(1 / clamped) };
}
