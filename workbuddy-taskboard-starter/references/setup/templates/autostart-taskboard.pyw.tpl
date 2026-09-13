r"""Logon launcher for the local task board service.

Started by the HKCU Run value (see references/autostart.md). It runs under
pythonw.exe so no console window appears at logon, and it spawns the Node
server detached and windowless.

Why this exists instead of dropping start-taskboard.cmd into Startup:
a .cmd in Startup flashes a console window that must stay open for the
service to keep running. Here the service survives on its own.

Configuration is NOT duplicated in this file. Every CODEX_TASKBOARD_* value
is parsed out of start-taskboard.cmd, which stays the single source of
truth. That matters for CODEX_TASKBOARD_TRUSTED_ORIGINS: it must stay
identical to the TRUSTED_ORIGIN constant in apps\mail-bridge\common.py, and
any drift there makes the phone get HTTP 403. Two copies is already one too
many; a third would be worse.

Behaviour:
  * Writes a banner line first, so any run leaves a trace even if it dies early.
  * If the port is already listening (user started it by hand), do nothing.
  * Otherwise start the server and wait for /health before returning.
  * Records a machine-readable outcome in logs\last-logon.json.

Smoke-test helpers (not used at logon):
  pythonw autostart-taskboard.pyw --port 47899 --data-dir C:\Temp\tb-smoke
lets you exercise the whole launch path on a spare port without touching the
real instance. Smoke runs write logs\autostart-smoke.log and
logs\last-logon-smoke.json instead, so they never pollute the evidence you read
after a reboot.

Log: logs\autostart.log  (also receives the Node child's stdout/stderr, and is
rotated to .1 past 1 MiB)
"""

import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CMD_FILE = os.path.join(APP_DIR, "start-taskboard.cmd")
NODE_EXE = r"{{NODE_EXE}}"
LOG_DIR = os.path.join(APP_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "autostart.log")
STATE_FILE = os.path.join(LOG_DIR, "last-logon.json")

# Smoke-test runs (--port / --data-dir) write here instead of the real files, so
# they never pollute the evidence you read after a reboot.
SMOKE_LOG_FILE = os.path.join(LOG_DIR, "autostart-smoke.log")
SMOKE_STATE_FILE = os.path.join(LOG_DIR, "last-logon-smoke.json")

HOST = "127.0.0.1"
DEFAULT_PORT = {{PORT}}
CREATE_NO_WINDOW = 0x08000000

# The self-check script decides "did autostart run at this logon?" by finding this
# marker plus its timestamp. If you change the wording, change the regex in
# selfcheck-taskboard.py too -- a mismatch makes the check report a silent
# false negative ("did not run" when it actually did).
LOGON_MARKER = "=== logon run ==="
LOG_ROTATE_BYTES = 1024 * 1024  # 1 MiB; keep one generation (.1)

_LOG_FILE = LOG_FILE      # rewritten by main() on a smoke run
_STATE_FILE = STATE_FILE  # ditto
RUN = {}                  # machine-readable fields for this run, handed to write_state()


def log(message):
    """Append one timestamped line. Never raises; returns whether it was written.

    The failure path used to be a silent `pass`, which meant "the log is empty"
    could equally mean "the launcher never ran" or "it ran and died instantly".
    Now a write failure at least tries stderr -- but under pyw.exe at logon there
    IS no stderr, so getattr() is required.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(_LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write("[%s] %s\n" % (stamp, message))
        return True
    except Exception as exc:
        stream = getattr(sys, "stderr", None)
        if stream is not None:
            try:
                stream.write("[autostart] log() FAILED: %r (message=%r)\n" % (exc, message))
                stream.flush()
            except Exception:
                pass
        return False


def boot_epoch():
    """Local epoch seconds of the last boot. ctypes only -- no WMI, no PowerShell.

    GetTickCount64 is already 64-bit, but ctypes defaults the return type to
    c_int, which would wrap after ~24.8 days of uptime. Set restype explicitly.
    Fast Startup makes this read slightly EARLY (the kernel session is resumed),
    which only makes the check more permissive -- the logon-time check catches it.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return time.time() - kernel32.GetTickCount64() / 1000.0
    except Exception:
        return None


def write_state(**fields):
    """Best-effort machine-readable record. Never raises."""
    try:
        payload = {
            "schema": 1,
            "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "host": HOST,
        }
        boot = boot_epoch()
        if boot is not None:
            payload["boot_epoch"] = round(boot, 3)
            payload["boot_local"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot))
            payload["uptime_sec"] = round(time.time() - boot, 1)
        payload.update(fields)
        os.makedirs(LOG_DIR, exist_ok=True)
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, _STATE_FILE)
        return True
    except Exception:
        return False


def finish(code, result, note=None):
    """Single exit point: persist the outcome, then return the exit code."""
    RUN["result"] = result
    RUN["exit_code"] = code
    if note:
        RUN["note"] = note
    write_state(**RUN)
    return code


def rotate_log(max_bytes=LOG_ROTATE_BYTES):
    """Best-effort rotation, called before anything else in main().

    On Windows os.replace fails with PermissionError while another process still
    holds an open handle (the CRT opens without FILE_SHARE_DELETE), so this can
    only succeed when nobody holds the file -- i.e. exactly when a new instance is
    about to be spawned. There is no Unix-style "child keeps writing to the
    renamed inode" hazard here: renaming requires that no writer exists.
    """
    try:
        if os.path.isfile(_LOG_FILE) and os.path.getsize(_LOG_FILE) > max_bytes:
            os.replace(_LOG_FILE, _LOG_FILE + ".1")
            return True
    except Exception:
        pass
    return False


