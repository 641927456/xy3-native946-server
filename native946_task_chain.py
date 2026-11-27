"""Durable 300-ring flow using stock NPC dialogs, journal and battle packets.

Published reward cadence and native identities take precedence. Undisclosed
templates, prices and probabilities live in the versioned growth configuration.
"""
from datetime import datetime,timedelta,timezone
from pathlib import Path
from random import Random
from secrets import token_hex
from time import time,monotonic
import json
from native946_codec import packet, screen_notice, text_notice
from native946_wilderness import WildBattle

REFERENCE_WIRE_VERSION=2
DATA=json.loads(Path(__file__).with_name('native946_growth_rules.json').read_text('utf8'))
RULE=DATA['chain'];QID_BASE=9300000

def schema(db):
    statements=[
      '''CREATE TABLE IF NOT EXISTS native_chain_runs (
         id INTEGER PRIMARY KEY AUTOINCREMENT,character_id INTEGER NOT NULL REFERENCES characters(id),
         seed TEXT NOT NULL,week TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('active','frozen','completed','cancelled','expired')),
         ring INTEGER NOT NULL CHECK(ring BETWEEN 1 AND 300),level INTEGER NOT NULL,
         submitted_value INTEGER NOT NULL DEFAULT 0 CHECK(submitted_value>=0),
         task TEXT NOT NULL,expires INTEGER NOT NULL,remaining INTEGER NOT NULL DEFAULT 0,
         created INTEGER NOT NULL,changed INTEGER NOT NULL,rule_version TEXT NOT NULL)''',
      "CREATE UNIQUE INDEX IF NOT EXISTS native_chain_one_active ON native_chain_runs(character_id) WHERE state IN ('active','frozen')",
      'CREATE INDEX IF NOT EXISTS native_chain_week ON native_chain_runs(character_id,week)',
      '''CREATE TABLE IF NOT EXISTS native_chain_rewards (
         run_id INTEGER NOT NULL REFERENCES native_chain_runs(id),ring INTEGER NOT NULL,
         character_id INTEGER NOT NULL REFERENCES characters(id),receipt TEXT NOT NULL UNIQUE,
         reward TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(run_id,ring))''',
      '''CREATE TABLE IF NOT EXISTS native_chain_pending (
         run_id INTEGER NOT NULL REFERENCES native_chain_runs(id),character_id INTEGER NOT NULL REFERENCES characters(id),
         type INTEGER NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),PRIMARY KEY(run_id,type))''']
    for sql in statements:db.execute(sql)

def week_key(now=None):
    d=datetime.fromtimestamp(time() if now is None else now,timezone(timedelta(hours=8)))
    return (d-timedelta(days=d.weekday())).date().isoformat()

def unpack(row):
    if row is None:return None
    row=dict(row);row['task']=json.loads(row['task']);return row

def active(db,cid):
    return unpack(db.execute("SELECT * FROM native_chain_runs WHERE character_id=? AND state IN ('active','frozen')",(cid,)).fetchone())

