"""Legacy saves, native panel replies and inventory conservation regressions."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import SCHEMAS,number
from native946_gameplay import NativeGameplay
from native946_game_login import message_id
from native946_login import MessageReader
from native946_progression import Progression
from native946_summons import Summons


class AttributeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'test.sqlite3'
        LocalAccounts(self.path).register({'username':'owner946','password':'123456789'})
        self.characters=Characters(self.path)
        self.character=self.characters.create('owner946','迁移测试',702)
        self.game=NativeGameplay(self.character,self.characters,lambda:())
        self.p=self.game.progression;self.s=self.game.summons

    def call(self,mid,*args,tail=b''):
        return self.game.receive(mid,MessageReader(b''.join(number(n) for n in args)+tail))

    def test_legacy_hero_migration_preserves_allocation_vital_fraction_and_is_once(self):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=3 WHERE id=?',(self.p.cid,))
            db.execute('UPDATE progression SET cor=12,mag=14,strength=17,speed=11,points=7,hp=73,mp=40,formula_version=0 '
                       'WHERE character_id=?',(self.p.cid,))
        loaded=Progression(self.path,'owner946',self.p.cid);s=loaded.snapshot()
        self.assertEqual([s[k] for k in ('cor','mag','strength','speed','points')],[14,16,19,13,7])
        self.assertEqual((s['hp'],s['mp']),(73*s['hpmax']//146,40*s['mpmax']//80))
        self.assertEqual(Progression(self.path,'owner946',self.p.cid).snapshot(),s)

    def test_legacy_guardian_migration_keeps_points_affinity_and_missing_health(self):
        sid=self.s.claim_starter()
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=5,cor=14,mag=15,strength=19,speed=12,points=6,hp=68,mp=32,formula_version=0 WHERE id=?',(sid,))
        loaded=Summons(self.p);s=loaded.get(sid)
        self.assertEqual([s[k] for k in ('cor','mag','strength','speed','points','amity','growth')],[14,15,19,12,6,100,1030])
        self.assertEqual((s['hp'],s['mp']),(68*s['hpmax']//137,32*s['mpmax']//65))
        self.assertEqual(Summons(self.p).get(sid),s)

    def test_native_hero_resistance_modes_and_unsupported_spend_reply(self):
        for mode in (1,2):
            reply=self.call(729,mode);self.assertEqual(reply[:3],b'\x00\xba\x02')
            r=MessageReader(reply[3:]);self.assertEqual(r.integer(),mode)
            text=r.string().decode('gb18030');r.finish()
            self.assertIn('["DodAll"]=9',text);self.assertIn('["ResistPoint"]=0',text)
        before=self.p.snapshot()
        for mid,args in [(741,[1]+[0]*12),(740,[1,1]),(1387,[])]:
            self.assertTrue(self.call(mid,*args).startswith(message_id(98)))
        self.assertEqual(self.p.snapshot(),before)

    def test_guardian_affinity_resistance_units_boundaries_and_ownership(self):
        sid=self.s.claim_starter()
        for amity,combo,fatal,count in [(3999,0,0,0),(4000,10,0,0),(7000,10,10,1),(50000,120,70,7)]:
            with self.p.transaction() as db:db.execute('UPDATE summons SET amity=? WHERE id=?',(amity,sid))
            reply=self.call(734,sid);r=MessageReader(reply[3:]);self.assertEqual(r.integer(),sid)
            values={}
            for key,kind in SCHEMAS['summon_resist']:values[key]=r.string() if kind=='string' else r.integer()
            r.finish()
            self.assertEqual([values[k] for k in ('Amity','DoubleRate','FatalRate','DoubleCount','Grow')],
                             [amity,combo,fatal,count,1020])
        self.s.settle(sid,'cap',10,20,False);self.assertEqual(self.s.get(sid)['amity'],50000)
        other=self.characters.create('owner946','其他角色',702)
        foreign=NativeGameplay(other,self.characters,lambda:())
        self.assertTrue(foreign.receive(734,MessageReader(number(sid))).startswith(message_id(98)))

    def test_guardian_medicine_consumes_once_and_invalid_messages_roll_back(self):
        sid=self.s.claim_starter();item=self.p.snapshot()['items'][0]
        self.s.set_vitals(sid,self.s.get(sid)['hpmax']-10,20)
        reply=self.call(807,sid,item['id'])
        self.assertTrue(any(p[:3]==b'\x00\xc2\x02' for p in reply))
        self.assertEqual(self.s.get(sid)['hp'],self.s.get(sid)['hpmax'])
        self.assertEqual(self.p.snapshot()['items'][0]['amount'],4)
        self.assertTrue(self.call(807,sid,item['id']).startswith(message_id(98)))
        self.s.set_vitals(sid,1,20)
        before=(self.s.get(sid),self.p.snapshot())
        self.assertTrue(self.call(807,sid,item['id'],tail=b'junk').startswith(message_id(98)))
        self.assertEqual((self.s.get(sid),self.p.snapshot()),before)
        self.assertTrue(self.call(807,sid+1,item['id']).startswith(message_id(98)))
        self.assertEqual((self.s.get(sid),self.p.snapshot()),before)


if __name__=='__main__':unittest.main()
