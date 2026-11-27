"""Native 946 new-role reward icon, preview and all-or-nothing claim."""
from time import monotonic

from native946_codec import packet, screen_notice, text_notice
from native946_storage import StorageBusy, transaction

QUERY = 1393
CLAIM = 1394
GUIDE = 8714
STARTER_CHEST = 45526

# Elapsed online seconds per character. The second five-minute interval
# starts when the first reward becomes available.
TIERS = {
    1: (60, ((4000, 30), (4001, 30), (STARTER_CHEST, 1), (GUIDE, 1))),
    2: (360, ((6782, 5), (6783, 5))),
}
LAST_DEADLINE = max(deadline for deadline, _ in TIERS.values())
ITEMS = {
    str(GUIDE): dict(name='西游指引', icon=6742, max_stack=1, desc='使用后打开升级指引。'),
    str(STARTER_CHEST): dict(name='幸运宝箱', icon=8554, max_stack=1, desc='打开可领取五件初级装备。'),
}
ITEM_TYPES = frozenset(map(int, ITEMS))


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_newbie_rewards (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id),
        online_seconds INTEGER NOT NULL DEFAULT 0 CHECK(online_seconds >= 0),
        claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed >= 0))''')


def seed(db, cid):
    db.execute('INSERT OR IGNORE INTO native_newbie_rewards(character_id) VALUES(?)', (cid,))


def row(db, cid):
    return db.execute('SELECT online_seconds,claimed FROM native_newbie_rewards WHERE character_id=?',
                      (cid,)).fetchone()


def icon(operation, tier, seconds=0):
    return packet(1210, ['number'] * 3, [operation, tier, seconds])


def icons(seconds, claimed, *, initialize=False):
    visible = []
    for tier, (deadline, _) in TIERS.items():
        if claimed & (1 << tier):
            continue
        if seconds < deadline or initialize:
            visible.append(icon(2, tier, max(1, deadline - seconds)))
        if seconds >= deadline:
            visible.append(icon(3, tier))
        if seconds < deadline:
            break
    return tuple(visible)


def begin(game):
    with game.progression.transaction(write=False) as db:
        state = row(db, game.progression.cid)
    if state is None:
        return ()
    seconds, claimed = state
    game._newbie_award_clock = monotonic() if seconds < LAST_DEADLINE else None
    return icons(seconds, claimed, initialize=True)


def tick(game):
    start = getattr(game, '_newbie_award_clock', None)
    if start is None:
        return ()
    elapsed = int(monotonic() - start)
    if elapsed < 5:
        return ()
    try:
        with transaction(game.characters.database, write=True) as db:
            state = row(db, game.progression.cid)
            if state is None:
                return ()
            old, claimed = state
            new = min(LAST_DEADLINE, old + elapsed)
            db.execute('UPDATE native_newbie_rewards SET online_seconds=? WHERE character_id=?',
                       (new, game.progression.cid))
    except StorageBusy:
        return ()
    game._newbie_award_clock = start + elapsed if new < LAST_DEADLINE else None
    if any(old < deadline <= new for deadline, _ in TIERS.values()):
        return icons(new, claimed)
    return ()


def queue_items(db, cid, source, items):
    from native946_campaign import deliver

    for kind, amount in items:
        db.execute('INSERT INTO native_campaign_pending VALUES(?,?,?,?)', (cid, source, kind, amount))
    _, pending = deliver(db, cid, chapter=source)
    if pending:
        raise ValueError('背包空间不足，请整理后再领取。')


def receive(game, mid, reader):
    tier = reader.integer()
    reader.finish()
    if tier not in TIERS:
        raise ValueError('该档新手奖励尚未开放。')
    deadline, items = TIERS[tier]
    bit = 1 << tier
    progression = game.progression
    with progression.transaction(write=(mid == CLAIM)) as db:
        state = row(db, progression.cid)
        if state is None:
            raise ValueError('当前角色没有建角计时奖励。')
        seconds, claimed = state
        if claimed & bit:
            return icon(1, tier)
        if seconds < deadline:
            raise ValueError('本档奖励尚未到领取时间。')
        if mid == QUERY:
            return packet(1211, ['number', ('array', 'fresh_award_item')],
                          [tier, [dict(type=kind, Amount=amount) for kind, amount in items]])
        if game.battle:
            raise ValueError('请先结束战斗，再领取新手奖励。')
        queue_items(db, progression.cid, f'newbie-award:{tier}', items)
        db.execute('UPDATE native_newbie_rewards SET claimed=claimed|? WHERE character_id=?',
                   (bit, progression.cid))
    progression._cached_snapshot = None
    return game.defer_packets(lambda: (*progression.item_packets(), icon(1, tier),
                                       screen_notice('已领取本档新手奖励。')))


def use(game, iid):
    from native946_equipment import COMPLETION, persist
    from native946_inventory_features import owned
    from native946_mall_uses import consume
    from native946_rookie_guide import overview
    from native946_characters import ROLES

    progression = game.progression
    with progression.transaction() as db:
        item = owned(db, progression.cid, iid)
        if item['type'] == GUIDE:
            return overview(progression._snapshot(db))
        if item['type'] != STARTER_CHEST:
            raise ValueError('当前道具没有新手奖励用途。')
        if game.battle:
            raise ValueError('请先结束战斗，再开启宝箱。')
        role = ROLES[str(game.character['role_id'])]
        starter = COMPLETION['starter']
        weapon = next((profile for profile in starter.values() if profile['slot'] == 4
                       and profile['role'] == role['name']), None)
        if weapon is None:
            raise ValueError('当前造型缺少已核实的初级武器。')
        kinds = [5000 if role['sex'] == 1 else 5001, 5002,
                 5003 if role['sex'] == 1 else 5004, weapon['native_type'], 5023]
        known = {r[0] for r in db.execute('SELECT id FROM inventory WHERE character_id=?', (progression.cid,))}
        consume(db, item)
        queue_items(db, progression.cid, f'newbie-box:{iid}', ((kind, 1) for kind in kinds))
        for value in db.execute('SELECT id,type FROM inventory WHERE character_id=?', (progression.cid,)).fetchall():
            profile = starter.get(str(value['type']))
            if value['id'] not in known and profile:
                persist(db, progression.cid, value['id'], profile, profile['properties'])
    progression._cached_snapshot = None
    return game.defer_packets(lambda: (*progression.item_packets(), text_notice('五件初级装备已放入背包。')))
