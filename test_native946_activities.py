"""Native activity metadata, calendar dispatch and unimplemented-flow limits."""
from pathlib import Path
from datetime import date
from unittest.mock import patch
import json,sys,unittest

from native946_activities import Activities,RECORDS,CALLBACK,CALENDAR,RULES,EXTRA_NAMES,calendar_records,rule_pages,guide_pages
from native946_codec import packet
from native946_game_login import message_id
from native946_login import MessageReader
from native946_npc_quests import day_key
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
        self.assertEqual(actual,{'ghost','king','heaven','sanjie'})
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


class ActivityDispatchTests(unittest.TestCase):
    def setUp(self):
        self.f=task_fixtures.TaskJournalTests()
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.g=self.f.g;self.p=self.f.p;self.login=self.f.login;self.hub=Activities(self.g)

    def choose(self,index,callback=CALLBACK):
        return self.login.receive(packet(1032,['byte','number'],[index,callback]))

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

    def test_visitor_activity_exposes_sourced_auto_task_rules_without_accepting(self):
        before=self.p.snapshot()
        self.hub.detail(74)
        choice=next(index for index,action in enumerate(self.g.dialog_actions,1) if action['op']=='rules')
        reply=self.choose(choice)
        reader=MessageReader(reply[3:])
        reader.take(1)
        text=reader.string().decode('gb18030')
        for part in ('至少3人组队','跨服组队集结仅可选简单','领取冷却按难度区分',
                     '2024年7月','封圣120级','封圣185级','查询队员获得的高额奖励次数',
                     '每带队取得40次高额奖励','可额外获得一枚自行丹','奖励结算待接通'):
            self.assertIn(part,text)
        choice=next(index for index,action in enumerate(self.g.dialog_actions,1) if action.get('page')==1)
        reply=self.choose(choice)
        reader=MessageReader(reply[3:]);reader.take(1)
        preview=reader.string().decode('gb18030')
        self.assertIn('常规服组队集结奖励预览',preview)
        self.assertIn('悔梦石、天运灵宝、龙之骨、仙玉莲叶、仙玉莲蓬、九转易筋丸',preview)
        self.assertIn('实际发放的物品和概率仍待核实',preview)
        self.assertIn('增加1点活跃度，累计上限40点',preview)
        self.assertEqual(self.p.snapshot(),before)

    def test_visitor_giver_uses_current_team_and_difficulty_without_grants(self):
        from native946_gameplay import NativeGameplay
        from native946_party import packets
        from native946_world import world

        giver=world().npc(2001,7510)
        position=dict(map_id=2001,x=giver['x']//20,y=giver['y']//20)
        self.g.character.update(position)

        def check():
            self.assertTrue(any(p.startswith(message_id(1025)) for p in packets(self.f.send(1034,7510))))
            choice=next(i for i,a in enumerate(self.g.dialog_actions,1)
                        if isinstance(a,dict) and a.get('op')=='visitor_readiness')
            reply=next(p for p in packets(self.choose(choice,7510)) if p.startswith(message_id(1025)))
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
        self.assertIn('简单（80级） 未达标 队员乙',text)
        self.assertIn('困难（封圣120级） 未达标 队员乙',text)
        self.assertIn('挑战（封圣185级） 未达标 队员甲、队员乙',text)
        self.assertEqual(self.p.snapshot(),before)
        with self.p.transaction() as db:
            db.execute('UPDATE cultivation_state SET rebirth=1 WHERE character_id=?',(members[2]['character_id'],))
        text=check()
        self.assertIn('简单（80级） 当前参战成员等级达标',text)
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
        self.assertIn('简单（80级） 队伍人数不足',text)

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
        reply=self.f.send(1988)
        self.assertEqual(reply[0],packet(1970,[('array','dungeon_task')],[[]]))
        self.assertEqual(self.f.send(1989),(packet(1975,[('array','task_trace_info')],[[]]),
                                         packet(1978,[('array','task_trace_info')],[[]])))

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
