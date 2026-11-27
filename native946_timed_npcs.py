"""Publish the 946 timed activity entrances on the ordinary server."""

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from functools import lru_cache
from random import Random

from native946_activities import RULES
from native946_codec import packet
from native946_world import EVENT_ROTATION_START, EVENT_TIMEZONE


TIMED_NPC_VERSION = 1


@dataclass(frozen=True)
class Entrance:
    nid: int
    scene: int
    position: tuple
    name: str
    title: str
    shape: int
    direction: int
    guide: str


def from_config(nid, config, guide):
    return Entrance(nid, config.get('NpcSceneId', 0), tuple(config.get('NpcPos', ())),
                    config['NpcName'], config['NpcTitle'], config['NpcType'],
                    config['NpcDirection'], config[guide])


# Runtime IDs are allocated locally outside the existing star and fishing ranges.
JCHJ = RULES['special']['九重幻境']['BaseConfig']
YJMZ = RULES['special']['云阶秘藏']['BaseConfig']
NZSL = RULES['special']['逆转时轮']['BaseConfig']
ILLUSION = from_config(8_950_001, JCHJ, 'InfoMsg')
SKY = from_config(8_950_002, YJMZ, 'InfoMsg')
TIME_GATE = from_config(8_950_003, NZSL, 'SignNpcHelp')
ENTRANCE_IDS = frozenset((ILLUSION.nid, SKY.nid, TIME_GATE.nid))
GATE_SCENES = tuple(NZSL['NpcSceneIds'])
ILLUSION_START = time.fromisoformat(RULES['controller']['100251']['StartTime'])
SKY_START = time.fromisoformat(RULES['controller']['100381']['StartTime'])
GATE_STARTS = tuple(datetime.fromisoformat(value).time() for value in NZSL['MiJingStartTime'])


@lru_cache(maxsize=8)
def time_gate_for(start):
    from native946_npc_quests import spawn_position

    # The launch notice specifies a random refresh among these four maps.
    # Use the existing reachable encounter cells and keep the draw stable
    # for the whole session, across clients and a server restart.
    rng = Random(f'nzsl:{start.isoformat()}')
    scene = rng.choice(GATE_SCENES)
    return replace(TIME_GATE, scene=scene, position=spawn_position(scene, rng))


def active_entrance(scene, now=None):
    if scene not in (ILLUSION.scene, SKY.scene, *GATE_SCENES):
        return None
    now = datetime.now(EVENT_TIMEZONE) if now is None else now.astimezone(EVENT_TIMEZONE)
    day = now.date()

    if scene == ILLUSION.scene and day.weekday() == 3:
        start = datetime.combine(day, ILLUSION_START, EVENT_TIMEZONE)
        if start <= now < start + timedelta(seconds=JCHJ['DestroyNpcTime']):
            return ILLUSION
    if scene == SKY.scene and (day - EVENT_ROTATION_START).days % 14 == 8:
        start = datetime.combine(day, SKY_START, EVENT_TIMEZONE)
        if start <= now < start + timedelta(seconds=YJMZ['ActiveTime']):
            return SKY
    if scene in GATE_SCENES:
        for hour in GATE_STARTS:
            start = datetime.combine(day, hour, EVENT_TIMEZONE)
            if start <= now < start + timedelta(seconds=NZSL['DestroyNpcTime']):
                gate = time_gate_for(start)
                if scene == gate.scene:
                    return gate
    return None


def nearby(game, radius):
    if getattr(game.characters, 'trial_server', False):
        return None
    role = game.character
    entrance = active_entrance(role['map_id'])
    if entrance and max(abs(role['x']-entrance.position[0]), abs(role['y']-entrance.position[1])) <= radius:
        return entrance
    return None


def scene_packets(game, force=False):
    previous = getattr(game, '_timed_activity_npc', None)
    entrance = nearby(game, 72)
    if not force and previous == entrance:
        return ()
    packets = []
    if previous is not None and previous != entrance:
        if not force:
            packets.append(packet(1604, ['number'], [previous.nid]))
        if game.dialog_npc == previous.nid:
            packets.append(game.close_expired_npc_dialog())
    if entrance is not None:
        x, y = entrance.position
        packets.append(packet(1589, ['oci_npc'], [dict(id=entrance.nid, name=entrance.name,
            title=entrance.title, type=entrance.shape, class_type=1, x=x*20, y=y*20,
            dir=entrance.direction, layer=1, member_click=1)]))
    game._timed_activity_npc = entrance
    return tuple(packets)


def look(game, nid):
    entrance = nearby(game, 12)
    if entrance is None or entrance.nid != nid:
        return None
    game.dialog_actions = []
    game.npc_service_session = None
    return game.menu(entrance.guide + '#r本机开放入口显示和活动说明。', npc_id=nid)
