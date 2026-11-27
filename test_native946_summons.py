from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import number, packet
from native946_gameplay import NativeGameplay, ENEMY_ID
from native946_login import MessageReader, string_field
from native946_progression import Progression
from native946_summons import Summons


class SummonTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'local.sqlite3'
        LocalAccounts(self.path).register({'username':'owner946','password':'123456789'})
        self.characters = Characters(self.path)
        self.character = self.characters.create('owner946','守护测试',702)
        self.game = NativeGameplay(self.character, self.characters, lambda:())
        self.s = self.game.summons

    def call(self, mid, *args):
        return self.game.receive(mid, MessageReader(b''.join(number(n) for n in args)))

    def test_starter_claim_is_idempotent_and_persistent(self):
        sid = self.s.claim_starter()
        self.assertGreater(sid,100000)
        self.assertLessEqual(sid,199999)
        self.assertEqual(self.s.claim_starter(),sid)
        loaded = Summons(Progression(self.path,'owner946',self.character['id']))
        self.assertEqual(loaded.snapshot(),self.s.snapshot())
        self.assertEqual(loaded.active()['id'],sid)

    def test_native_list_inserts_stats_before_enabling_fight(self):
        from native946_codec import SCHEMAS
        from native946_guardian_equipment import equipment_page
        sid=self.s.claim_starter();packets=self.s.sync()
        self.assertEqual(packets[0],packet(711,['number','number'],[sid,equipment_page(sid)]))
        r=MessageReader(packets[1][3:]);self.assertEqual(r.take(1),b'\x01');values={}
        for key,kind in SCHEMAS['summon_list']:
            values[key]=r.string() if kind=='string' else r.take(1)[0] if kind=='byte' else r.integer()
        r.finish()
        self.assertEqual((values['Id'],values['Fight'],values['Mark']),(sid,0,b'{}'))
        self.assertEqual(packets[2][:3],b'\x00\xc2\x02')  # c706: complete stats.
        self.assertIn(packet(718,['number','number'],[sid,1]),packets[2:])

    def test_unlock_slot_id_migration_keeps_rewards_neidan_and_appearance(self):
        from native946_neidan_legacy import LegacyNeidan
        from native946_mall_guardians import GuardianAppearance
        sid=self.s.claim_starter();self.s.settle(sid,'saved',99,20,True)
        pills=LegacyNeidan(self.s);pills.grant_test_kit()
        with self.game.progression.transaction(write=False) as db:
            item=db.execute('SELECT item_id FROM legacy_neidan_items n JOIN inventory i ON i.id=n.item_id '
                            'WHERE i.character_id=? AND n.kind=211',(self.game.progression.cid,)).fetchone()[0]
        pills.use(sid,item)
        before=self.s.get(sid)
        with self.game.progression.transaction() as db:
            GuardianAppearance(self.game)._save(db,sid,color1=123)
            db.execute('PRAGMA defer_foreign_keys=ON')
            db.execute('UPDATE summons SET id=10001 WHERE id=?',(sid,))
            for table in ['summon_battle_rewards','legacy_neidan','cosmetic_guardian_appearance']:
                db.execute('UPDATE '+table+' SET summon_id=10001 WHERE summon_id=?',(sid,))
        reloaded=Summons(self.game.progression);row=reloaded.snapshot()[0]
        self.assertGreater(row['id'],100000)
        self.assertEqual(row['neidan'],before['neidan'])
        self.assertEqual(row['color1'],123)
        with self.game.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        reloaded.settle(row['id'],'saved',0,0,True)
        self.assertEqual((reloaded.get(row['id'])['hp'],reloaded.get(row['id'])['experience']),(99,25))
        self.assertEqual(Summons(self.game.progression).snapshot(),reloaded.snapshot())

    def test_migration_skips_valid_guardian_ids_and_character_ids(self):
        sid=self.s.claim_starter()
        valid=self.s.acquire(3197,'keep:valid')
        with self.game.progression.transaction() as db:
            db.execute('UPDATE summons SET id=100000 WHERE id=?',(sid,))
            # Leave one valid guardian in place and reserve the next slot for a hero.
            other=self.characters.create('owner946','编号避让',702)
            db.execute('UPDATE characters SET id=100001 WHERE id=?',(other['id'],))
        valid_before=self.s.get(valid)
        reloaded=Summons(self.game.progression)
        self.assertEqual(reloaded.get(valid),valid_before)
        starter=next(s for s in reloaded.snapshot() if s['grant_key']=='starter')
        self.assertEqual(starter['id'],100003)
        self.assertEqual(self.s.acquire(3197,'after:migration'),100004)

    def test_follow_uses_native_follower_and_respects_hide(self):
        sid=self.s.claim_starter();self.s.status(sid,3)
        replies=self.game.sync_summon_scene()
        self.assertEqual(replies[-1],packet(1605,['number']*3,[sid,self.character['id'],96]))
        self.s.status(sid,4)
        self.assertEqual(self.game.sync_summon_scene(),(packet(1604,['number'],[sid]),))

    def test_foreign_character_cannot_read_or_mutate(self):
        sid = self.s.claim_starter()
        other = self.characters.create('owner946','另一角色',702)
        foreign = Summons(Progression(self.path,'owner946',other['id']))
        for action in [lambda:foreign.get(sid),lambda:foreign.rename(sid,'偷改'),
                       lambda:foreign.status(sid,1),lambda:foreign.allocate(sid,[1,0,0,0]),
                       lambda:foreign.level_up(sid)]:
            with self.assertRaises(ValueError):action()
        self.assertEqual(self.s.get(sid)['name'],'猴儿精')

    def test_rest_and_fight_persist(self):
        sid=self.s.claim_starter()
        self.s.status(sid,2);self.assertIsNone(self.s.active())
        self.s.status(sid,1);self.assertEqual(self.s.active()['id'],sid)
        self.s.status(sid,3);self.assertEqual(self.s.get(sid)['show'],1)
        self.s.status(sid,4);self.assertEqual(self.s.get(sid)['show'],0)
        with self.assertRaises(ValueError):self.s.status(sid,5)

    def test_native_release_removes_active_guardian_and_frees_slot(self):
        from native946_guardian_chamber import carried_count
        from native946_reserve_food import sync as reserve_packet

        sid = self.s.acquire(3197, 'release:target', level=10)
        self.s.settle(sid, 'before-release', 99, 20, True)
        self.s.status(sid, 3)
        self.game.sync_summon_scene()
        pet = self.s.list_record(self.s.get(sid))
        keep = [self.s.acquire(3197, f'release:keep:{i}') for i in range(self.s.carry_limit() - 1)]
        p = self.game.progression
        with self.assertRaisesRegex(ValueError, '携带栏已满'):
            self.s.acquire(3197, 'release:replacement')
        self.assertEqual(p.attribute_values()['SummonScore'], self.s.get(sid)['score'])

        reply = self.call(750, sid, 5)
        self.assertEqual(self.game.last_event['action'], 'summon_released')
        self.assertEqual(reply[:3], (packet(718, ['number', 'number'], [sid, 0]),
                                    packet(719, ['number', 'number'], [sid, 0]),
                                    packet(714, ['summon_list', 'number'], [pet, 2])))
        self.assertIn(packet(1604, ['number'], [sid]), reply)
        self.assertIn(reserve_packet(self.game, None), reply)
        self.assertIn(packet(695, ['number', 'number'], [88, max(self.s.get(i)['score'] for i in keep)]), reply)
        self.assertIsNone(self.s.active())
        with self.assertRaisesRegex(ValueError, '不属于'):
            self.s.get(sid)
        self.call(750, sid, 5)
        self.assertEqual(self.game.last_event['action'], 'action_rejected')

        loaded = Summons(Progression(self.path, 'owner946', p.cid))
        self.assertEqual([row['id'] for row in loaded.snapshot()], keep)
        self.assertEqual(loaded.acquire(3197, 'release:target'), sid)
        replacement = loaded.acquire(3197, 'release:replacement')
        self.assertNotEqual(replacement, sid)
        with p.transaction(write=False) as db:
            self.assertEqual(carried_count(db, p.cid), loaded.carry_limit())
            self.assertEqual(db.execute('SELECT count(*) FROM native_guardian_releases').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT summon_id FROM summon_battle_rewards').fetchone()[0], sid)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_native_release_rejects_unavailable_guardians_without_changes(self):
        from native946_guardian_equipment import equipment_page

        sid = self.s.claim_starter()
        protected = self.s.acquire(3405, 'release:protected')
        p = self.game.progression
        other = self.characters.create('owner946', '他人守护', 702)
        foreign = Summons(Progression(self.path, 'owner946', other['id'])).claim_starter()
        with p.transaction() as db:
            db.execute("INSERT INTO native_object_locks VALUES(?,'summon',?,1,0)", (p.cid, sid))
        for target, reason in ((sid, '加锁'), (foreign, '不属于'), (protected, '无法放生')):
            self.call(750, target, 5)
            self.assertIn(reason, self.game.last_event['reason'])

        with p.transaction() as db:
            db.execute("DELETE FROM native_object_locks WHERE kind='summon' AND object_id=?", (sid,))
            item_id = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                                 (p.cid, 42050, equipment_page(sid), 1)).lastrowid
            db.execute("INSERT INTO native_guardian_equipment VALUES(?,?,'artifact:1')", (item_id, sid))
        self.call(750, sid, 5)
        self.assertIn('先卸下', self.game.last_event['reason'])
        with p.transaction() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM inventory WHERE id=?', (item_id,)).fetchone())
            db.execute('DELETE FROM native_guardian_equipment WHERE item_id=?', (item_id,))
            page, slot = p.free_bag_slot()
            db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?', (page, slot, item_id))
            db.execute('INSERT INTO native_guardian_shops VALUES(?,1,0)', (p.cid,))
            db.execute('INSERT INTO native_guardian_shop_stock(owner,summon_id,price) VALUES(?,?,1)', (p.cid, sid))
        self.call(750, sid, 5)
        self.assertIn('不属于', self.game.last_event['reason'])
        with p.transaction() as db:
            db.execute('DELETE FROM native_guardian_shop_stock WHERE summon_id=?', (sid,))
            db.execute("INSERT INTO native_guardian_shop_sales VALUES(1,?,?,?,'sale:history',1,1)", (other['id'], p.cid, sid))
        self.call(750, sid, 5)
        self.assertEqual(self.game.last_event['action'], 'summon_released')
        self.assertEqual([row['id'] for row in self.s.snapshot()], [protected])
        with p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT summon_id FROM native_guardian_shop_sales').fetchone()[0], sid)

    def test_guardian_rating_uses_highest_carried_and_refreshes_with_skill_training(self):
        from native946_innate_training import schema
        from native946_neidan import threshold

        p = self.game.progression
        self.assertEqual(p.attribute_values()['SummonScore'], 0)
        lower = self.s.claim_starter()
        higher = self.s.acquire(3200, 'score:stronger', level=10)
        before = self.s.get(higher)['score']
        self.assertGreater(before, self.s.get(lower)['score'])
        self.assertEqual(self.s.active()['id'], lower)
        self.assertEqual(p.attribute_values()['SummonScore'], before)
        self.assertIn(packet(695, ['number', 'number'], [88, before]), self.call(750, lower, 2))

        with p.transaction() as db:
            schema(db)
            db.execute('UPDATE summons SET experience=500 WHERE id=?', (higher,))
            db.execute('INSERT INTO native_innate_training(summon_id,skill,level,experience) VALUES(?,?,?,?)',
                       (higher, 307, 9, threshold(9, 0) - 100))
        reply = self.call(754, higher, 307)
        after = before + 65
        self.assertEqual(self.s.get(higher)['score'], after)
        self.assertIn(packet(695, ['number', 'number'], [88, after]), reply)
        loaded = Summons(Progression(self.path, 'owner946', self.character['id']))
        self.assertEqual(loaded.get(higher)['score'], after)
        self.assertEqual(loaded.progression.attribute_values()['SummonScore'], after)

    def test_name_and_allocation_are_saved_and_bounded(self):
        sid=self.s.claim_starter()
        self.s.rename(sid,'小猴123')
        self.s.allocate(sid,[1,0,3,0]);s=self.s.get(sid)
        self.assertEqual((s['name'],s['cor'],s['strength'],s['points']),('小猴123',2,4,0))
        for values in [[1,0,0,0],[-1,0,0,0],[1.2,0,0,0]]:
            with self.assertRaises(ValueError):self.s.allocate(sid,values)
        with self.assertRaises(ValueError):self.s.rename(sid,'#R注入')
        self.assertEqual(self.s.get(sid),s)

    def test_settlement_deduplicates_experience_and_vitals(self):
        sid=self.s.claim_starter()
        self.s.settle(sid,'battle1',50,20,True)
        self.s.settle(sid,'battle1',0,0,True)
        s=self.s.get(sid)
        self.assertEqual((s['experience'],s['hp'],s['mp'],s['amity']),(25,50,20,1))

    def test_battle_experience_levels_at_threshold_and_rest_caps_vitals(self):
        sid=self.s.claim_starter()
        with self.assertRaises(ValueError):self.s.level_up(sid)
        self.s.settle(sid,'one',1,1,True,experience=39)
        self.assertEqual((self.s.get(sid)['level'],self.s.get(sid)['experience']),(1,39))
        self.s.settle(sid,'two',1,1,True,experience=1)
        self.s.settle(sid,'two',1,1,True,experience=1)
        s=self.s.get(sid)
        self.assertEqual((s['level'],s['experience'],s['points']),(2,0,8))
        self.assertEqual(tuple(s[k] for k in ('cor','mag','strength','speed')),(2,2,2,2))
        self.assertEqual((s['hp'],s['mp']),(s['hpmax'],s['mpmax']))
        with self.assertRaises(ValueError):self.s.level_up(sid)
        self.s.set_vitals(sid,999999,-10);s=self.s.get(sid)
        self.assertEqual((s['hp'],s['mp']),(s['hpmax'],0))
        self.s.rest();self.assertEqual(self.s.get(sid)['mp'],s['mpmax'])

    def test_saved_experience_waits_at_the_cap_until_the_hero_levels(self):
        from native946_progression import level_up_cost

        p=self.game.progression
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=31,experience=? WHERE id=?',(level_up_cost(31,32),p.cid))
        with self.assertRaisesRegex(ValueError,'上限'):
            self.s.acquire(3374,'over-the-cap',level=42)
        sid=self.s.acquire(3374,'stored-experience',level=41)
        with p.transaction() as db:
            db.execute('UPDATE summons SET experience=499984025,points=0 WHERE id=?',(sid,))
        self.game=NativeGameplay(self.character,self.characters,lambda:())
        loaded=self.game.summons
        pet=loaded.get(sid)
        self.assertEqual((pet['level'],pet['experience'],pet['points']),(41,499984025,0))
        self.assertEqual(self.game.progression.snapshot()['level'],31)
        self.assertFalse(loaded.reconcile_levels())
        with self.assertRaisesRegex(ValueError,'41级'):
            loaded.level_up(sid)
        loaded.settle(sid,'at-the-cap',pet['hp'],pet['mp'],True,experience=375)
        pet=loaded.get(sid)
        self.assertEqual((pet['level'],pet['experience'],pet['points']),(41,499984400,0))
        reply=self.call(748)
        pet=loaded.get(sid)
        self.assertEqual((pet['level'],pet['experience'],pet['points']),(42,499982760,4))
        self.assertIn(packet(706,['number','summon'],[sid,loaded.info_record(pet)]),reply)

    def test_guardian_stage_never_bypasses_the_owner_level_limit(self):
        sid=self.s.claim_starter()
        p=self.game.progression
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=31 WHERE id=?',(p.cid,))
        for rebirth,ascension,sanctification in ((0,0,0),(1,0,0),(2,0,0),(2,1,0),(2,1,1)):
            with self.subTest(rebirth=rebirth,ascension=ascension,sanctification=sanctification):
                with self.game.progression.transaction() as db:
                    db.execute('UPDATE summons SET level=40,rebirth=?,ascension=?,sanctification=?,experience=10000 WHERE id=?',
                               (rebirth,ascension,sanctification,sid))
                reply=self.call(775,sid)
                pet=self.s.get(sid)
                self.assertEqual((pet['level'],pet['experience']),(41,8400))
                self.assertIn(packet(706,['number','summon'],[sid,self.s.info_record(pet)]),reply)
                self.assertFalse(self.s.reconcile_levels())
                with self.assertRaisesRegex(ValueError,'41级'):
                    self.s.level_up(sid)
                self.assertEqual(self.s.get(sid),pet)

    def test_guardian_battle_and_manual_leveling_stop_at_each_stage_cap(self):
        sid = self.s.claim_starter()
        p = self.game.progression
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=180 WHERE id=?', (p.cid,))
        with self.assertRaisesRegex(ValueError, '上限'):
            self.s.acquire(3374, 'over-stage-cap', level=101)
        for rebirth, ascension, sanctification, cap in (
                (0, 0, 0, 100), (1, 0, 0, 120), (2, 0, 0, 140), (2, 1, 0, 180), (2, 1, 1, 185)):
            with self.subTest(cap=cap):
                with p.transaction() as db:
                    db.execute('UPDATE summons SET level=?,rebirth=?,ascension=?,sanctification=?,experience=0 WHERE id=?',
                               (cap - 1, rebirth, ascension, sanctification, sid))
                self.s.settle(sid, f'stage-cap-{cap}', 1, 1, True, experience=20000)
                pet = self.s.get(sid)
                self.assertEqual((pet['level'], pet['experience']), (cap, 20000 - 40 * (cap - 1)))
                with self.assertRaisesRegex(ValueError, f'{cap}级'):
                    self.s.level_up(sid)
                self.assertFalse(self.s.reconcile_levels())
                self.assertEqual(self.s.get(sid), pet)

    def test_legacy_overleveled_guardian_is_repaired_on_load_once(self):
        sid = self.s.claim_starter()
        p = self.game.progression
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=100 WHERE id=?', (p.cid,))
            db.execute('UPDATE summons SET level=110,experience=70,cor=545,mag=110,strength=110,speed=110,'
                       'points=5,hp=9999,mp=9999 WHERE id=?', (sid,))
        loaded = NativeGameplay(self.character, self.characters, lambda:()).summons
        with p.transaction(write=False) as db:
            saved = db.execute('SELECT level,experience,cor,mag,strength,speed,points,hp,mp '
                               'FROM summons WHERE id=?', (sid,)).fetchone()
        self.assertEqual(tuple(saved[:7]), (100, 41870, 100, 100, 100, 100, 400))
        pet = loaded.get(sid)
        self.assertEqual(tuple(saved[7:]), (pet['hpmax'], pet['mpmax']))
        self.assertFalse(loaded.reconcile_levels())
        self.assertEqual(loaded.get(sid), pet)

    def test_native_fighter_and_pet_binding_are_emitted(self):
        sid=self.s.claim_starter();packets=self.game.begin_battle()
        self.assertIn(packet(4206,['number'],[sid]),packets)
        fighter=next(p for p in packets if p[:3]==b'\x00\x63\x08' and p[3:].startswith(number(sid)))
        r=MessageReader(fighter[3:]);self.assertEqual([r.integer() for _ in range(3)],[sid,3,3197])
        self.assertEqual(r.string().decode('gb18030'),'猴儿精')
        self.assertEqual([r.integer() for _ in range(3)],[4,6,1])

    def test_both_orders_are_required_and_duplicate_is_noop(self):
        sid=self.s.claim_starter();self.game.begin_battle()
        before=self.game.battle.copy()
        self.call(2185,9,6100,ENEMY_ID)
        self.assertEqual(self.game.battle['enemy_hp'],before['enemy_hp'])
        self.assertEqual(self.game.battle['mp'],before['mp'])
        self.call(2185,9,6100,ENEMY_ID)
        self.call(2181,3,ENEMY_ID)
        self.assertEqual(self.game.battle['phase'],'drawing')
        remaining=self.game.battle['enemy_hp'];mp=self.game.battle['mp']
        self.call(2181,3,ENEMY_ID)
        self.assertEqual((self.game.battle['enemy_hp'],self.game.battle['mp']),(remaining,mp))

    def test_pet_first_order_resolves_once_in_initiative_order(self):
        self.game.progression.allocate([0,0,0,4])
        sid=self.s.claim_starter();self.game.begin_battle()
        self.call(2181,3,ENEMY_ID)
        packets=self.call(2177,3,ENEMY_ID)
        actors=[]
        for p in packets:
            if p[:3]==b'\x00\x6d\x08':actors.append(MessageReader(p[3:]).integer())
        self.assertGreater(self.game.battle['pet']['initiative'],self.game.progression.snapshot()['initiative'])
        self.assertEqual(actors[:2],[sid,self.character['id']])

    def test_pet_defence_and_damage_persist_after_battle(self):
        sid=self.s.claim_starter();self.game.begin_battle()
        self.call(2178,1);self.call(2182,1);self.call(2184)
        self.call(2178,1);self.call(2182,1)
        self.assertLess(self.game.battle['pet']['hp'],self.s.get(sid)['hpmax'])
        hp=self.game.battle['pet']['hp']
        self.call(2184);self.call(2178,2)
        self.assertEqual(self.s.get(sid)['hp'],hp)

    def test_native_mutations_are_atomic_on_trailing_bytes(self):
        sid=self.s.claim_starter();before=self.s.get(sid)
        self.game.receive(764,MessageReader(b''.join(number(n) for n in [sid,1,0,0,0])+b'x'))
        self.assertEqual(self.s.get(sid),before)

    def test_native_pet_queries_and_battle_mutation_rejection(self):
        sid=self.s.claim_starter()
        for mid in [727,732,733,734,760,761,762]:self.assertIsNotNone(self.call(mid,sid))
        self.game.begin_battle();self.call(750,sid,2)
        self.assertEqual(self.s.active()['id'],sid)
        self.assertEqual(self.game.last_event['action'],'action_rejected')
        self.call(750,sid,5)
        self.assertIn('战斗中',self.game.last_event['reason'])
        self.assertEqual(self.s.active()['id'],sid)

    def test_dead_or_resting_pet_does_not_block_hero(self):
        sid=self.s.claim_starter();self.s.set_vitals(sid,0,0)
        self.game.begin_battle();self.assertIsNone(self.game.battle['pet'])
        self.call(2177,3,ENEMY_ID);self.assertEqual(self.game.battle['phase'],'drawing')

    def test_movement_does_not_replace_local_click_path(self):
        from struct import pack
        x,y=self.character['x'],self.character['y']
        self.assertIsNone(self.game.receive(1643,MessageReader(number(4)+pack('<HH',x,y)+string_field(b''))))


if __name__=='__main__':unittest.main()
