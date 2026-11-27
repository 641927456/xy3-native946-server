"""Today's list reports owned progress without accepting or rewarding tasks."""
import unittest

from native946_codec import packet
from native946_login import MessageReader
from native946_task_recommendations import TaskRecommendations


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        from test_native946_tasks import TaskJournalTests
        self.fixture=TaskJournalTests(methodName='test_native_query_event_returns_only_server_owned_story_state')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.g=self.fixture.g;self.p=self.fixture.p

    def test_all_tabs_and_reward_queries_leave_player_state_unchanged(self):
        before=self.p.snapshot()
        for tab in range(1,5):
            response=self.fixture.login.receive(packet(1450,['byte'],[tab]))
            first=response[0] if isinstance(response,tuple) else response
            self.assertTrue(first.startswith(packet(1233,[],[])))
            reader=MessageReader(first[3:])
            self.assertEqual((reader.integer(),reader.integer()),(0,0))
            self.assertEqual(reader.take(1)[0],4)
            for threshold,energy in ((60,20),(120,30),(240,40),(400,60)):
                self.assertIn(f'活力×{energy}',reader.string().decode('gb18030'))
                self.assertEqual((reader.integer(),reader.take(2)),(threshold,b'\0\0'))
        self.fixture.login.receive(packet(1400,['number','byte','byte'],[1,1,1]))
        self.assertEqual(before,self.p.snapshot())

    def test_lists_only_implemented_flows_and_marks_unmet_requirements(self):
        rows={r['name']:r for r in TaskRecommendations(self.g).rows()}
        self.assertEqual(set(rows),{'捉鬼任务','鬼王任务','天庭任务','三界令','任务链','师门任务','普通藏宝图'})
        self.assertEqual(rows['捉鬼任务']['type'],'条件不足')
        self.assertEqual(rows['鬼王任务']['type'],'条件不足')
        self.assertEqual(rows['三界令']['type'],'条件不足')
        self.assertEqual(rows['任务链']['type'],'条件不足')
        self.assertEqual(rows['师门任务']['type'],'条件不足')
        self.assertEqual(rows['师门任务']['done'],'0/20')
        self.assertEqual(rows['任务链']['time'],'每周')
        self.assertEqual(rows['师门任务']['active'],'0/60')
        self.assertEqual(rows['天庭任务']['active'],'0/30')
        self.assertEqual(rows['任务链']['active'],'0')

    def test_counts_are_owned_and_reset_with_the_existing_daily_period(self):
        from native946_npc_quests import day_key
        other=self.fixture.characters.create('story_owner','别的角色',701)
        with self.p.transaction() as db:
            for cid,count in [(self.p.cid,3),(other['id'],19)]:
                db.execute('INSERT INTO native_npc_activity_progress(character_id,kind,day,accepted,completed) '
                           "VALUES(?,'ghost',?,?,?)",(cid,day_key(),count,count))
        rows={r['name']:r for r in TaskRecommendations(self.g).rows()}
        self.assertEqual(rows['捉鬼任务']['done'],'3/200')
        with self.p.transaction() as db:
            db.execute("UPDATE native_npc_activity_progress SET day='2000-01-01' WHERE character_id=?",(self.p.cid,))
        rows={r['name']:r for r in TaskRecommendations(self.g).rows()}
        self.assertEqual(rows['捉鬼任务']['done'],'0/200')

    def test_malformed_tab_and_reward_messages_are_rejected(self):
        for payload in [packet(1450,['byte'],[0]),packet(1450,['byte'],[5]),
                        packet(1450,['byte','byte'],[1,1]),packet(1400,['number'],[1]),
                        packet(1400,['number','byte','byte'],[5,1,1]),
                        packet(1400,['number','byte','byte'],[1,0,1])]:
            self.assertTrue(self.fixture.login.receive(payload).startswith(packet(98,[],[])))

    def test_sidebar_query_uses_current_native_pair_record(self):
        self.assertEqual(self.fixture.login.receive(packet(4310,[],[])),packet(4309,[('array','pair_string')],[[]]))


if __name__=='__main__':unittest.main()
