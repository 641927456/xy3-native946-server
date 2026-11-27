import unittest

from native946_codec import packet
from native946_innate_training import schema,visible_innate
from test_native946_skillbooks import BasicBookTests


class InnateTrainingTests(unittest.TestCase):
    setUp = BasicBookTests.setUp
    send = BasicBookTests.send
    item = BasicBookTests.item

    def test_convert_experience_and_forget_then_relearn(self):
        with self.p.transaction() as db:
            schema(db)
            db.execute('UPDATE summons SET experience=500 WHERE id=?', (self.sid,))
            db.execute('INSERT INTO native_innate_training(summon_id,skill,level) VALUES(?,307,9)', (self.sid,))
        self.assertEqual(self.s.get(self.sid)['book_fatal'], 0)
        self.send(754, self.sid, 307)
        trained = next(row for row in self.s.get(self.sid)['innate_skills'] if row['stunt_id'] == 307)
        self.assertEqual((trained['grade'], trained['stuntexp'], trained['stuntexp_next']), (10, 0, 100))
        self.assertEqual(self.s.get(self.sid)['book_fatal'], 1)
        with self.p.transaction(write=False) as db:
            self.assertEqual(tuple(db.execute('SELECT level,experience FROM native_innate_training WHERE summon_id=? AND skill=?',
                                        (self.sid, 307)).fetchone()), (10, 0))
            self.assertEqual(db.execute('SELECT experience FROM summons WHERE id=?', (self.sid,)).fetchone()[0], 400)
        self.send(756, self.sid, 307)
        with self.p.transaction(write=False) as db:
            self.assertFalse([entry for entry in visible_innate(db, self.s.get(self.sid)) if entry['stunt_id'] == 307])
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET experience=500 WHERE id=?', (self.sid,))
        before = self.s.get(self.sid)
        self.send(754, self.sid, 307)
        self.assertEqual(self.s.get(self.sid), before)
        self.send(763, self.sid, 307)
        with self.p.transaction(write=False) as db:
            self.assertTrue([entry for entry in visible_innate(db, self.s.get(self.sid)) if entry['stunt_id'] == 307])

    def test_guardian_stage_upgrades(self):
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=140,experience=50000 WHERE id=?', (self.sid,))
        self.send(757, self.sid)
        with self.p.transaction() as db:
            self.assertEqual(tuple(db.execute('SELECT sanctification,level,experience FROM summons WHERE id=?', (self.sid,)).fetchone()), (1, 1, 0))
            db.execute('UPDATE summons SET level=140,experience=50000 WHERE id=?', (self.sid,))
        self.send(758, self.sid)
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT ascension FROM summons WHERE id=?', (self.sid,)).fetchone()[0], 1)

    def test_taoxian_learns_stage_passive_and_packet_uses_the_same_saved_skill(self):
        from native946_skillbooks import SkillBooks

        sid = self.s.acquire(3389, 'taoxian:stage-passive')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=119,rebirth=2 WHERE id=?', (sid,))
        books = SkillBooks(self.s)
        self.send(763, sid, 4762)
        self.assertEqual([row['stunt_id'] for row in books.records(sid)], [4539])
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=120 WHERE id=?', (sid,))
        self.send(763, sid, 4762)
        learned = [(row['stunt_id'], row['position']) for row in books.records(sid)]
        self.assertEqual(learned, [(4539, 1), (4762, 5)])
        self.assertEqual([row['stunt_id'] for row in self.s.get(sid)['innate_skills']], [4539, 4762])
        self.send(756, sid, 4762)
        self.assertEqual([row['stunt_id'] for row in books.records(sid)], [4539])
        self.send(763, sid, 4762)
        self.assertEqual([(row['stunt_id'], row['position']) for row in books.records(sid)], learned)

    def test_cansheng_yijiu_is_learned_at_the_native_stage_and_listed_in_battle(self):
        from native946_guardian_magic import combat_skills
        from native946_skillbooks import SkillBooks

        for species, rebirth, level, position in ((3389, 0, 50, 2), (3246, 2, 120, 5)):
            with self.subTest(species=species):
                sid = self.s.acquire(species, f'cansheng:stage:{species}')
                with self.p.transaction() as db:
                    db.execute('UPDATE summons SET level=?,rebirth=? WHERE id=?', (level - 1, rebirth, sid))
                self.send(763, sid, 4046)
                self.assertNotIn(4046, [entry['stunt_id'] for entry in SkillBooks(self.s).records(sid)])
                with self.p.transaction() as db:
                    db.execute('UPDATE summons SET level=? WHERE id=?', (level, sid))
                self.send(763, sid, 4046)
                entry = next(entry for entry in SkillBooks(self.s).records(sid) if entry['stunt_id'] == 4046)
                self.assertEqual(entry['position'], position)
                row = next(row for row in combat_skills(self.s.get(sid)) if row['skill_id'] == 4046)
                self.assertEqual(row['cost'], '法力：8000')
                self.assertIn('复活最多3个', row['des'])
                self.assertIn('29.36%', row['des'])

    def test_ranking_calibrated_skills_use_the_native_learning_stages(self):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=100 WHERE id=?', (self.p.cid,))
            db.execute('UPDATE cultivation_state SET rebirth=1 WHERE character_id=?', (self.p.cid,))
        for species, skill, rebirth, level, field, bonus in (
                (3294, 4149, 1, 100, 'hpmax', 4504),
                (3302, 4146, 0, 100, 'mpmax', 4504),
                (3300, 4148, 0, 100, 'attack', 415),
                (3389, 4765, 1, 100, 'EquipElementResist', 1500),
                (2103, 308, 0, 100, 'cruel_basis_points', 3151)):
            with self.subTest(skill=skill):
                sid = self.s.acquire(species, f'ranking:{skill}')
                with self.p.transaction() as db:
                    db.execute('UPDATE summons SET level=?,rebirth=? WHERE id=?', (level, rebirth, sid))
                before = self.s.get(sid)
                self.send(763, sid, skill)
                with self.p.transaction() as db:
                    db.execute('UPDATE native_innate_training SET level=180,ascension=1 WHERE summon_id=? AND skill=?',
                               (sid, skill))
                pet = self.s.get(sid)
                self.assertEqual(sum(entry['stunt_id'] == skill for entry in pet['innate_skills']), 1)
                self.assertEqual(pet.get(field, 0) - before.get(field, 0), bonus)
                self.assertEqual(pet['score'] - before['score'], 40 + 180 * 13 // 2 + 100)

    def test_available_list_and_stage_upgrades(self):
        self.assertIsNotNone(self.send(759, self.sid))
        with self.p.transaction() as db:
            schema(db)
            db.execute('UPDATE summons SET level=50,experience=20000 WHERE id=?', (self.sid,))
            db.execute('INSERT INTO native_innate_training(summon_id,skill,level,experience) VALUES(?,307,20,0)', (self.sid,))
        self.send(768, self.sid, 307)
        with self.p.transaction() as db:
            self.assertEqual(tuple(db.execute('SELECT level,experience FROM native_innate_training WHERE summon_id=? AND skill=?',
                                        (self.sid, 307)).fetchone()), (1, 0))
        self.send(771, self.sid, 307)
        self.send(772, self.sid, 307)
        with self.p.transaction() as db:
            db.execute('UPDATE native_innate_training SET level=50 WHERE summon_id=? AND skill=?', (self.sid, 307))
        self.send(770, self.sid, 307)
        with self.p.transaction(write=False) as db:
            row = db.execute('SELECT rebirth,ascension,sanctification FROM native_innate_training WHERE summon_id=? AND skill=?',
                             (self.sid, 307)).fetchone()
            self.assertEqual(tuple(row), (1, 1, 1))

    def test_guardian_rebirth(self):
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=100,experience=10000 WHERE id=?', (self.sid,))
        self.send(769, self.sid)
        with self.p.transaction(write=False) as db:
            self.assertEqual(tuple(db.execute('SELECT rebirth,level,experience FROM summons WHERE id=?',
                                             (self.sid,)).fetchone()), (1, 1, 0))

    def test_unverified_transfer_releases_native_panel_without_spending(self):
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET experience=500 WHERE id=?', (self.sid,))
        item = self.item(4000, 2)
        before = self.s.get(self.sid)
        for direction in (0, 1):
            for request in (773, 774, 774):
                with self.subTest(direction=direction, request=request):
                    reply = self.send(request, self.sid, 307, item, direction)
                    self.assertIn(packet(724, ['number'] * 4, [307, item, direction, 0]), reply)
        self.assertEqual(self.s.get(self.sid), before)
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT amount FROM inventory WHERE id=?', (item,)).fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
