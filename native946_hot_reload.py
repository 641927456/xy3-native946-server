"""Authenticated maintenance for the local 946 gameplay service."""

from contextlib import contextmanager
from importlib import invalidate_caches, reload
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from threading import Condition
from time import time_ns
import os
import sys

from local_accounts import AccountError

RELOAD_PATH = "/local/native-reload"
TOKEN_HEADER = "X-Local-Reload-Token"
TOKEN_NAME = ".native-reload-token"


def reload_modules():
    import native946_equipment
    reload(native946_equipment)
    import native946_shops
    reload(native946_shops)
    import native946_fashion_shop
    reload(native946_fashion_shop)
    import native946_gameplay, native946_progression, native946_codec, native946_summons,native946_game_login,native946_map
    reload(native946_codec)
    import native946_classified_mall
    reload(native946_classified_mall)
    import native946_refinement
    reload(native946_refinement)
    import native946_mall_uses,native946_cosmetics,native946_characters,native946_acceptance,native946_tasks,native946_activities,native946_campaign,native946_premium_items
    import native946_role_growth
    reload(native946_role_growth)
    import native946_school
    reload(native946_school)
    reload(native946_premium_items)
    reload(native946_campaign)
    import native946_mall_guardians,native946_mall_bags,native946_mall_checkout,native946_mall_carving,native946_mall_portraits
    for module in (native946_mall_guardians,native946_mall_bags,native946_mall_checkout,native946_mall_carving,native946_mall_portraits,native946_mall_uses,native946_cosmetics,native946_characters,native946_acceptance,native946_tasks,native946_activities):reload(module)
    import native946_friends, native946_mail, native946_trade
    reload(native946_friends)
    reload(native946_mail)
    reload(native946_trade)
    import native946_skill6
    reload(native946_skill6)
    import native946_growth_battle
    reload(native946_growth_battle)
    # Long-lived party services retain their registries across reloads.
    # Refresh imported encoder references without replacing those services.
    for name,module in tuple(sys.modules.items()):
        encoder=getattr(module,'packet',None) if name.startswith('native946') else None
        if getattr(encoder,'__module__',None)=='native946_codec':module.packet=native946_codec.packet
    import native946_inventory,native946_inventory_locks
    import native946_double_time
    reload(native946_double_time)
    import native946_inventory_features,native946_lua_values,native946_equipment_plans,native946_transform,native946_fishing,native946_quick_glyphs
    import native946_hero_page,native946_hero_extra_points,native946_hero_resistance,native946_hero_titles
    import native946_spell_preview
    reload(native946_spell_preview)
    import native946_lineups,native946_role_battle,native946_role_artifacts,native946_role_systems,native946_role_growth,native946_role_arts,native946_role_society,native946_role_city_war,native946_role_baby_tools,native946_role_honors
    for module in (native946_role_battle,native946_role_systems,native946_lineups,native946_role_artifacts,native946_role_growth,native946_role_arts,native946_role_society,native946_role_city_war,native946_role_baby_tools,native946_role_honors):reload(module)
    import native946_combat_control
    reload(native946_combat_control)
    import native946_battle_actions,native946_guardian_rules,native946_presentation_acceptance
    for module in (native946_battle_actions,native946_guardian_rules,native946_presentation_acceptance):reload(module)
    import native946_innate_effects,native946_guardian_passives,native946_guardian_innate,native946_innate_training,native946_book_combat
    for module in (native946_innate_effects,native946_guardian_passives,native946_guardian_innate,native946_innate_training,native946_book_combat):reload(module)
    import native946_guardian_magic
    reload(native946_guardian_magic)
    import native946_guardian_jade,native946_guardian_skill_details,native946_guardian_ui,native946_neidan,native946_skillbooks,native946_guardian_score
    for module in (native946_guardian_jade,native946_guardian_skill_details,native946_guardian_ui,native946_neidan,native946_skillbooks,native946_guardian_score):
        reload(module)
    for module in (native946_hero_page,native946_hero_extra_points,native946_hero_resistance,native946_hero_titles):reload(module)
    import native946_daily_content
    reload(native946_daily_content)
    import native946_star_rewards, native946_achievements, native946_constellations
    reload(native946_star_rewards)
    reload(native946_constellations)
    reload(native946_achievements)
    for module in (native946_inventory_features,native946_lua_values,native946_equipment_plans,native946_transform,native946_fishing,native946_quick_glyphs):reload(module)
    from sys import modules
    reload(native946_inventory_locks);reload(native946_inventory)
    reload(native946_progression)
    reload(native946_summons)
    reload(native946_map)
    import native946_temp_inventory,native946_wilderness
    reload(native946_temp_inventory)
    reload(native946_wilderness)
    reload(native946_campaign)
    import native946_archive_versions, native946_world, native946_timed_npcs, native946_npc_services
    import native946_dungeons, native946_dungeon_routes

    reload(native946_archive_versions)
    reload(native946_world)
    native946_world.world().warm()
    reload(native946_timed_npcs)
    reload(native946_npc_services)
    reload(native946_dungeons)
    reload(native946_dungeon_routes)
    reload(native946_gameplay)
    reload(native946_game_login)
    for name in ('native946_afei_expansion', 'native946_ordinary_treasure', 'native946_star_challenge',
                 'native946_local_dungeons'):
        if name in modules:reload(modules[name])
    import native946_afei_society
    reload(native946_afei_society)
    import native946_guardian_chamber
    reload(native946_guardian_chamber)


