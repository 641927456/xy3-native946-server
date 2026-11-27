"""Character-page systems: native contracts, owned state and atomic operations.

Economic fallbacks are explicitly local and kept in the accompanying catalog.
Opening a panel never creates achievements, tournament results or currency.
"""
import json
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from time import time
from native946_codec import packet
from native946_codec import text_notice

DATA=json.loads(Path(__file__).with_name('native946_role_systems_data.json').read_text('utf8'))
RULES=DATA['local_rules'];WIRE_VERSION=1
GROUPS={
 'growth':{5111,5112,3180,3181,3182,3183,3184,4290,4291,4292,4293,2549,2550,2551,2799,5022},
 'arts':{3336,3340,3341,3342,3343,3344,3345,3357,4431,4432,856,857,858,859,860,861,862,863,864,865,866,867,875,876,877,878,879,880},
 'society':set(range(660,683))|{2419,3072,3073,3074,3075,3076,3083,3084,3086,3087,3088,3089,3090,3091,3092,3093,3094,3095,3096,3097,3098,3099,2457,2458}|set(range(2850,2865)),
 'honors':set(range(4565,4572))|{5323,5324,5326},
}
# 3336 is a server response, never accept it as a request.
GROUPS['arts'].discard(3336)
GROUPS['city_war']={int(mid) for mid,row in DATA['protocols'].items() if row['name'].startswith(('s_knight_','s_cocfight_','s_get_coc_ranklist'))}|{2936}
GROUPS['baby_tools']=set(range(3077,3083))|{3088,3089,3090,3091,4178,4746}
GROUPS['society']-=GROUPS['baby_tools']
GROUPS['artifacts']={310,313,314,322}
DATA['protocols'].update({
 '310':dict(name='s_add_last',fields=[]),'313':dict(name='s_get_fabao_info',fields=[['fabao_id','number']]),
 '314':dict(name='s_get_fabao_list',fields=[]),'322':dict(name='s_set_lianyaolu',fields=[['fabao_id','number',0]]),
 '302':dict(name='c_fabao_info',fields=[['info','fabao_info']]),
 '303':dict(name='c_fabao_list',fields=[['pos1','number'],['pos2','number'],['pos3','number'],['comp_max','number'],['fabao_id','number',0],['msg','string']])})
REQUESTS=set().union(*GROUPS.values());WRITES=REQUESTS
CARD_ITEMS={int(v['item_type']):int(k) for k,v in DATA['lingyou_info'].items()}
ITEM_TYPES=set(CARD_ITEMS)|{46988,46222,47932,47933,45222,45223,46090}|set(map(int,DATA['baby_equip']))
from native946_role_battle import DATA as COMBAT_DATA
ITEM_TYPES.update(map(int,COMBAT_DATA['wear_items']))
DATA['items'].update(COMBAT_DATA['wear_items'])
DATA['items'].update(COMBAT_DATA['resource_items'])

def schema(db):
    from native946_party import schema as party_schema
    party_schema(db)
    for sql in (
      'CREATE TABLE IF NOT EXISTS native_role_system_state(character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,system TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(character_id,system))',
      'CREATE TABLE IF NOT EXISTS native_role_system_events(character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,source TEXT NOT NULL,event_id TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(character_id,source,event_id))',
      'CREATE TABLE IF NOT EXISTS native_medals(character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,medal_id INTEGER NOT NULL,progress INTEGER NOT NULL DEFAULT 0,unlocked INTEGER NOT NULL DEFAULT 0,expires INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(character_id,medal_id))',
      'CREATE TABLE IF NOT EXISTS native_match_follows(character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,target_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,PRIMARY KEY(character_id,target_id),CHECK(character_id<>target_id))',
      'CREATE TABLE IF NOT EXISTS native_match_results(character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,match_type TEXT NOT NULL,season INTEGER NOT NULL,event_id TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(character_id,match_type,season))',
      'CREATE TABLE IF NOT EXISTS native_match_likes(character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,target_id INTEGER NOT NULL,match_type TEXT NOT NULL,season INTEGER NOT NULL,PRIMARY KEY(character_id,target_id,match_type,season),FOREIGN KEY(target_id,match_type,season) REFERENCES native_match_results(character_id,match_type,season) ON DELETE CASCADE)',
      'CREATE TABLE IF NOT EXISTS native_coc_buildings(id INTEGER PRIMARY KEY AUTOINCREMENT,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,kind INTEGER NOT NULL,position INTEGER NOT NULL,level INTEGER NOT NULL DEFAULT 0,ready INTEGER NOT NULL DEFAULT 0,harvest INTEGER NOT NULL DEFAULT 0,UNIQUE(character_id,position))',
      'CREATE TABLE IF NOT EXISTS native_babies(id INTEGER PRIMARY KEY AUTOINCREMENT,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,value TEXT NOT NULL)',
      'CREATE TABLE IF NOT EXISTS native_official_posts(id INTEGER PRIMARY KEY AUTOINCREMENT,character_id INTEGER NOT NULL REFERENCES characters(id),amount INTEGER NOT NULL,reward INTEGER NOT NULL,taken_by INTEGER REFERENCES characters(id),done INTEGER NOT NULL DEFAULT 0)',
    ):db.execute(sql)
    from native946_role_city_war import schema as city_war_schema
    city_war_schema(db)

