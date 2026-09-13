#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提交前密钥自查 —— 双闸中的第二道（第一道是 .gitignore）。

用法：
    python scan-secrets.py --root .              # 扫整个仓库
    python scan-secrets.py --files a.md b.py     # 只扫指定文件
    python scan-secrets.py --selftest            # 自检：证明这些规则不是空转

退出码：0 = 零命中（干净）；1 = 有命中（不要提交）。

设计取舍：
  * **不内置任何真实值。** 想「用已知授权码去比对」听起来省事，但那等于把密钥写进一个
    要公开的脚本里 —— 比原本要防的问题更糟。这里只做**形态匹配**：像不像密钥，
    而不是等不等于某个已知密钥。
  * **本机私有值一律不落字面量**：用户名/机器名运行时从环境变量推导；项目 id、
    专用编号前缀、课题关键词从 `scan-secrets.local.json`（已 gitignore）读。
    取不到就让对应规则退化为「永不匹配」，本文件因此可以公开且无需自我豁免。
  * 每条规则都配一个「放行」条件，专门放过文档里的占位写法
    （`<tailnet>.ts.net`、`你的QQ号@qq.com`、`%USERPROFILE%` …）。
"""
import argparse
import json
import os
import re
import sys

SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".venv", ".idea", ".vscode"}
SKIP_SUFFIXES = (".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".gz", ".woff", ".woff2")

# ---------------------------------------------------------------- 私有字面量
# ⚠️ **本文件是要公开的**，所以绝不能把「本机真实值」写成字面量 ——
# 用户名、机器名、tailnet 名、项目 id 一旦写进来，就等于把要防的东西放进了防线里。
# 两种取法，都让本文件保持干净：
#
#   1) 用户名 / 机器名 —— **运行时从环境变量推导**。无需硬编码，换台机器自动生效。
#   2) 项目 id、课题关键词等推导不出来的 —— 放在同目录的
#      `scan-secrets.local.json`（已被 .gitignore 排除），形如：
#          {"literals": ["<项目 id>", "<专用编号前缀>", "<课题关键词>"]}
#
# 两者都取不到时，对应规则退化为「永不匹配」；其余形态规则照常工作。
LOCAL_LITERALS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "scan-secrets.local.json")

NEVER_MATCH = r"(?!)"


def _env_value(*names, min_len=3):
    """按顺序取第一个够长的环境变量值（太短的没有区分度，容易误报）。"""
    for name in names:
        raw = (os.environ.get(name) or "").strip()
        if len(raw) >= min_len:
            return raw
    return ""


CUR_USER = _env_value("USERNAME", "USER", "LOGNAME")
CUR_HOST = _env_value("COMPUTERNAME", "HOSTNAME")


def _load_local_literals():
    """从（已 gitignore 的）本地文件读私有字面量；读不到就返回空列表。"""
    if not os.path.isfile(LOCAL_LITERALS_FILE):
        return []
    try:
        with open(LOCAL_LITERALS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return []
    values = data.get("literals") if isinstance(data, dict) else data
    if not isinstance(values, list):
        return []
    return [str(v) for v in values if str(v).strip()]


LOCAL_LITERALS = _load_local_literals()


def _literal_alt(values):
    """把字面量拼成一个 alternation。

    首尾是「词字符」的加 `\\b`：这样 `\\babc\\b` 不会误伤 `abcdef` 或 `xabc`。
    """
    if not values:
        return NEVER_MATCH
    parts = []
    for value in values:
        escaped = re.escape(value)
        if re.match(r"\w", value[0]) and re.search(r"\w$", value):
            escaped = r"\b" + escaped + r"\b"
        parts.append(escaped)
    return "|".join(parts)

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

    # 用户名从环境变量推导（见上方说明）—— 本文件里不出现真实值。
    ("personal-username",
     re.compile(re.escape(CUR_USER)) if CUR_USER else re.compile(NEVER_MATCH),
     None),

    # 放行占位与**测试夹具**用的假用户名（expected/ 快照里就用 demo）。
    # 这条规则要抓的是「源码里混进了真实用户名」，不是文档示例。
    ("absolute-user-path",
     re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}(?![<%$])([^\\\s\"']{2,})"),
     re.compile(r"(?i)^[A-Za-z]:\\{1,2}Users\\{1,2}(<|%|\$\{|demo|user|you|example|someone|your)")),

    # 项目 id、专用编号前缀、课题关键词 —— 推导不出来，从本地文件读（见上方说明）。
    ("local-private-literal",
     re.compile(_literal_alt(LOCAL_LITERALS)),
     None),

    ("private-automation-path",
     re.compile(r"\.workbuddy[\\/]memory[\\/]automations"),
     None),

    # 机器名同样从环境变量推导；不区分大小写（COMPUTERNAME 通常是大写）。
    ("hostname",
     re.compile(re.escape(CUR_HOST), re.I) if CUR_HOST else re.compile(NEVER_MATCH),
     None),

    # 值必须「像凭据」：无空白、无中日韩字符、长度 >= 8。
    # 这样能放过 "在QQ邮箱 设置→账号 处生成的16位授权码" 这类说明文字。
    ("secret-looking-literal",
     re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|authcode)\s*[:=]\s*"
                r"[\"']([A-Za-z0-9_\-./+=]{8,})[\"']"),
     re.compile(r"(?i)(your|<|xxx|placeholder|example|dummy|fake)")),
]

def selftest_samples():
    """故意样本。**只用假值** —— 真实值靠环境变量 / 本地文件在运行时注入。"""
    samples = [
        ("email", "contact " + "leak" + "@" + "qq" + ".com now"),
        ("authCode-literal-16", '"authCode": "' + "a1b2c3d4e5f6g7h8" + '"'),
        ("wecom-webhook-key",
         "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
         + "120d7245-aaaa-bbbb-cccc-ddeeaebb"),
        ("real-tailnet-fqdn", "https://" + "ex-machine" + ".demo-tailnet.ts.net"),
        ("absolute-user-path", "C:\\Users\\" + "realleak" + "\\.workbuddy\\apps"),
        ("private-automation-path",
         "apps\\mail-bridge\\.workbuddy\\memory\\automations\\x\\memory.md"),
        ("secret-looking-literal", 'apiKey = "' + "sk9f3j2k4l5m6n7p" + '"'),
    ]
    # 依赖运行环境的规则：能取到值才测，取不到就跳过（不算失败）
    if CUR_USER:
        samples.append(("personal-username", "C:\\Users\\" + CUR_USER + "\\x"))
    if CUR_HOST:
        samples.append(("hostname", "runner " + CUR_HOST + " here"))
    if LOCAL_LITERALS:
        samples.append(("local-private-literal", "prefix " + LOCAL_LITERALS[0] + " suffix"))
    return samples

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


# 本地私有字面量清单**本身必然含真实值**（它就是那份清单），必须豁免，
# 否则扫描器会把自己撞成 DIRTY。该文件已被 .gitignore 排除，不会进仓库。
# 注意：本脚本自己**不再**豁免 —— 它已不含任何真实值，把密钥粘进来是能被抓到的。
SKIP_BASENAMES = {"scan-secrets.local.json"}


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
    print("  （依赖环境的规则：用户名 %s / 机器名 %s / 本地字面量 %d 条）"
          % (CUR_USER or "未取到", CUR_HOST or "未取到", len(LOCAL_LITERALS)))
    fails = 0
    for expected_rule, sample in selftest_samples():
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
