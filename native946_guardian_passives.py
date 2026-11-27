"""Documented death effects with deterministic local target selection."""
from pathlib import Path
import json

REFERENCE_WIRE_VERSION=2
DATA=json.loads(Path(__file__).with_name('native946_guardian_passives_data.json').read_text('utf8'))
RULES={int(key):rule for key,rule in DATA['rules'].items()}


def ready(victim):
    from native946_innate_effects import DEATH_SKILLS

    return [entry['stunt_id'] for entry in victim.get('innate_skills',[])
            if (entry['stunt_id'] in RULES or entry['stunt_id'] in DEATH_SKILLS)
            and entry['stunt_id'] not in victim.get('innate_death_used',[])]


def death_effects(victim,attacker,fighters,rng,*,defend=False,apply=True,runtime=None):
    if victim['hp']>0 or victim.get('removed'):return []
    events=[]
    for skill in ready(victim):
        if skill not in RULES:
            from native946_innate_effects import death_effect
            from native946_role_battle import Runtime
            if runtime is not None and death_effect(runtime,attacker,victim,skill):
                events.append(dict(skill=skill,source=victim,target=victim,damage=0,miss=False,effect=0))
            continue
        rule=RULES[skill]
        # Bookkeeping is owned by the battle actor, never the persistent guardian.
        victim.setdefault('innate_death_used',[]).append(skill)
        if skill==4922:
            if attacker is None or attacker['hp']<=0 or attacker.get('removed'):
                continue
            from native946_book_combat import roll
            outcome=roll(victim,attacker,rng,defend=defend)
            amount=outcome['damage']*rule['damage_percent']//100
            hit=min(attacker['hp'],amount) if apply else amount
            if apply:attacker['hp']-=hit
            events.append(dict(skill=skill,source=victim,target=attacker,damage=hit,
                               miss=outcome['miss'],effect=outcome['effect']))
        elif skill==4923:
            allies=sorted((f for f in fighters if f['id']!=victim['id'] and f['hp']>0 and
                    f.get('camp')==victim.get('camp') and not f.get('removed')),key=lambda f:(f['hp'],f['id']))
            if not allies:
                continue
            target=allies[0]
            maximum=target.get('hp_limit',target['hpmax'])
            actual=max(0,min(maximum-target['hp'],target['hpmax']*rule['heal_max_hp_percent']//100))
            if target.get('no_heal'):actual=0
            actual=actual*max(0,1000-target.get('role_heal_reduce',0))//1000
            if apply:target['hp']+=actual
            events.append(dict(skill=skill,source=victim,target=target,damage=-actual,miss=False,effect=0))
    return events