def state(db,cid,system,defaults=None):
    row=db.execute('SELECT value FROM native_role_system_state WHERE character_id=? AND system=?',(cid,system)).fetchone()
    result=deepcopy(defaults or {})
    if row:result.update(json.loads(row[0]))
    return result

def save(db,cid,system,value):
    raw=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    if len(raw.encode('utf8'))>100000:raise ValueError('玩法存档过大。')
    db.execute('INSERT INTO native_role_system_state VALUES(?,?,?) ON CONFLICT(character_id,system) DO UPDATE SET value=excluded.value',(cid,system,raw))

def day():return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
def counter(value,key,limit,amount=1):
    stamp=day()
    if value.get(key+'_day')!=stamp:value[key+'_day']=stamp;value[key]=0
    if value[key]+amount>limit:raise ValueError('今日操作次数已达上限。')
    value[key]+=amount

def wire(mid,*values):
    fields=DATA['protocols'][str(mid)]['fields'];types=[]
    for f in fields:
        kind=f[1]
        if len(f)>2 and str(f[2]).isdigit():kind=('array',kind) if int(f[2])==0 else ('fixed_array',kind,int(f[2]))
        types.append(kind)
    return packet(mid,types,values)

def text(value):
    from native946_progression import lua_data
    if value is True:return 'true'
    if value is False:return 'false'
    if value is None:return 'nil'
    if isinstance(value,dict):return '{'+','.join('['+text(k)+']='+text(v) for k,v in value.items())+'}'
    if isinstance(value,(list,tuple)):return '{'+','.join(text(v) for v in value)+'}'
    return lua_data(value)
def notice(message):return text_notice(message)
def read(mid,r):
    def field(kind):
        if kind=='number':return r.integer()
        if kind=='string':
            raw=r.string()
            if len(raw)>24000:raise ValueError('玩法请求内容过长。')
            return raw.decode('gb18030')
        if kind in ('byte','word'):return int.from_bytes(r.take(1 if kind=='byte' else 2),'little')
        raise ValueError('玩法请求字段类型无效。')
    values=[]
    for f in DATA['protocols'][str(mid)]['fields']:
        if len(f)>2 and str(f[2]).isdigit():
            count=int(f[2]) or r.take(1)[0]
            if count>100:raise ValueError('玩法请求数量过多。')
            values.append([field(f[1]) for _ in range(count)])
        else:values.append(field(f[1]))
    r.finish();return values

def require(value,message):
    if not value:raise ValueError(message)
def number(value,lo,hi,label='参数'):
    require(type(value) is int and lo<=value<=hi,label+'无效。');return value
def character(db,cid):
    row=db.execute('SELECT * FROM characters WHERE id=?',(cid,)).fetchone()
    require(row is not None,'角色不存在。');return dict(row)
def grade(db,cid):
    from native946_training import state as training_state
    s=training_state(db,cid);return character(db,cid)['level']+1000*s['rebirth']+10000*s['ascension']+100000*s['sanctification']
def pay(db,cid,cash=0,exp=0,fund=0):
    require(all(type(n) is int and n>=0 for n in (cash,exp,fund)),'消耗数据无效。')
    c=character(db,cid)
    from native946_training import state as training_state
    # Native star/talent panels compare cash + AccumulationFund, rather than
    # requiring the entire Fund cost from that balance alone.
    from_fund=min(fund,training_state(db,cid)['tongbao']);from_cash=cash+fund-from_fund
    require(c['gold']>=from_cash and c['experience']>=exp,'现金、积累资金或角色经验不足。')
    db.execute('UPDATE characters SET gold=gold-?,experience=experience-? WHERE id=?',(from_cash,exp,cid))
    if from_fund:db.execute('UPDATE cultivation_state SET tongbao=tongbao-? WHERE character_id=?',(from_fund,cid))
