"""The four 946 Today's Recommendations lists and daily liveness rewards."""
from datetime import datetime, timedelta

from native946_codec import packet, screen_notice, text_notice
from native946_liveness import ACTIVITY_POINTS, TIERS, award, balances, daily_claims, day_start, score, total

WIRE_VERSION = 2
REQUESTS={1450,1400,4310}
CLAIM_REWARD = 1400


def activity_row(name, kind, time, desc, done='—', active='—'):
    return dict(name=name, type=kind, time=time, desc=desc, done=done,
                active=active, status=1, icon_list='{}')


def encounter_completions(db, cid, day):
    """Count only settled victories owned by this character on this HK day."""
    start, end = int(day.timestamp()), int((day + timedelta(days=1)).timestamp())
    stars = 0
    events = {}
    for (key,) in db.execute(
        "SELECT battle_id FROM battle_results WHERE character_id=? AND settled_at>=? AND settled_at<? "
        "AND victory=1 AND (battle_id LIKE 'star:%' OR battle_id LIKE 'afei-event:%')",
        (cid, start, end),
    ):
        if key.startswith('star:'):
            stars += 1
        else:
            event = int(key.split(':', 2)[1])
            events[event] = events.get(event, 0) + 1
    return stars, events


def challenge_rows(db, cid, day, level):
    from native946_activities import RECORDS
    from native946_afei_expansion import STAR_EVENTS
    from native946_star_challenge import RULE

    stars, wins = encounter_completions(db, cid, day)
    stars += sum(wins.get(event, 0) for event in STAR_EVENTS)
    return [
        activity_row('星宿挑战', '本机挑战' if level >= RULE['level'] else '条件不足', '每小时整点',
                     f"{RULE['level']}级可单人或组队挑战。每整点刷新，保留{RULE['window_minutes']}分钟。#r"
                     '从活动与任务总目录的「星宿挑战」可定位目标。编队参考阿飞，属性与奖池采用现有本机规则。',
                     str(stars), '0'),
        activity_row('比武大会', '待接通', '周一/周四',
                     f"946 任务提示：周一{RECORDS[19]['period']}，周四{RECORDS[24]['period']}。#r"
                     + RECORDS[19]['tips'] + '#r本机尚未开放比武和结算。'),
        activity_row('宗师阁', '待接通', '见活动界面', RECORDS[75]['tips'] + '#r本机尚未开放匹配和结算。'),
    ]


def timed_rows(db, cid, day, now):
    from native946_activities import calendar_records
    from native946_afei_expansion import CALENDAR_EVENTS, EVENTS, window
    from native946_world import world

    _, wins = encounter_completions(db, cid, day)
    rows = []
    for index in CALENDAR_EVENTS:
        event = EVENTS[index]
        today = [rule for rule in event['timing'] if day.weekday() in rule['days']]
        started = window(event, int(now.timestamp()))
        if not today and started is None and index not in wins:
            continue
        times = []
        for rule in today:
            hours = rule['hours']
            time = f'{hours[0]:02d}时' if len(hours) == 1 else f'{hours[0]:02d}–{hours[-1]:02d}时'
            minutes = '、'.join(f'{minute:02d}' for minute in rule['minutes'])
            times.append((time, f'{time}的{minutes}分'))
        label = times[0][0] if times else '见说明'
        if len(today) == 1 and len(today[0]['hours']) == len(today[0]['minutes']) == 1:
            label = f"{today[0]['hours'][0]:02d}:{today[0]['minutes'][0]:02d}"
        places = '、'.join(world().scene(scene)['scene_name'] for scene in event['scenes'])
        starts = '；'.join(detail for _, detail in times)
        timing = f'今天在{starts}刷新。' if starts else '今天无新场次。'
        desc = (f"阿飞参考活动。{timing}每次刷新保留{event['seconds'] // 60}分钟。#r"
                f"地点：{places}。单人{event['level']}级，组队至少{event['team_min']}人且每人{event['team_level']}级。#r"
                '从活动与任务总目录的「阿飞参考活动」查看当前目标。')
        rows.append(activity_row(event['name'], '刷新时段' if started is not None else '未刷新',
                                 label, desc, str(wins.get(index, 0)), '0'))

    for source, record in calendar_records(day.date()):
        name = source['name'].strip()
        if name == '比武大会':
            continue
        kind = '仅入口' if name in ('九重幻境', '云阶秘藏') else '待接通'
        desc = (record['tips'] if record else '') + '#r本机尚未开放活动战斗和结算。'
        if kind == '仅入口':
            desc += '#r入口 NPC 按本机活动时段出现。'
        rows.append(activity_row(name, kind, source['tips'], desc))
    return rows


