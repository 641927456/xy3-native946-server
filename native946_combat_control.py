"""Human controls with 946 status IDs/resources and explicit server policy.

INFO_WAR_LAST_STATUS 36/37/38 and STATUS_SHOWTYPE 113/213/313 were
recovered independently from original configs, not inferred from names.
"""
from native946_codec import packet
from native946_guardian_magic import control_chance
STATUS={'faint':36,'disorder':37,'seal':38}
SKILL={'faint':13,'disorder':23,'seal':33}
RESIST={'faint':'ReFaint','disorder':'ReDisorder','seal':'ReSeal'}
HUMAN_CONTROL=('disorder','seal','faint')
OTHER_ACTION_BLOCKS=('wenxin','soul_seal')

def blocked(fighter,round):
    return any(fighter.get('controls',{}).get(key,0)>=round for key in HUMAN_CONTROL+OTHER_ACTION_BLOCKS)

def can_select_order(fighter,round):
    return (fighter['hp']>0 and not fighter.get('removed')
            and not any(fighter.get('controls',{}).get(key,0)>=round for key in OTHER_ACTION_BLOCKS))

def expire(fighters,round):
    replies=[]
    for f in fighters:
        for key,until in list(f.get('controls',{}).items()):
            if until<round:
                del f['controls'][key]
                f.get('role_control_info',{}).pop(key,None)
                if key in STATUS:replies.append(packet(2138,['number','number'],[f['id'],STATUS[key]]))
    return replies

def wake(target,damage):
    if damage>0 and 'faint' in target.get('controls',{}):
        del target['controls']['faint'];target.get('role_control_info',{}).pop('faint',None)
        return [packet(2138,['number','number'],[target['id'],STATUS['faint']])]
    return []

def cast(actor,target,control,round,rng,*,base=700,duration=2):
    from native946_book_combat import value
    chance=control_chance(base,target,control)-value(target,RESIST[control])+value(actor,'RoleControl')+value(actor,'AddControllerDegree')-value(target,'ReControllerDegree')
    chance=max(0,min(1000,chance))*max(0,1000-value(target,'RoleLineupControlResist'))//1000
    success=rng.randrange(1000)<chance
    replies=[packet(2126,['number'],[1]),packet(2157,['number','number',('array','number'),'number'],
                    [actor['id'],target['id'],[target['id']],SKILL[control]])]
    if success:
        controls=target.setdefault('controls',{});controls[control]=max(controls.get(control,0),round+duration-1)
        target.get('role_control_info',{}).pop(control,None)
        replies.append(packet(2130,['number']*3,[target['id'],STATUS[control],0]))
    else:replies.append(packet(2144,['number'],[target['id']]))
    replies.append(packet(2126,['number'],[2]))
    return replies
