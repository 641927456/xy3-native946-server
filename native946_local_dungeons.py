"""Solo local dungeon instances, using the existing 946 dialogue and battle UI."""

from copy import deepcopy
from secrets import token_hex

from native946_codec import screen_notice, text_notice
from native946_role_systems import day, save, state
from native946_wilderness import WildBattle


SOURCE = '本机规则'
STORE = 'local_dungeons'

# Each wave gives a title, a roster of (label, client shape, health in
# ordinary attacks), and the casting interval of the first actor.
DUNGEONS = {
    'pine_drill': {
        'name': '松风演练',
        'level': 1,
        'experience': 160,
        'gold': 40,
        'waves': (
            ('巡场', (('木牌阵兵', 2108, 1), ('木牌阵兵', 2107, 1)), 0),
            ('校阵', (('鸣哨阵兵', 2052, 3), ('护旗阵兵', 2107, 1)), 3),
            ('守旗', (('守旗阵兵', 2090, 5), ('持旗阵兵', 2108, 2)), 2),
        ),
    },
    'echo_court': {
        'name': '回声试场',
        'level': 30,
        'experience': 560,
        'gold': 100,
        'waves': (
            ('试音', (('引音兽', 3196, 2), ('回音兽', 2090, 2)), 3),
            ('稳阵', (('护铃兽', 3327, 3), ('循声兽', 3196, 2)), 0),
            ('止鸣', (('鸣阵兽', 2090, 6), ('回音兽', 3196, 2)), 2),
        ),
    },
}


class LocalDungeons:
    def __init__(self, game):
        self.game = game
        self.progression = game.progression

    def menu(self, text, choices):
        from native946_activities import Activities
        return Activities(self.game)._menu(text, choices)

    def read(self, db):
        return state(db, self.progression.cid, STORE, {'run': None, 'clears': {}})

    def listing(self):
        with self.progression.transaction(write=False) as db:
            record = self.read(db)
        choices = []
        for key, design in DUNGEONS.items():
            label = design['name']
            if record['run'] and record['run']['key'] == key:
                label += '（进行中）'
            elif record['clears'].get(key, {}).get('day') == day():
                label += '（今日已结算）'
            choices.append((label, dict(op='local_dungeon_detail', key=key)))
        choices.append(('返回副本目录', dict(op='dungeon_list', page=0)))
        return self.menu('本机原创副本。单人参加，每个副本每天可结算一次。', choices)

    def detail(self, key):
        design = DUNGEONS[key]
        with self.progression.transaction(write=False) as db:
            record = self.read(db)
        run = record['run']
        receipt = record['clears'].get(key)
        text = f"{design['name']}（{SOURCE}）#r需要{design['level']}级。共{len(design['waves'])}场战斗。"
        choices = []
        if run and run['key'] == key:
            wave = design['waves'][run['wave']][0]
            text += f"#r当前进度：第{run['wave'] + 1}场「{wave}」。"
            args = dict(key=key, run_id=run['id'], wave=run['wave'])
            if run['entered']:
                choices.append((f'挑战「{wave}」', dict(op='local_dungeon_fight', **args)))
                choices.append(('退出副本并保留进度', dict(op='local_dungeon_leave', **args)))
            else:
                choices.append(('进入副本', dict(op='local_dungeon_enter', **args)))
            choices.append(('放弃任务', dict(op='local_dungeon_confirm', **args)))
        elif receipt and receipt['day'] == day():
            text += f"#r今日已通关。获得经验{receipt['experience']}，现金{receipt['gold']}。"
        elif run is None:
            text += f"#r通关奖励：经验{design['experience']}，现金{design['gold']}。"
            choices.append(('领取副本任务', dict(op='local_dungeon_accept', key=key)))
        else:
            text += '#r请先完成或放弃当前副本任务。'
        choices.append(('返回本机副本', dict(op='local_dungeon_list')))
        return self.menu(text, choices)

    def execute(self, action):
        op = action['op']
        if self.game.battle:
            raise ValueError('请先结束当前战斗。')
        if op == 'local_dungeon_list':
            return self.listing()
        key = action.get('key')
        if key not in DUNGEONS:
            raise ValueError('本机副本编号无效。')
        if op == 'local_dungeon_detail':
            return self.detail(key)
        if op == 'local_dungeon_confirm':
            return self.menu('放弃后将清除当前副本进度。', [
                ('确认放弃', dict(op='local_dungeon_abandon', key=key,
                                run_id=action['run_id'], wave=action['wave'])),
                ('返回当前副本', dict(op='local_dungeon_detail', key=key)),
            ])
        if op == 'local_dungeon_fight':
            return self.game.defer_packets(lambda: self.start(action))

        with self.progression.transaction() as db:
            record = self.read(db)
            if op == 'local_dungeon_accept':
                if record['run'] or record['clears'].get(key, {}).get('day') == day():
                    raise ValueError('已有进行中的副本，或本副本今日已结算。')
                snapshot = self.progression._snapshot(db)
                if snapshot['level'] < DUNGEONS[key]['level']:
                    raise ValueError('角色未达到这个副本的要求等级。')
                record['run'] = dict(id=token_hex(16), key=key, entered=False, wave=0)
            else:
                run = self.require_run(record, action)
                if op == 'local_dungeon_enter':
                    if self.game.party_service.team(self.progression.cid, db) is not None:
                        raise ValueError('请退出队伍后进入单人副本。')
                    run['entered'] = True
                elif op == 'local_dungeon_leave':
                    run['entered'] = False
                elif op == 'local_dungeon_abandon':
                    record['run'] = None
                else:
                    raise ValueError('本机副本操作无效。')
            save(db, self.progression.cid, STORE, record)
        return self.game.defer_packets(lambda: self.detail(key))

    def require_run(self, record, action):
        run = record['run']
        if not run or (run['key'], run['id'], run['wave']) != (
                action.get('key'), action.get('run_id'), action.get('wave')):
            raise ValueError('副本进度已变化，请重新打开入口。')
        return run

    def start(self, action):
        game = self.game
        service = game.party_service
        cid = self.progression.cid
        if game.battle or service.battles.get(cid):
            raise ValueError('当前角色已有战斗。')
        with self.progression.transaction(write=False) as db:
            run = self.require_run(self.read(db), action)
            if not run['entered'] or service.team(cid, db) is not None:
                raise ValueError('请先进入单人副本。')
            hero = self.progression._snapshot(db)
        if hero['hp'] <= 0:
            raise ValueError('请先恢复气血，再继续挑战。')
        saved = {name: deepcopy(getattr(game, name)) for name in
                 ('battle', 'dialog_open', 'dialog_npc', 'dialog_actions')}
        try:
            battle = LocalDungeonBattle(game, run, hero)
            messages = battle.entry(game)
        except BaseException:
            service.battles.pop(cid, None)
            game._wild_battle = None
            for name, value in saved.items():
                setattr(game, name, value)
            raise
        service.enqueue(game, *messages)
        return ()


