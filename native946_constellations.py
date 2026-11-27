"""Native star talismans, persisted encounter credit and title rewards."""
from functools import lru_cache
import json
from pathlib import Path
from random import Random
from time import monotonic, time

from native946_codec import packet, text_notice


DATA = json.loads(Path(__file__).with_name('native946_constellation_data.json').read_text('utf8'))
GROUPS = {int(key): value for key, value in DATA['groups'].items()}
GROUP_POLICY = DATA['local_group_policy']
SIGILS = {int(key): value for key, value in DATA['sigils'].items()} | GROUPS
PENDING_GROUPS = {int(key): value for key, value in DATA['pending_groups'].items()}
SIGIL_ITEMS = {kind: rule['item'] for kind, rule in SIGILS.items()}
GROUP_PRIZES = GROUP_POLICY['prize_items']
NPCS = {(2001, 4285), (2015, 4236)}
MASTER = (2015, 4236)
BASE = 8_800_000
ID_RANGE = 100_000
LEVEL = 50
PAGE_SIZE = 6
LOW_STAR_SHOP = {'source':'本服规则', 'ranks':(1,2,3), 'cash_per_rank':800}


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_star_summons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner INTEGER NOT NULL REFERENCES characters(id),
        item_type INTEGER NOT NULL, scene INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
        exclusive_until INTEGER NOT NULL, ends INTEGER NOT NULL, members TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS native_star_summon_scene ON native_star_summons(scene,ends)')
    db.execute('''CREATE TABLE IF NOT EXISTS native_star_credit (
        character_id INTEGER NOT NULL REFERENCES characters(id), step INTEGER NOT NULL,
        mode INTEGER NOT NULL CHECK(mode IN (0,1)), PRIMARY KEY(character_id,step,mode))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_star_import (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_star_claims (
        character_id INTEGER NOT NULL REFERENCES characters(id), title INTEGER NOT NULL,
        experience INTEGER NOT NULL, PRIMARY KEY(character_id,title))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_star_equipment_claims (
        character_id INTEGER NOT NULL REFERENCES characters(id), title INTEGER NOT NULL,
        item_type INTEGER NOT NULL, item_id INTEGER NOT NULL, PRIMARY KEY(character_id,title))''')


def is_sigil_npc(npc):
    return BASE <= npc < BASE + ID_RANGE


def battle_key(row):
    return f"star-sigil:{row['id']}"


def credit(db, cid, star, summoned):
    steps = (star['step'], 1000+star['rank']) if star['step'] else (1000+star['rank'],)
    db.executemany('INSERT OR IGNORE INTO native_star_credit VALUES(?,?,?)',
                   ((cid, step, int(summoned)) for step in steps))


def completed(db, cid, *, include_history=False):
    result = {0: set(), 1: set()}
    for row in db.execute('SELECT step,mode FROM native_star_credit WHERE character_id=?', (cid,)):
        result[row['mode']].add(row['step'])
    if include_history and not db.execute('SELECT 1 FROM native_star_import WHERE character_id=?',(cid,)).fetchone():
        for star in historic_stars(db,cid):
            result[0].add(1000+star['rank'])
            if star['step']:
                result[0].add(star['step'])
    return result


def need(rule, finished, *, only_natural=False):
    if rule['summoned']:
        actual = finished[1]
    elif rule['naturals'] or only_natural:
        actual = finished[0]
    else:
        actual = finished[0] | finished[1]
    return [n for n in rule['steps'] if n not in actual]


def unlock(db, cid):
    finished = completed(db, cid)
    for title, rule in DATA['titles'].items():
        if not need(rule, finished):
            db.execute('INSERT INTO native_hero_titles(character_id,name,description) VALUES(?,?,?) '
                       'ON CONFLICT(character_id,name) DO UPDATE SET description=excluded.description,custom=0',
                       (cid, rule['name'], rule['desc']))


def historic_stars(db, cid):
    from native946_afei_expansion import EVENTS

    for old, payload in db.execute("SELECT battle_id,payload FROM native_encounter_rewards WHERE character_id=? "
                           "AND (battle_id LIKE 'star:%' OR battle_id LIKE 'afei-event:%')", (cid,)):
        if old.startswith('star:'):
            yield DATA['natural']['1']
            continue
        event = int(old.split(':')[1])
        if event < len(EVENTS) and (key := EVENTS[event].get('star_slot')) is not None:
            if key==4 and json.loads(payload).get('reference_encounter')!=100016:
                # Earlier builds used the four-unit 地贼星 roster for both spawns.
                key=3
            yield DATA['natural'][str(key)]


def migrate_history(db, cid):
    if db.execute('INSERT OR IGNORE INTO native_star_import VALUES(?)', (cid,)).rowcount:
        for star in historic_stars(db,cid):
            credit(db,cid,star,False)
        unlock(db,cid)


