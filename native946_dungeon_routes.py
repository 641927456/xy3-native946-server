"""Playthroughs on installed 946 scenes with locally authored battles and rewards."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from secrets import randbelow, token_hex
from time import time

from native946_codec import packet, text_notice
from native946_dungeons import ENTRANCES, RECORDS, _puzzle
from native946_map import load_map
from native946_wilderness import WildBattle
from native946_world import INSTANCE_SCENES as SCENES


GUIDE = {25: 980025, 26: 980026}

# Locally authored encounters on preinstalled scenes. The battle roster entries
# describe display shape, health in ordinary attacks, and bell credit.
ROUTES = {
    25: dict(level=180, cultivation='ascension', grade='飞升180级', experience=3800, gold=480,
             waves=(
                 (8716, '巡阵', (('寻隙阵灵', 2108, 2, 1), ('护阵阵灵', 2107, 2, 1)), 0),
                 (8717, '破阵', (('回响阵灵', 2052, 3, 2), ('护阵阵灵', 2107, 2, 1)), 3),
                 (8718, '镇阵', (('镇阵幻影', 2090, 6, 4), ('回响阵灵', 2108, 2, 2)), 2),
             )),
    26: dict(level=165, cultivation='sanctification', grade='封圣165级', experience=4200, gold=520,
             waves=(
                 (8627, '测隙', (('裂隙巡灵', 3196, 2, 0), ('回声巡灵', 2107, 2, 0)), 0),
                 (8628, '守界', (('逐潮幻影', 2052, 4, 0), ('小潮幻影', 2108, 2, 0)), 3),
                 (8634, '合界', (('凝潮幻影', 3327, 7, 0), ('回声巡灵', 3196, 2, 0)), 2),
             )),
}
ENTRY_KEYS = {(ENTRANCES[key][0]['scene'], ENTRANCES[key][0]['npc']): key for key in ROUTES}
SCENE_KEYS = {wave[0]: key for key, route in ROUTES.items() for wave in route['waves']}
HK = timezone(timedelta(hours=8))


def read_run(db, cid, key):
    row = db.execute('''SELECT r.*,p.stage FROM native_dungeon_runs r
        JOIN native_dungeon_route_progress p USING(instance_id,character_id)
        WHERE r.character_id=? AND r.dungeon_id=? AND r.reset_at>? AND r.state<>'abandoned'
        ORDER BY r.reset_at DESC,r.instance_id LIMIT 1''', (cid,key,int(time()))).fetchone()
    return dict(row) if row else None


def stage_guide(game):
    scene = game.character['map_id']
    if scene not in SCENE_KEYS:
        return ()
    x,y = SCENES[scene]['guide']
    return (packet(1589,['oci_npc'],[dict(id=GUIDE[SCENE_KEYS[scene]],name='副本执事',title='本机战斗流程',
                type=2052,class_type=1,x=x*20,y=y*20,dir=4,layer=1,member_click=1)]),)


class DungeonRoute:
    def __init__(self, game):
        self.g = game
        self.p = game.progression

    def menu(self, nid, text, choices):
        self.g.dialog_actions = [dict(kind='dungeon_route', **action) for _,action in choices]
        return self.g.menu(text, [label for label,_ in choices], nid)

    def validate(self, key, nid):
        from native946_world import world

        current = self.g.character
        scene = current['map_id']
        if ENTRY_KEYS.get((scene,nid)) == key:
            npc = world().npc(scene,nid)
            if npc is None:
                raise ValueError('副本入口暂时不可用。')
            x,y = npc['x']//20,npc['y']//20
            inside = False
        elif SCENE_KEYS.get(scene) == key and GUIDE[key] == nid:
            x,y = SCENES[scene]['guide']
            inside = True
        else:
            raise ValueError('请从本副本入口操作。')
        if max(abs(current['x']-x),abs(current['y']-y)) > 12:
            raise ValueError('请靠近副本联系人。')
        return inside

    def look(self, nid):
        key = SCENE_KEYS.get(self.g.character['map_id'])
        if key is not None and GUIDE[key] == nid:
            return self.show(key,nid)

    def resume(self, nid):
        key = ENTRY_KEYS.get((self.g.character['map_id'],nid))
        if key is not None:
            with self.p.transaction(write=False) as db:
                run = read_run(db,self.p.cid,key)
            if run and run['state'] == 'entered':
                return self.show(key,nid)

    def attach_entry(self, nid, reply):
        key = ENTRY_KEYS.get((self.g.character['map_id'], nid))
        if key is None:
            return reply
        return self.g.append_npc_menu_option(nid, reply, RECORDS[key]['Name']+'（本机战斗流程）',
                                             dict(kind='dungeon_route', op='show', key=key))

    def puzzle(self, db, run):
        if run['dungeon_id']==26 and run['stage']==1:
            return db.execute('SELECT * FROM native_tianpeng_puzzles WHERE instance_id=? AND character_id=? AND solved=0',
                              (run['instance_id'], self.p.cid)).fetchone()
        return None

    def show(self, key, nid):
        inside = self.validate(key,nid)
        with self.p.transaction(write=False) as db:
            run = read_run(db,self.p.cid,key)
            puzzle = self.puzzle(db,run) if run else None
        design = ROUTES[key]
        title = RECORDS[key]['Name'] + '（本机战斗与奖励规则）'
        args = dict(key=key)
        if inside:
            choices = [('离开副本',dict(op='leave', **args))]
        else:
            choices = []
        if not run:
            if not inside:
                choices.append(('领取副本任务',dict(op='accept', **args)))
                title += f"#r{design['grade']}可领取。当前支持单人流程，每天可领取一次通关奖励。"
            else:
                title += '#r当前没有有效进度，请返回领取人。'
        elif run['state']=='completed':
            title += f"#r今日已领取经验{design['experience']}、现金{design['gold']}。"
        else:
            stage = run['stage']
            waves = design['waves']
            args.update(instance=run['instance_id'],stage=stage)
            if stage == len(waves):
                title += '#r已通关，可领取奖励。'
                choices.insert(0,('领取通关奖励',dict(op='claim', **args)))
            elif puzzle and inside and self.g.character['map_id']==puzzle['scene_id']:
                hint = '、'.join(str(puzzle['answer_'+column]) for column in 'abc')
                title += f'#r从左至右的机关刻度为{hint}。开启机关后继续。'
                choices.insert(0,('查看机关',dict(op='puzzle', **args)))
            elif inside and self.g.character['map_id']==waves[stage][0]:
                title += f"#r第{stage+1}场：{waves[stage][1]}。"
                choices.insert(0,('开始本阶段战斗',dict(op='fight', **args)))
            else:
                choices.insert(0,('前往下一场' if inside else '进入副本',dict(op='travel', **args)))
        choices.append(('关闭',dict(op='close',key=key)))
        return self.menu(nid,title,choices)

    def require_run(self, db, action):
        run = read_run(db,self.p.cid,action['key'])
        if not run or run['state']!='entered' or (run['instance_id'],run['stage']) != (
                action.get('instance'),action.get('stage')):
            raise ValueError('副本进度已经变化，请重新查看。')
        return run

    def execute(self, nid, action):
        key = action['key']
        op = action['op']
        if self.g.battle:
            raise ValueError('请先结束当前战斗。')
        inside = self.validate(key,nid)
        design = ROUTES[key]
        if op=='show':
            return self.show(key,nid)
        if op=='close':
            return packet(1183,['string'],['dlg_npc_chat'])
        if op=='leave' and inside:
            entry = ENTRANCES[key][0]
            arrival = load_map(entry['scene']).nearest_walkable(entry['x'],entry['y'])
            return self.transfer(entry['scene'],arrival)
        if op=='fight':
            if not inside:
                raise ValueError('请先进入副本。')
            return self.g.defer_packets(lambda:self.start(nid,action))

        with self.p.transaction() as db:
            if op=='accept' and not inside:
                if read_run(db,self.p.cid,key):
                    raise ValueError('本副本已有可用进度或已结算。')
                hero = self.p._snapshot(db)
                if hero['level'] < design['level'] or not hero[design['cultivation']]:
                    raise ValueError('领取需要达到'+design['grade']+'。')
                if self.g.party_service.team(self.p.cid,db) is not None:
                    raise ValueError('本机流程为单人副本，请先退出队伍。')
                now = datetime.now(HK)
                tomorrow = (now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
                instance = token_hex(16)
                db.execute("INSERT INTO native_dungeon_runs VALUES(?,?,?,?,'entered',?)",
                           (instance,self.p.cid,key,now.date().isoformat(),int(tomorrow.timestamp())))
                db.execute('INSERT INTO native_dungeon_route_progress VALUES(?,?,0)',(instance,self.p.cid))
            else:
                run = self.require_run(db,action)
                puzzle = self.puzzle(db,run)
                if op=='travel':
                    if self.g.party_service.team(self.p.cid,db) is not None:
                        raise ValueError('本机流程为单人副本，请先退出队伍。')
                    if run['stage']>=len(design['waves']):
                        raise ValueError('请领取已完成的奖励。')
                    if puzzle and inside:
                        raise ValueError('请先解开机关。')
                    scene = puzzle['scene_id'] if puzzle else design['waves'][run['stage']][0]
                    return self.transfer(scene,SCENES[scene]['arrival'])
                if op=='puzzle' and inside:
                    owned = _puzzle(self.g,db)
                    if not puzzle or not owned or owned['instance_id']!=run['instance_id']:
                        raise ValueError('当前机关不可用。')
                    return packet(4164,['string'],[puzzle['clue']]),packet(4163,[],[])
                if op!='claim' or run['stage']!=len(design['waves']):
                    raise ValueError('当前阶段不能领取通关奖励。')
                self.g.characters.settle_battle(self.g.character['owner'],self.p.cid,'dungeon:'+run['instance_id'],
                    True,experience=design['experience'],gold=design['gold'],advance_quest=False)
                db.execute("UPDATE native_dungeon_runs SET state='completed' WHERE instance_id=? AND character_id=?",
                           (run['instance_id'], self.p.cid))
        self.p._cached_snapshot = None
        return self.g.defer_packets(lambda:(self.show(key,nid),*self.g.sync_progression()))

    def transfer(self, scene, arrival):
        return self.g.change_scene(dict(to=scene,arrival=arrival,id=-scene),('dungeon-instance',scene))

    def start(self, nid, action):
        game = self.g
        key = action['key']
        if not self.validate(key,nid) or game.battle or game.party_service.battles.get(self.p.cid):
            raise ValueError('当前不能开始一场新的副本战斗。')
        with self.p.transaction(write=False) as db:
            run = self.require_run(db,action)
            waves = ROUTES[key]['waves']
            if (run['stage']>=len(waves) or waves[run['stage']][0]!=game.character['map_id']
                    or self.puzzle(db,run) or game.party_service.team(self.p.cid,db)):
                raise ValueError('请先进入当前关卡。')
            hero = self.p._snapshot(db)
        if hero['hp']<=0:
            raise ValueError('请返回恢复气血后再挑战。')
        saved = {name:deepcopy(getattr(game,name)) for name in ('battle','dialog_open','dialog_actions','dialog_npc')}
        try:
            battle = DungeonRouteBattle(game,run,hero)
            replies = battle.entry(game)
        except BaseException:
            game.party_service.battles.pop(self.p.cid,None)
            game._wild_battle = None
            for name,value in saved.items():
                setattr(game,name,value)
            raise
        game.party_service.enqueue(game,*replies)
        return ()


class DungeonRouteBattle(WildBattle):
    def __init__(self, game, run, hero):
        self.run = run
        key = run['dungeon_id']
        _,label,lineup,interval = ROUTES[key]['waves'][run['stage']]
        templates = []
        for index,(name,shape,blows,credit) in enumerate(lineup):
            attack = max(1,hero['hpmax']//(32 if index==0 else 48))
            stats = dict(hpmax=max(15,hero['attack']*blows),attack=attack,defense=0,mpmax=160,
                         initiative=max(1,hero['initiative']*2//3),bell_reward=credit)
            if index==0 and interval:
                stats.update(skills={63:1},enemy_spell_percent=100,enemy_spell_interval=interval,
                             local_spell_damage=attack*2)
            templates.append(dict(type=shape,name=name,guardian_type=0,combat_stats=stats))
        zone = dict(scene_name=RECORDS[key]['Name'],levels=[hero['level']]*2,enemies=templates,
                    fixed_roster=True,entry_notice=f'第{run["stage"]+1}场：{label}（本机战斗规则）。')
        if key==25:
            from native946_zhuziguo_bell import MECHANISM
            zone['dungeon_mechanism'] = MECHANISM
        super().__init__(game,[game],zone)

    def settle(self, db, victory):
        if self.state['settled']:
            return
        game = self.games[self.leader]
        cid = self.leader
        key = self.run['dungeon_id']
        if victory and read_run(db,cid,key)==self.run:
            db.execute('UPDATE native_dungeon_route_progress SET stage=stage+1 WHERE instance_id=? AND character_id=?',
                       (self.run['instance_id'],cid))
            if key==26 and self.run['stage']==0:
                digits = tuple(randbelow(9)+1 for _ in range(3))
                clue = '{'+','.join(map(str,digits))+'}'
                db.execute('INSERT INTO native_tianpeng_puzzles VALUES(?,?,?,?,?,?,?,0)',
                           (self.run['instance_id'],cid,game.character['map_id'],clue,*digits))
            self.state['route_advanced'] = True
        for pet in self.state['participants'][cid].values():
            game.summons.settle(pet['id'],self.id,pet['hp'],pet['mp'],victory,experience=0)
        self.state['settled'] = True

    def finish_messages(self, cid, victory):
        if not self.state.get('route_advanced'):
            return (text_notice('当前进度已保留，可恢复后重试。'),)
        if self.run['stage']+1==len(ROUTES[self.run['dungeon_id']]['waves']):
            return (text_notice('已通关，请向副本执事领取奖励。'),)
        return (text_notice('本阶段已完成，请与副本执事交谈。'),)
