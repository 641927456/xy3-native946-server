"""Task ownership, trace persistence and original-story gap boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
import json, unittest

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import number, packet
from native946_game_login import NativeGameLogin, message_id
from native946_login import MessageReader
from native946_progression import Progression
from native946_sessions import LEASES
from native946_tasks import DATA, STORIES, TaskJournal, eligibility, grade_number
from native946_world import world
from native946_map import load_map


class StoryCatalogTests(unittest.TestCase):
    def test_original_story_metadata_is_preserved(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent / 'analysis/town-portals-20261125'))
        from client_archive import Archive
        from literal_schema import replay
        from build_world import plain
        original = plain(replay(Archive(Path(__file__).parent / 'client-946/resource/configs.wdf').read('info/info_task.lua'))[0])
        self.assertEqual(len(STORIES), 170)
        for key, row in original.items():
            for field, value in row.items():
                self.assertEqual(STORIES[int(key)][field], value, (key, field))
        self.assertTrue(all(r['implementation'] == 'missing_original_stage_script' for r in STORIES.values()))

    def test_native_grade_hierarchy_and_exact_role_restriction(self):
        self.assertEqual(grade_number(30, 1, 1, 1), 111030)
        row = STORIES[111600]
        s = dict(role=702, level=29, rebirth=0, ascension=0, sanctification=0)
        self.assertFalse(eligibility(row, s)[0])
        s['level'] = 30
        self.assertTrue(eligibility(row, s)[0])
        restricted = dict(row, fit_role='702')
        s['role'] = 70
        self.assertFalse(eligibility(restricted, s)[0])
        s['role'] = 702
        self.assertTrue(eligibility(restricted, s)[0])

    def test_predecessor_is_never_inferred_from_level(self):
        s = dict(role=702, level=100, rebirth=1, ascension=0, sanctification=0)
        row = STORIES[110507]
        self.assertIn('重见天日', eligibility(row, s)[1])
        self.assertTrue(eligibility(row, s, {'重见天日'})[0])


class TaskJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'tasks.sqlite3'
        LocalAccounts(self.db).register(dict(username='story_owner', password='fixture-password'))
        self.characters = Characters(self.db)
        row = self.characters.create('story_owner', '剧情测试', 702)
        self.login = NativeGameLogin(19260, None, number, None, self.characters)
        self.login.username = 'story_owner'
        self.addCleanup(lambda: LEASES.release_port(self.db, 19260))
        self.login.receive(message_id(915) + number(row['id']))
        self.g = self.login.gameplay; self.p = self.g.progression
        self.g.practice_trainer = True
        self.g.npc_position = self.g.practice_npc_position()
        self.g.ensure_progression()

    def send(self, mid, *values):
        return self.login.receive(packet(mid, ['number'] * len(values), values))

    def trace_ids(self):
        r = MessageReader(self.p.task_packets()[0][3:]); count = r.take(1)[0]
        # Zero and a single row are sufficient for the visibility assertions.
        return count

    def daily(self, cid=None):
        cid = self.p.cid if cid is None else cid
        with self.p.transaction() as db:
            qid = db.execute('INSERT INTO native_npc_quests(token,kind,branch,leader_id,created,expires,scene,x,y,npc,name,shape,dynamic) '
                "VALUES(?,'ghost',0,?,?,?,?,?,?,?,?,?,1)",
                ('fixture:' + str(cid), cid, int(time()), int(time()) + 1200, 2001, 302, 210, 950001, '测试游魂', 311)).lastrowid
            db.execute("INSERT INTO native_npc_quest_members VALUES(?,?,1,'active')", (qid, cid))
        return 930000 + qid

    def test_training_trace_can_hide_show_and_restore_after_reconstruction(self):
        self.send(1492, 910001)
        self.assertEqual(self.trace_ids(), 1)
        self.send(1981, 910001)
        self.assertEqual(self.trace_ids(), 0)
        restored = Progression(self.db, 'story_owner', self.p.cid)
        self.assertEqual(restored.snapshot()['task_preferences'], {910001: 0})
        self.send(1990, 910001)
        self.assertEqual(self.trace_ids(), 1)
        self.assertEqual(self.p.snapshot()['quest']['state'], 'active')

    def test_daily_detail_and_trace_use_owned_target(self):
        tid = self.daily()
        reply = self.send(1986, tid)
        self.assertTrue(reply.startswith(message_id(1971)))
        r = MessageReader(reply[3:]); self.assertEqual(r.integer(), tid)
        text = r.string().decode('gb18030'); r.finish()
        self.assertIn('测试游魂', text); self.assertIn('NpcId', text)
        self.send(1981, tid); self.assertEqual(self.trace_ids(), 0)
        self.send(1990, tid); self.assertEqual(self.trace_ids(), 1)

    def test_hide_all_keeps_tasks_and_expired_task_cannot_be_retraced(self):
        self.send(1492, 910001); tid = self.daily()
        self.assertEqual(self.trace_ids(), 2)
        self.send(1982); self.assertEqual(self.trace_ids(), 0)
        self.assertEqual(len(self.p.snapshot()['npc_quests']), 1)
        with self.p.transaction() as db:
            db.execute('UPDATE native_npc_quests SET expires=0')
        reply = self.send(1990, tid)
        self.assertTrue(reply.startswith(message_id(98)))
        self.assertEqual(self.p.snapshot()['task_preferences'][tid], 0)

    def test_other_character_task_id_cannot_be_queried_or_tracked(self):
        other = self.characters.create('story_owner', '另一角色', 701)
        Progression(self.db, 'story_owner', other['id'])
        tid = self.daily(other['id'])
        for mid in (1986, 1990, 1981):
            self.assertTrue(self.send(mid, tid).startswith(message_id(98)))
        self.assertEqual(self.p.snapshot()['task_preferences'], {})

    def test_trailing_fields_do_not_change_trace_preferences(self):
        self.send(1492, 910001)
        reply = self.login.receive(packet(1981, ['number', 'number'], [910001, 7]))
        self.assertTrue(reply.startswith(message_id(98)))
        self.assertEqual(self.p.snapshot()['task_preferences'], {})
        self.assertEqual(self.trace_ids(), 1)

    def test_remote_story_entry_navigates_while_reset_and_unearned_rewards_stay_blocked(self):
        before = self.p.snapshot()
        self.assertTrue(self.send(1492,111600).startswith(message_id(2966)))
        for mid in (1493, 2805, 4347, 4348, 5438):
            reply = self.send(mid, 111600)
            self.assertTrue(reply.startswith(message_id(98)), mid)
        after = self.p.snapshot()
        self.assertEqual((before['gold'], before['experience'], before['quest'], before['items']),
                         (after['gold'], after['experience'], after['quest'], after['items']))
        self.assertEqual(after['task_preferences'], {})

    def test_story_details_distinguish_reconstructed_mainline_from_other_gaps(self):
        reply = self.send(1986, 111600)
        r = MessageReader(reply[3:]); self.assertEqual(r.integer(), 111600)
        text = r.string().decode('gb18030'); r.finish()
        self.assertIn(STORIES[111600]['desc_str'], text)
        self.assertIn('领取条件', text)
        self.assertEqual(self.send(4344), packet(4339, [('array', 'number'), ('array', 'string'), ('array', 'number')], [[], [], []]))
        self.assertTrue(self.send(1986, 192500).startswith(message_id(1971)))

    def test_native_query_event_returns_only_server_owned_story_state(self):
        reply = self.login.receive(packet(1495, ['number', 'string'], [2, 'forged completion']))
        self.assertEqual(reply, packet(1326, ['number', 'string'], [2, '{{},{},{}}']))
        self.assertEqual(self.p.snapshot()['quest']['state'], 'available')

    def test_catalog_is_appended_to_original_npc_business_and_distance_is_rechecked(self):
        target = STORIES[151401]['locations'][0]
        c = self.g.character; sid = target['scene']; grid = load_map(sid)
        x, y = grid.nearest_walkable(target['x'], target['y'])
        rev = self.characters.change_scene(c['owner'], c['id'], c['map_id'], sid, x, y, c.get('position_revision', 0))
        c.update(map_id=sid, x=x, y=y, position_revision=rev); self.g.grid = grid
        reply = self.g.world_npc_look(target['npc'])
        self.assertTrue(reply.startswith(message_id(1025)))
        choice = next(i for i,a in enumerate(self.g.dialog_actions,1) if a.get('kind')=='story')
        self.assertTrue(any(a.get('kind')=='campaign' for a in self.g.dialog_actions))
        c['x'] = 0; c['y'] = 0
        rejected = self.login.receive(packet(1032, ['byte', 'number'], [choice, target['npc']]))
        self.assertTrue(rejected.startswith(message_id(98)))

    def test_every_original_story_is_reachable_from_paginated_catalog(self):
        found=set();journal=TaskJournal(self.g)
        for section in ('1','2','other'):
            page=0
            while True:
                journal.catalog(900001,section,page)
                found.update(a['task'] for a in self.g.dialog_actions if a['op']=='detail')
                next_page=next((a['page'] for a in self.g.dialog_actions if a['op']=='catalog' and
                               a.get('section')==section and a.get('page',-1)>page),None)
                if next_page is None:break
                page=next_page
        self.assertEqual(found,set(STORIES))

    def test_native_story_entry_cleans_orphan_and_opens_original_container(self):
        c=self.g.character;c['x'],c['y']=self.g.npc_position
        reply=TaskJournal(self.g).execute(900001,dict(kind='story',op='native'))
        self.assertEqual(reply,(
            packet(1183,['string'],['dlg_npc_chat']),
            packet(1183,['string'],['dlg_scnr_review_main']),
            packet(1183,['string'],['dlg_scnr_review_story']),
            packet(3993,['string'],['dlg_scnr_review_main'])))

    def test_diagnostic_child_alias_opens_and_closes_whole_story_window(self):
        from native946_control import execute
        opened=execute(self.login,dict(action='panel',name='dlg_scnr_review_story'))
        self.assertEqual(opened[-1],packet(3993,['string'],['dlg_scnr_review_main']))
        self.assertEqual(execute(self.login,dict(action='panel',name='dlg_scnr_review_main')),opened)
        closed=execute(self.login,dict(action='panel',name='dlg_scnr_review_story',show=False))
        self.assertEqual(closed,opened[:-1])


if __name__ == '__main__':
    unittest.main()
