"""Opt-in death-event setup for the already-scoped native acceptance owner."""
from time import monotonic
from native946_codec import packet
from native946_guardian_passives import RULES


def run(login,request):
    game=login.gameplay;server=getattr(login,'_acceptance_server',None)
    if server is None or server.native_test_control.character_id!=game.character['id']:
        raise ValueError('No scoped guardian acceptance session')
    if request['action']=='abort_guardian_passive':
        if game.battle is None:return (packet(2146,[],[]),)
        if not game.battle.get('guardian_acceptance'):raise ValueError('An unrelated battle is active')
        from native946_acceptance import cleanup
        result=(packet(2146,[],[]),*game.finish_battle(False))
        cleanup(login,notify=True)
        return result
    if request['action']=='restore_guardian_passive':
        saved=getattr(login,'_guardian_passive_restore',None)
        if game.battle:raise ValueError('Leave the fixture battle before restoring vitals')
        if saved is None:return (packet(2146,[],[]),*game.restore_scene(),*game.sync_progression())
        game.progression.set_vitals(saved['hp'],saved['mp'])
        for sid,vitals in saved['fixtures'].items():game.summons.set_vitals(sid,*vitals)
        if saved['fight'] is not None:game.summons.status(saved['fight'],1)
        else:
            active=game.summons.active()
            if active:game.summons.status(active['id'],2)
        del login._guardian_passive_restore
        from native946_acceptance import cleanup
        cleanup(login)
        return (packet(2146,[],[]),*game.restore_scene(),*game.scene_objects(),*game.sync_progression())
    if game.battle is not None:raise ValueError('Finish the current battle first')
    skill=request.get('skill')
    if type(skill) is not int or skill not in RULES:raise ValueError('Unsupported guardian fixture')
    if not hasattr(login,'_guardian_passive_restore'):
        state=game.progression.snapshot();active=game.summons.active()
        login._guardian_passive_restore=dict(hp=state['hp'],mp=state['mp'],fight=active['id'] if active else None,fixtures={})
    sid=game.summons.acquire(RULES[skill]['species'],'acceptance:innate-core:'+str(skill))
    guardian=game.summons.get(sid);saved=login._guardian_passive_restore
    saved['fixtures'].setdefault(sid,(guardian['hp'],guardian['mp']))
    game.summons.set_vitals(sid,1,guardian['mpmax']);game.summons.status(sid,1)
    hero=game.progression.snapshot();game.progression.set_vitals(min(20,hero['hpmax']),hero['mp'])
    sync=game.sync_progression();replies=game.begin_battle()
    battle=game.battle
    battle.update(enemy_hp=100000,enemy_hpmax=100000,guardian_acceptance=skill,input_deadline=monotonic()+180,
                  _guardian_input_draw_acks=1)
    from native946_gameplay import ENEMY_ID
    from native946_role_battle import solo_fighters
    fighters=solo_fighters(game)
    fighters[game.character['id']]['hp']=min(20,hero['hpmax'])
    fighters[ENEMY_ID].update(hp=100000,hpmax=100000,slot=2)
    nums=lambda mid,*v:packet(mid,['number']*len(v),v)
    login._acceptance_reply=dict(skill=skill,summon_id=sid,source_mp=guardian['mpmax'],
                                  expected_retaliation_attack=guardian['attack'],
                                  hero_max_hp=hero['hpmax'],fixture_round=1)
    return (*sync,*replies,nums(2148,ENEMY_ID,100000,100000,100000,0,0,0,0,100,0),nums(2167))
