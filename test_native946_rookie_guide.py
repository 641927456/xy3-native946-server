"""Native guide queries cannot manufacture quest progress or rewards."""
from pathlib import Path
from types import SimpleNamespace
import json
import unittest

from native946_codec import packet
from native946_login import MessageReader
from native946_rookie_guide import DATA, GROUPS, ACHIEVEMENTS, attained, overview, section, RookieGuide


class RookieGuideTests(unittest.TestCase):
    def snapshot(self, **changes):
        return dict(level=1, rebirth=0, ascension=0, sanctification=0, **changes)

    def test_catalog_matches_current_client_and_contains_each_positioned_record_once(self):
        import sys
        sys.path[:0] = [str(Path(__file__).parent/'analysis/town-portals-20261125'),
                       str(Path(__file__).parent/'analysis/story-quests-20261126')]
        from client_archive import Archive
        from literal_schema import replay
        from build_catalog import plain
        original = plain(replay(Archive(Path(__file__).parent/'client-946/resource/configs.wdf').read('info/info_achieve.lua'))[0])
        self.assertEqual(DATA['achievements'], original)
        ids = [aid for group in GROUPS for aid in group['ids']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {int(k) for k,v in original.items() if v['pos']})

    def test_level_is_exact_and_never_completes_a_story_or_item_action(self):
        s = self.snapshot();s['level'] = 29
        self.assertFalse(attained(ACHIEVEMENTS[1110301], s))
        s['level'] = 30
        self.assertTrue(attained(ACHIEVEMENTS[1110301], s))
        s['rebirth'] = 1;s['level'] = 1
        self.assertTrue(attained(ACHIEVEMENTS[1110301], s))
        self.assertFalse(attained(ACHIEVEMENTS[1110304], s))
        s['level'] = 70
        self.assertTrue(attained(ACHIEVEMENTS[1110304], s))
        for aid in [1110101,1110201,1120101]:
            self.assertFalse(attained(ACHIEVEMENTS[aid], s))

    def test_guide_initial_query_populates_both_overview_and_first_section(self):
        game = SimpleNamespace(progression=SimpleNamespace(snapshot=self.snapshot))
        handled, result = RookieGuide(game).receive(1473, MessageReader(b''))
        self.assertTrue(handled)
        self.assertEqual(result, (overview(self.snapshot()),section(1,self.snapshot())))
        r = MessageReader(result[0][3:])
        total, earned, flag = r.integer(),r.integer(),r.take(1)[0]
        self.assertGreater(total,0);self.assertEqual((earned,flag),(0,0))

    def test_actual_login_dispatch_preserves_progress_and_rejects_bad_category(self):
        from test_native946_tasks import TaskJournalTests
        fixture=TaskJournalTests(methodName='test_native_query_event_returns_only_server_owned_story_state')
        fixture.setUp()
        try:
            before=fixture.p.snapshot()
            self.assertIsInstance(fixture.send(1473),tuple)
            for index in range(1,len(GROUPS)+1):
                self.assertTrue(fixture.send(1474,index).startswith(packet(1298,[],[])))
            self.assertTrue(fixture.send(1474,0).startswith(packet(98,[],[])))
            fixture.send(1475)
            self.assertEqual(before,fixture.p.snapshot())
        finally:
            fixture.doCleanups()

    def test_invalid_and_trailing_requests_are_rejected_before_using_snapshot(self):
        game = SimpleNamespace(progression=SimpleNamespace(snapshot=self.snapshot))
        for mid, values in [(1473,[1]),(1474,[1,1]),(1475,[1]),(1474,[0]),(1474,[-1]),(1474,[len(GROUPS)+1])]:
            with self.assertRaises(ValueError):
                RookieGuide(game).receive(mid,MessageReader(packet(mid,['number']*len(values),values)[3:]))

    def test_reward_request_is_read_only_and_keeps_button_disabled(self):
        calls = []
        game = SimpleNamespace(progression=SimpleNamespace(snapshot=lambda:(calls.append('read') or self.snapshot())))
        handled, result = RookieGuide(game).receive(1475,MessageReader(b''))
        self.assertTrue(handled);self.assertEqual(calls,['read'])
        self.assertEqual(result[-1],overview(self.snapshot()))

    def test_all_sections_use_no_more_than_seven_native_icons_per_subpage(self):
        import sys
        sys.path.insert(0,str(Path(__file__).parent/'.test-deps'))
        from lupa.lua51 import LuaRuntime
        lua = LuaRuntime(encoding='gb18030')
        decode = lua.eval('function(s) return assert(loadstring("return "..s))() end')
        for index in range(1,len(GROUPS)+1):
            r=MessageReader(section(index,self.snapshot())[3:]);self.assertEqual(r.integer(),index)
            pages=decode(r.string().decode('gb18030'));r.finish()
            self.assertGreater(len(pages),0);self.assertLessEqual(len(pages),7)
            for _,page in pages.items():
                self.assertTrue(page['name']);self.assertTrue(page['desc'])
                self.assertLessEqual(len(list(page['achieve_list'].items())),7)


if __name__ == '__main__':
    unittest.main()
