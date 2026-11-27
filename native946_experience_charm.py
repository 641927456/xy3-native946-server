"""946 experience charm (47020), with the personal blessing in position 12."""
from secrets import randbelow
from time import monotonic, time

from native946_codec import packet, screen_notice, text_notice
from native946_inventory_features import consume, owned
from native946_role_systems import save, state


ITEM = 47020
BLESSING = 1006
POSITION = 12
SYSTEM = 'experience_charm'
HOUR = 3600
# 本机在线经验规则。946 道具表仅提供模式和 60 分钟时长。
ONLINE_INTERVAL = 60
ONLINE_XP_PER_LEVEL = 50
INTERVAL_MS = ONLINE_INTERVAL * 1000


def clock_ms():
    return int(monotonic() * 1000)


def expires(db, cid):
    return state(db, cid, SYSTEM).get('expires', 0)


def reward_experience(db, cid, amount, now):
    if amount and expires(db, cid) > now:
        return amount + amount // 2
    return amount


def remaining_packet(seconds):
    return packet(38, ['number', 'number', 'byte', 'number'],
                  [BLESSING, POSITION, 1, max(0, seconds)])


def status(game):
    with game.progression.transaction(write=False) as db:
        value = state(db, game.progression.cid, SYSTEM)
    remaining = value.get('remaining_ms', 0)
    if remaining and getattr(game, '_charm_clock', None) is None:
        game._charm_clock = clock_ms()
        game._charm_remaining = remaining
    expiry = value.get('expires', 0)
    now = int(time())
    if expiry > now:
        game._experience_charm_expires = expiry
        return (packet(37, ['number'] * 3, [1, BLESSING, POSITION]), remaining_packet(expiry - now))
    return expire(game)


def expire(game):
    previous = getattr(game, '_experience_charm_expires', 0)
    if previous and previous <= time():
        game._experience_charm_expires = 0
        return (packet(37, ['number'] * 3, [0, BLESSING, POSITION]),)
    return ()


def online_tick(game, *, stopped_at=None):
    start = getattr(game, '_charm_clock', None)
    if start is None:
        return ()
    if stopped_at is None and game.party_service.online(game.progression.cid) is not game:
        return ()
    now = clock_ms() if stopped_at is None else stopped_at
    elapsed = max(0, now - start)
    # Save partial minutes every ten seconds and when the connection closes.
    until_reward = (game._charm_remaining - 1) % INTERVAL_MS + 1
    if stopped_at is None and elapsed < min(10000, until_reward):
        return ()
    p = game.progression
    replies = ()
    stats = None
    with p.transaction() as db:
        value = state(db, p.cid, SYSTEM)
        before = value.get('remaining_ms', 0)
        remaining = max(0, before - elapsed)
        elapsed_before = HOUR * 1000 - before
        elapsed_after = HOUR * 1000 - remaining
        paid = elapsed_after // INTERVAL_MS - elapsed_before // INTERVAL_MS
        gained = 0
        for _ in range(paid):
            level = db.execute('SELECT level FROM characters WHERE id=?', (p.cid,)).fetchone()[0]
            amount = level * ONLINE_XP_PER_LEVEL
            db.execute('UPDATE characters SET experience=experience+? WHERE id=?', (amount, p.cid))
            gained += amount
            if p.auto_level():
                game.summons.reconcile_levels()
        if before != remaining:
            value['remaining_ms'] = remaining
            save(db, p.cid, SYSTEM, value)
        if gained:
            stats = p.snapshot(fields=('level', 'experience'))
            if stopped_at is None:
                replies = (*p.attribute_packets(), text_notice(f'经验符在线经验：获得{gained}点人物经验。'))
    game._charm_clock = now if remaining and stopped_at is None else None
    game._charm_remaining = remaining
    if stats:
        game.character.update(stats)
    return replies


def open_item(game, target, iid):
    if target not in (0, game.progression.cid):
        raise ValueError('请对人物使用经验符。')
    with game.progression.transaction(write=False) as db:
        if owned(db, game.progression.cid, iid)['type'] != ITEM:
            raise ValueError('背包中没有该经验符。')
    token = 0x60000000 + randbelow(0x10000000)
    game.dialog_actions = [dict(kind='experience_charm', item=iid, mode=mode) for mode in ('bonus', 'online')]
    return game.menu(f'经验符有效时间为60分钟。在线模式每满{ONLINE_INTERVAL}秒获得人物等级×{ONLINE_XP_PER_LEVEL}经验，离线暂停。',
                     ('人物经验加成50%', '在线持续获得经验'), token)


def activate(game, action):
    if game.battle is not None:
        raise ValueError('请先结束战斗再使用经验符。')
    p = game.progression
    now = int(time())
    with p.transaction() as db:
        if owned(db, p.cid, action['item'])['type'] != ITEM:
            raise ValueError('背包中没有该经验符。')
        value = state(db, p.cid, SYSTEM)
        if value.get('expires', 0) > now or value.get('remaining_ms', 0):
            raise ValueError('经验符仍在生效，当前经验符已保留。')
        consume(db, p.cid, action['item'])
        online = action['mode'] == 'online'
        save(db, p.cid, SYSTEM, dict(remaining_ms=HOUR * 1000) if online else dict(expires=now + HOUR))
    if online:
        game._charm_clock = clock_ms()
        game._charm_remaining = HOUR * 1000
    close = game.close_npc_dialogue()
    message = ('已开启在线经验，累计在线60分钟。' if online else '经验符已生效，60分钟内人物经验加成50%。')
    return game.defer_packets(lambda: (close, *p.item_packets(), *status(game),
                                       screen_notice(message)))
