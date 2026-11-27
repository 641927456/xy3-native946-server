"""Guardian lifecycle and medicine conservation on native battle requests."""
from pathlib import Path
from contextlib import closing
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from local_accounts import LocalAccounts
from native946_battle_actions import numbers
from native946_characters import Characters
from native946_codec import number,SCHEMAS
from native946_gameplay import NativeGameplay,ENEMY_ID
from native946_login import MessageReader
from native946_progression import Progression
from native946_summons import Summons


def mid(payload):return int.from_bytes(payload[1:3],'little') if payload[0]==0 else payload[0]


class BattleActionTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'fixture.sqlite3'
        LocalAccounts(self.path).register({'username':'owner946','password':'123456789'})
        self.characters=Characters(self.path);self.c=self.characters.create('owner946','战斗测试',702)
        self.g=NativeGameplay(self.c,self.characters,lambda:(),practice_trainer=True)
        self.p=self.g.progression;self.s=self.g.summons;self.sid=self.s.claim_starter()

    def call(self,kind,*args,tail=b''):
        return self.g.receive(kind,MessageReader(b''.join(number(n) for n in args)+tail))

    def clone_pet(self,name='第二只猴儿精'):
        # Fixture only; production acquisition and existing player pets are untouched.
        with self.p.transaction() as db:
            saved=dict(db.execute('SELECT * FROM summons WHERE id=?',(self.sid,)).fetchone())
            saved.pop('id');saved.update(grant_key='fixture:'+name,name=name,fight=0,show=0)
            return db.execute('INSERT INTO summons('+','.join(saved)+') VALUES('+','.join('?' for _ in saved)+')',tuple(saved.values())).lastrowid

    def medicine(self,kind=4000):return next(i for i in self.p.snapshot()['items'] if i['type']==kind)

    def test_controlled_hero_and_guardian_can_defend_in_the_same_round(self):
        from native946_role_battle import solo_fighters

        self.g.begin_battle()
        actors=solo_fighters(self.g)
        actors[self.p.cid]['controls']={'disorder':1}
        actors[self.sid]['controls']={'seal':1}
        actors[ENEMY_ID]['controls']={'seal':1}
        rejected=self.call(2176,ENEMY_ID)
        self.assertIn(98,[mid(p) for p in rejected])
        self.assertFalse(self.g.battle['orders'])
        self.assertIn(2151,[mid(p) for p in self.call(2178,1)])
        replies=self.call(2182,1)
        self.assertEqual(self.g.battle['phase'],'drawing')
        self.assertNotIn(2157,[mid(p) for p in replies])

    def test_native_guardian_escape_finishes_training_without_victory(self):
        before=self.p.snapshot()
        self.g.begin_battle()
        self.call(2178,1)
        self.assertIn(2146,[mid(p) for p in self.call(2182,2)])
        self.assertIsNone(self.g.battle)
        self.assertEqual(self.p.snapshot()['experience'],before['experience'])

    def test_native_summon_list_schema_and_parallel_eligibility(self):
        second=self.clone_pet();third=self.clone_pet('健康不足')
        with self.p.transaction() as db:db.execute('UPDATE summons SET honesty=49 WHERE id=?',(third,))
        self.g.begin_battle();reply=self.call(2317);self.assertEqual(mid(reply),2274)
        r=MessageReader(reply[3:]);count=r.take(1)[0];records=[]
        for _ in range(count):
            records.append({key:r.string().decode('gb18030') if kind=='string' else r.integer() for key,kind in SCHEMAS['zq_ctrl_summon']})
        self.assertEqual([x['summon_id'] for x in records],[self.sid,second,third])
        self.assertEqual(r.take(1),b'\x01');self.assertEqual(r.integer(),self.sid)
        self.assertEqual(r.integer(),0);self.assertEqual(r.take(1),b'\x03');self.assertEqual(r.take(3),b'\0\x01\0');r.finish()

    def test_recall_does_not_attack_and_withdrawn_pet_settles_once(self):
        self.g.begin_battle();maximum=self.g.battle['enemy_hp']
        replies=self.call(2178,7)
        self.assertIn(2137,[mid(p) for p in replies]);self.assertIsNone(self.g.battle['pet'])
        self.assertEqual(self.g.battle['enemy_hp'],maximum)
        self.assertEqual(self.g.battle['phase'],'drawing')
        self.g.finish_battle(False);self.assertIsNone(self.g.finish_battle(False))
        saved=Summons(Progression(self.path,'owner946',self.c['id'])).get(self.sid)
        self.assertEqual((saved['amity'],saved['experience'],saved['fight']),(1,0,1))

    def test_replacement_adds_native_fighter_and_both_participants_save(self):
        second=self.clone_pet();self.g.begin_battle()
        replies=self.call(2177,6,second)
        self.assertEqual(self.g.battle['pet']['id'],second)
        add=next(p for p in replies if mid(p)==2128);r=MessageReader(add[3:]);values=[]
        for i in range(24):
            values.append(r.string() if i==3 else r.take(1)[0] if i in (7,8,9) else r.take(3) if i==10 else r.integer())
        r.finish();self.assertEqual(values[:7],[second,3,3197,'第二只猴儿精'.encode('gb18030'),4,6,1])
        self.assertEqual(values[17],0)
        self.call(2184)
        before=self.g.battle['pet']['id']
        self.assertEqual(mid(self.call(2177,6,self.sid)[0]),98)
        self.assertEqual(self.g.battle['pet']['id'],before)
        self.g.finish_battle(False)
        self.assertEqual([self.s.get(sid)['amity'] for sid in (self.sid,second)],[1,1])

    def test_foreign_dead_and_unhealthy_guardians_cannot_be_summoned(self):
        other=self.characters.create('owner946','其他角色',702)
        foreign=Summons(Progression(self.path,'owner946',other['id']));foreign_id=foreign.claim_starter()
        second=self.clone_pet();self.g.begin_battle()
        for sid in (foreign_id,99999):self.assertEqual(mid(self.call(2177,6,sid)[0]),98)
        self.s.set_vitals(second,0,10)
        self.assertEqual(mid(self.call(2177,6,second)[0]),98)
        self.s.rest()
        with self.p.transaction() as db:db.execute('UPDATE summons SET honesty=49 WHERE id=?',(second,))
        self.assertEqual(mid(self.call(2177,6,second)[0]),98)
        self.assertEqual(self.g.battle['orders'],{})

    def test_hero_uses_hp_medicine_as_a_turn_with_native_vital_update(self):
        self.p.set_vitals(1,1);self.g.begin_battle();item=self.medicine()
        self.call(2185,8,item['id'],self.c['id'])
        self.assertEqual(self.medicine()['amount'],5)
        replies=self.call(2182,1)
        self.assertEqual(self.p.snapshot()['hp'],196)
        self.assertEqual(self.medicine()['amount'],4)
        hp_values=[]
        for reply in replies:
            if mid(reply)!=2158:continue
            reader=MessageReader(reply[3:]);actor=reader.integer();field=reader.integer();value=reader.integer()
            if actor==self.c['id'] and field==1:hp_values.append(value)
        self.assertIn(201,hp_values);self.assertEqual(hp_values[-1],196)
        self.assertEqual(mid(replies[-1]),2139)
        self.call(2182,1);self.assertEqual(self.medicine()['amount'],4)

    def test_pet_can_use_mp_medicine_on_hero_and_persists_the_cost(self):
        self.p.set_vitals(self.p.snapshot()['hpmax'],1);self.g.begin_battle();item=self.medicine(4001)
        self.call(2178,1);replies=self.call(2183,8,item['id'],self.c['id'])
        self.assertIn(numbers(2158,self.c['id'],4,101),replies);self.assertEqual(self.g.battle['mp'],101)
        self.assertEqual(self.medicine(4001)['amount'],4)
        self.g.finish_battle(False)
        cold=Progression(self.path,'owner946',self.c['id'])
        self.assertEqual(cold.snapshot()['mp'],101)
        self.assertEqual(next(i for i in cold.snapshot()['items'] if i['id']==item['id'])['amount'],4)

    def test_advanced_medicine_uses_client_recovery_in_a_battle_turn(self):
        with self.p.transaction() as db:
            item=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4005,8,6,2)',
                            (self.c['id'],)).lastrowid
        s=self.p.snapshot()
        self.p.set_vitals(s['hpmax'],1)
        self.g.begin_battle()
        self.call(2185,8,item,self.c['id'])
        self.assertIn(numbers(2158,self.c['id'],4,min(s['mpmax'],401)),self.call(2182,1))
        self.assertEqual(self.g.battle['mp'],min(s['mpmax'],401))
        self.assertEqual(self.medicine(4005)['amount'],1)

    def test_shared_stack_reservation_rejects_second_order_without_consuming(self):
        item=self.medicine()
        with self.p.transaction() as db:db.execute('UPDATE inventory SET amount=1 WHERE id=?',(item['id'],))
        self.p.set_vitals(1,1);self.s.set_vitals(self.sid,1,1);self.g.begin_battle()
        self.call(2185,8,item['id'],self.c['id'])
        self.assertEqual(mid(self.call(2183,8,item['id'],self.sid)[0]),98)
        self.assertEqual(self.medicine()['amount'],1);self.assertEqual(self.g.battle['hp'],1)
        self.call(2182,1)
        self.assertFalse(any(i['id']==item['id'] for i in self.p.snapshot()['items']))
        self.assertEqual(self.g.battle['hp'],196)

    def test_two_heals_of_same_target_cap_and_only_one_consumes(self):
        s=self.p.snapshot();self.p.set_vitals(s['hpmax']-10,s['mp']);self.g.begin_battle();item=self.medicine()
        maximum=self.g.battle['enemy_hp']
        self.call(2185,8,item['id'],self.c['id'])
        self.call(2183,8,item['id'],self.c['id'])
        self.assertEqual(self.medicine()['amount'],4)
        self.assertEqual(self.g.battle['hp'],s['hpmax']-5)
        self.assertEqual(self.g.battle['enemy_hp'],maximum)

    def test_invalid_targets_and_trailing_bytes_do_not_change_orders_or_items(self):
        item=self.medicine();self.g.begin_battle();before=self.p.snapshot()
        deadline=self.g.battle['input_deadline']
        replies=(self.call(2185,8,item['id'],ENEMY_ID),self.call(2185,8,item['id'],self.c['id'],tail=b'!'),
                 self.call(2183,9,338,ENEMY_ID))
        for reply in replies:self.assertEqual([mid(p) for p in reply],[98,5349,2151,2139])
        self.assertEqual(self.g.battle['input_deadline'],deadline)
        self.assertEqual(self.p.snapshot(),before);self.assertEqual(self.g.battle['orders'],{})

    def test_missing_queued_item_does_not_stall_the_battle(self):
        self.p.set_vitals(1,1);self.g.begin_battle();item=self.medicine()
        self.call(2185,8,item['id'],self.c['id'])
        with self.p.transaction() as db:db.execute('DELETE FROM inventory WHERE id=?',(item['id'],))
        replies=self.call(2182,1)
        self.assertEqual(self.g.battle['phase'],'drawing');self.assertEqual(mid(replies[-1]),2139)
        self.assertEqual(self.g.battle['hp'],0)

    def test_training_low_mana_native_orders_progress_as_normal_attacks(self):
        from native946_role_battle import solo_fighters
        snapshot=self.p.snapshot();self.p.set_vitals(snapshot['hp'],1)
        self.g.begin_battle();hero=self.c['id']
        fighters=solo_fighters(self.g)
        fighters[ENEMY_ID].update(hp=10000,hpmax=10000)
        fighters[self.sid].update(mp=37,innate_skills=[dict(stunt_id=4906,level=1)])
        replies=self.call(2185,9,6100,ENEMY_ID)
        self.assertEqual(self.g.battle['orders'][hero]['skill'],10)
        self.assertIn(2151,[mid(p) for p in replies])
        replies=self.call(2183,9,4906,ENEMY_ID)
        self.assertEqual(self.g.battle['phase'],'drawing')
        self.assertIn(2167,[mid(p) for p in replies])
        self.assertEqual((fighters[hero]['mp'],fighters[self.sid]['mp']),(1,37))

    def test_medicine_defaults_target_to_acting_pet(self):
        self.s.set_vitals(self.sid,1,1);self.g.begin_battle();item=self.medicine(4001)
        self.call(2178,1);self.call(2183,8,item['id'],0)
        self.assertEqual(self.g.battle['pet']['mp'],101)
        self.assertEqual(self.s.get(self.sid)['mp'],101)

    def test_enemy_defeated_before_medicine_turn_does_not_consume_the_item(self):
        from native946_role_battle import solo_fighters

        self.p.set_vitals(1,1);self.g.begin_battle();item=self.medicine()
        self.g.battle['enemy_hp']=1
        solo_fighters(self.g)[ENEMY_ID]['hp']=1
        self.call(2185,8,item['id'],self.c['id'])
        self.call(2181,3,ENEMY_ID)
        self.assertTrue(self.g.battle['victory'])
        self.assertEqual(self.medicine()['amount'],5);self.assertEqual(self.g.battle['hp'],1)

    def test_order_collection_avoids_writer_lock_but_resolution_is_atomic(self):
        from native946_gameplay import TRAINER_ID
        self.p.set_vitals(1,1);item=self.medicine();self.characters.enable_deferred_positions()
        self.addCleanup(self.characters.position_writer.close,2)
        self.call(1034,TRAINER_ID)
        external=sqlite3.connect(self.path)
        try:
            external.execute('BEGIN IMMEDIATE')
            replies=self.g.receive(1032,MessageReader(b'\x01'+number(TRAINER_ID)))
            self.assertIn(2151,[mid(p) for p in replies])
            replies=self.call(2185,8,item['id'],self.c['id'])
            self.assertIn(2151,[mid(p) for p in replies])
            rejected=self.call(2182,1)
            self.assertEqual([mid(p) for p in rejected],[98,5349,2151,2139])
            self.assertEqual(set(self.g.battle['orders']),{self.c['id']})
            self.assertEqual((self.g.battle['hp'],self.medicine()['amount']),(1,5))
        finally:external.rollback();external.close()
        self.call(2182,1)
        self.assertEqual((self.g.battle['hp'],self.medicine()['amount']),(196,4))

    def test_scene_return_is_built_after_committing_the_battle_result(self):
        from native946_storage import _CURRENT
        self.g.begin_battle();battle_id=self.g.battle['id'];restore=self.g.restore_scene;observed=[]
        def build_scene():
            self.assertIsNone(_CURRENT.get())
            with closing(sqlite3.connect(self.path)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM battle_results WHERE battle_id=?',(battle_id,)).fetchone()[0],1)
            observed.append(True);return restore()
        with patch.object(self.g,'restore_scene',side_effect=build_scene):replies=self.call(2178,2)
        self.assertEqual(observed,[True]);self.assertIn(2146,[mid(p) for p in replies])
        self.assertNotIn(2170,[mid(p) for p in replies])

    def test_failed_return_commit_does_not_build_or_send_a_successful_scene(self):
        self.g.begin_battle();external=sqlite3.connect(self.path)
        try:
            external.execute('BEGIN IMMEDIATE')
            with patch.object(self.g,'restore_scene',side_effect=AssertionError('Rendered before commit')):
                self.assertEqual(mid(self.call(2178,2)[0]),98)
            self.assertIsNotNone(self.g.battle)
        finally:external.rollback();external.close()
        self.assertIsInstance(self.call(2178,2),tuple);self.assertIsNone(self.g.battle)

    def test_bounded_retry_after_rollback_does_not_double_consume_medicine(self):
        from native946_storage import StorageBusy
        self.p.set_vitals(1,1);self.g.begin_battle();item=self.medicine()
        self.call(2185,8,item['id'],self.c['id'])
        save=self.g.persist_battle_vitals;attempts=[]
        def fail_once():
            save();attempts.append(True)
            if len(attempts)==1:raise StorageBusy('forced transient busy')
        with patch.object(self.g,'persist_battle_vitals',side_effect=fail_once):replies=self.call(2182,1)
        self.assertEqual(len(attempts),2);self.assertEqual(mid(replies[-1]),2139)
        self.assertEqual((self.p.snapshot()['hp'],self.medicine()['amount']),(196,4))

    def test_post_commit_panel_failure_sends_battle_fallback_without_reapplying(self):
        from native946_storage import StorageBusy
        self.p.set_vitals(1,1);self.g.begin_battle();item=self.medicine()
        self.call(2185,8,item['id'],self.c['id'])
        with patch.object(self.p,'item_packets',side_effect=StorageBusy('read busy')):replies=self.call(2182,1)
        self.assertIn(2167,[mid(p) for p in replies]);self.assertEqual(mid(replies[-1]),98)
        self.assertEqual((self.p.snapshot()['hp'],self.medicine()['amount']),(196,4))
        self.assertEqual(self.g.battle['phase'],'drawing')

    def test_transaction_owner_cache_does_not_authorize_a_different_account(self):
        with self.p.transaction():
            self.p.snapshot()
            with self.assertRaises(ValueError):Progression(self.path,'foreign_account',self.c['id'])
            self.assertEqual(self.p.snapshot()['points'],4)


if __name__=='__main__':unittest.main()
