"""Timed experience effects and the Imperial Guard commander’s services."""
from datetime import datetime, timedelta, timezone
from secrets import choice, randbelow
from time import time

from native946_codec import text_notice


NPC = (2001, 1077)
SYSTEM = 'double_time'
HOUR = 3600
WEEKLY_SECONDS = 24 * HOUR
FREEZE_COST = 10
MEDICINES = {6204: '守护催长丹', 6205: '内丹催长丹'}
RESTORE = 6206
MEDICINE_LIMIT = 4 * HOUR
TASKS = ('ghost', 'heaven')
CAPTAIN_DAILY_POINTS = 7000
CAPTAIN_POINTS_PER_TASK = 250
CAPTAIN_COST = 250
CAPTAIN_DAILY_DRAWS = 40
PRIZES = (6036, 45793, 46878, 46988, 47973)
ITEMS = {
    '6204': dict(name='守护催长丹', icon=5233, max_stack=99,
                 desc='封圣185级可用。使用后两小时内参战守护经验加倍，人物经验降为0。'),
    '6205': dict(name='内丹催长丹', icon=5234, max_stack=20,
                 desc='封圣185级可用。使用后两小时内参战守护的内丹经验加倍，人物经验降为20%。'),
    '6206': dict(name='催长还原丹', icon=2027, max_stack=20, desc='使用后消除守护催长丹和内丹催长丹效果。'),
    '6036': dict(name='高级金柳露', icon=5246, max_stack=20, desc='可用于守护炼气。'),
    '45793': dict(name='无间蛊', icon=6435, max_stack=999, desc='可增加5000点守护亲密。'),
}
MENU = (
    ('我来领取双倍时间', 'claim'),
    ('我来查看双倍时间', 'view'),
    ('冻结双倍时间(10两)', 'freeze'),
    ('冻结守护/内丹催长丹效果', 'pause_medicine'),
    ('取消冻结守护/内丹催长丹效果', 'resume_medicine'),
    ('我要用统帅进行封赏', 'captain'),
    ('您忙，我随便看看', 'close'),
)


def load(db, cid, now):
    from native946_role_systems import state

    day = datetime.fromtimestamp(now, timezone(timedelta(hours=8))).date()
    week = (day - timedelta(days=day.weekday())).isoformat()
    value = state(db, cid, SYSTEM, dict(expires=0, frozen=0, week='', used=0,
                                       medicine=0, medicine_expires=0, medicine_frozen=0,
                                       points=0, day='', earned=0, draws=0))
    if value['week'] != week:
        value.update(week=week, used=0)
    if value['day'] != day.isoformat():
        value.update(day=day.isoformat(), earned=0, draws=0)
    return value


def save(db, cid, value):
    from native946_role_systems import save as save_state
    save_state(db, cid, SYSTEM, value)


def duration(seconds):
    hours, remainder = divmod(max(0, seconds), HOUR)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours}小时{minutes:02d}分{seconds:02d}秒'


def experience_rates(db, cid, now):
    value = load(db, cid, now)
    double = 2 if value['expires'] > now else 1
    medicine = value['medicine'] if value['medicine_expires'] > now else 0
    return (double * {6204: 0, 6205: 20}.get(medicine, 100),
            2 * double if medicine == 6204 else double,
            2 if medicine == 6205 else 1)


def award_captain(db, cid, now):
    value = load(db, cid, now)
    points = min(CAPTAIN_POINTS_PER_TASK, CAPTAIN_DAILY_POINTS - value['earned'])
    if points > 0:
        value['points'] += points
        value['earned'] += points
        save(db, cid, value)
    return max(0, points)


