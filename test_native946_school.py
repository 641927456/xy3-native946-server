"""Play the normal school NPC and task flow using temporary characters."""
from pathlib import Path
from random import Random
from struct import pack
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import SCHEMAS, number
from native946_gameplay import NativeGameplay
from native946_login import MessageReader, string_field
from native946_map import DIRECTIONS, load_map
from native946_npc_quests import Quests, day_key
from native946_progression import DATA, Progression
from native946_school import (EXTRA_SKILLS, MASTERS, NATIVE_SCHOOL_DESCRIPTIONS, SKILLS, SUPPLY_TIERS,
                              VISIT_TIERS, contribution, draw_objective)
from native946_world import world
from test_native946_wilderness_party import mid


class SchoolTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / 'school.sqlite3'
        owner = 'school_fixture'
        LocalAccounts(self.db).register(dict(username=owner, password='fixture-password'))
        self.characters = Characters(self.db)
        self.hero = self.characters.create(owner, '门派测试', 113)
        self.game = NativeGameplay(self.hero, self.characters, lambda: ())
        self.progression = self.game.progression
        with self.progression.transaction() as db:
            db.execute('UPDATE characters SET level=20,gold=500 WHERE id=?', (self.progression.cid,))
            db.execute('UPDATE progression SET cor=20,mag=20,strength=20,speed=20 WHERE character_id=?',
                       (self.progression.cid,))
        s = self.progression.snapshot()
        self.progression.set_vitals(s['hpmax'], s['mpmax'])

    def request(self, kind, *args, prefix=b'', suffix=b''):
        result = self.game.receive(kind, MessageReader(prefix + b''.join(number(n) for n in args) + suffix))
        return result if isinstance(result, tuple) else (result,) if result else ()

    def position(self, scene, x, y):
        c = self.hero
        revision = self.characters.change_scene(c['owner'], c['id'], c['map_id'], scene, x, y, c.get('position_revision',0))
        c.update(map_id=scene, x=x, y=y, position_revision=revision)
        self.game.grid = load_map(scene)
        self.game._arrival_guard = None

    def visit(self, scene, npc):
        info = world().npc(scene,npc)
        self.position(scene,*load_map(scene).nearest_walkable(info['x']//20,info['y']//20))
        return self.request(1034, npc)

    def choose(self, selector):
        index = next(i for i, action in enumerate(self.game.dialog_actions, 1) if selector(action))
        return self.request(1032, self.game.dialog_npc, prefix=bytes([index]))

    def choose_op(self, operation):
        return self.choose(lambda action: isinstance(action, dict) and action.get('op') == operation)

    def join(self):
        self.visit(2001, 1139)
        route = next(row for row in world().available_transports(2001, 1139, self.hero) if row['to'] == 2030)
        self.choose(lambda action: action == route['trans_index'])
        self.assertEqual(self.hero['map_id'], 2030)
        self.visit(2030, 3101)
        self.assertIn(5019,[mid(p) for p in self.choose_op('join')])
        self.assertEqual(self.progression.snapshot()['school'],2)

    def read_learn_list(self, packets):
        raw = next(p for p in packets if mid(p) == 701)
        reader = MessageReader(raw[3:])
        rows = [{key: reader.integer() for key, field in SCHEMAS['learn_skill_info']}
                for _ in range(reader.take(1)[0])]
        points = reader.integer()
        reader.finish()
        return rows, points

    def accept_supply(self,ring=2,item=4000,amount=1,level=20):
        self.join()
        with self.progression.transaction() as db:
            db.execute("INSERT INTO native_npc_activity_progress VALUES(?,'school',?,?,?,?)",
                       (self.progression.cid,day_key(),ring-1,ring-1,ring))
            db.execute('UPDATE characters SET level=? WHERE id=?',(level,self.progression.cid))
        self.visit(2030,3101)
        with patch('native946_npc_quests.draw_objective',return_value=('supply',item,amount)):
            packets=self.choose_op('task')
        return Quests(self.game).active('school'),packets

    def open_supply(self):
        self.visit(2030,3101)
        raw=next(p for p in self.choose_op('supply') if mid(p)==3100)
        reader=MessageReader(raw[3:])
        header=(reader.integer(),reader.take(1)[0],reader.string().decode('gb18030'))
        rows=tuple([reader.integer() for _ in range(reader.take(1)[0])] for _ in range(3))
        session=reader.integer()
        reader.finish()
        return (*header,*rows,session)

    def test_master_services_pay_and_unlock_the_native_map_flight(self):
        from native946_inventory_features import give
        from native946_role_growth import talents
        from native946_school import add_contribution

        self.join()
        p = self.progression
        with p.transaction() as db:
            add_contribution(db,p.cid,400)
            give(db,p,8611)

        def activate(service):
            self.visit(2030,3101)
            self.choose_op(service)

        activate('nature')
        self.choose(lambda action: action.get('value') == 1)
        self.assertEqual(p.snapshot()['disposition'],1)
        activate('donate')
        self.choose_op('school_service_confirm')
        activate('meditation')
        self.choose(lambda action: action.get('service') == 'meditation' and not action['value'])
        for _ in range(2):
            activate('meditation')
            self.choose(lambda action: action.get('service') == 'flight')
        with p.transaction(write=False) as db:
            self.assertEqual(contribution(db,p.cid),170)
            self.assertEqual(talents(db,p.cid)['zhenqi'],20)
        self.assertEqual(p.snapshot()['flight_level'],2)
        response, = self.request(1766,2001)
        self.assertEqual(mid(response),1762)
        reader = MessageReader(response[3:])
        target = world().npc(2001,1139)
        self.assertEqual(reader.integer(),2001)
        self.assertEqual(reader.string(),f"{{{{{target['x']},{target['y']}}}}}".encode('ascii'))
        self.assertEqual(reader.take(1),b'\x01')
        self.assertEqual(reader.integer(),2)
        reader.finish()
        self.assertIn(1593,[mid(packet) for packet in self.request(1764,2001,1)])
        self.assertEqual(self.hero['map_id'],2001)

    def test_school_supply_native_panel_purchase_and_settlement(self):
        import sys
        root = Path(__file__).parent
        sys.path[:0] = [str(root/'analysis/story-content-mining-20261126'), str(root/'analysis/cosmetics-20261126')]
        from trace_original_links import Archive, Reader, chunk
        from validate_lua import LuaRuntime, function

        quest,packets=self.accept_supply()
        item=quest['supply_item']
        name=DATA['items'][str(item)]['name']
        guardian=self.game.summons.claim_starter()
        self.assertIn(item,SUPPLY_TIERS[0])
        self.assertEqual((quest['ring'],quest['scene'],quest['npc'],quest['dynamic']),(2,2030,3101,0))
        self.assertTrue({1973,1978}<={mid(p) for p in packets})
        for kind in (1973,1978):
            self.assertIn(name,next(p for p in packets if mid(p)==kind).decode('gb18030',errors='ignore'))
        tid=930000+quest['id']
        detail=next(p for p in self.request(1986,tid) if mid(p)==1971).decode('gb18030',errors='ignore')
        self.assertIn(name,detail)
        self.assertIn('华重生',detail)
        self.assertIn('NpcId',detail)

        with self.progression.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=? AND type=?',(self.progression.cid,item))
        *_,held,session=self.open_supply()
        self.assertEqual(held,[0])
        self.assertIn(98,[mid(p) for p in self.request(3102,session,1)])
        self.visit(2030,3101)
        self.assertIn(2966,[mid(p) for p in self.choose_op('supply_shop')])
        self.visit(2034,1271)
        self.assertIn(1697,[mid(p) for p in self.choose_op('open')])
        self.request(1716,1271,item,2)
        self.assertIn(98,[mid(p) for p in self.request(3102,session,1)])
        before=Progression(self.db,self.hero['owner'],self.hero['id']).snapshot()
        self.assertEqual(before['npc_quests'][0]['supply_item'],item)
        self.assertEqual(sum(i['amount'] for i in before['items'] if i['type']==item),2)

        npc,flag,chat,types,needed,held,session=self.open_supply()
        self.assertEqual((npc,flag,types,needed,held),(3101,1,[item],[1],[2]))
        self.assertIn(name,chat)
        archive=Archive(root/'client-946/bin_script.wdf')
        lua=LuaRuntime(encoding='gb18030')
        shown=[]
        env=lua.table()
        env['import']=lambda name: lua.table(show=lambda _, *payload: shown.append((name,payload)))
        dispatcher=Reader(chunk(archive,0xfc0f2a11)).prototype()['children'][9]
        payload=(npc,flag,chat,*(lua.table_from(values) for values in (types,needed,held)),session)
        function(lua,dispatcher,env)(*payload)
        self.assertEqual((shown[0][0],shown[0][1][0],shown[0][1][-1]),('dlg_npc_give',npc,session))
        self.assertEqual(shown[0][1][3][1],item)
        requests=[]
        env=lua.table(panel=lua.table(session_id=session,release=lambda _:None),
                      for_caller=lua.table(s_new_give_item_panel=lambda *args:requests.append(args)))
        button=Reader(chunk(archive,0xb0203b54)).prototype()
        for index in (0,1,0,0):
            button=button['children'][index]
        function(lua,button,env)(lua.table(idx=1),None,None)
        self.assertEqual(requests,[(session,1)])
        self.assertIn(98,[mid(p) for p in self.request(3102,session+1,1)])
        self.assertIn(98,[mid(p) for p in self.request(3102,session,2)])
        with patch('native946_npc_quests.settle_reward',side_effect=ValueError('fixture settlement failure')):
            self.assertIn(98,[mid(p) for p in self.request(3102,*requests[0])])
        self.assertEqual(before['items'],self.progression.snapshot()['items'])
        self.assertEqual(self.game.summons.get(guardian)['experience'],0)
        self.assertEqual(Quests(self.game).active('school')['id'],quest['id'])
        completed=self.request(3102,*requests[0])
        self.assertTrue({1183,1973,1978}<={mid(p) for p in completed})
        experience_notice = MessageReader(next(p for p in completed if mid(p)==100)[1:]).string().decode('gb18030')
        self.assertIn('您获得#G426#n经验',experience_notice)
        self.assertIn(f'您的#G{self.game.summons.get(guardian)["name"]}#n获得#G426#n经验',experience_notice)
        self.assertIn(98,[mid(p) for p in self.request(3102,*requests[0])])
        self.assertIn(98,[mid(p) for p in self.request(1986,tid)])
        self.assertIsNone(self.game.battle)
        after=Progression(self.db,self.hero['owner'],self.hero['id']).snapshot()
        self.assertEqual(after['npc_quests'],[])
        self.assertEqual((after['experience']-before['experience'],after['gold']-before['gold']),(426,7))
        pet=self.game.summons.get(guardian)
        self.assertEqual((pet['level'],pet['experience']),(5,26))
        self.assertEqual(sum(i['amount'] for i in after['items'] if i['type']==item),1)
        with self.progression.transaction(write=False) as db:
            self.assertEqual(contribution(db,self.progression.cid),10)
            self.assertEqual(db.execute('SELECT count(*) FROM native_npc_quest_rewards').fetchone()[0],1)
            self.assertEqual(tuple(db.execute("SELECT accepted,completed,next_ring FROM native_npc_activity_progress WHERE kind='school'").fetchone()),(2,2,3))

    def test_school_supply_schema_migrates_and_locked_or_expired_tasks_cannot_spend(self):
        with self.progression.transaction() as db:
            db.execute('DROP TABLE native_school_quest_details')
        Progression(self.db,self.hero['owner'],self.hero['id'])
        quest,_=self.accept_supply()
        item=quest['supply_item']
        with self.progression.transaction() as db:
            # Older accepted tasks still need exactly one item.
            db.execute('DELETE FROM native_school_quest_details WHERE task_id=?',(quest['id'],))
            db.execute("INSERT INTO native_object_locks(character_id,kind,object_id,flag) "
                       "SELECT character_id,'item',id,1 FROM inventory WHERE character_id=? AND type=?",
                       (self.progression.cid,item))
        *_,held,session=self.open_supply()
        self.assertEqual(held,[0])
        self.assertIn(98,[mid(p) for p in self.request(3102,session,1)])
        self.visit(2034,1271)
        self.choose_op('open')
        self.request(1716,1271,item,1)
        *_,held,session=self.open_supply()
        self.assertEqual(held,[1])
        before=self.progression.snapshot()['items']
        with self.progression.transaction() as db:
            db.execute('UPDATE native_npc_quests SET expires=0 WHERE id=?',(quest['id'],))
        self.assertIn(98,[mid(p) for p in self.request(3102,session,1)])
        self.assertEqual(self.progression.snapshot()['items'],before)
        self.assertIsNone(Quests(self.game).active('school'))
        with self.progression.transaction(write=False) as db:
            self.assertEqual(contribution(db,self.progression.cid),0)
            self.assertEqual(db.execute('SELECT count(*) FROM native_npc_quest_rewards').fetchone()[0],0)

    def test_saved_last_ring_medicine_requires_one_and_rolls_into_a_new_round(self):
        quest,_=self.accept_supply(10,4005,3,100)
        item=quest['supply_item']
        self.assertIn(item,SUPPLY_TIERS[2])
        detail=self.request(1986,930000+quest['id'])[0].decode('gb18030',errors='ignore')
        self.assertIn('带1件清平丸',detail)
        *_,needed,held,session=self.open_supply()
        self.assertEqual((needed,held),([1],[0]))
        self.assertIn(98,[mid(p) for p in self.request(3102,session,1)])
        self.visit(2034,1271)
        self.choose_op('open')
        self.request(1716,1271,item,2)
        *_,needed,held,session=self.open_supply()
        self.assertEqual((needed,held),([1],[2]))
        self.assertEqual(self.progression.snapshot()['gold'],484)
        self.assertIn(1978,[mid(p) for p in self.request(3102,session,1)])
        self.assertEqual(self.progression.snapshot()['gold'],515)
        self.assertEqual(sum(i['amount'] for i in self.progression.snapshot()['items'] if i['type']==item),1)
        self.visit(2030,3101)
        with patch('native946_npc_quests.draw_objective',return_value=('supply',4002,1)):
            self.choose_op('task')
        current=Quests(self.game).active('school')
        self.assertEqual((current['ring'],current['supply_item'],current['dynamic']),(1,4002,0))

    def test_random_draw_pool_expands_at_both_level_boundaries(self):
        for level,tier in ((20,0),(49,0),(50,1),(99,1),(100,2)):
            rows=[draw_objective(level,Random(seed)) for seed in range(400)]
            self.assertEqual({r[0] for r in rows},{'visit','supply','patrol'})
            self.assertEqual({r[1] for r in rows if r[0]=='visit'},{npc for group in VISIT_TIERS[:tier+1] for npc in group})
            self.assertEqual({r[1] for r in rows if r[0]=='supply'},{item for group in SUPPLY_TIERS[:tier+1] for item in group})
            self.assertEqual({r[2] for r in rows if r[0]=='supply'},{1})
        supply = [draw_objective(20,Random(seed),4001) for seed in range(50)]
        self.assertEqual({r[1] for r in supply if r[0]=='supply'},{4000})

    def test_visit_requires_the_assigned_contact_and_preserves_other_dialogue(self):
        self.join()
        self.visit(2030,3101)
        with patch('native946_npc_quests.draw_objective',return_value=('visit',(2001,1053),1)):
            self.choose_op('task')
        row=Quests(self.game).active('school')
        self.assertEqual((row['scene'],row['npc'],row['dynamic']),(2001,1053,0))
        trace = next(r for r in self.progression.task_records()[0] if r['id'] == 930000 + row['id'])
        self.assertEqual(trace['title'], '#G【师门】#Y寻人')
        target = world().npc(2001,1053)
        self.assertEqual(trace['txt'], f'去拜访#R长安（{target["x"] // 20}，{target["y"] // 20}）#n的#u#R罗婆婆#n。')
        self.assertEqual((trace['scene'],trace['x'],trace['y']), (row['scene'],row['x'],row['y']))
        data=next(p for p in self.request(1986,930000+row['id']) if mid(p)==1971)
        self.assertIn('罗婆婆',data.decode('gb18030',errors='ignore'))
        self.visit(2001,1053)
        self.assertIn(1025,[mid(p) for p in self.choose_op('visit_other')])
        self.assertIsNotNone(Quests(self.game).active('school'))
        self.visit(2001,1053)
        self.position(2030,366,37)
        self.assertIn(98,[mid(p) for p in self.choose_op('visit')])
        self.visit(2001,1053)
        self.assertIn(1978,[mid(p) for p in self.choose_op('visit')])
        self.assertIsNone(Quests(self.game).active('school'))
        self.assertIsNone(self.game.battle)
        self.visit(2001,1053)
        self.assertFalse(any(isinstance(action,dict) and action.get('op')=='visit' for action in self.game.dialog_actions))
        with self.progression.transaction(write=False) as db:
            self.assertEqual(contribution(db,self.progression.cid),10)
            self.assertEqual(db.execute('SELECT count(*) FROM native_npc_quest_rewards').fetchone()[0],1)

    def test_original_master_and_regular_skill_catalog(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent/'analysis/town-portals-20261125'))
        from client_archive import Archive
        from literal_schema import replay
        from build_world import plain
        root = Path(__file__).parent
        archive = Archive(root/'client-946/resource/configs.wdf')
        raw = archive.read('info/info_school_skill.lua')
        table = plain(replay(raw)[0])
        self.assertEqual(SKILLS, {int(k):tuple(v[f'skill_id{i}'] for i in range(1,6)) for k,v in table.items()})
        raw = Archive(root/'client-946/resource/configs.wdf').read('info/info_hero_school_skill.lua')
        actual = plain(replay(raw)[0])
        self.assertEqual({skill: school for school, base in SKILLS.items()
                          for skill in base+EXTRA_SKILLS[school]},
                         {int(skill): row['school_id'] for skill,row in actual.items()})
        help_rows = plain(replay(archive.read('info/info_help_center.lua'))[0])
        names = {2:'方寸山',7:'东海龙宫',12:'幽冥地府',18:'蜥王殿'}
        for school, description in NATIVE_SCHOOL_DESCRIPTIONS.items():
            name = names.get(school, world().npc(*next(key for key, value in MASTERS.items() if value == school))['title'].removesuffix('门派师父'))
            row = next(row for row in help_rows.values() if row.get('name') == name)
            self.assertEqual(description,row['desc'].split('#r常规技能')[0].strip())
        for scene, npc in MASTERS:
            info = world().npc(scene, npc)
            x,y = load_map(scene).nearest_walkable(info['x']//20, info['y']//20)
            self.assertLessEqual(max(abs(x-info['x']//20),abs(y-info['y']//20)),12)

    def test_original_learning_panel_consumes_the_reply_and_sends_both_button_counts(self):
        import sys
        root = Path(__file__).parent
        sys.path[:0] = [str(root/'analysis/story-content-mining-20261126'), str(root/'analysis/cosmetics-20261126')]
        from trace_original_links import Archive, Reader, chunk
        from validate_lua import LuaRuntime, closure, function

        self.join()
        self.visit(2030,3101)
        rows, points = self.read_learn_list(self.choose_op('learn'))
        archive = Archive(root/'client-946/bin_script.wdf')
        lua = LuaRuntime(encoding='gb18030')
        shown = []
        env = lua.table()
        env['import'] = lambda name: lua.table(show=lambda _, data, total: shown.append((data[1]['skill_id'], total)))
        consumer = Reader(chunk(archive, 0x8694efd0)).prototype()['children'][46]
        function(lua,consumer,env)(lua.table_from([lua.table_from(row) for row in rows]),points)
        self.assertEqual(shown,[(11,0)])
        init = Reader(chunk(archive,0x83850ea3)).prototype()['children'][0]
        shown_cost = {}

        def label(name):
            return lua.table(set_text=lambda _, value: shown_cost.update({name:value}))

        quote = lua.table_from(rows[1])
        panel = lua.table(skill_list=lua.table_from({12:quote}),next_pote=label('potential'),
                          next_gongxian=label('contribution'),next_money=label('cash'))
        selection = lua.table(get_select_ext=lambda _:12)
        function(lua,init['children'][2],lua.table(panel=panel))(selection,None,None)
        self.assertEqual(shown_cost,{'potential':0,'contribution':2,'cash':10})
        requests = []
        env = lua.table(panel=lua.table(skill=lua.table(get_select_ext=lambda _: 11)),
                        for_caller=lua.table(s_learn_skill=lambda *args: requests.append(args)))
        for index, count in ((3,1),(4,10)):
            closure(lua,init['children'][index],env,[count])(None,None,None)
        self.assertEqual(requests,[(11,1),(11,10)])

    def test_master_uses_original_preview_cap_for_each_cultivation_stage(self):
        import sys
        root = Path(__file__).parent
        sys.path[:0] = [str(root/'analysis/story-content-mining-20261126'), str(root/'analysis/cosmetics-20261126')]
        from trace_original_links import Archive, Reader, chunk
        from validate_lua import LuaRuntime, closure

        archive = Archive(root/'client-946/bin_script.wdf')
        preview = Reader(chunk(archive,0x7b1cf8c1)).prototype()['children'][1]['children'][5]
        lua = LuaRuntime(encoding='gb18030')
        env = lua.table(safetonumber=int, string=lua.globals().string,
                        MIN_GRADE_DICT=lua.table_from({0:0,1:0,2:0}))
        set_max_practice = closure(lua, preview, env, [0,999])

        def field(value):
            return lua.table(get_text=lambda _:str(value),set_text=lambda _,v:None)

        self.join()
        for rebirth, ascension, sanctification, cap in (
                (0,0,0,10000), (1,0,0,15000), (2,0,0,20000), (2,1,0,25000), (2,1,1,25000)):
            with self.subTest(rebirth=rebirth,ascension=ascension,sanctification=sanctification):
                panel = lua.table(rei=field(rebirth),fly=field(ascension),grade=field(120))
                maxima = []
                for i in range(1,6):
                    panel[f'practice{i}'] = field(0)
                    panel[f'practice{i}']['is_valid'] = lambda _: True
                    panel[f'practice{i}']['set_max_value'] = lambda _,n: maxima.append(n)
                set_max_practice(panel)
                self.assertEqual(maxima,[cap]*5)
                with self.progression.transaction() as db:
                    db.execute('UPDATE cultivation_state SET rebirth=?,ascension=?,sanctification=? WHERE character_id=?',
                               (rebirth,ascension,sanctification,self.progression.cid))
                self.visit(2030,3101)
                rows, _ = self.read_learn_list(self.choose_op('learn'))
                self.assertTrue(rows)
                self.assertEqual({row['max_value'] for row in rows},{cap})

    def test_join_native_school_and_learn_with_single_and_batch_buttons(self):
        self.join()
        self.assertIn(98, [mid(p) for p in self.request(749,11,1)])
        self.visit(2030,3101)
        rows, points = self.read_learn_list(self.choose_op('learn'))
        self.assertEqual([r['skill_id'] for r in rows], [11,12])
        self.assertEqual([(r['next_money'],r['next_contribute'],r['next_pote']) for r in rows],[(5,0,0),(10,2,0)])
        self.assertEqual(points, 0)
        for skill, times, extra in ((12,1,b''), (13,1,b''), (21,1,b''), (11,1,number(7)), (11,2,b'')):
            self.assertIn(98,[mid(p) for p in self.request(749,skill,times,suffix=extra)])
        self.assertEqual(self.progression.snapshot()['gold'],500)
        self.assertIn(699,[mid(p) for p in self.request(749,11,1)])
        rows, _ = self.read_learn_list(self.request(749,11,10))
        self.assertEqual(rows[0]['cur_value'],11)
        with self.progression.transaction() as db:
            db.execute('UPDATE learned_skills SET rank=9996 WHERE character_id=? AND skill_id=11',
                       (self.progression.cid,))
        rows, _ = self.read_learn_list(self.request(749,11,10))
        self.assertEqual((rows[0]['cur_value'],rows[0]['max_value']),(10000,10000))
        self.assertIn(98,[mid(p) for p in self.request(749,11,1)])
        restored = Progression(self.db,self.hero['owner'],self.hero['id']).snapshot()
        self.assertEqual((restored['skills'][11],restored['gold']),(10000,425))
        self.visit(2080,2709)
        self.assertFalse(any(isinstance(a,dict) and a.get('op') in ('join','task') for a in self.game.dialog_actions))
        rows,_=self.read_learn_list(self.choose_op('learn'))
        self.assertEqual([r['skill_id'] for r in rows],[21,22])
        self.assertIn(98,[mid(p) for p in self.request(749,11,1)])

    def test_learning_rejection_uses_native_system_chat_instead_of_login_popup(self):
        import sys
        root = Path(__file__).parent
        sys.path[:0] = [str(root/'analysis/story-content-mining-20261126'),str(root/'analysis/cosmetics-20261126')]
        from trace_original_links import Archive, Reader, chunk
        from validate_lua import LuaRuntime, closure

        self.join()
        self.visit(2030,3101)
        self.choose_op('learn')
        before = self.progression.snapshot()
        for _ in range(2):
            replies = self.request(749,12,1)
            self.assertNotIn(888,[mid(p) for p in replies])
            notices = [p for p in replies if mid(p)==98]
            matching = [p for p in notices if MessageReader(p[1:]).string().decode('gb18030')=='金钱或师门贡献不足。']
            self.assertEqual(len(matching),1)
            reader = MessageReader(matching[0][1:])
            self.assertEqual(reader.string().decode('gb18030'),'金钱或师门贡献不足。')
            self.assertEqual(reader.string(),b'')
            self.assertEqual((reader.integer(),reader.integer()),(1,0))
            reader.finish()
        after = self.progression.snapshot()
        self.assertEqual((after['gold'],after['skills']),(before['gold'],before['skills']))

        archive = Archive(root/'client-946/bin_script.wdf')
        dispatcher = Reader(chunk(archive,0xfc0f2a11)).prototype()
        lua = LuaRuntime(encoding='gb18030')
        shown = []
        env = lua.table()
        env['import'] = lambda name: lua.table(add_chat=lambda *args:shown.append(args))
        consumer = closure(lua,dispatcher['children'][14],env,
                           [lua.table(SYSTEM_CHANNEL=6),lua.table(clone=lambda value:value)])
        consumer('金钱或师门贡献不足。','',1,0)
        self.assertEqual(len(shown),1)
        message = shown[0][0]
        self.assertEqual((message['channel'],message['content'],message['bg_index']),(6,'金钱或师门贡献不足。',0))

    def test_saved_proficiency_over_current_cap_cannot_be_charged_or_reduced(self):
        from native946_school import add_contribution

        self.join()
        with self.progression.transaction() as db:
            db.execute('INSERT INTO learned_skills VALUES(?,12,15000)',(self.progression.cid,))
            add_contribution(db,self.progression.cid,20)
        self.visit(2030,3101)
        rows, points = self.read_learn_list(self.choose_op('learn'))
        self.assertEqual((rows[1]['cur_value'],rows[1]['max_value'],points),(15000,10000,20))
        for times in (1,10):
            self.assertIn(98,[mid(p) for p in self.request(749,12,times)])
        saved = self.progression.snapshot()
        self.assertEqual((saved['skills'][12],saved['gold']),(15000,500))
        with self.progression.transaction(write=False) as db:
            self.assertEqual(contribution(db,self.progression.cid),20)

    def test_near_cap_batch_charges_both_balances_for_the_actual_gain(self):
        from native946_school import add_contribution

        self.join()
        with self.progression.transaction() as db:
            db.execute('INSERT INTO learned_skills VALUES(?,12,9997)',(self.progression.cid,))
            add_contribution(db,self.progression.cid,5)
        self.visit(2030,3101)
        self.choose_op('learn')
        self.assertIn(98,[mid(p) for p in self.request(749,12,10)])
        saved = self.progression.snapshot()
        self.assertEqual((saved['skills'][12],saved['gold']),(9997,500))
        with self.progression.transaction() as db:
            self.assertEqual(contribution(db,self.progression.cid),5)
            add_contribution(db,self.progression.cid,1)
        rows, points = self.read_learn_list(self.request(749,12,10))
        self.assertEqual((rows[1]['cur_value'],points),(10000,0))
        self.assertEqual(self.progression.snapshot()['gold'],470)

    def test_unaffiliated_legacy_witch_joins_at_lizard_instructor(self):
        self.hero = self.characters.create('school_fixture','蜥王殿拜师测试',702)
        self.game = NativeGameplay(self.hero,self.characters,lambda:())
        self.progression = self.game.progression
        with self.progression.transaction() as db:
            db.execute("DELETE FROM native_role_system_state WHERE character_id=? AND system='school'",(self.progression.cid,))
            db.execute('UPDATE characters SET level=20 WHERE id=?',(self.progression.cid,))
        self.assertEqual(self.progression.snapshot()['school'],0)

        reply = self.visit(2361,6521)
        reader = MessageReader(next(p for p in reply if mid(p)==1025)[3:])
        reader.take(1)
        text = reader.string().decode('gb18030')
        self.assertIn('石羿',text)
        self.assertEqual(reader.integer(),6521)
        self.assertFalse(any(a.get('op') in ('join','learn','talent','task') for a in self.game.dialog_actions))
        route = self.choose_op('master')
        waypoint = MessageReader(next(p for p in route if mid(p)==2966)[3:])
        waypoint.integer();waypoint.integer()
        self.assertEqual((waypoint.integer(),waypoint.integer()),(2361,6311))

        reply = self.visit(2361,6311)
        reader = MessageReader(next(p for p in reply if mid(p)==1025)[3:])
        reader.take(1)
        text = reader.string().decode('gb18030')
        self.assertEqual(text,'蜥王殿传功师父 石羿')
        self.assertEqual(reader.integer(),6311)
        self.assertNotIn('当前师门贡献',text)
        self.assertEqual({a['op'] for a in self.game.dialog_actions},{'join'})
        self.assertIn(5019,[mid(p) for p in self.choose_op('join')])
        self.assertEqual(self.progression.snapshot()['school'],18)
        self.assertEqual(Progression(self.db,self.hero['owner'],self.hero['id']).snapshot()['school'],18)

        self.visit(2361,6311)
        self.assertEqual([a['op'] for a in self.game.dialog_actions],['task_menu','learn','talent','nature',
            'meditation','practice_exchange','donate','donate_craft','leave'])
        self.choose_op('task_menu')
        self.assertIn('task',{a['op'] for a in self.game.dialog_actions})
        self.visit(2361,6521)
        self.assertEqual({a['op'] for a in self.game.dialog_actions},{'master','talent'})
        self.assertIn(5019,[mid(p) for p in self.choose_op('talent')])

    def test_lizard_disciples_see_wu_hui_dialogue_and_can_learn(self):
        self.hero = self.characters.create('school_fixture', '跨门派对话测试', 702)
        self.game = NativeGameplay(self.hero, self.characters, lambda: ())
        self.progression = self.game.progression
        with self.progression.transaction() as db:
            db.execute('UPDATE characters SET level=42 WHERE id=?', (self.progression.cid,))
        self.visit(2361, 6311)
        self.choose_op('join')
        self.assertEqual(self.progression.snapshot()['school'], 18)

        reply = self.visit(2360, 6245)
        reader = MessageReader(next(p for p in reply if mid(p) == 1025)[3:])
        self.assertEqual(reader.take(1)[0], 1)
        self.assertEqual(reader.string().decode('gb18030'), '想知道，飞的滋味吗？')
        self.assertEqual(reader.integer(), 6245)
        labels = [reader.string().decode('gb18030') for _ in range(reader.take(1)[0])]
        self.assertEqual(labels, ['技能学习', '离开'])
        self.assertEqual(reader.integer(), 6245)
        reader.integer()
        reader.finish()
        rows, _ = self.read_learn_list(self.choose_op('learn'))
        self.assertTrue(rows)

        self.visit(2360, 6245)
        self.assertIn(1183, [mid(p) for p in self.choose_op('leave')])
        self.assertFalse(self.game.dialog_open)
        self.assertEqual(self.progression.snapshot()['school'], 18)

    def test_unaffiliated_witches_use_their_actual_cloud_palace_masters(self):
        for role,name,school,master,other in ((711,'流光测试',15,6931,6932),(702,'东岚测试',16,6932,6931)):
            with self.subTest(role=role):
                self.hero=self.characters.create('school_fixture',name,role)
                self.game=NativeGameplay(self.hero,self.characters,lambda:())
                self.progression=self.game.progression
                self.assertEqual(self.progression.snapshot()['school'],0)
                self.request(5022,1,0)
                route=self.choose(lambda a:a.get('school')==school)
                waypoint=MessageReader(next(p for p in route if mid(p)==2966)[3:])
                waypoint.integer();waypoint.integer()
                self.assertEqual((waypoint.integer(),waypoint.integer()),(2408,master))
                self.assertEqual(self.progression.snapshot()['school'],0)
                self.visit(2408,other)
                self.assertFalse(any(a.get('op')=='join' for a in self.game.dialog_actions))
                self.visit(2408,master)
                self.assertIn(5019,[mid(p) for p in self.choose_op('join')])
                self.assertEqual(self.progression.snapshot()['school'],school)
                self.visit(2408,master)
                self.assertIn(5019,[mid(p) for p in self.choose_op('talent')])
                self.visit(2408,master)
                learned,_=self.read_learn_list(self.choose_op('learn'))
                self.assertEqual(learned[0]['skill_id'],SKILLS[school][0])

    def test_west_admission_requires_ascension_and_lists_incompatible_learned_skills(self):
        self.join()
        self.assertIn(1314, [mid(p) for p in self.request(1483, 17, suffix=b'\x01')])
        self.visit(2451,8187)
        self.assertFalse(any(a.get('op')=='west_offer' for a in self.game.dialog_actions))
        with self.progression.transaction() as db:
            db.execute('UPDATE characters SET level=140 WHERE id=?',(self.hero['id'],))
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1 WHERE character_id=?',(self.hero['id'],))
            db.executemany(
                'INSERT INTO learned_skills VALUES(?,?,?) ON CONFLICT(character_id,skill_id) DO UPDATE SET rank=excluded.rank',
                [(self.hero['id'],11,2),(self.hero['id'],21,3),(self.hero['id'],16,1)])
            db.execute('INSERT INTO skill_shortcuts VALUES(?,2,21)',(self.hero['id'],))
        self.visit(2451,8187)
        confirmation=next(p for p in self.choose_op('west_offer') if mid(p)==1025).decode('gb18030',errors='ignore')
        self.assertIn('唧唧歪歪',confirmation)
        self.assertIn('如沐春风',confirmation)
        self.assertEqual(self.progression.snapshot()['school'],2)
        response=self.choose_op('west_join')
        panel=MessageReader(next(p for p in response if mid(p)==5019)[3:])
        self.assertEqual(panel.integer(),119)
        cold=Progression(self.db,self.hero['owner'],self.hero['id']).snapshot()
        self.assertEqual(cold['school'],119)
        self.assertEqual(cold['skills'][11],2)
        self.assertTrue({16,21}.isdisjoint(cold['skills']))
        self.assertNotIn(1,cold['shortcuts'])
        self.assertNotIn(2,cold['shortcuts'])
        self.visit(2451,8187)
        self.assertFalse(any(a.get('op')=='west_offer' for a in self.game.dialog_actions))
        self.assertIn(701,[mid(p) for p in self.choose_op('learn')])
        self.assertIn(699,[mid(p) for p in self.request(749,1701,1)])
        self.visit(2080,2709)
        self.assertFalse(any(a.get('op')=='learn' for a in self.game.dialog_actions))

    def test_master_talent_page_and_upgrade_share_actual_grade_and_balances(self):
        from native946_role_growth import cfg, talents
        from native946_role_systems import save

        self.join()
        self.visit(2030,3101)
        self.assertIn(5019,[mid(p) for p in self.choose_op('talent')])
        self.assertIn(98,[mid(p) for p in self.request(5022,4,1)])
        price=cfg()['UpgradeConfig'][0][0]
        with self.progression.transaction() as db:
            db.execute('UPDATE characters SET level=100,gold=0,experience=? WHERE id=?',(price['NeedExp'],self.hero['id']))
            db.execute('UPDATE cultivation_state SET tongbao=? WHERE character_id=?',(price['NeedFund'],self.hero['id']))
            save(db,self.hero['id'],'talent',dict(levels={},switches=0,zhenqi=price['NeedZhenQi']))
        self.assertIn(5021,[mid(p) for p in self.request(5022,2,1)])
        self.assertTrue({5019,5021}<={mid(p) for p in self.request(5022,4,1)})
        self.assertIn(98,[mid(p) for p in self.request(5022,4,2)])
        self.assertEqual(self.progression.snapshot()['experience'],0)
        with self.progression.transaction(write=False) as db:
            self.assertEqual(talents(db,self.hero['id'])['levels'],{'1':1})
            self.assertEqual(talents(db,self.hero['id'])['zhenqi'],0)

    def test_native_world_map_school_flight_uses_the_saved_main_school(self):
        import sys
        root=Path(__file__).parent
        sys.path[:0]=[str(root/'analysis/story-content-mining-20261126'),str(root/'analysis/cosmetics-20261126')]
        from trace_original_links import Archive, Reader, chunk
        from validate_lua import LuaRuntime, function

        reply=MessageReader(next(p for p in self.request(1767) if mid(p)==1763)[3:])
        self.assertEqual(reply.take(1),b'\0')
        self.assertEqual(reply.integer(),1);reply.finish()
        self.assertIn(98,[mid(p) for p in self.request(1765,2)])
        self.join()
        self.visit(2001,1139)
        reply=MessageReader(next(p for p in self.request(1767) if mid(p)==1763)[3:])
        destinations=[reply.integer() for _ in range(reply.take(1)[0])]
        tag=reply.integer();reply.finish()
        self.assertEqual((destinations,tag),([2],1))
        archive=Archive(root/'client-946/bin_script.wdf')
        lua=LuaRuntime(encoding='gb18030')
        shown=[]
        module=lua.table(panel=lua.table(is_valid=lambda _:True,get_show=lambda _:True,
                                        set_hero_fly_stunt=lambda _,data:shown.extend(data.values())))
        env=lua.table()
        env['import']=lambda name:module
        consumer=Reader(chunk(archive,0x80356a68)).prototype()['children'][60]
        function(lua,consumer,env)(lua.table_from(destinations),tag)
        self.assertEqual(shown,[2])
        dispatched=[]
        env=lua.table(for_caller=lua.table(s_fly_goto_school=dispatched.append))
        button=Reader(chunk(archive,0x8fd2b8ca)).prototype()['children'][0]['children'][8]['children'][0]
        function(lua,button,env)(lua.table(school_id=shown[0]),None,None)
        self.assertEqual(dispatched,[2])
        self.assertIn(98,[mid(p) for p in self.request(1765,4)])
        self.assertIn(98,[mid(p) for p in self.request(1765,2,suffix=number(4))])
        self.assertEqual(self.hero['map_id'],2001)
        self.request(1765,*dispatched)
        self.assertEqual(self.hero['map_id'],2030)
        target=world().npc(2030,3101)
        self.assertEqual((self.hero['x'],self.hero['y']),load_map(2030).nearest_walkable(target['x']//20,target['y']//20))
        self.assertEqual(Progression(self.db,self.hero['owner'],self.hero['id']).snapshot()['map_id'],2030)

    def test_buzhou_flight_skill_appears_on_admission_and_reopens_from_saved_school(self):
        self.hero = self.characters.create('school_fixture', '不周山飞行测试', 702)
        self.game = NativeGameplay(self.hero, self.characters, lambda: ())
        self.progression = self.game.progression

        def flight_rows(packets):
            raw = next(p for p in packets if mid(p) == 699)
            reader = MessageReader(raw[3:])
            school = reader.integer()
            rows = [{name: reader.integer() for name, _ in SCHEMAS['fly_stunt_info']}
                    for _ in range(reader.take(1)[0])]
            return school, rows

        self.assertEqual(flight_rows(self.request(742, prefix=b'\x01')), (0, []))
        self.assertIn(98, [mid(p) for p in self.request(1485, 6138)])
        self.visit(2360, 6245)
        expected = (17, [dict(level=0, cost=0, skill_id=6138)])
        self.assertEqual(flight_rows(self.choose_op('join')), expected)
        self.assertEqual(flight_rows(self.request(742, prefix=b'\x01')), expected)
        self.assertIn(98, [mid(p) for p in self.request(1483, 6135, suffix=b'\x01')])
        self.assertIn(1314, [mid(p) for p in self.request(1483, 6138, suffix=b'\x01')])
        cold = Progression(self.db, self.hero['owner'], self.hero['id'])
        self.assertEqual(flight_rows(cold.skill_packets()), expected)
        self.assertNotIn(6138, cold.snapshot()['skills'])
        self.assertEqual(cold.snapshot()['shortcuts'][1], 6138)

        self.visit(2001, 1139)
        self.assertIn(98, [mid(p) for p in self.request(1485, 6135)])
        raw = next(p for p in self.request(1485, 6138) if mid(p)==1763)
        reply = MessageReader(raw[3:])
        self.assertEqual((reply.take(1),reply.integer(),reply.integer()),(b'\x01',17,1))
        reply.finish()
        self.assertIn(98, [mid(p) for p in self.request(1765, 18)])
        self.assertEqual(flight_rows(self.request(1765, 17)), expected)
        reloaded = Progression(self.db, self.hero['owner'], self.hero['id'])
        self.assertEqual(reloaded.snapshot()['map_id'], 2360)

    def test_school_battle_credits_contribution_and_cannot_settle_again(self):
        self.join()
        self.visit(2030,3101)
        with patch('native946_npc_quests.draw_objective',return_value=('patrol',None,1)):
            reply=self.choose_op('task')
        self.assertIn(1978,[mid(p) for p in reply])
        quest=Quests(self.game).active('school')
        self.assertIn('师门任务',next(p for p in reply if mid(p)==1973).decode('gb18030',errors='ignore'))
        trace = next(r for r in self.progression.task_records()[0] if r['id'] == 930000 + quest['id'])
        self.assertEqual((trace['title'], trace['txt']),
                         ('#G【师门】#Y巡逻', '在这附近来回走动，可遇到前来偷窃的宵小之辈。'))
        self.assertEqual((quest['branch'],quest['ring']),(2,1))
        self.assertEqual((quest['school_objective'],quest['scene']),('patrol',2030))
        self.position(quest['scene'],quest['x'],quest['y'])
        self.request(1034,quest['npc'])
        self.assertIsNone(self.game.battle)
        x,y=quest['x'],quest['y']
        direction=next(code for code,(dx,dy) in DIRECTIONS.items() if self.game.grid.walkable(x+dx,y+dy))
        battles = []
        for win in (1, 2):
            self.position(quest['scene'],x,y)
            self.request(1643,prefix=number(4)+pack('<HH',x,y)+string_field(bytes([direction])))
            battle=self.game._wild_battle
            battles.append(battle)
            for _ in range(30):
                if battle.state['phase']=='finished':
                    break
                if battle.state['phase']=='input':
                    self.request(2177,3,battle.enemies()[0]['id'])
                else:
                    self.request(2184)
            self.assertTrue(battle.state['victory'])
            if win == 1:
                ongoing = Quests(self.game).active('school')
                self.assertEqual((ongoing['id'],ongoing['patrol_first_battle']),(quest['id'],battle.id))
                cold = Progression(self.db,self.hero['owner'],self.progression.cid).snapshot()['npc_quests']
                self.assertEqual(cold[0]['patrol_first_battle'],battle.id)
                with self.progression.transaction() as db:
                    battle.state['settled'] = False
                    battle.settle(db,True)
                    self.assertEqual(contribution(db,self.progression.cid),0)
                    self.assertEqual(db.execute('SELECT count(*) FROM native_npc_quest_rewards').fetchone()[0],0)
        self.assertNotEqual(battles[0].id,battles[1].id)
        self.assertIsNone(Quests(self.game).active('school'))
        with self.progression.transaction() as db:
            battle.settle(db,True)
            self.assertEqual(contribution(db,self.progression.cid),10)
            self.assertEqual(db.execute('SELECT count(*) FROM native_npc_quest_rewards').fetchone()[0],1)
        self.visit(2030,3101)
        _, points=self.read_learn_list(self.choose_op('learn'))
        self.assertEqual(points,10)
        rows, points=self.read_learn_list(self.request(749,12,1))
        self.assertEqual((rows[1]['cur_value'],points),(1,8))
        self.assertIn(12,{row['skill_id'] for row in self.progression.combat_skills()})
        with self.progression.transaction() as db:
            db.execute("UPDATE native_npc_activity_progress SET accepted=20 WHERE character_id=? AND kind='school'",
                       (self.progression.cid,))
        self.visit(2030,3101)
        self.assertFalse(any(isinstance(a,dict) and a.get('op')=='task' for a in self.game.dialog_actions))

    def test_auto_school_uses_master_menu_and_waits_for_return_to_issue_next(self):
        self.join()
        self.visit(2030,3101)
        opening = self.choose_op('auto_start')
        self.assertIn(100, [mid(p) for p in opening])
        with patch('native946_npc_quests.draw_objective', return_value=('visit',(2001,1053),1)):
            self.assertIn(1978, [mid(p) for p in self.game.tick()])
            first = Quests(self.game).active('school')
            self.position(first['scene'],first['x'],first['y'])
            self.game._auto_school['next'] = 0
            self.assertIn(1025, [mid(p) for p in self.game.tick()])
            self.assertEqual(self.game.dialog_npc,1053)
            self.game._auto_school['next'] = 0
            finished = self.game.tick()
            self.assertTrue(any('#G飞行技能#Y' in p.decode('gb18030',errors='ignore')
                                for p in finished if mid(p)==100))
            self.assertIsNone(Quests(self.game).active('school'))
            self.game._auto_school['next'] = 0
            self.assertIn(1183, [mid(p) for p in self.game.tick()])
            self.assertEqual(self.game._last_transition[-1],'school-flight')
            self.assertEqual(self.hero['map_id'],2030)
            self.assertIsNone(Quests(self.game).active('school'))
            master = world().npc(2030,3101)
            self.assertEqual((self.hero['x'],self.hero['y']),load_map(2030).nearest_walkable(master['x']//20,master['y']//20))
            self.game._auto_school['next'] = 0
            self.assertIn(1978, [mid(p) for p in self.game.tick()])
        second = Quests(self.game).active('school')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(second['ring'],2)
        self.visit(2030,3101)
        self.choose_op('auto_stop')
        self.assertIsNone(self.game._auto_school)
        self.assertEqual(Quests(self.game).active('school')['id'],second['id'])

    def test_auto_school_buys_required_medicine_and_delivers_once(self):
        task,_ = self.accept_supply()
        cid = self.progression.cid
        with self.progression.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=? AND type=?',(cid,task['supply_item']))
        self.visit(2030,3101)
        self.choose_op('auto_start')
        route = next(p for p in self.game.tick() if mid(p)==2966)
        shop = MessageReader(route[3:])
        shop.integer();shop.integer()
        self.assertEqual((shop.integer(),shop.integer()),(2034,1271))
        # Native auto-trace may itself open the target NPC's ordinary dialog.
        self.visit(2034,1271)
        self.assertEqual([a['kind'] for a in self.game.dialog_actions],['medicine'])
        self.game._auto_school['next'] = 0
        self.assertIn(100,[mid(p) for p in self.game.tick()])
        with self.progression.transaction(write=False) as db:
            self.assertEqual(tuple(db.execute('SELECT offer,amount,cost FROM native_shop_orders').fetchone()),
                             (task['supply_item'],1,1))
        self.game._auto_school['next'] = 0
        self.assertIn(1183,[mid(p) for p in self.game.tick()])
        self.assertEqual(self.game._last_transition[-1],'school-flight')
        self.assertEqual((self.hero['map_id'],self.hero['x'],self.hero['y']),(task['scene'],task['x'],task['y']))
        self.game._auto_school['next'] = 0
        self.assertTrue({1183,100,1978}<={mid(p) for p in self.game.tick()})
        self.assertIsNone(Quests(self.game).active('school'))
        self.game._auto_school['next'] = 0
        self.game.tick()
        with self.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_shop_orders').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM native_npc_quest_rewards').fetchone()[0],1)

    def test_auto_patrol_relocates_saved_target_and_opens_native_battle_input(self):
        from native946_npc_quests import school_patrol_cells

        self.join()
        self.visit(2030,3101)
        with patch('native946_npc_quests.draw_objective',return_value=('patrol',None,1)):
            self.choose_op('task')
        original = Quests(self.game).active('school')
        with self.progression.transaction() as db:
            db.execute('UPDATE native_npc_quests SET scene=2361,x=91,y=120 WHERE id=?',(original['id'],))
        self.visit(2030,3101)
        self.choose_op('auto_start')
        replies = self.game.tick()
        task = Quests(self.game).active('school')
        self.assertEqual((task['id'],task['scene']),(original['id'],2030))
        self.assertIn((task['x'],task['y']),school_patrol_cells(2030,3101))
        self.assertIn(1978,[mid(p) for p in replies])
        if not self.game.battle:
            self.position(task['scene'],task['x'],task['y'])
            self.game._auto_school['next'] = 0
            replies = self.game.tick()
        entry = next(p for p in replies if mid(p)==2171)
        self.assertEqual(MessageReader(entry[3:]).integer(),0)
        self.assertTrue({2135,2151,2139}<={mid(p) for p in replies})
        self.assertIn(3817,[mid(p) for p in self.request(2178,1)])

    def test_twentieth_school_task_stops_auto_unlocks_native_achievement_and_activity_guide(self):
        from datetime import timedelta
        from native946_liveness import balances, daily_claims, day_start, score
        from native946_school_daily import ACHIEVEMENT_ID, LIVENESS_GUIDE_ID

        self.join()
        cid = self.progression.cid
        with patch('native946_npc_quests.draw_objective', return_value=('visit',(2001,1053),1)):
            for _ in range(19):
                self.visit(2030,3101)
                self.choose_op('task')
                self.visit(2001,1053)
                self.choose_op('visit')
            self.assertIn(98, [mid(p) for p in self.request(1400,1,suffix=b'\x01\x01')])
            with self.progression.transaction() as db:
                db.execute('INSERT OR IGNORE INTO learned_skills VALUES(?,?,0)', (cid,13))
            self.visit(2030,3101)
            self.choose_op('auto_start')
            self.game.tick()
        self.visit(2001,1053)
        replies = self.choose_op('visit')
        self.assertTrue({5,7,100,1978}.issubset({mid(p) for p in replies}))
        self.assertIsNone(self.game._auto_school)
        guide = next(r for r in self.progression.task_records()[0] if r['id'] == LIVENESS_GUIDE_ID)
        self.assertEqual(guide['title'], '#G【指引】#Y初入江湖[主线]')
        self.assertEqual(guide['txt'], '打开#G任务->今日活跃界面#n，达成活跃目标，领取一次活跃度奖励。')
        self.assertIn(1259, [mid(p) for p in self.request(1986,LIVENESS_GUIDE_ID)])
        before = self.progression.snapshot()['skills'][13]
        self.assertIn(6, [mid(p) for p in self.request(3482,ACHIEVEMENT_ID)])
        self.assertEqual(self.progression.snapshot()['skills'][13], before+300)
        self.request(3482,ACHIEVEMENT_ID)
        self.assertEqual(self.progression.snapshot()['skills'][13], before+300)
        day = day_start()
        with self.progression.transaction(write=False) as db:
            self.assertEqual(score(db,cid,day),{'school':60})
        panel = next(p for p in self.request(1450,suffix=b'\x01') if mid(p)==1233)
        reader = MessageReader(panel[3:])
        self.assertEqual((reader.integer(),reader.integer(),reader.take(1)[0]),(60,0,4))
        reader.string()
        self.assertEqual((reader.integer(),reader.take(2)),(60,b'\x00\x01'))
        claimed = self.request(1400,1,suffix=b'\x01\x01')
        self.assertTrue({695,1233,1978,100}.issubset({mid(p) for p in claimed}))
        self.assertIn(98, [mid(p) for p in self.request(1400,1,suffix=b'\x01\x01')])
        cold = Progression(self.db,self.hero['owner'],cid)
        self.assertEqual(cold.snapshot()['energy'],120)
        self.assertTrue(cold.snapshot()['school_milestone'])
        self.assertNotIn(LIVENESS_GUIDE_ID,cold.task_records()[1]['升级'])
        with cold.transaction(write=False) as db:
            balance = balances(db,cid)
            self.assertEqual({key:balance[key] for key in ('points','rest','escort','bounty')},
                             dict(points=5,rest=1,escort=5,bounty=2))
            tomorrow = day + timedelta(days=1)
            self.assertEqual(daily_claims(balance,tomorrow),[])
            self.assertEqual(score(db,cid,tomorrow),{})