def item_rows(db,cid,kind):
    from native946_mall_bags import pages
    opened=pages(db,cid)
    return [dict(r) for r in db.execute("SELECT i.* FROM inventory i LEFT JOIN native_object_locks l ON l.kind='item' AND l.object_id=i.id WHERE i.character_id=? AND i.type=? AND l.object_id IS NULL ORDER BY i.id",(cid,kind)) if r['page'] in opened]
def count(db,cid,kind):return sum(r['amount'] for r in item_rows(db,cid,kind))
def take(db,cid,kind,amount=1):
    from native946_inventory_features import consume
    require(type(amount) is int and amount>=1,'消耗数量无效。');rows=item_rows(db,cid,kind)
    require(sum(r['amount'] for r in rows)>=amount,'未加锁的'+DATA['items'].get(str(kind),{}).get('name','所需材料')+'不足。')
    for row in rows:
        used=min(row['amount'],amount);consume(db,cid,row['id'],used);amount-=used
        if amount==0:return
def give(db,p,kind,amount=1):
    from native946_inventory_features import give as give_item
    give_item(db,p,kind,amount)

def receive(game,mid,r):
    args=read(mid,r);group=next(k for k,v in GROUPS.items() if mid in v)
    if game.battle is not None and mid not in {5111,3180,3182,3184,4292,2549,3341,3342,3343,4431,878,879,865,860,864,5323,5326,4565,4566,4567,4568,4569,3083,3086,2860,673,672}:raise ValueError('请先结束战斗再培养或修改玩法。')
    from importlib import import_module
    module=import_module('native946_role_'+group)
    with game.progression.transaction() as db:
        result=module.receive(game,db,mid,args)
        if mid in (664,678):
            from native946_lineups import reset_invalid_selection
            team=db.execute('SELECT id FROM native_teams WHERE leader_id=?',(game.progression.cid,)).fetchone()
            if team:reset_invalid_selection(db,team['id'],game.progression.cid)
        after=game.progression._snapshot(db)
        db.execute('UPDATE progression SET hp=min(hp,?),mp=min(mp,?) WHERE character_id=?',(after['hpmax'],after['mpmax'],game.progression.cid))
    def reply():
        if mid in (875,876,880,664,678):
            from native946_lineups import notify_team
            notify_team(game)
        updates = (*(result or ()), *game.progression.sync(), *game.summons.sync())
        if mid == 2551:
            from native946_cosmetics import Cosmetics
            return (*updates, *Cosmetics(game).visual_packets())
        return updates
    return game.defer_packets(reply)

def use(game,iid,target=None):
    from native946_inventory_features import owned,consume
    from native946_role_arts import apply_card
    with game.progression.transaction() as db:
        item=owned(db,game.progression.cid,iid);kind=item['type'];cid=game.progression.cid
        if kind in CARD_ITEMS:result=apply_card(game,db,CARD_ITEMS[kind],target or cid,False);consume(db,cid,iid)
        elif kind==46988:
            s=state(db,cid,'goldbody',dict(ranks=[0]*10,exp=0,forge={},eff_time=0,colors={}))
            require(s['exp']<=99999999-20000,'凝气经验已满。');consume(db,cid,iid);s['exp']+=20000;save(db,cid,'goldbody',s)
            result=[notice('获得20000凝气经验。')]
        elif kind==46222:
            from native946_role_growth import reset_jing
            result=reset_jing(game,db,0);consume(db,cid,iid)
        elif kind in (47932,47933):
            from native946_role_arts import lingyou_quick
            result=lingyou_quick(game,db)
        elif kind in (45222,45223,46090):
            required,species={45222:(100,3208),45223:(2045,3209),46090:(2120,3275)}[kind]
            require(grade(db,cid)>=required,'尚未达到木甲图纸的原生使用等级。')
            sequence=state(db,cid,'wood_grants',dict(sequence=0));sequence['sequence']+=1
            game.summons.acquire(species,'tianji-wood:'+str(sequence['sequence']));consume(db,cid,iid);save(db,cid,'wood_grants',sequence);result=[notice('木甲守护已按原生种类加入携带栏。')]
        else:raise ValueError('此道具没有接入当前玩法。')
        after=game.progression._snapshot(db)
        if kind==46222:
            for equipped in after['items']:
                if equipped['page']==after['equipment_page']:game.progression.validate_equipment(after,equipped,equipped['slot'],equipped['page'])
        db.execute('UPDATE progression SET hp=min(hp,?),mp=min(mp,?) WHERE character_id=?',(after['hpmax'],after['mpmax'],cid))
    return game.defer_packets(lambda:(*result,*game.progression.sync(),*game.summons.sync()))

