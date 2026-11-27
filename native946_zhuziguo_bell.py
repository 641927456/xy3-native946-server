"""Verified bell input/energy rules, enabled only by a trusted battle definition.

This module supplies no encounter stats, AI, drops, instance or entry route.
The current activity catalog keeps Zhuzi entry unavailable until those exist.
Native protocol IDs are from the revision-946 registry, not archive indexes.
"""
from time import monotonic
from native946_codec import packet

MECHANISM='zhuziguo_bell'
PROTECTION_WIRE_VERSION=1


def numbers(mid,*values):return packet(mid,['number']*len(values),values)


def configure(battle):
    if battle.zone.get('dungeon_mechanism')!=MECHANISM:return
    battle.state['zhuziguo_bell']=dict(energy=5,armed=False,active_round=0,gain_round=1,
        last_toggle=None,credited=set())
    battle.state['extra_orders']={}


def count(state,fid):
    bell=state.get('zhuziguo_bell')
    return 2 if bell and bell['active_round']==state['round'] and state['fighters'][fid]['kind']=='hero' else 1


def orders(state,fid):
    first=state['orders'].get(fid)
    return ([first] if first else [])+state.get('extra_orders',{}).get(fid,[])


def all_orders(state):
    yield from state['orders'].values()
    for extra in state.get('extra_orders',{}).values():yield from extra


def command(order):
    kind=order['kind']
    if kind=='medicine':return 8,order.get('display_item_type',0)
    if kind=='summon':return 6,order.get('display_summon_type',0)
    if order.get('skill_id') or order.get('guardian_skill'):return 9,order.get('skill_id',order.get('guardian_skill'))
    return {'defend':1,'attack':3,'protect':4,'capture':5,'dismiss':7}[kind],0


def entry(state):
    bell=state.get('zhuziguo_bell')
    if not bell:return ()
    # Close creates a fresh panel value before applying the authoritative delta.
    return (numbers(4888,0),numbers(4888,1),numbers(4889,bell['energy']),
            numbers(4888,3 if bell['armed'] or bell['active_round']==state['round'] else 4))


def activate(state):
    bell=state['zhuziguo_bell']
    if bell['active_round']==state['round']:return ()
    if bell['energy']<10:raise ValueError('紫金铃灵气不足10点。')
    bell.update(energy=bell['energy']-10,armed=False,active_round=state['round'])
    return numbers(4892,-10),numbers(4888,3),numbers(4887)


def toggle(battle,cid):
    state=battle.state;bell=state.get('zhuziguo_bell')
    if not bell:raise ValueError('当前战斗没有紫金铃。')
    if cid!=battle.leader:raise ValueError('只有队长可以操作紫金铃。')
    if state['phase'] not in ('input','drawing'):return ()
    if state['victory'] or state.get('defeat'):return ()
    if bell['active_round']==state['round'] and state['phase']=='input':
        raise ValueError('本回合紫金铃已经生效，不能取消。')
    now=monotonic()
    if bell['last_toggle'] is not None and now-bell['last_toggle']<3:
        raise ValueError('紫金铃操作过快，请稍后再试。')
    if state['phase']=='input' and now>=state['deadline']:return ()
    if bell['armed']:
        bell['armed']=False;replies=(numbers(4888,4),)
    elif state['phase']=='drawing':
        if bell['energy']<10:raise ValueError('紫金铃灵气不足10点。')
        bell['armed']=True;replies=(numbers(4888,3),)
    else:
        replies=activate(state)
    bell['last_toggle']=now
    battle.broadcast(*replies)
    if state['phase']=='input':
        for owner,game in battle.games.items():
            # Input-end for the first slot may already have reached this client.
            state['input_ended'][owner].discard(owner)
            actor=battle.pending_actor(owner)
            if actor:battle.service.enqueue(game,numbers(2151,actor,max(1,int(state['deadline']-now)),0),numbers(2139,2))
    battle.project()
    return ()


def next_round(state):
    bell=state.get('zhuziguo_bell')
    if not bell:return ()
    if bell['gain_round']>=state['round']:return ()
    state['extra_orders']={};bell['active_round']=0
    replies=[]
    if state['round']>=2 and bell['gain_round']<state['round']:
        gain=min(2,20-bell['energy']);bell['energy']+=gain;bell['gain_round']=state['round']
        if gain:replies.append(numbers(4889,gain))
    if bell['armed']:
        if bell['energy']>=10:replies.extend(activate(state))
        else:bell['armed']=False;replies.append(numbers(4888,4))
    else:replies.append(numbers(4888,4))
    return tuple(replies)


def defeated(state,fighter):
    """Credit only a roster's explicit verified star/boss award, once per life."""
    bell=state.get('zhuziguo_bell');award=fighter.get('bell_reward')
    if not bell or fighter.get('kind')!='enemy' or fighter.get('hp',1)>0 or type(award) is not int or award not in (1,2,3,4):return ()
    if fighter['id'] in bell['credited']:return ()
    bell['credited'].add(fighter['id'])
    gain=min(award,20-bell['energy']);bell['energy']+=gain
    return (numbers(4889,gain),) if gain else ()


def revived(state,fighter):
    if state.get('zhuziguo_bell'):state['zhuziguo_bell']['credited'].discard(fighter['id'])
