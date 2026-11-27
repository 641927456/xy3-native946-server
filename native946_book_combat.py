"""Reference physical-book outcomes, displayed through native 946 effects."""
from math import floor
from random import Random
from native946_skillbooks import DATA
REFERENCE_WIRE_VERSION=7


def involved(*fighters):
    return any(any(305<=r['skill']<=309 for r in f.get('learned_books',[])) or
               any(value(f,k) for k in ('AttValue','DodValue','FatalRate','CruelRate','cruel_basis_points','FanjiRate','XQAddHurtHpP','XQSubHurtHpP','RolePhysicalPercent','RolePhysicalExtraHit','RoleArmorBreak')) for f in fighters)

def value(actor,key):return actor.get('cultivation',{}).get(key,0)+actor.get(key,0)

def physical_value(actor,name):
    field,divisor={'hit':('AttValue',1),'dodge':('DodValue',1),'fatal':('FatalRate',10),'cruel':('CruelRate',10),'counter':('FanjiRate',10)}[name]
    return actor.get('book_'+name,0)+value(actor,field)/divisor


def local_rng(battle,actor,phase):
    # Retrying a rolled-back operation must not reroll a stronger outcome.
    return Random(str(battle['id'])+':'+str(battle['round'])+':'+str(actor)+':'+phase)


def roll(actor,target,rng,*,defend=False):
    miss=False;effect=0;damage=actor['attack']
    if involved(actor,target):
        chance=max(0,min(100,90+physical_value(actor,'hit')-physical_value(target,'dodge')))
        miss=rng.randint(1,100)>chance
        if miss:return dict(damage=0,miss=True,effect=0)
        fatal=max(0,min(100,physical_value(actor,'fatal')-target.get('anti_fatal',0)-value(target,'EquipReFatal')/10))
        cruel=physical_value(actor,'cruel')-target.get('anti_cruel',0)-value(target,'EquipReCruel')/10
        precise_cruel=value(actor,'cruel_basis_points')
        cruel_scale=100 if precise_cruel else 1
        if precise_cruel:
            cruel=round(cruel*cruel_scale)+precise_cruel
        cruel=max(0,min(100*cruel_scale,cruel))
        if fatal and rng.randint(1,100)<=fatal:
            damage=damage*(1500+actor.get('role_fatal_multiplier',0))/1000+target['hp']*.1
            effect=DATA['native_attack_effects']['mATTACK_EFFECT_FATAL']
        elif cruel and rng.randint(1,100*cruel_scale)<=cruel:
            damage*= (1500+actor.get('role_cruel_multiplier',0))/1000
            damage*= (10000-target.get('new_neidan_power',{}).get(280,0))/10000
            effect=DATA['native_attack_effects']['mATTACK_EFFECT_CRUEL']
    defense=target.get('defense',0)
    defense=defense*max(0,1000-actor.get('role_physical_penetration',0))//1000
    ignore=value(actor,'role_ignore_defense')
    if target['hp']*10<target.get('hpmax',target['hp'])*4 and (target.get('role_child') or {}).get('skills',{}).get('772'):ignore//=2
    defense=max(0,defense-ignore)
    if value(actor,'RoleArmorBreak') and rng.randrange(1000)<100:
        target['defense']=defense*7//10;defense=target['defense']
    damage=max(1,floor(damage-defense))
    damage=damage*(1000+value(actor,'RolePhysicalPercent')+value(actor,'XQAddHurtHpP'))//1000
    damage=damage*max(0,1000-value(target,'XQSubHurtHpP'))//1000
    if value(actor,'RolePhysicalExtraHit'):damage*=1+min(1,value(actor,'RolePhysicalExtraHit'))
    # The native spirit-element bonus explicitly excludes monsters.
    ae,te=value(actor,'RoleLingElement'),value(target,'RoleLingElement')
    if ae and target.get('kind') in ('hero','pet') and target.get('owner'):
        defeats={1:2,2:5,3:4,4:1,5:3};relation=1 if not te or defeats[ae]==te else -1 if defeats.get(te)==ae else 0
        if relation:
            difference=value(actor,'RoleLingElementValue')-value(target,'RoleLingElementValue');damage=floor(damage*(1+relation*(6+relation*difference/22.5)/100))
    if defend:damage=max(1,floor(damage*.5))
    return dict(damage=damage,miss=miss,effect=effect)


def counter_ready(pet,used,rng,*,attacker=None):
    if attacker and attacker.get('book_ignore_counter',0)>0 and rng.randint(1,100)<=min(100,attacker['book_ignore_counter']):return False
    chance=pet.get('book_counter',0)+value(pet,'FanjiRate')/10
    maximum=max(pet.get('book_counter_count',0),int(chance>0))
    return pet['hp']>0 and used<maximum and chance>0 and rng.randint(1,100)<=min(100,chance)
