"""Native basic and physical guardian books with supplied-reference balance.

Book IDs, use request, kinds, positions and reply layouts come from 946.
Stat coefficients and growth costs come from the supplied DH3HSH server;
they must not be presented as recovered official server formulas.
"""
import json
from pathlib import Path
from native946_codec import packet
from native946_codec import text_notice
from native946_growth import species
from native946_neidan_legacy import threshold

REFERENCE_WIRE_VERSION=6
DATA=json.loads(Path(__file__).with_name('native946_skillbooks_data.json').read_text('utf8'))
EXTENDED=json.loads(Path(__file__).with_name('native946_growth_book_rules.json').read_text('utf8'))
DATA['rules'].update(EXTENDED['rules'])
for _item,_coefficients in EXTENDED.get('rule_overrides',{}).items():DATA['rules'][_item]['coefficients'].update(_coefficients)
BOOKS={int(k):v for k,v in DATA['rules'].items()}
SKILLS={v['skill']:v for v in BOOKS.values()}
REQUESTS={754,756}


def schema(db):
    old=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='native_guardian_books'").fetchone()
    old_columns=[r[1] for r in db.execute('PRAGMA table_info(native_guardian_books)')]
    migrate=old is not None and ('tier' not in old_columns or '365' not in old[0])
    if migrate:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if any(fk[2]=='native_guardian_books' for fk in db.execute('PRAGMA foreign_key_list("'+name.replace('"','""')+'")')):
                raise ValueError('守护技能存档存在额外引用，无法应用当前迁移。')
        saved=[tuple(r) for r in db.execute('SELECT * FROM native_guardian_books ORDER BY summon_id,skill')]
        db.execute('ALTER TABLE native_guardian_books RENAME TO native_guardian_books_v1')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_books (
      summon_id INTEGER NOT NULL REFERENCES summons(id) ON DELETE CASCADE,
      skill INTEGER NOT NULL CHECK(skill IN (300,301,302,303,304,305,306,307,308,309,336,337,338,339,365,366,367,369,370,371)),
      position INTEGER NOT NULL CHECK(position BETWEEN 7 AND 11),
      level INTEGER NOT NULL DEFAULT 0 CHECK(level BETWEEN 0 AND 102),
      rebirth INTEGER NOT NULL DEFAULT 0 CHECK(rebirth=0),
      experience INTEGER NOT NULL DEFAULT 0 CHECK(experience>=0),
      expnext INTEGER NOT NULL DEFAULT 40 CHECK(expnext>0),
      tier INTEGER NOT NULL DEFAULT 0 CHECK(tier IN (0,1)),
      CHECK((skill BETWEEN 300 AND 304 AND tier=0 AND position=7) OR
            (skill IN (305,306,307,308,309,336,337,338,339,365,366,367,369,370,371) AND tier=1 AND position BETWEEN 8 AND 11)),
      PRIMARY KEY(summon_id,skill),UNIQUE(summon_id,position))''')
    if migrate:
        columns=','.join(old_columns)
        db.execute('INSERT INTO native_guardian_books('+columns+') SELECT '+columns+' FROM native_guardian_books_v1')
        copied=[tuple(r) for r in db.execute('SELECT '+columns+' FROM native_guardian_books ORDER BY summon_id,skill')]
        if copied!=saved:raise ValueError('守护技能存档迁移校验失败。')
        db.execute('DROP TABLE native_guardian_books_v1')


def raw_list(db,sid):
    return [dict(r) for r in db.execute('SELECT * FROM native_guardian_books WHERE summon_id=? ORDER BY position',(sid,))]


def effective_books(db, pet):
    cap = level_cap(pet)
    return [dict(row, level=min(row['level'], cap)) for row in raw_list(db, pet['id'])]


def effects(rows):
    bonus={}
    for row in rows:
        rule=SKILLS.get(row['skill'])
        if rule is None:raise ValueError('守护特技数据不在已核实范围。')
        for key,(num,den) in rule['coefficients'].items():
            bonus[key]=bonus.get(key,0)+row['level']*num//den
        if rule.get('counter_count'):bonus['book_counter_count']=bonus.get('book_counter_count',0)+rule['counter_count']
    return bonus


def records(rows,pet=None):
    result=[]
    for row in rows:
        rule=SKILLS[row['skill']];bonus=effects([row])
        cost=''
        if rule.get('active'):
            from native946_guardian_magic import values
            v=values(pet or dict(level=0,mpmax=0),row)
            args=[v['proficiency'],v['damage'],0,0]
            if row['skill'] in (338,339):args.insert(3,v['proc_per_mille']/10)
            cost='{'+str(v['mana'])+'}'
        elif row['skill']>=365:args=list(bonus.values())
        elif row['skill']==300:
            args=[bonus.get(k,0) for k in ('hpmax','mpmax','attack','initiative')]+[0,0]
        else:args=[next(iter(bonus.values())),0]
        if row['skill']==306:args.append(bonus.get('book_ignore_counter',0))
        result.append(dict(stunt_id=row['skill'],position=row['position'],name=rule['name'],
                           grade=row['level'],rei=row['rebirth'],stuntexp=row['experience'],
                           stuntexp_next=row['expnext'],desc='{'+','.join(map(str,args))+'}',cost=cost))
    return result

def level_cap(pet):
    return min(102,pet['level'])

def experience_to_cap(row,cap):
    if row['level']>=cap:return 0
    required=row['expnext']+sum(threshold(level,row['rebirth']) for level in range(row['level']+1,cap))
    return max(0,required-row['experience'])


def empty_positions(pet,rows):
    used={r['position'] for r in rows};kind=species(pet['type'])['kind']
    rank=pet['rebirth']*1000+pet['level'] if not pet['ascension'] else 10000
    return [dict(position=int(position),level=slot['tier'],
                 can_do=int(not pet['grant_key'].startswith('capture:') and rank>=slot['rank'] and
                            any(kind in rule['allowed_kinds'] and rule['tier']==slot['tier'] for rule in BOOKS.values())))
            for position,slot in DATA['policy']['crafted_slots'].items() if int(position) not in used]


class SkillBooks:
    def __init__(self,summons):self.s=summons;self.p=summons.progression

    def records(self,sid):
        return self.list_state(sid)[0]

    def list_state(self,sid):
        from native946_guardian_innate import STUNT_RANKS, learning_slots, records as innate_records
        from native946_guardian_skill_details import apply_details
        from native946_innate_training import jing_books, trained_records, visible_innate
        with self.p.transaction(write=False) as db:
            pet=self.s._owned(db,sid);rows=effective_books(db,pet)
            stats=self.s.stats(pet)
            pet['innate_training']=trained_records(db,sid,pet['level'])
            pet['jing_books']=jing_books(db,sid)
            pet['innate_skills']=innate_records(pet,excluded={row['skill'] for row in rows})
            hidden={entry['base'] for entry in pet['jing_books']}
            extra=[row for row in pet['jing_books'] if row['base']<0]
            innate=apply_details(stats,visible_innate(db,pet))
            for entry in innate:
                skill=entry['stunt_id']
                if not entry['desc'].startswith('{') and skill in SKILLS and not SKILLS[skill].get('active'):
                    # A species-born passive may use the same native template as a crafted one.
                    effect=records([dict(skill=skill,position=entry['position'],level=entry['grade'] if entry['grade']>=0 else 1,
                                         rebirth=entry['rei'],experience=entry['stuntexp'],expnext=entry['stuntexp_next'])])[0]
                    entry['desc']=effect['desc']
            stunts=innate+records([row for row in rows if row['skill'] not in hidden],stats)
            used={row['position'] for row in stunts}
            available=learning_slots(stats)
            # The 946 panel renders positions 1–5 as innate slots. It maps wire
            # position 13 to the sixth cell and reads the guardian's jing state there.
            empty=[dict(position=position,level=0,can_do=int(bool(available.get(position))))
                   for position in range(1,len(STUNT_RANKS)+1) if position not in used]
            empty.extend(empty_positions(pet,rows+extra))
            if 12 not in used:
                empty.append(dict(position=12,level=99,can_do=int(
                    pet['sanctification']>0 and not pet['grant_key'].startswith('capture:'))))
            if not used.intersection((6,13)):
                empty.append(dict(position=13,level=0,can_do=0))
            return stunts,empty

    def packet(self,sid):
        stunts,empty=self.list_state(sid)
        return packet(722,['number',('array','stunt_info'),('array','stunt_pos_empty'),
                           ('array','stunt_info'),('array','newzq_stunt_info'),'number'],
                          [sid,stunts,empty,[],[],0])

    def training_reply(self, sid, skill, notice):
        row = next((entry for entry in self.records(sid) if entry['stunt_id'] == skill), None)
        if row is None:
            raise ValueError('守护没有这个特技。')
        return (*self.s.info_packets(sid), packet(721, ['number', 'stunt_info'], [sid, row]), text_notice(notice))

    def learn(self,sid,iid):
        from native946_inventory_locks import require_unlocked
        from native946_mall_bags import pages
        with self.p.transaction() as db:
            pet=self.s._owned(db,sid)
            item=db.execute('SELECT * FROM inventory WHERE character_id=? AND id=?',(self.p.cid,iid)).fetchone()
            if item is None or item['page'] not in pages(db,self.p.cid):
                raise ValueError('技能书不在当前角色已开启的背包中。')
            rule=BOOKS.get(item['type'])
            if rule is None:raise ValueError('此技能书的实际效果尚未核定，本次未消耗物品。')
            require_unlocked(db,self.p.cid,iid)
            if pet['grant_key'].startswith('capture:'):raise ValueError('野生守护不能使用武侯天演图。')
            if species(pet['type'])['kind'] not in rule['allowed_kinds']:raise ValueError('该守护种类不能学习此天演图。')
            learned=raw_list(db,sid)
            if any(r['skill']==rule['skill'] for r in learned):raise ValueError('守护已经学会此特技。')
            from native946_innate_training import jing_books
            slots=[s for s in empty_positions(pet,learned+[r for r in jing_books(db,sid) if r['base']<0])
                   if s['can_do'] and s['level']==rule['tier']]
            if not slots:raise ValueError('尚未解锁对应阶数的打造特技格，或该阶技能格已满。')
            db.execute('INSERT INTO native_guardian_books(summon_id,skill,position,tier) VALUES(?,?,?,?)',
                       (sid,rule['skill'],slots[0]['position'],rule['tier']))
            if item['amount']==1:db.execute('DELETE FROM inventory WHERE id=?',(iid,))
            else:db.execute('UPDATE inventory SET amount=amount-1 WHERE id=?',(iid,))

    def add_experience(self,db,sid,amount,skill=None):
        if type(amount) is not int or amount<0:raise ValueError('守护特技经验无效。')
        pet=self.s._owned(db,sid)
        for row in raw_list(db,sid):
            if skill is not None and row['skill']!=skill:continue
            cap=level_cap(pet)
            if row['level']>=cap:continue
            new=dict(row);new['experience']+=amount
            while new['level']<cap and new['experience']>=new['expnext']:
                new['experience']-=new['expnext'];new['level']+=1
                new['expnext']=threshold(new['level'],new['rebirth'])
            db.execute('UPDATE native_guardian_books SET level=?,experience=?,expnext=? WHERE summon_id=? AND skill=?',
                       (new['level'],new['experience'],new['expnext'],sid,row['skill']))

    def receive(self,game,mid,r):
        sid,skill=r.integer(),r.integer();r.finish()
        if game.battle is not None:raise ValueError('请先完成当前战斗。')
        with self.p.transaction() as db:
            pet=self.s._owned(db,sid)
            row=next((v for v in raw_list(db,sid) if v['skill']==skill),None)
            if row is None:
                from native946_guardian_innate import records as innate_records
                if any(entry['stunt_id']==skill for entry in innate_records(pet)):
                    raise ValueError('该天生技能的培养规则尚未核实，本次未消耗资源。')
                raise ValueError('守护没有这个已接通的特技。')
            if mid==754:
                cap=level_cap(pet)
                if row['level']>=cap:raise ValueError('特技已经达到守护等级或当前阶段上限。')
                if pet['experience']<500:raise ValueError('守护当前经验不足500，不能转换。')
                amount=min(pet['experience']//5,experience_to_cap(row,cap))
                self.add_experience(db,sid,amount,skill)
                db.execute('UPDATE summons SET experience=experience-? WHERE id=?',(amount,sid))
                notice='已将'+str(amount)+'点守护经验转换为特技经验。'
            elif mid==756:
                db.execute('DELETE FROM native_guardian_books WHERE summon_id=? AND skill=?',(sid,skill))
                current=self.s.stats(pet)
                db.execute('UPDATE summons SET hp=min(hp,?),mp=min(mp,?) WHERE id=?',(current['hpmax'],current['mpmax'],sid))
                notice='已经放弃该打造特技。'
            else:raise ValueError('特技请求无效。')
            # Essential state is built before committing; optional panels do
            # not turn a committed mutation into an apparent failed action.
            reply=self.training_reply(sid,skill,notice) if mid==754 else (
                *self.s.info_packets(sid),self.packet(sid),text_notice(notice))
        game.last_event=dict(action='guardian_book_changed',message_id=mid,summon_id=sid,skill=skill)
        return reply
