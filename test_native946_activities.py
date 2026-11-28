"""Native activity metadata, calendar dispatch and unimplemented-flow limits."""
from pathlib import Path
from datetime import date, datetime
from unittest.mock import patch
import json,sys,unittest

from native946_activities import (
    ACTIVITY_TIMEZONE, Activities, RECORDS, CALLBACK, CALENDAR, RULES, EXTRA_NAMES,
    calendar_records, rule_pages, guide_pages,
)
from native946_codec import packet
from native946_game_login import message_id
from native946_login import MessageReader
from native946_npc_quests import day_key
from native946_party import packets
import test_native946_tasks as task_fixtures


class ActivityCatalogTests(unittest.TestCase):
    def test_calendar_rotation_matches_current_original_selector(self):
        sys.path.insert(0,str(Path(__file__).parent/'analysis/calendar-activities-20261126'))
        from replay_calendar import selector
        query=selector(False)
        for day in (date(2026,11,1),date(2026,11,7),date(2026,11,14),date(2026,11,26),date(2027,1,1)):
            self.assertEqual(CALENDAR['days'][day.isoformat()],query(day))
        self.assertEqual(len({r['name'].strip() for rows in CALENDAR['days'].values() for r in rows}),26)
        for day in CALENDAR['days']:
            for source,row in calendar_records(date.fromisoformat(day)):
                self.assertIsNotNone(row);self.assertEqual(source['name'].strip(),row['name'])

    def test_dedicated_conditions_and_stage_rewards_preserve_current_archive(self):
        sys.path.insert(0,str(Path(__file__).parent/'analysis/calendar-activities-20261126'))
        from build_rules import plain
        from client_archive import Archive
        from literal_schema import replay
        archive=Archive(Path(__file__).parent/'client-946/resource/configs.wdf')
        for name,label in RULES['special_labels'].items():
            exports={};replay(archive.read('info/'+label.lower()+'.lua'),exports)
            self.assertEqual(RULES['special'][name],{k:plain(v) for k,v in exports.items()})
        stages=RULES['special']['宝象国降妖']['StageRewardConfig']
        self.assertEqual(stages[0],dict(RoundRequire=1,Reward=[[46988,2,0],[47101,10,0]]))
        row=next(r for r in RECORDS.values() if r['name']=='宝象国降妖')
        pages='#r'.join(rule_pages(row))
        self.assertIn('任务提示表仅列周一',pages);self.assertIn('周四',pages)
        self.assertIn('凝气天书×2',pages);self.assertIn('地载灵宝×1',pages)

    def test_original_stage_connections_and_monster_descriptions_are_reachable(self):
        row=next(r for r in RECORDS.values() if r['name']=='天蓬问情')
        pages=guide_pages(row);source=RULES['special']['天蓬问情']
        self.assertEqual(len(pages),6+len(source['MonsterConfig']))
        self.assertIn('初至时间隧道；后续：第一次穿越',pages[0])
        for monster,page in zip(source['MonsterConfig'],pages[6:]):
            self.assertIn(monster['Name'],page);self.assertIn(monster['Desc'],page)
        row=next(r for r in RECORDS.values() if r['name']=='九重幻境')
        self.assertIn('第9层：首领积分100',guide_pages(row)[1])
        self.assertIn('200积分',rule_pages(row)[-1])

    def test_current_native_rows_and_original_rules_are_preserved(self):
        sys.path.insert(0,str(Path(__file__).parent/'analysis/town-portals-20261125'))
        from client_archive import Archive
        from literal_schema import replay
        from build_world import plain
        native=plain(replay(Archive(Path(__file__).parent/'client-946/resource/configs.wdf').read('info/info_task_tips.lua'))[0])
        self.assertEqual(len(RECORDS),len(native))
        for index,source in enumerate(native,1):
            for key,value in source.items():
                self.assertEqual(RECORDS[index]['original_name'] if key=='name' else RECORDS[index][key],value)

    def test_only_verified_existing_daily_flows_are_enabled(self):
        actual={r['reference_kind'] for r in RECORDS.values() if r['reference_kind']}
        self.assertEqual(actual,{'ghost','king','heaven','sanjie','lotus'})
        for row in RECORDS.values():
            if row['name'] in ('拯救牛魔王','天地劫','群星会','御前武举','一字千金','会猎狮驼'):
                self.assertIsNone(row['reference_kind']);self.assertIn('活动领取与阶段脚本',row['missing'])

    def test_npc_locators_match_published_original_world(self):
        from native946_world import world
        for row in RECORDS.values():
            for loc in row['locations']:
                npc=world().npc(loc['scene'],loc['npc'])
                self.assertIsNotNone(npc)
                self.assertEqual((npc['x']//20,npc['y']//20),(loc['x'],loc['y']))
                self.assertEqual(npc.get('name') or str(loc['npc']),loc['name'])

    def test_new_activity_navigation_uses_the_givers(self):
        from native946_activities import navigation_targets

        for rid,giver in ((11,(2221,4544)),(74,(2001,7510))):
            targets=navigation_targets(RECORDS[rid])
            self.assertEqual([(n['scene'],n['npc']) for n in targets],[giver])


class ActivityDispatchTests(unittest.TestCase):
    def setUp(self):
        self.f=task_fixtures.TaskJournalTests()
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.g=self.f.g;self.p=self.f.p;self.login=self.f.login;self.hub=Activities(self.g)

    def choose(self,index,callback=CALLBACK):
        return self.login.receive(packet(1032,['byte','number'],[index,callback]))

    def choose_action(self, op, callback=CALLBACK):
        index=next(i for i,a in enumerate(self.g.dialog_actions,1) if a['op']==op)
        return self.choose(index,callback)

    def menu_text(self, reply):
        raw=next(p for p in packets(reply) if p.startswith(message_id(1025)))
        reader=MessageReader(raw[3:])
        reader.take(1)
        return reader.string().decode('gb18030')

    def at_entrance(self, scene, npc):
        from native946_world import world

        target=world().npc(scene,npc)
        char=self.g.character
        x,y=target['x']//20,target['y']//20
        revision=self.f.characters.change_scene(char['owner'],self.p.cid,char['map_id'],scene,x,y)
        char.update(map_id=scene,x=x,y=y,position_revision=revision)
        return self.f.send(1034,npc)

    def test_calendar_invitation_opens_actionable_native_dialog(self):
        with patch('native946_activities.calendar_day',return_value=date(2026,11,26)):
            reply=self.f.send(1448)
        self.assertTrue(reply.startswith(message_id(1025)))
        self.assertEqual(self.g.dialog_npc,CALLBACK)
        names=[RECORDS[a['id']]['name'] for a in self.g.dialog_actions if a['op']=='detail']
        self.assertEqual(names,['剿灭流寇','西梁除魔','九重幻境','比武大会'])
        self.hub.root()
        self.assertEqual(len(self.g.dialog_actions),9)
        self.assertTrue(any(a['op']=='dungeon_list' for a in self.g.dialog_actions))
        self.assertIn(dict(kind='activity', op='afei_list', page=0), self.g.dialog_actions)
        self.choose(2)
        shown = {a['id'] for a in self.g.dialog_actions if a.get('op') == 'detail'}
        self.assertEqual(len(shown), 7)
        self.assertTrue({73, 81}.issubset(shown))

    def test_all_native_records_are_reachable_through_pagination(self):
        seen=set();page=0
        while True:
            self.hub.listing('all',page)
            seen.update(a['id'] for a in self.g.dialog_actions if a['op']=='detail')
            nxt=next((a['page'] for a in self.g.dialog_actions if a['op']=='list' and a.get('page',-1)>page),None)
            if nxt is None:break
            page=nxt
        self.assertEqual(seen,set(RECORDS))

    def test_supplementary_original_controller_activities_are_reachable_without_grants(self):
        before=self.p.snapshot();seen=set();page=0
        while True:
            self.hub.extra_listing(page)
            seen.update(a['name'] for a in self.g.dialog_actions if a['op']=='extra_detail')
            nxt=next((a['page'] for a in self.g.dialog_actions if a['op']=='extra_list' and a['page']>page),None)
            if nxt is None:break
            page=nxt
        self.assertEqual(seen,set(EXTRA_NAMES));self.assertEqual(len(seen),16)
        for name in seen:
            self.hub.extra_detail(name)
            self.assertTrue(all(a['op'] in ('extra_detail','extra_list') for a in self.g.dialog_actions))
        self.assertEqual(self.p.snapshot(),before)

    def test_pending_activity_never_claims_quest_completion_or_grants_rewards(self):
        before=self.p.snapshot();rid=next(i for i,r in RECORDS.items() if r['name']=='拯救牛魔王')
        data=self.hub.detail(rid);r=MessageReader(data[3:]);r.take(1)
        text=r.string().decode('gb18030');self.assertIn('待接通',text);self.assertIn('服务端奖励与日次结算处理',text)
        self.assertFalse(any(a['op'] in ('accept','fight','reward','claim') for a in self.g.dialog_actions))
        with self.assertRaises(ValueError):self.hub.execute(CALLBACK,dict(op='accept',id=rid))
        self.assertEqual(self.p.snapshot(),before)

    def test_unavailable_notice_requires_an_explicit_npc_binding(self):
        from native946_activities import npc_activity_names

        self.assertEqual(npc_activity_names(2001,6859),('神器试炼',))
        self.assertNotIn('鬼王任务',npc_activity_names(2001,7682))

    def test_visitor_activity_exposes_current_trial_rules_without_accepting(self):
        before=self.p.snapshot()
        self.hub.detail(74)
        choice=next(index for index,action in enumerate(self.g.dialog_actions,1) if action['op']=='rules')
        reply=self.choose(choice)
        reader=MessageReader(reply[3:])
        reader.take(1)
        text=reader.string().decode('gb18030')
        for part in ('至少3人组队','简单需110级','封圣120级','封圣185级','查询队员获得的高额奖励次数',
                     '每日有10次高额奖励','骊山、五原、潼关','巡守妖兵'):
            self.assertIn(part,text)
        self.assertEqual(self.p.snapshot(),before)

    def test_visitor_npc_keeps_transport_and_activity_info_checks_team(self):
        from native946_gameplay import NativeGameplay
        from native946_party import packets
        from native946_world import world

        giver=world().npc(2001,7510)
        position=dict(map_id=2001,x=giver['x']//20,y=giver['y']//20)
        self.g.character.update(position)
        opened=packets(self.f.send(1034,7510))
        self.assertTrue(any(p.startswith(message_id(1025)) for p in opened))
        self.assertEqual(self.g.dialog_actions[2],dict(kind='field_trial',op='visitor_look'))
        self.assertEqual(world().npc_route(2001,7510,1,(position['x'],position['y']))['to'],2431)
        self.assertFalse(any(isinstance(action,dict) and action['kind']=='activity'
                             for action in self.g.dialog_actions))

        def check():
            self.hub.detail(74)
            choice=next(i for i,a in enumerate(self.g.dialog_actions,1)
                        if isinstance(a,dict) and a.get('op')=='visitor_readiness')
            reply=next(p for p in packets(self.choose(choice)) if p.startswith(message_id(1025)))
            reader=MessageReader(reply[3:]);reader.take(1)
            return reader.string().decode('gb18030')

        self.assertIn('请先组队',check())
        self.f.send(2042,self.p.cid)
        for index,name in enumerate(('队员甲','队员乙'),1):
            actor=self.f.characters.create('story_owner',name,701)
            actor.update(position)
            game=NativeGameplay(actor,self.f.characters,lambda:())
            game.source_port=19260+index
            self.addCleanup(lambda game=game: self.g.party_service.detach(game.character['id']))
            game.receive(1034,MessageReader(packet(1034,['number'],[7510])[3:]))
            self.f.send(2039,actor['id'])
            game.receive(2038,MessageReader(packet(2038,['number'],[self.p.cid])[3:]))
        members=self.g.party_service.team(self.p.cid)['members']
        self.assertEqual(len(members),3)
        with self.p.transaction() as db:
            for row,level,sanctified in zip(members,(185,120,79),(1,1,0)):
                cid=row['character_id']
                db.execute('UPDATE characters SET level=? WHERE id=?',(level,cid))
                db.execute('INSERT INTO cultivation_state(character_id,sanctification) VALUES(?,?) '
                           'ON CONFLICT(character_id) DO UPDATE SET sanctification=excluded.sanctification',
                           (cid,sanctified))
        before=self.p.snapshot()
        text=check()
        self.assertIn('当前可跟随参战 3/3 人',text)
        self.assertIn('简单（110级） 未达标 队员乙',text)
        self.assertIn('困难（封圣120级） 未达标 队员乙',text)
        self.assertIn('挑战（封圣185级） 未达标 队员甲、队员乙',text)
        self.assertEqual(self.p.snapshot(),before)
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=110 WHERE id=?',(members[2]['character_id'],))
        text=check()
        self.assertIn('简单（110级） 当前参战成员等级达标',text)
        self.assertIn('困难（封圣120级） 未达标 队员乙',text)
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=185 WHERE id=?',(members[2]['character_id'],))
        text=check()
        self.assertIn('困难（封圣120级） 未达标 队员乙',text)
        self.assertIn('挑战（封圣185级） 未达标 队员甲、队员乙',text)
        with self.p.transaction() as db:
            db.execute('UPDATE native_team_members SET away=1 WHERE character_id=?',(members[2]['character_id'],))
        text=check()
        self.assertIn('当前可跟随参战 2/3 人',text)
        self.assertIn('简单（110级） 队伍人数不足',text)

    def test_reference_progress_is_owned_and_uses_current_reference_period(self):
        other=self.f.characters.create('story_owner','活动他人',701)
        with self.p.transaction() as db:
            db.execute('INSERT INTO native_npc_activity_progress(character_id,kind,day,accepted,completed) VALUES(?,?,?,?,?)',
                       (self.p.cid,'ghost',day_key(),2,1))
            db.execute('INSERT INTO native_npc_activity_progress(character_id,kind,day,accepted,completed) VALUES(?,?,?,?,?)',
                       (other['id'],'ghost',day_key(),99,88))
        row=next(r for r in RECORDS.values() if r['reference_kind']=='ghost')
        text=self.hub.progress(row);self.assertIn('已领2次',text);self.assertIn('已完成1次',text)
        self.assertNotIn('99',text);self.assertNotIn('88',text)

    def test_navigation_uses_verified_target_without_accepting_an_activity(self):
        row=next(r for r in RECORDS.values() if r['reference_kind']=='sanjie')
        before=self.p.snapshot()
        reply=self.hub.execute(CALLBACK,dict(op='navigate',id=row['id'],target=0))
        self.assertTrue(reply[1].startswith(message_id(2966)))
        self.assertEqual(self.p.snapshot(),before)

    def test_calendar_queries_do_not_fabricate_festival_or_dungeon_completion(self):
        self.assertEqual(self.f.send(1987),packet(1969,[('array','festival_info')],[[]]))
        self.assertEqual(self.f.send(1988),packet(1970,[('array','dungeon_task')],[[]]))
        self.assertEqual(self.f.send(1989),(packet(1975,[('array','task_trace_info')],[[]]),
                                         packet(1978,[('array','task_trace_info')],[[]])))

    def test_mijing_previews_use_original_rewards_and_only_the_actual_entrances(self):
        from native946_navigation import npc_auto_trace

        before=self.p.snapshot()
        for rid,target in ((98,(2360,79,53,8010,'陌上岚化身')),
                           (111,(2451,33,113,8270,'魔影'))):
            with self.subTest(activity=rid):
                self.hub.detail(rid)
                actions={op:[i for i,a in enumerate(self.g.dialog_actions,1) if a['op']==op]
                         for op in ('navigate','rules','guide')}
                self.assertTrue(all(len(indexes)==1 for indexes in actions.values()))
                reply=packets(self.choose(actions['navigate'][0]))
                self.assertIn(npc_auto_trace(*target),reply)
                self.hub.detail(rid)
                self.assertTrue(self.choose(actions['rules'][0]).startswith(message_id(1025)))
                self.hub.detail(rid)
                self.assertTrue(self.choose(actions['guide'][0]).startswith(message_id(1025)))
        self.assertIn('1000积分', '#r'.join(rule_pages(RECORDS[111])))
        self.assertIn('高级守护内丹宝箱×1',rule_pages(RECORDS[111])[-1])
        self.assertIn('困难 · 末世衰弱（二）','#r'.join(guide_pages(RECORDS[111])))
        self.assertEqual(len(guide_pages(RECORDS[98])),33)
        self.assertIn('4点幻境积分、九转元婴丹×6',guide_pages(RECORDS[98])[0])
        self.assertEqual(self.p.snapshot(),before)

    def test_current_mijing_contacts_offer_native_read_only_help(self):
        for rid in (98,111):
            with self.subTest(activity=rid):
                entrance=next(e for e in RECORDS[rid]['locations'] if e['npc'] in (8010,8270))
                char=self.g.character
                revision=self.f.characters.change_scene(char['owner'],self.p.cid,char['map_id'],
                                                         entrance['scene'],entrance['x'],entrance['y'])
                char.update(map_id=entrance['scene'],x=entrance['x'],y=entrance['y'],
                            position_revision=revision)
                reply=self.f.send(1034,entrance['npc'])
                self.assertTrue(reply.startswith(message_id(1025)))
                self.assertEqual(self.g.dialog_actions[0],
                                 dict(kind='activity',op='rules',id=rid,page=0))
                before=self.p.snapshot()
                self.assertTrue(self.choose(1,entrance['npc']).startswith(message_id(1025)))
                self.assertEqual(self.p.snapshot(),before)

    def test_demon_guide_shows_nine_native_names_and_verified_coordinates(self):
        from native946_map import load_map

        before=self.p.snapshot()
        self.hub.detail(82)
        description=self.menu_text(self.choose_action('demon_guide'))
        expected=(
            ('金角大王之怨','平顶山',2220,114,94),('真假禺狨王','火焰山',2256,109,84),
            ('如来之幻','御天岭',2357,204,158),('萧晚音','大唐东',2027,458,24),
            ('牛魔王之难','火焰山',2256,215,150),('赤煞妖姬','西梁女国集市',2236,11,168),
            ('百花劫','宝象国',2041,232,39),('幽冥之幻','幽冥地府',2009,40,127),
            ('黄风大王','乌斯藏',2024,68,163),
        )
        self.assertEqual(tuple((row['name'],row['scene_name'],row['scene'],row['x'],row['y'])
                               for row in RULES['demon_bosses']),expected)
        for name,scene_name,scene,x,y in expected:
            self.assertIn(f'{name}：{scene_name}（{x},{y}）',description)
            self.assertTrue(load_map(scene).walkable(x,y))
        self.assertIn('每小时第30分钟接受挑战',description)
        self.assertIn('均为「化境·五蕴」前置',description)
        self.assertEqual(self.p.snapshot(),before)

    def test_tower_readiness_checks_captain_five_nearby_players_grade_and_hours(self):
        from native946_gameplay import NativeGameplay

        self.at_entrance(2451,8270)

        def check(hour, minute=0):
            self.f.send(1034,8270)
            with patch('native946_activities.datetime',wraps=datetime) as clock:
                clock.now.return_value=datetime(2026,11,27,hour,minute,tzinfo=ACTIVITY_TIMEZONE)
                return self.menu_text(self.choose_action('tower_readiness',8270))

        self.assertIn('当前未组队',check(8,29))
        self.assertIn('当前未开放',check(8,29))
        self.f.send(2042,self.p.cid)
        position={key:self.g.character[key] for key in ('map_id','x','y')}
        followers=[]
        for i in range(1,5):
            actor=self.f.characters.create('story_owner',f'炼狱队员{i}',701)
            actor.update(position)
            member=NativeGameplay(actor,self.f.characters,lambda:())
            member.source_port=19260+i
            self.addCleanup(self.g.party_service.detach,actor['id'])
            member.receive(1034,MessageReader(packet(1034,['number'],[8270])[3:]))
            self.f.send(2039,actor['id'])
            member.receive(2038,MessageReader(packet(2038,['number'],[self.p.cid])[3:]))
            followers.append(member)
        team=self.g.party_service.team(self.p.cid)
        self.assertEqual(len(team['members']),5)
        with self.p.transaction() as db:
            for row in team['members']:
                cid=row['character_id']
                db.execute('UPDATE characters SET level=140 WHERE id=?',(cid,))
                db.execute('INSERT INTO cultivation_state(character_id,rebirth,ascension) VALUES(?,2,1) '
                           'ON CONFLICT(character_id) DO UPDATE SET rebirth=2,ascension=1',(cid,))
            cid=followers[-1].character['id']
            db.execute('UPDATE characters SET level=139 WHERE id=?',(cid,))
        self.assertIn('等级未达标：炼狱队员4',check(8,30))
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=140 WHERE id=?',(cid,))
            db.execute('UPDATE native_team_members SET away=1 WHERE character_id=?',(cid,))
        self.assertIn('当前无法跟随参战：炼狱队员4',check(8,30))
        with self.p.transaction() as db:
            db.execute('UPDATE native_team_members SET away=0 WHERE character_id=?',(cid,))
        before=self.p.snapshot()
        self.assertIn('满足人数、等级、时段和跟随条件',check(8,30))
        self.assertIn('当前未开放',check(23))
        self.assertNotIn('满足人数、等级、时段和跟随条件',check(23))
        self.assertEqual(self.p.snapshot(),before)
        self.assertIsNone(self.g.battle)

    def test_mijing_readiness_and_native_shop_are_read_only(self):
        self.at_entrance(2360,8010)

        def check(hour=8):
            self.f.send(1034,8010)
            with patch('native946_activities.datetime',wraps=datetime) as clock:
                clock.now.return_value=datetime(2026,11,27,hour,tzinfo=ACTIVITY_TIMEZONE)
                return self.menu_text(self.choose_action('mijing_readiness',8010))

        self.assertIn('封圣140级门槛未达标',check())
        self.assertIn('请携带至少一只有气血的守护',check())
        sid=self.g.summons.claim_starter()
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=140 WHERE id=?',(self.p.cid,))
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1,sanctification=1 WHERE character_id=?',
                       (self.p.cid,))
        self.assertIn('满足时段、等级、单人和守护携带条件',check())
        self.assertIn('猴儿精',check())
        self.assertIn('当前未开放',check(23))
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET hp=0 WHERE id=?',(sid,))
        self.assertIn('其中 0 只气血大于零',check())
        self.f.send(2042,self.p.cid)
        self.assertIn('当前仍在队伍',check())
        before=self.p.snapshot()
        guardians=self.g.summons.snapshot()
        expected=packet(3983,['number','string','string'],[0,'{}','{}'])
        self.f.send(1034,8010)
        self.assertIn(expected,packets(self.choose_action('mijing_shop',8010)))
        self.assertIn(expected,packets(self.f.send(3989)))
        self.hub.detail(98)
        self.assertIn(expected,packets(self.choose_action('mijing_shop')))
        self.assertIn('兑换尚未接通',self.f.send(3988,1,1).decode('gb18030',errors='ignore'))
        self.assertEqual(self.p.snapshot(),before)
        self.assertEqual(self.g.summons.snapshot(),guardians)
        self.assertIsNone(self.g.battle)
        self.g.character['x']+=30
        self.assertNotIn(expected,packets(self.f.send(3989)))

    def test_native_baoxiang_reward_preview_and_claims_do_not_change_owned_state(self):
        self.hub.root()
        self.assertTrue(self.g.dialog_open)
        before=self.p.snapshot();reply=self.f.send(3742)
        self.assertFalse(self.g.dialog_open)
        self.assertEqual(self.g.dialog_actions,[])
        self.assertIsNone(self.g.dialog_npc)
        self.assertEqual(reply[1],packet(3704,['number',('array','pair_number')],[0,[]]))
        for index in (1,10):
            reply=self.f.send(3705,index);self.assertTrue(reply.startswith(message_id(98)))
        self.assertTrue(self.f.send(3707).startswith(message_id(98)))
        for index in (0,11):self.assertTrue(self.f.send(3705,index).startswith(message_id(98)))
        self.assertEqual(self.p.snapshot(),before)

    def test_rule_pages_and_out_of_range_calendar_do_not_change_owned_state(self):
        before=self.p.snapshot();rid=next(r['id'] for r in RECORDS.values() if r['name']=='九重幻境')
        reply=self.hub.rules(rid);r=MessageReader(reply[3:]);r.take(1)
        text=r.string().decode('gb18030');self.assertIn('80级',text);self.assertIn('5人组队',text)
        with patch('native946_activities.calendar_day',return_value=date(2028,1,1)):
            self.hub.today()
        self.assertEqual(self.g.dialog_actions,[dict(kind='activity',op='root')])
        self.assertEqual(self.p.snapshot(),before)

    def test_malformed_calendar_request_and_stale_trainer_callback_do_not_execute(self):
        self.f.send(1448);before=self.p.snapshot()
        self.assertIsNone(self.choose(1,900001))
        self.assertEqual(self.g.dialog_npc,CALLBACK)
        reply=self.login.receive(packet(1987,['number'],[1]))
        self.assertTrue(reply.startswith(message_id(98)))
        self.assertEqual(self.p.snapshot(),before)


if __name__=='__main__':unittest.main()
