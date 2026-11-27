"""Native today's list for daily flows that actually exist on this server."""
from native946_codec import packet, screen_notice, text_notice
from native946_liveness import ACTIVITY_POINTS, TIERS, award, balances, daily_claims, day_start, score, total

WIRE_VERSION = 1
REQUESTS={1450,1400,4310}
CLAIM_REWARD = 1400


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
        rewards = [dict(need_liveness=threshold, has_get_reward=int(threshold in claimed),
                        can_get_reward=int(points >= threshold and threshold not in claimed),
                        reward_str=f'本机发放：活力×{rule["energy"]}，家园休息×{rule["rest"]}。')
                   for threshold, rule in TIERS.items()]
        return packet(1233, ['number', 'number', ('array', 'liveness_reward'), ('array', 'activity_list'), 'string', 'string'],
                      [points, 0, rewards, self.rows(by_activity) if tab == 1 else [], '{}', '{}'])

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
        result=self.panel(tab, day_start())
        if tab!=1:
            return True,(result,text_notice('此分类暂无已开放的完整流程；日常活动页可查看已接通的参考日常。'))
        return True,result
