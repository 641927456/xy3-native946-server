"""Native high-level bounds and real rare-item fulfillment on disposable saves."""
from copy import deepcopy
from pathlib import Path
from random import Random
from unittest.mock import patch
import json,unittest

from native946_equipment import DATA,EXTENDED,persist,roll
from native946_crafting import Crafting,MAKE,REFRESH
from native946_progression import Progression
from native946_workshops import DATA as WORKSHOPS
from native946_npc_quests import Quests,deliver
import test_native946_reference_crafting as craft_fixture
import test_native946_npc_quests as quest_fixture

class HighProductionTests(unittest.TestCase):
    setUp=craft_fixture.ReferenceProductionTests.setUp
    send=craft_fixture.ReferenceProductionTests.send;open=craft_fixture.ReferenceProductionTests.open
    rows=craft_fixture.ReferenceProductionTests.rows

    def prepare_high(self,medicine=6110):
        p=self.game.progression;recipes=WORKSHOPS['recipes']
        recipe=next(r for r in recipes if r['equip_grade']==15 and r['result_name']=='楚天太阿')
        previous=next(r for r in recipes if r['equip_grade']==14 and r['result_name']=='玄霜冰魄')
        source=DATA['crafting']['profiles'][str(recipes.index(previous))]['力量']
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=180,gold=1000000,role_id=111,race=1,sex=1 WHERE id=?',(p.cid,))
            db.execute('UPDATE progression SET cor=100,mag=100,strength=600,speed=100 WHERE character_id=?',(p.cid,))
            db.execute('UPDATE cultivation_state SET ascension=1 WHERE character_id=?',(p.cid,))
            db.execute('INSERT INTO native_workshops(character_id,workshop_id,rank) VALUES(?,1,7)',(p.cid,))
            db.execute('DELETE FROM inventory WHERE character_id=?',(p.cid,));ids=[]
            for slot,(kind,amount) in enumerate([(source['native_type'],1),(recipe['make_items'][0][0],3),(medicine,3)],1):
                ids.append(db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,?,?)',(p.cid,kind,slot,amount)).lastrowid)
            persist(db,p.cid,ids[0],source,roll(source,Random(1)))
        self.send(3597);self.open();return recipe,ids,source

    def test_real_14_to_15_upgrade_recast_and_receipt_replay(self):
        recipe,ids,old=self.prepare_high();self.send(MAKE,*ids,0);iid=self.game.last_event['item_id'];p=self.game.progression
        state=p.snapshot();gear=state['equipment'][iid]
        self.assertEqual(gear['grade'],15);self.assertEqual(gear['requirements']['力量'],520)
        self.assertTrue(1062<=gear['properties']['攻击']<=1500)
        self.assertEqual(gear['max_durability'],old['durability']);self.assertEqual(state['gold'],993000)
        self.send(MAKE,*ids,0);self.assertEqual(p.snapshot(),state)
        self.assertEqual(Progression(self.path,'npc_owner',p.cid).snapshot()['equipment'],state['equipment'])
        p.move_item(iid,1,4);self.assertGreater(p.snapshot()['attack'],state['attack'])
        p.move_item(iid,8,1)
        from native946_codec import number
        from native946_login import MessageReader
        self.game.receive(REFRESH,MessageReader(b''.join(number(v) for v in (iid,ids[1],ids[2],0))+b'\0'))
        self.assertEqual(p.snapshot()['gold'],990000);self.assertEqual(len(self.rows('native_craft_orders')),2)

    def test_balanced_gear_requires_two_base_abilities_and_ascension(self):
        _,ids,_=self.prepare_high(6114);self.send(MAKE,*ids,0);iid=self.game.last_event['item_id'];p=self.game.progression
        self.assertEqual(p.snapshot()['equipment'][iid]['requirements']['balanced'],450)
        with self.assertRaises(ValueError):p.move_item(iid,1,4)
        with p.transaction() as db:db.execute('UPDATE progression SET mag=450 WHERE character_id=?',(p.cid,))
        with p.transaction() as db:db.execute('UPDATE cultivation_state SET ascension=0 WHERE character_id=?',(p.cid,))
        with self.assertRaises(ValueError):p.move_item(iid,1,4)
        with p.transaction() as db:db.execute('UPDATE cultivation_state SET ascension=1 WHERE character_id=?',(p.cid,))
        p.move_item(iid,1,4);self.assertEqual(next(r for r in p.snapshot()['items'] if r['id']==iid)['page'],1)

    def test_failed_high_generation_preserves_every_input_and_gold(self):
        _,ids,_=self.prepare_high();p=self.game.progression;before=p.snapshot()
        with patch('native946_equipment.persist',side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):self.send(MAKE,*ids,0)
        self.assertEqual(p.snapshot(),before);self.assertEqual(self.rows('native_craft_orders'),[])

    def test_missing_source_instance_and_insufficient_rank_never_consume(self):
        _,ids,_=self.prepare_high();p=self.game.progression
        with p.transaction() as db:db.execute('DELETE FROM native_equipment WHERE item_id=?',(ids[0],))
        before=p.snapshot();self.send(MAKE,*ids,0);self.assertEqual(p.snapshot(),before)
        with p.transaction() as db:db.execute('UPDATE native_workshops SET rank=6 WHERE character_id=?',(p.cid,))
        before=p.snapshot();self.send(MAKE,*ids,0);self.assertEqual(p.snapshot(),before)

    def test_native_help_profiles_have_exact_percentage_ticks_and_real_current_types(self):
        root=Path(__file__).parent/'analysis/remaining-native-rules-20261126'
        help_rows=json.loads((root/'native/83a21957-data.json').read_text('utf8'))[0]
        for groups in EXTENDED['high_profiles'].values():
            for profiles in groups.values():
                for profile in profiles:
                    self.assertIn(str(profile['native_help']),help_rows)
                    for seed in range(10):
                        for name,value in roll(profile,Random(seed)).items():
                            choices=[c for row in profile['rows'] for c in row if c['name']==name]
                            self.assertTrue(any(c['low']/c['divisor']<=value<=c['high']/c['divisor'] for c in choices))
        self.assertEqual(len(EXTENDED['reward_map']),62)

