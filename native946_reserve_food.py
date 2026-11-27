"""946 reserve food and the four portrait bottles."""
from time import time

from native946_codec import packet, text_notice

# info/info_item.lua in the 946 configs archive (505d8e68).
FOODS = {
    6782: ('相思肉', 6898, 'hp', 1, 20000),
    6783: ('温汤面', 6899, 'mp', 1, 20000),
    6784: ('百味糕', 6900, 'hp', 80, 50000),
    6785: ('蜜芦荟', 6901, 'mp', 80, 50000),
    6786: ('燕窝滋补羹', 6902, 'hp', 100, 80000),
    6787: ('桂子合欢汤', 6903, 'mp', 100, 80000),
    6788: ('灵台山馥草梅子糕', 6904, 'hp', 120, 130000),
    6789: ('蟠桃会清菱藕片', 6905, 'mp', 120, 130000),
    46765: ('不周山人参汤', 7811, 'hp', 120, 200000),
    46766: ('云中殿鹿茸丹', 7812, 'mp', 120, 200000),
    47229: ('青荷五香鸡', 8071, 'hp', 120, 500000),
    47230: ('鹿茸暖冬膏', 8072, 'mp', 120, 500000),
    47231: ('仙山莲花台', 8073, 'hp', 120, 1000000),
    47232: ('玄天续灵水', 8074, 'mp', 120, 1000000),
}
RESOURCES = {'hp': '气血', 'mp': '法力'}
FAST_USE = 3234
SLOT_STORAGE_VERSION = 1
GUARDIAN_SLOT = 'guardian'
RESERVE_LIMIT = 2147483647


def _reserve(db, cid, target):
    keys = (f'reserve:{target}:hp', f'reserve:{target}:mp')
    if target == GUARDIAN_SLOT:
        # Include older per-guardian balances until the resource is next written.
        rows = db.execute("SELECT substr(key,-2),sum(value) FROM cosmetic_features WHERE character_id=? "
                          "AND (key IN (?,?) OR key GLOB 'reserve:[1-9]*:hp' OR key GLOB 'reserve:[1-9]*:mp') "
                          "GROUP BY substr(key,-2)", (cid, *keys))
    else:
        rows = db.execute('SELECT substr(key,-2),value FROM cosmetic_features WHERE character_id=? AND key IN (?,?)',
                          (cid, *keys))
    balances = dict(rows.fetchall())
    return [balances.get(resource, 0) for resource in RESOURCES]


def _save(db, cid, target, resource, amount):
    db.execute('INSERT INTO cosmetic_features VALUES(?,?,?,0) '
               'ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value',
               (cid, f'reserve:{target}:{resource}', amount))
    if target == GUARDIAN_SLOT:
        db.execute('DELETE FROM cosmetic_features WHERE character_id=? AND key GLOB ?',
                   (cid, f'reserve:[1-9]*:{resource}'))


def _restore(db, game, subject):
    cid = game.progression.cid
    target = GUARDIAN_SLOT if subject is not None else 0
    reserve = _reserve(db, cid, target)
    if not any(reserve):
        return False
    state = subject if subject is not None else game.progression._snapshot(db)
    current = {resource: state[resource] for resource in RESOURCES}
    changed = False
    for resource, available in zip(RESOURCES, reserve):
        restored = min(available, max(0, state[resource + 'max'] - current[resource]))
        if restored:
            current[resource] += restored
            _save(db, cid, target, resource, available - restored)
            changed = True
    if changed:
        if subject is None:
            game.progression.set_vitals(current['hp'], current['mp'])
        else:
            game.summons.set_vitals(subject['id'], current['hp'], current['mp'])
    return changed


def restore_guardian_slot(db, game, guardian):
    if guardian is not None:
        return _restore(db, game, guardian)
    return False


def restore_after_battle(db, game, guardian_id=None):
    _restore(db, game, None)
    guardian = game.summons.get(guardian_id) if guardian_id else game.summons.active()
    restore_guardian_slot(db, game, guardian)


