"""946 team-platform settings and same-server task matching."""

from dataclasses import dataclass
from time import monotonic, time

from native946_codec import packet, text_notice
from native946_progression import lua_data
from native946_role_systems import grade, save, state
from native946_storage import StorageBusy, transaction

REQUESTS = frozenset((4422,))
# Client configs.wdf/07a8590a, TaskTypeConfig and DungeonTaskConfig.
TASKS = {
    1: ('官职任务', 100), 2: ('捉鬼任务', 80), 3: ('鬼王任务', 90),
    4: ('万妖塔', 100), 5: ('百年后的来客', 80), 6: ('神器试炼', 1110),
    7: ('强袭七星宫', 100), 8: ('降魔挑战', 100),
    1001: ('重返车迟国', 112165), 1002: ('天蓬问情', 112165),
    1003: ('朱紫秘辛', 12180), 1004: ('白夜双生', 12180), 1005: ('玉华州伏狮', 12180),
}
MATCH_TIMEOUT = 3600
MAX_TARGETS = 5
NUMBERS = ('array', 'number')
DEFAULTS = {'tasks': [], 'auto': 0, 'reminder': 1}


@dataclass(frozen=True)
class Entry:
    cid: int
    tasks: tuple
    team_id: int
    members: frozenset
    volunteer: int
    began: int
    expires: float


