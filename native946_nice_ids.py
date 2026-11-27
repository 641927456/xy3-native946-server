"""Ordinary-server number leases restricted to the documented number catalog."""

from calendar import monthrange
from datetime import datetime, timedelta, timezone
from time import monotonic, time

from native946_codec import packet, text_notice


NPC = (2001, 4775)
REQUESTS = frozenset(range(1859, 1867))
WRITES = frozenset({1860, 1861, 1862})
PAGE_SIZE = 9
FAVORITE_LIMIT = 9
SINGLE_TICKET_PACK = 55219
CERTIFICATE = 45805
VOUCHERS = (45844, 55197, 65197)
ITEMS = {str(CERTIFICATE): dict(name='极品靓号',icon=6429,max_stack=1,
                              desc='全服独享极品靓号专属凭证。右键使用后更换显示ID。#r【不可交易】')}
ZH_TIME = timezone(timedelta(hours=8))
PROTECTION = 59 * 86400

# Official 2013 number-system guide, screenshots 377157 and 377163.
# (Number, native theme, ticket price). The archived promotion is separate.
QUOTES = (
    (24582, 1, 16),
    (19850205, 3, 12),
    (61070, 1, 16),
    (19920727, 3, 12),
    (31149, 1, 16),
    (20070520, 3, 12),
    (31104, 1, 16),
    (65713, 1, 16),
    (19740805, 3, 12),
    (69828, 1, 16),
    (82444, 1, 16),
)
PRICES = {showid: price for showid, _, price in QUOTES}
ARCHIVED_PROMOTIONS = {69828: 8}
CATALOG_NOTICE = '本机按官网历史号码和券数开放选号，69828保留历史五折价，续费券数按六折向上取整。'


def calendar_shift(timestamp, months):
    date = datetime.fromtimestamp(timestamp, ZH_TIME)
    year, month = divmod(date.year * 12 + date.month - 1 + months, 12)
    return int(date.replace(year=year, month=month+1,
                            day=min(date.day,monthrange(year,month+1)[1])).timestamp())


def active_lease(db, cid, now):
    return db.execute('SELECT * FROM nice_id_leases WHERE character_id=? AND active=1 AND expires+?>?',
                      (cid, PROTECTION, now)).fetchone()


