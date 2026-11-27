from contextlib import closing
from pathlib import Path
from struct import pack
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import field, number, packet
from native946_gameplay import ENEMY_ID, TRAINER_ID, NativeGameplay
from native946_login import MessageReader, string_field
from native946_map import DIRECTIONS, load_map


class GameplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "characters.sqlite3"
        LocalAccounts(path).register({"username": "owner946", "password": "123456789"})
        self.characters = Characters(path)
        self.character = self.characters.create("owner946", "基础测试", 702)
        self.game = NativeGameplay(self.character, self.characters, lambda: (), practice_trainer=True)

    def call(self, mid, fields=b""):
        return self.game.receive(mid, MessageReader(fields))

    def test_valid_movement_survives_reload(self):
        x, y = self.character["x"], self.character["y"]
        code, (dx, dy) = next((c, d) for c, d in DIRECTIONS.items() if self.game.grid.walkable(x+d[0], y+d[1]))
        self.call(1643, number(4) + pack("<HH", x, y) + string_field(bytes([code])))
        loaded = Characters(self.characters.database).list("owner946")[0]
        self.assertEqual((loaded["x"], loaded["y"]), (x+dx, y+dy))

    def test_invalid_path_does_not_change_position(self):
        before = self.characters.list("owner946")
        self.call(1643, number(4) + pack("<HH", 300, 200) + string_field(b"<"))
        self.assertEqual(self.characters.list("owner946"), before)
        self.assertEqual(self.game.last_event["action"], "movement_rejected")

    def test_battle_requires_npc_dialogue(self):
        self.call(1032, b"\x00" + number(TRAINER_ID))
        self.assertIsNone(self.game.battle)
        self.call(1034, number(TRAINER_ID))
        # Original dlg_npc_chat._show_options captures the ipairs index
        # unchanged: native options start at one; -1 (byte 255) cancels.
        self.call(1032, b"\x00" + number(TRAINER_ID))
        self.assertIsNone(self.game.battle)
        self.call(1032, b"\x01" + number(TRAINER_ID))
        self.assertIsNotNone(self.game.battle)

    def test_fighter_dye_is_three_unprefixed_bytes(self):
        # Registry: arraybytebyte with fixed length 3, not a variable array.
        self.assertEqual(field(("fixed_array", "byte", 3), [7, 8, 9]), b"\x07\x08\x09")
        with self.assertRaises(ValueError):
            field(("fixed_array", "byte", 3), [])
        encoded = self.game.fighter(1, 702, "1233", 4, 1)
        reader = MessageReader(encoded[3:])
        self.assertEqual([reader.integer() for _ in range(3)], [1, 1, 702])
        self.assertEqual(reader.string(), b"1233")
        self.assertEqual([reader.integer() for _ in range(3)], [4, 1, 1])
        self.assertEqual(reader.take(6), bytes(6))
        self.assertEqual([reader.integer() for _ in range(6)], [0]*6)
        self.assertEqual(reader.take(1), b"\x00")
        self.assertEqual([reader.integer() for _ in range(6)], [0, 0, 1, 0, 0, 0])
        reader.finish()

    def test_training_explicitly_closes_npc_dialogue(self):
        packets = self.game.begin_battle()
        self.assertEqual(packets[0][:3], b"\x00\x9f\x04")
        reader = MessageReader(packets[0][3:])
        self.assertEqual(reader.string(), b"dlg_npc_chat")
        reader.finish()

    def test_training_preserves_native_auto_mode_and_panel_on_entry(self):
        replies=self.game.begin_battle()
        self.assertNotIn(packet(5349,[],[]),replies)
        # Entry itself never submits a default hero or pet attack.
        self.assertEqual(self.game.battle['orders'],{})
        self.assertEqual(self.game.battle['enemy_hp'],self.game.battle['enemy_hpmax'])
        self.assertEqual(self.game.battle['phase'],'input')

    def test_exit_preserves_auto_mode_and_panel_in_the_world(self):
        self.game.begin_battle();replies=self.call(2178,number(2))
        self.assertNotIn(packet(5349,[],[]),replies)
        self.assertIsNone(self.game.battle)

    def test_defend_replaces_queued_attack_before_the_round_resolves(self):
        sid=self.game.summons.claim_starter();self.game.begin_battle()
        self.call(2177,number(3)+number(ENEMY_ID))
        self.call(2178,number(1))
        self.assertEqual(self.game.battle['orders'][self.character['id']]['kind'],'defend')
        replies=self.call(2181,number(3)+number(ENEMY_ID))
        actors=[]
        for reply in replies:
            if reply[:3]!=b'\x00\x6d\x08':continue
            reader=MessageReader(reply[3:]);actors.append((reader.integer(),reader.integer()))
        self.assertNotIn(self.character['id'],[actor for actor,target in actors])
        self.assertIn(sid,[actor for actor,target in actors])

    def test_native_timeout_default_enemy_works_for_hero_and_pet(self):
        sid=self.game.summons.claim_starter();self.game.begin_battle()
        self.call(2177,number(3)+number(0))
        self.assertIn(self.character['id'],self.game.battle['orders'])
        self.assertEqual(self.game.battle['enemy_hp'],self.game.battle['enemy_hpmax'])
        self.call(2181,number(3)+number(0))
        self.assertIn(sid,self.game.battle['orders'])
        self.assertEqual(self.game.battle['phase'],'drawing')
        self.assertLess(self.game.battle['enemy_hp'],self.game.battle['enemy_hpmax'])

    def test_training_guardian_protects_hero_and_consumes_its_order(self):
        cid=self.character['id'];sid=self.game.summons.claim_starter()
        self.game.begin_battle()
        from native946_role_battle import solo_fighters
        fighters=solo_fighters(self.game)
        fighters[ENEMY_ID].update(attack=fighters[cid]['defense']+40,AttValue=100)
        before={fid:fighters[fid]['hp'] for fid in (cid,sid)}
        self.call(2178,number(1))
        replies=self.call(2181,number(4)+number(cid))
        self.assertIn(packet(2159,['number','number'],[cid,sid]),replies)
        self.assertEqual([before[fid]-fighters[fid]['hp'] for fid in (cid,sid)],[10,10])

    def test_timeout_shows_auto_panel_and_records_notice_once(self):
        self.game.summons.claim_starter();self.game.begin_battle()
        first=self.call(2177,number(3)+number(0))
        self.assertEqual(first[0][0],100)
        self.assertEqual(sum(p[0] in (98,100) for p in first),1)
        self.assertIn(packet(2758,[],[]),first)
        self.assertIsNone(self.call(2177,number(3)+number(0)))
        pet_reply=self.call(2181,number(3)+number(0))
        self.assertFalse(any(p[0] in (97,98,100) for p in pet_reply))

    def test_each_actor_gets_native_cast_and_return_boundaries(self):
        sid=self.game.summons.claim_starter();self.game.begin_battle()
        self.call(2177,number(3)+number(ENEMY_ID))
        replies=self.call(2181,number(3)+number(ENEMY_ID))
        # Replay native c_start_draw's forward/backward host search. A single
        # pair around the whole round gives flags only to first/last actors.
        commands=[]
        for p in replies:
            if p[:3]==b'\x00\x4e\x08':commands.append(('boundary',MessageReader(p[3:]).integer()))
            elif p[:3]==b'\x00\x6d\x08':commands.append(('actor',MessageReader(p[3:]).integer()))
        flags={value:set() for kind,value in commands if kind=='actor'}
        for i,(kind,value) in enumerate(commands):
            if kind!='boundary':continue
            direction=1 if value==1 else -1
            for j in range(i+direction,len(commands) if direction==1 else -1,direction):
                if commands[j][0]=='actor':
                    flags[commands[j][1]].add(value);break
        self.assertEqual(set(flags),{self.character['id'],sid,ENEMY_ID})
        self.assertTrue(all(values=={1,2} for values in flags.values()))

    def test_server_deadline_resolves_default_round_only_once(self):
        with patch('native946_gameplay.monotonic',return_value=100):self.game.begin_battle()
        self.assertIsNone(self.game.tick(144.99))
        replies=self.game.tick(145)
        self.assertEqual(self.game.battle['phase'],'drawing')
        self.assertEqual(self.game.battle['enemy_hp'],152)
        self.assertIn(packet(2758,[],[]),replies)
        self.assertIsNone(self.game.tick(200))
        self.assertEqual(self.game.battle['enemy_hp'],152)

    def test_drawing_timeout_recovers_round_and_wakes_native_processor(self):
        with patch('native946_gameplay.monotonic',return_value=100):self.game.begin_battle()
        replies=self.game.tick(145)
        ids=[int.from_bytes(p[1:3],'little') if p[:1]==b'\0' else p[0] for p in replies]
        self.assertEqual(ids[ids.index(2167)+1],2139)
        deadline=self.game.battle['input_deadline']
        self.assertIsNone(self.game.tick(deadline-1))
        self.game.tick(deadline)
        self.assertEqual((self.game.battle['phase'],self.game.battle['round']),('input',2))
        self.assertIsNotNone(self.game.battle['input_deadline'])

    def test_drawing_timeout_finishes_victory_without_duplicate_rewards(self):
        import sqlite3
        self.game.begin_battle()
        self.game.battle.update(phase='drawing',victory=True,enemy_hp=0,input_deadline=0)
        self.game.tick(1)
        self.assertIsNone(self.game.battle)
        self.assertIsNone(self.game.tick(10000))
        with closing(sqlite3.connect(self.characters.database)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM battle_results').fetchone()[0],1)

    def test_partial_timeout_preserves_skill_and_only_defaults_unsubmitted_pet(self):
        sid=self.game.summons.claim_starter()
        with patch('native946_gameplay.monotonic',return_value=100):self.game.begin_battle()
        with patch('native946_gameplay.monotonic',return_value=110):
            handoff=self.call(2185,number(9)+number(6100)+number(ENEMY_ID))
        self.assertIn(packet(2151,['number']*3,[sid,35,0]),handoff)
        self.assertEqual(self.game.battle['input_deadline'],145)
        self.assertIsNone(self.game.tick(144.99))
        replies=self.game.tick(145)
        self.assertEqual(self.game.battle['orders'][self.character['id']]['skill'],6100)
        self.assertEqual(self.game.battle['orders'][sid]['skill'],10)
        self.assertEqual(self.game.battle['mp'],238)
        self.assertNotIn(packet(2758,[],[]),replies)

    def test_f5_request_receives_native_basic_panel_info_and_learned_skills(self):
        reply=self.call(2757)
        self.assertEqual(reply[:3],b'\x00\xc4\x0a')
        r=MessageReader(reply[3:]);self.assertEqual(r.integer(),0)
        skills = {}
        for _ in range(r.take(1)[0]):
            description = r.string().decode('gb18030')
            cost = r.string().decode('gb18030')
            skills[r.integer()] = (description, cost)
        self.assertEqual(set(skills), {4592, 6100, 6107, 6128})
        self.assertIn('#G160#n点固定气血伤害', skills[4592][0])
        self.assertIn('结算伤害：#G224#n', skills[4592][0])
        self.assertEqual(skills[4592][1], '法力：60')
        description, cost = skills[6100]
        self.assertIn('基础伤害为#G144#n',description)
        self.assertNotIn('%d',description)
        self.assertEqual(cost, '法力：8')
        self.assertEqual(r.take(1),b'\x00');self.assertEqual(r.integer(),0);r.finish()

    def test_f5_uses_current_mana_cost_and_all_role_descriptions_render(self):
        from native946_role_battle import DATA, DESCRIPTION_SLOT, description
        state=self.game.progression.snapshot()
        for skill in DATA['profiles']:
            text=description(int(skill),state)
            self.assertTrue(text,skill)
            self.assertIsNone(DESCRIPTION_SLOT.search(text),skill)
        state['role_talents']={'7033':20}
        with patch.object(self.game.progression,'snapshot',return_value=state):
            reply=self.call(2757)
        reader = MessageReader(reply[3:])
        reader.integer()
        costs = {}
        for _ in range(reader.take(1)[0]):
            reader.string()
            cost = reader.string().decode('gb18030')
            costs[reader.integer()] = cost
        self.assertEqual(costs[6100], '法力：6')
        self.assertEqual(costs[4592], '法力：48')

    def test_f5_skill_default_target_uses_learned_native_spell(self):
        self.game.begin_battle()
        self.call(2185,number(9)+number(6100)+number(0))
        self.assertEqual(self.game.battle['orders'][self.character['id']]['skill'],6100)
        self.assertEqual(self.game.battle['mp'],238)
        self.assertLess(self.game.battle['enemy_hp'],self.game.battle['enemy_hpmax'])

    def test_newbie_spell_4592_uses_native_battle_order_and_shortcut(self):
        p = self.game.progression
        self.assertEqual(p.snapshot()['skills'][4592], 0)
        p.shortcut(1, 4592)
        self.assertEqual(p.snapshot()['shortcuts'][1], 4592)
        with self.assertRaisesRegex(ValueError, '当前训练不支持该技能'):
            p.learn(4592)

        self.game.begin_battle()
        before_mp, before_hp = (self.game.battle[k] for k in ('mp', 'enemy_hp'))
        self.call(2185, number(9) + number(4592) + number(ENEMY_ID))
        self.assertEqual(self.game.battle['orders'][self.character['id']]['skill'], 4592)
        self.assertEqual(self.game.battle['mp'], before_mp - 60)
        self.assertEqual(self.game.battle['enemy_hp'], max(0, before_hp - 224))

    def test_background_round_does_not_accept_its_own_native_timeout_echo(self):
        self.game.summons.claim_starter();self.game.begin_battle()
        self.game._background_round=(self.game.battle['id'],1)
        self.assertEqual(self.call(2757),packet(5349,[],[]))
        self.assertIsNone(self.call(2177,number(3)+number(0)))
        self.assertIsNone(self.call(2181,number(3)+number(0)))
        self.assertEqual(self.game.battle['orders'],{})
        # Positive targets from the background command still execute normally.
        self.call(2177,number(3)+number(ENEMY_ID));self.call(2181,number(3)+number(ENEMY_ID))
        self.call(2184)
        self.assertIsNone(self.game._background_round)

    def test_input_end_is_not_sent_again_after_acknowledged_manual_order(self):
        self.game.summons.claim_starter();self.game.begin_battle()
        hero_ack=packet(2152,['number'],[self.character['id']])
        first=self.call(2177,number(3)+number(ENEMY_ID))
        second=self.call(2181,number(3)+number(ENEMY_ID))
        self.assertEqual(first.count(hero_ack),1)
        self.assertNotIn(hero_ack,second)

    def test_new_native_animation_ack_arms_fresh_deadline(self):
        with patch('native946_gameplay.monotonic',return_value=100):self.game.begin_battle()
        self.game.tick(145)
        self.assertIsNone(self.game.tick(1000))
        with patch('native946_gameplay.monotonic',return_value=200):self.call(2184)
        self.assertEqual(self.game.battle['input_deadline'],245)
        self.assertEqual(self.game.battle['input_ended'],[])
        self.assertIsNone(self.game.tick(244))

    def test_training_revert_preserves_deadline_and_returns_to_hero_input(self):
        self.game.summons.claim_starter()
        with patch('native946_gameplay.monotonic',return_value=100):
            entry=self.game.begin_battle()
            self.assertIn(packet(2151,['number']*3,[self.character['id'],45,0]),entry)
            self.call(2178,number(1))
        with patch('native946_gameplay.monotonic',return_value=115):
            self.assertEqual(self.call(3819),(
                packet(3818,['number'],[0]),packet(5349,[],[]),
                packet(2151,['number']*3,[self.character['id'],30,0]),packet(2139,['number'],[2])))
        self.assertFalse(self.game.battle['orders'])
        self.assertEqual(self.game.battle['input_deadline'],145)
        with patch('native946_gameplay.monotonic',return_value=145):
            self.assertIsNone(self.call(3819))

    def test_single_target_with_empty_path_is_valid_movement(self):
        point = self.game.grid.nearby(self.character["x"], self.character["y"])
        self.call(1643, number(4) + pack("<HH", *point) + string_field(b""))
        loaded = self.characters.list("owner946")[0]
        self.assertEqual((loaded["x"], loaded["y"]), point)

    def test_native_road_and_wall_reference_cells(self):
        self.assertTrue(self.game.grid.walkable(296, 207))
        self.assertFalse(self.game.grid.walkable(300, 200))
        point = self.game.grid.nearest_walkable(300, 200)
        self.assertTrue(self.game.grid.walkable(*point))

    def test_attack_is_not_replayed_during_animation_and_reward_is_once(self):
        self.game.begin_battle()
        for turn in range(3):
            self.call(2176, number(ENEMY_ID))
            remaining = self.game.battle["enemy_hp"]
            self.call(2176, number(ENEMY_ID))
            self.assertEqual(self.game.battle["enemy_hp"], remaining)
            self.call(2184)
        self.assertIsNone(self.game.battle)
        self.call(2184)
        character = self.characters.list("owner946")[0]
        self.assertEqual((character["experience"], character["gold"]), (20, 5))

    def test_escape_returns_without_reward(self):
        self.game.begin_battle()
        self.call(2178, number(2))
        self.assertIsNone(self.game.battle)
        character = self.characters.list("owner946")[0]
        self.assertEqual((character["experience"], character["gold"]), (0, 0))

    def test_native_attack_command_and_animation_fields(self):
        self.game.begin_battle()
        replies = self.call(2177, number(3) + number(ENEMY_ID))
        actor = next(p for p in replies if p[:3] == b"\x00\x6d\x08")
        r = MessageReader(actor[3:])
        self.assertEqual([r.integer(), r.integer()], [self.character["id"], ENEMY_ID])
        self.assertEqual(r.take(1), b"\x01")
        self.assertEqual(r.integer(), ENEMY_ID)
        self.assertEqual(r.integer(), 10)
        r.finish()
        self.assertEqual(self.game.battle["enemy_hp"], 152)

    def test_settlement_idempotency_survives_new_store(self):
        self.assertTrue(self.characters.settle_battle("owner946", self.character["id"], "stable-battle", True))
        self.assertFalse(Characters(self.characters.database).settle_battle("owner946", self.character["id"], "stable-battle", True))
        character = self.characters.list("owner946")[0]
        self.assertEqual((character["experience"], character["gold"]), (20, 5))

    def test_victory_reward_survives_disconnect_before_animation_ack(self):
        self.game.begin_battle()
        for _ in range(2):
            self.call(2176, number(ENEMY_ID))
            self.call(2184)
        self.call(2176, number(ENEMY_ID))
        self.assertEqual(self.game.battle["phase"], "drawing")
        loaded = Characters(self.characters.database).list("owner946")[0]
        self.assertEqual((loaded["experience"], loaded["gold"]), (20, 5))


if __name__ == "__main__":
    unittest.main()
