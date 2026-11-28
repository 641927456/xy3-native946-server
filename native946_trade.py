"""Native double-confirm trade with the local guardian transfer policy."""
import json
from pathlib import Path
from time import monotonic

from native946_codec import SCHEMAS, packet, text_notice
from native946_jing_gems import TRADEABLE as REALM_TRADEABLE, TYPES as REALM_GEMS
from native946_jing_gems import level as realm_level
from native946_constellations import GROUPS, GROUP_PRIZES
from native946_inventory_locks import require_unlocked
from native946_mall_bags import pages
from native946_nice_ids import identity
from native946_storage import StorageBusy, transaction


REQUESTS = frozenset(range(2063, 2067))
ITEM_TYPES = frozenset(json.loads(Path(__file__).with_name('native946_plain_trade_types.json').read_text('utf8')))
ITEM_TYPES |= frozenset(kind for kind, row in GROUPS.items() if row['item'].get('trade_flag') == 1)
ITEM_TYPES |= frozenset(int(kind) for kind, row in GROUP_PRIZES.items() if row.get('trade_flag') == 1)
TRADE_ITEM_TYPES = ITEM_TYPES | frozenset(REALM_TRADEABLE)
LOCAL_RULES = json.loads(Path(__file__).with_name('native946_local_trade_rules.json').read_text('utf8'))
OFFER_FIELDS = ['number', ('array', 'trade_item'), ('array', 'trade_summon')]
MAX_CASH = (1 << 40) - 1


def guardian_block(db, guardian):
    sid = guardian['id']
    if guardian['grant_key'] in LOCAL_RULES['guardian_excluded_grant_keys']:
        return '该角色专属守护不可交易。'
    if guardian['fight'] or guardian['show']:
        return '请先让守护休息并取消展示。'
    if db.execute("SELECT 1 FROM native_object_locks WHERE kind='summon' AND object_id=?", (sid,)).fetchone():
        return '请先解除守护锁。'
    if (db.execute('SELECT 1 FROM native_guardian_equipment WHERE summon_id=?', (sid,)).fetchone()
            or db.execute('SELECT 1 FROM native_guardian_jade WHERE summon_id=?', (sid,)).fetchone()):
        return '请先卸下守护的装备和宝玉。'
    return None


def offered_guardians(db, sender, receiver, ids):
    from native946_growth import species
    from native946_guardian_chamber import AVAILABLE
    from native946_summons import guardian_level_cap
    from native946_wilderness import carry_rank

    if not ids:
        return []
    hero = receiver.progression._snapshot(db)
    rows = []
    for sid in ids:
        guardian = db.execute('SELECT * FROM summons WHERE id=? AND character_id=? AND ' + AVAILABLE,
                              (sid, sender)).fetchone()
        if guardian is None:
            raise ValueError('守护已不在当前角色的携带栏。')
        reason = guardian_block(db, guardian)
        if reason:
            raise ValueError(reason)
        if (carry_rank(hero) < species(guardian['type'])['take_grade_num']
                or guardian['level'] > guardian_level_cap(hero['level'], guardian)):
            raise ValueError('对方未达到这只守护的携带条件。')
        rows.append(dict(guardian))
    return rows


def move_guardians(db, left, right, guardians):
    from native946_guardian_chamber import carried_count
    from native946_mall_bags import summon_limit

    directions = ((left, right), (right, left))
    for sender, receiver in directions:
        if (guardians[sender] and carried_count(db, receiver) - len(guardians[receiver]) + len(guardians[sender])
                > summon_limit(db, receiver)):
            raise ValueError('接收方守护携带栏已满，交易未执行。')
    for sender, receiver in directions:
        for guardian in guardians[sender]:
            sid = guardian['id']
            transfer = db.execute('INSERT INTO native_guardian_trade_transfers(sender,receiver,summon_id,grant_key) '
                                  'VALUES(?,?,?,?)', (sender, receiver, sid, guardian['grant_key'])).lastrowid
            grant_key = f'guardian-trade:{transfer}'
            if guardian['grant_key'].startswith('capture:'):
                grant_key = 'capture:' + grant_key
            db.execute('UPDATE summons SET character_id=?,grant_key=? WHERE id=? AND character_id=?',
                       (receiver, grant_key, sid, sender))
            db.execute('UPDATE cosmetic_guardian_appearance SET character_id=? WHERE summon_id=? AND character_id=?',
                       (receiver, sid, sender))


