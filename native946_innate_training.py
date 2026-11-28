from native946_codec import packet, text_notice
from native946_login import MessageReader


WIRE_VERSION = 10
JING_PANEL = 2811  # s_summon_jinginfo_open_panel
REQUESTS = {754, 756, 757, 758, 763, 769, 770, 771, 772, 773, 774, JING_PANEL}
WRITES = REQUESTS - {773, 774, JING_PANEL}
REBIRTH_EXPERIENCE_COST = 2500  # Existing local service cost.
STAGE_FLAGS = ((0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 1, 0), (2, 1, 1))
STAGE_NAMES = ('0转', '1转', '2转', '飞升', '封圣')
SKILL_PROMOTIONS = {772: (0, REBIRTH_EXPERIENCE_COST), 771: (30, 5000), 770: (50, 10000)}
SKILL_PROMOTION_NOTICES = {772: '特技转生完成，等级和经验已重置。',
                           771: '特技飞升完成。', 770: '特技封圣完成。'}
# 本机规则：守护一转、二转、飞升、封圣后的起始等级。
GUARDIAN_START_LEVELS = (30, 45, 60, 75)
JING_BOOKS={46584:(301,4146,3),46585:(302,4147,2),46586:(303,4148,5),46587:(304,4149,4)}
EXTRA_BOOKS={47099:(4542,100,3),47589:(4626,0,0),47590:(4627,0,0),47591:(4628,0,0),47592:(4629,0,0),
             47593:(4630,0,0),47594:(4631,0,0)}


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS native_innate_training(
          summon_id INTEGER NOT NULL REFERENCES summons(id) ON DELETE CASCADE,
          skill INTEGER NOT NULL, level INTEGER NOT NULL DEFAULT 1,
          experience INTEGER NOT NULL DEFAULT 0, rebirth INTEGER NOT NULL DEFAULT 0,
          ascension INTEGER NOT NULL DEFAULT 0, sanctification INTEGER NOT NULL DEFAULT 0,
          position INTEGER,
          PRIMARY KEY(summon_id,skill))""")
    if not any(row[1] == 'position' for row in db.execute('PRAGMA table_info(native_innate_training)')):
        db.execute('ALTER TABLE native_innate_training ADD COLUMN position INTEGER')
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
            source = saved_training(db,sid,base)
            level=source.get('level',1);position=record['position'];experience=source.get('experience',0)
            stages=tuple(stage_flags(training_stage(source)).values())
        else:
            book=next((row for row in pet.get('learned_books',[]) if row['skill']==base),None)
            if book is None:raise ValueError('请先学习对应的普通特技。')
            from native946_skillbooks import raw_list

            source = next(row for row in raw_list(db,sid) if row['skill']==base)
            level=source['level'];position=source['position'];experience=source['experience']
            stages=tuple(stage_flags(training_stage(source)).values())
        # Keep the ordinary record in its original slot. The upgrade owns all effects while installed.
        db.execute('INSERT INTO native_guardian_jing_books VALUES(?,?,?,?)',(sid,base,skill,position))
        db.execute('INSERT INTO native_innate_training(summon_id,skill,level,experience,rebirth,ascension,sanctification) VALUES(?,?,?,?,?,?,?)',
                   (sid,skill,level,experience,*stages))
        db.execute('DELETE FROM native_innate_suppressed WHERE summon_id=? AND skill=?',(sid,skill))
        consume(db,item)
    return game.defer_packets(lambda:(*p.item_packets(),game.summons.stunt_packet(sid),*game.summons.info_packets(sid)))


def visible_innate(db, row):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_innate_suppressed'").fetchone():
        return row.get('innate_skills', [])
    suppressed = {r[0] for r in db.execute("SELECT skill FROM native_innate_suppressed WHERE summon_id=?", (row['id'],))}
    return [entry for entry in row.get('innate_skills', []) if entry['stunt_id'] not in suppressed]


def training_stage(row):
    if row.get('sanctification'):
        return 4
    if row.get('ascension'):
        return 3
    return min(2, max(0, row.get('rebirth', 0)))


def stage_flags(stage):
    rebirth, ascension, sanctification = STAGE_FLAGS[stage]
    return dict(rebirth=rebirth, ascension=ascension, sanctification=sanctification)


def skill_level_cap(pet, training):
    from native946_summons import guardian_stage_level_cap

    skill_stage, pet_stage = training_stage(training), training_stage(pet)
    if skill_stage < pet_stage:
        return guardian_stage_level_cap(stage_flags(skill_stage))
    return min(pet['level'], guardian_stage_level_cap(stage_flags(pet_stage)))


def effective_training(pet, training):
    stage = min(training_stage(training), training_stage(pet))
    return dict(training, level=min(training['level'], skill_level_cap(pet, training)), **stage_flags(stage))


def skill_promotion(pet, training, mid):
    stage = training_stage(training)
    if (mid == 772 and stage >= 2) or (mid == 771 and stage != 2) or (mid == 770 and stage != 3):
        raise ValueError('请按转生、飞升、封圣的顺序培养特技。')
    if stage >= training_stage(pet):
        raise ValueError('请先提升守护阶段，特技阶段不能超过守护。')
    cap = skill_level_cap(pet, training)
    if mid in (772, 771) and training.get('level', 1) < cap:
        raise ValueError(f'特技须达到{STAGE_NAMES[stage]}{cap}级才能升阶。')
    minimum, cost = SKILL_PROMOTIONS[mid]
    if pet['level'] < minimum or pet['experience'] < cost:
        raise ValueError('守护等级或经验不足，本次未执行。')
    flags = stage_flags(stage + 1)
    if mid == 772:
        flags.update(level=0, experience=0)
    return flags, cost


def saved_training(db, sid, skill):
    row = db.execute('SELECT * FROM native_innate_training WHERE summon_id=? AND skill=?', (sid, skill)).fetchone()
    return dict(row) if row else {}


def trained_records(db, sid, pet):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_innate_training'").fetchone():
        return {}
    result = {}
    for row in db.execute('SELECT * FROM native_innate_training WHERE summon_id=?', (sid,)):
        record = effective_training(pet, dict(row))
        del record['summon_id']
        if record.get('position') is None:
            record.pop('position', None)
        result[row['skill']] = record
    return result


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

    def convert_skill_exp(self, db, pet, skill):
        from native946_neidan import threshold

        training = saved_training(db, pet['id'], skill)
        level = training.get('level', 1)
        experience = training.get('experience', 0)
        stage = training_stage(training)
        if stage > training_stage(pet):
            raise ValueError('当前特技阶段高于守护，暂不能转换经验。')
        cap = skill_level_cap(pet, training)
        if level >= cap:
            raise ValueError(f'特技已达到当前可培养上限（{STAGE_NAMES[stage]}{cap}级）。')
        if pet['experience'] < 500:
            raise ValueError('守护经验不足500，不能转换。')

        # Local policy: use the cubic inner-pill curve, including its high-level extension.
        rebirth = stage_flags(stage)['rebirth']
        costs = [threshold(grade, rebirth) for grade in range(level, cap)]
        amount = min(pet['experience'] // 5, max(0, sum(costs) - experience))
        experience += amount
        for cost in costs:
            if experience < cost:
                break
            level += 1
            experience -= cost

        db.execute("INSERT INTO native_innate_training(summon_id,skill,level,experience) VALUES(?,?,?,?) "
                   "ON CONFLICT(summon_id,skill) DO UPDATE SET level=excluded.level,experience=excluded.experience",
                   (pet['id'], skill, level, experience))
        db.execute("UPDATE summons SET experience=experience-? WHERE id=?", (amount, pet['id']))
        return amount

    def stage(self, db, pet, column, level, experience, start_level):
        if pet['level'] < level or pet['experience'] < experience:
            raise ValueError(f'守护需要达到{level}级并拥有{experience}点经验，本次未执行。')
        sid = pet['id']
        bonus_stages = pet['rebirth'] + int(column in ('rebirth', 'sanctification'))
        points = 4 * start_level + 30 * bonus_stages
        db.execute(f'UPDATE summons SET {column}={column}+1,level=?,experience=0,'
                   'cor=?,mag=?,strength=?,speed=?,points=? WHERE id=?',
                   (start_level, start_level, start_level, start_level, start_level, points, sid))
        updated = self.s.stats(self.s._owned(db, sid))
        db.execute('UPDATE summons SET hp=?,mp=? WHERE id=?', (updated['hp'], updated['mp'], sid))

    def receive(self, mid, reader):
        if mid not in REQUESTS:
            return None
        original = MessageReader(reader.data[reader.offset:])
        sid = reader.integer()
        if mid == JING_PANEL:
            reader.finish()
            self.s.get(sid)
            return text_notice('守护化境暂未开放。')
        skill = reader.integer() if mid not in (757, 758, 769) else None
        if mid in (773, 774):
            item_id, direction = reader.integer(), reader.integer()
        reader.finish()
        if mid == 763:
            from native946_guardian_innate import known_skills, learning_slots
            with self.p.transaction() as db:
                pet = self.s.get(sid)
                if skill in known_skills(pet):
                    raise ValueError('守护已经学会此特技。')
                position = next((slot for slot, skills in learning_slots(pet).items() if skill in skills), None)
                if position is None:
                    raise ValueError('该守护当前不能领悟这个特技。')
                schema(db)
                db.execute('DELETE FROM native_innate_suppressed WHERE summon_id=? AND skill=?', (sid, skill))
                db.execute('INSERT OR REPLACE INTO native_innate_training(summon_id,skill,position) VALUES(?,?,?)',
                           (sid, skill, position))
            notice = '领悟特技成功。'
            return self.g.defer_packets(lambda: self.reply(sid, notice), fallback=text_notice(notice))
        if mid in (773, 774):
            self.s.get(sid)
            # The original panel moves stunt experience into or out of item 6279.
            # It freezes its buttons until 724 confirms or declines the request.
            return (packet(724, ['number'] * 4, [skill, item_id, direction, 0]),
                    text_notice('千回百转丸的经验转出与注入尚未接通。道具和经验未扣除。'))
        if self.g.battle is not None and (mid in (754, 756) or mid in SKILL_PROMOTIONS):
            raise ValueError('请先完成当前战斗。')
        with self.p.transaction() as db:
            pet = self.s.get(sid)
            if (mid in (754, 756) or mid in SKILL_PROMOTIONS) and any(
                    row['skill'] == skill for row in pet.get('learned_books', [])):
                from native946_skillbooks import SkillBooks
                return SkillBooks(self.s).receive(self.g, mid, original)
            schema(db)
            if skill is not None and not self.known(pet, skill):
                raise ValueError('守护没有这个特技。')
            if mid == 754:
                from native946_skillbooks import SkillBooks
                amount = self.convert_skill_exp(db, pet, skill)
                return SkillBooks(self.s).training_reply(
                    sid, skill, f'已将{amount}点守护经验转换为{amount}点特技经验。')
            if mid == 756:
                db.execute("DELETE FROM native_innate_training WHERE summon_id=? AND skill=?", (sid, skill))
                if db.execute('DELETE FROM native_guardian_jing_books WHERE summon_id=? AND skill=?',(sid,skill)).rowcount:
                    return self.reply(sid,'已遗忘该天演图特技。')
                db.execute("INSERT OR IGNORE INTO native_innate_suppressed VALUES(?,?)", (sid, skill))
                return self.reply(sid, '已经遗忘该天生特技，可通过领悟重新学习。')
            if mid == 757:
                from native946_summons import guardian_stage_level_cap

                if not pet['ascension'] or pet['sanctification']:
                    raise ValueError('只有尚未封圣的飞升守护可以办理封圣。')
                self.stage(db, pet, 'sanctification', guardian_stage_level_cap(pet), 20000, GUARDIAN_START_LEVELS[3])
                return self.reply(sid, '守护封圣完成。')
            if mid == 769:
                from native946_summons import STAGE_LEVEL_CAPS

                rebirth = pet['rebirth']
                if rebirth >= 2 or pet['ascension'] or pet['sanctification']:
                    raise ValueError('该守护已达到转生上限。')
                self.stage(db, pet, 'rebirth', STAGE_LEVEL_CAPS[rebirth], 10000, GUARDIAN_START_LEVELS[rebirth])
                return self.reply(sid, '守护转生完成。')
            if mid == 758:
                from native946_summons import STAGE_LEVEL_CAPS

                if pet['rebirth'] != 2 or pet['ascension'] or pet['sanctification']:
                    raise ValueError('只有二转守护可以办理飞升。')
                self.stage(db, pet, 'ascension', STAGE_LEVEL_CAPS[2], 20000, GUARDIAN_START_LEVELS[2])
                return self.reply(sid, '守护飞升完成。')
            if mid in SKILL_PROMOTIONS:
                from native946_skillbooks import SkillBooks

                changes, fee = skill_promotion(pet, saved_training(db, sid, skill), mid)
                columns = ','.join(f'{column}=?' for column in changes)
                db.execute(f'UPDATE native_innate_training SET {columns} WHERE summon_id=? AND skill=?',
                           (*changes.values(), sid, skill))
                db.execute('UPDATE summons SET experience=experience-? WHERE id=?', (fee, sid))
                return SkillBooks(self.s).training_reply(sid, skill, SKILL_PROMOTION_NOTICES[mid])
        return None
