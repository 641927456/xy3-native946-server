"""946 new inner-pill protocol, native advancement costs and local growth."""
import json
from random import choice

from native946_codec import packet
from native946_training import DATA
REFERENCE_WIRE_VERSION=4

CONFIG=DATA['new_neidan']
META={int(key):value for key,value in DATA['neidan_meta'].items()}
PILL_TYPES={int(kind):pill_type
            for pill_type,group in enumerate(CONFIG['NewNeidanIds'],start=1)
            for kind in group}
ITEM_TYPES={entry['ItemType']:int(kind)
            for group in CONFIG['NewNeidanIds'] for kind,entry in group.items()}
ENERGY_ITEM_TYPE=6128
# Revision-946 neidan_panel help: old pills cost one point per battle;
# both new pill categories cost five. New-pill installation stays gated.
BATTLE_ENERGY_COST={0:1,1:5,2:5}
WRITES={753,755,765,766,767,5226,5227}
MAX_SUMMON_EXPERIENCE=9_000_000_000
ENERGY_PER_ITEM=1000
MAX_ENERGY=6000
# 946 encyclopedia 101608 ties rebirth thresholds to guardian levels.
LEVEL_CAPS = (100, 120, 140, 180)
STAT_EFFECTS = {
    236: ('mpmax','initiative'), 237: ('hpmax','initiative'),
    238: ('hpmax','attack'), 239: ('attack','initiative'), 240: ('hpmax','mpmax'),
    241: ('hpmax','initiative'), 242: ('mpmax','attack'),
    245: ('ReMpAbsorb',), 246: ('ReWenxin',), 247: ('anti_cruel',),
    248: ('EquipElementResist',), 249: ('role_medicine_percent',),
    250: ('role_cruel_multiplier',), 251: ('role_fatal_multiplier',),
    253: ('EquipElementDamage',), 276: ('role_physical_penetration',), 277: ('IgImmAbsorb',),
}
# Percentage columns use basis points. These are the published five-tier maxima.
ENDPOINTS = {
    236:(1080,90),237:(1800,90),238:(1800,180),239:(180,90),240:(1800,1080),
    241:(2700,180),242:(1080,180),243:(3000,),244:(3000,),245:(3000,),246:(3000,),
    247:(3000,),248:(900,),249:(3000,),250:(600,),251:(600,),252:(3000,),253:(300,),
    254:(6000,),255:(1500,),276:(300,),277:(600,),278:(6000,),279:(3000,),
    280:(1500,),281:(3000,),282:(1200,),283:(3000,),284:(1500,),285:(3000,),
}
POINT_VALUES={236,237,238,239,240,241,242,248,253}

# Published classic 3.0.919 endpoints. Names/attributes use the 946 skill IDs.
# These are tooltip reference values for an unresonated pill, not its current effect.
MAX_LEVEL_PREVIEWS = {
    236: '法力+1080，速度+90',
    237: '气血+1800，速度+90',
    238: '气血+1800，攻击+180',
    239: '攻击+180，速度+90',
    240: '气血+1800，法力+1080',
    241: '气血+2700，速度−180',
    242: '法力+1080，攻击+180',
    243: '抗隔山程度+30%',
    244: '抗瞬杀+30%',
    245: '抗法力震慑+30%',
    246: '抗问心+30%',
    247: '抗狂暴+30%',
    248: '仙法抗性+900',
    249: '药品效果+30%',
    250: '狂暴伤害+6%',
    251: '致命伤害+6%',
    252: '仙法附伤为灵性点数的30%',
    253: '仙法伤害+300',
    254: '物理附伤按力量点数的60%预览（946说明及阶数反推）',
    255: '附法触发率+15%',
    276: '物理穿透+3%',
    277: '仙法穿透+6%',
    278: '血海毒花或寒烟羁水有60%概率多作用一个目标',
    279: '大杀四方和出奇制胜触发率+30%',
    280: '受到的物理狂暴和火法狂暴伤害−15%',
    281: '对带盾单位伤害+30%',
    282: '按实际法术伤害的12%回血',
    283: '隐身期间的物理伤害+30%（对照公告潜形匿迹）',
    284: '治疗效果+15%',
    285: '触发吸收时该次所受伤害−30%',
}


