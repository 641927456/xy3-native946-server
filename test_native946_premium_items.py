"""Shared and post-battle medical licenses on the existing local medical service."""
from time import time
import unittest
from unittest.mock import patch

from native946_cosmetics import Cosmetics
from native946_login import MessageReader
from native946_codec import number
from native946_premium_items import (
    AUTO_MEDICAL_DAYS, AUTO_MEDICAL_KEY, PENDING, SHARED_MEDICAL_KEY, SPEED_DAYS, SPEED_KEY, license_validity,
)
from native946_summons import Summons
from native946_world import world


class PremiumItemTests(unittest.TestCase):
    def setUp(self):
        from test_native946_wilderness_party import WildernessPartyTests
        self.fixture = WildernessPartyTests(methodName='test_create_invite_requires_recipient_consent')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.a, self.b = self.fixture.a, self.fixture.b
        self.service = self.fixture.svc
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=20')

    def send(self, game, mid, *args, tail=b''):
        return self.fixture.call(game, mid, *args, tail=tail)

    def add(self, game, kind):
        with game.progression.transaction() as db:
            before = {r['id'] for r in db.execute('SELECT id FROM inventory WHERE character_id=?', (game.progression.cid,))}
            Cosmetics(game)._add(db, kind)
            return next(dict(r) for r in db.execute('SELECT * FROM inventory WHERE character_id=?',
                (game.progression.cid,)) if r['id'] not in before)

    def use(self, game, kind):
        item = self.add(game, kind)
        reply = self.send(game, 807, 0, item['id'])
        self.assertFalse(any(p.startswith(b'\0x\3') for p in reply), reply)
        return item, reply

    def expiry(self, game, key=SHARED_MEDICAL_KEY):
        with game.progression.transaction(write=False) as db:
            return license_validity(db, game.progression.cid, key=key)

    def add_reserve_food(self, game, kind):
        with game.progression.transaction() as db:
            page, slot = game.progression.free_bag_slot(game.progression._snapshot(db))
            return db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                              (game.progression.cid, kind, page, slot)).lastrowid

    def test_portrait_food_selection_updates_both_bottles_and_persists(self):
        from native946_game_login import message_id
        from native946_reserve_food import FAST_USE

        hero, guardian = self.a.progression.cid, self.a.summons.claim_starter()
        self.a.summons.status(guardian, 1)
        hp_food, mp_food = self.add_reserve_food(self.a, 6782), self.add_reserve_food(self.a, 6783)
        opening = self.send(self.a, FAST_USE, tail=bytes((1, 1)))
        self.assertEqual(opening[0][:3], message_id(1025))
        self.assertIn(hp_food, {i['id'] for i in self.a.progression.snapshot()['items']})
        reply = self.a.receive(1032, MessageReader(b'\x01' + number(hero)))
        self.assertIn(message_id(685) + b''.join(number(n) for n in (20000, 0, 0, 0)), reply)
        self.send(self.a, FAST_USE, tail=bytes((2, 2)))
        self.assertEqual(self.a.dialog_actions[0]['item'], mp_food)
        reply = self.a.receive(1032, MessageReader(b'\x01' + number(hero)))
        self.assertIn(message_id(685) + b''.join(number(n) for n in (20000, 0, 0, 20000)), reply)
        self.a.receive(1032, MessageReader(b'\x01' + number(hero)))
        self.assertIn(message_id(685) + b''.join(number(n) for n in (20000, 0, 0, 20000)),
                      self.a.sync_progression())
        self.a.summons.status(guardian, 2)
        self.assertIn(message_id(685) + b''.join(number(n) for n in (20000, 0, 0, 0)),
                      self.a.sync_progression())

    def test_guardian_slot_combines_old_balances_and_recovers_the_replacement(self):
        from native946_codec import packet

        game = self.a
        original = game.summons.claim_starter()
        replacement = game.summons.acquire(3197, 'reserve:replacement')
        game.summons.status(original, 1)
        with game.progression.transaction() as db:
            db.executemany('INSERT INTO cosmetic_features VALUES(?,?,?,0)', [
                (game.progression.cid, f'reserve:{original}:hp', 20000),
                (game.progression.cid, f'reserve:{replacement}:hp', 300),
                (game.progression.cid, f'reserve:{original}:mp', 20000),
            ])
        self.send(game, 807, original, self.add_reserve_food(game, 6782))
        pet = game.summons.get(replacement)
        game.summons.set_vitals(replacement, pet['hpmax'] - 7, pet['mpmax'] - 9)
        for sid in (replacement, original, replacement):
            reply = self.send(game, 750, sid, 1)
            self.assertIn(packet(685, ['buff_info'] * 2, [{'Hp': 0, 'Mp': 0}, {'Hp': 40293, 'Mp': 19991}]), reply)
        self.assert_full(game)
        with game.progression.transaction(write=False) as db:
            balances = dict(db.execute('SELECT key,value FROM cosmetic_features WHERE character_id=? '
                                       "AND key GLOB 'reserve:*'", (game.progression.cid,)).fetchall())
        self.assertEqual(balances, {'reserve:guardian:hp': 40293, 'reserve:guardian:mp': 19991})

    def test_reserve_debits_only_the_vitals_restored_during_settlement(self):
        from native946_game_login import message_id
        from native946_wilderness import POLICY

        hero = self.a.progression.cid
        self.fixture.buff(self.a)
        guardian = self.a.summons.claim_starter()
        self.a.summons.status(guardian, 1)
        for target, kind in ((hero, 6782), (guardian, 6783)):
            item_id = self.add_reserve_food(self.a, kind)
            self.send(self.a, 807, target, item_id)
        battle = self.fixture.battle(party=False, pet=True)
        fighter = battle.state['fighters'][hero]
        fighter['hp'] = fighter['hpmax'] - 17
        pet = battle.current_pet(hero)
        pet['mp'] = pet['mpmax'] - 19
        for enemy in battle.enemies():
            enemy['hp'] = 0
        with patch.dict(POLICY, {'guardian_exp_per_enemy_level': 0}):
            battle.resolve()
        self.send(self.a, 2184)
        expected = message_id(685) + b''.join(number(n) for n in (19983, 0, 0, 19981))
        self.assertIn(expected, self.a.sync_progression())
        state = self.a.progression.snapshot()
        self.assertEqual(state['hp'], state['hpmax'])
        pet = self.a.summons.get(guardian)
        self.assertEqual(pet['mp'], pet['mpmax'])
        self.send(self.a, 2184)
        self.assertIn(expected, self.a.sync_progression())

    def test_auto_medical_blessing_timer_and_expiry_use_original_portrait_messages(self):
        from native946_codec import packet
        from native946_game_login import message_id

        _, reply = self.use(self.a, 70491)
        icon = packet(37, ['number'] * 3, [1, 1008, 98])
        self.assertIn(icon, reply)
        self.assertIn(icon, self.a.sync_progression())
        reply = self.send(self.a, 39, 1008, 98)
        self.assertEqual(reply[0][0], 38)
        countdown = MessageReader(reply[0][1:])
        self.assertEqual((countdown.integer(), countdown.integer(), countdown.take(1)), (1008, 98, b'\x01'))
        self.assertGreaterEqual(countdown.integer(), 86395)
        countdown.finish()
        with patch('native946_premium_items.time', return_value=self.expiry(self.a, AUTO_MEDICAL_KEY) + 1):
            self.assertIn(packet(37, ['number'] * 3, [0, 1008, 98]), self.a.tick())
            self.assertFalse(any(p.startswith(message_id(37)) for p in self.a.tick() or ()))

    def doctor(self, game, op=3):
        from test_native946_npcs import NpcGameplayTests
        from types import SimpleNamespace
        npc = next(n for rows in world().npcs.values() for n in rows.values()
                   if n['npc_type'] == 6 and n['scene_id'] in world().scenes)
        NpcGameplayTests.approach(SimpleNamespace(game=game, characters=game.characters), npc['scene_id'], npc['npc_id'])
        opening = self.send(game, 1034, npc['npc_id'])
        self.assertTrue(game.dialog_open, opening)
        self.assertEqual(game.dialog_actions[op-1], {'kind': 'medical', 'op': ('hero', 'summons', 'all')[op-1]})
        return game.receive(1032, MessageReader(bytes([op]) + number(npc['npc_id'])))

    def damage(self):
        for game in (self.a, self.b):
            game.progression.set_vitals(1, 0)
            sid = game.summons.claim_starter()
            game.summons.set_vitals(sid, 1, 0)

    def assert_full(self, game):
        state = game.progression.snapshot()
        self.assertEqual((state['hp'], state['mp']), (state['hpmax'], state['mpmax']))
        for pet in game.summons.snapshot():
            self.assertEqual((pet['hp'], pet['mp']), (pet['hpmax'], pet['mpmax']))

    def test_both_skus_renew_exactly_seven_days_consume_once_and_survive_cold_read(self):
        from native946_progression import Progression
        before = int(time())
        first, _ = self.use(self.a, 55580)
        self.assertLess(abs(self.expiry(self.a) - before - 7 * 86400), 3)
        expiry = self.expiry(self.a)
        self.use(self.a, 65580)
        self.assertEqual(self.expiry(self.a), expiry + 7 * 86400)
        self.send(self.a, 807, 0, first['id'])
        self.assertEqual(self.expiry(self.a), expiry + 7 * 86400)
        cold = Progression(self.a.progression.database, self.a.character['owner'], self.a.progression.cid)
        with cold.transaction(write=False) as db:
            self.assertEqual(license_validity(db, cold.cid), expiry + 7 * 86400)

    def test_member_owned_effect_shares_leader_medical_with_each_members_guardians(self):
        self.fixture.team()
        self.use(self.b, 55580)
        self.damage()
        before = [g.progression.snapshot()['gold'] for g in (self.a, self.b)]
        self.doctor(self.a)
        for game in (self.a, self.b):
            self.assert_full(game)
        self.assertEqual([g.progression.snapshot()['gold'] for g in (self.a, self.b)], [gold-3 for gold in before])
        self.assertTrue(self.service.drain(self.b))
        self.doctor(self.a)
        self.assertEqual([g.progression.snapshot()['gold'] for g in (self.a, self.b)], [gold-3 for gold in before])

    def test_low_balance_shared_patient_does_not_pay_or_block_other_patient(self):
        self.fixture.team()
        self.use(self.a, 55580)
        self.damage()
        with self.b.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=2 WHERE id=?', (self.b.progression.cid,))
        self.doctor(self.a)
        self.assert_full(self.a)
        self.assertEqual(self.a.progression.snapshot()['gold'],17)
        self.assertEqual(self.b.progression.snapshot()['gold'],2)
        self.assertEqual(self.b.progression.snapshot()['hp'],1)
        self.assertEqual(self.b.summons.snapshot()[0]['hp'],1)

    def test_no_effect_expired_effect_leave_and_member_doctor_do_not_share(self):
        self.fixture.team()
        for mode in ('none', 'expired', 'left', 'member'):
            with self.subTest(mode=mode):
                if mode == 'expired':
                    self.use(self.b, 55580)
                    with self.b.progression.transaction() as db:
                        db.execute('UPDATE cosmetic_features SET expires=1 WHERE character_id=? AND key=?',
                            (self.b.progression.cid, SHARED_MEDICAL_KEY))
                if mode == 'left':
                    self.use(self.b, 55580)
                    self.send(self.b, 2051)
                if mode == 'member':
                    self.fixture.team()
                self.damage()
                caller = self.b if mode == 'member' else self.a
                other = self.a if mode == 'member' else self.b
                self.doctor(caller)
                self.assert_full(caller)
                state = other.progression.snapshot()
                self.assertEqual((state['hp'], state['mp']), (1, 0))

    def test_failure_on_second_member_rolls_back_all_heroes_and_guardians(self):
        self.fixture.team()
        self.use(self.a, 55580)
        self.damage()
        before = [(g.progression.snapshot(), g.summons.snapshot()) for g in (self.a, self.b)]
        original = Summons.rest
        calls = []
        def fail(summons):
            calls.append(summons.progression.cid)
            if summons.progression.cid == self.b.progression.cid:
                raise ValueError('fixture second-member failure')
            return original(summons)
        with patch.object(Summons, 'rest', fail):
            self.doctor(self.a)
        self.assertEqual(calls, [self.a.progression.cid, self.b.progression.cid])
        for game, (hero, pets) in zip((self.a, self.b), before):
            current = game.progression.snapshot()
            for key in ('hp', 'mp', 'gold'):
                self.assertEqual(current[key], hero[key])
            self.assertEqual(game.summons.snapshot(), pets)

    def test_unknown_numeric_rules_reject_every_known_variant_without_consumption(self):
        for kind, rule in PENDING.items():
            with self.subTest(kind=kind):
                item = self.add(self.a, int(kind))
                before = self.a.progression.snapshot()
                reply = self.send(self.a, 807, 0, item['id'])
                self.assertEqual(reply[0][0], 98)
                reader = MessageReader(reply[0][1:])
                self.assertEqual(reader.string().decode('gb18030'), rule['reason'])
                self.assertEqual(reader.string(), b'')
                self.assertEqual((reader.integer(), reader.integer()), (1, 0))
                reader.finish()
                after = self.a.progression.snapshot()
                self.assertEqual(after['items'], before['items'])
                self.assertEqual((after['hp'], after['mp'], after['gold']),
                                 (before['hp'], before['mp'], before['gold']))
                with self.a.progression.transaction() as db:
                    db.execute('DELETE FROM inventory WHERE id=?', (item['id'],))

    def test_entire_native_convenience_family_is_either_usable_or_explicitly_pending(self):
        from native946_cosmetics import DATA as CATALOG
        from native946_mall_uses import DATA as USES
        families = {kind: row for kind, row in CATALOG['items'].items()
                    if row['name'].startswith(('神速丹', '共济丸', '笑忘入梦饮', '自行丹', '逍遥无极丹'))
                    or row['name'] in ('九枢', '九枢（1天）')}
        self.assertEqual(len(families), 27)
        self.assertEqual({kind for kind in families if kind not in USES['rules']}, set(PENDING))
        for kind in families:
            if kind in PENDING:
                continue
            item, reply = self.use(self.a, int(kind))
            self.assertFalse(any(p[0] == 97 for p in reply))
            with self.a.progression.transaction(write=False) as db:
                self.assertIsNone(db.execute('SELECT 1 FROM inventory WHERE id=?', (item['id'],)).fetchone())

    def test_team_serialization_has_actual_expiry_and_clears_after_expiry(self):
        self.fixture.team()
        self.use(self.b, 65580)
        team = self.service.team(self.a.progression.cid)
        record = next(row for row in team['members'] if row['character_id'] == self.b.progression.cid)
        self.assertEqual(self.service.member_record(record)['autocure_validity'], self.expiry(self.b))
        with self.b.progression.transaction() as db:
            db.execute('UPDATE cosmetic_features SET expires=1 WHERE character_id=? AND key=?',
                       (self.b.progression.cid, SHARED_MEDICAL_KEY))
        self.assertEqual(self.service.member_record(record)['autocure_validity'], 0)

    def test_auto_medical_skus_renew_and_publish_expiry_independently(self):
        self.fixture.team()
        self.use(self.b, 55580)
        shared = self.expiry(self.b)
        for kind, days in AUTO_MEDICAL_DAYS.items():
            previous = self.expiry(self.b, AUTO_MEDICAL_KEY)
            earliest = int(time())
            self.use(self.b, kind)
            actual = self.expiry(self.b, AUTO_MEDICAL_KEY)
            self.assertGreaterEqual(actual, max(earliest, previous) + days * 86400)
            self.assertLessEqual(actual, max(int(time()), previous) + days * 86400)
        self.assertEqual(self.expiry(self.b), shared)
        team = self.service.team(self.a.progression.cid)
        record = next(row for row in team['members'] if row['character_id'] == self.b.progression.cid)
        self.assertEqual(self.service.member_record(record)['autoheal_validity'], actual)
        with self.b.progression.transaction() as db:
            db.execute('UPDATE cosmetic_features SET expires=1 WHERE character_id=? AND key=?',
                       (self.b.progression.cid, AUTO_MEDICAL_KEY))
        self.assertEqual(self.service.member_record(record)['autoheal_validity'], 0)

    def test_training_medical_includes_escape_and_cannot_be_replayed(self):
        self.use(self.a, 70491)
        sid = self.a.summons.claim_starter()
        self.a.summons.status(sid, 1)
        for request in (2178, 2182):
            self.damage()
            before = self.a.progression.snapshot()['gold']
            self.a.begin_battle()
            self.send(self.a, request, 2)
            self.assertIsNone(self.a.battle)
            self.assert_full(self.a)
            self.assertEqual(self.a.progression.snapshot()['gold'], before - 3)
            self.send(self.a, request, 2)
            self.assertEqual(self.a.progression.snapshot()['gold'], before - 3)
        self.a.summons.status(sid, 2)
        self.damage()
        for victory in (True, False):
            before = self.a.progression.snapshot()['gold']
            self.a.begin_battle()
            self.a.battle.update(phase='drawing', victory=victory, hp=int(victory), mp=0)
            self.send(self.a, 2184)
            self.assertIsNone(self.a.battle)
            self.assert_full(self.a)
            self.assertEqual(self.a.progression.snapshot()['gold'],before + (5 if victory else 0) - 3)
            self.damage()
            self.send(self.a, 2184)
            self.assertEqual(self.a.progression.snapshot()['hp'], 1)
        with self.a.progression.transaction() as db:
            db.execute('UPDATE cosmetic_features SET expires=1 WHERE character_id=? AND key=?',
                       (self.a.progression.cid, AUTO_MEDICAL_KEY))
        self.a.begin_battle()
        self.a.battle.update(phase='drawing', victory=True, hp=1, mp=0)
        self.send(self.a, 2184)
        self.assertLess(self.a.progression.snapshot()['hp'], self.a.progression.snapshot()['hpmax'])

    def test_team_retreat_restores_premium_and_uses_reserve_if_cash_is_short(self):
        from native946_codec import packet
        from test_native946_wilderness_party import mid

        self.use(self.a, 70491)
        self.use(self.b, 55580)
        for game in (self.a, self.b):
            self.fixture.buff(game)
            sid = game.summons.claim_starter()
            game.summons.status(sid, 1)
            for target, kind in ((game.progression.cid, 6782), (sid, 6783)):
                self.send(game, 807, target, self.add_reserve_food(game, kind))
        with self.b.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=2 WHERE id=?', (self.b.progression.cid,))
        battle = self.fixture.battle(pet=True)
        before = [game.progression.snapshot() for game in (self.a, self.b)]
        for cid in battle.games:
            fighter = battle.state['fighters'][cid]
            fighter['hp'] = fighter['hpmax'] - 17
            pet = battle.current_pet(cid)
            pet['mp'] = pet['mpmax'] - 19
        replies = (self.send(self.a, 2178, 2), self.service.drain(self.b))
        for game, previous, used, reply in zip((self.a, self.b), before, (False, True), replies):
            self.assertIsNone(game.battle)
            self.assert_full(game)
            self.assertIn(2146, [mid(p) for p in reply])
            expected = packet(685, ['buff_info'] * 2, [{'Hp': 20000 - 17 * used, 'Mp': 0},
                                                       {'Hp': 0, 'Mp': 20000 - 19 * used}])
            self.assertIn(expected, reply)
            self.send(game, 2184)
            self.assertIn(expected, game.sync_progression())
            after = game.progression.snapshot()
            self.assertEqual(after['experience'], previous['experience'])
            self.assertEqual(after['gold'], previous['gold'] - (0 if used else 3))

    def test_auto_medical_insufficient_funds_does_not_roll_back_battle(self):
        self.use(self.a, 70491)
        self.damage()
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=2 WHERE id=?', (self.a.progression.cid,))
        self.a.begin_battle()
        self.a.battle.update(phase='drawing', victory=False, hp=0, mp=0)
        self.send(self.a, 2184)
        self.assertIsNone(self.a.battle)
        state=self.a.progression.snapshot()
        self.assertEqual((state['gold'],state['hp']),(2,1))

    def test_speed_variants_publish_and_renew_without_multiplying_speed(self):
        from struct import pack
        from native946_characters import scene_player
        from native946_game_login import message_id
        from native946_login import string_field
        from native946_party import numbers
        self.fixture.team()
        for kind, days in SPEED_DAYS.items():
            previous=self.expiry(self.b, SPEED_KEY)
            earliest=int(time())
            _,reply=self.use(self.b,kind)
            expiry=self.expiry(self.b,SPEED_KEY)
            self.assertGreaterEqual(expiry,max(previous,earliest)+days*86400)
            self.assertLessEqual(expiry,max(previous,int(time()))+days*86400)
            if not previous:
                self.assertIn(numbers(1621,self.b.progression.cid,15,5),reply)
                self.assertIn(numbers(1621,self.a.progression.cid,15,5),self.service.drain(self.a))
            for game in (self.a,self.b):
                self.assertEqual(scene_player(game.character)['run_speed'],5)
        team=self.service.team(self.a.progression.cid)
        row=next(row for row in team['members'] if row['character_id']==self.b.progression.cid)
        self.assertEqual(self.service.member_record(row)['speed_validity'],expiry)
        self.service.drain(self.b)
        target=self.a.grid.nearest_walkable(self.a.character['x']+1,self.a.character['y'])
        self.send(self.a,1643,5,tail=pack('<HH',*target)+string_field(b''))
        moves=[]
        for reply in self.service.drain(self.b):
            if reply.startswith(message_id(1608)):
                reader=MessageReader(reply[3:])
                moves.append((reader.integer(),reader.integer()))
        self.assertIn((self.a.progression.cid,5),moves)
        self.assertIn((self.b.progression.cid,5),moves)

    def test_speed_share_ends_on_away_leave_disconnect_and_expiration(self):
        from native946_characters import scene_player
        from native946_party import numbers
        self.fixture.team()
        self.use(self.b,56659)
        self.send(self.b,2044)
        self.assertEqual(scene_player(self.a.character)['run_speed'],4)
        self.assertEqual(scene_player(self.b.character)['run_speed'],5)
        self.send(self.b,2045)
        self.assertEqual(scene_player(self.a.character)['run_speed'],5)
        self.send(self.b,2051)
        self.assertEqual(scene_player(self.a.character)['run_speed'],4)
        self.assertEqual(scene_player(self.b.character)['run_speed'],5)
        self.fixture.team()
        self.service.detach(self.b.progression.cid)
        self.assertEqual(scene_player(self.a.character)['run_speed'],4)
        self.service.attach(self.b)
        self.service.refresh_run_speeds(self.b)
        self.assertEqual(scene_player(self.a.character)['run_speed'],5)
        self.service.drain(self.a)
        with self.b.progression.transaction() as db:
            db.execute('UPDATE cosmetic_features SET expires=1 WHERE character_id=? AND key=?',
                       (self.b.progression.cid,SPEED_KEY))
        self.a._run_speed_check_at=0
        self.assertIn(numbers(1621,self.a.progression.cid,15,4),self.service.tick(self.a))
        self.assertEqual(scene_player(self.b.character)['run_speed'],4)

    def test_group_medical_restores_other_participants_only_with_active_shared_effect(self):
        from native946_wilderness import POLICY

        self.use(self.b, 80492)
        for shared in (False, True):
            if shared:
                self.use(self.a, 65580)
            battle = self.fixture.battle(pet=True)
            for game in (self.a, self.b):
                cid = game.progression.cid
                battle.state['fighters'][cid].update(hp=1, mp=0)
                battle.current_pet(cid).update(hp=1, mp=0)
            for enemy in battle.enemies():
                enemy['hp'] = 0
            # A guardian level-up refills both bars independently of treatment.
            with patch.dict(POLICY, {'guardian_exp_per_enemy_level': 0}):
                battle.resolve()
            self.assertTrue(battle.state['settled'])
            self.send(self.a, 2184)
            self.send(self.b, 2184)
            self.assert_full(self.b)
            if shared:
                self.assert_full(self.a)
            else:
                self.assertLess(self.a.progression.snapshot()['hp'], self.a.progression.snapshot()['hpmax'])
                self.assertLess(self.a.summons.snapshot()[0]['hp'], self.a.summons.snapshot()[0]['hpmax'])

    def test_disconnected_license_owner_does_not_supply_shared_treatment(self):
        self.fixture.team()
        self.use(self.b, 55580)
        self.service.detach(self.b.progression.cid)
        self.damage()
        self.doctor(self.a)
        self.assert_full(self.a)
        self.assertEqual(self.b.progression.snapshot()['hp'], 1)

    def test_locked_item_cannot_create_a_license_and_expired_item_cannot_extend_one(self):
        item = self.add(self.a, 55580)
        with self.a.progression.transaction() as db:
            db.execute('INSERT INTO native_object_locks(character_id,kind,object_id,flag,release_at) VALUES(?,?,?,?,?)',
                       (self.a.progression.cid, 'item', item['id'], 1, 0))
        self.send(self.a, 807, 0, item['id'])
        self.assertEqual(self.expiry(self.a), 0)
        with self.a.progression.transaction() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM inventory WHERE id=?', (item['id'],)).fetchone())
            db.execute('DELETE FROM native_object_locks WHERE character_id=?', (self.a.progression.cid,))
            db.execute('UPDATE cosmetic_expiry SET expires=1 WHERE item_id=?', (item['id'],))
        self.send(self.a, 807, 0, item['id'])
        self.assertEqual(self.expiry(self.a), 0)


if __name__ == '__main__':
    unittest.main()