def read_board_env():
    """Extract the `set "NAME=VALUE"` pairs from start-taskboard.cmd."""
    values = {}
    if not os.path.isfile(CMD_FILE):
        return values
    with open(CMD_FILE, "r", encoding="utf-8-sig", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if line[:3].lower() != "set":
                continue
            match = re.match(r'set\s+"([A-Za-z_][A-Za-z0-9_]*)=(.*)"\s*$', line)
            if match is None:
                match = re.match(r'set\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$', line)
            if match:
                values[match.group(1)] = match.group(2)
    return values


def parse_argv(argv):
    """Return (port_override, data_dir_override) from the smoke-test flags."""
    port = None
    data_dir = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--port" and index + 1 < len(argv):
            port = int(argv[index + 1])
            index += 2
            continue
        if token == "--data-dir" and index + 1 < len(argv):
            data_dir = argv[index + 1]
            index += 2
            continue
        index += 1
    return port, data_dir


def is_listening(port):
    probe = socket.socket()
    probe.settimeout(1.0)
    try:
        probe.connect((HOST, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def is_healthy(port, timeout=3.0):
    # No-proxy opener: this machine sets HTTP_PROXY, and urllib would
    # otherwise route the loopback request through it and get a fake 502.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://%s:%d/health" % (HOST, port)
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def main(argv):
    global _LOG_FILE, _STATE_FILE

    # Smoke-test isolation. Plain string checks, so this cannot raise.
    if "--port" in argv or "--data-dir" in argv:
        _LOG_FILE, _STATE_FILE = SMOKE_LOG_FILE, SMOKE_STATE_FILE

    # Banner first, before anything that can fail. Any execution leaves a trace
    # the self-check script can key on.
    RUN["pid"] = os.getpid()
    RUN["argv"] = list(argv)
    RUN["exe"] = os.path.basename(sys.executable or "")
    rotated = rotate_log()
    log("%s pid=%d exe=%s argv=%s rotated=%s"
        % (LOGON_MARKER, RUN["pid"], RUN["exe"],
           json.dumps(list(argv), ensure_ascii=False), "yes" if rotated else "no"))
    write_state(**dict(RUN, result="started", exit_code=None))

    board_env = read_board_env()
    if not board_env:
        log("ERROR no `set` lines could be read from %s" % CMD_FILE)
        return finish(3, "error", "no set lines in start-taskboard.cmd")

    port_override, data_dir_override = parse_argv(argv)
    try:
        port = port_override or int(board_env.get("CODEX_TASKBOARD_PORT") or DEFAULT_PORT)
    except (TypeError, ValueError) as exc:
        log("ERROR CODEX_TASKBOARD_PORT is not a number: %r (%r)"
            % (board_env.get("CODEX_TASKBOARD_PORT"), exc))
        return finish(4, "error", "bad CODEX_TASKBOARD_PORT in start-taskboard.cmd")

    RUN["port"] = port

    if port_override:
        # Keep the env handed to Node consistent with the port we probe,
        # otherwise the overrides only affect this script's own checks.
        board_env["CODEX_TASKBOARD_PORT"] = str(port)
        board_env["CODEX_TASKBOARD_URL"] = "http://%s:%d" % (HOST, port)
    if data_dir_override:
        board_env["CODEX_TASKBOARD_DATA_DIR"] = data_dir_override

    # Logged AFTER the overrides so this line always shows the values the child
    # actually receives. (Logging it earlier reported the real data dir even on a
    # smoke run, which reads like the smoke instance had touched the live DB.)
    log("logon run detail: port=%d data_dir=%s"
        % (port, board_env.get("CODEX_TASKBOARD_DATA_DIR", "<unset>")))

    if is_listening(port):
        log("port %d is already in use - nothing to do" % port)
        return finish(0, "already_running")

    if not os.path.isfile(NODE_EXE):
        log("ERROR node.exe not found at %s" % NODE_EXE)
        return finish(2, "error", "node.exe missing")

    env = os.environ.copy()
    env.update(board_env)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        env.pop(name, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        sink = open(_LOG_FILE, "a", encoding="utf-8")
    except Exception as exc:
        log("ERROR cannot open the log file as the child's stdout: %r" % (exc,))
        return finish(5, "error", "log file not writable: %r" % (exc,))

    log("starting server/index.mjs on port %d (origin allowlist: %s)"
        % (port, board_env.get("CODEX_TASKBOARD_TRUSTED_ORIGINS", "<unset>")))

    try:
        proc = subprocess.Popen(
            [NODE_EXE, os.path.join("server", "index.mjs")],
            cwd=APP_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
    except Exception as exc:
        log("ERROR failed to spawn node: %r" % (exc,))
        return finish(5, "error", "spawn failed: %r" % (exc,))
    RUN["node_pid"] = proc.pid

    for _ in range(30):
        time.sleep(1.0)
        if is_healthy(port):
            log("service is up on http://%s:%d" % (HOST, port))
            return finish(0, "up")
        if proc.poll() is not None:
            # Node bailed out; no point waiting the remaining 29 seconds.
            log("ERROR server exited early with code %s" % proc.returncode)
            return finish(6, "error", "node exited with code %s" % proc.returncode)

    log("WARNING service did not answer /health on port %d within 30s" % port)
    return finish(1, "no_health")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        # SystemExit inherits from BaseException, not Exception, so the normal
        # sys.exit() path never lands here -- only a real unhandled error does.
        # KeyboardInterrupt is likewise passed through.
        try:
            _tb = traceback.format_exc()
            log("ERROR unhandled exception in launcher:\n%s" % _tb.rstrip())
            _lines = [l for l in _tb.strip().splitlines() if l.strip()]
            write_state(**dict(RUN, result="crash", exit_code=70,
                               note=(_lines[-1] if _lines else "unknown")))
        except Exception:
            pass
        raise SystemExit(70)
