"""Native 946 圣心秘法 panels and persistent training and washing."""

from secrets import randbelow

from native946_codec import packet, text_notice
from native946_role_systems import day, give, save, state, take


WIRE_VERSION = 2
MANUAL = 45672
ITEM = dict(name='易筋心法', icon=6379, max_stack=99,
            desc='可用于圣心秘法洗髓，可用意守丹田进度撰写。')
# The client displays these server-supplied values. This is the local balance.
MAX_LEVEL = 10
DAILY_TRAIN = 10
TRAIN_QI = 100
TRAIN_EXP = 100
MANUAL_EXP = 100
REQUESTS = set(range(1874, 1883)) | {2817}
WRITES = {1874, 1875, 1877, 1878, 1880, 2817}
ARGUMENTS = {1874: 0, 1875: 1, 1876: 1, 1877: 2, 1878: 3, 1879: 0,
             1880: 2, 1881: 1, 1882: 2, 2817: 1}

POWER_FAMILIES = {'fire', 'water', 'thunder', 'wind', 'gushu', 'poison', 'wuli',
                  'physical', 'heal', 'attack_buff', 'guard_buff', 'speed_buff',
                  'shock', 'seal', 'disorder', 'faint', 'wenxin', 'jin_gu', 'entangle'}
AFFIXES = {'mana': ('节省法力（千分比）', 10, 60), 'power': ('秘法效果（千分比）', 10, 100)}
# 946 info_hero_school_skill entries with series='圣心秘法'.
SCHOOL_FENG_SKILLS = frozenset({
    1710, 6000, 6002, 6004, 6006, 6008, 6010, 6012, 6014, 6016,
    6018, 6020, 6022, 6024, 6026, 6028, 6030, 6032, 6034, 6036,
    6038, 6105, 6112, 6124, 6133,
})
EXTRA_FENG_STAGE = 2
SECOND_PASSIVE_STAGE = 5
PASSIVE_START = {1:6051, 2:6056, 3:6061}
PASSIVE_IDS = frozenset(range(6051,6066))


def school_skills(school):
    from native946_role_battle import profile
    from native946_school import EXTRA_SKILLS, base_school

    available = EXTRA_SKILLS.get(base_school(school), ())
    modern = tuple(skill for skill in available if profile(skill)['source'] == 'modern')
    return modern + tuple(skill for skill in available if skill in SCHOOL_FENG_SKILLS and skill not in modern)


def saved(db, cid):
    return state(db, cid, 'skill6', dict(skills={}, pending={}, train_day='', train_count=0))


def combat_bonuses(db, cid):
    from native946_role_growth import school
    from native946_training import state as cultivation

    if not cultivation(db, cid)['sanctification']:
        return {}
    allowed = set(school_skills(school(db, cid)))
    return {int(key): dict(power=20 * (row['level'] - 1) + row['wash'].get('power', 0),
                           mana=row['wash'].get('mana', 0))
            for key, row in saved(db, cid)['skills'].items() if int(key) in allowed}


def learn_primary(db, cid, school):
    known = school_skills(school)
    if not known:
        return
    data = saved(db, cid)
    key = str(known[0])
    if key not in data['skills']:
        data['skills'][key] = dict(level=1, exp=0, wash={})
        db.execute('INSERT OR IGNORE INTO learned_skills VALUES(?,?,1)', (cid, known[0]))
        save(db, cid, 'skill6', data)


def skill_row(skill, row, character=None):
    from native946_role_battle import cost_description, description, profile

    actor = character or {}
    return dict(skill_id=skill, skill_exp=row['exp'], skill_level=row['level'],
                desc=description(skill, actor), cost=cost_description(actor, profile(skill)))


def passive_rows(character, active):
    from native946_role_battle import DATA

    first=PASSIVE_START.get(character.get('race'))
    if first is None or not character.get('sanctification'):
        return []
    highest=max((active[str(skill)]['level'] for skill in school_skills(character.get('school',0))
                 if str(skill) in active),default=0)
    catalog=DATA['passive_skill_catalog']
    rows=[]
    for offset in range(5):
        sid=first+offset
        unlocked=highest>=3+offset
        detail='已达到研修等级。激活及洗髓待接通。' if unlocked else '最高主动圣心研修等级：'+str(highest)
        desc=catalog[str(sid)]['desc']+'#n'
        if sid==6057:
            desc+='#r单次扣血上限#R50000#n点。'
        rows.append(dict(skill_id=sid, skill_status=int(unlocked), cost='', desc=desc+'#r'+detail))
    return rows


def list_packet(db, cid, character):
    data = saved(db, cid)
    skills = school_skills(character['school']) if character['sanctification'] else ()
    active = [skill_row(skill, data['skills'][str(skill)], character)
              for skill in skills if str(skill) in data['skills']]
    more = [skill for skill in skills[1:] if str(skill) not in data['skills']]
    stage=character.get('jing_stage',0)
    extra_unlocked = bool(skills) and stage >= EXTRA_FENG_STAGE
    passives=passive_rows(character,data['skills'])
    return packet(686, [('array', 'feng_skill_info'), ('array', 'passive_skill_info'),
                        'number', 'number', ('array', 'number')],
                  [active, passives, int(extra_unlocked), int(bool(passives) and stage>=SECOND_PASSIVE_STAGE), more])


def training_detail(skill, row):
    additions = [f'{AFFIXES[key][0]}：{amount}' for key, amount in row['wash'].items()]
    return dict(skill_id=skill, level=row['level'], exp=row['exp'],
                exp_next=row['level'] * TRAIN_EXP if row['level'] < MAX_LEVEL else max(1, row['exp']),
                base_eff=f'研修效果加成：{(row["level"] - 1) * 2}%', add_eff='#r'.join(additions))


