"""Classic race artifact commands, shared Xuan Yi and eight native skills."""
from time import monotonic

from native946_codec import packet


COMMAND=5259
COST=5
COOLDOWN=10
SKILLS={(1,1):(4857,8),(1,2):(4856,5), (2,1):(4859,8),(2,2):(4858,2.5),
        (3,1):(4861,10),(3,2):(4860,2.5), (7,1):(4863,10),(7,2):(4862,10)}


def initialize(state, fighters, games):
    from native946_race_fabao import state as artifact_state

    participants={}
    for cid,game in games.items():
        with game.progression.transaction(write=False) as db:
            try: saved,attrs=artifact_state(db,cid)
            except ValueError: continue
        yin=sum(row['yinyang']==1 for row in attrs);yang=sum(row['yinyang']==2 for row in attrs)
        side=saved['skill_yinyang']
        if side not in (1,2):side=1 if yin>yang else 2 if yin<yang else 0
        race=fighters[cid].get('race',game.character['race'])
        spec=SKILLS.get((race,side))
        participants[cid]=(spec[0],spec[1]*saved['tier'],saved['tier']) if spec else None
    if participants:
        state['race_fabao']=dict(participants=participants,energy=2,pending={},ready={})


def view(state,cid):
    from native946_role_battle import DATA

    artifact=state.get('race_fabao')
    if not artifact or cid not in artifact['participants']:return ()
    spec=artifact['participants'][cid]
    skills=[]
    if spec:
        skill,rate,_=spec
        desc=DATA['native_skills'][str(skill)]['war_des']
        # Lua accepts literal percent glyphs in these strings after formatting.
        desc=desc.replace('%0.1f',f'{rate:.1f}').replace('%d',str(int(rate)))
        skills.append(dict(skill_id=skill,des=desc,cost=f'#cFFD787消耗玄意：{COST}#n'))
    return (packet(5258,['number','number',('array','attack_skill_info')],[artifact['energy'],cid,skills]),)


def next_round(state):
    phase=state.get('race_fabao')
    if phase:
        phase['energy']=min(10,phase['energy']+1)
        phase['pending'].clear()


def submit(state,fighters,cid,reader):
    skill,target=reader.integer(),reader.integer();reader.finish()
    phase=state.get('race_fabao')
    actor=fighters.get(cid)
    if (not phase or not actor or actor['hp']<=0 or actor.get('removed') or state['phase']!='input'
            or monotonic()>=state.get('deadline',state.get('input_deadline',0))):
        raise ValueError('当前不能释放种族法宝。')
    spec=phase['participants'].get(cid)
    if not spec or skill!=spec[0]:raise ValueError('当前没有该种族法宝技能。')
    if actor.get('controls',{}).get('faint',0)>=state['round']:
        raise ValueError('昏睡中无法释放种族法宝。')
    if cid in phase['pending'] or phase['ready'].get(cid,0)>state['round']:
        raise ValueError('种族法宝技能还未冷却。')
    if phase['energy']<COST:raise ValueError('队伍玄意值不足。')
    if target and (target not in fighters or fighters[target].get('removed')):
        raise ValueError('种族法宝目标不在战场中。')
    phase['energy']-=COST
    phase['pending'][cid]=spec
    phase['ready'][cid]=state['round']+COOLDOWN


def cast_for_turn(runtime,actor):
    from native946_role_battle import BUFF_STATUS, profile

    phase=runtime.state.get('race_fabao')
    queued=phase and phase['pending'].pop(actor['id'],None)
    if not queued:return
    skill,rate,tier=queued
    actor['role_cooldowns'][f'skill:{skill}']=phase['ready'][actor['id']]
    if actor['controls'].get('faint',0)>=runtime.round:return
    allies=runtime.allies(actor)
    enemies=runtime.enemies(actor)
    if skill in (4856,4858,4860):
        runtime.group(actor,[actor],skill)
        key={4856:'race_fabao_slash',4858:'race_fabao_physical',4860:'race_fabao_spell'}[skill]
        runtime.buff(actor,key,int(rate*10),4,skill,locked=True)
    elif skill in (4857,4859):
        runtime.group(actor,allies,skill)
        key='race_fabao_spell_reduction' if skill==4857 else 'race_fabao_physical_reduction'
        amount=int(rate*10)
        for other in allies:
            runtime.buff(other,key,max(amount,other.get(key,0)),1,skill)
    elif skill==4861:
        for family,base_skill in (('thunder',54),('fire',44),('wind',64),('water',74)):
            targets=runtime.enemies(actor)
            if not targets or (family!='thunder' and runtime.rng.randrange(100)>=60):break
            targets=runtime.rng.sample(targets,min(len(targets),runtime.rng.randint(3,5)))
            runtime.group(actor,targets,skill)
            for other in targets:
                meta=profile(base_skill) or dict(family=family,cast_id=base_skill,tier=4,group=True)
                damage=runtime.base_damage(actor,other,meta)*tier//10
                runtime.damage(actor,other,damage,family)
            runtime.end()
        return
    elif skill==4862:
        if not enemies:return
        runtime.group(actor,enemies,skill)
        if runtime.rng.randrange(100)<rate:
            for other in enemies:
                for buff in list(other['role_buffs']):
                    meta=profile(buff['source'])
                    if buff['locked'] or not meta or meta['source']!='secret' or buff['amount']<=0:continue
                    key=buff['key'];other[key]-=buff['amount'];other['role_buffs'].remove(buff)
                    if key in BUFF_STATUS and not any(b['key']==key for b in other['role_buffs']):
                        runtime.out.append(packet(2138,['number','number'],[other['id'],BUFF_STATUS[key]]))
    elif skill==4863:
        runtime.group(actor,allies,skill)
        if runtime.rng.randrange(100)<rate:
            for other in allies:
                runtime.cleanse(actor,other,('seal',))
                runtime.buff(other,'race_fabao_seal_immunity',1,1,skill)
    runtime.end()
