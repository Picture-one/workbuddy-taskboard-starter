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

  const applyZoom = useCallback((target: number): boolean => {
    const normalized = Math.min(Math.max(target, 0.1), 5);
    const webFrame = getWebFrame();
    if (webFrame && typeof webFrame.setZoomFactor === "function") {
      try {
        webFrame.setZoomFactor(normalized);
        return true;
      } catch {
        /* 落到 body.style.zoom */
      }
    }
    if (typeof document !== "undefined" && document.body) {
      document.body.style.zoom = String(normalized);
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
      const current = readZoom(baselineDprRef.current);
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
    if (isAtOrBelowMinZoom(next, minZoom)) {
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
