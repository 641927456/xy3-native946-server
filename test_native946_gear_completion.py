"""Exercise the new equipment routes with original messages and disposable saves."""

import unittest
from random import Random

from native946_codec import number, packet
from native946_cosmetics import appearance
from native946_equipment import COMPLETION, DATA
from native946_gear_sockets import jewel, properties
from native946_login import MessageReader
from native946_workshops import DATA as WORKSHOPS
import test_native946_refinement as fixtures
from test_native946_npc_services import mid


def ids(reply):
    return {mid(p) for p in ((reply,) if isinstance(reply,bytes) else reply)}


class GearCompletionTests(unittest.TestCase):
    setUp=fixtures.RefinementTests.setUp
    approach=fixtures.RefinementTests.approach
    p=fixtures.RefinementTests.p
    request=fixtures.RefinementTests.request
    visit=fixtures.RefinementTests.visit
    choose=fixtures.RefinementTests.choose
    add=fixtures.RefinementTests.add
    amount=fixtures.RefinementTests.amount
    cash=fixtures.RefinementTests.cash

    def row(self,kind):
        return next(item for item in self.p.snapshot()['items'] if item['type']==kind)

    def test_native_shop_purchase_can_equip_and_is_an_actual_workshop_input(self):
        self.cash(100000)
        self.visit(2039,1276)
        self.assertIn(1697,ids(self.choose(1276,'open')))
        for kind in (5005,6109,6110):
            self.assertNotIn(888,ids(self.request(1716,1276,kind,1)))
        weapon=self.row(5005)
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET role_id=111,race=1,sex=1 WHERE id=?',(self.p.cid,))
            db.execute('INSERT INTO native_workshops(character_id,workshop_id,rank) VALUES(?,?,3)',(self.p.cid,1))
        self.game.character.update(role_id=111,race=1,sex=1)
        self.assertNotIn(888,ids(self.request(807,0,weapon['id'])))
        self.assertNotIn(888,ids(self.request(807,0,weapon['id'])))
        workshop=WORKSHOPS['workshops']['1']
        self.visit(workshop['scene'],workshop['tool'])
        self.assertIn(220,ids(self.choose(workshop['tool'],'production')))
        self.assertIn(5471,ids(self.request(273,weapon['id'],self.row(6109)['id'],self.row(6110)['id'],0)))
        new=self.game.last_event['item_id']
        self.assertNotIn(888,ids(self.request(807,0,new)))
        with self.p.transaction(write=False) as db:self.assertEqual(appearance(db,self.p.cid)['weapon_idx'],3)

    def test_new_recipes_and_accessory_inlay_apply_real_stats(self):
        self.assertEqual(len(DATA['crafting']['profiles']),252)
        self.assertEqual(len(COMPLETION['accessories']),28)
        index,profile=next((int(i),p) for i,p in COMPLETION['accessories'].items() if p['slot']==10 and p['grade']==1001)
        recipe=WORKSHOPS['recipes'][index]
        kind=int(next(k for k,v in COMPLETION['items'].items() if v['name']==recipe['material_base_name'] and v['icon']==recipe['material_shape_1']))
        self.cash(100000)
        source=self.add(kind)
        material=self.add(recipe['make_items'][0][0])
        drug=self.add(recipe['medicines'][0])
        wid=recipe['zuofang_id']
        workshop=WORKSHOPS['workshops'][str(wid)]
        with self.p.transaction() as db:
            db.execute('INSERT INTO native_workshops(character_id,workshop_id,rank) VALUES(?,?,8)',(self.p.cid,wid))
        self.visit(workshop['scene'],workshop['tool'])
        self.choose(workshop['tool'],'production')
        self.assertIn(5471,ids(self.request(273,source,material,drug,0)))
        iid=self.game.last_event['item_id']
        self.assertIn(98,ids(self.request(807,0,iid)))
        self.visit(2092,4755)
        self.assertIn(215,ids(self.choose(4755,'open')))
        self.assertIn(216,ids(self.request(271,iid,b'\x01\x01',0)))
        gem=self.add(45683)
        self.assertIn(216,ids(self.request(271,iid,b'\x02\x01',gem)))
        with self.p.transaction(write=False) as db:self.assertIn(properties(db,iid)['速度'],range(1,5))
        self.assertEqual(self.amount(45683),0)
        self.assertEqual(jewel(45683),(10,'速度',1,4))

    def test_every_grade_eleven_recipe_has_attainable_native_base_equipment(self):
        from native946_crafting import META
        from native946_progression import GEAR
        from native946_shops import DATA as SHOPS
        stock={int(k) for m in SHOPS['merchants'].values() for k in m['goods']}|set(GEAR)
        obtainable={META[str(k)]['icon'] for k in stock if META.get(str(k),{}).get('btype')==5}
        self.assertTrue({r['material_shape_1'] for r in WORKSHOPS['recipes'] if r['equip_grade']==11}<=obtainable)

    def test_ring_and_guardian_equipment_returns_owned_native_items(self):
        self.cash(200000)
        ring=self.add(47580)
        self.assertNotIn(888,ids(self.request(807,0,ring)))
        self.assertEqual(self.amount(47580),0)
        item=self.row(COMPLETION['ring'][str(self.game.character['race'])])
        self.assertEqual(self.p.snapshot()['equipment'][item['id']]['slot'],12)
        sid=self.game.summons.claim_starter()
        before=self.game.summons.get(sid)
        first=self.add(6110)
        second=self.add(6792)
        self.visit(2221,4545)
        self.assertIn(1924,ids(self.choose(4545,'open')))
        self.assertNotIn(888,ids(self.request(1929,first,1,second,1,sid,42000)))
        artifact=self.row(42000)
        tooltip=self.request(806,artifact['id'],0)
        reader=MessageReader(tooltip[3:])
        self.assertEqual(reader.integer(),artifact['id'])
        self.assertIn('本机计分',reader.string().decode('gb18030'))
        self.assertEqual(reader.integer(),0)
        score=reader.integer()
        self.assertGreater(score,0)
        self.assertEqual(self.p.attribute_values()['SummonScore'],before['score'])
        reply=self.request(807,sid,artifact['id'])
        self.assertNotIn(888,ids(reply))
        equipped=self.game.summons.get(sid)
        self.assertEqual(equipped['attack'],before['attack']+16)
        self.assertEqual(equipped['score'],before['score']+score)
        self.assertIn(packet(695,['number','number'],[88,equipped['score']]),reply)
        self.assertIn(packet(706,['number','summon'],[sid,self.game.summons.info_record(equipped)]),reply)
        reply=self.request(807,sid,artifact['id'])
        self.assertNotIn(888,ids(reply))
        self.assertEqual(self.game.summons.get(sid)['attack'],before['attack'])
        self.assertIn(packet(695,['number','number'],[88,before['score']]),reply)

    def test_guardian_ornament_score_counts_only_confirmed_refinement(self):
        sid=self.game.summons.acquire(3313,'score:ornament')
        iid=self.add(43025)
        self.assertNotIn(888,ids(self.request(807,sid,iid)))
        before=self.game.summons.get(sid)['score']
        with self.p.transaction() as db:
            db.execute('INSERT INTO native_guardian_ornament_refinement(item_id,pending) VALUES(?,?)',
                       (iid,'{"cor":20,"mag":20}'))
        self.assertEqual(self.p.attribute_values()['SummonScore'],before)
        reply=self.request(3962,iid,3)
        after=before+200
        self.assertNotIn(888,ids(reply))
        self.assertIn(packet(695,['number','number'],[88,after]),reply)
        self.assertEqual(self.game.summons.get(sid)['score'],after)

    def test_repair_and_jing_socket_persist_and_reject_wrong_gem(self):
        self.cash(1200000)
        profile=dict(reference_id=21279,grade=1,slot=1,role='女',level=1,rebirth=0,need={},durability=100,kind='境装备')
        iid=self.add(21279,profile=profile)
        with self.p.transaction() as db:
            db.execute('UPDATE native_equipment SET durability=95 WHERE item_id=?',(iid,))
        self.visit(2039,1276)
        self.choose(1276,'repair')
        self.assertNotIn(888,ids(self.request(256,iid)))
        self.assertEqual(self.p.snapshot()['equipment'][iid]['durability'],100)
        self.visit(2436,7629)
        self.assertIn(3525,ids(self.choose(7629,'jing_gems')))
        self.assertIn(3825,ids(self.request(3821,iid)))
        wrong=self.add(47405)
        allowed=self.add(47406)
        self.assertIn(98,ids(self.request(3530,iid,wrong,1)))
        self.assertIn(3523,ids(self.request(3530,iid,allowed,1)))
        self.assertEqual(self.amount(47405),1)
        self.assertEqual(self.amount(47406),0)
        with self.p.transaction(write=False) as db:self.assertEqual(properties(db,iid),{'最大气血':384,'抗封印':1})
        self.assertIn(3521,ids(self.request(3528,iid,1)))
        self.assertEqual(self.amount(47406),1)

    def test_shenbing_holy_and_realm_transitions_reuse_original_item(self):
        kind=5005
        profile=dict(reference_id=kind,grade=16,slot=4,role='火薇',level=1,rebirth=0,need={},durability=100)
        self.cash(1000000)
        divine=self.add(kind,profile=profile)
        normal=self.add(5010,profile=profile)
        for material,amount in ((46545,3),(6109,1),(45161,1),(47222,1)):self.add(material,amount)
        for branch,expected in (('shenbing','神兵'),('shenbing','神兵'),('holy','圣武')):
            self.visit(2039,4190)
            index=next(i for i,a in enumerate(self.game.dialog_actions,1)
                       if isinstance(a,dict) and a.get('branch')==branch)
            self.assertIn(1025,ids(self.request(1032,bytes([index]),4190)))
            index=next(i for i,a in enumerate(self.game.dialog_actions,1) if a.get('item')==divine)
            self.assertNotIn(888,ids(self.request(1032,bytes([index]),4190)))
            self.assertEqual(self.p.snapshot()['equipment'][divine]['requirements']['kind'],expected)
        self.visit(2436,7629)
        self.choose(7629,'transition')
        self.assertNotIn(888,ids(self.choose(7629,'transition_do')))
        self.assertEqual(self.p.snapshot()['equipment'][normal]['requirements']['kind'],'境装备')
        self.assertEqual(self.amount(47222),0)

    def test_plain_socket_and_battle_wear_are_persistent(self):
        p=self.p
        profile=dict(reference_id=5002,grade=1,slot=2,role='全部',level=1,rebirth=0,need={},durability=1)
        iid=self.add(5002,profile=profile)
        gem=self.add(1500)
        self.cash(10000)
        self.visit(2039,1276)
        self.assertTrue({208,209}.issubset(ids(self.choose(1276,'ordinary_sockets'))))
        self.assertIn(211,ids(self.request(264,iid,b'\x01',gem)))
        self.assertEqual(self.amount(1500),0)
        p.move_item(iid,1,2)
        attack=p.snapshot()['attack']
        self.assertTrue(self.characters.settle_battle('npc_owner',p.cid,'socket-gear-wear',True))
        self.assertFalse(self.characters.settle_battle('npc_owner',p.cid,'socket-gear-wear',True))
        self.assertEqual(p.snapshot()['equipment'][iid]['durability'],0)
        self.assertEqual(attack-p.snapshot()['attack'],5)

    def test_secondary_traits_and_turn_regeneration_used_in_combat(self):
        from native946_role_battle import Runtime
        profile=dict(reference_id=5002,grade=1,slot=2,role='全部',level=1,rebirth=0,need={},durability=100)
        item=self.add(5002,profile=profile)
        with self.p.transaction() as db:
            db.execute('UPDATE native_equipment SET properties=? WHERE item_id=?',
                       ('{"加强昏睡": 16, "忽视抗混": 8, "回合回血": 75}',item))
        self.p.move_item(item,1,2)
        hero=dict(self.p.snapshot(),kind='hero',camp=1,hp=1)
        attrs=hero['cultivation']
        self.assertEqual((attrs['AddFaint'],attrs['IgDisorder'],attrs['EquipHpRegen']),(160,80,75))
        Runtime({hero['id']:hero},{},1,Random(1),{}).begin_round()
        self.assertEqual(hero['hp'],min(76,hero['hpmax']))
