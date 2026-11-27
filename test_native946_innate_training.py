import unittest

from native946_codec import packet, text_notice
from native946_innate_training import schema,visible_innate
from native946_neidan import threshold
from native946_skillbooks import SkillBooks
from native946_summons import Summons
from test_native946_skillbooks import BasicBookTests, decode_record
from native946_login import MessageReader


class InnateTrainingTests(unittest.TestCase):
    setUp = BasicBookTests.setUp
    send = BasicBookTests.send
    item = BasicBookTests.item

    def test_convert_experience_and_forget_then_relearn(self):
        with self.p.transaction() as db:
            schema(db)
            db.execute('UPDATE summons SET experience=500 WHERE id=?', (self.sid,))
            db.execute('INSERT INTO native_innate_training(summon_id,skill,level,experience) VALUES(?,307,9,?)',
                       (self.sid, threshold(9, 0) - 100))
        self.assertEqual(self.s.get(self.sid)['book_fatal'], 0)
        self.send(754, self.sid, 307)
        trained = next(row for row in self.s.get(self.sid)['innate_skills'] if row['stunt_id'] == 307)
        self.assertEqual((trained['grade'], trained['stuntexp'], trained['stuntexp_next']), (10, 0, 779))
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

    def test_guardian_sanctification(self):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=180 WHERE id=?', (self.p.cid,))
        for before_level, experience in ((179, 20000), (180, 19999), (180, 20000)):
            with self.p.transaction() as db:
                db.execute('UPDATE summons SET level=?,rebirth=2,ascension=1,experience=? WHERE id=?',
                           (before_level, experience, self.sid))
            before = self.s.get(self.sid)
            reply = self.send(757, self.sid)
            if before_level < 180 or experience < 20000:
                self.assertEqual(self.s.get(self.sid), before)
        pet = Summons(self.p).get(self.sid)
        with self.p.transaction(write=False) as db:
            row = db.execute('SELECT sanctification,level,experience,cor,mag,strength,speed,points '
                             'FROM summons WHERE id=?', (self.sid,)).fetchone()
            self.assertEqual(tuple(row), (1, 75, 0, 75, 75, 75, 75, 390))
        info = dict(self.s.info_record(pet), Feng=1, Grade=75, Fly=1, Rei=2)
        roster = dict(self.s.list_record(pet), Feng=1, Grade=75, Fly=1, Rei=2, Fight=0, Show=0)
        self.assertIn(packet(706, ['number', 'summon'], [self.sid, info]), reply)
        self.assertIn(packet(713, [('array', 'summon_list')], [[roster]]), reply)
        self.send(757, self.sid)
        self.assertEqual(self.s.get(self.sid), pet)
        self.assertEqual(self.send(2811, self.sid), text_notice('守护化境暂未开放。'))
        self.assertEqual(self.s.get(self.sid), pet)

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
                self.send(763, sid, skill)
                self.assertIn(skill, [entry['stunt_id'] for entry in self.s.get(sid)['innate_skills']])
                with self.p.transaction() as db:
                    db.execute('UPDATE summons SET level=180 WHERE id=?', (sid,))
                    db.execute('INSERT INTO native_innate_suppressed VALUES(?,?)', (sid, skill))
                before = self.s.get(sid)
                with self.p.transaction() as db:
                    db.execute('DELETE FROM native_innate_suppressed WHERE summon_id=? AND skill=?', (sid, skill))
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
        with self.p.transaction() as db:
            db.execute('UPDATE native_innate_training SET level=50 WHERE summon_id=? AND skill=?', (self.sid, 307))
        self.send(770, self.sid, 307)
        with self.p.transaction(write=False) as db:
            row = db.execute('SELECT rebirth,ascension,sanctification FROM native_innate_training WHERE summon_id=? AND skill=?',
                             (self.sid, 307)).fetchone()
            self.assertEqual(tuple(row), (0, 1, 1))

    def test_jinguang_taisui_learns_its_current_stage_and_restores_it_from_save(self):
        sid = self.s.acquire(3374, 'taisui:stunt-unlock')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=49,experience=1000 WHERE id=?', (sid,))
        books = SkillBooks(self.s)
        self.assertEqual(self.send(759, sid), packet(720, ['number', ('array', 'number')], [sid, []]))
        self.assertEqual(self.send(763, sid, 302)[0], 98)
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=62 WHERE id=?', (sid,))
        self.assertEqual(self.send(759, sid), packet(720, ['number', ('array', 'number')], [sid, [302]]))
        self.assertIn(dict(position=2, level=0, can_do=1), books.list_state(sid)[1])
        before = self.s.get(sid)
        for skill in (366, 4904, 307, 4906):
            self.assertEqual(self.send(763, sid, skill)[0], 98)
        reply = self.send(763, sid, 302)
        self.assertIn(text_notice('领悟特技成功。'), reply)
        self.assertIn(books.packet(sid), reply)
        saved = Summons(self.p).get(sid)
        self.assertEqual([(row['stunt_id'], row['position']) for row in saved['innate_skills']],
                         [(4906, 1), (302, 2)])
        self.assertEqual(saved['innate_training'][302]['level'], 1)
        self.assertEqual(saved['experience'], before['experience'])
        self.assertEqual(self.send(763, sid, 302)[0], 98)
        self.assertEqual(self.s.get(sid), saved)
        self.assertEqual(self.send(759, sid), packet(720, ['number', ('array', 'number')], [sid, []]))
        reply = self.send(754, sid, 302)
        self.assert_training_update(reply, sid, 302, 4, 0)
        self.assertGreater(self.s.get(sid)['initiative'], before['initiative'])

    def assert_training_update(self, reply, sid, skill, level, rebirth):
        ids = [int.from_bytes(part[1:3], 'little') if not part[0] else part[0] for part in reply]
        self.assertNotIn(713, ids)
        self.assertNotIn(722, ids)
        self.assertEqual(ids.count(721), 1)
        reader = MessageReader(reply[ids.index(721)][3:])
        self.assertEqual(reader.integer(), sid)
        row = decode_record(reader, 'stunt_info')
        reader.finish()
        self.assertEqual((row['stunt_id'], row['grade'], row['rei']), (skill, level, rebirth))
        self.assertEqual(row['stuntexp_next'], threshold(level, rebirth))
        return row

    def test_level_54_uses_neidan_curve_and_converts_multiple_levels(self):
        sid = self.s.acquire(3374, 'taisui:stunt-experience')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=62,experience=20000 WHERE id=?', (sid,))
        self.send(763, sid, 302)
        with self.p.transaction() as db:
            db.execute('UPDATE native_innate_training SET level=54,experience=98 WHERE summon_id=? AND skill=302',
                       (sid,))

        reply = self.send(754, sid, 302)
        row = self.assert_training_update(reply, sid, 302, 54, 0)
        self.assertEqual((row['stuntexp'], row['stuntexp_next']), (4098, 116875))
        self.assertIn(text_notice('已将4000点守护经验转换为4000点特技经验。'), reply)
        self.assertEqual(self.s.get(sid)['experience'], 16000)

        converted = 116875 + 123487 + 123 - row['stuntexp']
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET experience=? WHERE id=?', (converted * 5, sid))
        row = self.assert_training_update(self.send(754, sid, 302), sid, 302, 56, 0)
        self.assertEqual((row['stuntexp'], row['stuntexp_next']), (123, 130344))
        saved = Summons(self.p).get(sid)
        self.assertEqual(saved['experience'], converted * 4)
        self.assertEqual((saved['innate_training'][302]['level'], saved['innate_training'][302]['experience']),
                         (56, 123))

    def test_conversion_caps_at_guardian_level_and_retains_legacy_saved_progress(self):
        with self.p.transaction() as db:
            schema(db)
            db.execute('UPDATE summons SET experience=1000,level=62 WHERE id=?', (self.sid,))
            db.execute('INSERT INTO native_innate_training(summon_id,skill,level,experience) VALUES(?,307,61,?)',
                       (self.sid, threshold(61, 0) - 2))
        self.assert_training_update(self.send(754, self.sid, 307), self.sid, 307, 62, 0)
        self.assertEqual(self.s.get(self.sid)['experience'], 998)
        with self.p.transaction() as db:
            db.execute('UPDATE native_innate_training SET level=127,rebirth=1 WHERE summon_id=? AND skill=307',
                       (self.sid,))
        self.assertEqual(self.s.get(self.sid)['innate_training'][307]['level'], 62)
        self.assertEqual(self.send(754, self.sid, 307)[0], 98)
        self.assertEqual(self.send(772, self.sid, 307)[0], 98)
        self.assertEqual(self.s.get(self.sid)['experience'], 998)
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT level FROM native_innate_training WHERE summon_id=? AND skill=307',
                                        (self.sid,)).fetchone()[0], 127)

    def test_ordinary_rebirth_requires_skill_threshold_and_resets_grade_and_experience(self):
        with self.p.transaction() as db:
            schema(db)
            db.execute('UPDATE summons SET experience=10000,level=120 WHERE id=?', (self.sid,))
            db.execute('INSERT INTO native_innate_training(summon_id,skill,level,experience) VALUES(?,307,99,99)',
                       (self.sid,))
        for current, required in ((0, 100), (1, 120)):
            self.assertEqual(self.send(772, self.sid, 307)[0], 98)
            with self.p.transaction() as db:
                db.execute('UPDATE native_innate_training SET level=?,experience=27 WHERE summon_id=? AND skill=307',
                           (required, self.sid))
            reply = self.send(772, self.sid, 307)
            self.assert_training_update(reply, self.sid, 307, 0, current + 1)
            training = self.s.get(self.sid)['innate_training'][307]
            self.assertEqual((training['level'], training['experience'], training['rebirth']), (0, 0, current + 1))
        self.assertEqual(self.send(772, self.sid, 307)[0], 98)
        self.assertEqual(self.s.get(self.sid)['experience'], 5000)

    def test_shared_candidates_use_open_slots_and_keep_their_saved_positions(self):
        sid = self.s.acquire(2103, 'langtaosha:stunt-unlock')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=50 WHERE id=?', (sid,))
        books = SkillBooks(self.s)
        self.send(763, sid, 323)
        self.assertEqual(self.send(763, sid, 338)[0], 98)
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=100 WHERE id=?', (sid,))
        self.send(763, sid, 338)
        self.assertEqual([(entry['stunt_id'], entry['position']) for entry in books.records(sid)],
                         [(303, 1), (323, 2), (338, 3)])
        self.send(756, sid, 323)
        self.assertEqual([(entry['stunt_id'], entry['position']) for entry in books.records(sid)],
                         [(303, 1), (338, 3)])
        self.send(763, sid, 323)
        self.assertEqual([(entry['stunt_id'], entry['position']) for entry in books.records(sid)],
                         [(303, 1), (323, 2), (338, 3)])

    def test_guardian_rebirth_and_ascension_reset_level_and_refresh_native_stats(self):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=180 WHERE id=?', (self.p.cid,))
        before = self.s.get(self.sid)
        self.send(758, self.sid)
        self.assertEqual(self.s.get(self.sid), before)
        for mid, required, experience, level, rebirth, ascension in (
                (769, 100, 10000, 30, 1, 0), (769, 120, 10000, 45, 2, 0), (758, 140, 20000, 60, 2, 1)):
            with self.subTest(mid=mid, required=required):
                for grade, stored in ((required - 1, experience), (required, experience - 1)):
                    with self.p.transaction() as db:
                        db.execute('UPDATE summons SET level=?,experience=? WHERE id=?', (grade, stored, self.sid))
                    before = self.s.get(self.sid)
                    self.send(mid, self.sid)
                    self.assertEqual(self.s.get(self.sid), before)
                with self.p.transaction() as db:
                    db.execute('UPDATE summons SET experience=?,cor=278,mag=201,strength=210,speed=201,'
                               'points=40,hp=9999,mp=9999 WHERE id=?', (experience, self.sid))
                if mid == 758:
                    before = self.s.get(self.sid)
                    self.send(757, self.sid)
                    self.assertEqual(self.s.get(self.sid), before)
                reply = self.send(mid, self.sid)
                pet = Summons(self.p).get(self.sid)
                self.assertIn(packet(706, ['number','summon'], [self.sid, self.s.info_record(pet)]), reply)
                with self.p.transaction(write=False) as db:
                    saved = db.execute('SELECT level,rebirth,ascension,experience,cor,mag,strength,speed,points,hp,mp '
                                       'FROM summons WHERE id=?', (self.sid,)).fetchone()
                self.assertEqual(tuple(saved), (level, rebirth, ascension, 0, level, level, level, level,
                                                4 * level + 30 * rebirth, pet['hp'], pet['mp']))
                self.send(mid, self.sid)
                self.assertEqual(self.s.get(self.sid), pet)

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
