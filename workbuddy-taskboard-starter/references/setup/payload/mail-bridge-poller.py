#!/usr/bin/env python3
"""邮件下行：用 IMAP 拉取收件箱里的回复，解析成指令回写到本地看板。

用法：
  python poller.py --once    跑一轮就退出（测试用）
  python poller.py           常驻，每 pollIntervalSec 秒跑一轮

为什么不需要公网入口：
  本进程是「本机主动发起」的 IMAP 连接，邮件服务器把新邮件放在那里等我们取。
  所以不需要域名、不需要备案、不需要内网穿透。

⚠️ 本进程对邮箱严格只读，绝不改动你的邮件状态：
  - INBOX 以 readonly 方式打开，从根上禁止改 flag
  - 用 BODY.PEEK[] 取正文（BODY[] / RFC822 会隐式把邮件标记为已读）
  - 全程不发送任何 STORE 指令
  去重靠 state/processed.json 里的 Message-ID，而不是靠「已读/未读」状态。

⚠️ 只处理「回复」类邮件：
  我们自己发出的清单/回执邮件会回流到同一个收件箱，而清单正文里印着
  指令示例行（"r1 新内容"、"+ 新任务"、"复盘 内容"）。若不拦住，
  这些示例会被误执行成改标题、凭空加任务等破坏性操作。
  因此要求：主题含 [任务面板] 且以回复前缀开头（Re: / 回复： / 答复: 等）。
"""

import argparse
import datetime
import email
import imaplib
import os
import re
import sys
import time
from email.header import decode_header, make_header

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    THREAD_ID, die, list_tasks, load_config, load_json, processed_path,
    resolve_index, review_path, save_json, taskctl, today,
)

SUBJECT_TAG = '[任务面板]'

# 只认这些回复前缀。宁可漏执行（安全），也不能误执行（破坏性）。
REPLY_PREFIXES = ('re:', 're：', 're :', '回复:', '回复：', '答复:', '答复：')


def looks_like_reply(subject):
    return (subject or '').strip().lower().startswith(REPLY_PREFIXES)


# ---------------------------------------------------------------- 指令解析

RE_DONE_LINE = re.compile(r'^c\s*[:\-]?\s*(.+)$', re.I)
RE_DONE_BY_ID = re.compile(r'^(?:c|done)\s+([A-Za-z]{2,6}-\d+)$', re.I)
RE_REPLACE = re.compile(r'^r\s*(\d+)\s+(.+)$', re.I)
RE_ADD = re.compile(r'^[+＋]\s*(.+)$')
RE_REVIEW = re.compile(r'^(?:复盘|review|note)\s*[:：]?\s*(.*)$', re.I)
RE_PING = re.compile(r'^ping$', re.I)

# ---------------------------------------------------------------- 自然语言
#
# 为什么要这一层：要求用户背 `c1 c2` 这种代号语法是不合理的 ——
# 实测用户收到清单后，直接回了一句「把1、2项标记为已完成」。
# 所以补上中文自然语言的「完成」识别。
#
# ⚠️ 安全边界：自然语言**只**开放「标记完成」这一个动作。
#    「改标题(r1)」「新增任务(+)」这类会改动/新增数据的动作，
#    仍然只认严格语法 —— 宁可漏执行，也不能让一句随意的话凭空改数据。
#    （标记完成是可逆的、且是用户最高频的意图，收益远大于风险。）

NL_DONE_VERBS = (
    '完成', '做完', '做好了', '做好', '搞定', '处理完', '处理好了',
    '打勾', '打钩', '勾上', '勾掉', '勾选', '勾了', '标记', '标注', 'done',
)
NL_DONE_NEG = (
    '未完成', '没完成', '没做完', '没做好', '未做', '还没', '尚未', '待完成',
    '不做', '不完成', '没搞定', '未搞定', '没勾', '先不', '无法完成', '不能完成',
)

