#!/usr/bin/env python3
"""把 2026-09-14 ~ 2026-09-17 的历史推送卡片回填成「一天一张任务卡片」。

**为什么需要回填**：在此之前每日推送**根本不创建任务** —— `paper_push.py` / `db_push.py`
的模式是「1 个 Hub 任务（WOR-5 论文 / WOR-7 数据库）+ 每天往 Hub 追加一条评论」，
而看板卡片不渲染评论，所以这些内容在面板上完全不可见。回填把它们补成带 date marker 的
独立任务卡片，从而与新的 `daily_card_task.py` 流程对齐。

**铁律**
1. 本脚本**不直接写看板**，只调用 `daily_card_task.py` —— 那是唯一的写侧实现，
   自带幂等（静态标签 + description 里的 marker，含 `--archived all` 扫描）与审计。
2. 归属日期靠 `--start-date`：服务端 `parseTaskCreate` 的允许字段集里**没有 `createdAt`**
   （`shared/task-input.mjs:116-142`），由服务端 `now()` 生成，所以只能靠 `start_date`
   把卡片落回原推送日，否则全部会堆在「今天」。
3. 用 `--no-history`：`paper-push-history.json` / `db-push-history.json` 已记录了当时的
   Hub 评论（含 commentId），回填再写会制造「同一天两条记录」。回填自身的审计单独落
   `state/daily-card-backfill.json`。
4. 素材里的旧 `<!-- paper-push-marker ... -->` 入库前必须剥掉 —— 否则卡片正文会残留一个
   指向旧机制的 marker，日后排查时误导。

用法：
    python backfill_daily_cards.py                 # 预演（默认，零写入）
    python backfill_daily_cards.py --apply         # 实际执行
    python backfill_daily_cards.py --apply --only paper
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import common  # noqa: E402

DAILY_CARD_SCRIPT = os.path.join(SCRIPT_DIR, 'daily_card_task.py')
BACKFILL_LOG = os.path.join(common.STATE_DIR, 'daily-card-backfill.json')

# 旧机制的 comment marker：`<!-- paper-push-marker v1 date=... doi=... -->`
LEGACY_MARKER_RE = re.compile(r'<!--\s*(?:paper|db)-push-marker\s+v1\b[^>]*?-->')

# 回填清单。日期取自 `state/*-push-history.json`，并与卡片文件自身的日期交叉核对。
MANIFEST = [
    {'kind': 'paper', 'date': '2026-09-14', 'source': os.path.join(common.STATE_DIR, 'paper-2026-09-14.body.md')},
    {'kind': 'paper', 'date': '2026-09-16', 'source': os.path.join(common.STATE_DIR, 'paper-2026-09-16.body.md')},
    {'kind': 'paper', 'date': '2026-09-17', 'source': os.path.join(common.STATE_DIR, 'paper-2026-09-17.body.md')},
    {'kind': 'db', 'date': '2026-09-16', 'source': r'F:\database_learn\cards\day01-IBDMDB.md'},
    {'kind': 'db', 'date': '2026-09-17', 'source': r'F:\database_learn\cards\day02-MGnify.md'},
]


def strip_legacy_markers(text: str) -> tuple[str, int]:
    """剥掉旧 `*-push-marker` 注释；返回 (清理后正文, 剥掉个数)。"""
    cleaned, count = LEGACY_MARKER_RE.subn('', text)
    return cleaned.rstrip() + '\n', count


def source_date_hint(text: str) -> str | None:
    """从素材正文里读出一个 `YYYY-MM-DD`，用于与清单日期交叉核对。"""
    match = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
    return match.group(1) if match else None


def run_one(entry: dict, apply: bool, project: str) -> dict:
    """调用 daily_card_task.py 处理一条；返回其 JSON 结果。"""
    if not os.path.exists(entry['source']):
        raise FileNotFoundError(entry['source'])
    raw = open(entry['source'], encoding='utf-8').read()
    hint = source_date_hint(raw)
    if hint and hint != entry['date']:
        raise ValueError('日期不一致：清单 %s vs 素材 %s（%s）' % (entry['date'], hint, entry['source']))

    body, stripped = strip_legacy_markers(raw)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            'w', suffix='.md', encoding='utf-8', newline='\n', delete=False
        ) as handle:
            handle.write(body)
            tmp_path = handle.name
        cmd = [
            sys.executable, DAILY_CARD_SCRIPT,
            '--kind', entry['kind'],
            '--body-file', tmp_path,
            '--date', entry['date'],
            '--start-date', entry['date'],
            '--project', project,
            '--no-history',
            '--json',
        ]
        if not apply:
            cmd.append('--dry-run')
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        out = (proc.stdout or '').strip()
        # ⚠️ write 侧脚本把人类可读的进度行也打到 stdout，JSON 只在**最后一行**。
        #    直接 json.loads(整个 stdout) 会失败并静默退化成 raw_stdout（action 永远缺失）。
        result: dict = {}
        for line in reversed(out.splitlines()):
            candidate = line.strip()
            if candidate.startswith('{') and candidate.endswith('}'):
                try:
                    result = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue
        if not result:
            result = {'raw_stdout': out}
        result['_exit'] = proc.returncode
        if proc.returncode != 0:
            result['_stderr'] = (proc.stderr or '').strip()
        result['_stripped_markers'] = stripped
        result['_source'] = entry['source']
        result['_date'] = entry['date']
        return result
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def main() -> int:
    parser = argparse.ArgumentParser(description='回填历史每日推送卡片（默认预演）')
    parser.add_argument('--apply', action='store_true', help='实际写入（不加则只预演）')
    parser.add_argument('--only', choices=['paper', 'db', 'all'], default='all')
    parser.add_argument('--project', default='wb-demo')
    args = parser.parse_args()

    if not os.path.exists(DAILY_CARD_SCRIPT):
        print('找不到写侧脚本：%s' % DAILY_CARD_SCRIPT, file=sys.stderr)
        return 3

    entries = [e for e in MANIFEST if args.only in ('all', e['kind'])]
    mode = 'APPLY' if args.apply else 'DRY-RUN'
    print('=== 回填历史每日推送卡片 [%s] project=%s 共 %d 条 ===' % (mode, args.project, len(entries)))

    results = []
    failures = 0
    for entry in entries:
        label = '%s %s' % (entry['kind'], entry['date'])
        try:
            result = run_one(entry, args.apply, args.project)
        except Exception as error:  # noqa: BLE001 - 逐条隔离，单条失败不中断其余
            failures += 1
            print('  [失败] %-16s %s' % (label, error))
            results.append({'date': entry['date'], 'kind': entry['kind'], 'ok': False, 'error': str(error)})
            continue

        ok = result.get('_exit') == 0
        if not ok:
            failures += 1
        action = result.get('action') or result.get('status') or ('error' if not ok else '?')
        print('  [%s] %-16s action=%-9s identifier=%-8s markers_stripped=%d%s' % (
            '成功' if ok else '失败', label, action,
            result.get('identifier') or '-', result.get('_stripped_markers', 0),
            '' if ok else '  stderr=%s' % result.get('_stderr', ''),
        ))
        results.append({
            'date': entry['date'],
            'kind': entry['kind'],
            'ok': ok,
            'action': action,
            'identifier': result.get('identifier'),
            'taskId': result.get('taskId'),
            'startDate': result.get('startDate'),
            'strippedMarkers': result.get('_stripped_markers', 0),
            'source': entry['source'],
            'error': result.get('_stderr') or result.get('error'),
        })

    if args.apply:
        common.ensure_state_dir()
        # 幂等留痕：同 (kind,date) 覆盖写入，避免重复运行时审计文件无限增长
        existing = {}
        if os.path.exists(BACKFILL_LOG):
            try:
                for item in json.load(open(BACKFILL_LOG, encoding='utf-8')).get('items', []):
                    existing[(item.get('kind'), item.get('date'))] = item
            except (json.JSONDecodeError, OSError):
                existing = {}
        for item in results:
            existing[(item['kind'], item['date'])] = item
        common.save_json(BACKFILL_LOG, {'items': sorted(
            existing.values(), key=lambda i: (i.get('date') or '', i.get('kind') or ''),
        )})
        print('审计记录已写入：%s' % BACKFILL_LOG)

    creates = [r for r in results if str(r.get('action') or '').startswith(('created', 'would-create'))]
    skips = [r for r in results if str(r.get('action') or '').startswith(('skipped', 'duplicate'))]
    print('--- 合计：create=%d skip=%d failed=%d ---' % (len(creates), len(skips), failures))
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