def wash_preview(skill, row, result):
    draws = []
    for name, amount in result.items():
        title, low, high = AFFIXES[name]
        draws.append(dict(title=title, min=low, max=high, value=amount,
                          base=row['wash'].get(name, 0), value_type=0))
    return packet(1872, ['number', 'number', ('array', 'skill6_lianhua_random')], [skill, 0, draws])


def receive(game, mid, reader):
    from native946_role_battle import profile
    from native946_role_growth import talents

    args = [reader.integer() for _ in range(ARGUMENTS[mid])] if mid != 1882 else [reader.integer(), reader.take(1)[0]]
    reader.finish()
    if game.battle:
        raise ValueError('请先结束战斗再修习圣心秘法。')
    p = game.progression
    show = None
    refresh = False
    with p.transaction(write=mid in WRITES) as db:
        character = p._snapshot(db)
        if not character['sanctification']:
            raise ValueError('人物封圣后方可修习圣心秘法。')
        if mid == 1882 or (mid in (1880,1881,1877) and args[0] in PASSIVE_IDS):
            raise ValueError('灵络秘法激活与洗髓暂未开放。')
        school = character['school']
        options = school_skills(school)
        if not options:
            raise ValueError('请加入传授圣心秘法的门派。')
        if mid == 1874:
            learn_primary(db, p.cid, school)
            refresh = True
        data = saved(db, p.cid)
        active = data['skills']
        rows = [training_detail(s, active[str(s)]) for s in options if str(s) in active]
        if mid == 1874:
            show = packet(1867, [('array', 'feng_skill_info_detail')], [rows])
        elif mid == 1879:
            if not any(row['level'] >= 3 for row in rows):
                raise ValueError('任意圣心秘法达到3级后可撰写易筋心法。')
            show = packet(1870, [('array', 'number'), ('array', 'number'), ('array', 'feng_skill_info_detail')],
                          [[MANUAL], [MANUAL_EXP], rows])
        else:
            skill = args[0]
            key = str(skill)
            if skill not in options:
                raise ValueError('该秘法不属于当前门派。')
            if mid == 2817:
                if skill == options[0] or key in active or character.get('jing_stage', 0) < EXTRA_FENG_STAGE:
                    raise ValueError('化境达到贰境界方可领悟更多未习得的圣心秘法。')
                active[key] = dict(level=1, exp=0, wash={})
                db.execute('INSERT OR IGNORE INTO learned_skills VALUES(?,?,1)', (p.cid, skill))
            else:
                if key not in active:
                    raise ValueError('请先从圣心秘法研修界面开启该秘法。')
                row = active[key]
                if mid in (1875, 1876):
                    today = day()
                    count = data['train_count'] if data['train_day'] == today else 0
                    if mid == 1875:
                        talent = talents(db, p.cid)
                        if row['level'] >= MAX_LEVEL or count >= DAILY_TRAIN:
                            raise ValueError('当前秘法已满级或达到今日研修次数上限。')
                        if talent['zhenqi'] < TRAIN_QI:
                            raise ValueError('真气不足。')
                        talent['zhenqi'] -= TRAIN_QI
                        save(db, p.cid, 'talent', talent)
                        count += 1
                        data.update(train_day=today, train_count=count)
                        row['exp'] += TRAIN_EXP
                        while row['level'] < MAX_LEVEL and row['exp'] >= row['level'] * TRAIN_EXP:
                            row['exp'] -= row['level'] * TRAIN_EXP
                            row['level'] += 1
                    show = packet(1868, ['feng_skill_info_detail', 'number', 'number', 'number'],
                                  [training_detail(skill, row), TRAIN_EXP, TRAIN_QI,
                                   max(0, DAILY_TRAIN - count) if row['level'] < MAX_LEVEL else 0])
                elif mid == 1878:
                    item, count = args[1:]
                    if item != MANUAL or not 1 <= count <= 99 or row['level'] < 3 or row['exp'] < count * MANUAL_EXP:
                        raise ValueError('撰写易筋心法需要秘法达到3级且有足够的丹田进度。')
                    give(db, p, MANUAL, count)
                    row['exp'] -= count * MANUAL_EXP
                elif mid == 1881:
                    show = packet(1871, ['feng_skill_info', ('array', 'number'), 'string'],
                                  [skill_row(skill, row, character), [MANUAL], '放入易筋心法进行洗髓，可保留本次结果或重新洗髓。'])
                    if key in data['pending']:
                        show = (show, wash_preview(skill, row, data['pending'][key]))
                elif mid == 1880:
                    if args[1] != MANUAL:
                        raise ValueError('洗髓需要易筋心法。')
                    take(db, p.cid, MANUAL)
                    names = ('mana', 'power') if profile(skill)['family'] in POWER_FAMILIES else ('mana',)
                    result = {}
                    for name in names:
                        _, low, high = AFFIXES[name]
                        result[name] = low + randbelow(high - low + 1)
                    data['pending'][key] = result
                    show = wash_preview(skill, row, result)
                elif mid == 1877:
                    if args[1] not in (0, 1) or key not in data['pending']:
                        raise ValueError('没有可以确认的洗髓结果。')
                    result = data['pending'].pop(key)
                    if args[1] == 0:
                        row['wash'] = result
                    show = packet(1869, [], [])
        if mid in WRITES and mid != 1874:
            save(db, p.cid, 'skill6', data)
            refresh = True

    def reply():
        updates = p.sync() if refresh else ()
        if mid == 1878:
            return (*updates, text_notice('已撰写易筋心法。'))
        if isinstance(show, tuple):
            return (*updates, *show)
        return (*updates, show) if show is not None else updates

    return game.defer_packets(reply)
