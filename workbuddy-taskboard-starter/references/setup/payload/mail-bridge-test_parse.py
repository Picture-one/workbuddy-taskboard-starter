# -*- coding: utf-8 -*-
"""指令解析回归测试 —— 只调用解析函数，不联网、不读写看板、不动邮箱。

改动 poller.py 的解析逻辑后，跑一遍这个脚本：
    python test_parse.py

它守住两条底线：
  ① 用户能想到的「说人话」写法都要认得（不能逼用户背 c1 c2 这种代号）；
  ② 任何含糊的、带否定的、或者只是叙述性的句子都不能被误认成指令
     —— 误判会直接改看板数据，所以这里的「应当被拒绝」用例比「应当被识别」更重要。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import poller  # noqa: E402

SHOULD_PARSE = [
    # 中文自然语言（用户真实会写的）
    ("把1、2项标记为已完成", [('done', ['1', '2'])]),
    ("1、2项已完成", [('done', ['1', '2'])]),
    ("1和2做完了", [('done', ['1', '2'])]),
    ("第1项完成", [('done', ['1'])]),
    ("完成1 2 3", [('done', ['1', '2', '3'])]),
    ("1 2 都完成", [('done', ['1', '2'])]),
    ("请把第1、2、3项都标记为已完成", [('done', ['1', '2', '3'])]),
    ("done 1、2", [('done', ['1', '2'])]),
    # 简写代号
    ("c1 c2", [('done', ['1', '2'])]),
    ("c1、c3", [('done', ['1', '3'])]),
    ("done ABC-1", [('done_by_id', 'ABC-1')]),
    ("ping", [('ping', None)]),
    ("复盘 今天把方法学补齐了", [('review', '今天把论文方法学补齐了')]),
    ("+ 整理 示例素材表", [('add', '整理 示例素材表')]),
]

SHOULD_NOT_PARSE = [
    # 叙述性句子：含数字但不是在下达完成指令 —— 误判会勾错任务
    "完成了3篇论文的阅读",
    "完成了文献综述的1和2部分",
    "今天完成了不少工作",
    "把任务1的进度写进报告第2章",
    "我完成了",
    # 否定句：绝不能在用户说「还没完成」时勾掉任务
    "1、2项还没完成",
    "1和2都没完成",
    "未完成的是第1项",
    "1还没做完",
    # 模板邮件的示例行（以「·」开头）—— 防线是「·」标记 + 长度限制
    "\u00b7 c1 c2        把第 1、2 项标记为已完成",
    "\u00b7 r1 新标题    把第 1 项的标题换成新内容",
    "\u00b7 + 新任务     追加一条任务",
    # 空行
    "",
]

fails = 0

print('=== 应当被识别 ===')
for text, expect in SHOULD_PARSE:
    got = poller.parse_commands(text)
    ok = got == expect
    fails += 0 if ok else 1
    print('%s  %-36s -> %s' % ('OK  ' if ok else 'FAIL', text, got))
    if not ok:
        print('      期望: %s' % (expect,))

print()
print('=== 应当被拒绝（必须解析出 0 条指令）===')
for text in SHOULD_NOT_PARSE:
    got = poller.parse_commands(text)
    ok = got == []
    fails += 0 if ok else 1
    print('%s  %-36s -> %s' % ('OK  ' if ok else 'FAIL', text, got))

print()
print('失败项：%d' % fails)
sys.exit(1 if fails else 0)
