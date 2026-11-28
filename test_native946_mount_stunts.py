"""Protocol and persistence regressions for guardian-capped mount stunts."""

import unittest

from native946_codec import SCHEMAS, packet
from native946_game_login import message_id
from native946_login import MessageReader
from native946_mount_stunts import MEDICINE, MOUNT, DIRECTION, store_training
from native946_innate_training import stage_flags
from native946_neidan import threshold
import test_native946_skillbooks as book_tests


def take_field(reader, kind):
    if isinstance(kind, (tuple, list)):
        return [take_field(reader, kind[1]) for _ in range(reader.take(1)[0])]
    if kind == 'number':
        return reader.integer()
    if kind == 'string':
        return reader.string().decode('gb18030')
    if kind == 'byte':
        return reader.take(1)[0]
    return {name: take_field(reader, field) for name, field in SCHEMAS[kind]}


class MountStuntTests(unittest.TestCase):
    setUp = book_tests.BasicBookTests.setUp
    send = book_tests.BasicBookTests.send
    item = book_tests.BasicBookTests.item

    def own_mount(self):
        with self.p.transaction() as db:
            db.execute('INSERT INTO cosmetic_features VALUES(?,?,0,0)', (self.p.cid, f'mount:{MOUNT}'))

    def bind(self, sid, enabled=1):
        return self.send(2308, MOUNT, DIRECTION, enabled, sid)

    def choose(self, sid, skills):
        data = packet(4753, ['number', ('array', 'number')], [sid, skills])
        return self.g.receive(4753, MessageReader(data[3:]))

    def wire(self, sid):
        data = self.send(761, sid)
        reader = MessageReader(data[len(message_id(722)):])
        self.assertEqual(reader.integer(), sid)
        for kind in ('stunt_info', 'stunt_pos_empty', 'stunt_info'):
            take_field(reader, ('array', kind))
        rows = take_field(reader, ('array', 'newzq_stunt_info'))
        direction = reader.integer()
        reader.finish()
        return rows, direction

    def change_pet(self, stage, level, experience, sid=None):
        flags = stage_flags(stage)
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=?,experience=?,rebirth=?,ascension=?,sanctification=? WHERE id=?',
                       (level, experience, *flags.values(), sid or self.sid))

    def train(self, stage, level, experience=0, skill=4065):
        with self.p.transaction() as db:
            store_training(db, self.p.cid, skill, dict(level=level, experience=experience, **stage_flags(stage)))

    def test_requires_own_mount_and_guardian(self):
        self.assertEqual(self.wire(self.sid), ([], 0))
        self.assertEqual(self.send(762, self.sid), packet(712, ['number', 'byte'], [self.sid, 0]))
        self.assertEqual(self.bind(self.sid)[0], 98)
        self.assertEqual(self.send(768, self.sid, 4065)[0], 98)
        self.own_mount()
        self.assertEqual(self.send(2293, MOUNT)[:3], message_id(2260))
        self.assertEqual(self.send(2294, MOUNT, DIRECTION, -1)[:3], message_id(2261))
        self.assertEqual(self.send(2316, MOUNT, DIRECTION)[:3], message_id(2273))
        self.assertEqual(self.send(2317)[:3], message_id(2274))
        self.assertEqual(self.bind(-123)[0], 98)
        self.assertIn(packet(712, ['number', 'byte'], [self.sid, 1]), self.bind(self.sid))
        self.assertEqual(self.send(768, self.sid, 307)[0], 98)
        self.assertEqual(self.wire(self.sid)[1], DIRECTION)
        self.bind(self.sid, 0)
        self.assertEqual(self.wire(self.sid), ([], 0))

    def test_carry_selection_independent_and_shared_experience_is_clamped(self):
        self.own_mount()
        second = self.s.acquire(self.s.get(self.sid)['type'], 'mount-stunt-test-second')
        self.bind(self.sid)
        self.bind(second)
        self.choose(self.sid, [4065, 4066])
        self.choose(second, [4067, 4068])
        self.assertEqual(self.choose(second, [4065, 4065])[0], 98)
        self.change_pet(1, 40, 50_000)
        self.change_pet(2, 105, 50_000, second)
        self.train(2, 95, 250)
        first_rows = self.wire(self.sid)[0]
        second_rows = self.wire(second)[0]
        self.assertEqual([r['stunt_id'] for r in first_rows if r['active']], [4065, 4066])
        self.assertEqual([r['stunt_id'] for r in second_rows if r['active']], [4067, 4068])
        self.assertEqual((first_rows[0]['rei'], first_rows[0]['grade'], first_rows[0]['stuntexp_next']), (1, 40, -1))
        self.assertEqual((second_rows[0]['rei'], second_rows[0]['grade'], second_rows[0]['stuntexp']), (2, 95, 250))

    def test_original_rebirth_command_follows_all_guardian_stages(self):
        self.own_mount()
        self.bind(self.sid)
        for stage, owner_level, prior_level, trained_level, fee in (
            (1, 40, 100, 0, 2500), (2, 89, 120, 0, 2500),
            (3, 150, 140, 140, 5000), (4, 183, 180, 180, 10000),
        ):
            with self.subTest(stage=stage):
                self.change_pet(stage, owner_level, 50_000)
                self.train(stage - 1, prior_level - 1)
                self.assertEqual(self.send(768, self.sid, 4065)[0], 98)
                self.train(stage - 1, prior_level)
                promoted = self.send(768, self.sid, 4065)
                self.assertTrue(any(payload.startswith(message_id(722)) for payload in promoted))
                row = self.wire(self.sid)[0][0]
                self.assertEqual((row['grade'], row['rei'], row['fly']), (trained_level, min(stage, 2), int(stage >= 3)))
                self.assertIn(f'{("0转", "1转", "2转", "飞升", "封圣")[stage]}{owner_level}级', row['desc'])
                self.assertEqual(self.send(768, self.sid, 4065)[0], 98)
                self.assertEqual(self.s.get(self.sid)['experience'], 50_000 - fee)
                with self.p.transaction(write=False) as db:
                    saved = db.execute('SELECT stage,level FROM native_mount_stunts WHERE skill=4065').fetchone()
                    self.assertEqual(tuple(saved), (stage, trained_level))

    def test_retry_after_ascension_cannot_charge_sanctification(self):
        self.own_mount()
        self.bind(self.sid)
        self.change_pet(4, 183, 50_000)
        self.train(2, 140)
        self.send(768, self.sid, 4065)
        self.assertEqual(self.send(768, self.sid, 4065)[0], 98)
        self.assertEqual(self.s.get(self.sid)['experience'], 45_000)
        self.assertIn('飞升180级', self.wire(self.sid)[0][0]['desc'])

    def test_medicine_respects_current_stage_cap_and_daily_limit(self):
        self.own_mount()
        self.bind(self.sid)
        self.choose(self.sid, [4065, 4066])
        self.change_pet(1, 40, 10_000)
        self.train(1, 39, threshold(39, 1) - 1)
        self.train(1, 40, skill=4066)
        for other in (4067, 4068, 4069):
            self.train(2, 45, skill=other)
        item = self.item(MEDICINE, 2)
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET fight=1 WHERE id=?', (self.sid,))
        reply = self.send(807, self.p.cid, item)
        self.assertTrue(any(payload.startswith(message_id(722)) for payload in reply))
        self.assertEqual(self.wire(self.sid)[0][0]['grade'], 40)
        self.assertEqual(self.send(807, self.p.cid, item)[0], 98)
        with self.p.transaction() as db:
            self.assertEqual(db.execute('SELECT amount FROM inventory WHERE id=?', (item,)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT used FROM native_mount_stunt_medicine').fetchone()[0], 1)
            db.execute('UPDATE native_mount_stunt_medicine SET used=25')
        self.change_pet(1, 41, 10_000)
        self.assertEqual(self.send(807, self.p.cid, item)[0], 98)
        self.assertEqual(self.wire(self.sid)[0][0]['grade'], 40)
