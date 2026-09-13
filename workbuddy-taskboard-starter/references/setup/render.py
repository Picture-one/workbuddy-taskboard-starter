# -*- coding: utf-8 -*-
"""极简模板渲染库 —— `{{VAR}}` 字面替换 + 渲染后断言无残留。

刻意做得很小：只有标准库，没有 Jinja，没有条件语法。
唯一需要「条件」的地方（远程访问被关闭时不能留空的 origin 赋值）
由 `trusted_origins_block()` 在渲染前生成整块文本解决。
"""
import os
import re

PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")
CMD_SUFFIXES = (".cmd", ".bat")

# 远程访问关闭时用的替身行。绝不能产出空赋值：服务端对「未设置」宽容，
# 对「设置但为空」会直接抛错，服务起不来。
ORIGIN_DISABLED_LINE = ("REM Remote access disabled: "
                        "CODEX_TASKBOARD_TRUSTED_ORIGINS is intentionally not set.")


def read_text(path):
    """返回 (归一化为 \\n 的文本, 原行尾)。顺手吃掉 UTF-8 BOM。"""
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", "replace")
    eol = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), eol


def write_text(path, text, eol):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    out = text.replace("\n", eol) if eol == "\r\n" else text
    with open(path, "wb") as handle:
        handle.write(out.encode("utf-8"))


def default_eol(path):
    """`.cmd` / `.bat` 一律 CRLF（cmd.exe 对 LF 容错性差），其余跟随模板。"""
    return "\r\n" if path.lower().endswith(CMD_SUFFIXES) else None


def render(text, variables):
    """替换全部占位符；有任何残留（拼错的键名、忘传的值）就抛错。"""
    def _sub(match):
        key = match.group(1)
        if key not in variables:
            return match.group(0)
        return str(variables[key])

    rendered = PLACEHOLDER_RE.sub(_sub, text)
    leftover = sorted(set(PLACEHOLDER_RE.findall(rendered)))
    if leftover:
        raise ValueError("模板里还有未替换的占位符：%s" % ", ".join(leftover))
    return rendered


def trusted_origins_block(origin):
    """生成 start-taskboard.cmd 里那段 origin 配置。

    有 origin：注释说明 + 一行 set。
    没 origin：**只有一行 REM**，绝不产生 `set "...="` 空赋值。
    """
    origin = (origin or "").strip()
    if not origin:
        return ORIGIN_DISABLED_LINE
    return "\n".join([
        "REM Tailscale serve proxies here from %s." % origin,
        "REM The server trusts only localhost/private ranges, not *.ts.net, so this",
        "REM origin must be allowlisted explicitly or phone requests get 403.",
        "REM Keep in sync with TRUSTED_ORIGIN in mail-bridge\\common.py.",
        'set "CODEX_TASKBOARD_TRUSTED_ORIGINS=%s"' % origin,
    ])


def render_file(src, dst, variables):
    text, eol = read_text(src)
    rendered = render(text, variables)
    write_text(dst, rendered, default_eol(dst) or eol)
    return dst


def build_variables(node_exe, python_exe, pythonw_exe, app_dir, data_dir,
                    port, origin, run_value_name):
    return {
        "NODE_EXE": node_exe,
        "PYTHON_EXE": python_exe,
        "PYTHONW_EXE": pythonw_exe,
        "APP_DIR": app_dir,
        "DATA_DIR": data_dir,
        "PORT": str(port),
        "TRUSTED_ORIGIN": (origin or "").strip(),
        "TRUSTED_ORIGINS_BLOCK": trusted_origins_block(origin),
        "RUN_VALUE_NAME": run_value_name,
    }
