from native946_codec import packet, text_notice
from native946_login import MessageReader


WIRE_VERSION = 4
REQUESTS = {754, 756, 757, 758, 763, 768, 769, 770, 771, 772, 773, 774}
WRITES = REQUESTS - {773, 774}
JING_BOOKS={46584:(301,4146,3),46585:(302,4147,2),46586:(303,4148,5),46587:(304,4149,4)}
EXTRA_BOOKS={47099:(4542,100,3),47589:(4626,0,0),47590:(4627,0,0),47591:(4628,0,0),47592:(4629,0,0),
             47593:(4630,0,0),47594:(4631,0,0)}


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS native_innate_training(
          summon_id INTEGER NOT NULL REFERENCES summons(id) ON DELETE CASCADE,
          skill INTEGER NOT NULL, level INTEGER NOT NULL DEFAULT 1,
          experience INTEGER NOT NULL DEFAULT 0, rebirth INTEGER NOT NULL DEFAULT 0,
          ascension INTEGER NOT NULL DEFAULT 0, sanctification INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(summon_id,skill))""")
    db.execute("""CREATE TABLE IF NOT EXISTS native_innate_suppressed(
          summon_id INTEGER NOT NULL REFERENCES summons(id) ON DELETE CASCADE,
          skill INTEGER NOT NULL, PRIMARY KEY(summon_id,skill))""")
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_jing_books (
        summon_id INTEGER NOT NULL REFERENCES summons(id) ON DELETE CASCADE,
        base INTEGER NOT NULL, skill INTEGER NOT NULL, position INTEGER NOT NULL,
        PRIMARY KEY(summon_id,skill),UNIQUE(summon_id,base))''')


def jing_books(db,sid):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='native_guardian_jing_books'").fetchone():return []
    return [dict(row) for row in db.execute('SELECT base,skill,position FROM native_guardian_jing_books WHERE summon_id=? ORDER BY position',(sid,))]


def use_jing_book(game,sid,iid):
    from native946_growth import species
    from native946_mall_uses import consume
    from native946_refinement import Refinement
    if game.battle:raise ValueError('请先完成战斗再进行特技升境。')
    p=game.progression
    with p.transaction() as db:
        schema(db)
        pet=game.summons.get(sid)
        item=Refinement(game).item(db,iid)
        if item['type'] in EXTRA_BOOKS:
            skill,required,race=EXTRA_BOOKS[item['type']]
            base=-skill
        else:
            base,skill,race=JING_BOOKS[item['type']]
            required=0
        if race and species(pet['type'])['race']!=race:raise ValueError('守护种族不符合本天演图条件。')
        if pet['level']<required:raise ValueError('守护尚未达到天演图要求的等级。')
        visible=pet.get('innate_skills',[])
        if any(row['stunt_id']==skill for row in visible):raise ValueError('守护已拥有该境特技。')
        record=next((row for row in visible if row['stunt_id']==base),None)
        if base<0:
            from native946_skillbooks import empty_positions,raw_list
            slots=empty_positions(pet,raw_list(db,sid)+[j for j in jing_books(db,sid) if j['base']<0])
            slot=next((s for s in slots if s['level']==1 and s['can_do']),None)
            if slot is None:raise ValueError('守护没有空闲的一阶打造特技格。')
            position=slot['position'];level=1;experience=0;stages=(0,0,0)
        elif record:
            level=max(1,record['grade']);position=record['position'];experience=max(0,record['stuntexp'])
            stages=(max(0,record['rei']),record['fly'],record['feng'])
        else:
            book=next((row for row in pet.get('learned_books',[]) if row['skill']==base),None)
            if book is None:raise ValueError('请先学习对应的普通特技。')
            level=max(1,book['level']);position=book['position'];experience=book['experience']
            stages=(book['rebirth'],0,0)
        gained,experience=divmod(experience,100)
        # Keep the ordinary record in its original slot. The upgrade owns all effects while installed.
        db.execute('INSERT INTO native_guardian_jing_books VALUES(?,?,?,?)',(sid,base,skill,position))
        db.execute('INSERT INTO native_innate_training(summon_id,skill,level,experience,rebirth,ascension,sanctification) VALUES(?,?,?,?,?,?,?)',
                   (sid,skill,level+gained,experience,*stages))
        db.execute('DELETE FROM native_innate_suppressed WHERE summon_id=? AND skill=?',(sid,skill))
        consume(db,item)
    return game.defer_packets(lambda:(*p.item_packets(),game.summons.stunt_packet(sid),*game.summons.info_packets(sid)))


def visible_innate(db, row):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_innate_suppressed'").fetchone():
        return row.get('innate_skills', [])
    suppressed = {r[0] for r in db.execute("SELECT skill FROM native_innate_suppressed WHERE summon_id=?", (row['id'],))}
    return [entry for entry in row.get('innate_skills', []) if entry['stunt_id'] not in suppressed]


def trained_records(db, sid):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_innate_training'").fetchone():
        return {}
    return {row['skill']: dict(row) for row in db.execute(
        'SELECT skill,level,experience,rebirth,ascension,sanctification '
        'FROM native_innate_training WHERE summon_id=?', (sid,))}


class InnateTraining:
    def __init__(self, game):
        self.g = game
        self.s = game.summons
        self.p = game.progression

    def reply(self, sid, text):
        return (*self.s.sync(), self.s.stunt_packet(sid), text_notice(text))

    def known(self, pet, skill):
        if any(entry['stunt_id'] == skill for entry in pet.get('innate_skills', [])):
            return True
        return any(row['skill'] == skill for row in pet.get('learned_books', []))

    def add_skill_exp(self, db, sid, skill, amount):
        db.execute("INSERT INTO native_innate_training(summon_id,skill,experience) VALUES(?,?,?) "
                   "ON CONFLICT(summon_id,skill) DO UPDATE SET experience=experience+excluded.experience",
                   (sid, skill, amount))
        db.execute("UPDATE native_innate_training SET level=level+experience/100,experience=experience%100 "
                   "WHERE summon_id=? AND skill=?", (sid, skill))

    def stage(self, db, sid, column, level, experience, maximum=1):
        current = db.execute(f"SELECT {column} FROM summons WHERE id=?", (sid,)).fetchone()[0]
        if current >= maximum:
            raise ValueError('该守护已经达到当前培养上限。')
        pet = self.s._owned(db, sid)
        if pet['level'] < level or pet['experience'] < experience:
            raise ValueError('守护等级或经验不足，本次未执行。')
        db.execute(f"UPDATE summons SET {column}={column}+1,level=1,experience=0 WHERE id=?", (sid,))

    def skill_stage(self, db, sid, skill, column, level, experience):
        row = db.execute(f"SELECT {column} FROM native_innate_training WHERE summon_id=? AND skill=?",
                         (sid, skill)).fetchone()
        if row and row[0] >= 1:
            raise ValueError('该技能已经达到当前培养上限。')
        pet = self.s._owned(db, sid)
        if pet['level'] < level or pet['experience'] < experience:
            raise ValueError('守护等级或经验不足，本次未执行。')
        db.execute("INSERT INTO native_innate_training(summon_id,skill," + column + ") VALUES(?,?,1) "
                   "ON CONFLICT(summon_id,skill) DO UPDATE SET " + column + "=1", (sid, skill))
        db.execute("UPDATE summons SET experience=experience-? WHERE id=?", (experience, sid))

    def receive(self, mid, reader):
        if mid not in REQUESTS:
            return None
        original = MessageReader(reader.data[reader.offset:])
        sid = reader.integer()
        skill = reader.integer() if mid not in (757, 758, 769) else None
        if mid in (773, 774):
            item_id, direction = reader.integer(), reader.integer()
        reader.finish()
        pet = self.s.get(sid)
        if mid in (773, 774):
            # The original panel moves stunt experience into or out of item 6279.
            # It freezes its buttons until 724 confirms or declines the request.
            return (packet(724, ['number'] * 4, [skill, item_id, direction, 0]),
                    text_notice('千回百转丸的经验转出与注入尚未接通。道具和经验未扣除。'))
        if mid in (754, 756) and any(row['skill'] == skill for row in pet.get('learned_books', [])):
            from native946_skillbooks import SkillBooks
            return SkillBooks(self.s).receive(self.g, mid, original)
        with self.p.transaction() as db:
            schema(db)
            if skill is not None and not self.known(pet, skill) and mid != 763:
                raise ValueError('守护没有这个特技。')
            if mid == 754:
                if pet['experience'] < 500:
                    raise ValueError('守护经验不足500，不能转换。')
                amount = min(100, pet['experience'] // 5)
                db.execute("UPDATE summons SET experience=experience-? WHERE id=?", (amount, sid))
                self.add_skill_exp(db, sid, skill, amount)
                return self.reply(sid, f'已将{amount}点守护经验转换为{amount}点特技经验。')
            if mid == 756:
                db.execute("DELETE FROM native_innate_training WHERE summon_id=? AND skill=?", (sid, skill))
                if db.execute('DELETE FROM native_guardian_jing_books WHERE summon_id=? AND skill=?',(sid,skill)).rowcount:
                    return self.reply(sid,'已遗忘该天演图特技。')
                db.execute("INSERT OR IGNORE INTO native_innate_suppressed VALUES(?,?)", (sid, skill))
                return self.reply(sid, '已经遗忘该天生特技，可通过领悟重新学习。')
            if mid == 757:
                self.stage(db, sid, 'sanctification', 140, 20000, 1)
                return self.reply(sid, '守护封圣完成。')
            if mid == 769:
                self.stage(db, sid, 'rebirth', 100, 10000, 2)
                return self.reply(sid, '守护转生完成。')
            if mid == 758:
                self.stage(db, sid, 'ascension', 120, 20000, 1)
                return self.reply(sid, '守护飞升完成。')
            if mid == 763:
                from native946_guardian_innate import can_learn
                suppressed = db.execute("SELECT 1 FROM native_innate_suppressed WHERE summon_id=? AND skill=?",
                                        (sid, skill)).fetchone()
                if self.known(pet, skill) and not suppressed:
                    raise ValueError('守护已经学会此特技。')
                if not can_learn(pet, skill):
                    raise ValueError('该守护不能领悟这个特技。')
                db.execute("DELETE FROM native_innate_suppressed WHERE summon_id=? AND skill=?", (sid, skill))
                db.execute("INSERT OR REPLACE INTO native_innate_training(summon_id,skill,level,experience) VALUES(?,?,1,0)",
                           (sid, skill))
                return self.reply(sid, '领悟特技成功。')
            if mid == 768:
                db.execute("INSERT INTO native_innate_training(summon_id,skill,level,experience) VALUES(?,?,1,0) "
                           "ON CONFLICT(summon_id,skill) DO UPDATE SET level=1,experience=0", (sid, skill))
                return self.reply(sid, '特技转生完成，等级和经验已重置。')
            if mid == 770:
                self.skill_stage(db, sid, skill, 'sanctification', 50, 10000)
                return self.reply(sid, '特技封圣完成。')
            if mid == 771:
                self.skill_stage(db, sid, skill, 'ascension', 30, 5000)
                return self.reply(sid, '特技飞升完成。')
            if mid == 772:
                self.skill_stage(db, sid, skill, 'rebirth', 20, 2500)
                return self.reply(sid, '特技转生完成。')
        return None
