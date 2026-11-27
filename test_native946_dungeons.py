"""Original dungeon metadata, native dispatch, entry ownership and no settlement."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import json
import sys
import unittest

from native946_activities import Activities,CALLBACK,RECORDS as ACTIVITIES
from native946_codec import packet
from native946_dungeons import DATA,RECORDS,BY_NAME,ENTRANCES,Dungeons,NPC_RECORDS,description,VERIFIED_RULES
from native946_login import MessageReader
from native946_map import load_map
from native946_world import world
import test_native946_tasks as fixtures


def packets(value):
    if isinstance(value,bytes):return [value]
    if isinstance(value,(tuple,list)):return [p for v in value for p in packets(v)]
    return []


def mid(value):return int.from_bytes(value[1:3],'little') if value[0]==0 else value[0]


def text(reply):
    result=[]
    for value in packets(reply):
        if mid(value)!=1025:continue
        reader=MessageReader(value[3:])
        for _ in range(reader.take(1)[0]):result.append(reader.string().decode('gb18030'));reader.integer()
    return '#r'.join(result)


class DungeonCatalogTests(unittest.TestCase):
    def test_revision_945_daily_reentry_rule_supersedes_eight_stale_client_descriptions(self):
        daily={'勇闯狮驼岭','无底洞','逃离万妖国','白龙剑冢','连环灭佛','智破通天','封印千鬼域','无界孤城'}
        self.assertEqual(set(VERIFIED_RULES['daily_entry_revision']['names']),daily)
        before=deepcopy(DATA)
        for name,row in BY_NAME.items():
            shown=description(name)
            if name in daily:
                self.assertIn('10分钟',row['Desc'])
                self.assertNotIn('10分钟',shown)
                self.assertIn('每天只可进入1次',shown)
                self.assertIn('普通与困难共享次数',shown)
                self.assertIn('完成或中途放弃',shown)
                self.assertIn('当天均不能再次进入',shown)
            else:self.assertIn(row['Desc'],shown)
        self.assertEqual(DATA,before)
        self.assertFalse(VERIFIED_RULES['daily_entry_revision']['reset_hour_verified'])

    def test_current_archive_catalog_and_virtual_reward_names_are_preserved(self):
        root=Path(__file__).parent
        sys.path[:0]=[str(root/'analysis/town-portals-20261125'),str(root/'analysis/calendar-activities-20261126')]
        from client_archive import Archive
        from literal_schema import replay
        from build_rules import plain
        archive=Archive(root/'client-946/resource/configs.wdf')
        exports={};values=replay(archive.read('info/info_dungeon.lua'),exports)
        original=plain(values[0]) if values else {key:plain(value) for key,value in exports.items()}
        self.assertEqual(DATA['DungeonConfig'],original['DungeonConfig'])
        self.assertEqual(len(RECORDS),28)
        self.assertEqual(BY_NAME['生肖问剑']['Index'],19)
        self.assertEqual(DATA['item_metadata']['910117']['name'],'仙器')
        self.assertEqual(DATA['item_metadata']['910154']['name'],'圣珠')
        self.assertEqual(DATA['item_metadata']['910247']['name'],'珍稀坐骑随机礼包')
        self.assertTrue(all(str(kind) in DATA['item_metadata'] for row in RECORDS.values() for kind,_ in row['Rewards']))

    def test_every_static_entrance_matches_original_world_without_coordinate_guessing(self):
        self.assertEqual(sum(bool(v) for v in ENTRANCES.values()),26)
        for key,targets in ENTRANCES.items():
            for target in targets:
                npc=world().npc(target['scene'],target['npc'])
                self.assertIsNotNone(npc)
                self.assertEqual((target['x'],target['y']),(npc['x']//20,npc['y']//20))
                self.assertIn(key,NPC_RECORDS[target['scene'],target['npc']])
        self.assertEqual(ENTRANCES[11],[]) # Original randomized instance portals.
        self.assertEqual(ENTRANCES[18],[]) # Coordinate-only spatial rift.
        self.assertEqual(ENTRANCES[19],[dict(scene=2223,npc=8013,name='问剑使者',x=87,y=48)])


class DungeonDispatchTests(unittest.TestCase):
    setUp=fixtures.TaskJournalTests.setUp
    send=fixtures.TaskJournalTests.send

    def choose(self,op,**fields):
        index=next(i for i,a in enumerate(self.g.dialog_actions,1)
            if a['op']==op and all(a.get(k)==v for k,v in fields.items()))
        return self.login.receive(packet(1032,['byte','number'],[index,self.g.dialog_npc]))

    def teleport(self,target):
        c=self.g.character
        rev=self.characters.change_scene(c['owner'],c['id'],c['map_id'],target['scene'],target['x'],target['y'],c.get('position_revision',0))
        c.update(map_id=target['scene'],x=target['x'],y=target['y'],position_revision=rev)
        self.g.grid=load_map(c['map_id']);self.g.npc_position=None

    def test_native_hall_refresh_only_sends_progress_and_preserves_existing_dialogue(self):
        before=self.p.snapshot()
        progress=packet(1970,[('array','dungeon_task')],[[]])
        self.assertEqual(packets(self.send(1988)),[progress])
        self.assertFalse(self.g.dialog_open)
        Activities(self.g).root()
        dialogue=(self.g.dialog_open,self.g.dialog_npc,deepcopy(self.g.dialog_actions))
        self.assertEqual(packets(self.send(1988)),[progress])
        self.assertEqual((self.g.dialog_open,self.g.dialog_npc,self.g.dialog_actions),dialogue)
        self.assertEqual(self.p.snapshot(),before)

    def test_explicit_activity_menu_can_still_browse_all_dungeons(self):
        before=self.p.snapshot()
        Activities(self.g).root()
        self.assertIn('副本与秘境',text(self.choose('dungeon_list')))
        seen=set();page=0
        while True:
            seen.update(a['dungeon'] for a in self.g.dialog_actions if a['op']=='dungeon_detail')
            if not any(a['op']=='dungeon_list' and a.get('page')==page+1 for a in self.g.dialog_actions):break
            page+=1;self.choose('dungeon_list',page=page)
        self.assertEqual(seen,set(RECORDS))
        self.assertEqual(self.p.snapshot(),before)

    def test_sword_npc_is_unavailable_and_explicit_catalog_keeps_navigation(self):
        target=ENTRANCES[19][0];self.teleport(target);before=self.p.snapshot()
        opened=self.send(1034,target['npc'])
        self.assertEqual([mid(p) for p in packets(opened)],[1183,100])
        self.assertFalse(self.g.dialog_open)
        reply=Dungeons(self.g).detail(19,CALLBACK)
        self.assertIn('封圣165级',text(reply));self.assertNotIn('十二生肖',text(reply))
        reply=packets(self.choose('dungeon_navigate'))
        expected=packet(2966,['number']*4+['string'],[87,48,2223,8013,'问剑使者'])
        self.assertIn(expected,reply);self.assertFalse(self.g.dialog_open)
        self.assertEqual(self.p.snapshot(),before)

    def test_shared_unavailable_entrance_displays_one_native_notice(self):
        target=ENTRANCES[1][0];self.teleport(target)
        closed=packets(self.send(1034,target['npc']))
        self.assertEqual([mid(p) for p in closed],[1183,100])
        notice=MessageReader(closed[-1][1:]).string().decode('gb18030')
        self.assertTrue(all(RECORDS[key]['Name'] in notice for key in (1,6,8)))
        self.assertEqual(self.g.dialog_actions,[])

    def test_all_detail_documents_and_reward_pages_do_not_accept_or_pay(self):
        before=self.p.snapshot();dungeons=Dungeons(self.g)
        for key,row in RECORDS.items():
            self.assertIn(row['Desc'].split('#r')[0],text(dungeons.detail(key,CALLBACK)))
            self.assertIn('待接通',text(dungeons.detail(key,CALLBACK)))
            output=[];page=0
            while True:
                output.append(text(dungeons.document(key,page,CALLBACK)))
                if not any(a['op']=='dungeon_rewards' and a.get('page')==page+1 for a in self.g.dialog_actions):break
                page+=1
            all_text='#r'.join(output)
            for kind,_ in row['Rewards']:self.assertIn(DATA['item_metadata'][str(kind)]['name'],all_text)
            if row['Name'] in DATA['legacy_background']:
                self.assertIn('旧版',text(dungeons.document(key,0,CALLBACK,background=True)))
        self.assertEqual(self.p.snapshot(),before)
        self.assertIsNone(self.g.battle)

    def test_activity_details_use_current_dungeon_description_and_keep_existing_movie_choices(self):
        row=next(row for row in ACTIVITIES.values() if row['name']=='白龙剑冢')
        reply=Activities(self.g).detail(row['id'])
        self.assertIn('普通与困难共享次数',text(reply))
        self.assertNotIn('10分钟',text(reply))
        self.assertTrue(any(a['op']=='dungeon_detail' for a in self.g.dialog_actions))
        row=next(row for row in ACTIVITIES.values() if row['name']=='天蓬问情')
        Activities(self.g).detail(row['id'])
        self.assertTrue(any(a['op']=='dungeon_dialogues' for a in self.g.dialog_actions))
        self.assertTrue(any(a['op']=='guide' for a in self.g.dialog_actions))

    def test_unavailable_dungeon_invalidates_prior_npc_dialogue(self):
        target=ENTRANCES[19][0];self.teleport(target)
        self.g.dialog_actions=[dict(kind='activity',op='dungeon_list',section='npc',page=0)]
        self.g.menu('旧对话',['旧入口'],target['npc'])
        self.send(1034,target['npc']);before=self.p.snapshot()
        self.assertIsNone(self.login.receive(packet(1032,['byte','number'],[1,target['npc']])))
        self.assertEqual(self.p.snapshot(),before)

    def test_battle_and_malformed_hall_refresh_do_not_replace_state_or_dialogue(self):
        Dungeons(self.g).listing();actions=deepcopy(self.g.dialog_actions)
        rejected=packets(self.send(1988,1));self.assertIn(98,[mid(p) for p in rejected])
        self.assertEqual(self.g.dialog_actions,actions)
        self.g.battle=dict(id='fixture-owned',phase='input',round=1)
        battle=deepcopy(self.g.battle);reply=packets(self.send(1988))
        self.assertEqual(reply,[packet(1970,[('array','dungeon_task')],[[]])])
        self.assertEqual(self.g.battle,battle);self.assertEqual(self.g.dialog_actions,actions)

    def test_invalid_ids_and_pages_and_changed_world_target_do_not_mutate_progress(self):
        dungeons=Dungeons(self.g);before=self.p.snapshot()
        for key in (-1,True,29):
            with self.assertRaises(ValueError):dungeons.detail(key,CALLBACK)
        for page in (-1,True,4):
            with self.assertRaises(ValueError):dungeons.listing(page)
        for index in (-1,True,1):
            with self.assertRaises(ValueError):dungeons.navigate(19,index)
        with patch.object(world(),'npc',return_value=None):
            with self.assertRaises(ValueError):dungeons.navigate(19,0)
        self.assertEqual(self.p.snapshot(),before)


class LocalDungeonTests(unittest.TestCase):
    setUp=fixtures.TaskJournalTests.setUp
    send=fixtures.TaskJournalTests.send
    choose=DungeonDispatchTests.choose

    def record(self):
        from native946_local_dungeons import LocalDungeons
        with self.p.transaction(write=False) as db:
            return LocalDungeons(self.g).read(db)

    def open_local(self, key):
        Activities(self.g).root()
        self.choose('dungeon_list')
        self.choose('local_dungeon_list')
        return self.choose('local_dungeon_detail',key=key)

    def play_current_wave(self):
        self.choose('local_dungeon_fight')
        battle=self.g._wild_battle
        self.assertIsNotNone(battle)
        for _ in range(40):
            if self.g.battle is None:
                return battle
            if battle.state['phase']=='drawing':
                self.send(2184)
                continue
            for actor_id in sorted(battle.required()):
                if self.g.battle is None or battle.state['phase']!='input':
                    break
                actor=battle.state['fighters'][actor_id]
                self.send(2181 if actor['kind']=='pet' else 2177,3,battle.enemies()[0]['id'])
        self.fail('Local dungeon battle did not exit')

    def test_both_local_dungeons_play_through_the_native_hall_and_pay_once(self):
        from native946_local_dungeons import DUNGEONS
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=30 WHERE id=?',(self.p.cid,))
            db.execute('UPDATE progression SET cor=40,strength=40 WHERE character_id=?',(self.p.cid,))
        self.g.character['level']=30
        attributes=self.p.snapshot()
        self.p.set_vitals(attributes['hpmax'],attributes['mpmax'])
        before=self.p.snapshot()
        for key, design in DUNGEONS.items():
            self.assertIn('本机规则',text(self.open_local(key)))
            self.choose('local_dungeon_accept')
            run=self.record()['run']
            self.assertFalse(run['entered'])
            self.choose('local_dungeon_enter')
            self.assertTrue(self.record()['run']['entered'])
            for wave in range(len(design['waves'])):
                self.assertEqual(self.record()['run']['wave'],wave)
                battle=self.play_current_wave()
                self.assertTrue(battle.state['victory'])
                self.assertEqual(battle.run['wave'],wave)
            self.assertIsNone(self.record()['run'])
            self.assertIn(key,self.record()['clears'])
            self.assertNotIn('local_dungeon_accept',{item['op'] for item in self.g.dialog_actions})
            self.send(2184)
            with self.p.transaction() as db:
                # Simulate a repeated callback from a stale battle controller.
                battle.state['settled']=False
                battle.settle(db,True)
                self.assertEqual(db.execute('SELECT count(*) FROM battle_results WHERE battle_id=?',
                                           ('local-dungeon:'+run['id'],)).fetchone()[0],1)
            self.p.set_vitals(attributes['hpmax'],attributes['mpmax'])
        after=self.p.snapshot()
        self.assertEqual(after['experience']-before['experience'],sum(d['experience'] for d in DUNGEONS.values()))
        self.assertEqual(after['gold']-before['gold'],sum(d['gold'] for d in DUNGEONS.values()))

    def test_retreat_and_reopen_uses_saved_instance_and_requires_enter_after_leave(self):
        self.open_local('pine_drill')
        self.choose('local_dungeon_accept')
        self.choose('local_dungeon_enter')
        self.assertTrue(self.play_current_wave().state['victory'])
        progress=self.record()['run']
        self.choose('local_dungeon_fight')
        self.send(2178,2)
        self.assertEqual(self.record()['run'],progress)
        self.assertFalse(self.record()['clears'])
        self.choose('local_dungeon_leave')
        self.open_local('pine_drill')
        self.assertIn('local_dungeon_enter',{action['op'] for action in self.g.dialog_actions})
        self.assertEqual(self.record()['run'],dict(progress,entered=False))
        self.choose('local_dungeon_enter')
        self.assertEqual(self.record()['run'],progress)
        self.choose('local_dungeon_confirm')
        self.choose('local_dungeon_abandon')
        self.assertIsNone(self.record()['run'])
        self.assertFalse(self.record()['clears'])


class NativeEntranceDungeonTests(unittest.TestCase):
    setUp=fixtures.TaskJournalTests.setUp
    send=fixtures.TaskJournalTests.send
    choose=DungeonDispatchTests.choose
    teleport=DungeonDispatchTests.teleport

    def qualify(self):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=180 WHERE id=?',(self.p.cid,))
            db.execute('UPDATE cultivation_state SET ascension=1,sanctification=1 WHERE character_id=?',(self.p.cid,))
            db.execute('UPDATE progression SET cor=200,strength=200,speed=200 WHERE character_id=?',(self.p.cid,))
        self.g.character['level']=180
        hero=self.p.snapshot()
        self.p.set_vitals(hero['hpmax'],hero['mpmax'])

    def open_entry(self, key):
        entry=ENTRANCES[key][0]
        x,y=load_map(entry['scene']).nearest_walkable(entry['x'],entry['y'])
        self.teleport(dict(entry,x=x,y=y))
        self.assertEqual([mid(p) for p in packets(self.send(1034,entry['npc']))],[1025])
        self.assertFalse(any(a.get('kind')=='activity' for a in self.g.dialog_actions if isinstance(a,dict)))
        return self.choose('show',key=key)

    def test_zhuzi_local_entry_coexists_with_campaign_at_same_npc(self):
        from native946_campaign import CHAPTERS

        target=CHAPTERS['125051']['nodes'][44]['target']
        x,y=target['arrival']
        self.teleport(dict(target,x=x,y=y))
        with self.p.transaction() as db:
            db.execute("INSERT INTO native_campaign_runs VALUES(?,'125051',44,'active',1,0)",(self.p.cid,))
        self.p._cached_snapshot=None
        reply=self.send(1034,target['npc'])
        self.assertIn('恶紫夺朱',text(reply))
        self.assertEqual(self.g.dialog_actions[0]['op'],'complete')
        self.assertIn('本机战斗与奖励规则',text(self.choose('show',key=25)))
        self.assertEqual(self.p.snapshot()['campaign']['runs']['125051']['node'],44)

    def play_wave(self):
        reply=packets(self.choose('fight'))
        battle=self.g._wild_battle
        self.assertIsNotNone(battle)
        self.assertIn(2171,[mid(value) for value in reply])
        if battle.run['dungeon_id']==25:
            self.assertIn(4888,[mid(value) for value in reply])
        for _ in range(35):
            if not self.g.battle:
                self.assertTrue(battle.state['victory'])
                return
            if battle.state['phase']=='drawing':
                self.send(2184)
            else:
                self.send(2177,3,battle.enemies()[0]['id'])
        self.fail('Dungeon wave did not finish')

    def play(self, key):
        from native946_dungeon_routes import ROUTES,GUIDE,read_run
        self.qualify()
        start=self.p.snapshot()
        self.assertIn('本机战斗与奖励规则',text(self.open_entry(key)))
        self.choose('accept')
        self.send(1034,ENTRANCES[key][0]['npc'])
        self.assertTrue(any(action['op']=='travel' for action in self.g.dialog_actions))
        reply=packets(self.choose('travel'))
        self.assertIn(1597,[mid(value) for value in reply])
        for index,(scene,_,_,_) in enumerate(ROUTES[key]['waves']):
            self.assertEqual(self.g.character['map_id'],scene)
            self.send(1034,GUIDE[key])
            self.play_wave()
            with self.p.transaction(write=False) as db:
                run=read_run(db,self.p.cid,key)
                self.assertEqual(run['stage'],index+1)
                self.assertEqual(run['state'],'entered')
            self.send(1034,GUIDE[key])
            if index==0 and key==26:
                replies=packets(self.choose('puzzle'))
                clue=next(value for value in replies if mid(value)==4164)
                answer=MessageReader(clue[3:]).string().decode('gb18030')
                bad=packets(self.login.receive(packet(4168,['string'],['{0,0,0}'])))
                self.assertIn(98,[mid(value) for value in bad])
                solved=packets(self.login.receive(packet(4168,['string'],[answer])))
                self.assertIn(4162,[mid(value) for value in solved])
                self.send(1034,GUIDE[key])
            if index<2:
                self.choose('travel')
        self.assertIn('可领取奖励',text(self.send(1034,GUIDE[key])))
        stale=next(a for a in self.g.dialog_actions if a['op']=='claim')
        self.choose('claim')
        from native946_dungeon_routes import DungeonRoute
        with self.assertRaises(ValueError):DungeonRoute(self.g).execute(GUIDE[key],stale)
        after=self.p.snapshot()
        self.assertEqual(after['experience']-start['experience'],ROUTES[key]['experience'])
        self.assertEqual(after['gold']-start['gold'],ROUTES[key]['gold'])
        with self.p.transaction(write=False) as db:
            self.assertEqual(read_run(db,self.p.cid,key)['state'],'completed')
            self.assertEqual(db.execute('SELECT count(*) FROM battle_results WHERE battle_id=?',
                                        ('dungeon:'+run['instance_id'],)).fetchone()[0],1)
        progress=packet(1970,[('array','dungeon_task')],[[dict(Status=2,DungeonName=RECORDS[key]['Name'],
                        ResetTime=run['reset_at'])]])
        self.assertEqual(packets(self.send(1988)),[progress])
        self.send(1034,GUIDE[key])
        self.choose('leave')
        self.assertEqual(self.g.character['map_id'],ENTRANCES[key][0]['scene'])

    def test_zhuzi_original_npc_and_three_native_scene_transitions(self):
        self.play(25)

    def test_tianpeng_original_npc_puzzle_and_reward(self):
        self.play(26)

    def test_accept_rejects_ineligible_character_before_creating_progress(self):
        self.open_entry(25)
        reply=packets(self.choose('accept'))
        self.assertIn(98,[mid(value) for value in reply])
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_dungeon_route_progress').fetchone()[0],0)


if __name__=='__main__':unittest.main()
