"""Owned equipment and appearance refinement through their 946 request paths."""

import json
from pathlib import Path
from secrets import choice, randbelow
from time import time

from native946_codec import packet, text_notice


REFERENCE_WIRE_VERSION=1
DATA=json.loads(Path(__file__).with_name('native946_refinement_data.json').read_text('utf8'))
DING=46782
FAIRY_DUST=46517
DAY=86400
GEAR_NPCS={(2039,4190):'shenbing',(2039,1141):'xianqi',(2436,7629):'jing'}
GEAR_RULES={
    'shenbing':(2761,2762,46545,'info_shenbing_lianhua_info'),
    'xianqi':(2764,2765,46544,'info_xianqi_lianhua_info'),
    'jing':(3422,3423,47223,'info_jing_luling_equip_info'),
}
GEAR_OPERATIONS={2767:('shenbing',False),2768:('shenbing',True),2770:('xianqi',False),
                 2771:('xianqi',True),3424:('jing',False),3425:('jing',True)}
DISASSEMBLY={2766:'shenbing',2769:'xianqi'}
REQUESTS={2981,*GEAR_OPERATIONS,*DISASSEMBLY}
WRITES=REQUESTS
FORGE_VIGOR_MAX=3000
FAIRY_UPGRADE_CASH=30000
FAIRY_UPGRADE_ESSENCE=3
SUPPLY_CASH=10000
DIRECT={'DudsAttAll':'attack','DudsHpMax':'hpmax','DudsMpMax':'mpmax',
        'DudsDodAll':'initiative','DudsDefAll':'defense',
        'Cor':'cor','Mag':'mag','Str':'strength','Spe':'speed'}
BATTLE={'Duds'+name:name for name in (
    'ReFire','ReWater','ReWind','ReThunder','ReDisorder','ReFaint','ReSeal','RePoison','ReGushu',
    'ReHaoran','ReGeshan','ImmAbsorbDegree','PhyAbsorbDegree','AddThunder','AddWind','AddFire','AddWater',
    'IgThunder','IgWind','IgFire','IgWater',
)}
LABELS={name:row['AttrName'] for groups in DATA['ForgeTraitTbl'].values() for group in groups
        for section in group for name,row in section.items()}
REFERENCE_KEYS={1:'Cor',2:'Mag',3:'Str',4:'Spe',6:'DudsHpMax',8:'DudsMpMax',
                9:'DudsAttAll',10:'DudsDodAll',11:'DudsDefAll',20:'DudsReFaint',
                21:'DudsReDisorder',22:'DudsRePoison',23:'DudsReSeal',24:'DudsReFire',
                25:'DudsReWind',26:'DudsReWater',27:'DudsReThunder'}
for _pool in DATA['reference_traits'].values():
    for _key,_label,*_range in _pool:LABELS.setdefault(REFERENCE_KEYS[_key],_label)
REFERENCE_SLOTS={'xianqi':{1:6,2:7,3:8,4:9,5:10}}
ORDINARY_SLOTS={1:3,2:4,3:2,4:1,5:5}


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_refinement (
        item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id),
        traits TEXT NOT NULL DEFAULT '[]', expires INTEGER NOT NULL DEFAULT 0,
        lucky INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
        pending TEXT NOT NULL DEFAULT '{}'
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_refinement_vigor (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id),
        current INTEGER NOT NULL, updated INTEGER NOT NULL
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS native_refinement_owner ON native_refinement(character_id)')


def item_extras(db,cid,iid):
    value=state(db,cid,iid)
    rank=forge_rank(value)
    pending=value['pending']
    return dict(ForgeTimes=value['attempts'],ForgeLevel=rank, ForgeLucky=value['lucky'],
                ForgeTraitInfo=describe(value) if value['traits'] else '',
                ForgeLuckySaveBound=DATA['jing_lucky'].get(str(rank),0),
                LastForgeSaveTraitCache=describe(dict(traits=pending['traits'],expires=0))
                    if pending.get('gear') else '')


def forge_rank(value):
    return max((row[2] for row in value['traits']),default=0)