class RareFulfillmentTests(unittest.TestCase):
    setUp=quest_fixture.NpcQuestTests.setUp;ready=quest_fixture.NpcQuestTests.ready
    accept=quest_fixture.NpcQuestTests.accept;buff=quest_fixture.NpcQuestTests.buff;team=quest_fixture.NpcQuestTests.team
    call=quest_fixture.NpcQuestTests.call;start=quest_fixture.NpcQuestTests.start;position=quest_fixture.NpcQuestTests.position
    fight=quest_fixture.NpcQuestTests.fight

    def test_all_62_previous_pending_types_are_actual_native_items_without_resampling(self):
        row=self.accept();p=self.a.progression;observed={};equipment_count=0
        with p.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=?',(p.cid,))
            for ref in EXTENDED['reward_map']:db.execute('INSERT INTO native_npc_pending_items(task_id,character_id,reference_type,amount) VALUES(?,?,?,1)',(row['id'],p.cid,int(ref)))
        for _ in range(4):
            with p.transaction() as db:
                delivered=deliver(p,db)
                for ref,count in delivered:observed[ref]=observed.get(ref,0)+count
            snapshot=p.snapshot();equipment_count+=len(snapshot['equipment'])
            for item in snapshot['items']:self.assertTrue(p.item_description(item['id']))
            with p.transaction() as db:db.execute('DELETE FROM inventory WHERE character_id=?',(p.cid,))
        self.assertEqual(observed,{int(ref):1 for ref in EXTENDED['reward_map']});self.assertEqual(equipment_count,39)
        with p.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM native_npc_pending_items').fetchone()[0],0)

    def test_failed_fixed_equipment_delivery_rolls_back_then_claims_exactly_once(self):
        row=self.accept();p=self.a.progression
        with p.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=?',(p.cid,))
            db.execute('INSERT INTO native_npc_pending_items(task_id,character_id,reference_type,amount) VALUES(?,?,200189,3)',(row['id'],p.cid))
        with patch('native946_equipment.persist',side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):
                with p.transaction() as db:deliver(p,db)
        self.assertEqual(p.snapshot()['items'],[])
        with p.transaction() as db:self.assertEqual(deliver(p,db),[(200189,3)]);self.assertEqual(deliver(p,db),[])
        state=Progression(self.path,p.owner,p.cid).snapshot();self.assertEqual(len(state['equipment']),3)
        for gear in state['equipment'].values():
            self.assertEqual(gear['properties'],EXTENDED['rare_equipment']['200189']['fixed_properties'])
            self.assertEqual(gear['requirements']['kind'],'仙器');self.assertEqual(gear['max_durability'],6000)

    def test_real_battle_settlement_grants_books_beads_and_fixed_gear_once(self):
        row=self.accept();forced=dict(experience=111,gold=22,guardian_experience=111,points=0,drops={16:1,2012:2,200001:1},rare=False,victory=True)
        with patch('native946_npc_quests.reward',return_value=forced):
            battle=self.start(row);self.fight(battle)
        state=self.a.progression.snapshot();types={r['type']:r['amount'] for r in state['items']}
        self.assertEqual(types[6214],1);self.assertEqual(types[6134],2)
        self.assertEqual(len(state['equipment']),1)
        with self.a.progression.transaction() as db:battle.settle(db,True)
        self.assertEqual(self.a.progression.snapshot(),state)

if __name__=='__main__':unittest.main()
