#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把使用说明的 Markdown 构建成**自包含单文件 HTML**。

为什么它跟 `check-docs.py` 放在一起
-----------------------------------
`usage-guide.html` 是**提交进仓库的构建产物**，源是 `usage-guide.md`。
这种「源在仓库、产物也在仓库」的形态有一个必然的失败模式：

    改了 .md → 忘了重建 → 仓库/线上 静默展示旧内容（不报任何错）

`check-docs.py` 的 E 项会把这种漂移拦在 CI 上。但**只拦不给门**是不负责任的 ——
报出「HTML 已过期」之后，总得有人能把它重建出来。所以构建器必须跟守卫一起在仓库里，
而不是留在某个人的本机工具目录里。

产物自带**溯源标记**，就是那个对账用的钥匙：

    <!-- built-from: usage-guide.md sha256:<64位十六进制> -->

依赖
----
    pip install markdown          # 只此一个第三方包，其余全标准库

用法
----
    # 仓库版（默认：src/dst 由脚本位置推断，即 references/usage-guide.*）
    python .github/scripts/build-usage-guide.py

    # 任意指定（例如另做一份本机详细版）
    python .github/scripts/build-usage-guide.py \
        --src %USERPROFILE%\\.workbuddy\\docs\\taskboard-usage.local.md \
        --title "任务面板使用说明" \
        --subtitle "面向科研工作的日常操作手册 · 本机详细版"

退出码：0 = 构建成功且自检通过 / 1 = 构建失败或自检不通过 / 2 = 用法错误

产物永远是自包含的：**零外部资源**（无 CDN、无字体外链、无图片外链）。
装 skill 的机器常常是断网的，外链必断 —— 这条是硬约束，构建时会断言。
"""

import argparse
import hashlib
import html as html_mod
import os
import re
import sys

try:
    import markdown
    from markdown.extensions.toc import TocExtension
except ImportError:                                     # pragma: no cover
    sys.stderr.write(
        "缺少依赖 markdown。请先：pip install markdown\n"
        "（这是本脚本唯一的第三方依赖，其余全用标准库。）\n")
    sys.exit(2)

# ---------------------------------------------------------------- 版面常量

STATUSES = ["backlog", "todo", "in_progress", "in_review",
            "blocked", "done", "canceled"]
PRIORITIES = ["urgent", "high", "medium", "low", "none"]

STAT_CARDS = [
    ("7", "个状态", "从「记下来」到「已验收」，每步含义固定"),
    ("5", "个视图", "仪表盘 / 看板 / 列表 / 时间线 / 项目说明"),
    ("0", "云依赖", "数据在本机 SQLite，断网可用、不需账号"),
]

CSS = """
:root{
  --ink:#1c2024; --ink-soft:#4a5259; --ink-faint:#6d7680;
  --line:#e3e7ea; --line-strong:#c9d0d6;
  --bg:#ffffff; --bg-soft:#f7f9fa; --bg-code:#f4f6f8;
  --accent:#0f6fbf; --accent-soft:#e8f2fb;
  --s-backlog:#7a8288; --s-todo:#1f6fd0; --s-progress:#b8860b;
  --s-review:#7a4fbf; --s-blocked:#c0392b; --s-done:#1f7a4d; --s-canceled:#9aa1a7;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --sans:system-ui,-apple-system,"Segoe UI","Microsoft YaHei","PingFang SC",
         "Hiragino Sans GB",sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth; scroll-padding-top:64px}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:var(--sans); font-size:15.5px; line-height:1.75;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:940px; margin:0 auto; padding:0 26px 90px}

/* ---------- 顶部导航 ---------- */
.topbar{
  position:sticky; top:0; z-index:50;
  background:rgba(255,255,255,.94); backdrop-filter:blur(8px);
  border-bottom:1px solid var(--line);
}
.topbar-inner{
  max-width:940px; margin:0 auto; padding:9px 26px;
  display:flex; align-items:center; gap:16px; flex-wrap:wrap;
}
.topbar .brand{
  font-weight:700; font-size:13.5px; color:var(--ink);
  white-space:nowrap; letter-spacing:.2px;
}
.topbar nav{display:flex; gap:3px; flex-wrap:wrap}
.topbar nav a{
  font-size:12.5px; color:var(--ink-soft); text-decoration:none;
  padding:3px 8px; border-radius:5px; white-space:nowrap;
}
.topbar nav a:hover{background:var(--accent-soft); color:var(--accent)}

