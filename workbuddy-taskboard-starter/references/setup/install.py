#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workbuddy-taskboard-starter · 一键安装器

在 Windows 上把一套本地任务面板装进 WorkBuddy：

  1. 探测 Node / Python / Tailscale
  2. 从上游按**固定 tag** 下载 tarball（codeload，免 API 限流）
  3. 剥掉顶层目录、落位到 <prefix>\\apps\\dashi-taskboard
  4. npm ci + npm run build:web   ← 上游 tag 里没有 dist/，不构建就没有界面
  5. 渲染生成外壳脚本（把探测到的绝对路径填进模板）
  6. 写 HKCU\\...\\Run 自启项（免提权）
  7. 可选：装 mail-bridge、装上游的 manage-taskboard skill
  8. 落 manifest，跑自检并解释退出码

安全性说明（本脚本会做这几件「有副作用」的事，仅此而已）：
  * 往 <prefix> 下写文件
  * 写一个 HKCU 注册表值（当前用户，免提权）
  * 执行 npm ci / npm run build:web（在上游代码目录内）
  * 执行一次只读自检脚本
不下载并执行任何安装包，不写系统目录，不动 PATH。

只支持 Windows。纯标准库，无 pip 依赖。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, "templates")
PAYLOAD_DIR = os.path.join(HERE, "payload")
sys.path.insert(0, HERE)

import render as R  # noqa: E402

DEFAULT_REPO = "chuspeeism/dashi-taskboard"
DEFAULT_REF = "v1.1.22"
MIN_NODE = (22, 5)
RUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
UA = "workbuddy-taskboard-starter-installer"

# 退出码
E_OK, E_USAGE, E_PLATFORM, E_NONODE, E_NODEVER = 0, 2, 3, 4, 5
E_DOWNLOAD, E_EXTRACT, E_NPMCI, E_BUILD, E_SELFCHECK, E_RUNKEY, E_PORTBUSY = 6, 7, 8, 9, 10, 11, 12
E_SERVFAIL = 13          # 装完起不来：配置写错了 / 端口被抢 / 上游行为变化

# 外壳文件：模板名 -> 目标文件名
SHELLS = {
    "start-taskboard.cmd.tpl": "start-taskboard.cmd",
    "taskctl.cmd.tpl": "taskctl.cmd",
    "status-taskboard.cmd.tpl": "status-taskboard.cmd",
    "stop-taskboard.cmd.tpl": "stop-taskboard.cmd",
    "selfcheck-taskboard.cmd.tpl": "selfcheck-taskboard.cmd",
    "autostart-taskboard.pyw.tpl": "autostart-taskboard.pyw",
    "selfcheck-taskboard.py.tpl": "selfcheck-taskboard.py",
}
# mail-bridge：模板 -> 目标
MAIL_SHELLS = {
    "mail-bridge-common.py.tpl": "common.py",
    "mail-bridge-poll-once.cmd.tpl": "poll-once.cmd",
    "mail-bridge-send-daily.cmd.tpl": "send-daily.cmd",
    "mail-bridge-send-test.cmd.tpl": "send-test.cmd",
    "mail-bridge-start-poller.cmd.tpl": "start-poller.cmd",
}
MAIL_PAYLOAD = {
    "mail-bridge-mailer.py": "mailer.py",
    "mail-bridge-poller.py": "poller.py",
    "mail-bridge-test_parse.py": "test_parse.py",
}
MANAGE_SKILL_READ_WHEN = [
    "管理任务看板 / taskboard",
    "用 taskctl 查询或更新任务、项目、评论",
    "认领任务、同步任务状态",
    "维护任务之间的父子/依赖关系",
]

RESULTS = {}


def log(message):
    print(message, flush=True)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- 1) 前置探测

def detect_node(prefix):
    """先找 WorkBuddy 自带的托管 node（取版本号最大者），再退回 PATH。

    注意：托管运行时位于 **WorkBuddy 安装根**下，不一定在 `--prefix` 下 ——
    `--prefix` 说的是「应用装到哪」，两者只在默认情况下重合。隔离安装
    （`--prefix <某临时目录>`）时若只找 prefix，会静默退化成 PATH 查找；
    在一台 node 只在托管目录、不在 PATH 的机器上就直接装不上了。
    所以两处都找，且优先托管。
    """
    versions_dirs = []
    for root in (prefix, os.path.join(os.path.expanduser("~"), ".workbuddy")):
        versions_dir = os.path.join(root, "binaries", "node", "versions")
        if os.path.isdir(versions_dir) and versions_dir not in versions_dirs:
            versions_dirs.append(versions_dir)
    candidates = []
    for versions_dir in versions_dirs:
        for name in os.listdir(versions_dir):
            exe = os.path.join(versions_dir, name, "node.exe")
            if os.path.isfile(exe):
                candidates.append((name, exe))
    if candidates:
        candidates.sort(key=lambda item: [int(x) for x in re.findall(r"\d+", item[0])[:3]] or [0])
        return candidates[-1][1], "managed:%s" % candidates[-1][0]
    found = shutil.which("node")
    if found:
        return found, "PATH"
    return None, None


