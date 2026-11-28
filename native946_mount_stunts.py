"""946 降魔神技培养。技能进度归坐骑，携带组合归守护。"""

from datetime import datetime, timedelta, timezone

from native946_codec import packet, text_notice
from native946_innate_training import STAGE_NAMES, effective_training, skill_level_cap, skill_promotion
from native946_innate_training import stage_flags, training_stage
from native946_mall_uses import consume, features
from native946_neidan import threshold


WIRE_VERSION = 2
MOUNT = 18  # 避水金睛兽
DIRECTION = 1  # 怒吼，解锁撼山和仙音需先培养坐骑本身。
# 946 info/info_skill.lua 与怒吼的五个可携带技能（1301–1305）。
SKILLS = (
    (4065, '晓梦迷蝶', 1, '物理攻击时，有几率使对手陷入混乱状态。'),
    (4066, '烈刃惊魂', 2, '物理攻击时，有几率施放一个震摄法术。'),
    (4067, '灵刃封喉', 3, '物理攻击时，有几率将对方一击必杀。'),
    (4068, '幽刃蚀骨', 4, '物理攻击时，有几率施放一个毒系法术。'),
    (4069, '作壁上观', 5, '物理攻击时，有几率使对手陷入脱离战斗状态。'),
)
SELECTABLE = frozenset(skill for skill, *_ in SKILLS)
REBORN = 768
CHANGE = 4753
MEDICINE = 45792  # 大圣膏
MEDICINE_EXPERIENCE = 500_000
MEDICINE_DAILY_LIMIT = 25  # 946 道具说明的基础额度。
GAME_TIMEZONE = timezone(timedelta(hours=8))
WRITES = {REBORN, 2308, CHANGE}
REQUESTS = WRITES | {2293, 2294, 2306, 2316}


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS native_mount_guardians (
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        summon_id INTEGER NOT NULL REFERENCES summons(id) ON DELETE CASCADE,
        mount INTEGER NOT NULL, direction INTEGER NOT NULL,
        first_skill INTEGER NOT NULL DEFAULT 0, second_skill INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(summon_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS native_mount_stunts (
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        mount INTEGER NOT NULL, direction INTEGER NOT NULL, skill INTEGER NOT NULL,
        stage INTEGER NOT NULL, level INTEGER NOT NULL, experience INTEGER NOT NULL,
        PRIMARY KEY(character_id,mount,direction,skill))""")
    db.execute("""CREATE TABLE IF NOT EXISTS native_mount_stunt_medicine (
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        day TEXT NOT NULL, used INTEGER NOT NULL, PRIMARY KEY(character_id,day))""")


def load_controls(db, cid):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='native_mount_guardians'").fetchone():
        return {}
    if f'mount:{MOUNT}' not in features(db, cid):
        return {}
    from native946_guardian_chamber import AVAILABLE

    return {r['summon_id']: dict(r) for r in db.execute(
        'SELECT g.summon_id,g.first_skill,g.second_skill FROM native_mount_guardians AS g '
        'WHERE g.character_id=? AND g.mount=? AND g.direction=? AND g.summon_id IN '
        '(SELECT id FROM summons WHERE character_id=? AND ' + AVAILABLE + ')',
        (cid, MOUNT, DIRECTION, cid))}


def load_training(db, cid):
    saved = {}
    for row in db.execute('SELECT skill,stage,level,experience FROM native_mount_stunts '
                          'WHERE character_id=? AND mount=? AND direction=?', (cid, MOUNT, DIRECTION)):
        saved[row['skill']] = dict(level=row['level'], experience=row['experience'], **stage_flags(row['stage']))
    return {skill: saved.get(skill, dict(level=1, experience=0, **stage_flags(0))) for skill, *_ in SKILLS}


def store_training(db, cid, skill, record):
    db.execute('INSERT INTO native_mount_stunts VALUES(?,?,?,?,?,?,?) '
               'ON CONFLICT(character_id,mount,direction,skill) DO UPDATE SET '
               'stage=excluded.stage,level=excluded.level,experience=excluded.experience',
               (cid, MOUNT, DIRECTION, skill, training_stage(record), record['level'], record['experience']))


def add_experience(record, pet, amount):
    if training_stage(record) > training_stage(pet):
        return 0
    level, experience = record['level'], record['experience']
    cap = skill_level_cap(pet, record)
    costs = [threshold(grade, record['rebirth']) for grade in range(level, cap)]
    accepted = min(amount, max(0, sum(costs) - experience))
    if accepted:
        experience += accepted
        for cost in costs:
            if experience < cost:
                break
            experience -= cost
            level += 1
        record.update(level=level, experience=experience)
    return accepted


def panel(db, cid, pet):
    control = load_controls(db, cid).get(pet['id'])
    if not control:
        return [], 0
    selected = {control['first_skill'], control['second_skill']}
    saved = load_training(db, cid)
    rows = []
    for position, (skill, name, race, description) in enumerate(SKILLS, 1):
        record = saved[skill]
        shown = effective_training(pet, record)
        stage = training_stage(shown)
        cap = skill_level_cap(pet, record)
        can_train = training_stage(record) == stage and shown['level'] < cap
        next_exp = threshold(shown['level'], shown['rebirth']) if can_train else -1
        desc = f'{description}#r当前上限：{STAGE_NAMES[stage]}{cap}级'
        if stage == 4:
            # newzq_stunt_info in 946 carries Rei and Fly, but has no Feng field.
            desc = '技能阶段：封圣#r' + desc
        rows.append(dict(active=int(skill in selected), stunt_id=skill, position=position,
                         grade=shown['level'], rei=shown['rebirth'], fly=shown['ascension'],
                         stuntexp=shown['experience'] if can_train else 0, stuntexp_next=next_exp,
                         name=name, desc=desc, race=race))
    return rows, DIRECTION


class MountStunts:
    def __init__(self, game):
        self.game = game
        self.p = game.progression
        self.s = game.summons

    def bound(self, sid):
        with self.p.transaction(write=False) as db:
            self.s._owned(db, sid)
            return packet(712, ['number', 'byte'], [sid, int(sid in load_controls(db, self.p.cid))])

    def control_list(self, mid, mount=0, direction=0):
        with self.p.transaction(write=False) as db:
            controls = load_controls(db, self.p.cid)
            pets = self.s.snapshot()
            rows = [dict(summon_id=s['id'], summon_type=s['type'], summon_name=s['name'],
                         summon_honest=s['honesty'], ctrl_zuoqi=MOUNT if s['id'] in controls else 0,
                         ctrl_lingfu=DIRECTION if s['id'] in controls else 0) for s in pets]
        if mid in (2270, 2273):
            return packet(mid, ['number', 'number', ('array', 'zq_ctrl_summon')], [mount, direction, rows])
        return packet(2274, [('array', 'zq_ctrl_summon'), ('array', 'number'), 'number', ('array', 'byte')],
                      [rows, [s['id'] for s in pets if s['fight']], 0, [s['show'] for s in pets]])

    def require_mount(self, db, mount, direction=DIRECTION):
        if mount != MOUNT or direction != DIRECTION:
            raise ValueError('此坐骑或灵符方向的降魔神技培养尚未开放。')
        owned = features(db, self.p.cid)
        if f'mount:{MOUNT}' not in owned:
            raise ValueError('请先取得避水金睛兽。')
        return owned

    def mount_panel(self, mount, direction=None):
        with self.p.transaction(write=False) as db:
            owned = self.require_mount(db, mount, DIRECTION if direction is None else direction)
            if direction is None:
                riding = int(owned.get('ride', {}).get('value') == mount)
                return packet(2260, ['number', 'number', ('array', 'number'), 'number', 'byte', ('array', 'byte')],
                              [mount, riding, [DIRECTION], DIRECTION, 0, [0, 0, 0]])
            controls = load_controls(db, self.p.cid)
            pets = [s for s in self.s.snapshot() if s['id'] in controls]
            rows = [dict(summon_id=s['id'], summon_type=s['type'], summon_name=s['name'],
                         summon_honest=s['honesty'], ctrl_zuoqi=mount, ctrl_lingfu=direction) for s in pets]
            return packet(2261, ['number', 'number', 'number', 'fu_info', ('array', 'zq_skill_info'),
                                 ('array', 'zq_ctrl_summon'), 'number', 'string', 'number', 'number', 'byte', 'number'],
                          [mount, direction, 1, {}, [], rows, 0, '', 0, 0, 0, 2])

    def refresh_skills(self, sid, notice, *, experience_spent=False):
        return self.game.defer_packets(lambda: (
            *(self.s.info_packets(sid) if experience_spent else ()),
            self.bound(sid), self.s.stunt_packet(sid), text_notice(notice)), fallback=text_notice(notice))

    def set_control(self, mount, direction, enabled, sid):
        if enabled not in (0, 1):
            raise ValueError('管制操作无效。')
        with self.p.transaction() as db:
            self.require_mount(db, mount, direction)
            self.s._owned(db, sid)
            schema(db)
            if enabled:
                controls = load_controls(db, self.p.cid)
                if sid not in controls and len(controls) >= 2:
                    raise ValueError('避水金睛兽的管制位置已满。')
                # A different owner, including after a trade, starts with a new selection.
                db.execute('INSERT INTO native_mount_guardians(character_id,summon_id,mount,direction) VALUES(?,?,?,?) '
                           'ON CONFLICT(summon_id) DO UPDATE SET character_id=excluded.character_id,'
                           'mount=excluded.mount,direction=excluded.direction,first_skill=0,second_skill=0 '
                           'WHERE native_mount_guardians.character_id!=excluded.character_id',
                           (self.p.cid, sid, mount, direction))
            else:
                db.execute('DELETE FROM native_mount_guardians WHERE summon_id=? AND character_id=?',
                           (sid, self.p.cid))
        return self.game.defer_packets(lambda: (self.control_list(2270, mount, direction),
                                                  self.bound(sid), self.s.stunt_packet(sid)))

    def change(self, sid, skills):
        if len(skills) != 2 or len(set(skills)) != 2 or not SELECTABLE.issuperset(skills):
            raise ValueError('请选择两种不同的可携带降魔神技。')
        with self.p.transaction() as db:
            self.s._owned(db, sid)
            if sid not in load_controls(db, self.p.cid):
                raise ValueError('请先用避水金睛兽管制这只守护。')
            db.execute('UPDATE native_mount_guardians SET first_skill=?,second_skill=? '
                       'WHERE character_id=? AND summon_id=?', (*sorted(skills), self.p.cid, sid))
        return self.refresh_skills(sid, '已经更新此守护携带的降魔神技。')

    def promote(self, sid, skill):
        with self.p.transaction() as db:
            pet = self.s._owned(db, sid)
            if sid not in load_controls(db, self.p.cid):
                raise ValueError('请先用避水金睛兽管制这只守护。')
            training = load_training(db, self.p.cid).get(skill)
            if training is None:
                raise ValueError('此技能不属于当前坐骑方向。')
            stage = training_stage(training)
            if stage == len(STAGE_NAMES) - 1:
                raise ValueError('此降魔神技已完成封圣。')
            if stage >= training_stage(pet):
                raise ValueError('请先提升守护阶段，降魔神技阶段不能超过守护。')
            cap = skill_level_cap(pet, training)
            # One native request handles all four transitions. Requiring the outgoing
            # stage cap prevents a repeated fly request from spending the seal fee.
            if training['level'] < cap:
                raise ValueError(f'降魔神技须达到{STAGE_NAMES[stage]}{cap}级才能升阶。')
            changes, cost = skill_promotion(pet, training, (772, 772, 771, 770)[stage])
            training.update(changes)
            store_training(db, self.p.cid, skill, training)
            db.execute('UPDATE summons SET experience=experience-? WHERE id=?', (cost, sid))
        return self.refresh_skills(sid, f'降魔神技已升至{STAGE_NAMES[stage + 1]}。', experience_spent=True)

    def use_medicine(self, target, item_id):
        from native946_mall_bags import pages
        if self.game.battle is not None:
            raise ValueError('请先完成当前战斗。')
        cid = self.p.cid
        day = datetime.now(GAME_TIMEZONE).date().isoformat()
        with self.p.transaction() as db:
            item = db.execute('SELECT * FROM inventory WHERE id=? AND character_id=? AND type=?',
                              (item_id, cid, MEDICINE)).fetchone()
            if item is None or item['page'] not in pages(db, cid):
                raise ValueError('大圣膏不在已开启的背包中。')
            controls = load_controls(db, cid)
            if target in (0, cid):
                fighting = db.execute('SELECT id FROM summons WHERE character_id=? AND fight=1', (cid,)).fetchone()
                target = fighting['id'] if fighting else 0
            pet = self.s._owned(db, target) if target else None
            if target not in controls:
                raise ValueError('请让避水金睛兽管制的守护出战后使用大圣膏。')
            control = controls[target]
            if not control['first_skill'] or not control['second_skill']:
                raise ValueError('请先为该守护选择两个降魔神技。')
            used = db.execute('SELECT used FROM native_mount_stunt_medicine WHERE character_id=? AND day=?',
                              (cid, day)).fetchone()
            if used and used['used'] >= MEDICINE_DAILY_LIMIT:
                raise ValueError('今日已达到大圣膏的使用上限。')
            saved = load_training(db, cid)
            selected = (control['first_skill'], control['second_skill'])
            # 先培养携带的技能。组内按原生面板的技能顺序承接剩余经验。
            order = (*selected, *(skill for skill in saved if skill not in selected))
            remaining = MEDICINE_EXPERIENCE
            for skill in order:
                if not remaining:
                    break
                record = saved[skill]
                credited = add_experience(record, pet, remaining)
                if credited:
                    remaining -= credited
                    store_training(db, cid, skill, record)
            if remaining == MEDICINE_EXPERIENCE:
                raise ValueError('当前守护的降魔神技均已达到可培养上限。')
            consume(db, item)
            db.execute('INSERT INTO native_mount_stunt_medicine VALUES (?,?,1) '
                       'ON CONFLICT(character_id,day) DO UPDATE SET used=used+1', (cid, day))
        return self.game.defer_packets(lambda: (
            *self.p.item_packets(), self.s.stunt_packet(target),
            text_notice(f'增加了{MEDICINE_EXPERIENCE - remaining}点降魔神技经验。')))

    def receive(self, mid, reader):
        if mid in WRITES and self.game.battle is not None:
            raise ValueError('请先完成当前战斗。')
        if mid == REBORN:
            sid, skill = reader.integer(), reader.integer()
            reader.finish()
            return self.promote(sid, skill)
        if mid == CHANGE:
            sid, count = reader.integer(), reader.take(1)[0]
            if count != 2:
                raise ValueError('请选择两个降魔神技。')
            skills = [reader.integer() for _ in range(count)]
            reader.finish()
            return self.change(sid, skills)
        if mid == 2308:
            arguments = [reader.integer() for _ in range(4)]
            reader.finish()
            return self.set_control(*arguments)
        if mid == 2306:
            direction = reader.integer()
            reader.finish()
            return self.mount_panel(MOUNT, direction)
        mount = reader.integer()
        direction = reader.integer() if mid != 2293 else None
        if mid == 2294:
            reader.integer()  # previously viewed direction
        reader.finish()
        if mid == 2316:
            with self.p.transaction(write=False) as db:
                self.require_mount(db, mount, direction)
            return self.control_list(2273, mount, direction)
        return self.mount_panel(mount, direction)
