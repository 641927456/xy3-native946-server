from copy import deepcopy
from unittest.mock import patch
import unittest

from native946_activities import Activities,CALLBACK,RECORDS
from native946_cinematics import play
from native946_codec import packet
from native946_dungeon_dialogues import CATALOG,DungeonDialogues
from native946_login import MessageReader
from native946_storage import StorageBusy
from native946_world import world
import test_native946_tasks as fixtures
import test_native946_campaign as campaign_fixtures


def packets(value):
    if isinstance(value,bytes):return [value]
    if isinstance(value,(tuple,list)):return [p for v in value for p in packets(v)]
    return []


def mid(value):return int.from_bytes(value[1:3],'little') if value[0]==0 else value[0]


class DungeonDialogueTests(unittest.TestCase):
    setUp=fixtures.TaskJournalTests.setUp
    send=fixtures.TaskJournalTests.send

    def row(self,name):return next(r for r in RECORDS.values() if r['name']==name)

    def start(self,name,index=0):
        return DungeonDialogues(self.g).play(self.row(name),index,CALLBACK)

    def test_both_activity_details_offer_verified_dialogues(self):
        for name,count in (('天蓬问情',5),('朱紫秘辛',2)):
            row=self.row(name);Activities(self.g).detail(row['id'])
            self.assertTrue(any(a.get('op')=='dungeon_dialogues' for a in self.g.dialog_actions))
            DungeonDialogues(self.g).menu(row,CALLBACK)
            choices=[a for a in self.g.dialog_actions if a['op']=='dungeon_dialogue_play']
            self.assertEqual(len(choices),count)
            self.assertEqual([a['clip'] for a in choices],list(range(count)))

    def test_play_end_restore_owned_scene_and_menu_without_rewards_or_progress(self):
        before=self.p.snapshot();position={k:self.g.character[k] for k in ('map_id','x','y')}
        outgoing=self.start('天蓬问情');self.assertIn(1276,[mid(p) for p in packets(outgoing)])
        self.assertFalse(self.g.dialog_open)
        replies=packets(self.send(1458,20065))
        scene=next(p for p in replies if mid(p)==1597)
        from struct import unpack
        self.assertEqual(unpack('<HHH',scene[3:9]),tuple(position.values()))
        self.assertIn(1025,[mid(p) for p in replies]);self.assertTrue(self.g.dialog_open)
        self.assertIsNone(self.g._cinematic_pending)
        self.assertEqual(self.p.snapshot(),before)
        self.assertEqual({k:self.g.character[k] for k in position},position)
        self.assertEqual(packets(self.send(1458,20065)),[])

    def test_mismatched_and_malformed_end_do_not_consume_pending(self):
        self.start('朱紫秘辛');pending=deepcopy(self.g._cinematic_pending)
        self.assertEqual(packets(self.send(1458,20065)),[])
        self.assertEqual(self.g._cinematic_pending,pending)
        self.assertIn(98,[mid(p) for p in packets(self.send(1458,20074,1))])
        self.assertEqual(self.g._cinematic_pending,pending)

    def test_newer_script_supersedes_old_and_same_script_does_not_restart(self):
        self.start('天蓬问情');same=self.start('天蓬问情')
        self.assertNotIn(1276,[mid(p) for p in packets(same)])
        self.start('朱紫秘辛');pending=deepcopy(self.g._cinematic_pending)
        self.assertEqual(packets(self.send(1458,20065)),[])
        self.assertEqual(self.g._cinematic_pending,pending)
        self.assertIn(1597,[mid(p) for p in packets(self.send(1458,20074))])

    def test_ending_cinematic_cannot_restore_over_a_battle_or_different_map(self):
        self.start('天蓬问情');self.g.battle=dict(id='fixture-active-battle',phase='input',round=1)
        battle=deepcopy(self.g.battle)
        self.assertEqual(packets(self.send(1458,20065)),[])
        self.assertEqual(self.g.battle,battle)
        self.g.battle=None;self.start('天蓬问情')
        other_scene=2008 if self.g.character['map_id']!=2008 else 2001
        self.g.character['map_id']=other_scene
        self.assertEqual(packets(self.send(1458,20065)),[])
        self.assertEqual(self.g.character['map_id'],other_scene)

    def test_busy_scene_restore_reuses_existing_background_refresh_without_settlement(self):
        self.start('朱紫秘辛');before=self.p.snapshot()
        with patch.object(self.g,'scene_objects',side_effect=StorageBusy('fixture busy')):
            replies=packets(self.send(1458,20074))
        self.assertTrue(self.g._party_refresh_pending)
        self.assertIsNone(self.g._cinematic_pending)
        self.assertIn(98,[mid(p) for p in replies])
        refreshed=self.g.party_service.tick(self.g)
        self.assertIn(1597,[mid(p) for p in packets(refreshed)])
        self.assertFalse(self.g._party_refresh_pending)
        self.assertEqual(self.p.snapshot(),before)

    def test_invalid_clip_and_battle_play_cannot_create_a_session(self):
        for index in (-1,100,True):
            with self.assertRaises(ValueError):self.start('天蓬问情',index)
        self.assertIsNone(getattr(self.g,'_cinematic_pending',None))
        self.g.battle=dict(id='fixture')
        with self.assertRaises(ValueError):self.start('朱紫秘辛')
        self.assertIsNone(getattr(self.g,'_cinematic_pending',None))

    def test_all_original_ids_round_trip_with_current_map_and_unchanged_state(self):
        before=self.p.snapshot()
        for name,clips in CATALOG.items():
            for index,clip in enumerate(clips):
                self.start(name,index)
                replies=packets(self.send(1458,clip['script']))
                self.assertIn(1597,[mid(p) for p in replies]);self.assertIn(1025,[mid(p) for p in replies])
        self.assertEqual(self.p.snapshot(),before)

    def test_activity_choices_reach_native_play_and_completion_dispatch(self):
        row=self.row('朱紫秘辛');before=self.p.snapshot()
        Activities(self.g).detail(row['id'])
        choice=next(i for i,a in enumerate(self.g.dialog_actions,1) if a['op']=='dungeon_dialogues')
        self.login.receive(packet(1032,['byte','number'],[choice,CALLBACK]))
        choice=next(i for i,a in enumerate(self.g.dialog_actions,1) if a['op']=='dungeon_dialogue_play')
        result=self.login.receive(packet(1032,['byte','number'],[choice,CALLBACK]))
        self.assertIn(1276,[mid(p) for p in packets(result)])
        result=self.send(1458,20074)
        self.assertIn(1025,[mid(p) for p in packets(result)])
        self.assertEqual(self.p.snapshot(),before)

    def test_rollback_does_not_leave_an_unsent_cinematic_pending(self):
        self.g.dialog_open=True;self.g.dialog_actions=['fixture old choice'];self.g.dialog_npc=123
        try:
            with self.assertRaises(StorageBusy):
                with self.g.operation(write=True,memory_only=True):
                    self.g._serialize_after_commit=True
                    play(self.g,20065)
                    raise StorageBusy('fixture commit failed')
        finally:self.g._serialize_after_commit=False
        self.assertIsNone(getattr(self.g,'_cinematic_pending',None))
        self.assertTrue(self.g.dialog_open)
        self.assertEqual(self.g.dialog_actions,['fixture old choice'])

    def test_restore_recreates_world_npcs_after_the_client_cinematic_scene(self):
        character=self.g.character
        nearby=world().nearby(character['map_id'],character['x'],character['y'])
        self.assertTrue(nearby)
        self.g._world_visible=set(nearby)
        self.g._world_bucket=(character['map_id'],character['x']//16,character['y']//16)
        self.start('天蓬问情')
        result=packets(self.send(1458,20065))
        restored={p for p in result if mid(p)==1589}
        expected={world().npc_packet(character['map_id'],nid) for nid in nearby}
        self.assertTrue(restored.intersection(expected))

    def test_completion_restores_shown_guardian_but_keeps_hidden_guardian_hidden(self):
        sid=self.g.summons.claim_starter();self.g.summons.status(sid,3)
        self.g.sync_summon_scene();before=self.g.summons.snapshot()
        self.start('天蓬问情')
        result=packets(self.send(1458,20065))
        follow=packet(1605,['number','number','number'],[sid,self.p.cid,96])
        self.assertIn(follow,result)
        self.assertEqual(self.g._shown_summon_id,sid)
        self.assertEqual(self.g.summons.snapshot(),before)
        self.g.summons.status(sid,4);before=self.g.summons.snapshot()
        self.start('朱紫秘辛')
        result=packets(self.send(1458,20074))
        self.assertNotIn(follow,result)
        self.assertIsNone(self.g._shown_summon_id)
        self.assertEqual(self.g.summons.snapshot(),before)


class CampaignCinematicTests(unittest.TestCase):
    setUp=campaign_fixtures.CampaignTests.setUp
    send=campaign_fixtures.CampaignTests.send
    teleport=campaign_fixtures.CampaignTests.teleport
    choose=campaign_fixtures.CampaignTests.choose
    open_current=campaign_fixtures.CampaignTests.open_current

    def test_existing_campaign_movie_returns_to_current_npc_without_advancing(self):
        _,_,run=self.open_current()
        if run['state']=='available':self.choose('accept')
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET node=5,state='active' WHERE character_id=? AND chapter='newbie'",(self.p.cid,))
        chapter,node,run=self.open_current();self.assertEqual(node['movie'],1176)
        before=self.p.snapshot();self.choose('movie')
        result=self.send(1458,['number'],[1176])
        self.assertIn(1025,[mid(p) for p in packets(result)])
        self.assertTrue(self.g.dialog_open)
        self.assertTrue(any(isinstance(a,dict) and a.get('kind')=='campaign' for a in self.g.dialog_actions))
        self.assertEqual(self.p.snapshot(),before)

    def test_late_completion_does_not_reopen_an_advanced_campaign_stage(self):
        self.open_current()
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET node=5,state='active' WHERE character_id=? AND chapter='newbie'",(self.p.cid,))
        self.open_current();self.choose('movie')
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET node=6 WHERE character_id=? AND chapter='newbie'",(self.p.cid,))
        before=self.p.snapshot()
        result=packets(self.send(1458,['number'],[1176]))
        self.assertIn(1597,[mid(p) for p in result])
        self.assertNotIn(1025,[mid(p) for p in result])
        self.assertFalse(self.g.dialog_open)
        self.assertEqual(self.p.snapshot(),before)


if __name__=='__main__':unittest.main()