class Platform:
    def __init__(self, service):
        self.service = service
        if not hasattr(service, 'match_entries'):
            service.match_entries = {}
        self.entries = service.match_entries

    def status(self, team, cid):
        entry = self.entries.get(team['leader_id'] if team else cid)
        if entry is None:
            return packet(4418, [NUMBERS, 'number', 'number'], [[], 0, 0])
        return packet(4418, [NUMBERS, 'number', 'number'],
                      [entry.tasks, int(entry.team_id != 0 or entry.volunteer == 1), entry.began])

    def notify_status(self, team, cid):
        status = self.status(team, cid)
        members = [row['character_id'] for row in team['members']] if team else [cid]
        for member in members:
            game = self.service.online(member)
            if game is not None:
                self.service.enqueue(game, status)

    def cancel(self, cid):
        entry = self.entries.pop(cid, None)
        if entry is not None:
            for member in entry.members:
                game = self.service.online(member)
                if game is not None:
                    self.service.enqueue(game, self.status(self.service.team(member), member))

    def preferences(self, db, cid):
        return state(db, cid, 'team_platform', DEFAULTS)

    def task_info(self, grade_value, settings):
        return {task: {'Select': int(task in settings['tasks'])}
                for task, (_, minimum) in TASKS.items() if grade_value >= minimum}

    def validate_tasks(self, tasks, grade_value):
        if not 1 <= len(tasks) <= MAX_TARGETS or len(set(tasks)) != len(tasks):
            raise ValueError('请选择1到5个不同的匹配目标。')
        if any(task not in TASKS or TASKS[task][1] > grade_value for task in tasks):
            raise ValueError('请选择当前角色等级符合的组队目标。')

    def receive(self, game, reader):
        op = reader.integer()
        args = [reader.integer() for _ in range(reader.take(1)[0])]
        reader.finish()
        cid = game.character['id']
        team = self.service.team(cid)
        is_leader = team is not None and team['leader_id'] == cid
        if op in (1, 2, 3, 5, 10, 11, 12, 13, 14, 15, 21):
            with transaction(self.service.database, write=op in (14, 15, 21)) as db:
                settings = self.preferences(db, cid)
                grade_value = grade(db, cid)
                if op in (14, 15, 21):
                    if op in (14, 15) and not is_leader:
                        raise ValueError('只有队长可以调整队伍匹配设置。')
                    if op == 15:
                        if cid in self.entries:
                            raise ValueError('请先取消匹配，再调整队伍目标。')
                        self.validate_tasks(args, grade_value)
                        settings['tasks'] = args
                    else:
                        if args not in ([0], [1]):
                            raise ValueError('组队选项无效。')
                        settings['auto' if op == 14 else 'reminder'] = args[0]
                    save(db, cid, 'team_platform', settings)
        if op in (1, 5, 15):
            if op in (5, 15) and not is_leader:
                raise ValueError('只有队长可以打开队伍匹配设置。')
            info = lua_data(self.task_info(grade_value, settings))
            if op == 1:
                return (packet(4421, ['string'] + ['number'] * 8,
                               [info, int(is_leader), 0, 0, 0, 0, 0, settings['reminder'], 0]),
                        self.status(team, cid))
            if op == 15:
                self.service.publish(team)
                if settings['auto']:
                    return self.start(game, team, settings['tasks'], 1)
                return (text_notice('队伍匹配目标已保存。'),)
            return (packet(4420, ['string'] + ['number'] * 6,
                           [info, settings['auto'], 0, 0, 0, 0, settings['reminder']]),)
        if op == 2:
            if len(args) != 1 or args[0] not in TASKS:
                raise ValueError('组队目标无效。')
            entries = [entry for entry in self.entries.values() if args[0] in entry.tasks]
            leaders = sum(bool(entry.team_id or entry.volunteer) for entry in entries)
            return (packet(4416, ['number'] * 3, [args[0], leaders, len(entries) - leaders]),)
        if op == 3:
            return (self.status(team, cid),)
        if op in (10, 11):
            if op == 10 and (not args or args[0] not in (0, 1)):
                raise ValueError('请选择有效的匹配身份。')
            if (op == 10 and team) or (op == 11 and not is_leader):
                raise ValueError('请以单人身份或由队长发起匹配。')
            tasks = args[1:] if op == 10 else settings['tasks']
            self.validate_tasks(tasks, grade_value)
            return self.start(game, team, tasks, args[0] if op == 10 else 1)
        if op in (12, 13):
            if team and not is_leader:
                raise ValueError('只有队长可以取消队伍匹配。')
            if cid in self.entries:
                self.cancel(cid)
                return ()
            return (self.status(team, cid),)
        if op in (14, 21):
            return (text_notice('组队匹配选项已保存。'),)
        raise ValueError('当前本机开放同服组队匹配，该扩展操作暂不可用。')

    def start(self, game, team, tasks, volunteer):
        cid = game.character['id']
        if game.battle:
            raise ValueError('请先结束战斗再开始匹配。')
        if team:
            self.service.stable(team)
            if len(team['members']) >= 5 or not self.ready(team):
                raise ValueError('匹配需要少于五名队员，且现有队员须在线并归队。')
            with transaction(self.service.database) as db:
                self.validate_tasks(tasks, min(grade(db, row['character_id']) for row in team['members']))
        entry = self.entries.get(cid)
        if entry is None:
            members = frozenset(row['character_id'] for row in team['members']) if team else frozenset((cid,))
            self.entries[cid] = Entry(cid, tuple(tasks), team['id'] if team else 0, members,
                                      volunteer, int(time()), monotonic() + MATCH_TIMEOUT)
            self.notify_status(team, cid)
            self.match()
        return ()

    def ready(self, team):
        return all(not row['away'] and (game := self.service.online(row['character_id'])) is not None
                   and not game.battle for row in team['members'])

    def match(self):
        now = monotonic()
        ready = []
        for entry in tuple(self.entries.values()):
            game = self.service.online(entry.cid)
            team = self.service.team(entry.cid)
            if team is None:
                valid_team = not entry.team_id
            else:
                members = {row['character_id'] for row in team['members']}
                valid_team = team['id'] == entry.team_id and team['leader_id'] == entry.cid and members == entry.members
            if game is None or entry.expires <= now or not valid_team:
                self.cancel(entry.cid)
            elif not game.battle and (team is None or self.ready(team)):
                ready.append((entry, team))
        solos = [entry for entry, team in ready if team is None]
        # Existing parties retain their leader. Among solo entries the
        # earliest volunteer takes the lead, with ordinary entries as fallback.
        ready.sort(key=lambda value: (not value[0].team_id, not value[0].volunteer))
        for leader, team in ready:
            if leader.cid not in self.entries:
                continue
            for task in leader.tasks:
                recruits = [entry for entry in solos if entry.cid != leader.cid and
                            entry.cid in self.entries and task in entry.tasks]
                seats = 5 - (len(team['members']) if team else 1)
                if len(recruits) >= seats:
                    self.form(leader, recruits[:seats], task)
                    break

    def form(self, leader, recruits, task):
        with transaction(self.service.database, write=True, batchable=True) as db:
            team = self.service.team(leader.cid, db)
            if team is None:
                team = self.service._create(db, leader.cid)
            settings = self.preferences(db, leader.cid)
            settings['tasks'] = [task]
            save(db, leader.cid, 'team_platform', settings)
            for entry in recruits:
                team = self.service._join(db, team, entry.cid)

        for entry in (leader, *recruits):
            self.entries.pop(entry.cid, None)
        normalized = {row['character_id'] for row in team['members']}
        self.service.invitations = {key: end for key, end in self.service.invitations.items() if key[1] not in normalized}
        self.service.applications = {key: end for key, end in self.service.applications.items() if key[1] not in normalized}
        from native946_party import numbers
        events = (numbers(2025, leader.cid), *(numbers(2026, leader.cid, entry.cid) for entry in recruits))
        ready = packet(4417, ['string'], [TASKS[task][0]])
        self.service.publish(team, extras=(*events, self.status(team, leader.cid), ready,
                                           text_notice(f"组队匹配成功。目标为{TASKS[task][0]}。")))
        try:
            self.service.follow_team(team)
        except StorageBusy:
            self.service.pending_follow.add(team['id'])