def use_medicine(game, iid):
    from native946_inventory_features import consume, owned

    p = game.progression
    with p.transaction() as db:
        item = owned(db, p.cid, iid)
        now = int(time())
        value = load(db, p.cid, now)
        remaining = max(0, value['medicine_expires'] - now) + value['medicine_frozen']
        kind = item['type']
        if kind == RESTORE:
            if not remaining:
                raise ValueError('当前没有可消除的催长丹效果。')
            value.update(medicine=0, medicine_expires=0, medicine_frozen=0)
            message = '已消除催长丹效果。'
        elif kind in MEDICINES:
            hero = p._snapshot(db)
            if not hero['sanctification'] or hero['level'] < 185:
                raise ValueError('催长丹需要封圣185级。')
            if remaining and value['medicine'] != kind:
                raise ValueError('请先结束或消除另一种催长丹效果。')
            if remaining + 2 * HOUR > MEDICINE_LIMIT:
                raise ValueError('催长丹效果最多可叠加至4小时。')
            remaining += 2 * HOUR
            value['medicine'] = kind
            if value['medicine_frozen']:
                value['medicine_frozen'] = remaining
            else:
                value['medicine_expires'] = now + remaining
            message = f'{MEDICINES[kind]}剩余{duration(remaining)}。'
        else:
            raise ValueError('该物品并非催长丹。')
        consume(db, p.cid, iid)
        save(db, p.cid, value)
    return game.defer_packets(lambda: (*p.item_packets(), text_notice(message)))


def execute(game, op):
    p = game.progression
    if op == 'close':
        return game.close_npc_dialogue()
    now = int(time())
    with p.transaction(write=op not in ('view', 'captain')) as db:
        value = load(db, p.cid, now)
        active = max(0, value['expires'] - now)
        remaining = max(0, WEEKLY_SECONDS - value['used'])
        if op == 'view':
            pill = max(0, value['medicine_expires'] - now)
            status = (f'生效双倍：{duration(active)}#r已冻结：{duration(value["frozen"])}'
                      f'#r本周可领：{duration(remaining)}#r催长丹：{duration(pill)}'
                      f'#r已冻结催长丹：{duration(value["medicine_frozen"])}')
            game.dialog_actions = []
            return game.menu(status, npc_id=NPC[1], speaker=NPC[1])
        if op == 'captain':
            callback = 0x60000000 + randbelow(0x10000000)
            game.dialog_actions = [dict(kind='double_time', op='draw'), dict(kind='double_time', op='close_draw')]
            return game.menu(f'统帅值：{value["points"]}#r每次封赏消耗{CAPTAIN_COST}点，今日已封赏{value["draws"]}次。',
                             ('领取封赏', '离开'), callback, speaker=NPC[1])
        if op == 'claim':
            if value['frozen']:
                seconds = value['frozen']
                value['frozen'] = 0
            else:
                if remaining < HOUR:
                    raise ValueError('本周的双倍时间已领完。')
                seconds = HOUR
                value['used'] += seconds
            value['expires'] = now + active + seconds
            message = f'双倍时间剩余{duration(active + seconds)}。'
        elif op == 'freeze':
            if not active:
                raise ValueError('当前没有生效的双倍时间可以冻结。')
            if not db.execute('UPDATE characters SET gold=gold-? WHERE id=? AND gold>=?',
                              (FREEZE_COST, p.cid, FREEZE_COST)).rowcount:
                raise ValueError('现金不足10两。')
            value.update(expires=0, frozen=value['frozen'] + active)
            message = f'已冻结{duration(active)}双倍时间。'
        elif op == 'pause_medicine':
            seconds = value['medicine_expires'] - now
            if seconds <= 0:
                raise ValueError('当前没有生效的催长丹效果可以冻结。')
            value.update(medicine_expires=0, medicine_frozen=seconds)
            message = f'已冻结{duration(seconds)}催长丹效果。'
        elif op == 'resume_medicine':
            seconds = value['medicine_frozen']
            if not seconds:
                raise ValueError('当前没有已冻结的催长丹效果。')
            value.update(medicine_expires=now + seconds, medicine_frozen=0)
            message = f'已恢复{duration(seconds)}催长丹效果。'
        elif op == 'draw':
            if value['draws'] >= CAPTAIN_DAILY_DRAWS:
                raise ValueError('今日封赏次数已达上限。')
            if value['points'] < CAPTAIN_COST:
                raise ValueError(f'统帅值不足{CAPTAIN_COST}点。')
            from native946_inventory_features import DATA, give

            prize = choice(PRIZES)
            give(db, p, prize)
            value['points'] -= CAPTAIN_COST
            value['draws'] += 1
            message = f'获得{DATA["items"][str(prize)]["name"]}。'
        else:
            raise ValueError('该选项不可用。')
        save(db, p.cid, value)

    close = game.close_npc_dialogue()
    def render():
        refresh = p.attribute_packets() if op == 'freeze' else p.item_packets() if op == 'draw' else ()
        return close, *refresh, text_notice(message)

    return game.defer_packets(render, fallback=(close, text_notice(message)))
