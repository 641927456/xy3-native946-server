"""Higher book slots, true combat effects, native display and save migration."""
from unittest.mock import patch
from random import Random
import unittest
from native946_skillbooks import SkillBooks,BOOKS,raw_list,effects,schema
from native946_book_combat import roll,counter_ready
from native946_codec import packet,SCHEMAS
from native946_login import MessageReader
from native946_summons import Summons
import test_native946_skillbooks as basic
from test_native946_skillbooks import decode_record


class Fixed:
    def __init__(self,*values):self.values=iter(values)
    def randint(self,*bounds):return next(self.values)


class PhysicalFormulaTests(unittest.TestCase):
    def test_exact_leveled_coefficients_and_native_critical_effect_ids(self):
        rows=[dict(skill=305,level=100),dict(skill=306,level=100),dict(skill=307,level=100),
              dict(skill=308,level=100),dict(skill=309,level=100)]
        self.assertEqual(effects(rows),dict(book_dodge=114,book_hit=172,book_fatal=10,book_cruel=17,
                                           book_counter=20,book_counter_count=1,book_ignore_counter=68))
        attacker=dict(attack=100,book_fatal=10,book_cruel=17,learned_books=[rows[2]])
        target=dict(hp=1000,defense=20)
        self.assertEqual(roll(attacker,target,Fixed(1,10)),dict(damage=230,miss=False,effect=2))
        self.assertEqual(roll(attacker,target,Fixed(1,11,17)),dict(damage=130,miss=False,effect=1))
        self.assertEqual(roll(attacker,target,Fixed(1,11,18)),dict(damage=80,miss=False,effect=0))
        self.assertEqual(roll(attacker,target,Fixed(91)),dict(damage=0,miss=True,effect=0))
        self.assertEqual(roll(attacker,target,Fixed(1,10),defend=True)['damage'],115)

    def test_dodge_hit_boundaries_counter_limit_and_no_book_baseline(self):
        actor=dict(attack=20,book_hit=100,learned_books=[dict(skill=306)])
        target=dict(hp=500,defense=5,book_dodge=190,learned_books=[dict(skill=305)])
        self.assertTrue(roll(actor,target,Fixed(1))['miss'])
        actor['book_hit']=200;target['book_dodge']=0
        self.assertFalse(roll(actor,target,Fixed(100))['miss'])
        pet=dict(hp=1,book_counter=20,book_counter_count=1)
        self.assertTrue(counter_ready(pet,0,Fixed(20)))
        self.assertFalse(counter_ready(pet,0,Fixed(21)))
        self.assertFalse(counter_ready(pet,1,Fixed()))
        pet['hp']=0;self.assertFalse(counter_ready(pet,0,Fixed()))
        self.assertEqual(roll(dict(attack=20),dict(hp=100,defense=5),Fixed()),dict(damage=15,miss=False,effect=0))


