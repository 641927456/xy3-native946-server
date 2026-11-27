"""Real dispatcher tests for native score rules, atomic claims and UI contracts."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from native946_codec import number
from native946_login import MessageReader,string_field
from native946_fashion import Fashion,DATA,RULES,record_use,score,normalized
from native946_cosmetics import Cosmetics
from native946_progression import Progression
from test_native946_npcs import NpcGameplayTests

def mid(p):return int.from_bytes(p[1:3],'little') if p[0]==0 else p[0]
def messages(p):return p if isinstance(p,tuple) else (p,)

class FashionTests(unittest.TestCase):
    setUp=NpcGameplayTests.setUp
    def send(self,kind,*args,tail=b''):
        return self.game.receive(kind,MessageReader(b''.join(number(x) for x in args)+tail))
    def service(self):return Fashion(self.game)
    def total(self):
        with self.game.progression.transaction(write=False) as db:return score(db,self.game.progression.cid)
    def add(self,kind):
        with self.game.progression.transaction() as db:Cosmetics(self.game)._add(db,kind)
        return next(i for i in self.game.progression.snapshot()['items'] if i['type']==kind)
    def seed(self,minimum):
        # Test storage only. Each row is still one real native scorable type.
        with self.game.progression.transaction() as db:
            for kind in RULES['ItemConfig']:
                record_use(db,self.game.progression.cid,int(kind))
                if score(db,self.game.progression.cid)>=minimum:break

    def test_open_panel_has_all_native_tiers_and_real_attribute(self):
        reply=self.send(4014);ids=[mid(p) for p in messages(reply)]
        self.assertIn(4012,ids);self.assertIn(695,ids)
        r=MessageReader(next(p for p in reply if mid(p)==4012)[3:]);self.assertEqual(r.integer(),0);r.finish()
        self.assertEqual([r['FashionScore'] for r in RULES['GradeConfig']],[200,500,1000,2000,5000])
        self.assertEqual(RULES['CollectConfig'][0]['Item'][0],46207)

    def test_ownership_does_not_score_repeated_use_and_unequip_score_once(self):
        item=self.add(55150);self.assertEqual(self.total(),0)
        for _ in range(4):self.send(807,0,item['id'])
        self.assertEqual(self.total(),2)
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        with cold.transaction(write=False) as db:self.assertEqual(score(db,cold.cid),2)
        self.assertEqual(cold.attribute_values()['FashionScore'],2)

    def test_normalized_permanent_variant_cannot_score_twice(self):
        with self.game.progression.transaction() as db:
            self.assertTrue(record_use(db,self.game.progression.cid,65150))
            self.assertFalse(record_use(db,self.game.progression.cid,55150))
        self.assertEqual(normalized(65150),55150);self.assertEqual(self.total(),2)

    def test_non_scoring_invalid_owner_and_expired_uses_never_add_points(self):
        item=self.add(55150)
        with self.game.progression.transaction() as db:db.execute('UPDATE cosmetic_expiry SET expires=1 WHERE item_id=?',(item['id'],))
        self.assertEqual(mid(self.send(807,0,item['id'])),98)
        self.assertEqual(mid(self.send(807,0,999999)),98);self.assertEqual(self.total(),0)

    def test_consumed_portrait_or_mount_scores_with_its_real_use(self):
        from native946_mall_uses import DATA as USES
        candidates=[int(k) for k,v in USES['rules'].items() if v['op']=='mount' and str(normalized(int(k))) in RULES['ItemConfig']]
        self.assertTrue(candidates);kind=candidates[0];item=self.add(kind)
        reply=self.send(807,0,item['id']);self.assertNotEqual(mid(messages(reply)[0]),888)
        self.assertEqual(self.total(),RULES['ItemConfig'][str(normalized(kind))]['FashionScore'])

    def test_collect_claim_is_durable_and_duplicate_rejected(self):
        self.send(807,0,self.add(55150)['id'])
        reply=self.send(4013,1);self.assertNotEqual(mid(messages(reply)[0]),888)
        before=self.game.progression.snapshot()['items']
        self.assertEqual(mid(self.send(4013,1)),98);self.assertEqual(self.game.progression.snapshot()['items'],before)
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        with cold.transaction(write=False) as db:self.assertEqual(db.execute("SELECT count(*) FROM native_fashion_claims WHERE kind='collect'").fetchone()[0],1)

    def test_tier_claim_requires_score_is_sequential_and_replay_safe(self):
        self.assertEqual(mid(self.send(4015)),98)
        self.seed(205);self.send(4015)
        before=self.game.progression.snapshot()['items']
        self.assertEqual(mid(self.send(4015)),98);self.assertEqual(self.game.progression.snapshot()['items'],before)
        self.assertEqual(sum(i['amount'] for i in before if i['type']==56029),1)

    def test_permanent_title_uses_native_owned_list_and_persists_selection(self):
        self.seed(205);self.send(4015)
        reply=self.send(746);self.assertEqual(mid(reply),700)
        r=MessageReader(reply[3:]);self.assertEqual(r.string(),b'');self.assertEqual(r.string(),b'')
        self.assertEqual(r.take(1),b'\1');self.assertEqual(r.string().decode('gb18030'),'风华初露')
        request=string_field('风华初露')+b'\1'
        self.game.receive(747,MessageReader(request))
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        from native946_fashion import title
        with cold.transaction(write=False) as db:self.assertEqual(title(db,cold.cid),'风华初露')
        self.assertEqual(mid(self.game.receive(747,MessageReader(string_field('玉山倾倒')+b'\1'))),98)

    def test_trailing_payload_never_commits_reward(self):
        self.seed(205);self.assertEqual(mid(self.send(4015,tail=b'x')),98)
        with self.game.progression.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM native_fashion_claims').fetchone()[0],0)

    def test_full_bag_rolls_back_claim_and_all_rewards(self):
        self.seed(205)
        with self.game.progression.transaction() as db:
            for p in (8,):
                for slot in range(1,25):db.execute('INSERT OR IGNORE INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',(self.game.progression.cid,4000,p,slot))
        self.assertEqual(mid(self.send(4015)),98)
        with self.game.progression.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM native_fashion_claims').fetchone()[0],0)

    def test_concurrent_duplicate_claim_commits_one_reward(self):
        self.seed(205)
        def claim(_):
            p=Progression(self.path,'npc_owner',self.game.progression.cid)
            g=SimpleNamespace(progression=p,character=dict(self.game.character),defer_packets=lambda build:None)
            try:Fashion(g).unlock();return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,range(2)))
        self.assertEqual(sorted(results),[False,True])
        with self.game.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT sum(amount) FROM inventory WHERE type=56029').fetchone()[0],1)

    def test_sunmoon_legacy_preview_and_user_selected_mall_do_not_charge(self):
        reply=self.service().sunmoon_panel();self.assertEqual(mid(reply),5408)
        r=MessageReader(reply[3:]);self.assertEqual(r.take(2),b'\0\0');self.assertEqual(r.integer(),0);self.assertEqual(r.integer(),0)
        self.assertEqual(r.take(1),b'\x06')
        for i in range(1,7):self.assertEqual((r.integer(),r.integer()),(i,0))
        r.finish();self.seed(3000)
        before=self.game.progression.snapshot()['items']
        for index in (1,2,3):self.send(5410,index)
        self.assertEqual(self.game.progression.snapshot()['items'],before)
        self.assertIn(3658,[mid(p) for p in self.send(5409,1)])

    def test_style_rejects_unowned_full_suit_and_invalid_cycle(self):
        self.assertEqual(mid(self.game.receive(5411,MessageReader(b'\1'+number(300)))),98)
        reward=DATA['sunmoon']['RewardConfig'][0]['FemaleItemList'][0][0]
        from native946_cosmetics import DATA as CATALOG
        for kind in CATALOG['sets'][str(reward)]:self.send(807,0,self.add(kind)['id'])
        reply=self.game.receive(5411,MessageReader(b'\2'+number(300)))
        self.assertIn(5414,[mid(p) for p in messages(reply)])
        self.assertEqual(self.service().style()[:2],(2,300))
        self.assertEqual(mid(self.game.receive(5411,MessageReader(b'\1'+number(5)))),98)

    def test_native_fashion_rank_self_and_page_use_current_score(self):
        self.send(807,0,self.add(55150)['id'])
        reply=self.send(3737,18,1,30);self.assertEqual(mid(reply),3732)
        r=MessageReader(reply[3:]);self.assertEqual([r.integer() for _ in range(4)],[18,1,30,1])
        self.assertEqual(r.take(1),b'\1');self.assertIn('交互测试',r.string().decode('gb18030'));r.finish()
        reply=self.send(3739,18);self.assertEqual(mid(reply),3733)
        self.assertEqual(mid(self.send(3737,18,0,30)),98)
        self.assertEqual(mid(self.send(3738,18)),98)

if __name__=='__main__':unittest.main()
