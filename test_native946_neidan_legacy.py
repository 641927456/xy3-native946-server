"""Storage, protocol and reference-formula checks for the local pill profile."""
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import number
from native946_gameplay import NativeGameplay,ENEMY_ID,NORMAL_ATTACK
from native946_login import MessageReader
from native946_neidan_legacy import LegacyNeidan,grow,threshold,effect,KINDS
from native946_neidan import battle_energy_cost
from native946_progression import Progression
from native946_summons import Summons


class LegacyFormulaTests(unittest.TestCase):
    def test_current_client_energy_categories(self):
        self.assertEqual([battle_energy_cost(n) for n in (0,1,2)],[1,5,5])
        for value in (-1,3,True,'0'):
            with self.assertRaises(ValueError):battle_energy_cost(value)

    def test_all_levels_against_recovered_numeric_expression(self):
        for rebirth,cap in enumerate((102,122,142)):
            for level in range(cap+1):
                self.assertEqual(threshold(level,rebirth),math.floor((level**3+50)*(1.06+rebirth*.24)*.7))

    def test_equal_threshold_and_each_new_cost(self):
        initial=dict(kind=211,level=0,rebirth=0,experience=0,expnext=40,energy=6000)
        self.assertEqual(grow(initial,39)['level'],0)
        self.assertEqual((grow(initial,40)['level'],grow(initial,40)['experience']),(1,0))
        self.assertEqual((grow(initial,40+37)['level'],grow(initial,40+37)['experience']),(2,0))
        self.assertEqual(grow(initial,10000)['level'],15)

    def test_cap_does_not_recurse_or_drop_remaining_experience(self):
        for stage, cap in enumerate((100, 120, 140)):
            row=dict(kind=211,level=cap-1,rebirth=stage,experience=0,expnext=threshold(cap-1,stage),energy=6000)
            out=grow(row,10**9)
            self.assertEqual((out['level'], out['experience']), (cap, 10**9-row['expnext']))
            self.assertEqual(row['level'],cap-1)

    def test_effect_formula_and_native_tooltip_argument_counts(self):
        pet=dict(level=100,amity=50000,mpmax=1000)
        for kind in KINDS:
            row=dict(kind=kind,level=70,rebirth=1,energy=6000)
            e=effect(row,pet)
            self.assertGreater(e['chance'],0)
            self.assertLessEqual(e['chance'],100)
            self.assertEqual(len(e['args']),1 if kind==233 else 3)
            if kind in (211,212,213,214):
                self.assertEqual(e['damage'],math.floor(296.1572+.0002364957*1000**1.57))
            row['energy']=0;self.assertFalse(effect(row,pet)['enabled'])


class LegacyStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'test.sqlite3'
        LocalAccounts(self.path).register({'username':'pill_owner','password':'123456789'})
        self.characters=Characters(self.path)
        self.character=self.characters.create('pill_owner','内丹测试',702)
        self.game=NativeGameplay(self.character,self.characters,lambda:())
        self.p=self.game.progression;self.s=self.game.summons;self.n=LegacyNeidan(self.s)
        self.sid=self.s.claim_starter();self.n.grant_test_kit()

    def item(self,kind=211):
        with self.p.transaction(write=False) as db:
            return db.execute('SELECT item_id FROM legacy_neidan_items n JOIN inventory i ON i.id=n.item_id '
                              'WHERE i.character_id=? AND n.kind=?',(self.p.cid,kind)).fetchone()[0]

    def xp_item(self):return next(x['id'] for x in self.p.snapshot()['items'] if x['type']==6024)

    def call(self,mid,*args):return self.game.receive(mid,MessageReader(b''.join(number(x) for x in args)))

    def test_kit_idempotent_and_pills_do_not_merge(self):
        before=self.p.snapshot()['items'];self.assertFalse(self.n.grant_test_kit())
        self.p.merge([i['id'] for i in before])
        self.assertEqual(before,self.p.snapshot()['items'])
        with self.p.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM legacy_neidan_items').fetchone()[0],7)

    def test_existing_character_login_does_not_wait_for_schema_write_lock(self):
        with closing(sqlite3.connect(self.path)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            try:
                cold=Progression(self.path,'pill_owner',self.p.cid)
                pets=Summons(cold)
                self.assertEqual(pets.get(self.sid)['id'],self.sid)
            finally:blocker.rollback()

    def test_native_install_feed_list_and_cold_reload(self):
        self.call(807,self.sid,self.item())
        self.assertEqual(self.s.get(self.sid)['neidan'][0]['level'],0)
        messages=self.call(807,self.sid,self.xp_item())
        self.assertEqual(self.s.get(self.sid)['neidan'][0]['level'],15)
        self.assertEqual(self.call(760,self.sid)[:3],b'\x00\xcc\x02')
        cold=Summons(Progression(self.path,'pill_owner',self.p.cid))
        self.assertEqual(cold.get(self.sid)['neidan'],self.s.get(self.sid)['neidan'])
        self.assertGreater(len(messages),0)

    def test_foreign_pet_and_full_slot_leave_items_unchanged(self):
        before=self.p.snapshot()['items']
        with self.assertRaises(ValueError):self.n.use(self.sid+1,self.item())
        self.assertEqual(before,self.p.snapshot()['items'])
        self.n.use(self.sid,self.item());before=self.p.snapshot()['items']
        with self.assertRaises(ValueError):self.n.use(self.sid,self.item(212))
        self.assertEqual(before,self.p.snapshot()['items'])

    def test_convert_twenty_percent_with_fresh_level_cost_and_no_gold_spend(self):
        self.n.use(self.sid,self.item())
        with self.p.transaction() as db:db.execute('UPDATE summons SET experience=10000 WHERE id=?',(self.sid,))
        gold=self.p.snapshot()['gold'];self.call(753,self.sid,211)
        self.assertEqual(self.s.get(self.sid)['experience'],8000)
        expected=grow(dict(kind=211,level=0,rebirth=0,experience=0,expnext=40,energy=6000),2000)
        self.assertEqual(self.s.get(self.sid)['neidan'][0]['level'],expected['level'])
        self.assertEqual(self.p.snapshot()['gold'],gold)

    def test_failed_request_trailing_bytes_roll_back_installation(self):
        before=self.p.snapshot()['items']
        self.game.receive(807,MessageReader(number(self.sid)+number(self.item())+b'\xff'))
        self.assertEqual(self.p.snapshot()['items'],before)
        self.assertEqual(self.s.get(self.sid)['neidan'],[])

    def test_new_neidan_and_energy_item_rejections_preserve_resources(self):
        from native946_neidan import MAX_LEVEL_PREVIEWS, PILL_TYPES

        self.n.use(self.sid,self.item())
        with self.p.transaction() as db:
            pill=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,91508,8,23,1)',
                            (self.p.cid,)).lastrowid
            energy=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,6128,8,24,2)',
                              (self.p.cid,)).lastrowid
        before=self.p.snapshot()['items']
        guardian=self.s.get(self.sid)
        self.assertEqual(set(MAX_LEVEL_PREVIEWS), set(PILL_TYPES))
        payload=self.call(806,pill,0)
        self.assertEqual(int.from_bytes(payload[1:3],'little'),791)
        reader=MessageReader(payload[3:])
        self.assertEqual(reader.integer(),pill)
        description=reader.string().decode('gb18030')
        self.assertIn('飞升180级五阶基准，未共鸣',description)
        self.assertIn('隐身期间的物理伤害+30%',description)
        for mid,args,expected in [
            (753,(self.sid,283),'潜龙在渊'),
            (755,(self.sid,283),'潜龙在渊'),
            (767,(self.sid,283),'潜龙在渊'),
            (5226,(self.sid,283,1,0),'潜龙在渊'),
            (5227,(self.sid,283),'潜龙在渊'),
            (5228,(self.sid,283),'潜龙在渊'),
            (5229,(self.sid,283),'潜龙在渊'),
            (807,(self.sid,pill),'潜龙在渊'),
            (807,(self.sid,energy),'暂不可补充内丹元气'),
            (807,(0,pill),'切换为守护'),
            (807,(0,energy),'切换为守护'),
        ]:
            with self.subTest(mid=mid,args=args):
                reply=self.call(mid,*args)
                self.assertEqual(reply[0],98)
                self.assertIn(expected,self.game.last_event['reason'])
                self.assertEqual(self.p.snapshot()['items'],before)
                self.assertEqual(self.s.get(self.sid),guardian)

    def test_drop_loses_three_levels_and_can_reinstall(self):
        self.n.use(self.sid,self.item());self.n.use(self.sid,self.xp_item())
        with self.p.transaction() as db:db.execute('UPDATE legacy_neidan SET experience=0 WHERE summon_id=?',(self.sid,))
        before=self.s.get(self.sid)['neidan'][0]
        self.n.drop(self.sid,211);self.assertEqual(self.s.get(self.sid)['neidan'],[])
        expected=dict(before,level=before['level']-3,expnext=threshold(before['level']-3,before['rebirth']))
        self.n.use(self.sid,self.item());self.assertEqual(self.s.get(self.sid)['neidan'][0],expected)
        self.assertEqual(Summons(Progression(self.path,'pill_owner',self.p.cid)).get(self.sid)['neidan'],[expected])

    def test_drop_with_remaining_experience_keeps_xp_and_loses_three_levels(self):
        self.n.use(self.sid,self.item());self.n.use(self.sid,self.xp_item())
        before=self.s.get(self.sid)['neidan'][0]
        self.assertGreater(before['experience'],0)
        self.call(755,self.sid,211)
        self.assertEqual(self.game.last_event['action'],'summon_neidan_request')
        self.assertEqual(self.s.get(self.sid)['neidan'],[])
        returned=self.item()
        with self.p.transaction(write=False) as db:
            item_row=dict(db.execute('SELECT * FROM legacy_neidan_items WHERE item_id=?',(returned,)).fetchone())
        self.assertEqual(item_row['level'],before['level']-3)
        self.assertEqual(item_row['experience'],before['experience'])
        items=self.p.snapshot()['items']
        self.call(755,self.sid,211)
        self.assertEqual(self.p.snapshot()['items'],items)
        self.call(807,self.sid,returned)
        expected=dict(before,level=before['level']-3,expnext=threshold(before['level']-3,before['rebirth']))
        self.assertEqual(self.s.get(self.sid)['neidan'],[expected])
        self.assertEqual(Summons(Progression(self.path,'pill_owner',self.p.cid)).get(self.sid)['neidan'],[expected])

    def test_drop_keeps_xp_even_above_the_lower_level_threshold(self):
        self.n.use(self.sid,self.item())
        experience=threshold(15,0)-1
        with self.p.transaction() as db:
            db.execute('UPDATE legacy_neidan SET level=15,experience=?,expnext=? WHERE summon_id=?',
                       (experience,threshold(15,0),self.sid))
        self.n.drop(self.sid,211);self.n.use(self.sid,self.item())
        row=self.s.get(self.sid)['neidan'][0]
        self.assertEqual((row['level'],row['experience'],row['expnext']),(12,experience,threshold(12,0)))

    def test_drop_low_level_boundary_and_full_bag_rollback(self):
        self.n.use(self.sid,self.item())
        for level in range(4):
            with self.p.transaction() as db:
                db.execute('UPDATE legacy_neidan SET level=?,experience=0,expnext=? WHERE summon_id=?',
                           (level,threshold(level,0),self.sid))
            self.n.drop(self.sid,211);self.n.use(self.sid,self.item())
            row=self.s.get(self.sid)['neidan'][0]
            self.assertEqual((row['level'],row['experience'],row['expnext']),(0,0,40))
        with self.p.transaction() as db:
            slots={r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.p.cid,))}
            db.executemany('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)',
                           [(self.p.cid,slot) for slot in range(1,25) if slot not in slots])
            db.execute('UPDATE legacy_neidan SET level=15,experience=0 WHERE summon_id=?',(self.sid,))
        before=self.s.get(self.sid);items=self.p.snapshot()['items']
        with self.assertRaises(ValueError):self.n.drop(self.sid,211)
        with self.assertRaises(ValueError):self.n.drop(self.sid+500,211)
        self.assertEqual(self.s.get(self.sid),before);self.assertEqual(self.p.snapshot()['items'],items)

    def test_returned_neidan_can_be_reused_from_an_open_extra_bag(self):
        self.n.use(self.sid,self.item())
        self.n.use(self.sid,self.xp_item())
        with self.p.transaction() as db:
            db.execute("INSERT INTO cosmetic_features VALUES(?, 'bag_item', 1, 0)",(self.p.cid,))
            occupied={row[0] for row in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.p.cid,))}
            db.executemany('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)',
                           [(self.p.cid,slot) for slot in range(1,25) if slot not in occupied])
        before=self.s.get(self.sid)['neidan'][0]
        saved=[dict(before,level=before['level']-3,expnext=threshold(before['level']-3,before['rebirth']))]
        self.call(755,self.sid,211)
        iid=self.item()
        bounced=next(row for row in self.p.snapshot()['items'] if row['id']==iid)
        self.assertEqual((bounced['page'],bounced['slot']),(16,1))
        self.assertEqual(self.s.get(self.sid)['neidan'],[])
        self.call(807,self.sid,iid)
        self.assertEqual(self.s.get(self.sid)['neidan'],saved)
        self.assertFalse(any(row['id']==iid for row in self.p.snapshot()['items']))

    def test_locked_and_expired_bag_items_are_preserved(self):
        iid=self.item()
        with self.p.transaction() as db:
            db.execute("INSERT INTO cosmetic_features VALUES(?, 'bag_temp', 1, 1)",(self.p.cid,))
            db.execute('UPDATE inventory SET page=15,slot=1 WHERE id=?',(iid,))
        before=self.p.snapshot()['items']
        with self.assertRaisesRegex(ValueError,'已开启的背包'):
            self.n.use(self.sid,iid)
        self.assertEqual(self.p.snapshot()['items'],before)
        with self.p.transaction() as db:
            db.execute("UPDATE cosmetic_features SET expires=0 WHERE character_id=? AND key='bag_temp'",(self.p.cid,))
            db.execute("INSERT INTO native_object_locks VALUES(?, 'item', ?, 1, 0)",(self.p.cid,iid))
        before=self.p.snapshot()['items']
        with self.assertRaisesRegex(ValueError,'加锁'):
            self.n.use(self.sid,iid)
        self.assertEqual(self.p.snapshot()['items'],before)
        self.assertEqual(self.s.get(self.sid)['neidan'],[])

    def test_victory_rewards_inner_pill_once(self):
        self.n.use(self.sid,self.item())
        pet=self.s.get(self.sid)
        self.s.settle(self.sid,'same-battle',pet['hp'],pet['mp'],True)
        first=self.s.get(self.sid)
        self.s.settle(self.sid,'same-battle',pet['hp'],pet['mp'],True)
        self.assertEqual(self.s.get(self.sid),first)
        self.assertEqual(first['neidan'][0]['experience'],25)
        self.assertEqual(first['neidan'][0]['energy'],5999)

    def test_battle_participation_costs_every_equipped_pill_without_proc(self):
        with self.p.transaction() as db:db.execute('UPDATE summons SET rebirth=2 WHERE id=?',(self.sid,))
        for kind in (211,212,213):self.n.use(self.sid,self.item(kind))
        resting=self.s.acquire(3197,'neidan:resting')
        with self.p.transaction() as db:
            db.execute('UPDATE legacy_neidan SET energy=1 WHERE summon_id=? AND kind=212',(self.sid,))
            db.execute('UPDATE legacy_neidan SET energy=0 WHERE summon_id=? AND kind=213',(self.sid,))
            db.execute('INSERT INTO legacy_neidan(summon_id,kind,energy) VALUES(?,214,6000)',(resting,))
        pet=self.s.get(self.sid)
        self.s.settle(self.sid,'no-proc-defeat',0,0,False)
        rows=self.s.get(self.sid)['neidan']
        self.assertEqual([r['energy'] for r in rows],[5999,0,0])
        self.assertEqual([r['experience'] for r in rows],[0,0,0])
        self.assertEqual(self.s.get(resting)['neidan'][0]['energy'],6000)
        self.s.settle(self.sid,'no-proc-defeat',pet['hp'],pet['mp'],True)
        self.assertEqual(self.s.get(self.sid)['neidan'],rows)
        self.s.settle(self.sid,'next-battle',0,0,False)
        self.assertEqual([r['energy'] for r in self.s.get(self.sid)['neidan']],[5998,0,0])
        self.assertFalse(effect(rows[1],pet)['enabled'])

    def test_settlement_failure_rolls_back_energy_experience_and_receipt(self):
        self.n.use(self.sid,self.item());before=self.s.get(self.sid)
        with patch.object(LegacyNeidan,'_save',side_effect=RuntimeError('injected persistence failure')):
            with self.assertRaises(RuntimeError):self.s.settle(self.sid,'retry-pill',0,0,True)
        self.assertEqual(self.s.get(self.sid),before)
        with self.p.transaction(write=False) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM summon_battle_rewards WHERE battle_id=?',('retry-pill',)).fetchone())
        self.s.settle(self.sid,'retry-pill',before['hp'],before['mp'],True)
        row=self.s.get(self.sid)['neidan'][0]
        self.assertEqual((row['experience'],row['energy']),(25,5999))

    def test_duplicate_feeding_clamps_energy_instead_of_setting_remaining_gap(self):
        self.n.use(self.sid,self.item());self.n.drop(self.sid,211)
        iid=self.item()
        with self.p.transaction() as db:
            db.execute('INSERT INTO legacy_neidan(summon_id,kind,energy) VALUES(?,211,5980)',(self.sid,))
        self.n.use(self.sid,iid)
        self.assertEqual(self.s.get(self.sid)['neidan'][0]['energy'],6000)

    def test_trained_duplicate_is_preserved_when_active_neidan_needs_energy(self):
        self.n.use(self.sid,self.item())
        self.n.use(self.sid,self.xp_item())
        with self.p.transaction() as db:db.execute('UPDATE legacy_neidan SET experience=0 WHERE summon_id=?',(self.sid,))
        self.call(755,self.sid,211)
        trained_item=self.item()
        with self.p.transaction() as db:
            other=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,6052,8,24,1)',
                             (self.p.cid,)).lastrowid
            db.execute('INSERT INTO legacy_neidan_items(item_id,kind,energy) VALUES(?,211,5800)',(other,))
        self.call(807,self.sid,other)
        before=self.s.get(self.sid)
        items=self.p.snapshot()['items']
        description=self.p.item_description(trained_item)
        self.call(807,self.sid,trained_item)
        self.assertIn('已培养的同名内丹',self.game.last_event['reason'])
        self.assertEqual(self.s.get(self.sid),before)
        self.assertEqual(self.p.snapshot()['items'],items)
        self.assertEqual(self.p.item_description(trained_item),description)

    def test_rebirth_requires_guardian_stage_and_resets_progress(self):
        self.n.use(self.sid,self.item())
        with self.p.transaction() as db:db.execute('UPDATE legacy_neidan SET level=100 WHERE summon_id=?',(self.sid,))
        with self.assertRaises(ValueError):self.n.reborn(self.sid,211)
        with self.p.transaction() as db:db.execute('UPDATE summons SET rebirth=1 WHERE id=?',(self.sid,))
        self.n.reborn(self.sid,211)
        row=self.s.get(self.sid)['neidan'][0]
        self.assertEqual((row['rebirth'],row['level'],row['experience'],row['expnext']),(1,0,0,45))

    def test_reopen_reconciles_equipped_and_bag_pills_once_and_refreshes_client(self):
        from native946_game_login import message_id
        from native946_neidan import installed, threshold as new_threshold, NewNeidan

        self.n.use(self.sid, self.item())
        bag_id = self.item(231)
        with self.p.transaction() as db:
            db.execute('UPDATE legacy_neidan SET level=102,experience=181784602,expnext=787453 WHERE summon_id=?',
                       (self.sid,))
            db.execute('UPDATE legacy_neidan_items SET rebirth=1,level=121,experience=90 WHERE item_id=?', (bag_id,))
            db.execute('INSERT INTO native_new_neidan(summon_id,kind,stage,level,experience,advance) '
                       'VALUES(?,236,2,142,123,3)', (self.sid,))
        expected_old = 181784602 + threshold(100, 0) + threshold(101, 0)
        expected_new = 123 + new_threshold(140, 2) + new_threshold(141, 2)
        self.game.progression = Progression(self.path, self.p.owner, self.p.cid)
        self.game.summons.progression = self.game.progression
        updates = self.game.sync_corrected_neidan_levels()
        self.assertEqual(len(updates), 1)
        self.assertTrue(updates[0].startswith(message_id(716)))
        self.assertEqual(updates[0], self.game.summons.neidan_packet(self.sid))
        self.assertEqual(self.game.sync_corrected_neidan_levels(), ())

        reopened = Progression(self.path, self.p.owner, self.p.cid)
        self.assertEqual(reopened.neidan_level_updates, ())
        with reopened.transaction() as db:
            NewNeidan(self.game.summons).settle(db, self.sid, True, 25)
            new = installed(db, self.sid)[0]
            self.assertEqual((new['level'], new['experience'], new['advance']), (140, expected_new, 3))
            legacy = db.execute('SELECT level,experience,expnext FROM legacy_neidan WHERE summon_id=?',
                                (self.sid,)).fetchone()
            bag = db.execute('SELECT level,experience,expnext FROM legacy_neidan_items WHERE item_id=?', (bag_id,)).fetchone()
        self.assertEqual(tuple(legacy), (100, expected_old, threshold(100, 0)))
        self.assertEqual(tuple(bag), (120, 90 + threshold(120, 1), threshold(120, 1)))

    def test_native_followup_uses_matching_cast_and_not_certain_trigger(self):
        self.n.use(self.sid,self.item());self.n.use(self.sid,self.xp_item())
        self.game.begin_battle();pet=self.game.battle['pet'];self.assertIsNotNone(pet)
        with patch('random.random',return_value=.999):self.assertEqual(self.game.neidan_followups(pet),[])
        hp=self.game.battle['enemy_hp']
        with patch('random.random',return_value=0):result=self.game.neidan_followups(pet)
        self.assertTrue(result);self.assertLess(self.game.battle['enemy_hp'],hp)
        r=MessageReader(result[1][3:])
        self.assertEqual(r.integer(),self.sid);self.assertEqual(r.integer(),ENEMY_ID)
        self.assertEqual(r.take(1),b'\x01');self.assertEqual(r.integer(),ENEMY_ID);self.assertEqual(r.integer(),211)

    def test_haoran_feedback_uses_target_mana_not_attacker_mana(self):
        self.n.use(self.sid,self.item(231));self.game.begin_battle()
        pet=self.game.battle['pet'];pet['mpmax']=9000
        fixed=dict(enabled=True,chance=100,ratio=50)
        with patch('native946_neidan_legacy.effect',return_value=fixed),patch('random.random',return_value=0):
            for target_mana,own_mana in [(200,9000),(200,18000),(400,18000)]:
                # Set target current and maximum equally: this checks ownership
                # without deciding the still-unverified current/max distinction.
                self.game.battle.update(enemy_hp=10000,enemy_mp=target_mana,enemy_mpmax=target_mana)
                pet['mpmax']=own_mana
                self.game.neidan_followups(pet)
                self.assertEqual(10000-self.game.battle['enemy_hp'],target_mana//2)
        self.assertEqual(self.s.get(self.sid)['neidan'][0]['energy'],6000)


if __name__=='__main__':unittest.main()
