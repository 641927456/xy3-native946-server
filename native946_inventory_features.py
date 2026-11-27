"""Dispatch and shared owned-item helpers for the remaining native bag tools."""
import json
from pathlib import Path
from time import time
from native946_codec import packet
DATA=json.loads(Path(__file__).with_name('native946_inventory_features_data.json').read_text('utf8'))
RULES=DATA['local_rules']
from native946_hero_page import REQUESTS as HERO_REQUESTS,WRITES as HERO_WRITES,DATA as HERO_DATA
from native946_hero_extra_points import ITEM_TYPES as EXTRA_POINT_ITEMS
from native946_daily_content import ITEMS as DAILY_CONTENT_ITEMS
for _kind,_meta in DAILY_CONTENT_ITEMS.items():DATA['items'].setdefault(_kind,_meta)
for _kind,_meta in json.loads(Path(__file__).with_name('native946_afei_expansion_data.json').read_text('utf8'))['items'].items():
    DATA['items'].setdefault(_kind,_meta)
from native946_star_rewards import ITEMS as STAR_REWARD_ITEMS
for _kind,_meta in STAR_REWARD_ITEMS.items():DATA['items'].setdefault(_kind,_meta)
from native946_constellations import SIGIL_ITEMS, GROUP_POLICY, GROUP_PRIZES
for _kind,_meta in SIGIL_ITEMS.items():DATA['items'].setdefault(str(_kind),_meta)
for _kind,_meta in GROUP_PRIZES.items():DATA['items'].setdefault(_kind,_meta)
for _kind,_meta in GROUP_POLICY['prize_materials'].items():DATA['items'].setdefault(_kind,_meta)
from native946_role_systems import REQUESTS as ROLE_REQUESTS,WRITES as ROLE_WRITES,ITEM_TYPES as ROLE_ITEMS,DATA as ROLE_DATA
from native946_skill6 import ITEM as SKILL6_ITEM, MANUAL as SKILL6_MANUAL
for _kind,_meta in ROLE_DATA['items'].items():DATA['items'].setdefault(_kind,_meta)
for _kind,_meta in HERO_DATA['food_items'].items():DATA['items'].setdefault(_kind,_meta)
DATA['items'].setdefault('45671',{k:v for k,v in HERO_DATA['switch_item'].items() if k!='type'})
DATA['items'].setdefault(str(SKILL6_MANUAL),SKILL6_ITEM)
REQUESTS=set(range(3464,3471))|{3574,3575,3576,3587,3588,3589,3590,3592,3593,4317,4318,5201,5202,5205,5206}
WRITES=REQUESTS|HERO_WRITES|ROLE_WRITES
REQUESTS=REQUESTS|HERO_REQUESTS|ROLE_REQUESTS
ITEM_TYPES={int(k) for k in DATA['transform_cards']}|set(range(47448,47471))|{int(k) for k in DATA['fish']['fishes']}|{47512,47513,47514,45671}|{int(k) for k in DATA['glyph_slots']}|EXTRA_POINT_ITEMS
ITEM_TYPES|=ROLE_ITEMS
ITEM_TYPES|={6694,47973,47974}

def schema(db):
    from native946_equipment_plans import schema as plans
    from native946_transform import schema as transform
    from native946_fishing import schema as fishing
    from native946_quick_glyphs import schema as glyphs
    from native946_hero_page import schema as hero
    from native946_role_systems import schema as roles
    for apply in (plans,transform,fishing,glyphs,hero,roles):apply(db)

def owned(db,cid,iid,*,fish=False):
    from native946_mall_bags import pages
    row=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',(iid,cid)).fetchone()
    if not row or row['page'] not in (*pages(db,cid),*(range(7000,7004) if fish else ())):raise ValueError('物品不在当前角色可用背包中。')
    from native946_inventory_locks import require_unlocked
    require_unlocked(db,cid,iid)
    return dict(row)

def consume(db,cid,iid,count=1,*,fish=False):
    item=owned(db,cid,iid,fish=fish)
    if type(count) is not int or count<1 or item['amount']<count:raise ValueError('所需物品数量不足。')
    if count==item['amount']:db.execute('DELETE FROM inventory WHERE id=? AND character_id=?',(iid,cid))
    else:db.execute('UPDATE inventory SET amount=amount-? WHERE id=? AND character_id=?',(count,iid,cid))
    return item