def identity(db, cid, now=None):
    now = int(time()) if now is None else now
    lease = active_lease(db, cid, now)
    if lease is not None and lease['expires'] > now:
        return dict(showid=lease['showid'], showid_expires=lease['expires'])
    return dict(showid=cid, showid_expires=0)


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS nice_id_favorites (
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        showid INTEGER NOT NULL,
        PRIMARY KEY(character_id, showid)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS nice_id_leases (
        showid INTEGER PRIMARY KEY,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        item_id INTEGER UNIQUE,
        active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
        expires INTEGER NOT NULL
    )''')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS nice_id_one_active ON nice_id_leases(character_id) WHERE active=1')


class NiceIds:
    def __init__(self, game):
        self.game = game
        self.progression = game.progression

    def open(self):
        if self.game.characters.trial_server:
            raise ValueError('试玩服可右键使用对应的靓号购买券兑换通宝。')
        self.game.npc_service_session = dict(
            kind='nice_id', npc=NPC[1], scene=NPC[0], expires=monotonic() + 600
        )
        return (
            packet(1183, ['string'], ['dlg_npc_chat']),
            self.list_packet(1, 0, 1),
            text_notice(CATALOG_NOTICE),
        )

    def list_packet(self, tab, theme, page, *, query=''):
        if tab not in (1, 2, 3) or theme not in range(6) or page < 1:
            raise ValueError('靓号查询条件无效。')
        now = int(time())
        rows = []
        favorites = set()
        with self.progression.transaction(write=False) as db:
            lease = active_lease(db,self.progression.cid,now)
            busy = {row['showid']:row['expires'] for row in db.execute(
                'SELECT * FROM nice_id_leases WHERE expires+CASE active WHEN 1 THEN ? ELSE 0 END>?',
                (PROTECTION,now))}
            if tab == 3:
                favorites = {row[0] for row in db.execute(
                    'SELECT showid FROM nice_id_favorites WHERE character_id=?',
                    (self.progression.cid,)
                )}
        for showid, category, price in QUOTES:
            if (theme and category != theme) or (query and query not in str(showid)):
                continue
            if (tab == 2 and showid not in ARCHIVED_PROMOTIONS) or (tab == 3 and showid not in favorites):
                continue
            current = ARCHIVED_PROMOTIONS.get(showid,price)
            rows.append(dict(id=showid, text=str(showid), price=current, ori_price=price,
                             end_day=busy.get(showid,0)))

        # The native search panel paginates its result list locally.
        total = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total)
        if not query:
            start = (page - 1) * PAGE_SIZE
            rows = rows[start:start + PAGE_SIZE]
        return packet(1857, ['number', 'number', ('array', 'niceid'), 'number', 'number', 'byte'],
                      [tab, theme, rows, page, total, int(self.can_renew(lease,now))])

    def collect(self, operation, showid):
        if showid not in PRICES or operation not in (1, 2):
            raise ValueError('靓号收藏请求无效。')
        cid = self.progression.cid
        with self.progression.transaction() as db:
            if operation == 1:
                saved = {row[0] for row in db.execute(
                    'SELECT showid FROM nice_id_favorites WHERE character_id=?', (cid,)
                )}
                if showid not in saved and len(saved) >= FAVORITE_LIMIT:
                    raise ValueError('最多可收藏9个靓号。')
                db.execute('INSERT OR IGNORE INTO nice_id_favorites VALUES(?, ?)', (cid, showid))
            else:
                db.execute('DELETE FROM nice_id_favorites WHERE character_id=? AND showid=?', (cid, showid))
        if operation == 2:
            return self.list_packet(3, 0, 1)
        return text_notice('已收藏该靓号。')

    @staticmethod
    def can_renew(lease, now):
        return lease is not None and calendar_shift(lease['expires'],-1)<=now<lease['expires']+PROTECTION

    def voucher_rows(self, db, now):
        from native946_mall_bags import pages
        available = pages(db,self.progression.cid)
        slots = ','.join('?' for _ in available)
        return db.execute(
            'SELECT i.* FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id '
            f'WHERE i.character_id=? AND i.type IN (?,?,?) AND i.page IN ({slots}) '
            'AND (coalesce(e.expires,0)=0 OR e.expires>?) '
            "AND NOT EXISTS (SELECT 1 FROM native_object_locks l WHERE l.kind='item' AND l.object_id=i.id) "
            'ORDER BY CASE WHEN coalesce(e.expires,0)=0 THEN 1 ELSE 0 END,e.expires,i.id',
            (self.progression.cid,*VOUCHERS,*available,now),
        ).fetchall()

    def offer(self, db, now, showid, renewal):
        if renewal:
            lease = active_lease(db,self.progression.cid,now)
            if not self.can_renew(lease,now):
                raise ValueError('只有到期前一个月及到期后59天内可续期。')
            showid = lease['showid']
        else:
            if showid not in PRICES:
                raise ValueError('该靓号不在本机投放目录。')
            exists = db.execute('SELECT 1 FROM characters WHERE id=?', (showid,)).fetchone()
            held = db.execute(
                'SELECT 1 FROM nice_id_leases WHERE showid=? AND expires+CASE active WHEN 1 THEN ? ELSE 0 END>?',
                (showid,PROTECTION,now),
            ).fetchone()
            if exists or held:
                raise ValueError('该靓号已被占用或处于保护期。')
        original = PRICES[showid]
        current = ARCHIVED_PROMOTIONS.get(showid,original)
        if renewal:
            current = (current*6+9)//10
        return dict(id=showid,text=str(showid),price=current,ori_price=current if renewal else original,end_day=0)

    def quote(self, session, showid=None, *, renewal=False):
        now = int(time())
        with self.progression.transaction(write=False) as db:
            row = self.offer(db,now,showid,renewal)
            owned = sum(item['amount'] for item in self.voucher_rows(db,now))
        session['quote'] = (renewal,row['id'],row['price'],monotonic()+120)
        return packet(1858 if renewal else 1856,['niceid']+['number']*5,
                      [row,45844,owned,row['price'],0,0])

    def confirm(self, session, showid=None, *, renewal=False):
        from native946_cosmetics import Cosmetics
        from native946_mall_uses import consume
        now = int(time())
        cid = self.progression.cid
        with self.progression.transaction() as db:
            row = self.offer(db,now,showid,renewal)
            showid, price = row['id'], row['price']
            quote = session.get('quote')
            if not quote or quote[:3]!=(renewal,showid,price) or quote[3]<monotonic():
                raise ValueError('请重新确认靓号及券数。')
            tickets = self.voucher_rows(db,now)
            if sum(item['amount'] for item in tickets)<price:
                raise ValueError('有效且未锁定的靓号购买券不足。')
            due = price
            for item in tickets:
                amount = min(due,item['amount'])
                consume(db,item,amount)
                due -= amount
                if not due:
                    break
            if renewal:
                lease = active_lease(db,cid,now)
                expiry = calendar_shift(max(now,lease['expires']),12)
                db.execute('UPDATE nice_id_leases SET expires=? WHERE showid=?',(expiry,showid))
            else:
                certificate = Cosmetics(self.game)._add(db,CERTIFICATE,backpack=True)
                expiry = calendar_shift(now,12)
                db.execute('UPDATE cosmetic_expiry SET expires=? WHERE item_id=?',(expiry,certificate))
                db.execute('DELETE FROM nice_id_leases WHERE showid=?',(showid,))
                db.execute('INSERT INTO nice_id_leases(showid,character_id,item_id,expires) VALUES(?,?,?,?)',
                           (showid,cid,certificate,expiry))
        session.pop('quote',None)
        message = '靓号续期成功。' if renewal else '靓号已购买，请右键使用背包内的极品靓号凭证。'
        return self.game.defer_packets(lambda: (*self.progression.item_packets(),*self.refresh_identity(),
                                                 self.list_packet(1,0,1),text_notice(message)))

    def activate(self, db, item):
        from native946_mall_uses import consume
        cid = self.progression.cid
        lease = db.execute('SELECT * FROM nice_id_leases WHERE item_id=? AND character_id=? AND active=0 AND expires>?',
                           (item['id'],cid,int(time()))).fetchone()
        if lease is None:
            raise ValueError('该靓号凭证无效或已到期。')
        db.execute('DELETE FROM nice_id_leases WHERE character_id=? AND active=1',(cid,))
        db.execute('UPDATE nice_id_leases SET active=1,item_id=NULL WHERE showid=?',(lease['showid'],))
        consume(db,item)
        return self.game.defer_packets(lambda: (*self.progression.item_packets(),*self.refresh_identity(),
                                                 text_notice(f"已启用靓号 {lease['showid']}。")))

    def refresh_identity(self):
        from native946_characters import scene_actor_packet
        from native946_progression import DATA
        character = self.game.character
        cid = self.progression.cid
        previous = character.get('showid',cid)
        with self.progression.transaction(write=False) as db:
            character.update(identity(db,cid))
        if character['showid']==previous:
            return ()
        changed = packet(695,['number','number'],[int(DATA['attributes']['ShowId']),character['showid']])
        remove = packet(1596,['number'],[cid])
        actor = scene_actor_packet(character)
        service = self.game.party_service
        with service.lock:
            for peer in service.games.values():
                if (peer is not self.game and not peer.battle and peer.character['map_id']==character['map_id']
                    and max(abs(peer.character[k]-character[k]) for k in ('x','y'))<=96):
                    service.enqueue(peer,remove,actor)
        return changed,

    def receive(self, mid, reader):
        from native946_npc_services import NpcServices

        session = NpcServices(self.game)._session('nice_id', NPC[1])
        if mid == 1863:
            tab, theme, page = (reader.integer() for _ in range(3))
            reader.finish()
            return self.list_packet(tab, theme, page)
        if mid == 1865:
            query = reader.string()
            reader.finish()
            if not 1 <= len(query) <= 8 or not query.isdigit():
                raise ValueError('请输入1至8位数字查询靓号。')
            return self.list_packet(1, 0, 1, query=query.decode('ascii'))
        if mid == 1860:
            operation, showid = reader.integer(), reader.integer()
            reader.finish()
            return self.collect(operation, showid)
        if mid == 1866:
            item_num = reader.integer()
            reader.finish()
            if item_num != 0:
                raise ValueError('靓号购券请求无效。')
            from native946_cosmetics import Cosmetics
            cosmetics = Cosmetics(self.game)
            return (*cosmetics.currency_packets(), cosmetics.mall(SINGLE_TICKET_PACK))

        showid = reader.integer() if mid in (1859,1861) else None
        reader.finish()
        if mid in (1859,1864):
            return self.quote(session,showid,renewal=mid==1864)
        return self.confirm(session,showid,renewal=mid==1862)
