"""Authoritative school/child combat events shared by all local battle modes.

Native catalogs define identities and semantics. Missing server coefficients
live in role_combat_data.local_rules, never in client-supplied expressions.
All temporary state belongs to the battle snapshot and rolls back with it.
"""
import json
import re
from pathlib import Path
from time import monotonic
from native946_codec import packet
import native946_spell_preview

# Older running hot-reload controllers reload this module before its preview dependency.
if not hasattr(native946_spell_preview, 'local_spell_description'):
    from importlib import reload

    reload(native946_spell_preview)

from native946_spell_preview import (
    HEALING, guard_human_resistance, healing_percent, local_spell_description,
    native_calculator_description, render,
)
DATA=json.loads(Path(__file__).with_name('native946_role_combat_data.json').read_text('utf8'))
RULE=DATA['local_rules'];WIRE_VERSION=25
GUARDIAN_PASSIVE_WIRE_VERSION=3
PRESENTATION_WIRE_VERSION=4
MEDICINE_WIRE_VERSION=1
NIUDAO_XIAOSHI = 4592
WATER_HP_LIMIT_PERCENT = 35  # Classic 3.0.921, based on final damage.
LIULI_LAMP = 4709
LIULI_LAMP_ROUNDS = 2
FEIXUE_WUHEN = 6139
SPELL_LOCK_TARGETS = 2
SPELL_LOCK_ROUNDS = 2
SPELL_LOCK_COOLDOWN = 3
STUNT_ROUNDS = RULE['default_duration']
CONTROL_ROUNDS = 2
SECONDARY_PROC_RATE = 350
STICK_SEAL_DAMAGE = 10000
STICK_SEAL_RATE = 300
ABSORB_CONVERSION_PERCENT = 50
ABSORB_CONVERSION_LIMIT = 350  # Classic 3.0.774 limits reduction to 35 percentage points.
ABSORB_HEAL_REDUCTION = 250
ENDURE_ROUNDS = 3
ELEMENTS=('fire','water','wind','thunder');CONTROLS=('disorder','seal','faint','wenxin','jin_gu','entangle')
PILL_ELEMENTS = {211: 'wind', 212: 'thunder', 213: 'water', 214: 'fire'}
RESIST={'fire':'ReFire','water':'ReWater','wind':'ReWind','thunder':'ReThunder','disorder':'ReDisorder','seal':'ReSeal','faint':'ReFaint','gushu':'ReGushu','poison':'RePoison','wenxin':'ReWenxin'}
STATUS={'faint':36,'disorder':37,'seal':38,'wenxin':40,'jin_gu':53,'entangle':52,'soul_seal':198}
SHIELD_STATUS={'control_poison':245,'control_sleep':246,'element':191,'physical':192,'endure':170}
BUFF_STATUS={'initiative':195,'role_reveal':218,'role_reflect':30,'role_heal_reduce':174,'hpmax':248,
             'race_fabao_slash':365,'race_fabao_spell_reduction':366,'race_fabao_physical_reduction':367,
             'race_fabao_physical':368,'race_fabao_spell':369,'race_fabao_seal_immunity':370}
GROUP_CHILD={'wind':(705,706),'water':(707,708),'fire':(709,710),'thunder':(711,712),'shock':(713,714),'disorder':(725,726),'seal':(727,728),'poison':(729,730),'faint':(731,732)}
ADVANCED={i:i-12 for i in range(757,769)}
FRIENDLY=('heal','cleanse','attack_buff','guard_buff','speed_buff','hunyuan','time_buff','endure_buff','shadow','element_buff','reflect_buff','lamp')
# Native 946 role calculator. Red Makeup (6130) retains the three targets
# specified by the skill description and observed in the 947 tooltip.
SCHOOL_TARGET_COUNTS = {
    3: (3, (3000, 9000)),
    5: (4, (1000, 5000, 11000)),
    13: (3, (3000, 9000)),
    15: (4, (1000, 5000, 11000)),
    23: (2, (1000, 9000)),
    25: (3, (1000, 5000)),
    33: (3, (3000, 9000)),
    35: (4, (1000, 5000, 11000)),
    43: (3, (3000, 10500)),
    45: (4, (3000,)),
    53: (3, (4000, 10500)),
    55: (4, (4000,)),
    63: (3, (3000, 10500)),
    65: (4, (3000,)),
    73: (3, (4000, 10500)),
    75: (4, (4000,)),
    83: (3, (2000, 8000)),
    85: (4, (1000, 4000, 10500)),
    93: (3, (2000, 8000)),
    95: (4, (1000, 4000, 10500)),
    103: (3, (2000, 8000)),
    105: (4, (1000, 4000, 10500)),
    113: (3, (3000, 9000)),
    115: (4, (1000, 5000, 10500)),
    6102: (3, (3000, 10500)),
    6104: (4, (3000,)),
    6109: (2, (1000, 9000)),
    6111: (3, (1000, 5000)),
    6118: (2, (1000, 9000)),
    6122: (3, (1000, 5000)),
    6132: (3, (1000, 5000)),
}
# 946 configs.wdf f18c9529. The 947 panel fits an additional 4/5
# multiplier on the calculator result, using its effective grade input.
CLEANSE_BASE_RATE = {
    6107: (20, 5, 3), 6108: (50, 7, 5), 6109: (40, 6, 4),
    6110: (80, 11, 7), 6111: (60, 9, 6),
}
GUSHU_HIT_RATE = {
    6128: (200, 17, 10), 6129: (300, 25, 16), 6130: (250, 23, 14),
    6131: (400, 31, 20), 6132: (350, 27, 17),
}
DESCRIPTION_SLOT = re.compile(r'%\d*(?:\.\d+)?[dfgs]')
EFFECT_DESCRIPTION = {
    'absorb_convert': '将敌方部分物理吸收转为防御，降低敌方受到的治疗。',
    'attack_buff': '提高友方攻击力。',
    'cleanse': '有几率解除友方不利状态，或移除敌方有利状态。',
    'desperate': '造成伤害。自身法力上限越高、剩余气血越低，伤害越高。',
    'disorder': '有几率使敌方陷入混乱。',
    'dispel_strike': '造成物理伤害并解除目标的混乱。',
    'element_buff': '提升友方的仙法伤害。',
    'endure_buff': '为友方提供一次免死效果。',
    'faint': '有几率使敌方陷入昏睡。',
    'fire': '对敌方造成火系伤害。',
    'guard_buff': '提高友方防御或减轻友方受到的伤害。',
    'gushu': '使中蛊的敌方在攻击或施法时受到伤害。',
    'heal': '为友方恢复气血。',
    'hunyuan': '提高友方的仙法伤害、仙法抗性和抗震慑。',
    'physical': '对敌方造成物理伤害。',
    'pierce': '对敌方造成物理伤害。',
    'poison': '有几率使敌方中毒并受到持续伤害。',
    'reflect_buff': '减轻友方受到的伤害，并返还部分伤害。',
    'resist_break': '降低敌方抗性和物理防御。',
    'seal': '有几率封印敌方，使其暂时无法行动。',
    'shadow': '召唤魔影，使其跟随施法者行动。',
    'shock': '削减敌方当前的气血和法力。',
    'speed_buff': '提高友方行动速度。',
    'spell_lock': '有几率暂时封锁敌方玩家最近施放的法术。',
    'stick': '用棍法对敌方造成物理伤害。',
    'thunder': '对敌方造成雷系伤害。',
    'time_buff': '提高友方识破隐身的能力。',
    'water': '对敌方造成水系伤害，并降低气血上限。',
    'wenxin': '有几率使目标暂时只能待机。',
    'wind': '对敌方造成风系伤害。',
    'wuli': '以巫族神力造成物理伤害，攻击力越高，伤害越高。',
}

def nums(mid,*v):return packet(mid,['number']*len(v),v)
def value(actor,key):return actor.get('cultivation',{}).get(key,0)+actor.get(key,0)
def level(actor,skill):return int(actor.get('role_talents',{}).get(str(skill),actor.get('role_talents',{}).get(skill,0)))
def child_values(b):
    out={k:b.get(k,0) for k in ('qizhi','neili','naili','moyi')}
    for key,skill in zip(out,(776,777,778,779)):out[key]+=int(b.get('skills',{}).get(str(skill),0))*RULE['child_stat_per_grade']
    return out
def snapshot(db,cid):
    from native946_role_growth import talent_levels
    from native946_skill6 import combat_bonuses
    child=None
    for row in db.execute('SELECT id,value FROM native_babies WHERE character_id=? ORDER BY id',(cid,)):
        b=json.loads(row['value'])
        if b.get('status')==1 and not b.get('travel'):
            child=dict(b,id=row['id'])
            from native946_child_fashion import worn_model
            from native946_child_halos import active_halo
            child['fashion']=worn_model(db,cid,b);child['halo']=active_halo(b);break
    from native946_inventory import active_page
    equipped={r['type'] for r in db.execute('SELECT type FROM inventory WHERE character_id=? AND page=?',(cid,active_page(db,cid)))}
    from native946_role_systems import state
    tianji=state(db,cid,'tianji',{})
    from native946_role_artifacts import active as active_artifacts, lamp_grade
    artifacts=active_artifacts(db,cid)
    artifact_levels = {name:int(skill in artifacts) for name,skill in (('绝情鞭',128),('金箍儿',131))}
    artifact_levels['琉璃灯'] = lamp_grade(db, cid)
    return dict(role_talents={str(k):v for k,v in talent_levels(db,cid).items() if v},role_child=child,role_tianji=tianji.get('active',[]),
                role_feng=combat_bonuses(db,cid),
                role_artifacts=artifact_levels,
                role_secret=int(bool(equipped.intersection(DATA['secret_items']))))
def profile(sid):return DATA['profiles'].get(str(sid))


def practice_damage_bonus(skill, practice):
    from native946_school import SCHOOL_SKILLS

    # This local fallback was balanced for five learning levels.
    return 20 * (min(practice, 5) if skill in SCHOOL_SKILLS else practice)


def school_target_count(actor, sid):
    rule = SCHOOL_TARGET_COUNTS.get(sid)
    if rule is None:
        return None
    base, thresholds = rule
    practices = actor.get('skills', {})
    practice = practices.get(sid, practices.get(str(sid), 0))
    return base + sum(practice >= threshold for threshold in thresholds)


