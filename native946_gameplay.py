"""Native world gameplay and isolated practice fixtures."""

from secrets import token_hex
from math import ceil
from struct import pack, unpack
from time import monotonic,sleep
import sys
from importlib import reload
from contextlib import contextmanager, nullcontext
from copy import deepcopy

from native946_codec import experience_notice_lines, packet, screen_notice, text_notice
from native946_map import load_map
from native946_storage import StorageBusy, transaction as storage_transaction

_durable_writer = sys.modules.get('native946_durable_writer')
if _durable_writer is not None and not hasattr(_durable_writer.DurableWriter, 'flush_idle'):
    reload(_durable_writer).refresh_registered_writers()

# The listener warms world data before it loads the gameplay module.
_world = sys.modules.get('native946_world')
if _world is not None and getattr(_world, 'NPC_VISIBILITY_VERSION', 0) < 1:
    reload(_world)
if _world is not None and _world.world().data.get('route_revision', 0) < 1:
    _world.world.cache_clear()
    _world.world().warm()

import native946_battle_actions as _battle_actions
if getattr(_battle_actions,'REFERENCE_WIRE_VERSION',0)<12:reload(_battle_actions)
from native946_battle_actions import BattleActions
_innate_effects=sys.modules.get('native946_innate_effects')
if _innate_effects is not None and getattr(_innate_effects,'WIRE_VERSION',0)<10:reload(_innate_effects)
_role_battle=sys.modules.get('native946_role_battle')
if _role_battle is not None and (getattr(_role_battle,'GUARDIAN_PASSIVE_WIRE_VERSION',0)<3 or getattr(_role_battle,'PRESENTATION_WIRE_VERSION',0)<4 or getattr(_role_battle,'WIRE_VERSION',0)<25 or getattr(_role_battle,'MEDICINE_WIRE_VERSION',0)<1):reload(_role_battle)
_bell=sys.modules.get('native946_zhuziguo_bell')
if _bell is not None and getattr(_bell,'PROTECTION_WIRE_VERSION',0)<1:reload(_bell)
# The recommendation panel may have imported the old score handler at startup.
_liveness = sys.modules.get('native946_liveness')
_liveness_stale = _liveness is not None and getattr(_liveness, 'WIRE_VERSION', 0) < 1
if _liveness_stale:
    reload(_liveness)
_task_recommendations = sys.modules.get('native946_task_recommendations')
if _task_recommendations is not None and (
        _liveness_stale or getattr(_task_recommendations, 'WIRE_VERSION', 0) < 2):
    reload(_task_recommendations)

# The existing controller reloads this module on its owning socket thread.
# Refresh already imported reference features without replacing sessions,
# controllers or party registries. Cold starts import these modules normally.
_realm_gems=sys.modules.get('native946_jing_gems')
_realm_gems_stale=_realm_gems is not None and getattr(_realm_gems,'REFERENCE_WIRE_VERSION',0)<1
if _realm_gems_stale:reload(_realm_gems)
_ornaments=sys.modules.get('native946_guardian_ornaments')
if _ornaments is not None and (_realm_gems_stale or getattr(_ornaments,'REFERENCE_WIRE_VERSION',0)<1):reload(_ornaments)
_guilds=sys.modules.get('native946_guilds')
if _guilds is not None and getattr(_guilds,'REFERENCE_WIRE_VERSION',0)<1:reload(_guilds)

_shops=sys.modules.get('native946_shops')
if _shops is not None and getattr(_shops,'REFERENCE_WIRE_VERSION',0)<8:reload(_shops)
_growth_battle=sys.modules.get('native946_growth_battle')
if _growth_battle is not None and getattr(_growth_battle,'ICON_LIST_VERSION',0)<3:reload(_growth_battle)
_reserve_food = sys.modules.get('native946_reserve_food')
if _reserve_food is not None and getattr(_reserve_food, 'SLOT_STORAGE_VERSION', 0) < 1:
    reload(_reserve_food)
for _feature in ('native946_book_combat','native946_refinement','native946_fashion_refinement','native946_fashion','native946_cosmetics','native946_nice_ids','native946_mall_mounts','native946_mount_spaces','native946_hero_titles','native946_equipment','native946_party','native946_workshops','native946_crafting','native946_task_chain',
                 'native946_npc_quests','native946_pawnshop','native946_npc_services','native946_guardian_passives','native946_guardian_innate','native946_guardian_ui','native946_neidan','native946_neidan_legacy','native946_skillbooks','native946_guardian_magic','native946_guardian_lottery','native946_guardian_acquisition','native946_guardian_channels','native946_campaign'):
    _loaded=sys.modules.get(_feature)
    _battle_base_stale=(_feature=='native946_npc_quests' and _loaded is not None and
                       'native946_wilderness' in sys.modules and hasattr(_loaded,'QuestBattle') and
                       _loaded.QuestBattle.__bases__[0] is not sys.modules['native946_wilderness'].WildBattle)
    _minimum=5 if _feature=='native946_guardian_acquisition' else 2 if _feature=='native946_guardian_channels' else 5 if _feature=='native946_skillbooks' else 13 if _feature=='native946_guardian_ui' else 6 if _feature in ('native946_crafting','native946_party','native946_npc_services') else 5
    _minimum=2 if _feature=='native946_task_chain' else max(_minimum,{'native946_crafting':11,'native946_party':8,'native946_equipment':8,'native946_workshops':9,'native946_skillbooks':4,'native946_npc_quests':12}.get(_feature,0))
    if _feature=='native946_task_chain' and _loaded is not None and 'native946_wilderness' in sys.modules:
        _battle_base_stale=hasattr(_loaded,'ChainBattle') and _loaded.ChainBattle.__bases__[0] is not sys.modules['native946_wilderness'].WildBattle
    if _feature=='native946_guardian_magic':_minimum=5
    if _feature=='native946_guardian_passives':_minimum=2
    if _feature=='native946_guardian_innate':_minimum=3
    if _feature=='native946_campaign':
        _minimum=4
        if _loaded is not None and 'native946_wilderness' in sys.modules:
            _battle_base_stale=_loaded.CampaignBattle.__bases__[0] is not sys.modules['native946_wilderness'].WildBattle
    if _feature in ('native946_fashion','native946_cosmetics','native946_hero_titles'):_minimum=2
    if _feature in ('native946_refinement','native946_fashion_refinement','native946_nice_ids','native946_mount_spaces'):_minimum=1
    if _feature=='native946_mall_mounts':_minimum=4
    if _feature=='native946_cosmetics':_minimum=9
    if _feature=='native946_pawnshop':_minimum=1
    if _feature=='native946_npc_services':_minimum=25
    if _feature=='native946_book_combat':_minimum=6
    if _feature=='native946_neidan':_minimum=4
    if _feature=='native946_neidan_legacy':_minimum=8
    if _feature=='native946_party':_minimum=29
    if _feature=='native946_guardian_lottery':_minimum=9
    elif _feature in ('native946_guardian_acquisition','native946_guardian_channels'):
        _minimum=max(_minimum,{'native946_guardian_acquisition':8,'native946_guardian_channels':4}[_feature])
    _inventory_pages_stale=(_feature=='native946_equipment' and _loaded is not None and getattr(_loaded,'INVENTORY_PAGE_VERSION',0)<1)
    _acquisition_exports_stale=(_feature=='native946_guardian_acquisition' and _loaded is not None and not hasattr(_loaded,'WRITES'))
    _gem_catalog_stale=_feature=='native946_cosmetics' and _realm_gems_stale
    _loaded_version=getattr(_loaded,'REFERENCE_WIRE_VERSION',0)
    if _loaded is not None and (_loaded_version<_minimum or _battle_base_stale or _inventory_pages_stale or _acquisition_exports_stale or _gem_catalog_stale):
        reload(_loaded)
        if _feature=='native946_party':
            with _loaded._LOCK:
                for _service in tuple(_loaded._SERVICES.values()):
                    _service.__class__=_loaded.PartyService
                    # Repair clients that were connected before the handoff fix.
                    if _loaded_version < 29:
                        for _game in tuple(_service.games.values()):
                            if _service.online(_game.character['id']) is _game:
                                _game._team_scene_resync_pending=True

# Group constellation battles inherit WildBattle through AfeiBattle.
_group_battle = sys.modules.get('native946_constellation_battle')
if _group_battle is not None:
    from native946_wilderness import WildBattle as _wild_battle_base
    if _group_battle.GroupBattle.__mro__[2] is not _wild_battle_base:
        _afei_battle = sys.modules['native946_afei_expansion']
        if _afei_battle.AfeiBattle.__bases__[0] is not _wild_battle_base:
            reload(_afei_battle)
        reload(_group_battle)

for _feature in ('native946_gear_services','native946_gear_sockets','native946_guardian_equipment'):
    _loaded=sys.modules.get(_feature)
    _minimum_version={'native946_gear_services':2,'native946_gear_sockets':1,'native946_guardian_equipment':7}[_feature]
    if _loaded is not None and (_realm_gems_stale or getattr(_loaded,'REFERENCE_WIRE_VERSION',0)<_minimum_version):reload(_loaded)
_guardian_score=sys.modules.get('native946_guardian_score')
if _guardian_score is not None:reload(_guardian_score)

# Progression can have been reloaded before the feature modules by the
# existing controller. Merge newly verified item identities into that same
# module so an online session can serialize new drops without reconnecting.
_inventory=sys.modules.get('native946_inventory')
if _inventory is not None and getattr(_inventory,'WIRE_VERSION',0)<11:
    for _name in ('native946_role_battle','native946_role_systems','native946_lineups','native946_role_artifacts','native946_role_growth','native946_role_arts','native946_role_society','native946_role_city_war','native946_role_baby_tools','native946_role_honors','native946_combat_control','native946_hero_page','native946_hero_extra_points','native946_hero_resistance','native946_hero_titles','native946_inventory_features','native946_lua_values','native946_equipment_plans','native946_transform','native946_fishing','native946_quick_glyphs'):
        if _name in sys.modules:reload(sys.modules[_name])
    import native946_inventory_locks
    reload(native946_inventory_locks);reload(_inventory)
_progression=sys.modules.get('native946_progression')
if _realm_gems_stale and _progression is not None:
    for _kind,_meta in _realm_gems.DATA['items'].items():
        _progression.DATA['items'].setdefault(_kind,_meta)
_equipment=sys.modules.get('native946_equipment')
if _progression is not None and _equipment is not None and hasattr(_progression,'DATA'):
    for _kind,_meta in _equipment.DATA['reward_metadata'].items():
        _progression.DATA['items'].setdefault(_kind,_meta)
_guardian_acquisition=sys.modules.get('native946_guardian_acquisition')
if _progression is not None and _guardian_acquisition is not None and hasattr(_progression,'DATA'):
    _progression.DATA['items'].update(_guardian_acquisition.ITEMS)
if _progression is not None and hasattr(_progression,'DATA'):
    from native946_anniversary_cards import ITEMS as _anniversary_items
    _progression.DATA['items'].update(_anniversary_items)
if _progression is not None and _shops is not None:
    for _kind,_meta in _shops.ITEMS.items():_progression.DATA['items'].setdefault(_kind,_meta)

# Upgrade the already-enabled diagnostic adapter through the existing reload
# command. This does not recreate its controller, token, queues or game socket.
_adapter=sys.modules.get('native946_control')
if _adapter is not None and (getattr(_adapter,'BACKGROUND_WIRE_VERSION',0)<21 or getattr(_adapter,'HERO_PAGE_RELOAD_VERSION',0)<10):
    reload(_adapter)
    import native946_refinement
    reload(native946_refinement)
    import native946_fashion_shop
    reload(native946_fashion_shop)
    import native946_mall_uses,native946_cosmetics,native946_progression,native946_characters,native946_acceptance,native946_tasks,native946_activities
    import native946_mall_guardians,native946_mall_bags,native946_mall_checkout,native946_mall_carving,native946_mall_portraits
    for _module in (native946_mall_guardians,native946_mall_bags,native946_mall_checkout,native946_mall_carving,native946_mall_portraits,native946_mall_uses,native946_cosmetics,native946_progression,native946_characters,native946_acceptance,native946_tasks,native946_activities):reload(_module)
    if 'server' in sys.modules:reload(sys.modules['server'])
    from native946_runtime import install_live_adapter
    from native946_connections import install_legacy_storage_adapter
    install_legacy_storage_adapter()
    install_live_adapter()


TRAINER_ID = 900001
ENEMY_ID = 900002
FIGHTER_TYPES = ["number"] * 24
FIGHTER_TYPES[3] = "string"
for index in (7, 8, 9, 17):
    FIGHTER_TYPES[index] = "byte"
# Native descriptor +56 is 3: the three dye bytes have no count prefix.
FIGHTER_TYPES[10] = ("fixed_array", "byte", 3)
NORMAL_ATTACK = 10
QUICK_MEDICINE_REQUESTS = {1412, 1489}


def numbers(mid, *values):
    return packet(mid, ["number"] * len(values), values)


