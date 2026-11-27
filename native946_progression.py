"""Persistent, bounded local progression using verified native 946 records."""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
from time import time

from native946_codec import packet
from native946_storage import transaction as storage_transaction
from native946_growth import hero_stats
import native946_training as training
import native946_neidan_legacy as legacy_neidan
import native946_npc_services as npc_services
import native946_equipment as equipment
import native946_inventory as inventory_controls

DATA = json.loads(Path(__file__).with_name('native946_progression_data.json').read_text(encoding='utf8'))
STACK_LIMITS=json.loads(Path(__file__).with_name('native946_inventory_data.json').read_text('utf8'))['stack_limits']
# In 946 three bag items have different names from their installed skills.
NEIDAN_BAG_NAMES = {91009: '定神思', 91010: '守灵台', 91508: '潜形匿迹'}
for _prefix, _group in zip(('低级内丹·', '高级内丹·'), training.DATA['new_neidan']['NewNeidanIds'], strict=True):
    for _sid, _config in _group.items():
        _kind = _config['ItemType']
        _meta = training.DATA['neidan_meta'][_sid]
        DATA['items'][str(_kind)] = dict(name=_prefix + NEIDAN_BAG_NAMES.get(_kind, _meta['name']),
                                        icon=_meta['icon'], desc=_meta['desc'], max_stack=_meta['stack'])
DATA['items'].update(legacy_neidan.DATA['items'])
from native946_guardian_jade import ITEMS as GUARDIAN_CULTIVATION_ITEMS
for _kind,_meta in GUARDIAN_CULTIVATION_ITEMS.items():
    DATA['items'].setdefault(_kind,dict(name=_meta['name'],icon=_meta['shape'],desc=_meta['desc'],max_stack=_meta['amount_max']))
from native946_cosmetics import DATA as COSMETICS_DATA, schema as cosmetic_schema
DATA['items'].update(COSMETICS_DATA['items'])
from native946_crafting import META as CRAFT_ITEMS
for _kind,_meta in CRAFT_ITEMS.items():
    DATA['items'].setdefault(_kind,{key:_meta[key] for key in ('name','icon','desc')})
    DATA['items'][_kind].setdefault('max_stack',_meta['amount_max'])
for _kind,_meta in equipment.DATA['reward_metadata'].items():DATA['items'].setdefault(_kind,_meta)
from native946_guardian_acquisition import ITEMS as GUARDIAN_ACQUISITION_ITEMS
DATA['items'].update(GUARDIAN_ACQUISITION_ITEMS)
from native946_anniversary_cards import ITEMS as ANNIVERSARY_CARD_ITEMS
DATA['items'].update(ANNIVERSARY_CARD_ITEMS)
from native946_campaign import DATA as CAMPAIGN_DATA
for _kind, _meta in CAMPAIGN_DATA['items'].items():
    _existing = DATA['items'].setdefault(_kind, {})
    for _field, _value in _meta.items():
        _existing.setdefault(_field, _value)
from native946_inventory_features import DATA as INVENTORY_FEATURE_DATA
for _kind,_meta in INVENTORY_FEATURE_DATA['items'].items():DATA['items'].setdefault(_kind,_meta)
from native946_shops import ITEMS as SHOP_ITEMS, DATA as SHOP_DATA
for _kind,_meta in SHOP_ITEMS.items():DATA['items'].setdefault(_kind,_meta)
from native946_reserve_food import FOODS as RESERVE_FOODS, RESOURCES as RESERVE_RESOURCES
for _kind, (_name, _icon, _resource, _level, _points) in RESERVE_FOODS.items():
    DATA['items'].setdefault(str(_kind), dict(name=_name, icon=_icon, max_stack=30,
        desc=f'使用后{RESERVE_RESOURCES[_resource]}储备+{_points}，战斗外自动恢复。使用等级：{_level}。'))
from native946_newbie_rewards import ITEMS as NEWBIE_AWARD_ITEMS
DATA['items'].update(NEWBIE_AWARD_ITEMS)
_growth_equipment = json.loads(Path(__file__).with_name('native946_growth_battle_data.json').read_text('utf8'))
DATA['items'].update(_growth_equipment['first_charge']['equipment'])
QUEST_ID = 910001
QUEST_NAME = '初试身手'
# Base recovery values from the 946 item descriptions.
MEDICINE_EFFECTS = {
    4000: ('hp', 'hpmax', 200), 4001: ('mp', 'mpmax', 100),
    4002: ('hp', 'hpmax', 500), 4003: ('mp', 'mpmax', 200),
    4004: ('hp', 'hpmax', 900), 4005: ('mp', 'mpmax', 400),
}
MEDICINE_EFFECTS.update({int(k):tuple(v[0]) for k,v in SHOP_DATA['medicine_effects'].items() if len(v)==1})

def medicine_effects(kind,*,outside_combat=False):
    if kind in RESERVE_FOODS:
        return ()
    effects=SHOP_DATA['medicine_effects'].get(str(kind),())
    if not effects and outside_combat:effects=SHOP_DATA.get('outside_combat_effects',{}).get(str(kind),())
    return tuple(tuple(v) for v in effects)


def medicine_recovery(subject, effects, count=1):
    return {attr: min(subject[maximum], subject[attr] + recovery * count)
            for attr, maximum, recovery in effects if subject[attr] < subject[maximum]}


