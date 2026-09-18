import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MAX_DEEPLINK_PROMPT,
  buildCodexNewThreadUrl,
  buildCodexThreadUrl,
  buildWorkBuddyNewTaskUrl,
  conversationAppDisplayName,
  openExternalUrl,
  resolveConversationApp,
} from "./conversationTarget";

describe("buildWorkBuddyNewTaskUrl", () => {
  it("产出 WorkBuddy 新建任务深链（host=task, action=start）", () => {
    const url = new URL(buildWorkBuddyNewTaskUrl("继续处理 WOR-6", "F:\\ws\\2026-09-07-21-05-00"));
    expect(url.protocol).toBe("workbuddy:");
    expect(url.host).toBe("task");
    expect(url.searchParams.get("action")).toBe("start");
    expect(url.searchParams.get("prompt")).toBe("继续处理 WOR-6");
    expect(url.searchParams.get("cwd")).toBe("F:\\ws\\2026-09-07-21-05-00");
  });

  it("不传 cwd 时不产生 cwd 参数", () => {
    expect(buildWorkBuddyNewTaskUrl("x")).not.toContain("cwd");
  });

  it("超长 prompt 截断到上限，且不劈开代理对", () => {
    const long = new URL(buildWorkBuddyNewTaskUrl("字".repeat(20000))).searchParams.get("prompt")!;
    expect(long.length).toBeLessThanOrEqual(MAX_DEEPLINK_PROMPT);
    const surrogate = new URL(
      buildWorkBuddyNewTaskUrl("a".repeat(MAX_DEEPLINK_PROMPT - 1) + "😀" + "b".repeat(50)),
    ).searchParams.get("prompt")!;
    expect(/[\uD800-\uDBFF]$/.test(surrogate)).toBe(false);
  });
});

describe("宿主解析与回退", () => {
  it("默认 workbuddy；显式 codex 才切换；非法值回落", () => {
    expect(resolveConversationApp(new URLSearchParams(""))).toBe("workbuddy");
    expect(resolveConversationApp(new URLSearchParams("conversationApp=codex"))).toBe("codex");
    expect(resolveConversationApp(new URLSearchParams("conversation-app=codex"))).toBe("codex");
    expect(resolveConversationApp(new URLSearchParams("conversationApp=CODEX"))).toBe("codex");
    expect(resolveConversationApp(new URLSearchParams("conversationApp=foo"))).toBe("workbuddy");
    expect(conversationAppDisplayName("workbuddy")).toBe("WorkBuddy");
    expect(conversationAppDisplayName("codex")).toBe("Codex");
  });

  it("codex 链接仅在显式回退时构造，且形状与上游一致", () => {
    expect(buildCodexThreadUrl(" abc ")).toBe("codex://threads/abc");
    expect(buildCodexNewThreadUrl("p", "C:\\w")).toContain("codex://threads/new?path=C%3A%5Cw&prompt=p");
  });
});

describe("openExternalUrl 的协议缺失探测（降级路径）", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("窗口失焦（协议已被外部程序接管）→ 不报错", () => {
    vi.useFakeTimers();
    const onMissing = vi.fn();
    openExternalUrl("workbuddy://task?action=start&prompt=x", onMissing, 1000);
    window.dispatchEvent(new Event("blur"));
    vi.advanceTimersByTime(5000);
    expect(onMissing).not.toHaveBeenCalled();
  });

  it("始终没有失焦（协议未注册）→ 触发 onMissing", () => {
    vi.useFakeTimers();
    const onMissing = vi.fn();
    openExternalUrl("codex://threads/nope", onMissing, 1000);
    vi.advanceTimersByTime(999);
    expect(onMissing).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2);
    expect(onMissing).toHaveBeenCalledTimes(1);
    expect(onMissing.mock.calls[0][0]).toBe("codex://threads/nope");
  });

  it("页面可见性变化（协议已被外部程序接管）→ 不报错", () => {
    vi.useFakeTimers();
    const onMissing = vi.fn();
    openExternalUrl("workbuddy://task?action=start&prompt=x", onMissing, 1000);
    document.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(5000);
    expect(onMissing).not.toHaveBeenCalled();
  });

  it("未提供 onMissing 时不抛异常（无探测、直接导航）", () => {
    vi.useFakeTimers();
    expect(() => openExternalUrl("workbuddy://task?action=start")).not.toThrow();
    vi.advanceTimersByTime(5000);
  });

  it("超时与失焦都发生后，onMissing 至多调用一次", () => {
    vi.useFakeTimers();
    const onMissing = vi.fn();
    openExternalUrl("codex://threads/x", onMissing, 1000);
    vi.advanceTimersByTime(2000);
    window.dispatchEvent(new Event("blur"));
    vi.advanceTimersByTime(2000);
    expect(onMissing).toHaveBeenCalledTimes(1);
  });
});
