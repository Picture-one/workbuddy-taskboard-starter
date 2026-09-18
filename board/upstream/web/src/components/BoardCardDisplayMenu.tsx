import { listenForOutsidePointerDown } from "../menuEvents";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import { createPortal } from "react-dom";
import { taskStatusLabel, useTaskboardI18n } from "../i18n";
import {
  MAIN_STATUSES,
  SECONDARY_STATUSES,
} from "../issueBoardStatuses";
import type { OtherTaskTab } from "../issueBoardStatuses";
import type { TaskStatus } from "../types";
import { LinearIcon } from "./LinearIcon";
import { DeleteIcon, StatusIcon } from "./SemanticIcons";

export type BoardStatusPlacement = "main" | "sidebar" | "hidden";

export interface BoardDisplaySettings {
  cover: boolean;
  body: boolean;
  mainStatuses: OtherTaskTab[];
  sidebarStatuses: OtherTaskTab[];
  hiddenStatuses: OtherTaskTab[];
  /** 列内按天分节（默认开启） */
  groupByDay: boolean;
  /** 看板顶部「今日推送」专区（默认开启） */
  showTodayPanel: boolean;
}

export const DEFAULT_BOARD_DISPLAY_SETTINGS: BoardDisplaySettings = {
  cover: true,
  body: false,
  mainStatuses: [...MAIN_STATUSES],
  sidebarStatuses: [...SECONDARY_STATUSES, "archived"],
  hiddenStatuses: [],
  groupByDay: true,
  showTodayPanel: true,
};

/** 看板上可能出现的全部列（含 `archived`），用于校验与补全。 */
const ALL_BOARD_STATUSES: OtherTaskTab[] = [...MAIN_STATUSES, ...SECONDARY_STATUSES, "archived"];

/** 某状态的「默认归属」，用于把孤立状态放回它本来该在的地方。 */
function defaultPlacementFor(status: OtherTaskTab): BoardStatusPlacement {
  if (DEFAULT_BOARD_DISPLAY_SETTINGS.mainStatuses.includes(status)) return "main";
  if (DEFAULT_BOARD_DISPLAY_SETTINGS.sidebarStatuses.includes(status)) return "sidebar";
  return "hidden";
}

/** 过滤非法值与重复项（保留首次出现的位置）。 */
function normalizeStatusList(value: unknown): OtherTaskTab[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<OtherTaskTab>();
  const result: OtherTaskTab[] = [];
  for (const item of value) {
    if (typeof item !== "string") continue;
    const status = item as OtherTaskTab;
    if (!ALL_BOARD_STATUSES.includes(status) || seen.has(status)) continue;
    seen.add(status);
    result.push(status);
  }
  return result;
}

/**
 * 向后兼容归一化 —— 读设置时**必须**走这里，不能裸强转。
 *
 * 为什么必须有它：`readProjectBoardDisplaySettings()`（`App.tsx:340-355`）只有**对象级**检查，
 * 老用户的 v3 JSON 里没有新字段 → 新 boolean 拿到 `undefined`。那**不会崩**，
 * 而是静默落到「关闭」，恰好与「默认开启」相反 —— 表现为「功能像没生效」，是最难查的一类问题。
 *
 * 顺手修掉两个既有隐患：
 * 1. **状态重复**：同一状态出现在多个分区 → 看板上该列渲染两次；
 * 2. **状态孤立**：不在 main/sidebar/hidden 任何一处 → 该列凭空消失（拖到一半关弹窗可能产生）。
 *    孤立状态放回默认位置，而不是一律塞进 main，以免凭空多出用户没要的列。
 */
