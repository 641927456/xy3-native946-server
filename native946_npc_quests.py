"""Daily NPC tasks with durable ownership and reward receipts."""
from contextlib import contextmanager, nullcontext
from datetime import datetime,timedelta,timezone
from functools import lru_cache, partial
from hashlib import sha256
from pathlib import Path
from random import Random
from secrets import token_hex
from time import time,monotonic
import json
from collections import deque

REFERENCE_WIRE_VERSION=15

from native946_codec import packet, screen_notice, text_notice
from native946_equipment import DATA
from native946_map import load_map
from native946_navigation import task_trace_title
from native946_world import world
from native946_wilderness import ENEMY_SLOTS, WildBattle,POLICY as WILD_POLICY,enter_shared_battle
from native946_school import (MASTERS, TASK_LEVEL, TASK_LIMIT, OBJECTIVE_NAMES, PATROL_MAPS, PATROL_RADIUS, VISIT_TARGETS,
                              MEDICINE_SUPPLY_AMOUNT, SUPPLY_MEDICINES, School, add_contribution, base_school, draw_objective)
from native946_school_daily import completion_packets, record_completion, reward_packets

GIVERS={tuple(v['giver']):k for k,v in DATA['activities'].items()}
ACTIVITIES=dict(DATA['activities'],school=dict(name='师门任务',level=TASK_LEVEL,ascension=0,
    team=False,limit=TASK_LIMIT,pet_factor=1))
LOTUS_RULES=json.loads(Path(__file__).with_name('native946_lotus_data.json').read_text('utf8'))
ACTIVITIES.update(LOTUS_RULES)
GIVERS[tuple(LOTUS_RULES['lotus']['giver'])]='lotus'
LOTUS_HELP_TARGET=tuple(LOTUS_RULES['lotus_help']['targets'][0][key] for key in ('scene_id','npc_id'))
from native946_daily_content import DATA as DAILY_CONTENT
TREASURE=DAILY_CONTENT['treasure']
ACTIVITIES['treasure']=dict(name='普通藏宝图',level=TREASURE['level'],ascension=0,team=False,
                            limit=TREASURE['limit'],giver=TREASURE['giver'],maps=TREASURE['maps'])
TTL=1200
ENCOUNTERS=json.loads(Path(__file__).with_name('native946_quest_encounter_data.json').read_text('utf8'))
SANJIE_DATA=json.loads(Path(__file__).with_name('native946_sanjie_data.json').read_text('utf8'))
# The three original branch IDs remain valid for tasks already saved.
SANJIE_TIERS=dict(enumerate(SANJIE_DATA['tiers'], 10))
SANJIE_OPPONENTS=SANJIE_DATA['opponents']
SANJIE_KITS={12: 6901, 16: 6902}
QUEST_PAGE=8

def numbers(mid,*values):return packet(mid,['number']*len(values),values)
def notice(text):return text_notice(text)
def day_key(now=None):
    return (datetime.fromtimestamp(time() if now is None else now,timezone.utc)+timedelta(hours=-4)).date().isoformat()

def day_start(now):
    return (now-14400)//86400*86400+14400