def sync(game, active):
    cid = game.progression.cid
    with game.progression.transaction(write=False) as db:
        hero = _reserve(db, cid, 0)
        guardian = [min(amount, RESERVE_LIMIT) for amount in _reserve(db, cid, GUARDIAN_SLOT)] if active else [0, 0]
    return packet(685, ['buff_info', 'buff_info'], [dict(zip(('Hp', 'Mp'), values))
                                                  for values in (hero, guardian)])


def use(game, target, item_id):
    from native946_mall_bags import pages
    from native946_mall_uses import consume

    if game.battle is not None:
        raise ValueError('战斗中不能补充气血或法力储备。')
    cid = game.progression.cid
    with game.progression.transaction() as db:
        item = db.execute('SELECT * FROM inventory WHERE character_id=? AND id=?', (cid, item_id)).fetchone()
        if not item or item['type'] not in FOODS or item['page'] not in pages(db, cid):
            raise ValueError('该食品已不在可用背包中。')
        if db.execute('SELECT 1 FROM cosmetic_expiry WHERE item_id=? AND expires>0 AND expires<=?',
                      (item_id, int(time()))).fetchone():
            raise ValueError('该储备食品已到期。')
        subject = game.summons.get(target) if target not in (0, cid) else None
        target = GUARDIAN_SLOT if subject is not None else 0
        name, _, resource, level, added = FOODS[item['type']]
        if (subject if subject is not None else game.progression._snapshot(db))['level'] < level:
            raise ValueError(f'{name}需要使用对象达到{level}级。')
        existing = _reserve(db, cid, target)[0 if resource == 'hp' else 1]
        if existing + added > RESERVE_LIMIT:
            raise ValueError('储备已接近上限，请先使用现有储备。')
        consume(db, item)
        _save(db, cid, target, resource, existing + added)
        guardian = (subject if subject['fight'] else game.summons.active()) if subject is not None else None
        restored = restore_guardian_slot(db, game, guardian) if subject is not None else _restore(db, game, None)

    def render():
        vitals = ()
        if restored:
            vitals = game.summons.info_packets(guardian['id']) if guardian else game.progression.attribute_packets()
        return (*game.progression.item_packets(), *vitals, sync(game, game.summons.active()),
                text_notice(f'{name}补充了{added}点{RESOURCES[resource]}储备。'))

    return game.defer_packets(render)


def open_selection(game, reader):
    from native946_inventory_locks import records
    from native946_mall_bags import pages

    resource, target_type = reader.take(2)
    reader.finish()
    if resource not in (1, 2) or target_type not in (1, 2):
        raise ValueError('储备食品类型或使用对象无效。')
    if game.battle is not None:
        raise ValueError('战斗中不能补充气血或法力储备。')
    guardian = game.summons.active() if target_type == 2 else None
    if target_type == 2 and guardian is None:
        raise ValueError('请先设置出战守护。')
    resource = ('hp', 'mp')[resource - 1]
    cid = game.progression.cid
    level = (guardian if guardian is not None else game.progression.snapshot())['level']
    with game.progression.transaction(write=False) as db:
        open_pages = pages(db, cid)
        locked = records(db, cid)
        available = {}
        for row in db.execute('SELECT i.*, e.expires FROM inventory i LEFT JOIN cosmetic_expiry e ON i.id=e.item_id '
                              'WHERE i.character_id=? AND i.amount>0 ORDER BY i.page,i.slot', (cid,)):
            food = FOODS.get(row['type'])
            if (food and food[2] == resource and food[3] <= level and row['page'] in open_pages
                    and row['id'] not in locked and (not row['expires'] or row['expires'] > time())):
                available.setdefault(row['type'], row['id'])
    if not available:
        return text_notice(f'背包中没有可用的{RESOURCES[resource]}储备食品。')
    kinds = sorted(available, key=lambda kind: FOODS[kind][4])
    target = guardian['id'] if guardian is not None else cid
    game.dialog_actions = [dict(kind='reserve_food', target=target, item=available[kind]) for kind in kinds]
    return game.menu('请选择要使用的储备食品。',
                     [f'{FOODS[kind][0]}（+{FOODS[kind][4]}）' for kind in kinds], npc_id=cid)
