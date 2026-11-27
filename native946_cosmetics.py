"""Owned native 946 mall, wardrobe, palette and skill-skin state.

Catalog prices are client data. The explicitly labelled local test kit and
weapon-model selector are local test services, not official economy rules.
"""
from pathlib import Path
from time import time,monotonic
import json,re,secrets
from native946_codec import packet
from native946_codec import text_notice
from native946_child_fashion import REQUESTS as CHILD_FASHION_REQUESTS
from native946_classified_mall import DATA as CLASSIFIED_MALL_DATA, REQUESTS as CLASSIFIED_REQUESTS, WRITES as CLASSIFIED_WRITES
REFERENCE_WIRE_VERSION=9
LOCAL_UNLIMITED_YUBO_ACCOUNTS={'123123123'}
LOCAL_UNLIMITED_YUBO_BALANCE=2000000000


def unlimited_yubo(owner):
    return owner in LOCAL_UNLIMITED_YUBO_ACCOUNTS


DATA=json.loads(Path(__file__).with_name('native946_cosmetics_data.json').read_text('utf8'))
for _kind, _meta in CLASSIFIED_MALL_DATA['items'].items():
    DATA['items'].setdefault(_kind, _meta)
from native946_refinement import DATA as REFINEMENT_DATA
from native946_equipment import COMPLETION as GEAR_COMPLETION
for _kind,_meta in GEAR_COMPLETION['items'].items():DATA['items'].setdefault(_kind,_meta)
from native946_jing_gems import DATA as SPECIAL_EQUIPMENT
for _kind,_meta in SPECIAL_EQUIPMENT['items'].items():DATA['items'].setdefault(_kind,_meta)
from native946_race_fabao_cultivation import DATA as RACE_FABAO_CULTIVATION
for _kind,_meta in RACE_FABAO_CULTIVATION['materials'].items():
    DATA['items'].setdefault(_kind,_meta)
if 'gear_items' not in REFINEMENT_DATA:
    # Older live controllers may reload consumers before their catalog owner.
    from importlib import reload as _reload
    import native946_refinement as _refinement
    REFINEMENT_DATA=_reload(_refinement).DATA
for _kind,_meta in {**REFINEMENT_DATA['materials'],**REFINEMENT_DATA['fashion_items'],**REFINEMENT_DATA['gear_items']}.items():
    DATA['items'].setdefault(_kind,dict(name=_meta['name'],icon=int(_meta['shape']),
                                       desc=_meta['desc'],max_stack=int(_meta['amount_max'])))
from native946_nice_ids import ITEMS as NICE_ID_ITEMS
DATA['items'].update(NICE_ID_ITEMS)
import native946_mall_mounts as _mounts
if getattr(_mounts,'REFERENCE_WIRE_VERSION',0)<4:
    from importlib import reload as _reload
    _reload(_mounts)
DATA['items'].update(_mounts.ITEMS)
DATA['goods'][str(_mounts.DYE_MATERIAL)] = {'yubo': _mounts.LOCAL_RULES['dye_yubo']}
from native946_mall_event_boxes import ITEMS as EVENT_BOX_ITEMS
for _kind,_meta in EVENT_BOX_ITEMS.items():DATA['items'].setdefault(_kind,_meta)
from native946_fashion import Fashion,REQUESTS as FASHION_REQUESTS,WRITES as FASHION_WRITES,ITEMS as FASHION_ITEMS,record_use
DATA['items'].update(FASHION_ITEMS)
from native946_fashion_shop import FashionShop,DATA as FASHION_SHOP,REQUESTS as SHOP_REQUESTS,WRITES as SHOP_WRITES
DATA['items'].update(FASHION_SHOP['items'])
DATA['goods'].update(FASHION_SHOP['goods'])
def build_sale_catalog(config):
    aliases = {int(old): new for old, new in config.get('aliases', {}).items()}
    display = config.get('display', {})
    goods = {
        key: {**row, **display.get(key, {})}
        for key, row in DATA['goods'].items() if int(key) not in aliases
    }
    for old, new in aliases.items():
        prior, current = DATA['goods'].get(str(old)), goods.get(str(new))
        if not prior or not current or prior['yubo'] != current['yubo']:
            raise ValueError(f'商城重复编号映射无效：{old} → {new}')
    return aliases, goods


_sale_path = Path(__file__).with_name('native946_mall_catalog.json')
_sale_catalog = json.loads(_sale_path.read_text('utf8')) if _sale_path.exists() else {}
SALE_ALIASES, SALE_GOODS = build_sale_catalog(_sale_catalog)


def sale_type(kind):
    return SALE_ALIASES.get(kind, kind)


def mall_owned(types):
    owned = set(types)
    canonical = {sale_type(kind) for kind in owned}
    for key, parts in DATA['sets'].items():
        if key in SALE_GOODS and all(sale_type(part) in canonical for part in parts):
            owned.add(int(key))
    owned.update(canonical)
    owned.update(old for old, new in SALE_ALIASES.items() if new in owned)
    return sorted(owned)


SKIN_TYPES={}
for _kind,_skin in sorted(DATA['skills'].items(),key=lambda v:int(v[0])):
    SKIN_TYPES.setdefault((_skin['SkillId'],_skin['SkinId']),int(_kind))
PAGES=tuple(range(25,29))+tuple(range(5800,5816))
WEAR_PAGE=29
REQUESTS={825,826,827,830,831,833,835,837,838,3128,3663,3665,3667,3668,3669,3670,3671,3672,3679,3685,3689,4823,4824,1033}
WRITES={826,827,830,835,838,3665,3671,3679,4823,1033}
REQUESTS.update(FASHION_REQUESTS)
WRITES.update(FASHION_WRITES)
REQUESTS.update(SHOP_REQUESTS)
WRITES.update(SHOP_WRITES)
REQUESTS.update(CHILD_FASHION_REQUESTS)
WRITES.update(CHILD_FASHION_REQUESTS)
REQUESTS.update(CLASSIFIED_REQUESTS)
WRITES.update(CLASSIFIED_WRITES)

