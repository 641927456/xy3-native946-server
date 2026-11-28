from native946_codec import packet
from native946_guardian_innate import CANSHENG_YIJIU, ZHONGLING_YUXIU
from native946_guardian_rules import DATA as GUARDIAN_RULES

WIRE_VERSION = 10

# Classic 3.0.856 introduced the effect. 3.0.904 raised speed scaling and wrath.
XINYI_FOLLOW_UP = 4749
XINYI_TARGETS = 5
XINYI_SPEED_PERCENT = 900
XINYI_WRATH = 250

DUOMU = 4804
XIANZHI_BAOHUN = 4806
XIANZHI_SHIELD_ROUNDS = 3
XIANZHI_REDUCTION_ROUNDS = 2
XIANZHI_REDUCTION_PER_MILLE = 150

CANSHENG_RULES = GUARDIAN_RULES['skills'][str(CANSHENG_YIJIU)]
CANSHENG_VERIFIED = CANSHENG_RULES['verified_constraints']
CANSHENG_LOCAL = CANSHENG_RULES['local_battle_rule']


def nums(mid, *values):
    return packet(mid, ['number'] * len(values), values)


def has(pet, skill):
    return any(row['stunt_id'] == skill for row in pet.get('innate_skills', []))


STATS = {
    342: dict(ReWind=100, role_neidan_reduction=200),
    4004: dict(hero_hp_percent=1000), 4005: dict(hero_hp_percent=1500), 4006: dict(hero_hp_percent=2000),
    4132: dict(hpmax=2000, mpmax=2000, attack=500, initiative=100),
    4140: dict(hpmax=1500, mpmax=1500, attack=300, initiative=100, ReSeal=100, ReDisorder=100, role_all_reduction=100),
    4636: dict(role_all_reduction=100),
    4639: dict(role_all_reduction=100),
    4714: dict(hpmax=10000), 4715: dict(hpmax=10000), 4716: dict(hpmax=10000),
    4717: dict(hpmax=10000), 4718: dict(hpmax=10000), 4719: dict(hpmax=10000), 4727: dict(hpmax=10000),
    4724: dict(CruelRate=400, role_cruel_multiplier=400),
    4725: dict(FatalRate=300, role_fatal_multiplier=300),
    4726: dict(AttValue=900, book_ignore_counter=100), 4728: dict(role_combo_chance=180, role_combo_count=1),
    4731: dict(attack=1000, role_physical_penetration=150), 4733: dict(DodValue=600),
    4734: dict(ReSeal=120, ReDisorder=120, ReFaint=120), 4735: dict(initiative=320),
    4736: dict(hpmax=50000),
    4745: dict(role_slow_taken_reduction=400),
    4746: dict(anti_fatal=1000, anti_cruel=1000),
    4747: dict(hpmax=20000, mpmax=5000, initiative=-500), 4748: dict(initiative=320),
}

# Leaderboard calibrations apply to the skill's own ascended level 180.
ASCENDED_180_STATS = {
    308: dict(cruel_basis_points=3151),
    4140: dict(hpmax=1285, mpmax=934, attack=340, initiative=81,
               ReSeal=290, ReDisorder=290, ReJieti=1240, PhyAbsorbDegree=116),
    4146: dict(mpmax=4504),
    4147: dict(initiative=259),
    4148: dict(attack=415),
    4149: dict(hpmax=4504),
    4765: dict(EquipElementResist=1500),
}


def ascended_180(skill):
    return skill.get('grade') == 180 and skill.get('fly') == 1 and not skill.get('feng')


def has_ascended_180(pet, kind):
    return any(skill['stunt_id'] == kind and ascended_180(skill) for skill in pet.get('innate_skills', ()))


ACTIVE = {
    316: dict(targets=3, mana=80, element='physical'),
    317: dict(targets=1, mana=100, element='physical', hits=2),
    319: dict(targets=1, mana=100, element='physical', shock=True),
    321: dict(targets=1, mana=100, element='support', transfer_mana=True),
    323: dict(targets=3, mana=80, element='support', team_buff='defense'),
    324: dict(targets=1, mana=100, element='support', damage_share=True),
    332: dict(targets=3, mana=80, element='support', team_buff='mana_regen'),
    335: dict(targets=3, mana=100, element='poison'),
    336: dict(targets=3, mana=100, element='thunder'),
    337: dict(targets=3, mana=100, element='water'),
    338: dict(targets=3, mana=100, element='fire'),
    339: dict(targets=3, mana=100, element='wind'),
    346: dict(targets=3, mana=120, element='water_fire'),
    348: dict(targets=3, mana=100, element='wind', heal=True),
    350: dict(targets=1, mana=80, element='support', team_buff='human_damage'),
    351: dict(targets=1, mana=80, element='support', team_buff='immortal_damage'),
    352: dict(targets=1, mana=60, element='support', remove_buff='defense'),
    353: dict(targets=3, mana=80, element='support', team_buff='immortal_resist'),
    354: dict(targets=1, mana=60, element='support', remove_buff='speed'),
    356: dict(targets=3, mana=80, element='support', team_debuff='human_resist'),
    361: dict(targets=3, mana=80, element='support', seal_to_faint=True),
    4042: dict(targets=3, mana=120, element='thunder', resist_down=True),
    4043: dict(targets=3, mana=80, element='support', slow_rounds=3),
    4044: dict(targets=3, mana=120, element='fire_wind'),
    4227: dict(targets=5, mana=120, element='support', inner_wound=True),
    CANSHENG_YIJIU: dict(targets=CANSHENG_LOCAL['enemy_shock_targets'],
                        mana=CANSHENG_VERIFIED['mana_cost'], element='shock',
                        cooldown_rounds=CANSHENG_VERIFIED['cooldown_rounds'],
                        revive_targets=CANSHENG_LOCAL['ally_revive_targets'],
                        recovery_basis_points=CANSHENG_LOCAL['recovery_basis_points']),
    4539: dict(targets=3, mana=120, element='water_fire'),
    4541: dict(targets=4, mana=120, element='fire'),
    4576: dict(targets=3, mana=80, element='physical'),
    4578: dict(targets=3, mana=80, element='physical'),
    4614: dict(targets=3, mana=100, element='physical', remove_random_buff=True),
    4615: dict(targets=3, mana=100, element='poison', slow_rounds=2),
    4730: dict(targets=5, mana=120, element='thunder', resist_down=True),
    4740: dict(targets=4, mana=100, element='physical'),
    4743: dict(targets=4, mana=100, element='physical', random_targets=True),
    DUOMU: dict(targets=1, mana=8000, element='support', cooldown_rounds=2),
    4832: dict(targets=3, mana=100, element='thunder', weaken=True, primary_bonus=True),
    4897: dict(targets=1, mana=100, element='physical', hits=4, lost_hp_bonus=True),
    4899: dict(targets=5, mana=150, element='support', revive_heal=True),
    4906: dict(targets=5, mana=100, element='physical'),
    4909: dict(targets=1, mana=120, element='thunder', hits=2, random_multiplier=True, no_revive=True),
}