def battle_energy_cost(nei_type):
    if type(nei_type) is not int or nei_type not in BATTLE_ENERGY_COST:
        raise ValueError('内丹类别无效。')
    return BATTLE_ENERGY_COST[nei_type]


def slot_limits(rebirth,ascension):
    if type(rebirth) is not int or not 0<=rebirth<=2 or type(ascension) is not int or not 0<=ascension<=2:
        raise ValueError('守护阶段无效。')
    return (1+rebirth,int(ascension>0))


def advance_cost(kind,advance):
    if kind not in META or type(advance) is not int or not 1<=advance<=4:
        raise ValueError('内丹类型或进阶等级无效，五阶内丹已满。')
    cost=CONFIG['NewNeidanLevelConfig'][advance-1]['NeidanAdvanceCost']
    double_exp=PILL_TYPES[kind]==CONFIG['BaseInfo']['AdvanceExpDoubleCostType']
    return (cost['NeedSummonExp']*(2 if double_exp else 1),cost['NeedSameAmount'])


def split_return(advance):
    if type(advance) is not int or not 1<=advance<=5:raise ValueError('内丹进阶等级无效。')
    return CONFIG['NewNeidanLevelConfig'][advance-1]['NeidanSplitReturn']


def effect_coefficient(kind,advance,installed_low=(),resonances=()):
    if kind not in META or type(advance) is not int or not 1<=advance<=5:raise ValueError('内丹进阶等级无效。')
    coefficient=CONFIG['NewNeidanLevelConfig'][advance-1]['LuckyBoundary']
    if PILL_TYPES[kind]==1 and kind in installed_low and kind in resonances:
        coefficient+=CONFIG['BaseInfo']['ResonanceConstant']
    return coefficient


