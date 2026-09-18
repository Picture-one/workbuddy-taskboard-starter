import { useState } from "react";
import type { ActorIdentity, Task, TaskDraft } from "../types";
import { useTaskboardI18n } from "../i18n";
import { dailyCardTasksForDay, localDay } from "../boardGrouping";
import type { TaskCardPresentation, TaskConversationItem } from "../taskConversations";
import { TaskCard } from "./TaskCard";
import { LinearIcon } from "./LinearIcon";

/**
 * 看板内的「今日推送」板块。
 *
 * 展示形态：**看板里的一个普通列**，与其它板块完全一致 —— 共用
 * `.board-column` / `.column-header` / `.column-heading` / `.column-list` 四件套，
 * 因此宽度参与 .board 的等分网格、表头样式、卡片样式、滚动行为都与状态列相同。
 * 唯一的视觉差异是配色用强调色（`--accent`）而非某个任务状态色。
 *
 * 设计约束：
 * - **只放每日推送卡片**：判据是静态标签 `daily-card*`，普通任务不混入。
 * - **只读、不进拖拽体系**：根节点用 `onDragStartCapture` 取消整棵子树的 `dragstart`。
 *   `TaskCard` 自带 `draggable={!isMoving}`，若只把 `onDragStart` 传成空函数，
 *   浏览器仍会发起原生拖拽（只是落地时 `dataTransfer` 为空）。在捕获阶段 `preventDefault()`
 *   会直接取消这次拖拽（规范行为），从而无需改动 `TaskCard` 这个被复用的组件。
 *   本列也因此不注册 onDragOver/onDrop —— 它不是任何任务的落点。
 * - 卡片用**默认 variant（main）**，与状态列一致；不用 sidebar variant，
 *   否则会多出 `.sidebar-card-creator` 底栏、少掉内联参与者，风格就不统一了。
 * - **折叠态只存组件内 state**，刻意不新增 storage key —— 否则又多一处需要向后兼容归一化的配置。
 *   折叠按钮复用其它板块 `.column-actions` 里的图标按钮位，视觉语言一致。
 */
interface TodayPushPanelProps {
  tasks: Task[];
  presentations: Record<string, TaskCardPresentation>;
  /** 可注入，便于测试固定「今天」。 */
  todayKey?: string;
  contextMenuTaskId: string | null;
  availableLabels: string[];
  projectNames?: Record<string, string>;
  currentUser: ActorIdentity;
  showCover: boolean;
  showBody: boolean;
  onCreateLabel: (label: string, projectId?: string) => Promise<void>;
  onEdit: (task: Task) => void;
  onUpdate: (task: Task, changes: Partial<TaskDraft>) => Promise<Task>;
  onContextMenu: (task: Task, position: { x: number; y: number }) => void;
  onOpenConversation: (conversation: TaskConversationItem) => void;
}

export function TodayPushPanel({
  tasks,
  presentations,
  todayKey,
  contextMenuTaskId,
  availableLabels,
  projectNames,
  currentUser,
  showCover,
  showBody,
  onCreateLabel,
  onEdit,
  onUpdate,
  onContextMenu,
  onOpenConversation,
}: TodayPushPanelProps) {
  const { text } = useTaskboardI18n();
  const [collapsed, setCollapsed] = useState(false);
  const dayKey = todayKey ?? localDay(new Date());
  const cards = dailyCardTasksForDay(tasks, dayKey);
  const label = text("今日推送", "Today's push");

  return (
    <section
      className={"board-column today-push-column" + (collapsed ? " is-collapsed" : "")}
      aria-labelledby="column-today-push"
      data-today-key={dayKey}
      // 捕获阶段取消整棵子树的 dragstart → 本板块的卡片不参与拖拽
      onDragStartCapture={(event) => event.preventDefault()}
    >
      <header className="column-header">
        <div className="column-heading">
          <span className="column-status-icon">
            <LinearIcon name="panel" style={{ width: "14px", height: "14px" }} />
          </span>
          <h2 id="column-today-push">
            {label}{cards.length > 0 ? ` ${cards.length}` : ""}
          </h2>
        </div>
        <div className="column-actions">
          <button
            type="button"
            className="icon-button today-push-collapse"
            aria-expanded={!collapsed}
            aria-label={collapsed
              ? text("展开今日推送", "Expand today's push")
              : text("折叠今日推送", "Collapse today's push")}
            title={collapsed
              ? text("展开今日推送", "Expand today's push")
              : text("折叠今日推送", "Collapse today's push")}
            onClick={() => setCollapsed((current) => !current)}
          >
            <LinearIcon name="chevronDown" />
          </button>
        </div>
      </header>

      <div className="column-list">
        {cards.length === 0 ? (
          <div className="column-empty">
            <strong>{text("今天还没有每日推送卡片", "No daily cards yet today")}</strong>
            <span className="today-push-empty-hint">
              {text(
                "论文与数据库卡片每天 09:00 由自动化投递到这里。",
                "Paper and database cards are delivered here by automation at 09:00 each day.",
              )}
            </span>
          </div>
        ) : cards.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            presentation={presentations[task.id]}
            isDragging={false}
            dragShift={0}
            isMoving={false}
            isSettling={false}
            isContextMenuOpen={contextMenuTaskId === task.id}
            availableLabels={availableLabels}
            projectName={projectNames?.[task.projectId]}
            currentUser={currentUser}
            showCover={showCover}
            showBody={showBody}
            onCreateLabel={(label) => onCreateLabel(label, task.projectId)}
            onEdit={onEdit}
            onUpdate={onUpdate}
            onContextMenu={onContextMenu}
            onDragStart={() => {}}
            onDragEnd={() => {}}
            onOpenConversation={onOpenConversation}
          />
        ))}
      </div>
    </section>
  );
}
