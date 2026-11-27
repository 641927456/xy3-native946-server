"""Owned guardians with native 946 base growth and persisted individual traits."""
from time import time

from native946_codec import packet
from native946_growth import guardian_defaults,guardian_stats,species
import native946_guardian_chamber as guardian_chamber


STARTER_TYPE = 3197  # SUMMON_CALC_LIST and SHAPE_INFO: 猴儿精, native shape 3143.
ATTRS = ('cor', 'mag', 'strength', 'speed')
WIRE_VERSION = 23
LEVEL_EXPERIENCE_FACTOR = 40
STAGE_LEVEL_CAPS = (100, 120, 140)
SANCTIFICATION_LEVEL_CAP = 185  # 化境守护样本仍以185作为基础等级，境阶单独显示。
# The original list renderer reserves IDs <= 100000 for unlock-slot buttons.
SUMMON_ID_MIN = 100001
SUMMON_ID_MAX = 199999  # Bounded range reserved by this local server.
RELEASE = 5  # SUMMON_SET_FREE in the 946 client.
# 2026-05-20 classic maintenance notice, matched to the 946 species table.
PROTECTED_RELEASE_TYPES = frozenset(range(3404, 3413))  # 蛋融融、小辣鸡、辣椒鸡


def migrate_native_ids(db):
    """Preserve every declared guardian reference when repairing old slot IDs."""
    used = {r[0] for r in db.execute('SELECT id FROM summons')}
    used.update(r[0] for r in db.execute('SELECT id FROM characters'))
    available = (n for n in range(SUMMON_ID_MIN, SUMMON_ID_MAX + 1) if n not in used)
    mapping = {}
    for (old,) in db.execute('SELECT id FROM summons WHERE id<? ORDER BY id', (SUMMON_ID_MIN,)).fetchall():
        new = next(available, None)
        if new is None:raise ValueError('本机守护编号已用完。')
        mapping[old] = new
    if mapping:
        def quoted(name):return '"' + name.replace('"', '""') + '"'
        references = []
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for fk in db.execute('PRAGMA foreign_key_list(' + quoted(table) + ')'):
                if fk[2] == 'summons' and fk[4] == 'id':references.append((table, fk[3]))
        db.execute('PRAGMA defer_foreign_keys=ON')
        for old, new in mapping.items():
            for table, column in references:
                db.execute('UPDATE ' + quoted(table) + ' SET ' + quoted(column) + '=? WHERE ' + quoted(column) + '=?', (new, old))
            db.execute('UPDATE summons SET id=? WHERE id=?', (new, old))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_object_locks'").fetchone():
                db.execute("UPDATE native_object_locks SET object_id=? WHERE kind='summon' AND object_id=?", (new,old))
    db.execute("INSERT INTO sqlite_sequence(name,seq) SELECT 'summons',? "
               "WHERE NOT EXISTS(SELECT 1 FROM sqlite_sequence WHERE name='summons')", (SUMMON_ID_MIN - 1,))
    db.execute("UPDATE sqlite_sequence SET seq=max(seq,?,(SELECT coalesce(max(id),0) FROM summons)) WHERE name='summons'",
               (SUMMON_ID_MIN - 1,))
    return mapping


def numbers(mid, *values):
    return packet(mid, ['number'] * len(values), values)


def guardian_stage_level_cap(pet=None):
    from native946_wilderness import POLICY

    if pet is None:
        return STAGE_LEVEL_CAPS[0]
    if pet['sanctification']:
        return SANCTIFICATION_LEVEL_CAP
    if pet['ascension']:
        return POLICY['guardian_max_level']
    return STAGE_LEVEL_CAPS[min(pet['rebirth'], len(STAGE_LEVEL_CAPS) - 1)]


def guardian_level_cap(hero_level, pet=None):
    return min(guardian_stage_level_cap(pet), hero_level + 10)


