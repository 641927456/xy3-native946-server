"""Native calendar/task routing with explicit availability and owned progress.

Calendar descriptions are evidence, not runnable server scripts. Existing
reference daily flows remain clearly identified; no missing activity grants
acceptance, completion, entry or rewards through this module.
"""
from pathlib import Path
from datetime import datetime,time,timezone,timedelta
import json
import re

from native946_codec import packet, screen_notice
from native946_codec import text_notice

DATA=json.loads(Path(__file__).with_name('native946_activity_data.json').read_text('utf8'))
RULES=json.loads(Path(__file__).with_name('native946_activity_rules.json').read_text('utf8'))
CALENDAR=json.loads(Path(__file__).with_name('native946_calendar_data.json').read_text('utf8'))
RECORDS={r['id']:r for r in DATA['records']}
for _rid,_record in tuple(RECORDS.items()):
    if _record['name']=='任务链':
        from native946_world import world
        from native946_task_chain import RULE as CHAIN_RULE
        _npc=world().npc(*CHAIN_RULE['giver'])
        RECORDS[_rid]=dict(_record,implementation='task_chain_v1',missing=[],locations=[dict(
            scene=_npc['scene_id'],npc=_npc['npc_id'],name=_npc['name'],x=_npc['x']//20,y=_npc['y']//20,reachable=True)])
EXTRA_NAMES=sorted(set(RULES['by_name'])-{r['name'] for r in RECORDS.values()})
CALLBACK=900100
PAGE_SIZE=7
MIJING_SHOP_EXCHANGE=3988
MIJING_SHOP_OPEN=3989
OPEN_BOSS_RECORDS=2412
REQUESTS={1448,1987,1988,1989,OPEN_BOSS_RECORDS,3705,3707,3742,MIJING_SHOP_EXCHANGE,MIJING_SHOP_OPEN}
LOTUS_ACTIVITY=11
VISITOR_ACTIVITY=74
FIELD_TRIAL_ACTIVITIES={
    4:'请组队找长安罗炎领取神器试炼。前往狮驼岭收取散逸的阴阳之气。',
    VISITOR_ACTIVITY:'至少3人组队，110级起找长安苍穹月领取。按任务指引清除巡守妖兵后迎战。',
}
RECORDS[LOTUS_ACTIVITY]=dict(RECORDS[LOTUS_ACTIVITY],implementation='npc_quest:lotus',reference_kind='lotus',missing=[])
DEMON_CHALLENGE=82
SUMMON_MIJING=98
REINCARNATION_TOWER=111
ACTIVITY_TIMEZONE=timezone(timedelta(hours=8))
DEMON_BOSSES=RULES['demon_bosses']
DEMON_RECORD_IDS={str(row['record_id']):row['record_id'] for row in DEMON_BOSSES}
VISITOR_MIN_TEAM=3
NPC_RECORDS={}
NPC_ENTRY_RECORDS={}
for _row in RECORDS.values():
    # The activity catalogue also contains name-only navigation candidates.
    # They cannot establish that a particular NPC hosts the activity.
    _entry_ids={int(value) for value in re.findall(r'#c\((\d+)\)#(?:R|G)(?:#u)?[^#]+',_row['tips'])}
    _aim=_row['auto_path_aim'].strip()
    if _aim.isascii() and _aim.isdigit():
        _entry_ids.add(int(_aim))
    _entry_ids.difference_update((0,900))
    for _npc in _row['locations']:
        _key=(_npc['scene'],_npc['npc'])
        NPC_RECORDS.setdefault(_key,[]).append(_row['id'])
        if _npc['npc'] in _entry_ids:
            NPC_ENTRY_RECORDS.setdefault(_key,[]).append(_row['id'])


def notice(text):return text_notice(text)


def npc_activity_names(scene, npc):
    from native946_dungeons import NPC_RECORDS as DUNGEON_NPCS, RECORDS as DUNGEON_RECORDS

    key = (scene, npc)
    names = [RECORDS[rid]['name'] for rid in NPC_ENTRY_RECORDS.get(key, ())]
    names.extend(DUNGEON_RECORDS[rid]['Name'] for rid in DUNGEON_NPCS.get(key, ()))
    return tuple(dict.fromkeys(names))


def unavailable_npc_notice(game, message):
    return game.close_npc_dialogue(), screen_notice(message, optimize_display=True)


def calendar_day():return datetime.now(ACTIVITY_TIMEZONE).date()


def demon_victories(progression):
    # Read the server's persisted results. The settlement key is
    # demon:<946 record ID>:<unique result ID>, with one result per participant.
    with progression.transaction(write=False) as db:
        rows = db.execute("SELECT battle_id FROM battle_results WHERE character_id=? AND owner=? "
                          "AND victory=1 AND battle_id GLOB 'demon:*'", (progression.cid, progression.owner))
        wins = set()
        for (key,) in rows:
            _, _, remainder = key.partition(':')
            record_id, _, result_id = remainder.partition(':')
            if result_id and record_id in DEMON_RECORD_IDS:
                wins.add(DEMON_RECORD_IDS[record_id])
        return wins


def demon_guide_text(wins):
    now = datetime.now(ACTIVITY_TIMEZONE)
    opening = now.replace(minute=30, second=0, microsecond=0)
    if now.minute >= 30:
        opening += timedelta(hours=1)
    lines = ['降魔挑战首领指引', '以下九个首领均为「化境·五蕴」前置。每小时第30分钟接受挑战，单人或组队均可。',
             f'原版下一次开放时刻 {opening:%H:%M}，本机已记录 {len(wins)}/{len(DEMON_BOSSES)}。']
    for row in DEMON_BOSSES:
        marker = ' [已击败]' if row['record_id'] in wins else ''
        lines.append(f'{row["name"]}：{row["scene_name"]}（{row["x"]},{row["y"]}）{marker}')
    lines.append('本机降魔首领刷新及战斗尚未开放。')
    return '#r'.join(lines)


def calendar_records(day):
    """Join original date-selector rows to their original task-tip records."""
    result=[]
    for source in CALENDAR['days'].get(day.isoformat(),[]):
        candidates=[r for r in RECORDS.values() if r['name']==source['name'].strip()]
        candidates.sort(key=lambda r:(r['original_name']!=source['name'],
            r['weekday']!=day.isoweekday(),r['weekday']!=11,r['id']))
        result.append((source,candidates[0] if candidates else None))
    return result


def navigation_targets(row):
    if row['id'] in (SUMMON_MIJING,REINCARNATION_TOWER):
        from native946_dungeons import BY_NAME, ENTRANCES

        return ENTRANCES[BY_NAME[row['name']]['Index']]
    targets=[target for target in row['locations'] if target['reachable']]
    if row['id']==LOTUS_ACTIVITY:
        from native946_npc_quests import LOTUS_RULES

        giver=tuple(LOTUS_RULES['lotus']['giver'])
    elif row['id']==VISITOR_ACTIVITY:
        from native946_field_trials import VISITOR_GIVER as giver
    else:
        return targets
    return [target for target in targets if (target['scene'],target['npc'])==giver]


def reward_name(kind):
    return RULES['item_metadata'].get(str(kind), {}).get('name') or f'奖励编号 {kind}'


def rule_pages(row):
    """Present original descriptions; display lists never become drop rules."""
    name=row['name'];pages=[]
    controller=RULES['by_name'].get(name,[])
    conditions=list(dict.fromkeys(r['LimitDesc'] for r in controller if r.get('LimitDesc')))
    times=list(dict.fromkeys(r['TimeDesc'] for r in controller if r.get('TimeDesc')))
    if conditions:pages.append('原版活动面板条件：#r'+'#r'.join(conditions)+
        ('#r面板时段：'+'；'.join(times) if times else ''))
    pages.extend(dict.fromkeys(r['ContentDesc'] for r in controller if r.get('ContentDesc')))
    special=RULES['special'].get(name,{});base=special.get('BaseConfig',{})
    for key in ('SignNpcHelp','InfoMsg','Rule','RuleText'):
        if base.get(key):
            # Split at rich-text paragraph boundaries, retaining original text.
            text=''
            for paragraph in base[key].split('#r'):
                if text and len(text)+len(paragraph)>650:pages.append(text);text=''
                text+=('#r' if text else '')+paragraph
            if text:pages.append(text)
    if name=='宝象国降妖':
        pages.append('原版资料差异：任务提示表仅列周一；本活动专用说明列周一、周四20:00-22:00。两处原文均保留，当前尚未开放活动领取。')
        for first in (0,5):
            lines=[]
            for stage in special['StageRewardConfig'][first:first+5]:
                rewards=[RULES['item_metadata'].get(str(v[0]),{}).get('name',str(v[0]))+'×'+str(v[1]) for v in stage['Reward']]
                lines.append(str(stage['RoundRequire'])+'轮：'+'、'.join(rewards))
            pages.append('原版固定阶段奖励：#r'+'#r'.join(lines)+'#r完成有效活动轮次后才可领取；完整活动流程尚未接通。')
    if name=='九重幻境':
        lines=[]
        for stage in special['ScoreConfig']:
            rewards=[RULES['item_metadata'].get(str(v[0]),{}).get('name',str(v[0]))+'×'+str(v[1]) for v in stage['Reward']]
            lines.append(str(stage['Score'])+'积分：'+'、'.join(rewards))
        pages.append('原版积分奖励：#r'+'#r'.join(lines)+'#r完整活动流程尚未接通，当前没有可领取的有效活动积分。')
    if name=='轮回炼狱':
        pages.append(f"每日开放时段：{base['BeginTime'].strip()}–{base['EndTime'].strip()}。"
                     f"#r每层{base['LevelMonsterCount']}关，通关一层后可选择是否增加负面词条。")
        lines=[f"{box['ScoreRequire']}积分：{reward_name(box['ItemType'])}"
               for box in special['ChestConfig']]
        pages.append('原版积分宝箱预览（每赛季每档一次）#r'+'#r'.join(lines))
        lines=[]
        for rank in special['RankConfig']:
            first,last=rank['StartPos'],rank['EndPos']
            place=str(first) if first==last else f'{first}–{last}'
            kind,amount,_=rank['Item']
            lines.append(f'第{place}名：{reward_name(kind)}×{amount}')
        pages.append('946 原版排行奖励预览#r'+'#r'.join(lines))
    if name=='玄天幻境':
        pages.append(base['DifficultyHelp']+'#r'+base['RankRewardTips'])
        goods=special['ScoreShopConfig']
        for start in range(0,len(goods),8):
            lines=[f"{item['Score']}积分：{reward_name(item['ItemType'])}"
                   for item in goods[start:start+8]]
            pages.append('原版积分商店预览#r'+'#r'.join(lines))
    if name=='百年后的来客':
        from native946_field_trials import VISITOR_DAILY_HIGH, VISITOR_STAGES

        route='、'.join(stage['scene_name'] for stage in VISITOR_STAGES)
        pages.append(
            f'由队长找长安的苍穹月领取，至少{VISITOR_MIN_TEAM}人组队。'
            '#r简单需110级，困难需封圣120级，挑战需封圣185级。'
            f'#r依次前往{route}。清完当前场景的巡守妖兵后，再向苍穹月迎战。'
            f'#r每日有{VISITOR_DAILY_HIGH}次高额奖励。可向苍穹月查询队员获得的高额奖励次数。')
    return pages


def guide_pages(row):
    special=RULES['special'].get(row['name'],{});pages=[]
    if row['name']=='天蓬问情':
        groups=special['GroupConfig']
        for start in range(0,len(groups),5):
            lines=[]
            for index,group in enumerate(groups[start:start+5],start+1):
                names=[groups[n-1]['Desc'] for n in group.get('NextGroups',[])]
                lines.append(str(index)+'. '+group['Desc']+('；后续：'+' / '.join(names) if names else ''))
            pages.append('原版阶段连接（尚未开放执行）：#r'+'#r'.join(lines))
        pages.extend('原版怪物图鉴：'+m['Name']+'#r'+m['Desc'] for m in special['MonsterConfig'])
    if row['name']=='九重幻境':
        scenes=special['SceneConfig']
        for start in range(0,len(scenes),5):
            pages.append('原版层级说明：#r'+'#r'.join('第'+str(i)+'层：首领积分'+str(s['BossScore'])+
                '，赐福守卫'+str(len(s['LuckyIndexList']))+'种。' for i,s in enumerate(scenes[start:start+5],start+1)))
        monsters=special['MonsterConfig']
        if isinstance(monsters,dict):monsters=monsters.values()
        tips=list(dict.fromkeys(m['Tips'] for m in monsters if m.get('Tips')))
        pages.extend('原版守卫说明：#r'+tip for tip in tips)
    if row['name']=='轮回炼狱':
        for variants in special['ThemeConfig']:
            theme=variants[0]
            pages.append(theme['Name']+'#r'+theme['Desc'])
        pages.append('原版词条库设有普通和困难选项，实际组合及对应关卡以赛季配置为准。')
        from native946_dungeons import pages as document_pages

        labels={0:'未选',1:'普通',2:'困难'}
        buffs=sorted(special['BuffConfig'].values(),key=lambda buff:buff['Index'])
        pages.extend(document_pages('#r'.join(f"{labels[buff['HardFlag']]} · {buff['Name']}：{buff['Desc']}"
                                               for buff in buffs)))
    if row['name']=='玄天幻境':
        currency=special['BaseConfig']['CurrencyType']
        for index,monster in enumerate(special['MonsterConfig'],1):
            lines=[f"第{index}关：{monster['DungeonName']}",
                   '普通特性：'+monster['DungeonDesc'], '困难特性：'+monster['HardDungeonDesc']]
            for key,label in (('FirstRewardShow','普通首通'), ('HardFirstRewardShow','困难首通'),
                              ('RepeatRewardShow','普通复通'), ('HardRepeatRewardShow','困难复通')):
                rewards='、'.join(f'{amount}点幻境积分' if kind==currency else f'{reward_name(kind)}×{amount}'
                                 for kind,amount,*_ in monster[key])
                lines.append(label+'奖励预览：'+rewards)
            pages.append('#r'.join(lines))
    return pages


class Activities:
    def __init__(self,game):self.g=game;self.p=game.progression

    def receive(self,mid,reader):
        if mid not in REQUESTS:return False,None
        if mid==MIJING_SHOP_EXCHANGE:
            reader.integer()
            reader.integer()
            reader.finish()
            return True,notice('玄天幻境积分商店目前开放展示，兑换尚未接通。')
        if mid==MIJING_SHOP_OPEN:
            from native946_npc_services import NpcServices

            reader.finish()
            target=navigation_targets(RECORDS[SUMMON_MIJING])[0]
            if self.g.character['map_id']!=target['scene']:
                raise ValueError('请到不周山的陌上岚化身处查看积分商店。')
            NpcServices(self.g).validate(target['npc'])
            return True,self.mijing_score_shop()
        if mid==3705:
            index=reader.integer();reader.finish()
            if not 1<=index<=len(RULES['special']['宝象国降妖']['StageRewardConfig']):
                raise ValueError('阶段奖励编号无效。')
            return True,notice('尚未完成宝象国降妖有效轮次，不能领取阶段奖励。完整活动流程尚未接通。')
        reader.finish()
        if mid==OPEN_BOSS_RECORDS:
            # 946 c_open_taskrank opens the original panel and populates its victory badges.
            return True,packet(2410,[('array','number')],[sorted(demon_victories(self.p))])
        if mid==1448:return True,self.today()
        if mid==3742:return True,self.baoxiang_rewards()
        if mid==3707:return True,notice('尚无宝象国降妖有效排名，不能领取排名奖励。完整活动流程尚未接通。')
        if mid==1987:
            # Weekly rotation is already drawn by the original client. This
            # server has no additional dated festival event to announce.
            return True,packet(1969,[('array','festival_info')],[[]])
        if mid==1988:
            from native946_dungeons import hall_reply
            return True,hall_reply(self.g)
        trace,_=self.p.task_records()
        # The suggestion callback in 946 is empty. Refresh the native trace
        # list as well so the client actually displays current owned tasks.
        return True,(packet(1975,[('array','task_trace_info')],[trace]),
                     packet(1978,[('array','task_trace_info')],[trace]))

    def _menu(self,text,choices,nid=CALLBACK):
        self.g.dialog_actions=[dict(kind='activity',**action) for _,action in choices]
        return self.g.menu(text,[label for label,_ in choices],nid)

    def root(self):
        return self._menu('原版活动与任务：'+str(len(RECORDS))+'条说明，共'+str(len({r['name'] for r in RECORDS.values()}))+'个名称。已开放捉鬼、鬼王、天庭、三界令、300环任务链、星宿挑战和普通藏宝图。各玩法规则与来源见详情。',[
            ('本日原版月历',dict(op='today')),
            ('可参与的日常任务',dict(op='list',section='supported',page=0)),
            ('日常任务与玩法',dict(op='list',section='daily',page=0)),
            ('周常和周末活动',dict(op='list',section='weekly',page=0)),
            ('全部原版活动与任务',dict(op='list',section='all',page=0)),
            ('副本与秘境目录',dict(op='dungeon_list',page=0)),
            ('客户端补充活动说明（'+str(len(EXTRA_NAMES))+'项）',dict(op='extra_list',page=0)),
            ('阿飞参考活动',dict(op='afei_list',page=0)),
            ('查看已领取任务',dict(op='journal'))])

    def today(self):
        day=calendar_day();rows=calendar_records(day)
        text='本日原版月历（'+day.isoformat()+'）：#r'
        choices=[]
        if day.isoformat() not in CALENDAR['days']:
            text+='此日期的原版轮换尚未核对，请查看客户端月历。'
        else:
            text+='#r'.join(source['name'].strip()+' '+source.get('tips','') for source,_ in rows)
            text+='#r#r请选择活动查看原版条件和接入状态。'
            choices=[(source['name'].strip(),dict(op='detail',id=row['id'])) for source,row in rows if row]
        choices.append(('返回活动与任务总目录',dict(op='root')))
        return self._menu(text,choices)

    def extra_listing(self,page=0):
        if type(page) is not int or not 0<=page<max(1,(len(EXTRA_NAMES)+PAGE_SIZE-1)//PAGE_SIZE):
            raise ValueError('补充活动目录页码无效。')
        choices=[(name+'（待接通）',dict(op='extra_detail',name=name,page=0))
            for name in EXTRA_NAMES[page*PAGE_SIZE:(page+1)*PAGE_SIZE]]
        if page:choices.append(('上一页',dict(op='extra_list',page=page-1)))
        if (page+1)*PAGE_SIZE<len(EXTRA_NAMES):choices.append(('下一页',dict(op='extra_list',page=page+1)))
        choices.append(('返回活动与任务总目录',dict(op='root')))
        return self._menu('客户端补充活动：第'+str(page+1)+'页，共'+str(len(EXTRA_NAMES))+'项。原版任务提示表未收录这些项目；开放日期需以实际活动公告为准。',choices)

    def extra_detail(self,name,page=0):
        if name not in EXTRA_NAMES:raise ValueError('没有这项补充活动。')
        pages=rule_pages(dict(name=name))
        if not pages:pages=['仅找到活动名称，完整活动尚未接通。']
        if type(page) is not int or not 0<=page<len(pages):raise ValueError('补充活动说明页码无效。')
        choices=[]
        if page:choices.append(('上一页',dict(op='extra_detail',name=name,page=page-1)))
        if page+1<len(pages):choices.append(('下一页',dict(op='extra_detail',name=name,page=page+1)))
        choices.append(('返回补充活动目录',dict(op='extra_list',page=0)))
        return self._menu(name+'：原版资料 '+str(page+1)+'/'+str(len(pages))+'#r'+pages[page]+
            '#r接入状态：待接通。完整活动流程与结算尚未开放。',choices)

    def listing(self,section='all',page=0,nid=CALLBACK):
        if section=='npc':
            ids=set(NPC_RECORDS.get((self.g.character['map_id'],nid),()))
            rows=[r for r in RECORDS.values() if r['id'] in ids]
        elif section=='supported':
            added=(LOTUS_ACTIVITY,*FIELD_TRIAL_ACTIVITIES)
            rows=[r for r in RECORDS.values() if r['id'] not in added and
                  (r['reference_kind'] or r['name']=='任务链' or r['id'] in (73,81))]
            rows.extend(RECORDS[rid] for rid in added)
        elif section=='daily':rows=[r for r in RECORDS.values() if r['weekday']==10]
        elif section=='weekly':rows=[r for r in RECORDS.values() if r['weekday']!=10]
        elif section=='all':rows=list(RECORDS.values())
        else:raise ValueError('活动目录分类无效。')
        if type(page) is not int or not 0<=page<max(1,(len(rows)+PAGE_SIZE-1)//PAGE_SIZE):
            raise ValueError('活动目录页码无效。')
        choices=[(r['name']+('' if r['reference_kind'] or r['name']=='任务链' or r['id'] in (73,81,*FIELD_TRIAL_ACTIVITIES) else '（待接通）'),dict(op='detail',id=r['id']))
                 for r in rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]]
        if page:choices.append(('上一页',dict(op='list',section=section,page=page-1)))
        if (page+1)*PAGE_SIZE<len(rows):choices.append(('下一页',dict(op='list',section=section,page=page+1)))
        choices.append(('返回活动与任务总目录',dict(op='root')))
        return self._menu('原版资料与接入状态。第'+str(page+1)+'页，共'+str(len(rows))+'项。',choices,nid)

    def progress(self,row):
        kind=row['reference_kind']
        if not kind:return ''
        from native946_npc_quests import day_key
        with self.p.transaction(write=False) as db:
            r=db.execute('SELECT day,accepted,completed FROM native_npc_activity_progress WHERE character_id=? AND kind=?',
                         (self.p.cid,kind)).fetchone()
        accepted=completed=0
        if r and r['day']==day_key():accepted,completed=r['accepted'],r['completed']
        active=next((r for r in self.p.snapshot()['npc_quests'] if r['kind']==kind),None)
        return '#r今日已领'+str(accepted)+'次，已完成'+str(completed)+'次。'+(
            '#r当前第'+str(active['ring'])+'环，目标：'+active['name']+'。' if active else '')

    def detail(self,rid,nid=CALLBACK):
        row=RECORDS.get(rid)
        if row is None:raise ValueError('没有这项原版活动。')
        weekday={1:'周一',2:'周二',3:'周三',4:'周四',5:'周五',6:'周六',7:'周日',10:'每日',11:'周末轮换，以原版日历为准'}.get(row['weekday'],'原版轮换')
        text=(row['name']+'#r原版日期：'+weekday+'#r原版时段：'+row['period']+
              '#r原版参与方式：'+row['count']+'#r#r'+row['tips'])
        from native946_dungeons import description,BY_NAME
        current_dungeon=BY_NAME.get(row['name'])
        if current_dungeon is not None:text=description(row['name'])
        if row['name']=='任务链':
            from native946_growth_guide import pages
            text=pages()[2][1]
        elif rid==LOTUS_ACTIVITY:
            text=(row['name']+'#r二转100级后，可单人到莲花洞找精细鬼领取。'
                  '#r每轮八环，领取每环交10枚三界令。'+self.progress(row))
        elif row['reference_kind']:
            text+='#r#r前往任务领取人处办理。'+self.progress(row)
        elif rid==73:
            text+='#r普通藏宝图（6020）已接入。50级可到长安第一楼找陈小知领强盗任务，获得宝图后右键查看地点。'
        elif rid==81:
            text+='#r每整点刷新星宿，保留15分钟。可前往傲来国1级地劣星，或查阅各地图其他星宿。编队参考阿飞，属性及奖池为本机规则。'
        elif rid==39:
            text+='#r#r可由守洞老猴进入水帘洞，和各层传送人对话往返灵泉洞、云霄径及翠石峰。四层均可通过暗雷胜利提升师门法术熟练度，组队胜利可获得额外经验。'
        elif rid in FIELD_TRIAL_ACTIVITIES:
            text=row['name']+'#r'+FIELD_TRIAL_ACTIVITIES[rid]
        else:
            missing={'原版奖励与日次结算':'服务端奖励与日次结算处理',
                     '活动实例及战斗配置':'服务端活动实例与战斗处理'}
            text+='#r#r接入状态：待接通。缺少：'+'、'.join(missing.get(v,v) for v in row['missing'])+'。'
        targets=navigation_targets(row)
        from native946_world import world
        catalog = world()
        choices=[('前往'+n['name'],dict(op='navigate',id=rid,target=i)) for i,n in enumerate(targets[:4])
                 if catalog.event_npc_is_open(n['scene'], n['npc'])]
        if targets and not choices:
            text += '#r当前活动 NPC 未开放。'
        if current_dungeon is not None:
            choices.append(('副本专用说明与奖励展示',dict(op='dungeon_detail',dungeon=current_dungeon['Index'])))
        if rid==73:choices.append(('前往陈小知处领取普通藏宝图任务',dict(op='treasure_giver')))
        if rid==81:
            choices.append(('前往傲来国的当前星宿',dict(op='star_location')))
            choices.append(('各地图星宿列表',dict(op='afei_list',page=0,stars=True)))
            choices.append(('前往陆伯言查询星宿称谓',dict(op='star_titles')))
        if rid==VISITOR_ACTIVITY:choices.append(('检查当前队伍条件',dict(op='visitor_readiness')))
        if rid==DEMON_CHALLENGE:choices.append(('查看降魔首领指引',dict(op='demon_guide')))
        choices.extend(self.mijing_choices(rid))
        if rule_pages(row):
            label='查看参与条件与玩法' if rid==VISITOR_ACTIVITY else '原版条件、规则与奖励说明'
            choices.append((label,dict(op='rules',id=rid,page=0)))
        if guide_pages(row):choices.append(('原版关卡与怪物图鉴',dict(op='guide',id=rid,page=0)))
        from native946_dungeon_dialogues import CATALOG as DUNGEON_DIALOGUES
        if row['name'] in DUNGEON_DIALOGUES:
            choices.append(('已恢复的剧情对话回放',dict(op='dungeon_dialogues',id=rid)))
        if row['name']=='宝象国降妖':choices.append(('原版阶段奖励预览',dict(op='baoxiang_rewards')))
        if row['name']=='任务链':
            current=self.p.snapshot().get('task_chain')
            if current:
                from native946_task_chain import QID_BASE
                choices.append(('查看当前任务详情',dict(op='task_detail',task=QID_BASE+current['id'])))
        elif row['reference_kind']:
            active=next((r for r in self.p.snapshot()['npc_quests'] if r['kind']==row['reference_kind']),None)
            if active:choices.append(('查看当前任务详情',dict(op='task_detail',task=930000+active['id'])))
        choices.append(('返回活动与任务总目录',dict(op='root')))
        return self._menu(text,choices,nid)

    def mijing_entrance(self,scene,npc):
        for rid in NPC_ENTRY_RECORDS.get((scene,npc),()):
            if rid not in (SUMMON_MIJING,REINCARNATION_TOWER):
                continue
            name=RECORDS[rid]['name']
            base=RULES['special'][name]['BaseConfig']
            if rid==SUMMON_MIJING:
                text,label=base['SignNpcHeadMsg'],base['SignNpcMenu5']
            else:
                text,label=name,base['NpcMenu2']
            return self._menu(text+'#r当前开放玩法资料，战斗及奖励结算待接通。',
                              [(label,dict(op='rules',id=rid,page=0)),
                               ('关卡与奖励预览',dict(op='detail',id=rid)),
                               *self.mijing_choices(rid)],npc)

    def mijing_choices(self, rid):
        if rid==REINCARNATION_TOWER:
            return [('检查当前队伍条件',dict(op='tower_readiness'))]
        if rid==SUMMON_MIJING:
            base=RULES['special']['玄天幻境']['BaseConfig']
            return [('检查守护出战条件',dict(op='mijing_readiness')),
                    (base['SignNpcMenu3'],dict(op='mijing_shop'))]
        return []

    def tower_readiness(self, nid):
        from native946_training import grade_number, state as training_state

        cfg=RULES['special']['轮回炼狱']['BaseConfig']
        start=time.fromisoformat(cfg['BeginTime'].strip())
        end=time.fromisoformat(cfg['EndTime'].strip())
        is_open=start<=datetime.now(ACTIVITY_TIMEZONE).time()<end
        count=cfg['TeamNumLimit']
        lines=['轮回炼狱队伍检查',
               f'活动时段 {start:%H:%M}–{end:%H:%M}，当前{"开放" if is_open else "未开放"}。',
               f'本赛季须固定{count}人组队，每人飞升140级以上。']
        party=self.g.party_service
        with self.p.transaction(write=False) as db:
            team=party.team(self.p.cid,db)
            if team is None:
                lines.append(f'当前未组队，需要{count}人组队。')
            elif team['leader_id']!=self.p.cid:
                lines.append('请由当前队长检查可参战队员。')
            else:
                members=team['members']
                ready={game.character['id'] for game in party.participants(self.g)}
                lines.append(f'队伍 {len(members)}/{count} 人，可跟随参战 {len(ready)}/{count} 人。')
                absent=[row['name'].replace('#','＃') for row in members if row['character_id'] not in ready]
                below=[row['name'].replace('#','＃') for row in members
                       if grade_number(row,training_state(db,row['character_id']))<cfg['GradeLimit']]
                if absent:
                    lines.append('当前无法跟随参战：'+'、'.join(absent)+'。')
                if below:
                    lines.append('等级未达标：'+'、'.join(below)+'。')
                if is_open and len(members)==len(ready)==count and not below:
                    lines.append('当前队伍满足人数、等级、时段和跟随条件。')
        lines.append('此处仅检查当前队伍。赛季登记、进场及结算待接通。')
        return self._menu('#r'.join(lines),[('返回活动详情',dict(op='detail',id=REINCARNATION_TOWER))],nid)

    def mijing_readiness(self, nid):
        from native946_training import grade_number

        cfg=RULES['special']['玄天幻境']['BaseConfig']
        now=datetime.now(ACTIVITY_TIMEZONE).hour
        is_open=cfg['OpenTimeHour']<=now<cfg['EndTimeHour']
        maximum=len(cfg['SlotUnlock'])
        current=self.p.snapshot()
        level_ok=grade_number(current,current['cultivation_state'])>=cfg['GradeLimit']
        solo=self.g.party_service.team(self.p.cid) is None
        carried=self.g.summons.snapshot()
        living=[guardian for guardian in carried if guardian['hp']>0]
        lines=['玄天幻境守护出战检查',
               f'活动时段 {cfg["OpenTimeHour"]:02d}:00–{cfg["EndTimeHour"]:02d}:00，当前{"开放" if is_open else "未开放"}。',
               '封圣140级门槛'+('已达标。' if level_ok else '未达标。'),
               '当前为单人。' if solo else '当前仍在队伍，玄天幻境为单人活动。',
               f'当前携带 {len(carried)} 只守护，其中 {len(living)} 只气血大于零。每场可派 1–{maximum} 只。']
        if living:
            lines.append('有气血的携带守护：'+'、'.join(
                guardian['name'].replace('#','＃') for guardian in living[:maximum])+'。')
        else:
            lines.append('请携带至少一只有气血的守护。')
        if is_open and level_ok and solo and living:
            lines.append('当前满足时段、等级、单人和守护携带条件。')
        lines.append('此处仅检查出战条件。布阵及战斗待接通。')
        return self._menu('#r'.join(lines),[('返回活动详情',dict(op='detail',id=SUMMON_MIJING))],nid)

    def mijing_score_shop(self):
        if self.g.battle:
            raise ValueError('请先结束当前战斗。')
        # 3983 forwards score and the two unpacked purchase tables to the native
        # mall. The client supplies names, costs and order from ScoreShopConfig.
        return (self.g.close_npc_dialogue(),
                packet(3983,['number','string','string'],[0,'{}','{}']),
                notice('玄天幻境积分商店预览。挑战积分与兑换待接通。'))

    def rules(self,rid,page=0,nid=CALLBACK,guide=False):
        row=RECORDS.get(rid)
        if row is None:raise ValueError('活动不存在。')
        pages=guide_pages(row) if guide else rule_pages(row);op='guide' if guide else 'rules'
        if type(page) is not int or not 0<=page<len(pages):raise ValueError('活动说明页码无效。')
        choices=[]
        if page:choices.append(('上一页',dict(op=op,id=rid,page=page-1)))
        if page+1<len(pages):choices.append(('下一页',dict(op=op,id=rid,page=page+1)))
        choices.append(('返回活动详情',dict(op='detail',id=rid)))
        title=row['name'] if rid==VISITOR_ACTIVITY else row['name']+'：原版资料 '+str(page+1)+'/'+str(len(pages))
        return self._menu(title+'#r'+pages[page],choices,nid)

    def baoxiang_rewards(self):
        # No server participation flow exists yet, so no character has earned
        # a valid round or claim through it. Use the real native panel, at zero.
        self.g.dialog_open=False
        self.g.dialog_actions=[]
        self.g.dialog_npc=None
        return (packet(1183,['string'],['dlg_npc_chat']),
            packet(3704,['number',('array','pair_number')],[0,[]]),
            notice('原版宝象国降妖奖励预览。完整活动流程尚未接通，当前没有可领取的有效活动轮次。'))

    def visitor_readiness(self,nid):
        from native946_training import state as training_state
        party=self.g.party_service
        text='百年后的来客队伍检查'
        with self.p.transaction(write=False) as db:
            team=party.team(self.p.cid,db)
            if team is None:
                text+=f'#r活动至少需要{VISITOR_MIN_TEAM}人组队。请先组队。'
            elif team['leader_id']!=self.p.cid:
                text+='#r请由当前队长检查队伍条件。'
            else:
                ids={member.character['id'] for member in party.participants(self.g)}
                levels=[(row,training_state(db,row['character_id']))
                        for row in team['members'] if row['character_id'] in ids]
                text+=f'#r当前可跟随参战 {len(levels)}/{len(team["members"])} 人。'
                if len(levels)<VISITOR_MIN_TEAM:
                    text+=f'#r至少需要{VISITOR_MIN_TEAM}名在线、在附近且未暂离的成员。'
                for mode,minimum,requires_sanctification in (
                    ('简单',110,False),('困难',120,True),('挑战',185,True)
                ):
                    if requires_sanctification:
                        below=[row['name'] for row,stage in levels
                               if not stage['sanctification'] or row['level']<minimum]
                    else:
                        below=[row['name'] for row,stage in levels if row['level']<minimum]
                    label=f'{"封圣" if requires_sanctification else ""}{minimum}级'
                    if below:status='未达标 '+'、'.join(name.replace('#','＃') for name in below)
                    elif len(levels)<VISITOR_MIN_TEAM:status='队伍人数不足'
                    else:status='当前参战成员等级达标'
                    text+=f'#r{mode}（{label}） {status}。'
        text+='#r队伍到齐后请找长安的苍穹月领取。'
        return self._menu(text,[('返回活动详情',dict(op='detail',id=VISITOR_ACTIVITY))],nid)

    def execute(self,nid,action):
        if self.g.battle:raise ValueError('请先结束当前战斗。')
        if nid!=CALLBACK:
            from native946_npc_services import NpcServices
            NpcServices(self.g).validate(nid)
        op=action['op']
        if op.startswith('local_dungeon_'):
            if nid!=CALLBACK:
                raise ValueError('请从本机副本入口进入。')
            from native946_local_dungeons import LocalDungeons
            return LocalDungeons(self.g).execute(action)
        if op in ('dungeon_list','dungeon_detail','dungeon_navigate','dungeon_rewards','dungeon_background','dungeon_puzzle'):
            from native946_dungeons import Dungeons
            return Dungeons(self.g).execute(nid,action)
        if op=='treasure_giver':
            from native946_ordinary_treasure import GIVER
            from native946_navigation import npc_auto_trace
            from native946_world import world
            scene,npc_id=GIVER
            npc=world().npc(scene,npc_id)
            return (packet(1183,['string'],['dlg_npc_chat']),
                    npc_auto_trace(scene,npc['x']//20,npc['y']//20,npc_id,npc['name']))
        if op=='star_titles':
            from native946_navigation import npc_auto_trace
            return (packet(1183,['string'],['dlg_npc_chat']),npc_auto_trace(2001,493,282,4285,'陆伯言'))
        if op=='star_location':
            from native946_star_challenge import StarChallenge
            return StarChallenge(self.g).location(nid)
        if op in ('afei_list','afei_detail','afei_location'):
            from native946_afei_expansion import AfeiWorld
            service=AfeiWorld(self.g)
            if op=='afei_list':return service.list_menu(self._menu,action['page'],action.get('stars',False))
            if op=='afei_detail':return service.detail(self._menu,action['event'])
            return service.location(action['event'],action['scene'])
        if op=='root':return self.root()
        if op=='today':return self.today()
        if op=='extra_list':return self.extra_listing(action.get('page',0))
        if op=='extra_detail':return self.extra_detail(action['name'],action.get('page',0))
        if op=='list':return self.listing(action.get('section','all'),action.get('page',0),nid)
        if op=='detail':return self.detail(action['id'],nid)
        if op in ('dungeon_dialogues','dungeon_dialogue_play'):
            from native946_dungeon_dialogues import DungeonDialogues
            row=RECORDS.get(action['id'])
            if row is None:raise ValueError('副本不存在。')
            dialogues=DungeonDialogues(self.g)
            return dialogues.menu(row,nid) if op=='dungeon_dialogues' else dialogues.play(row,action['clip'],nid)
        if op=='visitor_readiness':
            if nid!=CALLBACK and VISITOR_ACTIVITY not in NPC_RECORDS.get((self.g.character['map_id'],nid),()):
                raise ValueError('请在苍穹月处检查队伍条件。')
            return self.visitor_readiness(nid)
        if op=='demon_guide':
            return self._menu(demon_guide_text(demon_victories(self.p)),
                              [('返回活动详情',dict(op='detail',id=DEMON_CHALLENGE))],nid)
        if op in ('tower_readiness','mijing_readiness','mijing_shop'):
            rid=REINCARNATION_TOWER if op=='tower_readiness' else SUMMON_MIJING
            if nid!=CALLBACK and rid not in NPC_ENTRY_RECORDS.get((self.g.character['map_id'],nid),()):
                raise ValueError('请从对应的副本入口办理。')
            if op=='tower_readiness':return self.tower_readiness(nid)
            if op=='mijing_readiness':return self.mijing_readiness(nid)
            return self.mijing_score_shop()
        if op=='rules':return self.rules(action['id'],action.get('page',0),nid)
        if op=='guide':return self.rules(action['id'],action.get('page',0),nid,guide=True)
        if op=='baoxiang_rewards':return self.baoxiang_rewards()
        if op=='journal':
            return (packet(1183,['string'],['dlg_npc_chat']),*self.p.task_packets(),
                    packet(3993,['string'],['dlg_newtask_panel']))
        if op=='task_detail':
            from native946_tasks import TaskJournal
            return TaskJournal(self.g).detail(action['task'])
        if op=='navigate':
            row=RECORDS.get(action['id'])
            if row is None:raise ValueError('活动不存在。')
            targets=navigation_targets(row);index=action['target']
            if type(index) is not int or not 0<=index<len(targets):raise ValueError('活动领取人无效。')
            n=targets[index]
            from native946_navigation import npc_auto_trace
            from native946_world import world
            if not world().event_npc_is_open(n['scene'], n['npc']):
                raise ValueError('这个活动 NPC 当前未开放。')
            return (packet(1183,['string'],['dlg_npc_chat']),npc_auto_trace(n['scene'],n['x'],n['y'],n['npc'],n['name']))
        raise ValueError('此活动操作尚未接通，任务、物品和奖励均未改变。')
