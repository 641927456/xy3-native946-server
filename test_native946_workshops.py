"""Native membership, query-only recipes, and no speculative consumption."""
from pathlib import Path
import json,unittest
from unittest.mock import patch

from native946_codec import SCHEMAS,number
from native946_login import MessageReader
from native946_workshops import DATA,Workshops,actor
from native946_progression import Progression
from native946_world import world
import test_native946_npcs as fixtures
from test_native946_npc_services import mid,menu


class WorkshopTests(unittest.TestCase):
    setUp=fixtures.NpcGameplayTests.setUp
    approach=fixtures.NpcGameplayTests.approach

    def send(self,message,*args):return self.game.receive(message,MessageReader(b''.join(number(x) for x in args)))
    def join(self,wid):return self.game.receive(2487,MessageReader(bytes([wid])))
    def choose(self,nid,index):return self.game.receive(1032,MessageReader(bytes([index])+number(nid)))
    def level(self,value):
        with self.game.progression.transaction() as db:db.execute('UPDATE characters SET level=? WHERE id=?',(value,self.game.progression.cid))
    def decode(self,p):
        r=MessageReader(p[3:]);rows=[]
        for _ in range(r.take(1)[0]):
            row={}
            for name,kind in SCHEMAS['zuofang_equip']:row[name]=r.string().decode('gb18030') if kind=='string' else r.integer()
            rows.append(row)
        r.finish();return rows

    def test_empty_native_workshop_queries_preserve_wallet_and_inventory(self):
        before=self.game.progression.snapshot()
        self.assertEqual(self.decode(self.send(3597)),[])
        self.assertEqual(self.send(289),b'\xe6\0\0\0')
        self.assertEqual(self.game.progression.snapshot(),before)

    def test_level_twenty_join_all_seven_and_cold_reload(self):
        self.level(20);self.send(3597);before=self.game.progression.snapshot()
        for wid in range(1,8):
            replies=self.join(wid);rows=self.decode(next(p for p in replies if mid(p)==3596))
            self.assertEqual({r['Id'] for r in rows},set(range(1,wid+1)))
            self.assertTrue(all(r['Level']==r['Practice']==r['Great']==0 for r in rows))
            self.assertTrue(all(r['Master']==r['Skill']=='' for r in rows))
        self.assertEqual(self.game.progression.snapshot(),before)
        p=Progression(self.path,'npc_owner',self.game.progression.cid)
        with p.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM native_workshops WHERE character_id=?',(p.cid,)).fetchone()[0],7)

    def test_duplicate_join_never_resets_progress_or_repeats_other_rewards(self):
        self.level(20);self.send(3597);self.join(2)
        # Current 946 rank descriptions go through nine, not the old help's
        # seven. Existing native ranks must round trip without truncation.
        with self.game.progression.transaction() as db:db.execute('UPDATE native_workshops SET rank=9,practice=456,achievement=7 WHERE character_id=?',(self.game.progression.cid,))
        self.join(2);rows=Workshops(self.game).records()
        self.assertEqual([(r['Id'],r['Level'],r['Practice'],r['Great']) for r in rows],[(2,9,456,7)])

    def test_underlevel_unknown_and_unsolicited_join_do_not_create_membership(self):
        self.level(20);self.assertEqual(mid(self.join(1)),98)
        self.send(3597);self.level(19);self.assertEqual(mid(self.join(1)),98)
        self.level(20)
        for wid in (0,8,255):self.assertEqual(mid(self.join(wid)),98)
        self.assertEqual(Workshops(self.game).records(),[])

    def test_expired_query_and_battle_cannot_join(self):
        self.level(20);self.send(3597);self.game.workshop_query_until=0
        self.assertEqual(mid(self.join(1)),98)
        self.send(3597);self.game.battle={'phase':'input'}
        self.assertEqual(mid(self.join(1)),98);self.assertEqual(mid(self.send(3597)),98)
        self.assertEqual(Workshops(self.game).records(),[])

    def test_leader_and_tool_npcs_match_original_shapes_and_workshop_names(self):
        for row in DATA['workshops'].values():
            leader=world().npc(row['scene'],row['leader']);tool=world().npc(row['scene'],row['tool'])
            self.assertEqual(actor(leader),(row,'leader'));self.assertEqual(actor(tool),(row,'tool'))
            self.assertEqual(leader['title'],row['name']+'坊主')
            for nid in (row['leader'],row['tool']):
                self.approach(row['scene'],nid);_,options,session,flag=menu(self.send(1034,nid))
                self.assertEqual((session,flag),(nid,1));self.assertIn('查看原生配方',options)
                self.assertEqual(any(s.startswith('加入') for s in options),nid==row['leader'])

    def test_nearby_leader_join_obeys_distance_and_durable_storage(self):
        row=DATA['workshops']['2'];self.level(20);self.approach(row['scene'],row['leader']);self.send(1034,row['leader'])
        self.game.character.update(x=0,y=0);self.assertEqual(mid(self.choose(row['leader'],1)),98)
        self.assertEqual(Workshops(self.game).records(),[])
        self.approach(row['scene'],row['leader']);self.send(1034,row['leader']);self.choose(row['leader'],1)
        self.assertEqual([r['Id'] for r in Workshops(self.game).records()],[2])

    def test_join_failure_rolls_back_record_and_ui_session(self):
        self.level(20);self.send(3597);before=self.game.workshop_query_until
        with patch.object(self.game,'defer_packets',side_effect=ValueError('fixture rollback')):self.assertEqual(mid(self.join(1)),98)
        self.assertEqual(Workshops(self.game).records(),[]);self.assertEqual(self.game.workshop_query_until,before)

    def test_all_standard_recipe_menus_use_original_names_quantities_and_cash(self):
        row=DATA['workshops']['2'];nid=row['tool'];self.approach(row['scene'],nid)
        self.send(1034,nid);self.choose(nid,2);p=self.choose(nid,1)
        _,options,_,_=menu(p)
        rows=[r for r in DATA['recipes'] if r['zuofang_id']==2 and r['equip_grade']==11]
        self.assertEqual(options,[r['result_name'] for r in rows]);before=self.game.progression.snapshot()
        packets=self.choose(nid,1);p=next(p for p in packets if mid(p)==227);r=MessageReader(p[1:])
        self.assertEqual(r.string().decode('gb18030'),rows[0]['result_name']);text=r.string().decode('gb18030');r.finish()
        self.assertIn(rows[0]['material_base_name'],text);self.assertIn('【升级现金】'+str(rows[0]['make_cash'] or 7000),text)
        self.assertIn('当前不进行材料扣除或装备生成',text);self.assertEqual(self.game.progression.snapshot(),before)

    def test_recipe_source_matches_classic_resource_and_excludes_free_variant(self):
        root=Path(__file__).parent;source=root/'analysis/native-npc-completion-20261126/configs-55f1de05-data.json'
        self.assertEqual(DATA['recipes'],json.loads(source.read_text('utf8'))[0]);self.assertEqual(len(DATA['recipes']),280)
        free=json.loads((root/'analysis/native-npc-completion-20261126/infos-55f1de05-data.json').read_text('utf8'))[0]
        self.assertNotEqual(DATA['recipes'][0]['equip_grade'],free[0]['equip_grade'])
        self.assertEqual(SCHEMAS['zuofang_equip'],[['Master','string'],['Great','number'],['Id','number'],['Level','number'],['Skill','string'],['Practice','number']])

    def test_unverified_craft_and_study_requests_fail_without_consumption(self):
        before=self.game.progression.snapshot()
        for message,fields in DATA['unavailable_requests'].items():
            payload=b''
            for field in fields:payload+=b'\0' if len(field)>2 or field[1]=='byte' else number(1)
            result=self.game.receive(int(message),MessageReader(payload));self.assertEqual(mid(result),98,message)
        self.assertEqual(self.game.progression.snapshot(),before)

    def test_incomplete_task_accept_has_explicit_no_consumption_reply(self):
        before=self.game.progression.snapshot();self.assertEqual(mid(self.send(1983,110020)),98)
        self.assertEqual(self.game.progression.snapshot(),before)
        for giver in DATA['task_givers']:
            self.approach(giver['scene'],giver['npc']);_,choices,_,_=menu(self.send(1034,giver['npc']))
            if giver['npc']==2089:
                self.assertEqual(choices[0],'领取任务链')
                before_chain=self.game.progression.snapshot();self.choose(giver['npc'],1)
                self.assertEqual(self.game.progression.snapshot(),before_chain)
                self.assertIsNone(self.game.progression.snapshot()['task_chain'])
                continue
            if giver['npc']==4102:
                self.assertEqual(choices[0],'好吧，我愿意接受大唐的任务')
                before_sanjie=self.game.progression.snapshot()
                reply=self.choose(giver['npc'],1)
                self.assertEqual(mid(reply),1025)
                _,tiers,_,_=menu(reply)
                self.assertEqual(len(tiers),8)
                self.assertEqual(tiers[-1],'算了，让我再考虑一下')
                self.assertEqual(self.game.progression.snapshot(),before_sanjie)
                continue
            self.assertEqual(choices[0],'了解任务规则')
            packets=self.choose(giver['npc'],1);p=next(p for p in packets if mid(p)==227)
            r=MessageReader(p[1:]);self.assertEqual(r.string().decode('gb18030'),giver['name'])
            text=r.string().decode('gb18030')
            self.assertIn('限时20分钟',text);r.finish()

    def test_current_activity_npcs_supersede_stale_help_entries(self):
        chain=next(r for r in DATA['task_givers'] if r['name']=='任务链')
        self.assertEqual((chain['scene'],chain['npc']),(2001,2089));self.assertIn('飞升160级',chain['description'])
        self.assertEqual(world().npc(2001,2089)['name'],'白居士')
        self.assertNotIn(3444,[r['npc'] for r in DATA['task_givers']])
        self.assertIn(2142,[r['npc'] for r in DATA['task_givers']])


if __name__=='__main__':unittest.main()
