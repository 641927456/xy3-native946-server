"""Local item shops and marriage through the corresponding 946 NPCs."""
from time import monotonic, time

from native946_codec import packet, text_notice


CHAMBER = (2001, 1153)
REGISTRY = {(2081, 6753):'marry', (2081, 1043):'agree', (2081, 6755):'force'}
PRICE_STEPS = (100, 1_000, 10_000, 100_000, 1_000_000)
OPEN_CASH = 1
OPEN_LEVEL = 50
MARRY_CASH = 50_000
DIVORCE_CASH = 2_000_000
CLOSE = packet(1183, ['string'], ['dlg_npc_chat'])
LIST_PAGE = 6


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_afei_shops (
        owner INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        open INTEGER NOT NULL CHECK(open IN (0,1)), proceeds INTEGER NOT NULL DEFAULT 0 CHECK(proceeds>=0))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_afei_shop_goods (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL REFERENCES native_afei_shops(owner) ON DELETE CASCADE,
        kind INTEGER NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0), price INTEGER NOT NULL CHECK(price>0))''')
    db.execute('CREATE INDEX IF NOT EXISTS native_afei_shop_goods_owner ON native_afei_shop_goods(owner,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS native_afei_marriage (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        partner INTEGER NOT NULL UNIQUE REFERENCES characters(id) ON DELETE CASCADE,
        title TEXT NOT NULL, since INTEGER NOT NULL,
        CHECK(character_id<>partner))''')


def spend_cash(db, cid, amount):
    if not db.execute('UPDATE characters SET gold=gold-? WHERE id=? AND gold>=?',
                      (amount, cid, amount)).rowcount:
        raise ValueError('现金不足。')


def chamber_menu(game, npc):
    if (npc['scene_id'], npc['npc_id']) != CHAMBER:
        return None
    return Chamber(game).root()


def marriage_menu(game, npc):
    service = REGISTRY.get((npc['scene_id'], npc['npc_id']))
    if service is None:
        return None
    return Marriage(game).menu(npc['npc_id'], service)