export function normalizeBoardDisplaySettings(value: unknown): BoardDisplaySettings {
  const source = (value && typeof value === "object" && !Array.isArray(value) ? value : {}) as Record<string, unknown>;
  const mainStatuses = normalizeStatusList(source.mainStatuses);
  const sidebarStatuses = normalizeStatusList(source.sidebarStatuses)
    .filter((status) => !mainStatuses.includes(status));
  const hiddenStatuses = normalizeStatusList(source.hiddenStatuses)
    .filter((status) => !mainStatuses.includes(status) && !sidebarStatuses.includes(status));
  for (const status of ALL_BOARD_STATUSES) {
    if (mainStatuses.includes(status) || sidebarStatuses.includes(status) || hiddenStatuses.includes(status)) {
      continue;
    }
    const bucket = defaultPlacementFor(status);
    if (bucket === "main") mainStatuses.push(status);
    else if (bucket === "sidebar") sidebarStatuses.push(status);
    else hiddenStatuses.push(status);
  }
  return {
    cover: typeof source.cover === "boolean" ? source.cover : DEFAULT_BOARD_DISPLAY_SETTINGS.cover,
    body: typeof source.body === "boolean" ? source.body : DEFAULT_BOARD_DISPLAY_SETTINGS.body,
    mainStatuses,
    sidebarStatuses,
    hiddenStatuses,
    groupByDay: typeof source.groupByDay === "boolean"
      ? source.groupByDay
      : DEFAULT_BOARD_DISPLAY_SETTINGS.groupByDay,
    showTodayPanel: typeof source.showTodayPanel === "boolean"
      ? source.showTodayPanel
      : DEFAULT_BOARD_DISPLAY_SETTINGS.showTodayPanel,
  };
}

interface BoardCardDisplayMenuProps {
  settings: BoardDisplaySettings;
  onChange: (value: BoardDisplaySettings) => void;
  onReset: () => void;
}