def effects(db,cid):
    from native946_role_growth import effects as growth_effects
    from native946_role_arts import effects as arts_effects
    from native946_role_society import effects as society_effects
    result={}
    for additions in (growth_effects(db,cid),arts_effects(db,cid),society_effects(db,cid)):
        for key,value in additions.items():result[key]=result.get(key,0)+int(value)
    return result

def tick(game):
    now=int(time())
    if game.battle is not None or now<getattr(game,'_role_expiry_at',0):return ()
    game._role_expiry_at=now+10;cid=game.progression.cid
    with game.progression.transaction(write=False) as db:
        ling=state(db,cid,'lingyou',dict(effects={}));body=state(db,cid,'goldbody',dict(eff_time=0))
        expired=[k for k,v in ling['effects'].items() if v['expires']<=now]
        gold_expired=0<body['eff_time']<=now
        returning=[r['id'] for r in db.execute('SELECT id,value FROM native_babies WHERE character_id=?',(cid,)) if (v:=json.loads(r['value'])).get('travel') and v['travel']['end']<=now]
        construction=db.execute('SELECT id FROM native_coc_buildings WHERE character_id=? AND ready>0 AND ready<=?',(cid,now)).fetchall()
    if not (expired or gold_expired or returning or construction):return ()
    messages=[]
    with game.progression.transaction() as db:
        if expired:
            for k in expired:ling['effects'].pop(k,None)
            save(db,cid,'lingyou',ling)
        if gold_expired:body['eff_time']=0;save(db,cid,'goldbody',body)
        from native946_role_society import baby,baby_travel_finish,finish_city,own_building,build_info
        for bid in returning:baby_travel_finish(db,cid,baby(db,cid,bid))
        if construction:
            finish_city(db,cid)
            messages.extend(wire(2835,r['id'],text(build_info(own_building(db,cid,r['id'])))) for r in construction)
        after=game.progression._snapshot(db);db.execute('UPDATE progression SET hp=min(hp,?),mp=min(mp,?) WHERE character_id=?',(after['hpmax'],after['mpmax'],cid))
        for row in db.execute('SELECT * FROM summons WHERE character_id=?',(cid,)).fetchall():
            pet=game.summons.stats(row);db.execute('UPDATE summons SET hp=min(hp,?),mp=min(mp,?) WHERE id=?',(pet['hpmax'],pet['mpmax'],row['id']))
    return (*messages,*game.progression.sync(),*game.summons.sync())

def settle(db,cid,event_id,*,won,source='battle'):
    """Called only by committed real settlement; unique receipts block replays."""
    if not won:return
    if not db.execute('INSERT OR IGNORE INTO native_role_system_events VALUES(?,?,?,?)',(cid,source,str(event_id),int(time()))).rowcount:return
    official=state(db,cid,'official',dict(kind=0,level=0,merit=0,prestige=0,skills={},bars={},attributes=[],task=None))
    if official.get('task'):
        official['task']['kills']=min(official['task']['need'],official['task']['kills']+1)
        save(db,cid,'official',official)
    s=state(db,cid,'daily_growth',{})
    try:counter(s,'battles',RULES['cultivation_daily_limit'])
    except ValueError:return
    save(db,cid,'daily_growth',s)
    g=state(db,cid,'goldbody',dict(ranks=[0]*10,exp=0,forge={},eff_time=0,colors={}))
    g['exp']=min(99999999,g['exp']+RULES['cultivation_daily_battle_exp']);save(db,cid,'goldbody',g)
    j=state(db,cid,'jing',dict(level=0,exp=0));j['exp']+=100;save(db,cid,'jing',j)
    talent=state(db,cid,'talent',dict(levels={},switches=0,zhenqi=0));talent['zhenqi']+=100;save(db,cid,'talent',talent)
