import unittest
from unittest.mock import patch

from native946_characters import scene_actor_packet
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

    def test_npc_advancement_updates_actor_observers_and_character_select(self):
        self.assertEqual(self.stage(), (0, 0, 0))
        self.level(99)
        self.open(MENG_PO)
        self.assertFalse(any(isinstance(a, dict) and a.get('kind') == 'cultivation'
                             for a in self.game.dialog_actions))
        for location, level, action, stage, halo in (
            (MENG_PO, 100, 'first', (1, 0, 0), 0),
            (MENG_PO, 120, 'second', (2, 0, 0), 0),
            (MENG_PO, 140, 'third', (3, 0, 0), 0),
            (LAO_JUN, 140, 'ascend_after_third', (2, 1, 0), 100),
            (YU_RONG_WANG, 165, 'sanctify', (2, 1, 1), 101),
        ):
            with self.subTest(action=action):
                self.level(level)
                self.open(location)
                if action == 'first':
                    row = self.fixture.characters.create('npc_owner', '修行观测', 702)
                    row.update({key: self.game.character[key] for key in ('map_id', 'x', 'y')})
                    viewer = NativeGameplay(row, self.fixture.characters, lambda: ())
                else:
                    viewer.character.update({key: self.game.character[key] for key in ('map_id', 'x', 'y')})
                self.game.party_service.drain(viewer)
                index = next(i for i, item in enumerate(self.game.dialog_actions, 1)
                             if item == {'kind': 'cultivation', 'op': action})
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
                self.assertEqual([after[k] for k in ('level', 'experience', 'gold', 'points', 'items')],
                                 [before[k] for k in ('level', 'experience', 'gold', 'points', 'items')])
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
        from native946_role_systems import RULES
        from native946_storage import StorageBusy

        self.level(165)
        cost = RULES['jing_upgrade_exp']
        with self.progress.transaction() as db:
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1,sanctification=1 '
                       'WHERE character_id=?', (self.cid,))
            db.execute('UPDATE characters SET experience=? WHERE id=?', (cost, self.cid))
            MallUses(self.game).grant(db, SPEED_KEY, 56659, 1)
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
        self.assertEqual(find_actor(service.drain(distant), self.cid)['jing'], 0)
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
            self.assertEqual((actor['jing'], actor['halo'], actor['run_speed']), (1, 101, PILL_RUN_SPEED))
        panel = next(value for value in shown_to_peer if value.startswith(message_id(3228)))
        reader = MessageReader(panel[3:])
        reader.integer()
        reader.string()
        team = {row['id']: row for row in (read_record(reader, 'team_player_info')
                                          for _ in range(reader.take(1)[0]))}
        self.assertEqual(team[self.cid]['jing'], 1)
        self.assertEqual(self.progress.snapshot()['experience'], 0)
        failed = self.fixture.login.receive(message_id(2551))
        self.assertTrue(failed.startswith(message_id(98)))
        self.assertEqual(service.drain(viewer), ())
        self.assertEqual(service.drain(distant), ())

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