# 句子里允许出现的「填充词」：把它们全部剥掉后若还剩东西，说明这句话
# 不是单纯的下达完成指令（可能是普通叙述），一律拒绝。
NL_FILLERS = (
    '标记为已完成', '标记为完成', '标注为已完成', '标注为完成',
    '已完成', '已做完', '已搞定', '都已完成', '均已完成',
    '处理好了', '处理好', '处理完', '做好了', '做好', '做完', '搞定', '结束',
    '帮我', '麻烦', '一下', '请', '把', '将', '给', '第', '项', '条', '个', '号',
    '标记', '标注', '勾选', '打勾', '打钩', '勾上', '勾掉', '勾了', '勾', '完成',
    '全部', '全都', '全', '都', '均', '也', '已', '了', '掉', '完成掉',
    '和', '及', '与', '以及', '还有', '并且', '并', '然后', '同',
    '我', '的', '是', '个',
    'done', 'finish', 'finished', 'ok',
)
NL_FILLERS_SORTED = tuple(sorted(NL_FILLERS, key=len, reverse=True))

# 数字簇：1、2 / 1 和 2 / 1,2 / 1/2 都算
RE_NL_NUMBERS = re.compile(r'\d+(?:\s*[、,，和及与/＋+\s]\s*\d+)*')
# 排版标记：模板邮件里的示例行以「·」开头，必须拒绝，防止示例被当成指令执行
NL_LAYOUT_MARKS = '·•∙'


def _nl_residue(line, cluster):
    """剥掉数字簇 + 标点 + 填充词后剩下的渣。空字符串 = 确认是纯完成指令。"""
    r = line.replace(cluster, '')
    r = re.sub(r'[\s、,，;；|:：/\\#＋+*\-—_。.!！?？~～()（）\[\]]+', '', r)
    low = r.lower()
    for tok in NL_FILLERS_SORTED:
        low = low.replace(tok, '')
    return low


def parse_natural_done(line):
    """把「把1、2项标记为已完成」这类中文句子解析成 ['1','2']。不认识则返回 None。"""
    s = line.strip()
    if not s or len(s) > 60:
        return None
    if any(mark in s for mark in NL_LAYOUT_MARKS):
        return None
    if any(neg in s for neg in NL_DONE_NEG):
        return None
    if not any(v in s for v in NL_DONE_VERBS):
        return None

    m = RE_NL_NUMBERS.search(s)
    if not m:
        return None
    cluster = m.group(0)
    nums = [n for n in re.findall(r'\d+', cluster) if n != '0']
    if not nums:
        return None
    # 关键闸门：句子里除了「数字 + 完成类词」不能有别的内容。
    # 「完成了 3 篇论文的阅读」会剩下「篇论文阅读」→ 拒绝，不会误勾第 3 项。
    if _nl_residue(s, cluster):
        return None
    return nums


QUOTE_BREAK = (
    '-----原始邮件-----', '-----Original Message-----', '原始邮件', 'Original Message',
    '写道', '发件人', 'From: ', '发送时间', '日期: ', '____',
)


def extract_done_numbers(rest):
    """支持 c1 c2 / c1、c3 / c 1, 2 / c: 1 3 这几种写法。不能识别则返回 None。"""
    parts = [p for p in re.split(r'[\s,，、;；|]+', rest.strip()) if p]
    nums = []
    for p in parts:
        core = p.lstrip('cC')
        if core.isdigit():
            nums.append(core)
        else:
            return None
    return nums or None


