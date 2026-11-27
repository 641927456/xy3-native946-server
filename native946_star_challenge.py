import json
from functools import lru_cache
from random import Random
from time import monotonic, time

from native946_daily_content import DATA
from native946_codec import packet, screen_notice, text_notice
from native946_npc_quests import enter_quest_battle, numbers, spawn_position
from native946_temp_inventory import award
from native946_wilderness import WildBattle


RULE = DATA['stars']
ROSTER = []
for row in RULE['roster']:
    stats = dict(row['combat_stats'])
    stats['skills'] = {int(skill): points for skill, points in stats['skills'].items()}
    ROSTER.append(dict(row, combat_stats=stats))

NPC_BASE = 8_200_000
NPC_RANGE = 100_000


def is_star(nid):
    return NPC_BASE <= nid < NPC_BASE + NPC_RANGE


@lru_cache(maxsize=24)
def spawn(hour):
    scene = RULE['scene']
    key = f'star:{scene}:{hour}'
    x, y = spawn_position(scene, Random(key))
    return dict(npc=NPC_BASE + hour % NPC_RANGE, key=key, scene=scene, x=x, y=y, hour=hour)


def near(game, position, radius):
    role = game.character
    return role['map_id'] == position['scene'] and max(abs(role['x'] - position['x']),
                                                       abs(role['y'] - position['y'])) <= radius


def claimed(db, key):
    return db.execute('SELECT 1 FROM native_encounter_rewards WHERE battle_id=? LIMIT 1', (key,)).fetchone() is not None


class StarChallenge:
    def __init__(self, game):
        self.game = game

    def available(self, radius=None):
        now = int(time())
        if now % 3600 >= RULE['window_minutes'] * 60:
            return None
        row = spawn(now // 3600)
        if radius is not None and not near(self.game, row, radius):
            return None
        with self.game.progression.transaction(write=False) as db:
            if claimed(db, row['key']):
                return None
        return row

    def scene_packets(self, force=False):
        game = self.game
        scene = game.character['map_id']
        if scene != RULE['scene'] and not getattr(game, '_star_visible', None):
            return ()
        row = self.available(72) if scene == RULE['scene'] else None
        shown = getattr(game, '_star_visible', None)
        current = row['npc'] if row else None
        game._star_visible = current
        replies = []
        if shown is not None and shown != current and not force:
            replies.append(numbers(1604, shown))
        if row and (force or current != shown):
            replies.append(packet(1589, ['oci_npc'], [dict(
                id=current, name=RULE['name'], title='星宿挑战', type=RULE['roster'][0]['type'],
                class_type=1, x=row['x'] * 20, y=row['y'] * 20, dir=4, layer=1, member_click=1)]))
        return tuple(replies)

    def tick(self):
        now = monotonic()
        if now < getattr(self.game, '_star_poll_at', 0):
            return ()
        self.game._star_poll_at = now + 5
        return self.scene_packets()

    def target(self, nid):
        row = self.available(12)
        if row is None or nid != row['npc']:
            raise ValueError('星宿已离开、已被击败，或你尚未走到它附近。')
        return row

    def look(self, nid):
        self.target(nid)
        self.game.dialog_actions = [dict(kind='star_challenge')]
        return self.game.menu(
            f"{RULE['name']}。{RULE['level']}级可单人或组队挑战。#r编队参考阿飞，属性和掉落为本机规则。",
            ['挑战星宿'], nid)

    def start(self, nid):
        game = self.game
        target = self.target(nid)
        games = game.party_service.participants(game)
        if not games or any(not near(member, target, 12) for member in games):
            raise ValueError('请由队长带领在场队员走到星宿附近。')
        if any(member.progression.snapshot()['level'] < RULE['level'] for member in games):
            raise ValueError(f"每位参战者都须达到{RULE['level']}级。")
        if any(getattr(battle, 'star_key', None) == target['key'] for battle in game.party_service.battles.values()):
            raise ValueError('已有队伍在挑战这个星宿。')
        return enter_quest_battle(game, games, lambda: StarBattle(game, games, target))

    def location(self, callback):
        row = self.available()
        if row is None:
            return self.game.menu('本机星宿每到整点在傲来国刷新，保留15分钟，每次刷新只结算一次。', npc_id=callback)
        from native946_navigation import npc_auto_trace

        return (packet(1183, ['string'], ['dlg_npc_chat']),
                npc_auto_trace(row['scene'], row['x'], row['y'], row['npc'], RULE['name']))


class StarBattle(WildBattle):
    def __init__(self, leader, games, target):
        self.star_key = target['key']
        super().__init__(leader, games, dict(scene_name=RULE['name'], levels=[RULE['level']] * 2,
                                           enemies=ROSTER, fixed_roster=True))

    def keeps_captured_guardian(self):
        return False

    def settle(self, db, victory):
        if self.state['settled']:
            return
        valid = victory and not claimed(db, self.star_key)
        for cid, game in self.games.items():
            xp = RULE['experience'] if valid else 0
            gold = RULE['cash'] if valid else 0
            loot = Random(f'{self.star_key}:{cid}').choice(RULE['drops']) if valid else None
            reward = dict(victory=bool(valid), experience=xp, gold=gold, guardian_experience=xp,
                          drops={} if loot is None else {loot: 1}, policy=RULE['version'])
            if valid:
                from native946_constellations import DATA as STAR_DATA, win
                win(db,cid,STAR_DATA['natural']['1'],self.star_key,reward['drops'])
                db.execute('INSERT INTO native_encounter_rewards VALUES(?,?,?)',
                           (self.star_key, cid, json.dumps(reward, ensure_ascii=False)))
                game.characters.settle_battle(game.character['owner'], cid, f'{self.star_key}:{cid}', True,
                                              experience=xp, gold=gold, advance_quest=False)
                game.auto_level()
                award(db, game.progression, self.star_key, reward['drops'], offline=not self.service.online(cid))
            for pet in self.state['participants'][cid].values():
                game.summons.settle(pet['id'], self.id, pet['hp'], pet['mp'], valid, experience=xp)
            self.state.setdefault('rewards', {})[cid] = reward
        self.state['settled'] = True

    def finish_messages(self, cid, victory):
        reward = self.state.get('rewards', {}).get(cid, {})
        if not reward.get('victory'):
            return (text_notice('已离开星宿战斗，本次未结算奖励。'),)
        return (screen_notice(f"星宿挑战完成，经验＋{reward['experience']}，现金＋{reward['gold']}。"
                            '道具已发放，背包满时请查看摘星袋。'),)
