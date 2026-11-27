"""Offline 946 equipment-refinement requests over an isolated character save."""

import unittest
from time import time
from unittest.mock import patch

from native946_codec import number
from native946_cosmetics import Cosmetics, DATA as CATALOG
from native946_equipment import persist
from native946_fashion_refinement import ITEMS, UPGRADES, level
from native946_login import MessageReader
from native946_refinement import DATA, DAY, item_extras, state, vigor
import test_native946_npcs as fixtures
from test_native946_npc_services import menu


def ids(reply):
    return {p[0] or int.from_bytes(p[1:3],'little') for p in ((reply,) if isinstance(reply,bytes) else reply)}


class RefinementTests(unittest.TestCase):
    setUp=fixtures.NpcGameplayTests.setUp
    approach=fixtures.NpcGameplayTests.approach

    @property
    def p(self):
        return self.game.progression

    def request(self,mid,*fields):
        return self.game.receive(mid,MessageReader(b''.join(
            value if isinstance(value,bytes) else number(value) for value in fields)))

    def choose(self,npc,label):
        with self.subTest(option=label):
            selected=next(i for i,value in enumerate(self.game.dialog_actions,1)
                          if isinstance(value,dict) and value.get('op')==label)
            return self.request(1032,bytes([selected]),npc)

    def visit(self,scene,npc):
        self.approach(scene,npc)
        return self.request(1034,npc)

    def add(self,kind,amount=1,profile=None):
        with self.p.transaction() as db:
            Cosmetics(self.game)._add(db,kind,amount,backpack=True)
            iid=db.execute('SELECT id FROM inventory WHERE character_id=? AND type=? ORDER BY id DESC LIMIT 1',
                           (self.p.cid,kind)).fetchone()[0]
            if profile:persist(db,self.p.cid,iid,profile,{})
            return iid

    def amount(self,kind):
        with self.p.transaction(write=False) as db:
            return db.execute('SELECT coalesce(sum(amount),0) FROM inventory WHERE character_id=? AND type=?',
                              (self.p.cid,kind)).fetchone()[0]

    def saved(self,iid):
        with self.p.transaction(write=False) as db:return state(db,self.p.cid,iid)

    def cash(self,amount):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET gold=? WHERE id=?',(amount,self.p.cid))

    def test_existing_character_save_migrates_before_loading_refinement_stats(self):
        from native946_progression import Progression
        with self.p.transaction() as db:
            for table in ('native_refinement','native_refinement_vigor','native_fashion_strength'):
                db.execute(f'DROP TABLE {table}')
        reloaded=Progression(self.path,'npc_owner',self.p.cid)
        self.assertGreater(reloaded.snapshot()['hpmax'],0)

    def test_halo_native_packet_choice_extension_transfer_and_atomic_failure(self):
        self.cash(900000)
        ding=self.add(46782,8)
        target=self.add(55002)
        donors=[self.add(kind) for kind in (55005,55008,55010,55012)]
        self.assertIn(98,ids(self.request(2981,target,donors[0],1)))
        self.assertIn(2980,ids(self.request(807,0,ding)))
        self.assertIn(2979,ids(self.request(2981,target,0,4)))
        self.assertEqual(self.amount(46782),8)
        self.assertIn(2979,ids(self.request(2981,target,donors[0],1)))
        first=self.saved(target)
        self.assertGreaterEqual(first['expires'],int(time())+60*DAY-2)
        self.assertEqual(self.amount(55005),0)
        self.assertIn(2979,ids(self.request(2981,target,donors[1],11)))
        self.assertIn(2979,ids(self.request(2981,target,0,12)))
        self.assertEqual(self.saved(target)['traits'],first['traits'])
        self.assertIn(2979,ids(self.request(2981,target,donors[2],2)))
        self.assertEqual(self.saved(target)['expires'],first['expires']+15*DAY)
        self.assertIn(2979,ids(self.request(2981,donors[3],target,3)))
        self.assertEqual(self.saved(donors[3])['traits'],first['traits'])
        self.assertFalse(self.saved(target)['traits'])
        self.assertEqual(self.amount(55002),1)
        self.cash(0)
        self.assertIn(98,ids(self.request(2981,target,donors[3],1)))
        self.assertEqual(self.amount(46782),3)
        self.assertEqual(self.amount(55012),1)

    def test_refinement_battle_stats_use_only_active_items_and_unexpired_traits(self):
        iid=self.add(55002)
        with self.p.transaction() as db:
            from native946_refinement import save
            save(db,self.p.cid,iid,dict(traits=[['DudsAttAll',40,3],['DudsReGeshan',60,1]],
                                        expires=int(time())+3600,lucky=0,attempts=1,pending={}))
            db.execute('UPDATE inventory SET page=29,slot=10 WHERE id=?',(iid,))
        equipped=self.p.snapshot()
        with self.p.transaction() as db:
            db.execute('UPDATE native_refinement SET expires=1 WHERE item_id=?',(iid,))
        expired=self.p.snapshot()
        self.assertEqual(equipped['attack']-expired['attack'],40)
        self.assertEqual(equipped['cultivation']['ReGeshan'],60)
        self.assertNotIn('ReGeshan',expired['cultivation'])

    def test_iron_shop_guarded_preflight_first_roll_and_keep_previous(self):
        self.cash(50000)
        from native946_progression import DATA as PROGRESS
        profile=dict(reference_id=7640,grade=3,slot=4,role='火薇',need={},level=1,rebirth=0,durability=100)
        meta={'7640':dict(name='幻羽',max_stack=1,icon=7640,desc='仙器')}
        with patch.dict(PROGRESS['items'],meta),patch.dict(CATALOG['items'],meta):
            iid=self.add(7640,profile=profile)
            self.assertIn(98,ids(self.request(2770,iid,b'\x01\x00')))
            listing=self.visit(2039,1141)
            self.assertIn('装备炼化',menu(listing)[1])
            self.assertIn(2764,ids(self.choose(1141,'forge')))
            self.assertIn(98,ids(self.request(2770,iid,b'\x01\x01')))
            for material,amount in ((46544,3),(46546,3),(6046,30)):self.add(material,amount)
            self.assertTrue({695,5023}.issubset(ids(self.request(2770,iid,b'\x01\x01'))))
            self.assertEqual(self.amount(46544),3)
            self.assertIn(2765,ids(self.request(2770,iid,b'\x01\x00')))
            first=self.saved(iid)
            self.assertTrue(first['traits'])
            self.assertIn(98,ids(self.request(2770,iid,b'\x01\x00')))
            self.assertIn(5023,ids(self.request(2770,iid,b'\x02\x01')))
            self.assertIn(2765,ids(self.request(2770,iid,b'\x02\x00')))
            self.assertEqual(self.saved(iid)['traits'],first['traits'])
            self.assertIn(98,ids(self.request(2770,iid,b'\x01\x00')))
            self.assertNotIn(888,ids(self.request(2771,iid,b'\x01')))
            self.assertEqual(self.saved(iid)['traits'],first['traits'])
            self.assertFalse(self.saved(iid)['pending'])
            self.assertEqual(self.amount(46544),0)
            with self.p.transaction(write=False) as db:
                self.assertLess(vigor(db,self.p.cid,int(time())),1000)

    def test_existing_rare_reward_upgrades_and_can_use_normal_refinement(self):
        from native946_equipment import DATA as EQUIPMENT
        kind,upgrade=next(iter(DATA['fairy_upgrades'].items()))
        source=next(profile for profile in EQUIPMENT['rare_equipment'].values() if str(profile['native_type'])==kind)
        self.cash(100000)
        with self.p.transaction() as db:
            iid=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,24,1)',
                           (self.p.cid,int(kind))).lastrowid
            persist(db,self.p.cid,iid,source,source['fixed_properties'])
        self.add(46544,3)
        self.visit(2039,1141)
        self.assertIn(1025,ids(self.choose(1141,'fairy_list')))
        self.assertNotIn(888,ids(self.choose(1141,'fairy_upgrade')))
        self.assertEqual(self.p.snapshot()['equipment'][iid]['grade'],3)
        self.assertEqual(self.amount(upgrade['id']),1)
        self.visit(2039,1141)
        self.choose(1141,'supplies')
        self.visit(2039,1141)
        self.choose(1141,'forge')
        self.assertIn(5023,ids(self.request(2770,iid,b'\x01\x01')))
        self.assertIn(2765,ids(self.request(2770,iid,b'\x01\x00')))

    def test_exact_fairy_help_profile_applies_native_element_ticks_after_upgrade(self):
        from native946_equipment import DATA as EQUIPMENT, bonuses
        from native946_progression import Progression

        self.cash(50000)
        source=next(profile for profile in EQUIPMENT['rare_equipment'].values() if profile['native_type']==10384)
        iid=self.add(10384,profile=source)
        self.add(46544,3)
        self.visit(2039,1141)
        self.choose(1141,'fairy_list')
        self.assertNotIn(888,ids(self.choose(1141,'fairy_upgrade')))
        stored=Progression(self.path,'npc_owner',self.p.cid).snapshot()['equipment'][iid]
        self.assertEqual(stored['requirements']['native_help'],132187)
        self.assertEqual(stored['grade'],3)
        _, direct, battle=bonuses([{'page':1,'id':iid}],{iid:stored})
        self.assertEqual(direct['attack'],300)
        self.assertEqual(battle['AddFire'],188)
        self.assertEqual(battle['AddThunder'],240)
        self.assertEqual(battle['IgFire'],100)
        self.assertEqual(battle['IgThunder'],120)

    def test_fairy_native_panels_and_requests_never_spend_pending_recipes(self):
        self.cash(200000)
        before=self.p.snapshot()
        for op, expected in (('fairy_compose',3),('fairy_recast',4)):
            with self.subTest(op=op):
                self.visit(2039,1141)
                reply=self.choose(1141,op)
                opening=next(payload for payload in reply if 203 in ids(payload))
                self.assertEqual(MessageReader(opening[1:]).integer(),expected)
                for mid in (287,288):
                    reply=self.request(mid,expected,1,2)
                    self.assertIn(98,ids(reply))
                    self.assertIn('暂未开放结算',MessageReader(reply[1:]).string().decode('gb18030'))
        after=self.p.snapshot()
        self.assertEqual((after['gold'],after['items'],after['equipment']),
                         (before['gold'],before['items'],before['equipment']))

    def test_jing_lucky_upgrade_and_pending_restore_packet_data(self):
        from native946_progression import DATA as PROGRESS
        from native946_refinement import save
        self.cash(100000)
        kind=int(next(iter(DATA['info_jing_luling_equip_info'])))
        profile=dict(reference_id=kind,grade=3,slot=1,role='男',need={},level=1,rebirth=0,durability=100)
        meta={str(kind):dict(name='龙游四海冠·地',max_stack=1,icon=3688,desc='境装备')}
        with patch.dict(PROGRESS['items'],meta),patch.dict(CATALOG['items'],meta):
            iid=self.add(kind,profile=profile)
            with self.p.transaction() as db:
                save(db,self.p.cid,iid,dict(traits=[['DudsHpMax',12,2]],expires=0,lucky=5,attempts=4,pending={}))
            self.visit(2436,7629)
            self.assertIn(3422,ids(self.choose(7629,'forge')))
            for material,amount in ((47223,2),(46546,2),(6046,20)):self.add(material,amount)
            self.assertIn(5023,ids(self.request(3424,iid,b'\x02\x01')))
            reply=self.request(3424,iid,b'\x02\x00')
            r=MessageReader(next(p for p in reply if 3423 in ids(p))[3:])
            self.assertEqual(r.integer(),iid)
            self.assertEqual(r.take(1),b'\x02')
            r.string();r.string()
            self.assertEqual(r.integer(),1)
            r.finish()
            with self.p.transaction(write=False) as db:
                extras=item_extras(db,self.p.cid,iid)
                self.assertTrue(extras['LastForgeSaveTraitCache'])
                self.assertEqual(extras['ForgeLevel'],2)
            self.assertNotIn(888,ids(self.request(3425,iid,b'\x02')))
            self.assertEqual(self.saved(iid)['traits'][0][2],3)

    def test_shenbing_disassembly_supplies_real_refinement(self):
        from native946_progression import DATA as PROGRESS
        self.cash(100000)
        profile=dict(reference_id=7505,grade=4,slot=1,role='女',need={},level=1,rebirth=0,durability=100)
        meta={'7505':dict(name='凤翅瑶仙簪',max_stack=1,icon=3194,desc='神兵')}
        with patch.dict(PROGRESS['items'],meta),patch.dict(CATALOG['items'],meta):
            donor=self.add(7505,profile=dict(profile,grade=1))
            target=self.add(7505,profile=profile)
            self.visit(2039,4190)
            self.assertIn(2760,ids(self.choose(4190,'disassemble')))
            self.assertNotIn(888,ids(self.request(2766,b'\x01',donor)))
            self.assertEqual(self.amount(46545),1)
            self.assertEqual(self.amount(7505),1)
            self.add(46546)
            self.add(6046,10)
            self.visit(2039,4190)
            self.assertIn(2761,ids(self.choose(4190,'forge')))
            self.assertIn(5023,ids(self.request(2767,target,b'\x01\x01')))
            self.assertIn(2762,ids(self.request(2767,target,b'\x01\x00')))
            self.assertEqual(self.amount(46545),0)
            self.assertTrue(self.saved(target)['traits'])

    def test_workshop_forge_validates_recipe_rank_and_replay(self):
        from native946_crafting import DATA as CRAFTING, WORKSHOP_FORGE
        from native946_equipment import DATA as EQUIPMENT
        from native946_workshops import DATA as WORKSHOPS
        wid,kind,profile=next((recipe['zuofang_id'],v['native_type'],v)
            for index,sets in EQUIPMENT['crafting']['profiles'].items()
            for recipe in (WORKSHOPS['recipes'][int(index)],) if recipe['forge_cash']
            for values in sets.values() for v in (values if isinstance(values,list) else [values])
            if (recipe['zuofang_id'],v['grade'],v['native_type']) in WORKSHOP_FORGE)
        recipe=WORKSHOP_FORGE[wid,profile['grade'],kind]
        npc=WORKSHOPS['workshops'][str(wid)]
        self.cash(100000)
        with self.p.transaction() as db:
            iid=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,24,1)',
                           (self.p.cid,kind)).lastrowid
            persist(db,self.p.cid,iid,dict(profile,durability=100),{})
            db.execute('INSERT INTO native_workshops(character_id,workshop_id,rank) VALUES(?,?,?)',
                       (self.p.cid,wid,CRAFTING['craft_rank_by_grade'][str(profile['grade'])]-1))
        self.add(6128)
        self.add(6046)
        self.visit(npc['scene'],npc['tool'])
        self.assertIn(1025,ids(self.choose(npc['tool'],'forge_list')))
        self.assertIn(98,ids(self.choose(npc['tool'],'forge_quote')))
        self.assertEqual(self.amount(6128),1)
        with self.p.transaction() as db:
            db.execute('UPDATE native_workshops SET rank=rank+1 WHERE character_id=? AND workshop_id=?',(self.p.cid,wid))
        self.visit(npc['scene'],npc['tool'])
        self.choose(npc['tool'],'forge_list')
        self.assertIn(1025,ids(self.choose(npc['tool'],'forge_quote')))
        self.assertNotIn(888,ids(self.choose(npc['tool'],'forge_confirm')))
        self.assertIsNone(self.request(1032,b'\x01',npc['tool']))
        self.assertEqual(self.p.snapshot()['gold'],100000-recipe['forge_cash'])
        self.assertEqual(self.amount(6128),0)
        self.assertEqual(self.amount(6046),0)
        self.assertTrue(self.saved(iid)['traits'])

    def test_fashion_exchange_strengthen_decorate_and_powder_keep_item(self):
        self.cash(100000)
        self.add(56006)
        powder=self.add(46517)
        self.add(46207,15)
        self.add(46210)
        self.visit(2001,5657)
        self.choose(5657,'fashion_refinement')
        self.assertNotIn(888,ids(self.request(1032,b'\x06',5657)))
        target=next(int(kind) for kind in UPGRADES if ITEMS[kind]['role']=='火薇' and ITEMS[kind]['slot']=='服装')
        source=next(k for k in UPGRADES[str(target)]['materials'].values() if ITEMS[str(k)]['role']=='火薇')
        with self.p.transaction(write=False) as db:
            iid=db.execute('SELECT id FROM inventory WHERE character_id=? AND type=?',(self.p.cid,source)).fetchone()[0]
        self.assertIn(2510,ids(self.request(807,0,powder)))
        self.assertIn(2695,ids(self.request(2698)))
        self.assertIn(98,ids(self.request(2697,target,iid)))
        for _ in range(5):
            self.assertIn(2511,ids(self.request(2524,iid)))
            self.assertNotIn(888,ids(self.request(2523,iid,b'\0\0')))
        self.assertIn(2506,ids(self.request(2517,iid,46210)))
        self.assertIn(98,ids(self.request(2697,target,iid)))
        self.assertNotIn(888,ids(self.request(2518,iid,b'\x01')))
        decorations=self.saved(iid)['traits']
        self.assertEqual(len(decorations),3)
        self.assertIn(2696,ids(self.request(2699,iid,target)))
        self.assertIn(2696,ids(self.request(2697,target,iid)))
        self.assertEqual(self.amount(46517),0)
        self.assertEqual(self.saved(iid)['traits'],decorations)
        with self.p.transaction(write=False) as db:
            self.assertEqual(level(db,self.p.cid,iid),5)
            self.assertEqual(db.execute('SELECT type FROM inventory WHERE id=?',(iid,)).fetchone()[0],target)
        before=self.p.snapshot()
        self.assertNotIn(888,ids(self.request(807,0,iid)))
        after=self.p.snapshot()
        self.assertGreaterEqual(after['hpmax']-before['hpmax'],50)
        self.assertGreaterEqual(after['defense']-before['defense'],10)
