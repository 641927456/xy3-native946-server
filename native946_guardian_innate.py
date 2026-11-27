"""Species-owned birth and learned innate skills."""
import json
from pathlib import Path

from native946_growth import species
REFERENCE_WIRE_VERSION=7

DATA = json.loads(Path(__file__).with_name('native946_guardian_innate_data.json').read_text('utf8'))
SKILLS = {int(skill):meta for skill,meta in DATA['skills'].items()}
NATIVE_SKILLS = {**SKILLS, **{int(skill): meta for skill, meta in DATA['staged_skills'].items()}}
ZHONGLING_YUXIU = 4762
CANSHENG_YIJIU = 4046
# The original panel has five innate slots. Its sixth cell belongs to jing skills.
STUNT_RANKS = {'sk0': 0, 'sk50': 50, 'sk100': 100, 'sk1100': 1100, 'sk2120': 2120}
LEARNABLE = {
    ZHONGLING_YUXIU: dict(name='境·钟灵毓秀',
        intro='仙法伤害和常规治疗效果提升20%。境·水火无情的伤害目标数、残生忆旧的复活目标数各增加1。'),
    CANSHENG_YIJIU: dict(name='残生忆旧',
        intro='本机规则为先对敌方施放震慑，再复活己方倒地单位。可在战斗技能提示查看当前作用范围。'),
    308: SKILLS[308],
    334: SKILLS[334],
    4146: dict(name='境·修身养性', intro='增加法力。飞升180级本机样本值为4504点。'),
    4147: dict(name='境·我行我素', intro='增加速度。飞升180级本机样本值为259点。'),
    4148: dict(name='境·野性之力', intro='增加攻击。飞升180级本机样本值为415点。'),
    4149: dict(name='境·阴魂不散', intro='增加气血。飞升180级本机采用公告的4504点。'),
    4765: dict(name='境·桃灵护身', intro='飞升180级本机样本值为各项仙法抗性增加1500点，进场获得两层共用护盾。'),
    4227: dict(name='万剑诀',intro='对敌施加内伤，暂时限制人物法宝。'),
    4542: dict(name='仙法穿透',intro='仙法攻击部分忽视对手的仙法吸收。'),
    4582: dict(name='亲密无间',intro='守护倒地时为主人恢复气血。'),
    4583: dict(name='猪猪相伴',intro='在回合结束时自动恢复气血。'),
    4626: dict(name='法力强化',intro='提升守护仙法伤害。'),
    4627: dict(name='无尽火域',intro='提高火法的狂暴概率。'),
    4628: dict(name='破法一击',intro='施展仙法时可忽视目标一定比例的仙法抗性。'),
    4618: dict(name='言出法随',intro='施放部分仙法技能时，可以再次施放。'),
    4619: dict(name='龙战于野',intro='逐回合叠加仙法伤害，每场战斗可复活一次。'),
    4629: dict(name='月海茫茫',intro='水法可附加目标最大气血的一部分，附伤上限10000。'),
    4630: dict(name='出奇制胜',intro='其他参战守护死亡时，本场未出战者可自动入场。'),
    4631: dict(name='幻影迷踪',intro='守护入场可隐身2至3回合，物理伤害降低20%。'),
    4655: dict(name='势大力沉',intro='根据攻守双方力量差增加物理伤害。'),
    4656: dict(name='坚如磐石',intro='在偶数回合开始时按力量补充护盾。'),
    4806: dict(name='仙芝葆魂',intro='入场时给友方施加护盾与短时减伤。'),
    4908: dict(name='紫金护体',intro='入场时获得三类铃铛护盾。'),
}


def stunt_rank(pet):
    return 10000 if pet.get('ascension') else pet.get('rebirth', 0) * 1000 + pet.get('level', 0)


def known_skills(pet):
    return ({row['stunt_id'] for row in pet.get('innate_skills', ())}
            | {row['skill'] for row in pet.get('learned_books', ())}
            | {row['base'] for row in pet.get('jing_books', ()) if row['base'] > 0})


def learning_slots(pet):
    native = species(pet['type'])
    rank = stunt_rank(pet)
    known = known_skills(pet)
    occupied = {row['position'] for row in pet.get('innate_skills', ())}
    return {position: tuple(skill for skill in native[key] if skill in NATIVE_SKILLS and skill not in known)
            for position, (key, required) in enumerate(STUNT_RANKS.items(), 1)
            if rank >= required and position not in occupied}


def records(pet, excluded=()):
    from native946_guardian_passives import RULES as BATTLE_RULES
    from native946_neidan import threshold

    result = []
    training = pet.get('innate_training', {})
    native = species(pet['type'])
    entries = [(position, skill, SKILLS[skill])
               for position, skill in enumerate(native['sk0'], start=1)]
    assigned = set(native['sk0'])
    # Read old saves using their former slot mapping. New learns record the
    # selected slot, since some skills occur in several stages of one species.
    rank = stunt_rank(pet)
    for position, (key, required) in enumerate((*STUNT_RANKS.items(), ('sk3140', 3140)), 1):
        if position == 1 or rank < required:
            continue
        for skill in native[key]:
            trained = training.get(skill)
            if trained is None or skill in assigned:
                continue
            saved_position = trained.get('position')
            if saved_position == position:
                meta = NATIVE_SKILLS.get(skill)
            elif saved_position is None:
                meta = LEARNABLE.get(skill)
            else:
                continue
            if meta is not None:
                entries.append((position, skill, meta))
                assigned.add(skill)
    upgrades=pet.get('jing_books',())
    entries.extend((row['position'],row['skill'],LEARNABLE[row['skill']]) for row in upgrades
                   if not any(skill==row['skill'] for _,skill,_ in entries))
    hidden=set(excluded)|{row['base'] for row in upgrades}
    for position, skill, meta in entries:
        if skill in hidden:
            continue
        rule=BATTLE_RULES.get(skill)
        description=rule['description'] if rule else meta['intro']
        trained=training.get(skill)
        result.append(dict(stunt_id=skill, position=position, name=meta['name'],
                           grade=trained['level'] if trained else -1, rei=trained['rebirth'] if trained else -1,
                           fly=trained['ascension'] if trained else 0, feng=trained['sanctification'] if trained else 0,
                           stuntexp=trained['experience'] if trained else -1,
                           stuntexp_next=threshold(trained['level'], trained['rebirth']) if trained else -1,
                           qianhuiused=0, trans_stunt=0, cost='',
                           desc=description))
    return result
