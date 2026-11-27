"""Owned book learning, persisted stat changes and original 946 reply fields."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import unittest
from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_gameplay import NativeGameplay
from native946_summons import Summons
from native946_progression import Progression
from native946_skillbooks import SkillBooks,BOOKS,effects
from native946_codec import packet,SCHEMAS
from native946_login import MessageReader


def decode_record(r,kind):
    return {k:r.string().decode('gb18030') if t=='string' else r.integer() for k,t in SCHEMAS[kind]}


class BasicBookTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'books.sqlite3'
        LocalAccounts(self.path).register(dict(username='book_owner',password='fixture-password'))
        self.characters=Characters(self.path);c=self.characters.create('book_owner','特技验证',702)
        self.g=NativeGameplay(c,self.characters,lambda:());self.p=self.g.progression;self.s=self.g.summons
        self.sid=self.s.claim_starter();self.b=SkillBooks(self.s)
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=50,experience=500 WHERE id=?',(self.sid,))
            db.execute('DELETE FROM inventory WHERE character_id=?',(self.p.cid,))

    def item(self,kind,amount=2):
        with self.p.transaction() as db:
            slot=db.execute('SELECT coalesce(max(slot),0)+1 FROM inventory WHERE character_id=? AND page=8',(self.p.cid,)).fetchone()[0]
            return db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,?,?)',(self.p.cid,kind,slot,amount)).lastrowid

    def send(self,mid,*args):
        return self.g.receive(mid,MessageReader(packet(mid,['number']*len(args),args)[3:]))

    def book_records(self):
        return [row for row in self.b.records(self.sid) if row['position']>=7]

    def test_each_basic_book_learns_consumes_one_and_affects_real_stats_after_combat(self):
        for item,rule in BOOKS.items():
            if rule['tier']!=0:continue
            with self.subTest(item=item):
                iid=self.item(item);before=self.s.get(self.sid)
                result=self.send(807,self.sid,iid)
                self.assertNotEqual(result,packet(98,[],[]))
                self.assertEqual(self.book_records()[0]['grade'],0)
                self.s.settle(self.sid,'books-'+str(item),before['hp'],before['mp'],True,40)
                after=self.s.get(self.sid);bonus=effects(after['learned_books'])
                self.assertEqual(after['learned_books'][0]['level'],1)
                for key in ('hpmax','mpmax','attack','initiative'):
                    self.assertEqual(after[key]-before[key],bonus.get(key,0))
                records=self.book_records();self.assertEqual(records[0]['stunt_id'],rule['skill'])
                r=MessageReader(self.b.packet(self.sid)[3:]);self.assertEqual(r.integer(),self.sid)
                self.assertEqual(r.take(1),b'\x02')
                innate=decode_record(r,'stunt_info')
                self.assertEqual((innate['stunt_id'],innate['position']),(307,1))
                wire=decode_record(r,'stunt_info')
                self.assertEqual(wire,dict({k:0 for k,t in SCHEMAS['stunt_info'] if t=='number'},**records[0]))
                empty=[decode_record(r,'stunt_pos_empty') for _ in range(r.take(1)[0])]
                self.assertEqual([row['position'] for row in empty],[2,3,4,5,8,9,10,11,12,13])
                self.assertEqual(r.take(2),b'\0\0');self.assertEqual(r.integer(),0);r.finish()
                current=after['learned_books'];self.s.settle(self.sid,'books-'+str(item),1,1,True,40)
                self.assertEqual(self.s.get(self.sid)['learned_books'],current)
                self.send(756,self.sid,rule['skill'])

    def test_duplicate_and_full_slot_do_not_consume_books(self):
        first=self.item(6214);second=self.item(6217)
        self.send(807,self.sid,first);before=self.p.snapshot()
        for iid in (first,second):
            result=self.send(807,self.sid,iid)
            self.assertEqual(result[0],98);self.assertEqual(self.p.snapshot(),before)
        self.assertEqual(len(self.book_records()),1)

    def test_original_empty_slot_and_training_battle_use_saved_attack(self):
        self.assertIn(dict(position=7,level=0,can_do=1),self.b.list_state(self.sid)[1])
        self.send(807,self.sid,self.item(6217));self.send(754,self.sid,303)
        pet=self.s.get(self.sid);self.assertNotIn(7,[s['position'] for s in self.b.list_state(self.sid)[1]])
        self.g.begin_battle()
        self.assertEqual(self.g.battle['pet']['attack'],pet['attack'])
        self.assertEqual(self.g.battle['pet']['learned_books'][0]['level'],2)

    def test_level_wildness_kind_and_lock_validation_preserve_inputs(self):
        iid=self.item(6214);before=self.p.snapshot()
        for sql in ["level=49","level=50,grant_key='capture:test'"]:
            with self.p.transaction() as db:db.execute('UPDATE summons SET '+sql+' WHERE id=?',(self.sid,))
            self.send(807,self.sid,iid);self.assertEqual(self.p.snapshot(),before);self.assertEqual(self.book_records(),[])
        with self.p.transaction() as db:db.execute("UPDATE summons SET grant_key='starter',level=50 WHERE id=?",(self.sid,))
        with patch('native946_inventory_locks.require_unlocked',side_effect=ValueError('locked')):
            self.send(807,self.sid,iid)
        self.assertEqual(self.p.snapshot(),before)
        with patch('native946_skillbooks.species',return_value={'kind':999}):self.send(807,self.sid,iid)
        self.assertEqual(self.p.snapshot(),before)

    def test_unsupported_book_and_foreign_guardian_never_consume(self):
        iid=self.item(6230);before=self.p.snapshot()
        self.send(807,self.sid,iid);self.assertEqual(self.p.snapshot(),before)
        self.send(807,self.sid+100,iid);self.assertEqual(self.p.snapshot(),before)

    def test_native_conversion_500_boundary_and_cold_load(self):
        self.send(807,self.sid,self.item(6217));self.send(754,self.sid,303)
        pet=self.s.get(self.sid);self.assertEqual(pet['experience'],400)
        self.assertEqual(pet['learned_books'][0]['experience'],23)
        self.assertEqual(pet['learned_books'][0]['level'],2)
        cold=Summons(Progression(self.path,'book_owner',self.p.cid))
        self.assertEqual(cold.get(self.sid),pet)
        before=pet;self.send(754,self.sid,303);self.assertEqual(self.s.get(self.sid),before)

    def test_drop_clamps_saved_vitals_and_frees_original_slot(self):
        self.send(807,self.sid,self.item(6218));self.send(754,self.sid,304)
        pet=self.s.get(self.sid);self.s.set_vitals(self.sid,pet['hpmax'],pet['mpmax'])
        self.send(756,self.sid,304);now=self.s.get(self.sid)
        self.assertLess(now['hpmax'],pet['hpmax']);self.assertEqual(now['hp'],now['hpmax'])
        self.assertEqual(self.book_records(),[])
        self.send(807,self.sid,self.item(6215));self.assertEqual(self.book_records()[0]['position'],7)

    def test_bad_payload_and_battle_guard_do_not_apply(self):
        iid=self.item(6214);before=self.p.snapshot()
        with self.assertRaises((ValueError,IndexError)):
            self.g._receive(807,MessageReader(packet(807,['number']*3,[self.sid,iid,999])[3:]))
        # The ordinary dispatcher validates trailing data after this legacy
        # use-item branch; the skill-book interception must precede mutation.
        self.assertEqual(self.p.snapshot(),before)
        self.g.battle={'id':'fixture'}
        self.send(807,self.sid,iid);self.assertEqual(self.p.snapshot(),before)

    def test_learning_rolls_back_when_inventory_update_fails(self):
        iid=self.item(6214);before=self.p.snapshot()
        with self.p.transaction() as db:
            db.execute("CREATE TRIGGER fail_book BEFORE UPDATE ON inventory BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(Exception):self.b.learn(self.sid,iid)
        self.assertEqual(self.p.snapshot(),before);self.assertEqual(self.book_records(),[])

    def test_two_simultaneous_native_learning_requests_consume_once(self):
        iid=self.item(6214)
        def learn(_):
            try:
                SkillBooks(Summons(Progression(self.path,'book_owner',self.p.cid))).learn(self.sid,iid)
                return 'learned'
            except ValueError:return 'rejected'
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(learn,range(2)))
        self.assertEqual(results.count('learned'),1)
        self.assertEqual(next(x['amount'] for x in self.p.snapshot()['items'] if x['id']==iid),1)


if __name__=='__main__':unittest.main()
