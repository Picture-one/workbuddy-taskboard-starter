/**
 * 浏览器缩放的越界纠偏。
 *
 * ## 背景
 *
 * 网页**无法编程式阻止**浏览器缩放：`Ctrl+滚轮` / `Ctrl+加减号` 由宿主（WorkBuddy
 * 的 webview / Chrome）直接处理，不经过页面 JS，也不可被 `preventDefault()` 拦下。
 * 因此「限制最小尺度」只能做成**越界即纠偏 + 明确提示**：
 * 轮询读回真实缩放，一旦低于当前板块数要求的下限，就把缩放调回下限。
 *
 * ## 纠偏手段
 *
 * 1. 优先用 `webFrame.setZoomFactor`（Electron / WorkBuddy 宿主注入的接口）；
 * 2. 否则用 `document.body.style.zoom` 做页面级缩放（Chromium 支持，
 *    且会被后续 `devicePixelRatio` 变化反映出来）。
 *
 * 两条路都拿不到时**不静默失败**：仅提示，让用户自己按 `Ctrl+0`。
 * 静默失败比不提示更糟 —— 用户会以为功能坏了。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ABSOLUTE_MIN_ZOOM,
  APP_ZOOM_LAYOUT_VAR,
  bodyZoomDeclaration,
  computeMinZoom,
  formatZoom,
  isAtOrBelowMinZoom,
  readZoom,
} from "./boardZoom";

interface WebFrameLike {
  setZoomFactor?: (factor: number) => void;
  getZoomFactor?: () => number;
}

function getWebFrame(): WebFrameLike | null {
  const candidate = (window as unknown as { webFrame?: WebFrameLike }).webFrame;
  return candidate && typeof candidate === "object" ? candidate : null;
}

export interface BoardZoomGuard {
  /** 当前缩放比例（1 = 100%）。浏览器不支持读取时为 1。 */
  zoom: number;
  /** 当前板块数对应的缩放下限。 */
  minZoom: number;
  /** 是否已触及下限（UI 可据此禁用「缩小」按钮）。 */
  atMinZoom: boolean;
  /** 是否已因越界被纠偏（用于展示一次性提示）。 */
  clamped: boolean;
  /** 主动缩小一档；已触底时返回 false 且不做任何事。 */
  zoomOut: () => boolean;
  /** 主动放大一档。 */
  zoomIn: () => boolean;
  /** 复位到 100%。 */
  resetZoom: () => void;
}

/** 浏览器缩放的步进（Chrome 为 10%）。 */
const ZOOM_STEP = 0.1;

/**
 * 浮点与浏览器取整的容差。
 * 与 `boardZoom.ts` 的 `isAtOrBelowMinZoom` 默认容差（0.01）保持同一量级，
 * 用于判断「下限是否真的比当前值更小」，避免把 0.85 与 0.85 误判成需要吸附。
 */
const ZOOM_TOLERANCE = 0.001;

/**
 * 监听并约束浏览器缩放。
 *
 * @param columnCount 当前显示的板块数（含「今日推送」列）。
 * @param enabled     非看板视图时传 false，避免在列表/甘特图里也施加限制。
 */