def dungeon_rows(db, cid, hero, grouped):
    from native946_activities import RECORDS as ACTIVITIES
    from native946_dungeon_routes import ROUTES, read_run
    from native946_dungeons import ENTRANCES, RECORDS, description
    from native946_world import world

    periods = {row['name']: row['period'] for row in ACTIVITIES.values()}
    rows = []
    for key, record in RECORDS.items():
        if record.get('IsHide'):
            continue
        name = record['Name']
        if key not in ROUTES:
            kind = '仅入口' if name == '逆转时轮' else '待接通'
            rows.append(activity_row(name, kind, periods.get(name, '见说明'),
                                     description(name) + '#r本机尚未开放副本挑战和结算。'))
            continue

        route = ROUTES[key]
        run = read_run(db, cid, key)
        completed = run is not None and run['state'] == 'completed'
        if completed:
            kind = '已结算'
        elif run:
            kind = '进行中'
        elif grouped or hero['level'] < route['level'] or not hero[route['cultivation']]:
            kind = '条件不足'
        else:
            kind = '本机副本'
        entrance = ENTRANCES[key][0]
        scene = world().scene(entrance['scene'])['scene_name']
        desc = (f"入口：{scene}（{entrance['x']},{entrance['y']}）{entrance['name']}。#r"
                f"{route['grade']}可单人参加，每天可结算1次。"
                f"共{len(route['waves'])}场本机战斗，通关奖励为经验{route['experience']}、现金{route['gold']}。#r"
                '前往原入口 NPC，选择「本机战斗流程」。')
        if run and not completed:
            desc += f"#r本次已通过{run['stage']}/{len(route['waves'])}场战斗。"
        rows.append(activity_row(name, kind, '每日', desc, '1/1' if completed else '0/1', '0'))
    return rows


