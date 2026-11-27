"""Campaign persistence, native requests, battle gates and reward atomicity."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest,json
from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_game_login import NativeGameLogin,message_id
from native946_codec import number,packet,SCHEMAS
from native946_login import MessageReader
from native946_progression import Progression,level_up_cost
from native946_sessions import LEASES
from native946_map import load_map
from native946_campaign import Campaign,CampaignBattle,CHAPTERS,DATA,current,stage,review_packet


class CampaignTests(unittest.TestCase):
    legacy_route = True

    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'campaign.sqlite3'
        LocalAccounts(self.db).register(dict(username='campaign',password='fixture-password'))
        self.chars=Characters(self.db)
        c=self.chars.create('campaign','西游测试',702,newbie=True)
        # Preserve regression coverage of roles already on the legacy route.
        if getattr(self, 'legacy_route', True):
            from native946_storage import transaction
            with transaction(self.db,write=True) as db:
                db.execute("UPDATE native_campaign_runs SET version=1 WHERE character_id=? AND chapter='newbie'",(c['id'],))
        self.login=NativeGameLogin(19381,None,number,None,self.chars);self.login.username='campaign'
        self.addCleanup(lambda:LEASES.release_port(self.db,19381))
        self.login.receive(packet(915,['number'],[c['id']]))
        self.g=self.login.gameplay;self.p=self.g.progression;self.campaign=Campaign(self.g)
    def send(self,mid,types=(),values=()):return self.login.receive(packet(mid,list(types),list(values)))
    def teleport(self,t):
        g=self.g;c=g.character;grid=load_map(t['scene']);x,y=t['arrival']
        rev=self.chars.change_scene(c['owner'],c['id'],c['map_id'],t['scene'],x,y,c.get('position_revision',0))
        c.update(map_id=t['scene'],x=x,y=y,position_revision=rev);g.grid=grid
        g.npc_position=grid.nearby(300,210) if c['map_id']==2001 else None
    def choose(self,op):
        action=next((i for i,a in enumerate(self.g.dialog_actions,1) if isinstance(a,dict) and a.get('kind')=='campaign' and a['op']==op),None)
        self.assertIsNotNone(action,(op,self.g.dialog_actions))
        return self.send(1032,['byte','number'],[action,self.g.dialog_npc])
    def open_current(self):
        c,node,row=stage(self.p.snapshot());t=c['giver'] if row['state']=='available' else node['target']
        self.teleport(t);self.send(1034,['number'],[t['npc']]);return c,node,row
    def battle_to_end(self):
        battle=self.g._wild_battle
        self.assertIsInstance(battle,CampaignBattle)
        for _ in range(35):
            if not self.g.battle:return battle
            if battle.state['phase']=='drawing':self.send(2184);continue
            for fid in sorted(battle.required()):
                actor=battle.state['fighters'][fid]
                if actor['id'] in battle.state['orders']:continue
                target=battle.enemies()[0]['id']
                self.send(2181 if actor['kind']=='pet' else 2177,['number','number'],[3,target])
        self.fail('Campaign battle did not finish within 35 turns')
    def complete_current(self):
        c,node,row=self.open_current()
        if row['state']=='available':self.choose('accept');c,node,row=self.open_current()
        self.choose('recover') if node['action']=='battle' else None
        if node['action']=='battle':
            self.open_current();self.choose('fight');self.battle_to_end()
        elif node['action']=='cinematic':
            self.choose('movie');self.send(1458,['number'],[node['movie']])
        else:self.choose('guardian' if node['action']=='guardian' else 'complete')
        after=self.p.snapshot()['campaign']['runs'][c['id']]
        self.assertEqual(after['node'],row['node']+1,(c['name'],row['node'],after,self.g.last_event))
    def qualify(self,key):
        m=CHAPTERS[key]['metadata']
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=?,experience=1000000 WHERE id=?',(m['min_grade'],self.p.cid))
            db.execute('UPDATE cultivation_state SET rebirth=?,ascension=?,sanctification=? WHERE character_id=?',(m['min_rei'],m['min_fly'],m['min_feng'],self.p.cid))
            level=m['min_grade']
            db.execute('UPDATE progression SET cor=?,mag=?,strength=?,speed=? WHERE character_id=?',(level,level,level,level,self.p.cid))
        self.g.character['level']=m['min_grade'];s=self.p.snapshot();self.p.set_vitals(s['hpmax'],s['mpmax'])
    def test_native_creation_starts_in_aolai_with_visible_persistent_task(self):
        self.assertEqual(self.g.character['map_id'],2008)
        s=self.p.snapshot();self.assertEqual(current(s)['chapter'],'newbie')
        listing=self.p.task_packets()[1]
        r=MessageReader(listing[3:]);text=r.string().decode('gb18030')
        self.assertIn('初入三界',text);self.assertIn('水月仙',text)
        rebuilt=Progression(self.db,'campaign',self.p.cid)
        self.assertEqual(rebuilt.snapshot()['campaign'],s['campaign'])
        from native946_rookie_guide import GROUPS
        group = next(index for index, row in enumerate(GROUPS, 1) if row['source_group'] == 11)
        guide = MessageReader(self.send(1474, ['number'], [group])[3:])
        self.assertEqual(guide.integer(), group)
        self.assertIn('当前目标：与迎新使者水月仙交谈', guide.string().decode('gb18030'))
        guide.finish()
    def test_suggested_task_refresh_keeps_owned_campaign_and_tracking_preferences(self):
        def trace_ids(reply):
            self.assertEqual(reply[1][:3], message_id(1978))
            self.assertEqual(reply[0][3:], reply[1][3:])
            reply=reply[1]
            reader = MessageReader(reply[3:])
            ids = []
            for _ in range(reader.take(1)[0]):
                row={key:(reader.string() if kind=='string' else reader.integer())
                     for key,kind in SCHEMAS['task_trace_info']}
                ids.append(row['id'])
            reader.finish()
            return ids

        qid = CHAPTERS['newbie']['qid']
        self.assertIn(qid, trace_ids(self.send(1989)))
        self.send(1981, ['number'], [qid])
        self.assertNotIn(qid, trace_ids(self.send(1989)))
        self.send(1990, ['number'], [qid])
        self.assertIn(qid, trace_ids(self.send(1989)))
        self.assertEqual(current(self.p.snapshot())['node'], 0)

    def test_movie_closes_old_dialogue_without_advancing_or_rewarding(self):
        chapter = CHAPTERS['111600']
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET state='completed',node=22 WHERE character_id=?", (self.p.cid,))
            db.execute("INSERT INTO native_campaign_runs VALUES(?,'111600',7,'active',1,0)", (self.p.cid,))
        self.qualify(chapter['id'])
        self.open_current()
        choice = next(i for i, action in enumerate(self.g.dialog_actions, 1) if action['op'] == 'complete')
        npc = self.g.dialog_npc
        before = self.p.snapshot()
        reply = self.choose('movie')
        reader = MessageReader(reply[1][3:])
        self.assertEqual(reply[1][:3], message_id(1276))
        self.assertEqual([reader.integer(), reader.integer(), reader.take(1)], [1179, 0, b'\x01'])
        reader.finish()
        self.assertFalse(self.g.dialog_open)
        self.assertEqual(self.g.dialog_actions, [])
        self.send(1032, ['byte', 'number'], [choice, npc])
        after = self.p.snapshot()
        self.assertEqual(before['campaign'], after['campaign'])
        self.assertEqual((before['experience'], before['gold']), (after['experience'], after['gold']))
        self.send(1034, ['number'], [npc])
        self.choose('complete')
        self.assertEqual(current(self.p.snapshot())['node'], 8)

    def test_hub_opens_current_contact_when_near_a_different_npc(self):
        chapter, node, row = self.open_current()
        self.campaign.hub(900001)
        self.assertTrue(any(action['op'] == 'open' for action in self.g.dialog_actions))
        with patch.object(self.campaign, 'validate_npc'):
            reply = self.campaign.execute(900001, dict(op='open', chapter=chapter['id'], node=row['node'], state=row['state']))
        self.assertIsNotNone(reply)
        self.assertEqual(self.g.dialog_npc, node['target']['npc'])

    def test_jinghe_legacy_save_redirects_to_palace_and_settles_native_movie_once(self):
        from native946_world import world

        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET state='completed',node=22 WHERE character_id=?", (self.p.cid,))
            db.execute("INSERT INTO native_campaign_runs VALUES(?,'111645',13,'active',1,0)", (self.p.cid,))
        self.qualify('111645')
        old_contact = world().npc(2027, 4973)
        old_arrival = load_map(2027).nearest_walkable(old_contact['x']//20, old_contact['y']//20)
        self.teleport(dict(scene=2027, arrival=old_arrival))
        self.send(1034, ['number'], [4973])
        self.assertFalse(any(isinstance(action, dict) and action.get('op')=='complete' and action.get('chapter')=='111645'
                             for action in self.g.dialog_actions))
        _, node, _ = self.open_current()
        self.assertEqual((node['target']['scene'], self.g.dialog_npc), (2003, 2158))
        self.choose('complete')
        self.assertEqual(current(self.p.snapshot())['node'], 14)

        while current(self.p.snapshot())['node'] < 19:
            self.complete_current()
        _, node, _ = self.open_current()
        self.assertEqual((node['target']['scene'], self.g.dialog_npc), (2009, 2102))
        self.send(1458, ['number'], [1184])
        self.assertEqual(current(self.p.snapshot())['node'], 19)
        self.assertFalse(any(a.get('op')=='complete' for a in self.g.dialog_actions))
        self.assertIn(packet(1275, ['number','number','string'], [1184,1,'']), self.choose('movie'))
        self.send(1458, ['number'], [1183])
        self.assertEqual(current(self.p.snapshot())['node'], 19)
        self.send(1458, ['number'], [1184])
        self.send(1458, ['number'], [1184])
        self.assertEqual(current(self.p.snapshot())['node'], 20)
        with self.p.transaction(write=False) as db:
            rows = db.execute("SELECT experience,gold FROM native_campaign_receipts WHERE character_id=? AND chapter='111645' AND node=19",
                              (self.p.cid,)).fetchall()
        self.assertEqual([tuple(row) for row in rows], [(node['reward']['experience'], node['reward']['gold'])])

    def test_white_bone_battles_use_the_three_disguises_and_return_to_flower_fruit_mountain(self):
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET state='completed',node=22 WHERE character_id=?", (self.p.cid,))
            db.execute("INSERT INTO native_campaign_runs VALUES(?,'111201',1,'active',1,0)", (self.p.cid,))
        self.qualify('111201')
        for npc_id, name in ((1720, '女子'), (1721, '老妇人'), (1722, '老头')):
            _, node, _ = self.open_current()
            self.assertEqual((node['target']['scene'], self.g.dialog_npc), (2025, npc_id))
            self.choose('recover')
            self.open_current()
            self.choose('fight')
            self.assertEqual(self.g._wild_battle.enemies()[0]['name'], name)
            self.battle_to_end()
        _, next_node, row = self.open_current()
        self.assertEqual(row['node'], 4)
        self.assertEqual((next_node['target']['scene'], self.g.dialog_npc), (2011, 2601))

    def test_lion_ridge_battle_awards_waist_token_and_stage_nine_consumes_it(self):
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET state='completed',node=22 WHERE character_id=?", (self.p.cid,))
            db.execute("INSERT INTO native_campaign_runs VALUES(?,'123847',7,'active',1,0)", (self.p.cid,))
        self.qualify('123847')
        self.complete_current()
        items = self.p.snapshot()['campaign']['items']
        self.assertEqual([(i['type'], i['amount']) for i in items if i['chapter']=='123847'], [(8862, 1)])
        _, node, _ = self.open_current()
        self.assertEqual((node['target']['scene'], self.g.dialog_npc), (2384, 6822))
        self.complete_current()
        self.assertFalse(self.p.snapshot()['campaign']['items'])

    def test_lion_ridge_legacy_handoff_and_three_king_battle(self):
        key = '123847'
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET state='completed',node=22 WHERE character_id=?", (self.p.cid,))
            db.execute("INSERT INTO native_campaign_runs VALUES(?,?,8,'active',1,0)", (self.p.cid, key))
        self.qualify(key)
        self.open_current()
        self.choose('complete')
        self.assertEqual(current(self.p.snapshot())['node'], 8)
        with self.p.transaction() as db:
            db.execute("INSERT INTO native_campaign_receipts VALUES(?,?,7,'legacy-victory',0,0,0)", (self.p.cid, key))
        self.complete_current()
        self.assertFalse(self.p.snapshot()['campaign']['items'])

        with self.p.transaction() as db:
            db.execute('UPDATE native_campaign_runs SET node=38 WHERE character_id=? AND chapter=?', (self.p.cid, key))
        self.open_current()
        self.choose('fight')
        self.assertEqual([enemy['name'] for enemy in self.g._wild_battle.enemies()], ['云鹏王', '青狮王', '白象王'])
        self.battle_to_end()
        self.assertEqual(current(self.p.snapshot())['node'], 39)

    def test_stage_reward_notice_matches_committed_balance_and_next_target(self):
        chapter,node,row=self.open_current()
        before=self.p.snapshot()
        npc=self.g.dialog_npc
        reply=self.choose('complete')
        after=self.p.snapshot()
        notices=[]
        for message in reply:
            if message.startswith(message_id(100)):
                reader=MessageReader(message[1:])
                notices.append(reader.string().decode('gb18030'))
                self.assertEqual(reader.integer(), 0)
                reader.finish()
        self.assertTrue(any('经验+'+str(node['reward']['experience']) in text and
                            '金钱+'+str(node['reward']['gold']) in text and
                            CHAPTERS['newbie']['nodes'][1]['target']['name'] in text
                            for text in notices))
        self.send(1032,['byte','number'],[1,npc])
        duplicate=self.p.snapshot()
        self.assertEqual((after['experience'],after['gold']),(duplicate['experience'],duplicate['gold']))
    def test_first_newbie_fight_enables_spell_guide_after_battle_controls(self):
        self.complete_current()
        self.open_current()
        replies = self.choose('fight')
        guide = packet(1247, ['number'], [34])
        self.assertEqual(replies.count(guide), 1)
        self.assertGreater(replies.index(guide), next(i for i, p in enumerate(replies)
                                                     if p.startswith(message_id(2151))))
        self.assertEqual(current(self.p.snapshot())['node'], 1)

    def test_complete_newbie_battles_guardian_items_and_mainline_handoff(self):
        for _ in CHAPTERS['newbie']['nodes']:self.complete_current()
        s=self.p.snapshot();self.assertEqual(s['campaign']['runs']['newbie']['state'],'completed')
        self.assertEqual(current(s)['chapter'],'111600');self.assertEqual(current(s)['state'],'available')
        self.assertEqual(len([p for p in self.g.summons.snapshot() if p['grant_key']=='campaign:newbie:guardian']),1)
        self.assertFalse(s['campaign']['items'])
        self.assertEqual((s['level'],s['experience']),(19,7001))
        self.assertIn('再获得173674经验',next(iter(__import__('native946_campaign').task_details(s).values()))['text'])
        self.open_current()
        self.assertFalse(any(action['op'] in ('accept','handoff') for action in self.g.dialog_actions))
        self.chars.settle_battle('campaign',self.p.cid,'fixture-handoff-exp',True,
                                experience=173681,gold=0,advance_quest=False)
        self.open_current()
        accept = next(i for i, action in enumerate(self.g.dialog_actions, 1) if action['op'] == 'handoff')
        npc = self.g.dialog_npc
        self.choose('handoff')
        after = self.p.snapshot()
        self.assertEqual((after['level'], after['experience']), (30,7))
        self.assertEqual(after['points'], s['points']+44)
        self.assertEqual(current(after)['state'], 'active')
        self.send(1032, ['byte','number'], [accept,npc])
        self.assertEqual(self.p.snapshot()['experience'], after['experience'])
        self.complete_current()
        self.assertEqual(current(self.p.snapshot())['node'],1)

    def test_newbie_achievement_requires_final_campaign_settlement(self):
        from native946_achievements import NEWBIE_ACHIEVEMENT

        aid = NEWBIE_ACHIEVEMENT
        for _ in CHAPTERS['newbie']['nodes'][:-1]:
            self.complete_current()
        before = self.p.snapshot()
        self.assertEqual(before['campaign']['runs']['newbie']['state'], 'active')
        self.assertTrue(self.send(3482, ['number'], [aid]).startswith(message_id(98)))
        self.assertNotIn(str(aid).encode(), self.send(3483, ['number'], [self.p.cid])[0])
        self.complete_current()
        self.assertIn(str(aid).encode(), self.send(3483, ['number'], [self.p.cid])[0])

        previous = self.p.snapshot()['skills']
        self.assertIn(6100,previous)
        replies = self.send(3482, ['number'], [aid])
        if 6107 not in previous:
            self.assertTrue(replies.startswith(message_id(1020)) if isinstance(replies,bytes)
                            else replies[0].startswith(message_id(1020)))
            session = self.g._achievement_confirm[0]
            replies = self.send(1031, ['byte','number'], [1,session])
        self.assertEqual(replies[0], packet(6, ['number'], [aid]))
        claimed = self.p.snapshot()['skills']
        self.assertEqual({key: claimed[key] - previous.get(key, 0) for key in (6100, 6107)},
                         {6100: 200, 6107: 200})
        self.assertEqual(self.send(3482, ['number'], [aid])[0], replies[0])
        self.assertEqual(self.p.snapshot()['skills'], claimed)

    def test_mainline_and_stale_dialogue_cannot_award_ascension(self):
        chapter = CHAPTERS['111553']
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET state='completed',node=22 WHERE character_id=?", (self.p.cid,))
            db.executemany('INSERT INTO native_campaign_runs VALUES(?,?,?,?,1,0)',
                           [(self.p.cid, chapter['previous'], len(CHAPTERS[chapter['previous']]['nodes']), 'completed'),
                            (self.p.cid, chapter['id'], 0, 'available')])
            db.execute('UPDATE characters SET level=139 WHERE id=?', (self.p.cid,))
            db.execute('UPDATE cultivation_state SET rebirth=3 WHERE character_id=?', (self.p.cid,))
        self.g.sync_progression()
        self.open_current()
        self.assertFalse(any(action['op'] == 'cultivate' for action in self.g.dialog_actions))
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=140 WHERE id=?', (self.p.cid,))
        self.open_current()
        self.assertFalse(any(action['op'] == 'cultivate' for action in self.g.dialog_actions))
        self.g.dialog_actions.append(dict(kind='campaign', op='cultivate', chapter=chapter['id'], node=0, state='available'))
        reply = self.choose('cultivate')
        self.assertTrue(reply.startswith(message_id(98)))
        saved = self.p.snapshot()
        self.assertEqual((saved['rebirth'], saved['ascension'], saved['sanctification'], saved['level']), (3, 0, 0, 140))
        self.assertEqual(current(saved)['state'], 'available')

    def test_newbie_story_cannot_raise_character_stage_caps(self):
        self.assertEqual(level_up_cost(99, 100), 100**3 + 50)
        for rebirth, cap in ((0, 100), (1, 120), (2, 140)):
            with self.subTest(rebirth=rebirth):
                cost = level_up_cost(cap - 1, cap, cultivation={'rebirth': rebirth})
                with self.p.transaction() as db:
                    db.execute('UPDATE cultivation_state SET rebirth=? WHERE character_id=?', (rebirth, self.p.cid))
                    db.execute('UPDATE characters SET level=?,experience=? WHERE id=?', (cap - 1, 10000000, self.p.cid))
                self.assertEqual(self.g.auto_level(), 1)
                self.assertTrue(self.send(748).startswith(message_id(98)))
                saved = self.p.snapshot()
                self.assertEqual((saved['level'], saved['experience']), (cap, 10000000 - cost))
                self.assertEqual(current(saved)['chapter'], 'newbie')

    def test_review_chapter_award_requires_completed_group_and_remembers_each_claim(self):
        def complete(key):
            with self.p.transaction() as db:
                db.execute("INSERT INTO native_campaign_runs VALUES(?,?,?,'completed',1,0)",
                           (self.p.cid, key, len(CHAPTERS[key]['nodes'])))

        complete('111625')
        denied = self.send(4348, ['number'], [2])
        self.assertEqual(denied[0], packet(4340, ['number','string','string'], [20,'{}','{}']))
        complete('111645')
        for _ in range(2):
            result = self.send(4348, ['number'], [2])
            self.assertEqual(result[0], packet(4340, ['number','string','string'], [70,'{[2]=1}','{}']))
        self.assertEqual(self.send(4345), result[0])

    def test_review_milestone_full_bag_and_duplicate_claim_share_one_receipt(self):
        with self.p.transaction() as db:
            # Model a character who finished chapters before the receipt table was added.
            for key in DATA['mainline_order'][:15]:
                db.execute("INSERT INTO native_campaign_runs VALUES(?,?,?,'completed',1,0)",
                           (self.p.cid, key, len(CHAPTERS[key]['nodes'])))
            occupied = {r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8', (self.p.cid,))}
            for slot in set(range(1,25)) - occupied:
                db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)', (self.p.cid, slot))
        self.assertEqual(self.send(4345), packet(4340, ['number','string','string'], [300,'{}','{}']))
        expected = packet(4340, ['number','string','string'], [300,'{}','{[1]=1}'])
        for _ in range(2):
            self.assertEqual(self.send(4347, ['number'], [1])[0], expected)
        rebuilt = Progression(self.db, 'campaign', self.p.cid)
        self.assertEqual(len(rebuilt.snapshot()['campaign']['pending']), 1)
        from native946_campaign import deliver
        with self.p.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=? AND page=8 AND slot=24', (self.p.cid,))
            self.assertEqual(deliver(db, self.p.cid), (1, 0))
        self.assertEqual(self.send(4347, ['number'], [1])[0], expected)
        self.assertEqual(sum(i['amount'] for i in self.p.snapshot()['items'] if i['type']==46258), 1)

    def test_review_receipts_schema_migrates_already_initialized_role(self):
        with self.p.transaction() as db:
            db.execute('DROP TABLE native_campaign_review_claims')
        reopened = Progression(self.db, 'campaign', self.p.cid)
        with reopened.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_campaign_review_claims').fetchone()[0], 0)

    def test_mirror_biopic_recovers_legacy_reward_and_consumes_exactly_one_key(self):
        from native946_campaign import BIOPIC_CHAPTER, BIOPIC_INDEX, BIOPIC_KEY, biopic_panel

        self.assertTrue(self.send(4181, ['number'] * 2, [11, BIOPIC_INDEX]).startswith(message_id(98)))
        self.assertEqual(self.send(4181, ['number'] * 2, [1, BIOPIC_INDEX]), biopic_panel(0))
        with self.p.transaction() as db:
            db.execute("INSERT INTO native_campaign_runs VALUES(?,?,?,'completed',1,0)",
                       (self.p.cid, BIOPIC_CHAPTER, len(CHAPTERS[BIOPIC_CHAPTER]['nodes'])))
        first = self.send(4181, ['number'] * 2, [1, BIOPIC_INDEX])
        self.assertEqual(first[-1], biopic_panel(0))
        self.assertEqual(self.send(4181, ['number'] * 2, [1, BIOPIC_INDEX]), biopic_panel(0))
        self.assertEqual(sum(i['amount'] for i in self.p.snapshot()['items'] if i['type'] == BIOPIC_KEY), 1)
        unlocked = self.send(4181, ['number'] * 2, [11, BIOPIC_INDEX])
        self.assertEqual(unlocked[-1], biopic_panel(1))
        self.assertFalse(any(i['type'] == BIOPIC_KEY for i in self.p.snapshot()['items']))
        self.assertEqual(self.send(4181, ['number'] * 2, [11, BIOPIC_INDEX]), biopic_panel(1))
        self.assertEqual(self.send(4181, ['number'] * 2, [1, BIOPIC_INDEX]), biopic_panel(1))

    def test_mirror_final_stage_grants_key_and_material_in_the_same_settlement(self):
        from native946_campaign import BIOPIC_CHAPTER, BIOPIC_KEY

        chapter = CHAPTERS[BIOPIC_CHAPTER]
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET state='completed',node=? WHERE character_id=?",
                       (len(CHAPTERS['newbie']['nodes']), self.p.cid))
            db.execute("INSERT INTO native_campaign_runs VALUES(?,?,?,'active',1,0)",
                       (self.p.cid, BIOPIC_CHAPTER, len(chapter['nodes']) - 1))
        self.qualify(BIOPIC_CHAPTER)
        self.complete_current()
        s = self.p.snapshot()
        self.assertIsNone(current(s))
        for item_type in (47934, BIOPIC_KEY):
            self.assertEqual(sum(i['amount'] for i in s['items'] if i['type'] == item_type), 1)
        self.assertFalse(self.campaign.advance(BIOPIC_CHAPTER, len(chapter['nodes']) - 1))
    def test_real_native_create_packet_enrolls_only_created_character(self):
        legacy=self.chars.create('campaign','旧角色',701)
        self.assertEqual(Progression(self.db,'campaign',legacy['id']).snapshot()['campaign']['runs'],{})
        self.login.receive(packet(913,['string','number','number','byte'],['新建角色',701,0,0]))
        new=self.login.created_character;self.assertEqual(new['map_id'],2008)
        self.assertEqual(Progression(self.db,'campaign',new['id']).snapshot()['campaign']['runs']['newbie']['node'],0)
    def test_battle_cannot_be_replaced_by_dialogue_or_forged_receipt(self):
        self.complete_current();self.open_current()
        with self.assertRaisesRegex(ValueError,'尚未完成'):self.campaign.advance('newbie',1,battle=object())
        s=self.p.snapshot();before=s['experience']
        self.g.dialog_actions=[dict(kind='campaign',op='complete',chapter='newbie',node=1,state='active')]
        result=self.send(1032,['byte','number'],[1,self.g.dialog_npc])
        self.assertTrue(result.startswith(message_id(98)))
        self.assertEqual(self.p.snapshot()['experience'],before);self.assertEqual(current(self.p.snapshot())['node'],1)
    def test_escape_retry_does_not_advance_or_pay(self):
        self.complete_current();self.open_current();before=self.p.snapshot()
        self.choose('fight');self.send(2178,['number'],[2])
        self.assertIsNone(self.g.battle)
        after=self.p.snapshot();self.assertEqual(current(after)['node'],1)
        self.assertEqual((before['experience'],before['gold']),(after['experience'],after['gold']))
        self.complete_current()
    def test_defeat_and_disconnect_keep_stage_without_rewards(self):
        self.complete_current();self.open_current();before=self.p.snapshot()['experience']
        self.choose('fight');battle=self.g._wild_battle
        battle.state['fighters'][self.p.cid]['hp']=1
        battle.enemies()[0].update(attack=10000,initiative=10000)
        self.send(2178,['number'],[1])
        reply = self.send(2184)
        retry_id = CHAPTERS['newbie']['qid']
        self.assertIn(packet(5432, ['number'], [retry_id]), reply)
        self.assertIsNone(self.g.battle);self.assertEqual(current(self.p.snapshot())['node'],1)
        self.assertEqual(self.p.snapshot()['experience'],before)
        self.assertTrue(self.send(5438, ['number'], [retry_id + 1]).startswith(message_id(98)))
        self.assertTrue(self.send(5438, ['number'], [retry_id]).startswith(message_id(1025)))
        self.choose('recover')
        self.send(5438, ['number'], [retry_id])
        self.assertIsInstance(self.g._wild_battle, CampaignBattle)
        self.assertIsNone(self.g._campaign_retry)
        self.assertTrue(self.send(5438, ['number'], [retry_id]).startswith(message_id(98)))
        self.g.party_service.detach(self.p.cid)
        self.assertIsNone(self.g.battle)
        self.assertEqual(current(Progression(self.db,'campaign',self.p.cid).snapshot())['node'],1)
        self.assertEqual(self.p.snapshot()['experience'],before)

    def test_story_item_preview_only_marks_delivered_reward_on_own_completed_run(self):
        from native946_progression import lua_data

        task_id, item_type = 125200, 47934
        def expected(got):
            return packet(5445, ['number', 'string'], [
                task_id, lua_data([dict(ItemType=item_type, Amount=1, IsGot=got)])])
        self.assertEqual(self.send(5444, ['number'], [task_id]), expected(0))
        with self.p.transaction() as db:
            db.execute("INSERT INTO native_campaign_runs VALUES(?,'125200',?,'completed',1,0)",
                       (self.p.cid, len(CHAPTERS['125200']['nodes'])))
            db.execute("INSERT INTO native_campaign_pending VALUES(?,'125200',47934,1)", (self.p.cid,))
        before = self.p.snapshot()
        self.assertEqual(self.send(5444, ['number'], [task_id]), expected(0))
        self.assertEqual(self.p.snapshot(), before)
        from native946_campaign import deliver
        with self.p.transaction() as db:
            self.assertEqual(deliver(db, self.p.cid), (1, 0))
        self.assertEqual(self.send(5444, ['number'], [task_id]), expected(1))
        self.assertEqual(self.send(5444, ['number'], [task_id]), expected(1))
        self.assertEqual(sum(i['amount'] for i in self.p.snapshot()['items'] if i['type'] == item_type), 1)
        self.assertTrue(self.send(5444, ['number', 'number'], [task_id, 0]).startswith(message_id(98)))
    def test_native_recap_rejects_unseen_nodes_and_never_reawards_completed_nodes(self):
        c=CHAPTERS['111600']
        self.assertTrue(self.send(4346,['number','number'],[1,1]).startswith(message_id(98)))
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET state='completed',node=22 WHERE character_id=?",(self.p.cid,))
            db.execute("INSERT INTO native_campaign_runs VALUES(?,'111600',1,'active',1,0)",(self.p.cid,))
        trace = next(row for row in self.p.task_records()[0] if row['id'] == c['qid'])
        self.assertEqual(trace['title'], '#G【主线】#Y美猴王')
        self.assertEqual(trace['txt'], '与#u#R孙悟空#n交谈。#Y（建议将飞行旗坐标记录在此地，以免来回奔波。）#n')
        self.assertEqual((trace['scene'],trace['x'],trace['y']),
                         tuple(c['nodes'][1]['target'][key] for key in ('scene','x','y')))
        before=self.p.snapshot()['experience']
        reply=self.send(4346,['number','number'],[1,1])
        self.assertTrue(any(p.startswith(message_id(98)) for p in reply))
        self.assertEqual(self.p.snapshot()['experience'],before)
        self.assertTrue(self.send(4346,['number','number'],[1,2]).startswith(message_id(98)))
    def test_recap_does_not_unlock_other_chapters_with_the_same_head(self):
        def completed_heads(*keys):
            snapshot = {'campaign': {'runs': {key: {'state': 'completed'} for key in keys}}}
            reader = MessageReader(review_packet(snapshot)[3:])
            return [reader.integer() for _ in range(reader.take(1)[0])]

        first, second = '111625', '111645'
        self.assertNotIn(111625, completed_heads(first))
        self.assertEqual(completed_heads(first, second), [111625])
    def test_stale_menu_and_trailing_bytes_do_not_advance(self):
        self.open_current();old=dict(self.g.dialog_actions[0]);nid=self.g.dialog_npc
        result=self.send(1032,['byte','number','number'],[1,nid,77])
        self.assertTrue(result.startswith(message_id(98)))
        self.assertEqual(current(self.p.snapshot())['node'],0)
        self.choose('complete');before=self.p.snapshot()['experience']
        self.g.dialog_actions=[old];self.g.dialog_open=True;self.g.dialog_npc=nid
        result=self.send(1032,['byte','number'],[1,nid])
        self.assertTrue(result.startswith(message_id(98)))
        self.assertEqual(self.p.snapshot()['experience'],before)
        self.assertFalse(self.campaign.advance('newbie',0))
    def test_duplicate_dialog_answer_does_not_turn_into_a_travel_command(self):
        self.open_current()
        npc = self.g.dialog_npc
        self.choose('complete')
        position = tuple(self.g.character[k] for k in ('map_id', 'x', 'y'))
        self.send(1032, ['byte', 'number'], [1, npc])
        self.assertEqual(tuple(self.g.character[k] for k in ('map_id', 'x', 'y')), position)
        self.assertEqual(current(self.p.snapshot())['node'], 1)
        self.assertFalse(self.g.dialog_open)
        self.open_current()
        self.choose('hub')
        self.choose('travel')
        target = CHAPTERS['newbie']['nodes'][1]['target']
        self.assertTrue(self.campaign.near(target))
        self.send(1034, ['number'], [target['npc']])
        self.choose('fight')
        self.battle_to_end()
        self.assertEqual(current(self.p.snapshot())['node'], 2)
    def test_reward_failure_rolls_back_stage_currency_and_quest_items(self):
        self.open_current();before=self.p.snapshot()
        with patch.object(self.chars,'settle_battle',side_effect=RuntimeError('fixture write failure')):
            with self.assertRaises(RuntimeError):self.campaign.advance('newbie',0)
        after=self.p.snapshot()
        self.assertEqual(before['campaign'],after['campaign']);self.assertEqual(before['gold'],after['gold'])
        self.assertEqual(before['experience'],after['experience'])
    def test_relogin_preserves_quest_item_until_correct_delivery(self):
        for _ in range(11):self.complete_current()
        s=self.p.snapshot();self.assertEqual(s['campaign']['items'][0]['type'],8465)
        reconstructed=Progression(self.db,'campaign',self.p.cid)
        self.assertEqual(reconstructed.snapshot()['campaign']['items'],s['campaign']['items'])
        self.complete_current();self.assertFalse(self.p.snapshot()['campaign']['items'])
    def test_late_mainline_cannot_skip_predecessor_or_native_grade(self):
        c=CHAPTERS['125200'];self.teleport(c['giver'])
        with self.assertRaises(ValueError):self.campaign.accept('125200')
        self.assertEqual(current(self.p.snapshot())['chapter'],'newbie')
    def test_full_bag_retains_completion_rewards_and_claims_each_once(self):
        key='111600';c=CHAPTERS[key]
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET state='completed',node=? WHERE character_id=?",(len(CHAPTERS['newbie']['nodes']),self.p.cid))
            db.execute("INSERT INTO native_campaign_runs VALUES(?,?,?,'active',1,0)",(self.p.cid,key,len(c['nodes'])-1))
            occupied={r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.p.cid,))}
            for slot in set(range(1,25))-occupied:db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)',(self.p.cid,slot))
        self.qualify(key);self.complete_current()
        self.assertEqual(len(self.p.snapshot()['campaign']['pending']),2)
        with self.p.transaction() as db:db.execute('DELETE FROM inventory WHERE character_id=? AND page=8 AND slot IN (23,24)',(self.p.cid,))
        self.open_current()
        npc = self.g.dialog_npc
        self.choose('hub')
        selection = next(i for i, action in enumerate(self.g.dialog_actions, 1) if action['op'] == 'claim')
        self.choose('claim')
        self.send(1032, ['byte', 'number'], [selection, npc])
        s=self.p.snapshot();self.assertFalse(s['campaign']['pending'])
        for kind in (4017,4018):self.assertEqual(sum(i['amount'] for i in s['items'] if i['type']==kind),3)
    def test_delivery_uses_open_pages_and_does_not_merge_expiring_stacks(self):
        from native946_campaign import deliver
        with self.p.transaction() as db:
            db.execute("INSERT INTO cosmetic_features VALUES(?, 'bag_item', 1, 0)", (self.p.cid,))
            occupied = {r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8', (self.p.cid,))}
            for slot in sorted(set(range(1, 25)) - occupied):
                db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)', (self.p.cid, slot))
            expiring = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,46631,16,1,1)', (self.p.cid,)).lastrowid
            db.execute('INSERT INTO cosmetic_expiry VALUES(?,1)', (expiring,))
            db.execute("INSERT INTO native_campaign_pending VALUES(?, '124100', 46631, 501)", (self.p.cid,))
            db.execute("INSERT INTO native_campaign_pending VALUES(?, '111799', 65228, 1)", (self.p.cid,))
            with patch('native946_campaign.time', return_value=1000):
                self.assertEqual(deliver(db, self.p.cid), (502, 0))
            stacks = list(db.execute('SELECT amount FROM inventory WHERE character_id=? AND type=46631 ORDER BY slot', (self.p.cid,)))
            self.assertEqual([row[0] for row in stacks], [1, 500, 1])
            expires = db.execute('SELECT e.expires FROM inventory i JOIN cosmetic_expiry e ON e.item_id=i.id '
                                 'WHERE i.character_id=? AND i.type=65228', (self.p.cid,)).fetchone()[0]
            self.assertEqual(expires, 1000 + 180 * 86400)
            self.assertEqual(deliver(db, self.p.cid), (0, 0))
    def test_battle_entry_failure_restores_actor_and_registry(self):
        self.complete_current();self.open_current()
        with patch.object(CampaignBattle,'entry',side_effect=RuntimeError('fixture encode failure')):
            with self.assertRaises(RuntimeError):self.choose('fight')
        self.assertIsNone(self.g.battle);self.assertNotIn(self.p.cid,self.g.party_service.battles)
        self.complete_current()
    def test_every_recap_node_has_safe_distinct_binding_and_private_ids(self):
        self.assertEqual(len(CHAPTERS),38)
        self.assertEqual(sum(len(c['nodes']) for k,c in CHAPTERS.items() if k!='newbie'),1057)
        self.assertEqual(len({c['qid'] for c in CHAPTERS.values()}),38)
        for c in CHAPTERS.values():
            self.assertNotEqual(c['qid'],c.get('metadata',{}).get('id'))
            for n in c['nodes']:
                self.assertEqual(n['target']['name'],n['target']['name'].strip())
                self.assertGreaterEqual(len(n['target']['name']),2)
                self.assertTrue(load_map(n['target']['scene']).walkable(*n['target']['arrival']))
                self.assertEqual(n['source_type']==2,n['action']=='battle')
    def test_all_mainline_nodes_and_chapter_item_rewards_resume_and_complete(self):
        for _ in CHAPTERS['newbie']['nodes']:self.complete_current()
        for key in DATA['mainline_order']:
            self.open_current()
            c=CHAPTERS[key]
            s=self.p.snapshot()
            stage_gate = tuple(c['metadata']['min_'+stage] for stage in ('rei','fly','feng'))
            actual = tuple(s[stage] for stage in ('rebirth','ascension','sanctification'))
            # Cultivation trials are exercised separately. Seed their completed
            # stage here to keep this test focused on mainline settlement.
            if any(value < gate for value, gate in zip(actual, stage_gate)):
                with self.p.transaction() as db:
                    db.execute('UPDATE cultivation_state SET rebirth=?,ascension=?,sanctification=? WHERE character_id=?',
                               (*stage_gate, self.p.cid))
                    if stage_gate[1]:
                        db.execute("INSERT OR IGNORE INTO native_campaign_runs VALUES(?,'222374',3,'completed',?,0)",
                                   (self.p.cid, DATA['version']))
                s=self.p.snapshot()
            required=level_up_cost(s['level'],c['metadata']['min_grade'],cultivation=s)
            # This tests all story nodes. Earn the level gate's shortfall
            # outside the story, then level up through the native request.
            if required>s['experience']:
                self.chars.settle_battle('campaign',self.p.cid,'fixture-level-gate:'+key,True,
                                         experience=required-s['experience'],gold=0,advance_quest=False)
            while self.p.snapshot()['level']<c['metadata']['min_grade']:
                before=self.p.snapshot()['level'];self.send(748)
                self.assertEqual(self.p.snapshot()['level'],before+1,('Insufficient campaign XP',key,before,self.p.snapshot()['experience']))
            for _ in c['nodes'][:-1]:self.complete_current()
            before = self.p.snapshot()
            self.complete_current()
            after = self.p.snapshot()
            self.assertEqual(after['campaign']['runs'][key]['state'],'completed')
            for kind, amount in c['completion_items'].items():
                totals = [sum(item['amount'] for item in (*s['items'], *s['campaign']['pending'])
                              if item['type'] == int(kind)) for s in (before, after)]
                self.assertEqual(totals[1] - totals[0], amount, (c['name'], kind))
        s=self.p.snapshot();self.assertIsNone(current(s))
        self.assertEqual(sum(i['amount'] for i in (*s['items'], *s['campaign']['pending']) if i['type'] == 90001), 1)
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM native_campaign_receipts WHERE character_id=? AND chapter<>'newbie'",(self.p.cid,)).fetchone()[0],1057)
        wire = review_packet(s)
        r = MessageReader(wire[3:])
        done = [r.integer() for _ in range(r.take(1)[0])]
        expected = {c['review_first'] for key, c in CHAPTERS.items() if key != 'newbie'}
        self.assertEqual(len(done), len(expected))
        self.assertEqual(set(done), expected)
        for chapter in CHAPTERS.values():
            if chapter['id'] != 'newbie':
                self.send(4348, ['number'], [chapter['source_recap_index']])
        status = MessageReader(self.send(4345)[3:])
        self.assertEqual(status.integer(), 1850)
        for index in range(1, 7):
            reply = self.send(4347, ['number'], [index])[0]
            self.assertTrue(reply.startswith(message_id(4340)))
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM native_campaign_review_claims WHERE kind='score'").fetchone()[0], 6)


class CultivationQuestTests(unittest.TestCase):
    setUp = CampaignTests.setUp
    send = CampaignTests.send
    teleport = CampaignTests.teleport
    choose = CampaignTests.choose
    battle_to_end = CampaignTests.battle_to_end

    def level(self, value):
        with self.p.transaction() as db:
            delta = value - db.execute('SELECT level FROM characters WHERE id=?', (self.p.cid,)).fetchone()[0]
            db.execute('UPDATE characters SET level=? WHERE id=?', (value, self.p.cid))
            db.execute('UPDATE progression SET cor=cor+?,mag=mag+?,strength=strength+?,speed=speed+?,points=points+? '
                       'WHERE character_id=?', (delta, delta, delta, delta, delta * 4, self.p.cid))

    def open_trial_node(self):
        chapter, node, row = stage(self.p.snapshot(), True)
        self.teleport(node['target'])
        self.send(1034, ['number'], [node['target']['npc']])
        return chapter, node, row

    def test_cultivation_quests_run_from_native_acceptance_through_promotion(self):
        from native946_campaign import TRIALS, task_details
        from native946_cultivation import level_cap

        for key, entry, finish, expected, fights, start_level, resistance in (
            ('912101', 98, 100, (1, 0, 0), 4, 30, 30),
            ('912201', 118, 120, (2, 0, 0), 6, 45, 46),
            ('111321', 138, 140, (2, 1, 0), 9, 120, 65),
            ('222374', 160, 160, (2, 1, 0), 1, None, 0),
            ('222400', 165, 165, (2, 1, 1), 7, None, 45),
        ):
            with self.subTest(chapter=key):
                chapter = TRIALS[key]
                self.level(entry)
                if key == '222374':
                    with self.p.transaction() as db:
                        db.execute('UPDATE characters SET experience=20000 WHERE id=?', (self.p.cid,))
                    self.assertEqual(level_cap(self.p.snapshot()), 160)
                    self.assertTrue(self.send(748).startswith(message_id(98)))
                    self.assertEqual(self.g.auto_level(), 0)
                self.teleport(chapter['giver'])
                if key in ('912101', '222374', '222400'):
                    self.send(1034, ['number'], [chapter['giver']['npc']])
                    self.choose('accept')
                else:
                    self.send(1492, ['number'], [int(key)])
                initial = self.p.snapshot()
                self.assertEqual(current(initial)['chapter'], 'newbie')
                self.assertEqual(len(task_details(initial)), 2)
                self.assertEqual(current(initial, True)['chapter'], key)
                self.assertTrue(self.send(1986, ['number'], [chapter['qid']]).startswith(message_id(1971)))
                xp = gold = battles = 0
                for idx, expected_node in enumerate(chapter['nodes']):
                    _, node, row = self.open_trial_node()
                    self.assertEqual((row['node'], node), (idx, expected_node))
                    if node['action'] == 'battle':
                        self.choose('recover')
                        self.open_trial_node()
                        self.choose('fight')
                        battle = self.g._wild_battle
                        self.assertEqual([e['name'] for e in battle.enemies()], [e['name'] for e in node['roster']])
                        self.battle_to_end()
                        battles += 1
                    else:
                        if idx == len(chapter['nodes']) - 1 and entry < finish and self.p.snapshot()['level'] < finish:
                            blocked = self.choose('complete')
                            self.assertTrue(blocked.startswith(message_id(98)))
                            self.assertEqual(current(self.p.snapshot(), True)['node'], idx)
                            self.level(finish)
                            self.open_trial_node()
                        reply = self.choose('complete')
                        if idx == len(chapter['nodes']) - 1 and key != '222374':
                            self.assertTrue(any(value.startswith(message_id(1590)) for value in reply))
                    xp += node['reward']['experience']
                    gold += node['reward']['gold']
                saved = self.p.snapshot()
                self.assertEqual(battles, fights)
                self.assertEqual(tuple(saved[k] for k in ('rebirth','ascension','sanctification')), expected)
                self.assertEqual(self.g.character['appearance']['halo'], 101 if expected[2] else 100 if expected[1] else 0)
                if start_level is not None:
                    self.assertEqual((saved['level'], saved['experience']), (start_level, chapter['nodes'][-1]['reward']['experience']))
                    self.assertEqual(saved['points'], 4 * start_level + 60 * sum(expected[:2]))
                else:
                    self.assertGreaterEqual(saved['level'], finish)
                self.assertEqual(saved['gold'], initial['gold'] + gold)
                self.assertLessEqual(saved['experience'], initial['experience'] + xp)
                if key == '222400':
                    self.assertGreaterEqual(saved['points'] - initial['points'], 60)
                self.assertEqual(saved['cultivation_state']['earned_points'] - initial['cultivation_state']['earned_points'],
                                 resistance)
                self.assertEqual(saved['campaign']['runs'][key]['state'], 'completed')
                self.assertEqual(saved['campaign']['items'], [])
                self.assertIsNone(current(saved, True))
                self.assertEqual(current(saved)['node'], 0)
                self.assertFalse(self.campaign.advance(key, len(chapter['nodes']) - 1))
                self.assertEqual(self.p.snapshot()['experience'], saved['experience'])
                if key == '222374':
                    self.assertEqual(level_cap(saved), 180)
                    self.assertEqual((saved['level'], saved['experience']), (160, 20000))
                    self.send(748)
                    self.assertEqual(self.p.snapshot()['level'], 161)

    def test_old_store_keeps_mainline_and_resumes_trial_after_leaving_a_battle(self):
        from native946_campaign import TRIALS

        self.level(120)
        with self.p.transaction() as db:
            db.execute('UPDATE cultivation_state SET rebirth=1 WHERE character_id=?', (self.p.cid,))
            db.execute('DROP INDEX native_campaign_mainline_current')
            db.execute('DROP INDEX native_campaign_trial_current')
            db.execute("CREATE UNIQUE INDEX native_campaign_one_current ON native_campaign_runs(character_id) "
                       "WHERE state IN ('available','active')")
        chapter = TRIALS['912201']
        self.teleport(chapter['giver'])
        self.send(1492, ['number'], [int(chapter['id'])])
        self.open_trial_node()
        self.choose('complete')
        self.open_trial_node()
        self.choose('fight')
        self.g._wild_battle.finish(False)
        self.assertNotIn(5349,[int.from_bytes(p[1:3],'little') if p[0]==0 else p[0]
                              for p in self.g.party_service.drain(self.g)])
        cold = Progression(self.db, 'campaign', self.p.cid).snapshot()
        self.assertEqual((current(cold)['node'], current(cold, True)['node']), (0, 1))
        self.assertEqual(cold['rebirth'], 1)
        self.assertEqual(cold['experience'], chapter['nodes'][0]['reward']['experience'])
        self.open_trial_node()
        self.choose('fight')
        self.battle_to_end()
        self.assertEqual(current(self.p.snapshot(), True)['node'], 2)

    def test_third_rebirth_can_find_and_accept_the_same_ascension_quest(self):
        from native946_campaign import TRIALS
        from native946_world import world

        chapter = TRIALS['111321']
        deer = world().npc(2013, 3003)
        self.teleport(dict(scene=2013, arrival=load_map(2013).nearest_walkable(deer['x']//20, deer['y']//20)))
        self.level(140)
        with self.p.transaction() as db:
            db.execute('UPDATE cultivation_state SET rebirth=3 WHERE character_id=?', (self.p.cid,))
        self.send(1034, ['number'], [deer['npc_id']])
        self.assertTrue(self.choose('trial_guide').startswith(message_id(2966)))
        self.teleport(chapter['giver'])
        self.send(1492, ['number'], [int(chapter['id'])])
        self.assertEqual(current(self.p.snapshot(), True)['chapter'], chapter['id'])
        detail = self.send(1986, ['number'], [int(chapter['id'])])
        self.assertIn('袁天罡'.encode('gb18030'), detail)
        self.assertEqual(self.p.snapshot()['rebirth'], 3)


import test_native946_wilderness_party as party_fixtures

class CampaignPartyTests(unittest.TestCase):
    setUp=party_fixtures.WildernessPartyTests.setUp
    call=party_fixtures.WildernessPartyTests.call
    team=party_fixtures.WildernessPartyTests.team
    fight=party_fixtures.WildernessPartyTests.fight
    def prepare(self,helper=False):
        self.team();c=CHAPTERS['111600'];node=c['nodes'][4];t=node['target']
        for g in self.games:
            cid=g.character['id']
            with g.progression.transaction() as db:
                db.execute("INSERT INTO native_campaign_runs VALUES(?,'newbie',22,'completed',1,0)",(cid,))
                if not helper or g is self.a:db.execute("INSERT INTO native_campaign_runs VALUES(?,'111600',4,'active',1,0)",(cid,))
                db.execute('UPDATE characters SET level=30 WHERE id=?',(cid,))
                db.execute('UPDATE progression SET cor=30,mag=30,strength=30,speed=30 WHERE character_id=?',(cid,))
            g.character['level']=30;s=g.progression.snapshot();g.progression.set_vitals(s['hpmax'],s['mpmax'])
            x,y=t['arrival'];rev=g.characters.change_scene(g.character['owner'],cid,g.character['map_id'],t['scene'],x,y,g.character.get('position_revision',0))
            g.character.update(map_id=t['scene'],x=x,y=y,position_revision=rev);g.grid=load_map(t['scene'])
        return t
    def test_shared_story_fight_advances_only_matching_owned_stages_once(self):
        t=self.prepare()
        Campaign(self.a).execute(t['npc'],dict(op='fight',chapter='111600',node=4,state='active'))
        b=self.a._wild_battle;self.assertEqual(len(b.games),2);self.fight(b)
        for g in self.games:self.assertEqual(current(g.progression.snapshot())['node'],5)
        with self.a.progression.transaction() as db:
            b.settle(db,True)
            self.assertEqual(db.execute('SELECT count(*) FROM native_campaign_receipts').fetchone()[0],2)
    def test_shared_story_retreat_unpauses_each_peer_and_preserves_progress(self):
        target = self.prepare()
        Campaign(self.a).execute(target['npc'], dict(op='fight', chapter='111600', node=4, state='active'))
        for game in self.games:self.svc.drain(game)
        replies = (self.call(self.a, 2178, 2), self.svc.drain(self.b))
        for game, messages in zip(self.games, replies):
            end = messages.index(packet(2146, [], []))
            self.assertEqual(messages[end - 1], packet(2152, ['number'], [game.character['id']]))
            self.assertNotIn(packet(2170, [], []), messages)
            self.assertIsNone(game.battle)
            self.assertEqual(current(game.progression.snapshot())['node'], 4)
    def test_helper_with_different_progress_cannot_receive_stage_reward(self):
        t=self.prepare(helper=True);before=self.b.progression.snapshot()['experience']
        Campaign(self.a).execute(t['npc'],dict(op='fight',chapter='111600',node=4,state='active'))
        self.fight(self.a._wild_battle)
        self.assertEqual(self.b.progression.snapshot()['experience'],before)
        self.assertIsNone(current(self.b.progression.snapshot()))


if __name__=='__main__':unittest.main()
