from collections import Counter
import unittest
from unittest.mock import patch

from native946_campaign import CARTER_TARGET, NEWBIE_CHAPTER, stage
from native946_codec import SCHEMAS
from native946_game_login import message_id
from native946_zhuziguo_bell import numbers
from native946_login import MessageReader
import test_native946_campaign as campaign_tests


class NewbieReconstructionTests(unittest.TestCase):
    teleport = campaign_tests.CampaignTests.teleport
    send = campaign_tests.CampaignTests.send
    choose = campaign_tests.CampaignTests.choose
    open_current = campaign_tests.CampaignTests.open_current
    battle_to_end = campaign_tests.CampaignTests.battle_to_end

    def setUp(self):
        fixture = campaign_tests.CampaignTests()
        fixture.legacy_route = False
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        for name in ('db','g','p','login','chars'):
            setattr(self,name,getattr(fixture,name))

    def test_native_route_rewards_and_capture(self):
        self.assertEqual(stage(self.p.snapshot())[0],NEWBIE_CHAPTER)
        # New runs hand off to the separate Chang'an guide after delivering wood.
        expected = NEWBIE_CHAPTER['nodes'][:-1]
        for index,node in enumerate(expected):
            _,_,row = self.open_current()
            self.assertEqual(row['node'], index)
            if index in (2, 3):
                trace, journal = self.p.task_records()
                quest = next(entry for entry in trace if entry['id'] == NEWBIE_CHAPTER['qid'])
                self.assertEqual(quest['title'], '#G【指引】#Y初入江湖[主线]')
                self.assertIn('#u#R冼夫人#n', quest['txt'])
                self.assertEqual((quest['scene'], quest['x'], quest['y']), (2008, 301, 113))
                self.assertEqual(journal['剧情'][quest['id']]['npc_id'], 2006)
                self.assertFalse(node['take'])
            if node['action']=='cinematic':
                replies=self.choose('movie')
                self.assertTrue(any(p.startswith(message_id(1275)) for p in replies))
                self.send(1458,['number'],[node['movie']+1])
                self.assertEqual(stage(self.p.snapshot())[2]['node'],index)
                self.send(1458,['number'],[node['movie']])
                self.send(1458,['number'],[node['movie']])
                if node.get('travel_after'):
                    self.assertEqual(self.g.character['map_id'],2428)
            elif node['action']=='battle':
                self.choose('recover')
                self.open_current()
                replies = self.choose('fight')
                battle=self.g._wild_battle
                self.assertEqual([f['type'] for f in battle.enemies()],[f['shape'] for f in node['roster']])
                if node.get('hero_appearance'):
                    self.assertEqual(battle.state['fighters'][self.p.cid]['type'],2073)
                    self.assertNotEqual(self.g.character['role_id'],2073)
                if index==4:
                    guide_index = replies.index(numbers(1247, 34))
                    hint = next(p for p in replies[guide_index + 1:] if p.startswith(message_id(100)))
                    text = MessageReader(hint[1:]).string().decode('gb18030')
                    self.assertIn('小馋猴', text)
                    self.assertIn('捕捉', text)
                    dragon=next(e for e in battle.enemies() if e.get('capture_training'))
                    replies=self.send(2177,['number','number'],[5,dragon['id']])
                    self.assertIn(numbers(3817,self.p.cid,5,0),replies)
                    pet=self.g.summons.active()
                    self.assertEqual(pet['type'],3200)
                    self.assertEqual(battle.current_pet(self.p.cid)['id'],pet['id'])
                self.battle_to_end()
                if index == 4:
                    notice = battle.state['campaign_messages'][self.p.cid]
                    self.assertEqual(notice, '获得 2100 点经验。#r守护小龙人获得 2100 点经验。')
                    with self.p.transaction(write=False) as db:
                        self.assertIsNotNone(db.execute('SELECT 1 FROM summon_battle_rewards '
                                                       'WHERE battle_id=? AND summon_id=?',
                                                       (battle.id, pet['id'])).fetchone())
                if node.get('travel_after'):
                    self.assertEqual(self.g.character['map_id'],2008)
            elif node['action'] == 'give':
                npc = node['target']['npc']
                panel = next(p for p in self.send(1034, ['number'], [npc]) if p.startswith(message_id(3100)))
                reader = MessageReader(panel[3:])
                self.assertEqual((reader.integer(), reader.take(1)), (4044, b'\x00'))
                self.assertIn('木材', reader.string().decode('gb18030'))
                for value in (46155, 1, 1):
                    self.assertEqual((reader.take(1), reader.integer()), (b'\x01', value))
                session = reader.integer()
                reader.finish()
                for args in ((session, 0), (session + 1, 1)):
                    self.send(3102, ['number', 'number'], args)
                    self.assertEqual(stage(self.p.snapshot())[2]['node'], index)
                    self.assertEqual(self.p.snapshot()['campaign']['items'][0]['amount'], 1)
                self.teleport(NEWBIE_CHAPTER['nodes'][-1]['target'])
                self.send(3102, ['number', 'number'], [session, 1])
                self.assertEqual(stage(self.p.snapshot())[2]['node'], index)
                self.teleport(node['target'])
                self.send(3102, ['number', 'number'], [session, 1])
                self.send(3102, ['number', 'number'], [session, 1])
            else:
                self.choose('complete')
            run = self.p.snapshot()['campaign']['runs']['newbie']
            self.assertEqual(run['node'], index+1)
            if node['give']:
                self.assertEqual(self.p.snapshot()['campaign']['items'][0]['type'],46155)
            if node['take']:
                self.assertFalse(self.p.snapshot()['campaign']['items'])
        self.assertEqual(run['state'],'completed')
        self.assertEqual(stage(self.p.snapshot())[0]['id'],'111600')
        from native946_school_daily import guide_details
        guide=guide_details(self.p.snapshot())
        self.assertEqual((guide['scene'],guide['npc']),(2001,1140))
        from native946_school_daily import LIVENESS_GUIDE_ID
        trace_ids = {row['id'] for row in self.p.task_records()[0]}
        self.assertTrue({LIVENESS_GUIDE_ID,stage(self.p.snapshot())[0]['qid']}.issubset(trace_ids))
        self.teleport({**CARTER_TARGET, 'arrival': (CARTER_TARGET['x'], CARTER_TARGET['y'])})
        self.send(1034, ['number'], [CARTER_TARGET['npc']])
        self.choose('visit_carter')
        self.assertTrue(self.p.snapshot()['campaign']['carter_done'])
        self.assertEqual(stage(self.p.snapshot())[0]['id'],'111600')
        guide=guide_details(self.p.snapshot())
        self.assertEqual((guide['scene'],guide['npc']),(2001,1139))
        self.teleport(NEWBIE_CHAPTER['nodes'][-1]['target'])
        self.send(1034, ['number'], [1139])
        self.choose('visit_school_intro')
        self.assertEqual(self.p.snapshot()['campaign']['runs']['newbie']['node'], len(NEWBIE_CHAPTER['nodes']))
        school_trace = next(row for row in self.p.task_records()[0] if row['id'] == LIVENESS_GUIDE_ID)
        self.assertEqual(school_trace['title'], '#G【指引】#Y初入江湖[主线]')
        self.assertIn('#u#R师门传送仙#n', school_trace['txt'])
        with self.p.transaction(write=False) as db:
            seen=db.execute('SELECT experience FROM native_campaign_receipts WHERE character_id=? AND chapter=? AND node>=0 ORDER BY node',
                            (self.p.cid,'newbie')).fetchall()
        self.assertEqual([r[0] for r in seen],[n['reward']['experience'] for n in NEWBIE_CHAPTER['nodes']])

    def test_existing_run_past_wood_delivery_can_finish_at_school_guide(self):
        from native946_school_daily import guide_details

        index = len(NEWBIE_CHAPTER['nodes']) - 1
        with self.p.transaction() as db:
            db.execute("UPDATE native_campaign_runs SET node=? WHERE character_id=? AND chapter='newbie'",
                       (index, self.p.cid))
        self.p._cached_snapshot = None
        self.open_current()
        self.choose('complete')
        snapshot = self.p.snapshot()
        self.assertEqual(snapshot['campaign']['runs']['newbie']['state'], 'completed')
        self.assertFalse(snapshot['campaign']['carter_done'])
        self.assertEqual(guide_details(snapshot)['npc'], 1139)

    def test_tracker_wire_and_successful_settlement_notice(self):
        raw = self.p.task_packets()[0]
        self.assertTrue(raw.startswith(message_id(1978)))
        reader = MessageReader(raw[3:])
        self.assertEqual(reader.take(1), b'\x01')
        task = {key: (reader.string().decode('gb18030') if kind == 'string' else reader.integer())
                for key, kind in SCHEMAS['task_trace_info']}
        reader.finish()
        self.assertEqual(task['title'], '#G【指引】#Y初入江湖[主线]')
        self.assertIn('#u#R迎新使者水月仙#n', task['txt'])

        self.open_current()
        npc = self.g.dialog_npc
        reply = self.choose('complete')
        notice, = [row for row in reply if row.startswith(message_id(100))]
        reader = MessageReader(notice[1:])
        self.assertEqual(reader.string().decode('gb18030'), '获得 200 点经验。')
        self.assertEqual(reader.integer(), 0)
        reader.finish()
        duplicate = self.send(1032, ['byte', 'number'], [1, npc]) or ()
        self.assertFalse(any(row.startswith(message_id(100)) for row in duplicate))

    def test_timed_reward_before_due_claim_retry_and_chest_use(self):
        from native946_newbie_rewards import STARTER_CHEST, begin, icon, tick
        starting={r["id"] for r in self.p.snapshot()["items"]}
        self.assertIn(icon(2,1,60),begin(self.g))
        early = self.send(1394,['number'],[1])
        self.assertTrue(early.startswith(message_id(98)) if isinstance(early,bytes)
                        else any(p.startswith(message_id(98)) for p in early))
        clock=self.g._newbie_award_clock
        with patch('native946_newbie_rewards.monotonic',return_value=clock+360):
            self.assertIn(icon(3,1),tick(self.g))
        self.assertEqual(begin(self.g),(icon(2,1,1),icon(3,1),icon(2,2,1),icon(3,2)))
        preview=self.send(1393,['number'],[1])
        self.assertTrue(preview.startswith(message_id(1211)))
        claimed=self.send(1394,['number'],[1])
        self.assertIn(icon(1,1),claimed)
        duplicate=self.send(1394,['number'],[1])
        self.assertEqual(duplicate,icon(1,1))
        self.assertIn(icon(1,2),self.send(1394,['number'],[2]))
        with self.p.transaction(write=False) as db:
            rows = db.execute('SELECT id,type,amount FROM inventory WHERE character_id=?',(self.p.cid,)).fetchall()
        expected=Counter({4000:35,4001:35,6782:5,6783:5,STARTER_CHEST:1,8714:1})
        actual=Counter()
        for row in rows:
            if row['type'] in expected:
                actual[row['type']]+=row['amount']
        self.assertEqual(actual,expected)
        chest=next(r['id'] for r in rows if r['type']==STARTER_CHEST)
        replies=self.send(807,['number','number'],[self.p.cid,chest])
        self.assertTrue(any(p.startswith(message_id(140)) or p.startswith(message_id(98)) for p in replies))
        with self.p.transaction(write=False) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM inventory WHERE id=?',(chest,)).fetchone())
        snapshot=self.p.snapshot()
        self.assertEqual(len([r for r in snapshot['items'] if r['id'] not in starting and r['type'] not in expected]),5)
        for item in snapshot['items']:
            if item['id'] not in starting and item['type'] not in expected:
                from native946_progression import GEAR
                slot=(snapshot['equipment'].get(item['id']) or {'slot':GEAR.get(item['type'],(0,))[0]})['slot']
                self.assertGreater(slot,0)
                self.p.validate_equipment(snapshot,item,slot,1)

    def test_command_icons_precede_both_actor_input_transitions(self):
        from native946_gameplay import ENEMY_ID
        from native946_role_battle import mana_cost,profile
        self.g.summons.claim_starter()
        self.g.begin_battle()
        pet=self.g.battle['pet']['id']
        from native946_role_battle import solo_fighters
        hero=self.g.character['id']
        actor=solo_fighters(self.g)[hero]
        before=actor['mp']
        cost=mana_cost(actor,profile(6100))
        reply=self.g.attack(skill_id=6100,target=ENEMY_ID)
        self.assertEqual(reply[0],numbers(3817,hero,9,6100))
        self.assertEqual(reply[1],numbers(2152,hero))
        self.assertTrue(reply[2].startswith(message_id(2151)))
        reply=self.g.receive(2181,MessageReader(numbers(2181,3,ENEMY_ID)[3:]))
        ids=[p[:3] for p in reply]
        self.assertEqual(reply[0],numbers(3817,pet,3,0))
        self.assertLess(ids.index(message_id(2152)),ids.index(message_id(2167)))
        self.assertEqual(self.g.battle['mp'],before-cost)

    def test_spell_values_cover_947_samples_with_known_calculator_input(self):
        from native946_role_battle import cleanse_probability, description, gushu_duration, mana_cost, profile
        actor={'race':7,'school':17,'level':50,'skills':{6102:4011,6110:228}}
        self.assertEqual(mana_cost(actor,profile(6102)),183)
        self.assertEqual(mana_cost(actor,profile(6110)),234)
        self.assertIn('#G640#n',description(6102,actor))
        self.assertEqual(cleanse_probability(actor,6107),360)
        self.assertEqual(cleanse_probability(actor,6108),680)
        self.assertEqual(cleanse_probability(actor,6109),560)
        self.assertEqual(gushu_duration(actor,6130),2)
        self.assertEqual(gushu_duration(actor,6131),3)
        actor.update(level=72,skills={6102:4044})
        self.assertEqual(mana_cost(actor,profile(6102)),184)
        self.assertIn('#G817#n',description(6102,actor))
