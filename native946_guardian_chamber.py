"""Player-owned guardian counters using 946 dialogue and guardian records."""
from time import monotonic

from native946_afei_society import CHAMBER, CLOSE, OPEN_CASH, OPEN_LEVEL, PRICE_STEPS, Chamber, spend_cash
from native946_codec import packet, text_notice


COUNTER_LIMIT = 10
SELLER_PERCENT = 90
WINDOW_REQUESTS = {1839,1841,1845,1849}
WINDOW_BUY = 1839
SUMMON_SHOP = 2
SHOPS_PER_PAGE = 10
NOT_RELEASED = 'NOT EXISTS (SELECT 1 FROM native_guardian_releases WHERE summon_id=summons.id)'
AVAILABLE = NOT_RELEASED + ' AND NOT EXISTS (SELECT 1 FROM native_guardian_shop_stock WHERE summon_id=summons.id)'


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_shops (
        owner INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        open INTEGER NOT NULL CHECK(open IN (0,1)),
        proceeds INTEGER NOT NULL DEFAULT 0 CHECK(proceeds>=0))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_shop_stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner INTEGER NOT NULL REFERENCES native_guardian_shops(owner) ON DELETE CASCADE,
        summon_id INTEGER NOT NULL UNIQUE REFERENCES summons(id) ON DELETE CASCADE,
        price INTEGER NOT NULL CHECK(price>0))''')
    db.execute('CREATE INDEX IF NOT EXISTS native_guardian_shop_owner ON native_guardian_shop_stock(owner,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_shop_sales (
        listing INTEGER PRIMARY KEY, seller INTEGER NOT NULL REFERENCES characters(id),
        buyer INTEGER NOT NULL REFERENCES characters(id), summon_id INTEGER NOT NULL REFERENCES summons(id),
        grant_key TEXT NOT NULL, price INTEGER NOT NULL CHECK(price>0),
        proceeds INTEGER NOT NULL CHECK(proceeds>=0), UNIQUE(seller,grant_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_trade_transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender INTEGER NOT NULL REFERENCES characters(id),
        receiver INTEGER NOT NULL REFERENCES characters(id),
        summon_id INTEGER NOT NULL REFERENCES summons(id),
        grant_key TEXT NOT NULL,
        UNIQUE(sender,grant_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_releases (
        summon_id INTEGER PRIMARY KEY REFERENCES summons(id) ON DELETE CASCADE,
        released_at INTEGER NOT NULL)''')


def has_sale(db, owner, grant_key):
    return db.execute('SELECT 1 FROM native_guardian_shop_sales WHERE seller=? AND grant_key=?',
                      (owner, grant_key)).fetchone() is not None


def has_transferred(db, owner, grant_key):
    return db.execute('SELECT 1 FROM native_guardian_trade_transfers WHERE sender=? AND grant_key=?',
                      (owner, grant_key)).fetchone() is not None


def carried_count(db, cid):
    return db.execute('SELECT count(*) FROM summons WHERE character_id=? AND ' + NOT_RELEASED, (cid,)).fetchone()[0]


def validate_stock(db, owner, pet):
    from native946_inventory_locks import records

    if pet['grant_key'] == 'starter':
        raise ValueError('本机新手守护不可上架。')
    if pet['fight'] or pet['show']:
        raise ValueError('先将守护休息并取消展示。')
    if pet['id'] in records(db, owner, 'summon'):
        raise ValueError('请先解除守护锁。')
    if db.execute('SELECT 1 FROM native_guardian_jade WHERE summon_id=? LIMIT 1', (pet['id'],)).fetchone():
        raise ValueError('携带宝玉的守护暂不能在商铺上架或成交，请先卸下宝玉。')
    bundle = attachment_rows(db, pet['id'])
    locked = records(db, owner)
    if any(row['character_id'] != owner or row['id'] in locked or row['expires'] for row in bundle):
        raise ValueError('随售装备归属异常、被锁定或有期限，请先卸下。')
    return bundle


def attachment_rows(db, sid):
    return list(db.execute('''SELECT i.id,i.character_id,i.page,i.slot,coalesce(e.expires,0) AS expires
        FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id
        WHERE i.id IN (SELECT item_id FROM native_guardian_equipment WHERE summon_id=?)
        ORDER BY i.id''',(sid,)))


