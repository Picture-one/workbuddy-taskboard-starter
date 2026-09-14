#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安装器「模板 + 快照」的回归体检（只读）。

为什么它必须在仓库里
--------------------
`references/setup/expected/` 那 24 个快照**本身就是仓库内容**。
如果验证器留在仓库外，陌生人 clone 下来既无法自证「模板渲染是否正确」，
仓库里也没有任何关卡会发现「改了 `render.py` 或模板却没刷新快照」——
判据在仓库外 = 没有守卫。这和 `check-docs.py` 是同一条道理。

九项判据
--------
  1 模板渲染   全部 `.tpl` × 两套夹具都能渲染成功（无未替换占位符）
  2 占位符键   `.tpl` 里用到的每个键都必须是 `build_variables()` 提供的键
  3 产物约束   渲染结果无 `{{` 残留；`.cmd` 行尾全 CRLF 且纯 ASCII；`.py` 可编译
  4 origin 陷阱 远程访问关闭时**绝不能**出现空的 `set`（服务会起不来）
  5 关键路径   `APP_DIR` / `DATA_DIR` / `PORT` / `NODE_EXE` 真的被填进去了
  6 配置样例   `mail-bridge-config.example.json` 合法，且兜底通道默认关闭
  7 自身可编译 `setup/*.py` 与 `payload/*.py` 语法可编译
  8 快照回归   与 `expected/` 逐字节一致（模板确实改了要用 `--update-snapshots` 显式刷基线）
  9 快照孤儿   `expected/` 里不得留下已删模板的快照

为什么不能「渲染完就覆盖快照」
------------------------------
那等于每次跑都把基线刷新成当前值，**回归永远测不出来**。
所以默认只比对；漂移即 FAIL。

跨平台
------
`render.read_text()` 会把 `\\r\\n` 归一化成 `\\n`，所以渲染结果与宿主平台无关，
本脚本可以原样跑在 Linux CI 上。

用法
----
    python .github/scripts/check-setup.py                      # 体检（仓库根由脚本位置推断）
    python .github/scripts/check-setup.py --root <dir>         # 体检别的副本（如 tarball 解包出的）
    python .github/scripts/check-setup.py --update-snapshots    # 模板确实改了，显式刷新基线
    python .github/scripts/check-setup.py --quiet

退出码：0 = 全通过；1 = 有失败项；2 = 环境不对（找不到 setup 目录等）
"""

import argparse
import importlib
import json
import os
import re
import sys

# 宽松匹配 —— 故意不要求和 render.PLACEHOLDER_RE 一样严。
# 严格正则只认 {{UPPER_SNAKE}}，于是写成 {{ myKey }} / {{port}} 的占位符
# 既不会被替换、也不会被「残留检测」发现，会**静默漏进产物**。
# 这里用宽松模式把它们全抓出来单独报错，好告诉作者「键名不对」。
LOOSE_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]*)\}\}")

PKG_REL = os.path.join("workbuddy-taskboard-starter", "references", "setup")
SCRIPTS_REL = os.path.join(".github", "scripts")


def default_root():
    """`.github/scripts/check-setup.py` → 仓库根。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


class Report:
    """收集结果：控制台输出 + GitHub Actions 行内标注 + 退出码。"""

    def __init__(self, quiet=False, gha=False):
        self.failures = []          # [(label, detail)]
        self.quiet = quiet
        self.gha = gha
        self.root = ""

    def section(self, title):
        if not self.quiet:
            print()
            print("=== %s ===" % title)

    def ok(self, label):
        if not self.quiet:
            print("  OK   %s" % label)

    def fail(self, label, detail="", rel=None, line=1):
        self.failures.append((label, detail))
        print("  FAIL %s%s" % (label, ("  -> " + detail) if detail else ""))
        if self.gha and rel:
            msg = label + (("  -> " + detail) if detail else "")
            print("::error file=%s,line=%d::%s" % (rel, line, msg))

    def check(self, good, label, detail="", rel=None, line=1):
        if good:
            self.ok(label)
        else:
            self.fail(label, detail, rel, line)
        return good


