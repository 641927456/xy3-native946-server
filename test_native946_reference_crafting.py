"""Real production, cold saves, ownership, equip effects and atomic replay."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from random import Random
from pathlib import Path
import json,unittest
from unittest.mock import patch

from native946_crafting import Crafting,MAKE,REFRESH
from native946_equipment import DATA,roll
from native946_gameplay import NativeGameplay
from native946_progression import Progression
from native946_storage import StorageBusy
import test_native946_crafting as craft_fixtures
from test_native946_npc_services import mid


class ReferenceProductionTests(unittest.TestCase):
    setUp=craft_fixtures.CraftingTests.setUp;prepare=craft_fixtures.CraftingTests.prepare;send=craft_fixtures.CraftingTests.send
    open=craft_fixtures.CraftingTests.open;snapshot=craft_fixtures.CraftingTests.snapshot

    def rows(self,table):
        with self.game.progression.transaction(write=False) as db:return [dict(r) for r in db.execute('SELECT * FROM '+table)]

    def produce(self):
        recipe,ids=self.prepare();response=self.send(MAKE,*ids,0)
        self.assertIn(791,[mid(p) for p in response]);return recipe,ids,self.game.last_event['item_id']

    def test_upgrade_consumes_exact_inputs_and_saves_generated_instance(self):
        recipe,ids,iid=self.produce();state=self.snapshot()
        self.assertEqual(state['wallet']['gold'],93000)
        self.assertNotIn(ids[0],{r['id'] for r in state['items']})
        self.assertEqual({r['id']:r['amount'] for r in state['items'] if r['id'] in ids[1:]},{ids[1]:4,ids[2]:4})
        gear=self.game.progression.snapshot()['equipment'][iid]
        self.assertEqual((gear['grade'],gear['role'],gear['slot']),(11,'易水寒',4))
        self.assertTrue(760<=gear['properties']['攻击']<=1140)
        self.assertEqual(len(self.rows('native_craft_orders')),1)

    def test_native_result_window_contains_old_and_new_descriptions_in_wire_order(self):
        recipe,ids=self.prepare();response=self.send(MAKE,*ids,0);iid=self.game.last_event['item_id']
        from native946_login import MessageReader
        result=next(p for p in response if mid(p)==5471);reader=MessageReader(result[3:])
        self.assertEqual(reader.integer(),iid);self.assertEqual((reader.integer(),reader.integer()),(0,0))
        old,new=reader.string().decode('gb18030'),reader.string().decode('gb18030');reader.finish()
        self.assertIn(recipe['material_base_name'],old);self.assertIn(recipe['result_name'],new);self.assertIn('【攻击】',new)

    def test_same_request_replays_after_cold_load_without_recharging(self):
        recipe,ids,iid=self.produce();before=self.snapshot();properties=self.rows('native_equipment')
        self.game.progression=Progression(self.path,'npc_owner',self.game.progression.cid)
        reply=self.send(MAKE,*ids,0)
        self.assertIn(98,[mid(p) for p in reply]);self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.rows('native_equipment'),properties)

    def test_changed_payload_cannot_reuse_the_consumed_equipment(self):
        recipe,ids,iid=self.produce();before=self.snapshot()
        self.assertEqual(mid(self.send(MAKE,ids[0],ids[2],ids[1],0)),98)
        self.assertEqual(self.snapshot(),before)

    def test_recast_carries_settled_refinement_to_replacement(self):
        from native946_refinement import save, state
        recipe,ids,iid=self.produce()
        p=self.game.progression
        settled=dict(traits=[['DudsAttAll',10,1]],expires=0,lucky=0,attempts=2,pending={})
        with p.transaction() as db:
            db.execute('UPDATE inventory SET type=? WHERE id=?',(recipe['recast_items'][0][0],ids[1]))
            save(db,p.cid,iid,dict(settled,pending={'gear':'ordinary','traits':[]}))
        with self.assertRaises(ValueError):
            Crafting(self.game).produce(REFRESH,iid,ids[1],ids[2],0)
        with p.transaction() as db:save(db,p.cid,iid,settled)
        Crafting(self.game).produce(REFRESH,iid,ids[1],ids[2],0)
        replacement=self.game.last_event['item_id']
        with p.transaction(write=False) as db:
            self.assertEqual(state(db,p.cid,replacement),settled)
            self.assertFalse(state(db,p.cid,iid)['traits'])

    def test_locked_source_or_material_cannot_be_consumed_by_production(self):
        _,ids=self.prepare();p=self.game.progression
        for iid in ids:
            with p.transaction() as db:db.execute('INSERT INTO native_object_locks VALUES(?,\'item\',?,1,0)',(p.cid,iid))
            before=self.snapshot();self.assertEqual(mid(self.send(MAKE,*ids,0)),98);self.assertEqual(self.snapshot(),before)
            with p.transaction() as db:db.execute('DELETE FROM native_object_locks WHERE object_id=?',(iid,))

    def test_recast_changes_identity_and_charges_reference_cash_once(self):
        recipe,ids,iid=self.produce();p=self.game.progression
        with p.transaction() as db:db.execute('UPDATE inventory SET type=? WHERE id=?',(recipe['recast_items'][0][0],ids[1]))
        response=Crafting(self.game).produce(REFRESH,iid,ids[1],ids[2],0)
        self.assertIn(791,[mid(r) for r in response]);newid=self.game.last_event['item_id']
        self.assertNotEqual(newid,iid);self.assertEqual(p.snapshot()['gold'],90000)
        self.assertEqual([r['item_id'] for r in self.rows('native_equipment')],[newid])
        before=self.snapshot();Crafting(self.game).produce(REFRESH,iid,ids[1],ids[2],0)
        self.assertEqual(self.snapshot(),before)

    def test_failure_after_consumption_rolls_back_every_item_cash_and_receipt(self):
        recipe,ids=self.prepare();before=self.snapshot()
        with patch('native946_equipment.persist',side_effect=RuntimeError('fixture generation failure')):
            with self.assertRaises(RuntimeError):self.send(MAKE,*ids,0)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.rows('native_craft_orders'),[]);self.assertEqual(self.rows('native_equipment'),[])

    def test_reply_busy_after_commit_does_not_reroll_or_double_charge(self):
        recipe,ids=self.prepare()
        with patch.object(self.game.progression,'item_description',side_effect=StorageBusy('fixture rendering busy')):
            response=self.send(MAKE,*ids,0)
        kinds=[mid(p) for p in response]
        self.assertIn(5471,kinds);self.assertIn(786,kinds);self.assertIn(695,kinds)
        before=self.snapshot();gear=self.rows('native_equipment');self.send(MAKE,*ids,0)
        self.assertEqual(self.snapshot(),before);self.assertEqual(self.rows('native_equipment'),gear)
        self.assertEqual(before['wallet']['gold'],93000)

    def test_replayed_result_survives_busy_optional_panels_without_losing_new_item(self):
        _,ids,iid=self.produce();p=self.game.progression;before=self.snapshot()
        with patch.object(p,'attribute_packets',side_effect=StorageBusy('fixture optional panel busy')):
            response=self.send(MAKE,*ids,0)
        self.assertIn(5471,[mid(r) for r in response]);self.assertEqual(self.snapshot(),before)
        self.assertEqual(len(self.rows('native_craft_orders')),1)

    def test_insufficient_gold_and_unmapped_medicine_leave_all_inputs_intact(self):
        recipe,ids=self.prepare();p=self.game.progression
        with p.transaction() as db:db.execute('UPDATE characters SET gold=6999 WHERE id=?',(p.cid,))
        before=self.snapshot();self.assertEqual(mid(self.send(MAKE,*ids,0)),98);self.assertEqual(self.snapshot(),before)
        with p.transaction() as db:
            db.execute('UPDATE characters SET gold=100000 WHERE id=?',(p.cid,));db.execute('UPDATE inventory SET type=6114 WHERE id=?',(ids[2],))
        before=self.snapshot();self.assertEqual(mid(self.send(MAKE,*ids,0)),98);self.assertEqual(self.snapshot(),before)

    def test_produced_weapon_is_usable_and_removal_restores_base_damage(self):
        recipe,ids,iid=self.produce();p=self.game.progression
        with p.transaction() as db:db.execute('UPDATE characters SET role_id=111,race=1,sex=1 WHERE id=?',(p.cid,))
        before=p.snapshot()['attack'];amount=p.snapshot()['equipment'][iid]['properties']['攻击']
        p.move_item(iid,1,4);self.assertEqual(p.snapshot()['attack'],before+amount)
        from native946_login import MessageReader
        reader=MessageReader(p.item_description(iid)[3:]);self.assertEqual(reader.integer(),iid)
        self.assertIn('【攻击】'+str(amount),reader.string().decode('gb18030'))
        p.move_item(iid,8,1);self.assertEqual(p.snapshot()['attack'],before)

    def test_wrong_role_and_slot_cannot_equip_new_weapon(self):
        recipe,ids,iid=self.produce();before=self.snapshot()
        for slot in (1,4):
            with self.assertRaises(ValueError):self.game.progression.move_item(iid,1,slot)
        self.assertEqual(self.snapshot(),before)

    def test_parallel_replays_create_exactly_one_order_and_output(self):
        recipe,ids=self.prepare();other=NativeGameplay(dict(self.game.character),self.characters,lambda:())
        other.workshop_query_until=self.game.workshop_query_until
        other.workshop_production_session=deepcopy(self.game.workshop_production_session)
        from native946_codec import number
        from native946_login import MessageReader
        def call(game):return game.receive(MAKE,MessageReader(b''.join(number(n) for n in (*ids,0))))
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(call,[self.game,other]))
        self.assertEqual(self.snapshot()['wallet']['gold'],93000)
        self.assertEqual(len(self.rows('native_craft_orders')),1);self.assertEqual(len(self.rows('native_equipment')),1)

    def test_all_profiles_stay_inside_reference_ranges_and_use_real_native_types(self):
        native=json.loads((Path(__file__).parent/'analysis/native-formulas-online-20261126/equip-data.json').read_text('utf8'))['Equip']
        for profiles in DATA['crafting']['profiles'].values():
            for value in profiles.values():
              for profile in value if isinstance(value,list) else [value]:
                limits={}
                for choices in profile['rows']:
                    for c in choices:limits.setdefault(c['name'],[]).append((c['low']/c.get('divisor',1),c['high']/c.get('divisor',1)))
                for seed in range(5):
                    for name,value in roll(profile,Random(seed)).items():
                        self.assertTrue(any(low<=value<=high for low,high in limits[name]))
                self.assertEqual(native[str(profile['native_type'])]['Index'],profile['slot'])


if __name__=='__main__':unittest.main()