def templates(seed,ring):
    from native946_world import world
    from native946_workshops import ACTORS,TASK_GIVERS
    from native946_wilderness import DATA as WILD
    rng=Random(seed+':ring:'+str(ring));w=world()
    candidates=[r for sid in (2001,2004,2009,2011) for r in w.npcs.get(sid,{}).values()
                if r['npc_type'] in (0,3) and (sid,r['npc_id']) not in ACTORS and (sid,r['npc_id']) not in TASK_GIVERS
                and r['name'] and r['npc_id']<4000 and sid in w.scenes]
    candidates.sort(key=lambda r:(r['scene_id'],r['npc_id']))
    if not candidates:raise ValueError('当前没有可到达的任务联系人。')
    npc=rng.choice(candidates)
    kind='boss' if ring%RULE['boss_every']==0 else RULE['cycle'][(ring-1)%len(RULE['cycle'])]
    task=dict(kind=kind,scene=npc['scene_id'],npc=npc['npc_id'],name=npc['name'],x=npc['x']//20,y=npc['y']//20)
    if kind=='item':
        task['item']=int(rng.choice(sorted(RULE['supplies'])));task['amount']=1
        task['value']=RULE['supplies'][str(task['item'])]
    if kind in ('battle','boss','capture'):
        pets=sorted((r for r in WILD['species'].values() if 0<r['take_grade_num']<=20),key=lambda r:r['type'])
        if not pets:raise ValueError('没有合适的任务守护资料。')
        pet=rng.choice(pets);task.update(enemy_type=pet['type'],pet_type=pet['type'],pet_name=pet['name'])
    return task

def payout(level,ring,submitted,seed):
    if not 1<=ring<=RULE['rings'] or level<1 or submitted<0:raise ValueError('任务链奖励参数无效。')
    value=dict(experience=level*level*RULE['experience_level_squared']*(10+(ring-1)%10)//10,
               tongbao=max(1,level//RULE['tongbao_level_divisor']),gold=0,drops={},quality=0)
    if ring==RULE['rings']:
        quality=max(i for i,n in enumerate(RULE['quality_value_thresholds']) if submitted>=n)
        pool=RULE['reward_pools'][quality];rng=Random(seed+':final');n=rng.randrange(sum(r[2] for r in pool))
        for kind,amount,weight in pool:
            if n<weight:value['drops'][str(kind)]=amount;break
            n-=weight
        value.update(gold=RULE['final_cash_base']+submitted*RULE['submitted_cash_return_percent']//100,quality=quality)
    return value

def task_text(row):
    from native946_progression import DATA as ITEMS
    from native946_world import world
    t=row['task'];where=world().scene(t['scene'])['scene_name']+'（'+str(t['x'])+'，'+str(t['y'])+'）'+t['name']
    if row['state']=='frozen':return '任务链已冻结，请找长安白居士继续。'
    if row['expires']<=time():return '本环已超时，请找长安白居士重新领取。'
    if t['kind']=='item':return '向'+where+'交付'+ITEMS['items'][str(t['item'])]['name']+'×'+str(t['amount'])+'。'
    if t['kind']=='capture':return '前往'+where+'挑战并捕捉'+t['pet_name']+'。捕捉成功后直接交付。'
    if t['kind'] in ('battle','boss'):return '前往'+where+'完成'+('首领挑战' if t['kind']=='boss' else '战斗考验')+'。'
    return '拜访'+where+'。'

def navigation_target(row):
    from native946_map import load_map
    from native946_world import world
    target = row['task']
    if row['state'] == 'frozen' or row['expires'] <= time():
        scene, npc_id = RULE['giver']
        npc = world().npc(scene, npc_id)
        target = dict(scene=scene, npc=npc_id, name=npc['name'], x=npc['x']//20, y=npc['y']//20)
    x, y = load_map(target['scene']).nearest_walkable(target['x'], target['y'])
    return dict(target, x=x, y=y)

def append_records(row,trace,listing):
    if not row:return
    tid=QID_BASE+row['id'];t=navigation_target(row);title='任务链 第'+str(row['ring'])+'/300环';text=task_text(row)
    trace.append(dict(id=tid,title=title,txt=text,scene=t['scene'],x=t['x'],y=t['y'],tracetime=0))
    listing['其他'][tid]=dict(n=title,t=text,d='每环获得经验与通宝；完成300环获得现金及额外物品。',tr=1,
                           scene_id=t['scene'],x=t['x'],y=t['y'],npc_id=t['npc'],auto_path=0)

class TaskChain:
    def __init__(self,game):self.g=game;self.p=game.progression
    def current(self):
        with self.p.transaction(write=False) as db:return active(db,self.p.cid)
    def _menu(self,nid,text,choices):
        self.g.dialog_actions=[dict(kind='task_chain',**a) for _,a in choices]
        return self.g.menu(text,[label for label,_ in choices],nid)
    def validate(self,nid,row=None):
        from native946_world import world
        c=self.g.character
        if self.g.battle:raise ValueError('请先结束当前战斗。')
        expected=RULE['giver'] if row is None else [row['task']['scene'],row['task']['npc']]
        if [c['map_id'],nid]!=expected:raise ValueError('请向当前任务联系人办理。')
        npc=world().npc(*expected)
        if npc is None or max(abs(c['x']-npc['x']//20),abs(c['y']-npc['y']//20))>12:raise ValueError('请先走近任务联系人。')
    def look(self,nid):
        row=self.current();c=self.g.character
        if [c['map_id'],nid]==RULE['giver']:
            self.validate(nid)
            choices=[('了解任务链',dict(op='rules')),('领取暂存奖励',dict(op='claim')),('了解成长途径',dict(op='growth'))]
            if row:
                expired = row['state']=='active' and row['expires']<=time()
                if expired:choices.insert(0,('领取新一轮任务链',dict(op='accept')))
                if row['state']=='frozen':choices.insert(0,('继续任务链',dict(op='resume',run=row['id'])))
                elif not expired:
                    choices.insert(0,('前往任务目标',dict(op='navigate',run=row['id'])))
                    choices.append(('冻结任务链',dict(op='freeze',run=row['id'],ring=row['ring'])))
                    if row['task']['kind']=='item':
                        price=row['task']['value']
                        supply=dict(op='supply',run=row['id'],ring=row['ring'])
                        choices.extend([(f'备齐一份物资（缺额每份{price}现金）',dict(supply,quantity=1)),
                                        (f'备齐两份物资（缺额每份{price}现金）',dict(supply,quantity=2))])
                choices.append(('放弃任务链',dict(op='cancel',run=row['id'])))
            else:choices.insert(0,('领取任务链',dict(op='accept')))
            return self._menu(nid,'白居士：坊间商旅有事相托，完成三百环可获重酬。'+('#r'+task_text(row) if row else ''),choices)
        if row and [c['map_id'],nid]==[row['task']['scene'],row['task']['npc']]:
            self.validate(nid,row);t=row['task'];base=dict(run=row['id'],ring=row['ring'])
            choices=[]
            if row['state']=='active' and row['expires']>time():
                if t['kind'] in ('battle','boss','capture'):choices.append(('开始'+('捕捉' if t['kind']=='capture' else '挑战'),dict(op='fight',**base)))
                if t['kind'] in ('visit','item'):choices.append(('交付本环任务',dict(op='complete',**base)))
                if t['kind']=='item':choices.append(('交付两份物资（提高累计交付价值）',dict(op='complete',donate=True,**base)))
            return self._menu(nid,task_text(row),choices)
        return None
    def accept(self):
        now=int(time());week=week_key(now)
        with self.p.transaction() as db:
            previous=active(db,self.p.cid)
            db.execute("UPDATE native_chain_runs SET state='expired',changed=? WHERE character_id=? AND state='active' AND expires<=?",(now,self.p.cid,now))
            if active(db,self.p.cid):raise ValueError('你已有进行中的任务链。')
            s=self.p._snapshot(db)
            if s['level']<RULE['min_level'] or s['ascension']<RULE['min_ascension']:raise ValueError('任务链需要人物达到飞升160级。')
            if self.g.party_service.team(self.p.cid):raise ValueError('任务链为单人任务，请先离开队伍。')
            last=db.execute("SELECT max(changed) FROM native_chain_runs WHERE character_id=? AND state='cancelled'",(self.p.cid,)).fetchone()[0]
            if last is not None and now-last<RULE['cancel_cooldown']:raise ValueError('放弃任务链后需等待3分钟再领取。')
            count=db.execute('SELECT count(*) FROM native_chain_runs WHERE character_id=? AND week=?',(self.p.cid,week)).fetchone()[0]
            if count>=RULE['weekly_chains']:raise ValueError('本周任务链领取次数已用完。')
            seed=token_hex(24);task=templates(seed,1)
            db.execute('INSERT INTO native_chain_runs(character_id,seed,week,state,ring,level,task,expires,created,changed,rule_version) VALUES(?,?,?,\'active\',1,?,?,?,?,?,?)',
                       (self.p.cid,seed,week,s['level'],json.dumps(task,ensure_ascii=False),now+RULE['ring_seconds'],now,now,DATA['version']))
        expired_id=QID_BASE+previous['id'] if previous else None
        return self.refresh('已领取任务链第1环。',journal=True,removed=expired_id)

    def refresh(self, text, *, inventory=False, wallet=False, journal=False, removed=None):
        essential = [packet(1183, ['string'], ['dlg_npc_chat']), text_notice(text)]
        if removed is not None:
            essential.append(packet(1966, ['number'], [removed]))

        def render():
            if not (inventory or wallet or journal):
                return tuple(essential)
            snapshot = self.p.snapshot()
            return (*essential, *(self.p.item_packets(snapshot) if inventory else ()),
                    *(self.p.attribute_packets(snapshot) if wallet else ()),
                    *(self.p.task_packets(snapshot) if journal else ()))

        return self.g.defer_packets(render, fallback=tuple(essential))

    def usable_stacks(self, db, kind):
        from native946_mall_bags import pages
        bag_pages = pages(db, self.p.cid)
        return db.execute(
            "SELECT i.id,i.amount FROM inventory i WHERE i.character_id=? AND i.type=? "
            "AND i.page IN (" + ','.join('?' for _ in bag_pages) + ") AND NOT EXISTS "
            "(SELECT 1 FROM native_object_locks l WHERE l.kind='item' AND l.object_id=i.id) "
            "ORDER BY i.page,i.id", (self.p.cid, kind, *bag_pages)).fetchall()

    def put_in_bags(self, db, kind, remaining):
        from native946_mall_bags import pages
        maximum = self.p.stack_limit(dict(id=0, type=kind), {'equipment': {}})
        for stack in self.usable_stacks(db, kind):
            amount = min(max(0, maximum - stack['amount']), remaining)
            if amount:
                db.execute('UPDATE inventory SET amount=amount+? WHERE id=?', (amount, stack['id']))
                remaining -= amount
            if not remaining:
                return 0
        occupied = {(r['page'], r['slot']) for r in db.execute(
            'SELECT page,slot FROM inventory WHERE character_id=?', (self.p.cid,))}
        for page in pages(db, self.p.cid):
            for slot in range(1, 25):
                if (page, slot) in occupied:
                    continue
                amount = min(remaining, maximum)
                db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',
                           (self.p.cid, kind, page, slot, amount))
                remaining -= amount
                if not remaining:
                    return 0
        return remaining

    def deliver(self, db):
        delivered = pending = 0
        rows = db.execute('SELECT * FROM native_chain_pending WHERE character_id=? ORDER BY run_id,type',
                          (self.p.cid,)).fetchall()
        for row in rows:
            remaining = self.put_in_bags(db, row['type'], row['amount'])
            delivered += row['amount'] - remaining
            pending += remaining
            if remaining:
                db.execute('UPDATE native_chain_pending SET amount=? WHERE run_id=? AND type=?',
                           (remaining, row['run_id'], row['type']))
            else:
                db.execute('DELETE FROM native_chain_pending WHERE run_id=? AND type=?',
                           (row['run_id'], row['type']))
        return delivered, pending

    def _require_active(self,row,now=None):
        if row is None or row['state']!='active':raise ValueError('请先领取或继续任务链。')
        if row['expires']<=(time() if now is None else now):raise ValueError('本环任务已超时，请向白居士重新领取。')
    def complete(self,run,ring,*,battle_id=None,captured=False,donate=False,now=None):
        with self.p.transaction() as db:
            now=int(time()) if now is None else now
            old=db.execute('SELECT reward FROM native_chain_rewards WHERE run_id=? AND ring=? AND character_id=?',(run,ring,self.p.cid)).fetchone()
            if old:return json.loads(old[0])
            row=active(db,self.p.cid);self._require_active(row,now)
            if row['id']!=run or row['ring']!=ring:raise ValueError('任务进度已改变，请重新与联系人交谈。')
            t=row['task'];submitted=row['submitted_value']
            if t['kind'] in ('battle','boss','capture'):
                if not battle_id or (t['kind']=='capture' and not captured):raise ValueError('尚未完成本环战斗或捕捉。')
            elif t['kind']=='item':
                required=t['amount']*(2 if donate else 1)
                available=self.usable_stacks(db,t['item'])
                if sum(r['amount'] for r in available)<required:raise ValueError('请备齐所需物品并解除物品加锁。')
                amount=required
                for item in available:
                    use=min(amount,item['amount']);amount-=use
                    if use==item['amount']:db.execute('DELETE FROM inventory WHERE id=?',(item['id'],))
                    else:db.execute('UPDATE inventory SET amount=amount-? WHERE id=?',(use,item['id']))
                    if not amount:break
                submitted+=t['value']*(2 if donate else 1)
            value=payout(row['level'],ring,submitted,row['seed']);receipt='chain:'+str(run)+':'+str(ring)
            self.g.characters.settle_battle(self.g.character['owner'],self.p.cid,receipt,True,
                                          experience=value['experience'],gold=value['gold'],advance_quest=False)
            self.g.auto_level()
            db.execute('UPDATE cultivation_state SET tongbao=tongbao+? WHERE character_id=?',(value['tongbao'],self.p.cid))
            db.execute('INSERT INTO native_chain_rewards VALUES(?,?,?,?,?,?)',(run,ring,self.p.cid,receipt,json.dumps(value),now))
            for kind,amount in value['drops'].items():db.execute('INSERT INTO native_chain_pending VALUES(?,?,?,?)',(run,self.p.cid,int(kind),amount))
            if ring==RULE['rings']:db.execute("UPDATE native_chain_runs SET state='completed',submitted_value=?,changed=? WHERE id=?",(submitted,now,run))
            else:db.execute('UPDATE native_chain_runs SET ring=ring+1,submitted_value=?,task=?,expires=?,changed=? WHERE id=?',
                            (submitted,json.dumps(templates(row['seed'],ring+1),ensure_ascii=False),now+RULE['ring_seconds'],now,run))
            if value['drops']:self.deliver(db)
        return value
    def cancel(self,run):
        if self.g.battle:raise ValueError('战斗中不能放弃任务链。')
        with self.p.transaction() as db:
            changed=db.execute("UPDATE native_chain_runs SET state='cancelled',changed=? WHERE id=? AND character_id=? AND state IN ('active','frozen')",(int(time()),run,self.p.cid)).rowcount
            if not changed:raise ValueError('没有可以放弃的任务链。')
    def execute(self,nid,action):
        op=action['op'];row=self.current()
        self.validate(nid,row if op in ('complete','fight') else None)
        if op=='growth':
            from native946_growth_guide import menu
            return menu(self,nid)
        if op=='growth_page':
            from native946_growth_guide import detail
            return detail(nid,action['page'])
        if op=='rules':return self.g.menu('任务链：飞升160级可单人领取，每周6次，每环限时40分钟。#r途中可在白居士处冻结与继续；放弃后等待3分钟再领。'
                        '#r每环获得经验和通宝。第300环额外获得现金及道具，累计交付价值越高，可获得的奖励档次越高。#r寻物药引可自行准备，也可在白居士处采办。',npc_id=nid)
        if op=='accept':return self.accept()
        if op=='claim':
            with self.p.transaction() as db:
                delivered,pending=self.deliver(db)
            text=f'已领取{delivered}件奖励。' if delivered else '暂无可领取的奖励。'
            if pending:text+=f'还有{pending}件奖励暂存，请整理背包后领取。'
            return self.refresh(text,inventory=bool(delivered))
        if row is None or row['id']!=action.get('run'):raise ValueError('任务进度已改变。')
        if op=='cancel':
            self.cancel(row['id'])
            return self.refresh('任务链已放弃，3分钟后可重新领取。',journal=True,removed=QID_BASE+row['id'])
        if op=='resume':
            with self.p.transaction() as db:
                now=int(time())
                updated=db.execute("""UPDATE native_chain_runs SET state='active',expires=?+max(0,remaining),remaining=0,changed=?
                    WHERE id=? AND character_id=? AND state='frozen' RETURNING expires""",
                    (now,now,row['id'],self.p.cid)).fetchone()
                if not updated:raise ValueError('任务链没有可继续的冻结进度。')
            text='任务链已继续。' if updated['expires']>now else '本环的剩余时间已用完，可找白居士重新领取。'
            return self.refresh(text,journal=True)
        self._require_active(row)
        if op=='freeze':
            with self.p.transaction() as db:
                now=int(time())
                changed=db.execute("""UPDATE native_chain_runs SET state='frozen',remaining=expires-?,changed=?
                    WHERE id=? AND character_id=? AND state='active' AND ring=? AND expires>?""",
                    (now,now,row['id'],self.p.cid,action.get('ring'),now)).rowcount
                if not changed:raise ValueError('任务进度或剩余时间已变化，请重新交谈。')
            return self.refresh('任务链已冻结，剩余时间已保存。',journal=True)
        if op=='navigate':
            from native946_navigation import npc_auto_trace
            t=navigation_target(row)
            return (packet(1183,['string'],['dlg_npc_chat']),npc_auto_trace(t['scene'],t['x'],t['y'],t['npc'],t['name']))
        if row['ring']!=action.get('ring'):raise ValueError('任务环数已改变，请重新交谈。')
        if op=='supply':
            quantity=action.get('quantity')
            if quantity not in (1,2):raise ValueError('采办数量无效。')
            with self.p.transaction() as db:
                current=active(db,self.p.cid)
                self._require_active(current)
                if (current['id'],current['ring'])!=(row['id'],row['ring']) or current['task']['kind']!='item':
                    raise ValueError('任务进度已改变，请重新交谈。')
                t=current['task']
                have=sum(stack['amount'] for stack in self.usable_stacks(db,t['item']))
                missing=max(0,t['amount']*quantity-have)
                if not missing:return self.refresh('本环物资已备齐。')
                cost=t['value']*missing//t['amount']
                money=db.execute('SELECT gold FROM characters WHERE id=?',(self.p.cid,)).fetchone()[0]
                if money<cost:raise ValueError('采办现金不足。')
                if self.put_in_bags(db,t['item'],missing):raise ValueError('请先整理随身背包。')
                db.execute('UPDATE characters SET gold=gold-? WHERE id=?',(cost,self.p.cid))
            return self.refresh(f'补齐{missing}份物资，消耗{cost}现金。',inventory=True,wallet=True)
        if op=='complete':
            if row['task']['kind']=='capture':raise ValueError('请向任务联系人挑战并成功捕捉所需守护。')
            value=self.complete(row['id'],row['ring'],donate=bool(action.get('donate')))
            text=f"第{row['ring']}环完成，获得{value['experience']}经验和{value['tongbao']}通宝。"
            return self.refresh(text,inventory=row['task']['kind']=='item',wallet=True,journal=True)
        if op=='fight':return self.start(row)
        raise ValueError('任务选项无效。')
    def start(self,row):
        if self.g.party_service.team(self.p.cid):raise ValueError('任务链挑战请以单人身份进行。')
        def enter():
            from copy import deepcopy
            names=('battle','dialog_open','dialog_actions','dialog_npc')
            saved={name:deepcopy(getattr(self.g,name)) for name in names}
            previous=self.g.party_service.battles.get(self.p.cid);old=getattr(self.g,'_wild_battle',None)
            try:
                battle=ChainBattle(self.g,row)
                replies=battle.entry(self.g)
            except BaseException:
                if previous is None:self.g.party_service.battles.pop(self.p.cid,None)
                else:self.g.party_service.battles[self.p.cid]=previous
                self.g._wild_battle=old
                for key,value in saved.items():setattr(self.g,key,value)
                raise
            self.g.party_service.enqueue(self.g,*replies)
            return ()
        return self.g.defer_packets(enter)

class ChainBattle(WildBattle):
    def __init__(self,game,row):
        self.chain=dict(row);t=row['task'];level=row['level']
        zone=dict(scene_name='任务链考验',battle_title=f"任务链 第{row['ring']}环",entry_notice='任务链挑战开始。',
                  levels=[level,level],enemies=[dict(type=t['enemy_type'],name=t['pet_name'] if t['kind']=='capture' else '拦路恶徒',guardian_type=t['pet_type'] if t['kind']=='capture' else 0)])
        super().__init__(game,[game],zone)
        # One capture target, rather than accidentally requiring a second pet.
        if t['kind']=='capture':
            enemies=self.enemies()
            for enemy in enemies[1:]:self.state['fighters'].pop(enemy['id'])
        if t['kind']=='boss':
            for e in self.enemies():e.update(hp=e['hp']*2,hpmax=e['hpmax']*2,attack=e['attack']*6//5,
                                            control_spell=('faint','disorder','seal')[(row['ring']//RULE['boss_every']-1)%3],
                                            control_policy=RULE['boss_control'])
        self.project()

    def keeps_captured_guardian(self):
        return False

    def settle(self,db,victory):
        if self.state['settled']:return
        now=int(time())
        g=self.games[self.leader];captured=any(f.get('captured') for f in self.state['fighters'].values())
        current=active(db,self.leader)
        valid=(victory and current is not None and current['id']==self.chain['id'] and current['ring']==self.chain['ring']
               and current['state']=='active' and current['expires']>now and (self.chain['task']['kind']!='capture' or captured))
        value=TaskChain(g).complete(self.chain['id'],self.chain['ring'],battle_id=self.id,captured=captured,now=now) if valid else dict(experience=0,tongbao=0,gold=0,drops={})
        for pet in self.state['participants'][self.leader].values():g.summons.settle(pet['id'],self.id,pet['hp'],pet['mp'],valid,experience=value['experience']//2)
        self.state['rewards']={self.leader:dict(value,victory=valid)}
        self.state['chain_reward']=value;self.state['chain_completed']=valid;self.state['settled']=True

    def finish_messages(self,cid,victory):
        if not self.state.get('chain_completed'):
            return (text_notice('本次未结算任务奖励。请查看任务日志中的进度和时限。'),)
        value=self.state['chain_reward']
        ring=self.chain['ring']
        text=f"第{ring}环完成，获得{value['experience']}经验和{value['tongbao']}通宝。"
        if ring<RULE['rings']:
            return (screen_notice(text+'下一环已记入任务日志。'),)
        text+=f"整轮完成，额外获得{value['gold']}现金及道具。未放入背包的奖励可向白居士领取。"
        return (packet(1966,['number'],[QID_BASE+self.chain['id']]),screen_notice(text))

def receive(game,mid,r):
    if mid!=1980:return False,None
    from native946_login import MessageReader
    probe=MessageReader(r.data[r.offset:]);qid=probe.integer();probe.finish()
    if qid<QID_BASE:return False,None
    r.integer();r.finish();TaskChain(game).cancel(qid-QID_BASE)
    return True,game.defer_packets(lambda:(packet(1966,['number'],[qid]),*game.progression.task_packets()))
