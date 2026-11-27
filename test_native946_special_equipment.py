"""Offline 946 packet and persistence checks for realm gems and guardian ornaments."""

import unittest

from native946_gear_sockets import properties
from native946_codec import SCHEMAS
from native946_growth import guardian_defaults, species
from native946_guardian_equipment import equipment_page
from native946_guardian_ornaments import ORDINARY, SHOP, STONE, TOKEN, UNLIMITED_COINS, balance, state
from native946_jing_gems import ESSENCE, ESSENCE_PACK, level, put
from native946_login import MessageReader
from native946_progression import Progression
from native946_summons import Summons
from test_native946_npc_services import mid
from test_native946_npcs import read_npc
import test_native946_refinement as fixtures


class SpecialEquipmentTests(unittest.TestCase):
    setUp=fixtures.RefinementTests.setUp
    approach=fixtures.RefinementTests.approach
    request=fixtures.RefinementTests.request
    choose=fixtures.RefinementTests.choose
    visit=fixtures.RefinementTests.visit
    add=fixtures.RefinementTests.add
    amount=fixtures.RefinementTests.amount
    cash=fixtures.RefinementTests.cash
    p=fixtures.RefinementTests.p

    def response(self,reply,expected):
        packets=(reply,) if isinstance(reply,bytes) else reply
        return MessageReader(next(p[3:] for p in packets if mid(p)==expected))

    def guardian_record(self,reader,schema):
        def field(encoding):
            if encoding=='string':return reader.string()
            if encoding=='byte':return reader.take(1)[0]
            return reader.integer()
        return {name:field(encoding) for name,encoding in SCHEMAS[schema]}

    def assert_guardian_appearance(self,reply,sid,kind,shape,color=0):
        change=self.response(reply,714)
        row=self.guardian_record(change,'summon_list')
        self.assertEqual((row['Id'],row['Type'],row['ShowType'],row['WeaponColor']),(sid,kind,shape,color))
        self.assertEqual(change.integer(),3)
        change.finish()
        info=self.response(reply,706)
        self.assertEqual(info.integer(),sid)
        row=self.guardian_record(info,'summon')
        self.assertEqual((row['Type'],row['ShowType']),(kind,shape))
        info.finish()
        shown=read_npc(next(message for message in reply if mid(message)==1589))
        self.assertEqual((shown['id'],shown['type'],shown['weapon_color']),(sid,shape,color))

    def assert_fighter_appearance(self,reply,message_id,sid,shape,color=0):
        fighter=self.response(reply,message_id)
        self.assertEqual((fighter.integer(),fighter.integer(),fighter.integer()),(sid,3,shape))
        fighter.string()
        for _ in range(3):fighter.integer()
        self.assertEqual(fighter.take(3)[1],color)

    def inventory_id(self,kind):
        with self.p.transaction(write=False) as db:
            return db.execute('SELECT max(id) FROM inventory WHERE character_id=? AND type=?',(self.p.cid,kind)).fetchone()[0]

    def cash_balance(self):
        with self.p.transaction(write=False) as db:
            return db.execute('SELECT gold FROM characters WHERE id=?',(self.p.cid,)).fetchone()[0]

    def open_gems(self):
        self.visit(2436,7630)
        self.response(self.choose(7630,'open'),3525).finish()

    def ornament_state(self,iid):
        with self.p.transaction(write=False) as db:return state(db,iid)

    def guardian_type(self,sid,kind):
        with self.p.transaction() as db:
            fields=dict(type=kind,name=species(kind)['name'],**guardian_defaults(kind))
            db.execute('UPDATE summons SET '+','.join(key+'=?' for key in fields)+' WHERE id=?',(*fields.values(),sid))

    def test_realm_essence_pack_exchange_merge_and_reconnect(self):
        self.cash(72000)
        self.open_gems()
        pack=self.add(ESSENCE_PACK)
        self.response(self.request(807,0,pack),786)
        self.assertEqual(self.amount(ESSENCE_PACK),0)
        self.assertEqual(self.amount(ESSENCE),100)
        self.response(self.request(3527,47405,1,2),3520).finish()
        bag=self.response(self.request(3526),3519)
        self.assertIn(b'[4740501]=2',bag.string())
        self.assertEqual(self.response(self.request(3535,47405,1),3534).integer(),2)
        self.assertEqual(self.amount(ESSENCE),80)
        self.assertEqual(self.cash_balance(),72000)
        self.assertEqual(self.response(self.request(3531,47405,1),3524).integer(),1)
        self.assertEqual(self.cash_balance(),71000)
        self.assertEqual(self.amount(47405),1)
        iid=self.inventory_id(47405)
        self.response(self.request(3531,47405,1),98)
        self.response(self.request(3527,47405,6,1),98)
        self.response(self.request(3527,47405,5,0),98)
        self.response(self.request(3527,47404,101,1),98)
        self.assertEqual((self.cash_balance(),self.amount(ESSENCE)),(71000,80))
        result=self.response(self.request(3533,iid),3532)
        self.assertEqual((result.integer(),result.integer()),(47405,2))
        desc=self.response(self.p.item_description(iid),791)
        self.assertEqual(desc.integer(),iid)
        self.assertIn('2级境·红宝石',desc.string().decode('gb18030'))
        reloaded=Progression(self.path,'npc_owner',self.p.cid)
        with reloaded.transaction(write=False) as db:self.assertEqual(level(db,iid),2)

    def test_realm_rank_checked_for_inlay_preserved_when_removed_and_converted(self):
        self.cash(1100000)
        self.open_gems()
        profile=dict(reference_id=21279,grade=1,slot=1,role='女',level=1,rebirth=0,need={},durability=100,kind='境装备')
        gear=self.add(21279,profile=profile)
        self.response(self.request(3821,gear),3825).finish()
        with self.p.transaction() as db:
            high=put(self.game,db,47406,12)
            allowed=put(self.game,db,47406,11)
            target_material=put(self.game,db,47405,9)
        for payload in self.p.item_packets():
            if mid(payload)!=786:continue
            item=MessageReader(payload[3:])
            wire={name:(item.string() if encoding=='string' else item.take(1)[0] if encoding=='byte' else item.integer())
                  for name,encoding in SCHEMAS['item_info']}
            if wire['id']==allowed:
                self.assertEqual(wire['can_trade'],1)
                break
        else:self.fail('未发送境宝石的背包新增回包')
        self.response(self.request(3530,gear,high,1),98)
        self.assertEqual(self.cash_balance(),100000)
        detail=self.response(self.request(3530,gear,allowed,1),3523)
        self.assertEqual(tuple(detail.integer() for _ in range(3)),(1,47406,11))
        slot=self.response(self.request(3529,gear),3522)
        self.assertEqual(tuple(slot.integer() for _ in range(4)),(47406,11,0,0))
        self.assertEqual(self.cash_balance(),89000)
        with self.p.transaction(write=False) as db:self.assertEqual(properties(db,gear),{'最大气血':1024,'抗封印':11})
        self.response(self.request(3528,gear,1),3521)
        removed=self.inventory_id(47406)
        with self.p.transaction(write=False) as db:self.assertEqual(level(db,removed),11)
        self.assertIn(f'[{removed}]=11'.encode(),self.response(self.request(4517),4515).string())
        self.response(self.request(4516,high,target_material),98)
        self.response(self.request(4516,removed,high),98)
        self.response(self.request(4516,removed,target_material),4515)
        converted=self.inventory_id(47405)
        with self.p.transaction(write=False) as db:
            self.assertEqual(level(db,converted),11)
            self.assertIsNone(db.execute('SELECT id FROM inventory WHERE id=?',(target_material,)).fetchone())
        self.assertEqual((self.amount(47405),self.amount(47406),self.cash_balance()),(1,1,89000))

    def test_ordinary_ornament_shop_is_unlimited_and_color_shop_uses_tokens(self):
        ordinary,color=43000,43027
        self.assertEqual((len(ORDINARY),len(SHOP),ORDINARY[ordinary]['Cost'],SHOP[color]['Cost']),(39,9,60,20))
        self.cash(600000)
        self.response(self.request(3933,ordinary),98)
        self.visit(2221,4545)
        self.assertEqual(self.response(self.choose(4545,'ornaments'),3930).integer(),UNLIMITED_COINS)
        self.response(self.request(3933,ordinary),98)
        self.assertEqual(self.response(self.request(4096),4091).integer(),UNLIMITED_COINS)
        self.response(self.request(4094,color),98)
        initial=self.amount(ordinary)
        for bought in (1,2):
            self.assertEqual(self.response(self.request(4094,ordinary),4091).integer(),UNLIMITED_COINS)
            self.assertEqual(self.amount(ordinary),initial+bought)
        token=self.add(TOKEN,21)
        self.assertEqual(self.response(self.request(807,0,token),3993).string(),b'dlg_yuhua_mall')
        self.response(self.request(4694,ordinary),98)
        self.response(self.request(4694,color),786)
        self.response(self.request(4694,color),98)
        self.assertEqual((self.amount(TOKEN),self.amount(color),self.cash_balance()),(1,1,600000))
        with self.p.transaction(write=False) as db:self.assertEqual(balance(db,self.p.cid),UNLIMITED_COINS)

    def test_full_bag_rolls_back_currency_and_gem_exchange(self):
        self.cash(100000)
        self.add(ESSENCE,80)
        self.add(TOKEN,40)
        self.open_gems()
        with self.p.transaction() as db:
            occupied={row[0] for row in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.p.cid,))}
            db.executemany('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,47405,8,?,1)',
                           [(self.p.cid,slot) for slot in range(1,25) if slot not in occupied])
        self.response(self.request(3527,47406,2,2),98)
        self.assertEqual((self.amount(ESSENCE),self.amount(47406),self.cash_balance()),(80,0,100000))
        self.visit(2221,4545)
        self.request(3935)
        self.response(self.request(3933,43000),98)
        with self.p.transaction(write=False) as db:self.assertEqual(balance(db,self.p.cid),UNLIMITED_COINS)
        self.request(807,0,self.inventory_id(TOKEN))
        self.response(self.request(4694,43027),98)
        self.assertEqual((self.amount(TOKEN),self.amount(43000),self.amount(43027)),(40,0,0))

    def test_existing_rank_one_socket_survives_schema_upgrade(self):
        profile=dict(reference_id=21279,grade=1,slot=1,role='女',level=1,rebirth=0,need={},durability=100,kind='境装备')
        gear=self.add(21279,profile=profile)
        with self.p.transaction() as db:
            db.execute("INSERT INTO native_gear_sockets VALUES(?,1,'jing',47406,'防御',15,1)",(gear,))
            db.execute('ALTER TABLE native_gear_sockets DROP COLUMN gem_level')
            for table in ('native_jing_gem_levels','native_guardian_ornament_coins','native_guardian_ornament_refinement'):
                db.execute(f'DROP TABLE {table}')
        migrated=Progression(self.path,'npc_owner',self.p.cid)
        with migrated.transaction(write=False) as db:
            self.assertEqual(properties(db,gear),{'最大气血':384,'抗封印':1})
            self.assertEqual(state(db,gear),({},{}))
            self.assertEqual(balance(db,self.p.cid),UNLIMITED_COINS)

    def test_gold_taisui_ornament_moves_to_native_slot_and_unequips(self):
        iid=self.add(43046)
        sid=self.game.summons.claim_starter()
        self.guardian_type(sid,3374)
        self.request(750,sid,3)
        before=self.game.summons.get(sid)
        expected_page=equipment_page(sid)
        equipped=self.request(807,sid,iid)
        page=self.response(equipped,711)
        self.assertEqual((page.integer(),page.integer()),(sid,expected_page))
        self.assert_guardian_appearance(equipped,sid,3374,4906)
        with self.p.transaction(write=False) as db:
            position=db.execute('SELECT page,slot FROM inventory WHERE id=?',(iid,)).fetchone()
            self.assertEqual(tuple(position),(expected_page,12))
            self.assertEqual(db.execute('SELECT summon_id FROM native_guardian_equipment WHERE item_id=?',(iid,)).fetchone()[0],sid)
        current=self.game.summons.get(sid)
        self.assertEqual((current['attack']-before['attack'],current['hpmax']-before['hpmax'],current['defense']-before['defense']),(64,160,32))
        reloaded=Progression(self.path,'npc_owner',self.p.cid)
        with reloaded.transaction(write=False) as db:
            self.assertEqual(tuple(db.execute('SELECT page,slot FROM inventory WHERE id=?',(iid,)).fetchone()),(expected_page,12))
        synced=self.response(Summons(reloaded).sync(),713)
        self.assertEqual(synced.take(1)[0],1)
        row=self.guardian_record(synced,'summon_list')
        self.assertEqual((row['Type'],row['ShowType']),(3374,4906))
        fighter=next(message for message in self.game.begin_battle() if mid(message)==2147
                     and MessageReader(message[3:]).integer()==sid)
        self.assert_fighter_appearance(fighter,2147,sid,4906)
        self.game.battle=None
        self.assert_guardian_appearance(self.request(807,sid,iid),sid,3374,3374)
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT page FROM inventory WHERE id=?',(iid,)).fetchone()[0],8)
            self.assertIsNone(db.execute('SELECT summon_id FROM native_guardian_equipment WHERE item_id=?',(iid,)).fetchone())
        restored=self.game.summons.get(sid)
        self.assertEqual((restored['attack'],restored['hpmax'],restored['defense']),(before['attack'],before['hpmax'],before['defense']))

    def test_replacing_a_colored_ornament_refreshes_its_weapon_color(self):
        colored=self.add(43027)
        ordinary=self.add(next(kind for kind,v in ORDINARY.items() if v['SummonShape']==[3332,4815]))
        sid=self.game.summons.claim_starter()
        self.guardian_type(sid,3332)
        self.request(750,sid,3)
        self.assert_guardian_appearance(self.request(807,sid,colored),sid,3332,4815,6)
        preview=self.response(self.request(727,sid),684)
        self.assertEqual(preview.integer(),sid)
        row=self.guardian_record(preview,'summon_info')
        self.assertEqual((row['ShowType'],row['WeaponColor']),(4815,6))
        self.request(750,sid,2)
        self.game.begin_battle()
        summoned=self.game.apply_guardian_order(dict(kind='summon',summon_id=sid))
        self.assert_fighter_appearance(summoned,2128,sid,4815,6)
        self.game.battle=None
        self.assert_guardian_appearance(self.request(807,sid,ordinary),sid,3332,4815)
        self.assert_guardian_appearance(self.request(807,sid,ordinary),sid,3332,3332)

    def test_every_native_ornament_switches_and_restores_the_appearance(self):
        sid=self.game.summons.claim_starter()
        self.request(750,sid,3)
        for item_type,offer in (*ORDINARY.items(),*SHOP.items()):
            kind,shape,*colors=offer['SummonShape']
            with self.subTest(item_type=item_type,guardian_type=kind):
                self.guardian_type(sid,kind)
                item=self.add(item_type)
                self.assert_guardian_appearance(self.request(807,sid,item),sid,kind,shape,colors[0] if colors else 0)
                self.assert_guardian_appearance(self.request(807,sid,item),sid,kind,kind)
                with self.p.transaction() as db:
                    db.execute('DELETE FROM inventory WHERE character_id=? AND id=?',(self.p.cid,item))

    def test_same_name_guardian_cannot_equip_another_variants_ornament(self):
        sid=self.game.summons.claim_starter()
        self.guardian_type(sid,2128)
        item=self.add(43000)
        self.response(self.request(807,sid,item),98)
        with self.p.transaction() as db:
            self.assertIsNone(db.execute('SELECT item_id FROM native_guardian_equipment WHERE summon_id=?',(sid,)).fetchone())
            self.assertEqual(db.execute('SELECT page FROM inventory WHERE id=?',(item,)).fetchone()[0],8)
            # An incompatible association from an older save can still be removed.
            db.execute('INSERT INTO native_guardian_equipment VALUES(?,?,?)',(item,sid,'ornament'))
            db.execute('UPDATE inventory SET page=?,slot=12 WHERE id=?',(equipment_page(sid),item))
        self.response(self.request(807,sid,item),714)
        with self.p.transaction(write=False) as db:
            self.assertIsNone(db.execute('SELECT item_id FROM native_guardian_equipment WHERE summon_id=?',(sid,)).fetchone())
            self.assertEqual(db.execute('SELECT page FROM inventory WHERE id=?',(item,)).fetchone()[0],8)

    def test_ornament_refinement_keep_apply_and_guardian_stats(self):
        self.cash(25000)
        iid=self.add(43000)
        stone=self.add(STONE,3)
        sid=self.game.summons.claim_starter()
        self.response(self.request(807,sid,iid),98)
        self.guardian_type(sid,2046)
        self.request(807,sid,iid)
        old=self.game.summons.get(sid)
        self.assertEqual(self.response(self.request(807,0,stone),3961).integer(),0)
        self.assertEqual(self.response(self.request(3963,iid),3961).integer(),iid)
        self.response(self.request(3962,iid,1),3961)
        traits,pending=self.ornament_state(iid)
        self.assertEqual(traits,{})
        self.assertEqual(len(pending),2)
        self.assertTrue(all(10<=amount<=25 for amount in pending.values()))
        self.response(self.request(3962,iid,1),98)
        self.assertEqual((self.amount(STONE),self.cash_balance()),(2,15000))
        self.response(self.request(3962,iid,2),3961)
        self.assertEqual(self.ornament_state(iid),({},{}))
        self.response(self.request(3962,iid,1),3961)
        _,pending=self.ornament_state(iid)
        self.response(self.request(3962,iid,3),3961)
        self.response(self.request(3962,iid,3),98)
        self.response(self.request(3962,iid,1),98)
        self.assertEqual((self.amount(STONE),self.cash_balance()),(1,5000))
        self.assertEqual(self.ornament_state(iid),(pending,{}))
        current=self.game.summons.get(sid)
        self.assertEqual({key:current[key]-old[key] for key in pending},pending)
        worn=Progression(self.path,'npc_owner',self.p.cid)
        with worn.transaction(write=False) as db:self.assertEqual(state(db,iid),(pending,{}))
        peach=self.add(43043)
        self.guardian_type(sid,3388)
        self.response(self.request(807,sid,peach),98)
        self.guardian_type(sid,3389)
        self.request(807,sid,peach)
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT item_id FROM native_guardian_equipment WHERE summon_id=?',(sid,)).fetchone()[0],peach)


if __name__=='__main__':
    unittest.main()