def quick_medicine_recovery(db, cid, subject, resource):
    """Use eligible medicine from low to high grade until the clicked bar is full."""
    from native946_inventory_locks import records
    from native946_mall_bags import pages
    from native946_mall_uses import consume

    missing = subject[resource + 'max'] - subject[resource]
    if missing <= 0:
        return {}

    bag_pages = pages(db, cid)
    locks = records(db, cid)
    items = db.execute('SELECT * FROM inventory WHERE character_id=? AND amount>0 AND page IN ('
                       + ','.join('?' for _ in bag_pages) + ')', (cid, *bag_pages))
    candidates = []
    for item in items:
        if item['id'] in locks:
            continue
        effects = medicine_effects(item['type'], outside_combat=True)
        recovery = next((value for attr, _, value in effects if attr == resource and value > 0), 0)
        if not recovery:
            continue
        level = re.search(r'【等级】(\d+)', SHOP_ITEMS[str(item['type'])]['desc'])
        order = (int(level[1]) if level else float('inf'), item['type'], item['page'], item['slot'])
        candidates.append((order, item, effects, recovery))
    if not candidates:
        raise ValueError('背包中没有可用的' + ('补血' if resource == 'hp' else '补法') + '药品。')

    updated = dict(subject)
    for _, item, effects, recovery in sorted(candidates, key=lambda candidate: candidate[0]):
        count = min(item['amount'], (missing + recovery - 1) // recovery)
        consume(db, item, count)
        updated.update(medicine_recovery(updated, effects, count))
        missing = updated[resource + 'max'] - updated[resource]
        if not missing:
            break
    return {attr: updated[attr] for attr in ('hp', 'mp') if updated[attr] != subject[attr]}

# These are local training rules, not a reconstruction of official balance.
GEAR = {5000: (1, {'defense': 2}), 5003: (3, {'hpmax':20, 'defense':3}),
        5001: (1, {'defense': 2}), 5004: (3, {'hpmax': 20, 'defense': 3}),
        5023: (5, {'speed': 2})}
SKILLS = {6100: {'level': 1, 'mana': 8, 'cast_id': DATA['skills']['6100']['cast_id']},
          6101: {'level': 2, 'mana': 12, 'cast_id': DATA['skills']['6101']['cast_id']}}
from native946_role_battle import DATA as ROLE_COMBAT_DATA, LIULI_LAMP, NIUDAO_XIAOSHI
from native946_role_artifacts import ACTIVE_PAGE as ACTIVE_ARTIFACT_PAGE, STORAGE_PAGE as ARTIFACT_STORAGE_PAGE, MODERN_ARTIFACTS
DATA['items'].update({str(kind): row for kind, row in MODERN_ARTIFACTS.items()})
for _sid,_profile in ROLE_COMBAT_DATA['profiles'].items():
    if _profile['source'] != 'starter':
        SKILLS.setdefault(int(_sid),_profile)
    DATA['skills'].setdefault(_sid,_profile)
ATTRS = ('cor', 'mag', 'strength', 'speed')


def numbers(mid, *values):
    return packet(mid, ['number']*len(values), values)


def level_up_cost(level, target, *, cultivation=None):
    # The published 1–100 character table matches (grade + 1)^3 + 50
    # in every row. The 947 unreborn panels agree at their displayed precision.
    # Extend this curve to the 946 unreborn cap. Later stages use the local fallback.
    advanced = any(cultivation.get(key,0) for key in ('rebirth','ascension','sanctification')) if cultivation else False
    if advanced:
        return sum(rank*100 for rank in range(level,target))
    return sum((rank+1)**3+50 for rank in range(level,target))


def lua_data(value):
    """Serialize server-owned data, never execute text supplied by a client."""
    if isinstance(value, str):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return '{' + ','.join('['+lua_data(k)+']='+lua_data(v) for k,v in value.items()) + '}'
    if isinstance(value, list):
        return '{' + ','.join(lua_data(v) for v in value) + '}'
    raise ValueError('Unsupported local task data')


class Progression:
    def __init__(self, database, owner, character_id):
        self.database, self.owner, self.cid = database, owner, character_id
        self.wire_version = 4
        self.chain_version = 1
        self.cosmetic_storage_version = 5
        self.refinement_storage_version = 3
        self.inventory_controls_version = inventory_controls.WIRE_VERSION
        self.known_items = set()
        self._known_item_positions = {}
        self._known_item_quantities = None
        from native946_neidan import reconcile_level_caps

        self.neidan_level_updates = reconcile_level_caps(self)
        # Existing characters need no schema writes on login. Repeating DDL
        # here competes with gameplay commits while other peers are entering.
        with self.transaction(write=False) as db:
            ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN "
                             "('legacy_neidan','legacy_neidan_items','legacy_neidan_test_grants','cultivation_state','skill_shortcuts')").fetchone()[0]==5
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='npc_bank_accounts'").fetchone())
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_pawn_tickets'").fetchone())
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_shop_orders'").fetchone())
            if ready:ready='liuguang' in {r[1] for r in db.execute('PRAGMA table_info(mall_account_wallet)')}
            if ready:ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN "
                                      "('native_guardian_lottery_account_points','native_guardian_account_points')").fetchone()[0]==2
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_workshops'").fetchone())
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cosmetic_use_receipts'").fetchone())
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_fashion_styles'").fetchone())
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cosmetic_guardian_appearance'").fetchone())
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cosmetic_item_carvings'").fetchone())
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_equipment'").fetchone())
            if ready:ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN "
                                     "('native_refinement','native_refinement_vigor','native_fashion_strength',"
                                     "'native_gear_sockets','native_jing_gem_levels','native_guardian_ornament_refinement',"
                                     "'native_guardian_ornament_coins')").fetchone()[0]==7
            if ready:ready='gem_level' in {r[1] for r in db.execute('PRAGMA table_info(native_gear_sockets)')}
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_inventory_state'").fetchone())
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_temp_loot_expiry'").fetchone())
            if ready:ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('native_object_locks','native_inventory_security')").fetchone()[0]==2
            if ready:ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('native_equipment_plans','native_equipment_plan_state','native_equipment_attribute_sets','native_equipment_active_attributes','native_transform_levels','native_transform_cards','native_transform_slots','native_transform_state','native_fish_state','native_fishing_casts','native_temporary_glyphs','native_glyph_item_levels')").fetchone()[0]==12
            if ready:ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('native_hero_page_state','native_point_plans','native_hero_resist_groups','native_hero_resist_levels','native_hero_resist_daily','native_hero_titles','native_hero_title_state','native_hero_extra_attributes','native_hero_food_uses')").fetchone()[0]==9
            if ready:ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('native_role_system_state','native_role_system_events','native_medals','native_match_follows','native_match_results','native_match_likes','native_coc_buildings','native_babies','native_official_posts','native_coc_knights','native_coc_alliances','native_coc_alliance_members','native_coc_alliance_invites')").fetchone()[0]==13
            if ready:ready='result' in {r[1] for r in db.execute('PRAGMA table_info(native_craft_orders)')}
            if ready:ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN "
                                     "('native_npc_quests','native_school_supply_tasks','native_school_quest_details',"
                                     "'native_school_patrol_progress','native_school_milestones')").fetchone()[0]==5
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_task_preferences'").fetchone())
            if ready:ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('native_campaign_runs','native_campaign_review_claims','native_campaign_biopic_unlocks')").fetchone()[0]==3
            if ready:ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_chain_runs'").fetchone())
            if ready:ready=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN "
                                     "('native_growth_claims','native_growth_state')").fetchone()[0]==2
            if ready:ready={'formula_version','disposition'}<={r[1] for r in db.execute('PRAGMA table_info(progression)')}
            if ready:
                ready=db.execute('SELECT 1 FROM progression p JOIN cultivation_state c ON c.character_id=p.character_id '
                                 'WHERE p.character_id=? AND p.formula_version=1 AND NOT EXISTS '
                                 '(SELECT 1 FROM skill_shortcuts WHERE character_id=p.character_id AND slot=0) AND EXISTS '
                                 '(SELECT 1 FROM native_role_system_state WHERE character_id=p.character_id '
                                 "AND system='starter_skills')",(self.cid,)).fetchone()
            repair_skills = ready and self._missing_level_spells(db)
        if ready:
            if repair_skills:
                with self.transaction() as db:
                    self._learn_level_spells(db)
            return
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS progression (
                  character_id INTEGER PRIMARY KEY REFERENCES characters(id),
                  cor INTEGER NOT NULL, mag INTEGER NOT NULL, strength INTEGER NOT NULL,
                  speed INTEGER NOT NULL, points INTEGER NOT NULL, hp INTEGER NOT NULL, mp INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS inventory (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, character_id INTEGER NOT NULL REFERENCES characters(id),
                  type INTEGER NOT NULL, page INTEGER NOT NULL, slot INTEGER NOT NULL, amount INTEGER NOT NULL CHECK(amount>0),
                  UNIQUE(character_id,page,slot));
                CREATE TABLE IF NOT EXISTS learned_skills (
                  character_id INTEGER NOT NULL REFERENCES characters(id), skill_id INTEGER NOT NULL,
                  rank INTEGER NOT NULL, PRIMARY KEY(character_id,skill_id));
                CREATE TABLE IF NOT EXISTS skill_shortcuts (
                  character_id INTEGER NOT NULL REFERENCES characters(id), slot INTEGER NOT NULL,
                  skill_id INTEGER NOT NULL, PRIMARY KEY(character_id,slot));
                CREATE TABLE IF NOT EXISTS local_quests (
                  character_id INTEGER NOT NULL REFERENCES characters(id), quest_id INTEGER NOT NULL,
                  state TEXT NOT NULL, kills INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(character_id,quest_id));
                CREATE TABLE IF NOT EXISTS quest_battles (
                  character_id INTEGER NOT NULL, battle_id TEXT NOT NULL,
                  PRIMARY KEY(character_id,battle_id));
            ''')
            training.schema(db)
            legacy_neidan.schema(db)
            npc_services.schema(db)
            cosmetic_schema(db)
            equipment.schema(db)
            inventory_controls.schema(db)
            from native946_tasks import schema as task_schema
            task_schema(db)
            from native946_npc_quests import schema as npc_quest_schema
            npc_quest_schema(db)
            from native946_growth_battle import schema as growth_battle_schema
            growth_battle_schema(db)
            db.execute('BEGIN IMMEDIATE')
            from native946_guardian_lottery import schema as lottery_schema
            from native946_guardian_channels import schema as guardian_exchange_schema
            lottery_schema(db)
            guardian_exchange_schema(db)
            db.execute('INSERT OR IGNORE INTO cultivation_state(character_id) VALUES(?)',(self.cid,))
            columns={row[1] for row in db.execute('PRAGMA table_info(progression)')}
            for name in ('formula_version','disposition'):
                if name not in columns:db.execute(f'ALTER TABLE progression ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
            created=db.execute('INSERT OR IGNORE INTO progression(character_id,cor,mag,strength,speed,points,hp,mp,formula_version) '
                               'VALUES(?,1,1,1,1,4,1,1,1)',(self.cid,)).rowcount
            if created:
                from native946_role_systems import save as save_role_state
                save_role_state(db,self.cid,'school',dict(school=0))
                female=db.execute('SELECT sex FROM characters WHERE id=?',(self.cid,)).fetchone()[0]==2
                for slot,(kind,amount) in enumerate([(4000,5),(4001,5),(5001 if female else 5000,1),(5004 if female else 5003,1),(5023,1)],1):
                    db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,?,?)',
                               (self.cid,kind,slot,amount))
            # Native controls are skill1..skill10; slot zero has no widget.
            db.execute('UPDATE OR IGNORE skill_shortcuts SET slot=1 WHERE character_id=? AND slot=0',(self.cid,))
            db.execute('DELETE FROM skill_shortcuts WHERE character_id=? AND slot=0',(self.cid,))
            self._initialize_starter_skills(db, created)
            self._learn_level_spells(db)
            row=db.execute('SELECT c.level,p.formula_version FROM characters c JOIN progression p ON p.character_id=c.id '
                           'WHERE c.id=?',(self.cid,)).fetchone()
            if not row['formula_version']:
                old=dict(db.execute('SELECT c.*,p.cor,p.mag,p.strength,p.speed,p.points,p.hp,p.mp FROM characters c '
                                    'JOIN progression p ON p.character_id=c.id WHERE c.id=?',(self.cid,)).fetchone())
                bonus=sum(GEAR[r['type']][1].get('hpmax',0) for r in db.execute(
                    'SELECT type FROM inventory WHERE character_id=? AND page=1',(self.cid,)))
                old_hpmax=100+(old['cor']-10)*8+(old['level']-1)*15+bonus
                old_mpmax=50+(old['mag']-10)*5+(old['level']-1)*5
                natural=max(old['level']-1,0)
                db.execute('UPDATE progression SET cor=cor+?,mag=mag+?,strength=strength+?,speed=speed+?,formula_version=1 '
                           'WHERE character_id=?',(natural,natural,natural,natural,self.cid))
                updated=self._snapshot(db)
                db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',
                           (min(updated['hpmax'],max(0,old['hp'])*updated['hpmax']//max(1,old_hpmax)),
                            min(updated['mpmax'],max(0,old['mp'])*updated['mpmax']//max(1,old_mpmax)),self.cid))
            elif created:
                updated=self._snapshot(db)
                db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',(updated['hpmax'],updated['mpmax'],self.cid))

    def _initialize_starter_skills(self, db, created):
        from native946_role_growth import school, school_choices
        from native946_role_systems import save
        from native946_school import SKILLS, base_school

        if db.execute("SELECT 1 FROM native_role_system_state WHERE character_id=? AND system='starter_skills'",
                      (self.cid,)).fetchone():
            return
        if created:
            db.execute('INSERT OR IGNORE INTO learned_skills VALUES(?,?,0)', (self.cid, NIUDAO_XIAOSHI))
        # Keep spells previously forgotten when switching to 魔西天.
        choices = school_choices(db, self.cid) if base_school(school(db, self.cid)) != 19 else ()
        starters = [SKILLS[choice][0] for choice in choices]
        if starters:
            db.executemany('INSERT OR IGNORE INTO learned_skills VALUES(?,?,0)',
                           ((self.cid, skill) for skill in starters))
            first = 6100 if 6100 in starters else starters[0]
            if created:
                db.execute('INSERT OR IGNORE INTO skill_shortcuts VALUES(?,1,?)', (self.cid, first))
            elif 6100 not in starters:
                # The old initializer wrote this exact rank and slot to every race.
                removed = db.execute(
                    'DELETE FROM learned_skills WHERE character_id=? AND skill_id=6100 AND rank=1 AND EXISTS '
                    '(SELECT 1 FROM skill_shortcuts WHERE character_id=? AND slot=1 AND skill_id=6100)',
                    (self.cid, self.cid)).rowcount
                if removed:
                    db.execute('UPDATE skill_shortcuts SET skill_id=? WHERE character_id=? AND slot=1 AND skill_id=6100',
                               (first, self.cid))
                    db.execute('DELETE FROM skill_shortcuts WHERE character_id=? AND skill_id=6100', (self.cid,))
        save(db, self.cid, 'starter_skills', {})

    def _missing_level_spells(self, db):
        from native946_school import level_spells

        role = db.execute('SELECT level,race,sex FROM characters WHERE id=?', (self.cid,)).fetchone()
        learned = {row[0] for row in db.execute(
            'SELECT skill_id FROM learned_skills WHERE character_id=?', (self.cid,))}
        return [skill for skill in level_spells(db, self.cid, role) if skill not in learned]

    def _learn_level_spells(self, db):
        skills = self._missing_level_spells(db)
        if skills:
            db.executemany('INSERT INTO learned_skills VALUES(?,?,0) ON CONFLICT(character_id,skill_id) DO NOTHING',
                           ((self.cid, skill) for skill in skills))

    @contextmanager
    def transaction(self, write=True):
        if write:self._cached_snapshot=None
        with storage_transaction(self.database,write=write) as db:
            from native946_storage import _CURRENT
            current=_CURRENT.get();authorized=current[3] if current is not None and len(current)>3 else set()
            identity=(self.cid,self.owner)
            if identity not in authorized:
                if not db.execute('SELECT 1 FROM characters WHERE id=? AND owner=?',identity).fetchone():
                    raise ValueError('角色不属于当前账号。')
                authorized.add(identity)
            yield db

    def inventory_rows(self, db):
        from native946_mall_bags import pages as bag_pages
        from native946_role_baby_tools import pages as baby_pages
        guardian_pages=[]
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_guardian_equipment'").fetchone():
            guardian_pages=[r[0] for r in db.execute('SELECT DISTINCT page FROM inventory WHERE character_id=? AND id IN '
                                                     '(SELECT item_id FROM native_guardian_equipment)',(self.cid,))]
        visible_pages=(*inventory_controls.EQUIP_PAGES,*bag_pages(db,self.cid,True),*guardian_pages,*range(25,30),*range(5800,5816),*range(7000,7004),*range(7100,7104),*baby_pages(db,self.cid))
        items=[dict(r) for r in db.execute(
            'SELECT * FROM inventory WHERE character_id=? AND (page IN ('+','.join('?' for _ in visible_pages)+
            ') OR (page IN (?,?) AND type IN ('+','.join('?' for _ in MODERN_ARTIFACTS)+'))) ORDER BY id',
            (self.cid,*visible_pages,ACTIVE_ARTIFACT_PAGE,ARTIFACT_STORAGE_PAGE,*MODERN_ARTIFACTS))]
        from native946_inventory_locks import records as item_locks
        locks=item_locks(db,self.cid)
        for item in items:item.update(locks.get(item['id'],dict(lock=0,lock_block_time=0)))
        return items

    def _snapshot(self, db):
        row=dict(db.execute('SELECT c.*,p.cor,p.mag,p.strength,p.speed,p.points,p.hp,p.mp,p.disposition,p.formula_version FROM characters c '
                            'JOIN progression p ON p.character_id=c.id WHERE c.id=?',(self.cid,)).fetchone())
        row['equipment_page']=inventory_controls.active_page(db,self.cid)
        row['items']=self.inventory_rows(db)
        bank=db.execute('SELECT balance FROM npc_bank_accounts WHERE character_id=?',(self.cid,)).fetchone()
        row['bank_balance']=bank[0] if bank else 0
        row['skills']={r['skill_id']:r['rank'] for r in db.execute('SELECT * FROM learned_skills WHERE character_id=?',(self.cid,))}
        row['shortcuts']={r['slot']:r['skill_id'] for r in db.execute('SELECT * FROM skill_shortcuts WHERE character_id=?',(self.cid,))}
        q=db.execute('SELECT * FROM local_quests WHERE character_id=? AND quest_id=?',(self.cid,QUEST_ID)).fetchone()
        row['quest']=dict(q) if q else {'quest_id':QUEST_ID,'state':'available','kills':0}
        from native946_npc_quests import active_records, day_key
        row['npc_quests']=active_records(db,self.cid)
        school_progress=db.execute("SELECT completed FROM native_npc_activity_progress "
                                   "WHERE character_id=? AND kind='school' AND day=?",
                                   (self.cid,day_key())).fetchone()
        row['school_completed_today']=school_progress[0] if school_progress else 0
        from native946_school_daily import earned_on
        row['school_milestone'] = bool(earned_on(db,self.cid))
        from native946_liveness import balances as activity_balances
        liveness = activity_balances(db, self.cid)
        row['energy'] = liveness['energy']
        row['liveness_guide_done'] = liveness['guide_done']
        from native946_tasks import preferences
        row['task_preferences']=preferences(db,self.cid)
        from native946_campaign import snapshot as campaign_snapshot
        row['campaign']=campaign_snapshot(db,self.cid)
        from native946_race_fabao import guide_active
        row['race_fabao_guide']=guide_active(db,self.cid)
        row['equipment']=equipment.records(db,self.cid)
        from native946_hero_extra_points import values as permanent_points
        for key,value in permanent_points(db,self.cid).items():row[key]+=value
        from native946_gear_sockets import worn_properties
        socket_props=worn_properties(db,self.cid,row['equipment_page'])
        core,equip_direct,equip_percent=equipment.bonuses(row['items'],row['equipment'],row['equipment_page'],sockets=socket_props)
        from native946_refinement import bonuses as refinement_bonuses
        forge_direct,forge_battle,forge_expiry=refinement_bonuses(db,self.cid,row['items'],row['equipment_page'])
        for key in core:
            core[key]+=forge_direct.pop(key,0)
        from native946_role_growth import boost_equipment
        boost_equipment(db,self.cid,core,equip_direct,equip_percent)
        for key,value in core.items():row['base_'+key]=row[key];row[key]+=value
        bonus={'hpmax':0,'defense':0,'speed':0}
        for item in row['items']:
            if item['page']==row['equipment_page']:
                for key,value in GEAR.get(item['type'],(0,{}))[1].items():bonus[key]+=value
        row['cultivation_state']=training.state(db,self.cid)
        from native946_task_chain import active as active_chain
        row['task_chain']=active_chain(db,self.cid)
        for stage in ('rebirth','ascension','sanctification'):row[stage]=row['cultivation_state'][stage]
        row.update(hero_stats(row))
        base_hp=row['hpmax']
        row['hpmax']+=bonus['hpmax']
        row['defense']=bonus['defense']+max(0,(row['cor']-10)//2)
        row['initiative']+=bonus['speed']
        row['cultivation']=training.effects(db,self.cid)
        row['cultivation_levels']={r['skill_id']:r['level'] for r in db.execute('SELECT skill_id,level FROM cultivated_skills WHERE character_id=?',(self.cid,))}
        row['resistance_points']={r['attribute']:r['value'] for r in db.execute('SELECT attribute,value FROM resistance_points WHERE character_id=?',(self.cid,))}
        row['hpmax']+=base_hp*row['cultivation'].get('HpPercent',0)//1000
        row['mpmax']+=row['cultivation'].get('MpValue',0)
        row['defense']+=row['cultivation'].get('DefAll',0)
        row['initiative']+=row['cultivation'].get('DodAll',0)
        for key,value in equip_direct.items():row[key]+=value
        for key,value in equip_percent.items():row['cultivation'][key]=row['cultivation'].get(key,0)+value
        for key,value in forge_direct.items():row[key]+=value
        for key,value in forge_battle.items():row['cultivation'][key]=row['cultivation'].get(key,0)+value
        from native946_quick_glyphs import effects as glyph_effects
        for key,value in glyph_effects(db,self.cid,row['items'],row['equipment_page']).items():row[key]+=value
        from native946_hero_resistance import effects as hero_resistance_effects, records as hero_resist_records
        from native946_hero_titles import effects as title_effects
        from native946_role_systems import effects as role_effects
        from native946_race_fabao import effects as race_fabao_effects
        from native946_role_growth import school as current_school
        row['school']=current_school(db,self.cid)
        from native946_role_growth import jing as current_jing
        row['jing_stage']=current_jing(db,self.cid)['level']
        role_additions=role_effects(db,self.cid)
        for additions in (hero_resistance_effects(db,self.cid),title_effects(db,self.cid),role_additions,
                          race_fabao_effects(db,self.cid,row)):
            for key,value in additions.items():
                if key in ('hpmax','mpmax','attack','initiative','defense'):row[key]+=value
                else:row['cultivation'][key]=row['cultivation'].get(key,0)+value
        row['mpmax']+=row['mpmax']*role_additions.get('RoleMpPercent',0)//1000
        row['initiative']+=row['initiative']*role_additions.get('RoleSpeedPercent',0)//1000
        row['_hero_resistance_records'] = hero_resist_records(db, self.cid, row)
        row['_glyph_deadline']=db.execute('SELECT min(expires) FROM native_temporary_glyphs WHERE character_id=? AND expires>?',(self.cid,int(time()))).fetchone()[0] or 0
        from native946_role_systems import state as role_state
        row['local_guides']=role_state(db,self.cid,'local_quest_guides',dict(mount_step=0,practice_claimed=False))
        row['flight_level']=role_state(db,self.cid,'school_flight',{'level':0})['level']
        role_times=[v['expires'] for v in role_state(db,self.cid,'lingyou',dict(effects={}))['effects'].values() if v['expires']>time()]
        gold_expiry=role_state(db,self.cid,'goldbody',dict(eff_time=0))['eff_time']
        role_times.extend(t for t in (gold_expiry,row['_glyph_deadline'],forge_expiry) if t>time())
        row['_glyph_deadline']=min(role_times,default=0)
        from native946_role_battle import snapshot as battle_effects
        row.update(battle_effects(db,self.cid))
        for name,skill in (('绝情鞭',128),('金箍儿',131)):
            if row['role_artifacts'].get(name):row['skills'].setdefault(skill,1)
        if lamp_level:=row['role_artifacts']['琉璃灯']:row['skills'][LIULI_LAMP]=lamp_level
        from native946_role_battle import current_vim
        row['vim']=current_vim(role_state(db,self.cid,'tianji',{}))
        from native946_role_battle import vim_limit
        row['vim_max']=vim_limit(role_state(db,self.cid,'tianji',{}))
        if row['vim']<row['vim_max']:row['_glyph_deadline']=min(row['_glyph_deadline'] or time()+60,time()+60)
        return row

    def snapshot(self, *, fields=()):
        import native946_storage
        current=native946_storage._CURRENT.get()
        writable=current is not None and current[2]
        cacheable=getattr(self,'_cache_enabled',False) and not writable
        cached=getattr(self,'_cached_snapshot',None)
        if cacheable and cached is not None and (not cached.get('_glyph_deadline') or cached['_glyph_deadline']>time()):
            return deepcopy({key: cached[key] for key in fields} if fields else cached)
        with self.transaction(write=False) as db:result=self._snapshot(db)
        if cacheable:self._cached_snapshot=deepcopy(result)
        return {key: result[key] for key in fields} if fields else result

    def allocate(self, increments):
        if len(increments)!=4 or any(type(n) is not int or n<0 or n>1000 for n in increments) or not sum(increments):
            raise ValueError('属性点分配无效。')
        with self.transaction() as db:
            before=self._snapshot(db)
            if sum(increments)>before['points']:raise ValueError('可分配属性点不足。')
            db.execute('UPDATE progression SET cor=cor+?,mag=mag+?,strength=strength+?,speed=speed+?,points=points-? WHERE character_id=?',
                       (*increments,sum(increments),self.cid))
            after=self._snapshot(db)
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',
                       (min(after['hpmax'],before['hp']+after['hpmax']-before['hpmax']),
                        min(after['mpmax'],before['mp']+after['mpmax']-before['mpmax']),self.cid))

    def level_up(self, target=None):
        with self.transaction() as db:
            s=self._snapshot(db)
            target = s['level'] + 1 if target is None else target
            increase = target - s['level']
            from native946_cultivation import FLIGHT_LIMIT, level_cap
            cap=level_cap(s)
            if not s['level'] < target <= cap:
                if target > cap and s['ascension'] and not s['sanctification'] and cap == FLIGHT_LIMIT:
                    raise ValueError('飞升160级后，请到西梁国主处完成凝气化神再升级。')
                raise ValueError('已达到当前可升等级上限。')
            cost = level_up_cost(s['level'], target, cultivation=s)
            if s['experience'] < cost:
                raise ValueError('升级经验不足。')
            db.execute('UPDATE characters SET level=?,experience=experience-? WHERE id=?',(target,cost,self.cid))
            db.execute('UPDATE progression SET cor=cor+?,mag=mag+?,strength=strength+?,speed=speed+?,points=points+? '
                       'WHERE character_id=?',(increase,increase,increase,increase,increase*4,self.cid))
            self._learn_level_spells(db)
            after=self._snapshot(db)
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',
                       (min(after['hpmax'],s['hp']+after['hpmax']-s['hpmax']),
                        min(after['mpmax'],s['mp']+after['mpmax']-s['mpmax']),self.cid))

    def auto_level(self):
        increased = 0
        with self.transaction() as db:
            while True:
                s = self._snapshot(db)
                from native946_cultivation import level_cap
                if s['level'] >= level_cap(s) or s['experience'] < level_up_cost(s['level'],s['level']+1,cultivation=s):
                    break
                self.level_up()
                increased += 1
        self._cached_snapshot = None
        return increased

    def learn(self, skill, count=1):
        if skill == LIULI_LAMP:raise ValueError('琉璃灯由装备提供，不需要学习。')
        if skill not in SKILLS or count!=1:raise ValueError('当前训练不支持该技能。')
        with self.transaction() as db:
            s=self._snapshot(db);rank=s['skills'].get(skill,0)
            from native946_role_battle import profile,owned_profile
            owned_profile(dict(s,skills={**s['skills'],skill:1}),skill)
            if s['level']<SKILLS[skill]['level']:raise ValueError('角色等级不足。')
            if rank>=5:raise ValueError('已达到本机训练技能上限。')
            cost=5*(rank+1)
            if s['gold']<cost:raise ValueError('学习所需金钱不足。')
            db.execute('UPDATE characters SET gold=gold-? WHERE id=?',(cost,self.cid))
            db.execute('INSERT INTO learned_skills VALUES(?,?,?) ON CONFLICT(character_id,skill_id) DO UPDATE SET rank=excluded.rank',
                       (self.cid,skill,rank+1))

    def shortcut(self, slot, skill=None):
        from native946_role_growth import school
        from native946_school import FLIGHT_STUNTS, flight_stunt_id

        if not 1<=slot<=10:raise ValueError('快捷栏位置无效。')
        with self.transaction() as db:
            if skill is None:db.execute('DELETE FROM skill_shortcuts WHERE character_id=? AND slot=?',(self.cid,slot))
            else:
                if skill in FLIGHT_STUNTS.values():
                    learned = skill == flight_stunt_id(school(db, self.cid))
                else:
                    learned = db.execute('SELECT 1 FROM learned_skills WHERE character_id=? AND skill_id=?',
                                         (self.cid,skill)).fetchone() is not None
                if not learned:
                    raise ValueError('尚未学习此技能。')
                db.execute('INSERT INTO skill_shortcuts VALUES(?,?,?) ON CONFLICT(character_id,slot) DO UPDATE SET skill_id=excluded.skill_id',
                           (self.cid,slot,skill))

    def free_bag_slot(self,snapshot=None):
        from native946_mall_bags import pages
        with self.transaction(write=False) as db:
            normal=pages(db,self.cid)
            items=snapshot['items'] if snapshot is not None else db.execute(
                'SELECT page,slot FROM inventory WHERE character_id=?',(self.cid,))
            occupied={(i['page'],i['slot']) for i in items}
        for page in normal:
            for slot in range(1,25):
                if (page,slot) not in occupied:return page,slot
        raise ValueError('已开启的物品栏都已放满。')

    def validate_equipment(self,snapshot,item,slot,page):
        from native946_fashion_refinement import ITEMS as FASHION_ITEMS, validate_wear
        if str(item['type']) in FASHION_ITEMS:
            validate_wear(snapshot,item,slot)
            return
        if item['type'] in ROLE_COMBAT_DATA['secret_items']:
            if slot!=15 or item['amount']!=1:raise ValueError('秘宝只能单件放在秘宝位置。')
        elif item['id'] in snapshot['equipment']:equipment.validate_wear(snapshot,dict(item),slot,page=page)
        elif item['type'] not in GEAR or GEAR[item['type']][0]!=slot:raise ValueError('装备部位不匹配。')
        if ((item['type'] in (5001,5004) and snapshot['sex']!=2) or (item['type'] in (5000,5003) and snapshot['sex']!=1)):
            raise ValueError('装备角色要求不匹配。')

    def move_item(self, item_id, page, slot, expected_page=None, *, stack=True):
        with self.transaction() as db:
            from native946_mall_bags import pages as bag_pages
            bag_destinations=bag_pages(db,self.cid);bag_sources=bag_pages(db,self.cid,True)
            item=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',(item_id,self.cid)).fetchone()
            if not item or (expected_page is not None and item['page']!=expected_page):raise ValueError('物品位置已变化。')
            if {page, item['page']} & {ACTIVE_ARTIFACT_PAGE, ARTIFACT_STORAGE_PAGE}:
                from native946_role_artifacts import move_artifact
                return move_artifact(db,self.cid,item,page,slot,bag_destinations,bag_sources)
            from native946_fishing import PAGES as FISH_PAGES,validate_position
            from native946_role_baby_tools import pages as baby_pages,validate_position as validate_baby_position
            children=baby_pages(db,self.cid)
            normal=(*bag_destinations,*FISH_PAGES,*children);withdraw=(*bag_sources,*FISH_PAGES,*children)
            if not ((page in normal and 1<=slot<=24) or (page in inventory_controls.EQUIP_PAGES and slot in inventory_controls.EQUIP_SLOTS)):raise ValueError('目标位置无效或物品栏未开启。')
            bag_only=item['page'] in bag_sources and page in bag_destinations
            before=None if bag_only else self._snapshot(db)
            if item['page'] not in (*inventory_controls.EQUIP_PAGES,*withdraw):raise ValueError('物品位置已变化。')
            validate_position(db,self.cid,item['type'],page,slot)
            validate_baby_position(db,self.cid,item['type'],page,slot)
            if page in children and item['amount']!=1:raise ValueError('请先把孩子装备拆分为单件。')
            if page in inventory_controls.EQUIP_PAGES:self.validate_equipment(before,dict(item),slot,page)
            target=db.execute('SELECT * FROM inventory WHERE character_id=? AND page=? AND slot=?',(self.cid,page,slot)).fetchone()
            changed={item_id}
            if target and target['id']==item_id:return changed
            if target:
                changed.add(target['id'])
                validate_position(db,self.cid,target['type'],item['page'],item['slot'])
                validate_baby_position(db,self.cid,target['type'],item['page'],item['slot'])
                if stack and item['page'] in withdraw and page in normal and target['type']==item['type']:
                    from native946_inventory_locks import records as item_locks
                    flags=item_locks(db,self.cid)
                    stack_state=before
                    if stack_state is None:
                        equipped=db.execute('SELECT item_id FROM native_equipment WHERE character_id=? AND item_id=?',
                                            (self.cid,item_id)).fetchone()
                        stack_state={'equipment':{item_id} if equipped else ()}
                    maximum=self.stack_limit(item,stack_state)
                    if flags.get(item_id)!=flags.get(target['id']):maximum=1
                    if str(item['type']) in INVENTORY_FEATURE_DATA['glyph_slots']:
                        levels={r['item_id']:r['level'] for r in db.execute('SELECT item_id,level FROM native_glyph_item_levels WHERE character_id=? AND item_id IN (?,?)',(self.cid,item_id,target['id']))}
                        default=INVENTORY_FEATURE_DATA['local_rules']['glyph_default_level']
                        if levels.get(item_id,default)!=levels.get(target['id'],default):maximum=1
                    if maximum>1:
                        amount=min(item['amount'],max(0,maximum-target['amount']))
                        if amount:
                            db.execute('UPDATE inventory SET amount=amount+? WHERE id=?',(amount,target['id']))
                            if amount==item['amount']:db.execute('DELETE FROM inventory WHERE id=?',(item_id,))
                            else:db.execute('UPDATE inventory SET amount=amount-? WHERE id=?',(amount,item_id))
                        return changed
                if item['page'] in withdraw and item['page'] not in normal:raise ValueError('到期百纳袋只能取出，不能交换存入。')
                if item['page'] in inventory_controls.EQUIP_PAGES:self.validate_equipment(before,dict(target),item['slot'],item['page'])
                db.execute('UPDATE inventory SET page=-1,slot=-1 WHERE id=?',(item_id,))
                db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(item['page'],item['slot'],target['id']))
            db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(page,slot,item_id))
            if before is not None:
                after=self._snapshot(db)
                db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',(min(before['hp'],after['hpmax']),min(before['mp'],after['mpmax']),self.cid))
            return changed

    @staticmethod
    def stack_limit(item,snapshot):
        if item['type'] in GEAR or item['type']==6052 or item['id'] in snapshot['equipment']:return 1
        return min(500,STACK_LIMITS.get(str(item['type']),DATA['items'][str(item['type'])].get('max_stack',1)))

    def split(self,item_id,page,slot,amount):
        with self.transaction() as db:
            from native946_mall_bags import pages as bag_pages
            normal=bag_pages(db,self.cid)
            from native946_fishing import PAGES as FISH_PAGES,validate_position
            normal=(*normal,*FISH_PAGES)
            if page not in normal or not 1<=slot<=24 or amount<=0:raise ValueError('拆分位置或数量无效。')
            item=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',(item_id,self.cid)).fetchone()
            if not item or item['page'] not in normal or self.stack_limit(dict(item),self._snapshot(db))<=1 or amount>=item['amount']:raise ValueError('不能拆分该物品。')
            validate_position(db,self.cid,item['type'],page,slot)
            if db.execute('SELECT 1 FROM inventory WHERE character_id=? AND page=? AND slot=?',(self.cid,page,slot)).fetchone():
                raise ValueError('目标格子不为空。')
            db.execute('UPDATE inventory SET amount=amount-? WHERE id=?',(amount,item_id))
            split_id=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',(self.cid,item['type'],page,slot,amount)).lastrowid
            db.execute("INSERT INTO native_object_locks SELECT character_id,kind,?,flag,release_at FROM native_object_locks WHERE kind='item' AND object_id=?",(split_id,item_id))
            db.execute('INSERT INTO native_glyph_item_levels SELECT ?,character_id,level FROM native_glyph_item_levels WHERE item_id=? AND character_id=?',(split_id,item_id,self.cid))

    def use_item(self,item_id):
        with self.transaction() as db:
            from native946_mall_bags import pages as bag_pages
            from native946_neidan import ENERGY_ITEM_TYPE,ITEM_TYPES as NEIDAN_ITEMS
            s=self._snapshot(db)
            item=next((i for i in s['items'] if i['id']==item_id and i['page'] in bag_pages(db,self.cid)),None)
            if item is not None and (item['type'] in NEIDAN_ITEMS or item['type'] in (6052,6024,ENERGY_ITEM_TYPE)):
                raise ValueError('请将道具使用对象切换为守护。')
            effects=medicine_effects(item['type'],outside_combat=True) if item else ()
            if not effects:raise ValueError('当前不能使用该物品。')
            changes=medicine_recovery(s,effects)
            if not changes:raise ValueError('当前状态已满，无需消耗物品。')
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',(changes.get('hp',s['hp']),changes.get('mp',s['mp']),self.cid))
            if item['amount']==1:db.execute('DELETE FROM inventory WHERE id=?',(item_id,))
            else:db.execute('UPDATE inventory SET amount=amount-1 WHERE id=?',(item_id,))

    def quick_recover(self, resource):
        with self.transaction() as db:
            subject = self._snapshot(db)
            changes = quick_medicine_recovery(db, self.cid, subject, resource)
            if changes:
                db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',
                           (changes.get('hp', subject['hp']), changes.get('mp', subject['mp']), self.cid))
            return changes

    def merge(self,item_ids):
        if not item_ids or len(set(item_ids))!=len(item_ids):raise ValueError('整理物品列表无效。')
        with self.transaction() as db:
            items=[]
            for item_id in item_ids:
                item=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',(item_id,self.cid)).fetchone()
                from native946_mall_bags import pages
                if item and item['page'] not in (*pages(db,self.cid),*range(7000,7004)):item=None
                if not item:raise ValueError('物品不在当前角色背包中。')
                items.append(item)
            snapshot=self._snapshot(db)
            flags={i['id']:(i.get('lock',0),i.get('lock_block_time',0)) for i in snapshot['items']}
            glyph_levels={r['item_id']:r['level'] for r in db.execute('SELECT item_id,level FROM native_glyph_item_levels WHERE character_id=?',(self.cid,))}
            tier=lambda i:glyph_levels.get(i['id'],INVENTORY_FEATURE_DATA['local_rules']['glyph_default_level']) if str(i['type']) in INVENTORY_FEATURE_DATA['glyph_slots'] else 0
            for kind,lock,level in {(i['type'],flags[i['id']],tier(i)) for i in items}:
                stack=[i for i in items if i['type']==kind and flags[i['id']]==lock and tier(i)==level]
                maximum=min(self.stack_limit(dict(i),snapshot) for i in stack)
                if maximum<=1:continue
                total=sum(i['amount'] for i in stack)
                for item in stack:
                    amount=min(maximum,total);total-=amount
                    if amount:db.execute('UPDATE inventory SET amount=? WHERE id=?',(amount,item['id']))
                    else:db.execute('DELETE FROM inventory WHERE id=?',(item['id'],))

    def set_vitals(self,hp,mp):
        with self.transaction() as db:
            s=self._snapshot(db)
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',
                       (max(0,min(s['hpmax'],hp)),max(0,min(s['mpmax'],mp)),self.cid))

    def accept_quest(self):
        with self.transaction() as db:
            q=self._snapshot(db)['quest']
            if q['state']!='available':raise ValueError('该任务已经领取。')
            db.execute('INSERT INTO local_quests VALUES(?,?,?,0)',(self.cid,QUEST_ID,'active'))

    def record_victory(self,battle_id):
        with self.transaction() as db:
            inserted=db.execute('INSERT OR IGNORE INTO quest_battles VALUES(?,?)',(self.cid,battle_id)).rowcount
            if inserted:db.execute("UPDATE local_quests SET kills=1,state='ready' WHERE character_id=? AND quest_id=? AND state='active'",
                                   (self.cid,QUEST_ID))

    def turn_in(self):
        with self.transaction() as db:
            changed=db.execute("UPDATE local_quests SET state='completed' WHERE character_id=? AND quest_id=? AND state='ready'",(self.cid,QUEST_ID)).rowcount
            if not changed:raise ValueError('任务尚未完成，或奖励已经领取。')
            db.execute('UPDATE characters SET experience=experience+120,gold=gold+25 WHERE id=?',(self.cid,))
            self.auto_level()

    def cancel_quest(self):
        with self.transaction() as db:
            db.execute("DELETE FROM local_quests WHERE character_id=? AND quest_id=? AND state IN ('active','ready')",(self.cid,QUEST_ID))

    def attribute_values(self,s=None):
        from native946_equipment_score import equipped_total
        from native946_guardian_score import highest_score
        from native946_mall_portraits import current as current_portrait
        from native946_fashion import score as fashion_score
        from native946_fashion_shop import current_frame
        from native946_fishing import capacity
        from native946_cosmetics import appearance
        from native946_hero_page import active_plan
        from native946_role_growth import talents
        from native946_shops import liuguang_balance
        with self.transaction(write=False) as db:
            if s is None:s=self.snapshot()
            qicon=current_portrait(db,self.cid);fashion=fashion_score(db,self.cid);fish_bags=capacity(db,self.cid);visual=appearance(db,self.cid);shape=visual['shape'];frame=current_frame(db,self.cid);point_plan=active_plan(db,self.cid)
            zhenqi = talents(db, self.cid)['zhenqi']
            time_credit=liuguang_balance(db,self.cid)
            equip_score=equipped_total(db,self.cid,s,GEAR)
            summon_score=highest_score(db,self.cid)
        # The classic 3.0.945 notice adds four evaluation points per battle-secret level.
        # The native Battle Secret tab sends 3420/3421 and stores these levels in cultivated_skills.
        evaluate = 4 * sum(s['cultivation_levels'].values())
        cost = level_up_cost(s['level'],s['level']+1,cultivation=s)
        return {'Grade':s['level'],'Exp':s['experience'],'ExpNext':cost,'Cash':s['gold'],'Account':s['bank_balance'],'Point':s['points'],'Vim':s.get('vim',300),'VimMax':s.get('vim_max',300),'Energy':s['energy'],
                'Cor':s['cor'],'Mag':s['mag'],'Str':s['strength'],'Spe':s['speed'],'Hp':s['hp'],'HpMin':s['hpmax'],
                'HpMax':s['hpmax'],'Mp':s['mp'],'MpMin':s['mpmax'],'MpMax':s['mpmax'],'AttAll':s['attack'],
                'DodAll':s['initiative'],'School':s.get('school',0),'FlyLevel':s.get('flight_level',0),'Jing':s.get('jing_stage',0),'Evaluate':evaluate,'EquipScore':equip_score,
                'SummonScore':summon_score,
                'AccumulationFund':s['cultivation_state']['tongbao'], 'ZhenQi':zhenqi, 'TimeCredit':time_credit,
                'Rei':s['cultivation_state']['rebirth'],'Fly':s['cultivation_state']['ascension'],
                'Feng':s['cultivation_state']['sanctification'],'QIcon':qicon,'FashionScore':fashion,'Shape':shape,'IconFrame':frame,'ActPointScheme':point_plan,'TitleStyle':visual.get('title_style',0),
                **{'ExFishFrame'+str(i):fish_bags.get(6999+i,0) for i in range(1,5)}}

    def attribute_packets(self, s=None):
        values=self.attribute_values(s)
        from native946_hero_titles import current as title_current
        with self.transaction(write=False) as db:title=title_current(db,self.cid)['title']
        return (packet(696,['number','string'],[int(DATA['attributes']['Title']),title]),*tuple(numbers(695,int(DATA['attributes'][k]),v) for k,v in values.items()),numbers(694))

    def resistance_packet(self, mode):
        if mode not in (1,2):raise ValueError('抗性面板类型无效。')
        s=self.snapshot()
        values={name:0 for name in ('FirePoint','WaterPoint','WindPoint','ThunderPoint','DefPoint',
                'FaintPoint','PoisonPoint','SealPoint','DisorderPoint','DodPoint','AttPoint','SpePoint','GushuPoint',
                'ResistPoint','ReFire','ReWater','ReWind','ReThunder','ReFaint','RePoison','ReSeal','ReDisorder','ReGushu',
                'DodValue','AttValue','FatalRate','CruelRate','DoubleRate','DoubleCount','FanjiRate','FanjiCount')}
        values.update(Chc=s['disposition'],DefAll=s['defense'],DodAll=s['initiative'])
        values.update({k:v for k,v in s['cultivation'].items() if k not in ('DefAll','DodAll')})
        values.update(s['resistance_points'])
        values['ResistPoint']=s['cultivation_state']['earned_points']-sum(s['resistance_points'].values())
        return packet(698,['number','string'],[mode,lua_data(values)])

    def item_records(self, db, items):
        from native946_jing_gems import TRADEABLE as JING_TRADEABLE
        from native946_trade import ITEM_TYPES as PLAIN_TRADEABLE
        from native946_temp_inventory import PAGE as TEMP_PAGE

        metadata = legacy_neidan.item_metadata_batch(db, self.cid) if any(i['type']==6052 for i in items) else {}
        records = []
        for i in items:
            meta = metadata.get(i['id']) or DATA['items'][str(i['type'])]
            records.append(dict(id=i['id'], type=i['type'], page=i['page'], grid=i['slot'], amount=i['amount'],
                name=meta['name'], icon=meta['icon'],
                can_trade=int(i['page'] != TEMP_PAGE and (i['type'] in JING_TRADEABLE or i['type'] in PLAIN_TRADEABLE)),
                lock=i.get('lock',0), lock_block_time=i.get('lock_block_time',0)))
        return records

    def item_packets(self, s=None, *, item_ids=None, pages=None):
        if item_ids is not None and not item_ids:return ()
        from native946_mall_bags import pages as bag_pages
        from native946_temp_inventory import rows as temporary_items
        previous=getattr(self,'_known_item_positions',{})
        with self.transaction(write=False) as db:
            inventory=s['items'] if s is not None else self.inventory_rows(db)
            items = [*inventory, *map(dict, temporary_items(db, self.cid)), *npc_services.warehouse_rows(db, self.cid)]
            if pages is not None:
                # Include IDs formerly on these pages so the client can clear
                # a stale grid entry when an item is removed or relocated.
                item_ids={i['id'] for i in items if i['page'] in pages}
                item_ids.update(iid for iid,(page,_) in previous.items() if page in pages)
                if not item_ids:return ()
            if item_ids is not None:items=[i for i in items if i['id'] in item_ids]
            records = self.item_records(db, items)
            quantities = {row['id']:(row['type'],row['amount']) for row in db.execute(
                'SELECT id,type,amount FROM inventory WHERE character_id=?',(self.cid,))}
            effect_pages = bag_pages(db, self.cid)
        ids={i['id'] for i in items}
        positions={i['id']:(i['page'],i['slot']) for i in items}
        known=self.known_items if item_ids is None else self.known_items & item_ids
        relocated={i for i in ids & known if previous.get(i)!=positions[i]}
        # c_item_update deletes the old grid before adding the new one. During
        # a swap, updating A then B would clear A's newly occupied grid when
        # deleting B. Clear every old position before adding any moved item.
        messages=[numbers(780,i) for i in sorted((known-ids)|relocated)]
        messages.extend(packet(789 if item['id'] in known and item['id'] not in relocated else 786,
                               ['item_info'],[item]) for item in records)
        before = getattr(self, '_known_item_quantities', None)
        if before is not None:
            if item_ids is not None:
                quantities = {**{iid:entry for iid,entry in before.items() if iid not in item_ids},
                              **{iid:quantities[iid] for iid in item_ids if iid in quantities}}
            # Count all holdings, including storage, so moving or splitting a
            # stack never re-arms the new-item sprite after a player has seen it.
            gains = Counter()
            for kind, amount in quantities.values():
                gains[kind] += amount
            for kind, amount in before.values():
                gains[kind] -= amount
            for item in records:
                iid, kind = item['id'], item['type']
                previous_kind, previous_amount = before.get(iid, (kind, 0))
                if (item['page'] in effect_pages and gains[kind] > 0
                        and (previous_kind != kind or item['amount'] > previous_amount)):
                    messages.append(numbers(790, iid, 1))
        self._known_item_quantities = quantities
        if item_ids is None:
            self.known_items=ids
            self._known_item_positions=positions
        else:
            self.known_items=(self.known_items-item_ids)|ids
            self._known_item_positions={**{iid:place for iid,place in previous.items() if iid not in item_ids},**positions}
        return tuple(messages)

    def item_description(self,item_id,*,include_storage=False,include_pawn=False,carve_preview=None):
        item=next((i for i in self.snapshot()['items'] if i['id']==item_id),None)
        if item is None:
            from native946_temp_inventory import PAGE
            from native946_pawnshop import PAGE as PAWN_PAGE
            permitted = (PAGE, npc_services.STORAGE_PAGE if include_storage else PAGE,
                         PAWN_PAGE if include_pawn else PAGE)
            with self.transaction(write=False) as db:
                stored=db.execute('SELECT * FROM inventory WHERE character_id=? AND id=? AND page IN (?,?,?)',(self.cid,item_id,*permitted)).fetchone()
                if stored is not None:item=dict(stored)
        if not item:raise ValueError('物品不存在。')
        meta=DATA['items'][str(item['type'])]
        if item['type']==6052:
            with self.transaction(write=False) as db:meta=legacy_neidan.item_metadata(db,item_id) or meta
        desc=meta['desc']
        from native946_role_artifacts import BOX_RULES, box_description
        if item['type'] in BOX_RULES:
            desc+=box_description(item['type'])
        from native946_neidan import ITEM_TYPES as NEW_NEIDAN_ITEMS, MAX_LEVEL_PREVIEWS
        preview=MAX_LEVEL_PREVIEWS.get(NEW_NEIDAN_ITEMS.get(item['type']))
        if preview:
            desc+='#r#Y【飞升180级五阶基准，未共鸣】#n#r'+preview
        from native946_ordinary_treasure import MAP_TYPES, location as treasure_location
        if item['type'] in MAP_TYPES:
            desc+='#r'+treasure_location(item_id)
        if item['type'] in (70482,70483,80482,80483):
            from native946_mall_event_boxes import remaining_uses
            with self.transaction(write=False) as db:
                desc+=f'#r当前剩余开启次数：{remaining_uses(db,item)}。本机奖池。'
        starter=GEAR.get(item['type'])
        if starter:
            labels={'hpmax':'气血上限','defense':'防御','speed':'速度'}
            desc+='#r#Y装备属性：'+ '，'.join(f'{labels[k]}+{v}' for k,v in starter[1].items())
        from native946_equipment_score import item_score
        from native946_guardian_score import item_score as guardian_item_score
        from native946_refinement import state as refinement_state, describe as refinement_desc
        from native946_fashion_refinement import level as fashion_level, FashionRefinement
        with self.transaction(write=False) as db:
            from native946_gear_sockets import properties as socket_properties
            from native946_jing_gems import TYPES as JING_GEMS, detail as gem_detail
            from native946_guardian_ornaments import tooltip as ornament_tooltip
            instance=equipment.records(db,self.cid).get(item_id)
            refinement=refinement_state(db,self.cid,item_id)
            strength=fashion_level(db,self.cid,item_id)
            gems=socket_properties(db,item_id)
            ornament=ornament_tooltip(db,item_id) if equipment.COMPLETION['guardians'].get(str(item['type']),{}).get('summon') else ''
            guardian_score=guardian_item_score(db,item_id,item['type'])
            if item['type'] in JING_GEMS:desc=gem_detail(db,item)+'#r'+desc
        if instance:
            desc+='#r'+equipment.description(instance)
            desc+=f"#r【耐久】{instance['durability']}/{instance['max_durability']}"
        elif sample := equipment.CBG_GEAR['fairy'].get(str(item['type'])):
            desc+='#r【同编号五阶仙器基础属性参考】#r'+'#r'.join(
                equipment.property_lines(sample['properties'], sample['percent']))
        active_traits=refinement['traits'] if not refinement['expires'] or refinement['expires']>int(time()) else ()
        score=guardian_score or item_score(instance,starter,gems,active_traits)
        if score:desc+='#r#G装备评价采用本机计分。#n'
        if refinement['traits']:desc+='#r#G炼化属性#n#r'+refinement_desc(refinement)
        if strength:desc+='#r'+FashionRefinement.strength_desc(strength)
        if gems:desc+='#r#G镶嵌属性#n#r'+'#r'.join(f'{k}+{v}' for k,v in gems.items())
        if ornament:desc+='#r#G饰品精炼属性#n#r'+ornament
        from native946_quick_glyphs import description as glyph_description
        with self.transaction(write=False) as db:glyph=glyph_description(db,self.cid,item_id)
        if glyph:desc+='#r#G'+glyph+'#n'
        from native946_mall_carving import stored
        with self.transaction(write=False) as db:carve=stored(db,self.cid,item_id) if carve_preview is None else carve_preview
        # 946 c_new_item_string carries item_record_id and equip_score in
        # positions three and four. Durability belongs in the item text.
        return packet(791,['number','string','number','number','number','number','byte','string','carve_info'],
                      [item_id,desc,0,score,0,0,0,'',carve])

    def skill_packets(self,show=False,s=None):
        if s is None:s=self.snapshot()
        from native946_school import flight_stunt_records
        from native946_skill6 import list_packet
        feng = None
        if s['sanctification']:
            with self.transaction(write=False) as db:
                feng = list_packet(db, self.cid, s)
        skills=[{'skill_id':row['skill_id'],'practice':s['skills'][row['skill_id']],
                 'cost':row['cost'],'desc':row['des']} for row in self.combat_skills(s)]
        old_resists = s['_hero_resistance_records']
        school = s.get('school', 0)
        return (packet(699,['number',('array','fly_stunt_info'),('array','hero_skill_info'),'number',('array','resistant'),
                           'number',('array','resistant'),'number',('array','resistant'),'number','number'],
                       [school,flight_stunt_records(school,s.get('flight_level',0)),skills,*old_resists,int(s['rebirth']>=2 and s['level']>=101),int(show)]),
                packet(1314,[('array','pair_number')],[[{'key':k,'value':v} for k,v in s['shortcuts'].items()]]),
                training.list_packet(self,s), *((feng,) if feng else ()))

    def skill_damage(self,sid,s=None,target_defense=0):
        if s is None:s=self.snapshot()
        if sid not in s['skills']:raise ValueError('尚未学习此技能。')
        from native946_role_battle import niudao_damage, practice_damage_bonus, profile
        meta=profile(sid)
        if not meta:raise ValueError('技能没有原生战斗定义。')
        if sid == NIUDAO_XIAOSHI:
            return niudao_damage(s)
        target_defense=target_defense*max(0,1000-s['cultivation'].get('RoleIgnoreDefense',0))//1000
        result=(training.wuli_damage(sid,s['skills'][sid],s['attack'],s['level'],target_defense) if sid in training.WULI_BASE_DAMAGE
                else max(1,s['attack']-target_defense) if meta['family'] in ('physical','pierce','wuli')
                else 100+s['level']*8+practice_damage_bonus(sid,s['skills'][sid])+s.get('mag',0)*2)
        return max(1,result*(1000+s['cultivation'].get('XQAddHurtHpP',0)+s['cultivation'].get('RoleGroupPhysicalPercent',0))//1000)

    def skill_description(self,sid,s=None):
        from native946_role_battle import description
        return description(sid,s or self.snapshot())

    def combat_skills(self,s=None):
        from native946_role_battle import cost_description, owned_profile
        if s is None:s=self.snapshot()
        rows=[]
        for sid in s['skills']:
            try:meta=owned_profile(s,sid)
            except ValueError:continue
            rows.append(dict(skill_id=sid,cost=cost_description(s,meta),des=self.skill_description(sid,s)))
        return rows

    def combat_skills_packet(self):
        return packet(2149,['number',('array','attack_skill_info')],
                      [self.cid,self.combat_skills()])

    def task_records(self,s=None):
        if s is None:s=self.snapshot()
        q=s['quest'];active=bool(getattr(self, 'practice_trainer', False)) and q['state'] in ('active','ready')
        text='击败训练木偶 0/1' if q['state']=='active' else '训练已完成，请向本机教头交付。'
        trace=[{'id':QUEST_ID,'title':QUEST_NAME,'txt':text,'scene':2001,'x':302,'y':210,'tracetime':0}] if active else []
        listing={'升级':{},'剧情':{},'其他':{QUEST_ID:{'n':QUEST_NAME,'t':text,'d':'完成一次基础训练，向本机教头领取120经验和25金钱。',
                                                   'tr':1,'scene_id':2001,'x':302,'y':210,'npc_id':900001,'auto_path':0}} if active else {}}
        from native946_npc_quests import append_task_records
        append_task_records(s,trace,listing)
        from native946_task_chain import append_records
        append_records(s.get('task_chain'),trace,listing)
        from native946_campaign import append_records as append_campaign, newbie_trace
        from native946_school_daily import LIVENESS_GUIDE_ID, guide_details
        school_guide = guide_details(s)
        if school_guide:
            title, text = newbie_trace(school_guide['title'], school_guide.get('trace_text', school_guide['text']),
                                       school_guide.get('npc_name', ''))
            trace.append(dict(id=LIVENESS_GUIDE_ID,title=title,txt=text,
                              scene=school_guide['scene'],x=school_guide['x'],y=school_guide['y'],tracetime=0))
            listing['升级'][LIVENESS_GUIDE_ID] = dict(n=school_guide['title'],t=school_guide['text'],
                d=school_guide['description'],tr=1,scene_id=school_guide['scene'],x=school_guide['x'],
                y=school_guide['y'],npc_id=school_guide['npc'],auto_path=int(bool(school_guide['npc'])))
        from native946_local_guides import details as local_guides
        for tid, row in local_guides(s).items():
            trace.append(dict(id=tid,title=row['title'],txt=row['text'],scene=row['scene'],x=row['x'],y=row['y'],tracetime=0))
            listing['升级'][tid] = dict(n=row['title'],t=row['text'],d=row['description'],tr=1,scene_id=row['scene'],
                                      x=row['x'],y=row['y'],npc_id=row['npc'],auto_path=0)
        append_campaign(s,trace,listing)
        from native946_race_fabao import QUEST as RACE_QUEST, guide_details
        guide=guide_details(s)
        if guide:
            qid=int(RACE_QUEST)
            trace.append(dict(id=qid,title=guide['title'],txt=guide['text'],scene=guide['scene'],x=guide['x'],y=guide['y'],tracetime=0))
            listing['剧情'][qid]=dict(n=guide['title'],t=guide['text'],d=guide['description'],tr=1,
                                   scene_id=guide['scene'],x=guide['x'],y=guide['y'],npc_id=guide['npc'],auto_path=0)
        from native946_tasks import apply_preferences
        apply_preferences(s,trace,listing)
        return trace,listing

    def task_packets(self,s=None):
        trace,listing=self.task_records(s)
        return (packet(1978,[('array','task_trace_info')],[trace]),packet(1973,['string'],[lua_data(listing)]))

    def sync(self):
        s=self.snapshot()
        from native946_equipment_plans import active_packet
        with self.transaction(write=False) as db:plan=active_packet(db,self.cid)
        return (*self.attribute_packets(s),*self.item_packets(s),inventory_controls.page_packet(s['equipment_page']),plan,packet(1265,['byte'],[1]),*self.skill_packets(s=s),*self.task_packets(s))
