"""Publisher policy and exact native crafting inputs, with no invented gear."""
from pathlib import Path
from copy import deepcopy
import json,unittest
from unittest.mock import patch

from native946_codec import number
from native946_login import MessageReader
from native946_crafting import Crafting,DATA,MAKE,REFRESH,MAKE_FTR,REFRESH_FTR,META,extra_chain_reward_due
from native946_workshops import Workshops,DATA as WDATA
from native946_progression import DATA as ITEMS
import test_native946_npcs as fixtures
from test_native946_npc_services import mid


class CraftingTests(unittest.TestCase):
    setUp=fixtures.NpcGameplayTests.setUp
    approach=fixtures.NpcGameplayTests.approach

    def send(self,message,*args):return self.game.receive(message,MessageReader(b''.join(number(n) for n in args)))
    def open(self,wid=1):return self.game.receive(2488,MessageReader(bytes([wid])))
    def send_array(self,message,equip,stones,drug,owner=0,mode=0):
        body=number(equip)+bytes([len(stones)])+b''.join(number(n) for n in stones)+number(drug)+number(owner)
        if message==REFRESH_FTR:body+=bytes([mode])
        return self.game.receive(message,MessageReader(body))

    def prepare(self,upgrade=True,rank=3):
        p=self.game.progression;recipe=next(r for r in WDATA['recipes'] if r['zuofang_id']==1 and r['equip_grade']==11)
        kind=next(int(t) for t,m in META.items() if m['icon']==recipe['material_shape_1'] and m['btype']==5) if upgrade else recipe['equip_type_list'][0]
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=20,gold=100000 WHERE id=?',(p.cid,))
            db.execute('INSERT INTO native_workshops(character_id,workshop_id,rank) VALUES(?,1,?)',(p.cid,rank))
            db.execute('DELETE FROM inventory WHERE character_id=?',(p.cid,))
            ids=[]
            for slot,(t,amount) in enumerate([(kind,1),(recipe['make_items' if upgrade else 'recast_items'][0][0],5),(recipe['medicines'][0],5)],1):
                ids.append(db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,?,?)',(p.cid,t,slot,amount)).lastrowid)
        self.send(3597);self.open();return recipe,ids

    def snapshot(self):
        p=self.game.progression
        with p.transaction(write=False) as db:return dict(wallet=dict(db.execute('SELECT * FROM characters WHERE id=?',(p.cid,)).fetchone()),
            items=[dict(r) for r in db.execute('SELECT * FROM inventory WHERE character_id=? ORDER BY id',(p.cid,))],
            workshops=[dict(r) for r in db.execute('SELECT * FROM native_workshops WHERE character_id=?',(p.cid,))])

    def test_production_window_matches_native_field_widths_and_personal_workshop(self):
        self.prepare();before=self.snapshot();replies=self.open();p=next(p for p in replies if mid(p)==220)
        r=MessageReader(p[1:]);self.assertEqual(r.integer(),1);self.assertEqual(r.take(3),b'\2\1\2')
        self.assertEqual([r.integer() for _ in range(5)],[1,3,0,self.game.progression.cid,0]);r.finish()
        self.assertEqual(self.snapshot(),before)

    def test_unjoined_unknown_and_unsolicited_production_requests_are_rejected(self):
        self.assertEqual(mid(self.open()),98);self.send(3597);self.assertEqual(mid(self.open()),98)
        self.assertEqual(mid(self.open(8)),98)

    def test_native_upgrade_quote_checks_exact_jade_drug_and_cash_fields(self):
        recipe,ids=self.prepare();before=self.snapshot()
        q=Crafting(self.game).quote(MAKE,*ids,self.game.progression.cid)
        self.assertEqual((q['result'],q['grade'],q['required_rank']),(recipe['result_name'],11,3))
        self.assertEqual(q['cash'],recipe['make_cash'] or 7000);self.assertEqual([r['amount'] for r in q['materials']],[recipe['make_items'][0][1],recipe['make_medicine_num']])
        self.assertFalse(q['generation_verified']);self.assertFalse(q['resource_consumption']);self.assertEqual(self.snapshot(),before)

    def test_recast_does_not_misuse_refinement_cash_as_recast_price(self):
        recipe,ids=self.prepare(upgrade=False);q=Crafting(self.game).quote(REFRESH,*ids,0)
        self.assertEqual(q['cash'],3000);self.assertEqual(q['refinement_cash'],recipe['forge_cash'])
        self.assertEqual([r['amount'] for r in q['materials']],[recipe['recast_items'][0][1],recipe['recast_medicine_num']])

    def test_explicit_formula_preview_remains_non_consuming(self):
        recipe,ids=self.prepare();before=self.snapshot();replies=Crafting(self.game).preview(MAKE,*ids,self.game.progression.cid)
        p=next(p for p in replies if mid(p)==227);r=MessageReader(p[1:]);self.assertEqual(r.string().decode('gb18030'),'配方核对')
        self.assertIn('未扣除物品和金钱',r.string().decode('gb18030'));r.finish();self.assertEqual(self.snapshot(),before)
        self.assertFalse(self.game.last_event['resource_consumption'])

    def test_current_window_array_upgrade_and_recast_parse_the_native_payload(self):
        recipe,ids=self.prepare();before=self.snapshot()
        self.assertIn(791,[mid(p) for p in self.send_array(MAKE_FTR,ids[0],[ids[1]],ids[2],self.game.progression.cid)])
        self.assertEqual(self.game.last_event['operation'],'upgrade');created=self.game.last_event['item_id']
        self.assertEqual(self.snapshot()['wallet']['gold'],before['wallet']['gold']-7000)
        with self.game.progression.transaction() as db:
            db.execute('UPDATE inventory SET type=? WHERE id=?',(recipe['recast_items'][0][0],ids[1]))
        before=self.snapshot()
        self.assertIn(791,[mid(p) for p in self.send_array(REFRESH_FTR,created,[ids[1]],ids[2])])
        self.assertEqual(self.game.last_event['operation'],'recast')
        self.assertEqual(self.snapshot()['wallet']['gold'],before['wallet']['gold']-3000)

    def test_split_material_stacks_are_counted_and_consumed_once(self):
        recipe,ids=self.prepare();p=self.game.progression
        with p.transaction() as db:
            db.execute('UPDATE inventory SET amount=1 WHERE id=?',(ids[1],))
            extra=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,4,1)',
                             (p.cid,recipe['make_items'][0][0])).lastrowid
        before=self.snapshot()
        with patch.dict(recipe,make_items=[[recipe['make_items'][0][0],2]]):
            q=Crafting(self.game).quote(MAKE,ids[0],[ids[1],extra],ids[2],0)
            self.assertEqual([(r['item'],r['amount']) for r in q['materials'][:2]],[(ids[1],1),(extra,1)])
            self.assertIn(791,[mid(p) for p in self.send_array(MAKE_FTR,ids[0],[ids[1],extra],ids[2])])
            self.assertEqual(mid(self.send_array(MAKE_FTR,ids[0],[ids[1],ids[1]],ids[2])),98)
        self.assertFalse({ids[0],ids[1],extra}&{r['id'] for r in self.snapshot()['items']})
        self.assertEqual(self.snapshot()['wallet']['gold'],before['wallet']['gold']-7000)

    def test_array_requests_reject_empty_foreign_truncated_and_nondefault_recast_inputs(self):
        recipe,ids=self.prepare();before=self.snapshot()
        for materials in ([],[999999],[ids[0]],[ids[1],999999]):
            self.assertEqual(mid(self.send_array(MAKE_FTR,ids[0],materials,ids[2])),98)
        self.assertEqual(mid(self.send_array(REFRESH_FTR,ids[0],[ids[1]],ids[2],mode=1)),98)
        self.assertEqual(mid(self.game.receive(MAKE_FTR,MessageReader(number(ids[0])+b'\2'+number(ids[1])))),98)
        self.assertEqual(self.snapshot(),before)

    def test_insufficient_rank_and_wrong_workshop_do_not_consume(self):
        recipe,ids=self.prepare(rank=2);before=self.snapshot();self.assertEqual(mid(self.send(MAKE,*ids,0)),98)
        self.assertEqual(self.snapshot(),before)
        self.game.workshop_production_session['workshop']=2
        with self.assertRaises(ValueError):Crafting(self.game).quote(MAKE,*ids,0)

    def test_source_engraving_and_inputs_are_preserved_when_forging_is_refused(self):
        _,ids=self.prepare()
        p=self.game.progression
        with p.transaction() as db:
            db.execute('INSERT INTO cosmetic_item_carvings VALUES(?,?,?)',(ids[0],p.cid,'{}'))
        before=self.snapshot()
        self.assertEqual(mid(self.send(MAKE,*ids,0)),98)
        self.assertEqual(self.snapshot(),before)
        with p.transaction(write=False) as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM cosmetic_item_carvings WHERE item_id=?',(ids[0],)).fetchone())

    def test_wrong_material_wrong_drug_and_short_stacks_are_rejected(self):
        recipe,ids=self.prepare();p=self.game.progression
        for row_id,kind,amount in [(ids[1],4000,5),(ids[2],4001,5),(ids[1],recipe['make_items'][0][0],0)]:
            before=self.snapshot()
            if amount==0:
                # Use a recipe with a multi-item material requirement.
                with patch.dict(recipe,make_items=[[recipe['make_items'][0][0],6]]):
                    with self.assertRaises(ValueError):Crafting(self.game).quote(MAKE,*ids,0)
            else:
                with p.transaction() as db:db.execute('UPDATE inventory SET type=? WHERE id=?',(kind,row_id))
                with self.assertRaises(ValueError):Crafting(self.game).quote(MAKE,*ids,0)
                with p.transaction() as db:db.execute('UPDATE inventory SET type=? WHERE id=?',(before['items'][ids.index(row_id)]['type'],row_id))
            self.assertEqual(self.snapshot(),before)

    def test_stored_and_foreign_items_cannot_be_used_as_crafting_materials(self):
        recipe,ids=self.prepare();p=self.game.progression
        with p.transaction() as db:db.execute('UPDATE inventory SET page=20 WHERE id=?',(ids[1],))
        with self.assertRaises(ValueError):Crafting(self.game).quote(MAKE,*ids,0)
        self.assertEqual(mid(self.send(MAKE,ids[0],999999,ids[2],0)),98)

    def test_duplicate_ids_expiry_foreign_owner_and_battle_are_rejected(self):
        recipe,ids=self.prepare();before=self.snapshot()
        for args in [(MAKE,ids[0],ids[0],ids[2],0),(MAKE,*ids,999999),(REFRESH,*ids,0,1)]:
            with self.assertRaises(ValueError):Crafting(self.game).quote(*args)
        self.game.workshop_production_session['expires']=0
        with self.assertRaises(ValueError):Crafting(self.game).quote(MAKE,*ids,0)
        self.open();self.game.battle={'phase':'input'}
        with self.assertRaises(ValueError):Crafting(self.game).quote(MAKE,*ids,0)
        self.assertEqual(self.snapshot(),before)

    def test_queries_and_formula_preview_never_reserve_the_durable_writer(self):
        recipe,ids=self.prepare()
        with patch.object(self.characters,'pin_position',side_effect=AssertionError('unexpected writer')):
            self.open();replies=Crafting(self.game).preview(MAKE,*ids,0);self.assertIn(227,[mid(p) for p in replies])

    def test_trailing_data_cannot_open_a_session_or_consume_material(self):
        recipe,ids=self.prepare();before=self.snapshot()
        result=self.game.receive(MAKE,MessageReader(b''.join(number(n) for n in (*ids,0))+b'\xff'))
        self.assertEqual(mid(result),98);self.assertEqual(self.snapshot(),before)

    def test_publisher_2026_policy_never_awards_extra_items_at_ring_100_or_200(self):
        self.assertFalse(extra_chain_reward_due(100));self.assertFalse(extra_chain_reward_due(200));self.assertTrue(extra_chain_reward_due(300))
        for value in (0,301,-1,True,1.5):
            with self.assertRaises(ValueError):extra_chain_reward_due(value)
        rule=DATA['task_chain'];self.assertEqual(rule['effective_date'],'2026-08-12')
        self.assertIsNone(rule['per_ring_currency_formula']);self.assertIsNone(rule['extra_cash_formula']);self.assertIsNone(rule['extra_item_weights'])

    def test_new_native_input_metadata_keeps_existing_starter_and_cosmetic_records(self):
        self.assertEqual(ITEMS['items']['5001']['name'],'七彩花环')
        for t,m in list(META.items())[:20]:
            self.assertIn(t,ITEMS['items']);self.assertEqual(ITEMS['items'][t]['icon'],m['icon'])


if __name__=='__main__':unittest.main()