def give(db,p,kind,count=1,*,destinations=None):
    from native946_mall_bags import pages
    from native946_inventory_locks import records
    if str(kind) not in DATA['items'] or type(count) is not int or count<1:raise ValueError('奖励物品定义无效。')
    capacity=destinations if destinations is not None else {page:24 for page in pages(db,p.cid)}
    if not capacity:raise ValueError('请先装备有空间的鱼篓。')
    locked=records(db,p.cid);maximum=DATA['items'][str(kind)]['max_stack']
    for row in db.execute('SELECT * FROM inventory WHERE character_id=? AND type=? ORDER BY page,slot',(p.cid,kind)).fetchall():
        if row['page'] not in capacity or row['slot']>capacity[row['page']] or row['id'] in locked:continue
        add=min(count,max(0,maximum-row['amount']))
        if add:db.execute('UPDATE inventory SET amount=amount+? WHERE id=? AND character_id=?',(add,row['id'],p.cid));count-=add
        if not count:return
    used={(r['page'],r['slot']) for r in db.execute('SELECT page,slot FROM inventory WHERE character_id=?',(p.cid,))}
    for page,limit in capacity.items():
        for slot in range(1,limit+1):
            if (page,slot) in used:continue
            add=min(maximum,count)
            db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',(p.cid,kind,page,slot,add));count-=add
            if not count:return
    raise ValueError('物品存储空间不足，请先整理。')

def receive(game,mid,r):
    if mid in ROLE_REQUESTS:
        from native946_role_systems import receive as role_receive
        return role_receive(game,mid,r)
    if mid in HERO_REQUESTS:
        from native946_hero_page import HeroPage
        return HeroPage(game).receive(mid,r)
    if mid in range(3464,3471):
        from native946_transform import Transform
        return Transform(game).receive(mid,r)
    if mid in (4317,4318):
        from native946_quick_glyphs import QuickGlyphs
        return QuickGlyphs(game).receive(mid,r)
    if mid in (5201,5202,5205,5206):
        from native946_equipment_plans import EquipmentPlans
        return EquipmentPlans(game).receive(mid,r)
    from native946_fishing import Fishing
    return Fishing(game).receive(mid,r)

def use(game,iid):
    state=game.progression.snapshot();item=next((i for i in state['items'] if i['id']==iid),None)
    if item is None or item['type'] not in ITEM_TYPES:raise ValueError('道具不可用。')
    if game.battle is not None:raise ValueError('请先结束战斗。')
    if item['type'] in (6694,47973,47974):
        from native946_lineups import use_book
        return use_book(game,iid)
    from native946_role_battle import DATA as COMBAT_DATA
    if str(item['type']) in COMBAT_DATA['wear_items']:
        if item['type'] not in COMBAT_DATA['secret_items']:
            from native946_role_artifacts import use as artifact_use
            return artifact_use(game,iid)
        from native946_inventory_locks import require_unlocked
        with game.progression.transaction(write=False) as db:require_unlocked(db,game.progression.cid,iid)
        page,slot=(game.progression.free_bag_slot(state) if item['page'] in (1,3,4) else (state['equipment_page'],15))
        game.progression.move_item(iid,page,slot)
        return game.defer_packets(lambda:game.progression.sync())
    if str(item['type']) in ROLE_DATA['baby_equip']:
        from native946_role_baby_tools import equip
        return equip(game,iid)
    if item['type'] in ROLE_ITEMS:
        from native946_role_systems import use as role_use
        return role_use(game,iid)
    if item['type']==45671:
        with game.progression.transaction(write=False) as db:owned(db,game.progression.cid,iid)
        from native946_hero_page import HeroPage
        return HeroPage(game).quote_switch()
    if item['type'] in EXTRA_POINT_ITEMS:
        from native946_hero_extra_points import ExtraPoints
        return ExtraPoints(game).use(iid)
    if str(item['type']) in DATA['transform_cards']:
        from native946_transform import Transform
        return Transform(game).learn(iid)
    if str(item['type']) in DATA['glyph_slots']:
        from native946_quick_glyphs import QuickGlyphs
        return QuickGlyphs(game).use(iid)
    from native946_fishing import Fishing
    return Fishing(game).register(iid)

def tick(game):
    if game.battle is not None:return ()
    result=[]
    from native946_role_systems import tick as role_tick
    result.extend(role_tick(game))
    from native946_fishing import Fishing
    result.extend(Fishing(game).tick())
    now=time()
    if now>=getattr(game,'inventory_expiry_check',0):
        game.inventory_expiry_check=now+10
        with game.progression.transaction(write=False) as db:
            stale=db.execute('SELECT 1 FROM native_transform_state WHERE character_id=? AND shape<>0 AND expires<=?',(game.progression.cid,int(now))).fetchone()
            glyph=db.execute('SELECT 1 FROM native_temporary_glyphs WHERE character_id=? AND expires<=?',(game.progression.cid,int(now))).fetchone()
        if stale or glyph:
            with game.progression.transaction() as db:
                db.execute('UPDATE native_transform_state SET shape=0 WHERE character_id=? AND expires<=?',(game.progression.cid,int(now)))
                db.execute('DELETE FROM native_temporary_glyphs WHERE character_id=? AND expires<=?',(game.progression.cid,int(now)))
                after=game.progression._snapshot(db)
                db.execute('UPDATE progression SET hp=min(hp,?),mp=min(mp,?) WHERE character_id=?',(after['hpmax'],after['mpmax'],game.progression.cid))
            from native946_cosmetics import Cosmetics
            result.extend(game.progression.sync());result.extend(Cosmetics(game).visual_packets())
    return tuple(result)
