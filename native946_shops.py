"""Resource-backed merchant catalogs and the native periodic special mall.

Unknown NPC prices remain absent. Existing local mall overrides are preserved.
"""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from time import time,monotonic
from secrets import token_hex
import json
from native946_codec import packet,text_notice

DATA=json.loads(Path(__file__).with_name('native946_shop_data.json').read_text('utf8'))
REFERENCE_WIRE_VERSION=8
# Keep the reproducible client scan and the subsequent 66-row evidence review separate.
REVIEW=json.loads(Path(__file__).with_name('native946_shop_review_data.json').read_text('utf8'))
DATA['items'].update(REVIEW['items'])
DATA['outside_combat_effects']=REVIEW['outside_combat_effects']
DATA['review_status_counts']=REVIEW['status_counts']
for _removal in REVIEW['remove']:
    _merchant=DATA['merchants'][_removal['merchant']]
    _merchant['explicit_items'][:]=[r for r in _merchant['explicit_items']
        if not (r.get('type')==_removal['type'] and r.get('historical_name')==_removal['historical_name'])]
for _update in REVIEW['updates']:
    _merchant=DATA['merchants'][_update['merchant']]
    _row=next((r for r in _merchant['explicit_items'] if r.get('type')==_update['type']),None)
    if _row is None:
        _row=dict(name=DATA['items'][str(_update['type'])]['name'])
        _merchant['explicit_items'].append(_row)
    _row.update({k:v for k,v in _update.items() if k!='merchant'})
    _key=str(_row['type'])
    if not _row['unavailable_reason']:
        assert type(_row['price']) is int and _row['price']>0
        _merchant['goods'][_key]=_row
    else:_merchant['goods'].pop(_key,None)
ITEMS=DATA['items'];REQUESTS={3702,3703};WRITES={3702}
import native946_equipment as _equipment
if not hasattr(_equipment,'COMPLETION'):
    from importlib import reload
    reload(_equipment)
COMPLETION=_equipment.COMPLETION
for _kind,_meta in COMPLETION['items'].items():
    if _kind in COMPLETION['starter'] or _kind in COMPLETION['supplies']:
        ITEMS.setdefault(_kind,_meta)
for _merchant in DATA['merchants'].values():
    for _offer in _merchant['explicit_items']:
        _key=str(_offer.get('type'))
        if _key in COMPLETION['starter']:
            _offer['unavailable_reason']=''
            _merchant['goods'].setdefault(_key,_offer)
_forge_shop=DATA['merchants']['2039:1276']
for _key,_price in COMPLETION['supplies'].items():
    if _key in _forge_shop['goods']:continue
    _item=ITEMS[_key]
    _offer=dict(type=int(_key),price=_price,name=_item['name'],price_source='本机作坊补给',
                unavailable_reason='')
    _original=next((row for row in _forge_shop['explicit_items'] if row.get('type')==int(_key)),None)
    if _original is not None:
        if _original.get('price') is not None:
            _offer['price']=_original['price']
            _offer['price_source']=_original.get('price_source','')
        _original.update(_offer)
    else:_forge_shop['explicit_items'].append(_offer)
    _forge_shop['goods'][_key]=_offer
