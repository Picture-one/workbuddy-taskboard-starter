#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仓库文档体检（只读，**零第三方依赖**）。CI 守卫与本地工具两用。

为什么要有它
------------
本仓库出过一次「使用说明点不开」的事故。复盘后确认病因是**三类结构性缺陷**，
它们共同的特点是 —— **不产生任何报错**：

  * 反引号伪链接：`` `usage-guide.md` `` 在 GitHub 上点了没反应，
    没有 404、没有 CI 失败、视觉上与真链接几乎无差别。
  * HTML 与 Markdown 静默漂移：`usage-guide.html` 是构建产物，
    改了 `.md` 忘了重建，线上看到的就是旧内容。
  * 大小写不符：Windows 本地不区分大小写，GitHub 跑在 Linux 上区分 —— 本地全对、线上全 404。

靠人眼复查是防不住的（已经失败过一次）。所以把判据固化成脚本，挂到 CI 上。

检查六项
--------
  A 伪链接   反引号里提了仓库内真实存在的 .md/.html，却不是 Markdown 链接
  B 断链     [x](target) 的相对目标不存在
  C 大小写   引用里的名字与磁盘实际名不逐字节一致
  D 锚点     [x](#y) 在同文件的标题里找不到对应 slug
  E 溯源     生成物 HTML 内嵌的「源文件 sha256」必须与当前源文件逐字节吻合
  F 自包含   生成物 HTML 不得引用任何会被浏览器**主动请求**的外部资源

判定范围
--------
只把**正文**算数：围栏代码块（``` / ~~~）与行内代码（`` ` ``）里的内容一律跳过 ——
代码里出现的文件名是在举例，不是在给读者引用。有了这条，文档里才能心安理得地
写出「别把文件名包进反引号」这种话而不被判违规。

反过来说：**别指望用代码块藏起一个真的伪链接**。伪链接的病根是「读者点不动」，
而代码块里的内容本来就不是给读者点的 —— 那里的东西不算引用，也就不算病。

用法
----
    python .github/scripts/check-docs.py              # 仓库根由脚本位置推断
    python .github/scripts/check-docs.py --root DIR   # 指向别的仓库（复用它）
    python .github/scripts/check-docs.py --quiet      # 只输出问题

退出码：0 = 通过 / 1 = 有问题 / 2 = 用法或 IO 错误

在 GitHub Actions 里会自动输出 ``::error file=...,line=...::`` 注解，
问题直接标在 Files changed 页上。
"""

import argparse
import hashlib
import os
import re
import sys

# ---------------------------------------------------------------- 约定

# 生成了 HTML 却没内嵌 built-from 标记的，在这里登记豁免。
# 保持为空 —— 那个标记正是用来防「产物与源文件对不上账」的。
STANDALONE_HTML = set()

# 运行时文件：它们只在「装完之后」的用户机器上存在，仓库里没有。
# 这些名字出现在反引号里是**正常**的（在讲命令、讲路径），绝不能当成伪链接。
RUNTIME_NAMES = {
    "start-taskboard.cmd", "status-taskboard.cmd", "stop-taskboard.cmd",
    "selfcheck-taskboard.cmd", "send-daily.cmd", "poll-once.cmd",
    "send-test.cmd", "start-poller.cmd", "config.json", "config.example.json",
    "common.py", "mailer.py", "install.py", "render.py", "scan-secrets.py",
    "selfcheck-taskboard.py", "autostart-taskboard.pyw",
    "todo.md", "readme.md",
}

# 反引号里若含这些字符，说明是路径 / 占位符 / 命令，不是在引用某个文档
PLACEHOLDER_CHARS = set("\\<>%*|")

# 构建器写进 HTML 的溯源标记。格式改了这里也要改（两处必须同步）。
BUILD_MARKER = re.compile(r"<!--\s*built-from:\s*(\S+)\s+sha256:([0-9a-fA-F]{64})\s*-->")

# 会被浏览器**主动请求**的外部资源。普通 <a href> 不算 —— 那是用户点才走的。
EXT_RES = re.compile(
    r"""<(?:link|script|img|iframe|source|video|audio|embed|object)\b[^>]*?"""
    r"""\b(?:href|src|data)\s*=\s*["'](https?://[^"']+)""",
    re.I,
)
CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*)?["']?(https?://)""", re.I)


# ---------------------------------------------------------------- slugify
# 与构建器（_build/build-usage-html.py）保持一致 —— HTML 里的 id 就是它算出来的。
_SLUG_STRIP = re.compile(r"[^\w\s\-]", re.UNICODE)
_SLUG_WS = re.compile(r"\s", re.UNICODE)


def gh_slugify(value, separator="-"):
    """复刻 GitHub 的标题锚点算法。

    GitHub 的做法：转小写 → 去掉标点（保留 Unicode 字母/数字、连字符、下划线）
    → 每个空白字符替换成一个分隔符。
    """
    s = value.strip().lower()
    s = _SLUG_STRIP.sub("", s)
    s = _SLUG_WS.sub(separator, s)
    return s