class Chamber:
    def __init__(self, game):
        self.g = game
        self.p = game.progression

    def menu(self, message, choices):
        self.g.dialog_actions = [dict(kind='afei_shop', **action) for _, action in choices]
        return self.g.menu(message, [label for label, _ in choices], CHAMBER[1])

    def root(self):
        with self.p.transaction(write=False) as db:
            shop = db.execute('SELECT * FROM native_afei_shops WHERE owner=?', (self.p.cid,)).fetchone()
        choices = [('查看在售物品', dict(op='browse', page=0))]
        text = '物品商会。开店及资金流程参考阿飞，当前使用本机定价档位。'
        if shop is None:
            choices.append((f'开设物品商铺（{OPEN_LEVEL}级，{OPEN_CASH}现金）', dict(op='open')))
        else:
            text += f"#r状态：{'营业' if shop['open'] else '歇业'}。待提取：{shop['proceeds']}现金。"
            choices.extend([
                ('选择背包物品上架', dict(op='bag', page=0)),
                ('查看或撤回我的商品', dict(op='own', page=0)),
                ('暂停营业' if shop['open'] else '恢复营业', dict(op='toggle')),
                ('提取商铺收入', dict(op='withdraw')),
            ])
        choices.append(('守护商铺', dict(op='guardian')))
        return self.menu(text, choices)

    def paging(self, choices, op, page, total):
        if page:
            choices.append(('上一页', dict(op=op, page=page-1)))
        if (page+1)*LIST_PAGE < total:
            choices.append(('下一页', dict(op=op, page=page+1)))
        choices.append(('返回商会', dict(op='root')))
        return choices

    def listing(self, op, page):
        from native946_progression import DATA
        if type(page) is not int or not 0 <= page < 1000:
            raise ValueError('商会页码无效。')
        with self.p.transaction(write=False) as db:
            if op == 'bag':
                from native946_inventory_locks import records
                from native946_mall_bags import pages
                from native946_trade import ITEM_TYPES
                active = pages(db,self.p.cid)
                locked = records(db,self.p.cid)
                rows = [row for row in db.execute(
                    "SELECT i.* FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id "
                    "LEFT JOIN native_equipment q ON q.item_id=i.id "
                    "WHERE i.character_id=? AND coalesce(e.expires,0)=0 AND q.item_id IS NULL ORDER BY i.page,i.slot",
                    (self.p.cid,)) if row['type'] in ITEM_TYPES and row['page'] in active and row['id'] not in locked]
                entries = [(f"{DATA['items'][str(r['type'])]['name']}×{r['amount']}（{r['page']}页{r['slot']}格）",
                            dict(op='price', item=r['id'])) for r in rows[page*LIST_PAGE:(page+1)*LIST_PAGE]]
                return self.menu('选择整格上架的背包物品。', self.paging(entries, op, page, len(rows)))
            filter_sql = 'g.owner=?' if op == 'own' else 's.open=1 AND g.owner<>?'
            total = db.execute('SELECT count(*) FROM native_afei_shop_goods g JOIN native_afei_shops s ON s.owner=g.owner WHERE '+filter_sql,
                               (self.p.cid,)).fetchone()[0]
            rows = db.execute('SELECT g.*,c.name FROM native_afei_shop_goods g JOIN native_afei_shops s ON s.owner=g.owner '
                              'JOIN characters c ON c.id=g.owner WHERE '+filter_sql+' ORDER BY g.id DESC LIMIT ? OFFSET ?',
                              (self.p.cid, LIST_PAGE, page*LIST_PAGE)).fetchall()
        choices = [(f"{DATA['items'][str(r['kind'])]['name']}×{r['quantity']}，{r['price']}现金（{r['name']}）",
                    dict(op='withdraw_good' if op=='own' else 'buy', listing=r['id'])) for r in rows]
        headline = '点击撤回相应商品。' if op == 'own' else '点击购买整格商品，价格为总价。'
        return self.menu(headline if rows else '这一页暂无商品。',self.paging(choices,op,page,total))

    def back(self, text):
        self.p._cached_snapshot = None
        return self.g.defer_packets(lambda: (CLOSE,*self.p.item_packets(),*self.p.attribute_packets(),text_notice(text)))

    def execute(self, nid, action):
        from native946_inventory_features import consume, owned
        from native946_npc_services import NpcServices
        from native946_temp_inventory import bag_capacity, place
        from native946_trade import Trade

        npc = NpcServices(self.g).validate(nid)
        if (npc['scene_id'], nid) != CHAMBER:
            raise ValueError('请在章嘉处办理商会。')
        op = action['op']
        if op == 'guardian':
            from native946_guardian_chamber import GuardianChamber
            return GuardianChamber(self.g).root()
        if op == 'root':
            return self.root()
        if op in ('bag','own','browse'):
            return self.listing(op,action['page'])
        if op == 'price':
            with self.p.transaction(write=False) as db:
                item = owned(db,self.p.cid,action['item'])
                Trade.validate(db,self.p.cid,0,[item])
            return self.menu('请选择整格商品总价。上架后物品从背包移至商铺。',[
                (f'{cash}现金',dict(op='post',price=cash,item=item['id'],quantity=item['amount']))
                for cash in PRICE_STEPS])
        with self.p.transaction() as db:
            cid = self.p.cid
            shop = db.execute('SELECT * FROM native_afei_shops WHERE owner=?',(cid,)).fetchone()
            if op == 'open':
                if shop:
                    raise ValueError('你已开设物品商铺。')
                if self.p._snapshot(db)['level'] < OPEN_LEVEL:
                    raise ValueError(f'{OPEN_LEVEL}级可开设物品商铺。')
                spend_cash(db,cid,OPEN_CASH)
                db.execute('INSERT INTO native_afei_shops(owner,open) VALUES(?,1)',(cid,))
                message = '物品商铺已开设。'
            elif op == 'buy':
                row = db.execute('SELECT g.*,s.open FROM native_afei_shop_goods g '
                                 'JOIN native_afei_shops s ON s.owner=g.owner WHERE g.id=?',(action['listing'],)).fetchone()
                if not row or not row['open'] or row['owner'] == cid:
                    raise ValueError('这件商品已下架、售出或属于自己。')
                spend_cash(db,cid,row['price'])
                if place(db,self.p,row['kind'],row['quantity'],bag_capacity(db,cid)):
                    raise ValueError('背包位置不足。')
                db.execute('DELETE FROM native_afei_shop_goods WHERE id=?',(row['id'],))
                db.execute('UPDATE native_afei_shops SET proceeds=proceeds+? WHERE owner=?',(row['price'],row['owner']))
                message = f"购入物品，支付{row['price']}现金。"
            else:
                if not shop:
                    raise ValueError('请先开设商铺。')
                if op == 'toggle':
                    db.execute('UPDATE native_afei_shops SET open=1-open WHERE owner=?',(cid,))
                    message = '已暂停营业。' if shop['open'] else '已恢复营业。'
                elif op == 'withdraw':
                    if not shop['proceeds']:
                        raise ValueError('没有可提取的收入。')
                    db.execute('UPDATE native_afei_shops SET proceeds=0 WHERE owner=?',(cid,))
                    db.execute('UPDATE characters SET gold=gold+? WHERE id=?',(shop['proceeds'],cid))
                    message = f"已提取{shop['proceeds']}现金。"
                elif op == 'post':
                    item = owned(db,cid,action['item'])
                    Trade.validate(db,cid,0,[item])
                    if item['amount']!=action['quantity'] or action['price'] not in PRICE_STEPS:
                        raise ValueError('物品或价格已变化，请重新上架。')
                    if db.execute('SELECT 1 FROM native_equipment WHERE item_id=?',(item['id'],)).fetchone():
                        raise ValueError('带属性实例的装备暂不可上架。')
                    if db.execute('SELECT count(*) FROM native_afei_shop_goods WHERE owner=?',(cid,)).fetchone()[0]>=12:
                        raise ValueError('商铺已上架12格，请先撤回或卖出商品。')
                    consume(db,cid,item['id'],item['amount'])
                    db.execute('INSERT INTO native_afei_shop_goods(owner,kind,quantity,price) VALUES(?,?,?,?)',
                               (cid,item['type'],item['amount'],action['price']))
                    message = '商品已上架。'
                elif op == 'withdraw_good':
                    row = db.execute('SELECT * FROM native_afei_shop_goods WHERE id=? AND owner=?',
                                     (action['listing'],cid)).fetchone()
                    if not row:
                        raise ValueError('这件商品已撤回或售出。')
                    if place(db,self.p,row['kind'],row['quantity'],bag_capacity(db,cid)):
                        raise ValueError('背包位置不足。')
                    db.execute('DELETE FROM native_afei_shop_goods WHERE id=?',(row['id'],))
                    message = '已撤回商品。'
                else:
                    raise ValueError('商会操作无效。')
        return self.back(message)