def reload_gameplay(database, logins):
    import native946_game_login
    import native946_gameplay
    import native946_progression
    import native946_summons
    from native946_storage import transaction

    # Python reload updates module dictionaries in place. Keep the serving
    # definitions available if any import or migration fails before rebinding.
    modules = [(module, vars(module).copy()) for name, module in tuple(sys.modules.items())
               if name.startswith("native946_") and module is not None]
    bindings = []
    try:
        invalidate_caches()
        reload_modules()
        import native946_afei_society, native946_constellations, native946_friends
        import native946_guardian_chamber, native946_mail

        with transaction(database, write=True) as db:
            for module in (native946_afei_society, native946_constellations, native946_friends,
                           native946_guardian_chamber, native946_mail):
                module.schema(db)
        for login in logins:
            game = login.gameplay
            if game is None:
                continue
            old = game.progression
            current = native946_progression.Progression(
                database, game.character["owner"], game.character["id"])
            current.known_items = old.known_items
            current._known_item_positions = getattr(old, "_known_item_positions", {})
            current._known_item_quantities = getattr(old, "_known_item_quantities", None)
            current._cache_enabled = bool(getattr(game.characters, "runtime_caching", False))
            current.practice_trainer = getattr(game, "practice_trainer", False)
            bindings.append((login, game, current))
    except Exception:
        for module, original in modules:
            namespace = vars(module)
            for key in namespace.keys() - original.keys():
                del namespace[key]
            namespace.update(original)
        raise

    for login in logins:
        login.__class__ = native946_game_login.NativeGameLogin
    for login, game, progression in bindings:
        game.__class__ = native946_gameplay.NativeGameplay
        game.progression = progression
        game.summons.__class__ = native946_summons.Summons
        game.summons.progression = progression
        game.restore_scene = login.scene_packets


class LocalHotReload:
    def __init__(self, database, token_path):
        self.database = database
        self._token = token_urlsafe(32)
        self._condition = Condition()
        self._logins = set()
        self._in_flight = 0
        self._reloading = False
        self._generation = 0
        self._last_reload_ns = None
        token_path = Path(token_path)
        pending_path = token_path.with_suffix(f".{os.getpid()}.tmp")
        pending_path.write_text(self._token, encoding="ascii")
        pending_path.replace(token_path)

    @contextmanager
    def game_call(self):
        with self._condition:
            self._condition.wait_for(lambda: not self._reloading)
            self._in_flight += 1
        try:
            yield
        finally:
            with self._condition:
                self._in_flight -= 1
                if not self._in_flight:
                    self._condition.notify_all()

    def register(self, login):
        with self._condition:
            self._logins.add(login)

    def unregister(self, login):
        with self._condition:
            self._logins.discard(login)

    def status(self):
        with self._condition:
            return {
                "enabled": True,
                "pid": os.getpid(),
                "revision": self._generation,
                "last_reload_ns": self._last_reload_ns,
                "connections": len(self._logins),
            }

    def submit(self, headers, request):
        if len(headers) != 1 or not headers[0].isascii() or not compare_digest(headers[0], self._token):
            raise AccountError(403, "本机热加载凭据无效。")
        if request == {"action": "status"}:
            return self.status()
        if request != {"action": "reload"}:
            raise AccountError(400, "本机热加载仅支持 status 和 reload。")
        return self.reload()

    def reload(self):
        from native946_party import service

        with self._condition:
            if self._reloading:
                raise AccountError(409, "当前已有热加载正在执行。")
            self._reloading = True
        try:
            with self._condition:
                if not self._condition.wait_for(lambda: not self._in_flight, timeout=5):
                    raise AccountError(409, "游戏请求还在处理，请稍后重试。")
                logins = tuple(self._logins)
            if service(self.database).battles or any(
                login.gameplay is not None and login.gameplay.battle is not None for login in logins
            ):
                raise AccountError(409, "当前有战斗进行中，请在结束后重试热加载。")
            reload_gameplay(self.database, logins)
            with self._condition:
                self._generation += 1
                self._last_reload_ns = time_ns()
                return self.status()
        finally:
            with self._condition:
                self._reloading = False
                self._condition.notify_all()


def main():
    import argparse
    from http.client import HTTPConnection
    import json

    parser = argparse.ArgumentParser(description="维护当前本机 946 玩法服务")
    parser.add_argument("action", choices=("status", "reload"))
    options = parser.parse_args()
    from server import HOST, HTTP_PORT

    connection = HTTPConnection(HOST, HTTP_PORT, timeout=30)
    try:
        token = Path(__file__).resolve().with_name(TOKEN_NAME).read_text(encoding="ascii")
        connection.request("POST", RELOAD_PATH, json.dumps({"action": options.action}),
                           {"Content-Type": "application/json", TOKEN_HEADER: token})
        response = connection.getresponse()
        body = response.read()
    except FileNotFoundError:
        parser.exit(1, "当前服务尚未生成本机热加载凭据。\n")
    except OSError as error:
        parser.exit(1, f"无法连接本机热加载服务（{error}）\n")
    finally:
        connection.close()
    try:
        result = json.loads(body)
    except ValueError:
        parser.exit(1, f"当前进程未提供本机热加载（HTTP {response.status}）\n")
    print(json.dumps(result, ensure_ascii=False))
    return int(response.status != 200)


if __name__ == "__main__":
    raise SystemExit(main())