export function BoardCardDisplayMenu({
  settings,
  onChange,
  onReset,
}: BoardCardDisplayMenuProps) {
  const { language, text } = useTaskboardI18n();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0, ready: false });
  const [draggedStatus, setDraggedStatus] = useState<OtherTaskTab | null>(null);
  const [draggedStatusHeight, setDraggedStatusHeight] = useState(0);
  const [dropTarget, setDropTarget] = useState<{
    placement: BoardStatusPlacement;
    beforeStatus: OtherTaskTab | null;
  } | null>(null);

  useLayoutEffect(() => {
    if (!menuOpen || !triggerRef.current || !menuRef.current) return;
    const trigger = triggerRef.current.getBoundingClientRect();
    const menu = menuRef.current.getBoundingClientRect();
    const left = Math.max(8, Math.min(trigger.right - menu.width, window.innerWidth - menu.width - 8));
    const top = trigger.bottom + 8 + menu.height <= window.innerHeight
      ? trigger.bottom + 8
      : Math.max(8, trigger.top - menu.height - 8);
    setPosition({ left, top, ready: true });
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return;
    const stopOutside = listenForOutsidePointerDown([menuRef, triggerRef], () => setMenuOpen(false));
    function closeFromEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      triggerRef.current?.focus();
    }
    document.addEventListener("keydown", closeFromEscape);
    return () => {
      stopOutside();
      document.removeEventListener("keydown", closeFromEscape);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!dialogOpen) return;
    closeRef.current?.focus();
    function closeFromEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setDialogOpen(false);
      triggerRef.current?.focus();
    }
    document.addEventListener("keydown", closeFromEscape);
    return () => document.removeEventListener("keydown", closeFromEscape);
  }, [dialogOpen]);

  function statusesFor(placement: BoardStatusPlacement) {
    if (placement === "main") return settings.mainStatuses;
    if (placement === "sidebar") return settings.sidebarStatuses;
    return settings.hiddenStatuses;
  }

  function moveStatus(
    status: OtherTaskTab,
    placement: BoardStatusPlacement,
    beforeStatus: OtherTaskTab | null,
  ) {
    const next = {
      main: settings.mainStatuses.filter((candidate) => candidate !== status),
      sidebar: settings.sidebarStatuses.filter((candidate) => candidate !== status),
      hidden: settings.hiddenStatuses.filter((candidate) => candidate !== status),
    };
    const target = next[placement];
    const beforeIndex = beforeStatus ? target.indexOf(beforeStatus) : -1;
    target.splice(beforeIndex >= 0 ? beforeIndex : target.length, 0, status);
    onChange({
      ...settings,
      mainStatuses: next.main,
      sidebarStatuses: next.sidebar,
      hiddenStatuses: next.hidden,
    });
  }

  function findDropBefore(container: HTMLElement, clientY: number): OtherTaskTab | null {
    const items = Array.from(container.querySelectorAll<HTMLElement>("[data-display-status]"))
      .filter((item) => item.dataset.displayStatus !== draggedStatus);
    return (items.find((item) => (
      clientY < item.getBoundingClientRect().top + item.offsetHeight / 2
    ))?.dataset.displayStatus as OtherTaskTab | undefined) ?? null;
  }

  function getStatusDragShift(status: OtherTaskTab, placement: BoardStatusPlacement) {
    if (!draggedStatus || status === draggedStatus) return 0;
    const statuses = statusesFor(placement);
    const remainingStatuses = statuses.filter((candidate) => candidate !== draggedStatus);
    const statusIndex = statuses.indexOf(status);
    const remainingIndex = remainingStatuses.indexOf(status);
    const draggedIndex = statuses.indexOf(draggedStatus);
    const beforeIndex = dropTarget?.placement === placement
      ? dropTarget.beforeStatus
        ? remainingStatuses.indexOf(dropTarget.beforeStatus)
        : remainingStatuses.length
      : -1;
    let shift = 0;
    const dragDistance = draggedStatusHeight + 8;

    if (draggedIndex >= 0 && statusIndex > draggedIndex) shift -= dragDistance;
    if (beforeIndex >= 0 && remainingIndex >= beforeIndex) shift += dragDistance;
    return shift;
  }

  function handleDrop(event: DragEvent<HTMLElement>, placement: BoardStatusPlacement) {
    event.preventDefault();
    const status = (
      event.dataTransfer.getData("application/x-taskboard-display-status")
      || event.dataTransfer.getData("text/plain")
    ) as OtherTaskTab;
    if (status) moveStatus(status, placement, findDropBefore(event.currentTarget, event.clientY));
    setDraggedStatus(null);
    setDraggedStatusHeight(0);
    setDropTarget(null);
  }

  function closeDialog() {
    setDialogOpen(false);
    triggerRef.current?.focus();
  }

  const menu = menuOpen ? createPortal(
    <div
      ref={menuRef}
      className="project-automation-menu board-display-menu no-drag"
      role="dialog"
      aria-label={text("显示设置", "Display settings")}
      style={{ left: position.left, top: position.top, visibility: position.ready ? "visible" : "hidden" }}
    >
      <div className="project-automation-menu-heading">
        <strong>{text("显示设置", "Display settings")}</strong>
      </div>
      <div className="project-automation-switch">
        <span>{text("封面", "Cover")}</span>
        <button
          type="button"
          className={"board-setting-switch" + (settings.cover ? " is-on" : "")}
          role="switch"
          aria-label={text("显示封面", "Show cover")}
          aria-checked={settings.cover}
          onClick={() => onChange({ ...settings, cover: !settings.cover })}
        >
          <span aria-hidden="true" />
        </button>
      </div>
      <div className="project-automation-switch">
        <span>{text("正文", "Body")}</span>
        <button
          type="button"
          className={"board-setting-switch" + (settings.body ? " is-on" : "")}
          role="switch"
          aria-label={text("显示正文", "Show body")}
          aria-checked={settings.body}
          onClick={() => onChange({ ...settings, body: !settings.body })}
        >
          <span aria-hidden="true" />
        </button>
      </div>
      <button
        className="display-settings-more"
        type="button"
        aria-haspopup="dialog"
        onClick={() => {
          setMenuOpen(false);
          setDialogOpen(true);
        }}
      >
        <span>{text("更多显示设置", "More display settings")}</span>
        <span aria-hidden="true">›</span>
      </button>
    </div>,
    document.body,
  ) : null;

  const dialog = dialogOpen ? createPortal(
    <div
      className="display-settings-backdrop no-drag"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) closeDialog();
      }}
    >
      <div
        className="display-settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="display-settings-title"
      >
        <header className="display-settings-header">
          <h2 id="display-settings-title">{text("更多显示设置", "More display settings")}</h2>
          <button
            ref={closeRef}
            className="icon-button display-settings-close"
            type="button"
            aria-label={text("关闭显示设置", "Close display settings")}
            onClick={closeDialog}
          >
            <LinearIcon name="close" />
          </button>
        </header>

        <div className="display-settings-toggles">
          <div className="project-automation-switch">
            <span>{text("按天分组", "Group by day")}</span>
            <button
              type="button"
              className={"board-setting-switch" + (settings.groupByDay ? " is-on" : "")}
              role="switch"
              aria-label={text("任务按天分组", "Group tasks by day")}
              aria-checked={settings.groupByDay}
              onClick={() => onChange({ ...settings, groupByDay: !settings.groupByDay })}
            >
              <span aria-hidden="true" />
            </button>
          </div>
          <div className="project-automation-switch">
            <span>{text("今日推送专区", "Today's push panel")}</span>
            <button
              type="button"
              className={"board-setting-switch" + (settings.showTodayPanel ? " is-on" : "")}
              role="switch"
              aria-label={text("显示今日推送专区", "Show today's push panel")}
              aria-checked={settings.showTodayPanel}
              onClick={() => onChange({ ...settings, showTodayPanel: !settings.showTodayPanel })}
            >
              <span aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="display-settings-columns">
          {([
            ["main", text("正常显示", "Main board")],
            ["sidebar", text("侧边栏显示", "Sidebar")],
            ["hidden", text("隐藏", "Hidden")],
          ] as const).map(([placement, label]) => (
            <section
              className={"display-settings-column" + (
                dropTarget?.placement === placement ? " is-drop-target" : ""
              )}
              aria-label={label}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                setDropTarget({
                  placement,
                  beforeStatus: findDropBefore(event.currentTarget, event.clientY),
                });
              }}
              onDragLeave={(event) => {
                if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
                  setDropTarget(null);
                }
              }}
              onDrop={(event) => handleDrop(event, placement)}
              key={placement}
            >
              <h3>{label}</h3>
              <div className="display-settings-status-list">
                {statusesFor(placement).map((status) => {
                  const dragShift = getStatusDragShift(status, placement);
                  return (
                    <div
                      className={`display-settings-status-item status-${status}` + (
                        draggedStatus === status ? " is-dragging" : ""
                      ) + (dragShift ? " is-drag-shifted" : "") + (
                        dropTarget?.placement === placement && dropTarget.beforeStatus === status
                          ? " is-drop-before"
                          : ""
                      )}
                      style={dragShift ? { transform: `translate3d(0, ${dragShift}px, 0)` } : undefined}
                      draggable
                      data-display-status={status}
                      onDragStart={(event) => {
                        event.dataTransfer.effectAllowed = "move";
                        event.dataTransfer.setData("text/plain", status);
                        event.dataTransfer.setData("application/x-taskboard-display-status", status);
                        setDraggedStatus(status);
                        setDraggedStatusHeight(event.currentTarget.offsetHeight);
                      }}
                      onDragEnd={() => {
                        setDraggedStatus(null);
                        setDraggedStatusHeight(0);
                        setDropTarget(null);
                      }}
                      key={status}
                    >
                      {status === "archived"
                        ? <DeleteIcon color="var(--display-status-color)" size={15} />
                        : <StatusIcon status={status as TaskStatus} color="var(--display-status-color)" size={15} />}
                      <span>{status === "archived"
                        ? text("已归档", "Archived")
                        : status === "blocked"
                          ? text("遇到阻碍（默认隐藏）", "Blocked (hidden by default)")
                        : taskStatusLabel(language, status)}</span>
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>

        <footer className="display-settings-footer">
          <button className="button secondary" type="button" onClick={onReset}>
            {text("重置为默认", "Reset to default")}
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  ) : null;

  return (
    <>
      <button
        ref={triggerRef}
        className={"task-filter-trigger board-card-display-trigger" + (
          menuOpen || dialogOpen ? " is-open" : ""
        )}
        type="button"
        aria-label={text("显示设置", "Display settings")}
        aria-haspopup="dialog"
        aria-expanded={menuOpen}
        title={text("显示设置", "Display settings")}
        onClick={() => {
          if (!menuOpen) setPosition({ left: 0, top: 0, ready: false });
          setMenuOpen((current) => !current);
        }}
      >
        <LinearIcon name="displayOptions" />
      </button>
      {menu}
      {dialog}
    </>
  );
}