def spouse(db,cid):
    return db.execute('SELECT m.*,c.name FROM native_afei_marriage m JOIN characters c ON c.id=m.partner WHERE m.character_id=?',
                      (cid,)).fetchone()


class Marriage:
    def __init__(self, game):
        self.g = game
        self.p = game.progression

    def menu(self, nid, service):
        with self.p.transaction(write=False) as db:
            record = spouse(db,self.p.cid)
        text = '当前配偶：'+record['name']+'。' if record else '当前没有配偶。'
        permit = None
        if service == 'marry' and not record:
            text += f'#r两位不同性别角色组队，双方确认，发起人支付{MARRY_CASH}现金（本机规则）。'
            permit = ('发出结婚请求','request')
        elif service == 'agree' and record:
            text += f'#r夫妻二人组队确认，发起人支付{DIVORCE_CASH}现金（阿飞参考）。'
            permit = ('发出协议离婚请求','request')
        elif service == 'force' and record:
            text += f'#r单方办理，二次确认后扣{DIVORCE_CASH}现金并解除双方记录（阿飞费用）。'
            permit = ('申请强制离婚','force_prompt')
        pending = getattr(self.g,'_afei_marriage_request',None)
        if pending and service == pending['service'] and pending['until'] > monotonic():
            permit = ('确认对方请求','accept')
            text += '#r收到'+pending['name']+'的请求。'
        self.g.dialog_actions = [dict(kind='afei_marriage',op=permit[1])] if permit else []
        return self.g.menu(text,[permit[0]] if permit else (),nid)

    def pair(self, other=None):
        g = self.g
        found = g.party_service.team(self.p.cid)
        if not found or len(found['members']) != 2:
            raise ValueError('需要双方组成两人队伍。')
        partner = next(m for m in found['members'] if m['character_id']!=self.p.cid)
        target = g.party_service.online(partner['character_id'])
        if (partner['away'] or any(m['away'] for m in found['members']) or not target or target.battle
                or target.character['map_id']!=g.character['map_id'] or other not in (None,target.progression.cid)):
            raise ValueError('请和对方一起来到月老祠。')
        return target

    def execute(self,nid,op):
        from native946_npc_services import NpcServices

        service = REGISTRY.get((self.g.character['map_id'],nid))
        if not service:
            raise ValueError('当前NPC没有婚姻服务。')
        NpcServices(self.g).validate(nid)
        g = self.g
        cid = self.p.cid
        if op == 'force_prompt' and service == 'force':
            with self.p.transaction(write=False) as db:
                relation = spouse(db,cid)
            if not relation:
                raise ValueError('当前没有配偶。')
            g.dialog_actions = [dict(kind='afei_marriage',op='cancel'),dict(kind='afei_marriage',op='force_commit')]
            return g.menu(f"确认与{relation['name']}解除婚姻并支付{DIVORCE_CASH}现金？", ['取消','确认强制离婚'],nid)
        if op == 'cancel':
            return CLOSE
        if op == 'request' and service in ('marry','agree'):
            other = self.pair()
            NpcServices(other).validate(nid)
            with self.p.transaction(write=False) as db:
                self.validate_pair(db, cid, other.progression.cid, service)
            other._afei_marriage_request=dict(service=service, sender=cid, name=g.character['name'],until=monotonic()+120)
            g.party_service.enqueue(other,text_notice(g.character['name']+'发来请求，请和当前NPC交谈确认。'))
            return CLOSE,text_notice('请求已发出，等待对方确认。')
        with self.p.transaction() as db:
            if op == 'accept' and service in ('marry','agree'):
                pending = getattr(g,'_afei_marriage_request',None)
                if not pending or pending['service']!=service or pending['until']<=monotonic():
                    raise ValueError('请求已结束，请重新发起。')
                partner = self.pair(pending['sender'])
                NpcServices(partner).validate(nid)
                other = partner.progression.cid
                self.validate_pair(db,cid,other,service)
                spend_cash(db,other,MARRY_CASH if service=='marry' else DIVORCE_CASH)
                if service == 'marry':
                    for a,b in [(g,partner),(partner,g)]:
                        role=a.character;peer=b.character
                        title=peer['name']+('的夫君' if role['sex']==1 else '的娘子')
                        db.execute('INSERT INTO native_afei_marriage VALUES(?,?,?,?)',(role['id'],peer['id'],title,int(time())))
                        db.execute('INSERT OR IGNORE INTO native_hero_titles(character_id,name,description) VALUES(?,?,?)',
                                   (role['id'],title,'本机婚姻称谓，解除婚姻时收回。'))
                    message='双方已结婚，称谓已发放。'
                else:
                    self.clear(db,cid,other)
                    message='双方已解除婚姻。'
            elif op == 'force_commit' and service == 'force':
                current = spouse(db,cid)
                if not current:
                    raise ValueError('当前没有配偶。')
                other = current['partner']
                spend_cash(db,cid,DIVORCE_CASH)
                self.clear(db,cid,other)
                message='双方婚姻记录和婚姻称谓已解除。'
            else:
                raise ValueError('当前婚姻操作无效。')
        def publish():
            from native946_hero_titles import HeroTitles

            g._afei_marriage_request = None
            peer = g.party_service.online(other)
            if peer:
                peer.progression._cached_snapshot=None
                g.party_service.enqueue(peer,*HeroTitles(peer).refresh(),text_notice(message))
            self.p._cached_snapshot=None
            return CLOSE,*HeroTitles(g).refresh(),text_notice(message)
        return g.defer_packets(publish)

    def validate_pair(self,db,cid,other,service):
        a,b = spouse(db,cid),spouse(db,other)
        if service=='marry':
            rows=db.execute('SELECT sex FROM characters WHERE id IN (?,?)',(cid,other)).fetchall()
            if {r[0] for r in rows}!={1,2} or a or b:
                raise ValueError('结婚要求两位不同性别且未婚的角色。')
        elif not a or not b or a['partner']!=other or b['partner']!=cid:
            raise ValueError('组队双方须是彼此的配偶。')

    @staticmethod
    def clear(db,cid,other):
        for member,partner in [(cid,other),(other,cid)]:
            row=spouse(db,member)
            if row and row['partner']==partner:
                db.execute('DELETE FROM native_afei_marriage WHERE character_id=?',(member,))
                db.execute('DELETE FROM native_hero_titles WHERE character_id=? AND name=?',(member,row['title']))
                db.execute("UPDATE native_hero_title_state SET display_title=CASE WHEN display_title=? THEN '' ELSE display_title END,"
                           "attribute_title=CASE WHEN attribute_title=? THEN '' ELSE attribute_title END WHERE character_id=?",
                           (row['title'],row['title'],member))