def compile_ok(text, name):
    try:
        compile(text, name, "exec")
        return True, ""
    except SyntaxError as exc:
        return False, "%s (line %s)" % (exc.msg, exc.lineno)


def main(argv=None):
    ap = argparse.ArgumentParser(description="安装器模板与快照回归体检（只读）")
    ap.add_argument("--root", default=default_root(),
                    help="仓库根目录（默认由脚本位置推断）")
    ap.add_argument("--update-snapshots", action="store_true",
                    help="模板确实改了：把 expected/ 基线刷成当前渲染结果")
    ap.add_argument("--quiet", action="store_true", help="只报失败项")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    setup = os.path.join(root, PKG_REL)
    if not os.path.isdir(setup):
        print("找不到安装器目录：%s" % setup, file=sys.stderr)
        print("（用 --root 指向仓库根；目录布局应为 "
              "<root>/%s）" % PKG_REL.replace(os.sep, "/"), file=sys.stderr)
        return 2

    rel_setup = PKG_REL.replace(os.sep, "/") + "/"
    tpl_dir = os.path.join(setup, "templates")
    exp_dir = os.path.join(setup, "expected")

    rep = Report(quiet=args.quiet, gha=bool(os.environ.get("GITHUB_ACTIONS")))
    rep.root = root

    if not args.quiet:
        print("=" * 74)
        print("安装器模板与快照体检 · %s" % root)
        print("=" * 74)

    # render.py 只能从目标仓库里加载 —— 判据必须与对象同源，
    # 否则「用旧渲染器验新模板」会得出漂亮但无意义的结论。
    sys.path.insert(0, setup)
    try:
        render = importlib.import_module("render")
    except Exception as exc:
        print("加载 render.py 失败：%r" % (exc,), file=sys.stderr)
        return 2

    # ── 夹具：全部是脱敏的 demo 值，可安全进仓库 ──────────────────
    fix_a = render.build_variables(
        r"C:\Users\demo\.workbuddy\binaries\node\versions\22.22.2-3\node.exe",
        r"C:\Users\demo\.workbuddy\binaries\python\versions\3.13.12\python.exe",
        r"C:\Users\demo\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe",
        r"C:\Users\demo\.workbuddy\apps\dashi-taskboard",
        r"C:\Users\demo\.workbuddy\taskboard-data",
        47823,
        "https://demo-node.example.net",
        "Taskboard",
    )
    # 夹具 B：远程访问关闭（最容易出事的路径）+ 换掉 Run 值名以证明它真被模板化
    fix_b = dict(fix_a)
    fix_b["TRUSTED_ORIGIN"] = ""
    fix_b["TRUSTED_ORIGINS_BLOCK"] = render.trusted_origins_block("")
    fix_b["RUN_VALUE_NAME"] = "TaskboardVerify"
    fix_b["PORT"] = "47899"
    fixtures = (("A", fix_a), ("B", fix_b))

    tpl_files = sorted(f for f in os.listdir(tpl_dir) if f.endswith(".tpl"))
    if not tpl_files:
        print("templates/ 里没有 .tpl：%s" % tpl_dir, file=sys.stderr)
        return 2

    # ── 1 渲染 ────────────────────────────────────────────────
    rep.section("1) 渲染全部模板（两套夹具）")
    rendered = {}
    for name in tpl_files:
        rel = rel_setup + "templates/" + name
        with open(os.path.join(tpl_dir, name), "rb") as fh:
            raw = fh.read()
        text, _ = render.read_text(os.path.join(tpl_dir, name))
        for tag, fx in fixtures:
            try:
                out = render.render(text, fx)
            except ValueError as exc:
                rep.fail("%s [%s] 渲染" % (name, tag), str(exc), rel)
                continue
            rendered[(name, tag)] = out
            rep.ok("%s [%s] 渲染成功" % (name, tag))

    # ── 2 占位符键白名单 ───────────────────────────────────────
    rep.section("2) 模板用到的占位符键都必须是 build_variables() 提供的")
    known = set(fix_a)
    for name in tpl_files:
        rel = rel_setup + "templates/" + name
        with open(os.path.join(tpl_dir, name), "rb") as fh:
            raw = fh.read().decode("utf-8", "replace")
        for lineno, ln in enumerate(raw.replace("\r\n", "\n").split("\n"), 1):
            for m in LOOSE_PLACEHOLDER_RE.finditer(ln):
                key = m.group(1).strip()
                if key in known:
                    rep.ok("%s L%d {{%s}} 是已知键" % (name, lineno, key))
                else:
                    rep.fail("%s L%d 占位符 {{%s}} 不是已知键" % (name, lineno, key),
                             "可用键：%s" % ", ".join(sorted(known)),
                             rel, lineno)

    # ── 3 产物约束 ─────────────────────────────────────────────
    rep.section("3) 渲染结果的硬约束（无残留 / .cmd 行尾 / 纯 ASCII / 可编译）")
    for (name, tag), out in sorted(rendered.items()):
        rel = rel_setup + "templates/" + name
        rep.check("{{" not in out, "%s [%s] 无 {{ 残留" % (name, tag),
                  "仍有未替换的占位符", rel)
        target = name[:-4]                      # 去掉 .tpl
        if target.endswith((".cmd", ".bat")):
            # 渲染文本是归一化的 \n；写盘时由 render_file 转 CRLF，这里验的是写入逻辑
            written = out.replace("\n", "\r\n")
            rep.check("\r\n" in written and re.search(r"(?<!\r)\n", written) is None,
                      "%s [%s] .cmd 行尾全 CRLF" % (name, tag), "", rel)
            non_ascii = [c for c in out if ord(c) > 127]
            rep.check(not non_ascii, "%s [%s] .cmd 纯 ASCII" % (name, tag),
                      "非 ASCII 字符: %r" % (non_ascii[:8],), rel)
        if target.endswith((".py", ".pyw")):
            good, detail = compile_ok(out, target)
            rep.check(good, "%s [%s] 语法可编译" % (name, tag), detail, rel)

    # ── 4 origin 空值陷阱 ──────────────────────────────────────
    rep.section("4) 远程访问关闭时绝不能出现空的 set（服务会起不来）")
    rel_start = rel_setup + "templates/start-taskboard.cmd.tpl"
    a = rendered.get(("start-taskboard.cmd.tpl", "A"), "")
    b = rendered.get(("start-taskboard.cmd.tpl", "B"), "")
    rep.check('set "CODEX_TASKBOARD_TRUSTED_ORIGINS=https://demo-node.example.net"' in a,
              "有 origin 时写入了正确的一行", "", rel_start)
    rep.check('set "CODEX_TASKBOARD_TRUSTED_ORIGINS=' not in b,
              "无 origin 时**没有**任何 set 该变量",
              "; ".join(l for l in b.splitlines() if "TRUSTED_ORIGINS" in l), rel_start)
    for line in b.splitlines():
        if "TRUSTED_ORIGINS" in line:
            rep.check(line.lstrip().upper().startswith("REM"),
                      "无 origin 时该行是注释: %r" % line[:60], "", rel_start)
    rep.check("{{TRUSTED_ORIGINS_BLOCK}}" not in a and "{{TRUSTED_ORIGINS_BLOCK}}" not in b,
              "条件块占位符已被替换", "", rel_start)

    # ── 5 关键路径真的填进去了 ─────────────────────────────────
    rep.section("5) 关键路径已正确填入")
    combined_a = "\n".join(v for (n, t), v in rendered.items() if t == "A")
    for needle, label in [
        (r"C:\Users\demo\.workbuddy\apps\dashi-taskboard", "APP_DIR"),
        (r"C:\Users\demo\.workbuddy\taskboard-data", "DATA_DIR"),
        ("47823", "PORT"),
        (r"C:\Users\demo\.workbuddy\binaries\node\versions\22.22.2-3\node.exe",
         "NODE_EXE"),
    ]:
        rep.check(needle in combined_a, "渲染结果含 %s" % label, "", rel_start)
    rep.check("TaskboardVerify" in rendered.get(("selfcheck-taskboard.py.tpl", "B"), ""),
              "RUN_VALUE_NAME 已被模板化（隔离验证必需）", "", rel_start)

    # ── 6 配置样例 ─────────────────────────────────────────────
    rep.section("6) mail-bridge 配置样例是合法 JSON 且兜底通道默认关闭")
    rel_cfg = rel_setup + "templates/mail-bridge-config.example.json"
    try:
        with open(os.path.join(tpl_dir, "mail-bridge-config.example.json"),
                  "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        rep.check(True, "config.example.json 合法（%d 个键）" % len(cfg))
        rep.check(cfg.get("wecomWebhookUrl") == "" and cfg.get("pushWecom") is False,
                  "兜底通道默认关闭",
                  "wecomWebhookUrl=%r pushWecom=%r"
                  % (cfg.get("wecomWebhookUrl"), cfg.get("pushWecom")), rel_cfg)
    except Exception as exc:
        rep.fail("config.example.json 合法", repr(exc), rel_cfg)

    # ── 7 setup/ 与 payload/ 脚本自身可编译 ────────────────────
    rep.section("7) setup/ 与 payload/ 下的脚本自身可编译")
    targets = [(os.path.join(setup, n), rel_setup + n)
               for n in ("install.py", "render.py", "scan-secrets.py")]
    payload_dir = os.path.join(setup, "payload")
    if os.path.isdir(payload_dir):
        targets += [(os.path.join(payload_dir, n), rel_setup + "payload/" + n)
                    for n in sorted(os.listdir(payload_dir)) if n.endswith(".py")]
    for path, rel in targets:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
        good, detail = compile_ok(text, os.path.basename(path))
        rep.check(good, "%s 语法可编译" % rel.split("/")[-1], detail, rel)

    # ── 8 快照回归 ─────────────────────────────────────────────
    rep.section("8) 与 expected/ 快照比对（回归检测）")
    os.makedirs(exp_dir, exist_ok=True)
    rel_exp = rel_setup + "expected/"
    drift, fresh = [], []
    for (name, tag), out in sorted(rendered.items()):
        snap = "%s.%s.rendered.txt" % (name[:-4], tag)
        dst = os.path.join(exp_dir, snap)
        if not os.path.isfile(dst):
            fresh.append(snap)
            continue
        with open(dst, "r", encoding="utf-8", newline="") as fh:
            old = fh.read()
        if old != out:
            drift.append(snap)

    if args.update_snapshots:
        written = 0
        for (name, tag), out in sorted(rendered.items()):
            snap = "%s.%s.rendered.txt" % (name[:-4], tag)
            with open(os.path.join(exp_dir, snap), "w",
                      encoding="utf-8", newline="") as fh:
                fh.write(out)
            written += 1
        rep.check(True, "已刷新 %d 个快照（--update-snapshots；其中 %d 个是新增）"
                  % (written, len(fresh)))
    else:
        rep.check(not fresh, "全部 %d 个模板都有对应快照" % len(rendered),
                  "缺快照：%s（模板是新增的？加 --update-snapshots 建基线）"
                  % ", ".join(fresh[:6]), rel_exp)
        rep.check(not drift, "全部快照与 expected/ 逐字节一致（无回归）",
                  "有漂移：%s —— 改了模板/render.py 却没刷基线，"
                  "或渲染结果被意外改动" % ", ".join(drift[:6]), rel_exp)

    # ── 9 快照孤儿 ─────────────────────────────────────────────
    rep.section("9) expected/ 里没有已删模板留下的孤儿快照")
    want = {"%s.%s.rendered.txt" % (n[:-4], t) for n in tpl_files for t, _ in fixtures}
    have = {f for f in os.listdir(exp_dir) if f.endswith(".rendered.txt")}
    orphans = sorted(have - want)
    rep.check(not orphans, "无孤儿快照", "已无对应模板：%s" % ", ".join(orphans[:8]),
              rel_exp)

    # ── 汇总 ───────────────────────────────────────────────────
    print()
    print("=" * 74)
    if rep.failures:
        print("FAIL ✗  %d 项失败" % len(rep.failures))
        print("（模板/快照这类问题不会自己报错：改了 render.py 忘了刷快照，"
              "产物就是旧的。）")
        print("=" * 74)
        return 1
    print("PASS ✓  %d 个模板 · %d 个快照 · 9 组判据全部通过"
          % (len(tpl_files), len(want)))
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