# Reserve entry is handled once by Runtime.flash_guardian after death effects.
DEATH_SKILLS = {334, 349, 4582, 4619, 4634}
START_SKILLS = {4004, 4005, 4006, 4141, 4631, 4739, 4765, 4806, 4908, 5019}
ROUND_SKILLS = {332,4583,4619,4656}
CONTROL_REFLECT = {4734}
DODGE_SKILLS = {4797}
NO_REVIVE_SKILLS = {4736}
INVISIBLE_ON_ATTACK = {4748}
STAT_PASSIVES = set(STATS) | set(ASCENDED_180_STATS) | {4626,4628}
ACTIVE_SKILLS = set(ACTIVE)
BOOK_PATH = {300,301,302,303,304,305,306,307,308,309,336,337,338,339,365,366,367,369,370,371}
JING_EFFECTS = {4618,4635,4720,4721,4722,4723,4729,4732,4737,4762}
ALL_HANDLED = STAT_PASSIVES | ACTIVE_SKILLS | DEATH_SKILLS | START_SKILLS | ROUND_SKILLS | CONTROL_REFLECT | DODGE_SKILLS | NO_REVIVE_SKILLS | INVISIBLE_ON_ATTACK | BOOK_PATH | JING_EFFECTS | {4922,4923} | {310,311,312,314,316,317,319,321,322,323,324,332,334,335,346,348,349,350,351,352,353,354,356,361,4004,4005,4006,4042,4043,4044,4132,4140,4141,4144,4539,4541,4548,4559,4576,4578,4614,4615,4739,4740,4743,4797,4832,4868,4897,4899,4906,4909,4969,4971,5019}


