from datetime import date, datetime, time, timedelta
import unittest

from native946_activities import CALENDAR
from native946_map import load_map
from native946_timed_npcs import GATE_SCENES, ILLUSION, SKY, TIME_GATE, active_entrance, time_gate_for
from native946_world import EVENT_TIMEZONE


def at(day, hour, minute, second=0):
    return datetime(2026, 11, day, hour, minute, second, tzinfo=EVENT_TIMEZONE)


class TimedActivityNpcTests(unittest.TestCase):
    def test_weekly_and_fortnightly_days_match_the_original_normal_calendar(self):
        for day, items in CALENDAR['days'].items():
            expected = {row['name'].strip() for row in items}
            current = date.fromisoformat(day)
            with self.subTest(day=day):
                for entrance, name, hour in ((ILLUSION, '九重幻境', 20), (SKY, '云阶秘藏', 14)):
                    self.assertEqual(active_entrance(entrance.scene, datetime.combine(
                        current, time(hour), EVENT_TIMEZONE)) == entrance, name in expected)

    def test_illusion_stays_after_the_challenge_ends_and_retires_at_2355(self):
        self.assertIsNone(active_entrance(ILLUSION.scene, at(26, 19, 59, 59)))
        self.assertEqual(active_entrance(ILLUSION.scene, at(26, 20, 0)), ILLUSION)
        self.assertEqual(active_entrance(ILLUSION.scene, at(26, 22, 0)), ILLUSION)
        self.assertEqual(active_entrance(ILLUSION.scene, at(26, 23, 54, 59)), ILLUSION)
        self.assertIsNone(active_entrance(ILLUSION.scene, at(26, 23, 55)))
        self.assertIsNone(active_entrance(ILLUSION.scene, at(27, 20, 0)))

    def test_sky_escort_spans_preparation_and_exploration_on_the_active_sunday(self):
        self.assertIsNone(active_entrance(SKY.scene, at(29, 13, 59, 59)))
        self.assertEqual(active_entrance(SKY.scene, at(29, 14, 0)), SKY)
        self.assertEqual(active_entrance(SKY.scene, at(29, 14, 30)), SKY)
        self.assertEqual(active_entrance(SKY.scene, at(29, 16, 29, 59)), SKY)
        self.assertIsNone(active_entrance(SKY.scene, at(29, 16, 30)))
        self.assertIsNone(active_entrance(SKY.scene, at(22, 14, 0)))

    def test_gate_slot_stays_in_a_shared_reachable_location_until_its_cleanup(self):
        first = at(27, 15, 45)
        scenes = set()
        for day in range(14):
            for base in (first, first + timedelta(hours=6)):
                start = base + timedelta(days=day)
                gate = time_gate_for(start)
                self.assertEqual(gate.nid, TIME_GATE.nid)
                self.assertTrue(load_map(gate.scene).walkable(*gate.position))
                scenes.add(gate.scene)
                self.assertIsNone(active_entrance(gate.scene, start - timedelta(seconds=1)))
                self.assertEqual([active_entrance(scene, start) for scene in GATE_SCENES
                                  if active_entrance(scene, start) is not None], [gate])
                self.assertEqual(active_entrance(gate.scene, start + timedelta(minutes=15)), gate)
                self.assertEqual(active_entrance(gate.scene, start + timedelta(seconds=6299)), gate)
                self.assertIsNone(active_entrance(gate.scene, start + timedelta(seconds=6300)))
                time_gate_for.cache_clear()
                self.assertEqual(time_gate_for(start), gate)
        self.assertEqual(scenes, set(GATE_SCENES))
