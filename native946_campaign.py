"""Durable reconstructed beginner/main campaign, using original 946 content.

Native text, NPCs and completion item rewards are source-backed. Stage
bindings, combat coefficients and XP/cash are explicitly local policy in
native946_campaign_data.json, not recovered official server scripts.
"""
from pathlib import Path
from secrets import randbelow
from time import time,monotonic
import json
from native946_codec import experience_notice_lines, packet, screen_notice, text_notice
import native946_cultivation as cultivation
import native946_cultivation_quests as cultivation_quests

# The existing service reloads campaign in place. Refresh the rules before
# rebuilding the trial menu and catalog so connected players use the same gates.
if 'TRIALS' in globals():
    from importlib import reload
    reload(cultivation)
    reload(cultivation_quests)

from native946_cultivation_quests import GUIDES, MEDICINES, chapters as cultivation_chapters, promotion
from native946_tasks import STORIES

DATA=json.loads(Path(__file__).with_name('native946_campaign_data.json').read_text('utf8'))
CHAPTERS=DATA['chapters']
NEWBIE_DATA=json.loads(Path(__file__).with_name('native946_newbie_story_data.json').read_text('utf8'))
NEWBIE_VERSION=NEWBIE_DATA['version']
NEWBIE_CHAPTER=NEWBIE_DATA['chapter']
CARTER_TARGET = NEWBIE_DATA['carter']
# Use the existing receipt table without reserving an index in the ordered route.
CARTER_RECEIPT = -1
DATA['items'].update(NEWBIE_DATA['items'])
TRIALS=cultivation_chapters(STORIES)
QUESTS={**CHAPTERS, **TRIALS}
for _kind, _medicine in MEDICINES.items():
    DATA['items'].setdefault(_kind, _medicine)
BY_QID={c['qid']:c for c in QUESTS.values()}
BY_NPC={}
BY_NPC[(CARTER_TARGET['scene'], CARTER_TARGET['npc'])] = True
REVIEW_GROUPS={}
REVIEW_CHAPTERS={}
for _chapter in (*QUESTS.values(), NEWBIE_CHAPTER):
    for _target in [_chapter['giver'],*(n['target'] for n in _chapter['nodes'])]:
        BY_NPC.setdefault((_target['scene'],_target['npc']),True)
    if _chapter['id'] != 'newbie' and _chapter['id'] in CHAPTERS:
        _first = _chapter.get('review_first', int(_chapter['id']))
        REVIEW_GROUPS.setdefault(_first, []).append(_chapter['id'])
        REVIEW_CHAPTERS[_chapter['source_recap_index']] = _chapter
VERSION=DATA['version']
REFERENCE_WIRE_VERSION=5
# Local point policy. The milestones use the verified 946 client display.
REVIEW_COMPLETION_POINTS = 20
REVIEW_CHAPTER_POINTS = 30
REVIEW_MILESTONES = ((300, 46258), (600, 65023), (900, 65265),
                     (1200, 65305), (1500, 65539), (1800, 65645))
BIOPIC_CHAPTER = '125200'
BIOPIC_INDEX = 4896
BIOPIC_KEY = 90001
BIOPIC_PIECES = range(489601, 489605)


def trial_schema(db):
    # A running service can load a new dispatcher before a cold store open.
    indexes = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='index' "
               "AND name IN ('native_campaign_mainline_current','native_campaign_trial_current','native_campaign_one_current')"))
    trial_ids = ','.join(f"'{key}'" for key in TRIALS)
    for name in ('native_campaign_mainline_current', 'native_campaign_trial_current'):
        if name in indexes and trial_ids not in indexes[name]:
            db.execute(f'DROP INDEX {name}')
            del indexes[name]
    if 'native_campaign_mainline_current' not in indexes:
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS native_campaign_mainline_current ON native_campaign_runs(character_id) "
                   f"WHERE state IN ('available','active') AND chapter NOT IN ({trial_ids})")
    if 'native_campaign_trial_current' not in indexes:
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS native_campaign_trial_current ON native_campaign_runs(character_id) "
                   f"WHERE state IN ('available','active') AND chapter IN ({trial_ids})")
    if 'native_campaign_one_current' in indexes:
        db.execute('DROP INDEX IF EXISTS native_campaign_one_current')


def schema(db):
    from native946_newbie_rewards import schema as reward_schema
    reward_schema(db)
    for statement in [
        '''CREATE TABLE IF NOT EXISTS native_campaign_runs (
        character_id INTEGER NOT NULL REFERENCES characters(id),chapter TEXT NOT NULL,
        node INTEGER NOT NULL DEFAULT 0 CHECK(node>=0),state TEXT NOT NULL CHECK(state IN ('available','active','completed')),
        version INTEGER NOT NULL,changed INTEGER NOT NULL,PRIMARY KEY(character_id,chapter))''',
        '''CREATE TABLE IF NOT EXISTS native_campaign_receipts (
        character_id INTEGER NOT NULL REFERENCES characters(id),chapter TEXT NOT NULL,node INTEGER NOT NULL,
        battle_id TEXT,experience INTEGER NOT NULL,gold INTEGER NOT NULL,created INTEGER NOT NULL,
        PRIMARY KEY(character_id,chapter,node))''',
        '''CREATE TABLE IF NOT EXISTS native_campaign_items (
        character_id INTEGER NOT NULL REFERENCES characters(id),chapter TEXT NOT NULL,type INTEGER NOT NULL,
        amount INTEGER NOT NULL CHECK(amount>0),PRIMARY KEY(character_id,chapter,type))''',
        '''CREATE TABLE IF NOT EXISTS native_campaign_pending (
        character_id INTEGER NOT NULL REFERENCES characters(id),chapter TEXT NOT NULL,type INTEGER NOT NULL,
        amount INTEGER NOT NULL CHECK(amount>0),PRIMARY KEY(character_id,chapter,type))''',
        '''CREATE TABLE IF NOT EXISTS native_campaign_review_claims (
        character_id INTEGER NOT NULL REFERENCES characters(id),
        kind TEXT NOT NULL CHECK(kind IN ('chapter','score')), reward_id INTEGER NOT NULL,
        created INTEGER NOT NULL,PRIMARY KEY(character_id,kind,reward_id))''',
        '''CREATE TABLE IF NOT EXISTS native_campaign_biopic_unlocks (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id),
        unlocked INTEGER NOT NULL DEFAULT 0 CHECK(unlocked IN (0,1)))''',
    ]:db.execute(statement)
    trial_schema(db)


def start_new_character(db,cid):
    from native946_newbie_rewards import seed
    seed(db,cid)
    db.execute("INSERT OR IGNORE INTO native_campaign_runs VALUES(?,'newbie',0,'active',?,?)",(cid,NEWBIE_VERSION,int(time())))


def snapshot(db,cid):
    return dict(runs={r['chapter']:dict(r) for r in db.execute('SELECT * FROM native_campaign_runs WHERE character_id=?',(cid,))},
                items=[dict(r) for r in db.execute('SELECT * FROM native_campaign_items WHERE character_id=?',(cid,))],
                pending=[dict(r) for r in db.execute('SELECT * FROM native_campaign_pending WHERE character_id=?',(cid,))],
                carter_done=bool(db.execute("SELECT 1 FROM native_campaign_receipts WHERE character_id=? AND chapter='newbie' AND node=?",
                                            (cid, CARTER_RECEIPT)).fetchone()))


