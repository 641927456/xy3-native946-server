"""Scoped renderer fixtures on an owned native session; no device input."""
from random import Random
from time import monotonic
from native946_codec import packet

CASES=('hit','miss','cruel','fatal','defend','spell','heal','revive','seal','reflection','swap','dismiss','medicine_hp','medicine_mp','mana_restore','mana_drain','seal_clear','poison','shared_escape','completed_exit','official_combat')
GUARDIAN_VISUALS={4906:(3374,'境·金光太岁',5),4897:(3373,'境·红孩儿',1),4899:(3372,'境·善财龙女',5),4909:(3375,'境·鬼目观音',1)}
CASES+=tuple('guardian_'+str(skill) for skill in GUARDIAN_VISUALS)
def nums(mid,*values):return packet(mid,['number']*len(values),values)


def run(login,request):
    game=login.gameplay;server=getattr(login,'_acceptance_server',None)
    if server is None or server.native_test_control.character_id!=game.character['id']:
        raise ValueError('No scoped presentation acceptance owner')
    if request['action']=='prepare_battle_presentation':
        if game.battle is not None:raise ValueError('An existing battle is active')
        case=request.get('case')
        if case not in CASES:raise ValueError('Unknown presentation fixture')
        if hasattr(login,'_guardian_passive_restore'):raise ValueError('Restore the earlier fixture first')
        state=game.progression.snapshot();pets=game.summons.snapshot();active=game.summons.active()
        if not active:raise ValueError('The owned character needs an active guardian')
        login._guardian_passive_restore=dict(hp=state['hp'],mp=state['mp'],fight=active['id'],
                                              fixtures={pet['id']:(pet['hp'],pet['mp']) for pet in pets})
        if case=='shared_escape':
            from native946_wilderness import WildBattle,DATA
            battle=WildBattle(game,[game],DATA['zones'][0]);b=game.battle
            b.update(guardian_acceptance='presentation',presentation_case=case)
            login._acceptance_reply=dict(case=case,hero_id=game.character['id'],pet_id=active['id'],enemy_id=battle.enemies()[0]['id'])
            return (*battle.entry(game),nums(2167))
        replies=game.begin_battle();b=game.battle
        b.update(guardian_acceptance='presentation',presentation_case=case,
                 _guardian_input_draw_acks=1,input_deadline=monotonic()+180)
        from native946_role_battle import solo_fighters
        from native946_gameplay import ENEMY_ID
        fighters=solo_fighters(game);hero=fighters[game.character['id']];enemy=fighters[ENEMY_ID];pet=b['pet']
        enemy.update(hp=100000,hpmax=100000,defense=0,attack=10)
        visual_packets=[]
        if case.startswith('guardian_'):
            skill=int(case.split('_')[1]);kind,name,count=GUARDIAN_VISUALS[skill]
            pet.update(type=kind,name=name,innate_skills=[dict(stunt_id=skill)])
            from native946_login import MessageReader
            replies=tuple(game.fighter(pet['id'],kind,name,4,3,6,pet['level'])
                          if reply[:3]==nums(2147) and MessageReader(reply[3:]).integer()==pet['id'] else reply
                          for reply in replies)
            if skill==4906:
                for index in range(1,count):
                    unit=dict(enemy,id=ENEMY_ID+index,slot=index+1)
                    fighters[unit['id']]=unit
                    visual_packets.extend((game.fighter(unit['id'],211,'训练木偶',3,2,unit['slot']),game.battle_vitals_packet(unit)))
        # Fixture-only actor values isolate visual outcomes. No learned skills,
        # growth coefficients or inventory are written to the player's database.
        for actor in (hero,pet):
            actor.update(attack=37,AttValue=1000,learned_books=[])
            if not case.startswith('guardian_'):actor['innate_skills']=[]
        pet.update(hp=pet['hpmax'],mp=pet['mpmax'])
        if case=='miss':enemy['DodValue']=10000
        if case in ('cruel','fatal'):pet['CruelRate' if case=='cruel' else 'FatalRate']=1000
        if case in ('heal','reflection'):pet['hp']=10
        if case.startswith('medicine_'):hero['hp' if case=='medicine_hp' else 'mp']=1
        if case=='reflection':enemy['role_reflect']=1000
        if case=='mana_restore':hero['mp']=1
        if case=='mana_drain':enemy.update(mp=1000,mpmax=1000)
        if case=='official_combat':
            from native946_guardian_magic import skills_packet
            from native946_role_battle import control_status_packets
            hero['controls']={'seal':1}
            enemy.update(attack=100,AttValue=1000,role_all_reduction=200)
            pet['innate_skills']=[dict(stunt_id=337)]
            visual_packets.extend((*control_status_packets(hero,1),skills_packet(pet)))
        b.update(enemy_hp=enemy['hp'],enemy_hpmax=enemy['hpmax'])
        login._acceptance_reply=dict(case=case,hero_id=hero['id'],pet_id=pet['id'],enemy_id=enemy['id'])
        return (*replies,*visual_packets,nums(2148,enemy['id'],enemy['hp'],enemy['hpmax'],enemy['hpmax'],0,0,0,0,100,0),
                game.battle_vitals_packet(pet),game.battle_vitals_packet(hero),nums(2167))
    b=game.battle
    if not b or b.get('guardian_acceptance')!='presentation' or b['phase']!='input':
        raise ValueError('No ready owned presentation fixture')
    if request['action']=='finish_battle_presentation':
        if b['presentation_case']!='completed_exit' or not b.get('presentation_played'):raise ValueError('No completed presentation round')
        return game.finish_battle(False,completed=True)
    if b.get('presentation_played'):raise ValueError('Presentation fixture already played')
    if b['presentation_case']=='official_combat':raise ValueError('Submit this fixture through the native battle menu.')
    if b['presentation_case']=='shared_escape':
        reply=login.receive(packet(2178,['number'],[2]))
        login._acceptance_reply=dict(case='shared_escape',native_escape_request=2178)
        return (*(reply if isinstance(reply,tuple) else (reply,) if reply else ()),*game.party_service.drain(game))
    from native946_role_battle import Runtime,solo_fighters
    from native946_gameplay import ENEMY_ID
    fighters=solo_fighters(game);hero=fighters[game.character['id']];enemy=fighters[ENEMY_ID];pet=b['pet']
    runtime=Runtime(fighters,{hero['id']:game},b['round'],Random(1),b);case=b['presentation_case']
    if case.startswith('guardian_'):
        skill=int(case.split('_')[1])
        targets=runtime.allies(pet) if skill==4899 else runtime.enemies(pet)
        targets=targets[:GUARDIAN_VISUALS[skill][2]]
        # Native presentation only: no invented damage, healing or mana cost.
        runtime.group(pet,targets,skill);runtime.end()
    elif case in ('hit','miss','cruel','fatal','reflection','completed_exit'):runtime.attack(pet,enemy)
    elif case=='defend':
        runtime.defending.add(hero['id'])
        runtime.attack(enemy,hero)
    elif case=='spell':runtime.cast(hero,6100,enemy['id'])
    elif case=='heal':runtime.group(hero,[pet],41);runtime.heal(hero,pet,50);runtime.end()
    elif case=='revive':
        runtime.group(enemy,[pet],10);runtime.damage(enemy,pet,pet['hp']);runtime.end()
        runtime.group(hero,[pet],46);runtime.revive(hero,pet,200);runtime.end()
    elif case=='seal':runtime.group(hero,[enemy],33);runtime.control(hero,enemy,'seal',base=1000);runtime.end()
    elif case=='seal_clear':
        runtime.group(hero,[enemy],33);runtime.control(hero,enemy,'seal',base=1000);runtime.end()
        runtime.group(hero,[pet],41);runtime.heal(hero,pet,1);runtime.end();runtime.cleanse(hero,enemy,('seal',))
    elif case=='poison':runtime.cast(hero,3,enemy['id'])
    elif case=='mana_restore':runtime.mana(hero,100)
    elif case=='mana_drain':runtime.group(hero,[enemy],51);runtime.shock(hero,enemy);runtime.end()
    elif case.startswith('medicine_'):
        item=next(i for i in game.progression.snapshot()['items'] if i['type']==(4000 if case=='medicine_hp' else 4001))
        with game.progression.transaction() as db:
            db.execute('SAVEPOINT presentation_item')
            try:runtime.item(game,hero,dict(item_id=item['id'],target=hero['id']),db)
            finally:db.execute('ROLLBACK TO presentation_item');db.execute('RELEASE presentation_item')
    elif case in ('swap','dismiss'):
        order=dict(kind='dismiss')
        if case=='swap':
            candidate=next(p for p in game.summons.snapshot() if p['id']!=pet['id'] and p['hp']>0 and p['honesty']>=50)
            order=dict(kind='summon',summon_id=candidate['id'])
        runtime.out.extend(game.apply_guardian_order(order));solo_fighters(game)
    b.update(hp=hero['hp'],mp=hero['mp'],enemy_hp=enemy['hp'],phase='drawing',presentation_played=True)
    login._acceptance_reply=dict(case=case,hero_hp=hero['hp'],pet_hp=pet['hp'],enemy_hp=enemy['hp'],
                                  packet_ids=[int.from_bytes(p[1:3],'little') if p[0]==0 else p[0] for p in runtime.out])
    if case.startswith('guardian_'):login._acceptance_reply.update(formula_verified=False,mana_charged=0,visual_targets=[target['id'] for target in targets])
    return (nums(5349),*game.end_input(hero['id']),*game.end_input(pet['id']),*runtime.out,nums(2167))