# ---------------------------------------------------------------- 收集
def collect_docs(root):
    """返回仓库内全部 .md/.html 的相对路径（排序后）。"""
    docs = []
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__")]
        for f in files:
            if f.lower().endswith((".md", ".html")):
                docs.append(os.path.relpath(os.path.join(r, f), root).replace(os.sep, "/"))
    return sorted(docs)


def strip_md_links(line):
    """去掉 [label](target)，免得把链接 label 里的反引号误算成伪链接。"""
    return re.sub(r"\[[^\]]*\]\([^)]*\)", "", line)


def strip_code_spans(line):
    """把行内代码 `` `...` `` 挖空。

    语义：**代码里的东西不是文档引用**。`[名字](路径)` 写在反引号里是在
    「举例子」，不是在给读者链接 —— 不该按断链报错。
    """
    return re.sub(r"`[^`\n]*`", " ", line)


def fenced_lines(lines):
    """返回被 ``` / ~~~ 围起来的行号集合（含围栏行本身）。

    同样是为了让「举例」不被误判：代码块里的 `usage-guide.md` 是示例文本。
    """
    out, ch = set(), None
    for i, ln in enumerate(lines, 1):
        m = re.match(r"^(`{3,}|~{3,})", ln.lstrip())
        if ch is None:
            if m:
                ch = m.group(1)[0]
                out.add(i)
        else:
            out.add(i)
            if m and m.group(1)[0] == ch:
                ch = None
    return out


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------- A 伪链接
def check_fake_links(lines, fenced, repo_names):
    """反引号里提了仓库内**真实存在**的文档，却没有做成链接。

    只看正文（跳过代码块）—— 代码块里的文件名是示例，不是给读者的引用。
    """
    out = []
    for i, ln in enumerate(lines, 1):
        if i in fenced:
            continue
        for m in re.finditer(r"`([^`\n]{1,120})`", strip_md_links(ln)):
            inner = m.group(1).strip()
            if not inner.lower().endswith((".md", ".html")):
                continue
            if any(c in inner for c in PLACEHOLDER_CHARS):
                continue
            base = os.path.basename(inner.replace("/", os.sep))
            if base in RUNTIME_NAMES:
                continue
            if base in repo_names:
                out.append((i, inner))
    return out


# ---------------------------------------------------------------- B/C 断链与大小写
def check_dead_and_case(root, rel, lines, fenced):
    dead, case = [], []
    cur = os.path.dirname(os.path.join(root, rel))
    for i, raw in enumerate(lines, 1):
        if i in fenced:
            continue
        ln = strip_code_spans(raw)
        for m in re.finditer(r"\]\(([^)\s]+)\)", ln):
            tgt = m.group(1)
            if tgt.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", tgt, re.I):
                continue        # 纯锚点 / 绝对 URL（http:、mailto: 等）
            t = tgt.split("#")[0].split("?")[0]
            if not t:
                continue
            cand = os.path.normpath(os.path.join(cur, t.replace("/", os.sep)))
            if not os.path.isfile(cand):
                dead.append((i, tgt))
                continue
            # 逐段做大小写精确比对：本地不区分、远端区分，只能这样查
            parts = os.path.relpath(cand, root).split(os.sep)
            walk = root
            for seg in parts:
                if not os.path.isdir(walk):
                    break
                listing = os.listdir(walk)
                if seg not in listing:
                    near = [x for x in listing if x.lower() == seg.lower()]
                    if near:
                        case.append((i, tgt, seg, near[0]))
                    break
                walk = os.path.join(walk, seg)
    return dead, case


# ---------------------------------------------------------------- D 锚点
def check_anchors(lines, fenced):
    slugs = set()
    for i, ln in enumerate(lines, 1):
        if i in fenced:
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", ln)
        if m:
            slugs.add(gh_slugify(m.group(2)))
    bad = []
    for i, raw in enumerate(lines, 1):
        if i in fenced:
            continue
        for m in re.finditer(r"\]\(#([^)]+)\)", strip_code_spans(raw)):
            if m.group(1) not in slugs:
                bad.append((i, m.group(1)))
    return bad