class Summons:
    def __init__(self, progression):
        self.progression = progression
        self.wire_version = WIRE_VERSION
        with progression.transaction(write=False) as db:
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE name IN ('summons','summon_battle_rewards','one_fighting_summon','one_showing_summon','native_guardian_marks','native_guardian_mark_names','native_guardian_books','native_guardian_equipment')")}
            ready=len(names)==8
            if ready:ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('native_new_neidan','native_guardian_jade','native_guardian_jade_luck','native_guardian_stones','native_guardian_special_slot')").fetchone()[0]==5
            if ready:ready='tier' in {r[1] for r in db.execute('PRAGMA table_info(native_guardian_books)')}
            if ready:ready='365' in db.execute("SELECT sql FROM sqlite_master WHERE name='native_guardian_books'").fetchone()[0]
            if ready:
                columns={r[1] for r in db.execute('PRAGMA table_info(summons)')}
                required={'formula_version','direction','growth_base','initial_hp_adj','initial_mp_adj','initial_attack_adj',
                          'initial_speed_adj','high_growth','longgu','rebirth','ascension','sanctification','honesty','amity','endurance'}
                ready=required<=columns
            if ready:
                ready=(db.execute("SELECT 1 FROM sqlite_sequence WHERE name='summons' AND seq>=?", (SUMMON_ID_MIN - 1,)).fetchone()
                       and not db.execute('SELECT 1 FROM summons WHERE id<? OR (character_id=? AND formula_version=0) LIMIT 1',
                                          (SUMMON_ID_MIN, progression.cid)).fetchone())
        if ready:return
        with progression.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS summons (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  character_id INTEGER NOT NULL REFERENCES characters(id),
                  grant_key TEXT NOT NULL, type INTEGER NOT NULL, name TEXT NOT NULL,
                  level INTEGER NOT NULL DEFAULT 1, experience INTEGER NOT NULL DEFAULT 0,
                  cor INTEGER NOT NULL DEFAULT 10, mag INTEGER NOT NULL DEFAULT 10,
                  strength INTEGER NOT NULL DEFAULT 10, speed INTEGER NOT NULL DEFAULT 10,
                  points INTEGER NOT NULL DEFAULT 5, hp INTEGER NOT NULL DEFAULT 100,
                  mp INTEGER NOT NULL DEFAULT 40, fight INTEGER NOT NULL DEFAULT 0,
                  show INTEGER NOT NULL DEFAULT 0, UNIQUE(character_id,grant_key));
                CREATE UNIQUE INDEX IF NOT EXISTS one_fighting_summon
                  ON summons(character_id) WHERE fight=1;
                CREATE UNIQUE INDEX IF NOT EXISTS one_showing_summon
                  ON summons(character_id) WHERE show=1;
                CREATE TABLE IF NOT EXISTS summon_battle_rewards (
                  character_id INTEGER NOT NULL, battle_id TEXT NOT NULL,
                  summon_id INTEGER NOT NULL REFERENCES summons(id),
                  PRIMARY KEY(character_id,battle_id,summon_id));
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns={row[1] for row in db.execute('PRAGMA table_info(summons)')}
            defaults={'formula_version':0,'direction':0,'growth_base':0,'initial_hp_adj':0,'initial_mp_adj':0,
                      'initial_attack_adj':0,'initial_speed_adj':0,'high_growth':0,'longgu':0,
                      'rebirth':0,'ascension':0,'sanctification':0,'honesty':100,'amity':0,'endurance':100}
            for name,value in defaults.items():
                if name not in columns:db.execute(f'ALTER TABLE summons ADD COLUMN {name} INTEGER NOT NULL DEFAULT {value}')
            migrate_native_ids(db)
            from native946_guardian_ui import schema as ui_schema
            ui_schema(db)
            from native946_skillbooks import schema as book_schema
            book_schema(db)
            from native946_guardian_equipment import schema as guardian_gear_schema
            guardian_gear_schema(db)
            from native946_neidan import schema as new_neidan_schema
            new_neidan_schema(db)
            from native946_guardian_jade import schema as jade_schema
            jade_schema(db)
            for saved in db.execute('SELECT * FROM summons WHERE character_id=? AND formula_version=0',
                                    (self.progression.cid,)).fetchall():
                row=dict(saved);old_hpmax=70+row['cor']*3+row['level']*5;old_mpmax=20+row['mag']*2+row['level']*3
                parameters=guardian_defaults(row['type'])
                db.execute('UPDATE summons SET '+','.join(name+'=?' for name in parameters)+
                           ',formula_version=1,amity=100 WHERE id=?',(*parameters.values(),row['id']))
                updated=self.stats(self._owned(db,row['id']))
                db.execute('UPDATE summons SET hp=?,mp=? WHERE id=?',
                           (min(updated['hpmax'],max(0,row['hp'])*updated['hpmax']//max(1,old_hpmax)),
                            min(updated['mpmax'],max(0,row['mp'])*updated['mpmax']//max(1,old_mpmax)),row['id']))

    def _owned(self, db, sid):
        row = db.execute('SELECT * FROM summons WHERE id=? AND character_id=? AND '+guardian_chamber.AVAILABLE,
                         (sid, self.progression.cid)).fetchone()
        if row is None:
            raise ValueError('守护不属于当前角色。')
        return dict(row)

    def stats(self,row):
        row = dict(row)
        from native946_guardian_equipment import effects as gear_effects, ornament_appearance
        from native946_guardian_score import total
        with self.progression.transaction(write=False) as db:
            row['score']=total(db,row)
            gear=gear_effects(db,self.progression.cid,row['id'])
            row['show_type'],row['weapon_color']=ornament_appearance(db,self.progression.cid,row['id'],row['type'])
        for key in ATTRS:row[key]+=gear.pop(key,0)
        row.update(guardian_stats(row))
        row['unmodified_hpmax']=row['hpmax']
        from native946_training import effects
        with self.progression.transaction(write=False) as db:
            bonus=effects(db,self.progression.cid)
        row['hpmax']+=bonus.get('SummonHpValue',0)
        row['initiative']+=bonus.get('SummonDodAll',0)
        row.update(defense=row['cor']//5,expnext=LEVEL_EXPERIENCE_FACTOR*row['level'])
        from native946_neidan_legacy import raw_list
        from native946_neidan import attributes as new_neidan_attributes, installed
        with self.progression.transaction(write=False) as db:
            row['neidan']=raw_list(db,row['id'])
            new_neidan_attributes(row,installed(db,row['id']))
        from native946_skillbooks import effective_books,effects as book_effects
        with self.progression.transaction(write=False) as db:row['learned_books']=effective_books(db,row)
        from native946_guardian_innate import records as innate_records
        from native946_innate_training import jing_books, trained_records, visible_innate
        with self.progression.transaction(write=False) as db:
            row['innate_training']=trained_records(db,row['id'],row['level'])
            row['jing_books']=jing_books(db,row['id'])
            row['innate_skills']=innate_records(row)
            row['innate_skills']=visible_innate(db,row)
        hidden_books={entry['base'] for entry in row['jing_books']}
        row['learned_books']=[book for book in row['learned_books'] if book['skill'] not in hidden_books]
        from native946_innate_effects import stats as innate_stats
        row=innate_stats(row)
        for key,value in book_effects(row['learned_books']).items():row[key]=row.get(key,0)+value
        from native946_mall_guardians import appearance
        with self.progression.transaction(write=False) as db:row.update(appearance(db,self.progression.cid,row['id']))
        from native946_guardian_ui import marks,mark_wire
        with self.progression.transaction(write=False) as db:
            row['mark']=mark_wire(marks(db,row['id']))
            from native946_guardian_jade import effects as jade_effects
            jade_effects(db,row)
            from native946_role_arts import effects as role_effects
            for key,value in role_effects(db,self.progression.cid,row['id']).items():
                row[key]=row.get(key,0)+value
            for key,value in gear.items():
                row[key]=row.get(key,0)+value
            row['hpmax']+=row['unmodified_hpmax']*row.get('guardian_fabao_hp',0)//1000
            row['mpmax']+=row['mpmax']*row.get('RoleMpPercent',0)//1000
            row['initiative']+=row['initiative']*row.get('RoleSpeedPercent',0)//1000
        row['hp'] = min(row['hp'], row['hpmax'])
        row['mp'] = min(row['mp'], row['mpmax'])
        return row

    def snapshot(self):
        with self.progression.transaction(write=False) as db:
            from native946_inventory_locks import records
            locks=records(db,self.progression.cid,'summon')
            return [dict(self.stats(row),**locks.get(row['id'],dict(lock=0,lock_block_time=0))) for row in db.execute(
                'SELECT * FROM summons WHERE character_id=? AND '+guardian_chamber.AVAILABLE+' ORDER BY id', (self.progression.cid,))]

    def get(self, sid):
        with self.progression.transaction(write=False) as db:
            from native946_inventory_locks import records
            return dict(self.stats(self._owned(db, sid)),**records(db,self.progression.cid,'summon').get(sid,dict(lock=0,lock_block_time=0)))

    def active(self):
        return next((s for s in self.snapshot() if s['fight']), None)

    def carry_limit(self):
        from native946_mall_bags import summon_limit
        with self.progression.transaction(write=False) as db:return summon_limit(db,self.progression.cid)

    def claim_starter(self):
        with self.progression.transaction() as db:
            existing = db.execute('SELECT id FROM summons WHERE character_id=? AND grant_key=?',
                                  (self.progression.cid, 'starter')).fetchone()
            if existing:
                return existing[0]
            if guardian_chamber.carried_count(db, self.progression.cid) >= self.carry_limit():
                raise ValueError('守护携带栏已满。')
            if db.execute("SELECT seq FROM sqlite_sequence WHERE name='summons'").fetchone()[0] >= SUMMON_ID_MAX:
                raise ValueError('本机守护编号已用完。')
            fight = not db.execute('SELECT 1 FROM summons WHERE character_id=? AND fight=1',
                                   (self.progression.cid,)).fetchone()
            parameters=guardian_defaults(STARTER_TYPE)
            sid=db.execute('INSERT INTO summons(character_id,grant_key,type,name,hp,mp,fight,cor,mag,strength,speed,points,formula_version,'+
                           ','.join(parameters)+") VALUES(?,'starter',?,'猴儿精',1,1,?,1,1,1,1,4,1,"+
                           ','.join('?' for _ in parameters)+')',
                           (self.progression.cid,STARTER_TYPE,int(fight),*parameters.values())).lastrowid
            row=self.stats(self._owned(db,sid))
            db.execute('UPDATE summons SET hp=?,mp=? WHERE id=?',(row['hpmax'],row['mpmax'],sid))
            return sid

    def status(self, sid, operation):
        if operation not in (1, 2, 3, 4):
            raise ValueError('当前支持守护参战、休息、展示和隐藏。')
        with self.progression.transaction() as db:
            self._owned(db, sid)
            column = 'fight' if operation in (1, 2) else 'show'
            value = int(operation in (1, 3))
            if value:
                db.execute(f'UPDATE summons SET {column}=0 WHERE character_id=?', (self.progression.cid,))
            db.execute(f'UPDATE summons SET {column}=? WHERE id=?', (value, sid))

    def release(self, sid):
        from native946_inventory_locks import records

        cid = self.progression.cid
        with self.progression.transaction() as db:
            pet = self._owned(db, sid)
            if sid in records(db, cid, 'summon'):
                raise ValueError('加锁的守护无法放生，请先解锁。')
            if pet['type'] in PROTECTED_RELEASE_TYPES:
                raise ValueError('该守护无法放生。')
            if (db.execute('SELECT 1 FROM native_guardian_equipment WHERE summon_id=?', (sid,)).fetchone()
                or db.execute('SELECT 1 FROM native_guardian_jade WHERE summon_id=?', (sid,)).fetchone()):
                raise ValueError('请先卸下守护的装备和宝玉，再进行放生。')

            record = self.list_record(self.stats(pet))
            # Keep the acquisition key and historical sale/settlement references.
            db.execute('INSERT INTO native_guardian_releases VALUES(?,?)', (sid, int(time())))
            db.execute('UPDATE summons SET fight=0,show=0 WHERE id=?', (sid,))
            return record

    def acquire(self, kind, grant_key, *, level=1):
        """One owned native species per server-authorized acquisition source."""
        from native946_wilderness import DATA, carry_rank
        from secrets import randbelow, choice
        native=DATA['species'].get(str(kind))
        if native is None:raise ValueError('该守护未在当前版本开放。')
        if not isinstance(grant_key,str) or not grant_key or len(grant_key)>160:
            raise ValueError('守护取得来源无效。')
        if type(level) is not int or not 1<=level<=180:raise ValueError('守护等级无效。')
        with self.progression.transaction() as db:
            previous=db.execute('SELECT id FROM summons WHERE character_id=? AND grant_key=?',
                                (self.progression.cid,grant_key)).fetchone()
            if previous:return previous[0]
            if guardian_chamber.has_sale(db,self.progression.cid,grant_key):raise ValueError('此来源的守护已经转售。')
            hero=self.progression._snapshot(db)
            if carry_rank(hero)<native['take_grade_num']:raise ValueError('尚未达到该守护的携带等级。')
            if level > guardian_level_cap(hero['level']):
                raise ValueError('该守护等级超过当前人物可携带的守护等级上限。')
            if guardian_chamber.carried_count(db, self.progression.cid) >= self.carry_limit():
                raise ValueError('守护携带栏已满，请先整理携带栏。')
            if db.execute("SELECT seq FROM sqlite_sequence WHERE name='summons'").fetchone()[0]>=SUMMON_ID_MAX:
                raise ValueError('本机守护编号已用完。')
            params=guardian_defaults(kind,choice(native['direction']))
            params['growth_base']=(native['grow_min']+randbelow(native['grow_max']-native['grow_min']+1))*10
            for attr,field in [('hp','hp'),('mp','mp'),('attack','att'),('speed','dod')]:
                low,high=native[field+'_min'],native[field+'_max']
                params['initial_'+attr+'_adj']=low+randbelow(high-low+1)
            fighting=not db.execute('SELECT 1 FROM summons WHERE character_id=? AND fight=1',(self.progression.cid,)).fetchone()
            columns=['character_id','grant_key','type','name','level','hp','mp','fight','cor','mag','strength','speed','points','formula_version',*params]
            values=[self.progression.cid,grant_key,kind,native['name'],level,1,1,int(fighting),level,level,level,level,4*level,1,*params.values()]
            sid=db.execute('INSERT INTO summons('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',values).lastrowid
            row=self.stats(self._owned(db,sid))
            db.execute('UPDATE summons SET hp=?,mp=? WHERE id=?',(row['hpmax'],row['mpmax'],sid))
            return sid

    def rename(self, sid, name):
        if (not isinstance(name, str) or not 1 <= len(name.encode('gb18030')) <= 12
                or any(not (c.isalnum() or '\u4e00' <= c <= '\u9fff') for c in name)):
            raise ValueError('守护名仅限汉字、字母和数字，最多6个汉字或12个字母、数字。')
        with self.progression.transaction() as db:
            self._owned(db, sid)
            db.execute('UPDATE summons SET name=? WHERE id=?', (name, sid))

    def allocate(self, sid, increments):
        if (len(increments) != 4 or any(type(n) is not int or n < 0 for n in increments)
                or not sum(increments)):
            raise ValueError('守护加点需要四项非负整数增量。')
        with self.progression.transaction() as db:
            row = self._owned(db, sid)
            if sum(increments) > row['points']:
                raise ValueError('守护潜能点不足。')
            db.execute('UPDATE summons SET cor=cor+?,mag=mag+?,strength=strength+?,speed=speed+?,points=points-? WHERE id=?',
                       (*increments, sum(increments), sid))

    def _owner_level(self, db):
        return db.execute('SELECT level FROM characters WHERE id=?', (self.progression.cid,)).fetchone()[0]

    def _apply_experience(self, db, row, experience, cap):
        level = row['level']
        while level < cap and experience >= LEVEL_EXPERIENCE_FACTOR * level:
            experience -= LEVEL_EXPERIENCE_FACTOR * level
            level += 1
        gained = level - row['level']
        sid = row['id']
        if gained:
            db.execute('UPDATE summons SET experience=?,level=?,points=points+?,'
                       'cor=cor+?,mag=mag+?,strength=strength+?,speed=speed+? WHERE id=?',
                       (experience, level, 4 * gained, gained, gained, gained, gained, sid))
            updated = self.stats(self._owned(db, sid))
            db.execute('UPDATE summons SET hp=?,mp=? WHERE id=?', (updated['hpmax'], updated['mpmax'], sid))
        elif experience != row['experience']:
            db.execute('UPDATE summons SET experience=? WHERE id=?', (experience, sid))

    def add_experience(self, db, sid, amount):
        row = self._owned(db, sid)
        self._apply_experience(db, row, row['experience'] + amount, guardian_level_cap(self._owner_level(db), row))

    def _restore_stage_cap(self, db, row, cap):
        excess = row['level'] - cap
        refund = LEVEL_EXPERIENCE_FACTOR * excess * (cap + row['level'] - 1) // 2
        attrs = [max(cap, row[key] - excess) for key in ATTRS]
        points = row['points'] - 4 * excess
        if points < 0:
            # The excess points were already allocated. Return the remaining valid budget to potential.
            points = max(0, points + sum(value - cap for value in attrs))
            attrs = [cap] * len(ATTRS)
        db.execute('UPDATE summons SET level=?,experience=experience+?,points=?,cor=?,mag=?,strength=?,speed=? WHERE id=?',
                   (cap, refund, points, *attrs, row['id']))
        updated = self.stats(self._owned(db, row['id']))
        db.execute('UPDATE summons SET hp=?,mp=? WHERE id=?', (updated['hp'], updated['mp'], row['id']))

    def reconcile_levels(self):
        with self.progression.transaction() as db:
            owner_level = self._owner_level(db)
            rows = db.execute('SELECT * FROM summons WHERE character_id=? '
                              'AND (level>? OR (level<? AND experience>=?*level)) AND '+guardian_chamber.AVAILABLE,
                              (self.progression.cid, STAGE_LEVEL_CAPS[0], owner_level + 10, LEVEL_EXPERIENCE_FACTOR)).fetchall()
            changed = False
            for row in rows:
                stage_cap = guardian_stage_level_cap(row)
                cap = min(stage_cap, owner_level + 10)
                if row['level'] > stage_cap:
                    self._restore_stage_cap(db, row, stage_cap)
                    changed = True
                elif row['level'] < cap and row['experience'] >= LEVEL_EXPERIENCE_FACTOR * row['level']:
                    self._apply_experience(db, row, row['experience'], cap)
                    changed = True
            return changed

    def level_up(self, sid):
        with self.progression.transaction() as db:
            row = self._owned(db, sid)
            cap = guardian_level_cap(self._owner_level(db), row)
            if row['level'] >= cap:
                raise ValueError(f'守护已达到当前等级上限（{cap}级），已存经验保留。')
            if row['experience'] < LEVEL_EXPERIENCE_FACTOR * row['level']:
                raise ValueError('守护升级经验不足。')
            self._apply_experience(db, row, row['experience'], row['level'] + 1)

    def rest(self):
        with self.progression.transaction() as db:
            for row in db.execute('SELECT * FROM summons WHERE character_id=? AND '+guardian_chamber.AVAILABLE, (self.progression.cid,)).fetchall():
                s = self.stats(row)
                db.execute('UPDATE summons SET hp=?,mp=? WHERE id=?', (s['hpmax'], s['mpmax'], s['id']))

    def settle(self, sid, battle_id, hp, mp, victory, experience=25, *, stunt_experience=None,
               neidan_experience=None):
        with self.progression.transaction() as db:
            row = self.stats(self._owned(db, sid))
            inserted = db.execute('INSERT OR IGNORE INTO summon_battle_rewards VALUES(?,?,?)',
                                  (self.progression.cid, battle_id, sid)).rowcount
            if inserted:
                db.execute('UPDATE summons SET hp=?,mp=?,amity=min(50000,amity+1) WHERE id=?',
                           (max(0, min(hp, row['hpmax'])), max(0, min(mp, row['mpmax'])), sid))
                from native946_neidan import NewNeidan,battle_energy_cost
                from native946_neidan_legacy import LegacyNeidan,grow
                pill_experience = experience if neidan_experience is None else neidan_experience
                pills=LegacyNeidan(self)
                for pill in row['neidan']:
                    updated=grow(pill,pill_experience) if victory else dict(pill)
                    # Charge participation, regardless of whether the skill
                    # procced. Keep the cost in the same idempotent transaction
                    # as the guardian's battle receipt and experience.
                    updated['energy']=max(0,pill['energy']-battle_energy_cost(0))
                    pills._save(db,sid,updated)
                NewNeidan(self).settle(db,sid,victory,pill_experience)
                if victory:
                    self.add_experience(db, sid, experience)
                    from native946_skillbooks import SkillBooks
                    SkillBooks(self).add_experience(db, sid, experience if stunt_experience is None else stunt_experience)

    def set_vitals(self, sid, hp, mp):
        with self.progression.transaction() as db:
            s = self.stats(self._owned(db, sid))
            db.execute('UPDATE summons SET hp=?,mp=? WHERE id=?',
                       (max(0, min(hp, s['hpmax'])), max(0, min(mp, s['mpmax'])), sid))

    def quick_recover(self, sid, resource):
        from native946_progression import quick_medicine_recovery
        with self.progression.transaction() as db:
            subject = self.stats(self._owned(db, sid))
            changes = quick_medicine_recovery(db, self.progression.cid, subject, resource)
            if changes:
                db.execute('UPDATE summons SET hp=?,mp=? WHERE id=?',
                           (changes.get('hp', subject['hp']), changes.get('mp', subject['mp']), sid))
            return changes

    def use_item(self, sid, item_id):
        """Use items from an open bag atomically on an owned guardian."""
        from native946_mall_bags import pages
        from native946_inventory_locks import require_unlocked
        from native946_progression import medicine_effects, medicine_recovery
        with self.progression.transaction() as db:
            s=self.stats(self._owned(db,sid))
            item=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',
                            (item_id,self.progression.cid)).fetchone()
            from native946_skillbooks import BOOKS,SkillBooks
            if item is not None and item['type'] in BOOKS:
                SkillBooks(self).learn(sid,item_id)
                return
            if item is not None and item['page'] not in pages(db,self.progression.cid):
                raise ValueError('物品不在已开启的背包中。')
            if item is not None and item['type'] in (6052,6024):
                from native946_neidan_legacy import LegacyNeidan
                LegacyNeidan(self).use(sid,item_id)
                return
            if item is not None and item['type']==47100:
                from native946_mall_uses import consume
                expires=db.execute('SELECT expires FROM cosmetic_expiry WHERE item_id=?',(item_id,)).fetchone()
                if expires and 0<expires[0]<=time():raise ValueError('兽元丹已到期。')
                from native946_neidan import MAX_SUMMON_EXPERIENCE
                if s['experience']+200000>MAX_SUMMON_EXPERIENCE:raise ValueError('请先消化当前守护经验。')
                consume(db,item)
                self.add_experience(db,sid,200000)
                return
            from native946_neidan import ENERGY_ITEM_TYPE,ITEM_TYPES,NewNeidan
            if item is not None and (item['type'] in ITEM_TYPES or item['type']==ENERGY_ITEM_TYPE):
                NewNeidan(self).use(db,s,item)
                return
            effects=medicine_effects(item['type'],outside_combat=True) if item else ()
            if not effects:raise ValueError('该物品不能给守护使用。')
            require_unlocked(db,self.progression.cid,item_id)
            changes=medicine_recovery(s,effects)
            if not changes:raise ValueError('守护当前状态已满，无需消耗物品。')
            db.execute('UPDATE summons SET hp=?,mp=? WHERE id=?',(changes.get('hp',s['hp']),changes.get('mp',s['mp']),sid))
            if item['amount']==1:db.execute('DELETE FROM inventory WHERE id=?',(item_id,))
            else:db.execute('UPDATE inventory SET amount=amount-1 WHERE id=?',(item_id,))

    def resistance_packet(self, sid):
        s=self.get(sid)
        from native946_guardian_ui import carry_label
        from native946_wilderness import DATA as WILDERNESS
        native=species(s['type'])
        # Current-client help 0x83a21957: +1% combo per 4000 amity,
        # +1% fatal and +1 combo count per 7000; percentage wire units are per mille.
        values=dict(Name=s['name'],Amity=s['amity'],Grow=s['growth'],ShowGrow=s['growth']*10,DefAll=s['defense'],
                    NoTrade=1,Kind=WILDERNESS['kind_names'][str(native['kind'])],CanTakeGrade=carry_label(native['take_grade_num']),
                    Fly=s['ascension'],Rei=s['rebirth'],Feng=s['sanctification'],LongGu=s['longgu'],
                    DoubleRate=(s['amity']//4000)*10,FatalRate=(s['amity']//7000)*10,
                    DoubleCount=s['amity']//7000)
        values.update(AttValue=s.get('book_hit',0),DodValue=s.get('book_dodge',0),
                      CruelRate=s.get('book_cruel',0)*10+s.get('cruel_basis_points',0)//10,FanjiRate=s.get('book_counter',0)*10,
                      FanjiCount=s.get('book_counter_count',0))
        values['FatalRate']+=s.get('book_fatal',0)*10
        values.update(ReSeal=s.get('book_reseal',0)*10,ReDisorder=s.get('book_redisorder',0)*10,ReFaint=s.get('book_refaint',0)*10)
        return packet(717,['number','summon_resist'],[sid,values])

    @staticmethod
    def list_record(s):
        native=species(s['type'])
        return dict(Id=s['id'], SId=str(s['id']), Mark=s.get('mark','{}'), Type=s['type'], ShowType=s['show_type'],
                    Name=s['name'], Grade=s['level'], Race=native['race'], Kind=native['kind'], ValueType=s['direction'],
                    Rei=s['rebirth'], Fly=s['ascension'], Feng=s['sanctification'],
                    Fight=s['fight'], Show=s['show'], CanTrade=0,Lock=s.get('lock',0),Wildness=int(s.get('grant_key','').startswith('capture:')),
                    Color1=s.get('color1',0),Color2=s.get('color2',0),Color3=s.get('color3',0),Halo=s.get('halo',0),FootHalo=s.get('foot_halo',0),
                    LockBlockTime=s.get('lock_block_time',0),WeaponColor=s['weapon_color'])

    @staticmethod
    def info_record(s):
        return dict(Type=s['type'], ShowType=s['show_type'], Name=s['name'], Grade=s['level'],
                    Race=species(s['type'])['race'], Exp=s['experience'], ExpNext=s['expnext'], Point=s['points'],
                    Cor=s['cor'], Mag=s['mag'], Str=s['strength'], Spe=s['speed'],
                    Hp=s['hp'], HpMin=s['hpmax'], HpMax=s['hpmax'],
                    Mp=s['mp'], MpMin=s['mpmax'], MpMax=s['mpmax'],
                    AttAll=s['attack'], DodAll=s['initiative'], BaseAttAll=s['base_values'][2],
                    BaseDodAll=s['base_values'][3], BaseHpMax=s['base_values'][0], BaseMpMax=s['base_values'][1],
                    Grow=s['growth'], RealGrow=s['growth'], Honest=s['honesty'], SummonEndure=s['endurance'],
                    IniHpAdj=s['initial_hp_adj'], IniMpAdj=s['initial_mp_adj'],
                    IniAttAdj=s['initial_attack_adj'], IniDodAdj=s['initial_speed_adj'],
                    Rei=s['rebirth'],Fly=s['ascension'],Feng=s['sanctification'],Lock=s.get('lock',0),SummonScore=s['score'])

    def info_packets(self, sid, *, refresh_appearance=False):
        from native946_guardian_equipment import equipment_page
        from native946_guardian_score import highest_score
        from native946_progression import DATA as PROGRESSION_DATA
        with self.progression.transaction(write=False) as db:
            s = self.get(sid)
            score = highest_score(db,self.progression.cid)
        # The native list update carries weapon color. Full stats then redraw the open panel.
        return (packet(711, ['number', 'number'], [sid,equipment_page(sid)]),
                *((packet(714,['summon_list','number'],[self.list_record(s),3]),) if refresh_appearance else ()),
                packet(706, ['number', 'summon'], [sid, self.info_record(s)]),
                *((numbers(707,sid),) if s['points']>0 else ()),
                numbers(695,int(PROGRESSION_DATA['attributes']['SummonScore']),score))

    def base_packet(self, sid):
        s = self.get(sid)
        native=species(s['type'])
        names={1:'根骨方向',2:'灵性方向',3:'力量方向',4:'敏捷方向',5:'根骨灵性',6:'根骨力量',7:'根骨敏捷',8:'灵性力量',9:'灵性敏捷',10:'力量敏捷'}
        data=dict(Name=s['name'],ValueTypeName=names[s['direction']],Amity=s['amity'])
        for i,(name,field,key) in enumerate([('Hp','hp','hp_max'),('Mp','mp','mp_max'),
                                            ('Att','attack','att_max'),('Dod','speed','dod_max')]):
            base=s['base_values'][i];value=s['initial_'+field+'_adj'];maximum=native[key]
            data.update({'Base'+name:int(base*value/1000),'Base'+name+'Max':int(base*maximum/1000),
                         'Ini'+name+'Adj':value,'Ini'+name+'AdjMax':maximum})
        return packet(708, ['number', 'summon_baseinfo'], [sid, data])

    def sync(self, pets=None):
        if pets is None:pets = self.snapshot()
        # Native init() updates the status HUD before inserting a fighting
        # guardian into its lookup table. Seed resting entries and full stats
        # first, then enable battle/display state through their native packets.
        records = [dict(self.list_record(s), Fight=0, Show=0) for s in pets]
        from native946_guardian_equipment import equipment_page
        from native946_progression import DATA as PROGRESSION_DATA
        score = max((s['score'] for s in pets), default=0)
        return (*(packet(711,['number','number'],[s['id'],equipment_page(s['id'])]) for s in pets),
                packet(713, [('array', 'summon_list')], [records]),
                *(packet(706,['number','summon'],[s['id'],self.info_record(s)]) for s in pets),
                *(numbers(707,s['id']) for s in pets if s['points']>0),
                *(numbers(718, s['id'], s['fight']) for s in pets),
                *(numbers(719, s['id'], s['show']) for s in pets),
                *(self.neidan_packet(s['id']) for s in pets),
                *(self.stunt_packet(s['id']) for s in pets),
                numbers(695,int(PROGRESSION_DATA['attributes']['SummonScore']),score))

    def stunt_packet(self,sid):
        from native946_skillbooks import SkillBooks
        return SkillBooks(self).packet(sid)

    def neidan_packet(self,sid):
        from native946_neidan_legacy import LegacyNeidan
        from native946_neidan import installed,records
        with self.progression.transaction(write=False) as db:
            legacy=LegacyNeidan(self).records(sid)
            rows=records(installed(db,sid))
        return packet(716,['number',('array','neidan_info')],[sid,legacy+rows])