export function useBoardZoomGuard(columnCount: number, enabled: boolean): BoardZoomGuard {
  const minZoom = computeMinZoom({ columnCount });
  // 基准 DPR 只在首次挂载时取一次：devicePixelRatio 叠加了系统 DPI 缩放，
  // 不设基准会把「系统 125%」误当成「用户缩放到 125%」。
  const baselineDprRef = useRef<number>(
    typeof window === "undefined" ? 1 : (window.devicePixelRatio || 1),
  );
  const [zoom, setZoom] = useState(1);
  const [clamped, setClamped] = useState(false);
  const clampCountRef = useRef(0);

  /**
   * 走 body 路径时**实际落到 body 上的**缩放值（1 = 没施加）。
   *
   * 为什么必须自己记一份：`document.body.style.zoom` **不会**改变 `devicePixelRatio`，
   * 所以 `readZoom()` 读回来恒为 1。2026-09-18 的「控件显示 100%、body 实际 90%」
   * 正是这个脱钩 —— resize 时 `sync()` 用 `readZoom()` 把 state 重置成 1，
   * 于是「放大」因 `next > 1.0001` 被拒而彻底失效，残留的内联 zoom 与底侧空白带
   * 一直卡到刷新。
   *
   * 有效缩放 = 浏览器自身缩放（`readZoom`）× 我们施加的 body 缩放。
   */
  const bodyZoomRef = useRef(1);

  const applyZoom = useCallback((target: number): boolean => {
    const normalized = Math.min(Math.max(target, 0.1), 5);
    const webFrame = getWebFrame();
    if (webFrame && typeof webFrame.setZoomFactor === "function") {
      try {
        webFrame.setZoomFactor(normalized);
        // 这条路会真实改变 devicePixelRatio，`readZoom()` 自己就能读回来，
        // 因此**不要**记进 bodyZoomRef —— 否则有效缩放会被算成平方。
        return true;
      } catch {
        /* 落到 body.style.zoom */
      }
    }
    if (typeof document !== "undefined" && document.body) {
      const decl = bodyZoomDeclaration(normalized);
      if (decl.zoom === null) {
        // 100% 时必须**移除**而不是写 "1"：留下内联属性的话，
        // 任何一次没清理干净的写入都会让空白带复活，且刷新前看不出原因。
        document.body.style.removeProperty("zoom");
      } else {
        document.body.style.zoom = decl.zoom;
      }
      if (decl.layoutVar === null) {
        document.documentElement.style.removeProperty(APP_ZOOM_LAYOUT_VAR);
      } else {
        document.documentElement.style.setProperty(APP_ZOOM_LAYOUT_VAR, decl.layoutVar);
      }
      bodyZoomRef.current = decl.zoom === null ? 1 : Number(decl.zoom);
      return true;
    }
    return false;
  }, []);

  // 读回真实缩放。用 rAF 节流：resize 会在一帧内连续触发多次。
  useEffect(() => {
    if (!enabled || typeof window === "undefined") return;
    let frame = 0;
    const sync = () => {
      frame = 0;
      // 有效缩放 = 浏览器自身缩放 × 我们施加在 body 上的缩放。
      // 只取 `readZoom()` 会漏掉 body 路径（body 缩放不改 devicePixelRatio），
      // 结果是每次 resize 都把 state 冲回 100%、与 body 上的真实值脱钩。
      const raw = readZoom(baselineDprRef.current) * bodyZoomRef.current;
      const current = Math.min(Math.max(raw, 0.1), 5);
      setZoom(current);
      if (current < minZoom - 0.001) {
        // 越界：纠偏回下限。能用哪条路就用哪条，都不可用则只提示。
        applyZoom(minZoom);
        clampCountRef.current += 1;
        setClamped(true);
        setZoom(minZoom);
      }
    };
    const schedule = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(sync);
    };
    sync();
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("resize", schedule);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [applyZoom, enabled, minZoom]);

  const zoomOut = useCallback(() => {
    if (!enabled) return false;
    const next = Math.round((zoom - ZOOM_STEP) * 100) / 100;

    // 步进（0.10）与下限阶梯（0.05 的倍数）不是整数倍关系，因此会「跨过」下限：
    // 例如 minZoom=0.85 时从 0.90 缩小，next=0.80 已越界被拒，但当前 zoom=0.90
    // 仍高于下限 → atMinZoom=false → 按钮永不禁用，用户点了没反应也没提示。
    //
    // 正确行为：跨过时**吸附到下限**（若下限确实比当前值更小），
    // 这样「缩小」总能走到真实的边界；只有已经在下限时才拒绝并标 clamped。
    if (isAtOrBelowMinZoom(next, minZoom)) {
      if (minZoom < zoom - ZOOM_TOLERANCE) {
        applyZoom(minZoom);
        setZoom(minZoom);
        setClamped(true);
        return true;
      }
      setClamped(true);
      return false;
    }
    applyZoom(next);
    setZoom(next);
    return true;
  }, [applyZoom, enabled, minZoom, zoom]);

  const zoomIn = useCallback(() => {
    if (!enabled) return false;
    const next = Math.round((zoom + ZOOM_STEP) * 100) / 100;
    if (next > 1.0001) return false;
    applyZoom(next);
    setZoom(next);
    setClamped(false);
    return true;
  }, [applyZoom, enabled, zoom]);

  const resetZoom = useCallback(() => {
    if (!enabled) return;
    applyZoom(1);
    setZoom(1);
    setClamped(false);
  }, [applyZoom, enabled]);

  return {
    zoom,
    minZoom,
    atMinZoom: isAtOrBelowMinZoom(zoom, minZoom) || zoom <= ABSOLUTE_MIN_ZOOM,
    clamped,
    zoomOut,
    zoomIn,
    resetZoom,
  };
}

export { formatZoom };