/* ---------- 页头 ---------- */
header.hero{padding:52px 0 26px; border-bottom:2px solid var(--ink)}
header.hero h1{
  font-size:33px; line-height:1.25; margin:0 0 12px;
  letter-spacing:-.4px; font-weight:800;
}
.hero-sub{color:var(--ink-soft); font-size:15px; margin:0 0 4px}
.hero-meta{color:var(--ink-faint); font-size:12.5px; margin:0}

/* ---------- 数字卡 ---------- */
.stat-row{
  display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin:26px 0 8px;
}
.stat{
  border:1px solid var(--line); border-radius:10px; padding:16px 18px;
  background:var(--bg-soft);
}
.stat-num{
  display:block; font-size:30px; font-weight:800; line-height:1.1;
  color:var(--accent); letter-spacing:-1px;
}
.stat-label{display:block; font-weight:600; font-size:13px; margin-top:2px}
.stat-note{display:block; color:var(--ink-faint); font-size:11.5px; margin-top:6px; line-height:1.5}

/* ---------- 正文 ---------- */
h2{
  font-size:22px; margin:52px 0 16px; padding-top:8px;
  border-top:1px solid var(--line); letter-spacing:-.2px;
}
h2:first-of-type{border-top:none}
h3{font-size:17px; margin:30px 0 10px; color:var(--ink)}
h4{font-size:15px; margin:22px 0 8px; color:var(--ink-soft)}
p{margin:12px 0}
a{color:var(--accent)}
strong{font-weight:650}
hr{border:none; border-top:1px solid var(--line); margin:36px 0}
ul,ol{padding-left:24px; margin:12px 0}
li{margin:5px 0}

