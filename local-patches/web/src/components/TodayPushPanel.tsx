import { useState } from "react";
import type { ActorIdentity, Task, TaskDraft } from "../types";
import { useTaskboardI18n } from "../i18n";
import { dailyCardTasksForDay, localDay } from "../boardGrouping";
import type { TaskCardPresentation, TaskConversationItem } from "../taskConversations";
import { TaskCard } from "./TaskCard";
import { LinearIcon } from "./LinearIcon";

/**
 * 看板顶部的「今日推送」专区。
 *
 * 设计约束（与实施计划 P5 对应）：
 * - **只放每日推送卡片**：判据是静态标签 `daily-card*`，普通任务不混入（决策 7）。
 * - **只读、不进拖拽体系**：根节点用 `onDragStartCapture` 取消整棵子树的 `dragstart`。
 *   `TaskCard` 自带 `draggable={!isMoving}`，若只把 `onDragStart` 传成空函数，
 *   浏览器仍会发起原生拖拽（只是落地时 `dataTransfer` 为空）。在捕获阶段 `preventDefault()`
 *   会直接取消这次拖拽（规范行为），从而无需改动 `TaskCard` 这个被复用的组件。
 * - **折叠态只存组件内 state**，刻意不新增 storage key —— 否则又多一处需要向后兼容归一化的配置。
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

  return (
    <section
      className={"today-push-panel" + (collapsed ? " is-collapsed" : "")}
      aria-label={text("今日推送", "Today's push")}
      data-today-key={dayKey}
      // 捕获阶段取消整棵子树的 dragstart → 本专区的卡片不参与拖拽
      onDragStartCapture={(event) => event.preventDefault()}
    >
      <header className="today-push-header">
        <button
          className="today-push-toggle"
          type="button"
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((current) => !current)}
        >
          <LinearIcon name="panel" />
          <strong>{text("今日推送", "Today's push")}</strong>
          <span className="today-push-date">{dayKey}</span>
          {cards.length > 0 && <span className="today-push-count">{cards.length}</span>}
        </button>
      </header>

      {!collapsed && (
        <div className="today-push-cards">
          {cards.length === 0 ? (
            <div className="today-push-empty">
              <strong>{text("今天还没有每日推送卡片", "No daily cards yet today")}</strong>
              <span>
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
              variant="sidebar"
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
      )}
    </section>
  );
}
