"""Numerical arguments for the 946 guardian panel's native skill templates."""
from native946_innate_effects import (
    ACTIVE, ASCENDED_180_STATS, STATS, ascended_180, cold_values, dragon_round_bonus,
    echo_chance, elemental_base, intimacy_heal, physical_per_mille, residual_recovery,
    resistance_pierce, revive_target_count,
    spell_strengthening, stunt_scale, tai_sui_values, target_count, water_fire_damage,
)

ABSORB_SKILLS=(4714,4715,4716,4717,4718,4719,4727)
UPGRADE_STAT={4146:'mpmax',4147:'initiative',4148:'attack',4149:'hpmax'}


# Each tuple follows the native des argument order in info/info_skill.lua.
# These values are fixed by the current battle implementation.
FIXED_ARGUMENTS = {
    310:(40,100,2,0), 311:(40,200,3,0,0), 314:(40,100,3,0),
    324:(30,3), 332:(3,100,3,0,0), 349:(40,100,0), 352:(70,0),
    353:(3,200,3,0,0), 354:(70,0), 356:(3,3), 361:(60,3,0),
    4004:(100,), 4005:(150,), 4006:(200,), 4559:(35,),
    4614:(3,60), 4832:(100,30), 4899:(100,40,30),
}

# When the stock template would describe an unimplemented effect or
# a different unit, render the implemented behavior in plain text.
LOCAL_DETAILS = {
    319:'进行一次物理攻击，随后对存活的目标施放本机震慑。震慑按目标当前气血和法力结算，受抗震慑影响。',
    321:'消耗100点法力发动。可向选定队友转移最多100点法力，受其缺失法力限制，转出的法力同时从守护扣除。',
    322:'伤害命中时有40%概率令目标的药品恢复效果降低50%，持续3回合。',
    323:'最多为本方3个存活单位增加200点物理防御，持续3回合。',
    342:'增加100点风法抗性。本机同时将所受伤害降低20%。',
    350:'为本方1个单位提高20%伤害，持续2回合。原生人法熟练度换算待核实。',
    351:'为本方1个单位提高20%法术伤害，持续2回合。原生仙法熟练度换算待核实。',
    4141:'本机战斗中守护获得隐身。原生入场触发概率与物理伤害减幅待核实。',
    4144:'本场未出战且满足闪现条件时，可在参战守护倒地后入场。当前队列基准概率25%，可受内丹和其他守护特技影响。',
    4548:'物理攻击命中时有40%概率夺取目标一项可移除的正面效果，持续时间取该效果剩余回合。亲密加成待核实。',
    4739:'入场时给本方存活单位各一层仙法护盾，每层抵挡一次仙法伤害。',
    4797:'力量不低于灵性时有50%概率避开非物理伤害；灵性不低于力量时有50%概率避开物理伤害。',
    4969:'主动施放特技时获得一层水魄。本机每层增加50点速度及2%减伤，最多叠加10层。',
    4971:'主动施放特技时获得一层火魂。本机每层增加5%法术伤害及2%减伤，最多叠加10层。',
    5019:'入场时为本方存活单位恢复其最大气血的50%，并解除封印、昏睡、混乱、毒伤，以及本机已接入的禁复活、禁道具和部分治疗减益状态。',
}
ELEMENT_LABEL={'thunder':'雷','water':'水','fire':'火','wind':'风','poison':'毒'}


