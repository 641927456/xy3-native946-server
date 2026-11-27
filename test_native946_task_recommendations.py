"""Today's list reports owned progress without accepting or rewarding tasks."""
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from native946_codec import SCHEMAS, packet
from native946_liveness import DAY_ZONE
from native946_login import MessageReader
from native946_task_recommendations import TaskRecommendations


def read_panel(payload):
    assert payload.startswith(packet(1233, [], []))
    reader = MessageReader(payload[3:])
    points, maximum = reader.integer(), reader.integer()

    def read_array(name):
        readers = {'string': lambda: reader.string().decode('gb18030'),
                   'number': reader.integer, 'byte': lambda: reader.take(1)[0]}
        return [{field: readers[kind]() for field, kind in SCHEMAS[name]}
                for _ in range(reader.take(1)[0])]

    rewards = read_array('liveness_reward')
    rows = read_array('activity_list')
    assert (reader.string(), reader.string()) == (b'{}', b'{}')
    reader.finish()
    return points, maximum, rewards, {row['name']: row for row in rows}


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        from test_native946_tasks import TaskJournalTests
        self.fixture=TaskJournalTests(methodName='test_native_query_event_returns_only_server_owned_story_state')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.g=self.fixture.g;self.p=self.fixture.p

    def test_all_tabs_and_reward_queries_leave_player_state_unchanged(self):
        before=self.p.snapshot()
        expected = {1: '师门任务', 2: '星宿挑战', 3: '世界 BOSS', 4: '朱紫秘辛'}
        for tab in range(1,5):
            response=self.fixture.login.receive(packet(1450,['byte'],[tab]))
            points, maximum, rewards, rows = read_panel(response)
            self.assertEqual((points, maximum), (0, 0))
            self.assertIn(expected[tab], rows)
            self.assertEqual(len(rewards), 4)
            for reward, (threshold, energy) in zip(rewards, ((60, 20), (120, 30), (240, 40), (400, 60))):
                self.assertIn(f'活力×{energy}', reward['reward_str'])
                self.assertEqual((reward['need_liveness'], reward['has_get_reward'], reward['can_get_reward']),
                                 (threshold, 0, 0))
        self.fixture.login.receive(packet(1400,['number','byte','byte'],[1,1,1]))
        self.assertEqual(before,self.p.snapshot())

    def test_challenge_and_timed_rows_use_the_settlement_day_and_character(self):
        day = datetime(2026, 11, 27, tzinfo=DAY_ZONE)
        now = day.replace(hour=20, minute=5)
        other = self.fixture.characters.create('story_owner', '别的角色', 701)['id']
        stamp = int(now.timestamp())
        with self.p.transaction() as db:
            db.executemany('INSERT INTO battle_results(battle_id,character_id,victory,settled_at,owner,experience,gold) '
                           "VALUES(?,?,?,?,'story_owner',0,0)", [
                               ('star:2008:111:1', self.p.cid, 1, stamp),
                               ('afei-event:14:2006:111:1', self.p.cid, 1, stamp),
                               ('afei-event:6:2001:111:1', self.p.cid, 1, stamp),
                               ('afei-event:6:2027:111:1', self.p.cid, 0, stamp),
                               ('afei-event:6:2010:111:2', other, 1, stamp),
                               ('afei-event:0:2013:111:1', self.p.cid, 1, stamp - 86400),
                           ])
        rec = TaskRecommendations(self.g)
        with patch('native946_task_recommendations.datetime') as clock:
            clock.now.return_value = now
            _, _, _, challenge = read_panel(rec.panel(2, day))
            points, _, _, timed = read_panel(rec.panel(3, day))
            self.assertEqual(challenge['星宿挑战']['done'], '2')
            self.assertEqual(challenge['比武大会']['type'], '待接通')
            self.assertEqual(points, 0)
            self.assertEqual(timed['星宿下凡']['done'], '1')
            self.assertEqual(timed['星宿下凡']['type'], '刷新时段')
            self.assertEqual(timed['世界 BOSS']['done'], '0')
            self.assertEqual(timed['世界 BOSS']['type'], '未刷新')
            self.assertEqual(timed['决战平顶山']['type'], '待接通')
            self.assertNotIn('灵山决战', timed)
            tomorrow = day + timedelta(days=1)
            clock.now.return_value = tomorrow
            self.assertEqual(read_panel(rec.panel(2, tomorrow))[3]['星宿挑战']['done'], '0')

    def test_dungeon_rows_report_existing_runs_and_keep_unopened_routes_pending(self):
        from time import time
        from native946_liveness import day_start

        day = day_start()
        with self.p.transaction() as db:
            for key, state, stage in ((25, 'completed', 3), (26, 'entered', 1)):
                instance = f'recommendations:{key}'
                db.execute('INSERT INTO native_dungeon_runs VALUES(?,?,?,?,?,?)',
                           (instance, self.p.cid, key, day.date().isoformat(), state, int(time()) + 30))
                db.execute('INSERT INTO native_dungeon_route_progress VALUES(?,?,?)', (instance, self.p.cid, stage))
        _, _, _, rows = read_panel(TaskRecommendations(self.g).panel(4, day))
        self.assertEqual((rows['朱紫秘辛']['type'], rows['朱紫秘辛']['done']), ('已结算', '1/1'))
        self.assertEqual((rows['天蓬问情']['type'], rows['天蓬问情']['done']), ('进行中', '0/1'))
        self.assertIn('已通过1/3场战斗', rows['天蓬问情']['desc'])
        self.assertEqual(rows['白龙剑冢']['type'], '待接通')
        self.assertNotIn('三调芭蕉扇', rows)

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
