#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提交前密钥自查 —— 双闸中的第二道（第一道是 .gitignore）。

用法：
    python scan-secrets.py --root .              # 扫整个仓库
    python scan-secrets.py --files a.md b.py     # 只扫指定文件
    python scan-secrets.py --selftest            # 自检：证明这些规则不是空转

退出码：0 = 零命中（干净）；1 = 有命中（不要提交）。

设计取舍：
  * **不内置任何真实密钥字面量。** 想「用已知授权码去比对」听起来省事，但那等于
    把密钥写进一个要公开的脚本里 —— 比原本要防的问题更糟。这里只做**形态匹配**：
    像不像密钥，而不是等不等于某个已知密钥。
  * 每条规则都配一个「放行」条件，专门放过文档里的占位写法
    （`<tailnet>.ts.net`、`你的QQ号@qq.com`、`%USERPROFILE%` …）。
"""
import argparse
import os
import re
import sys

SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".venv", ".idea", ".vscode"}
SKIP_SUFFIXES = (".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".gz", ".woff", ".woff2")
# 扫描器**自己**必须排除：它的规则定义里必然含 `T2D`、`HENG`、`my-tasks` 这类字面量，
# 那是「要找什么」的声明，不是泄露。所以本文件无法自证清白，只能显式豁免。
# 已知取舍：有人往本文件里粘贴真实密钥时不会被抓到。
SKIP_BASENAMES = {"scan-secrets.py"}

# (名称, 正则, 放行正则 或 None)
RULES = [
    ("email",
     re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
     re.compile(r"你的QQ号@qq\.com|noreply@|@users\.noreply\.github\.com|"
                r"user@example\.com|you@example\.com|example@|@example\.(com|org|net)")),

    ("authCode-literal-16",
     re.compile(r'"authCode"\s*:\s*"([A-Za-z0-9]{16})"'),
     None),

    ("wecom-webhook-key",
     re.compile(r"qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[0-9a-zA-Z\-]{8,}"),
     re.compile(r"key=(xxx|<|\.\.\.|YOUR|your)")),

    ("bare-key-param",
     re.compile(r"[?&]key=[0-9a-fA-F\-]{8,}"),
     re.compile(r"key=(xxx|<|\.\.\.|YOUR|your)")),

    # 真实的 tailnet FQDN 形如 <机器名>.<tailnet>.ts.net（3 段以上）。
    # 文档里的占位写法 `*.ts.net` / `<机器名>.<tailnet>.ts.net` 含 `*` 或 `<>`，
    # 不在字符类里，天然不会被匹配 —— 不需要额外放行规则。
    ("real-tailnet-fqdn",
     re.compile(r"[a-z0-9][a-z0-9\-]*\.[a-z0-9\-]+\.ts\.net"),
     None),

    ("personal-username",
     re.compile(r"\b17414\b"),
     None),

    # 放行占位与**测试夹具**用的假用户名（expected/ 快照里就用 demo）。
    # 这条规则要抓的是「源码里混进了真实用户名」，不是文档示例。
    ("absolute-user-path",
     re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}(?![<%$])([^\\\s\"']{2,})"),
     re.compile(r"(?i)^[A-Za-z]:\\{1,2}Users\\{1,2}(<|%|\$\{|demo|user|you|example|someone|your)")),

    ("personal-task-marker",
     re.compile(r"\bMYT-\d|\bT2D\b|\bGNN\b|\"my-tasks\""),
     None),

    ("private-automation-path",
     re.compile(r"\.workbuddy[\\/]memory[\\/]automations"),
     None),

    ("hostname",
     re.compile(r"\bHENG\b"),
     None),

    # 值必须「像凭据」：无空白、无中日韩字符、长度 >= 8。
    # 这样能放过 "在QQ邮箱 设置→账号 处生成的16位授权码" 这类说明文字。
    ("secret-looking-literal",
     re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|authcode)\s*[:=]\s*"
                r"[\"']([A-Za-z0-9_\-./+=]{8,})[\"']"),
     re.compile(r"(?i)(your|<|xxx|placeholder|example|dummy|fake)")),
]

SELFTEST_SAMPLES = [
    ("email", "contact " + "leak" + "@" + "qq" + ".com now"),
    ("authCode-literal-16", '"authCode": "' + "a1b2c3d4e5f6g7h8" + '"'),
    ("wecom-webhook-key",
     "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + "120d7245-aaaa-bbbb-cccc-ddeeaebb"),
    ("real-tailnet-fqdn", "https://" + "heng" + "." + "tailfe5822" + ".ts.net"),
    ("personal-username", "C:\\Users\\" + "174" + "14\\x"),
    ("absolute-user-path", "C:\\Users\\" + "174" + "14\\.workbuddy\\apps"),
    ("personal-task-marker", "task " + "T2" + "D" + "-MR done"),
    ("private-automation-path", "apps\\mail-bridge\\.workbuddy\\memory\\automations\\x\\memory.md"),
    ("hostname", "runner " + "HEN" + "G" + " here"),
    ("secret-looking-literal", 'apiKey = "' + "sk9f3j2k4l5m6n7p" + '"'),
]

SELFTEST_SHOULD_PASS = [
    "https://<机器名>.<tailnet>.ts.net",
    "`<tailnet>.ts.net`（占位写法）",
    '"email": "你的QQ号@qq.com"',
    "%USERPROFILE%\\.workbuddy\\apps\\dashi-taskboard",
    'set "APP_DIR={{APP_DIR}}"',
    '"authCode": "在QQ邮箱 设置→账号→IMAP/SMTP服务 处生成的16位授权码"',
    '"wecomWebhookUrl": ""',
    "user@example.com",
]


def should_skip(path):
    if os.path.basename(path) in SKIP_BASENAMES:
        return True
    parts = os.path.normpath(path).split(os.sep)
    if any(part in SKIP_DIRS for part in parts):
        return True
    return path.lower().endswith(SKIP_SUFFIXES)


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not should_skip(full):
                yield full


def scan_text(text):
    """返回 [(规则名, 命中的片段)]"""
    found = []
    for name, pattern, allow in RULES:
        for match in pattern.finditer(text):
            snippet = match.group(0)
            if allow and allow.search(snippet):
                continue
            found.append((name, snippet))
    return found


def selftest():
    print("=== 自检：规则必须能抓到「故意的」样本 ===")
    fails = 0
    for expected_rule, sample in SELFTEST_SAMPLES:
        hit_rules = {name for name, _ in scan_text(sample)}
        ok = expected_rule in hit_rules
        fails += 0 if ok else 1
        print("  %s  期望命中 %-24s 实得 %s" % ("OK  " if ok else "FAIL", expected_rule,
                                                sorted(hit_rules) or "无"))
    print()
    print("=== 自检：占位写法必须放行 ===")
    for sample in SELFTEST_SHOULD_PASS:
        hits = scan_text(sample)
        ok = not hits
        fails += 0 if ok else 1
        print("  %s  %-58s %s" % ("OK  " if ok else "FAIL", sample[:56], hits or ""))
    print()
    print("SELFTEST FAILS = %d" % fails)
    return 1 if fails else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scan-secrets.py")
    parser.add_argument("--root", default=".")
    parser.add_argument("--files", nargs="*", default=None)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.selftest:
        return selftest()

    if args.files:
        files = [f for f in args.files if os.path.isfile(f) and not should_skip(f)]
        label = "%d 个指定文件" % len(files)
    else:
        root = os.path.abspath(args.root)
        files = sorted(iter_files(root))
        label = "root=%s" % root

    counts = {name: 0 for name, _, _ in RULES}
    hits = []
    for path in files:
        try:
            with open(path, "rb") as handle:
                text = handle.read().decode("utf-8", "replace")
        except Exception as exc:
            print("  (跳过 %s: %r)" % (path, exc))
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, snippet in scan_text(line):
                counts[name] += 1
                hits.append((path, lineno, name, snippet))

    total = len(hits)
    if not args.quiet:
        print("scan-secrets.py  %s" % label)
        print("  scanned files : %d" % len(files))
        for name, _, _ in RULES:
            print("  rule %-26s hits %d" % (name, counts[name]))

    if hits:
        print()
        print("!! 命中明细（不要提交！）")
        for path, lineno, name, snippet in hits:
            rel = os.path.relpath(path)
            safe = snippet if name in ("personal-task-marker", "hostname") else snippet[:16] + "***"
            print("   %s:%d | %s | %s" % (rel, lineno, name, safe))

    print()
    print("TOTAL HITS = %d" % total)
    print("RESULT: %s" % ("CLEAN" if total == 0 else "DIRTY"))
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
