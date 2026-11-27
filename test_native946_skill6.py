"""Native post-sanctification skill training and persistent wash receipts."""

import ast
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from native946_game_login import message_id
from native946_role_battle import profile, mana_cost, snapshot as battle_effects
from native946_role_systems import save
from native946_skill6 import ARGUMENTS, MANUAL, REQUESTS, saved, school_skills, skill_row
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
        self.assertEqual(skill_row(self.skill, row, actor)['cost'],
                         '法力：' + str(mana_cost(actor, profile(self.skill))))

    def test_legacy_native_feng_spells_and_extra_learning_follow_the_native_jing_gate(self):
        from native946_codec import SCHEMAS
        from native946_login import MessageReader

        self.seed()
        self.action(1874)
        self.assertEqual({school: skill for school, skill in
                          [(15,6124), (16,6133), (17,6112), (18,6105), (719,1710)]
                          if skill in school_skills(school)},
                         {15:6124,16:6133,17:6112,18:6105,719:1710})
        for stage in (1, 2):
            with self.p.transaction() as db:
                save(db, self.p.cid, 'jing', {'level':stage, 'exp':0})
            reply = next(p for p in self.p.skill_packets() if p.startswith(message_id(686)))
            reader = MessageReader(reply[3:])
            for _ in range(reader.take(1)[0]):
                for _, field in SCHEMAS['feng_skill_info']:
                    (reader.string if field == 'string' else reader.integer)()
            self.assertEqual(reader.take(1)[0], 0)
            self.assertEqual(reader.integer(), int(stage == 2))
            self.assertEqual(reader.integer(), 0)
            self.assertIn(6105, [reader.integer() for _ in range(reader.take(1)[0])])
            reader.finish()
            if stage == 1:
                self.assertTrue(self.action(2817, 6105).startswith(message_id(98)))
                self.assertNotIn('6105', self.state()['skills'])
        self.contains(self.action(2817, 6105), 686)
        self.assertIn(6105, self.p.snapshot()['skills'])

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

    def test_native_passive_catalog_matches_race_training_and_second_slot(self):
        from native946_codec import SCHEMAS
        from native946_login import MessageReader

        self.seed()
        for race,school,active,first in ((1,2,6002,6051),(2,9,6010,6056),(3,5,6016,6061)):
            with self.subTest(race=race):
                with self.p.transaction() as db:
                    db.execute('UPDATE characters SET race=? WHERE id=?',(race,self.p.cid))
                    save(db,self.p.cid,'school',dict(school=school))
                    save(db,self.p.cid,'jing',dict(level=5,exp=0))
                    self.assertIn(active,school_skills(school))
                    row=saved(db,self.p.cid)
                    row['skills'][str(active)]=dict(level=5,exp=0,wash={})
                    save(db,self.p.cid,'skill6',row)
                reply=next(p for p in self.p.skill_packets() if p.startswith(message_id(686)))
                reader=MessageReader(reply[3:])
                def records(schema):
                    return [{name:(reader.string().decode('gb18030') if kind=='string' else reader.integer())
                             for name,kind in SCHEMAS[schema]} for _ in range(reader.take(1)[0])]
                records('feng_skill_info')
                rows=records('passive_skill_info')
                self.assertEqual([r['skill_id'] for r in rows],list(range(first,first+5)))
                self.assertEqual([r['skill_status'] for r in rows],[1,1,1,0,0])
                self.assertIn('激活及洗髓待接通',rows[0]['desc'])
                self.assertIn('7级',rows[-1]['desc'])
                if race==2:
                    self.assertIn('#R50000#n',rows[1]['desc'])
                reader.integer()
                self.assertEqual(reader.integer(),1)
                for _ in range(reader.take(1)[0]):
                    reader.integer()
                reader.finish()
                before=self.p.snapshot()
                for request in ((1880,first,MANUAL),(1881,first),(1877,first,0)):
                    self.assertTrue(self.action(*request).startswith(message_id(98)))
                self.assertTrue(self.send(1882,['number','byte'],[first,1]).startswith(message_id(98)))
                self.assertEqual(self.p.snapshot(),before)

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
