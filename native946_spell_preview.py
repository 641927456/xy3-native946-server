"""Verified 946 calculator fields used by the original spell descriptions."""

import ast
from functools import cache
import json
import math
import operator
from pathlib import Path
import re


DATA = json.loads(Path(__file__).with_name('native946_spell_calculator.json').read_text(encoding='utf8'))
SLOT = re.compile(r'%\d*(?:\.\d+)?[dfgs]')
OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
MATH = {'floor': math.floor, 'max': max, 'min': min, 'pow': pow}

SPELL_GROUPS = (
    (1, 2, 3, 4, 5), (11, 12, 13, 14, 15), (21, 22, 23, 24, 25), (31, 32, 33, 34, 35),
    (41, 42, 43, 44, 45), (51, 52, 53, 54, 55), (61, 62, 63, 64, 65), (71, 72, 73, 74, 75),
    (81, 82, 83, 84, 85), (91, 92, 93, 94, 95), (101, 102, 103, 104, 105), (111, 112, 113, 114, 115),
    (6100, 6101, 6102, 6103, 6104), (6107, 6108, 6109, 6110, 6111),
    (6114, 6116, 6118, 6120, 6122), (6128, 6129, 6130, 6131, 6132),
)
GROUP_BY_SPELL = {skill: group for group in SPELL_GROUPS for skill in group}
HEALING = {
    6114: (120, 4, 80), 6116: (150, 5, 120), 6118: (140, 4.5, 100),
    6120: (170, 6, 150), 6122: (160, 5.5, 130),
}
FIELDS = {
    'poison': ('持续回合', '命中率', '毒伤害', '毒法加点'),
    'seal': ('持续回合', '命中率', '人法加点'),
    'faint': ('持续回合', '命中率', '人法加点'),
    'disorder': ('持续回合', '命中率', '人法加点'),
    'fire': ('狂暴率', '法术伤害', '仙法加点'),
    'wind': ('连击率', '法术伤害', '仙法加点'),
    'water': ('法术伤害', '仙法加点'),
    'thunder': ('法术伤害', '仙法加点'),
    'guard_buff': ('物理减伤', '妖法加点'),
    'attack_buff': ('加攻程度', '妖法加点'),
    'speed_buff': ('加速程度', '妖法加点'),
    'shock': ('震慑气血', '震慑法力', '妖法加点'),
}
PERCENT_FIELDS = {
    '命中率', '狂暴率', '连击率', '加攻程度', '加速程度', '震慑气血', '震慑法力',
}


@cache
def expression_tree(expression):
    return ast.parse(expression, mode='eval').body


def calculate(node, values):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.BinOp):
        return OPERATORS[type(node.op)](calculate(node.left, values), calculate(node.right, values))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'math' and not node.keywords):
        return MATH[node.func.attr](*(calculate(arg, values) for arg in node.args))
    raise ValueError('Unsupported native spell calculator expression')


def familiar(actor, skill):
    skills = actor.get('skills', {})
    return max(0, skills.get(skill, skills.get(str(skill), 0)))


def healing_percent(actor, skill):
    practice_rate, grade_rate, base = HEALING[skill]
    bonus = actor.get('AddZaisheng', 0) + actor.get('cultivation', {}).get('AddZaisheng', 0)
    return (base + actor.get('level', 1) * grade_rate / 10 + familiar(actor, skill) * practice_rate / 50000 + bonus) / 10


def render(template, values):
    available = iter(values)
    unresolved = False

    def replace(match):
        nonlocal unresolved
        value = next(available, None)
        if value is None:
            unresolved = True
            return '—'
        return match[0] % value

    result = SLOT.sub(replace, template)
    return result + '#r— 表示该数值待核实。' if unresolved else result


def calculator_stats(skill, actor):
    grade = actor.get('level', 1)
    stats = dict(grade=grade, mag=actor.get('mag', grade), cor=actor.get('cor', grade),
                 str=actor.get('strength', grade), dodall=actor.get('initiative', 0), fly=actor.get('ascension', 0))
    for tier, spell in enumerate(GROUP_BY_SPELL[skill], 1):
        stats[f'practice{tier}'] = familiar(actor, spell)
    # Neutral target and no calculator-only strengthening overrides.
    for key, expr in DATA['derived'].items():
        stats[key] = calculate(expression_tree(expr), stats)
    return stats


def calculator_value(formula, stats):
    tree=expression_tree(formula)
    inputs={node.id:0 for node in ast.walk(tree) if isinstance(node,ast.Name)}
    inputs.update(stats)
    return calculate(tree,inputs)


def guard_human_resistance(actor, skill):
    formula=DATA['skills'][str(skill)]['人法抗性']
    return math.floor(calculator_value(formula,calculator_stats(skill,actor)))