def vigor(db,cid,now):
    row=db.execute('SELECT current,updated FROM native_refinement_vigor WHERE character_id=?',(cid,)).fetchone()
    return min(FORGE_VIGOR_MAX,row[0]+max(0,now-row[1])//60) if row else FORGE_VIGOR_MAX


def state(db,cid,iid):
    row=db.execute('SELECT * FROM native_refinement WHERE character_id=? AND item_id=?',(cid,iid)).fetchone()
    if row is None:return dict(traits=[],expires=0,lucky=0,attempts=0,pending={})
    return dict(traits=json.loads(row['traits']),expires=row['expires'],lucky=row['lucky'],
                attempts=row['attempts'],pending=json.loads(row['pending']))


def save(db,cid,iid,value):
    db.execute('INSERT INTO native_refinement VALUES(?,?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET '
               'traits=excluded.traits,expires=excluded.expires,lucky=excluded.lucky,attempts=excluded.attempts,pending=excluded.pending',
               (iid,cid,json.dumps(value['traits']),value['expires'],value['lucky'],value['attempts'],json.dumps(value['pending'])))


def describe(value, now=None):
    if not value['traits']:return '尚未炼化'
    parts=[f"{LABELS.get(key,key)} +{amount:g}" for key,amount,*_ in value['traits']]
    if value['expires']:
        seconds=max(0,value['expires']-(int(time()) if now is None else now))
        parts.append(f'炼化有效天数：{(seconds+DAY-1)//DAY}' if seconds else '炼化已到期')
    return '#r'.join(parts)


def bonuses(db,cid,items,equipment_page):
    direct={};battle={};deadline=0
    now=int(time())
    worn={r['id'] for r in items if r['page'] in (29,equipment_page)}
    if not worn:return direct,battle,deadline
    worn.difference_update(r[0] for r in db.execute(
        'SELECT item_id FROM native_equipment WHERE character_id=? AND durability=0',(cid,)))
    for r in db.execute('SELECT item_id,traits,expires FROM native_refinement WHERE character_id=? '
                        'AND (expires=0 OR expires>?)',(cid,now)):
        if r['item_id'] not in worn:continue
        if r['expires']:deadline=min(deadline,r['expires']) if deadline else r['expires']
        for key,value,*_ in json.loads(r['traits']):
            if key in DIRECT:
                attr=DIRECT[key];direct[attr]=direct.get(attr,0)+round(value)
            elif key in BATTLE:
                attr=BATTLE[key]
                units=1 if attr in ('ReHaoran','ReGeshan') else 10
                battle[attr]=battle.get(attr,0)+round(value*units)
    strength=sum(r['level'] for r in db.execute(
        'SELECT item_id,level FROM native_fashion_strength WHERE character_id=?',(cid,)) if r['item_id'] in worn)
    if strength:
        direct['hpmax']=direct.get('hpmax',0)+strength*10
        direct['defense']=direct.get('defense',0)+strength*2
    return direct,battle,deadline


def debit(db,cid,amount):
    if amount and not db.execute('UPDATE characters SET gold=gold-? WHERE id=? AND gold>=?',
                                 (amount,cid,amount)).rowcount:
        raise ValueError('随身金钱不足。')


class Refinement:
    def __init__(self,game):
        self.game=game
        self.p=game.progression

    def item(self,db,iid,*,appearance=False,equipped=False,worn_cosmetic=False):
        from native946_mall_bags import pages
        from native946_inventory_locks import require_unlocked
        from native946_cosmetics import PAGES, WEAR_PAGE
        row=db.execute('SELECT i.*,coalesce(e.expires,0) expires FROM inventory i '
                       'LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.character_id=? AND i.id=?',
                       (self.p.cid,iid)).fetchone()
        allowed=(*pages(db,self.p.cid),*PAGES) if appearance else pages(db,self.p.cid)
        if worn_cosmetic:allowed=(*allowed,WEAR_PAGE)
        if equipped:
            from native946_inventory import EQUIP_PAGES
            guardian_pages=[r[0] for r in db.execute('SELECT DISTINCT i.page FROM inventory i JOIN native_guardian_equipment e ON e.item_id=i.id '
                                                     'WHERE i.character_id=?',(self.p.cid,))]
            allowed=(*allowed,*EQUIP_PAGES,*guardian_pages)
        if row is None or row['page'] not in allowed or row['expires'] and row['expires']<=time():
            raise ValueError('装备或材料不在有效背包或异宝匣中。')
        require_unlocked(db,self.p.cid,iid)
        return row

    def take(self,db,kind,count,*,check=False):
        from native946_mall_checkout import rows
        from native946_inventory_locks import require_unlocked
        from native946_mall_uses import consume
        for row in rows(db,self.p.cid,(kind,)):
            try:require_unlocked(db,self.p.cid,row['id'])
            except ValueError:continue
            amount=min(count,row['amount'])
            if not check:consume(db,row,amount)
            count-=amount
            if not count:return
        from native946_progression import DATA as ITEMS
        name=ITEMS['items'].get(str(kind),{}).get('name',str(kind))
        raise ValueError(f'有效且未锁定的{name}不足。')

    def open_halo(self):
        self.game.halo_refine_until=time()+600
        return packet(2980,['number'],[1])

    def vigor_packets(self):
        from native946_progression import DATA as PROGRESSION
        with self.p.transaction(write=False) as db:current=vigor(db,self.p.cid,int(time()))
        attrs=PROGRESSION['attributes']
        return (packet(695,['number','number'],[int(attrs['VimMax']),FORGE_VIGOR_MAX]),
                packet(695,['number','number'],[int(attrs['Vim']),current]),packet(694,[],[]))

    def open_gear(self,kind,operation):
        opening=GEAR_RULES[kind][0]
        if operation=='disassemble':
            opening={'shenbing':2760,'xianqi':2763}[kind]
        self.game.gear_refine_quote=None
        return (packet(1183,['string'],['dlg_npc_chat']),*self.vigor_packets(),packet(opening,[],[]))

    def gear_session(self,kind):
        from native946_npc_services import NpcServices
        session=NpcServices(self.game)._session('gear_refinement')
        if GEAR_NPCS.get((session['scene'],session['npc']))!=kind:
            raise ValueError('请前往对应的精炼师。')

    def supplies(self,kind):
        from native946_cosmetics import Cosmetics
        with self.p.transaction() as db:
            debit(db,self.p.cid,SUPPLY_CASH)
            for item,amount in ((GEAR_RULES[kind][2],1),(46546,1),(6046,10)):
                Cosmetics(self.game)._add(db,item,amount,backpack=True)
        return self.game.defer_packets(lambda:(*self.p.item_packets(),*self.p.attribute_packets(),
                                                text_notice('已购买一份本机装备炼化材料。')))

    def fairy_upgrade_menu(self,nid,page):
        from native946_equipment import records
        from native946_mall_bags import pages
        from native946_npc_services import NpcServices
        from native946_progression import DATA as ITEMS
        cid=self.p.cid
        choices=[]
        with self.p.transaction(write=False) as db:
            available=pages(db,cid)
            equipment=records(db,cid)
            for row in db.execute('SELECT * FROM inventory WHERE character_id=? ORDER BY page,slot',(cid,)):
                profile=DATA['fairy_upgrades'].get(str(row['type']))
                instance=equipment.get(row['id'])
                if not profile or not instance or instance['grade']!=1 or row['page'] not in available:continue
                place=f"背包{available.index(row['page'])+1}第{row['slot']}格"
                name=ITEMS['items'][str(row['type'])]['name']
                choices.append((f"将{name}升为三阶（{place}）",dict(kind='gear_refinement',op='fairy_upgrade',item=row['id'])))
        if not choices:raise ValueError('随身背包中没有与三阶原生档案匹配的一阶仙器。')
        visible=choices[page*8:(page+1)*8]
        for label,index in (('上一页',page-1),('下一页',page+1)):
            if 0<=index and index*8<len(choices):
                visible.append((label,dict(kind='gear_refinement',op='fairy_list',page=index)))
        return NpcServices(self.game)._menu(nid,f'本机仙器升阶需要仙器精魂{FAIRY_UPGRADE_ESSENCE}枚及现金{FAIRY_UPGRADE_CASH}，点击装备后立即办理。',visible)

    def upgrade_fairy(self,iid):
        from native946_equipment import persist, records
        cid=self.p.cid
        with self.p.transaction() as db:
            item=self.item(db,iid)
            profile=DATA['fairy_upgrades'].get(str(item['type']))
            old=records(db,cid).get(iid)
            if not profile or not old or old['grade']!=1 or item['amount']!=1:
                raise ValueError('仅可升阶有匹配三阶档案的一阶仙器。')
            current=state(db,cid,iid)
            if current['traits'] or current['pending'] or db.execute(
                'SELECT 1 FROM native_equipment_attribute_sets WHERE item_id=?',(iid,)).fetchone():
                raise ValueError('带有炼化或备用属性的仙器暂不能升阶。')
            self.take(db,46544,FAIRY_UPGRADE_ESSENCE)
            debit(db,cid,FAIRY_UPGRADE_CASH)
            db.execute('UPDATE inventory SET type=? WHERE character_id=? AND id=?',(profile['id'],cid,iid))
            db.execute('DELETE FROM native_equipment WHERE character_id=? AND item_id=?',(cid,iid))
            persist(db,cid,iid,dict(profile,grade=3,need={},kind='仙器'),profile['properties'])
            db.execute('UPDATE native_equipment SET durability=min(?,max_durability) WHERE item_id=?',
                       (old['durability'],iid))
        return self.game.defer_packets(lambda:(*self.p.item_packets(),*self.p.attribute_packets(),
                                               self.p.item_description(iid),text_notice('本机仙器升阶完成。')))

    def gear_item(self,db,iid,kind):
        from native946_equipment import records
        row=self.item(db,iid,equipped=True)
        instance=records(db,self.p.cid).get(iid)
        if (instance is None or row['amount']!=1 or
            not self.gear_allowed(row['type'],instance,kind)):
            raise ValueError('请选择原生炼化表支持且具有实际属性的装备。')
        return instance

    @staticmethod
    def gear_allowed(kind_id,instance,kind):
        allowed=DATA[GEAR_RULES[kind][3]]
        supported=str(kind_id) in allowed if isinstance(allowed,dict) else kind_id in allowed
        grade=instance['grade']
        if kind=='xianqi':return supported and 3<=grade<=5
        if kind=='shenbing':return supported and (grade in (4,5) or instance['requirements'].get('kind')=='圣武')
        return supported and grade>0

    def roll_gear(self,kind,slot,rank=1):
        slot_map=REFERENCE_SLOTS.get(kind,ORDINARY_SLOTS)
        pool=DATA['reference_traits'][str(slot_map.get(slot,2))]
        draws=[]
        available=list(pool)
        for _ in range(min(3,len(pool))):
            row=choice(available);available.remove(row)
            key,_,low,high=row
            if kind=='jing':
                floor=low+((high-low)*(rank-1))//11
                draws.append([REFERENCE_KEYS[key],floor,rank])
            else:
                draws.append([REFERENCE_KEYS[key],low+randbelow(high-low+1),rank])
        return draws

    def gear_effects(self,kind,iid,mode,check):
        self.gear_session(kind)
        if mode not in (1,2) or check not in (0,1):raise ValueError('装备炼化模式无效。')
        now=int(time())
        cid=self.p.cid
        claim=(kind,iid,mode)
        quote=getattr(self.game,'gear_refine_quote',None)
        if not check and (not quote or quote[0]!=claim or quote[1]<=now):
            raise ValueError('请先核对装备和材料，再确认炼化。')
        with self.p.transaction(write=not check) as db:
            instance=self.gear_item(db,iid,kind)
            current=state(db,cid,iid)
            if current['pending']:raise ValueError('请先选择上一次炼化的属性。')
            available=vigor(db,cid,now)
            if available<mode*1000:raise ValueError('炼化活力不足。')
            self.take(db,GEAR_RULES[kind][2],mode,check=bool(check))
            self.take(db,46546,mode,check=bool(check))
            self.take(db,6046,mode*10,check=bool(check))
            if check:
                if db.execute('SELECT gold FROM characters WHERE id=?',(cid,)).fetchone()[0]<mode*10000:
                    raise ValueError('随身金钱不足。')
                self.game.gear_refine_quote=(claim,now+120)
                return (*self.vigor_packets(),packet(5023,[],[]))
            debit(db,cid,mode*10000)
            db.execute('INSERT INTO native_refinement_vigor VALUES(?,?,?) ON CONFLICT(character_id) '
                       'DO UPDATE SET current=excluded.current,updated=excluded.updated',(cid,available-mode*1000,now))
            before=describe(current)
            rank=forge_rank(current)
            threshold=DATA['jing_lucky'].get(str(rank),0)
            lucky=kind=='jing' and mode==2 and 0<rank<12 and current['lucky']>=threshold
            next_rank=rank+1 if lucky else 1+randbelow(min(12,rank+2)) if kind=='jing' else 1
            next_traits=self.roll_gear(kind,instance['slot'],next_rank)
            after=describe(dict(traits=next_traits,expires=0))
            current['attempts']+=1
            if mode==1:
                current.update(traits=next_traits,lucky=0)
            else:
                if kind=='jing':
                    current['lucky']=0 if lucky else min(threshold,current['lucky']+1)
                current['pending']=dict(gear=kind,traits=next_traits)
            save(db,cid,iid,current)
        self.game.gear_refine_quote=None
        result=packet(GEAR_RULES[kind][1],['number','byte','string','string']+(['number'] if kind=='jing' else []),
                      [iid,mode,before,after]+([int(lucky)] if kind=='jing' else []))
        return self.game.defer_packets(lambda:(*self.p.item_packets(),*self.p.attribute_packets(),*self.vigor_packets(),
            packet(3757,['number'],[iid]),result))

    def choose_gear(self,kind,iid,option):
        self.gear_session(kind)
        if option not in (1,2):raise ValueError('请从原属性和新属性中选择一项。')
        with self.p.transaction() as db:
            self.gear_item(db,iid,kind)
            current=state(db,self.p.cid,iid)
            pending=current['pending']
            if pending.get('gear')!=kind:raise ValueError('没有待确认的炼化属性。')
            if option==2:current['traits']=pending['traits']
            current['pending']={}
            save(db,self.p.cid,iid,current)
        return self.game.defer_packets(lambda:(*self.p.attribute_packets(),*self.vigor_packets(),
            packet(3757,['number'],[iid]),self.p.item_description(iid),text_notice('炼化属性已保存。')))

    def disassemble(self,kind,ids):
        from native946_equipment import records
        from native946_mall_uses import consume
        from native946_cosmetics import Cosmetics
        self.gear_session(kind)
        if not 1<=len(ids)<=24 or len(set(ids))!=len(ids):raise ValueError('分解物品列表无效。')
        cid=self.p.cid
        with self.p.transaction() as db:
            equipment=records(db,cid)
            amount=0
            for iid in ids:
                row=self.item(db,iid)
                instance=equipment.get(iid)
                label='仙器' if kind=='xianqi' else '神兵'
                from native946_progression import DATA as ITEMS
                meta=ITEMS['items'].get(str(row['type']),{})
                if not instance or not 1<=instance['grade']<=(3 if kind=='xianqi' else 4) or label not in meta.get('desc',''):
                    raise ValueError('所选装备不符合原生分解种类和阶数。')
                current=state(db,cid,iid)
                from native946_gear_sockets import existing
                if existing(db,iid):raise ValueError('请选择没有开孔的装备。')
                if current['traits'] or current['pending'] or db.execute(
                    'SELECT 1 FROM cosmetic_item_carvings WHERE item_id=? UNION ALL '
                    'SELECT 1 FROM native_equipment_attribute_sets WHERE item_id=?',(iid,iid)).fetchone():
                    raise ValueError('请选择没有刻字、炼化或备用属性的装备。')
                consume(db,row)
                amount+=instance['grade']
            Cosmetics(self.game)._add(db,GEAR_RULES[kind][2],amount,backpack=True)
        return self.game.defer_packets(lambda:(*self.p.item_packets(),text_notice(f'本机分解获得精魂{amount}枚。')))

    def halo(self,db,iid,*,target=False):
        from native946_cosmetics import DATA as CATALOG
        item=self.item(db,iid,appearance=True,worn_cosmetic=target)
        kind=item['type']
        type_rule=DATA['ForgeTypes'].get(str(kind))
        meta=CATALOG['items'].get(str(kind),{})
        if not type_rule or item['amount']!=1 or item['expires'] or meta.get('days',0):
            raise ValueError('请选择原生炼化表中的永久光环或幻翼。')
        return item

    def roll_halo(self):
        race=str(self.game.character['race'])
        groups=DATA['ForgeTraitTbl'][race]
        primary=groups[0];neutral=groups[1]
        direct_filter=set(DIRECT)|set(BATTLE)
        traits=[]
        for index,pool in enumerate(primary):
            available={key:row for key,row in pool.items() if key in direct_filter}
            if not available:
                available={key:row for key,row in neutral[index].items() if key in direct_filter}
            key=choice(tuple(available))
            rank=choice((3,4,5))
            maximum=available[key]['Max'][rank-1]
            traits.append([key,maximum,rank])
        return traits

    def lucky(self,value,trigger=0):
        boundary=0
        if value['traits']:
            rank=max(t[2] for t in value['traits'][:2])
            boundary=DATA['ForgeLuckyTbl'][0][rank-1]['LuckyBoundary']
        return dict(ForgeLucky=value['lucky'],LuckyBoundary=boundary,IsTriggerLucky=trigger)

    def halo_result(self,mode,iid,before,after,lucky):
        from native946_progression import lua_data
        return packet(2979,['byte','number','string','string','string'],[mode,iid,before,after,lua_data(lucky)])

    def halo_operation(self,target_id,material_id,mode):
        if getattr(self.game,'halo_refine_until',0)<=time():
            raise ValueError('请先右键使用两仪宝鼎打开炼化界面。')
        if mode not in (1,2,3,4,11,12):raise ValueError('光环炼化操作无效。')
        now=int(time())
        cid=self.p.cid
        with self.p.transaction(write=mode!=4) as db:
            self.halo(db,target_id,target=True)
            current=state(db,cid,target_id)
            old=describe(current,now)
            if mode==4:return self.halo_result(4,target_id,'','',self.lucky(current))
            if mode==12:
                saved=current['pending']
                if material_id or not saved or saved['until']<=now:
                    raise ValueError('没有可恢复的光环炼化结果。')
                current.update(traits=saved['traits'],expires=saved['expires'],pending={})
            else:
                if target_id==material_id:raise ValueError('阳位与阴位必须是两件不同的物品。')
                material=self.halo(db,material_id)
                source=state(db,cid,material_id)
                if mode==2 and (not current['traits'] or source['traits']):
                    raise ValueError('补充天数需要已炼化的阳位与未经炼化的阴位。')
                if mode==3 and (not source['traits'] or current['traits']):
                    raise ValueError('炼化转移需要有炼化属性的阴位和未炼化的阳位。')
                self.take(db,DING,2 if mode==11 else 1)
                debit(db,cid,500000 if mode==3 else 0 if mode==2 else 30000)
                from native946_mall_uses import consume
                if mode!=3:consume(db,material)
                trigger=0
                if mode==2:
                    current.update(expires=max(now,current['expires'])+15*DAY,pending={})
                elif mode==3:
                    current.update(traits=source['traits'],expires=source['expires'],lucky=0,pending={})
                    db.execute('DELETE FROM native_refinement WHERE item_id=? AND character_id=?',(material_id,cid))
                else:
                    previous=dict(traits=current['traits'],expires=current['expires'],until=now+600)
                    traits=self.roll_halo()
                    boundary=self.lucky(current)['LuckyBoundary']
                    if mode==11 and boundary and current['lucky']>=boundary:
                        trigger=1
                        traits=[list(t) for t in current['traits']]
                        index=choice([i for i,t in enumerate(traits[:2]) if t[2]<10] or [0])
                        key,amount,rank=traits[index]
                        rank=min(10,rank+1)
                        row=next(table[index][key] for tables in DATA['ForgeTraitTbl'].values()
                                 for table in tables if key in table[index])
                        traits[index]=[key,row['Max'][rank-1],rank]
                    current.update(traits=traits,expires=now+60*DAY,
                                   pending=previous if mode==11 else {},
                                   lucky=current['lucky']+1 if mode==11 and not trigger else 0)
            save(db,cid,target_id,current)
            result=self.halo_result(4 if mode==12 else mode,target_id,old,describe(current,now),
                                    self.lucky(current,trigger if mode!=12 else 0))
        return self.game.defer_packets(lambda:(*self.p.item_packets(),*self.p.attribute_packets(),result,
                                                text_notice('光环炼化已保存。随机词条采用本机规则。')))

    def receive(self,mid,reader):
        if mid in GEAR_OPERATIONS:
            kind,select=GEAR_OPERATIONS[mid]
            iid,mode=reader.integer(),reader.take(1)[0]
            check=0 if select else reader.take(1)[0]
            reader.finish()
            return self.choose_gear(kind,iid,mode) if select else self.gear_effects(kind,iid,mode,check)
        if mid in DISASSEMBLY:
            ids=[reader.integer() for _ in range(reader.take(1)[0])]
            reader.finish()
            return self.disassemble(DISASSEMBLY[mid],ids)
        args=[reader.integer() for _ in range(3)];reader.finish()
        return self.halo_operation(*args)
