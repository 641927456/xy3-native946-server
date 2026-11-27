"""Owned cinematic playback and native completion, without quest settlement."""
from copy import deepcopy
from native946_codec import packet

NEW_CHARACTER_INTRO = 1432


def play(game,script,*,resume=None):
    if type(script) is not int or script<=0:raise ValueError('过场编号无效。')
    if game.battle:raise ValueError('请先结束战斗，再观看过场。')
    return game.defer_packets(lambda:_play(game,script,resume))


def _play(game,script,resume):
    current=getattr(game,'_cinematic_pending',None)
    game.dialog_open=False;game.dialog_actions=[];game.dialog_npc=None
    close=packet(1183,['string'],['dlg_npc_chat'])
    if current and current['script']==script and current['owner']==game.character['id']:
        return (close,)
    game._cinematic_pending=dict(script=script,owner=game.character['id'],
        scene=game.character['map_id'],resume=deepcopy(resume))
    if resume and resume['kind'] in ('new_character','campaign_stage'):
        # The scene player used by 1275 returns 1458 on finish or skip.
        return close, packet(1275, ['number', 'number', 'string'], [script, 1, ''])
    return close,packet(1276,['number','number','byte'],[script,0,1])


def _resume(game,request):
    if not request:return ()
    if request['kind']=='new_character':
        from native946_campaign import current
        run = current(game.progression.snapshot())
        if run and (run['chapter'], run['node'], run['state']) == ('newbie', 0, 'active'):
            game.new_character_welcome_pending = True
            return (packet(1222, ['number', 'number'], [100, 1]),)
        return ()
    if request['kind'] in ('campaign','campaign_stage'):
        from native946_campaign import Campaign,stage,settlement_message
        campaign=Campaign(game);current=stage(campaign.state())
        if not current:return ()
        chapter,node,run=current
        if (chapter['id'],run['node'],run['state'])!=(request['chapter'],request['node'],'active'):return ()
        if node['target']['npc']!=request['npc'] or not campaign.near(node['target']):return ()
        if request['kind']=='campaign_stage':
            if not campaign.advance(chapter['id'],run['node'],cinematic=node['movie']):
                return ()
            state=campaign.state()
            reply=campaign.refreshed(settlement_message(chapter['id'],run['node'],state),screen=True)
            if node.get('travel_after'):
                _, next_node, _ = stage(state)
                return (*reply,*campaign.travel(next_node['target']))
            return reply
        return campaign.look(request['npc']) or ()
    if request['kind']=='dungeon_dialogue':
        from native946_activities import RECORDS,CALLBACK
        from native946_dungeon_dialogues import DungeonDialogues
        row=RECORDS.get(request['activity'])
        if row is None:return ()
        if request['npc']!=CALLBACK:
            from native946_npc_services import NpcServices
            try:NpcServices(game).validate(request['npc'])
            except ValueError:return ()
        return DungeonDialogues(game).menu(row,request['npc'])
    return ()


def finish(game,script):
    pending=getattr(game,'_cinematic_pending',None)
    if not pending or (pending['script'],pending['owner'])!=(script,game.character['id']):return ()
    # A late completion cannot replace a newer map or an active battle.
    if game.battle or pending['scene']!=game.character['map_id']:
        game._cinematic_pending=None
        return ()
    names=('_world_visible','_world_bucket','_shown_summon_id')
    previous={name:deepcopy(getattr(game,name,None)) for name in names}
    game._world_visible=set();game._world_bucket=None;game._shown_summon_id=None
    try:
        replies=(*game.restore_scene(),*game.scene_objects(),*game.sync_summon_scene(),
                 *game.progression.task_packets())
        resumed=_resume(game,pending['resume'])
        replies=(*replies,*(resumed if isinstance(resumed,(tuple,list)) else (resumed,) if resumed else ()))
    except BaseException as error:
        for name,value in previous.items():setattr(game,name,value)
        from native946_storage import StorageBusy
        if isinstance(error,StorageBusy):
            # Reuse the established socket-thread refresh retry. This cannot
            # complete a task or grant a reward; it only restores owned state.
            game._party_refresh_pending=True
            game._cinematic_pending=None
            from native946_codec import text_notice
            return (text_notice('过场结束，正在恢复当前场景。'),)
        raise
    game._cinematic_pending=None
    game.last_event=dict(action='cinematic_finished',script_id=script,restored_map=game.character['map_id'])
    return replies
