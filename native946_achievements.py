"""The 946 character page's achievement panel and verified rewards."""

import json
import re
from pathlib import Path
from secrets import randbelow
from time import monotonic

from native946_codec import packet, text_notice
from native946_role_systems import save, state, text
from native946_training import state as cultivation
from native946_star_rewards import ACHIEVEMENTS as STAR_ACHIEVEMENTS, earned as star_earned, grant_experience
from native946_school_daily import ACHIEVEMENT_ID as SCHOOL_ACHIEVEMENT, ACHIEVEMENT_REWARD, earned_on as school_earned


DATA = json.loads(Path(__file__).with_name('native946_achievements_data.json').read_text('utf8'))
LEVELS = {int(key): row for key, row in DATA['levels'].items()}
NEWBIE_ACHIEVEMENT = 1110101
REWARDS = {**LEVELS, NEWBIE_ACHIEVEMENT: DATA['newbie'], **STAR_ACHIEVEMENTS, SCHOOL_ACHIEVEMENT: ACHIEVEMENT_REWARD}
LEVEL_CONDITION = re.compile(r'(?:成功)?升到(?:(\d)转|(飞升|封圣))?(\d+)级')
REQUEST_TYPES = {
    3484: ['number', 'number', 'number'], 3483: ['number'],
    3481: ['number'], 3482: ['number'], 1031: ['byte', 'number'],
}
WRITES = {3482, 1031}
SYSTEM = 'achievements'


def required_grade(row):
    match = LEVEL_CONDITION.fullmatch(row['mini_desc'])
    if not match:
        raise ValueError('殊勋等级条件未核实。')
    rebirth, stage, level = match.groups()
    if stage == '封圣':
        return 111000, int(level)
    if stage == '飞升':
        return 12000, int(level)
    return 1000 * int(rebirth or 0), int(level)


REQUIREMENTS = {aid: required_grade(row) for aid, row in LEVELS.items()}


def character(db, target):
    row = db.execute('SELECT id,name,role_id,race,sex,level FROM characters WHERE id=?', (target,)).fetchone()
    if row is None:
        raise ValueError('该角色不在本机。')
    return row


def completed(db, role):
    s = cultivation(db, role['id'])
    stage = 100000 * s['sanctification'] + 10000 * s['ascension'] + 1000 * s['rebirth']
    earned = {aid for aid, (required_stage, level) in REQUIREMENTS.items()
              if stage >= required_stage and role['level'] >= level}
    if db.execute("SELECT 1 FROM native_campaign_runs WHERE character_id=? "
                  "AND chapter='newbie' AND state='completed'", (role['id'],)).fetchone():
        earned.add(NEWBIE_ACHIEVEMENT)
    if school_earned(db, role['id']):
        earned.add(SCHOOL_ACHIEVEMENT)
    return earned | star_earned(db,role['id'])


def claims(db, cid):
    return state(db, cid, SYSTEM, {'claimed': []})['claimed']


def open_panel(db, viewer, owner):
    from native946_mall_portraits import current

    role = character(db, owner)
    done = set(claims(db, owner))
    pending = completed(db, role) - done
    profile = dict(owner_id=owner, owner_name=role['name'],
                   owner_icon=current(db, owner, role['role_id']) or role['role_id'],
                   achieve_value=sum(REWARDS[aid]['achieve_value'] for aid in done | pending))
    reply = packet(3479, ['achieve_show_info', 'number', 'string', 'string'],
                   [profile, 0, text(dict.fromkeys(sorted(done), 1)), text(dict.fromkeys(sorted(pending), 1))])
    if viewer == owner:
        return reply, packet(7, ['number'], [int(bool(pending))])
    return reply


def skill_reward(role, data):
    key = str(role['race'] * 10 + role['sex'])
    for entry in data.split(','):
        identity, skills, amount = entry.split('@')
        if identity == key:
            return [int(skill) for skill in skills.split('&')], int(amount)
    raise ValueError('当前角色没有对应的殊勋法术奖励。')