def require_trade_accepting(db, cid):
    from native946_role_systems import state

    if not state(db, cid, 'system_settings', {'trade': 1})['trade']:
        raise ValueError('对方已关闭接受交易。')


class Trade:
    def __init__(self, service, game):
        self.service = service
        self.game = game
        self.cid = game.character['id']
        if not hasattr(service, 'trades'):
            service.trades = {}

    def close(self, mid=2059, *, member=None, message='交易已取消。'):
        session = self.service.trades.get(self.cid if member is None else member)
        if session is None:
            return ()
        replies = (packet(mid, [], []), text_notice(message))
        for cid in session['members']:
            self.service.trades.pop(cid, None)
            peer = self.service.online(cid)
            if peer is not None and peer is not self.game:
                self.service.enqueue(peer, *replies)
        return replies

    def maintain(self, session):
        if session['expires'] <= monotonic():
            return self.close(message='交易等待超时，已取消。')
        other = next(cid for cid in session['members'] if cid != self.cid)
        try:
            self.check_peers(self.service.online(other))
        except ValueError as error:
            return self.close(message=f'交易已取消。{error}')
        return ()

    def check_peers(self, target):
        if target is None or self.game.battle or target.battle:
            raise ValueError('双方须在线并处于非战斗状态。')
        a, b = self.game.character, target.character
        if a['map_id'] != b['map_id'] or max(abs(a[k] - b[k]) for k in ('x', 'y')) > 12:
            raise ValueError('请走近对方再交易。')

    def guardian_preview(self, sid):
        session = self.service.trades.get(self.cid)
        if not session or session['expires'] <= monotonic():
            return None
        other = next(cid for cid in session['members'] if cid != self.cid)
        assets = session.get('guardian_offers', {}).get(other, ())
        if not any(asset[0] == sid for asset in assets):
            return None
        peer = self.service.online(other)
        self.check_peers(peer)
        from native946_guardian_ui import GuardianUI

        return GuardianUI(peer).information(sid)

    def begin(self, target):
        if target == self.cid:
            raise ValueError('不能与自己交易。')
        peer = self.service.online(target)
        self.check_peers(peer)
        for cid in (self.cid, target):
            previous = self.service.trades.get(cid)
            if previous is not None and previous['expires'] <= monotonic():
                self.close(member=cid, message='交易等待超时，已取消。')
            if cid in self.service.trades:
                raise ValueError('双方须先结束当前交易。')
        with transaction(self.service.database) as db:
            require_trade_accepting(db, target)

            def opening(other):
                c = other.character
                return packet(2058, ['number', 'string', 'number', 'number'],
                              [c['id'], c['name'], c['role_id'], identity(db, c['id'])['showid']])
            left, right = opening(peer), opening(self.game)
        session = dict(members=(self.cid, target), offers={}, guardian_offers={}, confirmed=set(),
                       expires=monotonic()+300)
        self.service.trades[self.cid] = self.service.trades[target] = session
        self.service.enqueue(peer, right)
        return (left,)

    @staticmethod
    def validate(db, cid, cash, items, *, realm=False):
        balance = db.execute('SELECT gold FROM characters WHERE id=?', (cid,)).fetchone()[0]
        if not 0 <= cash <= balance:
            raise ValueError('交易金额无效或现金不足。')
        active_pages = pages(db, cid)
        allowed_types = TRADE_ITEM_TYPES if realm else ITEM_TYPES
        for row in items:
            iid = row['id']
            actual = db.execute('SELECT page,type,amount FROM inventory WHERE id=? AND character_id=?', (iid, cid)).fetchone()
            if (actual is None or actual['page'] not in active_pages or actual['type'] != row['type']
                    or not 0 < row['amount'] <= actual['amount'] or row['type'] not in allowed_types):
                raise ValueError('报价里有不属于当前角色、数量变化或尚不支持交易的物品。')
            require_unlocked(db, cid, iid)
            if db.execute('SELECT 1 FROM cosmetic_expiry WHERE item_id=? AND expires<>0', (iid,)).fetchone():
                raise ValueError('限时物品不可通过此交易入口转移。')
            if actual['type'] in REALM_GEMS and (actual['amount'] != 1 or row['amount'] != 1):
                raise ValueError('境宝石必须按单颗实例交割。')
        return balance

    def receive(self, mid, reader):
        if mid == 2066:
            target = reader.integer()
            reader.finish()
            return self.begin(target)
        if mid == 2063:
            reader.finish()
            return self.close()
        session = self.service.trades.get(self.cid)
        if session is None:
            return (text_notice('交易会话已结束，请重新发起。'),)
        if session['expires'] <= monotonic():
            return self.close(message='交易等待超时，已取消。')
        other = next(cid for cid in session['members'] if cid != self.cid)
        peer = self.service.online(other)
        try:
            self.check_peers(peer)
        except ValueError as error:
            return self.close(message=f'交易已取消。{error}')
        if mid == 2065:
            try:
                cash = reader.integer()
                items = [{name: reader.integer() for name, _ in SCHEMAS['trade_item']}
                         for _ in range(reader.take(1)[0])]
                guardians = []
                guardian_count = reader.take(1)[0]
                limit = LOCAL_RULES['guardian_offer_limit']
                if guardian_count > limit:
                    raise ValueError(f'一次交易报价最多可放入{limit}只守护。')
                for _ in range(guardian_count):
                    guardians.append(reader.integer())
                    reader.string()  # The displayed name is loaded from the save.
                reader.finish()
                if (cash > MAX_CASH or len({row['id'] for row in items}) != len(items)
                        or len(set(guardians)) != len(guardians)):
                    raise ValueError('交易金额过大或报价出现重复。')
                # The client supplies the display flag, but amount and ownership
                # are checked against the actual inventory.
                for item in items:
                    item['usable'] = 0
                locked = session['offers'].get(self.cid)
                guardian_offers = session.setdefault('guardian_offers', {})
                if locked is not None:
                    if locked == (cash, items) and guardians == [row[0] for row in guardian_offers.get(self.cid, ())]:
                        return (packet(2062, ['number'], [1]),)
                    return self.close(message='已锁定的交易报价发生变化，请重新发起交易。')
                with transaction(self.service.database) as db:
                    self.validate(db, self.cid, cash, items, realm=True)
                    pets = offered_guardians(db, self.cid, peer, guardians)
                    visible = packet(2061, OFFER_FIELDS, [cash, items, pets])
                    from native946_progression import DATA

                    gems = [f"{DATA['items'][str(row['type'])]['name']}（{realm_level(db, row['id'])}级）"
                            for row in items if row['type'] in REALM_GEMS]
                session['offers'][self.cid] = cash, items
                guardian_offers[self.cid] = tuple((pet['id'], pet['name'], pet['level']) for pet in pets)
                self.service.enqueue(peer, visible)
                if gems:
                    self.service.enqueue(peer, text_notice('对方锁定的境宝石：' + '、'.join(gems) + '。'))
                if pets:
                    labels = '、'.join(f"{pet['name']}（{pet['level']}级）" for pet in pets)
                    self.service.enqueue(peer, text_notice(f'对方锁定的守护：{labels}。'))
                return (packet(2062, ['number'], [1]),)
            except (ValueError, StorageBusy) as error:
                if self.cid in session['offers']:
                    return self.close(message='已锁定的交易报价发生变化，请重新发起交易。')
                return (packet(2062, ['number'], [0]), text_notice(str(error)))

        reader.finish()
        if set(session['offers']) != set(session['members']):
            return (text_notice('请等待双方锁定后再确认交易。'),)
        if other not in session['confirmed']:
            if self.cid not in session['confirmed']:
                session['confirmed'].add(self.cid)
                self.service.enqueue(peer, text_notice('对方已确认交易。'))
            return (text_notice('已确认，等待对方确认。'),)
        try:
            updates = self.settle(peer, session['offers'], trade_receiver=session['members'][1],
                                  guardian_offers=session.get('guardian_offers'))
        except (ValueError, StorageBusy) as error:
            return self.close(message=f'交易已取消。{error}')
        end = self.close(2060, message='交易完成。')
        self.service.enqueue(peer, *updates[other])
        return (*end, *updates[self.cid])

    @staticmethod
    def receive_item(db, receiver, item):
        from native946_progression import Progression

        kind, amount = item['type'], item['amount']
        maximum = Progression.stack_limit(item, {'equipment': {}})
        active_pages = pages(db, receiver)
        bag = [r for r in db.execute('SELECT id,type,page,slot,amount FROM inventory WHERE character_id=?', (receiver,))
               if r['page'] in active_pages]
        for row in bag:
            if row['type'] != kind or row['amount'] >= maximum:
                continue
            try:
                require_unlocked(db, receiver, row['id'])
            except ValueError:
                continue
            if db.execute('SELECT 1 FROM cosmetic_expiry WHERE item_id=? AND expires<>0', (row['id'],)).fetchone():
                continue
            moved = min(amount, maximum - row['amount'])
            db.execute('UPDATE inventory SET amount=amount+? WHERE id=?', (moved, row['id']))
            amount -= moved
            if not amount:
                return
        used = {(r['page'], r['slot']) for r in bag}
        for page in active_pages:
            for slot in range(1, 25):
                if (page, slot) in used:
                    continue
                moved = min(maximum, amount)
                db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',
                           (receiver, kind, page, slot, moved))
                amount -= moved
                if not amount:
                    return
        raise ValueError('接收方背包空间不足，交易未执行。')

    def settle(self, peer, offers, *, gift=False, trade_receiver=None, guardian_offers=None):
        games = (self.game, peer)
        ids = (self.cid, peer.character['id'])
        progressions = {game.progression.cid: game.progression for game in games}
        prior = {g.progression.cid: (g.progression.known_items, g.progression._known_item_positions,
                                     getattr(g.progression, '_known_item_quantities', None)) for g in games}
        updates = {}
        try:
            with transaction(self.service.database, write=True) as db:
                if gift:
                    from native946_give import require_accepting
                    require_accepting(db, *ids)
                if trade_receiver is not None:
                    require_trade_accepting(db, trade_receiver)
                balances = {cid: self.validate(db, cid, *offers[cid], realm=not gift) for cid in ids}
                guardians = {cid: [] for cid in ids}
                if guardian_offers and any(guardian_offers.values()):
                    for sender, receiver in ((ids[0], peer), (ids[1], self.game)):
                        quote = guardian_offers.get(sender, ())
                        rows = offered_guardians(db, sender, receiver, [row[0] for row in quote])
                        if tuple((pet['id'], pet['name'], pet['level']) for pet in rows) != quote:
                            raise ValueError('守护的名称或等级有变化，请重新报价。')
                        guardians[sender] = rows
                    move_guardians(db, *ids, guardians)
                # Debit both sides first so each side can use the slots it frees.
                for cid, other in (ids, ids[::-1]):
                    cash, items = offers[cid]
                    new = balances[cid] - cash + offers[other][0]
                    if new > MAX_CASH:
                        raise ValueError('接收方的现金已达上限。')
                    db.execute('UPDATE characters SET gold=? WHERE id=?', (new, cid))
                    for row in items:
                        if row['type'] in REALM_GEMS:
                            # Reserve the instance outside the visible slots until
                            # both sides have freed the slots in their offers.
                            db.execute('UPDATE inventory SET slot=-id WHERE id=? AND character_id=?',
                                       (row['id'], cid))
                            continue
                        removed = db.execute('DELETE FROM inventory WHERE id=? AND character_id=? AND amount=?',
                                             (row['id'], cid, row['amount'])).rowcount
                        if not removed:
                            db.execute('UPDATE inventory SET amount=amount-? WHERE id=? AND character_id=?',
                                       (row['amount'], row['id'], cid))
                for sender, receiver in (ids, ids[::-1]):
                    for row in offers[sender][1]:
                        if row['type'] in REALM_GEMS:
                            page, slot = progressions[receiver].free_bag_slot()
                            db.execute('UPDATE inventory SET character_id=?,page=?,slot=? WHERE id=?',
                                       (receiver, page, slot, row['id']))
                        else:
                            self.receive_item(db, receiver, row)
                for game in games:
                    p = game.progression
                    p._cached_snapshot = None
                    snapshot = p._snapshot(db)
                    pets = game.summons.sync() if any(guardians.values()) else ()
                    updates[p.cid] = (*p.item_packets(snapshot), *p.attribute_packets(snapshot), *pets)
        except BaseException:
            for game in games:
                p = game.progression
                p.known_items, p._known_item_positions, p._known_item_quantities = prior[p.cid]
                p._cached_snapshot = None
            raise
        return updates

