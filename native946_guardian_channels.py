"""User-authorized local acquisition supplements at the real Yuan Tiangang NPC."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from time import monotonic,time
from secrets import token_hex
import json
from native946_codec import packet
from native946_codec import text_notice

REFERENCE_WIRE_VERSION=4
DATA=json.loads(Path(__file__).with_name('native946_guardian_channels_data.json').read_text('utf8'))
NPC=DATA['npc'];PAGE_SIZE=5
SHOP={r['type']:r for r in DATA['shop']}
EVENT=DATA['event_exchange']
EVENT_ROWS={(r['score_type'],r['item_type']):r for r in EVENT['rows']}


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS native_guardian_points ('
        'character_id INTEGER NOT NULL REFERENCES characters(id),score_type INTEGER NOT NULL CHECK(score_type BETWEEN 1 AND 3),'
        'amount INTEGER NOT NULL CHECK(amount BETWEEN 0 AND 2147483647),PRIMARY KEY(character_id,score_type))')
    db.execute('CREATE TABLE IF NOT EXISTS native_guardian_source_orders ('
        'token TEXT PRIMARY KEY,character_id INTEGER NOT NULL REFERENCES characters(id),operation TEXT NOT NULL,'
        'target INTEGER NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>0),cost INTEGER NOT NULL CHECK(cost>=0),'
        'result TEXT NOT NULL,created INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS native_guardian_event_limits ('
        'period TEXT NOT NULL,character_id INTEGER NOT NULL CHECK(character_id>=0),score_type INTEGER NOT NULL,'
        'item_type INTEGER NOT NULL,used INTEGER NOT NULL CHECK(used>=0),'
        'PRIMARY KEY(period,character_id,score_type,item_type))')
    if not exists(db, 'native_guardian_account_points'):
        migrate_account_points(db)


def migrate_account_points(db):
    # Local supplementary mall: each point type has its own account balance.
    db.execute("""CREATE TABLE IF NOT EXISTS native_guardian_account_points (
        owner TEXT NOT NULL REFERENCES accounts(username),
        score_type INTEGER NOT NULL CHECK(score_type BETWEEN 1 AND 3),
        amount INTEGER NOT NULL CHECK(amount>=0),
        PRIMARY KEY(owner,score_type)
    )""")
    db.execute("""INSERT INTO native_guardian_account_points(owner,score_type,amount)
        SELECT c.owner,p.score_type,sum(p.amount)
        FROM native_guardian_points p JOIN characters c ON c.id=p.character_id
        WHERE p.amount<>0
        GROUP BY c.owner,p.score_type
        ON CONFLICT(owner,score_type) DO UPDATE SET amount=amount+excluded.amount""")
    db.execute('UPDATE native_guardian_points SET amount=0 WHERE amount<>0 '
               'AND character_id IN (SELECT id FROM characters)')


def actor(npc):return npc and (npc['scene_id'],npc['npc_id'])==(NPC['scene'],NPC['id'])
def exists(db,name):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())
def period():
    now=datetime.now(timezone(timedelta(hours=8)))
    start=(now-timedelta(days=now.weekday())).replace(hour=12,minute=30,second=0,microsecond=0)
    if now<start:start-=timedelta(days=7)
    return start.isoformat()
def notice(text):return text_notice(text)


class GuardianChannels:
    def __init__(self,game):self.g=game;self.p=game.progression;self.s=game.summons

    def validate(self,nid):
        from native946_world import world
        hero=self.g.character;npc=world().npc(hero['map_id'],nid)
        if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
        if not actor(npc) or max(abs(npc['x']//20-hero['x']),abs(npc['y']//20-hero['y']))>12:
            raise ValueError('请到皇城袁天罡处办理守护兑换。')

    def _menu(self,text,choices):
        self.g.dialog_actions=[dict(kind='guardian_source',**value) for _,value in choices]
        return self.g.menu(text,[label for label,_ in choices],NPC['id'])

    def root(self):
        return self._menu('守护获取#r原生守护天缘：单抽、十连抽、卡牌仓库及天缘积分商店。'
            '#r当前开放的'+str(len(DATA['guardians']))+'种守护另保留本服补充兑换，仍须满足携带等级与栏位要求。',[
                ('打开原生守护天缘抽奖',dict(op='native_lottery')),
                ('其他守护补充兑换（本服）',dict(op='categories',page=0)),
                ('守护蛋、碎片与卡券（本服）',dict(op='shop',page=0)),
                ('卡牌活动积分与兑换（本服）',dict(op='points_root')),
                ('查看本服获取规则',dict(op='rules'))])

    def _paging(self,choices,page,total,op,**values):
        for target,label in [(page-1,'上一页'),(page+1,'下一页')]:
            if 0<=target<total:choices.append((label,dict(op=op,page=target,**values)))

    def categories(self,page):
        kinds=sorted({r['kind'] for r in DATA['guardians'].values()});total=(len(kinds)+PAGE_SIZE-1)//PAGE_SIZE
        if type(page) is not int or not 0<=page<total:raise ValueError('守护分类页无效。')
        choices=[(DATA['categories'][str(k)],dict(op='guardians',category=k,page=0)) for k in kinds[page*PAGE_SIZE:(page+1)*PAGE_SIZE]]
        self._paging(choices,page,total,'categories');choices.append(('返回守护获取',dict(op='root')))
        return self._menu('本服守护兑换：请选择类别。',choices)

    def guardians(self,category,page):
        rows=sorted((r for r in DATA['guardians'].values() if r['kind']==category),key=lambda r:r['type'])
        total=(len(rows)+PAGE_SIZE-1)//PAGE_SIZE
        if type(page) is not int or not 0<=page<total:raise ValueError('守护目录页无效。')
        choices=[(r['name'],dict(op='detail',target=r['type'],page=page)) for r in rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]]
        self._paging(choices,page,total,'guardians',category=category);choices.append(('返回分类',dict(op='categories',page=0)))
        return self._menu(DATA['categories'][str(category)]+'：第'+str(page+1)+'页。#r兑换价格均为本服价格。',choices)

    def detail(self,target,page=0):
        from native946_guardian_ui import carry_label
        row=DATA['guardians'].get(str(target))
        if not row:raise ValueError('当前版本没有开放此守护。')
        descriptions=[]
        for route in row['routes']:
            if route['op']=='capture':text='可在'+route['name']+'捕捉'
            else:text=route['name']+('×'+str(route['count']) if route.get('count',1)>1 else '')
            if text not in descriptions:descriptions.append(text)
        text=row['name']+'#r携带要求：'+carry_label(row['take_grade_num'])+'#r已接入渠道：'+('、'.join(descriptions[:4]) or '本服补充兑换')
        text+='#r本服指定兑换价：'+str(row['local_exchange']['gold'])+'现金。#r兑换获得1级守护，培养数值按当前原生种类生成。'
        return self._menu(text,[('兑换此守护（本服）',dict(op='quote',purchase='guardian',target=target,quantity=1)),
            ('返回列表',dict(op='guardians',category=row['kind'],page=page))])

    def shop(self,page):
        rows=DATA['shop'];total=(len(rows)+PAGE_SIZE-1)//PAGE_SIZE
        if type(page) is not int or not 0<=page<total:raise ValueError('道具兑换页无效。')
        choices=[(r['name'],dict(op='item_detail',target=r['type'],page=page)) for r in rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]]
        self._paging(choices,page,total,'shop');choices.append(('返回守护获取',dict(op='root')))
        return self._menu('本服道具兑换：第'+str(page+1)+'页。选择道具查看现金价格及获取规则。',choices)

    def item_detail(self,target,page):
        from native946_guardian_acquisition import DATA as ACQUISITION
        row=SHOP.get(target)
        if row is None:raise ValueError('没有这项兑换道具。')
        text=row['name']+'#r本服价格：每个'+str(row['gold'])+'现金。'
        rule=ACQUISITION.get('random_rules',{}).get(str(target))
        if rule:text+='#r'+rule['local_rule']+'#r使用时消耗'+str(rule['count'])+'个，当前候选共'+str(len(rule['guardians']))+'种。'
        else:text+='#r'+ACQUISITION['items'][str(target)]['desc']
        choices=[('兑换1个',dict(op='quote',purchase='item',target=target,quantity=1))]
        if target in (46882,47575):
            amount=8 if target==46882 else 10
            choices.append(('兑换'+str(amount)+'个（一份合成材料）',dict(op='quote',purchase='item',target=target,quantity=amount)))
        choices.append(('返回道具列表',dict(op='shop',page=page)))
        return self._menu(text,choices)

    def balance(self,db,score_type):
        if not exists(db,'native_guardian_account_points'):return 0
        row=db.execute('SELECT amount FROM native_guardian_account_points WHERE owner=? AND score_type=?',
                       (self.p.owner,score_type)).fetchone()
        return row[0] if row else 0

    def points_root(self):
        with self.p.transaction(write=False) as db:balances={n:self.balance(db,n) for n in EVENT['points_types']}
        choices=[('第'+str(n)+'类积分：'+str(balances[n]),dict(op='points',target=n)) for n in EVENT['points_types']]
        choices.append(('返回守护获取',dict(op='root')))
        return self._menu('本服卡牌积分兑换#r三类积分各自按账号共享，每1积分消耗'+str(EVENT['points_gold_rate'])+'现金。'
            '#r固定原始表价，先到先得，无抽签；周一12:30刷新限购。',choices)

    def points(self,target):
        if target not in EVENT['points_types']:raise ValueError('活动积分类型无效。')
        with self.p.transaction(write=False) as db:balance=self.balance(db,target)
        choices=[('兑换'+str(n)+'积分（'+str(n*EVENT['points_gold_rate'])+'现金）',
            dict(op='quote',purchase='points',target=target,quantity=n)) for n in (10,100,1000)]
        choices.extend([('打开活动物品兑换',dict(op='event',target=target)),('返回积分列表',dict(op='points_root'))])
        return self._menu('第'+str(target)+'类本服活动积分：'+str(balance)+'。#r每积分'+str(EVENT['points_gold_rate'])+'现金。',choices)

    def price(self,operation,target,quantity):
        if type(quantity) is not int or quantity<1:raise ValueError('兑换数量无效。')
        if operation=='guardian' and quantity==1 and str(target) in DATA['guardians']:
            return DATA['guardians'][str(target)]['local_exchange']['gold']
        if operation=='item' and target in SHOP and quantity in (1,8,10):
            if quantity!=1 and (target,quantity) not in ((46882,8),(47575,10)):raise ValueError('此道具的兑换数量无效。')
            return SHOP[target]['gold']*quantity
        if operation=='points' and target in EVENT['points_types'] and quantity in (10,100,1000):return EVENT['points_gold_rate']*quantity
        raise ValueError('守护兑换项目无效。')

    def quote(self,operation,target,quantity):
        cost=self.price(operation,target,quantity);token=token_hex(20)
        self.g.guardian_source_session=dict(token=token,operation=operation,target=target,quantity=quantity,cost=cost,until=monotonic()+180)
        name=DATA['guardians'][str(target)]['name'] if operation=='guardian' else SHOP[target]['name'] if operation=='item' else '第'+str(target)+'类活动积分'
        return self._menu('本服兑换确认#r'+name+'×'+str(quantity)+'#r消耗：'+str(cost)+'现金。',[
            ('确认兑换',dict(op='confirm',token=token)),('取消，返回获取目录',dict(op='root'))])

    def _record(self,db,token,operation,target,quantity,cost,result):
        db.execute('INSERT INTO native_guardian_source_orders VALUES(?,?,?,?,?,?,?,?)',
            (token,self.p.cid,operation,target,quantity,cost,json.dumps(result,ensure_ascii=False),int(time())))

    def _previous(self,db,token,operation,target):
        row=db.execute('SELECT * FROM native_guardian_source_orders WHERE token=?',(token,)).fetchone()
        if row:
            if (row['character_id'],row['operation'],row['target'])!=(self.p.cid,operation,target):raise ValueError('兑换凭据归属不符。')
            return json.loads(row['result'])

    def _finish(self,text):
        self.g.dialog_open=False;self.g.dialog_actions=[];self.g.dialog_npc=None
        return (*self.p.attribute_packets(),*self.p.item_packets(),*self.s.sync(),*self.g.sync_summon_scene(),
            packet(1183,['string'],['dlg_npc_chat']),notice(text))

    def confirm(self,token):
        session=getattr(self.g,'guardian_source_session',None)
        if not session or session['token']!=token or session['until']<monotonic():raise ValueError('兑换确认已失效，请重新选择。')
        operation,target,quantity=session['operation'],session['target'],session['quantity']
        cost=self.price(operation,target,quantity)
        if cost!=session['cost']:raise ValueError('本服价格已更新，请重新确认。')
        with self.p.transaction() as db:
            schema(db);old=self._previous(db,token,operation,target)
            if old is not None:return self.g.defer_packets(lambda:self._finish('此兑换已完成，没有重复扣除现金。'))
            if db.execute('UPDATE characters SET gold=gold-? WHERE id=? AND gold>=?',(cost,self.p.cid,cost)).rowcount!=1:
                raise ValueError('随身现金不足，本次未扣款。')
            if operation=='guardian':
                sid=self.s.acquire(target,'guardian-exchange:'+token);result=dict(summon_id=sid,guardian_type=target)
            elif operation=='item':
                from native946_guardian_acquisition import GuardianAcquisition
                acquisition=GuardianAcquisition(self.g);ids=[acquisition._give_item(db,target) for _ in range(quantity)]
                result=dict(item_ids=ids,item_type=target,amount=quantity)
            else:
                if self.balance(db,target)+quantity>2147483647:raise ValueError('活动积分已达上限，本次未扣款。')
                db.execute('INSERT INTO native_guardian_account_points(owner,score_type,amount) VALUES(?,?,?) '
                           'ON CONFLICT(owner,score_type) DO UPDATE SET amount=amount+excluded.amount',
                           (self.p.owner,target,quantity))
                result=dict(score_type=target,amount=quantity)
            self._record(db,token,operation,target,quantity,cost,result)
        return self.g.defer_packets(lambda:self._finish('本服兑换成功，已扣除'+str(cost)+'现金。'))

    def used(self,db,bucket,cid,score_type,item_type):
        if not exists(db,'native_guardian_event_limits'):return 0
        row=db.execute('SELECT used FROM native_guardian_event_limits WHERE period=? AND character_id=? AND score_type=? AND item_type=?',
            (bucket,cid,score_type,item_type)).fetchone();return row[0] if row else 0

    def event_panel(self,score_type,*,renew=True):
        if score_type not in EVENT['points_types']:raise ValueError('活动积分类型无效。')
        if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
        bucket=period();rows=[r for r in EVENT['rows'] if r['score_type']==score_type]
        if renew:self.g.guardian_event_session=dict(score_type=score_type,period=bucket,until=monotonic()+180,
            tokens={r['item_type']:token_hex(20) for r in rows},prices={r['item_type']:r['item_score'] for r in rows})
        data=[]
        with self.p.transaction(write=False) as db:
            balance=self.balance(db,score_type)
            for row in rows:
                personal=max(0,row['weekly_num']-self.used(db,bucket,self.p.cid,score_type,row['item_type']))
                server=max(0,row['weekly_num']-self.used(db,bucket,0,score_type,row['item_type'])) if row['all_servers'] else personal
                data.append(dict(item_type=row['item_type'],server_left_num=server,person_left_num=personal,
                    in_draw_duration=False,lucky_draw_total_num=0,need_score=row['item_score'],need_lucky_draw=False,
                    is_in_lucky_draw_queue=False,total_limit_num=row['weekly_num']))
        # Only trusted integer fields are serialized. Native UI consumes an
        # ipairs list, not an item-ID keyed dictionary or JSON string.
        def literal(value):return ('true' if value else 'false') if type(value) is bool else str(value)
        payload='{'+','.join('{'+','.join(k+'='+literal(v) for k,v in row.items())+'}' for row in data)+'}'
        return (notice('本服活动兑换：固定表价、先到先得，无抽签；每1积分消耗'+str(EVENT['points_gold_rate'])+'现金。'),
            packet(3157,['number','number','string','number'],[score_type,balance,payload,1 if renew else 2]))

    def event_exchange(self,score_type,item_type):
        if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
        row=EVENT_ROWS.get((score_type,item_type));session=getattr(self.g,'guardian_event_session',None)
        if not row or not session or session['score_type']!=score_type or session['until']<monotonic() or session['period']!=period():
            raise ValueError('活动兑换报价已失效，请重新打开兑换界面。')
        token=session['tokens'].get(item_type)
        if not token:raise ValueError('活动兑换项目无效。')
        if session.get('prices',{}).get(item_type)!=row['item_score']:raise ValueError('活动兑换价格已更新，请重新打开界面。')
        operation='event:'+str(score_type);bucket=session['period']
        with self.p.transaction() as db:
            schema(db)
            if self._previous(db,token,operation,item_type) is not None:
                return self.g.defer_packets(lambda:(notice('此笔兑换已完成，没有重复扣除积分。'),*self.event_panel(score_type,renew=False)))
            scopes=[self.p.cid]+([0] if row['all_servers'] else [])
            if any(self.used(db,bucket,cid,score_type,item_type)>=row['weekly_num'] for cid in scopes):
                raise ValueError('本周兑换限额已用完；积分未扣除。')
            if db.execute('UPDATE native_guardian_account_points SET amount=amount-? WHERE owner=? AND score_type=? AND amount>=?',
                (row['item_score'],self.p.owner,score_type,row['item_score'])).rowcount!=1:raise ValueError('本服活动积分不足；积分未扣除。')
            from native946_guardian_acquisition import GuardianAcquisition
            iid=GuardianAcquisition(self.g)._give_item(db,item_type)
            for cid in scopes:db.execute('INSERT INTO native_guardian_event_limits VALUES(?,?,?,?,1) ON CONFLICT(period,character_id,score_type,item_type) DO UPDATE SET used=used+1',
                (bucket,cid,score_type,item_type))
            self._record(db,token,operation,item_type,1,row['item_score'],dict(item_id=iid,item_type=item_type,amount=1))
        return self.g.defer_packets(lambda:(*self.p.item_packets(),notice('本服活动兑换成功。'),*self.event_panel(score_type,renew=False)))

    def execute(self,nid,action):
        self.validate(nid);op=action['op']
        if op=='native_lottery':
            from native946_guardian_lottery import GuardianLottery
            self.g.dialog_open=False;self.g.dialog_actions=[];self.g.dialog_npc=None
            return (packet(1183,['string'],['dlg_npc_chat']),*GuardianLottery(self.g).panel(announce=True))
        if op=='root':self.g.guardian_source_session=None;return self.root()
        if op=='categories':return self.categories(action['page'])
        if op=='guardians':return self.guardians(action['category'],action['page'])
        if op=='detail':return self.detail(action['target'],action['page'])
        if op=='shop':return self.shop(action['page'])
        if op=='item_detail':return self.item_detail(action['target'],action['page'])
        if op=='points_root':return self.points_root()
        if op=='points':return self.points(action['target'])
        if op=='event':return self.event_panel(action['target'])
        if op=='quote':return self.quote(action['purchase'],action['target'],action['quantity'])
        if op=='confirm':return self.confirm(action['token'])
        if op=='rules':return self._menu('本服补充获取规则#r所有现金价格、等概率随机、历史卡池替代及活动常驻兑换均为本服规则。'
            '#r专属蛋、自选名单与元神数量保留原生资料。随机守护只从已开放且满足携带等级的候选中抽取。'
            '#r原版未开放的守护不发放。所有兑换仍检查携带等级、栏位、背包、现金及积分。'
            '#r活动使用固定原表价，周一12:30刷新周限购，全服限量项目由本服全部角色共享，取消抽签。',
            [('返回守护获取',dict(op='root'))])
        raise ValueError('守护获取选项无效。')
