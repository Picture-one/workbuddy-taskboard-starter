/**
 * 对话目标（Conversation target）——「在任务面板里点开对话，应该开到哪里」的单一真源。
 *
 * 背景（本机实证 2026-09-16）：
 * 1. 上游把 `codex://threads/...` 硬编码在 3 处（打开已绑定的 thread、打开 legacy thread、
 *    为任务新开 thread）。本机（WorkBuddy 环境）**没有安装 Codex**，且注册表里
 *    `HKCU\Software\Classes\codex` 缺少 `shell\open\command` 子键 —— 也就是说
 *    `codex://` **根本打不开**，点了没有任何反应。
 * 2. 本机 `HKCU\Software\Classes\workbuddy\shell\open\command`
 *    = `"F:\workbuddy\WorkBuddy.exe" "%1"`，`workbuddy://` 可用。
 * 3. 现有任务的 `thread_id` 全是合成标签（`mail-bridge` / `paper-daily-push`），
 *    `thread_codex_project_id` 为 NULL —— **没有任何真实会话可以「回到」**。
 *
 * 因此统一语义为：**新开一条 WorkBuddy 对话**（草稿态，由用户确认后提交），
 * 而不是尝试恢复一个并不存在的旧会话。
 *
 * WorkBuddy 深链契约（逆向自 `F:\workbuddy\resources\app.asar`）：
 *   - host 必须是 `task`，action 必须是 `start`
 *   - `prompt` 为预填到新建任务输入框的草稿内容，**上限 8000 字符**
 *   - 命中后打开「新建任务」并预填，**停在草稿态、不自动提交**
 */

/** 深链 prompt 长度上限（超出会被宿主截断或拒收）。 */
export const MAX_DEEPLINK_PROMPT = 8000;

/** 支持作为「对话宿主」的应用。默认 workbuddy。 */
export type ConversationApp = "workbuddy" | "codex";

export const DEFAULT_CONVERSATION_APP: ConversationApp = "workbuddy";

/** 允许用 `?conversationApp=codex` 显式退回上游行为（无需改代码即可回退）。 */
export function resolveConversationApp(search: URLSearchParams): ConversationApp {
  const raw = (search.get("conversationApp") ?? search.get("conversation-app") ?? "")
    .trim()
    .toLowerCase();
  return raw === "codex" ? "codex" : DEFAULT_CONVERSATION_APP;
}

/** 按字符界截断，避免把代理对（emoji / 生僻字）劈成半个字符。 */
function clampPrompt(prompt: string): string {
  if (prompt.length <= MAX_DEEPLINK_PROMPT) return prompt;
  let cut = prompt.slice(0, MAX_DEEPLINK_PROMPT);
  const last = cut.charCodeAt(cut.length - 1);
  if (last >= 0xd800 && last <= 0xdbff) cut = cut.slice(0, -1);
  return cut;
}

/**
 * 构造「在 WorkBuddy 里新开一条对话」的深链。
 *
 * @param prompt 预填到输入框的草稿（通常是 `[$manage-taskboard](...) 议题 ID：XXX`）
 * @param cwd    可选工作目录；传工作区绝对路径能让新对话直接落在对的项目上
 */
export function buildWorkBuddyNewTaskUrl(prompt: string, cwd?: string): string {
  const url = new URL("workbuddy://task");
  url.searchParams.set("action", "start");
  url.searchParams.set("prompt", clampPrompt(prompt));
  if (cwd) url.searchParams.set("cwd", cwd);
  return url.toString();
}

/** `codex://` 深链（仅在显式选择 codex 宿主时使用）。 */
export function buildCodexThreadUrl(threadId: string): string {
  return `codex://threads/${encodeURIComponent(threadId.trim())}`;
}

/** 用于 UI 展示的宿主名称（「关联对话」列表的状态标签）。 */
export function conversationAppDisplayName(app: ConversationApp): string {
  return app === "codex" ? "Codex" : "WorkBuddy";
}

/** 仅在显式选择 codex 宿主时使用：新开一个 Codex thread。 */
export function buildCodexNewThreadUrl(prompt: string, cwd?: string): string {
  const url = new URL("codex://threads/new");
  if (cwd) url.searchParams.set("path", cwd);
  url.searchParams.set("prompt", clampPrompt(prompt));
  return url.toString();
}

/**
 * 打开外部协议链接，并在**协议未注册**时回调 `onMissing`。
 *
 * 为什么要探测：`window.location.assign("codex://...")` 在协议未注册时**不抛异常、不返回失败**
 * —— 页面毫无反应，用户只会觉得"点了没用"，开发者拿不到任何错误。这正是本机 `codex://`
 * 的实际情况。做法是利用「浏览器把协议交给外部程序时会触发 blur」这一事实反向判定。
 *
 * 注意：这是**启发式**判定。若浏览器从未失焦（例如前台弹窗拦截），会误判为"未安装"。
 * 因此 `onMissing` 只用于给出提示，不做破坏性动作。
 */
export function openExternalUrl(
  url: string,
  onMissing?: (url: string) => void,
  timeoutMs = 1200,
): void {
  if (!onMissing) {
    window.location.assign(url);
    return;
  }
  let handled = false;
  const markHandled = () => {
    handled = true;
  };
  window.addEventListener("blur", markHandled, { once: true });
  document.addEventListener("visibilitychange", markHandled, { once: true });
  try {
    window.location.assign(url);
  } catch {
    handled = false;
  }
  window.setTimeout(() => {
    window.removeEventListener("blur", markHandled);
    document.removeEventListener("visibilitychange", markHandled);
    if (!handled) onMissing(url);
  }, timeoutMs);
}