class NativeGameplay(BattleActions):
    def __init__(self, character, characters, restore_scene, *, practice_trainer=False):
        self.character = character
        self.characters = characters
        self.restore_scene = restore_scene
        self.grid = load_map(character["map_id"])
        self.practice_trainer = practice_trainer
        self.npc_position = self.practice_npc_position()
        self.last_path = {(character["x"], character["y"])}
        self.last_event = None
        self.dialog_open = False
        self.battle = None
        self.dialog_actions = []
        self.dialog_npc = None
        self._world_visible=set()
        self._world_bucket=None
        self._arrival_guard=None
        self._last_transition=None
        self.world_version=1
        self.ensure_progression()
        from native946_cosmetics import Cosmetics
        Cosmetics(self).refresh()
        from native946_world import world
        catalog = world()
        sid, x, y = character['map_id'], character['x'], character['y']
        closed = catalog.closed_event_npcs(sid)
        self._world_visible = set(catalog.nearby(sid, x, y)) - closed
        self._world_bucket = (sid, x//16, y//16, closed, catalog.npc_directions.get(sid, {}))
        from native946_party import service
        from native946_temp_inventory import open_session
        party = service(characters.database)
        open_session(self.progression)
        party.attach(self)

    def ensure_progression(self):
        if getattr(self,'world_version',0)!=1:
            self.grid=load_map(self.character['map_id'])
            self.npc_position=self.practice_npc_position()
            self.dialog_npc=None;self.dialog_open=False
            self._world_visible=set();self._world_bucket=None
            self._arrival_guard=None;self._last_transition=None;self.world_version=1
        if getattr(self.characters,'storage_version',0)<4:
            import native946_characters
            reload(native946_characters)
            self.characters.__class__=native946_characters.Characters
        from native946_inventory import WIRE_VERSION as INVENTORY_CONTROLS_VERSION
        if (not hasattr(self,'progression') or getattr(self.progression,'wire_version',0)!=4
                or getattr(self.progression,'cosmetic_storage_version',0)!=5
                or getattr(self.progression,'refinement_storage_version',0)!=3
                or getattr(self.progression,'inventory_controls_version',0)!=INVENTORY_CONTROLS_VERSION
                or getattr(self.progression,'chain_version',0)!=1):
            from native946_progression import Progression
            known=getattr(getattr(self,'progression',None),'known_items',set())
            self.progression = Progression(self.characters.database, self.character['owner'], self.character['id'])
            self.progression.known_items=known
        if not hasattr(self, 'dialog_actions'):
            self.dialog_actions = []
        self.progression.practice_trainer = getattr(self, 'practice_trainer', False)
        from native946_summons import WIRE_VERSION as SUMMON_WIRE_VERSION
        if not hasattr(self, 'summons') or getattr(self.summons,'wire_version',0) != SUMMON_WIRE_VERSION:
            from native946_summons import Summons
            summons = Summons(self.progression)
            summons.reconcile_levels()
            self.summons = summons
        self.summons.progression=self.progression
        self.progression._cache_enabled=bool(getattr(self.characters,'runtime_caching',False))

    def sync_progression(self):
        self.ensure_progression()
        with storage_transaction(self.characters.database):
            pets=self.summons.snapshot()
            from native946_cosmetics import Cosmetics
            from native946_mall_uses import MallUses
            from native946_guardian_lottery import GuardianLottery
            from native946_growth_battle import GrowthBattle
            from native946_premium_items import sync_auto_medical
            from native946_reserve_food import sync as reserve_packet
            active = next((pet for pet in pets if pet['fight']), None)
            return (*self.progression.sync(), *self.summons.sync(pets), *self.sync_summon_scene(pets),
                    reserve_packet(self, active), *sync_auto_medical(self, force=True),
                    *Cosmetics(self).currency_packets(), *Cosmetics(self).visual_packets(), *MallUses(self).sync(),
                    *GuardianLottery(self).entry_packets(include_icon=False), *GrowthBattle(self).entry_packets())

    def auto_level(self):
        increased = self.progression.auto_level()
        if increased:
            s = self.progression.snapshot()
            self.character.update(level=s['level'], experience=s['experience'], gold=s['gold'])
            self.summons.reconcile_levels()
        return increased

    def practice_npc_position(self):
        if getattr(self, 'practice_trainer', False) and self.character['map_id'] == 2001:
            return self.grid.nearby(300, 210)
        return None

    def navigate_to_trainer(self):
        if self.battle is not None:
            raise ValueError('请先完成当前战斗。')
        if not getattr(self, 'practice_trainer', False) or self.npc_position is None:
            raise ValueError('训练入口只在独立测试中开放。')
        from native946_navigation import npc_auto_trace
        x,y=self.npc_position
        if not self.grid.walkable(x,y):raise ValueError('目标附近没有可走位置。')
        self.last_event={'action':'native_navigation_started','npc_id':TRAINER_ID,'target':[x,y]}
        return npc_auto_trace(self.character['map_id'],x,y,TRAINER_ID,'本机教头')

    def sync_summon_scene(self,pets=None):
        if self.battle is not None:
            return ()
        previous = getattr(self, '_shown_summon_id', None)
        if pets is None:pets=self.summons.snapshot()
        shown = next((s for s in pets if s['show']), None)
        self._shown_summon_id = shown['id'] if shown else None
        packets = [numbers(1604, previous)] if previous else []
        if shown:
            x,y = self.grid.nearest_walkable(self.character['x']-2, self.character['y'])
            npc = dict(id=shown['id'], name=shown['name'], title='守护', type=shown['show_type'],
                       weapon_color=shown['weapon_color'],
                       class_type=1, x=x*20, y=y*20, dir=4, layer=1, member_click=0)
            npc.update({k:shown.get(k,0) for k in ('color1','color2','color3','halo','foot_halo')})
            packets.extend((packet(1589,['oci_npc'],[npc]),numbers(1605,shown['id'],self.character['id'],96)))
        return tuple(packets)

    def trainer_packet(self):
        if not getattr(self, 'practice_trainer', False) or self.npc_position is None:return None
        x, y = self.npc_position
        npc = {"id": TRAINER_ID, "name": "本机教头", "title": "切磋训练",
               "type": 311, "class_type": 1, "x": x * 20, "y": y * 20, "dir": 4, "layer": 1,
               "member_click": 1}
        return packet(1589, ["oci_npc"], [npc])

    def menu(self, message, options=(), npc_id=None, *, speaker=0):
        if npc_id is None:
            npc_id = TRAINER_ID if getattr(self, 'practice_trainer', False) else self.character['id']
        self.dialog_open = bool(options)
        self.dialog_npc=npc_id
        return packet(1025, [("array", "npc_chat"), ("array", "string"), "number", "number"],
                      [[{"chat_msg": message, "speaker": speaker}], list(options), npc_id,1])

    def close_npc_dialogue(self):
        self.dialog_open = False
        self.dialog_actions = []
        self.dialog_npc = None
        return packet(1183, ['string'], ['dlg_npc_chat'])

    def append_npc_menu_option(self, nid, reply, label, action):
        from native946_game_login import message_id
        from native946_login import MessageReader

        if not isinstance(reply, bytes) or not reply.startswith(message_id(1025)):
            return reply
        reader = MessageReader(reply[3:])
        chats = [dict(chat_msg=reader.string().decode('gb18030'), speaker=reader.integer())
                 for _ in range(reader.take(1)[0])]
        options = [reader.string().decode('gb18030') for _ in range(reader.take(1)[0])]
        callback, flag = reader.integer(), reader.integer()
        reader.finish()
        if callback != nid or len(options) != len(self.dialog_actions) or len(options) >= 254:
            return reply
        options.append(label)
        self.dialog_actions.append(action)
        self.dialog_open = True
        return packet(1025, [('array', 'npc_chat'), ('array', 'string'), 'number', 'number'],
                      [chats, options, callback, flag])

    def sync_static_npcs(self, force=False, *, relocated=False):
        from native946_world import world
        catalog = world()
        sid, x, y = (self.character[k] for k in ('map_id', 'x', 'y'))
        closed = catalog.closed_event_npcs(sid)
        directions = catalog.npc_directions.get(sid, {})
        bucket = (sid, x//16, y//16, closed, directions)
        previous = self._world_bucket
        if not force and not relocated and bucket == previous:
            return ()
        visible = set() if force else self._world_visible
        nearby = set(catalog.nearby(sid, x, y)) - closed
        retained = {nid for nid in visible - closed if (npc := catalog.npc(sid, nid)) is not None and
                    max(abs(npc['x']//20-x), abs(npc['y']//20-y)) <= 96}
        # Existing connections may still have the four-field bucket from before a hot reload.
        previous_directions = previous[4] if previous and previous[0] == sid and len(previous) == 5 else {}
        refacing = {nid for nid in retained & (previous_directions.keys() | directions.keys())
                    if previous_directions.get(nid, 4) != directions.get(nid, 4)}
        packets = [numbers(1604, nid) for nid in sorted((visible - retained) | refacing)]
        packets.extend(catalog.npc_packet(sid, nid) for nid in sorted((nearby - retained) | refacing))
        self._world_visible = retained | nearby
        self._world_bucket = bucket
        if self.dialog_npc in closed:
            packets.append(self.close_expired_npc_dialog())
        return tuple(packets)

    def close_expired_npc_dialog(self):
        self.npc_service_session = None
        return self.close_npc_dialogue()

    def sync_world_npcs(self,force=False,*,relocated=False):
        previous = self._world_bucket
        packets = self.sync_static_npcs(force, relocated=relocated)
        if not force and not relocated and previous == self._world_bucket:
            return packets
        from native946_npc_quests import Quests
        from native946_fishing import Fishing
        from native946_star_challenge import StarChallenge
        from native946_afei_expansion import AfeiWorld
        from native946_constellations import Constellations
        from native946_timed_npcs import scene_packets as timed_npc_packets
        return (*packets,*Quests(self).scene_packets(force),*Fishing(self).scene_packets(force),
                *StarChallenge(self).scene_packets(force),*AfeiWorld(self).scene_packets(force),
                *Constellations(self).scene_packets(force),*timed_npc_packets(self,force))

    def scene_objects(self):
        from native946_world import world
        from native946_fishing import Fishing
        from native946_dungeon_routes import stage_guide
        trainer=self.trainer_packet()
        return (*((trainer,) if trainer is not None else ()),*self.sync_world_npcs(force=True),
                *stage_guide(self),
                world().portal_packet(self.character['map_id']),
                *(self.party_service.scene_players(self) if hasattr(self,'party_service') else ()),*Fishing(self).scene_fishers())

    def travel_scene_packets(self, *, reposition=False):
        from native946_cosmetics import Cosmetics
        from native946_fishing import Fishing

        with storage_transaction(self.characters.database):
            if reposition:
                c = self.character
                # 946 c_nobj_goto moves the existing hero and stands its waypoint at the destination.
                goto = packet(1606, ['number', 'word', 'word'], [c['id'], c['x'], c['y']])
                return (goto, *self.sync_world_npcs(relocated=True),
                        *self.party_service.scene_players(self, relocated=True),
                        *Fishing(self).scene_fishers(), *self.sync_summon_scene())
            return (*self.restore_scene(), *self.scene_objects(), *self.sync_summon_scene(),
                    *Cosmetics(self).visual_packets(scene_only=True))

    def change_scene(self,route,request_key):
        if self.battle is not None:raise ValueError('战斗中不能传送。')
        old=self.character['map_id'];target=route['to'];grid=load_map(target)
        x,y=route['arrival']
        if not grid.walkable(x,y):raise ValueError('目标落点暂时不可用。')
        revision=self.characters.change_scene(self.character['owner'],self.character['id'],old,target,x,y,
                                             self.character.get('position_revision',0))
        self.character.update(map_id=target,x=x,y=y,position_revision=revision)
        if old != target:
            self._encounter_steps = 0
        self.grid=grid;self.npc_position=self.practice_npc_position()
        self.dialog_open=False;self.dialog_actions=[];self.dialog_npc=None;self.npc_service_session=None
        self.last_path={(x,y)}
        # The native goto handler can refuse a team-controlled scene object.
        reposition = old == target and not self.character.get('team_leader')
        if not reposition:
            self._shown_summon_id=None
            self._world_visible=set();self._world_bucket=None
        self._arrival_guard=(x,y);self._last_transition=request_key
        self.last_event={'action':'scene_changed','from_scene':old,'to_scene':target,'position':[x,y],'route_id':route['id']}
        return (packet(1183,['string'],['dlg_npc_chat']),*self.travel_scene_packets(reposition=reposition))

    def world_npc_look(self,nid,*,include_mount_guide=True,include_school_visit=True):
        from native946_timed_npcs import ENTRANCE_IDS, look as look_timed_npc
        if nid in ENTRANCE_IDS:return look_timed_npc(self,nid)
        from native946_world import world
        catalog = world()
        c = self.character
        if not catalog.event_npc_is_open(c['map_id'], nid):
            return None
        from native946_npc_quests import Quests
        from native946_dungeon_routes import DungeonRoute, ENTRY_KEYS
        dungeon=DungeonRoute(self)
        stage=dungeon.look(nid)
        if stage is not None:return stage
        quests=Quests(self)
        if include_school_visit:
            visit=quests.visit_menu(nid)
            if visit is not None:return visit
        if not getattr(self,'_campaign_bypass',False):
            from native946_campaign import Campaign
            campaign=Campaign(self).look(nid)
            if campaign is not None:
                return dungeon.attach_entry(nid,campaign)
        from native946_task_chain import TaskChain
        chain=TaskChain(self).look(nid)
        if chain is not None:return chain
        task=quests.look(nid)
        if task is not None:return task
        npc = catalog.npc(c['map_id'], nid)
        if npc is None:return None
        if max(abs(npc['x']//20-c['x']),abs(npc['y']//20-c['y']))>12:
            return self.menu('请走近一些再交谈。',npc_id=nid)
        from native946_local_guides import mount_option
        key = c['map_id'], nid
        local_mount = mount_option(self, key) if include_mount_guide else None
        from native946_tasks import FIVE_AGGREGATES_NPC

        def with_npc_options(reply):
            reply=dungeon.attach_entry(nid,reply)
            if key == FIVE_AGGREGATES_NPC:
                reply = self.append_npc_menu_option(nid, reply, '查看化境·五蕴领取条件',
                                                    dict(kind='story', op='five_aggregates_check'))
            return self.append_npc_menu_option(nid, reply, *local_mount) if local_mount else reply

        routes=catalog.available_transports(c['map_id'],nid,c)
        self.dialog_actions=[row['trans_index'] for row in routes]
        self.last_event={'action':'npc_dialogue','npc_id':nid}
        if routes:return with_npc_options(self.menu('请选择要前往的地方：',
                                                    [catalog.transport_label(row) for row in routes],nid))
        from native946_npc_services import NpcServices
        service_menu=NpcServices(self).menu(npc)
        if service_menu is not None:return with_npc_options(service_menu)
        saved_dungeon=dungeon.resume(nid)
        if saved_dungeon is not None:return saved_dungeon
        from native946_activities import Activities, npc_activity_names, unavailable_npc_notice
        mijing=Activities(self).mijing_entrance(*key)
        if mijing is not None:return with_npc_options(mijing)
        activities=npc_activity_names(c['map_id'],nid)
        if activities and key not in ENTRY_KEYS:
            return unavailable_npc_notice(self,'、'.join(activities)+'暂未开放。')
        if npc['npc_type']==2 and key not in ENTRY_KEYS and not local_mount:
            return unavailable_npc_notice(self,'当前没有可用的传送目的地。')
        message=npc.get('desc') or ''
        if message or key in ENTRY_KEYS or local_mount or key == FIVE_AGGREGATES_NPC:
            return with_npc_options(self.menu(message,npc_id=nid))
        if self.dialog_npc is not None:
            return self.close_npc_dialogue()

    def fighter(self, fid, role, name, camp, warrior_type, slot=1, level=1, *, mid=2147):
        args = [0] * 24
        args[:7] = [fid, warrior_type, role, name, camp, slot, level]
        args[10] = [0, 0, 0]
        if warrior_type==1:
            if fid==self.character['id']:a=self.character.get('appearance',{})
            else:
                from native946_cosmetics import appearance
                with self.progression.transaction(write=False) as db:a=appearance(db,fid)
            args[7]=a.get('weapon_idx',0);args[8]=a.get('weapon_color',0)
            args[10]=[a.get('color'+str(i),0) for i in (1,2,3)]
            for i,k in [(11,'hat_id'),(12,'suit_id'),(13,'cloak_id'),(17,'halo'),(18,'foot_halo'),(21,'charm_nimbus'),(22,'charm_head'),(23,'charm_shoulder')]:args[i]=a.get(k,0)
            args[2]=a.get('shape',role)
            args[19]=a.get('left_totem',0)
        elif warrior_type==3:
            from native946_guardian_equipment import ornament_appearance
            from native946_mall_guardians import appearance
            with self.progression.transaction(write=False) as db:
                row=db.execute('SELECT character_id FROM summons WHERE id=?',(fid,)).fetchone()
                a=appearance(db,row[0],fid) if row else {}
                if row:args[2],args[8]=ornament_appearance(db,row[0],fid,role)
            args[10]=[a.get('color'+str(i),0) for i in (1,2,3)]
            args[17]=a.get('halo',0);args[18]=a.get('foot_halo',0)
        args[20] = fid
        types=FIGHTER_TYPES
        if mid==2128:
            # c_add_fighter encodes the halo as a number, c_init_fighter as a byte.
            types=list(FIGHTER_TYPES)
            types[17]='number'
        return packet(mid, types, args)

    def begin_battle(self):
        from native946_cosmetics import Cosmetics
        from native946_role_battle import fighter_entry,solo_fighters
        from native946_guardian_magic import skills_packet as guardian_skills_packet
        from native946_world import world
        Cosmetics(self).refresh()
        cid = self.character["id"]
        stats = self.progression.snapshot()
        if stats['hp'] <= 0:
            return self.menu('请先休息或使用回生丹恢复气血。')
        self.battle = {"id": token_hex(16), "phase": "input", "round": 1,
                       "hp": stats['hp'], 'mp':stats['mp'], 'hpmax':stats['hpmax'], 'mpmax':stats['mpmax'],
                       "enemy_hp": 75, "victory": False}
        pet = self.summons.active()
        self.battle['pet'] = pet if pet and pet['hp'] > 0 else None
        self.battle['participants']={pet['id']:pet} if self.battle['pet'] else {}
        # Local practice target only: preserve several native action rounds
        # after switching from toy stats to the original base-stat scale.
        target_hp=max(75,3*(stats['attack']+(pet['attack'] if self.battle['pet'] else 0)))
        self.battle.update(enemy_hp=target_hp,enemy_hpmax=target_hp)
        self.battle['orders'] = {}
        from native946_race_fabao_battle import initialize as artifact_initialize, view as artifact_view
        artifact_initialize(self.battle,solo_fighters(self),{cid:self})
        input_seconds=self.begin_input(cid)
        self.last_event = {"action": "battle_started", "character_id": cid}
        return (
            packet(1183, ["string"], ["dlg_npc_chat"]),
            packet(1183, ["string"], ["dlg_summon_status"]),
            packet(1183, ["string"], ["dlg_skill_panel_new"]),
            packet(2171, ["number", "number", "number", "byte", "number", "string", "number",
                          "number", "number", ("array", "weapon2_skill")],
                   [world().battle_map(self.character['map_id']), 0, 0, 0, 0, "训练木偶", 0, 100, 100, []]),
            self.fighter(cid, self.character["role_id"], self.character["name"], 4, 1,level=stats['level']),
            numbers(2148, cid, stats['hp'], stats['hpmax'], stats['hpmax'], stats['mp'], stats['mpmax'], stats['mpmax'], 6, 10, 0),
            *fighter_entry(solo_fighters(self)[cid],self.battle['id']),
            self.fighter(ENEMY_ID, 211, "训练木偶", 3, 2),
            *((numbers(4206, pet['id']), self.fighter(pet['id'], pet['type'], pet['name'], 4, 3, 6, pet['level']),
               numbers(2148, pet['id'], pet['hp'], pet['hpmax'], pet['hpmax'], pet['mp'], pet['mpmax'], pet['mpmax'], 0, 100, 0))
              if self.battle['pet'] else ()),
            numbers(2148, ENEMY_ID, target_hp, target_hp, target_hp, 0, 0, 0, 0, 100, 0),
            *Cosmetics(self).enter_effects(),self.progression.combat_skills_packet(),
            *((guardian_skills_packet(pet),) if pet else ()),
            *artifact_view(self.battle,cid),
            # Release the native round-effect queue after enqueueing the input menu.
            numbers(2135,1), numbers(2151,cid,input_seconds,0), numbers(2139,2),
        )

    def finish_battle(self, victory, *, completed=False, drawing_timed_out=False):
        battle = self.battle
        if battle is None:
            return None
        self.characters.settle_battle(self.character["owner"], self.character["id"], battle["id"], victory)
        if victory:self.auto_level()
        battle['hp']=max(1,battle['hp'])
        self.persist_battle_vitals()
        if victory:
            self.progression.record_victory(battle['id'])
        self.settle_battle_guardians(victory)
        if completed:
            from native946_premium_items import heal_after_battle
            from native946_reserve_food import restore_after_battle
            with self.progression.transaction() as db:
                heal_after_battle(db, {self.progression.cid: self})
                guardian = battle.get('pet')
                restore_after_battle(db, self, guardian['id'] if guardian else None)
        self.battle = None
        self._shown_summon_id = None  # The native scene switch cleared world actors.
        self.last_event = {"action": "battle_finished", "character_id": self.character["id"],
                           "victory": victory, "experience": 20 if victory else 0, "gold": 5 if victory else 0}
        from native946_battle_actions import QUEUED_BATTLE_EXIT
        ending = QUEUED_BATTLE_EXIT if drawing_timed_out else (numbers(2146),)
        reward = ()
        if victory:
            lines = experience_notice_lines(self.last_event['experience'])
            lines.append(f'金钱#Y{self.last_event["gold"]}#n。')
            reward = (screen_notice('#r'.join(lines)),)
        prefix=(packet(1183,['string'],['dlg_npc_chat']),*ending)
        return self.defer_packets(lambda:(*prefix,*self.restore_scene(),*self.scene_objects(),*self.sync_progression(),*reward),
                                 fallback=lambda:(*prefix,*self.restore_scene(),*reward))

    def attack(self, defend=False, skill_id=NORMAL_ATTACK, pet=False, target=0):
        battle=self.battle
        if battle is None or battle['phase']!='input':return None
        from native946_role_battle import InsufficientHeroMana,solo_fighters,validate_order,profile,FRIENDLY
        fighters=solo_fighters(self);cid=self.character['id'];summon=battle.get('pet')
        actor_id=self.order_actor(pet);actor=fighters[actor_id]
        orders=battle.get('orders',{});existing=orders.get(actor_id)
        if defend:
            if existing:
                if existing.get('kind')!='attack' or existing.get('skill_id') or existing.get('guardian_skill') or existing.get('skill',NORMAL_ATTACK)!=NORMAL_ATTACK:return None
            return self.queue_order(actor_id,dict(kind='defend',defend=True),replace=existing is not None)
        if existing:return None
        if skill_id!=NORMAL_ATTACK:
            from native946_guardian_magic import InsufficientGuardianMana,prepared
            try:
                if pet:
                    if target and target not in fighters:raise ValueError('请选择有效的战斗目标。')
                    v=prepared(summon,skill_id)
                    support=v['element']=='support'
                    if target not in (0,ENEMY_ID) and (not support or fighters[target]['camp']!=4):
                        raise ValueError('请选择有效的友方目标。')
                    order=dict(kind='attack',guardian_skill=skill_id,target=target if support else ENEMY_ID,mana=v['mana'])
                else:
                    if not target:target=cid if (profile(skill_id) or {}).get('family') in FRIENDLY else ENEMY_ID
                    order=validate_order(actor,skill_id,target,fighters)
            except (InsufficientGuardianMana,InsufficientHeroMana):
                reply=self.queue_order(actor_id,dict(kind='attack',skill=NORMAL_ATTACK,target=ENEMY_ID,defend=False))
                label='守护' if pet else '人物'
                notice=text_notice(label+'法力不足，本次改用普通攻击。')
                return (notice,*reply) if isinstance(reply,tuple) else (notice,reply)
            return self.queue_order(actor_id,order)
        if target not in (0,ENEMY_ID):raise ValueError('请选择敌方目标。')
        return self.queue_order(actor_id,dict(kind='attack',skill=10,target=ENEMY_ID,defend=False))

    def begin_input(self, actor, *, new_round=True):
        from native946_role_battle import INPUT_SECONDS
        battle=self.battle
        now=monotonic()
        battle.update(input_actor=actor,input_timeout_sent=False)
        if new_round:
            battle['input_deadline']=now+INPUT_SECONDS
            battle['input_ended']=[]
            self._background_round=None
        return max(1,ceil(battle['input_deadline']-now))

    def end_input(self, actor):
        ended=self.battle.setdefault('input_ended',[])
        if actor in ended:return ()
        ended.append(actor)
        return (numbers(2152,actor),)

    def sync_scene_day_effect(self):
        from native946_world import DEFAULT_SCENE_STATUS, SCENE_DAY, SCENE_NIGHT, world

        if self.battle is not None or getattr(self, '_cinematic_pending', None):
            return ()
        sid = self.character['map_id']
        phase = world().scene_day_effect(sid)
        if phase == DEFAULT_SCENE_STATUS:
            return ()
        previous = getattr(self, '_scene_day_effect', None)
        self._scene_day_effect = (sid, phase)
        if previous == self._scene_day_effect or (previous != (sid, SCENE_NIGHT) and phase == SCENE_DAY):
            return ()
        # 452 c_doomrim_effect updates the current terrain or lighting in place.
        notice = '夜幕降临。' if phase == SCENE_NIGHT else '天亮了。'
        return (packet(452, ['number', 'string'], [phase, notice]),)

    def movement_path(self, old, start, directions):
        try:
            return self.grid.movement_path(old, start, directions)
        except ValueError:
            from native946_map import load_night_terrain
            from native946_world import NIGHT_MAPS, SCENE_NIGHT, world

            sid = self.character['map_id']
            night = getattr(self, '_scene_day_effect', None) == (sid, SCENE_NIGHT)
            scene = world().scenes.get(sid)
            terrain = NIGHT_MAPS.get(scene['map_id']) if scene else None
            if terrain is None or (not night and self.grid.walkable(*old)):
                raise
            # The stock client may keep its day terrain when Night Effect is off.
            # Validate the whole movement against one terrain. At dawn a player
            # standing on a night-only cell may leave it for a valid day cell.
            path = load_night_terrain(terrain).movement_path(old, start, directions)
            if not night and not self.grid.walkable(*path[-1]):
                raise
            return path

    def tick(self, now=None):
        if not hasattr(self,'party_service'):
            from native946_party import service
            service(self.characters.database).attach(self)
        with self.party_service.lock:
            from native946_mall_portraits import Portraits
            from native946_newbie_rewards import tick as award_tick
            portrait_updates=(*Portraits(self).tick(), *award_tick(self))
            if self.character['id'] in self.party_service.battles:
                return (*portrait_updates,*(self.party_service.tick(self,now) or ())) or None
            pending=self.party_service.tick(self,now)
            from native946_wilderness import pending_team_experience_popup
            team_popup = pending_team_experience_popup(self, now)
            reply=self._tick_local(now)
            from native946_premium_items import expire_auto_medical
            status_updates = expire_auto_medical(self)
            from native946_school import School
            try:
                school_updates = School(self).auto_tick(now) if getattr(self,'_auto_school',None) else ()
            except StorageBusy:
                school_updates = ()
            npc_updates = self.sync_static_npcs() if self.battle is None else ()
            neidan_updates = self.sync_corrected_neidan_levels() if self.battle is None else ()
            return (*portrait_updates,*pending,*team_popup,*(reply if isinstance(reply,tuple) else (reply,) if reply else ()),
                    *status_updates,*school_updates,*npc_updates,*neidan_updates,*self.sync_scene_day_effect()) or None

    def sync_corrected_neidan_levels(self):
        pending = getattr(self.progression, 'neidan_level_updates', ())
        if not pending:
            return ()
        try:
            available = {pet['id'] for pet in self.summons.snapshot()}
            packets = tuple(self.summons.neidan_packet(sid) for sid in pending if sid in available)
        except StorageBusy:
            return ()
        self.progression.neidan_level_updates = ()
        return packets

    def advance_solo_round(self, *, drawing_timed_out=False):
        battle=self.battle
        if battle is None or battle['phase']!='drawing':return None
        if not drawing_timed_out and not battle.get('round_end_received'):return None
        if battle['victory'] or battle['hp']==0:
            return self.finish_battle(battle['victory'], completed=True, drawing_timed_out=drawing_timed_out)
        if drawing_timed_out:
            battle['late_round_ends'] = battle.get('late_round_ends', 0) + 1
        battle.pop('round_end_received', None)
        battle['round']+=1
        battle['phase']='input'
        battle['orders']={}
        from native946_race_fabao_battle import next_round as artifact_next_round, view as artifact_view
        artifact_next_round(battle)
        artifact_packets=artifact_view(battle,self.character['id'])
        from native946_role_battle import solo_required
        required=solo_required(self)
        actor=required[0] if required else self.character['id']
        input_seconds=self.begin_input(actor)
        if not required:return (numbers(2135,battle['round']),*artifact_packets,self.resolve_round())
        return (numbers(2135,battle['round']),*artifact_packets,numbers(2151,actor,input_seconds,0),numbers(2139,2))

    def _tick_local(self, now=None):
        """Socket-thread timer: a native countdown alone only closes its UI."""
        if self.battle is None:
            from native946_inventory_features import tick as inventory_tick
            from native946_star_challenge import StarChallenge
            from native946_afei_expansion import AfeiWorld, ghost_tick
            from native946_constellations import Constellations
            from native946_timed_npcs import scene_packets as timed_npc_packets
            from native946_mall_mounts import expire_dye
            from native946_mount_spaces import check_guild_access

            changes = []
            ticks = (lambda: timed_npc_packets(self), lambda: inventory_tick(self), StarChallenge(self).tick, AfeiWorld(self).tick,
                     Constellations(self).tick, lambda: ghost_tick(self), lambda: expire_dye(self),
                     lambda: check_guild_access(self))
            for tick in ticks:
                try:
                    changes.extend(tick() or ())
                except StorageBusy:
                    # Each service tracks what has already been sent to this client.
                    # Its completed deltas must survive contention in a different service.
                    continue
            expires = self.character.get('showid_expires', 0)
            if expires:
                from time import time
                if expires <= time():
                    from native946_nice_ids import NiceIds
                    try:
                        changes.extend(NiceIds(self).refresh_identity())
                    except StorageBusy:
                        pass
            return tuple(changes) or None
        battle=self.battle
        if battle['phase']=='drawing':
            waiting = not battle.get('round_end_received')
            deadline = battle.get('input_deadline')
            if waiting and (deadline is None or (monotonic() if now is None else now) < deadline):
                return None
            try:
                with self.operation(write=True):return self.advance_solo_round(drawing_timed_out=waiting)
            except StorageBusy:return None
        if battle['phase']!='input' or battle.get('input_timeout_sent'):
            return None
        deadline=battle.get('input_deadline')
        if deadline is None or (monotonic() if now is None else now)<deadline:
            return None
        try:
            with self.operation(write=True):
                return self._timeout_round()
        except StorageBusy:
            # No input or vitals changed: retry on the next socket-thread tick.
            return None

    def _timeout_round(self):
        battle=self.battle
        actor=battle['input_actor']
        battle['input_timeout_sent']=True
        self.last_event={'action':'battle_input_timeout','round':battle['round'],'fighter_id':actor}
        # The server owns this round's defaults. The native countdown only
        # closes the input menu; c_input_end then enables the client's future
        # auto turns when the player supplied no command at all.
        orders=battle.setdefault('orders',{})
        full_timeout=not orders
        from native946_combat_control import blocked
        from native946_role_battle import solo_fighters
        missing=[f for f in solo_fighters(self).values()
                 if f['kind'] in ('hero','pet') and f['hp']>0 and not f.get('removed')
                 and f['id'] not in orders and not blocked(f,battle['round'])]
        for fighter in missing:
            orders[fighter['id']]={'kind':'attack','skill':NORMAL_ATTACK,'mana':0}
        enable_auto=full_timeout and bool(missing)
        notice=self.timeout_notice(enable_auto) if missing else ()
        replies=self.resolve_round()
        self.last_event={'action':'battle_input_timeout','round':battle['round'],'fighter_id':actor,
                         'auto_enabled':enable_auto}
        return (*notice,*replies)

    def native_action(self, actor, target, skill, damage, hp, *, defend=False, mp=None,miss=False,effect=0):
        # c_start_draw derives act_begin/act_end for the actor enclosed by
        # c_actor_begin(1/2). These are per action, not one pair per round.
        # The native cast/shape tables then decide singing, melee approach,
        # stationary/ranged attacks and return to the original position.
        replies = [numbers(2126,1),
                   packet(2157,['number','number',('array','number'),'number'],[actor,target,[target],skill])]
        if actor==self.character['id']:
            from native946_cosmetics import Cosmetics
            # The effect override follows c_actor_begin, before the action draws.
            replies.insert(2,Cosmetics(self).actor_skin(skill))
            from native946_role_growth import color_packet
            replies[2:2]=color_packet(self,skill)
        if defend:replies.append(numbers(2141,target))
        if effect:replies.append(numbers(2162,target,effect))
        if miss:replies.append(numbers(2144,target))
        else:replies.extend((numbers(2164,target,-damage,1,0), numbers(2158,target,1,hp)))
        if hp==0:replies.append(numbers(2142,target,2))  # native WAR_DIE_DEAD_BODY
        replies.append(numbers(2126,2))
        if mp is not None:replies.insert(0,numbers(2158,actor,4,mp))
        return replies

    def native_default_attack(self, target, *, pet=False):
        battle=self.battle
        if (target==0 and battle and getattr(self,'_background_round',None)==(battle['id'],battle['round'])):
            return None
        actor=battle.get('pet') if battle and pet else self.character
        if target==0 and battle and battle['phase']=='input' and actor:
            from native946_combat_control import blocked
            from native946_role_battle import solo_fighters
            if blocked(solo_fighters(self)[actor['id']],battle['round']):return None
        notify=bool(target==0 and battle and battle['phase']=='input' and actor
                    and actor['id'] not in battle.get('orders',{}) and not battle.get('timeout_notified'))
        replies=self.attack(pet=pet)
        if notify:
            return (*self.timeout_notice(True),
                    *(replies if isinstance(replies,tuple) else (replies,) if replies else ()))
        return replies

    def timeout_notice(self, enable_auto):
        if enable_auto:
            self.battle['timeout_notified']=True
            text='本回合未输入指令，已进入自动普通攻击。按F5可取消自动，并在下回合开始时重新下达指令。'
        else:
            text='本回合未完成全部指令，未操作的单位将使用普通攻击。下回合可以重新选择指令。'
        return (screen_notice(text, optimize_display=True), *((numbers(2758),) if enable_auto else ()))

    def resolve_round(self):
        from native946_role_battle import resolve_solo
        return resolve_solo(self)

    def neidan_followups(self,pet):
        import random
        from native946_neidan_legacy import effect,KINDS
        replies=[];battle=self.battle
        for row in pet.get('neidan',[]):
            if row['kind'] not in (211,212,213,214,231) or not battle['enemy_hp']:continue
            value=effect(row,pet)
            if not value['enabled'] or random.random()*100>=value['chance']:continue
            damage=value.get('damage')
            if damage is None:damage=int(battle.get('enemy_mpmax',0)*value['ratio']/100)
            damage=min(damage,battle['enemy_hp'])
            if damage<=0:continue
            battle['enemy_hp']-=damage
            replies.extend(self.native_action(pet['id'],ENEMY_ID,row['kind'],damage,battle['enemy_hp']))
            replies.append(text_notice(pet['name']+'的'+KINDS[row['kind']]['name']+'触发。'))
        # 隔山 requires another enemy; the one-target training battle must
        # not apply its secondary-target damage to the original target.
        return replies

    def neidan_counter(self,pet):
        import random
        from native946_neidan_legacy import effect
        replies=[]
        for row in pet.get('neidan',[]):
            if row['kind']!=233:continue
            value=effect(row,pet)
            if not value['enabled'] or random.random()*100>=value['chance']:continue
            for _ in range(value['counter_count']):
                damage=min(pet['attack'],self.battle['enemy_hp'])
                if not damage:break
                self.battle['enemy_hp']-=damage
                replies.extend(self.native_action(pet['id'],ENEMY_ID,NORMAL_ATTACK,damage,self.battle['enemy_hp']))
            replies.append(text_notice(pet['name']+'的借力打力触发反击。'))
        return replies

    @contextmanager
    def operation(self, *, write, memory_only=False):
        # Native replies are returned only after committing the whole action.
        # A lock error or invalid trailing fields cannot leave half a reward,
        # changed battle state, or stale item-delta cache behind.
        names=('battle','dialog_open','dialog_actions','dialog_npc','npc_service_session','workshop_query_until','workshop_production_session','cosmetic_quote','cosmetic_cart','cosmetic_dye','cosmetic_weapon','cosmetic_extension','cosmetic_carve','inventory_lock_session','last_path','_background_round','_shown_summon_id','baby_halo_choice',
               '_world_visible','_world_bucket','_quest_visible','_timed_activity_npc','_arrival_guard','_last_transition','npc_position',
               '_scene_day_effect',
               '_star_visible','_afei_visible','_sigil_visible','_encounter_steps',
               'equipment_plan_confirm','fishing_wake','fishing_bite_sent','fishing_initialized','fish_markers','fish_marker_map','inventory_expiry_check',
               'guardian_egg_session','guardian_source_session','guardian_event_session',
               'hero_point_quote','hero_resist_session','hero_resist_reset','song_firework_item')
        names=(*names,'halo_refine_until','gear_refine_quote','fashion_strength_quote','shop_session','shop_quote','guardian_lottery_icon_sent','guardian_lottery_panel_until','guardian_lottery_last_draw',
            'guardian_lottery_bag','guardian_lottery_shop_until','guardian_lottery_last_buy','ornament_shop_until',
            '_achievement_confirm','_inventory_advanced_until','classified_mall_open','classified_mall_cart',
            'classified_mall_single_quote','classified_mall_cart_quote','classified_mall_unit_offer','_auto_school')
        saved={name:deepcopy(getattr(self,name)) for name in names if hasattr(self,name)} if write else {}
        character=deepcopy(self.character) if write else None
        known=set(self.progression.known_items) if write else None
        positions=dict(getattr(self.progression,'_known_item_positions',{})) if write else None
        quantities=getattr(self.progression,'_known_item_quantities',None) if write else None
        old_grid=self.grid
        try:
            if write and not memory_only:self.progression._cached_snapshot=None
            context=nullcontext(None) if memory_only else storage_transaction(self.characters.database,write=write,batchable=write)
            with context as db:
                if write and db is not None:self.characters.pin_position(db,self.character)
                yield
        except BaseException:
            if write:
                self.character.clear();self.character.update(character)
                for name in names:
                    if name in saved:setattr(self,name,saved[name])
                    elif hasattr(self,name):delattr(self,name)
                self.progression.known_items=known
                self.progression._known_item_positions=positions
                self.progression._known_item_quantities=quantities
                self.grid=old_grid
            raise

    def npc_menu_memory_input(self,mid,reader):
        """Opening a bank/shop or listing stored items never queues a writer."""
        if mid!=1032:return False
        from native946_login import MessageReader
        try:
            probe=MessageReader(reader.data[reader.offset:])
            choice=probe.take(1)[0];callback=probe.integer();probe.finish()
        except ValueError:return False
        if callback!=getattr(self,'dialog_npc',None):return False
        if choice==255:return True
        actions=getattr(self,'dialog_actions',())
        if not self.dialog_open or not 1<=choice<=len(actions):return False
        action=actions[choice-1]
        return isinstance(action,dict) and (action.get('kind') in ('bank','pawnshop','medicine','merchant','shengzhu','task_rules','story','activity') or
                    (action.get('kind')=='cosmetic' and action['op'] in ('specmall','shop_catalog','shop_catalog_detail','shop_npcs','classified_mall')) or
                    (action.get('kind')=='storage' and action['op']=='open') or
                    (action.get('kind')=='workshop' and action['op'] not in ('join','study','advance','forge_confirm')) or
                    (action.get('kind')=='task_chain' and action['op'] in ('rules','growth','growth_page','navigate','fight')) or
                    (action.get('kind')=='npc_quest' and action['op'] in ('rules','navigate','fight')) or
                    action.get('guardian') in ('categories','page'))

    def transport_retry(self,mid,reader):
        previous=getattr(self,'_last_transition',None)
        if previous is None or mid not in (1644,1645):return False
        from native946_login import MessageReader
        probe=MessageReader(reader.data[reader.offset:])
        if mid==1644:
            key=unpack('<HHH',probe.take(6));probe.finish()
            return key==previous
        nid,index=probe.integer(),probe.integer();probe.finish()
        return isinstance(previous,tuple) and previous[1:]==(nid,index,'npc')

    def receive(self, mid, reader, *, _storage_retry=False):
        if not hasattr(self,'party_service'):
            from native946_party import service
            service(self.characters.database).attach(self)
        return self.party_service.dispatch(self,mid,reader,
                    lambda kind,source:self._receive_local(kind,source,_storage_retry=_storage_retry))

    def _receive_local(self, mid, reader, *, _storage_retry=False):
        self.ensure_progression()
        from native946_song_fireworks import SEND_FIREWORK
        writes={273,275,5418,5419,739,740,741,3420,3421,748,749,750,752,753,755,767,764,775,796,798,799,803,795,805,807,802,882,883,884,1033,1483,1484,1492,1493,1980,
                842,843,844,1350,1703,1716,1764,1765,2487,1643,1644,1645,1032,2176,2177,2178,2181,2182,2183,2184,2185,3819}
        writes.update(QUICK_MEDICINE_REQUESTS)
        from native946_guardian_chamber import WINDOW_BUY
        writes.add(WINDOW_BUY)
        writes.add(SEND_FIREWORK)
        from native946_achievements import WRITES as ACHIEVEMENT_WRITES
        writes.update(ACHIEVEMENT_WRITES)
        from native946_cosmetics import WRITES as COSMETIC_WRITES
        from native946_mall_uses import WRITES as MALL_USE_WRITES
        from native946_refinement import WRITES as REFINEMENT_WRITES
        from native946_fashion_refinement import WRITES as FASHION_REFINEMENT_WRITES
        from native946_nice_ids import WRITES as NICE_ID_WRITES
        from native946_gear_sockets import WRITES as SOCKET_WRITES
        from native946_jing_gems import WRITES as JING_GEM_WRITES
        from native946_guardian_ornaments import WRITES as ORNAMENT_WRITES
        from native946_neidan import WRITES as NEW_NEIDAN_WRITES
        from native946_guardian_jade import WRITES as JADE_WRITES
        from native946_guardian_equipment import REQUESTS as GUARDIAN_GEAR_WRITES
        writes.update(COSMETIC_WRITES)
        writes.update(MALL_USE_WRITES)
        writes.update(REFINEMENT_WRITES)
        writes.update(FASHION_REFINEMENT_WRITES)
        writes.update(NICE_ID_WRITES)
        writes.update(SOCKET_WRITES|GUARDIAN_GEAR_WRITES)
        writes.update(JING_GEM_WRITES|ORNAMENT_WRITES)
        writes.update(NEW_NEIDAN_WRITES)
        writes.update(JADE_WRITES)
        from native946_inventory_features import WRITES as INVENTORY_WRITES
        writes.update(INVENTORY_WRITES)
        from native946_temp_inventory import REQUESTS as TEMP_INVENTORY_REQUESTS
        writes.update(TEMP_INVENTORY_REQUESTS)
        from native946_tasks import WRITES as TASK_WRITES
        writes.update(TASK_WRITES)
        from native946_guardian_ui import WRITES as GUARDIAN_UI_WRITES
        writes.update(GUARDIAN_UI_WRITES)
        from native946_skillbooks import REQUESTS as BOOK_WRITES
        writes.update(BOOK_WRITES)
        from native946_guardian_acquisition import WRITES as ACQUISITION_WRITES
        writes.update(ACQUISITION_WRITES)
        from native946_guardian_lottery import WRITES as LOTTERY_WRITES
        writes.update(LOTTERY_WRITES)
        from native946_shops import WRITES as SHOP_WRITES
        writes.update(SHOP_WRITES)
        from native946_skill6 import WRITES as SKILL6_WRITES
        writes.update(SKILL6_WRITES)
        from native946_growth_battle import WRITES as GROWTH_BATTLE_WRITES
        writes.update(GROWTH_BATTLE_WRITES)
        from native946_innate_training import WRITES as INNATE_TRAINING_WRITES
        writes.update(INNATE_TRAINING_WRITES)
        from native946_friends import WRITES as FRIEND_WRITES
        writes.update(FRIEND_WRITES)
        from native946_race_fabao import WRITES as RACE_FABAO_WRITES
        writes.update(RACE_FABAO_WRITES)
        from native946_race_fabao_cultivation import WRITES as RACE_CULTIVATION_WRITES
        writes.update(RACE_CULTIVATION_WRITES)
        from native946_consignment import WRITES as CARD_WRITES
        writes.update(CARD_WRITES)
        from native946_guilds import WRITES as GUILD_WRITES
        writes.update(GUILD_WRITES)
        from native946_guild_construction import WRITES as GUILD_BUILD_WRITES
        writes.update(GUILD_BUILD_WRITES)
        from native946_guild_war import WRITES as GUILD_WAR_WRITES
        writes.update(GUILD_WAR_WRITES)
        from native946_dungeons import PUZZLE_REQUEST
        writes.add(PUZZLE_REQUEST)
        memory_only=mid==3819 or mid==1643 and getattr(self.characters,'position_writer',None) is not None
        if self.npc_menu_memory_input(mid,reader):memory_only=True
        if mid in (742,1985,1986,2757) and self.progression._cache_enabled:
            memory_only=True
        if self.progression._cache_enabled and self.memory_only_battle_input(mid,reader):
            memory_only=True
        original_payload=reader.data[reader.offset:]
        committed=False
        try:
            if mid == 2184 and not _storage_retry:
                reader.finish()
                self.last_event = None
                battle = self.battle
                # Background acceptance can emit input-only ACKs before the real draw.
                if battle and battle.get('guardian_acceptance') and battle.get('_guardian_input_draw_acks', 0):
                    battle['_guardian_input_draw_acks'] -= 1
                    return None
                if battle and battle.get('late_round_ends', 0):
                    battle['late_round_ends'] -= 1
                    return None
                if not battle or battle['phase'] != 'drawing':
                    return None
                # Retain the received ACK if the write transaction must retry.
                battle['round_end_received'] = True
            # A successful transport receipt is checked before pin_position
            # reserves another revision or opens a writer transaction.
            if self.transport_retry(mid,reader):
                self.last_event=None
                return None
            self._serialize_after_commit=True
            try:
                with self.operation(write=mid in writes,memory_only=memory_only):
                    result=self._receive(mid,reader)
                committed=True
            finally:self._serialize_after_commit=False
            if not self.needs_render(result):return result
            for render_attempt in range(2):
                try:
                    with self.operation(write=True,memory_only=True):return self.render_packets(result)
                except StorageBusy:
                    if render_attempt==0:sleep(.004)
            essential=self.fallback_packets(result)
            return (*(essential if isinstance(essential,tuple) else (essential,) if essential else ()),
                    text_notice('操作已保存，附加面板暂时繁忙，可稍后重新打开。'))
        except StorageBusy as error:
            if not committed and not _storage_retry and (mid in (SEND_FIREWORK,273,275,5418,5419,753,807,842,843,844,1032,1350,1716,2487,2176,2177,2178,2181,2182,2183,2184,2185) or mid in QUICK_MEDICINE_REQUESTS or mid in SHOP_WRITES or mid in COSMETIC_WRITES or mid in BOOK_WRITES or mid in LOTTERY_WRITES or mid in NICE_ID_WRITES):
                from native946_runtime import METRICS
                from native946_login import MessageReader
                METRICS.increment('native_storage_retries');sleep(.004)
                result=self.receive(mid,MessageReader(original_payload),_storage_retry=True)
                if not (self.last_event or {}).get('storage_busy'):METRICS.increment('native_storage_retry_recovered')
                return result
            self.last_event={'action':'action_rejected','reason':str(error),'storage_busy':True}
            notice=text_notice(str(error))
            if mid==1643:
                return (notice,packet(1606,['number','word','word'],[self.character['id'],self.character['x'],self.character['y']]))
            retry=self.retry_battle_input(mid)
            return (notice,*retry) if retry else notice
        except ValueError as error:
            self.last_event={'action':'action_rejected','reason':str(error)}
            notice=text_notice(str(error));retry=self.retry_battle_input(mid)
            return (notice,*retry) if retry else notice

    def _receive(self, mid, reader):
        self.last_event = None
        from native946_song_fireworks import SEND_FIREWORK, release as release_fireworks
        if mid == SEND_FIREWORK:
            return release_fireworks(self, reader)
        from native946_premium_items import AUTO_MEDICAL_TIME_REQUEST, auto_medical_time
        from native946_reserve_food import FAST_USE, open_selection
        if mid == AUTO_MEDICAL_TIME_REQUEST:
            return auto_medical_time(self, reader)
        if mid == FAST_USE:
            return open_selection(self, reader)
        from native946_temp_inventory import PAGE as TEMP_PAGE, REQUESTS as TEMP_REQUESTS, receive as temp_receive
        if mid in TEMP_REQUESTS:return temp_receive(self, mid, reader)
        from native946_achievements import REQUEST_TYPES as ACHIEVEMENT_REQUESTS, receive as achievement_receive
        if mid==1031 and (session:=getattr(self,'npc_service_session',None)) and session['kind']=='pawnshop' and session.get('pending'):
            from native946_login import MessageReader
            probe=MessageReader(reader.data[reader.offset:])
            answer,token=probe.take(1)[0],probe.integer()
            probe.finish()
            if token==session['pending'][0]:
                from native946_npc_services import NpcServices
                from native946_pawnshop import Pawnshop
                return Pawnshop(NpcServices(self)).confirm(answer,token)
        if mid in ACHIEVEMENT_REQUESTS:
            return achievement_receive(self, mid, reader)
        from native946_friends import REQUESTS as FRIEND_REQUESTS, Friends
        if mid in FRIEND_REQUESTS:return Friends(self).receive(mid,reader)
        from native946_race_fabao import REQUESTS as RACE_FABAO_REQUESTS, receive as race_fabao_receive
        if mid in RACE_FABAO_REQUESTS:return race_fabao_receive(self,mid,reader)
        from native946_race_fabao_cultivation import REQUESTS as RACE_CULTIVATION_REQUESTS, receive as race_cultivation_receive
        if mid in RACE_CULTIVATION_REQUESTS:return race_cultivation_receive(self,mid,reader)
        if mid==5259:
            from native946_race_fabao_battle import submit, view
            from native946_role_battle import solo_fighters

            if not self.battle:raise ValueError('只有战斗中才能释放种族法宝。')
            cid=self.character['id']
            submit(self.battle,solo_fighters(self),cid,reader)
            return view(self.battle,cid)
        from native946_guilds import REQUESTS as GUILD_REQUESTS, receive as guild_receive
        if mid in GUILD_REQUESTS:return guild_receive(self,mid,reader)
        from native946_guild_construction import REQUESTS as GUILD_BUILD_REQUESTS, receive as guild_build_receive
        if mid in GUILD_BUILD_REQUESTS:return guild_build_receive(self,mid,reader)
        from native946_guild_war import REQUESTS as GUILD_WAR_REQUESTS, receive as guild_war_receive
        if mid in GUILD_WAR_REQUESTS:return guild_war_receive(self,mid,reader)
        from native946_dungeons import PUZZLE_REQUEST, receive_puzzle
        if mid == PUZZLE_REQUEST:return receive_puzzle(self,reader)
        from native946_innate_training import InnateTraining, REQUESTS as INNATE_TRAINING_REQUESTS
        if mid in INNATE_TRAINING_REQUESTS:return InnateTraining(self).receive(mid,reader)
        from native946_growth_battle import GrowthBattle, REQUESTS as GROWTH_BATTLE_REQUESTS, ICON as GROWTH_BATTLE_ICON
        if mid in GROWTH_BATTLE_REQUESTS:return GrowthBattle(self).receive(mid,reader)
        from native946_skill6 import REQUESTS as SKILL6_REQUESTS, receive as skill6_receive
        if mid in SKILL6_REQUESTS:return skill6_receive(self,mid,reader)
        from native946_gear_sockets import REQUESTS as SOCKET_REQUESTS, receive as socket_receive
        if mid in SOCKET_REQUESTS:return socket_receive(self,mid,reader)
        from native946_jing_gems import REQUESTS as GEM_REQUESTS, receive as gem_receive
        if mid in GEM_REQUESTS:return gem_receive(self,mid,reader)
        from native946_guardian_ornaments import REQUESTS as ORNAMENT_REQUESTS, receive as ornament_receive
        if mid in ORNAMENT_REQUESTS:return ornament_receive(self,mid,reader)
        from native946_guardian_equipment import REQUESTS as GUARDIAN_GEAR_REQUESTS, receive as guardian_gear_receive
        if mid in GUARDIAN_GEAR_REQUESTS:return guardian_gear_receive(self,mid,reader)
        from native946_inventory_locks import InventoryLocks
        locks=InventoryLocks(self)
        if mid==882:
            operation,iid=reader.integer(),reader.integer();reader.finish()
            return locks.open(operation,iid)
        if mid in (883,884):
            reader.finish()
            return locks.reset() if mid==883 else locks.open(4)
        if mid==1033 and locks.matches(reader):return locks.input(reader)
        from native946_inventory import InventoryButtons,REQUESTS as INVENTORY_REQUESTS
        if mid in INVENTORY_REQUESTS:return InventoryButtons(self).receive(mid,reader)
        from native946_guardian_acquisition import GuardianAcquisition,REQUESTS as ACQUISITION_REQUESTS
        if mid in ACQUISITION_REQUESTS:return GuardianAcquisition(self).receive(mid,reader)
        from native946_anniversary_cards import REQUESTS as ANNIVERSARY_REQUESTS, receive as anniversary_receive
        if mid in ANNIVERSARY_REQUESTS:return anniversary_receive(self,mid,reader)
        from native946_guardian_lottery import GuardianLottery,REQUESTS as LOTTERY_REQUESTS,ICON as LOTTERY_ICON
        if mid in LOTTERY_REQUESTS:
            if mid!=1390:return GuardianLottery(self).receive(mid,reader)
            from native946_login import MessageReader
            probe=MessageReader(reader.data[reader.offset:]);icon=probe.integer();probe.finish()
            if icon==LOTTERY_ICON:return GuardianLottery(self).receive(mid,reader)
            if icon==GROWTH_BATTLE_ICON:return GrowthBattle(self).panel()
            from native946_classified_mall import ClassifiedMall, ICON as CLASSIFIED_MALL_ICON
            if icon==CLASSIFIED_MALL_ICON:return ClassifiedMall(self).open()
        from native946_guardian_ui import GuardianUI,REQUESTS as GUARDIAN_UI_REQUESTS
        from native946_guardian_jade import Cultivation,REQUESTS as JADE_REQUESTS
        if mid in JADE_REQUESTS:return Cultivation(self).receive(mid,reader)
        if mid in GUARDIAN_UI_REQUESTS:return GuardianUI(self).receive(mid,reader)
        from native946_tasks import TaskJournal
        from native946_shops import Shops,REQUESTS as SHOP_REQUESTS
        if mid in SHOP_REQUESTS:return Shops(self).receive(mid,reader)
        handled,task_reply=TaskJournal(self).receive(mid,reader)
        if handled:return task_reply
        from native946_activities import Activities
        handled,activity_reply=Activities(self).receive(mid,reader)
        if handled:return activity_reply
        if mid==1980:
            from native946_task_chain import receive as chain_receive
            handled,reply=chain_receive(self,mid,reader)
            if handled:return reply
            from native946_login import MessageReader
            probe=MessageReader(reader.data[reader.offset:]);qid=probe.integer();probe.finish()
            if qid>=930001:
                reader.integer();reader.finish()
                from native946_npc_quests import Quests
                quests=Quests(self);quests.cancel(qid-930000)
                return self.defer_packets(lambda:(*self.progression.task_packets(),*quests.scene_packets(),numbers(1966,qid)))
        from native946_skillbooks import SkillBooks,REQUESTS as BOOK_REQUESTS,SKILLS as BOOK_SKILLS
        if mid in BOOK_REQUESTS:
            from native946_login import MessageReader
            from native946_guardian_innate import SKILLS as INNATE_SKILLS
            probe=MessageReader(reader.data[reader.offset:])
            probe.integer();skill=probe.integer();probe.finish()
            if skill in BOOK_SKILLS or skill in INNATE_SKILLS:return SkillBooks(self.summons).receive(self,mid,reader)
        from native946_guardian_rules import PENDING_REQUESTS,reject_unverified
        if mid in PENDING_REQUESTS:return reject_unverified(self,mid,reader)
        if mid==2488:
            wid=reader.take(1)[0];reader.finish()
            from native946_crafting import Crafting
            return Crafting(self).open(wid)
        if mid in (273,275):
            equip,material,medicine,owner=[reader.integer() for _ in range(4)]
            mode=reader.take(1)[0] if mid==275 else 0;reader.finish()
            from native946_crafting import Crafting
            return Crafting(self).produce(mid,equip,material,medicine,owner,mode)
        if mid in (5418,5419):
            equip=reader.integer();materials=[reader.integer() for _ in range(reader.take(1)[0])]
            medicine,owner=reader.integer(),reader.integer()
            mode=reader.take(1)[0] if mid==5419 else 0;reader.finish()
            from native946_crafting import Crafting
            return Crafting(self).produce(273 if mid==5418 else 275,equip,materials,medicine,owner,mode)
        from native946_cosmetics import Cosmetics, REQUESTS as COSMETIC_REQUESTS, DATA as COSMETIC_DATA
        from native946_mall_uses import MallUses, REQUESTS as MALL_USE_REQUESTS
        if mid in MALL_USE_REQUESTS:
            if self.battle is not None and mid!=112:raise ValueError('请先完成当前战斗。')
            return MallUses(self).receive(mid,reader)
        if mid in COSMETIC_REQUESTS:
            if self.battle:raise ValueError('请先结束战斗再办理外观业务。')
            return Cosmetics(self).receive(mid,reader)
        from native946_refinement import Refinement, REQUESTS as REFINEMENT_REQUESTS
        if mid in REFINEMENT_REQUESTS:
            if self.battle:raise ValueError('请先结束战斗再炼化。')
            return Refinement(self).receive(mid,reader)
        from native946_fashion_refinement import FashionRefinement, REQUESTS as FASHION_REFINEMENT_REQUESTS
        if mid in FASHION_REFINEMENT_REQUESTS:
            if self.battle:raise ValueError('请先结束战斗再操作时装。')
            return FashionRefinement(self).receive(mid,reader)
        from native946_nice_ids import NiceIds, REQUESTS as NICE_ID_REQUESTS
        if mid in NICE_ID_REQUESTS:
            return NiceIds(self).receive(mid,reader)
        if mid in (289,3597,2487):
            from native946_workshops import Workshops
            wid=reader.take(1)[0] if mid==2487 else None;reader.finish()
            workshop=Workshops(self)
            if mid==2487:return workshop.join(wid)
            if mid==3597:return workshop.open()
            if self.battle:raise ValueError('请先结束战斗再查看作坊。')
            return packet(230,[('array','zuofang_longquan'),('array','zuofang_business'),('array','zuofang_official')],[[],[],[]])
        from native946_workshops import DATA as WORKSHOP_DATA
        if str(mid) in WORKSHOP_DATA['unavailable_requests']:
            for field in WORKSHOP_DATA['unavailable_requests'][str(mid)]:
                if len(field)>2:
                    for _ in range(reader.take(1)[0]):reader.integer()
                elif field[1]=='byte':reader.take(1)
                else:reader.integer()
            reader.finish()
            from native946_workshops import Workshops
            return Workshops(self).unavailable('作坊加工或培养')
        if mid==1983:
            reader.integer();reader.finish()
            raise ValueError('原版任务流程和奖励结算尚未核实，暂不能领取；金钱和活力未扣除。')
        if mid==842:
            args=[reader.integer() for _ in range(5)];reader.finish()
            from native946_npc_services import NpcServices
            return NpcServices(self).storage_transfer(*args)
        if mid in (3236,3816):
            nid,page=reader.integer(),reader.integer();reader.finish()
            from native946_npc_services import NpcServices
            return NpcServices(self).storage_permission(nid,page,mid==3816)
        if mid in (4512,4513):
            from native946_npc_services import NpcServices
            warehouse=NpcServices(self)
            bag_type=reader.integer()
            if mid==4512:
                page=reader.integer();reader.finish()
                return warehouse.storage_search_open(bag_type,page)
            item_types=[reader.integer() for _ in range(reader.take(1)[0])];reader.finish()
            return warehouse.storage_search(bag_type,item_types)
        if mid in (843,844):
            item_id,count=reader.integer(),reader.integer();reader.finish()
            from native946_npc_services import NpcServices
            from native946_pawnshop import Pawnshop
            pawnshop=Pawnshop(NpcServices(self))
            return pawnshop.quote_pawn(item_id,count) if mid==843 else pawnshop.exchange(mid,item_id,count)
        if mid==1350:
            operation,amount=reader.integer(),reader.integer();reader.finish()
            from native946_npc_services import NpcServices
            return NpcServices(self).bank(operation,amount)
        if mid==1703:
            npc,reward,category=reader.integer(),reader.integer(),reader.integer();reader.finish()
            from native946_npc_services import NpcServices
            return NpcServices(self).restore_pearl(npc,reward,category)
        if mid==1716:
            npc,kind,amount=reader.integer(),reader.integer(),reader.integer();reader.finish()
            from native946_npc_services import NpcServices
            return NpcServices(self).buy(npc,kind,amount)
        if mid==2317:
            reader.finish()
            return self.battle_call_list()
        if mid==729:
            mode=reader.integer();reader.finish()
            return self.progression.resistance_packet(mode)
        if mid in (3420,3421):
            from native946_training import learn,upgrade,upgrade_packet,list_packet
            skill=reader.integer()
            count=reader.integer() if mid==3421 else None
            reader.finish()
            if self.battle:raise ValueError('请先完成当前战斗。')
            if mid==3420:learn(self.progression,skill)
            else:upgrade(self.progression,skill,count)
            return self.defer_packets(lambda:(*self.progression.attribute_packets(),list_packet(self.progression),upgrade_packet(self.progression,skill)))
        if mid==741:
            points=[reader.integer() for _ in range(13)]
            if any(n<0 or n>115 for n in points):raise ValueError('抗性点分配无效。')
            reader.finish()
            if self.battle:raise ValueError('战斗中不能修改抗性培养。')
            from native946_training import allocate
            allocate(self.progression,points)
            return self.defer_packets(lambda:(*self.progression.attribute_packets(),self.progression.resistance_packet(1)))
        if mid in (753,755,765,766,767,5226,5227,5228,5229):
            sid,nid=reader.integer(),reader.integer()
            options=(reader.integer(),reader.integer()) if mid==5226 else ()
            reader.finish()
            from native946_neidan import META as NEW_NEIDAN, NewNeidan
            if nid in NEW_NEIDAN:return NewNeidan(self.summons).receive(self,mid,sid,nid,options)
            if self.battle:raise ValueError('战斗中不能培养内丹。')
            if mid in (753,755,767) and nid not in NEW_NEIDAN:
                from native946_neidan_legacy import LegacyNeidan
                pills=LegacyNeidan(self.summons)
                if mid==753:pills.convert(sid,nid)
                elif mid==755:pills.drop(sid,nid)
                else:pills.reborn(sid,nid)
                self.last_event={'action':'summon_neidan_request','message_id':mid,'summon_id':sid,'neidan_id':nid}
                # Return the preserved pill item only after its transaction
                # commits; refresh the installed list in that same response.
                return self.defer_packets(lambda:(
                    *(self.progression.item_packets() if mid==755 else ()),
                    self.summons.neidan_packet(sid),*self.summons.info_packets(sid)))
            self.summons.get(sid)
            raise ValueError('该内丹操作暂不可用，本次未消耗资源。')
        from native946_guardian_chamber import GuardianChamber, WINDOW_REQUESTS
        if mid in WINDOW_REQUESTS:
            return GuardianChamber(self).native_request(mid,reader)
        if mid == 1485:
            from native946_school import School
            stunt=reader.integer();reader.finish()
            return School(self).fly_home(stunt=stunt)
        if mid in (1764,1766):
            from native946_school import School
            scene=reader.integer();index=reader.integer() if mid==1764 else None
            reader.finish()
            return School(self).fly_points(scene,index)
        if mid in (1765,1767):
            from native946_school import School
            requested=reader.integer() if mid==1765 else None
            reader.finish()
            return School(self).fly_home(requested)
        if mid==1644:
            sid,x,y=unpack('<HHH',reader.take(6));reader.finish()
            key=(sid,x,y)
            if key==self._last_transition:return None
            if sid!=self.character['map_id']:raise ValueError('请等待当前地图加载完成。')
            from native946_world import world
            route=world().physical_route(sid,x,y,(self.character['x'],self.character['y']))
            if route['id']=='mount_space_exit':
                from native946_mount_spaces import exit_space
                return exit_space(self,key)
            return self.change_scene(route,key)
        if mid==1645:
            nid,index=reader.integer(),reader.integer();reader.finish()
            previous=getattr(self,'_last_transition',None)
            if isinstance(previous,tuple) and previous[1:]==(nid,index,'npc'):return None
            from native946_world import world
            c=self.character
            route=world().npc_route(c['map_id'],nid,index,(c['x'],c['y']),c)
            return self.change_scene(route,(c['map_id'],nid,index,'npc'))
        if mid==2757:
            reader.finish()
            if (self.battle and getattr(self,'_background_round',None)==
                    (self.battle['id'],self.battle['round'])):
                # Input-end is queued, whereas cancel-autofight is immediate.
                # This request confirms the queued native no-operation handler
                # has actually opened its panel; cancel at that point as well.
                return numbers(5349)
            # war_auto hides its legacy widget and waits for this F5 reply
            # before dlg_auto_fight_2 constructs the visible native panel.
            from native946_mall_uses import MallUses
            return MallUses(self).f5_info()
        if mid in (727,732,733,734,750,752,760,761,762,764,775):
            sid = reader.integer()
            if mid == 750:
                argument = reader.integer()
            elif mid == 752:
                argument = reader.string().decode('gb18030')
            elif mid == 764:
                argument = [reader.integer() for _ in range(4)]
            reader.finish()
            from native946_summons import RELEASE
            if mid == 750 and argument == RELEASE:
                if self.battle is not None:
                    raise ValueError('战斗中无法放生守护。')
                from native946_guardian_score import highest_score
                from native946_progression import DATA as PROGRESSION_DATA
                from native946_reserve_food import sync as reserve_packet

                pet = self.summons.release(sid)
                with self.progression.transaction(write=False) as db:
                    score = highest_score(db, self.progression.cid)
                self.last_event = {'action':'summon_released', 'message_id':mid, 'summon_id':sid}
                return (
                    *((numbers(718, sid, 0),) if pet['Fight'] else ()),
                    *((numbers(719, sid, 0),) if pet['Show'] else ()),
                    packet(714, ['summon_list', 'number'], [pet, 2]),
                    *(self.sync_summon_scene() if pet['Show'] else ()),
                    *((reserve_packet(self, None),) if pet['Fight'] else ()),
                    numbers(695, int(PROGRESSION_DATA['attributes']['SummonScore']), score),
                )
            self.summons.get(sid)  # Check ownership before every read or write.
            if mid in (750,752,764,775) and self.battle is not None:
                raise ValueError('战斗中不能修改守护状态、姓名或属性。')
            if mid == 750:
                from native946_reserve_food import restore_guardian_slot

                with self.progression.transaction() as db:
                    self.summons.status(sid, argument)
                    if argument == 1:
                        restore_guardian_slot(db, self, self.summons.get(sid))
            elif mid == 752:self.summons.rename(sid, argument)
            elif mid == 764:self.summons.allocate(sid, argument)
            elif mid == 775:self.summons.level_up(sid)
            self.last_event = {'action':'summon_request', 'message_id':mid, 'summon_id':sid}
            if mid == 732:
                return self.summons.base_packet(sid)
            if mid == 734:
                return self.summons.resistance_packet(sid)
            if mid == 760:
                return self.summons.neidan_packet(sid)
            if mid == 761:
                return self.summons.stunt_packet(sid)
            if mid == 762:
                return packet(712, ['number','byte'], [sid,0])
            if mid in (727,733) or self.battle is not None:
                return self.summons.info_packets(sid)
            from native946_reserve_food import sync as reserve_packet
            return (*self.summons.sync(), *self.sync_summon_scene(), reserve_packet(self, self.summons.active()))
        if mid in QUICK_MEDICINE_REQUESTS:
            op = reader.integer()
            sid = reader.integer() if mid == 1489 else None
            reader.finish()
            if op not in (0, 1):
                raise ValueError('补药类型无效。')
            if self.battle is not None:
                raise ValueError('请先完成当前战斗。')
            resource = ('hp', 'mp')[op]
            if sid is None:
                from native946_progression import DATA as PROGRESSION_DATA
                changes = self.progression.quick_recover(resource)
                if not changes:
                    return None
                vitals = [numbers(695, int(PROGRESSION_DATA['attributes'][attr.title()]), value)
                          for attr, value in changes.items()]
                vitals.append(numbers(694))
            else:
                if not self.summons.quick_recover(sid, resource):
                    return None
                vitals = self.summons.info_packets(sid)
            self.last_event = {'action': 'quick_medicine', 'message_id': mid, 'resource': resource}
            return (*vitals, *self.progression.item_packets())
        if mid in (739,748,749,803,795,805,807,802,1483,1484,1492,1493,1980):
            healed_summon=None
            guardian_levels_changed=False
            appearance_changed=False
            if self.battle is not None:
                raise ValueError('请先完成当前战斗。')
            if mid==739:
                self.progression.allocate([reader.integer() for _ in range(4)])
            elif mid==748:
                self.progression.level_up()
                guardian_levels_changed=self.summons.reconcile_levels()
            elif mid==749:
                skill,times=reader.integer(),reader.integer()
                reader.finish()
                from native946_school import SCHOOL_SKILLS, School
                if skill in SCHOOL_SKILLS or (getattr(self,'npc_service_session',None) or {}).get('kind')=='school':
                    return School(self).learn(skill,times)
                self.progression.learn(skill,times)
            elif mid in (803,795):
                source,source_slot,destination,slot=[reader.integer() for _ in range(4)]
                reader.finish()
                if source == TEMP_PAGE:
                    from native946_temp_inventory import take_slot, move_packets, CAPACITY
                    if not 1 <= source_slot <= CAPACITY:
                        raise ValueError('摘星袋格子无效。')
                    moved = take_slot(self.progression, source_slot, None if (destination, slot) == (8, -1) else (destination, slot))
                    return self.defer_packets(lambda: move_packets(self.progression, moved))
                from native946_inventory import EQUIP_PAGES, active_page
                from native946_mall_bags import pages as bag_pages
                with self.progression.transaction(write=False) as db:
                    cid=self.progression.cid
                    item=next((i for i in self.progression.inventory_rows(db)
                               if i['page']==source and i['slot']==source_slot),None)
                    ordinary_move=source in bag_pages(db,cid,True) and destination in bag_pages(db,cid)
                    equipped=active_page(db,cid) if source in EQUIP_PAGES or destination in EQUIP_PAGES else None
                if item is None:raise ValueError('原位置已没有该物品。')
                if source in (*range(25,30),*range(5800,5816)) or destination in (*range(25,30),*range(5800,5816)):
                    Cosmetics(self).move(item['id'],destination,slot)
                    return self.defer_packets(lambda:(*self.progression.item_packets(),*Cosmetics(self).visual_packets()))
                if slot==-1 and destination==8:
                    destination,slot=self.progression.free_bag_slot()
                changed=self.progression.move_item(item['id'],destination,slot,source,stack=mid==803)
                if ordinary_move:
                    self.last_event={'action':'progression_changed','message_id':mid}
                    return self.progression.item_packets(item_ids=changed)
                appearance_changed=equipped in (source,destination)
            elif mid==805:
                item,page,slot,amount=[reader.integer() for _ in range(4)]
                self.progression.split(item,page,slot,amount)
            elif mid==807:
                target,item=reader.integer(),reader.integer()
                reader.finish()
                from native946_guardian_equipment import BLUEPRINTS, ITEMS as GUARDIAN_GEAR, SPECIAL_UNLOCK, blueprint, unlock_special, use as use_guardian_gear
                from native946_inventory_features import ITEM_TYPES as INVENTORY_FEATURE_TYPES,use as inventory_feature_use
                feature_snapshot=self.progression.snapshot()
                feature_item=next((i for i in feature_snapshot['items'] if i['id']==item),None)
                from native946_double_time import MEDICINES, RESTORE, use_medicine
                if feature_item and (feature_item['type'] in MEDICINES or feature_item['type'] == RESTORE):
                    if target not in (0, self.character['id']):raise ValueError('请由人物使用催长丹。')
                    return use_medicine(self, item)
                from native946_anniversary_cards import CARD_ITEMS as ANNIVERSARY_CARD_ITEMS, reject_card_use
                if feature_item and feature_item['type'] in ANNIVERSARY_CARD_ITEMS:
                    reject_card_use(feature_snapshot['level'])
                from native946_song_fireworks import ITEM_TYPES as FIREWORK_TYPES, open_panel as open_fireworks
                if feature_item and feature_item['type'] in FIREWORK_TYPES:
                    if target not in (0,self.character['id']):raise ValueError('请由人物使用烟花。')
                    return open_fireworks(self,item)
                from native946_reserve_food import FOODS, use as use_reserve_food
                if feature_item and feature_item['type'] in FOODS:
                    return use_reserve_food(self, target, item)
                from native946_constellations import GROUP_PRIZES, SIGIL_ITEMS, use as use_sigil
                if feature_item and feature_item['type'] in SIGIL_ITEMS:return use_sigil(self,target,item)
                if feature_item and str(feature_item['type']) in GROUP_PRIZES:
                    if target not in (0,self.character['id']):raise ValueError('请由人物打开星宿礼包。')
                    from native946_constellation_battle import open_prize
                    return open_prize(self,item)
                from native946_ordinary_treasure import MAP_TYPES, dig
                if feature_item and feature_item['type'] in MAP_TYPES:
                    if target not in (0,self.character['id']):raise ValueError('请由人物使用藏宝图。')
                    return dig(self,item)
                if feature_item and feature_item['type'] in BLUEPRINTS:
                    if target not in (0,self.character['id']):raise ValueError('请将图纸使用对象切换为人物。')
                    return blueprint(self,item)
                if feature_item and str(feature_item['type']) in GUARDIAN_GEAR:
                    if target in (0,self.character['id']):raise ValueError('请将装备使用对象切换为守护。')
                    return use_guardian_gear(self,target,item)
                if feature_item and feature_item['type']==SPECIAL_UNLOCK:
                    if target in (0,self.character['id']):raise ValueError('请将万象浊灵使用对象切换为守护。')
                    return unlock_special(self,target,item)
                from native946_innate_training import EXTRA_BOOKS,JING_BOOKS,use_jing_book
                if feature_item and feature_item['type'] in (JING_BOOKS.keys()|EXTRA_BOOKS.keys()):
                    if target in (0,self.character['id']):raise ValueError('请把天演图用在守护身上。')
                    return use_jing_book(self,target,item)
                if feature_item and feature_item['type'] in INVENTORY_FEATURE_TYPES:
                    from native946_role_systems import CARD_ITEMS,use as role_use
                    if feature_item['type'] in CARD_ITEMS:return role_use(self,item,target)
                    if target not in (0,self.character['id']):raise ValueError('请将道具使用对象切换为人物。')
                    return inventory_feature_use(self,item)
                from native946_guardian_acquisition import GuardianAcquisition,ITEM_TYPES as GUARDIAN_ACQUISITION_TYPES
                acquisition_item=next((i for i in self.progression.snapshot()['items'] if i['id']==item),None)
                from native946_guardian_lottery import GuardianLottery,EXPERIENCE_ITEM,SCORE_VOUCHER
                if acquisition_item and acquisition_item['type']==SCORE_VOUCHER:
                    if target not in (0,self.character['id']):raise ValueError('请将天缘积分道具的使用对象切换为人物。')
                    return GuardianLottery(self).use_score_voucher(item)
                if acquisition_item and acquisition_item['type']==EXPERIENCE_ITEM:
                    if target in (0,self.character['id']):raise ValueError('请将百年元婴丹的使用对象切换为守护。')
                    return GuardianLottery(self).use_experience(target,item)
                if acquisition_item and acquisition_item['type'] in GUARDIAN_ACQUISITION_TYPES:
                    if target not in (0,self.character['id']):raise ValueError('请将守护蛋的使用对象切换为人物。')
                    return GuardianAcquisition(self).use(item)
                if target not in (0,self.character['id']):
                    from native946_mall_guardians import GuardianAppearance, use_rule
                    if acquisition_item and use_rule(acquisition_item['type']):
                        return GuardianAppearance(self).use(target,item)
                    self.summons.use_item(target,item)
                    healed_summon=target
                from native946_progression import GEAR
                from native946_mall_uses import DATA as MALL_USES
                snapshot=self.progression.snapshot()
                owned=next((i for i in snapshot['items'] if i['id']==item),None)
                from native946_newbie_rewards import ITEM_TYPES as NEWBIE_ITEMS, use as use_newbie_item
                if healed_summon is not None:pass
                elif owned and owned['type'] in NEWBIE_ITEMS:
                    reader.finish()
                    return use_newbie_item(self,item)
                elif owned and (str(owned['type']) in COSMETIC_DATA['skills'] or str(owned['type']) in COSMETIC_DATA['goods'] or (str(owned['type']) in MALL_USES['rules'] and str(owned['type']) in COSMETIC_DATA['items'])):
                    reader.finish()
                    return Cosmetics(self).use(item)
                elif owned and (owned['type'] in GEAR or owned['id'] in snapshot['equipment']):
                    appearance_changed=True
                    from native946_inventory import EQUIP_PAGES
                    if owned['page'] in EQUIP_PAGES:
                        page,slot=self.progression.free_bag_slot(snapshot)
                        self.progression.move_item(item,page,slot)
                    else:
                        slot=snapshot['equipment'][item]['slot'] if item in snapshot['equipment'] else GEAR[owned['type']][0]
                        self.progression.move_item(item,snapshot['equipment_page'],slot)
                else:self.progression.use_item(item)
            elif mid==802:
                count=reader.take(1)[0]
                self.progression.merge([reader.integer() for _ in range(count)])
            elif mid==1483:
                skill=reader.integer();slot=reader.take(1)[0]
                self.progression.shortcut(slot,skill)
            elif mid==1484:
                self.progression.shortcut(reader.take(1)[0])
            else:
                if reader.integer()!=910001 or not getattr(self, 'practice_trainer', False):
                    raise ValueError('当前没有这个基础任务。')
                if mid==1980:self.progression.cancel_quest()
                else:self.progression.accept_quest()
            reader.finish()
            self.last_event={'action':'progression_changed','message_id':mid}
            if appearance_changed:
                # Team movement can hold the party lock while it awaits a durable commit.
                # Publish equipment changes only after releasing this player's writer slot.
                return self.defer_packets(lambda: (*self.progression.sync(), *Cosmetics(self).visual_packets()))
            return (*self.progression.sync(),
                    *(self.summons.sync() if guardian_levels_changed else ()),
                    *((*self.summons.info_packets(healed_summon),self.summons.neidan_packet(healed_summon),self.summons.stunt_packet(healed_summon)) if healed_summon is not None else ()),
                    *((numbers(1966,910001),) if mid==1980 else ()))
        if mid==801:
            page=reader.integer();reader.finish()
            from native946_role_artifacts import ACTIVE_PAGE, STORAGE_PAGE
            if page in (TEMP_PAGE, ACTIVE_PAGE, STORAGE_PAGE):
                return self.progression.item_packets(pages={page})
            from native946_mall_bags import pages
            with self.progression.transaction(write=False) as db:
                if page not in pages(db,self.progression.cid,True):raise ValueError('当前背包页尚未开放。')
            return self.progression.item_packets(pages={page})
        if mid==796:
            page=reader.integer();reader.finish()
            if self.battle is not None:raise ValueError('战斗中不能切换装备。')
            from native946_inventory import activate
            old=activate(self.progression,page)
            self.last_event={'action':'equipment_page_changed','from_page':old,'to_page':page}
            return self.defer_packets(lambda:(*self.progression.sync(),*Cosmetics(self).visual_packets()))
        if mid==742:
            show=reader.take(1)[0];reader.finish()
            # The native skill_shortcut constructor requests flag 1 on entry;
            # only later requests come from an explicit skill-panel action.
            initialized=getattr(self,'_skill_shortcut_initialized',False)
            self._skill_shortcut_initialized=True
            return self.progression.skill_packets(bool(show) and initialized)
        if mid in (806,808):
            item=reader.integer();reader.integer();reader.finish()
            from native946_npc_services import NpcServices, STORAGE_PAGE
            from native946_pawnshop import PAGE as PAWN_PAGE
            with self.progression.transaction(write=False) as db:
                stored=db.execute('SELECT page FROM inventory WHERE character_id=? AND id=? AND page IN (?,?)',
                                  (self.progression.cid,item,STORAGE_PAGE,PAWN_PAGE)).fetchone()
            if stored and stored[0]==PAWN_PAGE:
                session=NpcServices(self)._session('pawnshop')
                if session['operation']!='redeem' or item not in session['quotes']:
                    raise ValueError('请在赎回窗口中查看当物。')
                return self.progression.item_description(item,include_pawn=True)
            if stored:
                NpcServices(self)._session('storage')
                return self.progression.item_description(item,include_storage=True)
            return self.progression.item_description(item)
        if mid==1985:
            reader.finish()
            return self.progression.task_packets()
        if mid == 1643:
            speed = reader.integer()
            start = unpack("<HH", reader.take(4))
            route = reader.string()
            reader.finish()
            old = (self.character["x"], self.character["y"])
            try:
                if self.battle is not None or not 0 < speed <= 32:
                    raise ValueError("Movement unavailable")
                if self._arrival_guard is not None and max(abs(start[k]-self._arrival_guard[k]) for k in (0,1))>12:
                    raise ValueError('Stale movement from the previous scene')
                path = self.movement_path(old, start, route)
            except ValueError as error:
                self.last_event = {"action": "movement_rejected", "character_id": self.character["id"],
                                   "reason": str(error), "speed": speed, "point": list(start),
                                   "route_hex": route[:64].hex()}
                return packet(1606, ["number", "word", "word"], [self.character["id"], *old])
            end = path[-1]
            writer=getattr(self.characters,'position_writer',None)
            if writer is not None:
                self.character['position_revision']=writer.submit(self.character['owner'],self.character['id'],
                    self.character['map_id'],*end,baseline=self.character.get('position_revision',0))
            else:
                self.characters.move(self.character["owner"], self.character["id"], self.character["map_id"], *end)
            self.character.update(x=end[0], y=end[1])
            self._arrival_guard=None;self._last_transition=None
            self.last_path = set(path)
            self.last_event = {"action": "movement", "character_id": self.character["id"],
                               "from": list(start), "to": list(end), "steps": len(path)-1}
            # The local hero already follows its complete click path. c1608
            # calls run_to() and replaces that path with this short network
            # segment, causing a stop at every segment and discarding the
            # actual click destination. Only observers need a movement echo.
            changes=self.sync_world_npcs()
            return changes or None
        if mid == 1034:
            npc = reader.integer(); reader.finish()
            if self.battle is not None:
                return None
            from native946_fishing import Fishing,is_point
            if is_point(npc):return Fishing(self).look(npc)
            from native946_constellations import Constellations, is_sigil_npc
            if is_sigil_npc(npc):return Constellations(self).look(npc)
            from native946_star_challenge import StarChallenge, is_star
            if is_star(npc):return StarChallenge(self).look(npc)
            from native946_afei_expansion import AfeiWorld, is_encounter_npc
            if is_encounter_npc(npc):return AfeiWorld(self).look(npc)
            self.npc_service_session=None
            if npc!=TRAINER_ID:return self.world_npc_look(npc)
            if not getattr(self, 'practice_trainer', False) or self.npc_position is None:return None
            distance = max(abs(self.character["x"]-self.npc_position[0]), abs(self.character["y"]-self.npc_position[1]))
            if distance > 12:
                return self.menu("请走近一些，再与我交谈。")
            self.last_event = {"action": "npc_dialogue", "npc_id": TRAINER_ID}
            quest=self.progression.snapshot()['quest']['state']
            self.dialog_actions=['train','quest','learn','rest','summons','neidan_menu','growth_battle',dict(kind='story',op='catalog')]
            quest_label={'available':'领取初试身手任务','active':'查看初试身手进度','ready':'交付初试身手任务','completed':'查看已完成的任务'}[quest]
            return self.menu("这里是本机训练场。训练胜利可获得20经验和5金钱，也可以领取基础任务、学习技能和休息。",
                             ('开始基础训练',quest_label,'学习和提升技能','休息恢复气血法力','守护管理','内丹训练（参考规则）','成长大作战','原版剧情与任务目录'))
        if mid == 1032:
            choice = reader.take(1)[0]; callback = reader.integer(); reader.finish()
            if callback == self.dialog_npc:
                from native946_world import world
                if not world().event_npc_is_open(self.character['map_id'], callback):
                    return self.close_expired_npc_dialog()
            # Native byte(-1) is 255. Cancellation is never option zero or
            # the first action, and a stale dialog cannot cancel a newer one.
            if choice==255:
                if callback!=getattr(self,'dialog_npc',None):return None
                return self.close_npc_dialogue()
            if self.dialog_open and callback==getattr(self,'dialog_npc',None) and 1<=choice<=len(self.dialog_actions):
                action=self.dialog_actions[choice-1]
                if isinstance(action,dict) and action.get('kind')=='reserve_food':
                    from native946_reserve_food import use as use_reserve_food
                    if callback != self.character['id']:
                        raise ValueError('储备食品选择会话不匹配。')
                    result = use_reserve_food(self, action['target'], action['item'])
                    self.dialog_open = False
                    self.dialog_actions = []
                    return (packet(1183, ['string'], ['dlg_npc_chat']), result)
                if isinstance(action,dict) and action.get('kind')=='dungeon_route':
                    from native946_dungeon_routes import DungeonRoute
                    self.dialog_open=False
                    return DungeonRoute(self).execute(callback,action)
                if isinstance(action,dict) and action.get('kind')=='campaign':
                    from native946_campaign import Campaign
                    return Campaign(self).execute(callback,action)
                if isinstance(action,dict) and action.get('kind')=='local_mount_guide':
                    from native946_local_guides import advance_mount
                    return advance_mount(self,callback,action['step'])
                if isinstance(action,dict) and action.get('kind')=='hero_resist_reset':
                    if callback!=self.character['id']:raise ValueError('人物修炼更改会话不匹配。')
                    from native946_hero_resistance import HeroResistance
                    return HeroResistance(self).execute(action)
                if isinstance(action,dict) and action.get('kind')=='role_school':
                    if callback!=self.character['id']:raise ValueError('门派选择会话不匹配。')
                    from native946_school import School
                    self.dialog_open=False
                    return School(self).navigate_to_master(action['school'])
                if isinstance(action,dict) and action.get('kind')=='role_baby_equip':
                    if callback!=self.character['id']:raise ValueError('孩子装备会话不匹配。')
                    from native946_role_baby_tools import equip
                    return equip(self,action['item_id'],action['baby_id'])
                if isinstance(action,dict) and action.get('kind')=='role_alliance':
                    if callback!=self.character['id']:raise ValueError('同盟邀请会话不匹配。')
                    from native946_role_city_war import accept_alliance
                    return accept_alliance(self,action['alliance'])
                if isinstance(action,dict) and action.get('kind')=='guardian_acquisition':
                    from native946_guardian_acquisition import GuardianAcquisition
                    return GuardianAcquisition(self).execute(callback,action)
                if isinstance(action,dict) and action.get('kind')=='inventory_equipment':
                    if callback!=self.character['id']:raise ValueError('装备选择会话不匹配。')
                    return InventoryButtons(self).select(action['page'])
                if isinstance(action,dict) and action.get('kind')=='story':
                    self.dialog_open=False
                    return TaskJournal(self).execute(callback,action)
                if isinstance(action,dict) and action.get('kind')=='mall_use':
                    if callback!=self.character['id']:raise ValueError('道具选择会话不匹配。')
                    self.dialog_open=False
                    from native946_mall_bags import Bags
                    return Bags(self).open(action['target'],item_id=action['item_id'])
                if isinstance(action,dict) and action.get('kind')=='lineup_item':
                    if callback!=self.character['id']:raise ValueError('阵法书选择会话不匹配。')
                    from native946_lineups import use_book
                    self.dialog_open=False
                    return use_book(self,action['item_id'],action['index'])
                if isinstance(action,dict) and action.get('kind') in ('afei_encounter','auto_ghost'):
                    from native946_afei_expansion import AfeiWorld,set_ghost
                    self.dialog_open=False
                    if action['kind']=='auto_ghost':return set_ghost(self,callback,action['hours'])
                    return AfeiWorld(self).start(callback,action['key'])
                if isinstance(action,dict) and action.get('kind')=='constellation_challenge':
                    from native946_constellations import Constellations
                    self.dialog_open=False
                    return Constellations(self).start(callback,action['summon'])
                if isinstance(action,dict) and action.get('kind')=='star_challenge':
                    from native946_star_challenge import StarChallenge
                    self.dialog_open=False
                    return StarChallenge(self).start(callback)
                if isinstance(action,dict) and action.get('kind')=='activity':
                    self.dialog_open=False
                    return Activities(self).execute(callback,action)
            if callback!=TRAINER_ID:
                if not self.dialog_open or callback!=self.dialog_npc:return None
                if not 1<=choice<=len(self.dialog_actions):raise ValueError('这个选项不可用。')
                from native946_world import world
                c=self.character;index=self.dialog_actions[choice-1]
                if isinstance(index,dict):
                    from native946_npc_services import NpcServices
                    self.dialog_open=False
                    return NpcServices(self).execute(callback,index)
                route=world().npc_route(c['map_id'],callback,index,(c['x'],c['y']),c)
                return self.change_scene(route,(c['map_id'],callback,index,'npc'))
            actions=self.dialog_actions
            if choice==0:raise ValueError('这个选项不可用。')
            index=choice-1
            if (getattr(self, 'practice_trainer', False) and self.dialog_open and callback == TRAINER_ID
                    and callback==getattr(self,'dialog_npc',None) and index<len(actions)):
                self.dialog_open = False
                action=actions[index]
                if action in ('rare_guardian_sources','guardian_catalog','claim_summon') or isinstance(action,dict) and 'guardian' in action:
                    raise ValueError('教头守护领养入口已关闭，请通过原版道具或活动兑换获取。')
                if action=='growth_battle':
                    from native946_growth_battle import GrowthBattle
                    return packet(1183,['string'],['dlg_npc_chat']),GrowthBattle(self).panel()
                if action=='train':return self.begin_battle()
                if action=='neidan_menu':
                    self.dialog_actions=['neidan_kit','view_summons','drop_test_neidan']
                    return self.menu('本机旧内丹采用所提供参考包的经验及部分效果。守护内丹页可查看等级、下一级经验和效果。',
                                     ('领取内丹与圣气丹测试包','打开守护界面','吐出参战守护的一枚测试内丹'))
                if action=='drop_test_neidan':
                    from native946_neidan_legacy import LegacyNeidan
                    pet=self.summons.active()
                    if not pet or not pet['neidan']:raise ValueError('参战守护尚未安装测试内丹。')
                    LegacyNeidan(self.summons).drop(pet['id'],pet['neidan'][0]['kind'])
                    return self.defer_packets(lambda:(*self.progression.item_packets(),self.summons.neidan_packet(pet['id']),self.menu('内丹已经放回背包，等级降低3级，最低为0级。')))
                if action=='neidan_kit':
                    from native946_neidan_legacy import LegacyNeidan
                    granted=LegacyNeidan(self.summons).grant_test_kit()
                    return self.defer_packets(lambda:(*self.progression.item_packets(),self.menu(
                        '测试包已发放。道具栏切换使用对象为守护，使用内丹后再使用圣气丹；在守护内丹页查看数值及转换经验。' if granted else '该角色已经领取过内丹测试包。')))
                if action=='quest':
                    state=self.progression.snapshot()['quest']['state']
                    if state=='available':self.progression.accept_quest();msg='任务已领取：击败一次训练木偶，然后回来交付。'
                    elif state=='ready':self.progression.turn_in();msg='任务完成！获得120经验和25金钱。'
                    elif state=='active':msg='请先击败一次训练木偶。'
                    else:msg='你已经完成了初试身手。'
                    return (*self.progression.sync(),self.menu(msg))
                if action=='learn':
                    from native946_role_battle import DATA as COMBAT_DATA
                    from native946_school import SCHOOL_SKILLS
                    s=self.progression.snapshot()
                    available=[]
                    from native946_role_battle import owned_profile
                    for k,v in COMBAT_DATA['profiles'].items():
                        if v['source'] == 'starter':continue
                        if int(k) in SCHOOL_SKILLS and int(k) not in (6100,6101):continue
                        try:owned_profile(dict(s,skills={**s['skills'],int(k):1}),int(k))
                        except ValueError:continue
                        available.append((int(k),v))
                    self.dialog_actions=['learn'+str(k) for k,v in available]+['viewskills']
                    return self.menu('本机技能培养：每次消耗5×目标技能等级金钱，最高5级。请按人物等级选择技能。',
                                     tuple(v['name']+'（人物'+str(v['level'])+'级）' for k,v in available)+('查看已学技能',))
                if action=='viewskills':
                    return (packet(1183,['string'],['dlg_npc_chat']),*self.progression.skill_packets(show=True))
                if action.startswith('learn'):
                    self.progression.learn(int(action[5:]));return (*self.progression.sync(),self.menu('技能学习成功。'))
                if action=='rest':
                    s=self.progression.snapshot();self.progression.set_vitals(s['hpmax'],s['mpmax'])
                    self.summons.rest()
                    return (*self.sync_progression(),self.menu('角色和守护的气血、法力已经恢复。'))
                if action=='summons':
                    self.dialog_actions=['view_summons','rest_summons','upgrade_summon']
                    return self.menu('打开守护界面可查看属性、改名、加点及切换参战状态。',
                                     ('打开原生守护界面','恢复守护气血法力','提升参战守护等级'))
                if action=='view_summons':
                    return (packet(1183,['string'],['dlg_npc_chat']),*self.summons.sync(),
                            packet(3993,['string'],['dlg_summon_status']))
                if action=='rest_summons':
                    self.summons.rest()
                    return (*self.summons.sync(),self.menu('守护气血、法力已经恢复。'))
                if action=='upgrade_summon':
                    pet=self.summons.active()
                    if not pet:raise ValueError('请先选择一只参战守护。')
                    self.summons.level_up(pet['id'])
                    return (*self.summons.sync(),self.menu('守护升级成功，获得4点潜能。'))
            return None
        if mid==2175:
            reader.finish()
            from native946_role_battle import weather_reply
            return weather_reply(self.battle or {})
        if mid == 2176:
            target = reader.integer(); reader.finish()
            if target!=ENEMY_ID and self.battle:raise ValueError('请选择当前仍在场的敌方目标。')
            return self.attack() if target==ENEMY_ID else None
        if mid == 2177:
            operation = reader.integer(); target = reader.integer(); reader.finish()
            if operation==6:return self.change_battle_guardian(target)
            if operation==4:return self.queue_protection(target)
            # war_ui.test_operation emits target zero on input timeout. It
            # asks the server to choose an enemy, rather than fighter ID 0.
            if operation==3 and target in (0,ENEMY_ID):return self.native_default_attack(target)
            if self.battle:raise ValueError('请选择当前仍在场的敌方目标。')
            return None
        if mid == 2181:
            operation, target = reader.integer(), reader.integer(); reader.finish()
            if operation==4:return self.queue_protection(target,pet=True)
            if operation == 3 and target in (0,ENEMY_ID):
                return self.native_default_attack(target,pet=True)
            raise ValueError('请选择敌方进行守护普通攻击。')
        if mid == 2182:
            operation = reader.integer(); reader.finish()
            if operation == 1:
                return self.attack(defend=True, pet=True)
            if operation == 2:
                if not self.battle or self.battle['phase']!='input':return None
                self.order_actor(pet=True)
                return self.finish_battle(False, completed=True)
            raise ValueError('当前支持守护普通攻击和防御。')
        if mid == 2185:
            operation,skill,target = [reader.integer() for _ in range(3)]; reader.finish()
            if operation==8:return self.queue_medicine(skill,target)
            # F5's native skill entries also use zero for default targeting.
            if operation==9:return self.attack(skill_id=skill,target=target)
            raise ValueError('请选择已学技能及有效目标。')
        if mid==2183:
            operation,item,target=[reader.integer() for _ in range(3)];reader.finish()
            if operation==8:return self.queue_medicine(item,target,pet=True)
            if operation==9:return self.attack(skill_id=item,pet=True,target=target)
            raise ValueError('当前守护尚未学会可主动施放的战斗技能。')
        if mid == 2178:
            operation = reader.integer(); reader.finish()
            if self.battle is None:
                return None
            if operation==7:return self.change_battle_guardian()
            if operation == 2:return self.finish_battle(False, completed=True)
            return self.attack(defend=True) if operation == 1 else None
        if mid==3819:
            reader.finish()
            battle=self.battle
            if not battle or battle['phase']!='input' or monotonic()>=battle['input_deadline']:
                return None
            from native946_role_battle import solo_required
            required=solo_required(self)
            if not required:return None
            actor=required[0]
            battle['orders'].clear()
            battle['input_ended'].clear()
            self._background_round=None
            remaining=self.begin_input(actor,new_round=False)
            return (numbers(3818,0),numbers(5349),numbers(2151,actor,remaining,0),numbers(2139,2))
        if mid == 2184:
            reader.finish()
            return self.advance_solo_round()
        return None