class TaskRecommendations:
    def __init__(self,game):
        self.g=game
        self.p=game.progression

    def rows(self, by_activity=None):
        from native946_npc_quests import ACTIVITIES,day_key
        s=self.p.snapshot()
        with self.p.transaction(write=False) as db:
            if by_activity is None:
                by_activity = score(db, self.p.cid, day_start())
            progress={r['kind']:dict(r) for r in db.execute(
                'SELECT kind,accepted,completed FROM native_npc_activity_progress WHERE character_id=? AND day=?',
                (self.p.cid,day_key()))}
        result=[]
        for kind,meta in ACTIVITIES.items():
            row=progress.get(kind,{})
            accepted,completed=row.get('accepted',0),row.get('completed',0)
            available=s['level']>=meta['level'] and s['ascension']>=meta['ascension']
            if kind=='school' and not s['school']:available=False
            if meta['team'] and self.g.party_service.team(self.p.cid) is None:available=False
            if meta['limit'] and accepted>=meta['limit']:available=False
            rule = ACTIVITY_POINTS.get(kind)
            # The native status controls sorting/color, not authorization.
            # Expose unmet conditions as text rather than guessing an enum.
            result.append(dict(name=meta['name'],type='日常任务' if available else '条件不足',time='每日',
                done=str(completed)+'/'+(str(meta['limit']) if meta['limit'] else '不限'),
                active=f'{by_activity.get(kind, 0)}/{rule[1]}' if rule else '0',status=1,icon_list='{}',
                desc='最低等级'+str(meta['level'])+'级'+
                     ('，需要飞升' if meta['ascension'] else '')+
                     ('，需要先建立队伍' if meta['team'] else '')+
                     ('，需要拜师并在本门派师父处单人领取' if kind=='school' else '')+
                     '。今日已领取'+str(accepted)+'次，完成'+str(completed)+'次；每次任务限时20分钟。'+
                     ('当前条件不足。' if not available else '')+
                     (f'本机每次完成增加{rule[0]}点活跃度，本项每日上限{rule[1]}点。' if rule else '')))
        from native946_task_chain import RULE,week_key
        with self.p.transaction(write=False) as db:
            accepted=db.execute('SELECT count(*) FROM native_chain_runs WHERE character_id=? AND week=?',(self.p.cid,week_key())).fetchone()[0]
        chain=s.get('task_chain');available=s['level']>=RULE['min_level'] and s['ascension']>=RULE['min_ascension']
        result.append(dict(name='任务链',type='日常任务' if available else '条件不足',time='每周',
            done=str(accepted)+'/'+str(RULE['weekly_chains']),active='0',status=1,icon_list='{}',
            desc='飞升160级后在长安白居士处单人领取。每环经验与通宝，完成300环获得额外现金与道具。'+
                 ('当前第'+str(chain['ring'])+'环。' if chain else '')))
        return result

    def panel(self, tab, day):
        with self.p.transaction(write=False) as db:
            by_activity = score(db, self.p.cid, day)
            points = total(by_activity)
            balance = balances(db, self.p.cid, day)
            claimed = daily_claims(balance, day)
            if tab == 2:
                rows = challenge_rows(db, self.p.cid, day, self.g.character['level'])
            elif tab == 3:
                rows = timed_rows(db, self.p.cid, day, datetime.now(day.tzinfo))
            elif tab == 4:
                hero = self.p._snapshot(db)
                grouped = self.g.party_service.team(self.p.cid, db) is not None
                rows = dungeon_rows(db, self.p.cid, hero, grouped)
        if tab == 1:
            rows = self.rows(by_activity)
        rewards = [dict(need_liveness=threshold, has_get_reward=int(threshold in claimed),
                        can_get_reward=int(points >= threshold and threshold not in claimed),
                        reward_str=f'本机发放：活力×{rule["energy"]}，家园休息×{rule["rest"]}。')
                   for threshold, rule in TIERS.items()]
        return packet(1233, ['number', 'number', ('array', 'liveness_reward'), ('array', 'activity_list'), 'string', 'string'],
                      [points, 0, rewards, rows, '{}', '{}'])

    def claim(self, index, action, tab):
        if not 1 <= index <= len(TIERS) or action != 1 or not 1 <= tab <= 4:
            raise ValueError('活跃度领奖请求无效。')
        if self.g.battle is not None:
            raise ValueError('请在战斗结束后领取活跃度奖励。')
        threshold = tuple(TIERS)[index - 1]
        day = day_start()
        with self.p.transaction() as db:
            reward, balance = award(db, self.p.cid, day, threshold)
        if reward is None:
            message = text_notice('今天已经领取过这档活跃度奖励。')
            return self.g.defer_packets(lambda: (self.panel(tab, day_start()), message), fallback=message)

        from native946_progression import DATA

        message = screen_notice(f'已领取{threshold}点活跃度奖励。活跃积分+{reward["points"]}，活力+{reward["energy"]}，'
                                f'家园休息+{reward["rest"]}次，今日跑镖+{reward["escort"]}次，今日赏金+{reward["bounty"]}次。'
                                f'#r当前活跃积分：{balance["points"]}。')
        attributes = (packet(695, ['number', 'number'], [int(DATA['attributes']['Energy']), balance['energy']]),
                      packet(694, [], []))
        return self.g.defer_packets(lambda: (*attributes, *self.p.task_packets(), self.panel(tab, day_start()), message),
                                    fallback=(*attributes, message))

    def receive(self,mid,reader):
        if mid not in REQUESTS:return False,None
        if mid==4310:
            reader.finish()
            return True,packet(4309,[('array','pair_string')],[[]])
        if mid==CLAIM_REWARD:
            index=reader.integer();action,tab=reader.take(2);reader.finish()
            return True,self.claim(index,action,tab)
        tab=reader.take(1)[0];reader.finish()
        if not 1<=tab<=4:raise ValueError('今日推荐分类不存在。')
        return True,self.panel(tab, day_start())