def node_version(node_exe):
    try:
        proc = subprocess.run([node_exe, "--version"], capture_output=True,
                              text=True, timeout=30)
    except Exception:
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", proc.stdout or "")
    if not match:
        return None
    return tuple(int(x) for x in match.groups())


def npm_cli(node_exe):
    """找与 node 同级的 npm-cli.js（避免依赖 PATH 上的 npm.cmd）。"""
    for rel in (os.path.join("node_modules", "npm", "bin", "npm-cli.js"),
                os.path.join("lib", "node_modules", "npm", "bin", "npm-cli.js")):
        candidate = os.path.join(os.path.dirname(node_exe), rel)
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("npm")
    return found


def detect_python():
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return exe, (pythonw if os.path.isfile(pythonw) else None)


# 上游 server/app.mjs 的 parseTrustedOrigins 用 `new URL(v)` 解析每个值，要求：
#   协议必须是 https:；不能有凭证 / 路径 / 查询串 / 片段；不能含通配符 *。
# 裸主机名（tailscale 的 DNSName 就是裸的）会让 new URL() **直接抛异常**：
#   Error: CODEX_TASKBOARD_TRUSTED_ORIGINS must contain valid HTTPS origins
# 后果是 node 在启动阶段就退出（连 TCP 都不通），而且**只在装了 tailscale 的机器上出现**
# ——恰好是最想要远程访问的那批用户。所以这里必须补上 scheme 并严格校验。
_ORIGIN_RE = re.compile(
    r"^https://[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)+"
    r"(:\d{1,5})?$"          # 端口合法（上游 new URL 接受），路径/查询/片段不合法
)


def as_https_origin(host):
    """把裸主机名规范成上游要求的 exact HTTPS origin；不合法返回空串。

    宁可不写（退化成「未启用远程访问」的安全默认）也不能写坏 —— 写坏会让服务起不来。
    """
    host = (host or "").strip().strip(".").lower()
    if not host or "*" in host or "/" in host:
        return ""
    origin = "https://" + host
    return origin if _ORIGIN_RE.match(origin) else ""


def origin_list_problem(value):
    """校验（可能是逗号分隔的）origin 列表，返回问题描述；没问题返回 None。

    与自检脚本里的同名检查保持一致 —— 两边必须同规则，否则会出现
    「安装器说没问题、自检说有问题」这种更让人迷惑的状态。
    """
    items = [part.strip() for part in (value or "").split(",")]
    if not items or any(not item for item in items):
        return "有空项（逗号前后漏了值）"
    for item in items:
        lowered = item.lower()
        if "*" in item:
            return "含通配符 *（上游只接受精确 origin）：%s" % item
        if not lowered.startswith("https://"):
            return "缺 https:// 前缀：%s" % item
        if not _ORIGIN_RE.match(lowered):
            return "不是合法的 exact origin（不能含路径 / 查询串 / 片段 / 凭证）：%s" % item
    return None


def detect_tailscale_origin():
    """自动读出本机 tailnet 的 HTTPS origin。读不到就返回空（不向用户提问）。"""
    candidates = [r"C:\Program Files\Tailscale\tailscale.exe", shutil.which("tailscale")]
    exe = next((p for p in candidates if p and os.path.isfile(p)), None)
    if not exe:
        return "", "未安装 tailscale"
    try:
        proc = subprocess.run([exe, "status", "--json"], capture_output=True,
                              text=True, timeout=20)
        data = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return "", "读取 tailscale 状态失败: %r" % (exc,)
    name = (data.get("Self") or {}).get("DNSName") or ""
    origin = as_https_origin(name)
    if origin:
        return origin, "tailscale Self.DNSName"
    for cert in (data.get("CertDomains") or []):
        origin = as_https_origin(cert)
        if origin:
            return origin, "tailscale CertDomains"
    if name or (data.get("CertDomains") or []):
        return "", "tailscale 返回的域名不合法，已忽略（不写坏配置）"
    return "", "tailscale 已装但拿不到 DNSName（HTTPS 证书可能未开启）"


def port_listening(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- 2) 下载与解包

def download_tarball(repo, ref, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    url = "https://codeload.github.com/%s/tar.gz/refs/tags/%s" % (repo, ref)
    target = os.path.join(dest_dir, "upstream.tar.gz")
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=180) as response:
        with open(target, "wb") as handle:
            shutil.copyfileobj(response, handle)
    with open(target, "rb") as handle:
        if handle.read(2) != b"\x1f\x8b":
            raise RuntimeError("下载到的不是 gzip（tag 可能不存在）: %s" % url)
    return target, url


def _safe_member(name):
    norm = os.path.normpath(name.replace("\\", "/"))
    if norm.startswith("..") or os.path.isabs(norm):
        return None
    return norm