/* ---------- 表格 ---------- */
table{
  border-collapse:collapse; width:100%; margin:18px 0; font-size:14px;
  border:1px solid var(--line-strong);
}
caption{caption-side:top; text-align:left; font-size:12.5px; color:var(--ink-faint); padding-bottom:6px}
thead th{
  background:var(--bg-code); text-align:left; font-weight:650; font-size:13px;
  padding:10px 12px; border-bottom:1px solid var(--line-strong);
  border-right:1px solid var(--line);
}
thead th:last-child{border-right:none}
tbody td{
  padding:9px 12px; border-bottom:1px solid var(--line);
  border-right:1px solid var(--line); vertical-align:top; line-height:1.65;
}
tbody td:last-child{border-right:none}
tbody tr:last-child td{border-bottom:none}
tbody tr:nth-child(even){background:#fbfcfd}

/* ---------- 代码 ---------- */
code{
  font-family:var(--mono); font-size:.875em; background:var(--bg-code);
  padding:1.5px 5px; border-radius:4px; color:#b0355a;
  word-break:break-word;
}
pre{
  background:var(--bg-code); border-left:3px solid var(--line-strong);
  border-radius:0 7px 7px 0; padding:14px 16px; overflow-x:auto;
  margin:16px 0; font-size:13px; line-height:1.6;
}
pre code{
  background:none; padding:0; color:var(--ink); font-size:13px;
  white-space:pre; word-break:normal;
}

/* ---------- 引用块 ---------- */
blockquote{
  margin:16px 0; padding:12px 18px; border-left:3px solid var(--accent);
  background:var(--accent-soft); border-radius:0 7px 7px 0;
  color:var(--ink-soft);
}
blockquote p{margin:6px 0}
blockquote p:first-child{margin-top:0}
blockquote p:last-child{margin-bottom:0}

/* ---------- 状态色片（**必须带文字**，黑白打印才读得出） ---------- */
.chip{
  display:inline-block; font-family:var(--mono); font-size:12px; font-weight:600;
  padding:2px 8px; border-radius:20px; border:1px solid;
  background:#fff; white-space:nowrap;
}
.chip-backlog{color:var(--s-backlog); border-color:var(--s-backlog)}
.chip-todo{color:var(--s-todo); border-color:var(--s-todo)}
.chip-in_progress{color:var(--s-progress); border-color:var(--s-progress)}
.chip-in_review{color:var(--s-review); border-color:var(--s-review)}
.chip-blocked{color:var(--s-blocked); border-color:var(--s-blocked)}
.chip-done{color:var(--s-done); border-color:var(--s-done)}
.chip-canceled{color:var(--s-canceled); border-color:var(--s-canceled)}

/* ---------- 页脚 ---------- */
footer{
  margin-top:56px; padding-top:18px; border-top:1px solid var(--line);
  color:var(--ink-faint); font-size:12.5px;
}

/* ---------- 打印 ---------- */
@media print{
  :root{--bg:#fff}
  .topbar,.noprint{display:none !important}
  .wrap{max-width:none; padding:0}
  @page{size:A4; margin:14mm}
  body{font-size:10.5pt; line-height:1.5}
  header.hero{padding-top:0; border-bottom-width:1.5pt}
  header.hero h1{font-size:20pt}
  h2{font-size:13pt; margin-top:16pt; break-before:page}
  h2:first-of-type{break-before:auto}
  h3{font-size:11.5pt; margin-top:11pt}
  a{color:var(--ink); text-decoration:none}
  a[href^="http"]::after{content:" (" attr(href) ")"; font-size:8.5pt; color:#555}
  table{font-size:9pt; break-inside:auto}
  tr,blockquote,.stat{break-inside:avoid}
  thead{display:table-header-group}
  pre{font-size:8.5pt; break-inside:avoid; background:#f5f5f5}
  .stat-row{grid-template-columns:repeat(3,1fr); gap:8pt}
  .chip{background:#fff !important; -webkit-print-color-adjust:exact; print-color-adjust:exact}
}
"""

# 会被浏览器**主动请求**的外部资源（普通 <a href> 不算）
EXT_RES = re.compile(
    r"""<(?:link|script|img|iframe|source|video|audio|embed|object)\b[^>]*?"""
    r"""\b(?:href|src|data)\s*=\s*["'](https?://[^"']+)""",
    re.I,
)

# ---------------------------------------------------------------- slugify
_SLUG_STRIP = re.compile(r"[^\w\s\-]", re.UNICODE)
_SLUG_WS = re.compile(r"\s", re.UNICODE)


def gh_slugify(value, separator):
    """复刻 GitHub 的标题锚点算法，好让 Markdown 里手写的目录链接两边都能用。

    GitHub 的做法：转小写 → 去掉标点（保留 Unicode 字母/数字、连字符、下划线）
    → 每个空白字符替换成一个分隔符。
    """
    s = value.strip().lower()
    s = _SLUG_STRIP.sub("", s)
    s = _SLUG_WS.sub(separator, s)
    return s


# ---------------------------------------------------------------- 后处理
def render_stat_cards():
    cards = []
    for num, label, note in STAT_CARDS:
        cards.append(
            '<div class="stat">'
            f'<span class="stat-num">{html_mod.escape(num)}</span>'
            f'<span class="stat-label">{html_mod.escape(label)}</span>'
            f'<span class="stat-note">{html_mod.escape(note)}</span>'
            '</div>'
        )
    return '<div class="stat-row noprint">' + "".join(cards) + '</div>'


def chipify(body_html):
    """把 <td><code>todo</code></td> 这类单元格变成状态色片。"""
    names = STATUSES + PRIORITIES

    def repl(m):
        name = m.group(1)
        cls = "chip chip-" + name if name in STATUSES else "chip"
        return f'<td><span class="{cls}">{name}</span></td>'

    pattern = r"<td><code>(" + "|".join(names) + r")</code></td>"
    return re.sub(pattern, repl, body_html)


def build_toc(body_html):
    """从 h2 抽导航条目（用已生成的 id）。跳过「目录」自身。"""
    items = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', body_html, re.S)
    out = []
    for hid, text in items:
        label = re.sub(r"<[^>]+>", "", text).strip()
        if label == "目录":
            continue
        label_short = re.sub(r"^[一二三四五六七八九十]+、", "", label)
        out.append(f'<a href="#{hid}">{html_mod.escape(label_short)}</a>')
    return "".join(out)


def check_anchors(md_text, body_html, label):
    """校验 Markdown 里手写的目录链接都能在生成的 HTML 里找到对应 id。

    这是防「目录点不动」的关键检查 —— 之前踩过锚点算法不一致的坑。
    """
    ids = set(re.findall(r'<h[1-6] id="([^"]+)"', body_html))
    links = re.findall(r"\]\(#([^)]+)\)", md_text)
    missing = [l for l in links if l not in ids]
    print(f"   锚点校验：{len(links)} 个目录链接，缺失 {len(missing)} 个")
    if missing:
        for m in missing:
            print(f"     ✗ #{m}")
    return not missing


# ---------------------------------------------------------------- 主流程
def convert(src, dst, title, subtitle):
    print(f"\n=== {os.path.basename(src)} ===")
    if not os.path.isfile(src):
        print("   源文件不存在，跳过")
        return False

    with open(src, encoding="utf-8") as fh:
        md_text = fh.read()

    # ── 溯源指纹：对**源文件的字节**取 sha256。check-docs.py 用它对账。
    with open(src, "rb") as fh:
        src_sha = hashlib.sha256(fh.read()).hexdigest()
    src_name = os.path.basename(src)

    md = markdown.Markdown(extensions=[
        "tables", "fenced_code", "sane_lists", "md_in_html", "attr_list",
        TocExtension(slugify=gh_slugify, separator="-"),
    ])
    body = md.convert(md_text)

    # 首页数字卡：原地替换标记（先试被 <p> 包裹的形态，再试裸注释）
    cards = render_stat_cards()
    if "<p><!-- stat-cards --></p>" in body:
        body = body.replace("<p><!-- stat-cards --></p>", cards)
    elif "<!-- stat-cards -->" in body:
        body = body.replace("<!-- stat-cards -->", cards)
    elif "<!-- stat-cards -->" in md_text:
        print("   ⚠️ 标记没能定位到，数字卡未插入")
    body = re.sub(r"<p>\s*</p>", "", body)

    body = chipify(body)

    # 页头已经有 h1 了 —— 去掉正文里那个重复的标题
    body = re.sub(r"^\s*<h1[^>]*>.*?</h1>\s*", "", body, count=1, flags=re.S)

    ok_anchor = check_anchors(md_text, body, src_name)

    nav = build_toc(body)
    t = html_mod.escape(title)
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- built-from: {src_name} sha256:{src_sha} -->
<title>{t}</title>
<meta name="description" content="{html_mod.escape(subtitle)}">
<style>{CSS}</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-inner">
    <span class="brand">{t}</span>
    <nav>{nav}</nav>
  </div>
</div>
<div class="wrap">
<header class="hero">
  <h1>{t}</h1>
  <p class="hero-sub">{html_mod.escape(subtitle)}</p>
  <p class="hero-meta">生成自 <code>{html_mod.escape(src_name)}</code> · 可离线打开 · 可直接打印为 PDF</p>
</header>
{body}
<footer>
  <p>本页为自包含单文件（无任何外部资源），可离线打开、可直接打印为 PDF。</p>
  <p>内容由 <code>{html_mod.escape(src_name)}</code>
     （sha256 <code>{src_sha[:12]}…</code>）自动生成 —— 要改内容请改 Markdown
     后重新构建，别直接编辑本文件。</p>
</footer>
</div>
</body>
</html>
"""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(doc)

    # ── 自检 1：自包含（零外部资源）
    ext = EXT_RES.findall(doc)
    print(f"   已写出 {dst}")
    print(f"   体积 {len(doc.encode('utf-8')):,} B · 外部资源引用 {len(ext)} 个（应为 0）")
    for u in ext[:5]:
        print(f"     ✗ {u}")

    # ── 自检 2：溯源标记真的写进去了
    marker = re.search(r"<!-- built-from: \S+ sha256:([0-9a-f]{64}) -->", doc)
    ok_marker = bool(marker) and marker.group(1) == src_sha
    print(f"   溯源标记：{'已写入 ✓' if ok_marker else '缺失或对不上 ✗'} "
          f"sha256={src_sha[:12]}…")
    return ok_anchor and not ext and ok_marker


def default_repo_pair():
    """由脚本位置推断仓库里的 usage-guide.md / .html 一对。"""
    here = os.path.dirname(os.path.abspath(__file__))          # ROOT/.github/scripts
    root = os.path.dirname(os.path.dirname(here))
    refs = os.path.join(root, "workbuddy-taskboard-starter", "references")
    return os.path.join(refs, "usage-guide.md"), os.path.join(refs, "usage-guide.html")


def main(argv=None):
    d_src, d_dst = default_repo_pair()
    ap = argparse.ArgumentParser(
        description="把使用说明 Markdown 构建成自包含单文件 HTML")
    ap.add_argument("--src", default=d_src, help="源 Markdown")
    ap.add_argument("--dst", default=d_dst, help="输出的 HTML（默认与源同目录同名）")
    ap.add_argument("--title", default="任务面板使用说明")
    ap.add_argument("--subtitle",
                    default="面向科研工作的日常操作手册 · 通用版（示例已脱敏）")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.src):
        sys.stderr.write(f"源文件不存在：{args.src}\n")
        return 2

    ok = convert(args.src, args.dst, args.title, args.subtitle)
    print()
    print("BUILD OK" if ok else "BUILD FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
