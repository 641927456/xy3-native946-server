"""Original-client town metadata, nearby NPCs and validated transport routes."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import json
import hashlib
import re


ROOT=Path(__file__).resolve().parent
INSTANCE_SCENES={int(key):row for key,row in json.loads(
    (ROOT/'native946_dungeon_scenes.json').read_text('utf8'))['scenes'].items()}
WAR_MAPS = {int(scene): background for scene, background in json.loads(
    (ROOT / 'native946_battle_maps.json').read_text('utf8'))['war_maps'].items()}

# 946 can submit s_hero_tele while approaching a portal, before entering
# the small 1593 terrain rectangle. Keep that visual rectangle unchanged.
# The native movement/preload callback uses a 200-pixel (10-cell) window.
PORTAL_APPROACH_CELLS=10
# Native NPC auto-pilot finishes within 40 pixels of its configured from_pos.
# One extra grid cell covers floor conversion of its pixel coordinates.
NPC_ROUTE_APPROACH_CELLS=3

# The ordinary-server selector in 946 bin_script.wdf/24842d05 uses a
# 28-day rotation from 2011-06-25. Times are local to the game (UTC+8).
EVENT_ROTATION_START = date(2011, 6, 25)
EVENT_TIMEZONE = timezone(timedelta(hours=8))
NPC_VISIBILITY_VERSION = 1

# 946 info/info_map_night_effect.lua keys are terrain resources.
NIGHT_MAPS = {2001: 2327, 2002: 2371, 2003: 2372, 2007: 2379, 2443: 2444, 2457: 2450}
NIGHT_LIGHT_MAPS = frozenset((
    2004, 2005, 2006, 2008, 2010, 2011, 2012, 2013, 2015, 2019, 2020, 2021,
    2023, 2024, 2025, 2026, 2027, 2028, 2030, 2041, 2225, 2226, 2227, 2228,
    2233, 2235, 2236, 2237, 2256, 2357, 2360, 2362, 2384,
))
SCENE_DAY = 0
SCENE_NIGHT = 1
DEFAULT_SCENE_STATUS = 8
# Local schedule approved on 2026-11-27. The official changeover hours are unverified.
LOCAL_DAY_START_HOUR = 6
LOCAL_NIGHT_START_HOUR = 18

# Each window is (rotation days, first minute, first closed minute).
# 100081 is the township examination window; the 22:00 calendar entry also
# includes the later palace examination with the resident emperor.
_EXAM_WINDOW = (((21, 22), 10*60, 20*60),)
_EVENT_NPC_WINDOWS = {
    2001: {
        **dict.fromkeys(range(4695, 4700), _EXAM_WINDOW),  # 长安乡试考官
        4760: (((7, 8), 10*60, 23*60+55),),               # 璇玑幻域入场神秘道人
    },
    2002: dict.fromkeys((4700, 4701, 4702), _EXAM_WINDOW),  # 皇城乡试及省试
    2006: {2524: (((1, 8, 15, 22), 20*60+45, 22*60+30),)},  # 联盟大战入场宋楚
    2011: {5873: (((0, 14), 14*60, 20*60),
                  ((3, 17), 19*60, 23*60+55))},             # 天地劫2义军征召使
    2027: dict.fromkeys((4692, 4693, 4694), _EXAM_WINDOW),  # 大唐东乡试考官
}


@lru_cache(maxsize=32)
def _closed_event_npcs(scene, rotation_day, minute):
    return frozenset(nid for nid, windows in _EVENT_NPC_WINDOWS[scene].items()
                     if not any(rotation_day in days and start <= minute < end
                                for days, start, end in windows))


class World:
    def __init__(self,path=ROOT/'native946_world_data.json'):
        data=json.loads(Path(path).read_text(encoding='utf8'))
        if data['client_revision']!=946 or data['schema_version']!=1:raise ValueError('Unexpected native world version')
        self.data=data
        self.scenes={int(k):v for k,v in data['scenes'].items()}
        self.scene_catalog={int(k):v for k,v in data.get('scene_catalog',data['scenes']).items()}
        self.scene_catalog.update(INSTANCE_SCENES)
        backgrounds = defaultdict(set)
        for sid, scene in self.scene_catalog.items():
            if sid in WAR_MAPS:
                backgrounds[scene['map_id']].add(WAR_MAPS[sid])
        self._battle_maps_by_terrain = {
            terrain: next(iter(maps)) for terrain, maps in backgrounds.items() if len(maps) == 1
        }
        self.npcs={int(sid):{n['npc_id']:n for n in rows} for sid,rows in data['npcs'].items()}
        self.npc_directions = {
            sid: {nid: row['dir'] for nid, row in rows.items() if 'dir' in row}
            for sid, rows in self.npcs.items()
        }
        self.portals=defaultdict(list);self.transports=defaultdict(list);self.buckets=defaultdict(list)
        for row in data['portals']:self.portals[row['from']].append(row)
        for row in data['npc_transports']:self.transports[(row['from'],row['npc_id'])].append(row)
        for sid,rows in self.npcs.items():
            for npc in rows.values():self.buckets[(sid,npc['x']//640,npc['y']//640)].append(npc)
        self._npc_packets={};self._portal_packets={}
        self._source_verified=False

    def scene(self,sid):
        if type(sid) is int:
            if sid in self.scene_catalog:
                return self.scene_catalog[sid]
            from native946_mount_spaces import SPACES, space_kind
            kind = space_kind(sid)
            if kind:
                rule = SPACES[kind]
                return dict(self.scene_catalog[rule['template_scene']], scene_name=rule['name'], show_in_worldmap=0)
        raise ValueError('这个场景暂时不能进入。')

    def scene_day_effect(self, sid, now=None):
        scene = self.scenes.get(sid)
        if scene is None:
            return DEFAULT_SCENE_STATUS
        terrain = scene['map_id']
        if terrain not in NIGHT_MAPS and terrain not in NIGHT_LIGHT_MAPS:
            return DEFAULT_SCENE_STATUS
        local_time = datetime.now(EVENT_TIMEZONE) if now is None else now.astimezone(EVENT_TIMEZONE)
        return SCENE_DAY if LOCAL_DAY_START_HOUR <= local_time.hour < LOCAL_NIGHT_START_HOUR else SCENE_NIGHT

    def battle_map(self, sid):
        if sid in WAR_MAPS:
            return WAR_MAPS[sid]
        scene = self.scene_catalog.get(sid)
        if scene is not None:
            terrain = scene['map_id']
            if terrain in self._battle_maps_by_terrain:
                return self._battle_maps_by_terrain[terrain]
            original = self.scene_catalog.get(terrain)
            if original is not None and original['scene_name'] == scene['scene_name']:
                return WAR_MAPS.get(terrain, 0)
        # The 946 renderer uses the current terrain when c_war_start sends 0.
        return 0

    def nearby(self,sid,x,y,radius=72):
        rows={}
        for bx in range((x-radius)//32,(x+radius)//32+1):
            for by in range((y-radius)//32,(y+radius)//32+1):
                for npc in self.buckets.get((sid,bx,by),()):
                    if max(abs(npc['x']//20-x),abs(npc['y']//20-y))<=radius:rows[npc['npc_id']]=npc
        return rows

    def npc(self,sid,nid):return self.npcs.get(sid,{}).get(nid)

    def closed_event_npcs(self, sid):
        if sid not in _EVENT_NPC_WINDOWS:
            return frozenset()
        now = datetime.now(EVENT_TIMEZONE)
        rotation_day = (now.date() - EVENT_ROTATION_START).days % 28
        return _closed_event_npcs(sid, rotation_day, now.hour*60+now.minute)

    def event_npc_is_open(self, sid, nid):
        return nid not in _EVENT_NPC_WINDOWS.get(sid, ()) or nid not in self.closed_event_npcs(sid)

    def npc_packet(self,sid,nid):
        key=(sid,nid)
        if key not in self._npc_packets:
            from native946_codec import packet
            row=self.npcs[sid][nid]
            # The 946 NPC renderer uses is_flying to enter the flight layer and attach the cloud.
            value={'id':nid,'name':row.get('name',''),'title':row.get('title',''),'type':row['shape'],
                   'class_type':1,'x':row['x'],'y':row['y'],'dir':row.get('dir',4),'layer':1,
                   'title_style':row.get('title_style',0),
                   'member_click':int(bool(row.get('name')) or bool(self.transports[(sid,nid)])),
                   'is_flying':row.get('is_aviate',0),
                   'weapon_idx':row.get('weapon_index',0),'weapon_color':row.get('weapon_color',0),
                   'color1':row.get('color1',0),'color2':row.get('color2',0),'color3':row.get('color3',0)}
            self._npc_packets[key]=packet(1589,['oci_npc'],[value])
        return self._npc_packets[key]

    def visible_portals(self,sid):
        from native946_mount_spaces import exit_portal

        local_exit = exit_portal(sid)
        return (local_exit,) if local_exit else self.portals.get(sid,())

    def portal_packet(self,sid):
        if sid not in self._portal_packets:
            from native946_codec import packet
            values=[{'x':r['source'][0],'y':r['source'][1],'aoi_x':r['half_extent'],'aoi_y':r['half_extent']}
                    for r in self.visible_portals(sid)]
            self._portal_packets[sid]=packet(1593,[('array','tele_info')],[values])
        return self._portal_packets[sid]

    def physical_route(self,sid,x,y,actor_position):
        def distance(point,row):
            return max(abs(row['source'][k]-point[k]) for k in (0,1))
        candidates=[row for row in self.visible_portals(sid)
                    if distance((x,y),row)<=max(row['half_extent'],PORTAL_APPROACH_CELLS)]
        if candidates:
            if max(abs(actor_position[0]-x),abs(actor_position[1]-y))>10:
                raise ValueError('请走到传送阵附近再传送。')
            # Validate the authoritative actor against the entrance as well;
            # request/actor drift must not add two approach windows together.
            candidates=[row for row in candidates
                        if distance(actor_position,row)<=max(row['half_extent'],PORTAL_APPROACH_CELLS)]
            if not candidates:raise ValueError('请走到传送阵附近再传送。')
            return min(candidates,key=lambda row:sum((row['source'][k]-(x,y)[k])**2 for k in (0,1)))
        raise ValueError('当前位置没有可用的传送阵。')

    @staticmethod
    def eligible(route,character):
        rules=route.get('eligibility',())
        return not rules or (character is not None and any(
            rule['race'] in (0,character['race']) and rule['sex']==character['sex'] for rule in rules))

    def available_transports(self,sid,nid,character):
        return sorted((row for row in self.transports.get((sid,nid),()) if self.eligible(row,character)),
                      key=lambda row:row['trans_index'])

    def transport_label(self,route):
        """Use native destination aliases (e.g. 西市 inside 长安), not coordinates."""
        npc=self.npc(route['from'],route['npc_id']) or {}
        labels=[re.sub(r'#c[0-9a-fA-F]{6}|#[A-Za-z]','',s).strip()
                for s in npc.get('desc','').split('#r')[1:]]
        rows=self.transports.get((route['from'],route['npc_id']),())
        if labels and max((r['trans_index'] for r in rows),default=0)==len(labels):
            index=route['trans_index']-1
            if 0<=index<len(labels) and labels[index]:return labels[index]
        return self.scene(route['to'])['scene_name']

    def npc_route(self,sid,nid,index,actor_position,character=None):
        npc=self.npc(sid,nid)
        if npc is None:raise ValueError('当前场景没有这个传送人。')
        if not self.event_npc_is_open(sid,nid):raise ValueError('这个活动 NPC 当前未开放。')
        row=next((r for r in self.transports[(sid,nid)] if r['trans_index']==index),None)
        if row is None:raise ValueError('这条传送线路不可用。')
        near_npc=max(abs(npc['x']//20-actor_position[0]),abs(npc['y']//20-actor_position[1]))<=12
        near_route=max(abs(row['source'][k]-actor_position[k]) for k in (0,1))<=NPC_ROUTE_APPROACH_CELLS
        if not near_npc and not near_route:raise ValueError('请走近传送人再交谈。')
        if not self.eligible(row,character):raise ValueError('你的种族或性别不能使用这条师门传送线路。')
        return row

    def warm(self):
        from native946_map import load_map
        if not self._source_verified:
            from native946_archive_versions import (CLASSIFIED_MALL_CONFIG_PATCHES, MOUNT_CATALOG_CONFIG_PATCHES,
                                                    ORIGINAL_SHA256, VERIFIED_SCRIPT_PATCHES)
            # The pinned alternate archives retain every town source entry.
            verified_patches = {
                ('client-946/bin_script.wdf', ORIGINAL_SHA256): VERIFIED_SCRIPT_PATCHES,
                ('client-946/resource/configs.wdf', 'eb5fc4235e8c27cefdf18e084c2ae211b2f9251471b9af9491cc77e3e5342fcb'): {
                    '87e53edfc3d22187478a1452ac0bcca1f572a51ef552a9d2a51e354680c0c715',
                    'ef171d84c9647140667be05759e0d21cb6e2b20ea711e96b383887c46e551fa6',  # Guild founding help text.
                    *CLASSIFIED_MALL_CONFIG_PATCHES.values(),
                    *MOUNT_CATALOG_CONFIG_PATCHES.values(),
                },
            }
            for name,expected in self.data['source_sha256'].items():
                source=name.replace('\\','/')
                path=ROOT/Path(source)
                with path.open('rb') as stream:
                    digest=hashlib.file_digest(stream,'sha256').hexdigest()
                if digest!=expected and digest not in verified_patches.get((source,expected),()):
                    raise ValueError(f'客户端文件 {source} 已改变，需要重新生成城镇数据。')
            self._source_verified=True
        for sid in self.scenes:
            load_map(sid);self.portal_packet(sid)
        for sid,rows in self.npcs.items():
            for nid in rows:self.npc_packet(sid,nid)


@lru_cache(maxsize=1)
def world():return World()
