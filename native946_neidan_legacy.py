"""Local legacy-pill test profile recovered from the supplied DH3HSH package.

Native 946 IDs and packet layouts, third-party balance. New 2026 pills and
legacy effects with different native meanings are deliberately excluded.
"""
import json
import math
from pathlib import Path
from native946_codec import packet
from native946_neidan import LEVEL_CAPS
REFERENCE_WIRE_VERSION=8

DATA=json.loads(Path(__file__).with_name('native946_neidan_legacy.json').read_text('utf-8'))
KINDS={int(k):v for k,v in DATA['skills'].items()}


def schema(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS legacy_neidan (
        summon_id INTEGER NOT NULL REFERENCES summons(id), kind INTEGER NOT NULL,
        level INTEGER NOT NULL DEFAULT 0, rebirth INTEGER NOT NULL DEFAULT 0,
        experience INTEGER NOT NULL DEFAULT 0, expnext INTEGER NOT NULL DEFAULT 40,
        energy INTEGER NOT NULL DEFAULT 6000, PRIMARY KEY(summon_id,kind));
      CREATE TABLE IF NOT EXISTS legacy_neidan_items (
        item_id INTEGER PRIMARY KEY REFERENCES inventory(id), kind INTEGER NOT NULL,
        level INTEGER NOT NULL DEFAULT 0, rebirth INTEGER NOT NULL DEFAULT 0,
        experience INTEGER NOT NULL DEFAULT 0, expnext INTEGER NOT NULL DEFAULT 40,
        energy INTEGER NOT NULL DEFAULT 6000);
      CREATE TABLE IF NOT EXISTS legacy_neidan_test_grants (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id));
    ''')


def threshold(level,rebirth):
    if type(level) is not int or not 0<=level<=142 or type(rebirth) is not int or not 0<=rebirth<=2:
        raise ValueError('内丹等级或转生阶段无效。')
    return math.floor((level*level*level+50)*(1.06+rebirth*.24)*.7)


def grow(row,experience):
    if type(experience) is not int or experience<0:raise ValueError('内丹经验无效。')
    row=dict(row);cap=LEVEL_CAPS[row['rebirth']]
    row['experience']+=experience
    # The reference uses strict > and a second recursion path updates the
    # threshold too late. Equality must level up, with each new cost applied.
    while row['level']<cap and row['experience']>=row['expnext']:
        row['experience']-=row['expnext'];row['level']+=1
        row['expnext']=threshold(row['level'],row['rebirth'])
    return row


def effect(row,pet):
    kind=row['kind'];level=row['level'];rebirth=row['rebirth']
    b=math.floor((math.sqrt(pet['level']*level*.04)*(1+.25*rebirth)+
                  max(0,pet['amity'])**(1/6)*level/50)*1000)
    factor=4e-6 if kind in (211,212,213,214) else 1e-5 if kind==233 else 5e-6
    s=b*factor;chance=min(100,math.floor(s*10000+.5)/100)
    result={'kind':kind,'chance':chance,'enabled':row['energy']>0}
    if kind in (211,212,213,214):
        result['damage']=math.floor(296.1572+.0002364957*pet['mpmax']**1.57)
        result['args']=[chance,result['damage'],0]
    elif kind==231:
        ratio=math.floor((pet['level']**2*.2/(pet['mpmax']+1)+s)*10000)/10000
        # Reference battle damage divides displayed percentage by 500.
        # Present the effective percentage to keep the native tooltip honest.
        result['ratio']=(math.floor(ratio*10000+.5)/100)/5
        result['args']=[chance,result['ratio'],0]
    elif kind==235:
        ratio=math.floor((pet['level']**2*.2/(pet['mpmax']+1)+s*3)*1000)/1000
        result['ratio']=math.floor(ratio*10000+.5)/100
        result['args']=[chance,result['ratio'],0]
    else:
        result['counter_count']=6 if s>.51 else 5 if s>.45 else 4 if s>.32 else 3 if s>.17 else 2 if s>.09 else 1
        result['args']=[chance]
    return result


def raw_list(db,sid):
    return [dict(r) for r in db.execute('SELECT * FROM legacy_neidan WHERE summon_id=? ORDER BY kind',(sid,))]


def after_drop(row):
    """Apply the level penalty without blocking or deleting remaining XP.

    Spitting out a pill is allowed with nonzero XP. Keep the reference
    profile's stored XP unchanged while its official post-drop accounting
    is investigated; do not turn missing formula evidence into a usage ban.
    """
    result=dict(row)
    result['level']=max(0,row['level']-3)
    result['expnext']=40 if not result['level'] and not row['rebirth'] else threshold(result['level'],row['rebirth'])
    return result


class LegacyNeidan:
    def __init__(self,summons):self.summons=summons;self.p=summons.progression

    def owned(self,db,sid):return self.summons.stats(self.summons._owned(db,sid))

    def _equipped(self,db,sid,kind):
        self.owned(db,sid)
        row=db.execute('SELECT * FROM legacy_neidan WHERE summon_id=? AND kind=?',(sid,kind)).fetchone()
        if row is None or kind not in KINDS:raise ValueError('该守护没有这枚已接通的旧内丹。')
        return dict(row)

    def _save(self,db,sid,row):
        db.execute('UPDATE legacy_neidan SET level=?,rebirth=?,experience=?,expnext=?,energy=? WHERE summon_id=? AND kind=?',
                   (*[row[k] for k in ('level','rebirth','experience','expnext','energy')],sid,row['kind']))

    def records(self,sid):
        with self.p.transaction(write=False) as db:
            pet=self.owned(db,sid);records=[]
            for row in raw_list(db,sid):
                eff=effect(row,pet)
                records.append(dict(neidan_id=row['kind'],name=KINDS[row['kind']]['name'],nei_type=0,
                                    grade=row['level'],rei=row['rebirth'],neiexp=row['experience'],neiexp_next=row['expnext'],
                                    last=row['energy'],last_max=6000+1000*row['rebirth'],nei_advance=0,
                                    desc='{'+','.join(str(x) for x in eff['args'])+'}',resonance_data='{}'))
            return records

    def packet(self,sid):
        return packet(716,['number',('array','neidan_info')],[sid,self.records(sid)])

    def grant_test_kit(self):
        with self.p.transaction() as db:
            if db.execute('SELECT 1 FROM legacy_neidan_test_grants WHERE character_id=?',(self.p.cid,)).fetchone():return False
            occupied={r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.p.cid,))}
            free=[n for n in range(1,25) if n not in occupied]
            if len(free)<len(KINDS)+1:raise ValueError('领取内丹测试包需要8个背包空格。')
            for slot,kind in zip(free,KINDS):
                iid=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,6052,8,?,1)',(self.p.cid,slot)).lastrowid
                db.execute('INSERT INTO legacy_neidan_items(item_id,kind) VALUES(?,?)',(iid,kind))
            db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,6024,8,?,20)',(self.p.cid,free[len(KINDS)]))
            db.execute('INSERT INTO legacy_neidan_test_grants VALUES(?)',(self.p.cid,))
            return True

    def use(self,sid,item_id):
        from native946_mall_bags import pages
        from native946_inventory_locks import require_unlocked
        with self.p.transaction() as db:
            pet=self.owned(db,sid)
            item=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',(item_id,self.p.cid)).fetchone()
            if item is None or item['page'] not in pages(db,self.p.cid):
                raise ValueError('物品不在已开启的背包中。')
            require_unlocked(db,self.p.cid,item_id)
            if item['type']==6024:
                rows=raw_list(db,sid)
                # Native item usage specifies the guardian, not a pill.
                # The local test profile trains every equipped, non-max pill.
                rows=[r for r in rows if r['level']<LEVEL_CAPS[r['rebirth']]]
                if not rows:raise ValueError('请先安装内丹，或所装内丹已经满级。')
                for row in rows:self._save(db,sid,grow(row,10000))
            elif item['type']==6052:
                if item['amount']!=1:
                    raise ValueError('内丹数量异常，本次未消耗物品。')
                row=db.execute('SELECT * FROM legacy_neidan_items WHERE item_id=?',(item_id,)).fetchone()
                if row is None or row['kind'] not in KINDS:raise ValueError('这枚内丹的数值尚未接通，本次未消耗物品。')
                row=dict(row)
                installed=db.execute('SELECT * FROM legacy_neidan WHERE summon_id=? AND kind=?',(sid,row['kind'])).fetchone()
                if installed:
                    if row['level'] or row['rebirth'] or row['experience']:
                        raise ValueError('已培养的同名内丹不能用于补充元气，本次未消耗物品。')
                    maximum=6000+1000*installed['rebirth']
                    if installed['energy']>=maximum:raise ValueError('内丹元气已满，无需消耗同类内丹。')
                    db.execute('UPDATE legacy_neidan SET energy=min(?,energy+100) WHERE summon_id=? AND kind=?',(maximum,sid,row['kind']))
                else:
                    from native946_neidan import installed,PILL_TYPES
                    new_low=sum(PILL_TYPES[r['kind']]==1 for r in installed(db,sid))
                    if len(raw_list(db,sid))+new_low>=1+min(2,pet['rebirth']):raise ValueError('当前守护的低级内丹槽位已满。')
                    if row['rebirth']>pet['rebirth']:raise ValueError('内丹转生阶段高于守护。')
                    db.execute('INSERT INTO legacy_neidan VALUES(?,?,?,?,?,?,?)',
                               (sid,*[row[k] for k in ('kind','level','rebirth','experience','expnext','energy')]))
                db.execute('DELETE FROM legacy_neidan_items WHERE item_id=?',(item_id,))
            else:raise ValueError('这不是已接通的内丹物品。')
            if item['amount']==1:db.execute('DELETE FROM inventory WHERE id=?',(item_id,))
            else:db.execute('UPDATE inventory SET amount=amount-1 WHERE id=?',(item_id,))

    def convert(self,sid,kind):
        with self.p.transaction() as db:
            row=self._equipped(db,sid,kind);pet=self.owned(db,sid)
            if row['level']>=LEVEL_CAPS[row['rebirth']]:raise ValueError('内丹已达当前转生阶段上限。')
            amount=pet['experience']//5
            if amount<500:raise ValueError('守护至少需要2500点结余经验才能转换。')
            self._save(db,sid,grow(row,amount))
            db.execute('UPDATE summons SET experience=experience-? WHERE id=?',(amount,sid))

    def reborn(self,sid,kind):
        with self.p.transaction() as db:
            row=self._equipped(db,sid,kind);pet=self.owned(db,sid)
            if row['rebirth']>=2 or row['level']<LEVEL_CAPS[row['rebirth']]:raise ValueError('内丹尚未达到转生等级，或已经二转。')
            if row['rebirth']>=pet['rebirth']:raise ValueError('请先提升守护转生阶段。')
            row.update(rebirth=row['rebirth']+1,level=0,experience=0)
            row['expnext']=threshold(0,row['rebirth']);self._save(db,sid,row)

    def drop(self,sid,kind):
        with self.p.transaction() as db:
            row=after_drop(self._equipped(db,sid,kind))
            page,slot=self.p.free_bag_slot(self.p._snapshot(db))
            iid=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,6052,?,?,1)',
                           (self.p.cid,page,slot)).lastrowid
            db.execute('INSERT INTO legacy_neidan_items VALUES(?,?,?,?,?,?,?)',(iid,*[row[k] for k in ('kind','level','rebirth','experience','expnext','energy')]))
            db.execute('DELETE FROM legacy_neidan WHERE summon_id=? AND kind=?',(sid,kind))


def item_metadata(db,item_id):
    row=db.execute('SELECT * FROM legacy_neidan_items WHERE item_id=?',(item_id,)).fetchone()
    if row is None:return None
    return _item_metadata(row)


def item_metadata_batch(db,cid):
    return {r['item_id']:_item_metadata(r) for r in db.execute(
        'SELECT n.* FROM legacy_neidan_items n JOIN inventory i ON i.id=n.item_id WHERE i.character_id=?',(cid,))}


def _item_metadata(row):
    meta=KINDS[row['kind']]
    return dict(name=meta['name'],icon=DATA['items']['6052']['icon'],
                desc=f"{meta['intro']}#r内丹等级 {row['level']}，转生 {row['rebirth']}；经验 {row['experience']}/{row['expnext']}。")
