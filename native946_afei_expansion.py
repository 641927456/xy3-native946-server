"""Afei-inspired local encounters served through the 946 battle and NPC protocol."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
from pathlib import Path
from random import Random
from time import monotonic, time

from native946_codec import packet, screen_notice, text_notice
from native946_npc_quests import ACTIVITIES, Quests, day_key, enter_quest_battle, numbers, reference_roster, reward, spawn_position
from native946_temp_inventory import award
from native946_wilderness import WildBattle
from native946_world import world


DATA = json.loads(Path(__file__).with_name('native946_afei_expansion_data.json').read_text('utf8'))
EVENTS = DATA['events']
AUTO_GHOST = DATA['auto_ghost']
NPC_BASE = 8_400_000
STRIDE = 4_000
HK = timezone(timedelta(hours=8))
CLOSE = packet(1183, ['string'], ['dlg_npc_chat'])
DAY_LABELS = '一二三四五六日'

ROSTERS = []
for event in EVENTS:
    roster = []
    for row in event['roster']:
        stats = dict(row['combat_stats'])
        stats['skills'] = dict.fromkeys(stats['skills'], 1)
        roster.append(dict(row, combat_stats=stats))
    ROSTERS.append(roster)

EVENTS_BY_SCENE = {}
CALENDAR_EVENTS = []
STAR_EVENTS = []
for event_id, event in enumerate(EVENTS):
    (STAR_EVENTS if 'star_slot' in event else CALENDAR_EVENTS).append(event_id)
    for scene in event['scenes']:
        EVENTS_BY_SCENE.setdefault(scene, []).append(event_id)


def is_encounter_npc(nid):
    return NPC_BASE <= nid < NPC_BASE + len(EVENTS) * STRIDE


def window(event, now):
    """Return the latest valid start time, including windows spanning hours."""
    hour = now // 3600
    for hour_index in range(hour, hour - (event['seconds'] + 3599) // 3600 - 1, -1):
        start = hour_index * 3600
        local = datetime.fromtimestamp(start, HK)
        minute_starts = [
            start + 60 * m
            for rule in event['timing'] if local.weekday() in rule['days'] and local.hour in rule['hours']
            for m in rule['minutes']
            if start + 60 * m <= now < start + 60 * m + event['seconds']
        ]
        if minute_starts:
            return max(minute_starts)
    return None


@lru_cache(maxsize=256)
def spawn(event_id, scene, started):
    event = EVENTS[event_id]
    site = event['scenes'].index(scene)
    npc = NPC_BASE + event_id * STRIDE + site * 1000 + (started // 60) % 1000
    key = f'afei-event:{event_id}:{scene}:{started}'
    seed = key if event.get('mode') != 'personal_daily' else f'afei-cave:{scene}'
    x, y = spawn_position(scene, Random(seed))
    return dict(event=event_id, scene=scene, key=key, x=x, y=y, npc=npc)


def at(game, spot, radius):
    c = game.character
    return c['map_id'] == spot['scene'] and max(abs(c['x'] - spot['x']), abs(c['y'] - spot['y'])) <= radius


def already_claimed(db, key):
    return db.execute('SELECT 1 FROM native_encounter_rewards WHERE battle_id=? LIMIT 1', (key,)).fetchone() is not None


class AfeiWorld:
    def __init__(self, game):
        self.g = game

    def spots(self, scene, radius=None):
        ids = EVENTS_BY_SCENE.get(scene, ())
        if not ids:
            return []
        now = int(time())
        candidates = []
        for index in ids:
            event = EVENTS[index]
            personal = event.get('mode') == 'personal_daily'
            started = int(datetime.fromtimestamp(now, HK).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()) if personal else window(event, now)
            if started is None:
                continue
            spot = dict(spawn(index, scene, started))
            if personal:
                spot['key'] += f":{self.g.progression.cid}"
            if radius is None or at(self.g, spot, radius):
                candidates.append(spot)
        if not candidates:
            return []
        with self.g.progression.transaction(write=False) as db:
            return [row for row in candidates if not already_claimed(db, row['key'])]

    def scene_packets(self, force=False):
        previous = getattr(self.g, '_afei_visible', set())
        scene = self.g.character['map_id']
        if scene not in EVENTS_BY_SCENE and not previous:
            return ()
        rows = self.spots(scene, 72)
        current = {spot['npc'] for spot in rows}
        self.g._afei_visible = current
        replies = [] if force else [numbers(1604, nid) for nid in sorted(previous - current)]
        for spot in rows:
            if force or spot['npc'] not in previous:
                event = EVENTS[spot['event']]
                title = '星宿挑战' if 'star_slot' in event else '阿飞参考活动'
                replies.append(packet(1589, ['oci_npc'], [dict(id=spot['npc'], name=event['name'], title=title,
                    type=event['roster'][0]['type'], class_type=1, x=spot['x']*20, y=spot['y']*20,
                    dir=4, layer=1, member_click=1)]))
        return tuple(replies)

    def tick(self):
        now = monotonic()
        if now < getattr(self.g, '_afei_poll_at', 0):
            return ()
        self.g._afei_poll_at = now + 5
        return self.scene_packets()

    def target(self, nid):
        spot = next((s for s in self.spots(self.g.character['map_id'], 12) if s['npc'] == nid), None)
        if spot is None:
            raise ValueError('目标已离开、已结算，或距离太远。')
        return spot

    def look(self, nid):
        spot = self.target(nid)
        event = EVENTS[spot['event']]
        self.g.dialog_actions = [dict(kind='afei_encounter', key=spot['key'])]
        details = '单人，每个角色每天结算一次。' if event.get('mode') else '当前刷新点只结算一次。'
        return self.g.menu(event['name']+'。'+details+'阿飞参考编队及结算，本机缩放和已核道具奖池。',
                           ['开始挑战'], nid)

    def start(self, nid, key):
        spot = self.target(nid)
        if key != spot['key']:
            raise ValueError('活动已刷新，请重新交谈。')
        e = EVENTS[spot['event']]
        g = self.g
        games = g.party_service.participants(g)
        if not games or any(not at(member, spot, 12) for member in games):
            raise ValueError('请由队长带队走到活动目标附近。')
        if e.get('mode') and g.party_service.team(g.progression.cid) is not None:
            raise ValueError('本机山洞探秘请单人参加。')
        minimum = e['level']
        if g.party_service.team(g.progression.cid) is not None:
            if len(games) < e['team_min']:
                raise ValueError(f"组队时在场队员至少需要{e['team_min']}人。")
            minimum = e['team_level']
        if any(p.progression.snapshot()['level'] < minimum for p in games):
            raise ValueError(f'所有参战者至少{minimum}级。')
        if any(getattr(battle, 'afei_key', None) == key for battle in g.party_service.battles.values()):
            raise ValueError('已经有队伍在挑战。')
        return enter_quest_battle(g, games, lambda: AfeiBattle(g, games, spot))

    def list_menu(self, menu, page, stars=False):
        size = 6
        group = STAR_EVENTS if stars else CALENDAR_EVENTS
        if type(page) is not int or not 0 <= page < (len(group)+size-1)//size:
            raise ValueError('没有这一页活动。')
        choices = []
        for index in group[page*size:(page+1)*size]:
            event = EVENTS[index]
            label = event['name']
            if stars:
                label += ' · '+world().scene(event['scenes'][0])['scene_name']
            choices.append((label, dict(op='afei_detail', event=index)))
        if page:
            choices.append(('上一页', dict(op='afei_list', page=page-1, stars=stars)))
        if (page+1)*size < len(group):
            choices.append(('下一页', dict(op='afei_list', page=page+1, stars=stars)))
        choices.append(('返回星宿挑战', dict(op='detail', id=81)) if stars else ('返回活动目录', dict(op='root')))
        text = '星宿每个整点刷新，保留15分钟，50级可挑战。' if stars else '阿飞参考活动。地图及显示使用946资源，规则详见各活动。'
        return menu(text, choices)

    def detail(self, menu, index):
        if type(index) is not int or not 0 <= index < len(EVENTS):
            raise ValueError('活动无效。')
        event = EVENTS[index]
        stars = 'star_slot' in event
        info = event['name']
        info += '#r场景：'+'、'.join(world().scene(s)['scene_name'] for s in event['scenes'])
        if event.get('mode'):
            info += '#r全天可进入百灵塔-午未合，在场景找山洞探秘，每角色每日一次。'
        for rule in event['timing']:
            days = '每天' if len(rule['days']) == 7 else '/'.join('周'+DAY_LABELS[i] for i in rule['days'])
            hours = rule['hours']
            mins = '、'.join(f'{m:02d}' for m in rule['minutes'])
            info += f'#r{days} {hours[0]:02d}–{hours[-1]:02d}时，{mins}分刷新（香港时间）。'
        if event['team_min'] > 1:
            info += f"#r单人{event['level']}级，组队至少{event['team_min']}人且每人{event['team_level']}级。"
        if stars:
            info += '#r50级可单人或组队挑战。编队参考阿飞，属性及道具奖池为本机规则。'
        info += f"#r阿飞经验{event['experience']}，现金{event['cash']}。"
        if not stars:
            info += '使用本机缩放后的编队及已映射奖池。'
        choices = [(f"前往{world().scene(scene)['scene_name']}的目标", dict(op='afei_location', event=index, scene=scene))
                   for scene in event['scenes'] if any(s['event'] == index for s in self.spots(scene))]
        group = STAR_EVENTS if stars else CALENDAR_EVENTS
        choices.append(('返回星宿列表' if stars else '返回阿飞活动列表',
                        dict(op='afei_list', page=group.index(index)//6, stars=stars)))
        return menu(info, choices)

    def location(self, index, scene):
        spot = next((row for row in self.spots(scene) if row['event'] == index), None)
        if spot is None:
            raise ValueError('本场目标还未刷新或已经结束。')
        from native946_navigation import npc_auto_trace
        return CLOSE, npc_auto_trace(scene, spot['x'], spot['y'], spot['npc'], EVENTS[index]['name'])


class AfeiBattle(WildBattle):
    def __init__(self, leader, games, spot, *, event=None, roster=None):
        if event is None:
            event, roster = EVENTS[spot['event']], ROSTERS[spot['event']]
        self.event = event
        self.afei_key = spot['key']
        level = max(1, event['level'])
        zone = dict(scene_name=event['name'], levels=[level, level], enemies=roster, fixed_roster=True)
        if 'enemy_layout' in event:
            zone['enemy_layout'] = event['enemy_layout']
        super().__init__(leader, games, zone)

    def keeps_captured_guardian(self):
        return False

    def settle(self, db, victory):
        if self.state['settled']:
            return
        valid = bool(victory and not already_claimed(db, self.afei_key))
        for cid, game in self.games.items():
            xp = self.event['experience'] if valid else 0
            cash = self.event['cash'] if valid else 0
            drops = {Random(f'{self.afei_key}:{cid}').choice(self.event['drops']): 1} if valid else {}
            result = dict(victory=valid, experience=xp, gold=cash, guardian_experience=xp, drops=drops,
                          reference_encounter=self.event['reference_encounter'],
                          policy=self.event.get('policy', 'afei-local-events-v1'))
            if valid:
                from native946_constellations import DATA as STAR_DATA, SIGILS, win
                if 'star_sigil' in self.event:
                    kind = self.event['star_sigil']
                    win(db,cid,SIGILS[kind],self.afei_key,drops,summoned=True,item_type=kind)
                elif 'star_slot' in self.event:
                    win(db,cid,STAR_DATA['natural'][str(self.event['star_slot'])],self.afei_key,drops)
                pay(db, game, self.afei_key, result, offline=self.service.online(cid) is None)
            for pet in self.state['participants'][cid].values():
                game.summons.settle(pet['id'], self.id, pet['hp'], pet['mp'], valid, experience=xp)
            self.state.setdefault('rewards', {})[cid] = result
        self.state['settled'] = True

    def finish_messages(self, cid, victory):
        return settlement_notice(self.event['name'], self.state.get('rewards', {}).get(cid, {}))


def pay(db, game, key, result, *, offline):
    cid = game.progression.cid
    db.execute('INSERT INTO native_encounter_rewards VALUES(?,?,?)',
               (key, cid, json.dumps(result, ensure_ascii=False)))
    game.characters.settle_battle(game.character['owner'], cid, f'{key}:{cid}', True,
                                  experience=result['experience'], gold=result['gold'], advance_quest=False)
    game.auto_level()
    award(db, game.progression, key, result['drops'], offline=offline)


def settlement_notice(name, result):
    if not result.get('victory'):
        return (text_notice(f'{name}未结算奖励。'),)
    return (screen_notice(f"{name}完成，经验＋{result['experience']}，现金＋{result['gold']}。"
                        '背包满时请查看摘星袋。'),)


def ghost_options(game):
    if getattr(game, '_auto_ghost_until', 0) > monotonic():
        return [('停止自动抓鬼', dict(kind='auto_ghost', hours=0))]
    return [(f'开启{hours}小时自动抓鬼（本机）', dict(kind='auto_ghost', hours=hours)) for hours in AUTO_GHOST['hours']]


def set_ghost(game, nid, hours):
    Quests(game).validate_giver(nid, 'ghost')
    if game.battle:
        raise ValueError('请先结束当前战斗。')
    if hours == 0:
        game._auto_ghost_until = 0
        return CLOSE, text_notice('已停止自动抓鬼。')
    if type(hours) is not int or hours not in AUTO_GHOST['hours']:
        raise ValueError('自动抓鬼时长无效。')
    if game.progression.snapshot()['level'] < AUTO_GHOST['level']:
        raise ValueError('80级起可开启本机自动抓鬼。')
    if game.party_service.team(game.progression.cid) or Quests(game).active('ghost'):
        raise ValueError('请单人使用，并先完成或取消当前手动抓鬼任务。')
    game._auto_ghost_until = monotonic() + hours*3600
    game._auto_ghost_next = monotonic() + AUTO_GHOST['delay']
    return CLOSE, text_notice(f'已开启{hours}小时自动抓鬼。原地胜利后续战，离场、组队、败退或断线即停止。')


def ghost_progress(db, cid, day):
    row = db.execute("SELECT day,accepted,next_ring FROM native_npc_activity_progress WHERE character_id=? AND kind='ghost'", (cid,)).fetchone()
    if row is None:
        return 0, 1
    return (row['accepted'] if row['day'] == day else 0), row['next_ring']


def ghost_tick(game):
    deadline = getattr(game, '_auto_ghost_until', 0)
    if not deadline:
        return ()
    now = monotonic()
    if now < getattr(game, '_auto_ghost_next', 0):
        return ()
    scene, nid = AUTO_GHOST['giver']
    npc = world().npc(scene, nid)
    spot = dict(scene=scene, x=npc['x']//20, y=npc['y']//20)
    if (now >= deadline or not at(game, spot, 12)
            or game.party_service.team(game.progression.cid) or Quests(game).active('ghost')
            or game.progression.snapshot()['hp'] <= 0):
        game._auto_ghost_until = 0
        return (text_notice('本机自动抓鬼已停止。'),)
    with game.progression.transaction(write=False) as db:
        accepted, ring = ghost_progress(db, game.progression.cid, day_key())
    if accepted >= ACTIVITIES['ghost']['limit']:
        game._auto_ghost_until = 0
        return (text_notice('今日抓鬼次数已用完。'),)
    game._auto_ghost_next = now + AUTO_GHOST['delay']
    return enter_quest_battle(game, [game], lambda: AutoGhostBattle(game, ring))


class AutoGhostBattle(WildBattle):
    def __init__(self, game, ring):
        self.ring = ring
        roster = reference_roster({'kind':'ghost','token':f'auto:{game.progression.cid}:{monotonic()}'}, 1)
        super().__init__(game, [game], dict(scene_name='自动抓鬼', levels=[50,50], enemies=roster, fixed_roster=True))

    def keeps_captured_guardian(self):
        return False

    def settle(self, db, victory):
        if self.state['settled']:
            return
        cid = self.leader
        game = self.games[cid]
        key = f'afei-auto-ghost:{self.id}'
        day = day_key()
        accepted, ring = ghost_progress(db, cid, day)
        valid = bool(victory and accepted < ACTIVITIES['ghost']['limit'] and not already_claimed(db, key))
        result = dict(victory=False, experience=0, gold=0, guardian_experience=0, drops={})
        if valid:
            from native946_equipment import DATA as DAILY_REWARDS
            from native946_progression import DATA as PROGRESSION_DATA
            value = reward('ghost', game.progression.snapshot()['level'], ring, Random(key))
            result.update(value)
            result['drops'] = {item: count for ref, count in value['drops'].items()
                if type(item := DAILY_REWARDS['reward_map'].get(str(ref))) is int and str(item) in PROGRESSION_DATA['items']}
            result['policy'] = 'afei-auto-ghost-current-daily-rewards-v1'
            db.execute("INSERT INTO native_npc_activity_progress(character_id,kind,day,accepted,completed,next_ring) "
                       "VALUES(?,'ghost',?,1,1,?) ON CONFLICT(character_id,kind) DO UPDATE SET "
                       "day=excluded.day, accepted=CASE WHEN day=excluded.day THEN accepted+1 ELSE 1 END, "
                       "completed=CASE WHEN day=excluded.day THEN completed+1 ELSE 1 END, next_ring=excluded.next_ring",
                       (cid, day, ring%10+1))
            pay(db, game, key, result, offline=self.service.online(cid) is None)
            self.state['ghost_victory'] = True
        for pet in self.state['participants'][cid].values():
            game.summons.settle(pet['id'], self.id, pet['hp'], pet['mp'], valid, experience=result['guardian_experience'])
        self.state.setdefault('rewards', {})[cid] = result
        self.state['settled'] = True

    def finish(self, victory):
        for game in self.games.values():
            if not self.state.get('ghost_victory'):
                game._auto_ghost_until = 0
            game._auto_ghost_next = monotonic() + AUTO_GHOST['delay']
        return super().finish(victory)

    def finish_messages(self, cid, victory):
        return settlement_notice('自动抓鬼', self.state.get('rewards', {}).get(cid, {}))
