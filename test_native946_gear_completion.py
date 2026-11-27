"""Exercise the new equipment routes with original messages and disposable saves."""

import unittest
from random import Random

from native946_codec import number, packet
from native946_cosmetics import appearance
from native946_equipment import COMPLETION, DATA, bonuses
from native946_gear_sockets import jewel, properties
from native946_login import MessageReader, string_field
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

    def test_classic_fifth_tier_samples_preserve_rolls_and_skip_added_properties(self):
        import json
        from native946_equipment import CBG_GEAR

        loose = self.add(17540)
        self.assertIn('【同编号五阶仙器基础属性参考】'.encode('gb18030'), self.request(806,loose,0))
        for key, sample in CBG_GEAR['fairy'].items():
            rule = dict(grade=5, slot=sample['slot'], role='全部', reference_id=int(key), need={},
                        level=155, rebirth=0, ascension=1, durability=5000, kind='仙器')
            iid = self.add(int(key), profile=rule)
            view = self.p.snapshot()['equipment'][iid]
            self.assertEqual(view['requirements']['cbg_fairy_type'], int(key))
            self.assertEqual(view['properties'], sample['properties'])
            self.assertNotIn('native_help', view['requirements'])
            if key == '17571':
                self.assertNotIn('攻击', view['properties'])
                with self.p.transaction() as db:
                    db.execute('UPDATE characters SET level=155 WHERE id=?',(self.p.cid,))
                    db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1 WHERE character_id=?',(self.p.cid,))
                self.assertNotIn(888,ids(self.request(807,0,iid)))
                equipped = self.p.snapshot()
                self.assertEqual(next(i['page'] for i in equipped['items'] if i['id']==iid), equipped['equipment_page'])
                self.assertEqual(equipped['cultivation']['ReGeshan'],800)
                with self.p.transaction() as db:
                    db.execute('UPDATE native_equipment SET properties=?,durability=113 WHERE item_id=?',
                               (json.dumps({'速度': 17}), iid))
                other = self.p.snapshot()['equipment'][iid]
                self.assertEqual((other['properties'], other['durability']), ({'速度': 17}, 113))
                self.assertNotIn('cbg_fairy_type', other['requirements'])
            if key == '17540':
                self.assertIn('【抗致命】60%'.encode('gb18030'), self.p.item_description(iid))
                _, _, stats = bonuses([dict(id=iid, page=1)], {iid:view})
                self.assertEqual([stats[k] for k in ('ReFire','ReWind','ReJieti','ReHaoran','EquipHpRegen')],
                                 [300,280,500,500,400])
            if key == '18038':
                _, _, stats = bonuses([dict(id=iid, page=1)], {iid: view})
                self.assertEqual((stats['IgSeal'], stats['IgFaint']), (200, 300))
        self.assertIn('加强水法伤害', CBG_GEAR['fairy']['17627']['properties'])
        self.assertNotIn('加强水法伤害', CBG_GEAR['fairy']['17717']['properties'])

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

    def test_native_crown_quote_and_existing_upgrade_apply_the_946_formula(self):
        from native946_progression import Progression
        from native946_refinement import save

        self.cash(30000)
        crown=self.add(7505,profile=dict(reference_id=7505,grade=4,slot=1,role='全部',need={},level=1,
                                        rebirth=0,durability=750,kind='神兵'))
        material=self.add(6109)
        self.visit(2039,4190)
        self.assertIn(packet(202,['number'],[4]),self.choose(4190,'crown_upgrade'))
        self.assertEqual(self.request(281,crown,material),packet(197,['number','number'],[4,8000]))
        self.assertIn(98,ids(self.request(282,crown,material)))
        self.assertEqual(self.amount(6109),1)
        self.assertEqual(self.p.snapshot()['gold'],30000)
        with self.p.transaction() as db:
            save(db,self.p.cid,crown,dict(traits=[['DudsHpMax',12,1]],expires=0,lucky=0,attempts=1,pending={}))
            db.execute('UPDATE characters SET level=30 WHERE id=?',(self.p.cid,))
            db.execute('UPDATE progression SET strength=110 WHERE character_id=?',(self.p.cid,))
        self.visit(2039,4190)
        self.choose(4190,'transition')
        self.assertNotIn(888,ids(self.choose(4190,'transition_do')))
        before=self.p.snapshot()
        self.assertNotIn(888,ids(self.request(807,0,crown)))
        stored=Progression(self.path,'npc_owner',self.p.cid).snapshot()
        self.assertEqual(stored['gold'],22000)
        self.assertEqual(self.amount(6109),0)
        gear=stored['equipment'][crown]
        self.assertEqual((gear['grade'],gear['role'],gear['durability']),(5,'女',750))
        self.assertEqual(gear['requirements'],{'kind':'神兵','native_help':131003,'level':30,'rebirth':0,'力量':110})
        self.assertEqual(gear['properties'],{'防御':750,'抗混乱':10,'抗封印':10,'反震率':40,'反震程度':40})
        self.assertEqual(stored['defense']-before['defense'],750)
        self.assertEqual(stored['cultivation']['EquipReflectChance'],400)
        self.assertEqual(stored['hpmax']-before['hpmax'],12)

    def test_fairy_base_uses_native_type_for_new_and_existing_equipment(self):
        import json
        from native946_refinement import save as save_refinement, state as refinement_state

        head = dict(reference_id=1, grade=2, slot=1, role='男', need={},
                    level=100, rebirth=0, durability=1000, kind='仙器')
        iid = self.add(7605, profile=head)
        with self.p.transaction() as db:
            saved = db.execute('SELECT properties FROM native_equipment WHERE item_id=?', (iid,)).fetchone()
            self.assertEqual(json.loads(saved[0])['抗毒伤害'], 150)
            db.execute('UPDATE native_equipment SET properties=?,durability=123 WHERE item_id=?',
                       ('{"抗水法":999}', iid))
            save_refinement(db, self.p.cid, iid,
                            dict(traits=[['DudsHpMax', 20, 1]], expires=0, lucky=0, attempts=1, pending={}))
        weapon = dict(head, grade=4, slot=4, level=140, role='画魂')
        sword = self.add(16807, profile=weapon)
        control_sword = self.add(16806, profile=dict(weapon, role='慕容'))
        wrong_grade = self.add(16806, profile=dict(weapon, grade=3))
        equipment = self.p.snapshot()['equipment']
        self.assertEqual(equipment[wrong_grade]['properties'], {})
        self.assertEqual(equipment[iid]['requirements']['native_help'], 132012)
        self.assertEqual(equipment[iid]['durability'], 123)
        self.assertEqual(equipment[iid]['properties']['抗水法'], 90)
        self.assertIn('【物理反震几率】27%'.encode('gb18030'), self.p.item_description(iid))
        self.assertIn('【加强雷法伤害】340'.encode('gb18030'), self.p.item_description(sword))
        _, _, stats = bonuses([dict(id=iid,page=1),dict(id=sword,page=1)], equipment)
        self.assertEqual({key: stats[key] for key in ('ReWater','EquipReflectChance','AddThunder','IgThunder','EquipHpRegen')},
                         {'ReWater':90,'EquipReflectChance':270,'AddThunder':340,'IgThunder':150,'EquipHpRegen':200})
        _, _, stats = bonuses([dict(id=control_sword, page=1)], equipment)
        self.assertEqual((stats['AddDisorder'], stats['IgDisorder']), (300, 140))
        with self.p.transaction(write=False) as db:
            saved = db.execute('SELECT properties FROM native_equipment WHERE item_id=?', (iid,)).fetchone()
            self.assertEqual(json.loads(saved[0]), {'抗水法':999})
            self.assertEqual(refinement_state(db, self.p.cid, iid)['traits'], [['DudsHpMax', 20, 1]])

    def test_first_tier_weapon_regenerates_mana_and_third_tier_helmet_keeps_saved_sets(self):
        from native946_role_battle import Runtime
        from test_native946_innate_effects import AlwaysProc, fighter

        profile=next(p for p in DATA['rare_equipment'].values() if p['native_type']==18168)
        weapon=self.add(18168,profile=profile)
        helm=self.add(7640,profile=dict(profile,grade=3,slot=1,role='男'))
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET role_id=204,race=2,sex=2,level=100 WHERE id=?',(self.p.cid,))
            # An old instance retains its recorded wear requirements and durability.
            db.execute("UPDATE native_equipment SET properties='{}',durability=123 WHERE item_id=?",(helm,))
        self.game.character.update(role_id=204,race=2,sex=2)
        self.assertIn('【每回合回法】70'.encode('gb18030'),self.p.item_description(weapon))
        self.request(807,0,weapon)
        snap=self.p.snapshot()
        self.assertEqual(next(i['page'] for i in snap['items'] if i['id']==weapon),snap['equipment_page'])
        self.assertEqual(snap['cultivation']['EquipMpRegen'],70)
        actor=fighter(1,3)
        actor.update(mp=0,cultivation=snap['cultivation'])
        Runtime({1:actor},{},1,AlwaysProc(),{}).begin_round()
        self.assertEqual(actor['mp'],70)
        recovered=snap['equipment'][helm]
        self.assertEqual((recovered['requirements']['native_help'],recovered['durability']),(132018,123))
        self.assertEqual(recovered['properties']['抗隔山'],1550)
        with self.p.transaction() as db:
            db.execute('INSERT INTO native_equipment_attribute_sets VALUES(?,?,2,?)',
                       (helm,self.p.cid,'{"防御":17}'))
            db.execute('UPDATE native_equipment SET properties=? WHERE item_id=?',('{"防御":17}',helm))
        self.assertEqual(self.p.snapshot()['equipment'][helm]['properties'],{'防御':17})

    def test_ring_resistance_reaches_the_native_resistance_packet(self):
        from native946_lua_values import table

        kind = COMPLETION['ring'][str(self.game.character['race'])]
        ring = self.add(kind, profile=dict(reference_id=kind, grade=3, slot=12, role='全部',
                                           level=1, rebirth=0, need={}, durability=500, kind='神兵'))
        with self.p.transaction() as db:
            db.execute('UPDATE native_equipment SET properties=? WHERE item_id=?',
                       ('{"抗小楼":300}', ring))
        before = self.p.snapshot()['cultivation'].get('ReXiaolou', 0)
        self.p.move_item(ring, 1, 12)
        self.assertEqual(self.p.snapshot()['cultivation']['ReXiaolou'], before + 300)
        reply = self.p.resistance_packet(1)
        self.assertEqual(mid(reply), 698)
        reader = MessageReader(reply[3:])
        self.assertEqual(reader.integer(), 1)
        self.assertEqual(table(reader.string().decode('gb18030'))['ReXiaolou'], before + 300)
        reader.finish()

    def test_native_ring_panel_reads_saved_level_and_refuses_unverified_settlement(self):
        race=str(self.game.character['race'])
        kind=COMPLETION['ring'][race]
        iid=self.add(kind,profile=dict(reference_id=kind,grade=4,slot=12,role='全部',need={},level=1,
                                       rebirth=0,durability=500,kind='神兵'))
        self.cash(10000)
        self.add(6109)
        self.visit(2039,4190)
        self.assertIn(packet(202,['number'],[7]),self.choose(4190,'ring_upgrade'))
        extras=self.request(3758,iid,b'\x01',string_field('EquipLevel'))
        reader=MessageReader(extras[3:])
        self.assertEqual(reader.integer(),iid)
        from native946_lua_values import table
        self.assertEqual(table(reader.string().decode('gb18030')),{'EquipLevel':4})
        reader.finish()
        self.assertIn(4589,ids(self.request(4591,iid)))
        self.assertIn(98,ids(self.request(4587,iid,b'\x00')))
        self.assertEqual(self.p.snapshot()['equipment'][iid]['grade'],4)
        self.assertEqual(self.p.snapshot()['gold'],10000)
        self.assertEqual(self.amount(6109),1)

    def test_second_human_ring_is_recognized_without_opening_settlement(self):
        from native946_gear_services import plan
        from native946_refinement import Refinement

        kind = 25312
        iid = self.add(kind, profile=dict(reference_id=kind,grade=4,slot=12,role='全部',need={},
                                         native_race=1,level=1,rebirth=0,durability=500,kind='神兵'))
        self.assertEqual(COMPLETION['ring']['1'], 25256)
        self.assertEqual(COMPLETION['ring_variants']['25312'], {'race': 1, 'holy_target': 25314})
        self.assertTrue(any('缘定三生'.encode('gb18030') in msg for msg in self.p.item_packets(item_ids={iid})))
        self.assertIn('【装备角色】人族'.encode('gb18030'), self.p.item_description(iid))
        self.cash(10000)
        self.add(6109)
        self.visit(2039,4190)
        self.assertIn(packet(202,['number'],[7]),self.choose(4190,'ring_upgrade'))
        self.assertIn(4589,ids(self.request(4591,iid)))
        self.assertIn(98,ids(self.request(4587,iid,b'\x00')))
        instance = self.p.snapshot()['equipment'][iid]
        self.assertEqual(instance['grade'], 4)
        self.assertIsNone(plan(self.game, self.row(kind), instance, 'holy'))
        self.assertFalse(Refinement.gear_allowed(kind, instance, 'shenbing'))
        self.assertEqual(self.p.snapshot()['gold'], 10000)
        self.assertEqual(self.amount(6109),1)

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
