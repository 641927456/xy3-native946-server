"""Native post-sanctification skill training and persistent wash receipts."""

import ast
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from native946_game_login import message_id
from native946_role_battle import profile, mana_cost, snapshot as battle_effects
from native946_role_systems import save
from native946_skill6 import ARGUMENTS, MANUAL, REQUESTS, saved
import test_native946_campaign as fixtures


class Skill6Tests(unittest.TestCase):
    setUp = fixtures.CampaignTests.setUp
    send = fixtures.CampaignTests.send
    skill = 6036

    def seed(self, *, sanctified=1, qi=1000):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=165 WHERE id=?', (self.p.cid,))
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1,sanctification=? WHERE character_id=?',
                       (sanctified, self.p.cid))
            save(db, self.p.cid, 'school', dict(school=18))
            save(db, self.p.cid, 'talent', dict(zhenqi=qi))

    def action(self, mid, *args):
        return self.send(mid, ['number']*len(args), args)

    def contains(self, reply, mid):
        self.assertTrue(any(p.startswith(message_id(mid)) for p in reply if isinstance(p, bytes)), mid)

    def state(self):
        with self.p.transaction(write=False) as db:
            return saved(db, self.p.cid)

    def test_training_exchange_wash_keep_and_replay(self):
        self.seed()
        self.contains(self.action(1874), 1867)
        self.contains(self.action(1876, self.skill), 1868)
        self.assertTrue(self.action(1875, 6000).startswith(message_id(98)))
        for _ in range(4):
            self.contains(self.action(1875, self.skill), 1868)
        row = self.state()['skills'][str(self.skill)]
        self.assertEqual((row['level'], row['exp']), (3, 100))
        self.contains(self.action(1879), 1870)
        self.action(1878, self.skill, MANUAL, 1)
        self.assertEqual(self.state()['skills'][str(self.skill)]['exp'], 0)
        self.contains(self.action(1881, self.skill), 1871)
        with patch('native946_skill6.randbelow', return_value=15):
            self.contains(self.action(1880, self.skill, MANUAL), 1872)
        self.assertTrue(self.action(1880, self.skill, MANUAL).startswith(message_id(98)))
        self.assertEqual(self.state()['skills'][str(self.skill)]['wash'], {})
        self.contains(self.action(1881, self.skill), 1872)
        self.contains(self.action(1877, self.skill, 0), 1869)
        self.assertTrue(self.action(1877, self.skill, 0).startswith(message_id(98)))
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM inventory WHERE character_id=? AND type=?',
                                        (self.p.cid, MANUAL)).fetchone()[0], 0)
            actor = battle_effects(db, self.p.cid)
        self.assertEqual(actor['role_feng'][self.skill], dict(mana=25, power=65))
        self.assertLess(mana_cost(actor, profile(self.skill)), mana_cost({}, profile(self.skill)))

    def test_stage_and_resource_gates_leave_state_unchanged(self):
        self.seed(sanctified=0)
        self.assertTrue(self.action(1874).startswith(message_id(98)))
        self.assertEqual(self.state()['skills'], {})
        self.seed(qi=0)
        self.action(1874)
        before = self.state()
        for req in ((1875, self.skill), (1878, self.skill, MANUAL, 1), (1880, self.skill, MANUAL)):
            self.assertTrue(self.action(*req).startswith(message_id(98)))
        self.assertEqual(self.state(), before)

    def test_protocol_schemas_match_original_pto(self):
        import sys
        root = Path(__file__).parent
        sys.path.insert(0,str(root/'analysis/town-portals-20261125'))
        from client_archive import Archive
        from literal_schema import replay
        from build_world import plain

        registry = json.loads((root/'analysis/rare-guardian-sources-20261126/native-message-names.json').read_text('utf8'))
        archive = Archive(root/'client-946/bin_script.wdf')

        def fields(mid):
            rows = plain(replay(archive.read(registry[str(mid-2)]))[0])['arg_list']
            result = []
            for row in rows:
                if len(row)==2:
                    result.append(row[1])
                elif row[2]==0:
                    result.append(('array',row[1]))
                else:
                    result.append(('fixed_array',row[1],row[2]))
            return result

        source = root/'native946_skill6.py'
        checked = 0
        for node in ast.walk(ast.parse(source.read_text('utf8'))):
            if (isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='packet'
                    and isinstance(node.args[0],ast.Constant)):
                mid = node.args[0].value
                self.assertEqual(ast.literal_eval(node.args[1]),fields(mid),mid)
                checked += 1
        self.assertEqual(checked,7)
        self.assertEqual(REQUESTS,set(ARGUMENTS))
        for mid,count in ARGUMENTS.items():
            expected = fields(mid)
            self.assertEqual(count,len(expected),mid)
            self.assertTrue(all(kind=='number' for kind in expected[:-1]),mid)
            self.assertIn(expected[-1] if expected else 'number',('number','byte'),mid)

    def test_native_fixture_keeps_original_database_and_connection(self):
        from native946_cultivation_acceptance import run

        original = self.login.gameplay
        before = original.progression.snapshot()
        run(self.login, dict(action='cultivation_fixture_start'))
        self.addCleanup(lambda: run(self.login, dict(action='cultivation_fixture_restore'))
                        if getattr(self.login, '_cultivation_fixture', None) else None)
        sandbox = self.login.gameplay
        self.assertNotEqual(sandbox.characters.database, original.characters.database)
        self.assertEqual(sandbox.source_port, original.source_port)
        run(self.login, dict(action='cultivation_fixture_next'))
        self.assertEqual(sandbox.progression.snapshot()['level'], 98)
        run(self.login, dict(action='cultivation_fixture_restore'))
        self.assertIs(self.login.gameplay, original)
        self.assertEqual(original.progression.snapshot(), before)
