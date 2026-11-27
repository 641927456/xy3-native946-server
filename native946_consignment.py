"""946 fragmented point listings backed by an account billing ledger."""
from time import time

from native946_codec import packet, text_notice


REQUESTS = {1354, 1385, 1435, 1436, 1437, 1438, 1440, 1441, 1442, 1479, 1498}
WRITES = {1498}
READS = {1441, 1479}
CARD_FRAG = 1
PAGE_SIZE = 8  # dlg_cardtrade_buy_panel
LISTING_SECONDS = 12 * 3600  # 5876aafc, the fragmented-point UI
MAX_NUMBER = (1 << 40) - 1


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_card_accounts (
        username TEXT PRIMARY KEY REFERENCES accounts(username) ON DELETE CASCADE,
        consignment_points INTEGER NOT NULL DEFAULT 0 CHECK(consignment_points>=0))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_card_listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller INTEGER NOT NULL REFERENCES characters(id),
        owner TEXT NOT NULL REFERENCES native_card_accounts(username),
        remaining INTEGER NOT NULL CHECK(remaining>=0),
        unit_price INTEGER NOT NULL CHECK(unit_price>0),
        expires INTEGER NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS native_card_listing_price ON native_card_listings(unit_price,id) WHERE remaining>0')


def wallet(db, owner, now):
    row = db.execute('SELECT consignment_points FROM native_card_accounts WHERE username=?', (owner,)).fetchone()
    if row is None:
        return None
    expired = db.execute('SELECT coalesce(sum(remaining),0) FROM native_card_listings '
                         'WHERE owner=? AND expires<=?', (owner, now)).fetchone()[0]
    return row[0] + expired


def book(db, now, owner=None):
    where = ' AND owner=?' if owner is not None else ''
    args = (now, owner) if owner is not None else (now,)
    return db.execute('SELECT * FROM native_card_listings WHERE remaining>0 AND expires>?'
                      + where + ' ORDER BY unit_price,id', args).fetchall()


def listing_packet(db, mid, owner, now, page):
    if not 1 <= page <= 100000:
        raise ValueError('寄售列表页码无效。')
    own = mid == 1479
    rows = book(db, now, owner if own else None)
    pages = max(1, (len(rows)+PAGE_SIZE-1)//PAGE_SIZE)
    page = min(page, pages)
    records = [dict(sale_id=row['id'], point_type=CARD_FRAG, price=row['unit_price']*row['remaining'],
                    remain_time=row['expires']-now, avg_price=row['unit_price'], point=row['remaining'])
               for row in rows[(page-1)*PAGE_SIZE:page*PAGE_SIZE]]
    available = wallet(db, owner, now) or 0
    if own:
        return packet(1302, [('array', 'card_point')] + ['number']*3, [records, available, page, pages])
    volume = sum(row['remaining'] for row in rows)
    average = sum(row['unit_price']*row['remaining'] for row in rows)//volume if volume else 0
    low, high = (rows[0]['unit_price'], rows[-1]['unit_price']) if rows else (0, 0)
    return packet(1243, ['number', ('array', 'card_point')] + ['number']*6,
                  [CARD_FRAG, records, available, low, high, average, page, pages])


def publish(db, game, now, amount, price):
    owner = game.character['owner']
    available = wallet(db, owner, now)
    if available is None:
        raise ValueError('当前账号尚无计费服务提供的寄售点数账户。')
    if not 1 <= amount <= available or not 1 <= price <= MAX_NUMBER//amount:
        raise ValueError('寄售点数不足或寄售单价无效。')
    db.execute('UPDATE native_card_listings SET remaining=0 WHERE owner=? AND expires<=? AND remaining>0', (owner, now))
    db.execute('UPDATE native_card_accounts SET consignment_points=? WHERE username=?', (available-amount, owner))
    db.execute('INSERT INTO native_card_listings(seller,owner,remaining,unit_price,expires) VALUES(?,?,?,?,?)',
               (game.character['id'], owner, amount, price, now+LISTING_SECONDS))


def quote(db, owner, now, point):
    remaining = point
    total = 0
    for row in book(db, now):
        if row['owner'] == owner:
            continue
        quantity = min(remaining, row['remaining'])
        total += quantity * row['unit_price']
        remaining -= quantity
        if remaining == 0:
            if total > MAX_NUMBER:
                raise ValueError('报价超出可支付范围。')
            return total
    raise ValueError('当前散点供给不足。')


def receive(game, mid, reader):
    owner = game.character['owner']
    now = int(time())
    if mid == 1354:
        kind = reader.integer()
        reader.finish()
        if kind != CARD_FRAG:
            raise ValueError('整点和月卡寄售需要计费与权益服务。')
        with game.progression.transaction(write=False) as db:
            return listing_packet(db, 1441, owner, now, 1)
    if mid in READS:
        page = reader.integer()
        reader.finish()
        with game.progression.transaction(write=False) as db:
            return listing_packet(db, mid, owner, now, page)
    if mid == 1498:
        amount, price = reader.integer(), reader.integer()
        reader.finish()
        if game.battle:
            raise ValueError('请先结束战斗。')
        with game.progression.transaction() as db:
            publish(db, game, now, amount, price)
            reply = listing_packet(db, 1479, owner, now, 1)
        return reply, text_notice('散点已上架。')
    if mid in (1440, 1442):
        from local_accounts import USERNAME

        point = reader.integer()
        own_account = reader.take(1)[0]
        receiver = reader.string().decode('gb18030')
        price_change = reader.take(1)[0]
        if mid == 1440:
            reader.integer()
        reader.finish()
        if point < 1 or own_account not in (0, 1) or price_change not in (0, 1):
            raise ValueError('购卡参数无效。')
        if not own_account and not USERNAME.fullmatch(receiver):
            raise ValueError('请输入有效的充值账号。')
        receiver = '' if own_account else receiver
        if mid == 1440:
            raise ValueError('购卡、点数入账与卖家结算尚未开放，未扣除现金。')
        with game.progression.transaction(write=False) as db:
            if wallet(db, owner, now) is None:
                raise ValueError('当前账号尚未连接点卡计费账本。')
            total = quote(db, owner, now, point)
        return packet(1244, ['number', 'number', 'number', 'byte', 'string', 'byte'],
                      [point, total//point, total, own_account, receiver, price_change])
    if mid == 1435:
        page, kind = reader.integer(), reader.integer()
        reader.finish()
        if not 1 <= page <= 100000 or not 0 <= kind <= 100000:
            raise ValueError('月卡寄售列表参数无效。')
        return (packet(1245, [('array', 'month_card')] + ['number']*6, [[], 0, 0, 0, 1, 1, 0]),
                text_notice('月卡寄售尚未连接计费权益。'))
    for _ in range({1385: 2, 1436: 3, 1437: 1, 1438: 0}[mid]):
        reader.integer()
    reader.finish()
    raise ValueError('此点卡或月卡操作需要计费与权益服务。')