TZ=timezone(timedelta(hours=8))
SPEC=DATA['specmall'];BASE=SPEC['base_config']

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_shop_orders(
      id TEXT PRIMARY KEY,character_id INTEGER NOT NULL REFERENCES characters(id),
      shop TEXT NOT NULL,offer INTEGER NOT NULL,period TEXT NOT NULL,
      amount INTEGER NOT NULL CHECK(amount>0),currency TEXT NOT NULL,
      cost INTEGER NOT NULL CHECK(cost>=0),created INTEGER NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS native_shop_limits ON native_shop_orders(character_id,shop,offer,period)')
    db.execute('''CREATE TABLE IF NOT EXISTS native_shop_balances(
      character_id INTEGER PRIMARY KEY REFERENCES characters(id),
      liuguang INTEGER NOT NULL DEFAULT 0 CHECK(liuguang>=0))''')

    migrate_liuguang(db)


def migrate_liuguang(db):
    """Local rule: share liuguang by account, preserving both legacy ledgers."""
    if 'liuguang' not in {row[1] for row in db.execute('PRAGMA table_info(mall_account_wallet)')}:
        db.execute('ALTER TABLE mall_account_wallet ADD COLUMN liuguang INTEGER NOT NULL DEFAULT 0 CHECK(liuguang>=0)')

    db.execute("""INSERT INTO mall_account_wallet(owner,liuguang)
        SELECT c.owner,sum(legacy.balance)
        FROM (
            SELECT character_id,liuguang AS balance FROM native_shop_balances WHERE liuguang<>0
            UNION ALL
            SELECT character_id,value FROM cosmetic_features WHERE key='point'
        ) AS legacy JOIN characters c ON c.id=legacy.character_id
        GROUP BY c.owner
        ON CONFLICT(owner) DO UPDATE SET liuguang=liuguang+excluded.liuguang""")
    db.execute('UPDATE native_shop_balances SET liuguang=0 WHERE liuguang<>0 '
               'AND character_id IN (SELECT id FROM characters)')
    db.execute("DELETE FROM cosmetic_features WHERE key='point' "
               'AND character_id IN (SELECT id FROM characters)')


def liuguang_balance(db,cid):
    row = db.execute('SELECT w.liuguang FROM characters c '
                     'JOIN mall_account_wallet w ON w.owner=c.owner WHERE c.id=?', (cid,)).fetchone()
    return row[0] if row else 0


def change_liuguang(db,cid,delta):
    account = db.execute('SELECT owner FROM characters WHERE id=?', (cid,)).fetchone()
    if account is None:
        raise ValueError('找不到当前角色的商城账户。')
    if delta < 0:
        charged = db.execute('UPDATE mall_account_wallet SET liuguang=liuguang+? '
                             'WHERE owner=? AND liuguang>=?', (delta, account[0], -delta))
        if not charged.rowcount:
            raise ValueError('流光声望不足。')
    elif delta:
        db.execute("""INSERT INTO mall_account_wallet(owner,liuguang) VALUES(?,?)
            ON CONFLICT(owner) DO UPDATE SET liuguang=liuguang+excluded.liuguang""", (account[0], delta))

def liuguang_packets(balance):
    return (packet(695,['number','number'],[77,balance]),packet(694,[],[]),packet(815,['number'],[balance]))

def merchant(npc):return DATA['merchants'].get(f'{npc["scene_id"]}:{npc["npc_id"]}')

def cycle(now):
    # Native time.lua base=1072886400; GetRelaWeekNo adds four days to it.
    relative_week=(int(now)-1072886400-345600)//604800+1
    return (relative_week-BASE['PromotePeriod'])%4+1

def on_shelf(row,now):
    weeks=row.get('PromoteWeek')
    return not weeks or weeks[0]==-1 or cycle(now) in weeks

def period(row,now):
    dt=datetime.fromtimestamp(now,TZ)
    if row.get('PeriodSaleRefresh')==1:return 'day:'+dt.strftime('%Y-%m-%d')
    if row.get('PeriodSaleRefresh')==7:return 'week:'+(dt-timedelta(days=dt.weekday())).strftime('%Y-%m-%d')
    return 'all'

def price(row):return row.get('Promote',row['Price'])

class Shops:
    def __init__(self,game):self.g=game;self.p=game.progression

    def menu(self,npc):
        from native946_npc_services import NpcServices
        choices=[]
        if merchant(npc)['goods']:choices.append(('购买商品',dict(kind='merchant',op='open')))
        choices.append(('查看售卖目录',dict(kind='merchant',op='catalog')))
        if (npc['scene_id'],npc['npc_id'])==(2039,1276):
            choices.extend([('装备修理',dict(kind='gear_bench',op='repair')),
                            ('装备开孔与镶嵌',dict(kind='gear_bench',op='ordinary_sockets'))])
        return NpcServices(self.g)._menu(npc['npc_id'],'请选择商店服务。',choices)

    def ordinary_catalog(self,nid,page=0):
        from native946_npc_services import NpcServices
        npc=NpcServices(self.g).validate(nid);m=merchant(npc)
        if not m:raise ValueError('这个 NPC 没有商店目录。')
        rows=m['explicit_items'];pages=max(1,(len(rows)+9)//10)
        if type(page) is not int or not 0<=page<pages:raise ValueError('商品目录页码无效。')
        text=m['name']+' · '+(m['title'] or m['desc'])
        if not rows:text+='#r暂无可购买商品。'
        for row in rows[page*10:page*10+10]:
            currency='现金或通宝' if isinstance(row.get('currency'),list) else '金钱'
            label='历史售价 ' if row.get('availability')=='retired_confirmed' else ''
            text+='#r'+row['name']+(('（%d）'%row['type']) if row.get('type') else '')+'：'+(label+str(row['price'])+currency if row['price'] is not None else '售价待核实')
            if row.get('price_source')=='本机作坊补给':text+=' · 本机价格'
            if row.get('unavailable_reason'):text+=' · '+row.get('availability_text','暂未开售')
        choices=[]
        if page:choices.append(('上一页',dict(kind='merchant',op='catalog',page=page-1)))
        if page+1<pages:choices.append(('下一页',dict(kind='merchant',op='catalog',page=page+1)))
        # Retain a useful menu even when the server-supplied stock is absent.
        choices.append(('返回',dict(kind='merchant',op='menu')))
        return NpcServices(self.g)._menu(nid,text,choices)

    def open_medicine(self,nid):
        return self.open_ordinary(nid,medicine=True)

    def open_ordinary(self,nid,*,medicine=False):
        from native946_npc_services import NpcServices
        npc=NpcServices(self.g).validate(nid,'medicine' if medicine else None)
        return self.ordinary_window(npc, 'medicine' if medicine else 'merchant')

    def open_mount_shop(self,skill):
        from native946_mall_mounts import MOUNT_SHOPS
        scene,nid=MOUNT_SHOPS[skill]
        return self.ordinary_window(dict(scene_id=scene,npc_id=nid), 'merchant', mount_skill=skill)

    def ordinary_window(self,npc,shop_kind,*,mount_skill=None):
        m=merchant(npc)
        if not m or not m['goods']:raise ValueError('这个商店暂无可购买商品。')
        nid=npc['npc_id']
        self.g.npc_service_session=dict(kind=shop_kind,npc=nid,scene=self.g.character['map_id'],expires=monotonic()+120)
        if mount_skill is not None:
            self.g.npc_service_session['mount_skill']=mount_skill
        return (packet(1183,['string'],['dlg_npc_chat']),packet(1697,['number','number',('array','seller_item')],
            [nid,1,[dict(type=r['type'],price=r['price']) for r in m['goods'].values()]]))

    def grant(self,db,kind,amount):
        """Cash sellers grant ordinary inventory; preserve locks and item expiry."""
        from native946_mall_bags import pages
        from native946_equipment import persist
        meta=ITEMS[str(kind)];maximum=min(500,meta['max_stack']);allowed=pages(db,self.p.cid)
        equipment=COMPLETION['starter'].get(str(kind))
        if equipment:maximum=1
        if maximum>1:
            for row in db.execute("SELECT i.* FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id "
                "LEFT JOIN native_object_locks l ON l.kind='item' AND l.object_id=i.id AND l.character_id=i.character_id "
                "WHERE i.character_id=? AND i.type=? AND coalesce(e.expires,0)=0 AND coalesce(l.flag,0)=0 ORDER BY i.id",
                (self.p.cid,kind)).fetchall():
                if row['page'] not in allowed:continue
                add=min(amount,max(0,maximum-row['amount']))
                if add:db.execute('UPDATE inventory SET amount=amount+? WHERE id=?',(add,row['id']));amount-=add
                if not amount:return
        occupied={(r[0],r[1]) for r in db.execute('SELECT page,slot FROM inventory WHERE character_id=?',(self.p.cid,))}
        for p in allowed:
            for slot in range(1,25):
                if (p,slot) in occupied:continue
                n=min(maximum,amount)
                inserted=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',
                                    (self.p.cid,kind,p,slot,n)).lastrowid
                if equipment:persist(db,self.p.cid,inserted,equipment,equipment['properties'])
                amount-=n
                if not amount:return
        raise ValueError('背包空间不足，本次没有扣款。')

    def buy_medicine(self,nid,kind,amount):
        from native946_mall_mounts import MOUNT_SHOPS, known_skills
        from native946_mall_uses import features
        from native946_npc_services import NpcServices
        s=getattr(self.g,'npc_service_session',None)
        if not s or s['kind'] not in ('medicine','merchant') or s['npc']!=nid or s['scene']!=self.g.character['map_id'] or s['expires']<monotonic():
            raise ValueError('商店窗口已失效，请与对应 NPC 重新交谈。')
        shop_skill=s.get('mount_skill')
        if shop_skill is None:
            npc=NpcServices(self.g).validate(nid,'medicine' if s['kind']=='medicine' else None)
        else:
            scene,shop_nid=MOUNT_SHOPS[shop_skill]
            if nid!=shop_nid or self.g.battle:raise ValueError('当前无法使用坐骑商店。')
            npc=dict(scene_id=scene,npc_id=nid)
        with self.p.transaction() as db:
            if shop_skill is not None and shop_skill not in known_skills(features(db,self.p.cid)):
                raise ValueError('尚未拥有启用该技能的灵兽。')
            self.purchase_ordinary(db,npc,s['kind'],kind,amount)
        return self.g.defer_packets(lambda:(*self.p.attribute_packets(),*self.p.item_packets(),text_notice('商品购买成功。')))

    def purchase_ordinary(self, db, npc, shop_kind, kind, amount):
        m=merchant(npc)
        row=m['goods'].get(str(kind)) if m else None
        if row is None or type(amount) is not int or not 1<=amount<=500:raise ValueError('商品或购买数量无效。')
        cost=row['price']*amount
        changed=db.execute('UPDATE characters SET gold=gold-? WHERE id=? AND gold>=?',(cost,self.p.cid,cost)).rowcount
        if not changed:raise ValueError('随身金钱不足。')
        self.grant(db,kind,amount)
        db.execute('INSERT INTO native_shop_orders VALUES(?,?,?,?,?,?,?,?,?)',
            (token_hex(16),self.p.cid,f'{shop_kind}:{npc["scene_id"]}:{npc["npc_id"]}',kind,'all',amount,'gold',cost,int(time())))

    def catalog_menu(self,nid,page=0,selected=None):
        from native946_npc_services import NpcServices
        catalogs=DATA['resource_catalogs'];pages=(len(catalogs)+9)//10
        if type(page) is not int or not 0<=page<pages:raise ValueError('商店目录页码无效。')
        if selected is not None:return self.catalog_details(nid,selected,0)
        choices=[]
        for index in range(page*10,min(len(catalogs),page*10+10)):
            c=catalogs[index];b=c['base'];name=b.get('Name') or b.get('ActivityName') or b.get('ActivityTag') or ('活动商店'+str(index+1))
            choices.append((str(name)+'（%d项）'%len(c['records']),dict(kind='cosmetic',op='shop_catalog_detail',selected=index,page=0)))
        if page:choices.append(('上一页',dict(kind='cosmetic',op='shop_catalog',page=page-1)))
        if page+1<pages:choices.append(('下一页',dict(kind='cosmetic',op='shop_catalog',page=page+1)))
        return NpcServices(self.g)._menu(nid,'商品及兑换目录。活动开放状态与购买条件以各活动为准。',choices)

    def catalog_details(self,nid,index,page):
        from native946_npc_services import NpcServices
        from native946_cosmetics import DATA as CATALOG
        if type(index) is not int or not 0<=index<len(DATA['resource_catalogs']):raise ValueError('商店目录无效。')
        c=DATA['resource_catalogs'][index];rows=c['records'];pages=max(1,(len(rows)+5)//6)
        if type(page) is not int or not 0<=page<pages:raise ValueError('商店目录页码无效。')
        text='商品及兑换目录 %d/%d'%(page+1,pages)
        b=c['base']
        for k in ('Name','ActivityTime','TimeTips','ShopTimeTxt','EshopTimeTxt','GradeLimit','SpecMallLimitGrade'):
            if k in b:text+='#r'+({'GradeLimit':'参与等级','SpecMallLimitGrade':'参与阶段'}.get(k,'活动信息'))+'：'+str(b[k])
        for entry in rows[page*6:page*6+6]:
            r=entry['raw'];k=r.get('ItemType',r.get('ItemId',r.get('type')))
            meta=CATALOG['items'].get(str(k),ITEMS.get(str(k),{}))
            text+='#r#Y'+str(r.get('Name',meta.get('name','商品')))+(f'（{k}）' if k else '')+'#n'
            for field in ('Price','Promote','CashType','Cost','CostScore','NeedScore','Score','Value','PayItemType','Level','GradeLimit','BuyLimit','BuyLimitRefresh','DayLimitCount','TotalLimitCount','PeriodSaleLimit','PeriodSaleRefresh','PromoteWeek','MonthLimit','Limit','AllServerLimit'):
                if field in r:
                    labels={'Price':'原价','Promote':'特价','CashType':'货币类型','Cost':'消耗','CostScore':'所需积分','NeedScore':'所需积分','Score':'积分','Value':'兑换消耗','PayItemType':'支付道具','Level':'商店阶段','GradeLimit':'等级要求','BuyLimit':'限购','BuyLimitRefresh':'限购刷新','DayLimitCount':'每日限购','TotalLimitCount':'总限购','PeriodSaleLimit':'周期限购','PeriodSaleRefresh':'刷新天数','PromoteWeek':'轮换周','MonthLimit':'每月限购','Limit':'限购','AllServerLimit':'全服限购'}
                    text+=' '+labels[field]+'：'+str(r[field])
        choices=[]
        if page:choices.append(('上一页',dict(kind='cosmetic',op='shop_catalog_detail',selected=index,page=page-1)))
        if page+1<pages:choices.append(('下一页',dict(kind='cosmetic',op='shop_catalog_detail',selected=index,page=page+1)))
        choices.append(('返回目录',dict(kind='cosmetic',op='shop_catalog',page=index//10)))
        return NpcServices(self.g)._menu(nid,text,choices)

    def npc_catalog(self,nid,page=0):
        from native946_world import world
        from native946_npc_services import NpcServices
        rows=[*DATA['merchants'].values(),*DATA['mall_npcs']];pages=(len(rows)+5)//6
        if type(page) is not int or not 0<=page<pages:raise ValueError('NPC名录页码无效。')
        text='商店NPC名录 %d/%d'%(page+1,pages)
        for m in rows[page*6:page*6+6]:
            scene=world().scene(m['scene'])['scene_name']
            text+='#r#Y'+m['name']+'#n · '+scene+'（%d，%d）'%(m['x'],m['y'])
            text+=' · '+('商品%d种'%len(m['goods']) if m.get('goods') else '异宝阁' if 'goods' not in m else '暂无可购买商品')
        choices=[]
        if page:choices.append(('上一页',dict(kind='cosmetic',op='shop_npcs',page=page-1)))
        if page+1<pages:choices.append(('下一页',dict(kind='cosmetic',op='shop_npcs',page=page+1)))
        choices.append(('返回商品目录',dict(kind='cosmetic',op='shop_catalog',page=0)))
        return NpcServices(self.g)._menu(nid,text,choices)

    def open_special(self,nid=None,*,item=0):
        if nid is not None:
            from native946_npc_services import NpcServices
            NpcServices(self.g).validate(nid,'cosmetic')
        self.g.shop_session=dict(npc=nid,scene=self.g.character['map_id'],expires=monotonic()+120)
        self.g.shop_quote=None
        opened = self.special_packet()
        if nid is not None:
            return (packet(1183,['string'],['dlg_npc_chat']), opened)
        if item:
            for index, offer in enumerate(SPEC['item_list'], 1):
                if offer['ItemType'] != item:
                    continue
                try:
                    return opened, self.try_special(index)
                except ValueError:
                    continue
        return opened

    def session(self):
        from native946_npc_services import NpcServices
        s=getattr(self.g,'shop_session',None)
        if not s or s['expires']<monotonic() or s['scene']!=self.g.character['map_id']:raise ValueError('特卖会窗口已失效，请重新打开。')
        if s['npc'] is not None:
            NpcServices(self.g).validate(s['npc'],'cosmetic')
        return s

    def wallet(self,db):
        from native946_cosmetics import Cosmetics
        yubo,_=Cosmetics(self.g).wallet(db)
        return yubo,liuguang_balance(db,self.p.cid)

    def used(self,db,index,row,now):
        return db.execute("SELECT coalesce(sum(amount),0) FROM native_shop_orders WHERE character_id=? AND shop='specmall' AND offer=? AND period=?",
            (self.p.cid,index,period(row,now))).fetchone()[0]

    def special_packet(self):
        from native946_progression import lua_data
        now=int(time())
        with self.p.transaction(write=False) as db:
            yubo,liuguang=self.wallet(db)
            counts={i:self.used(db,i,r,now) for i,r in enumerate(SPEC['item_list'],1)}
        return packet(3700,['number','number','string'],[yubo,liuguang,lua_data(counts)])

    def special_offer(self,db,index,now):
        from native946_role_systems import grade
        if type(index) is not int or not 1<=index<=len(SPEC['item_list']):raise ValueError('特卖商品无效。')
        row=SPEC['item_list'][index-1]
        if not on_shelf(row,now):raise ValueError('该物品本次并未上架。')
        if grade(db,self.p.cid)<BASE['SpecMallLimitGrade']:raise ValueError('需要达到飞升2转140级才可购买特卖商品。')
        if not DATA['server'].get('ignore_open_delay',False):
            opened=DATA['server']['opened_at']
            if opened is None:raise ValueError('特卖会尚未开放。')
            if now<opened+BASE['OpenDelayDay']*86400:raise ValueError('本服尚未达到特卖会开放时间。')
        remaining=row.get('PeriodSaleLimit',500)-self.used(db,index,row,now)
        if remaining<=0:raise ValueError('本周期购买数量已达上限。')
        if row['CashType'] not in (1,2):raise ValueError('该商品支付方式尚未开放。')
        return row,remaining

    def try_special(self,index):
        self.session();self.g.shop_quote=None;now=int(time())
        with self.p.transaction(write=False) as db:
            row,left=self.special_offer(db,index,now);balances=self.wallet(db)
        self.g.shop_quote=dict(id=token_hex(16),index=index,price=price(row),currency=row['CashType'],period=period(row,now),until=monotonic()+120)
        return packet(3701,['number']*5,[index,left,row['CashType'],balances[row['CashType']-1],price(row)])

    def buy_special(self,index,amount):
        self.session();q=getattr(self.g,'shop_quote',None);now=int(time())
        if type(amount) is not int or not 1<=amount<=500:raise ValueError('购买数量无效。')
        if not q or q['index']!=index or q['until']<monotonic():raise ValueError('请重新确认特卖商品。')
        with self.p.transaction() as db:
            if db.execute('SELECT 1 FROM native_shop_orders WHERE id=?',(q['id'],)).fetchone():raise ValueError('该笔购买已经处理。')
            row,left=self.special_offer(db,index,now)
            if amount>left:raise ValueError('购买数量超过剩余限购。')
            if (q['price'],q['currency'],q['period'])!=(price(row),row['CashType'],period(row,now)):raise ValueError('商品价格或周期已变化，请重新确认。')
            cost=price(row)*amount
            if row['CashType']==2:
                change_liuguang(db,self.p.cid,-cost)
            else:
                from native946_cosmetics import spend_currency
                spend_currency(db, self.p.owner, 'yubo', cost)
            self.grant(db,row['ItemType'],amount)
            db.execute('INSERT INTO native_shop_orders VALUES(?,?,?,?,?,?,?,?,?)',
                (q['id'],self.p.cid,'specmall',index,period(row,now),amount,'yubo' if row['CashType']==1 else 'liuguang',cost,now))
        self.g.shop_quote=None
        from native946_cosmetics import Cosmetics
        def reply():
            currency=Cosmetics(self.g).currency_packets()
            if row['CashType']==2:
                with self.p.transaction(write=False) as db:
                    currency+=liuguang_packets(liuguang_balance(db,self.p.cid))
            return (packet(3709,[],[]),self.special_packet(),*currency,*self.p.item_packets(),text_notice('特卖商品购买成功。'))
        return self.g.defer_packets(reply)

    def receive(self,mid,r):
        index=r.integer();amount=r.integer() if mid==3702 else None;r.finish()
        return self.buy_special(index,amount) if mid==3702 else self.try_special(index)