def extract_upstream(tarball, dest_dir):
    """剥掉第一层目录后解包。返回 (顶层目录名, 解出的相对路径集合)。"""
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    extracted = set()
    with tarfile.open(tarball, "r:gz") as tar:
        members = tar.getmembers()
        tops = {m.name.split("/", 1)[0] for m in members if m.name and m.name != "."}
        if len(tops) != 1:
            raise RuntimeError("tarball 顶层不是单一目录：%r" % sorted(tops)[:5])
        top = tops.pop()
        for member in members:
            parts = member.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            rel = _safe_member(parts[1])
            if rel is None:
                raise RuntimeError("tarball 里有不安全的路径：%r" % member.name)
            member.name = rel
            try:
                tar.extract(member, path=dest_dir, filter="data")
            except TypeError:          # Python < 3.12 没有 filter 参数
                tar.extract(member, path=dest_dir)
            if member.isfile():
                extracted.add(rel.replace("/", os.sep))
    return top, extracted


PRESERVE_PREFIXES = ("node_modules" + os.sep, "dist" + os.sep, "logs" + os.sep,
                     ".git" + os.sep, "__pycache__" + os.sep)
PRESERVE_NAMES = set(SHELLS.values()) | {".starter-manifest.json", "server.log",
                                        "_il.err"}


