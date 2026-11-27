"""Native inventory buttons exercise real dispatch, ownership and persistence."""
import unittest
from unittest.mock import patch
from native946_gameplay import NativeGameplay
from native946_codec import number
from native946_login import MessageReader,string_field
from native946_inventory_locks import DELAY
import test_native946_progression as fixtures

def mid(p):return int.from_bytes(p[1:3],'little') if p[0]==0 else p[0]

class InventoryButtonTests(unittest.TestCase):
    setUp=fixtures.ProgressionTests.setUp
    def game(self):return NativeGameplay(self.character,self.characters,lambda:())
    def send(self,g,kind,*args):return g.receive(kind,MessageReader(b''.join(number(a) for a in args)))
    def enter(self,g,*values,session=None,trailing=b''):
        key=session if session is not None else g.inventory_lock_session['id']
        return g.receive(1033,MessageReader(bytes([len(values)])+b''.join(string_field(v) for v in values)+number(key)+trailing))
    def first_lock(self,g,iid=1,op=1):
        self.assertEqual(mid(self.send(g,882,op,iid)),1023)
        return self.enter(g,'123456','123456')
    def test_native_lock_prompt_is_masked_and_never_returns_the_code(self):
        g=self.game();p=self.send(g,882,1,1);r=MessageReader(p[3:])
        count=r.take(1)[0]
        for _ in range(count):r.string();r.integer()
        self.assertEqual(r.take(1)[0],2);r.string();r.string()
        self.assertEqual(r.take(1)[0],2);self.assertEqual((r.integer(),r.integer()),(1,1))
        reply=self.enter(g,'123456','123456');self.assertTrue(all(b'123456' not in p for p in reply))
    def test_item_lock_persists_and_unlock_requires_correct_code(self):
        g=self.game();self.first_lock(g)
        self.assertEqual(next(i for i in g.progression.snapshot()['items'] if i['id']==1)['lock'],1)
        with self.p.transaction(write=False) as db:
            row=db.execute('SELECT salt,digest FROM native_inventory_security WHERE owner=?',(self.p.owner,)).fetchone()
            self.assertEqual((len(row['salt']),len(row['digest'])),(16,32));self.assertNotEqual(row['digest'],b'123456')
        self.send(g,882,2,1);self.assertEqual(mid(self.enter(g,'654321')),98)
        self.assertEqual(next(i for i in g.progression.snapshot()['items'] if i['id']==1)['lock'],1)
        self.enter(g,'123456');self.assertEqual(next(i for i in g.progression.snapshot()['items'] if i['id']==1)['lock'],0)
    def test_locked_item_cannot_be_discarded_and_split_keeps_both_locks(self):
        g=self.game();self.first_lock(g);before=g.progression.snapshot()['items']
        self.assertEqual(mid(self.send(g,798,1)),98)
        self.assertEqual(g.progression.snapshot()['items'],before)
        self.send(g,805,1,8,12,1)
        stack=[i for i in g.progression.snapshot()['items'] if i['type']==4000]
        self.assertEqual(sum(i['amount'] for i in stack),5);self.assertTrue(all(i['lock']==1 for i in stack))
        g.receive(802,MessageReader(bytes([len(stack)])+b''.join(number(i['id']) for i in stack)))
        stack=[i for i in g.progression.snapshot()['items'] if i['type']==4000]
        self.assertEqual([(i['amount'],i['lock']) for i in stack],[(5,1)])
    def test_native_discard_deletes_only_the_owned_bag_item(self):
        g=self.game();g.progression.item_packets();reply=self.send(g,798,1)
        self.assertIn(780,[mid(p) for p in reply]);self.assertNotIn(1,{i['id'] for i in g.progression.snapshot()['items']})
        g.progression.move_item(4,3,3);self.assertEqual(mid(self.send(g,798,4)),98)
        self.assertEqual(mid(self.send(g,798,9999999)),98)
    def test_time_lock_requires_ten_day_wait_and_code_after_application(self):
        g=self.game();self.first_lock(g,op=3);self.send(g,882,2,1);self.enter(g,'123456')
        row=next(i for i in g.progression.snapshot()['items'] if i['id']==1)
        self.assertEqual(row['lock'],2);self.assertGreater(row['lock_block_time'],0)
        self.send(g,882,1,1);self.assertEqual(mid(self.enter(g,'123456')),98)
        with patch('native946_inventory_locks.time',return_value=row['lock_block_time']+1):
            self.send(g,882,2,1);self.enter(g,'123456')
        self.assertEqual(next(i for i in g.progression.snapshot()['items'] if i['id']==1)['lock'],0)
    def test_cancel_wrong_session_and_trailing_data_never_set_a_code(self):
        g=self.game();self.send(g,882,1,1);key=g.inventory_lock_session['id']
        self.assertEqual(mid(self.enter(g,'123456','123456',session=key+1)),98)
        self.assertEqual(mid(self.enter(g,'123456','123456',trailing=b'x')),98)
        self.enter(g);self.assertIsNone(g.inventory_lock_session)
        with self.p.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM native_inventory_security').fetchone()[0],0)
    def test_five_wrong_codes_throttle_further_attempts(self):
        g=self.game();self.first_lock(g);self.send(g,882,2,1)
        for _ in range(5):self.assertEqual(mid(self.enter(g,'654321')),98)
        self.assertEqual(mid(self.enter(g,'123456')),98)
        self.assertEqual(next(i for i in g.progression.snapshot()['items'] if i['id']==1)['lock'],1)
    def test_code_reset_request_is_delayed_and_does_not_unlock_items(self):
        g=self.game();self.first_lock(g);self.send(g,883)
        with self.p.transaction(write=False) as db:
            first=db.execute('SELECT reset_at FROM native_inventory_security WHERE owner=?',(self.p.owner,)).fetchone()[0]
        self.send(g,883)
        with self.p.transaction(write=False) as db:self.assertEqual(db.execute('SELECT reset_at FROM native_inventory_security WHERE owner=?',(self.p.owner,)).fetchone()[0],first)
        self.assertEqual(next(i for i in g.progression.snapshot()['items'] if i['id']==1)['lock'],1)
    def test_guardian_lock_is_owned_and_reaches_native_list_and_detail(self):
        g=self.game();sid=g.summons.claim_starter();self.first_lock(g,iid=sid)
        s=g.summons.get(sid);self.assertEqual(g.summons.list_record(s)['Lock'],1);self.assertEqual(g.summons.info_record(s)['Lock'],1)
        self.assertEqual(mid(self.send(g,882,1,9999999)),98)
    def test_empty_card_listing_and_icon_request_have_complete_native_replies(self):
        g=self.game();p=self.send(g,1441,1);self.assertEqual(mid(p),1243)
        r=MessageReader(p[3:]);self.assertEqual(r.integer(),0);self.assertEqual(r.take(1),b'\0')
        self.assertEqual([r.integer() for _ in range(6)],[0,0,0,0,1,1]);r.finish()
        self.assertEqual(mid(self.send(g,2610)),2609)
    def test_extra_data_and_shortcut_removal_do_not_discard_the_item(self):
        g=self.game();before=g.progression.snapshot()['items']
        p=g.receive(3758,MessageReader(number(1)+b'\1'+string_field('JingPlanIndex')));self.assertEqual(mid(p),3756)
        self.send(g,799,1);self.assertEqual(g.progression.snapshot()['items'],before)
        self.assertEqual(mid(g.receive(3758,MessageReader(number(999999)+b'\0'))),98)

if __name__=='__main__':unittest.main()
