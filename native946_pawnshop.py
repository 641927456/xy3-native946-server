"""Owned pawns and redemptions for the native Chang'an pawnshop."""

import json
from pathlib import Path
from secrets import randbelow
from time import monotonic

from native946_codec import packet, text_notice
from native946_inventory_locks import require_unlocked
from native946_mall_bags import pages
from native946_trade import MAX_CASH

NPC = (2047, 1279)
REFERENCE_WIRE_VERSION = 1
PAGE = 30  # COMMON_CONST.PAWN_PAGE
CAPACITY = 24  # The original redemption panel has one 6 x 4 grid.
REQUESTS = {843: 'pawn', 844: 'redeem'}
PANELS = {'pawn': 'dlg_pawn_panel', 'redeem': 'dlg_redeem_panel'}
PRICE_CONFIG = json.loads(Path(__file__).with_name('native946_pawn_prices.json').read_text('utf8'))


def cash_prices():
    from native946_shops import DATA

    prices = {}
    for merchant in DATA['merchants'].values():
        for key, offer in merchant['goods'].items():
            kind, price = int(key), offer['price']
            if type(price) is int and 0 < price <= MAX_CASH:
                prices[kind] = min(price, prices.get(kind, price))
    return prices


def pawn_cash(price, count):
    return price * count * PRICE_CONFIG['pawn_numerator'] // PRICE_CONFIG['pawn_denominator']


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_pawn_tickets (
      item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
      redeem_unit INTEGER NOT NULL CHECK(redeem_unit > 0))''')


def tickets(db, cid):
    return db.execute('''SELECT i.*, t.redeem_unit, e.durability
                         FROM inventory i JOIN native_pawn_tickets t ON t.item_id=i.id
                         LEFT JOIN native_equipment e ON e.item_id=i.id AND e.character_id=i.character_id
                         WHERE i.character_id=? AND i.page=? ORDER BY i.slot''', (cid, PAGE)).fetchall()


def price_slots(db, cid, *, item_id=None):
    prices = cash_prices()
    inventory_pages = pages(db, cid)
    rows = db.execute('''SELECT i.id,i.type,i.page,coalesce(l.flag,0) locked,coalesce(e.expires,0) expires
                         FROM inventory i
                         LEFT JOIN native_object_locks l ON l.object_id=i.id AND l.kind='item' AND l.character_id=i.character_id
                         LEFT JOIN cosmetic_expiry e ON e.item_id=i.id
                         WHERE i.character_id=? AND (? IS NULL OR i.id=?) ORDER BY i.id''', (cid, item_id, item_id))
    return {row['id']: prices.get(row['type'], 0) if not row['locked'] and not row['expires'] else 0
            for row in rows if row['page'] in inventory_pages}


def split_or_move(db, row, count, page, slot):
    if count == row['amount']:
        db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?', (page, slot, row['id']))
        return row['id']
    db.execute('UPDATE inventory SET amount=amount-? WHERE id=?', (count, row['id']))
    moved = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',
                       (row['character_id'], row['type'], page, slot, count))
    db.execute('INSERT INTO native_glyph_item_levels SELECT ?,character_id,level FROM native_glyph_item_levels '
               'WHERE item_id=? AND character_id=?', (moved.lastrowid, row['id'], row['character_id']))
    return moved.lastrowid


class Pawnshop:
    def __init__(self, services):
        self.services = services
        self.g = services.g
        self.p = services.p

    def menu(self):
        return self.services._menu(NPC[1], '客官，您要典当物品，还是赎回当物？#r'
                                   '典当按本机商店现金售价的一半结算，总额取整。赎回按原价。'
                                   '没有报价的物品无法典当。',
                                   [('典当物品', dict(kind='pawnshop', op='pawn')),
                                    ('赎回物品', dict(kind='pawnshop', op='redeem'))])

    def open(self, operation):
        from native946_progression import lua_data
        from native946_npc_services import bank_balances, bank_balance_packets

        close_chat = packet(1183, ['string'], ['dlg_npc_chat'])
        with self.p.transaction(write=False) as db:
            balances = bank_balances(db, self.p.cid)
            if operation == 'pawn':
                slots = price_slots(db, self.p.cid)
                quotes = {iid: price for iid, price in slots.items() if price}
                reply = tuple(packet(3756, ['number', 'string'], [iid, lua_data({'pawn_price': price})])
                              for iid, price in slots.items()) + (packet(840, [], []),)
            else:
                rows = tickets(db, self.p.cid)
                quotes = {row['id']: row['redeem_unit'] for row in rows}
                priced = [dict(id=row['id'], type=row['type'], amount=row['amount'], price=row['redeem_unit'])
                          for row in rows]
                extra = [dict(id=row['id'], endure=row['durability']) for row in rows if row['durability'] is not None]
                reply = (packet(841, [('array', 'price_item'), 'string'], [priced, lua_data(extra)]),)
        self.g.npc_service_session = dict(kind='pawnshop', npc=NPC[1], scene=NPC[0], operation=operation,
                                          expires=monotonic()+120, quotes=quotes)
        return (close_chat, *bank_balance_packets(*balances), *reply)

    def quote_pawn(self, item_id, count):
        from native946_progression import DATA

        session = self.services._session('pawnshop')
        price = session['quotes'].get(item_id)
        if session['operation'] != 'pawn' or not price or count <= 0:
            raise ValueError('物品没有当前窗口的有效报价，请重新向九算子办理。')
        with self.p.transaction(write=False) as db:
            row = db.execute('SELECT type,amount FROM inventory WHERE id=? AND character_id=?',
                             (item_id, self.p.cid)).fetchone()
            if not row or count > row['amount'] or price_slots(db, self.p.cid, item_id=item_id).get(item_id) != price:
                raise ValueError('物品数量、状态或报价已变化，请重新选择。')
        pending = session.get('pending')
        token = pending[0] if pending and pending[1:] == (item_id, count) else 1_000_000_000 + randbelow(1 << 29)
        session['pending'] = token, item_id, count
        name = DATA['items'][str(row['type'])]['name']
        message = f'典当{name}×{count}，获得现金{pawn_cash(price,count)}两，赎回共需{price*count}两。'
        return packet(1020, ['string','string','string','string','number'],
                      ['典当确认', message, '确认典当', '取消', token])

    def confirm(self, answer, token):
        session = self.services._session('pawnshop')
        pending = session.get('pending')
        if not pending or pending[0] != token or answer not in (1, 2, 3):
            raise ValueError('典当确认已失效，请重新选择。')
        if answer != 1:
            session.pop('pending')
            return None
        return self.exchange(843, pending[1], pending[2])

    def exchange(self, request, item_id, count):
        from native946_npc_services import bank_balances, bank_balance_packets

        session = self.services._session('pawnshop')
        operation = REQUESTS[request]
        if operation != session['operation'] or item_id not in session['quotes'] or count <= 0:
            raise ValueError('物品没有当前窗口的有效报价，请重新向九算子办理。')
        with self.p.transaction() as db:
            row = db.execute('SELECT * FROM inventory WHERE character_id=? AND id=?',
                             (self.p.cid, item_id)).fetchone()
            if row is None or count > row['amount']:
                raise ValueError('物品已移动或数量不足，请重新选择。')
            cash, savings = bank_balances(db, self.p.cid)
            require_unlocked(db, self.p.cid, item_id)
            if operation == 'pawn':
                redeem_unit = price_slots(db, self.p.cid, item_id=item_id).get(item_id)
                if not redeem_unit or redeem_unit != session['quotes'][item_id]:
                    raise ValueError('物品已移动、加锁或报价变更，请重新选择。')
                change = pawn_cash(redeem_unit, count)
                if cash + change > MAX_CASH:
                    raise ValueError('典当后的现金超出可显示范围。')
                occupied = {r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=?',
                                                    (self.p.cid, PAGE))}
                slot = next((n for n in range(1, CAPACITY+1) if n not in occupied), None)
                if slot is None:
                    raise ValueError('当铺已经放满，请先赎回物品。')
                target = PAGE, slot
            else:
                held = db.execute('SELECT redeem_unit FROM native_pawn_tickets WHERE item_id=?', (item_id,)).fetchone()
                if row['page'] != PAGE or held is None or held[0] != session['quotes'][item_id]:
                    raise ValueError('当物已变化，请重新打开赎回窗口。')
                change = -count * held[0]
                if cash + change < 0:
                    raise ValueError('随身现金不足，无法赎回。')
                target = self.p.free_bag_slot()
            if count != row['amount'] and self.p.stack_limit(dict(row), self.p.snapshot()) == 1:
                raise ValueError('该物品不能按部分数量办理。')
            moved = split_or_move(db, row, count, *target)
            if operation == 'pawn':
                db.execute('INSERT INTO native_pawn_tickets VALUES(?,?)', (moved, redeem_unit))
            elif moved == item_id:
                db.execute('DELETE FROM native_pawn_tickets WHERE item_id=?', (item_id,))
            db.execute('UPDATE characters SET gold=gold+? WHERE id=?', (change, self.p.cid))
        self.g.npc_service_session = None
        close = packet(1183, ['string'], [PANELS[operation]])
        notice = text_notice(f'典当成功，获得现金{change}两。' if operation == 'pawn'
                             else f'赎回成功，支付现金{-change}两。')
        return self.g.defer_packets(lambda: (*self.p.item_packets(item_ids={item_id, moved}),
                                             *bank_balance_packets(cash+change, savings), close, notice))
