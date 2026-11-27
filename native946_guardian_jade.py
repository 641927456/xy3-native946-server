"""Native jade and guardian stone messages with local cultivation rules."""
import json
from pathlib import Path
from random import choice, randrange

from native946_codec import packet

DATA=json.loads(Path(__file__).with_name('native946_guardian_jade_data.json').read_text('utf8'))
ITEMS=DATA['items']
GEMS=DATA['BaseConfig']['HoleItemType']
OLD_STONES={int(kind):values for kind,values in DATA['old_stones'].items()}
STARS={42350:('ReFire',96),42351:('ReWind',96),42352:('ReWater',96),42353:('ReThunder',96),
       42354:('defense',120),42355:('ReSeal',120),42356:('ReWenxin',120),42357:('ReFaint',120),42358:('hpmax',360)}
STAR_RACE={42350:'仙',42351:'鬼',42352:'人',42353:'兽',42354:'妖',42355:'人',42356:'人',42357:'人',42358:'人'}
RACES={1:'人',2:'妖',3:'仙',4:'鬼',5:'兽'}
JADE_READS={3948,3949,3950,3953,3957,3978}
JADE_WRITES={3945,3946,3947,3951,3956,3958,4908,4909,4910}
STONE_READS={952,3043}
STONE_WRITES={950,3042}
REQUESTS=JADE_READS|JADE_WRITES|STONE_READS|STONE_WRITES
WRITES=JADE_WRITES|STONE_WRITES

ATTRIBUTE_FIELDS=(
    'attack','initiative','EquipElementDamage','hpmax','PhyAbsorbDegreeLimit','EquipElementResist',
    'ImmAbsorbDegreeLimit','human_resist','attack','initiative','EquipElementDamage','hpmax','mpmax',
    'defense','ReGushu','ReMpAbsorb','EquipElementResist','human_resist','attack','initiative','hpmax',
    'mpmax','EquipElementDamage','EquipElementResist','defense','ReGushu',
)


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_jade (
        item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
        summon_id INTEGER REFERENCES summons(id) ON DELETE CASCADE, slot INTEGER,
        level INTEGER NOT NULL DEFAULT 1, progress INTEGER NOT NULL DEFAULT 0,
        main TEXT NOT NULL, bonus TEXT NOT NULL, fixed TEXT NOT NULL DEFAULT '[]', skill INTEGER NOT NULL DEFAULT 0,
        UNIQUE(summon_id,slot))''')
    db.execute('CREATE TABLE IF NOT EXISTS native_guardian_jade_luck (cid INTEGER PRIMARY KEY,luck INTEGER NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_stones (
        summon_id INTEGER NOT NULL REFERENCES summons(id) ON DELETE CASCADE,
        tier INTEGER NOT NULL, slot INTEGER NOT NULL, kind INTEGER NOT NULL,
        PRIMARY KEY(summon_id,tier,slot))''')


def jade_attributes(jade):
    level=jade['level']
    main=[(k,DATA['AttConfig'][k-1]['AttValue'][quality-1]*level) for k,quality in json.loads(jade['main'])]
    bonus=[(k,DATA['AttConfig'][k-1]['AttValue'][quality-1]*level) for k,quality in json.loads(jade['bonus'])]
    fixed=[(k,DATA['AttConfig'][k-1]['AttValue'][quality-1]) for k,quality in json.loads(jade['fixed'])]
    return main,bonus,fixed


def equipped_jades(db,sid):
    return [dict(row) for row in db.execute('SELECT * FROM native_guardian_jade WHERE summon_id=? ORDER BY slot',(sid,))]


def stones(db,sid,tier):
    return {row['slot']:row['kind'] for row in db.execute('SELECT slot,kind FROM native_guardian_stones WHERE summon_id=? AND tier=?',(sid,tier))}


def star_effects(row):
    totals={}
    for slot,kind in row.items():
        if not kind:continue
        key,base=STARS[kind]
        left=bool(row.get(slot-1))
        right=bool(row.get(slot+1))
        totals[key]=totals.get(key,0)+base+base//2*left
        if right:totals['initiative']=totals.get('initiative',0)+2
    return totals