class PhysicalBookTests(unittest.TestCase):
    setUp=basic.BasicBookTests.setUp;item=basic.BasicBookTests.item;send=basic.BasicBookTests.send
    book_records=basic.BasicBookTests.book_records

    def stage(self,level=100,rebirth=0):
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=?,rebirth=?,experience=1000000 WHERE id=?',(level,rebirth,self.sid))

    def install(self,item):
        self.send(807,self.sid,self.item(item))
        self.assertEqual(self.g.last_event['action'],'progression_changed')
        skill=BOOKS[item]['skill']
        with self.p.transaction() as db:
            db.execute('UPDATE native_guardian_books SET level=100 WHERE summon_id=? AND skill=?',(self.sid,skill))
        return skill

    def test_each_higher_book_has_its_native_fields_and_real_saved_growth(self):
        self.stage()
        for iid in (6219,6220,6221,6222,6223):
            sid=self.install(iid);pet=self.s.get(self.sid)
            entry=self.book_records()[0]
            self.assertEqual((entry['stunt_id'],entry['position'],entry['grade']),(sid,8,100))
            self.assertEqual(len(entry['desc'][1:-1].split(',')),3 if sid==306 else 2)
            r=MessageReader(self.s.resistance_packet(self.sid)[3:]);self.assertEqual(r.integer(),self.sid)
            values=decode_record(r,'summon_resist');r.finish()
            fields={305:('DodValue',114),306:('AttValue',172),307:('FatalRate',100),
                    308:('CruelRate',170),309:('FanjiRate',200)}
            name,value=fields[sid];self.assertEqual(values[name],value)
            self.send(756,self.sid,sid);self.assertEqual(self.book_records(),[])

    def test_native_tier_slots_and_selected_experience_do_not_train_all_skills(self):
        self.stage(140,2)
        for iid in (6214,6219,6220,6221,6222):self.send(807,self.sid,self.item(iid))
        records=self.book_records();self.assertEqual([r['position'] for r in records],[7,8,9,10,11])
        before=self.s.get(self.sid)['learned_books']
        self.send(754,self.sid,305)
        after=self.s.get(self.sid)['learned_books']
        self.assertGreater(next(r['level'] for r in after if r['skill']==305),0)
        self.assertEqual([r for r in before if r['skill']!=305],[r for r in after if r['skill']!=305])
        extra=self.item(6223);saved=self.p.snapshot();self.send(807,self.sid,extra);self.assertEqual(self.p.snapshot(),saved)

    def test_tier_one_cannot_exceed_guardian_level_and_capped_conversion_does_not_charge(self):
        self.stage(100);self.install(6221)
        before=self.s.get(self.sid);self.send(754,self.sid,307);self.assertEqual(self.s.get(self.sid),before)
        with self.p.transaction() as db:db.execute('UPDATE native_guardian_books SET level=99 WHERE summon_id=?',(self.sid,))
        self.send(754,self.sid,307);self.assertEqual(self.book_records()[0]['grade'],100)

    def test_training_battle_sends_native_fatal_cruel_and_miss_packets(self):
        self.stage();self.install(6221)
        for outcome,expected in [(dict(damage=90,miss=False,effect=2),2162),
                                 (dict(damage=90,miss=False,effect=1),2162),
                                 (dict(damage=0,miss=True,effect=0),2144)]:
            self.g.battle=None;self.g.begin_battle()
            cid=self.g.character['id'];pet=self.g.battle['pet'];old=self.g.battle['enemy_hp']
            self.g.battle['orders']={cid:dict(defend=True,skill=10,damage=0,mana=0),
                                    pet['id']:dict(defend=False,skill=10,damage=pet['attack'],mana=0)}
            with patch('native946_book_combat.roll',return_value=outcome):
                replies=self.g.resolve_round()
            self.assertEqual(old-self.g.battle['enemy_hp'],outcome['damage'])
            matches=[p for p in replies if p[:3]==packet(expected,[],[])]
            self.assertTrue(matches)
            if expected==2162:
                r=MessageReader(matches[0][3:]);r.integer();self.assertEqual(r.integer(),outcome['effect'])

    def test_training_counter_uses_original_attack_and_survives_cold_load(self):
        self.stage();self.install(6223);self.g.begin_battle();self.g.battle['round']=2
        cid=self.g.character['id'];pet=self.g.battle['pet'];old=self.g.battle['enemy_hp']
        self.g.battle['orders']={cid:dict(defend=True,skill=10,damage=0,mana=0),
                                pet['id']:dict(defend=True,skill=10,damage=0,mana=0)}
        with patch('native946_book_combat.counter_ready',return_value=True),patch('native946_book_combat.roll',return_value=dict(damage=15,miss=False,effect=0)):
            replies=self.g.resolve_round()
        self.assertEqual(old-self.g.battle['enemy_hp'],15)
        self.assertTrue(any(p[:3]==packet(2157,[],[]) for p in replies))
        self.assertEqual(Summons(self.p).get(self.sid)['learned_books'],self.s.get(self.sid)['learned_books'])

    def test_shared_battle_effects_and_misses_use_same_outcomes(self):
        from native946_wilderness import WildBattle,DATA
        self.stage();self.install(6221)
        for outcome in [dict(damage=90,miss=False,effect=2),dict(damage=0,miss=True,effect=0)]:
            battle=WildBattle(self.g,[self.g],DATA['zones'][0])
            target=battle.enemies()[0];target['hp']=target['hpmax']=10000
            for enemy in battle.enemies():enemy['attack']=1
            pet=battle.current_pet(self.p.cid);old=target['hp']
            battle.state['orders']={self.p.cid:dict(kind='defend'),pet['id']:dict(kind='attack',target=target['id'],skill=10,mana=0)}
            original=roll
            def resolved(a,b,rng,**kw):return outcome if a['id']==pet['id'] else original(a,b,rng,**kw)
            self.g.party_service.drain(self.g)
            with patch('native946_book_combat.roll',side_effect=resolved),patch('native946_role_battle.Runtime.pet_passives',return_value=None) as followup:
                battle.resolve()
            self.assertEqual(old-battle.state['fighters'][target['id']]['hp'],outcome['damage'])
            self.assertEqual(followup.call_count,0 if outcome['miss'] else 1)
            packets=self.g.party_service.drain(self.g);expected=2144 if outcome['miss'] else 2162
            self.assertTrue(any(p[:3]==packet(expected,[],[]) for p in packets))
            battle.finish(False)

    def test_shared_counter_is_limited_once_per_round(self):
        from native946_wilderness import WildBattle,DATA
        self.stage();self.install(6223);battle=WildBattle(self.g,[self.g],DATA['zones'][0])
        pet=battle.current_pet(self.p.cid);target=battle.enemies()[0];target['hp']=10000
        used={}
        with patch('native946_book_combat.counter_ready',side_effect=lambda p,n,r,**kwargs:n<1),patch('native946_book_combat.roll',return_value=dict(damage=15,miss=False,effect=0)):
            first=battle.counter(pet,target,used);second=battle.counter(pet,target,used)
        self.assertTrue(first);self.assertEqual(second,[]);self.assertEqual(used[pet['id']],1)
        self.assertEqual(target['hp'],9985)

    def test_v1_migration_preserves_saved_skill_and_is_idempotent(self):
        with self.p.transaction() as db:
            db.execute('DROP TABLE native_guardian_books')
            db.execute('CREATE TABLE native_guardian_books(summon_id INTEGER,skill INTEGER,position INTEGER,level INTEGER,rebirth INTEGER,experience INTEGER,expnext INTEGER,PRIMARY KEY(summon_id,skill))')
            db.execute('INSERT INTO native_guardian_books VALUES(?,300,7,12,0,17,999)',(self.sid,))
            schema(db);before=raw_list(db,self.sid);schema(db);self.assertEqual(raw_list(db,self.sid),before)
            self.assertEqual(before,[dict(summon_id=self.sid,skill=300,position=7,level=12,rebirth=0,experience=17,
                                          expnext=999,tier=0,ascension=0,sanctification=0)])

    def test_inventory_version_change_rebuilds_once_and_rebinds_guardian(self):
        import native946_inventory as inventory
        from native946_progression import Progression
        with patch.object(inventory,'WIRE_VERSION',inventory.WIRE_VERSION+1),patch('native946_progression.Progression',wraps=Progression) as constructor:
            self.g.ensure_progression();current=self.g.progression
            self.g.ensure_progression()
            self.assertEqual(constructor.call_count,1)
            self.assertIs(self.g.progression,current)
            self.assertIs(self.g.summons.progression,current)


if __name__=='__main__':unittest.main()