def carter_handoff(s):
    campaign = s.get('campaign', {})
    run = campaign.get('runs', {}).get('newbie')
    if not run or run['version'] != NEWBIE_VERSION or run['state'] != 'completed':
        return False
    # The last stored step remains available to saves already past wood delivery.
    nodes = NEWBIE_CHAPTER['nodes']
    node = nodes[run['node'] - 1] if 0 < run['node'] <= len(nodes) else None
    return bool(node and node.get('handoff_to_carter'))


def carter_target(s):
    if carter_handoff(s) and not s['campaign'].get('carter_done'):
        return CARTER_TARGET
    return None


def current(s, trial=False):
    return next((r for r in s.get('campaign',{}).get('runs',{}).values()
                 if r['state'] in ('available','active') and (r['chapter'] in TRIALS) == trial),None)


def completed_names(s):
    return {CHAPTERS[k]['name'] for k,r in s.get('campaign',{}).get('runs',{}).items() if r['state']=='completed' and k in CHAPTERS}


def chapter_for(row):
    if row['chapter']=='newbie' and row['version']==NEWBIE_VERSION:
        return NEWBIE_CHAPTER
    return QUESTS[row['chapter']]


def stage(s, trial=False):
    row=current(s, trial)
    if not row:return None
    c=chapter_for(row)
    return c,c['nodes'][row['node']],row


def newbie_handoff_cost(chapter, s):
    from native946_progression import level_up_cost
    from native946_tasks import eligibility

    if chapter['previous'] != 'newbie':
        return None
    active = current(s)
    if not active or active['chapter'] != chapter['id'] or active['state'] != 'available':
        return None
    completed = completed_names(s)
    minimum = chapter['metadata']['min_grade']
    if CHAPTERS['newbie']['name'] not in completed or s['level'] >= minimum:
        return None
    if not eligibility(chapter['metadata'], dict(s, level=minimum), completed)[0]:
        return None
    return level_up_cost(s['level'], minimum, cultivation=s)


def task_details(s):
    return {**stage_details(s, stage(s)), **stage_details(s, stage(s, True))}


def stage_details(s, state):
    if not state:return {}
    c,node,row=state;t=c['giver'] if row['state']=='available' else node['target']
    if row['state']=='available':
        level=c.get('metadata',{}).get('grade','')
        text='达到'+level+'后，向'+t['name']+'领取'+c['name']+'。'
        cost = newbie_handoff_cost(c, s)
        if cost is not None:
            remaining = max(0, cost - s['experience'])
            text = (f'再获得{remaining}经验即可在{t["name"]}处升级并领取{c["name"]}。' if remaining
                    else f'与{t["name"]}交谈，可用现有经验升至{level}并领取{c["name"]}。')
    else:text='与'+t['name']+'交谈'+('并完成战斗' if node['action']=='battle' else '')+'（'+str(row['node']+1)+'/'+str(len(c['nodes']))+'）。'
    if c['id'] in TRIALS and row['node'] + 1 == len(c['nodes']):
        rule = promotion(c['id'], (s['rebirth'], s['ascension'], s['sanctification']))
        if rule and s['level'] < rule.level:
            text += f'达到{rule.level}级后可确认{rule.name}。'
    carried=[DATA['items'][str(i['type'])]['name']+'×'+str(i['amount']) for i in s['campaign']['items'] if i['chapter']==c['id']]
    description=node['text']+('#r携带任务物品：'+'、'.join(carried) if carried else '')
    title=c.get('display_name',c['name']+'·'+node['title'])
    return {c['qid']:dict(title=title,text=text,description=description,target_name=t['name'],
                         **{k:t[k] for k in ('scene','x','y','npc')})}


def settlement_message(chapter,node_index,snapshot,*,guardian_rewards=()):
    chapter_data=chapter_for(snapshot['campaign']['runs'][chapter])
    node=chapter_data['nodes'][node_index]
    reward=node['reward']
    if chapter_data is NEWBIE_CHAPTER:
        lines = experience_notice_lines(reward['experience'], guardian_rewards)
        if reward['gold']:
            lines.append(f'获得 {reward["gold"]} 金钱。')
        for kind, count in node['give'].items():
            name = DATA['items'][kind]['name']
            lines.append(f'获得任务物品 #G{name}×{count}#n。')
        return '#r'.join(lines) or node['title'] + '已完成。'
    text=node['title']+'已完成。经验+'+str(reward['experience'])+'，金钱+'+str(reward['gold'])+'。'
    trial = chapter in TRIALS
    if chapter != 'newbie' and node_index + 1 == len(QUESTS[chapter]['nodes']):
        if trial:
            if chapter == cultivation.FLIGHT_BREAKTHROUGH:
                text += '#r飞升等级上限已开放至180级。'
            else:
                stage_rule = cultivation.ADVANCEMENTS[cultivation_quests.TRIAL_RULES[chapter][0]]
                text += f'#r已完成{stage_rule.name}，获得{stage_rule.points}点属性、{stage_rule.resistance}点抗性。'
                if stage_rule.start_level is not None:
                    text += f'#r当前等级为{stage_rule.start_level}级。'
                if stage_rule.is_rebirth:
                    from native946_school import practice_limit

                    text += f'#r法术熟练度已清零，上限提升至{practice_limit(snapshot)}。'
        else:
            text+=f'#r本章通关获得{REVIEW_COMPLETION_POINTS}积分，可在剧情回顾查看。'
        if chapter == BIOPIC_CHAPTER:
            text+='#r已结算镜心之象，可从剧情进度菜单打开特殊绘卷。'
    pending=sum(row['amount'] for row in snapshot['campaign']['pending'])
    if pending:
        text+='#r还有'+str(pending)+'件剧情奖励暂存，可在剧情进度菜单领取。'
    current_stage=stage(snapshot, trial)
    if current_stage:
        next_chapter,next_node,run=current_stage
        target=next_chapter['giver'] if run['state']=='available' else next_node['target']
        text+='#r下一步前往'+target['name']+'，目标已记入任务日志。'
    else:
        text+='#r当前修行任务已完成。' if trial else '#r当前开放的剧情已完成。'
    return text


def newbie_trace(title, text, npc_name=''):
    from native946_navigation import task_trace_title

    if npc_name:
        text = text.replace(npc_name, f'#u#R{npc_name}#n', 1)
    return task_trace_title('指引', title), text


def append_records(s,trace,listing):
    from native946_navigation import task_trace_title

    newbie_run = s.get('campaign', {}).get('runs', {}).get('newbie')
    monkey_king = s.get('campaign', {}).get('runs', {}).get('111600')
    for qid,row in task_details(s).items():
        title, text = row['title'], row['text']
        if qid == NEWBIE_CHAPTER['qid'] and newbie_run and newbie_run['version'] == NEWBIE_VERSION:
            title, text = newbie_trace(title, text, row['target_name'])
        elif qid == QUESTS['111600']['qid'] and monkey_king and monkey_king['state'] == 'active':
            title = task_trace_title('主线', '美猴王')
            name = row['target_name']
            text = text.replace(name, f'#u#R{name}#n', 1)
            if monkey_king['node'] == 1 and row['npc'] == 2601:
                text = '与#u#R孙悟空#n交谈。#Y（建议将飞行旗坐标记录在此地，以免来回奔波。）#n'
        trace.append(dict(id=qid,title=title,txt=text,scene=row['scene'],x=row['x'],y=row['y'],tracetime=0))
        listing['剧情'][qid]=dict(n=row['title'],t=row['text'],d=row['description'],tr=1,scene_id=row['scene'],
                                  x=row['x'],y=row['y'],npc_id=row['npc'],auto_path=0)


