"""Persistent characters for the isolated local server."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from time import time
import unicodedata

from local_accounts import AccountError
from native946_storage import transaction


ROLES = json.loads(Path(__file__).with_name("native946_roles.json").read_text(encoding="utf-8"))
START_MAP = 2001
START_X = 300
START_Y = 210
NEW_ROLE_MAP = 2008
NEW_ROLE_X = 172
NEW_ROLE_Y = 122


class Characters:
    storage_version = 4

    def __init__(self, database, *, trial_server=False):
        self.database = database
        self.trial_server = trial_server
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL REFERENCES accounts(username),
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                role_id INTEGER NOT NULL,
                race INTEGER NOT NULL,
                sex INTEGER NOT NULL,
                level INTEGER NOT NULL DEFAULT 1,
                map_id INTEGER NOT NULL DEFAULT 2001,
                x INTEGER NOT NULL DEFAULT 300,
                y INTEGER NOT NULL DEFAULT 210,
                created_at INTEGER NOT NULL,
                last_entered_at INTEGER NOT NULL DEFAULT 0
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(characters)")}
            for name, default in (("experience", 0), ("gold", 0), ('position_revision',0)):
                if name not in columns:
                    db.execute(f"ALTER TABLE characters ADD COLUMN {name} INTEGER NOT NULL DEFAULT {default}")
            db.execute("""CREATE TABLE IF NOT EXISTS battle_results (
                battle_id TEXT PRIMARY KEY, owner TEXT NOT NULL, character_id INTEGER NOT NULL,
                victory INTEGER NOT NULL, experience INTEGER NOT NULL, gold INTEGER NOT NULL,
                settled_at INTEGER NOT NULL)""")
            db.execute('CREATE INDEX IF NOT EXISTS native_battles_by_character_time ON battle_results(character_id,settled_at)')
            # Cosmetic schema is additive; all existing character fields stay intact.
            from native946_cosmetics import schema as cosmetic_schema
            # inventory belongs to Progression and may not exist on a new store.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inventory'").fetchone():
                cosmetic_schema(db)
            from native946_party import schema as party_schema
            party_schema(db)
            from native946_campaign import schema as campaign_schema
            campaign_schema(db)
            from native946_nice_ids import schema as nice_id_schema
            nice_id_schema(db)
            from native946_friends import schema as friend_schema
            friend_schema(db)
            from native946_mail import schema as mail_schema
            mail_schema(db)
            from native946_race_fabao import schema as race_fabao_schema
            race_fabao_schema(db)
            from native946_race_fabao_cultivation import schema as race_cultivation_schema
            race_cultivation_schema(db)
            from native946_consignment import schema as card_schema
            card_schema(db)
            from native946_guilds import schema as guild_schema
            guild_schema(db)
            from native946_guild_construction import schema as guild_construction_schema
            guild_construction_schema(db)
            from native946_guild_war import schema as guild_war_schema
            guild_war_schema(db)
            from native946_dungeons import schema as dungeon_schema
            dungeon_schema(db)
            from native946_afei_society import schema as afei_society_schema
            afei_society_schema(db)
            from native946_guardian_chamber import schema as guardian_shop_schema
            guardian_shop_schema(db)
            from native946_constellations import schema as constellation_schema
            constellation_schema(db)
            from native946_song_fireworks import schema as song_firework_schema
            song_firework_schema(db)

    def connect(self):
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def enable_deferred_positions(self):
        if getattr(self,'position_writer',None) is not None:return
        with transaction(self.database,write=True) as db:
            columns={row[1] for row in db.execute('PRAGMA table_info(characters)')}
            if 'position_revision' not in columns:
                db.execute('ALTER TABLE characters ADD COLUMN position_revision INTEGER NOT NULL DEFAULT 0')
        from native946_positions import PositionWriter
        self.position_writer=PositionWriter(self.database)
        self.runtime_caching=True

    def pin_position(self,db,character):
        writer=getattr(self,'position_writer',None)
        if writer is not None:
            rev=writer.revision(character['owner'],character['id'],character.get('position_revision',0))
            writer.apply(db,(character['owner'],character['id']),
                         (character['map_id'],character['x'],character['y'],rev))
            character['position_revision']=rev

    def create(self, owner, name, role_id, *, newbie=False):
        if (not isinstance(name, str) or name.strip() != name
                or not 4 <= len(name.encode("gb18030")) <= 14
                or sum(c.isdigit() for c in name) > 4
                or any(unicodedata.category(c)[0] not in "LN" for c in name)):
            raise AccountError(400, "角色名须为 4 至 14 字节的文字或字母，数字不超过 4 个。")
        role = ROLES.get(str(role_id))
        if role is None:
            raise AccountError(400, "请选择客户端提供的角色外观。")
        from native946_nice_ids import identity
        with transaction(self.database,write=True) as db:
            existing = db.execute("SELECT * FROM characters WHERE name = ?", (name,)).fetchone()
            if existing is not None:
                if existing["owner"] == owner and existing["role_id"] == role_id:
                    return dict(existing, **identity(db, existing['id']))
                raise AccountError(409, "这个角色名已被使用。")
            if db.execute("SELECT COUNT(*) FROM characters WHERE owner = ?", (owner,)).fetchone()[0] >= 6:
                raise AccountError(409, "本机账号最多创建 6 个角色。")
            cursor = db.execute(
                "INSERT INTO characters(owner,name,role_id,race,sex,created_at,map_id,x,y) VALUES(?,?,?,?,?,?,?,?,?)",
                (owner, name, role_id, role["race"], role["sex"], int(time()),
                 NEW_ROLE_MAP if newbie else START_MAP, NEW_ROLE_X if newbie else START_X,
                 NEW_ROLE_Y if newbie else START_Y))
            if newbie:
                from native946_campaign import start_new_character
                start_new_character(db,cursor.lastrowid)
            row = db.execute("SELECT * FROM characters WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return dict(row, **identity(db, row['id']))

    def list(self, owner, *, stage_fields=False):
        from native946_nice_ids import identity
        with transaction(self.database) as db:
            if not stage_fields:
                return [dict(row,**identity(db,row['id'])) for row in db.execute(
                    'SELECT * FROM characters WHERE owner=? ORDER BY id',(owner,))]
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                               "AND name IN ('cultivation_state','native_role_system_state','cosmetic_state')")}
            query='SELECT c.* FROM characters c WHERE c.owner=? ORDER BY c.id'
            if 'cultivation_state' in tables:
                query=('SELECT c.*,coalesce(s.rebirth,0) AS rei,coalesce(s.ascension,0) AS fly,'
                       'coalesce(s.sanctification,0) AS feng FROM characters c LEFT JOIN cultivation_state s '
                       'ON s.character_id=c.id WHERE c.owner=? ORDER BY c.id')
            rows=[dict(row,**identity(db,row['id'])) for row in db.execute(query,(owner,))]
            if 'native_role_system_state' in tables:
                from native946_role_growth import jing
                for row in rows:row['jing']=jing(db,row['id'])['level']
            if {'cosmetic_state','cultivation_state','native_role_system_state'}<=tables:
                from native946_cosmetics import appearance
                for row in rows:row['appearance']=appearance(db,row['id'])
            return rows

    def enter(self, owner, character_id):
        from native946_nice_ids import identity
        writer=getattr(self,'position_writer',None)
        if writer is not None and not writer.flush_key(owner,character_id,.15):
            raise AccountError(503,'位置正在保存，请稍后进入。')
        # Share the live gameplay writer instead of racing its staged batch
        # with a separate SQLite writer during simultaneous player entry.
        with transaction(self.database,write=True,batchable=True) as db:
            row = db.execute("SELECT * FROM characters WHERE owner = ? AND id = ?", (owner, character_id)).fetchone()
            if row is None:
                raise AccountError(404, "找不到这个账号的角色。")
            db.execute("UPDATE characters SET last_entered_at = ? WHERE id = ?", (int(time()), character_id))
            return dict(row,**identity(db,character_id))

    def move(self, owner, character_id, map_id, x, y):
        with transaction(self.database,write=True) as db:
            changed = db.execute("UPDATE characters SET x=?,y=? WHERE owner=? AND id=? AND map_id=?",
                                 (x, y, owner, character_id, map_id)).rowcount
            if changed != 1:
                raise AccountError(404, "找不到当前地图中的角色。")

    def change_scene(self,owner,character_id,old_scene,new_scene,x,y,baseline=0):
        with transaction(self.database,write=True) as db:
            current=db.execute('SELECT position_revision FROM characters WHERE owner=? AND id=? AND map_id=?',
                               (owner,character_id,old_scene)).fetchone()
            if current is None:raise ValueError('场景已经改变，请等待地图加载完成。')
            writer=getattr(self,'position_writer',None)
            revision=writer.revision(owner,character_id,max(baseline,current[0])) if writer is not None else max(baseline,current[0])+1
            db.execute('UPDATE characters SET map_id=?,x=?,y=?,position_revision=? WHERE owner=? AND id=? AND map_id=?',
                       (new_scene,x,y,revision,owner,character_id,old_scene))
            return revision

    def settle_battle(self, owner, character_id, battle_id, victory, *, experience=20, gold=5, advance_quest=True):
        experience, gold = (experience, gold) if victory else (0, 0)
        with transaction(self.database,write=True) as db:
            if not db.execute("SELECT 1 FROM characters WHERE id=? AND owner=?", (character_id, owner)).fetchone():
                raise AccountError(404, "找不到当前账号的角色。")
            result = db.execute("INSERT OR IGNORE INTO battle_results VALUES(?,?,?,?,?,?,?)",
                                (battle_id, owner, character_id, int(victory), experience, gold, int(time())))
            if result.rowcount:
                db.execute("UPDATE characters SET experience=experience+?,gold=gold+? WHERE id=? AND owner=?",
                           (experience, gold, character_id, owner))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='native_equipment' AND type='table'").fetchone():
                    from native946_inventory import active_page
                    db.execute('UPDATE native_equipment SET durability=max(0,durability-1) WHERE character_id=? '
                               'AND item_id IN (SELECT id FROM inventory WHERE character_id=? AND page=?)',
                               (character_id,character_id,active_page(db,character_id)))
                if (advance_quest or str(battle_id).startswith(('wild:','npcquest:'))) and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_role_system_state'").fetchone():
                    from native946_role_systems import settle as settle_role_systems
                    settle_role_systems(db,character_id,battle_id,won=bool(victory))
                if victory and advance_quest and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_quests'").fetchone():
                    db.execute('INSERT OR IGNORE INTO quest_battles VALUES(?,?)',(character_id,battle_id))
                    db.execute("UPDATE local_quests SET kills=1,state='ready' WHERE character_id=? AND state='active'",
                               (character_id,))
            return bool(result.rowcount)


def player_info(character):
    visual=character.get('appearance',{})
    values={"id": character["id"], "showid": character.get("showid",character["id"]), "name": character["name"],
            "icon": visual.get('shape',character["role_id"]), "race": character["race"], "sex": character["sex"],
            "level": character["level"], "rei": character.get('rei',0), "fly": character.get('fly',0),
            "feng": character.get('feng',0), "jing": character.get('jing',0), "online": 0,
            "logout_time": character.get("last_entered_at") or character.get("created_at", 0),
            "title": visual.get('title',''), "org": ""}
    for key in ('weapon_color','charm_shoulder','charm_head','charm_nimbus','cloak_id','color3',
                'weapon_idx','suit_id','color1','color2','hat_id'):
        values[key]=visual.get(key,0)
    return values


def hero_info(character):
    return {"Id": character["id"], "showid": character.get("showid",character["id"]), "Name": character["name"],
            "Icon": character["role_id"], "Race": character["race"], "Sex": character["sex"],
            "Grade": character["level"], "Hp": 100, "HpMax": 100, "HpMin": 100,
            "Mp": 50, "MpMax": 50, "MpMin": 50, "Str": 10, "Mag": 10, "Spe": 10,
            "Cor": 10, "Tili": 100, "TiliMax": 100, "Vim": 100, "VimMax": 100,
            "Neili": 100, "NeiliMax": 100, "ExpNext": 100, "PoteMax": 100,
            "Energy": 100, "AttAll": 10, "DodAll": 10,
            "Exp": character.get("experience", 0), "Cash": character.get("gold", 0)}


DEFAULT_RUN_SPEED = 4


def scene_player(character):
    return {"id": character["id"], "showid": character.get("showid",character["id"]), "name": character["name"],
            "shape": character["role_id"], "x": character["x"], "y": character["y"],
            "layer": 1, "title": "", "follow_info": "", "team_leader": character.get('team_leader', 0),
            **character.get('appearance', {}), "run_speed": character.get("run_speed", DEFAULT_RUN_SPEED)}

def scene_actor_packet(character):
    from native946_codec import packet
    values=scene_player(character)
    mount=character.get('appearance',{}).get('mount_shape')
    if not mount:return packet(1590,['oci_player'],[values])
    values.update(shape=mount,rider_shape=character['role_id'],rcolor1=values.get('color1',0),rcolor2=values.get('color2',0),rcolor3=values.get('color3',0),rhalo=values.get('halo',0))
    return packet(1592,['oci_rider'],[values])