def effects(db,pet):
    sid=pet['id']
    totals=star_effects(stones(db,sid,1))
    for kind in stones(db,sid,0).values():
        if kind:
            stat,amount=OLD_STONES[kind]
            totals[stat]=totals.get(stat,0)+amount
    jades=equipped_jades(db,sid)
    traits={row['skill']:DATA['SkillAttConfig'][row['skill']-1]['AttValue']*min(5,row['level'])
            for row in jades if row['skill']}
    for jade in jades:
        for position,group in enumerate(jade_attributes(jade)):
            for key,amount in group:
                if position<2 and jade['slot']>=3:amount+=amount*traits.get(18,0)//1000
                field=ATTRIBUTE_FIELDS[key-1]
                names=('ReSeal','ReFaint','ReDisorder') if field=='human_resist' else (field,)
                for stat in names:totals[stat]=totals.get(stat,0)+amount
    if traits.get(14):totals['hpmax']=totals.get('hpmax',0)+traits[14]
    for stat,amount in totals.items():pet[stat]=pet.get(stat,0)+amount
    if traits:pet['jade_skills']=traits


class Cultivation:
    def __init__(self,game):
        self.g=game
        self.p=game.progression
        self.s=game.summons

    def bag_item(self,db,iid,types):
        from native946_mall_bags import pages
        from native946_inventory_locks import require_unlocked
        item=db.execute('SELECT * FROM inventory WHERE character_id=? AND id=?',(self.p.cid,iid)).fetchone()
        if not item or item['type'] not in types or item['page'] not in pages(db,self.p.cid):
            raise ValueError('请选用当前背包中可用的材料。')
        require_unlocked(db,self.p.cid,iid)
        return dict(item)

    def spend(self,db,kind,count):
        from native946_neidan import NewNeidan
        NewNeidan(self.s).spend(db,kind,count)

    def grant(self,db,kind,count=1):
        page,slot=self.p.free_bag_slot(self.p._snapshot(db))
        return db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',
                          (self.p.cid,kind,page,slot,count)).lastrowid

    def unlock_jade(self,db):
        from native946_campaign import completed_names
        snapshot=self.p._snapshot(db)
        if '夜半鸡鸣' not in completed_names(snapshot) and not (snapshot['sanctification'] and snapshot['level']>=120):
            raise ValueError('完成夜半鸡鸣，或达到本机封圣120级开放条件后可使用真文玉匣。')

    def jade(self,db,iid):
        row=db.execute('SELECT j.*,i.type FROM native_guardian_jade j JOIN inventory i ON i.id=j.item_id '
                       'WHERE i.character_id=? AND j.item_id=?',(self.p.cid,iid)).fetchone()
        if not row:raise ValueError('请选择已打磨的宝玉。')
        return dict(row)

    def jade_at(self,db,sid,slot):
        self.s._owned(db,sid)
        if not 1<=slot<=len(GEMS):raise ValueError('宝玉槽位无效。')
        row=db.execute('SELECT item_id FROM native_guardian_jade WHERE summon_id=? AND slot=?',(sid,slot)).fetchone()
        return self.jade(db,row[0]) if row else None

    def jade_packet(self,mid,ids,jade):
        from native946_guardian_lottery import lua
        pairs=[('array','pair_number')]*2
        groups=jade_attributes(jade) if jade else ([],[],[])
        traits=jade['skill'] if jade else 0
        if traits:
            value=DATA['SkillAttConfig'][traits-1]['AttValue']*min(5,jade['level'])
            trait=lua([dict(key=traits,values=[value])])
        else:trait='{}'
        return packet(mid,['number']*(len(ids)+2)+pairs+['string',('array','pair_number')],
                      [*ids,jade['level'] if jade else 0,jade['progress'] if jade else 0,
                       *[[dict(key=k,value=v) for k,v in group] for group in groups[:2]],trait,
                       [dict(key=k,value=v) for k,v in groups[2]]])

    def list_packet(self,db,mid):
        from native946_guardian_lottery import lua
        from native946_mall_bags import pages
        bag=set(pages(db,self.p.cid))
        rows=[dict(row) for row in db.execute('SELECT j.*,i.type,i.page FROM native_guardian_jade j '
              'JOIN inventory i ON i.id=j.item_id WHERE i.character_id=? ORDER BY i.id',(self.p.cid,))]
        if mid==3953:
            return packet(3952,[('array','pair_number')],[[dict(key=j['item_id'],value=j['level']) for j in rows if j['page'] in bag]])
        def summary(jade):
            name=DATA['SkillAttConfig'][jade['skill']-1]['AttName'] if jade['skill'] else ''
            return dict(Level=jade['level'],Remark=name,Progress=jade['progress'])
        items=[dict(key=j['item_id'],value=lua(summary(j))) for j in rows if j['page'] in bag]
        guardians=[dict(key=j['summon_id'],value=lua(dict(summary(j),Index=j['slot'])))
                   for j in rows if j['summon_id'] is not None]
        return packet(3955,[('array','pair_string')]*2,[items,guardians])

    def forge(self,db,index):
        from native946_refinement import debit
        if not 1<=index<=len(GEMS):raise ValueError('请选择要打造的宝玉种类。')
        kind=GEMS[index-1]
        rule=DATA['ForgeConfig'][str(kind)]
        for row in rule['TypeAmount']:
            for item,n in row.items():self.spend(db,int(item),n)
        debit(db,self.p.cid,rule['CostCash'])
        iid=self.grant(db,kind)
        luck=db.execute('SELECT luck FROM native_guardian_jade_luck WHERE cid=?',(self.p.cid,)).fetchone()
        luck=luck[0] if luck else 1
        odds=next(row['Odds'] for row in DATA['LuckConfig'] if row['LuckRange'][0]<=luck<=row['LuckRange'][1])
        # Local payout uses the native odds and the effects implemented in combat.
        pool=[skill for skill in rule['SkillAtt'] if skill in (1,2,3,4,5,6,10,11,13,14,15,18)]
        skill=choice(pool) if pool and randrange(1000)<odds else 0
        db.execute('INSERT INTO native_guardian_jade_luck VALUES(?,?) ON CONFLICT(cid) DO UPDATE SET luck=excluded.luck',
                   (self.p.cid,1 if skill else min(100,luck+1)))
        main=json.dumps([[choice(rule['BaseAtt']),randrange(1,5)]])
        bonus=json.dumps([[choice(rule['ExtAtt']),randrange(1,5)] for _ in range(3)])
        db.execute('INSERT INTO native_guardian_jade(item_id,main,bonus,skill) VALUES(?,?,?,?)',(iid,main,bonus,skill))
        return (packet(3981,['number']*2,[index,iid]),self.jade_packet(3942,[iid],self.jade(db,iid)))

    def upgrade(self,db,jade,mode):
        from native946_mall_uses import consume
        from native946_refinement import debit
        level=jade['level']
        if level>=10:raise ValueError('宝玉已达到10级。')
        rule=DATA['UpgradeConfig'][level-1]
        if level<5:
            if mode not in (1,2,3):raise ValueError('请选择有效的升级消耗方式。')
            for kind,count in rule['TypeAmount'].items():self.spend(db,int(kind),count)
            if mode==2:
                changed=db.execute('UPDATE cultivation_state SET tongbao=tongbao-? WHERE character_id=? AND tongbao>=?',
                                   (rule['Fund'],self.p.cid,rule['Fund'])).rowcount
                if not changed:raise ValueError('通宝余额不足。')
            else:debit(db,self.p.cid,rule['Cash'])
            gain=100
        else:
            if mode==jade['item_id']:raise ValueError('不能使用自身升级。')
            item=self.bag_item(db,mode,(jade['type'],))
            material=self.jade(db,item['id'])
            if material['summon_id'] is not None:raise ValueError('请先卸下作为材料的宝玉。')
            self.spend(db,47663,10)
            consume(db,item)
            gain=1000 if material['skill'] else 250
        progress=jade['progress']+gain
        fixed=json.loads(jade['fixed'])
        if progress>=rule['Progress']:
            progress-=rule['Progress']
            level+=1
            if str(level) in DATA['BaseConfig']['FixAttrConfig']:
                fixed.append([choice(DATA['BaseConfig']['FixAttrList']),randrange(1,5)])
        db.execute('UPDATE native_guardian_jade SET level=?,progress=?,fixed=? WHERE item_id=?',
                   (level,progress if level<10 else 0,json.dumps(fixed),jade['item_id']))

    def jade_request(self,db,mid,args):
        if mid==3978:
            sid=args[0]
            self.s._owned(db,sid)
            slots=[j['slot'] for j in equipped_jades(db,sid)]
            return packet(3977,['number',('array','number')],[sid,slots])
        self.unlock_jade(db)
        if mid==3949:
            row=db.execute('SELECT luck FROM native_guardian_jade_luck WHERE cid=?',(self.p.cid,)).fetchone()
            return (packet(3943,['number'],[row[0] if row else 1]),)
        if mid in (3953,3957):return (self.list_packet(db,mid),)
        if mid==3946:return self.forge(db,args[0])
        if mid==3948:return (self.jade_packet(3942,args,self.jade(db,args[0])),)
        if mid==3950:return (self.jade_packet(3944,args,self.jade_at(db,*args)),)
        if mid in (3947,3951,3958,4909):
            sid,slot,*more=args
            old=self.jade_at(db,sid,slot)
            iid=more[0] if more else None
            if mid==3947:
                item=self.bag_item(db,iid,(GEMS[slot-1],))
                jade=self.jade(db,iid)
                if jade['summon_id'] is not None:raise ValueError('该宝玉已有佩戴守护。')
                if old:
                    db.execute('UPDATE inventory SET page=-2,slot=? WHERE id=?',(iid,iid))
                    db.execute('UPDATE native_guardian_jade SET summon_id=NULL,slot=NULL WHERE item_id=?',(old['item_id'],))
                    db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(item['page'],item['slot'],old['item_id']))
                db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(6000+sid%1000,slot,iid))
                db.execute('UPDATE native_guardian_jade SET summon_id=?,slot=? WHERE item_id=?',(sid,slot,iid))
            else:
                if old is None:raise ValueError('该槽位没有宝玉。')
                iid=old['item_id']
                if mid==3951:
                    page,bag_slot=self.p.free_bag_slot(self.p._snapshot(db))
                    db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(page,bag_slot,iid))
                    db.execute('UPDATE native_guardian_jade SET summon_id=NULL,slot=NULL WHERE item_id=?',(iid,))
                elif mid==3958:self.upgrade(db,old,more[0])
                else:self.reroll(db,old,*more)
            return (self.jade_packet(3944,[sid,slot],self.jade_at(db,sid,slot)),*self.s.info_packets(sid))
        iid=args[0]
        self.bag_item(db,iid,GEMS)
        jade=self.jade(db,iid)
        if mid==3945:
            count=DATA['BreakConfig'][min(5,jade['level'])-1]['TypeAmount']['47663']+10*max(0,jade['level']-5)
            db.execute('DELETE FROM inventory WHERE id=?',(iid,))
            self.grant(db,47663,count)
            return (packet(3941,['number'],[iid]),)
        if mid==3956:self.upgrade(db,jade,args[1])
        elif mid==4908:self.reroll(db,jade,*args[1:])
        elif mid==4910:
            material=self.bag_item(db,args[1],(jade['type'],))
            if material['id']==iid:raise ValueError('不能继承同一块宝玉。')
            donor=self.jade(db,material['id'])
            if jade['level']<8 or donor['level']<6:raise ValueError('继承需要8级主玉和6级副玉。')
            db.execute('UPDATE native_guardian_jade SET bonus=?,skill=? WHERE item_id=?',(donor['bonus'],donor['skill'],iid))
            db.execute('DELETE FROM inventory WHERE id=?',(donor['item_id'],))
        return (packet(4907 if mid==4908 else 3954,['number'],[iid]),self.jade_packet(3942,[iid],self.jade(db,iid)))

    def reroll(self,db,jade,material_id,index):
        from native946_mall_uses import consume
        fixed=json.loads(jade['fixed'])
        if not 1<=index<=len(fixed):raise ValueError('该宝玉的额外词条尚未解锁。')
        material=self.bag_item(db,material_id,(47663,))
        consume(db,material,DATA['BaseConfig']['ForgeFixAttrMaterials']['47663'])
        fixed[index-1]=[choice(DATA['BaseConfig']['FixAttrList']),randrange(1,5)]
        db.execute('UPDATE native_guardian_jade SET fixed=? WHERE item_id=?',(json.dumps(fixed),jade['item_id']))

    def stone_request(self,db,mid,args):
        from native946_growth import species
        from native946_role_systems import state
        from native946_refinement import debit
        from native946_mall_uses import consume
        sid,*more=args
        pet=self.s._owned(db,sid)
        native=species(pet['type'])
        old_slots=13 if pet['sanctification'] else 10
        old=stones(db,sid,0)
        new=stones(db,sid,1)
        active=state(db,self.p.cid,'tianji',{}).get('active',[])
        limit=min(9,1+max(0,pet['level']-20)//20) if 103 in active and sum(bool(x) for x in old.values())>=old_slots else 0
        if mid in STONE_WRITES:
            iid,slot=more
            is_star=mid==950
            data,maximum=(new,limit) if is_star else (old,old_slots)
            if not 1<=slot<=maximum or slot>1 and slot-1 not in data:
                raise ValueError('请满足开孔条件并按顺序操作。')
            allowed=STARS if is_star else OLD_STONES
            if iid:
                item=self.bag_item(db,iid,allowed)
                if is_star and slot in (4,7,8,9):
                    race=RACES.get(native['race'],'')
                    if STAR_RACE[item['type']]!=race:raise ValueError('该种族星位要求相合五行的九星石。')
                debit(db,self.p.cid,20000 if is_star else 5000)
                consume(db,item)
                kind=item['type']
            elif slot not in data:
                debit(db,self.p.cid,10000 if is_star else 3000)
                kind=0
            elif not is_star and data[slot]:
                kind=0
            else:raise ValueError('该孔位已开启。')
            db.execute('INSERT INTO native_guardian_stones VALUES(?,?,?,?) ON CONFLICT(summon_id,tier,slot) '
                       'DO UPDATE SET kind=excluded.kind',(sid,int(is_star),slot,kind))
            data[slot]=kind
        race=native['race']
        if mid in (950,952):
            descriptions=[dict(type=new.get(i,0),desc=self.stone_description(new.get(i))) for i in range(1,10)]
            response=packet(948,['number','string',*(['number']*6),('array','new_stone_info'),'number','byte'],
                            [sid,pet['name'],pet['type'],race,len(new),limit,sum(bool(x) for x in old.values()),old_slots,
                             descriptions,int(mid==950),0])
        else:
            statuses=[3 if old.get(i,0) else 2 if i in old else 1 if i<=old_slots and (i==1 or i-1 in old) else 0
                      for i in range(1,16)]
            descriptions=[dict(type=old.get(i,0),desc=self.stone_description(old.get(i))) for i in range(1,16)]
            rank=pet['level']+1000*pet['rebirth']+10000*pet['ascension']+100000*pet['sanctification']
            response=packet(3040,['number','string',*(['number']*4),('array','number'),('array','new_stone_info'),'byte','string'],
                            [sid,pet['name'],pet['type'],race,rank,0,statuses,descriptions,0,''])
        return (response,*self.s.info_packets(sid)) if mid in STONE_WRITES else (response,)

    def stone_description(self,kind):
        return ITEMS[str(kind)]['desc'] if kind else ''

    def receive(self,mid,reader):
        count={3945:1,3946:1,3947:3,3948:1,3949:0,3950:2,3951:2,3953:0,3956:2,3957:0,
               3958:3,3978:1,4908:3,4909:4,4910:2,950:3,952:1,3042:3,3043:1}[mid]
        args=[reader.integer() for _ in range(count)]
        reader.finish()
        write=mid in WRITES
        if write and self.g.battle:raise ValueError('请先完成战斗再培养守护。')
        with self.p.transaction(write=write) as db:
            reply=(self.stone_request(db,mid,args) if mid in STONE_READS|STONE_WRITES
                   else self.jade_request(db,mid,args))
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.p.attribute_packets(),*reply)) if write else reply