def stats(row):
    from native946_skillbooks import SKILLS as BOOK_SKILLS, effects as book_effects
    for skill in row.get('innate_skills', []):
        trained = row.get('innate_training', {}).get(skill['stunt_id'])
        if trained:
            skill.update(level=trained['level'], experience=trained['experience'])
        kind = skill['stunt_id']
        if kind == 308 and has(row, 4724):
            continue
        calibrated = ASCENDED_180_STATS.get(kind) if ascended_180(skill) else None
        if (kind in (4146,4147,4148,4149,4765) or kind==308 and skill.get('grade',0)>0) and calibrated is None:
            scale=stunt_scale(row,kind)
            calibrated={key:value*scale//10000 for key,value in ASCENDED_180_STATS[kind].items()}
        modifiers = calibrated if calibrated is not None else STATS.get(kind, {})
        if kind==4626:modifiers={'EquipElementDamage':spell_strengthening(row)}
        for key, amount in modifiers.items():
            row[key] = row.get(key, 0) + amount
        rule = BOOK_SKILLS.get(skill['stunt_id'])
        if rule and not rule.get('active') and (kind != 308 or calibrated is None):
            for key, amount in book_effects([dict(skill=skill['stunt_id'], level=skill.get('level', 1))]).items():
                row[key] = row.get(key, 0) + amount
    return row


def skill_level(pet, skill):
    entry = next((row for row in pet.get('innate_skills', []) if row['stunt_id'] == skill), {})
    return max(1, entry.get('level', 1))


def active_rule(pet, skill, *, round_number=None):
    rule = ACTIVE.get(skill)
    if not rule or not has(pet, skill):
        return None
    round_number = pet.get('role_round', 1) if round_number is None else round_number
    if rule.get('cooldown_rounds') and pet.get('role_cooldowns', {}).get(f'skill:{skill}', 0) > round_number:
        raise ValueError('夺目尚在冷却中。' if skill == DUOMU else '残生忆旧尚在冷却中。')
    if pet['mp'] < rule['mana']:
        from native946_guardian_magic import InsufficientGuardianMana
        raise InsufficientGuardianMana()
    return rule


def target_count(pet, skill, rule):
    maximum=rule['targets']
    if skill in (4043,4539):
        mana=pet.get('mpmax',0)
        maximum+=int(mana>=20000)+int(mana>=35000)
    if skill==4539:
        # Both leaderboard panels show five targets, including the peach with 4762.
        maximum=min(5,maximum+int(has(pet,ZHONGLING_YUXIU)))
    return maximum


def stunt_scale(pet,kind):
    entry=next((entry for entry in pet.get('innate_skills',()) if entry['stunt_id']==kind),{})
    grade=entry.get('grade',0)
    if grade<=0:return 5000
    stage=3 if entry.get('fly') or entry.get('feng') else max(0,min(2,entry.get('rei',0)))
    base=(0,3000,5000,7000)[stage]
    cap=(102,122,142,180)[stage]
    end=(3000,5000,7000,10000)[stage]
    return base+(end-base)*min(cap,grade)//cap


def spell_strengthening(pet):
    return pet.get('mag',0)*937*stunt_scale(pet,4626)//13_980_000


def resistance_pierce(pet):
    return min(2038,pet.get('mag',0)*2038//2000)*stunt_scale(pet,4628)//10000


def water_fire_damage(pet):
    delta=pet.get('mpmax',0)-37428
    base=(3250+delta*1101//18357,3641+delta*1170//18357)
    scale=stunt_scale(pet,4539)
    return tuple(max(1,x*scale//10000) for x in base)


def cold_values(pet):
    slow=min(3300,1500+pet.get('mpmax',0)//40)*stunt_scale(pet,4043)//10000
    slow=slow*(1000+pet.get('guardian_cold_bonus',0))//1000
    return slow,min(2200,slow*2//3)


def tai_sui_values(pet):
    scale=stunt_scale(pet,4906)
    physical=10000+5288*scale//10000
    element=max(1,pet.get('strength',0)*7281*scale//14_290_000)
    return physical,element


def residual_recovery(pet,rule):
    return (rule['recovery_basis_points']*(1000+pet.get('guardian_4046_bonus',0))+500)//1000


# 946 lists these nine skill IDs in the text. Full-level values are
# anchored to the archived 3352 leaderboard panels.
ECHO_SPELLS = {336,337,338,346,4044,4233,4539,4540,4541}
ECHO_DAMAGE_PER_MILLE = 600

def echo_chance(pet):
    return 50*stunt_scale(pet,4618)//10000*100

def echo_spell(runtime,pet,skill):
    return (pet['hp']>0 and skill in ECHO_SPELLS and has(pet,4618)
            and runtime.rng.randrange(10000)<echo_chance(pet))

def dragon_round_bonus(pet):
    return 15*stunt_scale(pet,4619)//10000*10

def xianzhi_shield_basis_points(pet):
    # Keep the existing cultivation curve and the official 90% preview anchor.
    return 9000 * stunt_scale(pet, XIANZHI_BAOHUN) // 10000

def revive_target_count(pet, rule):
    return rule['revive_targets'] + int(has(pet, ZHONGLING_YUXIU))


def combat_skills(pet):
    result = []
    for skill, rule in ACTIVE.items():
        if has(pet, skill):
            if skill == CANSHENG_YIJIU:
                description = (f"本机震慑最多{rule['targets']}个敌人，复活最多{revive_target_count(pet, rule)}个友方倒地单位。"
                               f"复活最多恢复其最大气血的{rule['recovery_basis_points']/100:.2f}%，"
                               f"为满级样本反推值。冷却{rule['cooldown_rounds']}回合。")
            elif skill == 4539:
                description = f"本机最多作用{target_count(pet, skill, rule)}个目标，造成水法和火法伤害。"
            elif skill == DUOMU:
                description = '自身物理减伤50%，持续2回合。使一名敌人误导1回合，冷却2回合。'
            else:
                description = '本机规则：最多作用5个目标，造成物理伤害并附加雷、火、风伤害。' if skill == 4906 else '本机规则：'+({'physical':'物理','water':'水系','fire':'火系','wind':'风系','thunder':'雷系','poison':'毒系','support':'辅助'}.get(rule['element'],'混合')+'效果。')
            result.append(dict(skill_id=skill, cost='法力：' + str(rule['mana']), des=description))
    return result


def physical_per_mille(pet,skill):
    return 1000+(skill_level(pet,skill)-1)*20

def elemental_base(pet,skill,multiplier=1):
    base=max(1,pet.get('strength',pet.get('str',0))*2+pet.get('level',1)*10)
    return base*multiplier*physical_per_mille(pet,skill)//1000

def physical(runtime, attacker, target, skill, *, hits=1, lost_hp=False, multiplier=1000, basis_points=10000):
    from native946_book_combat import roll
    preserve_numbers = hits > 1 or skill == 4906 or ACTIVE.get(skill, {}).get('random_targets', False)
    for index in range(hits):
        if target['hp'] <= 0:
            break
        if has(attacker, 4733):
            runtime.clear_invisibility(target)
        outcome = roll(attacker, target, runtime.rng)
        if outcome['miss']:
            runtime.out.append(nums(2144, target['id']))
            continue
        amount = outcome['damage'] * multiplier * basis_points * physical_per_mille(attacker,skill) // 10_000_000_000
        if lost_hp:
            lost = target['hpmax'] - target['hp']
            base_attack = attacker.get('role_initial_attack', attacker['attack'])
            bonus = max(base_attack, min(base_attack * 5, lost * 15 // 100))
            amount += max(0, bonus)
            runtime.out.append(nums(2162, target['id'], outcome['effect'] or 1))
        elif outcome['effect']:
            runtime.out.append(nums(2162, target['id'], outcome['effect']))
        runtime.damage(attacker, target, amount, 'physical', primary=index == 0, preserve_numbers=preserve_numbers)


def spirit_factor(attacker):
    return min(2000, 1000 + max(0, attacker.get('mag', 0)) * 2)


def water_skill_bonus(attacker, target):
    if has(attacker, 4720):
        bonus = (target['hpmax'] * 75 // 1000) * spirit_factor(attacker) // 1000
        return min(20000, bonus)
    if has(attacker, 4629):
        points = min(1100, 600 + attacker.get('mag', 0) * 23 // 100)
        return min(10000, target['hpmax'] * points // 10000)
    return 0


def roll_fire_critical(target, chance, rng, scale=1000):
    return not has(target, 4746) and rng.randrange(scale) < chance


def ordinary_fire_critical_damage(target, amount):
    return amount * 3 * (10000 - target.get('new_neidan_power', {}).get(280, 0)) // 20000


def jing_fire_bonuses(attacker):
    factor = spirit_factor(attacker)
    return 250 * factor // 1000, 200 * factor // 1000


def jing_fire_critical(attacker, target, amount, rng):
    chance, extra_damage = jing_fire_bonuses(attacker)
    if roll_fire_critical(target, chance, rng):
        amount = amount * (1000 + extra_damage) // 1000
        amount = amount * (10000 - target.get('new_neidan_power', {}).get(280, 0)) // 10000
    return amount


def elemental(runtime, attacker, targets, skill, family, *, multiplier=1, damage_per_mille=1000,
              active=True, preserve_numbers=False):
    elements = family.split('_')
    rule = ACTIVE.get(skill, {})
    preserve_numbers = preserve_numbers or len(elements) > 1 or rule.get('hits', 1) > 1
    for element in elements:
        for target in targets:
            if target['hp'] <= 0:
                continue
            amount = elemental_base(attacker,skill,multiplier)
            if skill==4539:
                amount=water_fire_damage(attacker)[int(element=='fire')]
                amount=amount*(1000+attacker.get('guardian_4539_bonus',0))//1000
            if skill==4906:amount=tai_sui_values(attacker)[1]
            factor = spirit_factor(attacker)
            if has(attacker, 4732):
                amount = amount * (100 + runtime.rng.randrange(81)) // 100
            if element == 'water':
                amount += water_skill_bonus(attacker, target)
            if element=='fire' and has(attacker,4627) and not has(attacker,4721):
                if roll_fire_critical(target,min(5000,400+attacker.get('mag',0)*23//10),runtime.rng,10000):
                    amount=ordinary_fire_critical_damage(target, amount)
            if has(attacker, 4721) and element == 'fire':
                amount = jing_fire_critical(attacker, target, amount, runtime.rng)
            if has(attacker, 4722) and element in ('water', 'fire', 'wind', 'thunder'):
                amount += 1500 * factor // 1000
            if has(attacker, 4723) and element == 'wind':
                amount += 1250 * factor // 1000
            if has(attacker, 4729):
                runtime.buff(target, 'ImmAbsorbDegree', -200 * factor // 1000, 1, 4729)
            primary_bonus = rule.get('primary_bonus', False) and target is targets[0]
            actual = runtime.damage(attacker, target, amount*damage_per_mille//1000, element,
                                    primary=False, active=active, preserve_numbers=preserve_numbers or primary_bonus)
            if element == 'poison' and actual and target['hp'] > 0:
                target.setdefault('role_dots', []).append(dict(kind='poison', source=attacker['id'], skill=skill,
                    damage=max(1, amount // 3), until=runtime.round + 2))
                runtime.out.append(nums(2130, target['id'], 106, 0))
            if has(attacker, 4723) and element == 'wind' and target['hp'] > 0 and runtime.rng.randrange(1000) < 325 * factor // 1000:
                runtime.damage(attacker, target, max(1, amount*damage_per_mille//2000), element,
                               primary=False, active=active, preserve_numbers=True)
    if any(element in ('fire','water','wind','thunder') for element in elements):
        runtime.apply_jade_spell_debuff(attacker,targets)


def cast_cansheng_yijiu(runtime, attacker, targets, rule):
    attacker['mp'] -= rule['mana']
    attacker['role_cooldowns'][f'skill:{CANSHENG_YIJIU}'] = runtime.round + rule['cooldown_rounds'] + 1
    runtime.bars(attacker)
    runtime.group(attacker, targets, CANSHENG_YIJIU)
    for index, enemy in enumerate(targets):
        runtime.shock(attacker, enemy, primary=index == 0)

    remaining = revive_target_count(attacker, rule)
    recovery=residual_recovery(attacker,rule)
    for ally in sorted(runtime.allies(attacker, dead=True), key=lambda row: row['id']):
        restored = ally['hpmax'] * recovery // 10000
        if runtime.revive(attacker, ally, restored):
            remaining -= 1
            if remaining == 0:
                break
    runtime.bars(attacker)
    runtime.end()


def cast(runtime, game, attacker, target_id, skill):
    rule = active_rule(attacker, skill, round_number=runtime.round)
    if rule is None:
        return False
    friendly = rule.get('transfer_mana') or rule.get('team_buff') or rule.get('damage_share')
    candidates = runtime.allies(attacker) if friendly else runtime.enemies(attacker)
    if not friendly and (source := runtime.misdirection_source(attacker)) is not None:
        candidates = [source, *(target for target in candidates if target is not source)]
        target_id = source['id']
    count=target_count(attacker,skill,rule)
    extra=attacker.get('new_neidan_power',{}).get(278,0)
    if skill in (4043,4615) and extra and runtime.rng.randrange(10000)<extra:count+=1
    targets = sorted(candidates, key=lambda row: (row['id'] != target_id, row['id']))[:count]
    if not targets and not rule.get('revive_heal'):
        raise ValueError('当前没有可以施法的目标。')
    if skill == CANSHENG_YIJIU:
        cast_cansheng_yijiu(runtime, attacker, targets, rule)
        return True
    if rule.get('revive_heal'):
        allies = sorted((row for row in runtime.fighters.values() if row['camp'] == attacker['camp'] and not row.get('removed')),
                        key=lambda row: (row['hp'] > 0, row['id']))
        primary = next((row for row in allies if row['id'] == target_id and row['hp'] <= 0), allies[0] if allies else None)
        if primary is None or primary['hp'] > 0:
            raise ValueError('没有需要复活的目标。')
        attacker['mp'] -= rule['mana']
        runtime.bars(attacker)
        runtime.group(attacker, [primary], skill)
        if runtime.revive(attacker, primary, 150000):
            runtime.buff(primary, 'role_all_reduction', 400, 1, skill)
        others = [ally for ally in allies if ally['id'] != primary['id'] and ally['hp'] > 0][:4]
        for ally in others:
            runtime.heal(attacker, ally, ally['hpmax'] * 30 // 100)
        runtime.bars(attacker)
        runtime.end()
        return True
    attacker['mp'] -= rule['mana']
    mana_transfer = None
    if rule.get('transfer_mana'):
        allies = runtime.allies(attacker)
        recipient = min(allies, key=lambda row: (row['id'] != target_id, row['mp']))
        amount = min(100, recipient['mpmax'] - recipient['mp'])
        if amount > 0:
            attacker['mp'] = max(0, attacker['mp'] - amount)
            mana_transfer = (recipient, amount)
    runtime.bars(attacker)
    runtime.group(attacker, targets, skill)
    if not friendly:
        targets = [target for target in targets if not runtime.misdirection_blocks(attacker, target)]
    if skill == DUOMU:
        runtime.buff(attacker, 'role_physical_taken', -500, 2, skill)
        for target in targets:
            runtime.apply_misdirection(attacker, target)
        attacker['role_cooldowns'][f'skill:{skill}'] = runtime.round + rule['cooldown_rounds']
    if has(attacker, 4969):
        count = min(10, attacker.get('role_stacks', {}).get('4969', 0) + 1)
        attacker['role_stacks']['4969'] = count
        runtime.buff(attacker, 'initiative', count * 50, 99, 4969)
        runtime.buff(attacker, 'role_all_reduction', count * 20, 99, 4969)
    if has(attacker, 4971):
        count = min(10, attacker.get('role_stacks', {}).get('4971', 0) + 1)
        attacker['role_stacks']['4971'] = count
        runtime.buff(attacker, 'role_spell_stack', count * 50, 99, 4971)
        runtime.buff(attacker, 'role_all_reduction', count * 20, 99, 4971)
    family = rule['element']
    if rule.get('random_targets'):
        hits = {}
        for _ in range(rule['targets']):
            living = runtime.enemies(attacker)
            if not living:
                break
            target = living[runtime.rng.randrange(len(living))]
            repeated = hits.get(target['id'], 0)
            hits[target['id']] = repeated + 1
            physical(runtime, attacker, target, skill, multiplier=max(250, 1000 - repeated * 250))
    elif skill == 4909:
        for target in targets:
            for _ in range(2):
                if target['hp'] <= 0:
                    break
                roll = runtime.rng.randrange(100)
                multiplier = 1 if roll < 50 else 2 if roll < 80 else 3 if roll < 95 else 4
                elemental(runtime, attacker, [target], skill, 'thunder', multiplier=multiplier)
                if runtime.rng.randrange(1000) < 450:
                    target['no_revive_until'] = runtime.round + 1
    elif family == 'physical':
        for target in targets:
            multiplier=1000
            if skill==4578:
                multiplier=1300 if attacker['attack']>=target.get('attack',0) else 850
                if runtime.pvp(attacker,target) and target.get('role_boar_round')==runtime.round:multiplier//=2
                target['role_boar_round']=runtime.round
            physical(runtime, attacker, target, skill, hits=rule.get('hits', 1), lost_hp=rule.get('lost_hp_bonus'),
                     multiplier=multiplier,basis_points=tai_sui_values(attacker)[0] if skill==4906 else 10000)
        if skill == 4906:
            elemental(runtime, attacker, targets, skill, 'thunder_fire_wind')
        if rule.get('shock'):
            for target in targets:
                if target['hp'] > 0:
                    runtime.shock(attacker, target)
    elif family != 'support':
        elemental(runtime, attacker, targets, skill, family)
    if family != 'physical' and (live:=[target for target in targets if target['hp']>0 and not target.get('removed')]):
        if echo_spell(runtime,attacker,skill):
            runtime.end()
            runtime.group(attacker,live,skill)
            elemental(runtime, attacker, live, skill, family, damage_per_mille=ECHO_DAMAGE_PER_MILLE,
                      preserve_numbers=True)
    if rule.get('primary_bonus') and targets and targets[0]['hp'] > 0:
        elemental(runtime, attacker, [targets[0]], skill, family)
    if rule.get('heal'):
        runtime.heal(attacker, attacker, attacker['hpmax'] // 4)
    if mana_transfer is not None:
        runtime.mana(*mana_transfer)
    if rule.get('team_buff'):
        allies = runtime.allies(attacker)[:rule['targets']]
        kind = rule['team_buff']
        for ally in allies:
            if kind == 'defense': runtime.buff(ally, 'defense', 200, 3, skill)
            elif kind == 'mana_regen': runtime.buff(ally, 'role_mana_regen', 100, 3, skill)
            elif kind == 'human_damage': runtime.buff(ally, 'role_damage_percent', 200, 2, skill)
            elif kind == 'immortal_damage': runtime.buff(ally, 'role_spell_stack', 200, 2, skill)
            elif kind == 'immortal_resist':
                for key in ('ReThunder', 'ReFire', 'ReWind', 'ReWater'):
                    runtime.buff(ally, key, 200, 3, skill)
    if rule.get('damage_share'):
        targets[0]['role_damage_share'] = dict(source=attacker['id'], percent=300, until=runtime.round + 2)
    if rule.get('team_debuff'):
        for enemy in targets:
            key = ('ReSeal', 'ReDisorder', 'ReFaint')[runtime.rng.randrange(3)]
            runtime.buff(enemy, key, -100, 3, skill)
    if rule.get('remove_buff'):
        key = 'defense' if rule['remove_buff'] == 'defense' else 'initiative'
        for enemy in targets:
            if runtime.rng.randrange(1000) >= 700:
                runtime.out.append(nums(2144, enemy['id']))
                continue
            for buff in list(enemy.get('role_buffs', [])):
                if buff['key'] == key and buff['amount'] > 0:
                    enemy[key] = enemy.get(key, 0) - buff['amount']
                    enemy['role_buffs'].remove(buff)
    if rule.get('seal_to_faint'):
        for enemy in targets:
            if 'seal' in enemy.get('controls', {}) and runtime.rng.randrange(1000) < 600:
                enemy['controls'].pop('seal')
                enemy['controls']['faint'] = runtime.round + 1
                runtime.out.append(nums(2138, enemy['id'], 38))
                runtime.out.append(nums(2130, enemy['id'], 36, 0))
    if rule.get('control'):
        for enemy in targets:
            runtime.control(attacker, enemy, rule['control'], base=600)
    if rule.get('slow_rounds'):
        for enemy in targets:
            amount=200
            if skill==4043:
                percent,pvp_cap=cold_values(attacker)
                amount=enemy.get('initiative',0)*percent//10000
                if runtime.pvp(attacker,enemy):amount=min(amount,attacker.get('role_initial_speed',0)*pvp_cap//10000)
            runtime.buff(enemy, 'initiative', -max(0,amount), rule['slow_rounds'], skill)
    if rule.get('inner_wound'):
        for enemy in targets:
            chance=1000 if enemy.get('role_sword_rain_round')==runtime.round else 3000
            enemy['role_sword_rain_round']=runtime.round
            runtime.damage(attacker,enemy,max(1,attacker['attack']),'physical')
            if enemy['hp']>0 and runtime.rng.randrange(10000)<chance:
                enemy['role_no_artifact_until']=runtime.round+2
    if rule.get('resist_down'):
        for enemy in targets:
            for key in ('ReThunder', 'ReFire', 'ReWind', 'ReWater'):
                runtime.buff(enemy, key, -1500, 3, skill)
    if rule.get('remove_random_buff'):
        for enemy in targets:
            buffs = [buff for buff in enemy.get('role_buffs', []) if buff['amount'] > 0 and not buff.get('locked')]
            if buffs and runtime.rng.randrange(1000) < 600:
                buff = buffs[runtime.rng.randrange(len(buffs))]
                enemy[buff['key']] = enemy.get(buff['key'], 0) - buff['amount']
                enemy['role_buffs'].remove(buff)
    if rule.get('weaken'):
        for enemy in targets:
            runtime.buff(enemy, 'role_damage_percent', -300, 2, skill)
    runtime.bars(attacker)
    runtime.end()
    return True


def before_damage(runtime, attacker, defender, amount, family):
    if defender.get('role_neidan_reduction'):
        amount = amount * (1000 - defender['role_neidan_reduction']) // 1000
    if family=='physical':
        if has(attacker,4631):amount=amount*4//5
        if has(attacker,4655):
            difference=max(0,attacker.get('strength',0)-defender.get('strength',0))
            amount+=difference*80333*stunt_scale(attacker,4655)//100_000_000
    if defender.get('role_slow_taken_reduction') and attacker.get('initiative', 0) < defender.get('initiative', 0):
        amount = amount * (1000 - defender['role_slow_taken_reduction']) // 1000
    if has(defender, 4746) and runtime.rng.randrange(1000) < 300:
        amount //= 2
    if has(defender, 4797):
        strongest = max(defender.get('strength', 0), defender.get('mag', 0))
        if (strongest == defender.get('strength', 0) and family != 'physical') or (strongest == defender.get('mag', 0) and family == 'physical'):
            if runtime.rng.randrange(1000) < 500:
                runtime.out.append(nums(2144, defender['id']))
                return 0
    return amount


def lethal_guard(runtime, defender, amount):
    if not has(defender, 4736) or not 0 < defender['hp'] <= amount:
        return amount
    if 'role_unyielding_until' not in defender:
        defender['role_unyielding_until'] = runtime.round + 1
        runtime.out.append(nums(2130, defender['id'], 170, 0))
    return 0


def after_damage(runtime, attacker, defender, actual, family):
    if actual <= 0:
        return
    if has(attacker, 310) and family == 'physical' and runtime.rng.randrange(1000) < 400:
        runtime.buff(defender, 'initiative', -100, 2, 310)
    if has(attacker, 311) and family == 'physical' and runtime.rng.randrange(1000) < 400:
        runtime.buff(defender, 'defense', -200, 3, 311)
        runtime.buff(defender, 'PhyAbsorbDegree', -105, 3, 311)
    if family == 'physical':
        recovery_skill = next((skill for skill in attacker.get('innate_skills', ()) if skill['stunt_id'] == 312), None)
        if recovery_skill is not None:
            if recovery_skill.get('grade',0)>0:
                scale=stunt_scale(attacker,312)
                chance=756+5209*scale//10000
                heal=880+3920*scale//10000
                if runtime.rng.randrange(10000)<chance:
                    runtime.heal(attacker,attacker,attacker['hpmax']*heal//10000)
            elif runtime.rng.randrange(1000) < 400:
                runtime.heal(attacker, attacker, actual // 4)
    if has(attacker, 314) and family == 'physical' and runtime.rng.randrange(1000) < 400:
        for key in ('ReSeal', 'ReDisorder', 'ReFaint'):
            runtime.buff(defender, key, -100, 3, 314)
    if has(attacker, 322) and runtime.rng.randrange(1000) < 400:
        runtime.buff(defender, 'role_medicine_percent', -500, 3, 322)
    if has(attacker, 4635) and family == 'physical' and runtime.rng.randrange(1000) < 596:
        runtime.buff(defender, 'defense', -693, 5, 4635)
        runtime.buff(defender, 'PhyAbsorbDegree', -105, 5, 4635)
    if has(attacker, 4548) and family == 'physical' and runtime.rng.randrange(1000) < 400:
        buffs = [buff for buff in defender.get('role_buffs', []) if buff['amount'] > 0 and not buff.get('locked')]
        if buffs:
            buff = buffs[0]
            defender[buff['key']] = defender.get(buff['key'], 0) - buff['amount']
            defender['role_buffs'].remove(buff)
            runtime.buff(attacker, buff['key'], buff['amount'], max(1, buff['until'] - runtime.round + 1), 4548)
    if has(attacker, 4559) and family == 'physical' and runtime.rng.randrange(1000) < 350:
        defender['no_revive_until'] = runtime.round + 1
    if has(attacker, 4868) and family != 'poison' and runtime.rng.randrange(1000) < 400 and defender['hp'] > 0:
        elemental(runtime, attacker, [defender], 4868, 'poison', active=False, preserve_numbers=True)
    for skill, family_name in ((4714, 'shock'), (4715, 'thunder'), (4716, 'fire'), (4717, 'poison'), (4718, 'water'), (4719, 'wind'), (4727, 'physical')):
        if has(defender, skill) and family == family_name and defender['hp'] > 0 and runtime.rng.randrange(1000) < 500:
            runtime.heal(defender, defender, 30000 if skill in (4714, 4727) else 21000)
    if has(attacker, 4748) and family == 'physical' and attacker.get('_normal_attack'):
        if defender['hp'] <= 0 or runtime.rng.randrange(1000) < 400:
            runtime.grant_invisibility(attacker, 4748, runtime.round + 1)


def xinyi_follow_up(runtime, guardian, primary_id):
    if guardian['kind'] != 'pet' or not has(guardian, XINYI_FOLLOW_UP) or guardian['hp'] <= 0:
        return
    if any(guardian['controls'].get(key, 0) >= runtime.round
           for key in ('disorder', 'seal', 'faint', 'wenxin', 'soul_seal', 'jin_gu', 'entangle')):
        return

    victims = sorted(runtime.enemies(guardian), key=lambda target: (target['id'] != primary_id, target['id']))[:XINYI_TARGETS]
    if not victims:
        return
    damage = max(0, guardian['role_initial_speed']) * XINYI_SPEED_PERCENT // 100
    runtime.group(guardian, victims, XINYI_FOLLOW_UP)
    for index, victim in enumerate(victims):
        repeated_pvp_hit = runtime.pvp(guardian, victim) and victim.get('role_xinyi_hit_round') == runtime.round
        amount = damage // 2 if repeated_pvp_hit else damage
        if runtime.damage(guardian, victim, amount, 'physical', primary=index == 0) > 0:
            victim['role_xinyi_hit_round'] = runtime.round
    runtime.end()

    owner = runtime.fighters.get(guardian.get('owner'))
    if owner and owner['kind'] == 'hero' and owner['camp'] == guardian['camp'] and not owner.get('removed'):
        runtime.restore_wrath(owner, XINYI_WRATH)
        runtime.bars(owner)


def prepare(runtime, fighter):
    if not fighter.get('role_entry_initialized') and fighter['hp']>0:
        fighter['role_entry_initialized']=True
        fighter['role_initial_speed']=fighter.get('initiative',0)
        fighter['role_initial_attack']=fighter.get('attack',0)
        if has(fighter,4631) and runtime.rng.randrange(10000)<min(10000,10232*stunt_scale(fighter,4631)//10000):
            runtime.grant_invisibility(fighter,4631,runtime.round+runtime.rng.randint(2,3)-1)
        if has(fighter, XIANZHI_BAOHUN):
            shield = fighter.get('unmodified_hpmax', fighter['hpmax']) * xianzhi_shield_basis_points(fighter) // 10000
            for ally in runtime.allies(fighter):
                ally.setdefault('role_absorb_hp', {})[XIANZHI_BAOHUN] = min(shield, ally['hpmax'] * 3 // 2)
                ally.setdefault('role_absorb_hp_until', {})[XIANZHI_BAOHUN] = runtime.round + XIANZHI_SHIELD_ROUNDS - 1
                ally.setdefault('role_buffs', [])
                runtime.buff(ally, 'role_all_reduction', XIANZHI_REDUCTION_PER_MILLE,
                             XIANZHI_REDUCTION_ROUNDS, XIANZHI_BAOHUN)
        if has(fighter,4908):
            fighter['role_shields'].update(bell_physical=1,bell_element=1,bell_control=1)
    if has(fighter,4765) and 'peach' not in fighter['role_shields']:
        fighter['role_shields']['peach']=2 if has_ascended_180(fighter,4765) else 1
    if has(fighter, 4141):
        runtime.grant_invisibility(fighter, 4141, runtime.round + 3)
    if has(fighter, 4739):
        for ally in runtime.allies(fighter):
            ally.setdefault('role_shields', {})['element'] = 1
    if has(fighter, 5019) and not fighter.get('role_spring_rain_done'):
        fighter['role_spring_rain_done'] = True
        allies = sorted(runtime.allies(fighter), key=lambda row: (row['hp'] / max(1, row['hpmax']), row['id']))
        for target in allies:
            runtime.heal(fighter, target, target['hpmax'] // 2)
            runtime.cleanse(fighter, target, ('seal', 'faint', 'disorder', 'poison'))
            target['no_revive_until'] = 0
            target.pop('role_no_item_until', None)
            for buff in list(target.get('role_buffs', [])):
                if buff['key'] in ('role_heal_reduce', 'role_mp_drain') and buff['amount'] > 0:
                    target[buff['key']] = target.get(buff['key'], 0) - buff['amount']
                    target['role_buffs'].remove(buff)
    if has(fighter, 4004) or has(fighter, 4005) or has(fighter, 4006):
        hero = next((f for f in runtime.fighters.values() if f.get('kind') == 'hero' and f.get('owner') == fighter.get('owner')), None)
        if hero:
            skill = next(skill for skill in (4004, 4005, 4006) if has(fighter, skill))
            percent = {4004: 1000, 4005: 1500, 4006: 2000}[skill]
            runtime.buff(hero, 'hpmax', hero['hpmax'] * percent // 1000, 99, skill)
            runtime.bars(hero)


def end_round(runtime, fighter):
    if fighter['hp']>0 and not fighter.get('removed'):
        if has(fighter,4583):
            runtime.heal(fighter,fighter,fighter['hpmax']*intimacy_heal(fighter,4583)//10000)
        if has(fighter,4908) and runtime.rng.randrange(10000)<min(10000,10192*stunt_scale(fighter,4908)//10000):
            fighter['role_shields'].update(bell_physical=1,bell_element=1,bell_control=1)
    until = fighter.get('role_unyielding_until')
    if until is not None and runtime.round >= until and not fighter.get('removed'):
        fighter['removed'] = True
        runtime.out.append(nums(2137, fighter['id']))


def death_effect(runtime, attacker, victim, skill):
    if skill==4619:
        if victim.get('role_last_damage_family') == 'poison':
            return False
        if victim.get('role_dragon_revived') or not runtime.revive(victim,victim,victim['hpmax']):return False
        victim['role_dragon_revived']=True
        return True
    if skill==4582:
        hero=next((f for f in runtime.allies(victim) if f.get('kind')=='hero' and f.get('owner')==victim.get('owner')),None)
        if hero is not None:
            runtime.heal(victim,hero,hero['hpmax']*intimacy_heal(victim,4582)//10000)
        return False
    if skill == 334:
        if victim.get('role_last_damage_family') == 'poison':
            return False
        entry=next((row for row in victim.get('innate_skills',()) if row['stunt_id']==334),{})
        if entry.get('grade',0)>0:
            scale=stunt_scale(victim,334)
            chance=1500+2041*scale//10000
            recovery=2500+4401*scale//10000
            if runtime.rng.randrange(10000)<chance:
                return runtime.revive(victim,victim,victim['hpmax']*recovery//10000)
        elif runtime.rng.randrange(1000) < 400:
            return runtime.revive(victim, victim, victim['hpmax'])
    if skill == 349:
        if runtime.rng.randrange(1000) < 400:
            runtime.revive(victim, victim, victim['hpmax'])
            victim['role_shields']['endure'] = 1
            victim['role_endure_until'] = runtime.round + 1
            return True
    if skill == 4634:
        if victim.get('role_last_damage_family') != 'poison' and runtime.rng.randrange(1000) < 413:
            runtime.revive(victim, victim, victim['hpmax'])
            return True
    return False


def intimacy_heal(fighter,skill):
    amity=min(50000,max(0,fighter.get('amity',0)))
    return (416+(584*amity+25000)//50000)*stunt_scale(fighter,skill)//10000


def after_control(runtime, attacker, defender):
    if has(defender, 4734) and attacker and attacker['hp'] > 0 and runtime.rng.randrange(1000) < 700:
        runtime.control(defender, attacker, 'disorder', duration=3, base=850)