def merge_upstream(src_dir, dst_dir):
    """升级用：把上游文件覆盖过去，但保留依赖/构建产物/日志/全部外壳脚本。"""
    os.makedirs(dst_dir, exist_ok=True)
    copied = skipped = 0
    for root, _dirs, files in os.walk(src_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, src_dir)
            if rel.startswith(PRESERVE_PREFIXES) or rel in PRESERVE_NAMES:
                skipped += 1
                continue
            target = os.path.join(dst_dir, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(full, target)
            copied += 1
    return copied, skipped


# ---------------------------------------------------------------- 3) 构建

def run_logged(argv, cwd, log_path):
    """把输出写进日志文件。**不接管道** —— 管道写端无人读会让子进程假死。"""
    with open(log_path, "ab") as handle:
        handle.write(("\n$ %s\n" % " ".join(argv)).encode("utf-8", "replace"))
        handle.flush()
        proc = subprocess.run(argv, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT)
    return proc.returncode


def build_frontend(app_dir, node_exe, log_path):
    npm = npm_cli(node_exe)
    if not npm:
        raise RuntimeError("找不到 npm（既没有 node_modules\\npm\\bin\\npm-cli.js，PATH 上也没有 npm）")
    if npm.lower().endswith((".js", ".mjs")):
        base = [node_exe, npm]
    else:
        base = [npm]
    code = run_logged(base + ["ci", "--no-audit", "--no-fund"], app_dir, log_path)
    if code != 0:
        return E_NPMCI, "npm ci 失败（退出码 %d）" % code
    code = run_logged(base + ["run", "build:web"], app_dir, log_path)
    if code != 0:
        return E_BUILD, "npm run build:web 失败（退出码 %d）" % code
    index_html = os.path.join(app_dir, "dist", "web", "index.html")
    if not os.path.isfile(index_html):
        return E_BUILD, "构建结束但 %s 不存在" % index_html
    return E_OK, "构建成功"


# ---------------------------------------------------------------- 3b) 服务冒烟

def tail_lines(path, count=14):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return "\n".join(handle.read().splitlines()[-count:])
    except Exception as exc:
        return "(读日志失败: %r)" % (exc,)


def http_health(port, timeout=3.0):
    """剥代理探测回环。本机常设 HTTP_PROXY，裸 urlopen 会拿到假 502。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:%d/health" % port, timeout=timeout) as resp:
            return resp.status, resp.read(200).decode("utf-8", "replace")
    except Exception as exc:
        return None, repr(exc)


def smoke_service(app_dir, node_exe, data_dir, port, origin, log_path):
    """真的把服务起一次、打一发 /health，然后无论成败都关掉。

    为什么必须有这一步：**只在 node 解析配置时才暴露的错误，不真起一次是发现不了的**。
    本项目实际踩到的例子是一处 origin 少了 `https://` 前缀 —— 安装器把外壳写得漂漂亮亮、
    Run 值也写对了，自检因为「两处副本一致」还报 OK，于是一个**永远起不来**的安装被
    报成成功；用户要到下次登录才看到 502，而那时排查成本高得多。
    冒烟把这类问题提前到安装当场暴露，并直接甩出 node 的报错原文。

    返回 (code, message, tail)。
    """
    entry = os.path.join(app_dir, "server", "index.mjs")
    if not os.path.isfile(entry):
        return E_SERVFAIL, "找不到 %s" % entry, ""

    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["CODEX_TASKBOARD_HOST"] = "127.0.0.1"
    env["CODEX_TASKBOARD_PORT"] = str(port)
    env["CODEX_TASKBOARD_DATA_DIR"] = data_dir
    if origin:
        env["CODEX_TASKBOARD_TRUSTED_ORIGINS"] = origin
    else:
        # 关键：**必须**删掉而不是置空 —— 被设置但为空值会让上游直接抛异常。
        env.pop("CODEX_TASKBOARD_TRUSTED_ORIGINS", None)

    creation = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
    with open(log_path, "ab") as sink:
        sink.write(("\n$ (smoke) %s %s  host=127.0.0.1 port=%d origin=%s\n"
                    % (node_exe, entry, port, origin or "<unset>")).encode("utf-8", "replace"))
        sink.flush()
        proc = subprocess.Popen([node_exe, entry], cwd=app_dir, env=env,
                                stdout=sink, stderr=subprocess.STDOUT,
                                creationflags=creation)
    try:
        deadline = time.time() + 45
        while time.time() < deadline:
            status, _body = http_health(port)
            if status == 200:
                return E_OK, "服务起来了，/health -> 200", ""
            if proc.poll() is not None:
                return (E_SERVFAIL,
                        "服务启动即退出（node 退出码 %s）" % proc.returncode,
                        tail_lines(log_path))
            time.sleep(0.6)
        return E_SERVFAIL, "等了 45 秒 /health 仍未返回 200", tail_lines(log_path)
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------- 4) 渲染外壳

def render_shells(app_dir, variables, manifest, force):
    os.makedirs(os.path.join(app_dir, "logs"), exist_ok=True)
    written, kept = [], []
    previous = (manifest.get("rendered_sha256") or {})
    for template, target_name in SHELLS.items():
        target = os.path.join(app_dir, target_name)
        if os.path.isfile(target) and not force:
            recorded = previous.get(target_name)
            if recorded is None:
                kept.append((target_name, "已存在（无历史哈希，保守保留）"))
                continue
            if sha256_file(target) != recorded:
                kept.append((target_name, "你改过它，保留"))
                continue
        R.render_file(os.path.join(TEMPLATE_DIR, template), target, variables)
        written.append(target_name)
    return written, kept


def manual_hint(app_dir, port, origin):
    cmd = os.path.join(app_dir, "start-taskboard.cmd")
    lines = []
    if not os.path.isfile(cmd):
        return []
    text, _ = R.read_text(cmd)
    if "CODEX_TASKBOARD_PORT=%d" % port not in text:
        lines.append('  端口要改成 %d：把 start-taskboard.cmd 里的 CODEX_TASKBOARD_PORT / URL 一起改' % port)
    if origin and origin not in text:
        lines.append('  origin 要补上 %s：在 start-taskboard.cmd 里加一行' % origin)
        lines.append('      set "CODEX_TASKBOARD_TRUSTED_ORIGINS=%s"' % origin)
    return lines


# ---------------------------------------------------------------- 5) 自启

def write_run_value(name, value):
    import winreg
    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    finally:
        winreg.CloseKey(key)


def read_run_value(name):
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)
    except OSError:
        return None
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None
    finally:
        winreg.CloseKey(key)


# ---------------------------------------------------------------- 6) 可选组件

TRUSTED_ORIGIN_LINE_RE = re.compile(r"(?m)^([ \t]*TRUSTED_ORIGIN[ \t]*=[ \t]*)(['\"])(.*?)(\2)([ \t]*)$")


def reconcile_mail_origin(mail_dir, origin):
    """让 mail-bridge 的 TRUSTED_ORIGIN 与外壳脚本里的值保持一致。

    为什么需要单独做这件事：`install_mail_bridge` 出于「别覆盖用户改过的文件」的考虑，
    对已存在的文件一律跳过 —— 好处是不破坏用户定制，坏处是**配置漂移永远修不好**。
    实测踩到：安装器早期版本写过缺 `https://` 的裸域名，修好探测逻辑后重跑，
    start-taskboard.cmd 被改对了，common.py 却仍是旧值 → 两处不一致 → 症状是"手机 403"。

    改写策略（保守）：**只碰那一行**，且**只在原值本身非法时**才自动改。
    原值合法但不等于当前 origin，很可能是用户有意为之（比如另开了域名），此时只告警。

    返回 (action, detail)，action ∈ {"skipped","none","repaired","warned"}。
    """
    path = os.path.join(mail_dir, "common.py")
    if not os.path.isfile(path):
        return "skipped", "common.py 不存在"
    try:
        text, eol = R.read_text(path)
    except Exception as exc:
        return "skipped", "读取失败：%r" % (exc,)
    match = TRUSTED_ORIGIN_LINE_RE.search(text)
    if not match:
        return "skipped", "找不到模块级 TRUSTED_ORIGIN 赋值（用户可能改成了别处）"
    current = match.group(3)
    if current == origin:
        return "none", current
    problem = origin_list_problem(current)
    if problem:
        patched = "%s%s%s%s%s" % (match.group(1), match.group(2), origin,
                                  match.group(4), match.group(5))
        R.write_text(path, text[:match.start()] + patched + text[match.end():], eol)
        return "repaired", "原值 %r 非法（%s）→ 已改为 %r" % (current, problem, origin)
    return "warned", "文件里是 %r，外壳里是 %r" % (current, origin)


def install_mail_bridge(prefix, variables, force):
    mail_dir = os.path.join(prefix, "apps", "mail-bridge")
    os.makedirs(mail_dir, exist_ok=True)
    for template, target_name in MAIL_SHELLS.items():
        target = os.path.join(mail_dir, target_name)
        if os.path.isfile(target) and not force:
            continue
        R.render_file(os.path.join(TEMPLATE_DIR, template), target, variables)
    for payload_name, target_name in MAIL_PAYLOAD.items():
        target = os.path.join(mail_dir, target_name)
        if os.path.isfile(target) and not force:
            continue
        shutil.copy2(os.path.join(PAYLOAD_DIR, payload_name), target)
    example = os.path.join(mail_dir, "config.example.json")
    shutil.copy2(os.path.join(TEMPLATE_DIR, "mail-bridge-config.example.json"), example)
    # 绝不自动创建 config.json（里面是授权码，得用户自己填）
    return mail_dir, os.path.join(mail_dir, "config.json")


def convert_frontmatter(text):
    """上游是 Claude/Codex 格式（只有 name + description）。
    WorkBuddy 需要 name / version / description / read_when。"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not match:
        return text
    blocking = match.group(1)
    body = text[match.end():]
    name = ""
    description = ""
    have_version = "version:" in blocking
    have_read_when = "read_when:" in blocking
    for line in blocking.splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("description:"):
            description = line.split(":", 1)[1].strip()
    if not name:
        return text
    head = ["---", "name: %s" % name]
    if not have_version:
        head.append('version: "1.0.0"')
    if description:
        head.append("description: %s" % description)
    if not have_read_when:
        head.append("read_when:")
        head.extend("  - %s" % item for item in MANAGE_SKILL_READ_WHEN)
    tail = [line for line in blocking.splitlines()
            if line.startswith(("version:", "read_when:", "  - "))]
    if tail and not have_version and not have_read_when:
        pass
    head.append("---")
    return "\n".join(head) + "\n" + body


def install_manage_skill(upstream_dir, skills_dir):
    src = os.path.join(upstream_dir, "skills", "manage-taskboard")
    if not os.path.isdir(src):
        return None, "上游 tarball 里没有 skills\\manage-taskboard"
    dst = os.path.join(skills_dir, "manage-taskboard")
    os.makedirs(dst, exist_ok=True)
    skill_md = os.path.join(src, "SKILL.md")
    if not os.path.isfile(skill_md):
        return None, "上游 skill 里没有 SKILL.md"
    text, _ = R.read_text(skill_md)
    R.write_text(os.path.join(dst, "SKILL.md"), convert_frontmatter(text), "\n")
    refs_src = os.path.join(src, "references")
    if os.path.isdir(refs_src):
        shutil.copytree(refs_src, os.path.join(dst, "references"), dirs_exist_ok=True)
    # agents/openai.yaml 是 Codex/OpenAI 专用，WorkBuddy 不读，跳过
    return dst, None


# ---------------------------------------------------------------- 7) 自检

SELFCHECK_MEANING = {
    0: "通过 —— 一切正常",
    1: "执行过，但服务不健康",
    2: "本次登录未执行自启（**首次安装后属正常**，注销重登一次再看）",
    3: "无法判定（时间戳对不上，自检会说明原因）",
    10: "配置或文件有问题（看上面报出的失败项）",
}


def run_selfcheck(app_dir, python_exe):
    script = os.path.join(app_dir, "selfcheck-taskboard.py")
    if not os.path.isfile(script):
        return None, "找不到 selfcheck-taskboard.py"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([python_exe, script], cwd=app_dir, env=env,
                          capture_output=True, text=True, errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ---------------------------------------------------------------- main

def parse_args(argv):
    home = os.path.expanduser("~")
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="把本地任务面板（上游 dashi-taskboard + 本项目外壳）装进 WorkBuddy。仅支持 Windows。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 成功 / 2 参数 / 3 非Windows / 4 无node / 5 node太低 / "
               "6 下载 / 7 解包 / 8 npm ci / 9 构建 / 10 自检 / 11 写自启 / 12 端口占用 / "
               "13 服务起不来")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="上游 owner/name")
    parser.add_argument("--ref", default=DEFAULT_REF, help="上游 tag / 分支 / sha（默认 %s）" % DEFAULT_REF)
    parser.add_argument("--prefix", default=os.path.join(home, ".workbuddy"),
                        help="安装根目录（隔离旋钮）。默认 %%USERPROFILE%%\\.workbuddy")
    parser.add_argument("--app-dir", default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--port", type=int, default=47823)
    parser.add_argument("--node-exe", default=None)
    parser.add_argument("--python-exe", default=None)
    parser.add_argument("--origin", default=None,
                        help='远程访问的 HTTPS origin。默认自动探测；传 "" 强制关闭')
    parser.add_argument("--run-value-name", default="Taskboard",
                        help="自启注册表值名（隔离验证时改成别的，如 TaskboardVerify）")
    parser.add_argument("--mail-bridge", dest="mail_bridge", action="store_true", help="一并安装邮件桥")
    parser.add_argument("--no-mail-bridge", dest="mail_bridge", action="store_false")
    parser.set_defaults(mail_bridge=False)
    parser.add_argument("--install-skill", dest="install_skill", action="store_true",
                        help="把上游的 manage-taskboard skill 装进 skills/（默认开）")
    parser.add_argument("--no-install-skill", dest="install_skill", action="store_false")
    parser.set_defaults(install_skill=True)
    parser.add_argument("--no-autostart", dest="autostart", action="store_false")
    parser.set_defaults(autostart=True)
    parser.add_argument("--no-build", dest="build", action="store_false",
                        help="跳过 npm ci / build:web（dist 已存在时）")
    parser.set_defaults(build=True)
    parser.add_argument("--no-smoke", dest="smoke", action="store_false",
                        help="跳过「起一次服务验证配置」这一步（默认会做）")
    parser.set_defaults(smoke=True)
    parser.add_argument("--force-shell", action="store_true",
                        help="允许覆盖已存在的 start-taskboard.cmd 等外壳（默认绝不覆盖）")
    parser.add_argument("--force", action="store_true",
                        help="允许覆盖 app-dir 内已存在的上游文件")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", dest="as_json", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    started = time.time()

    if os.name != "nt":
        log("本安装器只支持 Windows（自启依赖注册表 Run 键、外壳是 .cmd、"
            "自检用 ctypes/winreg）。为免留下半残的安装，这里直接退出。")
        return E_PLATFORM

    prefix = os.path.abspath(os.path.expanduser(args.prefix))
    app_dir = os.path.abspath(args.app_dir or os.path.join(prefix, "apps", "dashi-taskboard"))
    data_dir = os.path.abspath(args.data_dir or os.path.join(prefix, "taskboard-data"))
    skills_dir = os.path.join(prefix, "skills")
    tmp_dir = os.path.join(prefix, "tmp")
    upstream_dir = os.path.join(tmp_dir, "upstream")
    log_path = os.path.join(tmp_dir, "install.log")

    log("=== workbuddy-taskboard-starter 安装器 ===")
    log("  prefix   : %s" % prefix)
    log("  app_dir  : %s" % app_dir)
    log("  data_dir : %s" % data_dir)
    log("  上游     : %s @ %s" % (args.repo, args.ref))

    manifest_path = os.path.join(app_dir, ".starter-manifest.json")
    manifest = {}
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except Exception:
            manifest = {}

    # ---- 探测 ----------------------------------------------------------------
    node_exe = os.path.abspath(args.node_exe) if args.node_exe else None
    node_src = "显式指定"
    if not node_exe:
        node_exe, node_src = detect_node(prefix)
    if not node_exe:
        log("!! 找不到 node.exe（既没在 %s 下发现托管版本，PATH 上也没有）" % os.path.join(prefix, "binaries"))
        return E_NONODE
    version = node_version(node_exe)
    log("  node     : %s (%s) -> %s" % (node_exe, node_src, ".".join(map(str, version)) if version else "未知"))
    if version and version[:2] < MIN_NODE:
        log("!! Node 版本过低：需要 >= %d.%d，当前 %s" % (MIN_NODE[0], MIN_NODE[1], ".".join(map(str, version))))
        return E_NODEVER

    python_exe = os.path.abspath(args.python_exe) if args.python_exe else sys.executable
    _, pythonw_exe = detect_python()
    if not pythonw_exe:
        log("!! 在 %s 旁边找不到 pythonw.exe（自启需要它来无窗口启动）" % os.path.dirname(python_exe))
        return E_USAGE
    log("  python   : %s" % python_exe)
    log("  pythonw  : %s" % pythonw_exe)

    if args.origin is None:
        origin, origin_src = detect_tailscale_origin()
        log("  origin   : %s (%s)" % (origin or "<未启用远程访问>", origin_src))
    else:
        raw = args.origin.strip()
        if not raw:
            origin = ""
        elif raw.lower().startswith("https://"):
            origin = as_https_origin(raw[len("https://"):])
        else:
            # 容错：允许用户只给裸域名（例如从 tailscale status 里复制过来的）
            origin = as_https_origin(raw)
        if raw and not origin:
            log("!! --origin %r 不是合法的 exact HTTPS origin。" % raw)
            log("   上游要求：必须带 https:// 前缀，不能含路径 / 查询串 / 片段 / 凭证 / 通配符。")
            log("   正确形如：https://<机器名>.<tailnet>.ts.net")
            return E_USAGE
        log("  origin   : %s (显式指定)" % (origin or "<未启用远程访问>"))

    if port_listening(args.port):
        log("!! 端口 %d 已被占用。本安装器**不会**去杀那个进程 —— 请换一个端口：--port <别的>" % args.port)
        return E_PORTBUSY

    if args.dry_run:
        log("\n--dry-run：到此为止，未落盘。")
        return E_OK

    # ---- 目录 ----------------------------------------------------------------
    for directory in (prefix, app_dir, data_dir, skills_dir, tmp_dir):
        os.makedirs(directory, exist_ok=True)

    fresh = not os.path.isfile(os.path.join(app_dir, "server", "index.mjs"))
    same_ref = manifest.get("ref") == args.ref and not fresh
    mode = "首次安装" if fresh else ("修复/补装" if same_ref else "升级")
    log("\n=== 阶段 1/6：获取上游代码（%s）===" % mode)

    top = None
    if same_ref and not args.force:
        log("  已有同版本安装，跳过下载（要强制重拉就加 --force）")
        upstream_dir = None
    else:
        try:
            tarball, url = download_tarball(args.repo, args.ref, tmp_dir)
            log("  已下载：%s" % url)
        except Exception as exc:
            log("!! 下载失败：%r" % (exc,))
            return E_DOWNLOAD
        try:
            top, extracted = extract_upstream(tarball, upstream_dir)
            log("  已解包：顶层目录 %s，%d 个文件" % (top, len(extracted)))
        except Exception as exc:
            log("!! 解包失败：%r" % (exc,))
            return E_EXTRACT
        if fresh or not os.path.isfile(os.path.join(app_dir, "server", "index.mjs")):
            os.makedirs(app_dir, exist_ok=True)
            copied, skipped = merge_upstream(upstream_dir, app_dir)
            log("  落位 %s：写入 %d 个文件" % (app_dir, copied))
        else:
            copied, skipped = merge_upstream(upstream_dir, app_dir)
            log("  升级：覆盖 %d 个上游文件，保留 %d 个本地文件（依赖/构建产物/外壳）" % (copied, skipped))

    pkg = os.path.join(app_dir, "package.json")
    if not os.path.isfile(os.path.join(app_dir, "server", "index.mjs")):
        log("!! 落位后仍找不到 server\\index.mjs，目录结构不符预期")
        return E_EXTRACT
    if os.path.isfile(pkg):
        try:
            with open(pkg, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
            log("  上游包信息：%s@%s" % (meta.get("name"), meta.get("version")))
        except Exception:
            pass

    # ---- 构建 ----------------------------------------------------------------
    log("\n=== 阶段 2/6：构建前端（上游 tag 里没有 dist/，必须自建）===")
    dist_index = os.path.join(app_dir, "dist", "web", "index.html")
    if not args.build:
        log("  --no-build：跳过")
    elif os.path.isfile(dist_index) and same_ref:
        log("  dist\\web\\index.html 已存在，跳过构建")
    else:
        log("  正在跑 npm ci + build:web（可能要几分钟），日志：%s" % log_path)
        code, message = build_frontend(app_dir, node_exe, log_path)
        log("  %s" % message)
        if code != E_OK:
            log("  详见日志：%s" % log_path)
            return code

    # ---- 渲染外壳 ------------------------------------------------------------
    log("\n=== 阶段 3/6：生成外壳脚本 ===")
    variables = R.build_variables(node_exe, python_exe, pythonw_exe,
                                  app_dir, data_dir, args.port, origin,
                                  args.run_value_name)
    written, kept = render_shells(app_dir, variables, manifest, args.force_shell)
    for name in written:
        log("  写入 %s" % name)
    for name, why in kept:
        log("  保留 %s（%s）" % (name, why))
    for line in manual_hint(app_dir, args.port, origin):
        log(line)

    # ---- 服务冒烟（在写自启之前：配置根本起不来就不该装自启）------------------
    # 冒烟日志**必须**与 logs\autostart.log 分开。写进去会毁掉
    # 「banner 时间戳 ≥ 本次登录时间」这条自检判据 —— 那是判断"登录触发器到底跑没跑"
    # 的唯一硬证据，被噪声污染后整条验证链就失效了。
    smoke_log = os.path.join(tmp_dir, "smoke.log")
    log("\n=== 阶段 3b/6：起一次服务验证配置（随即关闭）===")
    if not args.smoke:
        log("  --no-smoke：跳过。装完请自己双击 start-taskboard.cmd 确认能起来。")
    else:
        code, message, tail = smoke_service(app_dir, node_exe, data_dir,
                                            args.port, origin, smoke_log)
        log("  %s" % message)
        if code != E_OK:
            if tail:
                log("  ---- node 输出（末尾）----")
                for line in tail.splitlines():
                    log("    " + line)
            log("  ---- 完整日志：%s ----" % smoke_log)
            log("")
            log("  安装已中止，**没有**写自启项 —— 免得留下一个每次登录都静默失败的启动项。")
            log("  先把上面的报错修掉再重跑；也可加 --origin 显式指定正确的 HTTPS origin。")
            return code

    # ---- 自启 ----------------------------------------------------------------
    log("\n=== 阶段 4/6：开机自启（HKCU Run 值）===")
    launcher = os.path.join(app_dir, "autostart-taskboard.pyw")
    if not args.autostart:
        log("  --no-autostart：跳过")
        run_value = None
    else:
        run_value = '"%s" "%s"' % (pythonw_exe, launcher)
        previous = read_run_value(args.run_value_name)
        try:
            write_run_value(args.run_value_name, run_value)
            log("  已写入 HKCU\\...\\Run\\%s" % args.run_value_name)
            log("    值：%s" % run_value)
            if previous and previous != run_value:
                log("    （旧值已记入 manifest 便于回滚）")
        except Exception as exc:
            log("!! 写注册表失败：%r" % (exc,))
            log('   可手工执行：reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" '
                '/v %s /t REG_SZ /d "\\"%s\\" \\"%s\\"" /f' % (args.run_value_name, pythonw_exe, launcher))
            return E_RUNKEY

    # ---- 可选组件 ------------------------------------------------------------
    log("\n=== 阶段 5/6：可选组件 ===")
    mail_dir = mail_config = None
    if args.mail_bridge:
        mail_dir, mail_config = install_mail_bridge(prefix, variables, args.force)
        log("  邮件桥已装到 %s" % mail_dir)
        log("  下一步：复制 config.example.json 为 config.json，填邮箱与 16 位授权码")
        log("  ⚠️ config.json 已被 .gitignore 排除，但**不要**外发或截图")
        action, detail = reconcile_mail_origin(mail_dir, origin)
        if action == "repaired":
            log("  已修正 common.py 的 TRUSTED_ORIGIN：%s" % detail)
        elif action == "warned":
            log("  ⚠️ 两处 trusted origin 不一致：%s" % detail)
            log("     → 值本身合法，可能是你有意为之。若并非本意，请把 common.py 里的")
            log("       TRUSTED_ORIGIN 改成 %r，否则手机访问会 403。" % origin)
        elif action == "skipped":
            log("  （未核对 TRUSTED_ORIGIN：%s）" % detail)
    else:
        log("  未装邮件桥（要装就加 --mail-bridge）")

    manage_skill = None
    if args.install_skill:
        if upstream_dir and os.path.isdir(upstream_dir):
            manage_skill, err = install_manage_skill(upstream_dir, skills_dir)
            log("  manage-taskboard skill：%s" % (manage_skill or ("跳过（%s）" % err)))
        else:
            log("  manage-taskboard skill：本次没下载 tarball，跳过")

    # ---- manifest + 自检 -----------------------------------------------------
    log("\n=== 阶段 6/6：落 manifest 并自检 ===")
    rendered = {}
    for target_name in SHELLS.values():
        target = os.path.join(app_dir, target_name)
        if os.path.isfile(target):
            rendered[target_name] = sha256_file(target)
    new_manifest = {
        "schema": 1,
        "repo": args.repo,
        "ref": args.ref,
        "app_dir": app_dir,
        "data_dir": data_dir,
        "port": args.port,
        "host": "127.0.0.1",
        "origin": origin,
        "node_exe": node_exe,
        "python_exe": python_exe,
        "pythonw_exe": pythonw_exe,
        "run_value_name": args.run_value_name,
        "run_value": run_value,
        "previous_run_value": (manifest.get("run_value") if args.autostart else None),
        "mail_bridge_dir": mail_dir,
        "mail_bridge_config": mail_config,
        "manage_skill_dir": manage_skill,
        "rendered_sha256": rendered,
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(new_manifest, handle, ensure_ascii=False, indent=2)
    log("  manifest：%s" % manifest_path)

    code, output = run_selfcheck(app_dir, python_exe)
    if code is None:
        log("  自检未能运行：%s" % output)
    else:
        log("  ---- 自检输出 ----")
        for line in (output or "").splitlines():
            log("  " + line)
        log("  ---- 退出码 %d：%s ----" % (code, SELFCHECK_MEANING.get(code, "未知")))

    # 只有 10（配置/文件问题）算安装失败：那是**安装器自己写出来的**不一致状态。
    # 1（执行过但不健康）/2（未执行）/3（无法判定）在首次安装后都属正常 ——
    # 登录触发器还没被触发过，不能因此判定安装失败。
    final = E_SELFCHECK if code == E_SELFCHECK else E_OK

    RESULTS.update({"exit": final, "app_dir": app_dir, "data_dir": data_dir,
                    "port": args.port, "origin": origin, "mode": mode,
                    "selfcheck_exit": code, "manifest": manifest_path,
                    "seconds": round(time.time() - started, 1)})

    if final != E_OK:
        log("\n=== 完成，但自检报出配置/文件问题（%.1fs）===" % (time.time() - started))
        log("上面【配置检查】里带 [FAIL] 的条目就是原因，逐条修掉再重跑本安装器。")
        log("（退出码 %d）" % final)
        return final

    log("\n=== 完成（%.1fs）===" % (time.time() - started))
    log("下一步：")
    log("  1. 双击 %s 看界面" % os.path.join(app_dir, "start-taskboard.cmd"))
    log("  2. 双击 %s 看自检结论" % os.path.join(app_dir, "selfcheck-taskboard.cmd"))
    log("  3. 注销 → 重新登录一次，再跑自检 —— 这是唯一能证明「登录触发点」有效的路径")
    if origin:
        log("  4. 手机访问：%s" % origin)
    else:
        log("  4. 想用手机访问？配好 Tailscale 后跑：tailscale serve --bg --https=443 http://127.0.0.1:%d" % args.port)

    if args.as_json:
        log("\n" + json.dumps(RESULTS, ensure_ascii=False, indent=2))
    return E_OK


if __name__ == "__main__":
    sys.exit(main())