class LocalDungeonBattle(WildBattle):
    def __init__(self, game, run, hero):
        self.run = dict(run)
        design = DUNGEONS[run['key']]
        self.wave_name, lineup, interval = design['waves'][run['wave']]
        enemies = []
        for index, (name, shape, blows) in enumerate(lineup):
            attack = max(1, hero['hpmax'] // (36 if index == 0 else 48))
            stats = dict(hpmax=max(12, hero['attack'] * blows), attack=attack, defense=0,
                         mpmax=100, initiative=max(1, hero['initiative'] * 2 // 3))
            if not index and interval:
                stats.update(skills={63: 1}, enemy_spell_percent=100,
                             enemy_spell_interval=interval, local_spell_damage=attack * 2)
            enemies.append(dict(type=shape, name=name, guardian_type=0, combat_stats=stats))
        super().__init__(game, [game], dict(scene_name=design['name'], levels=[hero['level']] * 2,
                         fixed_roster=True, enemies=enemies, entry_notice=f'开始挑战「{self.wave_name}」。'))

    def settle(self, db, victory):
        if self.state['settled']:
            return
        game = self.games[self.leader]
        service = LocalDungeons(game)
        record = service.read(db)
        run = record['run']
        if victory and run == self.run:
            design = DUNGEONS[run['key']]
            run['wave'] += 1
            if run['wave'] == len(design['waves']):
                receipt = dict(day=day(), experience=design['experience'], gold=design['gold'])
                receipt_id = 'local-dungeon:' + run['id']
                game.characters.settle_battle(game.character['owner'], self.leader, receipt_id, True,
                                              experience=receipt['experience'], gold=receipt['gold'],
                                              advance_quest=False)
                record['clears'][run['key']] = receipt
                record['run'] = None
                game.auto_level()
                self.state['rewards'] = {self.leader: receipt}
            save(db, self.leader, STORE, record)
            self.state['local_dungeon_advanced'] = True
        for pet in self.state['participants'][self.leader].values():
            game.summons.settle(pet['id'], self.id, pet['hp'], pet['mp'], victory, experience=0)
        self.state['settled'] = True

    def finish_messages(self, cid, victory):
        if self.state.get('rewards'):
            receipt = self.state['rewards'][cid]
            return (screen_notice(f"通关结算：经验{receipt['experience']}，现金{receipt['gold']}。"),)
        if self.state.get('local_dungeon_advanced'):
            return (text_notice('本场胜利，下一场已解锁。'),)
        return (text_notice('副本进度已保留。恢复后可重试。'),)

    def finish(self, victory):
        if self.state['phase'] == 'finished':
            return
        super().finish(victory)
        game = self.games[self.leader]
        if self.service.online(self.leader) is game:
            from native946_storage import StorageBusy
            try:
                self.service.enqueue(game, LocalDungeons(game).detail(self.run['key']))
            except StorageBusy:
                game._party_refresh_pending = True
