from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import number, packet
from native946_gameplay import NativeGameplay, ENEMY_ID
from native946_login import MessageReader
from native946_progression import Progression


class ProgressionTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'game.sqlite3'
        LocalAccounts(self.path).register({'username':'owner946','password':'123456789'})
        self.characters=Characters(self.path)
        self.character=self.characters.create('owner946','成长测试',702)
        self.p=Progression(self.path,'owner946',self.character['id'])

    def test_starter_inventory_is_granted_only_once(self):
        ids=[i['id'] for i in self.p.snapshot()['items']]
        p=Progression(self.path,'owner946',self.character['id'])
        self.assertEqual([i['id'] for i in p.snapshot()['items']],ids)
        self.assertEqual(len(ids),5)
        self.assertEqual(p.snapshot()['shortcuts'],{1:6100})

    def test_legacy_starter_repair_preserves_learned_skills_and_changed_shortcuts(self):
        with self.p.transaction() as db:
            db.execute("DELETE FROM native_role_system_state WHERE character_id=? AND system='starter_skills'",
                       (self.p.cid,))
            db.execute('DELETE FROM learned_skills WHERE character_id=?', (self.p.cid,))
            db.executemany('INSERT INTO learned_skills VALUES(?,?,?)',
                           [(self.p.cid, 6100, 51), (self.p.cid, 6101, 19)])
        self.p.shortcut(1, 6101)
        snapshot = Progression(self.path, 'owner946', self.p.cid).snapshot()
        self.assertEqual(snapshot['skills'], {6100: 51, 6101: 19, 6107: 0, 6128: 0})
        self.assertEqual(snapshot['shortcuts'], {1: 6101})
        self.assertEqual(Progression(self.path, 'owner946', self.p.cid).snapshot()['skills'], snapshot['skills'])

    def test_legacy_foreign_starter_and_shortcut_are_replaced(self):
        character = self.characters.create('owner946', '妖族测试', 212)
        cid = character['id']
        progression = Progression(self.path, 'owner946', cid)
        with progression.transaction() as db:
            db.execute("DELETE FROM native_role_system_state WHERE character_id=? AND system='starter_skills'", (cid,))
            db.execute('DELETE FROM learned_skills WHERE character_id=?', (cid,))
            db.execute('INSERT INTO learned_skills VALUES(?,6100,1)', (cid,))
            db.execute('UPDATE skill_shortcuts SET skill_id=6100 WHERE character_id=? AND slot=1', (cid,))
        snapshot = Progression(self.path, 'owner946', cid).snapshot()
        self.assertEqual(snapshot['skills'], {91: 0, 101: 0, 111: 0})
        self.assertEqual(snapshot['shortcuts'], {1: 101})

    def test_foreign_owner_cannot_access_progression(self):
        with self.assertRaises(ValueError):Progression(self.path,'another_owner',self.character['id'])

    def test_attributes_spend_points_and_reject_overallocation_atomically(self):
        self.p.allocate([1,1,1,1]);s=self.p.snapshot()
        self.assertEqual((s['points'],s['hpmax'],s['mpmax'],s['attack']),(0,346,252,77))
        with self.assertRaises(ValueError):self.p.allocate([1,0,0,0])
        self.assertEqual(self.p.snapshot()['cor'],2)

    def test_native_speed_reply_matches_battle_speed_after_points_and_equipment(self):
        from native946_progression import DATA
        def speed():
            for payload in self.p.attribute_packets():
                if payload[:3]!=b'\x00\xb7\x02':continue
                reader=MessageReader(payload[3:]);key,value=reader.integer(),reader.integer()
                if key==DATA['attributes']['DodAll']:return value
            self.fail('Missing native speed attribute reply')
        self.assertEqual(speed(),self.p.snapshot()['initiative'])
        self.p.allocate([0,0,0,1])
        self.assertEqual(speed(),10)
        shoes=next(i for i in self.p.snapshot()['items'] if i['type']==5023)
        self.p.move_item(shoes['id'],1,5)
        self.assertEqual(speed(),12)
        coat=next(i for i in self.p.snapshot()['items'] if i['type']==5004)
        self.p.move_item(coat['id'],1,3)
        self.assertEqual(speed(),12)

    def test_attribute_plan_quote_only_opens_the_native_confirmation(self):
        game = NativeGameplay(self.character, self.characters, lambda: ())
        reply = game.receive(3780, MessageReader(b''))
        self.assertEqual(reply, packet(3779, ['number'], [1]))
        self.assertEqual(game.hero_point_quote['cost'], 1)

    def test_split_merge_and_reload_conserve_items(self):
        item=self.p.snapshot()['items'][0]
        self.p.split(item['id'],8,10,2)
        stack=[i for i in self.p.snapshot()['items'] if i['type']==item['type']]
        self.assertEqual(sum(i['amount'] for i in stack),5)
        self.p.merge([i['id'] for i in stack])
        stack=[i for i in Progression(self.path,'owner946',self.character['id']).snapshot()['items'] if i['type']==item['type']]
        self.assertEqual([i['amount'] for i in stack],[5])

    def test_native_drag_and_split_field_order(self):
        game=NativeGameplay(self.character,self.characters,lambda:())
        game.receive(803,MessageReader(b''.join(number(n) for n in [8,1,8,12])))
        original=next(i for i in game.progression.snapshot()['items'] if i['type']==4000)
        self.assertEqual(original['slot'],12)
        game.receive(805,MessageReader(b''.join(number(n) for n in [original['id'],8,13,2])))
        stack=[i for i in game.progression.snapshot()['items'] if i['type']==4000]
        self.assertEqual({i['slot']:i['amount'] for i in stack},{12:3,13:2})

    def test_equipment_changes_stats_and_wrong_slot_is_atomic(self):
        coat=next(i for i in self.p.snapshot()['items'] if i['type']==5004)
        self.p.move_item(coat['id'],1,3)
        self.assertEqual((self.p.snapshot()['hpmax'],self.p.snapshot()['defense']),(358,3))
        with self.assertRaises(ValueError):self.p.move_item(coat['id'],1,5)
        self.assertEqual(self.p.snapshot()['defense'],3)
        self.p.move_item(coat['id'],8,4)
        self.assertEqual(self.p.snapshot()['defense'],0)

    def test_medicine_consumption_caps_health_and_full_health_is_noop(self):
        item=self.p.snapshot()['items'][0]
        with self.assertRaises(ValueError):self.p.use_item(item['id'])
        self.p.set_vitals(self.p.snapshot()['hpmax']-10,20);self.p.use_item(item['id'])
        s=self.p.snapshot();self.assertEqual(s['hp'],s['hpmax'])
        self.assertEqual(next(i['amount'] for i in s['items'] if i['id']==item['id']),4)

    def test_quest_reward_levels_up_once_and_is_idempotent(self):
        self.p.accept_quest();self.p.record_victory('victory1');self.p.record_victory('victory1')
        self.assertEqual(self.p.snapshot()['quest']['kills'],1)
        self.p.turn_in()
        with self.assertRaises(ValueError):self.p.turn_in()
        s=self.p.snapshot()
        self.assertEqual((s['level'],s['experience'],s['gold'],s['points']),(2,62,25,8))

    def test_battle_reward_auto_levels_and_caps_remain_available(self):
        game=NativeGameplay(self.character,self.characters,lambda:())
        self.assertTrue(self.characters.settle_battle('owner946',self.character['id'],'auto-level',True,
                                                       experience=1000,gold=0))
        self.assertEqual(game.auto_level(),5)
        s=Progression(self.path,'owner946',self.character['id']).snapshot()
        self.assertEqual((s['level'],s['experience'],s['points']),(6,310,24))
        self.assertEqual((s['cor'],s['mag'],s['strength'],s['speed']),(6,6,6,6))
        self.assertEqual((game.character['level'],game.character['experience']),(6,310))
        self.assertEqual(s['skills'][6101], 0)
        self.assertEqual(game.auto_level(),0)

    def test_manual_level_up_unlocks_eligible_spells_in_native_skill_reply(self):
        from native946_codec import SCHEMAS
        from native946_durable_writer import close
        from native946_game_login import message_id
        from native946_progression import level_up_cost
        from native946_sqlite_runtime import configure_database
        from sys import getswitchinterval, setswitchinterval

        game = NativeGameplay(self.character, self.characters, lambda: ())
        with game.progression.transaction() as db:
            db.execute('UPDATE characters SET level=10,experience=? WHERE id=?',
                       (level_up_cost(10,11), self.p.cid))
            db.execute('UPDATE learned_skills SET rank=75 WHERE character_id=? AND skill_id=6100', (self.p.cid,))
        self.addCleanup(setswitchinterval, getswitchinterval())
        configure_database(self.path)
        self.addCleanup(close, self.path)
        reply = game.receive(748, MessageReader(b''))
        raw = next(payload for payload in reply if payload[:3] == message_id(699))
        reader = MessageReader(raw[3:])
        self.assertEqual(reader.integer(), 0)
        self.assertEqual(reader.take(1)[0], 0)
        ranks = {}
        for _ in range(reader.take(1)[0]):
            row = {key: reader.string() if kind == 'string' else reader.integer()
                   for key, kind in SCHEMAS['hero_skill_info']}
            ranks[row['skill_id']] = row['practice']
        self.assertEqual({skill:ranks[skill] for skill in (6100,6101,6108,6129)},
                         {6100:75,6101:0,6108:0,6129:0})
        self.assertTrue({6102,6116,6105,6127}.isdisjoint(ranks))
        self.assertEqual(game.progression.snapshot()['level'], 11)

    def test_multi_level_upgrade_and_legacy_backfill_keep_saved_ranks_and_shortcuts(self):
        from native946_progression import level_up_cost
        from native946_school import SKILLS

        with self.p.transaction() as db:
            db.execute('UPDATE characters SET experience=? WHERE id=?',
                       (level_up_cost(1,41), self.p.cid))
            db.execute('UPDATE learned_skills SET rank=230 WHERE character_id=? AND skill_id=6107', (self.p.cid,))
        self.p.level_up(41)
        expected = {skill for branch in (16,17,18) for skill in SKILLS[branch]}
        leveled = self.p.snapshot()
        self.assertTrue(expected.issubset(leveled['skills']))
        self.assertEqual(leveled['skills'][6107], 230)
        self.assertEqual(leveled['shortcuts'], {1:6100})

        with self.p.transaction() as db:
            db.execute('DELETE FROM learned_skills WHERE character_id=? AND skill_id IN (6102, 6111, 6132)',
                       (self.p.cid,))
        reopened = Progression(self.path, self.p.owner, self.p.cid).snapshot()
        self.assertEqual(reopened['skills'], leveled['skills'])
        self.assertEqual(reopened['shortcuts'], leveled['shortcuts'])
        self.assertEqual(reopened['gold'], leveled['gold'])

    def test_native_unreborn_experience_matches_panel_and_multi_level_cost(self):
        from native946_progression import DATA, level_up_cost

        exp_next = int(DATA['attributes']['ExpNext'])
        self.assertIn(packet(695,['number','number'],[exp_next,58]),self.p.attribute_packets())
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=70,experience=357960 WHERE id=?',(self.p.cid,))
        self.assertIn(packet(695,['number','number'],[exp_next,357961]),self.p.attribute_packets())
        self.assertEqual(self.p.auto_level(),0)
        with self.assertRaisesRegex(ValueError,'升级经验不足'):
            self.p.level_up(71)
        self.assertEqual((self.p.snapshot()['level'],self.p.snapshot()['experience']),(70,357960))
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET experience=731266 WHERE id=?',(self.p.cid,))
        self.p.level_up(72)
        self.assertEqual((self.p.snapshot()['level'],self.p.snapshot()['experience']),(72,7))
        self.assertEqual(level_up_cost(70,71,cultivation={'rebirth':1}),7000)

    def test_shortcut_initialization_does_not_open_skill_panel_but_explicit_request_does(self):
        game=NativeGameplay(self.character,self.characters,lambda:())
        first=game.receive(742,MessageReader(b'\x01'))
        second=game.receive(742,MessageReader(b'\x01'))
        self.assertEqual(first,self.p.skill_packets(False))
        self.assertEqual(second,self.p.skill_packets(True))

    def test_battle_entry_closes_residue_skill_panel(self):
        from native946_codec import packet
        from native946_game_login import message_id
        game=NativeGameplay(self.character,self.characters,lambda:())
        replies=game.begin_battle()
        self.assertLess(replies.index(packet(1183,['string'],['dlg_skill_panel_new'])),
                        next(i for i,p in enumerate(replies) if p[:3]==message_id(2171)))
        input_index = next(i for i,p in enumerate(replies) if p[:3] == message_id(2151))
        round_index = next(i for i,p in enumerate(replies) if p[:3] == message_id(2135))
        self.assertLess(round_index, input_index)
        self.assertEqual(replies[input_index+1], packet(2139,['number'],[2]))

    def test_skill_level_requirement_and_persistent_rank(self):
        with self.assertRaises(ValueError):self.p.learn(6101)
        self.p.accept_quest();self.p.record_victory('v');self.p.turn_in()
        self.p.learn(6101)
        s=Progression(self.path,'owner946',self.character['id']).snapshot()
        self.assertEqual(s['skills'][6101],1)
        self.assertEqual(s['gold'],20)

    def test_viewing_learned_skills_does_not_charge_gold(self):
        game=NativeGameplay(self.character,self.characters,lambda:(),practice_trainer=True)
        from native946_gameplay import TRAINER_ID
        game.receive(1034,MessageReader(number(TRAINER_ID)))
        game.receive(1032,MessageReader(b'\x03'+number(TRAINER_ID)))
        view = game.dialog_actions.index('viewskills') + 1
        replies=game.receive(1032,MessageReader(bytes([view])+number(TRAINER_ID)))
        self.assertTrue(any(p[:3]==b'\x00\xbb\x02' for p in replies))
        self.assertEqual(self.p.snapshot()['gold'],0)

    def test_battle_health_wire_uses_hp_min_max_and_indexed_updates(self):
        game=NativeGameplay(self.character,self.characters,lambda:())
        init=next(p for p in game.begin_battle() if p[:3]==b'\x00\x64\x08' and len(p)>8 and MessageReader(p[3:]).integer()==ENEMY_ID)
        r=MessageReader(init[3:]);self.assertEqual([r.integer() for _ in range(10)],[ENEMY_ID,228,228,228,0,0,0,0,100,0])
        replies=game.receive(2185,MessageReader(number(9)+number(6100)+number(ENEMY_ID)))
        self.assertEqual(game.battle['mp'],238)
        updates=[]
        powers=[]
        for p in replies:
            if p[:3]==b'\x00\x6e\x08':
                r=MessageReader(p[3:]);updates.append([r.integer() for _ in range(3)])
            if p[:3]==b'\x00\x6e\x08':
                r=MessageReader(p[3:]);powers.append([r.integer() for _ in range(3)])
        # Native 6100: floor(76*0.5 + .024 + 1*6 + 100) = 144.
        self.assertIn([ENEMY_ID,1,84],updates)
        self.assertIn([self.character['id'],4,238],powers)
        before=game.battle.copy()
        game.receive(2185,MessageReader(number(9)+number(6100)+number(ENEMY_ID)))
        self.assertEqual(game.battle,before)


if __name__=='__main__':unittest.main()