def strip_quoted(text):
    """砍掉邮件客户端自动附带的引用原文，只留用户自己写的部分。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('>'):
            break
        if any(m in s for m in QUOTE_BREAK):
            break
        if s and set(s) <= {'_', '-'} and len(s) > 3:
            break
        out.append(line)
    return '\n'.join(out).strip()


def parse_commands(body):
    """返回指令列表：[('done', ['1']), ('replace', ('1','新标题')), ...]"""
    cmds = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 示例行防线：模板邮件里以「·」开头的行只是排版示例，永远不执行
        if any(mark in line for mark in NL_LAYOUT_MARKS):
            continue
        m = RE_PING.match(line)
        if m:
            cmds.append(('ping', None))
            continue
        m = RE_DONE_BY_ID.match(line)
        if m:
            cmds.append(('done_by_id', m.group(1).upper()))
            continue
        m = RE_DONE_LINE.match(line)
        if m:
            nums = extract_done_numbers(m.group(1))
            if nums:
                cmds.append(('done', nums))
                continue
        m = RE_REPLACE.match(line)
        if m:
            cmds.append(('replace', (m.group(1), m.group(2).strip())))
            continue
        m = RE_REVIEW.match(line)
        if m:
            cmds.append(('review', m.group(1).strip()))
            continue
        m = RE_ADD.match(line)
        if m:
            cmds.append(('add', m.group(1).strip()))
            continue
        # 兜底：中文自然语言说「完成」（只开放这一个动作）
        nums = parse_natural_done(line)
        if nums:
            cmds.append(('done', nums))
            continue
    return cmds


# ---------------------------------------------------------------- 执行回写

def current_status(project_id, identifier):
    """查任务当前状态。查不到就返回 None（不阻塞后续动作）。"""
    try:
        for t in list_tasks(project_id):
            if (t.get('identifier') or '').upper() == identifier.upper():
                return t.get('status')
    except Exception:
        pass
    return None


def mark_done(identifier, project_id=None):
    # 幂等：同一封回复被重复处理（用户连回两封、或去重状态被重置）时，
    # 已完成的任务不再重复 move，避免 taskctl 报错污染回执。
    if project_id and current_status(project_id, identifier) == 'done':
        return '%s 已是完成状态（重复的回复已忽略）' % identifier
    taskctl(['issue', 'move', identifier, '--status', 'done',
             '--thread-id', THREAD_ID, '--json'])
    return '已完成 %s' % identifier


def rename(identifier, title):
    taskctl(['issue', 'update', identifier, '--title', title,
             '--thread-id', THREAD_ID, '--json'])
    return '已改名 %s → %s' % (identifier, title)


def add_task(project_id, title):
    data = taskctl(['issue', 'create', '--project', project_id, '--title', title,
                    '--status', 'todo', '--thread-id', THREAD_ID, '--json'])
    ident = (data.get('task') or data).get('identifier') if isinstance(data, dict) else '?'
    return '已新增 %s：%s' % (ident, title)


def write_review(text):
    path = review_path(today())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write('\n## %s\n\n%s\n' % (time.strftime('%H:%M'), text))
    return '已记录复盘（%s）' % os.path.basename(path)


def execute_cmd(cfg, kind, payload):
    project = cfg['taskboardProjectId']

    if kind == 'ping':
        return 'pong —— 下行链路通了'

    if kind == 'review':
        if not payload:
            return None
        return write_review(payload)

    if kind == 'add':
        return add_task(project, payload)

    if kind == 'done_by_id':
        return mark_done(payload, project)

    if kind == 'done':
        results = []
        for n in payload:
            entry = resolve_index(n)
            if not entry:
                results.append('%s → 找不到对应任务（可能摘要邮件已被清理）' % n)
                continue
            results.append('%s → %s' % (n, mark_done(entry['identifier'], project)))
        return '；'.join(results) if results else None

    if kind == 'replace':
        n, title = payload
        entry = resolve_index(n)
        if not entry:
            return '%s → 找不到对应任务' % n
        return rename(entry['identifier'], title)

    return None


# ---------------------------------------------------------------- IMAP

_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def imap_date(d):
    """IMAP SEARCH 要求英文月份缩写；不依赖系统 locale。"""
    return '%02d-%s-%d' % (d.day, _MON[d.month - 1], d.year)


def decode_hdr(raw):
    try:
        return str(make_header(decode_header(raw or '')))
    except Exception:
        return raw or ''


def body_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain' and not part.get('Content-Disposition'):
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'replace')
                except Exception:
                    continue
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                try:
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'replace')
                    return re.sub(r'<[^>]+>', ' ', html)
                except Exception:
                    continue
        return ''
    try:
        return msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', 'replace')
    except Exception:
        return msg.get_payload() or ''


def run_once(cfg, verbose=True):
    state = load_json(processed_path(), {}) or {}
    seen_ids = set(state.get('messageIds') or [])

    lookback = max(1, int(cfg.get('lookbackDays') or 7))
    since = imap_date(datetime.date.today() - datetime.timedelta(days=lookback))

    M = None
    try:
        M = imaplib.IMAP4_SSL(cfg['imapHost'], int(cfg['imapPort']))
        M.login(cfg['email'], cfg['authCode'])
    except imaplib.IMAP4.error as e:
        die('IMAP 登录失败（多半是授权码不对，或 QQ 邮箱未开启 IMAP 服务）：%s' % e)
    except Exception as e:
        die('IMAP 连接失败：%s' % e)

    handled = 0
    scanned = 0
    ack_lines = []
    try:
        # readonly=True：本进程在协议层面被禁止修改任何 flag
        M.select('INBOX', readonly=True)

        # 只看「最近 N 天 + 发件人是自己」的邮件：
        # 收件箱里有几千封历史邮件，绝不能全量扫描。
        crit = '(SINCE "%s" FROM "%s")' % (since, cfg['email'])
        typ, data = M.uid('search', None, crit)
        uids = (data[0] or b'').split() if data and data[0] else []

        for uid in uids:
            scanned += 1
            # BODY.PEEK[] 不会把邮件标记为已读（BODY[]/RFC822 会）
            typ, raw = M.uid('fetch', uid, '(BODY.PEEK[])')
            if typ != 'OK' or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            mid = (msg.get('Message-ID') or '').strip() or ('uid-%s' % uid.decode())

            if mid in seen_ids:
                continue

            subject = decode_hdr(msg.get('Subject'))

            # 双重把关：必须是我们这套系统发出的线程，且确实是一封「回复」
            if SUBJECT_TAG not in subject or not looks_like_reply(subject):
                seen_ids.add(mid)
                if verbose:
                    print('--- 跳过（非回复 / 非本系统邮件）---')
                    print('  主题:', subject[:70])
                continue

            body = strip_quoted(body_text(msg))
            cmds = parse_commands(body)

            if verbose:
                print('--- 新回复 ---')
                print('  主题:', subject)
                print('  正文:', body.replace('\n', ' / ')[:200] or '(空)')
                print('  识别到 %d 条指令' % len(cmds))

            for kind, payload in cmds:
                try:
                    res = execute_cmd(cfg, kind, payload)
                except Exception as e:
                    res = '执行 %s 失败：%s' % (kind, e)
                if res:
                    ack_lines.append(res)
                    if verbose:
                        print('  →', res)

            seen_ids.add(mid)
            handled += 1
    finally:
        try:
            M.close()
        except Exception:
            pass
        try:
            M.logout()
        except Exception:
            pass

    save_json(processed_path(), {'messageIds': sorted(seen_ids)[-500:]})

    if verbose:
        print('[扫描] 最近 %d 天内本人邮件 %d 封，其中新回复 %d 封' % (lookback, scanned, handled))

    if ack_lines and cfg.get('ackByEmail', True):
        try:
            from mailer import send
            send(cfg, '[任务面板] 回执 · %s' % today(),
                 '看板已更新：\n\n' + '\n'.join('  ' + l for l in ack_lines))
            if verbose:
                print('  已发回执邮件')
        except Exception as e:
            print('  [警告] 回执发送失败：%s' % e)

    return handled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true', help='只跑一轮')
    args = ap.parse_args()

    cfg = load_config()
    interval = max(15, int(cfg.get('pollIntervalSec') or 60))

    if args.once:
        n = run_once(cfg)
        print('[完成] 本轮处理了 %d 封新回复' % n)
        return

    print('[mail-bridge] 开始轮询 %s，每 %d 秒一轮。Ctrl+C 退出。' % (cfg['imapHost'], interval))
    while True:
        try:
            run_once(cfg)
        except SystemExit:
            raise
        except KeyboardInterrupt:
            print('\n[mail-bridge] 已停止。')
            return
        except Exception as e:
            print('[警告] 本轮出错，将继续重试：%s' % e)
        time.sleep(interval)


if __name__ == '__main__':
    main()