def review_packet(s):
    runs = s.get('campaign', {}).get('runs', {})
    completed = {key for key, row in runs.items() if row['state'] == 'completed'}
    # The client indexes completion by a shared head ID. Reporting one chapter
    # early would also mark every other chapter with that ID as complete.
    done = [first for first, chapters in REVIEW_GROUPS.items() if completed.issuperset(chapters)]
    doing = [CHAPTERS[key]['name'] for key, row in runs.items()
             if key != 'newbie' and key in CHAPTERS and row['state'] == 'active']
    # Third native field is zuoqi_data (mounts), not a recap-stage array.
    return packet(4339,[('array','number'),('array','string'),('array','number')],[done,doing,[]])


def review_progress(db, cid):
    completed = {r['chapter'] for r in db.execute(
        "SELECT chapter FROM native_campaign_runs WHERE character_id=? AND state='completed'", (cid,))
                 if r['chapter'] != 'newbie' and r['chapter'] in CHAPTERS}
    claims = {'chapter': {}, 'score': {}}
    for row in db.execute('SELECT kind,reward_id FROM native_campaign_review_claims WHERE character_id=?', (cid,)):
        claims[row['kind']][row['reward_id']] = 1
    score = REVIEW_COMPLETION_POINTS * len(completed) + REVIEW_CHAPTER_POINTS * len(claims['chapter'])
    return completed, claims, score


def review_rewards_packet(claims, score):
    from native946_progression import lua_data

    return packet(4340, ['number','string','string'],
                  [score, lua_data(claims['chapter']), lua_data(claims['score'])])


def story_items_packet(task_id, s):
    from native946_progression import lua_data

    chapter = QUESTS.get(str(task_id))
    campaign = s['campaign']
    run = campaign['runs'].get(str(task_id))
    completed = run is not None and run['state'] == 'completed'
    pending = {row['type'] for row in campaign['pending'] if row['chapter'] == str(task_id)}
    items = []
    if chapter:
        for kind, amount in chapter['completion_items'].items():
            item_type = int(kind)
            items.append(dict(ItemType=item_type, Amount=amount, IsGot=int(completed and item_type not in pending)))
    return packet(5445, ['number', 'string'], [task_id, lua_data(items)])


def biopic_panel(unlocked):
    # The special page uses the complete-picture layout. Normal puzzle
    # rewards stay unclaimed and use their own flags.
    return packet(4180, ['number'] * 4 + [('array', 'number')] + ['number'] * 4,
                  [BIOPIC_INDEX, 1, 0, 0, [], 0, 0, 1, unlocked])


def queue_biopic_key(db, cid):
    added = db.execute('INSERT OR IGNORE INTO native_campaign_biopic_unlocks(character_id) VALUES(?)', (cid,)).rowcount
    if added:
        db.execute('INSERT INTO native_campaign_pending VALUES(?,?,?,1)', (cid, BIOPIC_CHAPTER, BIOPIC_KEY))
    return bool(added)


def retry_task_id(chapter):
    # The fail dialog locates the exact chapter by task_info.id. ScnrFirst
    # is shared by several chapters and would open the wrong review page.
    return chapter['qid'] if chapter['id'] == 'newbie' else int(chapter['id'])