def native_calculator_description(skill, template, actor, family):
    formulas = DATA['skills'].get(str(skill))
    fields = FIELDS.get(family)
    if not formulas or not fields:
        return None
    stats=calculator_stats(skill,actor)
    bonuses = {'人法加点': stats['hpmpadd1'] * 100,
               '毒法加点': stats['hpmpadd1'] * 200,
               '妖法加点': stats['hpmpadd2'] * 100,
               '仙法加点': stats['hpmpadd3'] * 100}
    slots = list(fields)
    if skill in (101, 102):
        slots.remove('加速程度')
    if skill in (93, 94):
        slots.insert(1, '命中增幅')
    if skill in (83, 84):
        slots.insert(0, '人法抗性')
    if re.search(r'%d(?:#n)?人', template):
        slots.insert(0, '目标个数')
    values = []
    for field in slots:
        if field in bonuses:
            values.append(bonuses[field])
        elif field in formulas:
            effect=calculator_value(formulas[field],stats)
            values.append(effect / 10 if field in PERCENT_FIELDS else math.floor(effect))
        else:
            values.append(None)
    return render(template, values) + '#r#Y数值为原生计算器预览。#n'


def local_spell_description(meta, actor):
    """Describe the rules actually used when the 946 calculator lacks a field."""
    import native946_role_battle as role_battle

    sid=meta['cast_id']
    family=meta['family']
    rounds=role_battle.STUNT_ROUNDS
    rate=role_battle.SECONDARY_PROC_RATE/10
    count=role_battle.base_spell_targets(actor,meta)
    if meta['group'] and meta['tier']==1:
        count=max(3,count)
    targets=f'基础最多#G{count}#n个目标。'
    power=role_battle.spell_feng_power(actor,sid)
    text=None
    if sid==6127:
        text=(targets+f'将目标现有物理吸收的#G{role_battle.ABSORB_CONVERSION_PERCENT}%#n转为防御，'
              f'最多降低#G{role_battle.ABSORB_CONVERSION_LIMIT/10:g}#n个百分点。'
              f'受到的治疗降低#G{role_battle.ABSORB_HEAL_REDUCTION/10:g}%#n，持续#G{rounds}#n回合。')
    elif sid==6140:
        text=render(meta['desc'],(count,role_battle.liuguang_reveal_rate(actor)/10,rounds))
    elif sid==6141:
        text=render(meta['desc'],(count,role_battle.ENDURE_ROUNDS))
    elif sid==6038:
        amount=role_battle.hunyuan_power(actor)
        text=(targets+f'仙法基础伤害增加#G{amount/10:g}%#n，四系仙法抗性增加#G{amount}#n点，'
              f'抗震慑提高#G{amount/10:g}#n个百分点，持续#G{rounds}#n回合。')
    elif sid in (16,46):
        base=role_battle.stunt_heal_base(actor)
        if sid==16:
            text=(targets+f'消耗当前法力。基础治疗#G{base}#n点，'
                  '额外加上扣除施法最低法力后剩余的法力，并恢复相应的气血上限。')
        else:
            text=(targets+f'可复活倒地目标，基础治疗和气血上限恢复均为#G{base}#n点，'
                  '另恢复目标最大法力的#G25%#n。')
    elif sid==6124:
        healing=role_battle.xuelong_heal_rate(actor)/10
        text=(targets+f'按目标最大气血的#G{healing:g}%#n计算基础治疗，单次回血最多#R20000#n。'
              f'每个受术者有#G{rate:g}%#n机会按当次治疗计算值的三分之一回复法力。'
              '研修、阵法和目标状态会影响实际结算。')
    elif sid==6112:
        chance=role_battle.cleanse_chance(actor,sid)/100
        text=(targets+f'主目标基础消融成功率#G{chance:.2f}%#n，敌方非主目标减半。'
              f'成功后使己方药品恢复量提高#G25%#n，或使敌方降低#G25%#n，持续#G{rounds}#n回合。')
    elif sid==6133:
        physical,spell=role_battle.gushu_damage(actor,{},sid)
        text=(targets+f'蛊术持续#G5#n回合。对无抗性怪物，物攻反噬#G{physical}#n，'
              f'施法反噬#G{spell}#n。每个中蛊目标有#G{rate:g}%#n机会被禁用物品#G{rounds}#n回合。')
    elif sid==56:
        text=targets+f'提高角色仙法基础伤害#G20%#n，持续#G{rounds}#n回合。'
    elif sid==66:
        defense=200+actor.get('level',1)
        text=(targets+f'降低封、混、睡抗性#G200#n点和四系仙法抗性#G200#n点，'
              f'物理防御最多降低#G{defense}#n点，持续#G{rounds}#n回合。')
    elif sid==76:
        text=('为我方1个单位减免#G20%#n伤害，并反弹所受实际伤害的#G20%#n，'
              f'持续#G{rounds}#n回合。')
    elif sid in (86,96):
        effect='混乱' if sid==86 else '只能待机'
        text=targets+f'命中后使对方#G{effect}#n，持续#G{role_battle.CONTROL_ROUNDS}#n回合。'
    elif sid==116:
        text=targets+'法力上限越高、已损失气血越多，伤害越高；速度越高，伤害越低。'
        if all(key in actor for key in ('mpmax','hpmax','hp')):
            text+=f'当前状态下的基础伤害为#G{role_battle.desperate_damage(actor)}#n点。'
    elif sid in (26,36,106,1701,1702,1703,1704,1705,1706,6036,6105):
        scale=role_battle.physical_spell_scale(meta)/10
        if sid==36 and role_battle.level(actor,7021):
            targets='可攻击#G2#n个目标。'
        text=targets+f'目标防御扣减前，基础倍率为攻击力的#G{scale:g}%#n。'
        if sid==26:
            text+='造成伤害前尝试解除目标的混乱。'
        elif sid in (36,1702,1704):
            hits=3 if sid==36 else 2
            text+=f'连续攻击#G{hits}#n次。'
        elif sid in (6036,6105):
            text+=f'本次施法有#G{rate:g}%#n机会再攻击一次。'
        elif sid==1706:
            text+=(f'消耗所有棍势，基础1次，每点棍势追加1次。命中后封魂概率为'
                   f'#G{role_battle.STICK_SEAL_RATE/10:g}%#n，单次伤害低于'
                   f'#G{role_battle.STICK_SEAL_DAMAGE}#n时减半。')
        if 1701<=sid<=1705:
            text+='施放后积累1点棍势。'
    elif sid==1710:
        rank=role_battle.level(actor,7501)
        hp=role_battle.RULE['shadow_hp_percent']/10+rank
        damage=role_battle.RULE['shadow_damage_percent']/10+rank
        text=(f'召出魔影，气血为本体上限的#G{hp:g}%#n，'
              f'跟随攻击使用#G{damage:g}%#n伤害系数。召唤冷却#G3#n回合。')
    elif family=='guard_buff' and sid in (81,82,83,84,85,6012):
        defense=role_battle.guard_buff_defense(actor,power)
        reduction=role_battle.level(actor,7039)+role_battle.value(actor,'RoleLineupGuardBuff')/10
        text=targets+f'物理防御提高#G{defense}#n点，持续#G{rounds}#n回合。'
        if 81<=sid<=85:
            text+=f'封印、混乱、昏睡抗性按原生计算器各提高#G{guard_human_resistance(actor, sid)}#n点。'
        if reduction:
            text+=f'当前天赋和阵法另提供#G{reduction:g}%#n物理减伤。'
        if sid==6012:
            text+='每回合额外恢复#G100#n点法力。'
    elif family=='attack_buff' and sid in (93,94,6014,6030):
        attack=role_battle.attack_buff_power(actor,power)/10
        hit=role_battle.level(actor,7322)*20+(12 if sid in (6014,6030) else 0)
        text=targets+f'攻击提高#G{attack:g}%#n，命中属性增加#G{hit}#n点，持续#G{rounds}#n回合。'
        if sid in (6014,6030):
            text+='普通攻击忽视目标#G100#n点防御。'
    elif sid==6010:
        grade=actor.get('level',1)
        speed=(100+grade)*(1000+role_battle.value(actor,'RoleLineupSpeedBuff')+power)//1000
        text=(targets+f'速度提高#G{speed}#n点，每回合自动回复#G{100+grade*2}#n点气血，'
              f'持续#G{rounds}#n回合。')
    elif sid in (6000,6002,6006,6024,6026):
        effect={'disorder':'混乱','faint':'昏睡','seal':'封印'}[family]
        follow='附带中毒' if sid==6000 else '禁止召唤守护' if family=='faint' else '禁止使用物品'
        text=(targets+f'命中后使目标#G{effect}#n，持续#G{role_battle.CONTROL_ROUNDS}#n回合，'
              f'并有#G{rate:g}%#n机会{follow}。实际命中率受双方属性影响。')
    elif sid in (6008,6028):
        shock=role_battle.shock_rate(actor,{},power//2)/10
        text=(targets+f'对无抗震慑目标，基础气血和法力扣除量均为当前值的#G{shock:g}%#n。'
              f'每个目标有#G{rate:g}%#n机会额外持续损失法力，'
              f'每回合为其最大法力的#G5%#n，持续#G{rounds}#n回合。')
    elif sid in (6016,6018,6020,6022,6032,6034):
        element='水' if family=='thunder' else '雷'
        spell={'thunder':'雷','wind':'风','water':'水','fire':'火'}[family]
        text=(targets+f'造成{spell}法伤害，存活目标有#G{rate:g}%#n机会额外受{element}法伤害，'
              f'追加伤害按{element}法基础值的#G70%#n计算。')
        if sid==6018:
            text+=f'本次施法另有#G{rate:g}%#n机会追加一次攻击。'
    elif sid==6004:
        text=(targets+'造成毒伤害，首击毒伤害计算值受目标最大气血四成上限限制。'
              f'有#G{rate:g}%#n机会移除目标可被驱散的加防、加速和加攻状态。')
    if text is not None:
        return '#Y本机结算预览#n#r'+text
    return None
