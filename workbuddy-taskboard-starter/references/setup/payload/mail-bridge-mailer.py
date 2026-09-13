#!/usr/bin/env python3
"""邮件上行：把任务清单发到你的 QQ 邮箱，微信「QQ邮箱提醒」把提醒推给你。

用法：
  python mailer.py --test     发一封测试邮件，用于验证微信是否弹出提醒
  python mailer.py            发送当前任务清单（会被 poller 记录编号映射）
  python mailer.py --dry-run  只打印不发送
"""

import argparse
import json
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, formatdate

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from common import (  # noqa: E402
    die, digest_path, load_config, open_tasks, save_json, today,
)

PRIORITY_ORDER = {'urgent': 0, 'high': 1, 'medium': 2, 'low': 3, 'none': 4}
STATUS_ORDER = {'in_progress': 0, 'in_review': 1, 'blocked': 2, 'todo': 3, 'backlog': 4}


def build_digest(cfg):
    project = cfg['taskboardProjectId']
    tasks = open_tasks(project)
    tasks.sort(key=lambda t: (
        STATUS_ORDER.get(t.get('status'), 9),
        PRIORITY_ORDER.get(t.get('priority'), 9),
        t.get('sortOrder') or 0,
    ))

    if not tasks:
        subject = '[任务面板] %s · 当前没有待办' % today()
        body = (
            '当前项目 %s 里没有未完成的任务。\n\n'
            '回复「+ 任务内容」可以直接新增一条任务。\n' % project
        )
        return subject, body, {}

    lines = []
    mapping = {}
    for i, t in enumerate(tasks, 1):
        ident = t.get('identifier')
        mapping[str(i)] = {
            'identifier': ident,
            'id': t.get('id'),
            'title': t.get('title'),
            'status': t.get('status'),
            'version': t.get('version'),
            'projectId': t.get('projectId'),
        }
        lines.append('%d. %s  [%s]' % (i, t.get('title'), ident))

    subject = '[任务面板] %s · %d 项待办' % (today(), len(tasks))
    body = '\n'.join([
        '今日任务（%s）' % today(),
        '',
        *lines,
        '',
        '-' * 32,
        '直接回复本邮件即可回写看板。最省事的写法（推荐）：',
        '  · 把1、2项标记为已完成      ← 直接说人话就行',
        '  · 1和2完成 / 完成 1 2        ← 这几种写法都认',
        '',
        '也支持简写代号：',
        '  · c1 c2        把第 1、2 项标记为已完成',
        '  · r1 新标题    把第 1 项的标题换成新内容',
        '  · + 新任务     追加一条任务',
        '  · 复盘 内容    写下今天的复盘',
        '',
        '（示例前的「·」只是排版标记，回复时不要写它；正文只写命令本身。）',
        '',
        '【怎么回复】',
        '  在微信「服务通知」里找到本邮件的提醒 → 直接回复；',
        '  或用 QQ邮箱 App / 网页版的「回复」功能回复本邮件。',
        '【不要在哪里回复】',
        '  不要在「企业微信」的群里回复 —— 那条通道只负责提醒，',
        '  它是单向的，收不到你的任何回复。',
        '（回复时不要带引用的原文。）',
    ])
    return subject, body, mapping


TEST_SUBJECT = '[任务面板] 链路测试'
TEST_BODY = """这是一封链路测试邮件（同一内容也推了一份到企业微信）。

请分别确认两件事：

① 微信里是否出现「QQ邮箱提醒」
   注意：它出现在微信的【服务通知】里，不是普通聊天窗口。
   没有的话 → 我 → 设置 → 通用 → 辅助功能 → QQ邮箱提醒 → 启用并打开「接收邮件提醒」

② 企业微信里那个只有你自己的群，是否收到同一条
   这条是企业微信「消息推送」通道，作为邮件通道的兜底。
   注意：它**只能单向推送**，不要在这个群里回复（回复无效）。

上行只要任意一条到达就成立。

然后验证下行：在微信「服务通知」里回复本邮件的提醒，
或用 QQ邮箱 App / 网页版的「回复」功能回复本邮件，
正文只写 4 个字母 —— ping
（必须用「回复」而不是新建邮件，否则主题不带 Re:，不会被识别。）
"""


WECOM_PREFIX = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send'
# 企业微信 text 消息 content 上限 2048 字节，留出余量
WECOM_MAX_BYTES = 1800