def resonance_slots(kind,advance):
    if kind not in META or type(advance) is not int or not 1<=advance<=5:raise ValueError('内丹进阶等级无效。')
    if PILL_TYPES[kind]!=2:return 0
    return CONFIG['NewNeidanLevelConfig'][advance-1].get('NeidanResonanceUnlockQuality',0)


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_new_neidan (
        summon_id INTEGER NOT NULL REFERENCES summons(id) ON DELETE CASCADE,
        kind INTEGER NOT NULL, level INTEGER NOT NULL DEFAULT 1,
        stage INTEGER NOT NULL DEFAULT 0, experience INTEGER NOT NULL DEFAULT 0,
        energy INTEGER NOT NULL DEFAULT 6000, advance INTEGER NOT NULL DEFAULT 1,
        resonance TEXT NOT NULL DEFAULT '{}', PRIMARY KEY (summon_id,kind))''')


def installed(db,sid):
    return [dict(row) for row in db.execute('SELECT * FROM native_new_neidan WHERE summon_id=? ORDER BY kind',(sid,))]


def threshold(level,stage):
    # Local training extends the existing cubic legacy cost through ascension.
    return (level**3+50)*(106+min(stage,2)*24)*7//1000


def _over_cap_rows(db, cid):
    tables = (
        ('legacy_neidan', 'rebirth', 'summons', 'summon_id', ('summon_id', 'kind')),
        ('legacy_neidan_items', 'rebirth', 'inventory', 'item_id', ('item_id',)),
        ('native_new_neidan', 'stage', 'summons', 'summon_id', ('summon_id', 'kind')),
    )
    present = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('legacy_neidan','legacy_neidan_items','native_new_neidan','inventory','summons')")}
    for table, stage, owner_table, owner_key, keys in tables:
        # Progression can create pill tables before guardian storage is initialized.
        if table not in present or owner_table not in present:
            continue
        rows = db.execute(f'SELECT n.* FROM {table} n JOIN {owner_table} o ON o.id=n.{owner_key} '
                          f'WHERE o.character_id=? AND n.{stage} BETWEEN 0 AND 2 '
                          f'AND n.level>CASE n.{stage} WHEN 0 THEN ? WHEN 1 THEN ? ELSE ? END',
                          (cid, *LEVEL_CAPS[:3])).fetchall()
        for row in rows:
            yield table, stage, keys, row


def reconcile_level_caps(progression):
    """Return the retired levels' XP to the pill on first login or reload."""
    with progression.transaction(write=False) as db:
        if not any(_over_cap_rows(db, progression.cid)):
            return ()

    from native946_neidan_legacy import threshold as legacy_threshold

    guardians = set()
    with progression.transaction() as db:
        for table, stage_key, keys, row in _over_cap_rows(db, progression.cid):
            stage = row[stage_key]
            cap = LEVEL_CAPS[stage]
            current_threshold = legacy_threshold if stage_key == 'rebirth' else threshold
            refund = sum(current_threshold(level, stage) for level in range(cap, min(row['level'], cap + 2)))
            condition = ' AND '.join(f'{key}=?' for key in keys)
            values = tuple(row[key] for key in keys)
            if stage_key == 'rebirth':
                db.execute(f'UPDATE {table} SET level=?,experience=experience+?,expnext=? WHERE {condition}',
                           (cap, refund, current_threshold(cap, stage), *values))
            else:
                db.execute(f'UPDATE {table} SET level=?,experience=experience+? WHERE {condition}',
                           (cap, refund, *values))
            if 'summon_id' in keys:
                guardians.add(row['summon_id'])
    return tuple(sorted(guardians))


def grow(row,experience):
    cap=LEVEL_CAPS[row['stage']]
    if row['level']>=cap:
        return dict(row)
    row=dict(row,experience=row['experience']+experience)
    while row['level']<cap and row['experience']>=threshold(row['level'],row['stage']):
        row['experience']-=threshold(row['level'],row['stage'])
        row['level']+=1
    if row['level']==cap:
        row['experience']=0
    return row


def amounts(row,resonant=False):
    kind,stage,level=row['kind'],row['stage'],row['level']
    # Local progression is continuous at rebirth, with 168/180 giving 95%.
    starts=(500,1200,1800,2500)
    ends=(1200,1800,2500,10000)
    growth=starts[stage]+(ends[stage]-starts[stage])*level//LEVEL_CAPS[stage]
    coefficient=int(effect_coefficient(kind,row['advance'])*2)
    base=[value*growth*coefficient//60000 for value in ENDPOINTS[kind]]
    extra=[value*growth//30000 if resonant else 0 for value in ENDPOINTS[kind]]
    return base,extra


def resonance_targets(rows):
    return {kind for row in rows if PILL_TYPES[row['kind']]==2 and row['energy']>0
            for slot,kind in json.loads(row['resonance']).items()
            if slot.isdecimal() and int(slot)<=resonance_slots(row['kind'],row['advance'])}


def description(row,resonant=False):
    base,extra=amounts(row,resonant)
    args=[n for pair in zip(base,extra) for n in pair] if PILL_TYPES[row['kind']]==1 else base
    if row['kind'] not in POINT_VALUES:
        args=[f'{value/100:.2f}' for value in args]
    return '{'+','.join(map(str,args))+'}'


def records(rows):
    targets=resonance_targets(rows)
    return [dict(neidan_id=row['kind'],name=META[row['kind']]['name'],nei_type=PILL_TYPES[row['kind']],
                 grade=row['level'],rei=min(row['stage'],2),fly=int(row['stage']==3),
                 neiexp=row['experience'],neiexp_next=threshold(row['level'],row['stage']),
                 last=row['energy'],last_max=MAX_ENERGY,nei_advance=row['advance'],
                 is_resonance=int(row['kind'] in targets),desc=description(row,row['kind'] in targets),
                 resonance_data=resonance_wire(row)) for row in rows]


def resonance_wire(row):
    data=json.loads(row['resonance'])
    return '{'+','.join(f'[{int(slot)}]={kind}' for slot,kind in sorted(data.items()) if slot.isdecimal())+'}'


def attributes(pet,rows):
    targets=resonance_targets(rows)
    powers={}
    for row in rows:
        if row['energy']<=0:
            continue
        kind=row['kind']
        base,extra=amounts(row,kind in targets)
        values=[a+b for a,b in zip(base,extra)]
        powers[kind]=values[0]
        divisor=100 if kind==247 else 1 if kind in POINT_VALUES else 10
        for field,value in zip(STAT_EFFECTS.get(kind,()),values):
            if kind==241 and field=='initiative':value=-value
            pet[field]=pet.get(field,0)+value//divisor
    pet['new_neidan_power']=powers


class NewNeidan:
    def __init__(self,summons):
        self.s=summons
        self.p=summons.progression

    def owned_bag(self,db,kind):
        from native946_mall_bags import pages
        from native946_inventory_locks import records as locks
        positions=set(pages(db,self.p.cid))
        locked=locks(db,self.p.cid,'item')
        return [dict(item) for item in db.execute('SELECT * FROM inventory WHERE character_id=? AND type=? ORDER BY id',
                                                  (self.p.cid,kind))
                if item['page'] in positions and not locked.get(item['id'],{}).get('lock')]

    def spend(self,db,kind,count):
        from native946_mall_uses import consume
        items=self.owned_bag(db,kind)
        if sum(item['amount'] for item in items)<count:raise ValueError('背包中的材料数量不足。')
        for item in items:
            amount=min(item['amount'],count)
            consume(db,item,amount)
            count-=amount
            if not count:break

    def use(self,db,pet,item):
        from native946_mall_uses import consume
        sid=pet['id']
        if item['type']==ENERGY_ITEM_TYPE:
            changed=db.execute('UPDATE native_new_neidan SET energy=min(?,energy+?) WHERE summon_id=? AND energy<?',
                               (MAX_ENERGY,ENERGY_PER_ITEM,sid,MAX_ENERGY)).rowcount
            if not changed:raise ValueError('暂不可补充内丹元气。请确认守护已安装新内丹且元气未满。')
        else:
            kind=ITEM_TYPES[item['type']]
            rows=installed(db,sid)
            pill_type=PILL_TYPES[kind]
            if any(row['kind']==kind for row in rows):raise ValueError('守护已安装同名内丹。')
            if pill_type==1:
                from native946_neidan_legacy import raw_list
                used=len(raw_list(db,sid))+sum(PILL_TYPES[row['kind']]==1 for row in rows)
            else:
                used=sum(PILL_TYPES[row['kind']]==2 for row in rows)
            if used>=slot_limits(min(2,pet['rebirth']),pet['ascension'])[pill_type-1]:
                raise ValueError(f'当前守护没有可用的「{META[kind]["name"]}」内丹槽位。')
            db.execute('INSERT INTO native_new_neidan(summon_id,kind) VALUES(?,?)',(sid,kind))
        consume(db,dict(item))

    def save(self,db,row):
        db.execute('UPDATE native_new_neidan SET level=?,stage=?,experience=?,energy=?,advance=?,resonance=? WHERE summon_id=? AND kind=?',
                   tuple(row[k] for k in ('level','stage','experience','energy','advance','resonance','summon_id','kind')))

    def settle(self,db,sid,victory,experience):
        for row in installed(db,sid):
            row['energy']=max(0,row['energy']-battle_energy_cost(PILL_TYPES[row['kind']]))
            self.save(db,grow(row,experience) if victory else row)

    def receive(self,game,mid,sid,kind,options):
        reads=(5228,5229)
        with self.p.transaction(write=mid not in reads) as db:
            pet=self.s._owned(db,sid)
            row=next((row for row in installed(db,sid) if row['kind']==kind),None)
            if row is None:raise ValueError(f'守护未安装「{META[kind]["name"]}」。')
            if mid in reads:
                return self.panel(mid,pet,row,installed(db,sid))
            if game.battle:raise ValueError('战斗中不能培养内丹。')
            cost=0
            if mid==753:
                if row['level']>=LEVEL_CAPS[row['stage']]:raise ValueError('内丹已达到当前阶段上限。')
                cost=pet['experience']//5
                if cost<500:raise ValueError('守护至少需要2500点结余经验才能转换。')
                row=grow(row,cost)
            elif mid==755:
                page,slot=self.p.free_bag_slot(self.p._snapshot(db))
                db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',
                           (self.p.cid,META[kind]['item_type'],page,slot,split_return(row['advance'])))
                db.execute('DELETE FROM native_new_neidan WHERE summon_id=? AND kind=?',(sid,kind))
            elif mid in (765,766,767):
                stage=row['stage']
                if mid==765:raise ValueError('本机新内丹的培养终点为飞升180级。')
                if row['level']<LEVEL_CAPS[stage] or (mid==767 and stage>=2) or (mid==766 and stage!=2):
                    raise ValueError('内丹未达到当前阶段上限，或阶段不符。')
                if (mid==767 and pet['rebirth']<=stage) or (mid==766 and not pet['ascension']):
                    raise ValueError('请先提升守护阶段。')
                row.update(stage=stage+1,level=1,experience=0)
            elif mid==5227:
                cost,count=advance_cost(kind,row['advance'])
                if pet['experience']<cost:raise ValueError('守护结余经验不足。')
                self.spend(db,META[kind]['item_type'],count)
                row['advance']+=1
            elif mid==5226:
                slot,operation=options
                if not 1<=slot<=resonance_slots(kind,row['advance']) or operation not in (0,1,2):
                    raise ValueError('请选择已开放的内丹共鸣位置。')
                data=json.loads(row['resonance'])
                pending=data.get('pending')
                if operation==0:
                    if pending:raise ValueError('请先确认或放弃上一条共鸣。')
                    cost=CONFIG['BaseInfo']['ResonanceConsumeExp']
                    if pet['experience']<cost:raise ValueError('守护结余经验不足。')
                    self.spend(db,META[kind]['item_type'],CONFIG['BaseInfo']['ResonanceConsumeAmount'])
                    data['pending']=[slot,choice([k for k in range(236,256) if k not in data.values()])]
                else:
                    if not pending or pending[0]!=slot:raise ValueError('该位置没有等待处理的共鸣。')
                    if operation==1:data[str(slot)]=pending[1]
                    del data['pending']
                row['resonance']=json.dumps(data)
            if cost:
                db.execute('UPDATE summons SET experience=experience-? WHERE id=?',(cost,sid))
            if mid!=755:self.save(db,row)
        def render():
            replies=(*self.p.item_packets(),self.s.neidan_packet(sid),*self.s.info_packets(sid))
            if mid in (5226,5227):
                with self.p.transaction(write=False) as db:
                    panel=self.panel(5229 if mid==5226 else 5228,self.s._owned(db,sid),row,installed(db,sid))
                return (*replies,panel)
            return replies
        return game.defer_packets(render)

    def panel(self,mid,pet,row,rows):
        kind=row['kind']
        if mid==5229:
            if PILL_TYPES[kind]!=2:raise ValueError('只有高级内丹能洗练共鸣。')
            data=json.loads(row['resonance']);active={r['kind'] for r in rows if r['energy']>0}
            slots=[]
            for i in range(1,4):
                fitted=data.get(str(i))
                category=3 if fitted in active else 2 if i<=resonance_slots(kind,row['advance']) else 1
                name=json.dumps(META[fitted]['name'] if fitted else '',ensure_ascii=False)
                slots.append(f'[{i}]={{id={i},type={category},name={name}}}')
            value='{slots={'+','.join(slots)+'}'
            if pending:=data.get('pending'):
                name=json.dumps(META[pending[1]]['name'],ensure_ascii=False)
                value+=f',pending={{slot_idx={pending[0]},new_name={name}}}'
            return packet(5225,['number']*4+['string'],[pet['id'],pet['experience'],kind,PILL_TYPES[kind],value+'}'])
        advance=row['advance']
        nxt=min(advance+1,5)
        resonant=kind in resonance_targets(rows)
        return packet(5224,['number']*6+['string']*2,
                      [pet['id'],kind,advance,nxt,PILL_TYPES[kind],pet['experience'],
                       description(row,resonant),description(dict(row,advance=nxt),resonant)])