def win(db, cid, star, key, drops, *, summoned=False, item_type=None):
    migrate_history(db, cid)
    credit(db, cid, star, summoned)
    unlock(db, cid)
    if not summoned:
        candidates = [item for item, rule in SIGILS.items() if star['rank'] in rule['source_natural_ranks']]
        rng = Random(f'sigil-drop:{key}:{cid}')
        if candidates and rng.randrange(100) < DATA['drop_percent']:
            drops[rng.choice(candidates)] = 1
        eligible = [kind for kind, rule in GROUPS.items()
                    if kind not in (45097,45101,45102) and star['rank'] >= rule['rank']-1]
        if eligible and rng.randrange(100) < GROUP_POLICY['supply_percent']:
            drops[rng.choice(eligible)] = 1
    elif (bonus := GROUP_POLICY['bonus'].get(str(item_type))):
        kind, percent = bonus
        if Random(f'group-sigil:{key}:{cid}').randrange(100) < percent:
            drops[kind] = 1


def allowed(finished, rank):
    return not rank or any(step >= 1000+rank for step in finished[0] | finished[1])


def in_range(game, row, radius):
    role = game.character
    return role['map_id'] == row['scene'] and max(abs(role['x']-row['x']), abs(role['y']-row['y'])) <= radius


@lru_cache(maxsize=96)
def battle_template(kind):
    from native946_afei_expansion import EVENTS, ROSTERS

    rule = SIGILS[kind]
    index = next(i for i, event in enumerate(EVENTS) if event.get('star_slot') == rule['template'])
    template = EVENTS[index]
    if kind in GROUPS:
        from native946_constellation_battle import group_roster
        return group_roster(kind, rule, ROSTERS[index], GROUP_POLICY)
    leader, *followers = ROSTERS[index]
    roster = [dict(leader, name=rule['star_name'], type=rule.get('native_shape', leader['type'])), *followers]
    if 'variant_size' in rule and len(roster) < rule['variant_size']:
        roster = (roster[:1]+followers*rule['variant_size'])[:rule['variant_size']]
    reference = template['reference_encounter'] if rule['roster_source'].startswith('阿飞同名') else 0
    return dict(name=rule['star_name'], level=LEVEL, experience=rule['rank']*1000, cash=rule['rank']*200,
                drops=template['drops'], reference_encounter=reference, star_sigil=kind), roster


def use(game, target, iid):
    from native946_inventory_features import consume, owned
    from native946_map import load_map
    from native946_navigation import npc_auto_trace

    cid = game.progression.cid
    if game.battle or target not in (0,cid):
        raise ValueError('请由人物在非战斗状态下使用星符。')
    with game.progression.transaction() as db:
        item = owned(db, cid, iid)
        kind = item['type']
        rule = SIGILS.get(kind)
        if not rule:
            raise ValueError('此星符的召唤战斗还未开放。')
        if game.character['map_id'] != rule['map']:
            return text_notice(f"请前往{rule['map_name']}使用这张星符。")
        members = game.party_service.participants(game)
        if not members or any(person.battle or person.character['map_id'] != rule['map'] for person in members):
            raise ValueError('请由队长和在场队员一起召唤。')
        minimum = rule.get('min_character_level', LEVEL)
        if game.progression._snapshot(db)['level'] < minimum:
            return text_notice(f'人物须满{minimum}级才能挑战。星符已保留。')
        migrate_history(db, cid)
        if not allowed(completed(db,cid), rule['history_gate']):
            return text_notice(f"需先击败{rule['history_gate']}级或更高的星宿。星符已保留。")
        now = int(time())
        if db.execute("SELECT 1 FROM native_star_summons s WHERE owner=? AND ends>? AND NOT EXISTS "
                      "(SELECT 1 FROM native_encounter_rewards WHERE battle_id='star-sigil:'||s.id)", (cid, now)).fetchone():
            raise ValueError('你召唤的星宿还在场。')
        position = rule.get('summon_position', (game.character['x']+2, game.character['y']))
        x,y = load_map(rule['map']).nearest_walkable(*position)
        consume(db, cid, iid)
        sid = db.execute('INSERT INTO native_star_summons(owner,item_type,scene,x,y,exclusive_until,ends,members) '
                         'VALUES(?,?,?,?,?,?,?,?)', (cid,item['type'],rule['map'],x,y,now+rule['exclusive'],
                           now+rule['lifetime'], json.dumps([g.progression.cid for g in members]))).lastrowid
    game.progression._cached_snapshot = None
    npc = BASE + sid%ID_RANGE
    notice = '星符已使用，星宿已出现。'
    if kind in GROUPS:
        notice += '群星阵容未核实的部分、战斗机制和掉落采用本服规则。'
    return game.defer_packets(lambda: (*game.progression.item_packets(), *Constellations(game).scene_packets(),
                         npc_auto_trace(rule['map'],x,y,npc,rule['star_name']),text_notice(notice)))