def account_wallet_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS mall_account_wallet (
        owner TEXT PRIMARY KEY NOT NULL REFERENCES accounts(username),
        yubo INTEGER NOT NULL DEFAULT 0 CHECK(yubo>=0),
        coupon INTEGER NOT NULL DEFAULT 0 CHECK(coupon>=0),
        test_yubo_claimed INTEGER NOT NULL DEFAULT 0 CHECK(test_yubo_claimed IN (0,1))
    )""")
    # Transfer the finite balances once. Leave per-character mail and kit flags intact.
    db.execute("""INSERT INTO mall_account_wallet(owner,yubo,coupon,test_yubo_claimed)
        SELECT c.owner,sum(w.yubo),sum(w.coupon),max(w.test_claimed!=0)
        FROM cosmetic_wallet w JOIN characters c ON c.id=w.character_id
        WHERE w.yubo<>0 OR w.coupon<>0 OR w.test_claimed<>0
        GROUP BY c.owner
        ON CONFLICT(owner) DO UPDATE SET yubo=yubo+excluded.yubo,
            coupon=coupon+excluded.coupon,
            test_yubo_claimed=max(test_yubo_claimed,excluded.test_yubo_claimed)
        WHERE excluded.yubo<>0 OR excluded.coupon<>0 OR
              excluded.test_yubo_claimed>test_yubo_claimed""")
    db.execute('UPDATE cosmetic_wallet SET yubo=0,coupon=0 WHERE yubo<>0 OR coupon<>0')


def wallet_balance(db, owner):
    row = db.execute('SELECT yubo,coupon FROM mall_account_wallet WHERE owner=?', (owner,)).fetchone()
    yubo, coupon = tuple(row) if row else (0, 0)
    return (LOCAL_UNLIMITED_YUBO_BALANCE if unlimited_yubo(owner) else yubo), coupon


def spend_currency(db, owner, currency, cost):
    if currency not in ('yubo', 'coupon') or type(cost) is not int or cost < 0:
        raise ValueError('商城扣费参数无效。')
    if not cost or currency == 'yubo' and unlimited_yubo(owner) and cost <= LOCAL_UNLIMITED_YUBO_BALANCE:
        return
    if currency == 'yubo' and unlimited_yubo(owner):
        raise ValueError('玉帛不足。')
    if not db.execute(f'UPDATE mall_account_wallet SET {currency}={currency}-? '
                      f'WHERE owner=? AND {currency}>=?', (cost, owner, cost)).rowcount:
        raise ValueError('福豆不足。' if currency == 'coupon' else '玉帛不足。')


def schema(db):
    # Called only at startup/migration, never from a read-only request.
    for statement in [
        'CREATE TABLE IF NOT EXISTS cosmetic_wallet (character_id INTEGER PRIMARY KEY REFERENCES characters(id), yubo INTEGER NOT NULL DEFAULT 0 CHECK(yubo>=0), coupon INTEGER NOT NULL DEFAULT 0 CHECK(coupon>=0), test_claimed INTEGER NOT NULL DEFAULT 0, mail INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS cosmetic_state (character_id INTEGER PRIMARY KEY REFERENCES characters(id), weapon_idx INTEGER NOT NULL DEFAULT 0, weapon_color INTEGER NOT NULL DEFAULT 0, color1 INTEGER NOT NULL DEFAULT 0, color2 INTEGER NOT NULL DEFAULT 0, color3 INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS cosmetic_expiry (item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE, expires INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS cosmetic_skins (character_id INTEGER NOT NULL REFERENCES characters(id), skill_id INTEGER NOT NULL, skin_id INTEGER NOT NULL, expires INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(character_id,skill_id,skin_id))',
        'CREATE TABLE IF NOT EXISTS cosmetic_orders (id TEXT PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters(id), cost INTEGER NOT NULL, items TEXT NOT NULL, created INTEGER NOT NULL)'
    ]:db.execute(statement)
    account_wallet_schema(db)
    from native946_mall_uses import schema as use_schema
    use_schema(db)
    from native946_fashion import schema as fashion_schema
    fashion_schema(db)
    from native946_shops import schema as shop_schema
    shop_schema(db)

def appearance(db,cid):
    row=db.execute('SELECT * FROM cosmetic_state WHERE character_id=?',(cid,)).fetchone()
    values={k:int(row[k]) if row else 0 for k in ['weapon_idx','weapon_color','color1','color2','color3']}
    # Slot 4 is the native weapon slot. A saved model is a selection, not
    # an equipped weapon; retain it in storage while hiding it when unarmed.
    from native946_inventory import active_page
    weapon=db.execute('SELECT type FROM inventory WHERE character_id=? AND page=? AND slot=4',
                      (cid,active_page(db,cid))).fetchone()
    if not weapon:
        values.update(weapon_idx=0,weapon_color=0)
    fields={'GARMENT_HEAD':'hat_id','GARMENT_CLOTHES':'suit_id','GARMENT_SHOULDER':'cloak_id',
            'GUANGHUANBIAN':'halo','TAGEXING':'foot_halo','TRACE':'trace_halo',
            'ICHARM_NIMBUS':'charm_nimbus','ICHARM_HEAD':'charm_head','ICHARM_SHOULDER':'charm_shoulder',
            'TEAMFLAG':'team_flag','TEAM_FRAME':'team_frame','ENTER_EFFECT':'enter_effect','SAYFRAME':'sayframe'}
    values.update({k:0 for k in fields.values()})
    role,sex=db.execute('SELECT role_id,sex FROM characters WHERE id=?',(cid,)).fetchone()
    if weapon and not values['weapon_idx']:
        from native946_progression import DATA as ITEMS
        from native946_mall_uses import DATA as NATIVE
        icon=ITEMS['items'].get(str(weapon[0]),{}).get('icon')
        model=next((row['wiwc'] for row in NATIVE['weapon'].get(str(role),()) if row['shape']==icon),None)
        if model is not None:
            index,color=divmod(model,10)
            values.update(weapon_idx=index,weapon_color=values['weapon_color'] or color)
    values.update(shape=role,follow_info='')
    from native946_fashion import title as fashion_title
    values['title']=fashion_title(db,cid)
    from native946_fashion_refinement import ITEMS as PHYSICAL_FASHION, cosmetic_type
    for equipped in db.execute('SELECT type FROM inventory WHERE character_id=? AND page=?',
                               (cid,active_page(db,cid))):
        kind=equipped['type']
        if str(kind) in PHYSICAL_FASHION:
            meta=DATA['wearables'][str(cosmetic_type(kind,sex))]
            values[fields[meta['kind']]]=meta['value']
    followers={}
    for row in db.execute('SELECT i.type,e.expires FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.character_id=? AND i.page=29',(cid,)):
        meta=DATA['wearables'].get(str(row['type']))
        if not meta or (row['expires'] and row['expires']<=time()):continue
        kind=meta['kind']
        if kind in fields:values[fields[kind]]=row['type'] if kind=='ENTER_EFFECT' else meta['value']
        elif kind=='YIRONGDAN':values['shape']=meta['value']
        elif kind=='NISHANG':
            palette=meta['value'].get(str(role),[])
            if palette:values.update(dict(zip(['color1','color2','color3'],palette[0])))
        elif kind in ('QIZHI','FLYPET'):
            from native946_mall_uses import DATA as USE_DATA
            preview=USE_DATA['preview'].get(str(row['type']),{})
            args=preview.get('args',[])
            if args:followers[meta['slot']]={'Type':args[0],'WeaponIndex':args[1] if len(args)>1 else 0,'WeaponColor':0}
    if followers:
        from native946_progression import lua_data
        values['follow_info']=lua_data(followers)
    from native946_mall_uses import features,DATA as USE_DATA
    saved=features(db,cid);ride=saved.get('ride',{}).get('value',0)
    totem=saved.get('totem_current',{}).get('value',0)
    values.update(left_totem=totem if 'totem:'+str(totem) in saved else 0,right_totem=0)
    if 1 <= ride <= len(USE_DATA['mounts']) and 'mount:'+str(ride) in saved:
        from native946_mall_mounts import equipped_colors
        values['mount_shape']=USE_DATA['mounts'][ride-1]['shape']
        values.update(zip(('hcolor1','hcolor2','hcolor3'),equipped_colors(saved,ride)))
        if dye := saved.get(f'mount_random_dye:{ride}'):
            values['_mount_dye_expires'] = dye['expires']
    # Timed transformations supersede wearable models, with the saved normal
    # appearance retained so expiry can restore it without inventing gear.
    transformed=None
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_transform_state'").fetchone():
        transformed=db.execute('SELECT shape FROM native_transform_state WHERE character_id=? AND expires>?',(cid,int(time()))).fetchone()
    if transformed and transformed[0]:
        values.update(shape=transformed[0],weapon_idx=0,weapon_color=0,color1=0,color2=0,color3=0,hat_id=0,suit_id=0,cloak_id=0)
        values.pop('mount_shape',None)
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_hero_title_state'").fetchone():
        from native946_hero_titles import current as current_title
        values.update(current_title(db,cid))
    from native946_training import state as cultivation
    from native946_role_growth import jing
    stage=cultivation(db,cid)
    values.update(rei=stage['rebirth'],fly=stage['ascension'],feng=stage['sanctification'],jing=jing(db,cid)['level'])
    # INFO_AURA_RESOURCE assigns 100 to fly_1 and 101 to 封圣. A selected
    # wearable takes precedence and removal reveals the stage aura again.
    if not values['halo']:
        values['halo']=101 if values['feng'] else 100 if values['fly'] else 0
    return values

def numeric_table(text):
    """Bounded Lua numeric tables only; no evaluation, names, calls or strings."""
    if len(text)>4096:raise ValueError('购物车列表过长。')
    parts=re.findall(r'\d+|[{}\[\]=,;]|\S',text);at=0;count=0
    def take(token=None):
        nonlocal at
        if at>=len(parts) or (token is not None and parts[at]!=token):raise ValueError('购物车列表格式无效。')
        v=parts[at];at+=1;return v
    def table(depth=0):
        nonlocal at,count
        if depth>2:raise ValueError('购物车列表层级无效。')
        take('{');out={};index=1
        while at<len(parts) and parts[at]!='}':
            count+=1
            if count>96:raise ValueError('购物车列表过长。')
            key=index
            if parts[at]=='[':
                take('[');key=int(take()) if at<len(parts) and parts[at].isdigit() else -1;take(']');take('=')
            else:index+=1
            if key<1 or key>65535 or key in out:raise ValueError('购物车索引无效。')
            if at<len(parts) and parts[at]=='{':value=table(depth+1)
            else:
                raw=take()
                if not raw.isascii() or not raw.isdecimal() or len(raw)>9:raise ValueError('购物车数量无效。')
                value=int(raw)
            out[key]=value
            if at<len(parts) and parts[at] in (',',';'):at+=1
            elif at>=len(parts) or parts[at]!='}':raise ValueError('购物车列表格式无效。')
        take('}');return out
    try:result=table()
    except (OverflowError,RecursionError):raise ValueError('购物车列表无效。') from None
    if at!=len(parts):raise ValueError('购物车列表含多余内容。')
    return result

class Cosmetics:
    def __init__(self, game, *, sale_goods=None):
        self.g = game
        self.p = game.progression
        self.sale_goods = SALE_GOODS if sale_goods is None else sale_goods

    def wallet(self,db):
        return wallet_balance(db, self.p.owner)

    def currency_packets(self):
        with self.p.transaction(write=False) as db:yubo,coupon=self.wallet(db)
        return (packet(814,['number'],[yubo]),packet(813,['number'],[coupon]))

    def items(self,db):
        return [dict(r) for r in db.execute('SELECT i.*,coalesce(e.expires,0) AS expires FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.character_id=? AND (i.page BETWEEN 25 AND 29 OR i.page BETWEEN 5800 AND 5815) ORDER BY i.page,i.slot',(self.p.cid,))]

    def refresh(self):
        with self.p.transaction(write=False) as db:self.g.character['appearance']=appearance(db,self.p.cid)

    def visual_packets(self,recreate=False,*,scene_only=False):
        self.refresh();c=self.g.character;a=c['appearance']
        previous=getattr(self.g,'_published_cosmetic_appearance',None)
        stage_changed=previous is not None and any(
            previous.get(key,0)!=a[key] for key in ('rei','fly','feng','jing'))
        recreate=recreate or stage_changed
        values=[c['id'],a['shape'],a['weapon_idx'],a['weapon_color'],0,*[a['color'+str(i)] for i in (1,2,3)],*[a[k] for k in ['hat_id','suit_id','cloak_id','charm_nimbus','charm_head','charm_shoulder']]]
        result=(packet(1620,['number']+['byte']*5,[c['id'],a['weapon_idx'],a['weapon_color'],*[a['color'+str(i)] for i in (1,2,3)]]),
                packet(2501,['number']*4+['byte']*4+['number']*6,values),
                *(packet(1621,['number']*3,[c['id'],index,a[name]]) for index,name in [(1,'shape'),(8,'halo'),(9,'foot_halo'),(10,'left_totem'),(11,'right_totem'),(12,'trace_halo'),(16,'team_flag')]),
                packet(1622,['number','number','string'],[c['id'],20,a['follow_info']]),
                packet(818,['number']*3,[a['color'+str(i)] for i in (1,2,3)]))
        if scene_only:
            # The scene header already creates the local actor using the current appearance.
            return result[:-1]
        if recreate:
            from native946_characters import scene_actor_packet
            # A stage change needs the old scene object gone before c_create_player
            # reads the new name style. c_nobj_del removes it synchronously.
            remove=packet(1604 if stage_changed else 1596,['number'],[c['id']])
            result=(remove,scene_actor_packet(c),packet(1585,['number']*2,[c['id'],c['id']]),*result)
        # A deferred render can retry if reading the wardrobe or frame panel is busy.
        # Finish the owner's reply before marking the scene update as published.
        fashion = Fashion(self.g)
        reply = (*result, *fashion.sync(), fashion.style_sync(), *FashionShop(self.g).frame_packets())
        service=getattr(self.g,'party_service',None)
        if service is not None:
            with service.lock:
                team = service.team(c['id']) if stage_changed else None
                if previous is not None and previous!=a:
                    published=(*result[:2],*result[3:-1]) if recreate else result[:-1]
                    for other in service.games.values():
                        if other is self.g or other.battle or other.character['map_id'] != c['map_id']:
                            continue
                        # Login can advertise an actor to distant observers in the same scene.
                        if stage_changed or max(abs(other.character[k]-c[k]) for k in ('x','y')) <= 96:
                            service.enqueue(other,*published)
                if team:
                    service.publish(team)
                self.g._published_cosmetic_appearance=dict(a)
        return reply

    def mall(self,selected=0,refresh=False):
        from native946_progression import lua_data
        from native946_mall_uses import features, owned_mount_items
        with self.p.transaction(write=False) as db:
            yubo,coupon=self.wallet(db)
            now = time()
            types = {r['type'] for r in self.items(db) if not r['expires'] or r['expires'] > now}
            types.update(owned_mount_items(features(db, self.p.cid), DATA['items']))
            owned = mall_owned(types)
        if refresh:return packet(3676,['number']*4+['string'],[yubo,coupon,0,0,lua_data(owned)])
        # The client catalog supplies names/prices. Omit default-zero flags so
        # the full 3,000+ item catalog stays inside the native string limit.
        from native946_mall_checkout import fudou_eligible
        sale={int(k):[0,v['yubo'] if fudou_eligible(int(k)) else 0,v.get('beauty',0)]
              for k,v in SALE_GOODS.items() if v.get('beauty',0) or fudou_eligible(int(k))}
        return packet(3658,['string']+['number']*6+['string','number'],[lua_data(sale),0,yubo,coupon,0,0,0,lua_data(owned),sale_type(selected)])

    def closet(self,open_panel=False):
        # The wardrobe consumes native inventory pages. c_mall_frame_open_ui
        # carries only collection unlocks/rewards, independent of owned items.
        from native946_child_fashion import closet
        with self.p.transaction(write=False) as db:children=closet(db,self.p.cid)
        return (*self.p.item_packets(pages=(*PAGES,WEAR_PAGE)),*((packet(3993,['string'],['dlg_fashion_closet']),) if open_panel else ()),
                Fashion(self.g).collection(),*FashionShop(self.g).frame_packets(),children)

    def skin_packet(self):
        with self.p.transaction(write=False) as db:
            rows=[dict(skill_id=r['skill_id'],skin_id=r['skin_id'],skin_time=r['expires'],skin_active=int(r['active'] and r['expires']>time())) for r in db.execute('SELECT * FROM cosmetic_skins WHERE character_id=? ORDER BY skill_id,skin_id',(self.p.cid,))]
        return packet(4822,[('array','skill_skin_info')],[rows])

    def enter_effects(self,cids=None):
        with self.p.transaction(write=False) as db:
            rows=[dict(key=str(cid),value=a['enter_effect']) for cid in (cids or [self.p.cid]) if (a:=appearance(db,cid))['enter_effect']]
        # Native common_const.mWAR_EFFECT_TYPE.ENTER_EFFECT = 9.
        return (packet(3832,['number',('array','pair_string_number')],[9,rows]),) if rows else ()

    def actor_skin(self,skill):
        with self.p.transaction(write=False) as db:
            row=db.execute('SELECT skin_id FROM cosmetic_skins WHERE character_id=? AND skill_id=? AND active=1 AND expires>?',(self.p.cid,skill,int(time()))).fetchone()
        return packet(4825,['number','number'],[skill,SKIN_TYPES[(skill,row[0])] if row else 0])

    def _quantity(self,kind,amount):
        meta=self.sale_goods.get(str(sale_type(kind)))
        if not meta:raise ValueError('该商品暂未开放购买。')
        if not 1<=amount<=24:raise ValueError('购买数量须为1至24。')
        return meta

    def quote(self,orders,*,quote_slot='cosmetic_quote'):
        if not orders or len(orders)>24:raise ValueError('购物车为空或商品过多。')
        cost=sum(self._quantity(k,n)['yubo']*n for k,n in orders)
        with self.p.transaction(write=False) as db:
            yubo,coupon=self.wallet(db)
        resolved = [(sale_type(kind), amount) for kind, amount in orders]
        setattr(self.g,quote_slot,dict(id=secrets.token_hex(16),orders=orders,resolved=resolved,cost=cost,until=monotonic()+120))
        return cost,yubo,coupon

    def single_buy_offer(self,kind,amount):
        from native946_progression import lua_data
        from native946_mall_checkout import rows,TICKET_TYPES,plan,fudou_cost

        orders=[(kind,amount)]
        _,_,coupon=self.quote(orders)
        with self.p.transaction(write=False) as db:
            tickets=sum(v['amount'] for v in rows(db,self.p.cid,TICKET_TYPES))
            cost,_=plan(db,self.p.cid,orders,0)
        discounts=lua_data({48112:tickets}) if tickets else '{}'
        return packet(3661,['number','number','string']+['number']*4,
                      [kind,amount,discounts,coupon,cost,fudou_cost(orders),0])

    def cart_packet(self):
        from native946_progression import lua_data
        from native946_mall_checkout import fudou_eligible
        rows = []
        for index, kind in enumerate(getattr(self.g, 'cosmetic_cart', []), 1):
            product = self._quantity(kind, 1)
            rows.append([sale_type(kind), 1, product['yubo'],
                         product['yubo'] if fudou_eligible(kind) else 0, index, {}])
        return packet(3660,['string'],[lua_data(rows)])

    def buy(self,orders,coupon=0,ticket=0,*,quote_slot='cosmetic_quote'):
        q=getattr(self.g,quote_slot,None)
        if coupon not in (0,1):raise ValueError('福豆支付选项无效。')
        if not q or q['orders']!=orders or q['until']<monotonic():raise ValueError('请重新确认购买。')
        cost=sum(self._quantity(k,n)['yubo']*n for k,n in orders)
        if q['cost']!=cost:raise ValueError('价格已变，请重新确认。')
        orders = [(sale_type(kind), amount) for kind, amount in orders]
        if q.get('resolved') != orders:
            raise ValueError('商城目录已更新，请重新确认购买。')
        with self.p.transaction() as db:
            if db.execute('SELECT 1 FROM cosmetic_orders WHERE id=?',(q['id'],)).fetchone():raise ValueError('该笔购买已经处理。')
            from native946_mall_checkout import plan,consume,fudou_cost
            cost,deductions=plan(db,self.p.cid,orders,ticket,sale_goods=self.sale_goods)
            coupon_cost=fudou_cost(orders) if coupon else 0
            if coupon and (not coupon_cost or ticket):raise ValueError('该订单不支持福豆支付或同时抵用外观券。')
            currency='coupon' if coupon else 'yubo'
            cost=coupon_cost if coupon else cost
            spend_currency(db, self.p.owner, currency, cost)
            for kind,n in orders:
                for _ in range(n):
                    for part in DATA.get('sets',{}).get(str(kind),[kind]):self._add(db,part)
            if not coupon:consume(db,deductions)
            db.execute('INSERT INTO cosmetic_orders VALUES(?,?,?,?,?)',(q['id'],self.p.cid,cost,json.dumps(orders),int(time())))
        setattr(self.g,quote_slot,None)
        return self.g.defer_packets(lambda:(*self.currency_packets(),*self.p.item_packets(),self.mall(refresh=True),text_notice('购买成功，物品已发放至异宝匣或背包。')))

    def _add(self,db,kind,amount=1,backpack=False):
        backpack=backpack or str(kind) not in DATA['wearables']
        from native946_mall_bags import pages as bag_pages
        pages=bag_pages(db,self.p.cid) if backpack else PAGES
        maximum=min(500,DATA['items'][str(kind)]['max_stack'])
        if amount<1 or amount>10000:raise ValueError('发放数量无效。')
        if maximum>1 and not DATA['items'][str(kind)].get('valid_days',0):
            for row in db.execute('SELECT i.id,i.amount FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.character_id=? AND i.type=? AND i.page=? AND i.amount<? AND coalesce(e.expires,0)=0 ORDER BY i.id',
                           (self.p.cid,kind,pages[0],maximum)).fetchall():
                add=min(amount,maximum-row['amount']);amount-=add
                db.execute('UPDATE inventory SET amount=amount+? WHERE id=?',(add,row['id']))
                if not amount:return
        if amount>maximum:
            for n in range(0,amount,maximum):self._add(db,kind,min(maximum,amount-n),backpack)
            return
        occupied={(r[0],r[1]) for r in db.execute('SELECT page,slot FROM inventory WHERE character_id=?',(self.p.cid,))}
        place=next(((p,s) for p in pages for s in range(1,25) if (p,s) not in occupied),None)
        if place is None:raise ValueError('背包或异宝匣已满。')
        row=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',(self.p.cid,kind,*place,amount))
        days=DATA['items'][str(kind)].get('valid_days',0) if backpack else DATA['items'][str(kind)]['days']
        # Wardrobe items begin their duration on acquisition; skill consumables
        # begin their active duration only when used.
        db.execute('INSERT INTO cosmetic_expiry VALUES(?,?)',(row.lastrowid,int(time())+days*86400 if days else 0))
        return row.lastrowid

    def _state(self,db,**values):
        db.execute('INSERT OR IGNORE INTO cosmetic_state(character_id) VALUES(?)',(self.p.cid,))
        allowed={'weapon_idx','weapon_color','color1','color2','color3'}
        if not values.keys()<=allowed:raise ValueError('外观字段无效。')
        db.execute('UPDATE cosmetic_state SET '+','.join(k+'=?' for k in values)+' WHERE character_id=?',(*values.values(),self.p.cid))

    def use(self,item_id):
        with self.p.transaction() as db:
            item=db.execute('SELECT i.*,coalesce(e.expires,0) AS expires FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.id=? AND i.character_id=?',(item_id,self.p.cid)).fetchone()
            if not item:raise ValueError('该外观不属于当前角色。')
            kind=str(item['type'])
            from native946_mall_bags import pages as bag_pages
            if item['page'] not in (*PAGES,WEAR_PAGE,*bag_pages(db,self.p.cid)):raise ValueError('请先取出该物品。')
            if item['expires'] and item['expires']<=time():raise ValueError('该外观已到期。')
            if kind in DATA['skills']:
                meta=DATA['skills'][kind];days=DATA['items'][kind]['days'] or 30
                old=db.execute('SELECT expires FROM cosmetic_skins WHERE character_id=? AND skill_id=? AND skin_id=?',(self.p.cid,meta['SkillId'],meta['SkinId'])).fetchone()
                until=max(int(time()),old[0] if old else 0)+days*86400
                db.execute('UPDATE cosmetic_skins SET active=0 WHERE character_id=? AND skill_id=?',(self.p.cid,meta['SkillId']))
                db.execute('INSERT INTO cosmetic_skins VALUES(?,?,?,?,1) ON CONFLICT(character_id,skill_id,skin_id) DO UPDATE SET expires=excluded.expires,active=1',(self.p.cid,meta['SkillId'],meta['SkinId'],until))
                if item['amount']==1:db.execute('DELETE FROM inventory WHERE id=?',(item_id,))
                else:db.execute('UPDATE inventory SET amount=amount-1 WHERE id=?',(item_id,))
            else:
                meta=DATA['wearables'].get(kind)
                if not meta:
                    if kind in FASHION_SHOP['frames']:return FashionShop(self.g).use_frame(db,item)
                    from native946_mall_uses import MallUses
                    result=MallUses(self.g).use(db,item,self)
                    left=db.execute('SELECT amount FROM inventory WHERE id=?',(item_id,)).fetchone()
                    scored=record_use(db,self.p.cid,item['type']) if left is None or left[0]<item['amount'] else False
                    return (result,self.g.defer_packets(Fashion(self.g).sync)) if scored else result
                if kind not in DATA['goods']:raise ValueError('此物品未开放穿戴。')
                if meta.get('sex',self.g.character['sex'])!=self.g.character['sex']:raise ValueError('该外观的性别不符。')
                if item['page']==WEAR_PAGE:self.move(item_id,PAGES[0],-1)
                else:self.move(item_id,WEAR_PAGE,meta['slot'])
        # c_skill_skin_open (4822) opens the panel; it is not a passive sync.
        # Only using a skill-skin license should open it after item use.
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.visual_packets(),
                                           *((self.skin_packet(),) if kind in DATA['skills'] else ())))

    def move(self,item_id,page,slot):
        with self.p.transaction() as db:
            from native946_mall_bags import pages as bag_pages
            normal=bag_pages(db,self.p.cid);withdraw=bag_pages(db,self.p.cid,True)
            row=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',(item_id,self.p.cid)).fetchone()
            if not row or row['page'] not in (*PAGES,WEAR_PAGE,*withdraw):raise ValueError('物品位置已变化。')
            if page not in (*PAGES,WEAR_PAGE,*normal):raise ValueError('目标物品栏尚未开启或已到期。')
            if str(row['type']) not in DATA['goods'] and str(row['type']) not in DATA['skills']:raise ValueError('这不是可收入异宝匣的外观物品。')
            from native946_child_fashion import is_fashion
            child_fashion=is_fashion(row['type'])
            if child_fashion and (row['page'] in PAGES)!=(page in PAGES):
                raise ValueError('请使用道具选择孩子，已绑定的孩子时装保存在异宝匣。')
            if page in PAGES and str(row['type']) not in DATA['wearables'] and not child_fashion:
                raise ValueError('该商品应存放在背包中。')
            meta=DATA['wearables'].get(str(row['type']))
            if page==WEAR_PAGE:
                if not meta or str(row['type']) not in DATA['goods'] or slot!=meta['slot']:raise ValueError('外观穿戴槽不匹配。')
                if meta.get('sex',self.g.character['sex'])!=self.g.character['sex']:raise ValueError('该角色不能穿戴此部件。')
                expiry=db.execute('SELECT expires FROM cosmetic_expiry WHERE item_id=?',(item_id,)).fetchone()
                if expiry and expiry[0] and expiry[0]<=time():raise ValueError('该外观已到期。')
                # Native GarmentInfo compares Part with the other BanPart in
                # both directions. Displace conflicting pieces atomically.
                part={'GARMENT_HEAD':1,'GARMENT_CLOTHES':2,'GARMENT_SHOULDER':3}.get(meta['kind'])
                if part:
                    for worn in db.execute('SELECT * FROM inventory WHERE character_id=? AND page=29 AND id<>?',(self.p.cid,item_id)).fetchall():
                        other_meta=DATA['wearables'].get(str(worn['type']),{})
                        other_part={'GARMENT_HEAD':1,'GARMENT_CLOTHES':2,'GARMENT_SHOULDER':3}.get(other_meta.get('kind'))
                        if other_part and (meta.get('ban')==other_part or other_meta.get('ban')==part):self.move(worn['id'],PAGES[0],-1)
            elif page not in (*PAGES,8):raise ValueError('外观物品只能放入异宝匣或背包。')
            if slot in (-1,0) and page!=WEAR_PAGE:
                places={(r[0],r[1]) for r in db.execute('SELECT page,slot FROM inventory WHERE character_id=?',(self.p.cid,))}
                options=PAGES if page in PAGES else normal
                place=next(((p,s) for p in options for s in range(1,25) if (p,s) not in places),None)
                if not place:raise ValueError('目标栏已满。')
                page,slot=place
            if page!=WEAR_PAGE and not 1<=slot<=24:raise ValueError('格子无效。')
            other=db.execute('SELECT * FROM inventory WHERE character_id=? AND page=? AND slot=?',(self.p.cid,page,slot)).fetchone()
            if other and other['id']==item_id:return
            if other and is_fashion(other['type']) and (row['page'] in PAGES)!=(page in PAGES):
                raise ValueError('孩子时装需要保留在原有物品栏。')
            if other and row['page'] in withdraw and row['page'] not in normal:raise ValueError('到期百纳袋只能取出物品，不能交换存入。')
            if other and row['page']==WEAR_PAGE:
                om=DATA['wearables'].get(str(other['type']))
                expiry=db.execute('SELECT expires FROM cosmetic_expiry WHERE item_id=?',(other['id'],)).fetchone()
                if (not om or str(other['type']) not in DATA['goods'] or om['slot']!=row['slot'] or
                    om.get('sex',self.g.character['sex'])!=self.g.character['sex'] or
                    (expiry and expiry[0] and expiry[0]<=time())):raise ValueError('不能将该物品交换到外观穿戴槽。')
            db.execute('UPDATE inventory SET page=-2,slot=-2 WHERE id=?',(item_id,))
            if other:db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(row['page'],row['slot'],other['id']))
            db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(page,slot,item_id))
            if page==WEAR_PAGE:record_use(db,self.p.cid,row['type'])
            elif other and row['page']==WEAR_PAGE:record_use(db,self.p.cid,other['type'])

    def menu(self,npc):
        nid=npc['npc_id'];dye='染色' in npc.get('title','')
        choices=[('全身染色',dict(kind='cosmetic',op='dye_all')),('局部染色',dict(kind='cosmetic',op='dye_part')),('恢复人物原色',dict(kind='cosmetic',op='reset_color'))] if dye else [
            ('购买外观',dict(kind='cosmetic',op='mall')),('查看异宝匣',dict(kind='cosmetic',op='closet')),
            ('武器幻化',dict(kind='cosmetic',op='weapons')),('人物染色',dict(kind='cosmetic',op='dye_part')),
            ('技能皮肤',dict(kind='cosmetic',op='skins')),('停用全部技能皮肤',dict(kind='cosmetic',op='skins_off')),
            ('领取本机外观测试用品（一次）',dict(kind='cosmetic',op='kit')),('武器染色',dict(kind='cosmetic',op='weapon_color')),
            ('网罗日月系列购买',dict(kind='cosmetic',op='fashion_shop')),
            ('特卖会',dict(kind='cosmetic',op='specmall')),
            ('商品及兑换目录',dict(kind='cosmetic',op='shop_catalog',page=0)),
            ('商店NPC名录',dict(kind='cosmetic',op='shop_npcs',page=0)),
            ('时装强化与雕饰',dict(kind='cosmetic',op='fashion_refinement')),
            ('分类道具商城',dict(kind='cosmetic',op='classified_mall'))]
        from native946_npc_services import NpcServices
        return NpcServices(self.g)._menu(nid,'欢迎光临异宝斋，请选择需要的服务。',choices)

    def execute(self,nid,action):
        from native946_npc_services import NpcServices
        NpcServices(self.g).validate(nid,'cosmetic');op=action['op']
        if op=='classified_mall':
            from native946_classified_mall import ClassifiedMall
            return (packet(1183,['string'],['dlg_npc_chat']), *ClassifiedMall(self.g).open())
        if op=='fashion_refinement':
            return NpcServices(self.g)._menu(nid,'可用背包或异宝匣中的同部位永久沁水晴岚外观兑换原生时装。',[
                ('强化时装',dict(kind='cosmetic',op='fashion_refine_tab',tab=3)),
                ('雕饰时装',dict(kind='cosmetic',op='fashion_refine_tab',tab=4)),
                ('粉黛升级',dict(kind='cosmetic',op='fashion_refine_tab',tab=2)),
                ('购买本机时装辅料（15000现金）',dict(kind='cosmetic',op='fashion_refine_supplies')),
                *((f'兑换沁水晴岚·{part}时装',dict(kind='cosmetic',op='fashion_refine_exchange',part=part))
                  for part in ('头冠','服装','肩饰')),
            ])
        if op.startswith('fashion_refine_'):
            from native946_fashion_refinement import FashionRefinement
            service=FashionRefinement(self.g)
            if op=='fashion_refine_tab':
                return (packet(1183,['string'],['dlg_npc_chat']),service.open(action['tab']))
            if op=='fashion_refine_exchange':return service.exchange(action['part'])
            from native946_refinement import debit
            with self.p.transaction() as db:
                debit(db,self.p.cid,15000)
                for kind,amount in ((46207,15),(46208,1),(46209,1),(46210,1)):
                    self._add(db,kind,amount,backpack=True)
            return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.p.attribute_packets(),
                text_notice('已获得繁花锦15个、玲珑珠花1个、龙灵宝佩1个及金凤玄羽1个。')))
        if op in ('specmall','shop_catalog','shop_catalog_detail','shop_npcs'):
            from native946_shops import Shops
            shops=Shops(self.g)
            if op=='specmall':return shops.open_special(nid)
            if op=='shop_npcs':return shops.npc_catalog(nid,action.get('page',0))
            if op=='shop_catalog':return shops.catalog_menu(nid,action.get('page',0))
            return shops.catalog_details(nid,action['selected'],action.get('page',0))
        if op=='fashion_shop':return FashionShop(self.g).menu(nid)
        if op=='fashion_shop_item':return FashionShop(self.g).open(action['item_type'])
        if op=='mall':return (*self.currency_packets(),self.mall())
        if op=='closet':
            return self.closet(open_panel=True)
        if op in ('dye_all','dye_part'):
            if str(self.g.character['role_id']) not in DATA['parts']:raise ValueError('此角色尚无染色配置。')
            self.refresh();session=secrets.randbelow(1<<29)+10000
            self.g.cosmetic_dye=dict(session=session,npc=nid,scene=self.g.character['map_id'],until=monotonic()+120,mode=op)
            return packet(115 if op=='dye_all' else 117,['number',('fixed_array','number',3),'number'],[self.g.character['role_id'],[self.g.character['appearance']['color'+str(i)] for i in (1,2,3)],session])
        if op=='reset_color':
            with self.p.transaction() as db:self._state(db,color1=0,color2=0,color3=0)
            return self.g.defer_packets(self.visual_packets)
        if op in ('weapons','weapon_color'):
            from native946_mall_uses import MallUses
            return MallUses(self.g).weapon_open(nid,op)
        if op=='weapon':
            if action['model'] not in DATA['models'][str(self.g.character['role_id'])]:raise ValueError('此角色没有该武器资源。')
            with self.p.transaction() as db:self._state(db,weapon_idx=action['model'])
            return self.g.defer_packets(self.visual_packets)
        if op=='skins':return self.skin_packet()
        if op=='skins_off':
            with self.p.transaction() as db:db.execute('UPDATE cosmetic_skins SET active=0 WHERE character_id=?',(self.p.cid,))
            return self.g.defer_packets(self.skin_packet)
        if op=='kit':
            with self.p.transaction() as db:
                db.execute('INSERT OR IGNORE INTO cosmetic_wallet(character_id) VALUES(?)',(self.p.cid,))
                changed=db.execute('UPDATE cosmetic_wallet SET test_claimed=1 WHERE character_id=? AND test_claimed=0',(self.p.cid,)).rowcount
                if not changed:raise ValueError('本角色已经领取过本机测试用品。')
                funded = db.execute("""INSERT INTO mall_account_wallet(owner,yubo,test_yubo_claimed)
                    VALUES(?,100000,1) ON CONFLICT(owner) DO UPDATE SET yubo=yubo+100000,test_yubo_claimed=1
                    WHERE test_yubo_claimed=0""", (self.p.owner,)).rowcount
                # 8 native skill appearances, with their original 30-day item IDs.
                for k in range(56313,56321):self._add(db,k,backpack=True)
            message = '已领取10万测试玉帛及8种技能皮肤道具，仅用于本机测试。' if funded else '已领取8种技能皮肤道具，本账号已领取过测试玉帛。'
            return self.g.defer_packets(lambda:(*self.currency_packets(),*self.p.item_packets(),self.mall(refresh=True),text_notice(message)))
        raise ValueError('外观操作无效。')

    def receive(self,mid,r):
        if mid in CLASSIFIED_REQUESTS:
            from native946_classified_mall import ClassifiedMall
            return ClassifiedMall(self.g).receive(mid,r)
        from native946_progression import lua_data
        if mid in CHILD_FASHION_REQUESTS:
            from native946_child_fashion import receive
            from native946_role_systems import read
            args=read(mid,r)
            with self.p.transaction() as db:
                result=receive(self.g,db,mid,args)
            if mid==5261:
                return self.g.defer_packets(lambda:(*self.p.item_packets(),*result,*Fashion(self.g).sync()))
            return result
        if mid in SHOP_REQUESTS:return FashionShop(self.g).receive(mid,r)
        if mid in FASHION_REQUESTS:return Fashion(self.g).receive(mid,r)
        if mid==1033:
            count=r.take(1)[0]
            if count!=3:raise ValueError('染色参数数量无效。')
            args=[r.string().decode('ascii') for _ in range(3)];session=r.integer();r.finish()
            d=getattr(self.g,'cosmetic_dye',None)
            if not d or session!=d['session'] or monotonic()>d['until'] or d['scene']!=self.g.character['map_id']:raise ValueError('染色会话已失效，请重新打开。')
            from native946_npc_services import NpcServices
            NpcServices(self.g).validate(d['npc'],'cosmetic')
            if any(not v.isascii() or not v.isdecimal() or len(v)>3 for v in args):raise ValueError('染色值无效。')
            colors=list(map(int,args));role=str(self.g.character['role_id'])
            valid=colors==[0,0,0] or (colors in [v for v in DATA['colors'][role].values() if isinstance(v,list)] if d['mode']=='dye_all' else all(v==0 or v in DATA['parts'][role]['color'+str(i)] for i,v in enumerate(colors,1)))
            if not valid:raise ValueError('该角色不支持这组染色。')
            with self.p.transaction() as db:self._state(db,**dict(zip(['color1','color2','color3'],colors)))
            self.g.cosmetic_dye=None
            return self.g.defer_packets(self.visual_packets)
        if mid in (3667,837,4824,3685):
            r.finish()
            return (*self.currency_packets(),self.mall()) if mid==3667 else self.closet() if mid==837 else self.skin_packet() if mid==4824 else packet(3684,['number'],[0])
        if mid in (3128,3689):
            kind=r.integer();r.finish();return (*self.currency_packets(),self.mall(kind))
        if mid==825:
            category,kind=r.integer(),r.integer();r.finish();return (*self.currency_packets(),self.mall(kind))
        if mid==830:
            flag=r.integer();r.finish()
            if flag not in (0,1):raise ValueError('邮件设置无效。')
            with self.p.transaction() as db:
                db.execute('INSERT OR IGNORE INTO cosmetic_wallet(character_id) VALUES(?)',(self.p.cid,))
                db.execute('UPDATE cosmetic_wallet SET mail=? WHERE character_id=?',(flag,self.p.cid))
            return packet(819,['number'],[flag])
        if mid==831:r.finish();return packet(820,[('array','fresh_award_item')],[[]])
        if mid==838:r.integer();r.finish();raise ValueError('本机不接入真实充值兑换。')
        if mid==4823:
            skill,skin=r.integer(),r.integer();active=r.take(1)[0];r.finish()
            if active not in (0,1):raise ValueError('皮肤开关无效。')
            with self.p.transaction() as db:
                row=db.execute('SELECT expires FROM cosmetic_skins WHERE character_id=? AND skill_id=? AND skin_id=?',(self.p.cid,skill,skin)).fetchone()
                if not row or row[0]<=time():raise ValueError('请先使用对应技能皮肤道具激活，且须在有效期内。')
                db.execute('UPDATE cosmetic_skins SET active=0 WHERE character_id=? AND skill_id=?',(self.p.cid,skill))
                db.execute('UPDATE cosmetic_skins SET active=? WHERE character_id=? AND skill_id=? AND skin_id=?',(active,self.p.cid,skill,skin))
            return self.g.defer_packets(self.skin_packet)
        if mid in (833,3663):
            kind,amount=r.integer(),r.integer()
            if mid==833:
                for _ in range(4):r.integer()
            r.finish()
            if mid==3663:return self.single_buy_offer(kind,amount)
            cost,yubo,coupon=self.quote([(kind,amount)])
            from native946_mall_checkout import plan,fudou_cost
            with self.p.transaction(write=False) as db:
                cost,_=plan(db,self.p.cid,[(kind,amount)],0)
            beans=fudou_cost([(kind,amount)])
            pay_coupon=beans if 0<beans<=coupon else 0
            return packet(822,['number']*9+['byte'],
                          [kind,amount,cost,0,yubo,pay_coupon,coupon,0 if pay_coupon else cost,int(bool(pay_coupon)),1])
        if mid in (826,827,835,3665):
            kind,amount=r.integer(),r.integer();coupon=0;ticket=0
            if mid==827:r.take(1);r.integer()
            if mid in (835,3665):coupon=r.integer()
            if mid==3665:ticket=r.integer()
            r.finish();return self.buy([(kind,amount)],coupon,ticket)
        if mid in (3668,3670):
            value=r.integer();r.finish();cart=list(getattr(self.g,'cosmetic_cart',[]))
            if mid==3668:
                self._quantity(value,1)
                if len(cart)>=24:raise ValueError('购物车已满。')
                cart.append(value)
            else:
                if not 1<=value<=len(cart):raise ValueError('购物车位置无效。')
                cart.pop(value-1)
            self.g.cosmetic_cart=cart
            self.g.cosmetic_quote=None
            return self.cart_packet()
        if mid in (3669,3671):
            values=numeric_table(r.string().decode('ascii'));coupon,ticket=r.integer(),r.integer();r.finish()
            cart=getattr(self.g,'cosmetic_cart',[]);orders=[]
            # The isolated client includes the edited quantity with its index;
            # original unpatched index-only clients keep one item per entry.
            if list(values)!=list(range(1,len(values)+1)):raise ValueError('购物车索引无效。')
            selected=set()
            for entry in values.values():
                if isinstance(entry,dict):
                    if set(entry)!={1,2}:raise ValueError('购物车数量格式无效。')
                    index,amount=entry[1],entry[2]
                else:index,amount=entry,1
                if not isinstance(index,int) or not isinstance(amount,int) or not 1<=index<=len(cart) or not 1<=amount<=24 or index in selected:raise ValueError('购物车内容已变化。')
                selected.add(index);orders.append((cart[index-1],amount))
            if mid==3669:
                self.quote(orders)
                from native946_mall_checkout import plan,fudou_cost
                with self.p.transaction(write=False) as db:cost,_=plan(db,self.p.cid,orders,ticket)
                beans=fudou_cost(orders)
                if coupon not in (0,1) or coupon and (not beans or ticket):raise ValueError('该订单不支持福豆支付。')
                return packet(3659,['number']*3,[0 if coupon else cost,beans if coupon else 0,ticket])
            result=self.buy(orders,coupon,ticket);self.g.cosmetic_cart=[]
            return (result,packet(3660,['string'],['{}']))
        if mid==3672:
            kind,item=r.integer(),r.integer();r.finish()
            if kind == 2:
                if item in (_mounts.DYE_MATERIAL, _mounts.DYE_SHORTCUT):
                    return self.single_buy_offer(_mounts.DYE_MATERIAL, 1)
                from native946_shops import Shops
                return Shops(self.g).open_special(item=item)
            if kind in (3, 5):
                from native946_mall_uses import MallUses
                return MallUses(self.g).weapon_open(None, 'weapons' if kind == 3 else 'weapon_color')
            if kind == 4:
                from native946_refinement import Refinement
                return Refinement(self.g).open_halo()
            if kind == 6:
                from native946_nice_ids import NiceIds
                return NiceIds(self.g).open(from_mall=True)
            if kind == 7:
                from native946_mall_uses import MallUses
                return MallUses(self.g).mounts(3683)
            return self.skin_packet() if kind==4824 else self.closet(open_panel=True)
        if mid==3679:r.string();r.finish();raise ValueError('请逐件购买套装部件，避免重复购买已拥有的部件。')
        raise ValueError('暂不支持该商城请求。')
