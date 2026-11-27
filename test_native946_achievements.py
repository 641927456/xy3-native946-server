"""Verify the character-page protocol and atomic reward claims."""

import unittest
from hashlib import sha256
from pathlib import Path
import sys

from native946_achievements import DATA, LEVELS, NEWBIE_ACHIEVEMENT, REWARDS, REQUEST_TYPES
from native946_codec import SCHEMAS, packet
from native946_game_login import message_id
from native946_login import MessageReader
from native946_role_systems import state
from native946_school_daily import ACHIEVEMENT_ID as SCHOOL_ACHIEVEMENT
import test_native946_tasks


class NativeAchievementSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent
        sys.path[:0] = [str(cls.root / 'analysis' / name)
                       for name in ('town-portals-20261125', 'cosmetics-20261126')]

    def test_archive_matches_reward_scope_and_response_field_order(self):
        from client_archive import Archive
        from literal_schema import replay

        script = Archive(self.root / 'client-946/bin_script.wdf')
        name, schema = replay(script.read('common/protocol/tbl/achieve_show_info.tbl'))
        self.assertEqual(SCHEMAS[name], [list(row) for row in schema.items()])
        configs = Archive(self.root / 'client-946/resource/configs.wdf')
        raw = configs.read('info/info_achieve_new.lua')
        self.assertEqual(DATA['source_chunk_sha256'], sha256(raw).hexdigest())
        rows = {aid: dict(row.items()) for aid, row in replay(raw)[0].items()}
        levels = {aid for aid, row in rows.items() if row['pos'] // 100 == 401}
        self.assertEqual(set(LEVELS), set(levels))
        self.assertEqual(DATA['newbie']['mini_desc'], '成功完成新手剧情')
        stars = {aid for aid,row in rows.items() if 2300101 <= aid <= 2301901 and row['pos']==80200}
        self.assertEqual(len(stars),19)
        self.assertEqual(set(REWARDS), levels | {NEWBIE_ACHIEVEMENT, SCHOOL_ACHIEVEMENT} | stars)
        for aid, row in REWARDS.items():
            decoded = {key: value for key,value in row.items() if key not in ('title','naturals')}
            self.assertEqual(decoded, {key: rows[aid][key] for key in decoded})

    def test_stock_click_and_replies_reach_new_panel(self):
        from client_archive import Archive
        from native946_battle_ui import PrototypeReader
        from validate_lua import LuaRuntime, function

        archive = Archive(self.root / 'client-946/bin_script.wdf')

        def callback(tag, *indices):
            offset, size = archive.rows[tag]
            with archive.path.open('rb') as file:
                file.seek(offset)
                raw = file.read(size)
            node = PrototypeReader(bytes(value ^ (raw[0] ^ 27) for value in raw)).prototype()
            for index in indices:
                node = node['children'][index]
            return node

        lua = LuaRuntime(encoding='gb18030', unpack_returned_tuples=True)
        calls = []
        modules = {
            'dlg_achievement_panel': lua.table(achieve_time_cache=lua.table()),
            'dlg_achievement_new_panel': lua.table(show=lambda *args: calls.append(('show', *args))),
        }
        env = lua.table(HERO_D=lua.table(get=lambda _, name: 17),
                        gi_controlkey_status=lua.table(shift=0),
                        safe_dostring=lua.eval('function(s) return assert(loadstring(s))() end'))
        env['import'] = modules.__getitem__
        env.for_caller = lua.table(s_achieve_pre_look=lambda *args: calls.append((3484, *args)),
                                  s_achieve_new_look=lambda *args: calls.append((3483, *args)))
        function(lua, callback(0x84C061E4, 1, 19), env)()
        self.assertEqual(calls.pop(), (3484, 17, 0, 2))
        function(lua, callback(0xB295BF90, 378), env)(2, 17, 0, 2)
        self.assertEqual(calls.pop(), (3483, 17))
        function(lua, callback(0xB295BF90, 377), env)(
            lua.table(owner_id=17), 0, '{}', '{[1110301]=1}')
        name, parent, owner, attention, done, pending = calls.pop()
        self.assertEqual((name, parent, owner.owner_id, attention), ('show', None, 17, 0))
        self.assertEqual(list(done.items()), [])
        self.assertEqual(list(pending.items()), [(1110301, 1)])


class AchievementTests(unittest.TestCase):
    def setUp(self):
        fixture = test_native946_tasks.TaskJournalTests('test_native_query_event_returns_only_server_owned_story_state')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.p, self.login = fixture.p, fixture.login

    def send(self, mid, *values):
        return self.login.receive(packet(mid, REQUEST_TYPES[mid], values))

    def level(self, level, *, rebirth=0, ascension=0, sanctification=0):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=? WHERE id=?', (level, self.p.cid))
            db.execute('UPDATE cultivation_state SET rebirth=?,ascension=?,sanctification=? WHERE character_id=?',
                       (rebirth, ascension, sanctification, self.p.cid))

    def panel(self):
        reply, highlight = self.send(3483, self.p.cid)
        r = self.reader(reply, 3479)
        name = r.string().decode('gb18030')
        fans, icon, evaluate, score, owner, rank, attention = (r.integer() for _ in range(7))
        self.assertEqual((name, owner, icon), ('剧情测试', self.p.cid, 702))
        self.assertEqual((fans, evaluate, rank, attention), (0, 0, 0, 0))
        done, pending = (r.string().decode('gb18030') for _ in range(2))
        r.finish()
        return score, done, pending, self.reader(highlight, 7).integer()

    def reader(self, reply, mid):
        prefix = message_id(mid)
        self.assertTrue(reply.startswith(prefix), (mid, reply[:8]))
        return MessageReader(reply[len(prefix):])

    def confirm_token(self, dialog):
        r = self.reader(dialog, 1020)
        for _ in range(4):
            self.assertTrue(r.string())
        token = r.integer()
        r.finish()
        return token

    def test_open_negotiates_version_and_reads_eligible_rewards(self):
        self.assertEqual(self.send(3484, self.p.cid, 0, 2),
                         packet(3480, ['number'] * 4, [2, self.p.cid, 0, 2]))
        self.assertEqual(self.panel(), (0, '{}', '{}', 0))
        self.level(30)
        self.assertEqual(self.panel(), (1, '{}', '{[1110301]=1}', 1))
        self.assertEqual(self.send(3481, 1110301),
                         packet(3478, ['number', 'number', 'string'], [1110301, -1, '']))

    def test_first_skill_grant_requires_confirmation_and_cannot_repeat(self):
        self.level(30)
        with self.p.transaction() as db:
            db.execute('DELETE FROM learned_skills WHERE character_id=? AND skill_id=6107', (self.p.cid,))
        before = self.p.snapshot()['skills']
        token = self.confirm_token(self.send(3482, 1110301))
        self.assertEqual(self.p.snapshot()['skills'], before)
        self.reader(self.send(1031, 1, token + 1), 98)
        self.assertIsNone(self.send(1031, 2, token))
        self.assertEqual(self.panel(), (1, '{}', '{[1110301]=1}', 1))
        token = self.confirm_token(self.send(3482, 1110301))
        # A fresh request cannot carry an arbitrary achievement ID in its answer.
        claim = self.send(1031, 1, token)
        self.assertEqual(claim[0], packet(6, ['number'], [1110301]))
        after = self.p.snapshot()['skills']
        self.assertEqual(after[6100], before[6100] + 200)
        self.assertEqual(after[6107], 200)
        self.assertEqual(self.panel(), (1, '{[1110301]=1}', '{}', 0))
        self.reader(self.send(1031, 1, token), 98)
        self.assertEqual(self.send(3482, 1110301)[0], claim[0])
        self.assertEqual(self.p.snapshot()['skills'], after)
        from native946_progression import Progression

        reopened = Progression(self.p.database, self.p.owner, self.p.cid)
        with reopened.transaction(write=False) as db:
            self.assertEqual(state(db, self.p.cid, 'achievements')['claimed'], [1110301])

    def test_claim_enforces_level_stage_and_uses_table_amounts(self):
        self.level(140)
        for aid in (1110308, 1110314, 1110101):
            self.reader(self.send(3482, aid), 98)
        self.level(140, rebirth=2, ascension=1)
        old = self.p.snapshot()
        self.reader(self.send(3482, 1110314)[0], 6)
        self.assertEqual(self.p.snapshot()['experience'] - old['experience'], 500000)
        self.reader(self.send(3482, 1110313)[0], 6)
        self.assertEqual(sum(i['amount'] for i in self.p.snapshot()['items'] if i['type'] == 6030), 5)

    def test_seventy_and_hundred_level_spell_rewards(self):
        for aid, level, skills, amount in (
            (1110302, 70, (6102, 6109), 600),
            (1110303, 100, (6103, 6110), 500),
        ):
            self.level(level - 1)
            self.reader(self.send(3482, aid), 98)
            self.level(level)
            self.assertIn(f'[{aid}]=1', self.panel()[2])
            token = self.confirm_token(self.send(3482, aid))
            self.reader(self.send(1031, 1, token)[0], 6)
            self.assertEqual([self.p.snapshot()['skills'][skill] for skill in skills], [amount, amount])
            self.assertIn(f'[{aid}]=1', self.panel()[1])
            self.assertNotIn(f'[{aid}]=1', self.panel()[2])

    def test_full_bag_preserves_pending_item_reward(self):
        self.level(120, rebirth=1)
        from native946_mall_bags import pages

        with self.p.transaction() as db:
            for page in pages(db, self.p.cid):
                for slot in range(1, 25):
                    db.execute('INSERT OR IGNORE INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,?,?,1)',
                               (self.p.cid, page, slot))
        self.reader(self.send(3482, 1110308), 98)
        self.assertIn('[1110308]=1', self.panel()[2])
        with self.p.transaction() as db:
            db.execute('DELETE FROM inventory WHERE id=(SELECT max(id) FROM inventory WHERE character_id=?)', (self.p.cid,))
        self.reader(self.send(3482, 1110308)[0], 6)
        self.assertEqual(sum(i['amount'] for i in self.p.snapshot()['items'] if i['type'] == 6030), 5)