class Constellations:
    def __init__(self, game):
        self.g = game
        self.p = game.progression

    def rows(self, radius):
        with self.p.transaction(write=False) as db:
            return [dict(row) for row in db.execute(
                "SELECT s.* FROM native_star_summons s WHERE scene=? AND ends>? AND NOT EXISTS "
                "(SELECT 1 FROM native_encounter_rewards WHERE battle_id='star-sigil:'||s.id)",
                (self.g.character['map_id'],int(time()))) if in_range(self.g,row,radius)]

    def scene_packets(self, force=False):
        from native946_npc_quests import numbers

        previous = getattr(self.g,'_sigil_visible',set())
        rows = self.rows(72)
        current = {BASE + row['id']%ID_RANGE for row in rows}
        self.g._sigil_visible = current
        replies = [] if force else [numbers(1604,npc) for npc in sorted(previous-current)]
        for row in rows:
            nid = BASE + row['id']%ID_RANGE
            if force or nid not in previous:
                event,roster = battle_template(row['item_type'])
                replies.append(packet(1589,['oci_npc'],[dict(id=nid,name=event['name'],title='星符召唤',
                    type=roster[0]['type'],class_type=1,x=row['x']*20,y=row['y']*20,dir=4,layer=1,member_click=1)]))
        return tuple(replies)

    def tick(self):
        now = monotonic()
        if now < getattr(self.g,'_sigil_poll_at',0):
            return ()
        self.g._sigil_poll_at = now+5
        return self.scene_packets()

    def target(self,npc):
        rows = [r for r in self.rows(12) if BASE + r['id']%ID_RANGE == npc]
        if len(rows) != 1:
            raise ValueError('请走近仍在场的星符召唤目标。')
        return rows[0]

    def look(self,npc):
        row = self.target(npc)
        rule = SIGILS[row['item_type']]
        self.g.dialog_actions = [dict(kind='constellation_challenge', summon=row['id'])]
        return self.g.menu(f"{rule['star_name']}。{rule['roster_source']}。召唤者队伍有优先挑战时间。",
                           ['挑战星符召唤的星宿'],npc)

    def start(self,npc,sid):
        from native946_afei_expansion import AfeiBattle
        from native946_constellation_battle import GroupBattle
        from native946_npc_quests import enter_quest_battle

        row = self.target(npc)
        if row['id'] != sid:
            raise ValueError('目标已更新，请重新对话。')
        rule = SIGILS[row['item_type']]
        games = self.g.party_service.participants(self.g)
        minimum = rule.get('min_character_level', LEVEL)
        if not games or any(not in_range(g,row,12) or g.progression.snapshot()['level']<minimum for g in games):
            raise ValueError(f'请由队长带队走近星宿，每位参战者须满{minimum}级。')
        if time() < row['exclusive_until'] and not {g.progression.cid for g in games} <= set(json.loads(row['members'])):
            raise ValueError('目前仍在召唤队伍的专属挑战时间。')
        with self.p.transaction() as db:
            for game in games:
                cid = game.progression.cid
                migrate_history(db,cid)
                if not allowed(completed(db,cid), rule['history_gate']):
                    raise ValueError(game.character['name']+'尚未达到此星符的挑战条件。')
        key = battle_key(row)
        if any(getattr(b,'afei_key',None) == key for b in self.g.party_service.battles.values()):
            raise ValueError('已有队伍在挑战。')
        event,roster = battle_template(row['item_type'])
        battle = GroupBattle if row['item_type'] in GROUPS else AfeiBattle
        return enter_quest_battle(self.g,games,lambda: battle(self.g,games,dict(key=key),event=event,roster=roster))

    def dialogue(self,npc,page=None):
        from native946_npc_services import NpcServices

        if page is None:
            choices = [('查看星宿称谓历程',dict(kind='constellation',op='list',page=0))]
            if npc == 4285:
                choices.append(('购买低级星符（本服）',dict(kind='constellation',op='sigil_shop',page=0)))
            return NpcServices(self.g)._menu(npc,'星宿称谓按击败记录解锁，祖老天师负责领奖。'
                '#r本服规则。野外星宿掉落单体星符的概率为20%，群星符另有掉落条件。'
                '#r使用前请前往道具所示地图。',choices)
        with self.p.transaction(write=False) as db:
            finished = completed(db,self.p.cid)
        titles = list(DATA['titles'].items())[page*PAGE_SIZE:(page+1)*PAGE_SIZE]
        choices = [(rule['name']+'（'+('已达成' if not need(rule,finished) else '未完成')+'）',
                    dict(kind='constellation',op='detail',title=title)) for title,rule in titles]
        if page:
            choices.append(('上一页',dict(kind='constellation',op='list',page=page-1)))
        if (page+1)*PAGE_SIZE<len(DATA['titles']):
            choices.append(('下一页',dict(kind='constellation',op='list',page=page+1)))
        return NpcServices(self.g)._menu(npc,'星宿称谓历程。',choices)

    def sigil_shop(self, npc, action):
        from native946_afei_society import spend_cash
        from native946_inventory_features import give
        from native946_npc_services import NpcServices

        if npc != 4285:
            raise ValueError('请到长安找陆伯言购买低级星符。')
        goods = {kind:rule for kind,rule in SIGILS.items() if kind not in GROUPS and rule['rank'] in LOW_STAR_SHOP['ranks']}
        if action['op'] == 'sigil_shop':
            page = action['page']
            entries = list(goods.items())
            choices = [(f"{rule['name']}（{rule['rank']*LOW_STAR_SHOP['cash_per_rank']}现金）",
                        dict(kind='constellation',op='sigil_buy',item=kind))
                       for kind,rule in entries[page*PAGE_SIZE:(page+1)*PAGE_SIZE]]
            for label, number in (('上一页',page-1),('下一页',page+1)):
                if 0 <= number*PAGE_SIZE < len(entries):
                    choices.append((label,dict(kind='constellation',op='sigil_shop',page=number)))
            return NpcServices(self.g)._menu(npc,'本服规则。只售一至三级单体星符，按现金结算。',choices)
        kind = action['item']
        if kind not in goods:
            raise ValueError('此处未出售该星符。')
        with self.p.transaction() as db:
            spend_cash(db,self.p.cid,goods[kind]['rank']*LOW_STAR_SHOP['cash_per_rank'])
            give(db,self.p,kind)
        self.p._cached_snapshot = None
        return self.g.defer_packets(lambda:(packet(1183,['string'],['dlg_npc_chat']),*self.g.sync_progression(),
                                             text_notice(f"本服规则。已购买{goods[kind]['name']}。")))

    def execute(self,npc,action):
        from native946_npc_services import NpcServices
        from native946_star_rewards import ITEMS, grant, issued, profile

        actor = NpcServices(self.g).validate(npc)
        location = (actor['scene_id'],npc)
        if location not in NPCS:
            raise ValueError('请找陆伯言或祖老天师。')
        op = action['op']
        if op in ('sigil_shop','sigil_buy'):
            return self.sigil_shop(npc,action)
        with self.p.transaction() as db:
            migrate_history(db,self.p.cid)
            finished = completed(db,self.p.cid)
            if op == 'list':
                return self.dialogue(npc,action['page'])
            title = action['title']
            rule = DATA['titles'][title]
            missing = need(rule, finished)
            claimed = issued(db,self.p.cid,title)
            race = db.execute('SELECT race FROM characters WHERE id=?',(self.p.cid,)).fetchone()[0]
            reward = profile(title,race)
            if op == 'detail':
                text = rule['desc']
                if missing:
                    text += '#r尚欠：'+'、'.join(DATA['steps'][str(i)] for i in missing)
                if claimed:
                    text += '#r称谓奖励已领取。'
                elif reward:
                    name = ITEMS[str(reward['item'])]['name']
                    label = '称谓装备'
                    if 'count' in reward:
                        name += f"×{reward['count']}"
                        label = '称谓奖励（本服）'
                    text += f'#r{label}：{name}，可在祖老天师处领取。'
                else:
                    text += '#r这项称谓装备的发放条件尚待核实。'
                text += '#r人物经验及殊勋点见人物属性的「我的殊勋」。'
                choices = [('领取称谓奖励' if 'count' in reward else '领取称谓装备',dict(kind='constellation',op='claim_gear',title=title))] if location==MASTER and reward and not missing and not claimed else []
                choices.append(('返回称谓历程',dict(kind='constellation',op='list',page=(int(title)-1)//PAGE_SIZE)))
                return NpcServices(self.g)._menu(npc,text,choices)
            if op != 'claim_gear' or location != MASTER or missing or not reward:
                raise ValueError('请在祖老天师处领取当前可领的称谓装备。')
            name = grant(db,self.p,title,reward)
        self.p._cached_snapshot = None
        return self.g.defer_packets(lambda: (packet(1183,['string'],['dlg_npc_chat']),*self.g.sync_progression(),
                                               text_notice(f"已领取{rule['name']}的称谓奖励{name}。")))


def npc_menu(game,npc):
    if (npc['scene_id'],npc['npc_id']) in NPCS:
        return Constellations(game).dialogue(npc['npc_id'])