# ---------------------------------------------------------------- E 构建溯源
def check_provenance(root, rel):
    """生成物 HTML 声明的源文件指纹，必须等于源文件**此刻**的 sha256。

    这是防「改了 .md 忘了重建 .html」的唯一硬约束 ——
    光靠肉眼对比两份文件是查不出来的。
    """
    raw = read_text(os.path.join(root, rel))
    m = BUILD_MARKER.search(raw)
    if not m:
        if rel in STANDALONE_HTML:
            return []
        return [(0, "没有 built-from 溯源标记。生成的 HTML 请由构建器产出；"
                    "手工维护的页面请加进 STANDALONE_HTML 白名单")]
    src_name, want = m.group(1), m.group(2).lower()
    src_rel = os.path.normpath(
        os.path.join(os.path.dirname(rel), src_name)).replace(os.sep, "/")
    src_abs = os.path.join(root, src_rel.replace("/", os.sep))
    if not os.path.isfile(src_abs):
        return [(0, f"built-from 指向的源文件不存在：{src_rel}")]
    with open(src_abs, "rb") as fh:
        got = hashlib.sha256(fh.read()).hexdigest()
    if got != want:
        return [(0, f"{rel} 已过期：它声明来自 {src_name}@{want[:12]}，"
                   f"而 {src_rel} 现在是 {got[:12]}"
                   f" —— 改完 Markdown 没有重新构建")]
    return []


# ---------------------------------------------------------------- F 自包含
def check_selfcontained(root, rel):
    txt = read_text(os.path.join(root, rel))
    out = []
    for m in EXT_RES.finditer(txt):
        out.append((0, f"引用外部资源 {m.group(1)} —— 离线打开会缺样式/图"))
    if CSS_IMPORT.search(txt):
        out.append((0, "@import 引用了外部样式表 —— 离线打开会缺样式"))
    return out


# ---------------------------------------------------------------- main
LABEL = {
    "A": "伪链接", "B": "断链", "C": "大小写",
    "D": "锚点", "E": "构建溯源", "F": "自包含",
}
HINT = {
    "A": "改成真链接 [显示名](相对路径)",
    "B": "目标写错了，或文件没提交",
    "C": "本地不区分大小写、GitHub 区分 —— 改成磁盘上的实际名字",
    "D": "锚点与本文件标题的 slug 对不上",
    "E": "改了 Markdown 就重新构建一遍 HTML",
    "F": "内联该资源（CSS/图都写进文件里）",
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="仓库文档体检（只读）")
    here = os.path.dirname(os.path.abspath(__file__))
    default_root = os.path.dirname(os.path.dirname(here))      # ROOT/.github/scripts/
    ap.add_argument("--root", default=default_root,
                    help="仓库根目录（默认由脚本位置推断）")
    ap.add_argument("--quiet", action="store_true", help="只输出问题，不打印成功明细")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"仓库根不存在：{root}", file=sys.stderr)
        return 2

    gha = bool(os.environ.get("GITHUB_ACTIONS"))
    docs = collect_docs(root)
    repo_names = {os.path.basename(d) for d in docs}

    if not args.quiet:
        print("=" * 74)
        print(f"仓库文档体检 · {root}")
        print(f"扫描 {len(docs)} 个 .md/.html")
        print("=" * 74)

    totals = {k: 0 for k in LABEL}
    files_with_problems = 0

    for rel in docs:
        path = os.path.join(root, rel.replace("/", os.sep))
        try:
            txt = read_text(path)
        except OSError as exc:
            print(f"读不了 {rel}: {exc}", file=sys.stderr)
            return 2

        lines = txt.split("\n")
        fenced = fenced_lines(lines)

        items = []
        items += [("A", i, s) for i, s in check_fake_links(lines, fenced, repo_names)]
        dead, case = check_dead_and_case(root, rel, lines, fenced)
        items += [("B", i, t) for i, t in dead]
        items += [("C", i, f"{t}（写的={w} 实际={r}）") for i, t, w, r in case]
        items += [("D", i, "#" + a) for i, a in check_anchors(lines, fenced)]
        if rel.lower().endswith(".html"):
            items += [("E", i, s) for i, s in check_provenance(root, rel)]
            items += [("F", i, s) for i, s in check_selfcontained(root, rel)]

        if not items:
            continue
        files_with_problems += 1
        for k, _, _ in items:
            totals[k] += 1

        print()
        print(f"--- {rel} ---")
        for k, line, msg in items:
            where = f"L{line} " if line else ""
            print(f"   [{LABEL[k]}] {where}{msg}")
            if gha:
                print(f"::error file={rel},line={line or 1}::"
                      f"[{LABEL[k]}] {msg}")
        for k in {k for k, _, _ in items}:
            print(f"   ↳ 怎么修（{LABEL[k]}）：{HINT[k]}")

    print()
    print("=" * 74)
    summary = " · ".join(f"{LABEL[k]} {totals[k]}" for k in "ABCDEF")
    print(summary)
    print("=" * 74)

    bad = sum(totals.values())
    if bad:
        print(f"FAIL ✗  {bad} 处问题，分布在 {files_with_problems} 个文件")
        print("（这类问题不会自己报错：伪链接点了没反应、漂移的 HTML 静默展示旧内容。"
              "所以必须在这里拦住。）")
        return 1

    print(f"PASS ✓  {len(docs)} 个文件全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
