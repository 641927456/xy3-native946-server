"""Read-only native upgrade guide backed by this client's achievement table.

Guide descriptions are not accepted quests or reward receipts. Only exact,
observable character-level conditions can be shown achieved here. In
particular a local practice battle never completes the original newbie plot.
"""
from pathlib import Path
import json
import re

from native946_codec import packet
from native946_codec import text_notice

DATA = json.loads(Path(__file__).with_name('native946_rookie_guide_data.json').read_text('utf8'))
GROUPS = DATA['groups']
ACHIEVEMENTS = {int(k): v for k, v in DATA['achievements'].items()}
REQUESTS = {1473, 1474, 1475}
ICON_PAGE_SIZE = 7


def attained(row, snapshot):
    """Do not infer missing story, daily, item-use or reward history."""
    text = row['mini_desc']
    match = re.fullmatch(r'(?:成功)?升到(?:(\d)转)?(\d+)级', text)
    if match is None:
        return False
    rebirth, level = int(match[1] or 0), int(match[2])
    actual = (snapshot['sanctification'], snapshot['ascension'], snapshot['rebirth'], snapshot['level'])
    return actual >= (0, 0, rebirth, level)


def group_info(snapshot):
    return [dict(group_name=g['name'], achieve_amount=len(g['ids']),
                 achieve_get=sum(attained(ACHIEVEMENTS[aid], snapshot) for aid in g['ids']))
            for g in GROUPS]


def overview(snapshot):
    groups = group_info(snapshot)
    # The denominator must be nonzero: the native progress callback divides
    # by it. Flag zero keeps the unimplemented guide reward disabled. The
    # final field only unlocks browsing of our metadata groups; completion
    # counts above continue to use observed character state.
    return packet(1297, ['number', 'number', 'byte', ('array', 'rookie_guide_level_group_info'), 'number'],
                  [sum(g['achieve_amount'] for g in groups), sum(g['achieve_get'] for g in groups),
                   0, groups, len(groups)])


def section(index, snapshot):
    if type(index) is not int or not 1 <= index <= len(GROUPS):
        raise ValueError('升级指引分类不存在。')
    from native946_progression import lua_data
    group = GROUPS[index - 1]
    pages = []
    for start in range(0, len(group['ids']), ICON_PAGE_SIZE):
        ids = group['ids'][start:start + ICON_PAGE_SIZE]
        # This legacy text area does not clip overflowing rich text. Full
        # original descriptions already belong to each native icon's tooltip.
        description = ('本页相关殊勋：' + '、'.join(ACHIEVEMENTS[aid]['name'] for aid in ids) +
                       '。#r鼠标移至下方图标查看原版条件。未开放的任务不会记作完成；指引奖励暂未开放。')
        if group['source_group'] == 11 and start == 0:
            from native946_campaign import task_details
            next_task = next(iter(task_details(snapshot).values()), None)
            description = '新手剧情从傲来水月仙开始，0转30级可向长安水月仙了解“美猴王”主线。'
            if next_task:
                description += '#r当前目标：' + next_task['text']
            description += '#r鼠标移至下方图标可查看条件。'
        pages.append(dict(name=ACHIEVEMENTS[ids[0]]['name'],
                          desc=description,
                          achieve_list={aid: int(attained(ACHIEVEMENTS[aid], snapshot)) for aid in ids}))
    return packet(1298, ['number', 'string'], [index, lua_data(pages)])


class RookieGuide:
    def __init__(self, game):
        self.g = game

    def receive(self, mid, reader):
        if mid not in REQUESTS:
            return False, None
        index = reader.integer() if mid == 1474 else None
        reader.finish()
        snapshot = self.g.progression.snapshot()
        if mid == 1473:
            # Send first-page detail after the overview. Reopening this native
            # panel does not itself request its first detail page.
            return True, (overview(snapshot), section(1, snapshot))
        if mid == 1474:
            return True, section(index, snapshot)
        return True, (text_notice('升级指引奖励尚未开放，当前没有可领取的奖励。'),
                      overview(snapshot))