def battle_attributes(chapter, node_index, stats):
    """Scale the local challenge to the heroes present when it starts."""
    if chapter['id'] == 'newbie':
        hp_multiplier, incoming_divisor, defended = 2, 16, False
    elif any(node['action'] == 'battle' for node in chapter['nodes'][node_index + 1:]):
        hp_multiplier, incoming_divisor, defended = 3, 15, False
    else:
        hp_multiplier, incoming_divisor, defended = 4, 12, True
    attack_sum = sum(s['attack'] for s in stats)
    average_health = sum(s['hpmax'] for s in stats) // len(stats)
    hp = max(15, hp_multiplier * attack_sum)
    defense = attack_sum // len(stats) // 8 if defended else 0
    return dict(hp=hp, hpmax=hp, attack=max(1, average_health // incoming_divisor), initiative=1, defense=defense)


def cultivation_hint(chapter, s):
    metadata = chapter.get('metadata', {})
    before = (s['rebirth'], s['ascension'], s['sanctification'])
    required = (metadata.get('min_rei', 0), metadata.get('min_fly', 0), metadata.get('min_feng', 0))
    if all(actual >= minimum for actual, minimum in zip(before, required)):
        return ''
    key = cultivation.FLIGHT_BREAKTHROUGH
    if before == cultivation.ADVANCEMENTS['breakthrough'].before and cultivation.level_cap(s) == cultivation.FLIGHT_LIMIT and s['level'] <= cultivation.FLIGHT_LIMIT:
        trial = TRIALS[key]
    else:
        trial = next((c for key, c in TRIALS.items() if key != cultivation.FLIGHT_BREAKTHROUGH and promotion(key, before)), None)
    if not trial:
        return ''
    return f'#r请达到{trial["metadata"]["grade"]}，向{trial["giver"]["name"]}领取{trial["name"]}。'


def deliver(db,cid,*,chapter=None):
    from native946_inventory_locks import require_unlocked
    from native946_mall_bags import pages
    from native946_progression import DATA as inventory_data, Progression

    filters = 'character_id=?'
    args = (cid,)
    if chapter is not None:
        filters += ' AND chapter=?'
        args += (chapter,)
    rewards = db.execute(
        f'SELECT * FROM native_campaign_pending WHERE {filters} ORDER BY chapter,type', args
    ).fetchall()
    if not rewards:
        return 0, 0

    bag_pages = pages(db, cid)
    placeholders = ','.join('?' for _ in bag_pages)
    occupied = {tuple(row) for row in db.execute(
        f'SELECT page,slot FROM inventory WHERE character_id=? AND page IN ({placeholders})',
        (cid, *bag_pages),
    )}
    free_slots = iter((page, slot) for page in bag_pages for slot in range(1, 25)
                      if (page, slot) not in occupied)
    equipment = {row[0] for row in db.execute('SELECT item_id FROM native_equipment WHERE character_id=?', (cid,))}
    inventory_state = {'equipment': equipment}
    now = int(time())
    delivered = pending = 0
    for reward in rewards:
        kind, amount = reward['type'], reward['amount']
        maximum = Progression.stack_limit({'type': kind, 'id': None}, inventory_state)
        valid_days = inventory_data['items'][str(kind)].get('valid_days', 0)
        expiry = now + valid_days * 86400 if valid_days else 0
        if maximum > 1 and not expiry:
            stacks = db.execute(
                f'''SELECT i.id,i.amount FROM inventory i
                    LEFT JOIN cosmetic_expiry e ON e.item_id=i.id
                    WHERE i.character_id=? AND i.type=? AND i.page IN ({placeholders})
                      AND i.amount<? AND coalesce(e.expires,0)=0 ORDER BY i.page,i.slot''',
                (cid, kind, *bag_pages, maximum),
            ).fetchall()
            for item in stacks:
                if item['id'] in equipment:
                    continue
                try:
                    require_unlocked(db, cid, item['id'])
                except ValueError:
                    continue
                added = min(amount, maximum - item['amount'])
                db.execute('UPDATE inventory SET amount=amount+? WHERE id=?', (added, item['id']))
                amount -= added
                if not amount:
                    break
        while amount and (slot := next(free_slots, None)) is not None:
            added = min(amount, maximum)
            item = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',
                              (cid, kind, *slot, added))
            if expiry:
                db.execute('INSERT INTO cosmetic_expiry VALUES(?,?)', (item.lastrowid, expiry))
            amount -= added
        key = (cid, reward['chapter'], kind)
        if amount:
            db.execute('UPDATE native_campaign_pending SET amount=? WHERE character_id=? AND chapter=? AND type=?', (amount, *key))
        else:
            db.execute('DELETE FROM native_campaign_pending WHERE character_id=? AND chapter=? AND type=?', key)
        delivered += reward['amount'] - amount
        pending += amount
    return delivered, pending


class Campaign:
    def __init__(self,game):self.g=game;self.p=game.progression
    def state(self):return self.p.snapshot()
    def near(self,target):
        c=self.g.character
        return c['map_id']==target['scene'] and max(abs(c['x']-target['x']),abs(c['y']-target['y']))<=12
    def validate_npc(self,nid):
        from native946_world import world
        from native946_gameplay import TRAINER_ID
        if self.g.battle:raise ValueError('请先结束当前战斗。')
        if nid==TRAINER_ID and getattr(self.g, 'practice_trainer', False) and self.g.npc_position:
            t=dict(scene=2001,x=self.g.npc_position[0],y=self.g.npc_position[1])
        else:
            n=world().npc(self.g.character['map_id'],nid)
            if n is None:raise ValueError('当前场景没有这位剧情联系人。')
            t=dict(scene=n['scene_id'],x=n['x']//20,y=n['y']//20)
        if not self.near(t):raise ValueError('请走近剧情联系人再交谈。')
    def menu(self,nid,text,choices):
        from native946_npc_quests import LOTUS_HELP_TARGET, Quests

        key=(self.g.character['map_id'],nid)
        if key==LOTUS_HELP_TARGET and Quests(self.g).active('lotus_help'):
            choices=[('寻求帮助' if label=='其他事务' and a['op']=='service' else label,a) for label,a in choices]
        self.g.dialog_actions=[dict(kind='campaign',**a) for _,a in choices]
        return self.g.menu(text,[label for label,_ in choices],nid)
    def close_menu(self):
        self.g.dialog_open = False
        self.g.dialog_actions = []
        self.g.dialog_npc = None
        return packet(1183, ['string'], ['dlg_npc_chat'])
    def refreshed(self,text,*,screen=False):
        self.p._cached_snapshot=None
        notice = screen_notice if screen else text_notice
        return self.g.defer_packets(lambda:(*self.g.sync_progression(),notice(text)))
    def eligibility(self,c,s):
        if c['id']=='newbie':return True,''
        if c['id'] in TRIALS:
            before = (s['rebirth'], s['ascension'], s['sanctification'])
            level = c['metadata']['min_grade']
            if promotion(c['id'], before) is None or s['level'] < level:
                return False, '领取条件：' + c['metadata']['grade'] + '。'
            return True, ''
        from native946_tasks import eligibility
        okay,reason=eligibility(c['metadata'],s,completed_names(s))
        if not okay:return okay,reason
        previous=s.get('campaign',{}).get('runs',{}).get(c['previous'])
        if not previous or previous['state']!='completed':return False,'请先完成'+CHAPTERS[c['previous']]['name']+'。'
        return True,''
    def accept(self,key,*,upgrade=False):
        c=QUESTS[key]
        if self.g.battle:raise ValueError('请先结束当前战斗。')
        if not self.near(c['giver']):raise ValueError('请前往'+c['giver']['name']+'处领取剧情。')
        with self.p.transaction() as db:
            s=self.p._snapshot(db);run=s.get('campaign',{}).get('runs',{}).get(key)
            if run and run['state']=='completed':raise ValueError('该剧情已完成，不能重复领取奖励。')
            active=current(s, key in TRIALS)
            if active and active['chapter']!=key:raise ValueError('请先完成当前剧情。')
            if upgrade:
                cost = newbie_handoff_cost(c, s)
                if cost is None or s['experience'] < cost:
                    raise ValueError('当前没有满足条件的新手升阶交接。')
                self.p.level_up(c['metadata']['min_grade'])
                self.g.summons.reconcile_levels()
                s = self.p._snapshot(db)
            okay,reason=self.eligibility(c,s)
            if not okay:raise ValueError(reason)
            if key in TRIALS:
                trial_schema(db)
            db.execute("INSERT INTO native_campaign_runs VALUES(?,?,0,'active',?,?) ON CONFLICT(character_id,chapter) DO UPDATE SET state='active',changed=excluded.changed WHERE native_campaign_runs.state='available'",(self.p.cid,key,NEWBIE_VERSION if key=='newbie' else VERSION,int(time())))
        self.p._cached_snapshot=None
    def review_rewards(self):
        with self.p.transaction(write=False) as db:
            _, claims, score = review_progress(db, self.p.cid)
        return review_rewards_packet(claims, score)
    def claim_review_reward(self, kind, reward_id):
        if kind == 'chapter':
            chapter = REVIEW_CHAPTERS.get(reward_id)
            if chapter is None:
                raise ValueError('没有这个章节奖励。')
        elif not 1 <= reward_id <= len(REVIEW_MILESTONES):
            raise ValueError('没有这个积分奖档。')
        if self.g.battle:
            return (self.review_rewards(), text_notice('请先结束战斗再领取剧情奖励。'))
        with self.p.transaction() as db:
            cid = self.p.cid
            completed, claims, score = review_progress(db, cid)
            if reward_id in claims[kind]:
                return (review_rewards_packet(claims, score), text_notice('本项剧情奖励已登记领取。'))
            if kind == 'chapter':
                if not completed.issuperset(REVIEW_GROUPS[chapter['review_first']]):
                    return (review_rewards_packet(claims, score), text_notice('请先完成对应剧情。'))
                score += REVIEW_CHAPTER_POINTS
                message = f'已领取{REVIEW_CHAPTER_POINTS}剧情积分。'
            else:
                threshold, item = REVIEW_MILESTONES[reward_id - 1]
                if score < threshold:
                    return (review_rewards_packet(claims, score), text_notice(f'达到{threshold}剧情积分后可领取。'))
                source = f'review-score:{reward_id}'
                db.execute('INSERT INTO native_campaign_pending VALUES(?,?,?,1)', (cid, source, item))
                deliver(db, cid)
                queued = db.execute('SELECT 1 FROM native_campaign_pending WHERE character_id=? AND chapter=?',
                                    (cid, source)).fetchone()
                message = ('积分奖品已暂存，可在剧情进度菜单领取。' if queued else '积分奖品已发到物品栏。')
            db.execute('INSERT INTO native_campaign_review_claims VALUES(?,?,?,?)',
                       (cid, kind, reward_id, int(time())))
            claims[kind][reward_id] = 1
        reply = review_rewards_packet(claims, score)
        if kind == 'score':
            return (reply, self.refreshed(message))
        return (reply, text_notice(message))
    def biopic(self, operation):
        if self.g.battle:
            raise ValueError('请先结束当前战斗。')
        from native946_mall_bags import pages
        from native946_mall_uses import consume

        cid = self.p.cid
        inventory_changed = False
        with self.p.transaction() as db:
            completed = db.execute(
                "SELECT 1 FROM native_campaign_runs WHERE character_id=? AND chapter=? AND state='completed'",
                (cid, BIOPIC_CHAPTER)).fetchone()
            if operation == 1 and completed and queue_biopic_key(db, cid):
                delivered, _ = deliver(db, cid)
                inventory_changed = bool(delivered)
            receipt = db.execute('SELECT unlocked FROM native_campaign_biopic_unlocks WHERE character_id=?', (cid,)).fetchone()
            unlocked = receipt[0] if receipt else 0
            if operation == 11 and not unlocked:
                if not completed or not receipt:
                    raise ValueError('请先完成镜影空天并领取镜心之象。')
                bag_pages = pages(db, cid)
                placeholders = ','.join('?' for _ in bag_pages)
                item = db.execute(
                    f'SELECT * FROM inventory WHERE character_id=? AND type=? AND page IN ({placeholders}) ORDER BY id LIMIT 1',
                    (cid, BIOPIC_KEY, *bag_pages)).fetchone()
                if not item:
                    raise ValueError('请将镜心之象放到可用物品栏再解锁。')
                consume(db, item)
                db.execute('UPDATE native_campaign_biopic_unlocks SET unlocked=1 WHERE character_id=?', (cid,))
                unlocked = 1
                inventory_changed = True
        panel = biopic_panel(unlocked)
        if inventory_changed:
            return self.g.defer_packets(lambda: (*self.p.item_packets(), panel))
        return panel
    def advance(self,key,node_index,*,battle=None,guardian=None,cinematic=None,delivery_npc=None):
        with self.p.transaction() as db:
            s=self.p._snapshot(db);run=s['campaign']['runs'].get(key)
            # Retries are idempotent even after advancing to the next chapter.
            if db.execute('SELECT 1 FROM native_campaign_receipts WHERE character_id=? AND chapter=? AND node=?',(self.p.cid,key,node_index)).fetchone():return False
            school_intro = key == 'newbie' and carter_handoff(s) and s['campaign']['carter_done']
            if not run or (run['state'] != 'active' and not school_intro) or run['node'] != node_index:
                raise ValueError('剧情阶段已改变，请重新交谈。')
            if run['version']!=VERSION and not (key=='newbie' and run['version']==NEWBIE_VERSION):
                raise ValueError('该剧情存档版本暂不支持。')
            c=chapter_for(run)
            node=c['nodes'][node_index]
            if node['action'] == 'give' and delivery_npc != node['target']['npc']:
                raise ValueError('请从给予面板交付任务物品。')
            if node['action']=='cinematic' and cinematic!=node['movie']:
                raise ValueError('请先结束当前过场。')
            if node['action']=='battle':
                expected=getattr(self.g,'_wild_battle',None)
                if not battle or battle is not expected or not isinstance(battle,CampaignBattle) or not battle.state['victory'] or self.p.cid not in battle.eligible:
                    raise ValueError('尚未完成本阶段剧情战斗。')
                if battle.eligible[self.p.cid]!=(key,node_index):raise ValueError('战斗不属于当前剧情阶段。')
            elif node['action']=='guardian':
                if not self.near(node['target']):raise ValueError('请前往当前剧情联系人处。')
                if guardian not in {r['type'] for r in DATA['starters']}:raise ValueError('请选择本阶段可领取的守护。')
                self.g.summons.acquire(guardian,'campaign:newbie:guardian')
            elif battle is not None:raise ValueError('本阶段无需战斗。')
            elif not self.near(node['target']):raise ValueError('请前往当前剧情联系人处。')
            last = node_index + 1 == len(c['nodes']) or node.get('handoff_to_carter', False)
            rule = None
            if last and key in TRIALS:
                before = (s['rebirth'], s['ascension'], s['sanctification'])
                rule = promotion(key, before)
                if rule is None:
                    raise ValueError('当前人物阶段已改变，无法重复办理修行。')
                cultivation.settle(self.p, db, s, rule)
            for kind,count in node['take'].items():
                owned=db.execute('SELECT amount FROM native_campaign_items WHERE character_id=? AND chapter=? AND type=?',(self.p.cid,key,int(kind))).fetchone()
                # Older runs could complete the preceding battle before it
                # issued this quest item. Honor their saved battle receipt.
                if owned is None and kind == node.get('legacy_missing_handoff') and db.execute(
                    'SELECT 1 FROM native_campaign_receipts WHERE character_id=? AND chapter=? AND node=? AND battle_id IS NOT NULL',
                    (self.p.cid, key, node_index-1),
                ).fetchone():
                    continue
                if not owned or owned[0]<count:raise ValueError('缺少任务物品：'+DATA['items'][kind]['name'])
                if owned[0]==count:db.execute('DELETE FROM native_campaign_items WHERE character_id=? AND chapter=? AND type=?',(self.p.cid,key,int(kind)))
                else:db.execute('UPDATE native_campaign_items SET amount=amount-? WHERE character_id=? AND chapter=? AND type=?',(count,self.p.cid,key,int(kind)))
            for kind,count in node['give'].items():
                db.execute('INSERT INTO native_campaign_items VALUES(?,?,?,?) ON CONFLICT(character_id,chapter,type) DO UPDATE SET amount=amount+excluded.amount',(self.p.cid,key,int(kind),count))
            reward=node['reward'];receipt='campaign:'+str(self.p.cid)+':'+key+':'+str(node_index)
            self.g.characters.settle_battle(self.g.character['owner'],self.p.cid,receipt,True,experience=reward['experience'],gold=reward['gold'],advance_quest=False)
            if rule is None or rule.start_level is None:
                self.g.auto_level()
            db.execute('INSERT INTO native_campaign_receipts VALUES(?,?,?,?,?,?,?)',(self.p.cid,key,node_index,battle.id if battle else None,reward['experience'],reward['gold'],int(time())))
            db.execute('UPDATE native_campaign_runs SET node=node+1,state=?,changed=? WHERE character_id=? AND chapter=? AND state=? AND node=?',
                       ('completed' if last else 'active', int(time()), self.p.cid, key, run['state'], node_index))
            if last:
                db.execute('DELETE FROM native_campaign_items WHERE character_id=? AND chapter=?',(self.p.cid,key))
                for kind,amount in c['completion_items'].items():db.execute('INSERT INTO native_campaign_pending VALUES(?,?,?,?)',(self.p.cid,key,int(kind),amount))
                if key == BIOPIC_CHAPTER:
                    queue_biopic_key(db, self.p.cid)
                if c['next']:db.execute("INSERT OR IGNORE INTO native_campaign_runs VALUES(?,?,0,'available',?,?)",(self.p.cid,c['next'],VERSION,int(time())))
            deliver(db,self.p.cid)
            if rule is not None:
                saved = db.execute('SELECT level,experience,gold FROM characters WHERE id=?', (self.p.cid,)).fetchone()
                self.g.character.update(dict(saved))
        self.p._cached_snapshot=None
        return True
    def hub(self,nid):
        s=self.state();state=stage(s);choices=[]
        if state:
            c,node,row=state
            t=c['giver'] if row['state']=='available' else node['target']
            choices.append(('前往'+t['name']+'继续'+c['name'],dict(op='travel',chapter=c['id'],node=row['node'],state=row['state'])))
            if self.near(t):choices.insert(0,('继续当前剧情',dict(op='open',chapter=c['id'],node=row['node'],state=row['state'])))
            text=c['name']+'：'+node['title']+'#r'+task_details(s)[c['qid']]['text']
        else:
            text='初入三界的历练从傲来的水月仙开始。'
            if any(key in CHAPTERS for key in s['campaign']['runs']):text='已完成当前开放的全部主线剧情。'
            else:choices.append(('前往水月仙开始新手历练',dict(op='newbie_route')))
        trial = stage(s, True)
        if trial:
            c,node,row = trial
            t = node['target']
            text += '#r'+c['name']+'：'+task_details(s)[c['qid']]['text']
            choices.insert(0, ('前往'+t['name']+'继续'+c['name'],
                               dict(op='travel', chapter=c['id'], node=row['node'], state=row['state'])))
        pending = sum(reward['amount'] for reward in s['campaign']['pending'])
        if pending:
            text += f'#r暂存奖励共{pending}件。'
            choices.append(('领取暂存剧情奖励',dict(op='claim')))
        mirror = s['campaign']['runs'].get(BIOPIC_CHAPTER)
        if mirror and mirror['state'] == 'completed':
            choices.append(('查看境·广目天王特殊绘卷', dict(op='biopic')))
        choices.append(('其他事务',dict(op='service')))
        return self.menu(nid,text,choices)
    def delivery_panel(self, chapter, node, run):
        npc = node['target']['npc']
        kinds = list(node['take'])
        owned = {str(item['type']): item['amount'] for item in self.state()['campaign']['items']
                 if item['chapter'] == chapter['id']}
        session = randbelow((1 << 30) - 1) + 1
        self.g.npc_service_session = dict(kind='campaign_delivery', npc=npc, scene=node['target']['scene'],
                                          id=session, expires=monotonic() + 180, chapter=chapter['id'], node=run['node'])
        return (self.close_menu(), packet(3100, ['number', 'byte', 'string', ('array', 'number'),
                                                ('array', 'number'), ('array', 'number'), 'number'],
                                          [npc, 0, node['text'], list(map(int, kinds)),
                                           [node['take'][kind] for kind in kinds],
                                           [owned.get(kind, 0) for kind in kinds], session]))

    def submit_delivery(self, session_id, option):
        session = getattr(self.g, 'npc_service_session', None)
        if (not session or session['kind'] != 'campaign_delivery' or session['id'] != session_id or option != 1
                or session['expires'] < monotonic() or session['scene'] != self.g.character['map_id'] or self.g.battle):
            raise ValueError('给予会话已失效，请重新交谈。')
        chapter, index, npc = session['chapter'], session['node'], session['npc']
        if not self.advance(chapter, index, delivery_npc=npc):
            return ()
        self.g.npc_service_session = None
        return (packet(1183, ['string'], ['dlg_npc_give']),
                self.refreshed(settlement_message(chapter, index, self.state()), screen=True))

    def visit_carter(self, npc):
        with self.p.transaction() as db:
            target = carter_target(self.p._snapshot(db))
            if not target or npc != target['npc'] or not self.near(target) or self.g.battle:
                raise ValueError('请跟踪任务，前往长安找对应车夫。')
            db.execute("INSERT INTO native_campaign_receipts VALUES(?,'newbie',?,NULL,0,0,?)",
                       (self.p.cid, CARTER_RECEIPT, int(time())))
        return (self.close_menu(), self.refreshed('已完成车夫指引。'))

    def look(self,nid):
        key=(self.g.character['map_id'],nid)
        if key not in BY_NPC:
            return None
        s=self.state();state=stage(s)
        carter = carter_target(s)
        if carter and key == (carter['scene'], carter['npc']):
            self.validate_npc(nid)
            return self.menu(nid, '可向车夫询问长安各处的路线。',
                             [('了解长安车夫', dict(op='visit_carter')), ('查看传送路线', dict(op='service'))])
        school_target = NEWBIE_CHAPTER['nodes'][-1]['target']
        if (key == (school_target['scene'], school_target['npc']) and carter_handoff(s)
                and s['campaign']['carter_done']):
            self.validate_npc(nid)
            return self.menu(nid, '向师门传送仙询问拜师路线。',
                             [('了解拜师路线', dict(op='visit_school_intro')), ('查看门派传送', dict(op='service'))])
        trial = stage(s, True)
        if trial and (trial[1]['target']['scene'], trial[1]['target']['npc']) == key:
            state = trial
        if state:
            c,node,row=state;t=c['giver'] if row['state']=='available' else node['target']
            if (t['scene'],t['npc'])==key:
                self.validate_npc(nid)
                if row['state']=='available':
                    okay,reason=self.eligibility(c,s)
                    choices=[('领取'+c['name'],dict(op='accept',chapter=c['id'],node=row['node'],state=row['state']))] if okay else []
                    cost = newbie_handoff_cost(c, s)
                    if cost is not None and s['experience'] >= cost:
                        choices.insert(0, (f'消耗{cost}经验升至{c["metadata"]["min_grade"]}级并领取',
                                           dict(op='handoff', chapter=c['id'], node=row['node'], state=row['state'])))
                    choices.extend([('查看当前进度',dict(op='hub')),('其他事务',dict(op='service'))])
                    hint = cultivation_hint(c, s) if not okay else ''
                    return self.menu(nid,c['name']+'#r'+(c.get('metadata',{}).get('desc_str') or node['text'])+('#r'+reason if reason else '')+hint,choices)
                action=dict(chapter=c['id'],node=row['node'],state=row['state'])
                if node['action'] == 'give':
                    return self.delivery_panel(c, node, row)
                if node['action']=='guardian':choices=[('结识'+p['name'],dict(op='guardian',guardian=p['type'],**action)) for p in DATA['starters']]
                else:choices=[('开始挑战' if node['action']=='battle' else '继续剧情',dict(op='fight' if node['action']=='battle' else 'complete',**action))]
                if c['id'] in TRIALS and row['node'] + 1 == len(c['nodes']):
                    rule = promotion(c['id'], (s['rebirth'], s['ascension'], s['sanctification']))
                    if rule:
                        choices = [(f'确认{rule.name}（{rule.level}级）', dict(op='complete', **action))]
                if node['action']=='cinematic':
                    choices=[('进入剧情',dict(op='movie',**action))]
                elif node['movie']:
                    choices.append(('观看相关过场',dict(op='movie',**action)))
                if node['action']=='battle':choices.append(('整理状态，准备挑战',dict(op='recover',**action)))
                choices.extend([('查看当前进度',dict(op='hub')),('其他事务',dict(op='service'))])
                description = node['text']
                if c['id'] in TRIALS and row['node'] + 1 == len(c['nodes']) and rule and rule.start_level is not None:
                    description += f'#r确认后将从{rule.start_level}级开始，当前经验清空，属性点按新等级重新分配。'
                    if rule.is_rebirth:
                        description += '#r已学法术的熟练度将清零，修习上限提高5000。'
                return self.menu(nid,c['name']+'·'+node['title']+'#r'+description,choices)
        elif key==(2008,5672) and not any(chapter in CHAPTERS for chapter in s['campaign']['runs']):
            self.validate_npc(nid)
            return self.menu(nid,'欢迎来到三界。准备好开始你的西游之旅了吗？',[('开始新手历练',dict(op='accept',chapter='newbie',node=0,state='none')),('其他事务',dict(op='service'))])
        if trial:
            if GUIDES.get(key) == trial[0]['id'] or any((c['giver']['scene'], c['giver']['npc']) == key for c in TRIALS.values()):
                self.validate_npc(nid)
                c,node,row = trial
                return self.menu(nid, '当前修行目标：'+node['target']['name'],
                                 [('前往继续'+c['name'], dict(op='travel', chapter=c['id'],
                                   node=row['node'], state=row['state'])), ('其他事务', dict(op='service'))])
            return None
        for c in TRIALS.values():
            giver = c['giver']
            saved = s['campaign']['runs'].get(c['id'])
            if (giver['scene'], giver['npc']) == key and not saved and self.eligibility(c,s)[0]:
                self.validate_npc(nid)
                return self.menu(nid, c['name']+'#r'+c['metadata']['desc_str'],
                                 [('领取'+c['name'], dict(op='accept', chapter=c['id'])),
                                  ('其他事务', dict(op='service'))])
        guide = TRIALS.get(GUIDES.get(key))
        if guide and self.eligibility(guide, s)[0]:
            self.validate_npc(nid)
            giver = guide['giver']
            return self.menu(nid, f'请先向{giver["name"]}领取{guide["name"]}。',
                             [('前往领取'+guide['name'], dict(op='trial_guide', chapter=guide['id'])),
                              ('其他事务', dict(op='service'))])
        return None
    def travel(self,target):
        self.g._campaign_bypass=False
        if self.near(target):return self.look(target['npc'])
        route=dict(id='campaign',to=target['scene'],arrival=target['arrival'])
        return self.g.change_scene(route,('campaign',target['scene'],target['npc'],monotonic()))
    def _expected(self,a):
        state=stage(self.state(), a.get('chapter') in TRIALS)
        if not state:raise ValueError('当前没有进行中的剧情。')
        c,node,row=state
        if (c['id'],row['node'],row['state'])!=(a.get('chapter'),a.get('node'),a.get('state')):raise ValueError('剧情阶段已改变，请重新交谈。')
        return c,node,row
    def movie(self,node):
        movie=node.get('movie',0)
        if not movie:return ()
        from native946_cinematics import play
        current=stage(self.state());resume=None
        if current and current[1]==node and current[2]['state']=='active':
            chapter,_,run=current
            kind='campaign_stage' if node['action']=='cinematic' else 'campaign'
            resume=dict(kind=kind,chapter=chapter['id'],node=run['node'],npc=node['target']['npc'])
        return play(self.g,movie,resume=resume)
    def retry(self, task_id):
        state = stage(self.state(), str(task_id) in TRIALS)
        if not state:
            raise ValueError('当前没有可重试的剧情战斗。')
        chapter, node, run = state
        expected = (task_id, chapter['id'], run['node'])
        if (run['state'] != 'active' or node['action'] != 'battle'
                or getattr(self.g, '_campaign_retry', None) != expected):
            raise ValueError('当前没有可重试的剧情战斗。')
        target = node['target']
        self.validate_npc(target['npc'])
        if not self.near(target):
            raise ValueError('请返回当前剧情联系人处重试。')
        return self.start_fight(chapter, run['node'], recovery_npc=target['npc'])
    def start_fight(self, chapter, node_index, *, recovery_npc=None):
        games = self.g.party_service.participants(self.g)
        if not games:
            raise ValueError('请由队长发起剧情战斗。')
        if chapter['id'] == 'newbie':
            games = [self.g]
        if any(g.battle or g.progression.snapshot()['hp'] <= 0 for g in games):
            if recovery_npc is not None:
                return self.look(recovery_npc)
            raise ValueError('请先恢复状态并结束已有战斗。')

        def enter():
            enter_shared_battle(games, lambda: CampaignBattle(self.g, games, chapter['id'], node_index))
            for g in games:g._campaign_retry = None
            return ()
        return self.g.defer_packets(enter)

    def execute(self,nid,a):
        self.validate_npc(nid);op=a['op']
        if op=='service':
            from native946_gameplay import TRAINER_ID
            if nid == TRAINER_ID:
                from native946_tasks import TaskJournal
                return TaskJournal(self.g).catalog(nid)
            self.g._campaign_bypass=True
            try:return self.g.world_npc_look(nid,include_mount_guide=False)
            finally:self.g._campaign_bypass=False
        if op=='hub':return self.hub(nid)
        if op == 'visit_carter':
            return self.visit_carter(nid)
        if op == 'visit_school_intro':
            index = len(NEWBIE_CHAPTER['nodes']) - 1
            if nid != NEWBIE_CHAPTER['nodes'][index]['target']['npc'] or self.g.battle:
                raise ValueError('请与师门传送仙交谈。')
            if not self.advance('newbie', index):
                return (self.close_menu(),)
            return (self.close_menu(), self.refreshed(settlement_message('newbie', index, self.state()), screen=True))
        if op=='biopic':return (self.close_menu(), self.biopic(1))
        if op=='newbie_route':return self.travel(CHAPTERS['newbie']['giver'])
        if op=='trial_guide':
            key = self.g.character['map_id'], nid
            if GUIDES.get(key) != a['chapter']:
                raise ValueError('请向对应修行联系人查询。')
            from native946_navigation import npc_auto_trace
            target = TRIALS[a['chapter']]['giver']
            return npc_auto_trace(target['scene'], target['x'], target['y'], target['npc'], target['name'])
        if op=='claim':
            with self.p.transaction() as db:
                delivered, pending = deliver(db, self.p.cid)
            if not delivered:
                message = '物品栏已满，剧情奖励仍保留在暂存中。' if pending else '没有待领取的剧情奖励。'
            else:
                message = f'已领取剧情奖励共{delivered}件。'
                if pending:
                    message += f'仍有{pending}件暂存。'
            return (self.close_menu(), self.refreshed(message))
        if op=='accept':
            if nid != QUESTS[a['chapter']]['giver']['npc']:
                raise ValueError('请向剧情领取人办理。')
            self.accept(a['chapter'])
            return (self.close_menu(), self.g.defer_packets(lambda:(*self.p.task_packets(),
                    text_notice('已领取任务。再次与当前联系人交谈可继续。'))))
        c,node,row=self._expected(a)
        if op=='handoff':
            if nid != c['giver']['npc']:
                raise ValueError('请向当前剧情领取人办理交接。')
            self.accept(c['id'], upgrade=True)
            return (self.close_menu(), self.refreshed('已升级并领取'+c['name']+'，目标已记入任务日志。'))
        if op=='travel':return self.travel(c['giver'] if row['state']=='available' else node['target'])
        if op=='open':return self.look((c['giver'] if row['state']=='available' else node['target'])['npc'])
        if op=='cultivate':
            raise ValueError('请完成相应的修行试炼后再领取主线剧情。')
        if row['state']!='active' or not self.near(node['target']) or nid!=node['target']['npc']:raise ValueError('请前往当前阶段的剧情联系人处。')
        if op=='movie':return self.movie(node)
        if op=='recover':
            if node['action'] != 'battle':
                raise ValueError('当前阶段无需战斗。')
            s=self.state();self.p.set_vitals(s['hpmax'],s['mpmax']);self.g.summons.rest()
            return (self.close_menu(), self.refreshed('状态已恢复，可以重新挑战。'))
        if op in ('complete','guardian'):
            if node['action']=='battle':raise ValueError('请先完成剧情战斗。')
            if not self.advance(c['id'],row['node'],guardian=a.get('guardian')):
                return (self.close_menu(),)
            return (self.close_menu(), self.refreshed(settlement_message(c['id'],row['node'],self.state()), screen=True))
        if op=='fight':
            if node['action']!='battle':raise ValueError('当前阶段无需战斗。')
            return self.start_fight(c, row['node'])
        raise ValueError('剧情选项已失效。')


from native946_wilderness import WildBattle,enter_shared_battle

class CampaignBattle(WildBattle):
    charge_guardian_participation_on_retreat = False

    def __init__(self,leader,games,chapter,node_index):
        data = chapter_for(leader.progression.snapshot()['campaign']['runs'][chapter])
        self.chapter_data=data
        self.chapter=chapter;self.node_index=node_index;node=data['nodes'][node_index]
        self.eligible={}
        for g in games:
            row=current(g.progression.snapshot(), chapter in TRIALS)
            if row and row['state']=='active' and (row['chapter'],row['node'])==(chapter,node_index):self.eligible[g.character['id']]=(chapter,node_index)
        stats=[g.progression.snapshot() for g in games];level=max(1,sum(s['level'] for s in stats)//len(stats))
        roster = node.get('roster')
        self.capture_training=any(enemy.get('capture_training') for enemy in roster or ())
        templates = [dict(type=enemy['shape'], name=enemy['name'],
                          guardian_type=enemy['shape'] if enemy.get('capture_training') else 0,
                          combat_stats={'capture_training':True} if enemy.get('capture_training') else {})
                     for enemy in (roster or [node['enemy']])]
        zone=dict(scene_name=data['name'], levels=[level,level], enemies=templates, fixed_roster=bool(roster),
                  immediate_defeat=chapter=='newbie')
        super().__init__(leader,games,zone)
        if node.get('hero_appearance'):
            self.state['fighters'][self.leader]['type']=node['hero_appearance']
        attributes = battle_attributes(data, node_index, stats)
        if not roster:
            for fighter in self.enemies()[1:]:self.state['fighters'].pop(fighter['id'])
        elif len(roster) > 1:
            # Keep a formation playable for a solo character on the local service.
            attributes = dict(attributes, hp=max(1, attributes['hp'] // len(roster)),
                              hpmax=max(1, attributes['hpmax'] // len(roster)),
                              attack=max(1, attributes['attack'] // len(roster)))
        for fighter in self.enemies():
            fighter.update(attributes)
            if fighter.get('capture_training'):
                fighter.update(level=1,hp=1,hpmax=1,attack=0)
        if chapter in CHAPTERS and chapter != 'newbie':
            # Tie the local spell damage to the existing stage scaling.
            first = self.enemies()[0]
            interval = 4 if any(n['action']=='battle' for n in data['nodes'][node_index + 1:]) else 3
            first.update(skills={63: 1}, enemy_spell_percent=100, enemy_spell_interval=interval,
                         local_spell_damage=first['attack'], mp=100, mpmax=100)
        self.project()
    def entry(self, game):
        replies = super().entry(game)
        if self.state['round']==1 and (self.capture_training or (self.chapter_data is CHAPTERS['newbie'] and self.node_index==1)):
            # The native guide anchors to the spell button after battle input opens.
            replies = (*replies, packet(1247, ['number'], [34]))
            if self.capture_training and game.character['id'] in self.eligible:
                replies = (*replies, screen_notice('攻击小馋猴。#r选择「捕捉」，再点选小龙人。'))
        return replies

    def entry_notice(self):
        text='剧情挑战开始：'+self.chapter_data['nodes'][self.node_index]['title']
        if self.chapter in CHAPTERS:text+='（本机战斗规则）'
        return text
    def settle(self,db,victory):
        if self.state['settled']:return
        advanced=[]
        if victory:
            for cid in self.eligible:
                if Campaign(self.games[cid]).advance(self.chapter,self.node_index,battle=self):advanced.append(cid)
        messages = {}
        node = self.chapter_data['nodes'][self.node_index]
        for cid,g in self.games.items():
            xp=node.get('guardian_experience',node['reward']['experience']//2) if cid in advanced else 0
            guardians = self.state['participants'][cid].values()
            for pet in guardians:
                g.summons.settle(pet['id'],self.id,pet['hp'],pet['mp'],victory,experience=xp)
            if cid in advanced:
                rewards = [(pet['name'], xp) for pet in guardians] if xp else ()
                messages[cid] = settlement_message(self.chapter,self.node_index,g.progression.snapshot(),
                                                   guardian_rewards=rewards)
        self.state['campaign_messages'] = messages
        self.state['campaign_advanced']=advanced;self.state['settled']=True
    def finish(self,victory):
        if self.state['phase']=='finished':return
        chapter = self.chapter_data
        task_id = retry_task_id(chapter)
        node = chapter['nodes'][self.node_index]
        self.close_battle(10)
        for cid,g in self.games.items():
            advanced=cid in self.state.get('campaign_advanced',[])
            g.progression._cached_snapshot=None
            message=(self.state.get('campaign_messages',{}).get(cid,'剧情挑战完成，后续目标已记入任务日志。') if advanced else
                     '剧情进度已保留，可恢复状态后再次挑战。')
            self.service.refresh_battle_scene(g)
            self.service.enqueue(g, screen_notice(message) if advanced else text_notice(message))
            if not victory and self.state.get('defeat') and cid == self.leader and cid in self.eligible and self.service.online(cid):
                g._campaign_retry = (task_id, self.chapter, self.node_index)
                self.service.enqueue(g, packet(5432, ['number'], [task_id]))
            if advanced and node.get('travel_after') and self.service.online(cid):
                next_state=stage(g.progression.snapshot())
                if next_state:
                    self.service.enqueue(g,*Campaign(g).travel(next_state[1]['target']))
            if advanced and node['movie']:self.service.enqueue(g,*Campaign(g).movie(node))
            g.last_event=dict(action='campaign_battle_finished',chapter=self.chapter,node=self.node_index,victory=bool(victory),advanced=advanced)