def base_spell_targets(actor, meta):
    if meta['cast_id']==86:
        # The original skill limits the mixed-disorder targets to 1–3.
        return 3
    count=school_target_count(actor,meta['cast_id']) or meta.get('target_count')
    if count is not None:
        return count
    if not meta['group']:
        return 1
    return min(RULE['spell_target_cap'],2+meta['tier']//2)


def spell_feng_power(actor, sid):
    return actor.get('role_feng',{}).get(sid,{}).get('power',0)


def stunt_heal_base(actor):
    return 500+actor.get('level',1)*20+actor.get('mag',1)*2+value(actor,'RoleLineupHeal')


def attack_buff_power(actor, feng_power):
    return 200+value(actor,'RoleLineupAttackBuff')+feng_power


def guard_buff_defense(actor, feng_power):
    return max(1,100+actor.get('level',1)*5+feng_power)


def hunyuan_power(actor):
    rank=level(actor,7412)
    return 200+(20*(value(actor,'AddXiaorongRate')//150) if rank>=10 else rank*5)


def liuguang_reveal_rate(actor):
    return 200+level(actor,7040)*10


def xuelong_heal_rate(actor):
    return 200+level(actor,7413)*5


def desperate_damage(actor):
    return max(1,(actor['mpmax']//4+(actor['hpmax']-actor['hp'])//3)*1000//max(1000,1000+actor.get('speed',0)))


def physical_spell_scale(meta):
    return 1000+meta['tier']*100


def shock_rate(actor, target, boost):
    resistance=max(0,value(target,'ReMpAbsorb')-level(actor,7343)*5)
    return max(0,min(450,200+value(actor,'RoleLineupShock')+boost-resistance))


def owned_profile(actor,sid):
    meta=profile(sid)
    if not meta:
        raise ValueError('当前版本没有此技能的原生战斗定义。')
    learned=actor.get('skills',{})
    if sid not in learned and str(sid) not in learned:raise ValueError('尚未学会此技能。')
    if meta['race'] and actor.get('race')!=meta['race']:raise ValueError('技能与人物种族不匹配。')
    if meta.get('sex') and actor.get('sex')!=meta['sex']:raise ValueError('该法术不属于当前人物可学习的师门法术。')
    if meta.get('schools') and actor.get('school') not in meta['schools']:raise ValueError('该门派特技需要加入对应门派。')
    if meta['source']=='artifact':
        if not actor.get('role_artifacts',{}).get(meta['name']):raise ValueError('请先装备对应法宝。')
    if meta['source']=='secret' and not actor.get('role_secret'):raise ValueError('请先装备秘宝。')
    if meta['source']=='shadow' and actor.get('school') not in (19,119,219,319,719):raise ValueError('该技能需要魔西天门派。')
    return meta


def niudao_value(actor, stat):
    samples = RULE['niudao_xiaoshi']['samples']
    first_level = samples[0]['level']
    grade = max(first_level, actor.get('level', first_level))
    for start, end in zip(samples, samples[1:]):
        if grade <= end['level']:
            break
    return start[stat] + (grade - start['level']) * (end[stat] - start[stat]) // (end['level'] - start['level'])


def niudao_damage(actor):
    multiplier = RULE['niudao_xiaoshi']['damage_multiplier_per_mille']
    return niudao_value(actor, 'damage') * multiplier // 1000


def base_mana_cost(actor, meta):
    skill = meta.get('cast_id')
    if skill == NIUDAO_XIAOSHI:
        return niudao_value(actor, 'mana')
    # The uninitiated training character uses the original local teaching costs.
    if actor.get('race') != 7 or not actor.get('school'):
        return meta['mana']
    rule = RULE['school_mana_samples'].get(str(skill))
    if rule is None:
        return meta['mana']
    practice = actor.get('skills', {})
    interval = rule.get('interval')
    familiarity = practice.get(skill, practice.get(str(skill), 0))
    return rule['base'] + (familiarity // interval if interval else 0)


def mana_cost(actor, meta):
    wash = actor.get('role_feng', {}).get(meta.get('cast_id'), {}).get('mana', 0)
    focus = level(actor, 7033)
    reduction = min(800, (200 if focus >= 10 else focus * 10) + wash)
    return max(0, base_mana_cost(actor, meta) * (1000 - reduction) // 1000)

def cost_description(actor,meta):
    costs=[]
    if meta.get('anger'):costs.append('战意：'+str(meta['anger']))
    if meta.get('wrath'):costs.append('怨气：'+str(meta['wrath']))
    if meta['mana']:costs.append('法力：'+str(mana_cost(actor,meta)))
    return '，'.join(costs) or '无消耗'
def familiarity(actor,sid):
    skills=actor.get('skills',{})
    return skills.get(sid,skills.get(str(sid),0))


def joined_witch(actor):
    return actor.get('race')==7 and bool(actor.get('school'))


def cleanse_probability(actor,sid):
    base,grade_factor,practice_factor=CLEANSE_BASE_RATE[sid]
    # The raw calculator gives per mille. Keep hundredths of a percentage point.
    chance=base*1000+actor['level']*grade_factor*100+familiarity(actor,sid)*practice_factor
    chance+=1000*value(actor,'AddXiaorongRate')
    return max(0,min(800000,chance))*8//1000


def gushu_duration(actor,sid):
    practice=familiarity(actor,sid)
    if sid==6132:return 3+min((practice+4000)//5000,2)
    if sid==6131:return min(int(3*(practice**0.3*0.05+1)),5)
    return int(2*(practice**0.3*0.06+1))


def gushu_hit_rate(actor,target,sid):
    base,grade_factor,practice_factor=GUSHU_HIT_RATE[sid]
    chance=base*1000+actor['level']*grade_factor*100+familiarity(actor,sid)*practice_factor
    chance+=1000*(4*value(actor,'AddGushuRate')-9*max(0,value(target,'ReGushu')-value(actor,'IgGushu')))
    return max(0,min(1000,chance//1000))


def spell_lock_chance(actor):
    # Classic 3.0.904 specifies a base 60%. Keep the existing local talent scaling.
    return min(950, 600 + level(actor, 7042) * 10)


def gushu_damage(actor, target, skill, *, pvp=False):
    grade = actor.get('level', 1)
    base = 100 + grade * 8 + practice_damage_bonus(skill, familiarity(actor, skill)) + actor.get('mag', grade) * 2
    resistance = max(0, value(target, 'ReGushu') - value(actor, 'IgGushu'))
    rank = level(actor, 7421)
    talent_bonus = (150 if pvp else 300) if rank >= 10 else rank * 10
    base = max(1, base - resistance) * (1000 + value(actor, 'AddGushu') + talent_bonus) // 1000
    base = base * (1000 + actor.get('role_feng', {}).get(skill, {}).get('power', 0)) // 1000
    base = base * (1000 + value(actor, 'XQAddHurtHpP')) // 1000
    # Preserve the local physical base. The 947 tooltips and 3.0.853
    # examples support independently rounding its 70:100 damage split.
    scaled = max(1000, base * max(0, 1000 - value(target, 'XQSubHurtHpP')))
    return scaled // 1000, scaled // 700


def lamp_wrath(grade):
    # Classic 3.0.921; 2158/7 carries whole points.
    return grade * 6 // 5 + 40


def description(sid,actor=None):
    meta = profile(sid)
    if not meta:
        return '当前客户端没有此技能定义。'
    if sid == LIULI_LAMP:
        grade = (actor or {}).get('role_artifacts', {}).get('琉璃灯', 1) or 1
        return f'对自己施放，无怨气消耗。接下来{LIULI_LAMP_ROUNDS}回合，每回合指令下达后回复{lamp_wrath(grade)}点怨气。'
    text = meta['desc']
    if sid == NIUDAO_XIAOSHI:
        actor = actor or {}
        return text % (niudao_value(actor, 'damage'), niudao_damage(actor))
    if sid == FEIXUE_WUHEN:
        actor = actor or {}
        count = SPELL_LOCK_TARGETS + (level(actor, 7423) >= 10)
        return render(text, (spell_lock_chance(actor) / 10, count, SPELL_LOCK_ROUNDS))
    from native946_training import WULI_BASE_DAMAGE, wuli_damage
    if actor and sid in WULI_BASE_DAMAGE:
        attack = 0 if joined_witch(actor) else actor['attack']
        damage = wuli_damage(sid, familiarity(actor,sid), attack, actor['level'])
        count = school_target_count(actor, sid)
        return render(text, (count, damage) if count is not None else (damage,))
    if not DESCRIPTION_SLOT.search(text):
        return text
    if meta['family'] == 'water':
        text = text.replace('最终伤害×25％', f'最终伤害×{WATER_HP_LIMIT_PERCENT}％')
    if actor:
        count = school_target_count(actor, sid)
        if sid in CLEANSE_BASE_RATE:
            return render(text, (cleanse_chance(actor, sid) / 100, count))
        if sid in HEALING:
            return render(text, (healing_percent(actor, sid), count))
        if sid in GUSHU_HIT_RATE:
            values = (gushu_duration(actor, sid), *gushu_damage(actor, {}, sid))
            tip = render(text, (count, *values) if sid == 6132 else values)
            return tip + '#r#Y反噬数值为本机无抗性怪物预览。#n'
        native = native_calculator_description(sid, text, actor, meta['family'])
        if native is not None and '— 表示该数值待核实' not in native:
            return native
        current = local_spell_description(meta, actor)
        if current is not None:
            return current
        if native is not None:
            return native
    return render(text, ())


def cleanse_chance(actor, skill):
    if joined_witch(actor) and skill in CLEANSE_BASE_RATE:
        return cleanse_probability(actor, skill) + level(actor, 7415) * 30
    return min(min(999, 950 + level(actor, 7415) * 3),
               700 + value(actor, 'AddXiaorongRate') + level(actor, 7415) * 3) * 10
def child_entry(actor):
    b=actor.get('role_child')
    if not b:return ()
    # Native child actor IDs are separate from owned summon IDs. The explicit
    # owner field binds this visual to its real fighter; children take no input.
    return (packet(2127,['number','number','string','number','byte',('fixed_array','byte',3),'number','number','number'],
                   [940000000+b['id'],b.get('icon',401),b.get('name',''),actor['id'],0,[0,0,0],b.get('halo',0),0,b.get('fashion',0)]),)


def stick_packets(actor):
    from native946_school import base_school

    if actor.get('kind') != 'hero' or base_school(actor.get('school', 0)) != 19:
        return ()
    return (nums(4830, actor['id'], actor.get('role_stick', 0), actor.get('role_stick_max', 2)),)


def fighter_entry(actor,battle_id):
    from random import Random
    if not actor.get('role_initialized'):
        actor['role_initialized']=1
        actor['role_stick_max']=stick_capacity(actor, Random(str(battle_id)+':'+str(actor['id'])+':stick'))
    wrath = actor.setdefault('role_wrath', RULE['wrath_start'])
    return (*child_entry(actor),*stick_packets(actor),
            nums(3458,actor['id'],wrath,RULE['wrath_max']))


def stick_capacity(actor, rng):
    rank = level(actor, 7503)
    if rank >= 10:
        return 3
    return 3 if rank and rng.randrange(1000) < min(900, rank * RULE['talent_proc_per_grade']) else 2

def invisibility_sources(f):
    sources=f.get('role_invisibility_sources')
    if sources is None:
        sources={};f['role_invisibility_sources']=sources
        old=f.pop('role_formation_invisible_until',None)
        if old is not None:sources['formation']=old
        elif f.get('invisible'):sources['legacy']=None
    return sources

def expire_invisibility(f,round):
    sources=invisibility_sources(f);before=f.get('invisible',False)
    for source,until in list(sources.items()):
        if until is not None and until<round:del sources[source]
    f['invisible']=bool(sources)
    visible_actor=not f.get('removed') and (f.get('kind')!='shadow' or f['hp']>0)
    return (nums(2138,f['id'],45),) if before and not f['invisible'] and visible_actor else ()

def input_status_packets(fighters,round):
    # Selection starts before Runtime.begin_round resolves submitted orders.
    packets=[]
    for f in fighters.values():
        expired=expire_invisibility(f,round)
        if not f.get('removed'):packets.extend(expired)
    return tuple(packets)

def control_status_packets(f,round):
    if f.get('removed') or f.get('kind')=='shadow' and f['hp']<=0:return ()
    statuses={STATUS[key] for key,until in f.get('controls',{}).items() if key in STATUS and until>=round}
    statuses.update(BUFF_STATUS[b['key']] for b in f.get('role_buffs',())
                    if b['key'] in BUFF_STATUS and b['key'].startswith('race_fabao_') and b['until']>=round)
    if f.get('invisible'):statuses.add(45)
    return tuple(nums(2130,f['id'],status,0) for status in sorted(statuses))

class Runtime:
    def __init__(self,fighters,games,round,rng,state):
        self.fighters=fighters;self.games=games;self.round=round;self.rng=rng;self.state=state;self.out=[]
        self.owner_packets = {cid: [] for cid in games}
        self.sent_power = {}
        self._presentation_actor = None
        self._pending_anger = {}
        self.protectors = {}
        self.defending = set()
        for f in fighters.values():self.prepare(f)
    def prepare(self,f):
        # Units may join after Runtime construction, during another actor's turn.
        f.setdefault('controls',{});f.setdefault('role_buffs',[]);f.setdefault('role_shields',{});f.setdefault('role_stacks',{});f.setdefault('role_cooldowns',{});f.setdefault('role_anger',RULE['anger_start']);f.setdefault('role_stick',0)
        f.setdefault('role_wrath',RULE['wrath_start'])
        if f.get('kind')=='hero' and not f.get('role_initialized'):
            f['role_initialized']=1;f['role_stick_max']=stick_capacity(f,self.rng)
        if f['id'] not in self.sent_power:
            self.sent_power[f['id']] = self.power_values(f)
        from native946_innate_effects import prepare as innate_prepare
        innate_prepare(self,f)
    def living(self,camp=None,dead=False):return [f for f in self.fighters.values() if not f.get('removed') and (camp is None or f['camp']==camp) and ((f['hp']<=0) if dead else (f['hp']>0))]
    def allies(self,a,dead=False):return self.living(a['camp'],dead)
    def enemies(self,a):
        from native946_innate_effects import has as has_innate
        return [f for f in self.living() if f['camp']!=a['camp'] and (not f.get('invisible') or a.get('role_reveal',0)>0 or has_innate(a,4733))]
    def pvp(self,a,t):return bool(a.get('owner') and t.get('owner') and a['camp']!=t['camp'])
    def invisibility_sources(self,f):
        return invisibility_sources(f)
    def grant_invisibility(self,f,source,until):
        sources=self.invisibility_sources(f)
        if source not in sources:sources[source]=until
        elif sources[source] is not None:sources[source]=max(sources[source],until)
        f['invisible']=True;self.out.append(nums(2130,f['id'],45,0))
    def expire_invisibility(self,f):
        self.out.extend(expire_invisibility(f,self.round))
    def clear_invisibility(self,f):
        before=f.get('invisible',False);f['role_invisibility_sources']={};f.pop('role_formation_invisible_until',None);f['invisible']=False
        if before:self.out.append(nums(2138,f['id'],45))
    def proc(self,a,skill,mult=1):return bool(level(a,skill) and self.rng.randrange(1000)<min(900,level(a,skill)*RULE['talent_proc_per_grade']*mult))
    def child_level(self,a,skill):
        b=a.get('role_child') or {};return int(b.get('skills',{}).get(str(skill),0))
    def child_proc(self,a,*skills):
        choices=[(self.child_level(a,k)+(10 if k in ADVANCED else 0),k) for k in skills if self.child_level(a,k)]
        if not choices:return 0
        _,key=max(choices);prob=RULE['child_proc_base']+self.child_level(a,key)*RULE['child_proc_per_grade']+(RULE['child_advanced_bonus'] if key in ADVANCED else 0)
        return key if self.rng.randrange(1000)<min(850,prob) else 0
    def group(self,a,targets,skill,child=False):
        if not targets:return
        self._presentation_actor=a['id']
        self.out.append(nums(2126,1));self.out.append(packet(2134 if child else 2157,['number','number',('array','number'),'number'],[a['id'],targets[0]['id'],[t['id'] for t in targets],skill]))
        if not child and a.get('kind')=='hero':
            g=self.games.get(a.get('owner',a['id']))
            if g:
                from native946_role_growth import color_packet
                from native946_cosmetics import Cosmetics
                self.out.extend(color_packet(g,skill));self.out.append(Cosmetics(g).actor_skin(skill))
    def end(self):
        pending=self.__dict__.get('_guardian_deaths',[])
        for item in list(pending):
            if not any(e['target']['hp']>0 and not e['target'].get('removed') for e in item[2]):
                pending.remove(item);self.kill(item[0],item[1],active=item[3])
        self.out.append(nums(2126,2))
        self._presentation_actor=None
        for fid,message in self.__dict__.pop('_presentation_deaths',{}).items():
            if self.fighters[fid]['hp']<=0:self.out.append(message)
        self.out.extend(nums(2158,fid,8,anger) for fid,anger in self._pending_anger.items())
        self._pending_anger.clear()
        self.guardian_deaths()
    def guardian_deaths(self):
        pending=self.__dict__.pop('_guardian_deaths',[])
        for attacker,victim,events,active in pending:
            events=[event for event in events if event['target']['hp']>0 and not event['target'].get('removed')]
            for index,event in enumerate(events):
                source,target=event['source'],event['target']
                if target['hp']<=0 or target.get('removed'):continue
                self.group(source,[target],event['skill'])
                if event['skill']==4922:
                    if event['miss']:self.out.append(nums(2144,target['id']))
                    else:
                        if event['effect']:self.out.append(nums(2162,target['id'],event['effect']))
                        self.damage(source,target,event['damage'],'physical',active=False)
                else:
                    actual=min(-event['damage'],max(0,target['hpmax']-target['hp']))
                    target['hp']+=actual
                    self.out.append(nums(2164,target['id'],actual,1,0));self.bars(target)
                self.end()
                # A source's own death must follow act_end. Embedding it in
                # its cast makes the native actor die during the return move.
                # A standalone do_fighter_die runs after that move completes.
                if index==len(events)-1:self.kill(attacker,victim,active=active)
    @staticmethod
    def power_values(f):
        # c_set_hpmp selectors. Update ceilings first, then the current values.
        values = {
            3: f['hpmax'],
            2: f.get('hp_limit',f['hpmax']),
            6: f['mpmax'],
            5: f['mpmax'],
            1: f['hp'],
            4: f['mp'],
        }
        if f.get('kind') == 'hero':
            values.update({7: f['role_wrath'], 8: f['role_anger']})
        return values

    def bars(self,f,*,initialize=False):
        fighter_id = f['id']
        current = self.power_values(f)
        if initialize:
            self.out.append(nums(2148,fighter_id,*(current[i] for i in range(1,7)),f['role_anger'],RULE['anger_max'],0))
        else:
            previous = self.sent_power[fighter_id]
            for index,value in current.items():
                if previous[index]==value:
                    continue
                if index==8 and self._presentation_actor is not None:
                    # Any in-group 2158 marks its fighter hit in the 946 client.
                    self._pending_anger[fighter_id] = value
                else:
                    self.out.append(nums(2158,fighter_id,index,value))
        self.sent_power[fighter_id] = current
    def buff(self,f,key,amount,duration=3,source=0,permanent=False,locked=False):
        until=10**9 if permanent else self.round+duration-1
        previous=next((v for v in f['role_buffs'] if v['key']==key and v['source']==source),None)
        if previous:
            f[key]=f.get(key,0)-previous['amount'];f['role_buffs'].remove(previous)
        f['role_buffs'].append(dict(key=key,amount=int(amount),until=until,source=source,locked=int(locked)));f[key]=f.get(key,0)+int(amount)
        if amount and key in BUFF_STATUS:self.out.append(nums(2130,f['id'],BUFF_STATUS[key],0))
    def buff_value(self,f,key):return sum(b['amount'] for b in f['role_buffs'] if b['key']==key)
    def shield(self,t,family):
        family={'disorder':'control_poison','poison':'control_poison','seal':'control_sleep','faint':'control_sleep'}.get(family,family)
        n=t['role_shields'].get(family,0)
        if n:
            t['role_shields'][family]=n-1
            if family in SHIELD_STATUS:self.out.append(nums(2138,t['id'],SHIELD_STATUS[family]))
            return True
        return False
    def cleanse(self,a,t,keys):
        cleared=False
        for key in list(t['controls']):
            if key not in keys:continue
            info=t.setdefault('role_control_info',{}).get(key,{})
            if info.get('escape_reduce') and self.rng.randrange(1000)<info['escape_reduce']:continue
            del t['controls'][key];cleared=True
            t.get('role_control_info',{}).pop(key,None)
            if key in STATUS:self.out.append(nums(2138,t['id'],STATUS[key]))
        for effect in list(t.get('role_dots',[])):
            if effect['kind'] in keys:t['role_dots'].remove(effect);cleared=True
        if 'poison' in keys and not any(v['kind']=='poison' for v in t.get('role_dots',[])):self.out.append(nums(2138,t['id'],106))
        if 'gushu' in keys and t.pop('role_gushu',None):cleared=True;self.out.append(nums(2138,t['id'],167))
        return cleared
    def revive(self,a,t,hp,mp=0):
        from native946_innate_effects import has as has_innate
        if t['hp']>0 or t.get('no_revive_until',0)>=self.round or has_innate(t,4736):return False
        previous_mp=t['mp']
        t['hp']=min(t.get('hp_limit',t['hpmax']),max(1,hp));t['mp']=min(t['mpmax'],max(t['mp'],mp))
        from native946_zhuziguo_bell import revived
        revived(self.state,t)
        self.__dict__.get('_presentation_deaths',{}).pop(t['id'],None)
        self.out.extend((nums(2145,t['id']),nums(2164,t['id'],t['hp'],1,0)))
        if t['mp']!=previous_mp:self.out.append(nums(2164,t['id'],t['mp']-previous_mp,2,0))
        self.bars(t);return True
    def mana(self,t,amount):
        before=t['mp'];t['mp']=max(0,min(t['mpmax'],before+amount));actual=t['mp']-before
        if actual:self.out.append(nums(2164,t['id'],actual,2,0))
        self.bars(t);return actual
    def heal(self,a,t,amount,*,revive=False,restore_limit=0,max_recovery=None):
        from native946_guardian_innate import ZHONGLING_YUXIU
        from native946_innate_effects import has as has_innate
        if restore_limit:t['hp_limit']=min(t['hpmax'],t.get('hp_limit',t['hpmax'])+restore_limit)
        if t['hp']<=0:
            if not revive:return 0
            return int(self.revive(a,t,amount))*amount
        before=t['hp'];factor=1000+value(a,'XQAddCureHpP')+value(t,'RoleCureReceivedPercent')
        factor+=a.get('new_neidan_power',{}).get(284,0)//10
        if has_innate(a,ZHONGLING_YUXIU):factor+=200
        amount=max(0,amount*factor//1000);amount=amount*max(0,1000-t.get('role_heal_reduce',0))//1000
        if self.child_proc(a,771) and t['hp']*10<t['hpmax']*3:amount*=2
        if max_recovery is not None:amount=min(amount,max_recovery)
        if restore_limit:t['hp_limit']=min(t['hpmax'],t.get('hp_limit',t['hpmax'])+restore_limit)
        t['hp']=min(t.get('hp_limit',t['hpmax']),t['hp']+amount);self.out.append(nums(2164,t['id'],t['hp']-before,1,0));self.bars(t);return t['hp']-before
    def kill(self,a,t,*,active=False):
        if t['hp']>0:return
        if t['role_shields'].get('endure',0):self.shield(t,'endure');t['hp']=1;self.bars(t);return
        from native946_guardian_passives import ready,death_effects
        if ready(t):
            events=death_effects(t,a,self.fighters.values(),self.rng,apply=False,runtime=self)
            if events:
                self.__dict__.setdefault('_guardian_deaths',[]).append((a,t,events,active))
                return
        t['role_stacks'].clear()
        t['role_death_round']=self.round
        t['role_stick']=0
        self.out.extend(stick_packets(t))
        for b in list(t['role_buffs']):
            if b['until']==10**9:t[b['key']]-=b['amount'];t['role_buffs'].remove(b)
        # Ordinary monsters have no revival action. Native mode 3 removes
        # their view immediately; special encounters and owned fighters keep corpses.
        die_mode=3 if t.get('exit_on_defeat') else 2
        if die_mode==3:t['removed']=True
        death=nums(2142,t['id'],die_mode)
        if self.__dict__.get('_presentation_actor')==t['id']:
            self.__dict__.setdefault('_presentation_deaths',{})[t['id']]=death
        else:self.out.append(death)
        from native946_zhuziguo_bell import defeated
        self.out.extend(defeated(self.state,t))
        if a.get('_role_kill_round')!=self.round:
            a['_role_kill_round']=self.round
            stacks=a['role_stacks'].get('7141',0)
            if level(a,7141) and stacks<4:a['role_stacks']['7141']=stacks+1;self.buff(a,'role_physical_stack',(stacks+1)*level(a,7141)*5,source=7141,permanent=True)
            if self.proc(a,7504):
                a['role_stick']=min(a.get('role_stick_max',2),a['role_stick']+1)
                self.out.extend(stick_packets(a))
        for f in self.fighters.values():
            if f.get('shadow_owner')==t['id']:f['removed']=True;self.out.append(nums(2137,f['id']))
        if t.get('kind')=='pet' and t.get('role_death_round')==self.round:
            self.flash_guardian(t)
        from native946_innate_effects import has,stunt_scale
        if active and a['camp']!=t['camp'] and a['hp']>0 and has(a,4908) and a.get('role_bell_follow_round')!=self.round:
            targets=self.enemies(a)
            if targets and self.rng.randrange(10000)<min(10000,10192*stunt_scale(a,4908)//10000):
                a['role_bell_follow_round']=self.round
                self.attack(a,targets[self.rng.randrange(len(targets))])

    def flash_guardian(self,victim):
        from native946_guardian_magic import skills_packet
        from native946_innate_effects import has,stunt_scale
        owner=victim.get('owner')
        game=self.games.get(owner)
        if game is None or victim.get('removed'):return
        used=set(self.fighters)
        speed_bonus=7000 if has(victim,4735) else 0
        for incoming in game.summons.snapshot():
            if incoming['id'] in used or incoming['hp']<=0 or incoming.get('honesty',100)<50:continue
            chance=3057*stunt_scale(incoming,4630)//10000 if has(incoming,4630) else 2500 if has(incoming,4144) else 0
            if not (chance or speed_bonus):continue
            chance+=speed_bonus+incoming.get('new_neidan_power',{}).get(279,0)
            speed_bonus=0
            if self.rng.randrange(10000)>=min(10000,chance):continue
            victim['removed']=True
            self.out.append(nums(2137,victim['id']))
            wild=getattr(game,'_wild_battle',None)
            if wild and wild.state is self.state:
                wild.add_pet(game,incoming,victim.get('slot',6))
                incoming=self.fighters[incoming['id']]
            else:
                game.summons.set_vitals(victim['id'], victim['hp'], victim['mp'])
                incoming.update(owner=owner,kind='pet',camp=victim['camp'],slot=victim.get('slot',6))
                self.fighters[incoming['id']]=incoming
                game.battle['pet']=incoming
                game.battle['participants'][incoming['id']] = incoming
            self.owner_packets[owner].append(nums(4206, incoming['id']))
            self.out.append(game.fighter(incoming['id'],incoming['type'],incoming['name'],incoming['camp'],3,incoming['slot'],incoming['level'],mid=2128))
            self.out.append(skills_packet(incoming))
            self.prepare(incoming)
            self.bars(incoming,initialize=True)
            return
    def owner_jade_bonus(self,actor,trait):
        if actor.get('kind')!='hero':return 0
        owner=actor.get('owner',actor['id'])
        return sum(max(pet.get('cor',0),pet.get('speed',0),pet.get('strength',0),pet.get('mag',0))
                   *pet.get('jade_skills',{}).get(trait,0)//1000
                   for pet in self.allies(actor) if pet.get('kind')=='pet' and pet.get('owner')==owner)

    def apply_jade_spell_debuff(self,actor,targets):
        chance=actor.get('jade_skills',{}).get(15,0)
        if not chance or self.rng.randrange(1000)>=chance:return
        for target in targets:
            if target['hp']<=0 or target.get('removed'):continue
            current=next((entry['amount'] for entry in target['role_buffs'] if entry['source']==4661),0)
            self.buff(target,'role_heal_reduce',min(300,current+100),3,4661)

    def damage(self,a,t,amount,family='physical',*,primary=True,penetration=0,hit=True,allow_protection=True,active=True,minimum=0):
        if t['hp']<=0:return 0
        family='physical' if family in ('physical','wuli','pierce','stick','dispel_strike') else family
        offense=a.get('new_neidan_power',{})
        defense=t.get('new_neidan_power',{})
        if family=='execute' and defense.get(244,0) and self.rng.randrange(10000)<defense[244]:return 0
        shield=family if family in CONTROLS or family in ('poison','gushu','physical') else 'element' if family in ELEMENTS else family
        breaker=769 if family in ELEMENTS else 770 if family=='physical' else 0
        breaks=primary and breaker and self.child_level(a,breaker)>0
        blocked=self.shield(t,shield)
        if not blocked and shield in ('physical','element'):
            blocked=self.shield(t,'peach') or self.shield(t,'bell_'+shield)
        if blocked and not breaks:self.out.append(nums(2144,t['id']));return 0
        if blocked:amount=amount*(10000-defense.get(285,0))//10000
        from native946_innate_effects import before_damage as innate_before_damage
        amount=innate_before_damage(self,a,t,amount,family)
        if amount<=0:return 0
        outgoing=a.get('role_damage_percent',0)+a.get('role_physical_stack' if family=='physical' else 'role_spell_stack',0)
        from native946_guardian_innate import ZHONGLING_YUXIU
        from native946_innate_effects import has as has_innate
        if family in ELEMENTS:
            amount+=self.owner_jade_bonus(a,13)
            if a.get('role_dragon_rounds'):
                from native946_innate_effects import dragon_round_bonus
                outgoing+=a['role_dragon_rounds']*dragon_round_bonus(a)
            if has_innate(a,ZHONGLING_YUXIU):outgoing+=200
        if family=='physical' and a.get('invisible'):outgoing+=offense.get(283,0)//10
        if any(t['role_shields'].values()) or any(t.get('role_absorb_hp',{}).values()):outgoing+=offense.get(281,0)//10
        if has_innate(a,4737) and t.get('kind') in ('pet','enemy'):outgoing+=500
        if has_innate(a,4745) and family in ('physical',*ELEMENTS):outgoing+=200
        outgoing+=value(a,'RoleLineupDamage')+value(a,'RoleLineupRestrain')+value(a,'RoleLineupPhysical' if family=='physical' else 'RoleLineupSpell')
        incoming=t.get('role_taken_percent',0)+t.get('role_physical_taken' if family=='physical' else 'role_spell_taken',0)
        if family in ELEMENTS:
            incoming+=t.get('role_element_taken',0)
        outgoing+=a.get('race_fabao_physical' if family=='physical' else 'race_fabao_spell',0)
        jade=a.get('jade_skills',{})
        defensive_jade=t.get('jade_skills',{})
        if family=='physical' or family in ELEMENTS:
            health=a['hp']*1000//max(1,a['hpmax'])
            if family=='physical':outgoing+=jade.get(2,0)*(1000-health)//1000+jade.get(4,0)*int(health>700)
            else:
                outgoing+=jade.get(3,0)*(1000-health)//1000+jade.get(5,0)*int(health>700)
                if a.get('initiative',0)>t.get('initiative',0):outgoing+=jade.get(6,0)
            key=11 if family=='physical' else 10
            if factor:=defensive_jade.get(key):
                counts=t.setdefault('role_jade_hit_count',{})
                round_seen,count=counts.get(key,(-1,0))
                count=count if round_seen==self.round else 0
                if count<2:incoming-=factor//(count+1)
                counts[key]=(self.round,count+1)
        incoming-=t.get('race_fabao_physical_reduction' if family=='physical' else 'race_fabao_spell_reduction',0)
        amount=amount*(1000+outgoing)//1000;amount=amount*max(0,1000+incoming)//1000
        if a.get('role_anti_books'):
            names=[DATA['native_skills'].get(str(row.get('skill')),{}).get('name','') for row in t.get('learned_books',[])]
            if any(n in ('武动乾坤','幻影迷踪') for n in names):amount=amount*(1000+a['role_anti_books'])//1000
        if family=='physical':amount=amount*max(0,1000-level(t,7301)*10-t.get('role_guard_percent',0))//1000
        elif family in ELEMENTS:amount=amount*max(0,1000-level(t,7015)*10)//1000
        amount=amount*max(0,1000-value(t,'RoleLineupPhysicalReduction' if family=='physical' else 'RoleLineupSpellReduction'))//1000
        if family=='poison':amount=max(0,amount-value(t,'RoleLineupPoisonReduction')-value(t,'EquipRePoisonDamage'))
        if family=='physical' or family in ELEMENTS:
            reduction=value(t,'PhyAbsorbDegree' if family=='physical' else 'ImmAbsorbDegree')
            ignore=value(a,'IgPhyAbsorb' if family=='physical' else 'IgImmAbsorb')
            if family=='physical':ignore+=min(jade.get(1,0),a.get('cor',0)//10)
            if family in ELEMENTS and has_innate(a,4628):
                from native946_innate_effects import resistance_pierce
                ignore+=max(0,reduction)*resistance_pierce(a)//10000
            if family in ELEMENTS and has_innate(a,4542):
                from native946_innate_effects import stunt_scale
                factor=1000+min(50000,a.get('amity',0))//200
                ignore+=max(0,reduction)*3233*factor*stunt_scale(a,4542)//100_000_000_000
            if t['hp']*10<t['hpmax']*4 and self.child_level(t,772 if family=='physical' else 773):ignore//=2
            remaining=reduction if reduction<0 else max(0,reduction-ignore)
            amount=amount*max(0,1000-max(-900,remaining))//1000
        if family in ELEMENTS:
            amount=max(0,amount+value(a,'EquipElementDamage')-value(t,'EquipElementResist'))
        if t.get('role_luohan') and family in (*ELEMENTS,'shock','poison'):amount=amount*7//10
        amount=amount*max(0,1000-t.get('role_all_reduction',0))//1000
        amount=max(minimum,amount)
        if level(t,7213):
            available=max(0,t['mp']-t['mpmax']//5);absorbed=min(available,amount*min(500,level(t,7213)*20)//1000);t['mp']-=absorbed;amount-=absorbed
            if absorbed:amount=amount*(10000-defense.get(285,0))//10000
        share=t.get('role_damage_share',{})
        source=self.fighters.get(share.get('source'))
        if source and source['id']!=t['id'] and source['hp']>0 and not source.get('removed') and share.get('until',0)>=self.round:
            transferred=amount*share['percent']//1000;amount-=transferred
            self.damage(a,source,transferred,family,primary=False,allow_protection=False,active=active)
        if family=='physical':amount=self.defended_damage(t,amount)
        if family=='physical':amount+=a.get('strength',0)*offense.get(254,0)//10000
        elif family in ELEMENTS:amount+=a.get('mag',0)*offense.get(252,0)//10000
        if allow_protection and family=='physical' and amount>=2 and a['camp']!=t['camp']:
            from native946_combat_control import blocked
            guard=next((f for f in self.protectors.get(t['id'],())
                        if f['hp']>0 and not f.get('removed') and not blocked(f,self.round)),None)
            if guard:
                # The 946 client shows the pair. The split is the local combat rule.
                transferred=min(guard['hp'],amount//2)
                self.out.append(nums(2159,t['id'],guard['id']))
                self.apply_damage(a,guard,self.defended_damage(guard,transferred),family,0,active=active)
                amount-=transferred
        return self.apply_damage(a,t,amount,family,breaker if breaks else 0,active=active)

    def defended_damage(self,target,amount):
        if target['id'] in self.defending and amount>0:
            self.out.append(nums(2141,target['id']))
            return max(1,amount//2)
        return amount

    def gain_wrath(self, attacker, target, hp_lost):
        # Classic 3.0.666: one full HP bar lost grants 1000 wrath.
        # Classic 3.0.717: damage attributed to the recipient does not count.
        if target['kind'] == 'hero' and attacker['id'] != target['id']:
            gained = hp_lost * RULE['wrath_per_full_hp'] // max(1, target['hpmax'])
            self.restore_wrath(target, gained)

    def restore_wrath(self, target, amount):
        target['role_wrath'] = min(RULE['wrath_max'], target['role_wrath'] + amount)

    def apply_damage(self,a,t,amount,family,breaker,*,active=True):
        from native946_innate_effects import lethal_guard
        if family=='physical' or family in ELEMENTS:
            shields=t.get('role_absorb_hp',{})
            for key in sorted(shields):
                absorbed=min(shields[key],max(0,amount))
                shields[key]-=absorbed
                amount-=absorbed
                if absorbed:
                    amount=amount*(10000-t.get('new_neidan_power',{}).get(285,0))//10000
                if amount<=0:break
        amount=lethal_guard(self,t,amount)
        t['role_last_damage_family']=family
        floor=int(bool(t.get('capture_training')))
        actual=min(t['hp']-floor,max(0,int(amount)));t['hp']-=actual
        if actual:
            # Classic 3.0.921, full-server combat balance.
            if family=='water':
                t['hp_limit']=max(1,t.get('hp_limit',t['hpmax'])-actual*WATER_HP_LIMIT_PERCENT//100)
            self.out.append(nums(2164,t['id'],-actual,1,0))
            if 'faint' in t['controls']:
                sleep=t.get('role_control_info',{}).get('faint',{})
                threshold=max(sleep.get('wake_threshold',0),(t['hpmax']*sleep.get('wake_hp_percent',0)+99)//100)
                if actual>=max(1,threshold):self.cleanse(a,t,('faint',))
            a['role_anger']=min(RULE['anger_max'],a['role_anger']+1)
            self.gain_wrath(a, t, actual)
        self.bars(t)
        if actual and a is not t:self.bars(a)
        if breaker and actual:
            if breaker==769:a['mp']=min(a['mpmax'],a['mp']+actual//5);self.bars(a)
            else:self.heal(a,a,actual//5)
        from native946_innate_effects import after_damage as innate_after_damage
        innate_after_damage(self,a,t,actual,family)
        if actual and family in (*ELEMENTS,'poison') and a['hp']>0:
            life=actual*a.get('new_neidan_power',{}).get(282,0)//10000
            if life:self.heal(a,a,life)
        self.kill(a,t,active=active)
        if actual and a['id']!=t['id'] and t.get('role_reflect') and a['hp']>0:
            reflected=actual*t['role_reflect']//1000
            if family=='physical':reflected=self.defended_damage(a,reflected)
            reflected=min(a['hp'],lethal_guard(self,a,reflected))
            if reflected:
                a['hp']-=reflected
                self.gain_wrath(t, a, reflected)
                self.out.append(nums(2164,a['id'],-reflected,1,0));self.kill(t,a);self.bars(a)
        reflect=value(t,'EquipReflectChance')
        if actual and reflect and family=='physical' and a['id']!=t['id'] and a['hp']>0:
            power=value(t,'EquipReflectPower')
            if power and self.rng.randrange(1000)<min(1000,reflect):
                reflected=min(a['hp'],lethal_guard(self,a,self.defended_damage(a,actual*min(1000,power)//1000)))
                if reflected:
                    a['hp']-=reflected
                    self.gain_wrath(t, a, reflected)
                    self.out.append(nums(2164,a['id'],-reflected,1,0))
                    self.kill(t,a);self.bars(a)
        self.bars(t);return actual
    def control(self,a,t,kind,*,primary=True,duration=CONTROL_ROUNDS,base=700):
        if kind=='seal' and t.get('race_fabao_seal_immunity'):
            self.out.append(nums(2144,t['id']));return False
        if self.shield(t,kind) or self.shield(t,'bell_control'):self.out.append(nums(2144,t['id']));return False
        resistance=value(t,RESIST.get(kind,''))+t.get('role_control_resist',0)
        resistance=max(0,resistance-value(a,'Ig'+kind.title()))
        if t['hp']*10<t['hpmax']*4 and self.child_level(t,774):resistance+=100
        from native946_guardian_magic import control_chance
        chance=control_chance(base,t,kind) if kind in ('seal','faint','disorder') else base
        chance=chance-resistance+value(a,'RoleControl')+value(a,'Add'+kind.title())+value(a,'AddControllerDegree')-value(t,'ReControllerDegree')
        cap=950
        if primary:chance+=level(a,7101)*3;cap=min(999,cap+level(a,7101)*3)
        if kind=='disorder' and t.get('kind') in ('pet','enemy'):chance+=level(a,7113)*5
        if kind=='seal' and t.get('kind') in ('pet','enemy'):chance+=level(a,7142)*5
        if kind=='wenxin' and self.pvp(a,t):chance=max(chance,100+level(a,7323)*5)
        chance=max(0,min(cap,chance))
        if kind in ('disorder','seal','faint'):chance=chance*max(0,1000-value(t,'RoleLineupControlResist'))//1000
        if self.rng.randrange(1000)>=chance:self.out.append(nums(2144,t['id']));return False
        t['controls'][kind]=max(t['controls'].get(kind,0),self.round+duration-1)
        info=dict(source=a['id'],escape_reduce=level(a,7112)*10)
        if kind=='faint' and self.pvp(a,t):
            depth=level(a,7122)
            if depth>=10:info['wake_hp_percent']=10
            else:info['wake_threshold']=depth*100
        if kind=='disorder':info['force_friendly']=int(self.proc(a,7111))
        t.setdefault('role_control_info',{})[kind]=info
        if kind in STATUS:self.out.append(nums(2130,t['id'],STATUS[kind],0))
        if kind=='faint':
            fatigue=level(a,7123);nightmare=level(a,7124)
            if fatigue:self.buff(t,'role_damage_percent',-300 if fatigue>=10 else -fatigue*10,2 if fatigue>=10 else duration,7123)
            if primary and nightmare:
                self.buff(t,'role_taken_percent',450 if nightmare>=10 else nightmare*10,1 if nightmare>=10 else duration,7124)
        if kind=='disorder' and t.get('kind')=='enemy' and level(a,7001):self.damage(a,t,t['hpmax']*level(a,7001)//1000,'poison')
        if kind=='wenxin' and self.proc(a,7035):self.shock(a,t,primary=primary)
        from native946_innate_effects import after_control as innate_after_control
        innate_after_control(self,a,t)
        return True
    def shock(self,a,t,primary=True,boost=0):
        rate=shock_rate(a,t,boost);damage=t['hp']*rate//1000
        minimum=0
        if self.pvp(a,t):
            bloodlust=level(a,7341)
            if bloodlust>=10:
                minimum=t['hp']//10  # Classic 3.0.921
                damage=max(damage,minimum)
            else:damage=max(damage,t['hp']*bloodlust*5//1000)
        actual=self.damage(a,t,damage,'shock',primary=primary,minimum=minimum)
        if not self.child_proc(t,739,740):self.mana(t,-(t['mp']*rate//1000))
        leech=level(a,7342)
        if primary and leech and actual:
            if leech>=10:
                self.heal(a,a,actual*15//100,max_recovery=a['hpmax']*30//100)
            else:self.heal(a,a,actual*leech//100)
        return actual
    def child_begin(self,a):
        if a['hp']<=0 or not a.get('role_child'):return
        for skills,key,amount in (((703,704),'initiative',200),((715,716),'ReWater',250),((717,718),'ReFire',250),((719,720),'ReThunder',250),((721,722),'ReWind',250),((741,742),'defense',500)):
            skill=self.child_proc(a,*skills)
            if skill:self.group(a,[a],skill,True);self.buff(a,key,amount,1,skill);self.end()
        for skills,keys in (((733,734),('disorder',)),((735,736),('seal',)),((737,738),('poison',)),((743,744),('gushu',))):
            if not (any(k in a['controls'] for k in keys) or any(v['kind'] in keys for v in a.get('role_dots',[])) or ('gushu' in keys and a.get('role_gushu'))):continue
            skill=self.child_proc(a,*skills)
            if skill:self.group(a,[a],skill,True);self.cleanse(a,a,keys);self.end()
        for base,families in ((745,('control_poison',)),(746,('control_sleep',)),(747,('element',)),(748,('physical',))):
            skill=self.child_proc(a,base,base+12)
            if skill:
                self.group(a,[a],skill,True)
                for f in families:a['role_shields'][f]=1;self.out.append(nums(2130,a['id'],SHIELD_STATUS[f],0))
                self.end()
        dead=[f for f in self.allies(a,True) if f['id']!=a['id'] and f.get('kind')!='shadow' and f.get('no_revive_until',0)<self.round]
        skill=self.child_proc(a,749,750,761,762) if dead else 0
        if skill:
            t=dead[0];self.group(a,[t],skill,True);self.revive(a,t,t['hpmax']//4,t['mpmax']//4 if skill in (750,762) else 0);self.end()
        pets=[f for f in self.allies(a) if f.get('kind')=='pet' and f.get('owner')==a.get('owner',a['id'])]
        for base,key in ((751,'hpmax'),(752,'endure'),(753,'attack'),(754,'initiative')):
            skill=self.child_proc(a,base,base+12) if pets else 0
            if skill:
                self.group(a,pets,skill,True)
                for t in pets:
                    if key=='endure':t['role_shields']['endure']=1;t['role_endure_until']=self.round+2;self.out.append(nums(2130,t['id'],170,0))
                    else:self.buff(t,key,max(1,t[key]//10),1,skill)
                    self.bars(t)
                self.end()
        enemies=self.enemies(a)
        for base,artifact,kind in ((755,'绝情鞭','entangle'),(756,'金箍儿','jin_gu')):
            if not enemies or not a.get('role_artifacts',{}).get(artifact) or a['role_cooldowns'].get(str(base),0)>self.round:continue
            skill=self.child_proc(a,base,base+12)
            if skill:
                from native946_role_artifacts import spend
                try:spend(self.db,a['id'],128 if base==755 else 131)
                except ValueError:continue
                self.group(a,[enemies[0]],skill,True)
                if base==755:self.buff(enemies[0],'role_mp_drain',max(1,a['attack']//5),3,128)
                else:self.control(a,enemies[0],kind,duration=1)
                a['role_cooldowns'][str(base)]=self.round+3;self.end()
    def begin_round(self):
        if self.state.get('role_started_round')==self.round:return
        self.state['role_started_round']=self.round
        if self.state.get('role_weather',{}).get('until',self.round)<self.round:self.state.pop('role_weather',None);self.out.append(nums(2172,0))
        for a in list(self.fighters.values()):
            for b in list(a['role_buffs']):
                if b['until']<self.round:
                    a[b['key']]=a.get(b['key'],0)-b['amount'];a['role_buffs'].remove(b)
                    if b['key'] in BUFF_STATUS and not any(v['key']==b['key'] for v in a['role_buffs']):self.out.append(nums(2138,a['id'],BUFF_STATUS[b['key']]))
            a['hp']=min(a['hp'],a['hpmax']);a['mp']=min(a['mp'],a['mpmax'])
            if not a.get('removed'):self.bars(a)
            for key,until in list(a['controls'].items()):
                if until<self.round:
                    del a['controls'][key];a.get('role_control_info',{}).pop(key,None)
                    self.out.extend((nums(2138,a['id'],STATUS[key]),) if key in STATUS else ())
            self.expire_invisibility(a)
            lamp = a.get('role_liuli_lamp')
            if lamp and lamp['until'] < self.round:
                a.pop('role_liuli_lamp')
                lamp = None
            if a['hp']<=0 or a.get('removed'):continue
            if lamp and lamp['start'] <= self.round:
                self.restore_wrath(a, lamp['wrath'])
                self.bars(a)
            from native946_innate_effects import has,stunt_scale
            if has(a,4619):a['role_dragon_rounds']=min(4,a.get('role_dragon_rounds',0)+1)
            if self.round%2==0:
                if has(a,4656):a.setdefault('role_absorb_hp',{})[4656]=a.get('strength',0)*40*stunt_scale(a,4656)//10000
            if a.get('role_endure_until',0)<self.round and a['role_shields'].pop('endure',None):self.out.append(nums(2138,a['id'],170))
            if a.get('role_gushu',{}).get('until',self.round)<self.round:a.pop('role_gushu',None);self.out.append(nums(2138,a['id'],167))
            for dot in list(a.get('role_dots',[])):
                if dot['until']<self.round:a['role_dots'].remove(dot);continue
                source=self.fighters.get(dot['source'],a);self.group(source,[a],dot['skill']);self.damage(source,a,dot['damage'],dot['kind'],active=False);self.end()
            if not any(v['kind']=='poison' for v in a.get('role_dots',[])) and a.pop('role_poison_icon',False):self.out.append(nums(2138,a['id'],106))
            if a['hp']<=0:continue
            if a.get('role_mana_regen'):self.mana(a,a['role_mana_regen'])
            if a.get('role_hp_regen'):self.heal(a,a,a['role_hp_regen'])
            if value(a,'EquipMpRegen') and a['mp']<a['mpmax']:self.mana(a,value(a,'EquipMpRegen'))
            if value(a,'EquipHpRegen') and a['hp']<a.get('hp_limit',a['hpmax']):self.heal(a,a,value(a,'EquipHpRegen'))
            if a.get('role_mp_drain'):self.mana(a,-a['role_mp_drain'])
            focus=level(a,7033)
            if focus and any(self.pvp(a,t) for t in self.fighters.values() if not t.get('removed')):
                self.mana(a,a['mpmax']*(50 if focus>=10 else focus)//1000)
            if a.get('kind')=='hero':self.child_begin(a)
        from native946_lineups import begin_round as formation_begin
        formation_begin(self)

    def control_blocked(self,a,casting=False):
        for key in ('seal','faint','wenxin','soul_seal'):
            if a['controls'].get(key,0)>=self.round:return True
        return bool(casting and a['controls'].get('jin_gu',0)>=self.round)
    def targets(self,a,meta,target_id):
        friendly=meta['family'] in FRIENDLY
        selected_target=self.fighters.get(target_id)
        if meta['family']=='cleanse' and selected_target:friendly=selected_target['camp']==a['camp']
        if meta['family']=='dispel_strike' and selected_target:friendly=selected_target['camp']==a['camp']
        candidates=self.allies(a) if friendly else self.enemies(a)
        if not friendly:candidates=[t for t in candidates if not t.get('invisible') or a.get('role_reveal',0)>0]
        if meta['cast_id'] == FEIXUE_WUHEN:
            candidates = [t for t in candidates if self.pvp(a, t)]
        if meta['cast_id']==46:candidates+=self.allies(a,True)
        if meta['family']=='shadow':return [a],1
        if meta['family']=='lamp':return [a],1
        if friendly and not any(t['id']==target_id for t in candidates):target_id=a['id']
        count = base_spell_targets(a,meta)
        skill=0
        if meta['group']:
            skill=self.child_proc(a,*GROUP_CHILD.get(meta['family'],()))
            if skill:count+=1
        family=meta['family'];sid=meta['cast_id']
        if sid==36 and level(a,7021):count+=1
        if sid==74 and level(a,7233):count+=1
        if family=='speed_buff' and self.proc(a,7313):count+=1
        if family=='shock' and (sid==6028 or meta['source']=='secret') and self.proc(a,7344):count+=1
        if sid == FEIXUE_WUHEN:
            count = SPELL_LOCK_TARGETS + (level(a, 7423) >= 10 or self.proc(a, 7423))
        if sid==4556:count=10
        if sid==1708:count+=a.get('role_stick',0)
        if meta['tier']==1 and meta['group']:count=max(3,count)
        selected=sorted(candidates,key=lambda t:(t['id']!=target_id,t['hp']>0 if family=='heal' else 0,t['id']))[:min(10,count)]
        if skill and selected:self.group(a,selected,skill,True);self.end()
        return selected,count
    def action_gushu(self,a,casting):
        effect=a.get('role_gushu')
        if not effect or effect['until']<self.round:return True
        source=self.fighters.get(effect['source'],a)
        self.damage(source,a,effect['cast_damage'] if casting else effect['attack_damage'],'gushu',active=False)
        return a['hp']>0
    def reveal(self,a,t):
        if t.get('invisible') and self.rng.randrange(1000)<min(950,a.get('role_reveal',0)):
            self.clear_invisibility(t)
    def apply_liuguang(self, actor, target, duration):
        self.buff(target, 'role_reveal', liuguang_reveal_rate(actor), duration, 6140)
        rank = level(actor, 7411)
        self.buff(target, 'role_anti_books', 300 if rank >= 10 else rank * 10, duration, 7411)
    def cast_stack(self,a,meta):
        family=meta['family'];sid=meta['cast_id']
        for talent in (7223,7231,7235):
            if not level(a,talent) or a.get('role_cast_round_'+str(talent))==self.round:continue
            if family not in ELEMENTS or not meta['group'] or (talent==7235 and sid not in (44,54,64,74)):continue
            a['role_cast_round_'+str(talent)]=self.round;n=min(8,a['role_stacks'].get(str(talent),0)+1);a['role_stacks'][str(talent)]=n
            self.buff(a,'role_spell_stack',n*level(a,talent)*5,source=talent,permanent=True)
            if talent==7223:self.buff(a,'initiative',n*level(a,talent),source=talent,permanent=True)
    def base_damage(self,a,t,meta):
        if meta['cast_id'] == NIUDAO_XIAOSHI:
            return niudao_damage(a)
        family=meta['family'];sid=meta['cast_id'];practice=int(a.get('skills',{}).get(sid,a.get('skills',{}).get(str(sid),1)))
        if family == 'gushu':
            return gushu_damage(a, t, sid, pvp=self.pvp(a, t))[0]
        base=100+a.get('level',1)*8+practice_damage_bonus(sid,practice)+a.get('mag',a.get('level',1))*2
        if family in ('physical','wuli','pierce','stick','dispel_strike'):
            defense=t.get('defense',0)
            if family=='wuli':defense=defense*max(0,1000-level(a,7043)*10)//1000
            if t['hp']*10<t['hpmax']*4 and self.child_level(t,772):defense+=max(0,t.get('defense',0)-defense)//2
            from native946_training import WULI_BASE_DAMAGE, wuli_damage
            if sid in WULI_BASE_DAMAGE:
                base=wuli_damage(sid,practice,a['attack'],a.get('level',1),max(0,defense))
            else:base=max(1,a['attack']*physical_spell_scale(meta)//1000-defense)
            if family=='pierce':base=base*(1000+level(a,7311)*10*(t.get('kind') in ('pet','enemy')))//1000
            if family=='wuli':base=base*(1000+level(a,7441)*a.get('role_attack_buff',0)//1000)//1000
        elif family in ELEMENTS:
            base=a.get('local_spell_damage',base)
            resist=value(t,RESIST[family]);ignore=value(a,'Ig'+family.title())
            if t['hp']*10<t['hpmax']*4 and self.child_level(t,773):ignore//=2
            # Reference task monsters use signed, flat elemental offsets.
            flat_power=a.get('reference_element_power',{}).get(family,0)
            flat_resist=t.get('reference_element_resist',{}).get(family,0)
            base=max(1,base-max(0,resist-ignore)+flat_power-flat_resist)
            base+=a.get('mag',0)*level(a,7221)//10
            if family=='thunder':base=base*(1000+level(a,7212)*10+(level(a,7212)*10 if t.get('role_luohan') else 0))//1000
            if t['initiative']<a['initiative']:base=base*(1000+level(a,7224)*10)//1000
            if family=='water':base=base*(1000+level(a,7234)*10+(level(a,7234)*10 if t['hp']*2<t['hpmax'] else 0)+(level(a,7236)*10 if t.get('kind')=='pet' else 0))//1000
            if family=='fire' and t['hp']*10>t['hpmax']*7:base=base*(1000+level(a,7242)*10)//1000
        elif family=='poison':base=max(1,base//2)+t['hpmax']*level(a,7131)*RULE['poison_hp_per_grade']//10000
        if family in ELEMENTS:base=base*(1000+value(a,'Add'+family.title())+value(a,'RoleSpellMastery')+a.get('role_element_power',0))//1000
        if family in ('physical','pierce','wuli','stick') and meta['group']:base=base*(1000+value(a,'RoleGroupPhysicalPercent'))//1000
        if family=='poison':base=base*(1000+value(a,'AddPoison')+value(a,'RoleLineupPoison')+value(a,'EquipPoisonDamage'))//1000
        weather=self.state.get('role_weather',{})
        if weather.get('until',0)>=self.round and family in ELEMENTS:
            boosted=('fire','thunder','wind','water','water')[weather['kind']]
            if family==boosted:base=base*(1000+weather.get('grade',1)*100)//1000
        if family=='pierce':base+=a.get('role_pierce_flat',0)
        if sid==36:base+=a.get('role_wuli_flat',0)
        base=base*(1000+a.get('role_feng',{}).get(sid,{}).get('power',0))//1000
        return max(1,base*(1000+value(a,'XQAddHurtHpP'))//1000*max(0,1000-value(t,'XQSubHurtHpP'))//1000)
    def attack(self,a,t,*,active=True):
        if a['hp']<=0 or self.control_blocked(a):return
        if a['controls'].get('entangle',0)>=self.round:return
        if not self.action_gushu(a,False):return
        skill=self.child_proc(a,723,724)
        if skill:self.group(a,[a],skill,True);self.buff(a,'attack',500,1,skill);self.buff(a,'AttValue',500,1,skill);self.end()
        from native946_book_combat import roll
        a['_normal_attack']=True
        hit=roll(a,t,self.rng)
        self.group(a,[t],10)
        if hit['miss']:self.out.append(nums(2144,t['id']))
        else:
            if hit['effect']:self.out.append(nums(2162,t['id'],hit['effect']))
            self.damage(a,t,hit['damage'],'physical',active=active)
        self.end()
        combo_chance=min(900,value(a,'RoleLineupCombo')+a.get('role_combo_chance',0))
        for _ in range(max(1,a.get('role_combo_count',0))):
            if a['hp']<=0 or hit['miss'] or t['hp']<=0 or self.rng.randrange(1000)>=combo_chance:break
            extra=roll(a,t,self.rng);self.group(a,[t],10)
            if extra['miss']:self.out.append(nums(2144,t['id']))
            else:self.damage(a,t,extra['damage'],'physical',primary=False,active=active)
            self.end()
        a.pop('_normal_attack',None)
        self.reveal(a,t)
        self.shadow_follow(a,t,dict(family='physical',cast_id=10))
        if active and a['kind']=='pet':
            from native946_innate_effects import xinyi_follow_up
            xinyi_follow_up(self,a,t['id'])
        return hit
    def cast(self,a,sid,target_id,*,child=False):
        meta=profile(sid)
        if not meta:raise ValueError('原生技能定义不存在。')
        if self.control_blocked(a,True) or a['hp']<=0:return
        if a['role_cooldowns'].get('skill:'+str(sid),0)>self.round:return
        if sid==1710 and a['role_cooldowns'].get('shadow',0)>self.round:return
        if a.get('role_forbidden',{}).get(str(sid),0)>=self.round:return
        targets,nominal=self.targets(a,meta,target_id)
        if not targets:return
        family=meta['family'];mana=mana_cost(a,meta);free=self.child_proc(a,775)
        feng_power = spell_feng_power(a,sid)
        if a['mp']<mana and not free:return False  # A queued player action can use a normal attack instead.
        if a['role_anger']<meta['anger']:return
        if a['role_wrath']<meta.get('wrath',0):return
        if family != 'cleanse' and not self.action_gushu(a,True):return
        guard_resistance=guard_human_resistance(a,sid) if 81<=sid<=85 else 0
        if meta['source']=='artifact':
            if a.get('role_no_artifact_until',0)>=self.round:return
            if sid == LIULI_LAMP:
                grade = a.get('role_artifacts', {}).get('琉璃灯', 0)
                if not grade:
                    return
            else:
                from native946_role_artifacts import spend
                try:spend(self.db,a.get('owner',a['id']),sid)
                except ValueError:return
        if not free:a['mp']-=mana
        elif a.get('role_child'):self.group(a,[a],free,True);self.end()
        a['role_anger']-=meta['anger'];a['role_wrath']-=meta.get('wrath',0);a['role_last_spell']=sid;self.cast_stack(a,meta)
        if sid == FEIXUE_WUHEN:
            a['role_cooldowns']['skill:'+str(sid)] = self.round + SPELL_LOCK_COOLDOWN
        sticks=a.get('role_stick',0)
        if sid in (1706,1708):a['role_stick']=0
        if sid==16:
            a['role_spring_mana']=a['mp'];a['mp']=0
        # 946 treats power updates inside a cast group as a hit on that unit.
        self.bars(a)
        self.group(a,targets,sid)
        repeats=1
        if sid in (44,54,64,74) and self.proc(a,7007):repeats+=1
        if family=='wind' and self.proc(a,7025):repeats+=1
        if family=='wind' and level(a,7222):repeats=min(3,repeats+1)
        if family=='poison' and self.proc(a,7133):repeats+=1
        if family in ELEMENTS and value(a,'RoleSpellExtraHit'):repeats+=1
        if sid==36:repeats=3
        if sid in (1702,1704):repeats=2
        if sid in (1706,1708):repeats=(2 if sid==1708 else 1)+sticks
        if sid in (6018,6105,6036) and self.rng.randrange(1000)<SECONDARY_PROC_RATE:repeats+=1
        for turn in range(repeats):
            for i,t in enumerate(targets):
                if sid==1708 and turn and i:continue
                primary=i==0
                if family in CONTROLS:
                    if self.control(a,t,family,primary=primary,base=700+feng_power):
                        if sid==6000 and self.rng.randrange(1000)<SECONDARY_PROC_RATE:t.setdefault('role_dots',[]).append(dict(kind='poison',source=a['id'],skill=sid,damage=max(1,self.base_damage(a,t,dict(meta,family='poison'))//3),until=self.round+2))
                        if sid in (6002,6024) and self.rng.randrange(1000)<SECONDARY_PROC_RATE:t['role_no_summon_until']=self.round+2
                        if sid in (6006,6026) and self.rng.randrange(1000)<SECONDARY_PROC_RATE:t['role_no_item_until']=self.round+2
                    continue
                if family=='shock':
                    self.shock(a,t,primary,boost=feng_power//2)
                    if sid in (6008,6028) and self.rng.randrange(1000)<SECONDARY_PROC_RATE:self.buff(t,'role_mp_drain',max(1,t['mpmax']//20),STUNT_ROUNDS,sid)
                    continue
                if family=='heal':
                    base=stunt_heal_base(a)
                    if sid==16:base+=a['role_spring_mana']
                    if sid in HEALING:
                        base=min(20000, int(t['hpmax'] * healing_percent(a, sid) / 100)
                                 + value(a, 'RoleLineupHeal'))
                    elif sid==6124:base=min(20000,t['hpmax']*xuelong_heal_rate(a)//1000+value(a,'RoleLineupHeal'))
                    base=base*(1000+feng_power)//1000
                    self.heal(a,t,base,revive=sid==46,
                              restore_limit=base*level(a,7413)//100 if 6114<=sid<=6124 else base if sid in (16,46) else 0,
                               max_recovery=20000 if sid in HEALING or sid==6124 else None)
                    if sid==46:self.mana(t,t['mpmax']//4)
                    if sid==6124 and self.rng.randrange(1000)<SECONDARY_PROC_RATE:self.mana(t,base//3)
                    if primary and (sid in HEALING and level(a, 7432) >= 10 or self.proc(a, 7432)):
                        t['role_shields']['endure']=1;t['role_endure_until']=self.round+2;self.out.append(nums(2130,t['id'],170,0))
                    continue
                if family=='cleanse':
                    chance=cleanse_chance(a,sid)
                    if t['camp']!=a['camp'] and not primary:chance//=2
                    success=self.rng.randrange(10000)<chance
                    if success:
                        if t['camp'] == a['camp']:
                            self.cleanse(a,t,(*CONTROLS,'poison','gushu'))
                            rank = level(a, 7414)
                            self.heal(a, t, value(a, 'AddXiaorongRate') * (30 if rank >= 10 else rank))
                            t.pop('role_gushu',None)
                        else:
                            for b in list(t['role_buffs']):
                                if not b['locked'] and b['amount']>0:t[b['key']]-=b['amount'];t['role_buffs'].remove(b)
                        if sid==6112:self.buff(t,'role_medicine_percent',250 if t['camp']==a['camp'] else -250,3,sid)
                    else:self.out.append(nums(2144,t['id']))
                    continue
                if family in ('attack_buff','guard_buff','speed_buff','time_buff','endure_buff','hunyuan','element_buff','reflect_buff'):
                    duration=STUNT_ROUNDS
                    if family=='attack_buff':
                        locked=primary and meta['source'] in ('modern','secret') and self.proc(a,7321)
                        power=attack_buff_power(a,feng_power)
                        self.buff(t,'attack',max(1,t['attack']*power//1000),duration,sid,locked=locked);self.buff(t,'role_attack_buff',power,duration,sid,locked=locked);self.buff(t,'AttValue',level(a,7322)*20,duration,7322)
                        if sid in (6014,6030):self.buff(t,'AttValue',12,duration,sid);self.buff(t,'role_ignore_defense',100,duration,sid)
                    elif family=='guard_buff':
                        locked=primary and meta['source'] in ('modern','secret') and self.proc(a,7333)
                        self.buff(t,'defense',guard_buff_defense(a,feng_power),duration,sid,locked=locked);self.buff(t,'role_guard_percent',level(a,7039)*10,duration,7039,locked=locked)
                        self.buff(t,'role_guard_percent',value(a,'RoleLineupGuardBuff'),duration,source=910000+sid,locked=locked)
                        if guard_resistance:
                            for key in ('ReSeal','ReDisorder','ReFaint'):
                                self.buff(t,key,guard_resistance,duration,81)
                        if meta['source'] in ('modern','secret'):self.buff(t,'role_control_resist',level(a,7331)*5,duration,7331)
                        if sid==4556:self.buff(t,'role_luohan',1,3,4556)
                        if level(a,7332) and 6012 in a.get('skills',{}):self.buff(t,'role_mana_regen',100+level(a,7332)*20,3,7332)
                        if sid==6012:self.buff(t,'role_mana_regen',100,3,sid)
                    elif family=='speed_buff':
                        self.buff(t,'initiative',(100+a.get('level',1))*(1000+value(a,'RoleLineupSpeedBuff')+feng_power)//1000,duration,sid)
                        if sid==6010:self.buff(t,'role_hp_regen',100+a.get('level',1)*2,duration,sid)
                    elif family=='time_buff':self.apply_liuguang(a, t, duration)
                    elif family=='element_buff':self.buff(t,'role_element_power',200,duration,sid)
                    elif family=='hunyuan':
                        scale = hunyuan_power(a)
                        self.buff(t,'role_element_power',scale,duration,sid)
                        for key in ('ReWind','ReWater','ReThunder','ReFire','ReMpAbsorb'):self.buff(t,key,scale,duration,sid)
                        if primary and (level(a, 7431) >= 10 or self.proc(a, 7431)):
                            self.apply_liuguang(a, t, duration)
                    elif family=='reflect_buff':self.buff(t,'role_all_reduction',200,duration,sid);self.buff(t,'role_reflect',200,duration,sid)
                    else:t['role_shields']['endure']=1;t['role_endure_until']=self.round+ENDURE_ROUNDS-1;self.out.append(nums(2130,t['id'],170,0))
                    self.bars(t);continue
                if family=='artifact':
                    bonus=1100 if 5001 in a.get('role_tianji',[]) else 1000
                    if sid==128:self.buff(t,'role_mp_drain',max(1,a['attack']*bonus//5000),3,128)
                    else:self.control(a,t,'jin_gu',duration=3,base=700*bonus//1000)
                    continue
                if family=='lamp':
                    a['role_liuli_lamp'] = dict(start=self.round+1, until=self.round+LIULI_LAMP_ROUNDS,
                                                wrath=lamp_wrath(grade))
                    continue
                if family=='shadow':self.shadow(a);continue
                if family=='spell_lock':
                    last=t.get('role_last_spell')
                    if last and self.rng.randrange(1000) < spell_lock_chance(a):
                        t.setdefault('role_forbidden',{})[str(last)] = self.round + SPELL_LOCK_ROUNDS
                    continue
                if family=='resist_break':
                    for key in ('ReDisorder','ReSeal','ReFaint',*('Re'+k.title() for k in ELEMENTS)):self.buff(t,key,-200,3,sid)
                    self.buff(t,'defense',-min(t['defense'],200+a.get('level',1)),3,sid);continue
                if family=='absorb_convert':
                    key='PhyAbsorbDegree'
                    # Reapplying the same debuff replaces it using its unmodified input.
                    prior=sum(b['amount'] for b in t['role_buffs'] if b['key']==key and b['source']==sid)
                    absorb=max(0,value(t,key)-prior)
                    reduction=min(ABSORB_CONVERSION_LIMIT,absorb*ABSORB_CONVERSION_PERCENT//100)
                    self.buff(t,key,-reduction,STUNT_ROUNDS,sid)
                    self.buff(t,'defense',reduction*2,STUNT_ROUNDS,sid)
                    self.buff(t,'role_heal_reduce',ABSORB_HEAL_REDUCTION,STUNT_ROUNDS,sid)
                    continue
                if family=='gushu':
                    duration=gushu_duration(a,sid) if sid in GUSHU_HIT_RATE else 5
                    if joined_witch(a) and sid in GUSHU_HIT_RATE:
                        if self.rng.randrange(1000)>=gushu_hit_rate(a,t,sid):
                            self.out.append(nums(2144,t['id']));continue
                    attack_damage, cast_damage = gushu_damage(a, t, sid, pvp=self.pvp(a, t))
                    t['role_gushu']=dict(source=a['id'],attack_damage=attack_damage,cast_damage=cast_damage,until=self.round+duration-1)
                    self.out.append(nums(2130,t['id'],167,0))
                    rank = level(a, 7422)
                    if rank:
                        self.buff(t, 'role_element_taken', 150 if rank >= 10 else rank * 10, 2, 7422)
                    if sid==6133 and self.rng.randrange(1000)<SECONDARY_PROC_RATE:t['role_no_item_until']=self.round+2
                    continue
                if family=='execute':
                    if t.get('kind')=='enemy' and not t.get('boss') and self.rng.randrange(1000)<100:self.damage(a,t,t['hp'],'execute')
                    else:self.out.append(nums(2144,t['id']))
                    continue
                if t['hp']<=0:continue
                if family == 'fixed':
                    self.apply_damage(a, t, self.base_damage(a, t, meta), family, 0)
                    continue
                if family=='poison':
                    chance=min(990,max(0,750+value(a,'AddPoison')+value(a,'RoleLineupPoisonHit')+value(a,'EquipPoisonHit')-value(t,'RePoison')-value(t,'RePoisonAtt')))
                    chance=chance*max(0,1000-value(t,'RoleLineupControlResist'))//1000
                    if self.rng.randrange(1000)>=chance:self.out.append(nums(2144,t['id']));continue
                damage=self.base_damage(a,t,meta)
                if family=='desperate':damage=desperate_damage(a)
                if family=='dispel_strike':self.cleanse(a,t,('disorder',))
                if meta['group'] and len(targets)<nominal:damage=damage*(1000+(nominal-len(targets))*level(a,7232)*5)//1000
                if i==1 and sid in (36,74) and nominal>1:
                    talent=7021 if sid==36 else 7233
                    if level(a,talent):damage=damage*min(1000,200+level(a,talent)*20)//1000
                if family=='pierce' and primary:damage=damage*(1000+level(a,7312)*10)//1000
                if family=='wind' and level(a,7222) and turn:damage=damage*7//10
                if family=='fire' and self.proc(a,7031):damage=damage*3//2;self.out.append(nums(2162,t['id'],1))
                if sid in (4552,4554):damage=damage*(1000+level(a,7443)*10)//1000
                if family=='poison':damage=min(damage,t['hpmax']*4//10)
                if sid==4552:self.buff(t,'PhyAbsorbDegree',-200,1,4552)
                actual=0
                for strike in range(2 if sid in (4552,4554) else 1):
                    actual+=self.damage(a,t,damage,family,primary=primary and not turn and not strike)
                if sid==36 and a.get('race_fabao_slash') and t['hp']>0:
                    self.damage(a,t,damage*a['race_fabao_slash']//1000,family,primary=False)
                if (sid==1706 or sid==1708 and turn) and actual and self.rng.randrange(1000)<(STICK_SEAL_RATE//2 if actual<STICK_SEAL_DAMAGE else STICK_SEAL_RATE):t['controls']['soul_seal']=max(t['controls'].get('soul_seal',0),self.round+2);self.out.append(nums(2130,t['id'],198,0))
                if sid in (6016,6018,6020,6022,6032,6034) and t['hp']>0 and self.rng.randrange(1000)<SECONDARY_PROC_RATE:
                    secondary='water' if sid in (6016,6032) else 'thunder'
                    self.damage(a,t,self.base_damage(a,t,dict(meta,family=secondary))*7//10,secondary,primary=False)
                if sid==6004 and self.rng.randrange(1000)<SECONDARY_PROC_RATE:
                    for b in list(t['role_buffs']):
                        if b['source'] in (81,82,83,84,85,91,92,93,94,95,101,102,103,104,105,6010,6012,6014) and not b['locked']:t[b['key']]-=b['amount'];t['role_buffs'].remove(b)
                if sid==4554 and self.rng.randrange(1000)<300:t['no_revive_until']=self.round+2
                if family=='poison' and t['hp']>0 and actual:
                    t.setdefault('role_dots',[]).append(dict(kind=family,source=a['id'],skill=sid,damage=max(1,damage//3),until=self.round+2))
                    t['role_poison_icon']=True;self.out.append(nums(2130,t['id'],106,0))
                    t['role_dots']=t['role_dots'][-8:]
                    if family=='poison' and level(a,7134):self.buff(t,'role_physical_taken',level(a,7134)*10,3,7134)
                if family=='fire':
                    if self.proc(a,7241):self.buff(t,'role_heal_reduce',350,3,7241)
                    if level(a,7243):self.buff(t,'role_spell_taken',level(a,7243)*10,2,7243)
                self.reveal(a,t)
        if family in ELEMENTS:self.apply_jade_spell_debuff(a,targets)
        if meta['source']=='secret' and meta['anger']<=3 and self.proc(a,7442):a['role_anger']=min(RULE['anger_max'],a['role_anger']+1)
        if 1701<=sid<=1705:a['role_stick']=min(a.get('role_stick_max',2),a.get('role_stick',0)+1)
        if family=='stick':self.out.extend(stick_packets(a))
        self.bars(a);self.end();self.shadow_follow(a,targets[0],meta)
    def shadow(self,a):
        if any(f.get('shadow_owner')==a['id'] and not f.get('removed') and f['hp']>0 for f in self.fighters.values()):return
        if a['role_cooldowns'].get('shadow',0)>self.round:return
        sid=930000000+a['id'];ratio=RULE['shadow_hp_percent']+level(a,7501)*10
        shadow=dict(a,id=sid,name=a.get('name','')+'·魔影',kind='shadow',shadow_owner=a['id'],hp=max(1,a['hpmax']*ratio//1000),hpmax=max(1,a['hpmax']*ratio//1000),role_child=None,role_talents={},role_buffs=[],role_stacks={},role_shields={},role_cooldowns={},controls={},removed=False)
        from copy import deepcopy
        shadow['role_control_info']={};shadow['role_dots']=[];shadow.pop('role_gushu',None)
        for key in ('role_invisibility_sources','role_forbidden'):
            if key in shadow:shadow[key]=deepcopy(shadow[key])
        self.fighters[sid]=shadow;a['role_cooldowns']['shadow']=self.round+3
        self.out.append(packet(2129,['number','number','string','string','number','number','number'],[sid,a['id'],shadow['name'],'魔影',a.get('type',a.get('role_id',0)),0,0]));self.bars(shadow,initialize=True)
    def shadow_follow(self,a,t,meta):
        if meta['family'] in FRIENDLY or a.get('kind')!='hero' or t['camp']==a['camp'] or t['hp']<=0 or a['hp']<=0:return
        shadow=next((f for f in self.fighters.values() if f.get('shadow_owner')==a['id'] and not f.get('removed') and f['hp']>0),None)
        if not shadow:return
        ratio=RULE['shadow_damage_percent']+level(a,7501)*10;damage=max(1,(a['attack']-t.get('defense',0))*ratio//1000)
        if meta['cast_id']!=10 and meta['source'] in ('school','modern','shadow'):
            shadow['role_damage_percent']=ratio-1000;self.cast(shadow,meta['cast_id'],t['id']);return
        self.group(shadow,[t],10);self.damage(shadow,t,damage);self.end()
    def end_round(self):
        from native946_lineups import end_round as formation_end
        formation_end(self)
        for a in list(self.fighters.values()):
            from native946_innate_effects import end_round as innate_end_round
            innate_end_round(self,a)
            if a.get('kind')=='hero' and a['hp']>0 and 1710 in a.get('skills',{}) and self.proc(a,7505) and a['role_cooldowns'].get('shadow',0)<=self.round:
                self.cast(a,1710,a['id'])
        for a in self.fighters.values():
            if a.get('kind')=='pet':
                from native946_guardian_innate import CANSHENG_YIJIU
                key=f'skill:{CANSHENG_YIJIU}'
                cooldowns=a['role_cooldowns']
                if key in cooldowns:
                    remaining=max(0,cooldowns[key]-self.round-1)
                    self.out.append(packet(2168,['number',('array','number'),('array','number')],[a['id'],[CANSHENG_YIJIU],[remaining]]))
                    if not remaining:del cooldowns[key]
                continue
            if a.get('kind')!='hero':continue
            cds={1710:a['role_cooldowns'].get('shadow',0)}
            cds.update({int(k.split(':')[1]):v for k,v in a['role_cooldowns'].items() if k.startswith('skill:')})
            self.out.append(packet(2168,['number',('array','number'),('array','number')],[a['id'],list(cds),[max(0,t-self.round) for t in cds.values()]]))

    def guardian_cast(self,game,a,target_id,skill):
        from native946_innate_effects import cast as innate_cast, xinyi_follow_up
        if innate_cast(self,game,a,target_id,skill):
            xinyi_follow_up(self,a,target_id)
            return
        from native946_guardian_magic import prepared,RULE as GR
        v=prepared(a,skill);targets=sorted(self.enemies(a),key=lambda t:(t['id']!=target_id,t['id']))[:v['targets']]
        if not targets:return
        a['mp']-=v['mana']
        self.bars(a)
        if skill==4906:
            self.group(a,targets,skill)
            for i,t in enumerate(targets):
                self.damage(a,t,max(1,a.get('attack',0)-t.get('defense',0)),'physical',primary=not i)
                for element in ('thunder','fire','wind'):
                    if t['hp']>0:self.damage(a,t,max(1,a.get('strength',a.get('str',0))*2+a.get('level',1)*10),element,primary=False)
            self.apply_jade_spell_debuff(a,targets)
            self.bars(a);self.end()
            xinyi_follow_up(self,a,target_id)
            return
        repeat=skill==339 and self.rng.randrange(1000)<v['proc_per_mille']
        from native946_innate_effects import echo_spell,ECHO_DAMAGE_PER_MILLE,roll_fire_critical,ordinary_fire_critical_damage
        normal_passes=1+int(repeat)
        for n in range(normal_passes+1):
            live=[t for t in targets if t['hp']>0 and not t.get('removed')]
            if not live:break
            echo=n==normal_passes
            if echo and not echo_spell(self,a,skill):break
            self.group(a,live,skill)
            for i,t in enumerate(live):
                amount=v['damage']*(GR['multi_percent'] if len(targets)>1 else 100)//100
                amount=max(1,amount-value(t,RESIST[v['element']]))
                if skill==338 and not echo and roll_fire_critical(t,v['proc_per_mille'],self.rng):
                    amount=ordinary_fire_critical_damage(t,amount)
                    self.out.append(nums(2162,t['id'],1))
                amount=amount*(1000+value(a,'XQAddHurtHpP')+value(a,'RoleSpellMastery'))//1000
                amount=amount*max(0,1000-value(t,'XQSubHurtHpP'))//1000
                if echo:amount=amount*ECHO_DAMAGE_PER_MILLE//1000
                self.damage(a,t,amount,v['element'],primary=not i and not n)
            self.bars(a);self.end()
        self.apply_jade_spell_debuff(a,targets)
        xinyi_follow_up(self,a,target_id)
    def item(self,game,a,order,db):
        from native946_inventory_features import consume
        from native946_progression import medicine_effects
        from native946_role_systems import state,save
        from native946_codec import text_notice
        from time import time
        try:item=validate_item(db,game,a,order['item_id'],order['target'],self.fighters,self.round)
        except ValueError as error:self.out.append(text_notice(str(error)));return
        kind=item['type'];t=self.fighters[order['target']]
        effects=medicine_effects(kind)
        if effects:
            restored=[(attr,min(amount*max(0,1000+t.get('role_medicine_percent',0))//1000,max(0,t[maximum]-t[attr]))) for attr,maximum,amount in effects]
            if any(amount for attr,amount in restored):
                consume(db,game.progression.cid,item['id'])
                from native946_progression import DATA as ITEMS
                self.out.append(packet(2165,['number','string','number','number'],[a['id'],'使用'+ITEMS['items'][str(kind)]['name'],1,0]))
                for attr,amount in restored:
                    if amount:
                        t[attr]+=amount
                        self.out.append(nums(2164,t['id'],amount,1 if attr=='hp' else 2,0))
                self.bars(t)
            return
        s=state(db,a['id'],'tianji',{});active=s.get('active',[])
        if 6824<=kind<=6833:
            consume(db,a['id'],item['id']);weather=(kind-6824)//2;grade=3 if kind%2 else 2
            if self.rng.randrange(1000)<min(990,700+(100 if 419 in active else 0)+(100 if kind%2 else 0)):
                self.state['role_weather']=dict(kind=weather,grade=grade,until=self.round+2,name=DATA['tianji_items'][str(kind)]['name'])
                self.out.extend((nums(2172,weather*3+grade),nums(2173,weather*3+grade)))
            else:self.out.append(text_notice('变天未成功。'))
            return
        harmful=kind in (6840,6842);targets=self.enemies(a) if harmful else self.allies(a)
        if kind==6836:targets+=self.allies(a,True)
        count={6834:3,6835:3,6836:2,6841:4,6842:4}.get(kind,1)
        if kind in (6834,6835,6836) and 403 in active:count+=1
        if kind in (6837,6838,6839,6840) and 2001 in active:count+=2
        if kind in (6841,6842) and 304 in active:count+=1
        targets=sorted(targets,key=lambda f:(f['id']!=t['id'],f['id']))[:count]
        if not targets:return
        cost=200 if kind==6836 else 100;stamp=int(time());s.update(vim=current_vim(s,stamp)-cost,vim_at=stamp)
        save(db,a['id'],'tianji',s);consume(db,a['id'],item['id'])
        self.out.append(packet(2165,['number','string','number','number'],[a['id'],DATA['tianji_items'][str(kind)]['name'],0,0]))
        for target in targets:
            medicine=max(0,1000+target.get('role_medicine_percent',0))
            if kind in (6834,6835,6836):
                hp,mp={6834:(2500,0),6835:(0,2200),6836:(1500,500)}[kind]
                factor=1000+(1000 if 403 in active else 0)+(400 if 2001 in active else 0)
                hp=hp*factor//1000*medicine//1000;mp=mp*factor//1000*medicine//1000
                if target['hp']<=0:
                    if kind==6836:self.revive(a,target,hp,mp)
                else:
                    if hp:self.heal(a,target,hp)
                    self.mana(target,mp)
            elif kind in (6837,6838,6839):self.buff(target,('ReDisorder','ReSeal','ReFaint')[kind-6837],250,2+int(405 in active),kind)
            elif kind==6840:
                for key,amount in (('ReThunder',-800),('ReFire',-600),('ReWind',-600),('ReWater',-500)):self.buff(target,key,amount,2+int(202 in active),kind)
            elif kind==6841:self.buff(target,'DodValue',1000+(400 if 2001 in active else 0),2+int(304 in active),kind)
            elif kind==6842:self.buff(target,'AttValue',-200 if 2001 in active else -100,2+int(304 in active),kind)
            elif kind in (6843,6844,6845,6846):
                node={6843:504,6844:505,6845:508,6846:507}[kind]
                chance=400+((300 if kind==6846 else 400) if node in active else 0)+((70 if kind==6846 else 200) if 2001 in active else 0)
                if self.rng.randrange(1000)<min(1000,chance):self.cleanse(a,target,({6843:'disorder',6844:'seal',6845:'jin_gu',6846:'entangle'}[kind],))
            elif kind==6847:self.buff(target,'role_pierce_flat',3150 if 2001 in active else 2100,3,kind)
            elif kind==6848:self.buff(target,'role_wuli_flat',3000 if 2001 in active else 2000,3,kind)

    def pet_passives(self,pet,target):
        from native946_neidan_legacy import effect
        from native946_innate_effects import has, jing_fire_critical, water_skill_bonus
        for row in pet.get('neidan',[]):
            if pet['hp']<=0:break
            skill = row['kind']
            element = PILL_ELEMENTS.get(skill)
            if not element and skill not in (231,235):continue
            v=effect(row,pet)
            chance=v.get('chance',0)
            if element:chance+=pet.get('new_neidan_power',{}).get(255,0)/100
            if not v.get('enabled') or self.rng.random()*100>=chance:continue
            subject=next((f for f in self.enemies(pet) if f['id']!=target['id']),None) if skill==235 else target
            if subject is None or subject['hp']<=0:continue
            amount=int(pet['attack']*v['ratio']/100) if skill==235 else v.get('damage',int(subject.get('mpmax',0)*v.get('ratio',0)/100))
            if element:
                family=element
                if element=='water':
                    amount+=water_skill_bonus(pet,subject)
                if element=='fire' and has(pet,4721):
                    amount=jing_fire_critical(pet,subject,amount,self.rng)
                amount=max(1,amount-value(subject,RESIST[element]))
            else:
                family='physical' if skill==235 else 'gushu'
                amount=max(0,amount-value(subject,'ReHaoran' if skill==231 else 'ReGeshan'))
                if skill==235:amount=amount*(10000-subject.get('new_neidan_power',{}).get(243,0))//10000
            if amount:self.group(pet,[subject],skill);self.damage(pet,subject,amount,family);self.end()
    def pet_counter(self,pet,target,used):
        if pet['hp']<=0 or self.control_blocked(pet):return
        from native946_book_combat import counter_ready
        if target['hp']>0 and counter_ready(pet,used.get(pet['id'],0),self.rng,attacker=target):used[pet['id']]=used.get(pet['id'],0)+1;self.attack(pet,target,active=False)
        from native946_neidan_legacy import effect
        for row in pet.get('neidan',[]):
            if row['kind']!=233:continue
            v=effect(row,pet)
            if not v['enabled'] or self.rng.random()*100>=v['chance']:continue
            for _ in range(v['counter_count']):
                if pet['hp']<=0 or target['hp']<=0:break
                self.group(pet,[target],10);self.damage(pet,target,max(1,pet['attack']-target.get('defense',0)),active=False);self.end()

def vim_limit(s):return RULE['tianji_vim_max']+(100 if 2001 in s.get('active',[]) else 0)

def current_vim(s,stamp=None):
    from time import time
    stamp=int(time()) if stamp is None else stamp
    limit=vim_limit(s)
    return min(limit,s.get('vim',limit)+max(0,stamp-s.get('vim_at',stamp))//RULE['tianji_vim_restore_seconds'])

def weather_reply(state):
    from native946_codec import packet
    w=state.get('role_weather',{})
    desc=(w.get('name','')+'：本机天气强化对应属性法术，剩余'+str(max(0,w.get('until',0)-state.get('round',1)+1))+'回合。') if w.get('until',0)>=state.get('round',1) else '当前没有天机变天效果。'
    return packet(2133,['string'],[desc])

def validate_item(db,game,actor,item_id,target,fighters,round):
    from native946_inventory_features import owned
    from native946_progression import medicine_effects
    item=owned(db,game.progression.cid,item_id);kind=item['type'];t=fighters.get(target)
    if actor.get('role_no_item_until',0)>=round:raise ValueError('当前处于禁止使用物品状态。')
    if t is None or t.get('removed') or (t['hp']<=0 and kind!=6836):raise ValueError('请选择仍在场的目标。')
    if kind==6836 and t['hp']<=0 and t.get('no_revive_until',0)>=round:raise ValueError('目标暂时无法复活。')
    harmful=kind in (6840,6842)
    if (t['camp']!=actor['camp'])!=harmful:raise ValueError('请选择敌方目标。' if harmful else '请选择己方目标。')
    effects=medicine_effects(kind)
    if effects:
        if all(t[attr]>=t[maximum] for attr,maximum,_ in effects):raise ValueError('目标状态已满，无需用药。')
    elif str(kind) in DATA['tianji_items']:
        if actor.get('kind')!='hero':raise ValueError('守护不能使用天机成品。')
        if kind>=6834 and actor.get('level',1)<100:raise ValueError('天机成品需人物100级。')
        from native946_role_systems import state
        cost=0 if kind<6834 else 200 if kind==6836 else 100
        if current_vim(state(db,actor['id'],'tianji',{}))<cost:raise ValueError('该成品需要'+str(cost)+'内力，内力每分钟恢复1点。')
    else:raise ValueError('请选择自己背包中可用于战斗的药品或天机成品。')
    return item

class InsufficientHeroMana(ValueError):
    def __init__(self):
        super().__init__('法力不足。')


def validate_order(actor,sid,target,fighters):
    meta=owned_profile(actor,sid)
    if actor.get('role_anger',RULE['anger_start'])<meta['anger']:raise ValueError('战意不足。')
    if actor.get('role_wrath',RULE['wrath_start'])<meta.get('wrath',0):raise ValueError('怨气不足。')
    if target not in fighters:raise ValueError('目标不在当前战斗中。')
    if sid == LIULI_LAMP:
        if target != actor['id']:raise ValueError('琉璃灯只能对自己施放。')
        if actor.get('role_no_artifact_until',0)>=actor.get('role_round',1):raise ValueError('当前无法施放法宝。')
    t=fighters[target];friendly=meta['family'] in FRIENDLY
    if actor.get('role_cooldowns',{}).get('skill:'+str(sid),0)>actor.get('role_round',1):raise ValueError('该技能尚在冷却中。')
    if sid==1710 and actor.get('role_cooldowns',{}).get('shadow',0)>actor.get('role_round',1):raise ValueError('圣心魔影尚在冷却中。')
    if actor.get('role_forbidden',{}).get(str(sid),0)>=actor.get('role_round',1):raise ValueError('该技能暂时被飞雪无痕封锁。')
    if actor.get('controls',{}).get('jin_gu',0)>=actor.get('role_round',1):raise ValueError('当前处于无法施法状态。')
    if t.get('invisible') and t['camp']!=actor['camp'] and not actor.get('role_reveal'):raise ValueError('目标当前处于隐身状态。')
    if meta['family']=='spell_lock' and not (t.get('owner') and t['camp']!=actor['camp']):raise ValueError('飞雪无痕只能用于与玩家的对战。')
    if friendly and t['camp']!=actor['camp'] and meta['family']!='cleanse':raise ValueError('请选择己方目标。')
    if not friendly and t['camp']==actor['camp'] and meta['family']!='dispel_strike':raise ValueError('请选择敌方目标。')
    if t.get('removed') or (t['hp']<=0 and sid!=46):raise ValueError('该技能不能作用于当前目标。')
    mana=mana_cost(actor,meta)
    if actor['mp']<mana and not (actor.get('role_child') or {}).get('skills',{}).get('775'):
        raise InsufficientHeroMana()
    return dict(kind='attack',target=target,skill=sid,skill_id=sid,mana=mana)

def protect_order(actor,target_id,fighters):
    target=fighters.get(target_id)
    if (target is None or target_id==actor['id'] or target['camp']!=actor['camp']
            or target['hp']<=0 or target.get('removed')):
        raise ValueError('请选择其他仍在场的己方单位进行保护。')
    return dict(kind='protect',target=target_id)


def capture(battle,runtime,actor,target,local):
    from native946_wilderness import DATA as WILD,POLICY
    native=WILD['species'][str(target['guardian_type'])];game=battle.games[actor['owner']]
    keep=battle.keeps_captured_guardian()
    if keep and len(game.summons.snapshot())>=game.summons.carry_limit():return
    tutorial=target.get('capture_training',False)
    chance=POLICY['capture_base_percent']+(target['hpmax']-target['hp'])*POLICY['capture_missing_hp_percent']//max(1,target['hpmax'])
    success=tutorial or runtime.rng.randrange(100)<chance
    if success:
        owned=None
        if keep:
            source='campaign:newbie:capture' if tutorial else 'capture:'+battle.id+':'+str(target['id'])
            owned=game.summons.acquire(native['type'],source,level=target['level'])
        target['captured']=True;target['removed']=True
    runtime.out.append(nums(2136,actor['id'],target['id'],int(success)))
    if success and tutorial and owned is not None:
        hero=battle.state['fighters'][actor['owner']]
        game.summons.status(owned,1)
        runtime.out.extend(battle.pet_order(hero,dict(kind='summon',summon_id=owned),local))
        for joined in runtime.fighters.values():runtime.prepare(joined)

def play_round(runtime,orders,db,*,battle=None):
    from native946_race_fabao_battle import cast_for_turn

    runtime.db=db;runtime.defending.clear();counters={}
    for fid in runtime.fighters:
        submitted=battle.actor_orders(fid) if battle else [orders.get(fid,{})]
        if any(order.get('kind')=='defend' for order in submitted):
            runtime.defending.add(fid)
    runtime.begin_round()
    roster=sorted(list(runtime.fighters.values()),key=lambda f:(-f['initiative'],f['id']))
    local=runtime.owner_packets
    for actor in roster:
        submitted=battle.actor_orders(actor['id']) if battle else [orders.get(actor['id'],{})]
        for target_id in {o['target'] for o in submitted if o.get('kind')=='protect'}:
            runtime.protectors.setdefault(target_id,[]).append(actor)
    schedule=[(a,index) for a in roster for index in range(battle.action_count(a['id']) if battle else 1)]
    for a,action_index in schedule:
        if a['hp']<=0 or a.get('removed') or a.get('kind')=='shadow':continue
        if not any(f['camp']!=a['camp'] for f in runtime.living()):continue
        if action_index==0 and a['kind']=='hero':cast_for_turn(runtime,a)
        if runtime.control_blocked(a):continue
        if a['controls'].get('disorder',0)>=runtime.round:
            if a['id'] in runtime.defending:continue
            forced=a.get('role_control_info',{}).get('disorder',{}).get('force_friendly')
            candidates=runtime.allies(a) if forced else runtime.living()
            if candidates:runtime.attack(a,runtime.rng.choice(candidates))
            continue
        if a.get('kind')=='enemy':
            if a.get('capture_training'):continue
            targets=runtime.enemies(a)
            if not targets:continue
            if 'enemy_spell_percent' in a:
                t=runtime.rng.choice(targets)
                interval=a.get('enemy_spell_interval',1)
                if (a.get('skills') and runtime.round%interval==0 and not runtime.control_blocked(a,True)
                        and runtime.rng.randrange(100)<a['enemy_spell_percent']):
                    sid=runtime.rng.choice(tuple(a['skills']))
                    if runtime.cast(a,sid,t['id']) is not False:continue
            else:
                t=targets[(runtime.round+a.get('slot',1)-2)%len(targets)]
            policy=a.get('control_policy',{})
            if a.get('control_spell') and runtime.round%policy.get('interval',3)==1:
                runtime.group(a,[t],{'faint':13,'disorder':23,'seal':33}[a['control_spell']]);runtime.control(a,t,a['control_spell'],base=policy.get('base_per_mille',700),duration=policy.get('duration',2));runtime.end()
            else:
                result=runtime.attack(a,t)
                if t.get('kind')=='pet' and result and not result['miss']:runtime.pet_counter(t,a,counters)
            continue
        submitted=battle.actor_orders(a['id']) if battle else [orders.get(a['id'],dict(kind='defend'))]
        order=submitted[action_index] if action_index<len(submitted) else dict(kind='defend')
        if order['kind'] in ('defend','protect'):continue
        game=runtime.games[a['owner']]
        if order['kind'] in ('summon','dismiss'):
            if a.get('role_no_summon_until',0)>=runtime.round:continue
            if battle:
                runtime.out.extend(battle.pet_order(a,order,local))
                for joined in runtime.fighters.values():runtime.prepare(joined)
            continue
        if order['kind']=='medicine':runtime.item(game,a,order,db);continue
        target=runtime.fighters.get(order.get('target',0))
        meta=profile(order.get('skill_id',0))
        from native946_innate_effects import has as has_innate
        if target is None or target.get('removed') or (target['hp']<=0 and not (meta and meta['family']=='heal')) or (target.get('invisible') and target['camp']!=a['camp'] and not a.get('role_reveal') and not has_innate(a,4733)):
            if order['kind']=='capture':continue
            candidates=runtime.allies(a) if meta and meta['family'] in FRIENDLY else runtime.enemies(a)
            if not candidates:continue
            target=candidates[0]
        if order['kind']=='capture':capture(battle,runtime,a,target,local);continue
        if order.get('guardian_skill'):
            from native946_guardian_magic import InsufficientGuardianMana
            try:runtime.guardian_cast(game,a,target['id'],order['guardian_skill'])
            except InsufficientGuardianMana:pass
            except ValueError:continue
            else:continue
        elif order.get('skill_id'):
            if runtime.cast(a,order['skill_id'],target['id']) is not False:continue
        if order.get('guardian_skill') or order.get('skill_id'):
            if target['camp']==a['camp']:
                enemies=runtime.enemies(a)
                if not enemies:continue
                target=enemies[0]
            from native946_codec import text_notice
            label='守护' if a['kind']=='pet' else '人物'
            local[a['owner']].append(text_notice(label+'法力不足，本次改用普通攻击。'))
        result=runtime.attack(a,target)
        if a.get('kind')=='pet' and result and not result['miss']:runtime.pet_passives(a,target)
    runtime.end_round()
    runtime.guardian_deaths()
    for a in runtime.fighters.values():a['role_round']=runtime.round+1
    return local

INPUT_SECONDS = 45  # Classic 3.0.921 full-server battle timer change, retained in 946.


def drawing_deadline(animation_packets):
    # 2126/1 starts an action. Leave enough time for a full batch before
    # recovering a connection that has not acknowledged the round.
    actions = animation_packets.count(nums(2126, 1))
    return monotonic() + max(30, 15 + 4 * actions)


def resolve_shared(battle,*,input_previews=None):
    state=battle.state
    local={cid:[input_previews[cid]] if input_previews and cid in input_previews else []
           for cid in battle.games}
    with battle.atomic() as db:
        for cid,g in battle.games.items():
            actor=state['fighters'][cid]
            if 'role_talents' not in actor:actor.update(snapshot(db,cid))
            for f in battle.allies():
                if f.get('kind') not in ('hero','pet'):continue
                if f['id'] not in state['input_ended'][cid]:local[cid].append(nums(2152,f['id']))
        runtime=Runtime(state['fighters'],battle.games,state['round'],battle.rng,state)
        messages=play_round(runtime,state['orders'],db,battle=battle)
        for cid,values in messages.items():local[cid].extend(values)
        after_round = getattr(battle, 'after_round', None)
        if after_round:
            after_round(runtime)
        # The recruitment tutorial ends when its designated guardian is caught.
        training_complete = any(f.get('capture_training') and f.get('captured')
                                for f in state['fighters'].values())
        state['defeat'] = not training_complete and not battle.allies()
        state['victory'] = training_complete or (not battle.enemies() and (not after_round or not state['defeat']))
        battle.persist()
        if (state['victory'] or state['defeat']) and not state['settled']:
            battle.settle(db,state['victory'])
            from native946_lineups import settle as formation_settle
            formation_settle(battle,db,state['victory'])
            battle.recover_after_battle(db)
        state.update(phase='drawing', acks=set(), deadline=drawing_deadline(runtime.out))
    for cid,g in battle.games.items():battle.service.enqueue(g,*local[cid])
    battle.broadcast(*runtime.out,nums(2167))
    for g in battle.games.values():g.last_event=dict(action='wild_battle_round',battle_id=battle.id,round=state['round'],victory=state['victory'])

def solo_fighters(game):
    from native946_gameplay import ENEMY_ID
    b=game.battle;cid=game.character['id']
    if 'role_fighters' not in b:
        s=game.progression.snapshot();hero=dict(s,id=cid,owner=cid,kind='hero',camp=4,slot=1,type=game.character['role_id'],name=game.character['name'],hp=b['hp'],mp=b['mp'])
        enemy=dict(id=ENEMY_ID,kind='enemy',camp=3,slot=1,name='训练木偶',type=211,level=1,hp=b['enemy_hp'],hpmax=b.get('enemy_hpmax',b['enemy_hp']),mp=0,mpmax=0,attack=5,defense=b.get('enemy_defense',0),initiative=0,exit_on_defeat=True)
        b['role_fighters']={cid:hero,ENEMY_ID:enemy}
    fighters=b['role_fighters'];pet=b.get('pet')
    for f in fighters.values():
        if f.get('kind')=='pet' and (not pet or pet['id']!=f['id']):f['removed']=True
    if pet:
        pet.update(owner=cid,kind='pet',camp=4,slot=6);fighters[pet['id']]=pet
    return fighters

def solo_required(game):
    from native946_combat_control import can_select_order
    return [f['id'] for f in solo_fighters(game).values() if f.get('kind') in ('hero','pet') and can_select_order(f,game.battle['round'])]
def resolve_solo(game):
    from native946_gameplay import ENEMY_ID
    from native946_book_combat import local_rng
    b=game.battle;cid=game.character['id'];packets=list(game.end_input(cid));pet=b.get('pet')
    if pet:packets.extend(game.end_input(pet['id']))
    changed=b['orders'].get(cid,{}).get('kind') in ('summon','dismiss')
    if changed:packets.extend(game.apply_guardian_order(b['orders'][cid]))
    fighters=solo_fighters(game);orders={}
    for aid,o in b['orders'].items():
        if o.get('kind') in ('summon','dismiss'):continue
        n=dict(o);n['kind']=n.get('kind','defend' if n.get('defend') else 'attack');n.setdefault('target',ENEMY_ID)
        if n.get('role_skill'):n['skill_id']=n['role_skill']
        elif n.get('skill') not in (None,10) and not n.get('guardian_skill'):n['skill_id']=n['skill']
        orders[aid]=n
    with game.progression.transaction() as db:
        runtime=Runtime(fighters,{cid:game},b['round'],local_rng(b,cid,'role-turn'),b)
        packets.extend(play_round(runtime,orders,db)[cid])
        hero=fighters[cid];enemy=fighters[ENEMY_ID];b.update(hp=hero['hp'],mp=hero['mp'],hpmax=hero['hpmax'],mpmax=hero['mpmax'],enemy_hp=enemy['hp'])
        b['victory']=enemy['hp']<=0 or enemy.get('removed',False)
        if b['victory']:game.characters.settle_battle(game.character['owner'],cid,b['id'],True);game.auto_level();game.progression.record_victory(b['id']);game.settle_battle_guardians(True)
        b.update(phase='drawing', input_deadline=drawing_deadline(runtime.out))
        game.persist_battle_vitals()
    game.last_event=dict(action='battle_turn',round=b['round'],enemy_hp=b['enemy_hp'],hero_hp=b['hp'])
    packets.extend(runtime.out)
    return game.defer_packets(lambda:(*packets,*game.progression.item_packets(),nums(2167),nums(2139,2)),fallback=(*packets,nums(2167),nums(2139,2)))