class GuardianChamber(Chamber):
    def menu(self, message, choices):
        self.g.dialog_actions = [dict(kind='guardian_shop', **action) for _, action in choices]
        return self.g.menu(message, [label for label, _ in choices], self.dialog_actor())

    def root(self):
        if self.is_remote():
            return self.menu('远程守护商铺', [('浏览在售守护', dict(op='browse', page=0)),
                                                ('打开守护商铺列表', dict(op='native_browse')),
                                                ('返回商会', dict(op='chamber'))])
        with self.p.transaction(write=False) as db:
            shop = db.execute('SELECT * FROM native_guardian_shops WHERE owner=?', (self.p.cid,)).fetchone()
        text = ('守护商铺。每柜10只，成交后店主收九成。价格档位、寄存占携带位置为本机规则。'
                '#r请先卸下宝玉再上架。本机允许符合条件的守护装备随售。')
        choices = [('浏览在售守护', dict(op='browse', page=0))]
        choices.append(('打开守护商铺列表',dict(op='native_browse')))
        if not shop:
            choices.append((f'开设守护商铺（{OPEN_LEVEL}级，{OPEN_CASH}现金）', dict(op='open')))
        else:
            text += f"#r{'营业' if shop['open'] else '歇业'}。待提取{shop['proceeds']}现金。"
            choices.extend([
                ('选择守护上架', dict(op='pets', page=0)), ('查看或撤回守护', dict(op='own', page=0)),
                ('暂停营业' if shop['open'] else '恢复营业', dict(op='toggle')),
                ('取出守护商铺收入', dict(op='withdraw')),
            ])
        choices.append(('返回商会总管', dict(op='chamber')))
        return self.menu(text, choices)

    def listing(self, op, page):
        if type(page) is not int or not 0 <= page < 1000:
            raise ValueError('商铺页码无效。')
        if op == 'pets':
            rows = [r for r in self.g.summons.snapshot()
                    if r['grant_key'] != 'starter' and not (r['fight'] or r['show'] or r['lock'] or r['lock_block_time'])]
            entries = [(f"{r['name']}（{r['level']}级）", dict(op='price', summon=r['id'])) for r in rows]
        else:
            condition = 's.owner=?' if op == 'own' else 's.open=1 AND s.owner<>?'
            with self.p.transaction(write=False) as db:
                rows = db.execute('SELECT l.id,l.price,p.name,p.level,c.name AS seller FROM native_guardian_shop_stock l '
                                  'JOIN native_guardian_shops s ON s.owner=l.owner '
                                  'JOIN summons p ON p.id=l.summon_id AND p.character_id=l.owner '
                                  'JOIN characters c ON c.id=s.owner WHERE '+condition+' ORDER BY l.id',
                                  (self.p.cid,)).fetchall()
            entries = [(f"{r['name']}（{r['level']}级，{r['price']}现金，{r['seller']}）",
                        dict(op='detail', listing=r['id'], own=op=='own')) for r in rows]
        return self.menu('请选择守护。' if entries else '这里暂时没有可选守护。',
                         self.paging(entries[page*6:(page+1)*6], op, page, len(entries)))

    @staticmethod
    def stock(db, listing):
        return db.execute('SELECT l.id AS listing,l.price,l.owner,s.open,p.* FROM native_guardian_shop_stock l '
                          'JOIN native_guardian_shops s ON s.owner=l.owner '
                          'JOIN summons p ON p.id=l.summon_id AND p.character_id=l.owner WHERE l.id=?',
                          (listing,)).fetchone()

    def details(self, action):
        from native946_growth import species

        with self.p.transaction(write=False) as db:
            row = self.stock(db, action['listing'])
            own = action['own']
            if not row or (row['owner'] == self.p.cid) != own or (not own and not row['open']):
                raise ValueError('守护已售出、撤回或商铺已关闭。')
            pet = self.g.summons.stats(row)
            attached = len(attachment_rows(db,row['id']) if own else validate_stock(db,row['owner'],row))
        text = (f"{pet['name']}（{species(pet['type'])['name']}），{pet['level']}级。售价{row['price']}现金。"
                f"#r成长{pet['growth_base']/1000:g}，气血{pet['hpmax']}，法力{pet['mpmax']}，"
                f"攻击{pet['attack']}，速度{pet['initiative']}。#r本机随售装备共{attached}件。")
        label = '取回该守护' if own else f"确认购买（{row['price']}现金）"
        return self.menu(text, [(label, dict(op='unlist' if own else 'buy', listing=row['listing'], price=row['price'])),
                                ('返回守护商铺', dict(op='root'))])

    def execute(self, nid, action):
        op = action['op']
        self.validate_access(nid, op, {'chamber','root','native_browse','browse','detail','buy'})
        if op == 'chamber':
            return Chamber(self.g).root()
        if op == 'root':
            return self.root()
        if op == 'native_browse':
            remote = self.is_remote()
            self.g.npc_service_session = dict(kind='guardian_shop_window',scene=self.g.character['map_id'],npc=CHAMBER[1],
                                              expires=monotonic()+300,shop=None,quotes={},remote_chamber=remote)
            return (CLOSE,self.native_catalog(SUMMON_SHOP,1))
        if op in ('pets','own','browse'):
            return self.listing(op, action['page'])
        if op == 'detail':
            return self.details(action)
        if op == 'price':
            with self.p.transaction(write=False) as db:
                pet = self.g.summons._owned(db, action['summon'])
                attached = len(validate_stock(db, self.p.cid, pet))
            return self.menu(f'选择售价。成交后店主获得九成。本机随售装备共{attached}件。', [
                (f'{price}现金', dict(op='post', summon=pet['id'], price=price)) for price in PRICE_STEPS])
        with self.p.transaction() as db:
            cid = self.p.cid
            shop = db.execute('SELECT * FROM native_guardian_shops WHERE owner=?', (cid,)).fetchone()
            if op == 'open':
                if shop or self.p._snapshot(db)['level'] < OPEN_LEVEL:
                    raise ValueError(f'须满{OPEN_LEVEL}级，且每个角色只能开一间守护商铺。')
                spend_cash(db, cid, OPEN_CASH)
                db.execute('INSERT INTO native_guardian_shops(owner,open) VALUES(?,1)', (cid,))
                message = '已开设守护商铺。'
            elif op in ('unlist','buy'):
                row = self.stock(db, action['listing'])
                if not row or row['price'] != action['price']:
                    raise ValueError('守护已售出、撤回或价格有变化。')
                if op == 'buy':
                    self.buy(db, row)
                elif row['owner'] != cid:
                    raise ValueError('只能取回自己上架的守护。')
                db.execute('DELETE FROM native_guardian_shop_stock WHERE id=?', (row['listing'],))
                if op == 'unlist':
                    from native946_summons import guardian_stage_level_cap

                    cap = guardian_stage_level_cap(row)
                    if row['level'] > cap:
                        self.g.summons._restore_stage_cap(db, row, cap)
                message = '已购入守护。' if op == 'buy' else '已取回守护。'
            elif not shop:
                raise ValueError('请先开设守护商铺。')
            elif op == 'post':
                pet = self.g.summons._owned(db, action['summon'])
                validate_stock(db, cid, pet)
                if action['price'] not in PRICE_STEPS:
                    raise ValueError('请选择有效售价。')
                if db.execute('SELECT count(*) FROM native_guardian_shop_stock WHERE owner=?', (cid,)).fetchone()[0] >= COUNTER_LIMIT:
                    raise ValueError('柜台已满。')
                db.execute('INSERT INTO native_guardian_shop_stock(owner,summon_id,price) VALUES(?,?,?)',
                           (cid, pet['id'], action['price']))
                message = '守护已上架。'
            elif op == 'toggle':
                db.execute('UPDATE native_guardian_shops SET open=1-open WHERE owner=?', (cid,))
                message = '已暂停营业。' if shop['open'] else '已恢复营业。'
            elif op == 'withdraw' and shop['proceeds']:
                db.execute('UPDATE native_guardian_shops SET proceeds=0 WHERE owner=?', (cid,))
                db.execute('UPDATE characters SET gold=gold+? WHERE id=?', (shop['proceeds'], cid))
                message = f"已提取{shop['proceeds']}现金。"
            else:
                raise ValueError('没有可办理的操作或可提取的收入。')
        self.p._cached_snapshot = None
        return self.g.defer_packets(lambda: (CLOSE, *self.p.attribute_packets(), *self.p.item_packets(), *self.g.summons.sync(),
                                            text_notice(message)))

    def native_catalog(self, shop_type, page):
        if shop_type not in (1,SUMMON_SHOP) or page < 1:
            raise ValueError('商铺类别或页码无效。')
        rows = []
        with self.p.transaction(write=False) as db:
            if shop_type == SUMMON_SHOP:
                rows = db.execute('SELECT s.owner,c.name FROM native_guardian_shops s '
                                  'JOIN characters c ON c.id=s.owner WHERE s.open=1 AND s.owner<>? '
                                  'AND EXISTS(SELECT 1 FROM native_guardian_shop_stock l WHERE l.owner=s.owner) '
                                  'ORDER BY s.owner',(self.p.cid,)).fetchall()
        maximum = max(1,(len(rows)+SHOPS_PER_PAGE-1)//SHOPS_PER_PAGE)
        page = min(page,maximum)
        records = [dict(id=r['owner'],type=SUMMON_SHOP,name=r['name']+'的守护商铺',valid_count=1,
                        total_count=1,owner_name=r['name'],date=0)
                   for r in rows[(page-1)*SHOPS_PER_PAGE:page*SHOPS_PER_PAGE]]
        return packet(1812,['number']*5+[('array','shop_info')],[CHAMBER[1],0,shop_type,page,maximum,records])

    def native_stock(self, shop):
        rows = []
        with self.p.transaction(write=False) as db:
            seller = db.execute('SELECT c.name FROM native_guardian_shops s JOIN characters c ON c.id=s.owner '
                                'WHERE s.owner=? AND s.open=1 AND s.owner<>?',(shop,self.p.cid)).fetchone()
            if seller:
                rows = db.execute('SELECT p.id,p.name,l.price,l.id AS listing FROM native_guardian_shop_stock l '
                                  'JOIN summons p ON p.id=l.summon_id AND p.character_id=l.owner '
                                  'WHERE l.owner=? ORDER BY l.id',(shop,)).fetchall()
        return seller,rows

    def native_counter(self, session, shop, *, open_panel=False):
        seller,rows = self.native_stock(shop)
        if not seller:
            raise ValueError('这间守护商铺已歇业或不可访问。')
        session.update(shop=shop,quotes={r['id']:(r['listing'],r['price']) for r in rows})
        stock = packet(1819,['number','number',('array','price_summon')],
                       [shop,1,[dict(id=r['id'],name=r['name'],price=r['price']) for r in rows]])
        if not open_panel:
            return stock
        header = dict(id=shop,type=SUMMON_SHOP,name=seller['name']+'的守护商铺',date=0,
                      owner_name=seller['name'],owner_id=shop,showid=shop)
        return (packet(1810,['shop_buy',('array','number')],[header,[1]]),stock)

    def native_request(self, mid, reader):
        from native946_npc_services import NpcServices

        session = NpcServices(self.g)._session('guardian_shop_window',CHAMBER[1])
        if mid == 1849:
            shop_type,page = reader.integer(),reader.integer()
            reader.finish()
            return self.native_catalog(shop_type,page)
        shop,value = reader.integer(),reader.integer()
        if mid == 1845:
            reader.finish()
            if value != 0:
                raise ValueError('请从守护商铺列表打开货架。')
            return self.native_counter(session,shop,open_panel=True)
        if shop != session['shop']:
            raise ValueError('请先打开此守护商铺。')
        if mid == 1841:
            reader.finish()
            if value != 1:
                raise ValueError('此守护商铺只有一个柜台。')
            return self.native_counter(session,shop)
        count,price = reader.integer(),reader.integer()
        reader.finish()
        quote = session['quotes'].get(value)
        if count != 1 or quote is None or quote[1] != price:
            raise ValueError('售价或柜台记录已变化，请重新打开货架。')
        response = self.execute(CHAMBER[1],dict(op='buy', listing=quote[0],price=price))
        return (response,self.native_counter(session,shop))

    def buy(self, db, row):
        from native946_summons import guardian_level_cap
        from native946_wilderness import carry_rank
        from native946_growth import species
        from native946_mall_bags import summon_limit

        cid = self.p.cid
        owner = row['owner']
        hero = self.p._snapshot(db)
        if owner == cid or not row['open']:
            raise ValueError('不能购买自己的守护或歇业商铺内的守护。')
        if (hero['level'] < OPEN_LEVEL or carry_rank(hero) < species(row['type'])['take_grade_num']
                or row['level'] > guardian_level_cap(hero['level'], row)):
            raise ValueError('未达到守护商会的购买条件或该守护的携带条件。')
        if carried_count(db, cid) >= summon_limit(db, cid):
            raise ValueError('守护携带栏已满。')
        bundle = validate_stock(db, owner, row)
        for item in bundle:
            if db.execute('SELECT 1 FROM inventory WHERE character_id=? AND page=? AND slot=?',
                          (cid,item['page'],item['slot'])).fetchone():
                raise ValueError('买家的守护装备格冲突，本次没有扣款。')
        spend_cash(db, cid, row['price'])
        proceeds = row['price'] * SELLER_PERCENT // 100
        db.execute('INSERT INTO native_guardian_shop_sales VALUES(?,?,?,?,?,?,?)',
                   (row['listing'], owner, cid, row['id'], row['grant_key'], row['price'], proceeds))
        db.execute('UPDATE summons SET character_id=?,grant_key=? WHERE id=? AND character_id=?',
                   (cid, f"guardian-shop:{row['listing']}", row['id'], owner))
        for item in bundle:
            for table in ('inventory','native_equipment','cosmetic_item_carvings','native_glyph_item_levels',
                          'native_temporary_glyphs','native_refinement','native_fashion_strength',
                          'native_equipment_attribute_sets','native_equipment_active_attributes'):
                key = 'id' if table == 'inventory' else 'item_id'
                db.execute(f'UPDATE {table} SET character_id=? WHERE {key}=? AND character_id=?',(cid,item['id'],owner))
        db.execute('UPDATE cosmetic_guardian_appearance SET character_id=? WHERE summon_id=? AND character_id=?',
                   (cid, row['id'], owner))
        db.execute('UPDATE native_guardian_shops SET proceeds=proceeds+? WHERE owner=?', (proceeds, owner))
