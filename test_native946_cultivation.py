import unittest
from unittest.mock import patch

from native946_characters import scene_actor_packet
from native946_campaign import TRIALS, VERSION
from native946_codec import SCHEMAS, number, packet
from native946_cosmetics import Cosmetics
from native946_cultivation import LAO_JUN, MENG_PO, YU_RONG_WANG
from native946_game_login import NativeGameLogin, message_id
from native946_gameplay import FIGHTER_TYPES, NativeGameplay
from native946_login import MessageReader, string_field
from native946_party import packets
from native946_sessions import LEASES
import test_native946_npcs as fixtures


def read_record(reader, schema):
    fields = {}
    for name, field in SCHEMAS[schema]:
        if field == 'number':
            fields[name] = reader.integer()
        elif field == 'string':
            fields[name] = reader.string().decode('gb18030')
        else:
            fields[name] = int.from_bytes(reader.take(1 if field == 'byte' else 2), 'little')
    return fields


def find_actor(replies, cid, kind='oci_player'):
    ident = message_id(1590 if kind == 'oci_player' else 1592)
    for reply in packets(replies):
        if reply.startswith(ident):
            reader = MessageReader(reply[3:])
            actor = read_record(reader, kind)
            reader.finish()
            if actor['id'] == cid:
                return actor
    raise AssertionError('Missing native scene actor')


class CultivationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NpcGameplayTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.game = self.fixture.game
        self.progress = self.game.progression
        self.cid = self.progress.cid

    def stage(self):
        state = self.progress.snapshot()
        return tuple(state[key] for key in ('rebirth', 'ascension', 'sanctification'))

    def level(self, value):
        with self.progress.transaction() as db:
            db.execute('UPDATE characters SET level=? WHERE id=?', (value, self.cid))

    def open(self, location):
        self.fixture.approach(*location)
        reply = self.fixture.login.receive(packet(1034, ['number'], [location[1]]))
        if any(isinstance(a, dict) and a.get('op') in ('accept', 'trial_guide') for a in self.game.dialog_actions):
            service = next(i for i, a in enumerate(self.game.dialog_actions, 1)
                           if isinstance(a, dict) and a.get('op') == 'service')
            return self.choose(location, service)
        return reply

    def choose(self, location, op):
        return self.fixture.login.receive(packet(1032, ['byte', 'number'], [op, location[1]]))

    def test_rebirth_resets_practice_and_sends_the_new_learning_cap(self):
        from native946_campaign import Campaign
        from native946_progression import DATA, Progression
        from native946_school import SCHOOL_SKILLS, add_contribution

        master = (2361, 6311)
        skill = 6100

        def select(location, op):
            index = next(i for i, action in enumerate(self.game.dialog_actions, 1)
                         if isinstance(action, dict) and action.get('op') == op)
            return packets(self.choose(location, index))

        self.level(100)
        self.open(master)
        select(master, 'join')
        self.progress.shortcut(2, skill)
        with self.progress.transaction() as db:
            db.executemany('INSERT INTO learned_skills VALUES(?,?,0)', ((self.cid, 6036), (self.cid, 6127)))
            db.execute('UPDATE characters SET gold=1000 WHERE id=?', (self.cid,))

        for chapter, level, stage, cap in (('912101', 100, 1, 15000), ('912201', 120, 2, 20000)):
            with self.subTest(chapter=chapter):
                self.level(level)
                final = len(TRIALS[chapter]['nodes']) - 1
                with self.progress.transaction() as db:
                    self.progress._learn_level_spells(db)
                    db.execute("INSERT INTO native_campaign_runs VALUES(?,?,?,'active',?,0)",
                               (self.cid, chapter, final, VERSION))
                    db.execute('UPDATE learned_skills SET rank=? WHERE character_id=?', (cap - 5000, self.cid))
                    add_contribution(db, self.cid, 50)
                before = self.progress.snapshot()
                self.open(MENG_PO)
                reply = select(MENG_PO, 'complete')
                self.assertIn(packet(695, ['number', 'number'], [int(DATA['attributes']['Rei']), stage]), reply)
                raw = next(value for value in reply if value.startswith(message_id(699)))
                reader = MessageReader(raw[3:])
                reader.integer()
                for _ in range(reader.take(1)[0]):
                    read_record(reader, 'fly_stunt_info')
                skills = {row['skill_id']: row['practice']
                          for row in (read_record(reader, 'hero_skill_info') for _ in range(reader.take(1)[0]))}
                self.assertIn(skill, skills)
                self.assertEqual(set(skills.values()), {0})
                saved = Progression(self.fixture.path, 'npc_owner', self.cid).snapshot()
                self.assertEqual(saved['skills'], dict.fromkeys(before['skills'], 0))
                self.assertEqual(saved['shortcuts'], before['shortcuts'])

                self.open(master)
                raw = next(value for value in select(master, 'learn') if value.startswith(message_id(701)))
                reader = MessageReader(raw[3:])
                rows = [read_record(reader, 'learn_skill_info') for _ in range(reader.take(1)[0])]
                self.assertEqual(len(rows), 16)
                self.assertEqual({row['skill_id'] for row in rows}, (saved['skills'].keys() & SCHOOL_SKILLS) - {6036})
                self.assertEqual({(row['cur_value'], row['max_value']) for row in rows}, {(0, cap)})
                cross_school = next(row for row in rows if row['skill_id'] == 6111)
                self.assertEqual((cross_school['next_money'], cross_school['next_contribute']), (25, 5))
                self.fixture.login.receive(packet(749, ['number', 'number'], [skill, 1]))
                gold = self.progress.snapshot()['gold']
                reply = packets(self.fixture.login.receive(packet(749, ['number', 'number'], [6111, 10])))
                self.assertTrue(any(value.startswith(message_id(701)) for value in reply))
                learned = Progression(self.fixture.path, 'npc_owner', self.cid).snapshot()
                self.assertEqual((learned['skills'][6111], learned['gold']), (10, gold - 250))
                self.assertFalse(Campaign(self.game).advance(chapter, final))
                self.assertEqual(self.progress.snapshot()['skills'][skill], 1)

    def test_npc_advancement_updates_actor_observers_and_character_select(self):
        self.assertEqual(self.stage(), (0, 0, 0))
        self.level(99)
        self.open(MENG_PO)
        self.assertFalse(any(isinstance(a, dict) and a.get('kind') == 'cultivation'
                             for a in self.game.dialog_actions))
        for location, level, chapter, stage, halo, new_level, resistance in (
            (MENG_PO, 100, '912101', (1, 0, 0), 0, 30, 30),
            (MENG_PO, 120, '912201', (2, 0, 0), 0, 45, 46),
            (LAO_JUN, 140, '111321', (2, 1, 0), 100, 120, 65),
            (YU_RONG_WANG, 165, '222400', (2, 1, 1), 101, 165, 45),
        ):
            with self.subTest(chapter=chapter):
                self.level(level)
                final = len(TRIALS[chapter]['nodes']) - 1
                with self.progress.transaction() as db:
                    db.execute('INSERT INTO native_campaign_runs VALUES(?,?,?,\'active\',?,0)', (self.cid, chapter, final, VERSION))
                    db.execute('UPDATE characters SET experience=1234 WHERE id=?', (self.cid,))
                    db.execute('UPDATE progression SET cor=?,mag=?,strength=?,speed=?,points=? WHERE character_id=?',
                               (level, level, level, level, level * 4 + 20, self.cid))
                self.open(location)
                if chapter == '912101':
                    row = self.fixture.characters.create('npc_owner', '修行观测', 702)
                    row.update({key: self.game.character[key] for key in ('map_id', 'x', 'y')})
                    viewer = NativeGameplay(row, self.fixture.characters, lambda: ())
                else:
                    viewer.character.update({key: self.game.character[key] for key in ('map_id', 'x', 'y')})
                self.game.party_service.drain(viewer)
                index = next(i for i, item in enumerate(self.game.dialog_actions, 1)
                             if item.get('kind') == 'campaign' and item.get('op') == 'complete')
                before = self.progress.snapshot()
                reply = packets(self.choose(location, index))
                spawn = next(i for i, value in enumerate(reply) if value.startswith(message_id(1590)))
                self.assertEqual(reply[spawn - 1], packet(1604, ['number'], [self.cid]))
                self.assertEqual(reply[spawn + 1], packet(1585, ['number', 'number'], [self.cid, self.cid]))
                actor = find_actor(reply, self.cid)
                self.assertEqual((actor['rei'], actor['fly'], actor['feng'], actor['halo']), (*stage, halo))
                received = self.game.party_service.drain(viewer)
                self.assertEqual(received[0], packet(1604, ['number'], [self.cid]))
                peer = find_actor(received, self.cid)
                self.assertEqual((peer['rei'], peer['fly'], peer['feng'], peer['halo']), (*stage, halo))
                after = self.progress.snapshot()
                self.assertEqual(self.stage(), stage)
                self.assertEqual((after['level'], after['points']), (new_level, new_level * 4 + 80))
                self.assertEqual(after['experience'], 1000 + (1234 if chapter == '222400' else 0))
                self.assertEqual(after['cultivation_state']['earned_points'] - before['cultivation_state']['earned_points'], resistance)
                self.choose(location, index)
                self.assertEqual(self.stage(), stage)

        service = self.game.party_service
        service.detach_port(self.fixture.login.source_port)
        self.assertEqual(service.drain(viewer), (packet(1604, ['number'], [self.cid]),))
        service.detach_port(self.fixture.login.source_port)
        self.assertEqual(service.drain(viewer), ())
        LEASES.release_port(self.fixture.path,self.fixture.login.source_port)
        login = NativeGameLogin(544, None, number, None, self.fixture.characters)
        login.username = 'npc_owner'
        self.addCleanup(lambda: LEASES.release_port(self.fixture.path,544))
        restored = find_actor(login.enter_world(self.cid), self.cid)
        self.assertEqual((restored['rei'], restored['fly'], restored['feng'], restored['halo']), (2, 1, 1, 101))
        self.assertEqual(find_actor(service.drain(viewer), self.cid), restored)
        reader = MessageReader(login.account_packets()[1][3:])
        rows = [read_record(reader, 'player_info') for _ in range(reader.take(1)[0])]
        self.assertEqual((rows[0]['rei'], rows[0]['fly'], rows[0]['feng']), (2, 1, 1))
        rider = dict(login.current_character, appearance=dict(login.current_character['appearance'], mount_shape=2018))
        mounted = find_actor(scene_actor_packet(rider), self.cid, 'oci_rider')
        self.assertEqual((mounted['rei'], mounted['fly'], mounted['feng'], mounted['rhalo']), (2, 1, 1, 101))
        for value in login.gameplay.begin_battle():
            if not value.startswith(message_id(2147)):
                continue
            reader = MessageReader(value[3:])
            if reader.integer() != self.cid:
                continue
            for kind in FIGHTER_TYPES[1:17]:
                if kind == 'number':
                    reader.integer()
                elif kind == 'string':
                    reader.string()
                else:
                    reader.take(3 if isinstance(kind, tuple) else 1)
            self.assertEqual(reader.take(1), bytes([101]))
            break
        else:
            self.fail('Missing native battle actor')

    def test_jing_upgrade_refreshes_scene_and_team_without_losing_speed(self):
        from native946_fashion_shop import FashionShop
        from native946_mall_uses import MallUses
        from native946_premium_items import PILL_RUN_SPEED, SPEED_KEY
        from native946_role_systems import RULES, save
        from native946_storage import StorageBusy

        self.level(184)
        cost = RULES['jing_upgrade_exp'] * 2
        with self.progress.transaction() as db:
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1,sanctification=1 '
                       'WHERE character_id=?', (self.cid,))
            db.execute('UPDATE characters SET experience=? WHERE id=?', (cost, self.cid))
            MallUses(self.game).grant(db, SPEED_KEY, 56659, 1)
        failed = self.fixture.login.receive(message_id(2551))
        self.assertTrue(failed.startswith(message_id(98)))
        self.assertEqual(self.progress.snapshot()['experience'], cost)
        self.level(185)
        blocked = self.fixture.login.receive(message_id(2551))
        self.assertIn('请先在幽冥地府找地藏王完成化境·五蕴'.encode('gb18030'),blocked)
        self.assertEqual(self.progress.snapshot()['experience'], cost)
        with self.progress.transaction() as db:
            save(db,self.cid,'jing',dict(level=1,exp=0))
        self.game.sync_progression()
        row = self.fixture.characters.create('npc_owner', '化境观测', 702)
        viewer = NativeGameplay(row, self.fixture.characters, lambda: ())
        service = self.game.party_service
        self.fixture.login.receive(packet(2039, ['number'], [row['id']]))
        viewer.receive(2038, MessageReader(number(self.cid)))
        self.assertEqual(self.game.character['run_speed'], PILL_RUN_SPEED)
        self.assertEqual(viewer.character['run_speed'], PILL_RUN_SPEED)
        row = self.fixture.characters.create('npc_owner', '远处观测', 702)
        row['x'] = self.game.character['x'] - 120
        distant = NativeGameplay(row, self.fixture.characters, lambda: ())
        service.reconnect_packets(self.game)
        self.assertEqual(find_actor(service.drain(distant), self.cid)['jing'], 1)
        service.drain(viewer)

        frame_packets = FashionShop.frame_packets
        with patch.object(FashionShop, 'frame_packets', autospec=True) as render:
            render.side_effect = [StorageBusy('fixture read contention'),
                                  frame_packets(FashionShop(self.game))]
            reply = self.fixture.login.receive(message_id(2551))
            self.assertEqual(render.call_count, 2)
        self.assertTrue(any(value.startswith(message_id(2548)) for value in reply))
        shown_to_peer = service.drain(viewer)
        for values in (reply, shown_to_peer, service.drain(distant)):
            actor = find_actor(values, self.cid)
            self.assertEqual(values.count(packet(1604, ['number'], [self.cid])), 1)
            self.assertEqual((actor['jing'], actor['halo'], actor['run_speed']), (2, 101, PILL_RUN_SPEED))
        panel = next(value for value in shown_to_peer if value.startswith(message_id(3228)))
        reader = MessageReader(panel[3:])
        reader.integer()
        reader.string()
        team = {row['id']: row for row in (read_record(reader, 'team_player_info')
                                          for _ in range(reader.take(1)[0]))}
        self.assertEqual(team[self.cid]['jing'], 2)
        self.assertEqual(self.progress.snapshot()['experience'], 0)
        failed = self.fixture.login.receive(message_id(2551))
        self.assertTrue(failed.startswith(message_id(98)))
        self.assertEqual(service.drain(viewer), ())
        self.assertEqual(service.drain(distant), ())

    def test_jing_panel_fields_and_unverified_actions_preserve_saved_points_and_items(self):
        from native946_role_systems import RULES,give,save

        self.level(185)
        with self.progress.transaction() as db:
            save(db,self.cid,'jing',dict(level=RULES['jing_max_level'],exp=0))
            db.execute('UPDATE characters SET experience=1000000 WHERE id=?',(self.cid,))
            db.execute('UPDATE progression SET cor=201,mag=202,strength=203,speed=204 WHERE character_id=?',
                       (self.cid,))
            db.executemany('INSERT INTO resistance_points VALUES(?,?,?)',
                           ((self.cid,'FaintPoint',2),(self.cid,'DefPoint',3)))
            give(db,self.progress,46222)
        before=self.progress.snapshot()
        item=next(row['id'] for row in before['items'] if row['type']==46222)
        reply=self.fixture.login.receive(message_id(2549))
        reader=MessageReader(next(p for p in packets(reply) if p.startswith(message_id(2548)))[3:])
        self.assertEqual(reader.integer(),RULES['jing_max_level'])
        reader.string()
        self.assertEqual(reader.integer(),0)
        reader.string()
        reader.integer()
        self.assertEqual(reader.integer(),0)
        reader.string()
        def pairs(count):
            return {reader.string().decode('gb18030'):reader.integer() for _ in range(count)}
        self.assertEqual(pairs(4),dict(Cor=201,Mag=202,Str=203,Spe=204))
        reader.string()
        resists=pairs(10)
        self.assertEqual(resists['FaintPoint'],2)
        self.assertEqual(resists['AbsorbPoint'],0)
        self.assertNotIn('DefPoint',resists)
        reader.finish()
        for request in (packet(2550,['number'],[1]), packet(2550,['number'],[2]),
                        message_id(2799),packet(807,['number','number'],[0,item])):
            reply=self.fixture.login.receive(request)
            msg_id=message_id(98)
            notice=next(p for p in packets(reply) if p.startswith(msg_id))
            self.assertIn('暂未开放',MessageReader(notice[len(msg_id):]).string().decode('gb18030'))
            self.assertEqual(self.progress.snapshot(),before)

    def test_attach_exposes_saved_stage_before_first_self_scene(self):
        with self.progress.transaction() as db:
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1 WHERE character_id=?', (self.cid,))
        row = self.fixture.characters.create('npc_owner', '入场观测', 702)
        viewer = NativeGameplay(row, self.fixture.characters, lambda: ())
        service = self.game.party_service
        service.detach(self.cid)
        saved = self.fixture.characters.enter('npc_owner', self.cid)
        reloaded = NativeGameplay(saved, self.fixture.characters, lambda: ())
        actor = find_actor(service.scene_players(viewer), reloaded.character['id'])
        self.assertEqual((actor['rei'], actor['fly'], actor['halo']), (2, 1, 100))

    def test_creating_another_role_keeps_saved_stage_in_character_list(self):
        with self.progress.transaction() as db:
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1 WHERE character_id=?', (self.cid,))
        request = message_id(913) + string_field('新建角色') + number(702) + number(0) + b'\x00'
        reply = self.fixture.login.receive(request)
        reader = MessageReader(reply[1][3:])
        rows = {row['id']: row for row in (read_record(reader, 'player_info')
                                          for _ in range(reader.take(1)[0]))}
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[self.cid]['rei'], rows[self.cid]['fly']), (2, 1))

    def test_existing_wearable_takes_priority_over_stage_aura(self):
        self.level(140)
        with self.progress.transaction() as db:
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1 WHERE character_id=?', (self.cid,))
            db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,55011,29,2,1)', (self.cid,))
        Cosmetics(self.game).refresh()
        self.assertEqual(self.game.character['appearance']['halo'], 2)
        with self.progress.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=? AND type=55011', (self.cid,))
        Cosmetics(self.game).refresh()
        self.assertEqual(self.game.character['appearance']['halo'], 100)


if __name__ == '__main__':
    unittest.main()
