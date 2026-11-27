"""Stock guardian cast IDs and group actions with versioned numerical balance."""
from native946_codec import packet
from native946_skillbooks import SKILLS,EXTENDED
REFERENCE_WIRE_VERSION=5
RULE=EXTENDED['spell']
RESIST={'fire':'ReFire','water':'ReWater','wind':'ReWind','thunder':'ReThunder'}
LOCAL_INNATE_ACTIVE={4906:dict(targets=5,mana=100,element='physical')}


class InsufficientGuardianMana(ValueError):
    def __init__(self):
        super().__init__('守护法力不足。')

def values(pet,row):
    level=row['level']
    return dict(proficiency=level*RULE['proficiency_per_level'],
                mana=RULE['mana_base']+level//RULE['mana_skill_divisor']+pet['level']//RULE['mana_pet_divisor'],
                damage=RULE['damage_base']+pet['level']*RULE['damage_pet_multiplier']+level*RULE['damage_skill_multiplier']+pet['mpmax']*RULE['damage_mp_percent']//100,
                targets=sum(level>=n for n in RULE['target_thresholds']),
                proc_per_mille=level*(RULE['fire_critical_per_skill_level_tenths'] if row['skill']==338 else RULE['wind_repeat_per_skill_level_tenths'] if row['skill']==339 else 0))

def prepared(pet,skill):
    rule=SKILLS.get(skill)
    row=next((r for r in pet.get('learned_books',[]) if r['skill']==skill),None)
    if row is None and any(r['stunt_id']==skill for r in pet.get('innate_skills',[])):
        from native946_innate_effects import active_rule
        rule=active_rule(pet,skill)
        if rule is not None:return rule
        from native946_guardian_passives import RULES as PASSIVES
        if skill in PASSIVES:raise ValueError('这是自动触发的天生被动技能，无须主动施放。')
        raise ValueError('该天生技能的战斗效果尚待核实，本次未消耗法力。')
    if row is None or not rule or not rule.get('active'):raise ValueError('守护尚未学会此主动特技。')
    v=values(pet,row)
    if pet['mp']<v['mana']:raise InsufficientGuardianMana()
    return dict(v,skill=skill,element=rule['element'])

def combat_skills(pet):
    if not pet:return []
    result=[dict(skill_id=row['skill'],cost='法力：'+str(values(pet,row)['mana']),
                 des='基础伤害：'+str(values(pet,row)['damage'])+'；最多作用'+str(values(pet,row)['targets'])+'个目标。')
            for row in pet.get('learned_books',[]) if SKILLS[row['skill']].get('active')]
    from native946_innate_effects import combat_skills as innate_combat_skills
    existing={row['skill_id'] for row in result}
    result.extend(row for row in innate_combat_skills(pet) if row['skill_id'] not in existing)
    return result

def skills_packet(pet):return packet(2149,['number',('array','attack_skill_info')],[pet['id'],combat_skills(pet)])

def cast(game,pet,enemies,main,skill,rng):
    v=prepared(pet,skill)
    targets=sorted((e for e in enemies if e['hp']>0 and not e.get('removed')),key=lambda e:(e['id']!=main,e['id']))[:v['targets']]
    if not targets:raise ValueError('当前没有可以施法的目标。')
    pet['mp']-=v['mana'];result=[]
    repeat=skill==339 and rng.randrange(1000)<v['proc_per_mille']
    for turn in range(2 if repeat else 1):
        living=[t for t in targets if t['hp']>0]
        if not living:break
        result.extend((packet(2126,['number'],[1]),
                       packet(2157,['number','number',('array','number'),'number'],[pet['id'],living[0]['id'],[t['id'] for t in living],skill])))
        for target in living:
            damage=v['damage']*(RULE['multi_percent'] if len(targets)>1 else 100)//100
            resistance=target.get(RESIST[v['element']],target.get('cultivation',{}).get(RESIST[v['element']],0))
            damage=max(1,damage-max(0,resistance))
            from native946_book_combat import value
            damage=damage*(1000+value(pet,'XQAddHurtHpP')+value(pet,'RoleSpellMastery'))//1000
            damage=damage*max(0,1000-value(target,'XQSubHurtHpP'))//1000
            damage=damage*max(0,1000-value(target,'RoleElementReduction'))//1000
            critical=skill==338 and rng.randrange(1000)<v['proc_per_mille']
            if critical:damage=damage*3//2;result.append(packet(2162,['number','number'],[target['id'],1]))
            hit=min(damage,target['hp']);target['hp']-=hit
            result.extend((packet(2164,['number']*4,[target['id'],-hit,1,0]),packet(2158,['number']*3,[target['id'],1,target['hp']])))
            from native946_combat_control import wake
            result.extend(wake(target,hit))
            if target['hp']==0:result.append(packet(2142,['number','number'],[target['id'],2]))
        if turn==0:result.append(packet(2158,['number']*3,[pet['id'],4,pet['mp']]))
        result.append(packet(2126,['number'],[2]))
    return result

def control_chance(base_per_mille,pet,control):
    """Human-control resistance points use native per-mille wire units."""
    key={'seal':'book_reseal','disorder':'book_redisorder','faint':'book_refaint'}[control]
    return max(0,min(1000,base_per_mille-pet.get(key,0)*10))
