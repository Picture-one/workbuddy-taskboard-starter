#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_card_task.py —— 把「每日卡片」写成 wb-demo 里**一天一个独立任务**。

为什么单独一个脚本、而不是改 paper_push.py / db_push.py：
  那两个脚本把契约写死成可 grep 复核的证明（见其文末 AUDIT 注释）——
  「issue create 全生命周期最多 1 次 + comment add + 写 state json」。
  「每天新建一个任务」在语义上直接违反它，改了会让 AUDIT 变成假声明。

本脚本自己的契约：
  * 允许的写操作仅 2 类：issue create（每次运行 <=1 次，同日同类已存在时 0 次）、写 state/*.json
  * 刻意不含 comment add / issue update|move|archive|restore / comment update|delete / attachment upload
  * 只新增，绝不修改、删除、归档任何既有任务、评论、标签

幂等：marker 写在任务 description 的**首行**
  <!-- daily-card v1 kind=<kind> date=<YYYY-MM-DD> -->
扫描时必须带 --archived all —— 否则卡片一旦被归档，次日重跑会再建一个重复任务
（db_push.py 已踩过这个坑）。

用法：
  python daily_card_task.py --kind paper|db --body-file <卡片.md>
                            [--date YYYY-MM-DD] [--start-date YYYY-MM-DD]
                            [--title ...] [--title-suffix ...] [--doi ...]
                            [--project wb-demo] [--status todo]
                            [--dry-run] [--force] [--no-history] [--json]

退出码：
  0 成功 / 已跳过 / 预演完成 · 2 参数或文件错误 · 3 看板服务不可用
  4 接口错误 · 5 版本冲突 · 6 同日同类任务不唯一（不猜、不删、直接报）
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import common  # noqa: E402

DEFAULT_PROJECT = 'wb-demo'
DEFAULT_STATUS = 'todo'          # 「等待认领」列；注意 taskctl 的缺省是 backlog（会掉进侧栏）
THREAD_ID = 'daily-card-push'    # 与 mail-bridge / paper-daily-push / db-daily-learn 区分
# ⚠️ 复查本脚本时如果要验证「--archived all 能否扫到归档任务」，**必须沿用同一个 --thread-id**。
#    archive/restore/update/move 四个动作都会写 thread_id（最后写入者获胜）：
#    storedThreadBindingForExisting()（server/database.mjs:28-37）仅在
#    threadBinding === undefined && 现存绑定.threadId === 传入值 时保留原绑定；
#    否则走 storedThreadBinding(undefined, 传入值) → 返回 [传入值, null×4]（数组恒真）
#    → 真的执行 thread_id=?, thread_codex_project_id=NULL, ... 。
#    实测踩过：用 --thread-id verify-xxx 做归档/还原，会把绑定的 4 个身份列置 NULL
#    （表现为 legacyLocal: true）。功能上良性（本机 thread_id 全是合成标签、无真实会话），
#    但会让"验证动作污染被测对象"。复原：issue update <ID> --status <同值> --thread-id <原值>。
LABEL_CORE = 'daily-card'
DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
MARKER_RE = re.compile(r'<!--\s*daily-card\s+v1\s+kind=(\w+)\s+date=([0-9]{4}-[0-9]{2}-[0-9]{2})\s*-->')

KIND_META = {
    'paper': {
        'label': 'daily-card-paper',
        'extra_labels': ['paper-reading'],
        'title_prefix': '【每日论文】',
        'history': 'paper-push-history.json',
    },
    'db': {
        'label': 'daily-card-db',
        'extra_labels': ['database', 'learning'],
        'title_prefix': '【每日数据库】',
        'history': 'db-push-history.json',
    },
}


def die(msg, code):
    print('[错误] %s' % msg)
    sys.exit(code)


# ── marker ─────────────────────────────────────────────────────────────

def build_marker(kind, day):
    return '<!-- daily-card v1 kind=%s date=%s -->' % (kind, day)


# ── 读取（纯读，零写入）─────────────────────────────────────────────────

def list_all_tasks(project_id):
    """含归档任务 —— 幂等扫描必须看到归档，否则归档后重跑会重复建任务。"""
    data = common.taskctl(['issue', 'list', '--project', project_id, '--archived', 'all', '--json'])
    if isinstance(data, dict):
        return data.get('tasks') or []
    return data or []


def find_existing(project_id, kind, day):
    """命中 (kind, day) 的任务列表。判据 = 静态标签 + description/title 里的 marker。"""
    label = KIND_META[kind]['label']
    hits = []
    for task in list_all_tasks(project_id):
        if label not in (task.get('labels') or []):
            continue
        haystack = '%s\n%s' % (task.get('description') or '', task.get('title') or '')
        for m in MARKER_RE.finditer(haystack):
            if m.group(1) == kind and m.group(2) == day:
                hits.append(task)
                break
    return hits


# ── 标题 ───────────────────────────────────────────────────────────────

# 卡片里既出现过「| 中文标题 | x |」也出现过「| **标题** | x |」，两种都要认
_PAPER_TITLE_ROW_RE = re.compile(
    r'^\|\s*(?:\*\*\s*)?\s*(?:中文)?标题\s*(?:\s*\*\*)?\s*\|\s*(.+?)\s*\|\s*$', re.M)
_DB_TITLE_PIPE_RE = re.compile(r'^医学数据库每日学习\s*[|｜]\s*', re.M)


def _clean_heading(line):
    s = line.strip()
    s = re.sub(r'^#{1,6}\s*', '', s)
    s = s.replace('\U0001F4DA', '').strip()          # 去 📚 装饰
    s = re.sub(r'^\d{4}-\d{2}-\d{2}\s*[·•]\s*', '', s)
    s = re.sub(r'^每日高分论文推荐\s*[·•]\s*\d{4}-\d{2}-\d{2}\s*', '', s)
    s = _DB_TITLE_PIPE_RE.sub('', s)
    return s.strip()


def _split_bilingual(text):
    """`中文 / *English*` → 只取中文那段（英文标题卡片正文里本来就有）。

    只在「空格 斜杠 空格」处切，避免误伤标题里天然存在的斜杠（如「活细胞/单细胞」）。
    """
    parts = re.split(r'\s+/\s+', text)
    if len(parts) < 2:
        return text.strip().strip('*').strip()
    for part in parts:
        if re.search(r'[\u4e00-\u9fff]', part):
            return part.strip().strip('*').strip()
    return parts[0].strip().strip('*').strip()


def derive_core(kind, body, title_suffix, title_override):
    """核心名：覆盖 > suffix > 正文抽取。"""
    if title_override or title_suffix:
        return ''
    if kind == 'paper':
        m = _PAPER_TITLE_ROW_RE.search(body)
        if m:
            return _split_bilingual(m.group(1))
    for line in body.splitlines():
        if not line.strip().startswith('#'):
            continue
        cleaned = _clean_heading(line)
        if cleaned:
            return cleaned
    return ''


def derive_title(kind, day, body, title_override, title_suffix):
    if title_override:
        return title_override.strip()
    core = (title_suffix or '').strip() or derive_core(kind, body, None, None)
    if len(core) > 60:
        core = core[:60].rstrip() + '…'
    parts = [KIND_META[kind]['title_prefix'] + day]
    if core:
        parts.append(core)
    return ' · '.join(parts)


# ── 写 payload（文件，非看板写入）───────────────────────────────────────

def write_payload(kind, day, body):
    common.ensure_state_dir()
    path = os.path.join(common.STATE_DIR, '_daily-card-%s-%s.desc.md' % (kind, day))
    payload = build_marker(kind, day) + '\n\n' + body.rstrip() + '\n'
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(payload)
    return path, payload


def upsert_history(kind, entry):
    """按 date upsert（不重复追加），保持既有 item schema 的超集。"""
    common.ensure_state_dir()
    path = os.path.join(common.STATE_DIR, KIND_META[kind]['history'])
    hist = common.load_json(path) if os.path.exists(path) else {'items': []}
    if not isinstance(hist, dict):
        hist = {'items': []}
    items = hist.get('items')
    if not isinstance(items, list):
        items = []
    kept = [it for it in items if not (isinstance(it, dict) and it.get('date') == entry.get('date'))]
    kept.append(entry)
    kept.sort(key=lambda it: str(it.get('date') or ''))
    hist['items'] = kept[-90:]
    common.save_json(path, hist)


# ── main ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kind', required=True, choices=sorted(KIND_META))
    ap.add_argument('--body-file', required=True)
    ap.add_argument('--date', default=None, help='YYYY-MM-DD，默认本机今天（本地时区）')
    ap.add_argument('--start-date', default=None, help='写入任务的 startDate，默认等于 --date')
    ap.add_argument('--title', default=None)
    ap.add_argument('--title-suffix', default=None)
    ap.add_argument('--doi', default=None)
    ap.add_argument('--project', default=DEFAULT_PROJECT)
    ap.add_argument('--status', default=DEFAULT_STATUS)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true', help='幂等命中时仍新建（唯一制造重复的入口）')
    ap.add_argument('--no-history', action='store_true', help='不写 history json（回填时用）')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    def out(payload):
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))

    day = args.date or common.today()
    if not DAY_RE.match(day):
        die('--date 格式应为 YYYY-MM-DD，收到：%s' % day, 2)
    start_date = args.start_date or day
    if not DAY_RE.match(start_date):
        die('--start-date 格式应为 YYYY-MM-DD，收到：%s' % start_date, 2)
    if args.status not in common.VALID_STATUSES:
        die('--status 非法：%s（合法值 %s）' % (args.status, ','.join(common.VALID_STATUSES)), 2)

    body_path = os.path.abspath(args.body_file)
    if not os.path.exists(body_path):
        die('卡片文件不存在：%s' % body_path, 2)
    try:
        with open(body_path, encoding='utf-8', errors='strict') as f:
            body = f.read()
    except UnicodeDecodeError:
        die('卡片文件不是合法 UTF-8：%s' % body_path, 2)
    if '\x00' in body:
        die('卡片文件含 NUL 字节：%s' % body_path, 2)
    if not body.strip():
        die('卡片文件为空：%s' % body_path, 2)

    title = derive_title(args.kind, day, body, args.title, args.title_suffix)

    try:
        common.ensure_taskboard()
    except SystemExit:
        raise
    except Exception as e:
        die('看板服务不可用：%s' % e, 3)

    try:
        hits = find_existing(args.project, args.kind, day)
    except Exception as e:
        die('扫描既有任务失败：%s' % e, 4)

    if len(hits) > 1:
        for t in hits:
            print('        · %s  %s' % (t.get('identifier'), t.get('title')))
        die('已存在 %d 个 %s/%s 的卡片任务，无法确定目标，请人工确认' % (len(hits), args.kind, day), 6)

    if hits and not args.force:
        print('[跳过] %s 的 %s 卡片已存在（%s），未做任何改动。' % (day, args.kind, hits[0].get('identifier')))
        out({'status': 'skipped', 'action': 'skipped', 'duplicate': True,
             'identifier': hits[0].get('identifier'), 'kind': args.kind, 'date': day})
        sys.exit(0)

    payload_path, payload = write_payload(args.kind, day, body)

    if args.dry_run:
        print('[预演] 将新建 1 个任务：%s' % title)
        print('       project=%s status=%s startDate=%s labels=%s thread=%s 正文=%d 字符，未真正写入。'
              % (args.project, args.status, start_date,
                 ','.join([LABEL_CORE, KIND_META[args.kind]['label']] + KIND_META[args.kind]['extra_labels']),
                 THREAD_ID, len(payload)))
        out({'status': 'dry-run', 'action': 'would-create', 'title': title,
             'kind': args.kind, 'date': day, 'startDate': start_date})
        sys.exit(0)

    labels = [LABEL_CORE, KIND_META[args.kind]['label']] + KIND_META[args.kind]['extra_labels']
    cmd = ['issue', 'create',
           '--project', args.project,
           '--title', title,
           '--description-file', payload_path,
           '--status', args.status,
           '--priority', 'medium',
           '--labels', ','.join(labels),
           '--thread-id', THREAD_ID,
           '--start-date', start_date,
           '--json']

    res = None
    for attempt in (1, 2):
        try:
            res = common.taskctl(cmd)
            break
        except Exception as e:
            msg = str(e)
            conflict = ('409' in msg) or ('CONFLICT' in msg.upper())
            if conflict and attempt == 1:
                print('[重试] 版本冲突，重试 1 次。')
                continue
            die('新建任务失败：%s' % msg, 5 if conflict else 4)

    task = (res or {}).get('task') or {}
    if not task.get('identifier'):
        die('create 成功但响应里没有 task 对象：%r' % (res,), 4)

    print('[完成] 已新建 %s  %s' % (task['identifier'], task.get('title') or title))

    if not args.no_history:
        entry = {
            'date': day,
            'identifier': task['identifier'],
            'taskId': task.get('id'),
            'title': title,
            'kind': args.kind,
            'startDate': start_date,
            'status': 'posted',
        }
        if args.doi:
            entry['doi'] = args.doi
        try:
            upsert_history(args.kind, entry)
        except Exception as e:
            print('[警告] history 写入失败（不影响看板结果）：%s' % e)

    out({'status': 'created', 'action': 'created', 'identifier': task['identifier'],
         'taskId': task.get('id'), 'title': task.get('title') or title,
         'kind': args.kind, 'date': day, 'startDate': start_date, 'project': args.project})
    sys.exit(0)


if __name__ == '__main__':
    main()

# ── AUDIT ─────────────────────────────────────────────────────────────
# 本脚本承诺：只新增，绝不修改 / 删除 / 归档任何既有任务、评论、标签。
#
# ⚠️ 不要用 grep 复核（本注释与上面的 docstring 本身就含有那些动作词，grep 必然命中，
#    是个永远 PASS 的假检查器）。请用 AST 审计，它解析真实的调用参数：
#
#   python F:\workbuddy_workspace\2026-09-07-21-05-00\_build\verify_daily_card_audit.py
#
# 判据：解析出所有 taskctl(...) 调用的前两个参数（含变量绑定），必须全部落在
# {('issue','list'), ('issue','create')} 内；出现 comment add / issue update|move|archive|
# restore / comment update|delete / attachment upload 任一即 rc=1。