def arguments(pet,entry):
    kind=entry['stunt_id']
    scale=stunt_scale(pet,kind)
    mag=pet.get('mag',0)
    if kind in FIXED_ARGUMENTS:return FIXED_ARGUMENTS[kind]
    if kind in (316,4576):return ACTIVE[kind]['targets'],0
    if kind==317:return ACTIVE[kind]['hits'],0
    if kind==4618:return (echo_chance(pet)//100,)
    if kind==4619:return (dragon_round_bonus(pet)//10,)
    if kind==4740:return ACTIVE[kind]['targets'],physical_per_mille(pet,kind)/10,0
    if kind==4897:return physical_per_mille(pet,kind)/10,100
    if kind in (4922,4923):
        from native946_guardian_passives import RULES
        return (RULES[kind]['damage_percent' if kind==4922 else 'heal_max_hp_percent'],)
    if kind==4906:
        attack,element=tai_sui_values(pet)
        return attack/100,element,element,element
    if kind==4046:
        rule=ACTIVE[kind]
        return (rule['targets'],revive_target_count(pet,rule),
                residual_recovery(pet,rule)/100,pet.get('guardian_4046_bonus',0)//10)
    if kind==4043:
        slow,cap=cold_values(pet)
        return target_count(pet,kind,ACTIVE[kind]),slow//100,3,pet.get('guardian_cold_bonus',0)//10,cap//100
    if kind==4539:
        water,fire=water_fire_damage(pet)
        return target_count(pet,kind,ACTIVE[kind]),water,fire,0,pet.get('guardian_4539_bonus',0)//10
    if kind in UPGRADE_STAT:
        return ASCENDED_180_STATS[kind][UPGRADE_STAT[kind]]*scale//10000,0
    if kind==4132:
        return (*[STATS[kind][field] for field in ('hpmax','mpmax','attack','initiative')],0,0)
    if kind==4140:
        modifiers=ASCENDED_180_STATS[kind] if ascended_180(entry) else STATS[kind]
        return (*[modifiers.get(key,0) for key in ('hpmax','mpmax','attack','initiative','ReSeal','ReDisorder','ReJieti')],
                modifiers.get('PhyAbsorbDegree',modifiers.get('role_all_reduction',0))/10)
    if kind==308 and entry['grade']>0:
        return (3151*scale//10000)/100,0
    if kind==312:
        if entry['grade']<1:return 40,25,0
        return (756+5209*scale//10000)/100,(880+3920*scale//10000)/100,0
    if kind==334:
        if entry['grade']<1:return 40,100,0
        return (1500+2041*scale//10000)/100,(2500+4401*scale//10000)/100,0
    if kind==4227:return 5,30,3,10
    if kind==4542:
        amity_factor=1000+min(50000,pet.get('amity',0))//200
        return (3233*amity_factor*scale//10_000_000/100,)
    if kind in (4582,4583):return (intimacy_heal(pet,kind)/100,)
    if kind==4626:return (spell_strengthening(pet),)
    if kind==4627:return (min(5000,400+mag*23//10)/100,)
    if kind==4628:return (resistance_pierce(pet)/100,)
    if kind==4629:return (min(1100,600+mag*23//100)/100,)
    if kind==4630:return (3057*scale//10000/100,)
    if kind==4631:return (min(10000,10232*scale//10000)/100,)
    if kind==4655:return (80333*scale//10000/100,)
    if kind==4656:return (40*scale//10000,)
    if kind in ABSORB_SKILLS:return 10000,50,30000 if kind in (4714,4727) else 21000
    if kind==4720:
        from native946_innate_effects import spirit_factor
        return (750*spirit_factor(pet)//1000/100,)
    if kind==4732:return (180,)
    if kind==4735:return 320,70
    if kind==4736:return (50000,)
    if kind==4737:return (50,)
    if kind==4762:return 20,1
    if kind==4765:
        from native946_innate_effects import has_ascended_180
        return 1500*scale//10000,2 if has_ascended_180(pet,4765) else 1
    if kind==4806:return 9000*scale//10000/100,2,15
    if kind==4908:
        chance=min(10000,10192*scale//10000)/100
        return chance,chance
    return None


def local_detail(pet,kind):
    if kind in LOCAL_DETAILS:return LOCAL_DETAILS[kind]
    if kind==4578:
        return ('同时攻击最多3名敌人。自身攻击不低于目标时，按本机普通攻击的130%结算，'
                '否则按85%。玩家对战中，同回合重复命中伤害减半。原生额外命中和亲密加成待核实。')
    if kind==4743:
        return (f"本机对随机敌人发出{ACTIVE[kind]['targets']}道剑气，"
                '同次施法重复命中时，每次递减25个百分点，最低25%。')
    if kind not in (335,336,337,338,339,346,348,4042,4044,4541,4615,4868,4909):
        return None
    damage=elemental_base(pet,kind)
    if kind==4868:return f'造成非毒系伤害后，有40%概率附加毒法，基础伤害{damage}点。'
    if kind==4909:
        return (f'对单个目标造成2段雷法，基础伤害每段{damage}点，伤害可在1至4倍间变化。'
                '每段有45%概率附加2回合禁复活。原生法宝加成与玩家对战重复伤害待核实。')
    rule=ACTIVE[kind]
    family=rule['element'].split('_')
    damage_text='、'.join(f'{ELEMENT_LABEL[element]}法' for element in family)
    detail=f"最多作用{rule['targets']}名敌人，{damage_text}的基础伤害均为{damage}点。"
    if kind==348:detail+='施放时回复施法者最大气血的25%。'
    if kind==4042:detail+='使目标雷、火、风和水抗各降低1500点，持续3回合。'
    if 'poison' in family:detail+='存活目标之后还会受到持续毒伤。'
    if kind==4615:detail+='本机同时降低200点速度，持续3回合，高级内丹可使目标数额外加1。'
    return detail


def apply_details(pet,rows):
    for row in rows:
        kind=row['stunt_id']
        values=arguments(pet,row)
        if values is not None:
            # The client formats the native des field with this numeric array.
            row['desc']='{'+','.join(map(str,values))+'}'
        elif detail:=local_detail(pet,kind):
            row['desc']=detail
        else:
            continue
        if rule:=ACTIVE.get(kind):row['cost']='{'+str(rule['mana'])+'}'
    return rows