def notify_wecom(cfg, subject, body, dry_run=False):
    """备用上行通道：把同一条内容推到企业微信「消息推送」群机器人。

    为什么需要它：邮件 → 微信「QQ邮箱提醒」这条路依赖微信侧功能开关，
    一旦没开或绑定失效就彻底静默。企业微信这条通道已实测能稳定到达手机，
    作为兜底保证「提醒一定会到」。

    注意：企业微信「消息推送」只支持主动推送、收不到回复，
    所以勾选回写仍然只能走邮件回复或看板 UI。
    """
    url = (cfg.get('wecomWebhookUrl') or '').strip()
    if not url:
        print('[跳过] 未配置 wecomWebhookUrl，只走邮件通道。')
        return None
    if not url.startswith(WECOM_PREFIX):
        print('[警告] wecomWebhookUrl 不是企业微信「消息推送」的 webhook 地址，已跳过。')
        print('        正确形式：https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...')
        print('        注意别误用应用消息接口 message/send（那个要 access_token 和可信 IP）。')
        return None

    # 必须显式写明「在此回复无效」：企业微信「消息推送」收不到回复，
    # 而用户在收到提醒后最自然的动作就是直接在该群里回复 —— 实测踩过这个坑。
    content = (
        '%s\n\n%s\n\n'
        '───\n'
        '本消息仅供提醒，在此回复无效（企业微信「消息推送」不支持接收回复）。\n'
        '若要勾选任务：请到微信「服务通知」里回复那封邮件，\n'
        '正文直接写「把1、2项标记为已完成」就行（或写 c1 c2）。'
    ) % (subject, body)
    raw = content.encode('utf-8')
    if len(raw) > WECOM_MAX_BYTES:
        content = raw[:WECOM_MAX_BYTES].decode('utf-8', 'ignore') + '\n…（过长已截断）'

    if dry_run:
        print('[dry-run] 不推企业微信。内容：')
        print('    ' + content.replace('\n', '\n    '))
        return True

    payload = json.dumps(
        {'msgtype': 'text', 'text': {'content': content}}, ensure_ascii=False
    ).encode('utf-8')
    req = urllib.request.Request(
        url, data=payload, headers={'Content-Type': 'application/json'}, method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8', 'replace') or '{}')
    except urllib.error.URLError as e:
        print('[警告] 企业微信推送失败（不影响邮件通道）：%s' % e)
        return None

    if data.get('errcode') == 0:
        print('[成功] 企业微信通道已推送（在「企业微信」App 里查看）。')
        return True
    print('[警告] 企业微信接口返回错误（不影响邮件通道）：%s' % data)
    return None


def send(cfg, subject, body, dry_run=False):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = formataddr(('任务面板', cfg['email']))
    msg['To'] = cfg['email']
    msg['Date'] = formatdate(localtime=True)
    msg.set_content(body, charset='utf-8')

    if dry_run:
        print('[dry-run] 不会真的发送。')
        print('  收件人:', cfg['email'])
        print('  主题:', subject)
        print('  正文:')
        print('    ' + body.replace('\n', '\n    '))
        return True

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(cfg['smtpHost'], int(cfg['smtpPort']), timeout=25, context=ctx) as s:
            s.login(cfg['email'], cfg['authCode'])
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        die('SMTP 登录失败（多半是授权码不对，注意不是 QQ 登录密码）：%s' % e)
    except Exception as e:
        die('发送失败：%s' % e)
    print('[成功] 邮件已投递到 %s（主题：%s）' % (cfg['email'], subject))
    return True


def push_all(cfg, subject, body, dry_run=False):
    """主通道发邮件；若开了 pushWecom，再推一条企业微信兜底。"""
    send(cfg, subject, body, dry_run=dry_run)
    if cfg.get('pushWecom'):
        notify_wecom(cfg, subject, body, dry_run=dry_run)
    else:
        print('[提示] pushWecom 未开启，只走邮件通道。')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--test', action='store_true', help='发送链路测试邮件')
    ap.add_argument('--dry-run', action='store_true', help='只打印不发送')
    ap.add_argument('--wecom-only', action='store_true', help='只推企业微信，不发邮件')
    args = ap.parse_args()

    cfg = load_config(require_auth=not args.dry_run)

    if args.test:
        if args.wecom_only:
            notify_wecom(cfg, TEST_SUBJECT, TEST_BODY, dry_run=args.dry_run)
            return
        push_all(cfg, TEST_SUBJECT, TEST_BODY, dry_run=args.dry_run)
        if not args.dry_run:
            print()
            print('[成功] 测试已投递。现在分头确认：')
            print('  ① 微信：看「服务通知」里有没有出现「QQ邮箱提醒」')
            print('  ② 企业微信：看那个只有你自己的群里有没有收到同一条')
            print('  任意一条到达都算上行成立；两条都到最好。')
            print('  接着回复那封邮件（正文写 ping）测下行。')
        return

    subject, body, mapping = build_digest(cfg)
    if args.wecom_only:
        notify_wecom(cfg, subject, body, dry_run=args.dry_run)
    else:
        push_all(cfg, subject, body, dry_run=args.dry_run)
    if args.dry_run:
        return

    day = today()
    save_json(digest_path(day), {'day': day, 'sentAt': day, 'map': mapping})
    print('[成功] 已发送：%s' % subject)
    print('  编号映射已存：%s（共 %d 项）' % (digest_path(day), len(mapping)))


if __name__ == '__main__':
    main()