def sanjie_stage_target(tier, stage):
    node=tier['targets'][stage-1]
    scene,nid=node['scene_id'],node['npc_id']
    npc=world().npc(scene,nid)
    x,y=load_map(scene).nearest_walkable(npc['x']//20,npc['y']//20)
    return scene,x,y,nid,node['name'],node['shape']

def sanjie_kit_rows(db,cid,kind):
    return db.execute('SELECT id,amount FROM inventory WHERE character_id=? AND page=? AND type=? '
                      'AND amount>0 ORDER BY slot',(cid,QUEST_PAGE,kind)).fetchall()

def lotus_target(kind,ring):
    rule=LOTUS_RULES[kind]
    node=rule['ring_targets'][ring-1] if kind=='lotus' else rule['targets'][0]
    scene,nid=node['scene_id'],node['npc_id']
    npc=world().npc(scene,nid)
    x,y=load_map(scene).nearest_walkable(npc['x']//20,npc['y']//20)
    return scene,x,y,nid,node['name'],node.get('shape',npc['shape'])


def spawn_position(scene,rng):
    catalog=world();grid=load_map(scene)
    anchors=[tuple(r['arrival']) for r in catalog.data['portals']+catalog.data['npc_transports']
             if r['to']==scene and grid.walkable(*r['arrival'])]
    if not anchors:raise ValueError('此场景没有已核对的任务落点。')
    return rng.choice(encounter_cells(scene, rng.choice(anchors)))


@lru_cache(maxsize=128)
def encounter_cells(scene, anchor):
    grid=load_map(scene)
    seen={anchor};queue=deque([(anchor,0)]);choices=[]
    while queue:
        (x,y),distance=queue.popleft()
        if distance>=4:choices.append((x,y))
        if distance==12:continue
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            other=x+dx,y+dy
            if other not in seen and grid.walkable(*other):seen.add(other);queue.append((other,distance+1))
    if not choices:raise ValueError('此场景暂时没有可达的任务目标位置。')
    return tuple(choices)


def school_patrol_cells(scene, giver):
    npc = world().npc(scene, giver)
    anchor = load_map(scene).nearest_walkable(npc['x']//20, npc['y']//20)
    return encounter_cells(scene, anchor)


def schema(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS native_npc_quests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,token TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,
        branch INTEGER NOT NULL,leader_id INTEGER NOT NULL REFERENCES characters(id),
        created INTEGER NOT NULL,expires INTEGER NOT NULL,scene INTEGER NOT NULL,
        x INTEGER NOT NULL,y INTEGER NOT NULL,npc INTEGER NOT NULL,name TEXT NOT NULL,
        shape INTEGER NOT NULL,dynamic INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS native_npc_quest_members (
        task_id INTEGER NOT NULL REFERENCES native_npc_quests(id),character_id INTEGER NOT NULL REFERENCES characters(id),
        ring INTEGER NOT NULL CHECK(ring BETWEEN 1 AND 10),state TEXT NOT NULL,
        PRIMARY KEY(task_id,character_id));
      CREATE INDEX IF NOT EXISTS native_npc_quest_owner ON native_npc_quest_members(character_id,state);
      CREATE TABLE IF NOT EXISTS native_npc_activity_progress (
        character_id INTEGER NOT NULL REFERENCES characters(id),kind TEXT NOT NULL,day TEXT NOT NULL,
        accepted INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,
        next_ring INTEGER NOT NULL DEFAULT 1 CHECK(next_ring BETWEEN 1 AND 10),
        PRIMARY KEY(character_id,kind));
      CREATE TABLE IF NOT EXISTS native_npc_quest_rewards (
        task_id INTEGER NOT NULL REFERENCES native_npc_quests(id),character_id INTEGER NOT NULL REFERENCES characters(id),
        battle_id TEXT NOT NULL,experience INTEGER NOT NULL,gold INTEGER NOT NULL,pet_experience INTEGER NOT NULL,
        points INTEGER NOT NULL,drops TEXT NOT NULL,rule_version TEXT NOT NULL,
        PRIMARY KEY(task_id,character_id));
      CREATE TABLE IF NOT EXISTS native_npc_quest_wallet (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id),sanjie INTEGER NOT NULL DEFAULT 0 CHECK(sanjie>=0));
      CREATE TABLE IF NOT EXISTS native_npc_pending_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER NOT NULL REFERENCES native_npc_quests(id),
        character_id INTEGER NOT NULL REFERENCES characters(id),reference_type INTEGER NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),
        UNIQUE(task_id,character_id,reference_type));
      CREATE TABLE IF NOT EXISTS native_school_supply_tasks (
        task_id INTEGER PRIMARY KEY REFERENCES native_npc_quests(id),
        item_type INTEGER NOT NULL CHECK(item_type>0));
      CREATE TABLE IF NOT EXISTS native_school_quest_details (
        task_id INTEGER PRIMARY KEY REFERENCES native_npc_quests(id),
        objective TEXT NOT NULL CHECK(objective IN ('visit','supply','patrol')),
        amount INTEGER NOT NULL CHECK(amount BETWEEN 1 AND 3));
      CREATE TABLE IF NOT EXISTS native_school_patrol_progress (
        task_id INTEGER NOT NULL, character_id INTEGER NOT NULL, first_battle TEXT NOT NULL,
        PRIMARY KEY(task_id,character_id),
        FOREIGN KEY(task_id,character_id) REFERENCES native_npc_quest_members(task_id,character_id));
      CREATE TABLE IF NOT EXISTS native_school_milestones (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id), earned_day TEXT NOT NULL);
    ''')
    db.execute("INSERT OR IGNORE INTO native_school_milestones SELECT character_id,day "
               "FROM native_npc_activity_progress WHERE kind='school' AND completed>=?", (TASK_LIMIT,))


def active_records(db,cid,now=None):
    rows = [dict(r) for r in db.execute('SELECT q.*,m.ring,s.item_type AS supply_item, '
                "coalesce(d.objective,CASE WHEN s.item_type IS NOT NULL THEN 'supply' END) AS school_objective, "
                'coalesce(d.amount,1) AS supply_amount, p.first_battle AS patrol_first_battle FROM native_npc_quests q '
                'JOIN native_npc_quest_members m ON m.task_id=q.id LEFT JOIN native_school_supply_tasks s ON s.task_id=q.id '
                'LEFT JOIN native_school_quest_details d ON d.task_id=q.id '
                'LEFT JOIN native_school_patrol_progress p ON p.task_id=q.id AND p.character_id=m.character_id '
                'WHERE m.character_id=? AND m.state=\'active\' AND q.expires>? ORDER BY q.id',
                (cid,int(time() if now is None else now)))]
    # Apply the medicine rule to accepted tasks saved with an older quantity.
    for row in rows:
        if row['supply_item'] in SUPPLY_MEDICINES:
            row['supply_amount'] = MEDICINE_SUPPLY_AMOUNT
    return rows


def reward(kind,level,ring,rng,branch=0,*,rare_available=True):
    if kind not in ACTIVITIES or type(level) is not int or level<1 or type(ring) is not int or not 1<=ring<=10:
        raise ValueError('任务奖励参数无效。')
    meta=ACTIVITIES[kind];drops={};points=0
    if kind=='ghost':xp=level*level*(10+ring)//10+rng.randint(500,2000);cash=1+ring*10+rng.randint(1,10)
    elif kind=='king':xp=level*level*ring//4;cash=1+ring*50+rng.randint(1,10)
    elif kind=='heaven':xp=level*level*(40+ring)//40;cash=1+ring*10+rng.randint(1,50)
    elif kind=='treasure':
        return dict(experience=0,gold=0,guardian_experience=0,points=0,
                    drops={TREASURE['reference_item']:1},rare=False,policy=TREASURE['version'],victory=True)
    elif kind=='school':
        xp=level*level*(30+ring)//30
        return dict(experience=xp,gold=1+ring*3,guardian_experience=xp,points=0,drops={},
                    rare=False,policy='school-local-v1',victory=True)
    elif kind in LOTUS_RULES:
        if not 1<=ring<=meta['rings']:raise ValueError('莲花洞任务环次无效。')
        target=meta['targets'][0];den=target['denominator']
        xp=level*level*(den+ring)//den;cash=1+ring*10
        if ring==meta['rings']:
            xp*=2;cash*=2;points=target['points']
        roll=rng.randint(1,1000)
        drops[rng.choice(meta['pools'][0])]=1 if roll<=300 else rng.randint(1,3)
        return dict(experience=xp,gold=cash,guardian_experience=xp,points=points,drops=drops,
                    rare=False,policy='lotus-c350930-v1',victory=True)
    else:
        if branch in SANJIE_TIERS:
            tier=SANJIE_TIERS[branch]
            if ring>len(tier['targets']):raise ValueError('任务奖励参数无效。')
            final=ring==len(tier['targets'])
            den=tier['denominator'];xp=level*level*(den+ring)//den
            cash=1+ring*10;points=tier['token_points'] if final else 0
            if final:xp*=2;cash*=2
        else:
            if type(branch) is not int or not 0<=branch<3:raise ValueError('三界令分支无效。')
            target=meta['targets'][branch];den=target['denominator'];xp=level*level*(den+ring)//den
            cash=1+ring*10;points=target['points']
    rare=False
    if kind=='sanjie':
        if (points if branch in SANJIE_TIERS else ring==10) and rng.randint(1,1000)<=500:
            drops[rng.choice(meta['pools'][0])]=rng.randint(1,5)
    elif (kind!='ghost' or ring%2==0) and (kind!='king' or rng.randint(1,100)<=50):
        roll=rng.randint(1,1000);pools=meta['pools']
        if kind=='king' and roll==1 and rare_available:drops[rng.choice(pools[0])]=1;rare=True
        elif kind in ('ghost','heaven') and roll<=(1 if kind=='ghost' else 3) and rare_available:drops[2015]=1;rare=True
        elif roll<=100:drops[rng.choice(pools[1] if kind=='king' else pools[0])]=1
        elif roll<=300:drops[rng.choice(pools[2] if kind=='king' else pools[1])]=1
        else:drops[rng.choice(pools[3] if kind=='king' else pools[2])]=rng.randint(1,7 if kind=='heaven' else 3)
    return dict(experience=xp,gold=cash,guardian_experience=xp*meta['pet_factor'],points=points,drops=drops,rare=rare,
                policy=DATA['version'],victory=True)


def settle_reward(db, game, task, ring, receipt_id, value, *, complete_round=True):
    from native946_experience_charm import reward_experience
    cid = game.progression.cid
    value['experience'] = reward_experience(db, cid, value['experience'], int(time()))
    db.execute('INSERT INTO native_npc_quest_rewards VALUES(?,?,?,?,?,?,?,?,?)',
               (task['id'],cid,receipt_id,value['experience'],value['gold'],value['guardian_experience'],
                value['points'],json.dumps(value['drops']),value.get('policy',DATA['version'])))
    db.execute("UPDATE native_npc_quest_members SET state='completed' WHERE task_id=? AND character_id=?",
               (task['id'],cid))
    next_ring = ring % LOTUS_RULES.get(task['kind'],{}).get('rings',10) + 1
    if task['kind']=='school':
        day = day_key()
        rollover = db.execute("UPDATE native_npc_activity_progress SET day=?,accepted=1,completed=0 "
                              "WHERE character_id=? AND kind='school' AND day<>?", (day,cid,day)).rowcount
        if rollover:
            next_ring = 2
    if complete_round:
        db.execute('UPDATE native_npc_activity_progress SET completed=completed+1,next_ring=? WHERE character_id=? AND kind=?',
                   (next_ring,cid,task['kind']))
    if task['kind']=='school':
        add_contribution(db,cid,10)
        value.update(record_completion(db,cid,day))
    else:
        db.execute('INSERT INTO native_npc_quest_wallet(character_id,sanjie) VALUES(?,?) '
                   'ON CONFLICT(character_id) DO UPDATE SET sanjie=sanjie+excluded.sanjie',(cid,value['points']))
    game.characters.settle_battle(game.character['owner'],cid,'npcquest:'+str(task['id'])+':'+str(cid),True,
                                  experience=value['experience'],gold=value['gold'],advance_quest=False)
    game.auto_level()


def deliver(progression,db):
    cid=progression.cid;delivered=[]
    for row in db.execute('SELECT * FROM native_npc_pending_items WHERE character_id=? ORDER BY id',(cid,)).fetchall():
        if row['reference_type']==TREASURE['reference_item']:
            mapped=TREASURE['item'];maximum=1
        else:
            mapped=DATA['reward_map'].get(str(row['reference_type']))
            if mapped is None:continue
            native=mapped['type'] if isinstance(mapped,dict) else mapped
            maximum=1 if isinstance(mapped,dict) else DATA['reward_metadata'][str(native)]['max_stack']
        kind=mapped['type'] if isinstance(mapped,dict) else mapped
        remaining=row['amount']
        if maximum>1:
            for stack in db.execute('SELECT id,amount FROM inventory WHERE character_id=? AND page=8 AND type=? AND amount<? ORDER BY id',(cid,kind,maximum)).fetchall():
                add=min(maximum-stack['amount'],remaining);remaining-=add
                db.execute('UPDATE inventory SET amount=amount+? WHERE id=?',(add,stack['id']))
                if not remaining:break
        while remaining:
            occupied={r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(cid,))}
            slot=next((n for n in range(1,25) if n not in occupied),None)
            if slot is None:break
            amount=min(maximum,remaining);remaining-=amount
            iid=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,?,?)',(cid,kind,slot,amount)).lastrowid
            if isinstance(mapped,dict):
                if 'neidan' in mapped:db.execute('INSERT INTO legacy_neidan_items(item_id,kind) VALUES(?,?)',(iid,mapped['neidan']))
                elif 'equipment' in mapped:
                    from native946_equipment import persist
                    profile=DATA['rare_equipment'][str(mapped['equipment'])]
                    persist(db,cid,iid,profile,profile['fixed_properties'])
                else:raise ValueError('任务奖励实例类型未核定。')
        if remaining:db.execute('UPDATE native_npc_pending_items SET amount=? WHERE id=?',(remaining,row['id']))
        else:db.execute('DELETE FROM native_npc_pending_items WHERE id=?',(row['id'],))
        if remaining!=row['amount']:delivered.append((row['reference_type'],row['amount']-remaining))
    progression._cached_snapshot=None
    return delivered


class Quests:
    def __init__(self,game):self.g=game;self.p=game.progression

    def active(self,kind=None):
        with self.p.transaction(write=False) as db:rows=active_records(db,self.p.cid)
        return next((r for r in rows if r['kind']==kind),None) if kind else rows

    def pending_text(self,db):
        rows=db.execute('SELECT reference_type,sum(amount) amount FROM native_npc_pending_items WHERE character_id=? GROUP BY reference_type',(self.p.cid,)).fetchall()
        return '、'.join(DATA['reference_items'].get(str(r['reference_type']),{}).get('name','参考物品'+str(r['reference_type']))+'×'+str(r['amount']) for r in rows) or '无'

    def menu(self,npc):
        location=(npc['scene_id'],npc['npc_id'])
        if location==tuple(TREASURE['giver']):
            current=self.active('treasure')
            if current:
                choices=[('前往强盗所在处',dict(kind='npc_quest',op='navigate',task=current['id'])),
                         ('取消挖宝任务',dict(kind='npc_quest',op='cancel',task=current['id']))]
            else:
                choices=[('领取挖宝任务',dict(kind='npc_quest',op='accept',activity='treasure'))]
            choices.append(('领取暂存任务奖励',dict(kind='npc_quest',op='claim',activity='treasure')))
            return self._menu(npc['npc_id'],'50级起可单人领取挖宝任务。击败强盗获得普通藏宝图。'
                              +'#r本机任务限时30分钟，每日最多10次。右键使用宝图获取地点提示，到点再次使用挖宝。',choices)
        kind=GIVERS.get(location)
        if not kind:return None
        if kind=='sanjie':
            return self.sanjie_menu(npc['npc_id'])
        if kind=='lotus':
            return self.lotus_menu(npc['npc_id'])
        current=next((r for r in self.active() if r['npc']==npc['npc_id'] and r['scene']==npc['scene_id']),None)
        if current:
            choices=[('挑战任务目标',dict(kind='npc_quest',op='fight',task=current['id']))]
            return self._menu(npc['npc_id'],'这是'+ACTIVITIES[current['kind']]['name']+'的第'+str(current['ring'])+'环目标。',choices)
        if not kind:return None
        from native946_workshops import TASK_GIVERS
        rule=TASK_GIVERS[(npc['scene_id'],npc['npc_id'])]['description']
        choices=[('了解任务规则',dict(kind='npc_quest',op='rules',activity=kind))]
        current=self.active(kind)
        if current:
            choices.extend([('前往当前任务目标',dict(kind='npc_quest',op='navigate',task=current['id'])),
                            ('取消当前任务',dict(kind='npc_quest',op='cancel',task=current['id']))])
        else:choices.append(('领取下一环',dict(kind='npc_quest',op='accept',activity=kind,branch=0)))
        if kind=='ghost':
            from native946_afei_expansion import ghost_options
            choices.extend(ghost_options(self.g))
        choices.append(('领取暂存任务奖励',dict(kind='npc_quest',op='claim',activity=kind)))
        return self._menu(npc['npc_id'],rule+'#r每10环一轮，限时20分钟完成当前任务。',choices)

    def sanjie_menu(self,nid):
        current=self.active('sanjie')
        if current:
            choices=[('前往当前任务目标',dict(kind='npc_quest',op='navigate',task=current['id'])),
                     ('取消当前任务',dict(kind='npc_quest',op='cancel',task=current['id'])),
                     ('查询队员完成三界令任务情况',dict(kind='npc_quest',op='sanjie_team')),
                     ('算了，让我再考虑一下',dict(kind='npc_quest',op='close'))]
            if kit:=SANJIE_KITS.get(current['branch']):
                choices.insert(1,('补领'+SANJIE_DATA['items'][str(kit)]['name'],dict(kind='npc_quest',op='sanjie_kit')))
            return self._menu(nid,'你还有三界令任务在进行中。',choices)
        choices=[('好吧，我愿意接受大唐的任务',dict(kind='npc_quest',op='sanjie_tiers')),
                 ('了解如何做任务',dict(kind='npc_quest',op='sanjie_rules')),
                 ('查询队员完成三界令任务情况',dict(kind='npc_quest',op='sanjie_team')),
                 ('我要雇佣侠客帮我完成任务',dict(kind='npc_quest',op='sanjie_hire')),
                 ('算了，让我再考虑一下',dict(kind='npc_quest',op='close'))]
        return self._menu(nid,'我是唐王任命的大唐任务发布使，完成我的任务有助于提高你在大唐的地位，你愿意接受这些任务吗？',choices)

    def sanjie_team_menu(self,nid):
        games=self.g.party_service.participants(self.g)
        lines=[]
        with self.p.transaction(write=False) as db:
            for g in games:
                progress=db.execute("SELECT accepted,completed FROM native_npc_activity_progress "
                                    "WHERE character_id=? AND kind='sanjie' AND day=?",
                                    (g.progression.cid,day_key())).fetchone()
                branches=db.execute("SELECT count(DISTINCT q.branch) FROM native_npc_quests q "
                                    "JOIN native_npc_quest_members m ON m.task_id=q.id "
                                    "WHERE m.character_id=? AND q.kind='sanjie' AND q.created>=? AND m.ring=1",
                                    (g.progression.cid,day_start(int(time())))).fetchone()[0]
                completed=progress['completed'] if progress else 0
                lines.append(f'#r{g.character["name"]}：今日完成 {completed} 环，已领 {branches} 档')
        return self._menu(nid,'三界令任务完成情况：'+(''.join(lines) if lines else '#r当前没有队员。'),
                          [('查看可领取档位',dict(kind='npc_quest',op='sanjie_tiers')),
                           ('算了，让我再考虑一下',dict(kind='npc_quest',op='close'))])

    def _menu(self,nid,text,choices):
        self.g.dialog_actions=[action for label,action in choices]
        return self.g.menu(text,[label for label,action in choices],nid)

    def lotus_menu(self,nid):
        current=self.active('lotus')
        help_task=self.active('lotus_help')
        close=('算了，这就去办差事',dict(kind='npc_quest',op='close'))
        if help_task:
            return self._menu(nid,'先去兜率宫找太上老君。你也可以取消求助。',[
                ('前往太上老君处',dict(kind='npc_quest',op='navigate',task=help_task['id'])),
                ('取消求助',dict(kind='npc_quest',op='cancel',task=help_task['id'])),close])
        if current:
            details=task_details(current)
            body=(f'大王的差事还没办完呢——第{current["ring"]}/8环。#r'+details['text']+
                  '#r要是办不动，也可以花#Y5000两#W请太上老君帮个忙。')
            return self._menu(nid,body,[
                ('前往当前任务目标',dict(kind='npc_quest',op='navigate',task=current['id'])),
                ('寻求帮助',dict(kind='npc_quest',op='lotus_help')),
                ('取消当前任务',dict(kind='npc_quest',op='cancel',task=current['id'])),close])
        label=LOTUS_RULES['lotus']['task_open']
        text=('我是这莲花洞看炉子的小鬼。精细鬼替大王派下差事，按吩咐去办。'
              '上交#Y10个三界令#W，我就把大王的差事交给你，共八环，环环有赏。')
        return self._menu(nid,text,[(label,dict(kind='npc_quest',op='accept',activity='lotus',branch=0)),
                                    ('了解如何做任务',dict(kind='npc_quest',op='lotus_rules')),close])

    def validate_giver(self,nid,kind):
        meta=ACTIVITIES.get(kind);c=self.g.character
        if kind=='lotus_help':meta=LOTUS_RULES['lotus']
        if kind=='school':
            master=MASTERS.get((c['map_id'],nid))
            if not master or master!=base_school(self.p.snapshot()['school']):
                raise ValueError('请向自己门派的师父领取或办理师门任务。')
        elif not meta or [c['map_id'],nid]!=meta['giver']:raise ValueError('任务领取人不匹配。')
        npc=world().npc(c['map_id'],nid)
        if max(abs(npc['x']//20-c['x']),abs(npc['y']//20-c['y']))>12:raise ValueError('请走近任务领取人。')

    def target(self,row):
        c=self.g.character
        if c['map_id']!=row['scene'] or max(abs(c['x']-row['x']),abs(c['y']-row['y']))>12:
            raise ValueError('请走到当前任务目标附近。')

    def execute(self,nid,action):
        op=action['op']
        if op=='close':return self.g.close_npc_dialogue()
        if self.g.battle:raise ValueError('请先结束当前战斗。')
        if op in ('lotus_rules','lotus_help'):
            self.validate_giver(nid,'lotus')
            if op=='lotus_help':return self.accept('lotus_help',giver=nid)
            return self._menu(nid,'莲花洞差事一共#R八环#W，找人带话也好，降妖除怪也罢，按提示一环环去办就行。'
                              '#r领取每环需上交#R10个三界令#W。#r遇到难处可花#R5000两#W请兜率宫太上老君出手。',[
                ('好吧，我愿意接差事',dict(kind='npc_quest',op='accept',activity='lotus',branch=0)),
                ('算了，这就去办差事',dict(kind='npc_quest',op='close'))])
        if op in ('sanjie_tiers','sanjie_rules','sanjie_team','sanjie_hire','sanjie_kit'):
            self.validate_giver(nid,'sanjie')
            if op=='sanjie_kit':return self.replenish_sanjie_kit()
            close=('算了，让我再考虑一下',dict(kind='npc_quest',op='close'))
            tiers=('查看可领取档位',dict(kind='npc_quest',op='sanjie_tiers'))
            if op=='sanjie_tiers':
                choices=[(tier['task_open'],dict(kind='npc_quest',op='accept',activity='sanjie',branch=branch))
                         for branch,tier in SANJIE_TIERS.items()]
                return self._menu(nid,'选择任务：',[*choices,close])
            if op=='sanjie_team':return self.sanjie_team_menu(nid)
            if op=='sanjie_hire':
                return self._menu(nid,'雇佣侠客的功能还没有开放，这些任务你可以自己完成，奖励一样丰厚。',[tiers,close])
            text=('凡是等级达到或超过40级的玩家每天可以组队在我这领取#R三界令任务#W，'
                  '然后根据任务提示完成任务，我将会给与你们一定的奖励。#r'
                  '使用一定数量的三界令，可以在三界令兑换使那里兑换装备。')
            return self._menu(nid,text,[('好吧，我愿意接受大唐的任务',tiers[1]),
                                     ('前往吕毅兑换三界令',dict(kind='npc_quest',op='exchange_location',activity='sanjie')),
                                     ('领取暂存任务奖励',dict(kind='npc_quest',op='claim',activity='sanjie')),close])
        if op in ('rules','accept','claim','exchange_location'):
            kind=action['activity'];self.validate_giver(nid,kind)
            if op=='exchange_location':
                if kind!='sanjie':raise ValueError('请找宋寒查询三界令兑换。')
                from native946_navigation import npc_auto_trace
                merchant=world().npc(2001,4101)
                return npc_auto_trace(2001,merchant['x']//20,merchant['y']//20,4101,merchant['name'])
            if op=='accept':return self.accept(kind,action.get('branch',0),giver=nid)
            if op=='claim':
                with self.p.transaction() as db:
                    items=deliver(self.p,db);pending=self.pending_text(db);inventory=self.p.item_packets()
                return self.g.defer_packets(lambda:(*inventory,self.g.menu('已领取可交付奖励。仍暂存：'+pending,npc_id=nid)),
                                           fallback=(*inventory,notice('奖励已保存。仍暂存：'+pending)))
            from native946_workshops import Workshops,TASK_GIVERS
            text=TASK_GIVERS[tuple(ACTIVITIES[kind]['giver'])]['description']
            with self.p.transaction(write=False) as db:
                pending=self.pending_text(db)
                wallet=db.execute('SELECT sanjie FROM native_npc_quest_wallet WHERE character_id=?',(self.p.cid,)).fetchone()
                text+='#r三界令积分：'+str(wallet[0] if wallet else 0)
            return Workshops.tip(ACTIVITIES[kind]['name'],text+'#r限时20分钟，每10环一轮。取消、超时不会退回当日领取次数。#r暂存奖励：'+pending)
        row=next((r for r in self.active() if r['id']==action['task']),None)
        if row is None:raise ValueError('任务已经完成、取消或超时。')
        if op=='lotus_talk':
            if row['kind']!='lotus' or row['npc']!=nid or LOTUS_RULES['lotus']['ring_targets'][row['ring']-1]['mode']!='talk':
                raise ValueError('请与本环指定的联系人交谈。')
            self.target(row)
            return self.complete_lotus_talk(row)
        if op=='sanjie_other':
            if row['kind']!='sanjie' or nid!=row['npc']:raise ValueError('请与目标本人对话。')
            self.target(row)
            tier=SANJIE_TIERS[row['branch']]
            return notice('眼下你只有'+tier['branch']+'这一件事，点上面那行任务就能开始。')
        if op in ('visit','visit_other'):
            if row['school_objective']!='visit' or nid!=row['npc']:
                raise ValueError('请找本环指定的联系人。')
            self.target(row)
            if op=='visit_other':
                return self.g.world_npc_look(nid,include_school_visit=False)
            with self.p.transaction() as db:
                row=next((r for r in active_records(db,self.p.cid) if r['id']==action['task']),None)
                if row is None:
                    raise ValueError('找人任务已完成、取消或超时。')
                school=School(self.g)
                value=school.settle_errand(db,row,'visit')
            return school.errand_reply('visit','dlg_npc_chat',value)
        if op=='fight':
            if nid!=row['npc']:raise ValueError('任务目标不匹配。')
            if row['school_objective'] is not None:
                raise ValueError('请按师门任务所示方式完成。')
            if row['kind']=='lotus' and LOTUS_RULES['lotus']['ring_targets'][row['ring']-1]['mode']!='fight':
                raise ValueError('这一环需要与目标交谈。')
            self.target(row)
            return self.start(row)
        self.validate_giver(nid,row['kind'])
        if op=='navigate':
            from native946_navigation import npc_auto_trace
            return npc_auto_trace(row['scene'],row['x'],row['y'],row['npc'],row['name'])
        if op=='cancel':
            self.cancel(row['id'])
            return self.g.defer_packets(lambda:(*self.p.task_packets(),*self.scene_packets(),notice('任务已取消，当日领取次数不返还。')))
        raise ValueError('任务选项无效。')

    def complete_lotus_talk(self,row):
        ring=row['ring']
        with self.p.transaction() as db:
            if not any(r['id']==row['id'] for r in active_records(db,self.p.cid)):
                raise ValueError('这一环已经办完了。')
            value=reward('lotus',self.p._snapshot(db)['level'],ring,Random(row['token']+':talk'))
            settle_reward(db,self.g,row,ring,'lotus-talk:'+str(row['id']),value)
            for ref,amount in value['drops'].items():
                db.execute('INSERT INTO native_npc_pending_items(task_id,character_id,reference_type,amount) VALUES(?,?,?,?)',
                           (row['id'],self.p.cid,ref,amount))
            deliver(self.p,db)
        self.p._cached_snapshot=None
        text=LOTUS_RULES['lotus']['task_ends'][ring-1]
        return self.g.defer_packets(lambda:(self.g.close_npc_dialogue(),*self.p.task_packets(),*self.p.item_packets(),
                                             screen_notice(f'{text} 经验{value["experience"]}，现金{value["gold"]}。')))

    def replenish_sanjie_kit(self):
        from native946_inventory_features import give

        with self.p.transaction() as db:
            current=next((row for row in active_records(db,self.p.cid) if row['kind']=='sanjie'),None)
            kit=SANJIE_KITS.get(current['branch']) if current else None
            if not kit:raise ValueError('当前没有需要补领道具的三界令任务。')
            need=len(SANJIE_TIERS[current['branch']]['targets'])-current['ring']+1
            extra=need-sum(row['amount'] for row in sanjie_kit_rows(db,self.p.cid,kit))
            if extra<=0:return notice('任务道具数量充足。')
            give(db,self.p,kit,extra,destinations={QUEST_PAGE:24})
        self.p._cached_snapshot=None
        name=SANJIE_DATA['items'][str(kit)]['name']
        return self.g.defer_packets(lambda:(*self.p.item_packets(pages={QUEST_PAGE}),notice(f'已补领{name}×{extra}。')))

    def cancel(self,task_id):
        if self.g.battle:raise ValueError('战斗中不能放弃任务。')
        with self.p.transaction() as db:
            changed=db.execute("UPDATE native_npc_quest_members SET state='cancelled' WHERE task_id=? AND character_id=? AND state='active'",(task_id,self.p.cid)).rowcount
            if not changed:raise ValueError('没有这项可以放弃的任务。')
            if db.execute("SELECT 1 FROM native_npc_quests WHERE id=? AND kind='school'", (task_id,)).fetchone():
                self.g._auto_school = None

    def accept(self,kind,branch=0,*,giver=None):
        if kind not in ACTIVITIES or self.g.battle:raise ValueError('当前不能领取该任务。')
        meta=ACTIVITIES[kind]
        tier=SANJIE_TIERS.get(branch) if kind=='sanjie' and type(branch) is int else None
        if kind=='school':
            if MASTERS.get((self.g.character['map_id'],giver))!=branch:
                raise ValueError('师门任务领取人不匹配。')
            self.validate_giver(giver,kind)
        elif tier is None and (type(branch) is not int or not 0<=branch<(3 if kind=='sanjie' else 1)):
            raise ValueError('任务分支无效。')
        if kind=='ghost' and getattr(self.g,'_auto_ghost_until',0) > monotonic():
            raise ValueError('请先停止自动抓鬼。')
        if kind in LOTUS_RULES:
            self.validate_giver(giver,kind)
            if self.g.party_service.team(self.p.cid) is not None:raise ValueError('莲花洞差事请单人领取。')
        if kind=='treasure':
            self.validate_giver(giver,kind)
            if self.g.party_service.team(self.p.cid) is not None:
                raise ValueError('普通藏宝图任务请单人领取。')
        games=self.g.party_service.participants(self.g)
        if not games:raise ValueError('请由队长领取任务，或先暂离队伍。')
        if kind=='school':games=[self.g]
        if meta['team'] and self.g.party_service.team(self.p.cid) is None:raise ValueError('该任务需要先建立队伍。')
        token=token_hex(24);rng=Random(token);now=int(time());day=day_key(now)
        with self.p.transaction() as db:
            rings={}
            for g in games:
                db.execute("UPDATE native_npc_quest_members SET state='expired' WHERE character_id=? AND state='active' AND task_id IN (SELECT id FROM native_npc_quests WHERE expires<=?)",(g.character['id'],now))
                s=g.progression._snapshot(db)
                if kind=='school' and base_school(s['school'])!=branch:
                    raise ValueError('请向自己门派的师父领取任务。')
                if s['level']<(tier['level'] if tier else meta['level']) or s['ascension']<meta['ascension']:
                    raise ValueError(g.character['name']+'尚未满足此任务的客户端等级条件。')
                if kind in LOTUS_RULES and s['level']+1000*s['rebirth']+10000*s['ascension']+100000*s['sanctification']<meta['min_grade']:
                    raise ValueError('须达到二转100级才能办理莲花洞差事。')
                if any(r['kind']==kind for r in active_records(db,g.character['id'],now)):raise ValueError(g.character['name']+'已经有此类任务。')
                cid=g.character['id']
                db.execute('INSERT OR IGNORE INTO native_npc_activity_progress(character_id,kind,day) VALUES(?,?,?)',(cid,kind,day))
                reset_ring = ",next_ring=1" if kind == 'school' else ''
                db.execute(f'UPDATE native_npc_activity_progress SET day=?,accepted=0,completed=0{reset_ring} WHERE character_id=? AND kind=? AND day<>?',(day,cid,kind,day))
                progress=db.execute('SELECT * FROM native_npc_activity_progress WHERE character_id=? AND kind=?',(cid,kind)).fetchone()
                if tier:
                    accepted=db.execute("SELECT count(*) FROM native_npc_quests q "
                                        "JOIN native_npc_quest_members m ON m.task_id=q.id "
                                        "WHERE m.character_id=? AND q.kind='sanjie' AND q.branch=? "
                                        "AND q.created>=? AND m.ring=1",(cid,branch,day_start(now))).fetchone()[0]
                    if accepted>=tier['limit']:raise ValueError(g.character['name']+'已达到当日领取上限。')
                    rings[cid]=1
                else:
                    if meta['limit'] and progress['accepted']>=meta['limit']:raise ValueError(g.character['name']+'已达到当日领取上限。')
                    rings[cid]=progress['next_ring']
            objective,target_id,amount = None,None,1
            if kind == 'school':
                previous = db.execute('SELECT a.item_type FROM native_school_supply_tasks a '
                                      'JOIN native_npc_quest_members m ON m.task_id=a.task_id '
                                      'WHERE m.character_id=? ORDER BY a.task_id DESC LIMIT 1',
                                      (self.p.cid,)).fetchone()
                objective,target_id,amount = draw_objective(s['level'],rng,previous[0] if previous else None)
            supply_item=target_id if objective=='supply' else None
            if objective in ('supply','visit'):
                scene,nid=(self.g.character['map_id'],giver) if supply_item else target_id
                npc=world().npc(scene,nid)
                x,y=load_map(scene).nearest_walkable(npc['x']//20,npc['y']//20)
                name,shape,dynamic,selected=npc['name'],npc['shape'],0,branch
            elif kind=='treasure':
                scene=rng.choice(meta['maps'])
                x,y=spawn_position(scene,rng)
                name,shape,nid,dynamic,selected='强盗',TREASURE['bandit_shape'],0,1,0
            elif kind in ('ghost','king','school'):
                if objective == 'patrol' and getattr(self.g, '_auto_school', None):
                    scene = self.g.character['map_id']
                    x,y = rng.choice(school_patrol_cells(scene, giver))
                else:
                    scene=rng.choice(PATROL_MAPS[branch] if objective=='patrol' else meta['maps'])
                    x,y=spawn_position(scene,rng)
                from native946_wilderness import DATA as NATIVE_WILD
                allowed=[r for r in NATIVE_WILD['species'].values() if r['name'].partition('(')[0] in ('赌鬼','酒鬼','猴刀兵','巡海夜叉','龟丞相')]
                if not allowed:raise ValueError('缺少当前客户端可以显示的任务造型。')
                shape=rng.choice(allowed)['type']
                name='可疑行人' if objective=='patrol' else rng.choice(['子时','午时','戌时'])+('鬼王' if kind=='king' else '游魂')
                nid,dynamic,selected=0,1,branch if kind=='school' else 0
            elif kind in LOTUS_RULES:
                ring=rings[self.p.cid]
                if kind=='lotus_help':
                    if not any(r['kind']=='lotus' for r in active_records(db,self.p.cid,now)):
                        raise ValueError('先接莲花洞差事，再来寻求帮助。')
                    if not db.execute('UPDATE characters SET gold=gold-? WHERE id=? AND gold>=?',
                                      (meta['cost_gold'],self.p.cid,meta['cost_gold'])).rowcount:
                        raise ValueError('现金不足5000两。')
                else:
                    if not db.execute('UPDATE native_npc_quest_wallet SET sanjie=sanjie-? WHERE character_id=? AND sanjie>=?',
                                      (meta['cost_sanjie'],self.p.cid,meta['cost_sanjie'])).rowcount:
                        raise ValueError('领取差事需要10枚三界令。')
                scene,x,y,nid,name,shape=lotus_target(kind,ring)
                dynamic,selected=0,0
            elif tier:
                scene,x,y,nid,name,shape=sanjie_stage_target(tier,1)
                dynamic,selected=0,branch
            else:
                selected=branch if kind=='sanjie' else rng.randrange(len(meta['targets']));target=meta['targets'][selected]
                scene=target['scene_id'];grid=load_map(scene);x,y=grid.nearest_walkable(target['x']//20,target['y']//20)
                nid=target['npc_id'];shape=target['shape'];name=target['name'];dynamic=0
            lifetime=TREASURE['minutes']*60 if kind=='treasure' else TTL
            qid=db.execute('INSERT INTO native_npc_quests(token,kind,branch,leader_id,created,expires,scene,x,y,npc,name,shape,dynamic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (token,kind,selected,self.p.cid,now,now+lifetime,scene,x,y,nid,name,shape,dynamic)).lastrowid
            if supply_item:db.execute('INSERT INTO native_school_supply_tasks VALUES(?,?)',(qid,supply_item))
            if objective:db.execute('INSERT INTO native_school_quest_details VALUES(?,?,?)',(qid,objective,amount))
            if dynamic:db.execute('UPDATE native_npc_quests SET npc=? WHERE id=?',(940000+qid,qid))
            for cid,ring in rings.items():
                db.execute('INSERT INTO native_npc_quest_members VALUES(?,?,?,\'active\')',(qid,cid,ring))
                db.execute('UPDATE native_npc_activity_progress SET accepted=accepted+1 WHERE character_id=? AND kind=?',(cid,kind))
            kit=SANJIE_KITS.get(branch) if tier else None
            if kit:
                from native946_inventory_features import give
                for g in games:
                    missing=len(tier['targets'])-sum(row['amount'] for row in sanjie_kit_rows(db,g.progression.cid,kit))
                    if missing>0:give(db,g.progression,kit,missing,destinations={QUEST_PAGE:24})
        def publish():
            result=[]
            for g in games:
                g.progression._cached_snapshot=None
                task_type=' '+OBJECTIVE_NAMES[objective] if objective else ''
                replies=(*g.progression.task_packets(),*Quests(g).scene_packets(),notice('已领取'+meta['name']+'第'+str(rings[g.character['id']])+'环'+task_type+'。'))
                if kind in LOTUS_RULES:replies=(g.close_npc_dialogue(),*replies)
                if kind=='lotus_help':
                    g.character['gold']=g.progression.snapshot()['gold']
                    replies=(*replies,numbers(695,34,g.character['gold']))
                if kit:replies=(*replies,*g.progression.item_packets(pages={QUEST_PAGE}))
                if g is self.g:result.extend(replies)
                else:g.party_service.enqueue(g,*replies)
            return tuple(result)
        self.g.last_event=dict(action='npc_quest_accepted',task_id=qid,activity=kind,participants=list(rings))
        return self.g.defer_packets(publish)

    def relocate_auto_patrol(self, row, scene, giver):
        cells = school_patrol_cells(scene, giver)
        if row['scene'] == scene and (row['x'], row['y']) in cells:
            return row, ()
        x,y = Random(row['token']).choice(cells)
        with self.p.transaction() as db:
            current = next((task for task in active_records(db,self.p.cid) if task['id']==row['id']),None)
            if current is None or current['school_objective'] != 'patrol':
                raise ValueError('巡逻任务已完成、取消或超时。')
            db.execute('UPDATE native_npc_quests SET scene=?,x=?,y=? WHERE id=?', (scene,x,y,row['id']))
        self.p._cached_snapshot = None
        visible = getattr(self.g, '_quest_visible', set())
        removed = (numbers(1604,row['npc']),) if row['npc'] in visible else ()
        visible.discard(row['npc'])
        return dict(row, scene=scene, x=x, y=y), (*removed,*self.p.task_packets(),*self.scene_packets())

    def scene_packets(self,force=False):
        c=self.g.character;active=self.active()
        rows=[r for r in active if r['dynamic'] and r['scene']==c['map_id'] and max(abs(r['x']-c['x']),abs(r['y']-c['y']))<=72]
        now={r['npc'] for r in rows};before=set() if force else getattr(self.g,'_quest_visible',set());result=[numbers(1604,nid) for nid in before-now]
        for row in rows:
            if row['npc'] not in before:result.append(packet(1589,['oci_npc'],[dict(id=row['npc'],name=row['name'],title='巡逻区域' if row['school_objective']=='patrol' else '任务目标',type=row['shape'],
                   class_type=1,x=row['x']*20,y=row['y']*20,dir=4,layer=1,member_click=1)]))
        self.g._quest_visible=now
        return (*result,*self.sanjie_scene_appearance(active,force=force))

    def sanjie_scene_appearance(self,active=None,*,force=False):
        g=self.g;sid=g.character['map_id'];catalog=world()
        old_sid,old=getattr(g,'_sanjie_scene_appearance',(None,{}))
        if force or old_sid!=sid:old={}
        visible=getattr(g,'_world_visible',())
        overrides={}
        if sid==2256:
            for task in self.active() if active is None else active:
                if task['kind']!='sanjie' or task['scene']!=sid or task['npc'] not in visible:
                    continue
                native=catalog.npc(sid,task['npc'])
                appearance={key:task[key] for key in ('name','shape')}
                if native and any(native[key]!=value for key,value in appearance.items()):
                    overrides[task['npc']]=appearance
        changed={nid for nid in old.keys()|overrides.keys() if nid in visible and old.get(nid)!=overrides.get(nid)}
        g._sanjie_scene_appearance=(sid,overrides)
        return tuple(payload for nid in sorted(changed) for payload in
                     (numbers(1604,nid),catalog.npc_packet(sid,nid,appearance=overrides.get(nid))))

    def look(self,nid):
        row=next((r for r in self.active() if r['school_objective'] not in ('supply','visit') and r['npc']==nid and r['scene']==self.g.character['map_id']),None)
        if row is None:return None
        self.target(row)
        if row['school_objective']=='patrol':
            return self._menu(nid,'在这一带走动即可巡逻。',[])
        if row['kind'] in LOTUS_RULES:
            talk=row['kind']=='lotus' and LOTUS_RULES['lotus']['ring_targets'][row['ring']-1]['mode']=='talk'
            rule=LOTUS_RULES[row['kind']]
            text=(rule['task_ends'][row['ring']-1] if talk else
                  f'这是{rule["name"]}第{row["ring"]}环任务目标。')
            label='把话带到（完成本环）' if talk else '挑战任务目标'
            return self._menu(nid,text,[(label,dict(kind='npc_quest',op='lotus_talk' if talk else 'fight',task=row['id']))])
        if row['kind']=='sanjie' and row['branch'] in SANJIE_TIERS:
            tier=SANJIE_TIERS[row['branch']]
            choice='任务： ['+tier['branch']+']'
            if kit:=SANJIE_KITS.get(row['branch']):
                item=SANJIE_DATA['items'][str(kit)]['name']
                with self.p.transaction(write=False) as db:
                    if not sanjie_kit_rows(db,self.p.cid,kit):
                        return self._menu(nid,'缺少'+item+'。#r回长安找宋寒「补领任务道具」后再来。',
                                          [('回长安找宋寒补领'+item,dict(kind='npc_quest',op='close')),
                                           ('离开',dict(kind='npc_quest',op='close'))])
                choice='使用'+item+'（'+choice+'）'
            return self._menu(nid,'',[
                (choice,dict(kind='npc_quest',op='fight',task=row['id'])),
                ('<我找你有其他事>',dict(kind='npc_quest',op='sanjie_other',task=row['id'])),
                ('离开',dict(kind='npc_quest',op='close')),
            ])
        return self._menu(nid,'这是'+ACTIVITIES[row['kind']]['name']+'第'+str(row['ring'])+'环任务目标。',
                          [('挑战任务目标',dict(kind='npc_quest',op='fight',task=row['id']))])

    def visit_menu(self,nid):
        from native946_npc_services import NpcServices

        c=self.g.character
        key=(c['map_id'],nid)
        if key not in VISIT_TARGETS:return None
        row=self.active('school')
        if row is None or row['school_objective']!='visit' or (row['scene'],row['npc'])!=key:return None
        self.target(row)
        return NpcServices(self.g)._menu(nid,'我在等这封书信。',[
            ('送交师父的书信',dict(kind='npc_quest',op='visit',task=row['id'])),
            ('查看其他对话',dict(kind='npc_quest',op='visit_other',task=row['id'])),
        ],speaker=nid)

    def patrol_moved(self,scene,x,y):
        row=self.active('school')
        if row is None or row['school_objective']!='patrol' or row['scene']!=scene:return False
        if max(abs(x-row['x']),abs(y-row['y']))>PATROL_RADIUS:return False
        snapshot=self.p.snapshot()
        if not snapshot['hp'] or row['branch']!=base_school(snapshot['school']) or not self.g.party_service.participants(self.g):
            return False
        self.start(row)
        return True

    def start(self,row):
        if row['kind'] in ('treasure', *LOTUS_RULES) and self.g.party_service.team(self.p.cid) is not None:
            raise ValueError('这个任务请单人完成。')
        if row.get('supply_item'):raise ValueError('请向领取任务的师父交付寻物。')
        games=self.g.party_service.participants(self.g)
        games=[g for g in games if any(r['id']==row['id'] for r in Quests(g).active())]
        if not games:raise ValueError('当前没有可以参加此任务战斗的队员。')
        kit=SANJIE_KITS.get(row['branch']) if row['kind']=='sanjie' else None
        if kit:
            @contextmanager
            def prepare_kit():
                from native946_inventory_locks import require_unlocked
                with self.p.transaction() as db:
                    for g in games:
                        cid=g.progression.cid
                        rows=sanjie_kit_rows(db,cid,kit)
                        if not rows:raise ValueError('队员缺少三界令任务道具，请找宋寒补领。')
                        item=rows[0]
                        require_unlocked(db,cid,item['id'])
                        if item['amount']==1:
                            db.execute('DELETE FROM inventory WHERE id=? AND character_id=?',(item['id'],cid))
                        else:
                            db.execute('UPDATE inventory SET amount=amount-1 WHERE id=? AND character_id=?',(item['id'],cid))
                    yield
            return enter_quest_battle(self.g,games,lambda:QuestBattle(self.g,games,row),prepare=prepare_kit)
        return enter_quest_battle(self.g,games,lambda:QuestBattle(self.g,games,row))


def enter_quest_battle(game,games,build,*,prepare=nullcontext):
    if any(g.battle or g.progression.snapshot()['hp']<=0 for g in games):raise ValueError('请先恢复队员状态，或结束现有战斗。')
    def enter():
        result=enter_shared_battle(games,build,prepare=prepare)
        if prepare is not nullcontext:
            for g in games:
                g.progression._cached_snapshot=None
                g.party_service.enqueue(g,*g.progression.item_packets(pages={QUEST_PAGE}))
        return result
    return game.defer_packets(enter)


def reference_roster(task, party_size):
    encounter=ENCOUNTERS['encounters'].get(task['kind'])
    if not encounter:
        return []
    count=4 if party_size<=3 else 7 if party_size==4 else 8
    rng=Random(task['token'])
    roster=[]
    for index in range(count):
        row=encounter['roster'][index] if 'roster' in encounter else encounter['stats']
        stats={key:row[key] for key in ('hpmax','mpmax','attack','initiative','defense','mag',
                                       'reference_element_resist','reference_element_power') if key in row}
        for key in ('attack','initiative'):
            if isinstance(stats[key],list):
                stats[key]=rng.randint(*stats[key])
        if 'models' in encounter:
            name,shape=rng.choice(tuple(encounter['models'].items()))
            name+='恶鬼'
        else:
            name,shape=row['name'],row['type']
        has_spells=rng.randint(1,100)<(100 if task['kind']=='ghost' else 40)
        stats.update(skills=dict.fromkeys(ENCOUNTERS['skills'],1) if has_spells else {},
                     enemy_spell_percent=80, reference_monster=row.get('reference_id'))
        roster.append(dict(type=shape,name=name,guardian_type=0,combat_stats=stats))
    return roster


class QuestBattle(WildBattle):
    charge_guardian_participation_on_retreat = False

    def __init__(self,leader,games,task):
        self.task=dict(task)
        self.sanjie_tier=SANJIE_TIERS.get(task['branch']) if task['kind']=='sanjie' else None
        self.sanjie_script=SANJIE_OPPONENTS[str(task['npc'])] if self.sanjie_tier else {}
        if self.sanjie_script.get('summon'):
            if self.sanjie_script['summon'].get('trigger')=='party':
                self.on_round_start=partial(self.reinforce_sanjie,before_actions=True)
            else:
                self.after_round=partial(self.reinforce_sanjie,before_actions=False)
        level=max(1,sum(g.progression.snapshot()['level'] for g in games)//len(games))
        roster=reference_roster(task,len(games))
        if roster:
            level=50 if task['kind']=='ghost' else 40
        if self.sanjie_script:
            stats=dict(roster[0]['combat_stats'])
            stats.pop('reference_monster',None)
            roster=[dict(name=unit['name'],type=unit['shape'],guardian_type=0,combat_stats=dict(stats))
                    for unit in self.sanjie_script['party']]
        buzhou_patrol = task.get('school_objective') == 'patrol' and task['scene'] == 2360
        if buzhou_patrol:
            # The captured Buzhou patrol has two distinct models. Keep the
            # saved patrol shape and use another model from the native pool.
            shapes = (task['shape'], 3150 if task['shape'] != 3150 else 3151)
            roster = [dict(type=shape, name='捣乱的宵小', guardian_type=0) for shape in shapes]
        zone=dict(scene_name=ACTIVITIES[task['kind']]['name'],levels=[level,level],
                  enemies=roster or [dict(type=task['shape'],name=task['name'],guardian_type=0)],
                  fixed_roster=bool(roster))
        if task.get('school_objective') == 'patrol':
            # 946 xyscene.load_war_map uses the current scene for a zero
            # resource ID. Patrol fights keep the terrain at the encounter.
            zone['battle_map'] = 0
        super().__init__(leader,games,zone)
        if task['kind']=='school':
            hero=self.state['fighters'][self.leader]
            enemies = self.enemies()
            if not buzhou_patrol:
                for enemy in enemies[1:]:
                    self.state['fighters'].pop(enemy['id'])
                enemies = enemies[:1]
            hp=max(15,hero['attack']*3)
            for enemy in enemies:
                enemy.update(hp=hp,hpmax=hp,attack=max(1,hero['hpmax']//14),initiative=1,defense=0)
            self.project()

    def entry_notice(self):
        return self.sanjie_script.get('challenge') or '任务战斗已开始。'

    def reinforce_sanjie(self,runtime,before_actions):
        summon=self.sanjie_script.get('summon')
        if not summon or (summon.get('trigger')=='party')!=before_actions:
            return
        if runtime.round!=summon.get('round',1) or self.state.get('sanjie_reinforced'):
            return
        enemies=self.enemies()
        if not enemies:return
        self.state['sanjie_reinforced']=True
        for text in (self.sanjie_script.get('buff_notice'),summon.get('notice')):
            if text:runtime.out.append(screen_notice(text))
        ref=enemies[0]
        occupied={enemy['slot'] for enemy in enemies}
        slots=[slot for slot in summon.get('slots',()) if slot not in occupied]
        slots.extend(slot for slot in range(1,ENEMY_SLOTS+1) if slot not in occupied and slot not in slots)
        fid=max(actor['id'] for actor in runtime.fighters.values() if actor['camp']==3)
        game=self.games[self.leader]
        units=(unit for unit in summon['units'] for _ in range(unit.get('count',1)))
        for slot,unit in zip(slots,units):
            fid+=1
            hp=max(1,ref['hpmax']//3)
            level=ref['level']
            enemy=dict(id=fid,name=unit['name'],type=unit['shape'],guardian_type=0,kind='enemy',camp=3,
                       slot=slot,hp=hp,hpmax=hp,mp=0,mpmax=0,level=level,attack=max(1,ref['attack']//2),
                       initiative=max(1,ref['initiative']//2),defense=0,captured=False,removed=False,exit_on_defeat=False)
            runtime.fighters[fid]=enemy
            runtime.prepare(enemy)
            runtime.out.append(game.fighter(fid,enemy['type'],enemy['name'],3,2,slot,level,mid=2128))
            runtime.bars(enemy,initialize=True)

    def advance_sanjie(self,db,members):
        stage=members[0][1]+1
        scene,x,y,nid,name,shape=sanjie_stage_target(self.sanjie_tier,stage)
        now=int(time())
        qid=db.execute('INSERT INTO native_npc_quests(token,kind,branch,leader_id,created,expires,scene,x,y,npc,name,shape,dynamic) '
                       "VALUES(?,'sanjie',?,?,?,?,?,?,?,?,?,?,0)",
                       (token_hex(24),self.task['branch'],self.leader,now,now+TTL,scene,x,y,nid,name,shape)).lastrowid
        db.executemany("INSERT INTO native_npc_quest_members VALUES(?,?,?,'active')",
                       [(qid,cid,stage) for cid,_ in members])

    def settle(self,db,victory):
        if self.state['settled']:return
        from native946_double_time import TASKS, award_captain, experience_rates

        rare_available=True
        double_time = self.task['kind'] in TASKS
        now = int(time())
        next_members=[]
        for cid,g in self.games.items():
            existing=db.execute('SELECT * FROM native_npc_quest_rewards WHERE task_id=? AND character_id=?',(self.task['id'],cid)).fetchone()
            if existing:
                value=dict(experience=existing['experience'],gold=existing['gold'],guardian_experience=existing['pet_experience'],points=existing['points'],drops=json.loads(existing['drops']),victory=True)
            else:
                member=db.execute('SELECT ring,state FROM native_npc_quest_members WHERE task_id=? AND character_id=?',(self.task['id'],cid)).fetchone()
                valid=victory and member is not None and member['state']=='active' and self.task['expires']>time()
                if valid and self.task.get('school_objective')=='patrol':
                    stage = db.execute('SELECT first_battle FROM native_school_patrol_progress '
                                       'WHERE task_id=? AND character_id=?', (self.task['id'],cid)).fetchone()
                    if not stage or stage['first_battle']==self.id:
                        if not stage:
                            db.execute('INSERT INTO native_school_patrol_progress VALUES(?,?,?)',
                                       (self.task['id'],cid,self.id))
                        for pet in self.state['participants'][cid].values():
                            g.summons.settle(pet['id'],self.id,pet['hp'],pet['mp'],False,experience=0)
                        self.state.setdefault('rewards',{})[cid] = {'patrol_remaining': 1}
                        continue
                value=reward(self.task['kind'],g.progression.snapshot()['level'],member['ring'],Random(self.task['token']+':'+str(cid)),
                             self.task['branch'],rare_available=rare_available) if valid else dict(experience=0,gold=0,guardian_experience=0,points=0,drops={},victory=False)
                base_guardian = value['guardian_experience']
                neidan_rate = 1
                if valid:
                    if self.task['kind']=='school' and not self.state['participants'][cid]:
                        value['guardian_experience']=0
                    if double_time:
                        hero_rate, guardian_rate, neidan_rate = experience_rates(db, cid, now)
                        value['experience'] = value['experience'] * hero_rate // 100
                        value['guardian_experience'] *= guardian_rate
                    if value['rare']:rare_available=False
                    round_complete=not self.sanjie_tier or member['ring']==len(self.sanjie_tier['targets'])
                    settle_reward(db,g,self.task,member['ring'],self.id,value,complete_round=round_complete)
                    if not round_complete:next_members.append((cid,member['ring']))
                    if double_time and cid == self.leader and len(self.games) == 5:
                        award_captain(db, cid, now)
                    for ref,amount in value['drops'].items():db.execute('INSERT INTO native_npc_pending_items(task_id,character_id,reference_type,amount) VALUES(?,?,?,?)',(self.task['id'],cid,ref,amount))
                    deliver(g.progression,db)
                for pet in self.state['participants'][cid].values():
                    g.summons.settle(pet['id'], self.id, pet['hp'], pet['mp'], valid,
                                     experience=value['guardian_experience'], neidan_experience=base_guardian * neidan_rate,
                                     stunt_experience=base_guardian)
            self.state.setdefault('rewards',{})[cid]=value
        if next_members:self.advance_sanjie(db,next_members)
        self.state['settled']=True

    def finish(self,victory):
        if self.state['phase']=='finished':return
        self.close_battle(WILD_POLICY['encounter_cooldown_seconds'])
        for cid,g in self.games.items():
            value=self.state.get('rewards',{}).get(cid,{})
            if value.get('patrol_remaining'):
                auto = getattr(g, '_auto_school', None)
                if auto:
                    auto['next'] = monotonic() + 1.5
                self.service.refresh_battle_scene(g)
                self.service.enqueue(g, screen_notice('你已完成1场战斗，还差1场完成任务。'))
                g.last_event=dict(action='school_patrol_first_win',task_id=self.task['id'],remaining=1)
                continue
            text=(ACTIVITIES[self.task['kind']]['name']+'完成：经验'+str(value.get('experience',0))+'，现金'+str(value.get('gold',0))+'。') if value.get('victory') else '已离开任务战斗，本次未结算奖励。'
            if value.get('victory') and self.task['kind']=='treasure':
                text='击败强盗，获得普通藏宝图。背包满时可找陈小知领取暂存奖励。'
            if value.get('victory') and self.task['kind'] in LOTUS_RULES:
                rule=LOTUS_RULES[self.task['kind']]
                dialog=rule['task_ends'][self.task['ring']-1] if self.task['kind']=='lotus' else rule['task_end']
                text=dialog+f' 经验{value["experience"]}，现金{value["gold"]}。'
            if value.get('points'):text+='三界令积分+'+str(value['points'])+'。'
            self.service.refresh_battle_scene(g)
            if value.get('victory') and self.task['kind']=='school':
                guardians = [pet['name'] for pet in self.state['participants'][cid].values()]
                self.service.enqueue(g,*reward_packets('巡逻',value,guardians),
                                     *completion_packets(value),*School(g).after_task(value))
            elif value.get('victory') and self.sanjie_tier:
                g.progression._cached_snapshot=None
                g.dialog_actions=[dict(kind='npc_quest',op='close')]
                ending=self.sanjie_script.get('defeat')
                if not ending:
                    ending=self.sanjie_tier['task_end_final' if value.get('points') else 'task_end']
                reward_lines=[f'您获得#G{value["experience"]}#n经验',f'您获得#G{value["gold"]}#n两现金']
                if value.get('points'):reward_lines.append(f'您获得#G{value["points"]}#n枚三界令')
                self.service.enqueue(g,screen_notice('#r'.join(reward_lines)),g.menu(ending,['离开'],npc_id=self.task['npc']),
                                     *g.progression.task_packets())
            else:
                self.service.enqueue(g,screen_notice(text) if value.get('victory') else notice(text))
            g.last_event=dict(action='npc_quest_battle_finished',task_id=self.task['id'],victory=value.get('victory',False),reward=value)


def task_details(row):
    title=ACTIVITIES[row['kind']]['name']+' 第'+str(row['ring'])+'环'
    tier=SANJIE_TIERS.get(row['branch']) if row['kind']=='sanjie' else None
    if row['kind'] in LOTUS_RULES:
        rule=LOTUS_RULES[row['kind']]
        source=rule['task_texts'][row['ring']-1] if row['kind']=='lotus' else rule['task_text']
        scene=world().scene(row['scene'])['scene_name']
        title=f'【{rule["name"]}】第{row["ring"]}/{rule["rings"]}环'
        text=source.replace('{目标}',row['name']).replace('{场景}',scene)
        text+=f'（{scene} {row["x"]}，{row["y"]}）'
        description=('按任务提示找到'+row['name']+
                     ('，交谈或将其击败后回来领下一环。' if row['kind']=='lotus' else '，完成求助挑战。'))
    elif tier:
        title='【三界令】'+tier['branch']
        total=len(tier['targets'])
        text=f'前往{world().scene(row["scene"])["scene_name"]}挑战{row["name"]}（{row["x"]}，{row["y"]}）。第{row["ring"]}/{total}场。'
        description='清完本轮的全部目标后获得三界令。'
    elif row.get('supply_item'):
        from native946_school import supply_text
        title+=' · 寻物'
        text,description=supply_text(row)
    elif row.get('school_objective')=='visit':
        title+=' · 找人'
        text='前往'+world().scene(row['scene'])['scene_name']+'，向'+row['name']+'送交书信。'
        description='找到联系人后选择送交师父的书信。'
    elif row.get('school_objective')=='patrol':
        title+=' · 巡逻'
        done = int(bool(row.get('patrol_first_battle')))
        text=f'在{world().scene(row["scene"])["scene_name"]}（{row["x"]}，{row["y"]}）附近巡逻（{done}/2场）。'
        description='在标记附近走动，完成两场巡逻战斗。'
    else:
        text='前往'+world().scene(row['scene'])['scene_name']+'挑战'+row['name']+'（'+str(row['x'])+'，'+str(row['y'])+'）。'
        description='30分钟内击败强盗，胜利后获得普通藏宝图（6020）。' if row['kind']=='treasure' else '20分钟内完成任务战斗。'
    return dict(title=title,text=text,description=description,
                **{key:row[key] for key in ('scene','x','y','npc')})


def append_task_records(snapshot,trace,listing):
    for row in snapshot.get('npc_quests',[]):
        if row['expires']<=time():continue
        qid=930000+row['id'];details=task_details(row)
        deadline=datetime.fromtimestamp(row['expires'],timezone(timedelta(hours=8))).strftime('%H:%M')
        text=details['text']+'截止时间 '+deadline+'。'
        trace_title, trace_text = details['title'], text
        objective = row.get('school_objective')
        if objective == 'visit':
            trace_title = task_trace_title('师门', '寻人')
            scene = world().scene(row['scene'])['scene_name']
            npc = world().npc(row['scene'], row['npc'])
            x, y = (npc['x'] // 20, npc['y'] // 20) if npc else (row['x'], row['y'])
            trace_text = f'去拜访#R{scene}（{x}，{y}）#n的#u#R{row["name"]}#n。'
        elif objective == 'patrol':
            trace_title = task_trace_title('师门', '巡逻')
            trace_text = '在这附近来回走动，可遇到前来偷窃的宵小之辈。'
        trace.append(dict(id=qid,title=trace_title,txt=trace_text,scene=row['scene'],x=row['x'],y=row['y'],tracetime=0))
        listing['其他'][qid]=dict(n=details['title'],t=text,d=details['description'],tr=1,scene_id=row['scene'],x=row['x'],y=row['y'],npc_id=row['npc'],auto_path=0)