def claim(game, db, aid, *, confirmed=False):
    cid = game.progression.cid
    claimed = claims(db, cid)
    if aid in claimed:
        return packet(6, ['number'], [aid]), text_notice('这项殊勋奖励已领取。')
    if aid not in REWARDS:
        raise ValueError('这项殊勋奖励尚未接入本机。')
    if game.battle is not None:
        raise ValueError('请结束战斗后领取殊勋奖励。')
    role = character(db, cid)
    earned = completed(db, role)
    if aid not in earned:
        if aid == NEWBIE_ACHIEVEMENT:
            raise ValueError('请完成新手剧情「初入三界」后再领取。')
        if aid in STAR_ACHIEVEMENTS:
            raise ValueError('尚未完成这项殊勋要求的星宿挑战。')
        if aid == SCHOOL_ACHIEVEMENT:
            raise ValueError('请先在同一日完成20次师门任务。')
        raise ValueError('尚未达到这项殊勋的等级条件。')

    reward = REWARDS[aid]
    kind, data = reward['award_type'], reward['award_data']
    message = '已领取殊勋「' + reward['name'] + '」的奖励。'
    if aid in STAR_ACHIEVEMENTS:
        paid = grant_experience(game,db,reward)
        message = f"已领取殊勋「{reward['name']}」的奖励，本次发放{paid}人物经验。"
    elif kind == 2:
        skills, amount = skill_reward(role, data)
        ranks = dict(db.execute('SELECT skill_id,rank FROM learned_skills WHERE character_id=?', (cid,)))
        if not confirmed and any(skill not in ranks for skill in skills):
            session = 100_000_000 + randbelow(900_000_000)
            game._achievement_confirm = session, aid, monotonic() + 60
            return packet(1020, ['string', 'string', 'string', 'string', 'number'],
                          ['殊勋奖励', '奖励包含尚未学习的法术。确认后将获得对应的法术熟练度。',
                           '确认领取', '取消', session])
        from native946_school import practice_limit

        cap = practice_limit(cultivation(db, cid))
        for skill in skills:
            rank = ranks.get(skill, 0)
            updated = max(rank, min(cap, rank + amount))
            db.execute('INSERT INTO learned_skills VALUES(?,?,?) ON CONFLICT(character_id,skill_id) '
                       'DO UPDATE SET rank=excluded.rank', (cid, skill, updated))
    elif kind == 1:
        db.execute('UPDATE characters SET experience=experience+? WHERE id=?', (int(data), cid))
    elif kind == 6:
        from native946_inventory_features import give

        identity, item, amount = data.split('@')
        if identity != '999':
            raise ValueError('当前角色没有对应的殊勋物品奖励。')
        give(db, game.progression, int(item), int(amount))
    elif kind == 12:
        from native946_role_growth import talents

        talent = talents(db, cid)
        talent['zhenqi'] += int(data)
        save(db, cid, 'talent', talent)
    else:
        raise ValueError('这项殊勋奖励尚未接入本机。')

    save(db, cid, SYSTEM, {'claimed': sorted([*claimed, aid])})
    ack = packet(6, ['number'], [aid])
    highlight = packet(7, ['number'], [int(bool(earned - set(claimed) - {aid}))])
    return (ack, highlight, text_notice(message),
            game.defer_packets(lambda: (*game.progression.sync(), game.progression.combat_skills_packet())))


def receive(game, mid, reader):
    if mid == 1031:
        answer = reader.take(1)[0]
        session = reader.integer()
        reader.finish()
        pending = getattr(game, '_achievement_confirm', None)
        if not pending or session != pending[0] or monotonic() >= pending[2]:
            raise ValueError('殊勋领奖确认已失效，请重新点击奖励。')
        if answer not in (1, 2, 3):
            raise ValueError('无效的确认选项。')
        game._achievement_confirm = None
        if answer != 1:
            return None
        with game.progression.transaction() as db:
            return claim(game, db, pending[1], confirmed=True)

    value = reader.integer()
    cid = game.progression.cid
    if mid == 3484:
        aid, need_time = reader.integer(), reader.integer()
        reader.finish()
        with game.progression.transaction(write=False) as db:
            character(db, value or cid)
        # Version 2 selects the new panel in the stock 946 callback.
        return packet(3480, ['number'] * 4, [2, value or cid, aid, need_time])
    reader.finish()
    if mid == 3483:
        with game.progression.transaction(write=False) as db:
            return open_panel(db, cid, value or cid)
    if mid == 3481:
        # Existing saves establish level eligibility but contain no gain date.
        return packet(3478, ['number', 'number', 'string'], [value, -1, ''])
    with game.progression.transaction() as db:
        return claim(game, db, value)
