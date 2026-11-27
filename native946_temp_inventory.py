"""946 摘星袋, with its original nine-grid inventory page and transfer requests."""

from time import time

from native946_codec import text_notice
from native946_inventory_locks import records, require_unlocked
from native946_mall_bags import pages


PAGE = 5500
CAPACITY = 9
OFFLINE_SECONDS = 15 * 60
REQUESTS = {804, 810}


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_temp_loot_expiry (
        item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
        expires_at INTEGER NOT NULL DEFAULT 0)''')


def rows(db, cid):
    return db.execute('SELECT * FROM inventory WHERE character_id=? AND page=? ORDER BY slot', (cid, PAGE)).fetchall()


def bag_capacity(db, cid):
    return dict.fromkeys(pages(db, cid), 24)


def place(db, p, kind, amount, destinations, *, source=None, expiry=None):
    """Merge or place owned reward goods and return the number still unplaced."""
    maximum = p.stack_limit({'id': 0, 'type': kind}, {'equipment': {}})
    locked = records(db, p.cid)
    same_kind = db.execute('''SELECT i.id,i.page,i.slot,i.amount,coalesce(e.expires,0) AS item_expiry,
        t.expires_at AS temporary_expiry FROM inventory i
        LEFT JOIN cosmetic_expiry e ON e.item_id=i.id
        LEFT JOIN native_temp_loot_expiry t ON t.item_id=i.id
        WHERE i.character_id=? AND i.type=? ORDER BY i.page,i.slot''', (p.cid, kind)).fetchall()
    for item in same_kind:
        if (item['page'] not in destinations or not 1 <= item['slot'] <= destinations[item['page']]
                or item['id'] in locked or item['item_expiry'] or item['temporary_expiry'] != expiry):
            continue
        quantity = min(amount, max(0, maximum - item['amount']))
        if quantity:
            db.execute('UPDATE inventory SET amount=amount+? WHERE id=?', (quantity, item['id']))
            amount -= quantity
        if not amount:
            return 0

    occupied = {(row[0], row[1]) for row in db.execute('SELECT page,slot FROM inventory WHERE character_id=?', (p.cid,))}
    for page, limit in destinations.items():
        for slot in range(1, limit + 1):
            if (page, slot) in occupied:
                continue
            if source is not None and amount <= maximum:
                db.execute('UPDATE inventory SET page=?,slot=?,amount=? WHERE id=?', (page, slot, amount, source))
                return 0
            quantity = min(amount, maximum)
            iid = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',
                             (p.cid, kind, page, slot, quantity)).lastrowid
            if expiry is not None:
                db.execute('INSERT INTO native_temp_loot_expiry VALUES(?,?)', (iid, expiry))
            amount -= quantity
            if not amount:
                return 0
    return amount


def award(db, p, battle_id, drops, *, offline=False):
    """Award once inside the caller's battle receipt transaction."""
    destination = bag_capacity(db, p.cid)
    expiry = int(time()) + OFFLINE_SECONDS if offline else 0
    counts = dict(bag=0, temporary=0, waiting=0, bag_items={})
    for kind, amount in drops.items():
        excess = place(db, p, kind, amount, destination)
        waiting = place(db, p, kind, excess, {PAGE: CAPACITY}, expiry=expiry) if excess else 0
        if waiting:
            db.execute('INSERT INTO native_pending_loot VALUES(?,?,?,?)', (battle_id, p.cid, kind, waiting))
        delivered = amount - excess
        if delivered:
            counts['bag_items'][kind] = delivered
        counts['bag'] += delivered
        counts['temporary'] += excess - waiting
        counts['waiting'] += waiting
    p._cached_snapshot = None
    return counts


def refill(db, p):
    """Expose previously saved overflow as spaces become available in 摘星袋."""
    pending = db.execute('SELECT * FROM native_pending_loot WHERE character_id=? ORDER BY rowid', (p.cid,)).fetchall()
    for row in pending:
        kind, count = row['type'], row['amount']
        remaining = place(db, p, kind, count, {PAGE: CAPACITY})
        receipt = (row['battle_id'], p.cid, kind)
        if not remaining:
            db.execute('DELETE FROM native_pending_loot WHERE battle_id=? AND character_id=? AND type=?', receipt)
        elif remaining != count:
            db.execute('UPDATE native_pending_loot SET amount=? WHERE battle_id=? AND character_id=? AND type=?',
                       (remaining, *receipt))


def open_session(p):
    with p.transaction(write=False) as db:
        pending = db.execute('SELECT 1 FROM native_pending_loot WHERE character_id=? LIMIT 1', (p.cid,)).fetchone()
        timed = db.execute('''SELECT 1 FROM native_temp_loot_expiry t JOIN inventory i ON i.id=t.item_id
            WHERE i.character_id=? AND i.page=? AND t.expires_at>0 LIMIT 1''', (p.cid, PAGE)).fetchone()
    if not pending and not timed:
        return
    with p.transaction() as db:
        db.execute('''DELETE FROM inventory WHERE character_id=? AND page=? AND id IN
            (SELECT item_id FROM native_temp_loot_expiry WHERE expires_at BETWEEN 1 AND ?)''', (p.cid, PAGE, int(time())))
        db.execute('''UPDATE native_temp_loot_expiry SET expires_at=0 WHERE expires_at>0 AND item_id IN
            (SELECT id FROM inventory WHERE character_id=? AND page=?)''', (p.cid, PAGE))
        refill(db, p)


def close_session(p):
    with p.transaction(write=False) as db:
        timed = db.execute('''SELECT 1 FROM native_temp_loot_expiry t JOIN inventory i ON i.id=t.item_id
            WHERE i.character_id=? AND i.page=? AND t.expires_at=0 LIMIT 1''', (p.cid, PAGE)).fetchone()
    if timed:
        with p.transaction() as db:
            db.execute('''UPDATE native_temp_loot_expiry SET expires_at=? WHERE expires_at=0 AND item_id IN
                (SELECT id FROM inventory WHERE character_id=? AND page=?)''', (int(time()) + OFFLINE_SECONDS, p.cid, PAGE))


def take(db, p, item, target=None):
    iid = item['id']
    require_unlocked(db, p.cid, iid)
    destinations = bag_capacity(db, p.cid)
    if target is not None:
        page, slot = target
        if page not in destinations or not 1 <= slot <= destinations[page]:
            raise ValueError('摘星袋物品只能取到开放的普通道具栏。')
        current = db.execute('SELECT * FROM inventory WHERE character_id=? AND page=? AND slot=?',
                             (p.cid, page, slot)).fetchone()
        if current is None:
            db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?', (page, slot, iid))
            remaining = 0
        else:
            require_unlocked(db, p.cid, current['id'])
            expiry = db.execute('SELECT expires FROM cosmetic_expiry WHERE item_id=?', (current['id'],)).fetchone()
            maximum = p.stack_limit(dict(item), {'equipment': {}})
            if current['type'] != item['type'] or expiry and expiry[0] or current['amount'] >= maximum:
                raise ValueError('目标格已被占用，请选择空格或可叠加的道具。')
            amount = min(item['amount'], maximum - current['amount'])
            db.execute('UPDATE inventory SET amount=amount+? WHERE id=?', (amount, current['id']))
            remaining = item['amount'] - amount
    else:
        remaining = place(db, p, item['type'], item['amount'], destinations, source=iid)
    if not remaining:
        db.execute('DELETE FROM inventory WHERE character_id=? AND id=? AND page=?', (p.cid, iid, PAGE))
        db.execute('DELETE FROM native_temp_loot_expiry WHERE item_id=?', (iid,))
    elif remaining != item['amount']:
        db.execute('UPDATE inventory SET amount=? WHERE id=?', (remaining, iid))
    return item['amount'] - remaining


def take_slot(p, slot, target=None):
    with p.transaction() as db:
        item = db.execute('SELECT * FROM inventory WHERE character_id=? AND page=? AND slot=?', (p.cid, PAGE, slot)).fetchone()
        return take(db, p, item, target) if item else 0


def receive(game, mid, reader):
    slot = None
    if mid == 810:
        page, slot = reader.integer(), reader.integer()
        if page != PAGE or not 1 <= slot <= CAPACITY:
            raise ValueError('摘星袋格子无效。')
    reader.finish()
    if game.battle is not None:
        raise ValueError('请先完成当前战斗。')
    p = game.progression
    if slot is not None:
        moved = take_slot(p, slot)
    else:
        with p.transaction() as db:
            moved = sum(take(db, p, item) for item in rows(db, p.cid))
            refill(db, p)
    game.last_event = dict(action='temp_inventory_transfer', quantity=moved)
    return game.defer_packets(lambda: move_packets(p, moved))


def move_packets(p, moved):
    return (*p.item_packets(), *((text_notice('没有取出物品，请检查摘星袋和普通道具栏空间。'),) if not moved else ()))
