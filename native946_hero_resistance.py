"""Durable native main/auxiliary cultivation, using client-described effects."""
from datetime import datetime,timezone,timedelta
from time import time
import json
from native946_codec import packet
from native946_codec import text_notice
from native946_hero_page import DATA,RULES

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_hero_resist_groups (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      role INTEGER NOT NULL CHECK(role BETWEEN 1 AND 3),group_id INTEGER NOT NULL CHECK(group_id BETWEEN 1 AND 3),
      PRIMARY KEY(character_id,role),UNIQUE(character_id,group_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_hero_resist_levels (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      role INTEGER NOT NULL,skill_id INTEGER NOT NULL,level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 20),
      PRIMARY KEY(character_id,role,skill_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_hero_resist_daily (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      day TEXT NOT NULL,count INTEGER NOT NULL DEFAULT 0)''')

def saved(db,cid):
    groups={r['role']:r['group_id'] for r in db.execute('SELECT * FROM native_hero_resist_groups WHERE character_id=?',(cid,))}
    levels={(r['role'],r['skill_id']):r['level'] for r in db.execute('SELECT * FROM native_hero_resist_levels WHERE character_id=?',(cid,))}
    return groups,levels

def effects(db,cid):
    groups,levels=saved(db,cid);result={}
    for (role,iid),level in levels.items():
        meta=DATA['resistance_skills'].get(str(iid))
        if not meta or not level or groups.get(role)!=meta['type']:continue
        first,second=meta['endpoints']['master' if role==1 else 'assist'];cap=10 if role==1 else 5
        level=min(cap*2,level)
        for key in first.keys()|second.keys():
            value=first.get(key,0)*min(level,cap)//cap
            if level>cap:value+=(second.get(key,0)-first.get(key,0))*(level-cap)//cap
            result[key]=result.get(key,0)+value
    return result

def records(db,cid,snapshot):
    groups,levels=saved(db,cid);out=[]
    for role in (1,2,3):
        group=groups.get(role,0);rows=[]
        if group:
            for pos in range(1,5):
                iid=group*1000+pos;meta=DATA['resistance_skills'][str(iid)];level=levels.get((role,iid),0)
                cap=20 if role==1 else 10
                enabled=int((role==1 or pos<4) and level<cap)
                if pos>1 and levels.get((role,iid-1),0)<(10 if role==1 else 5):enabled=0
                if role>1 and level>=5:
                    master=groups.get(1,0)*1000+pos
                    if levels.get((1,master),0)<11:enabled=0
                rows.append(dict(Id=iid,GroupId=group,Type=role,Pos=pos,Grade=level,Value=level,MaxValue=cap,
                    Enable=enabled,Name=meta['name'],Desc=meta['host_tips' if role==1 else 'assist_tips'],
                    Cash=RULES['resist_cash_per_level']*(level+1),Exp=RULES['resist_exp_per_level']*(level+1),
                    UserCash=snapshot['gold'],UserExp=snapshot['experience'],AccumulationFund=snapshot['cultivation_state']['tongbao'],ItemType=0,ItemAmount=0,UserItemAmount=0))
        out.extend((group,rows))
    return tuple(out)

class HeroResistance:
    def __init__(self,game):self.g=game;self.p=game.progression
    def eligible(self,s,role,groups,levels):
        if s['rebirth']<2 or s['level']<101:raise ValueError('人物主辅修炼需要达到2转101级。')
        if role>1 and not groups.get(1):raise ValueError('请先选择主修类别。')
        if role==3 and (s['ascension']<1 or s['level']<120 or not groups.get(2) or levels.get((1,groups.get(1,0)*1000+4),0)<10):raise ValueError('第二辅修需要飞升120级、已选第一辅修且主修四阶达到一境界10级。')
    def row(self,db,iid):
        s=self.p._snapshot(db);groups,levels=saved(db,self.p.cid)
        for role in groups:
            self.eligible(s,role,groups,levels)
            entries=records(db,self.p.cid,s)
            for row in entries[(role-1)*2+1]:
                if row['Id']==iid:return row
        raise ValueError('该修炼不属于当前人物已选的主辅类别。')
    def daily(self,db):
        day=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
        row=db.execute('SELECT day,count FROM native_hero_resist_daily WHERE character_id=?',(self.p.cid,)).fetchone()
        return day,row['count'] if row and row['day']==day else 0
    def upgrade_packet(self,iid):
        with self.p.transaction(write=False) as db:
            row=self.row(db,iid);day,count=self.daily(db)
        row=dict(row,Grade=min(row['MaxValue'],row['Grade']+1))
        return packet(1295,['number']*3+['resistant'],[0,count,RULES['resist_daily_limit'],row])
    def choose(self,group,role):
        if group not in (1,2,3) or role not in (1,2,3):raise ValueError('主辅修炼类别无效。')
        with self.p.transaction() as db:
            s=self.p._snapshot(db);groups,levels=saved(db,self.p.cid);self.eligible(s,role,groups,levels)
            if role in groups:
                if groups[role]==group:return self.g.defer_packets(self.p.skill_packets)
                raise ValueError('更换已选类别请使用人物修炼更改界面。')
            if group in groups.values():raise ValueError('主修和两项辅修必须选择不同的类别。')
            db.execute('INSERT INTO native_hero_resist_groups VALUES(?,?,?)',(self.p.cid,role,group))
        return self.g.defer_packets(lambda:(*self.p.skill_packets(),text_notice('本机旧修炼：每级消耗1000×下一等级现金及经验，每日100次；满级效果按原生说明，中间等级线性增长。')))
    def learn(self,flag,iid):
        if flag in (0,1):
            with self.p.transaction(write=False) as db:self.row(db,iid)
            self.g.hero_resist_session=dict(skill=iid,expires=time()+600)
            return self.upgrade_packet(iid)
        if flag!=2:raise ValueError('人物修炼操作无效。')
        session=getattr(self.g,'hero_resist_session',None)
        if not session or session['skill']!=iid or session['expires']<time():raise ValueError('请先从技能页打开该项人物修炼。')
        with self.p.transaction() as db:
            row=self.row(db,iid)
            if not row['Enable']:raise ValueError('前置修炼尚未完成，或已经达到当前修炼上限。')
            day,count=self.daily(db)
            if count>=RULES['resist_daily_limit']:raise ValueError('已达到本机每日人物修炼次数。')
            if row['UserCash']<row['Cash'] or row['UserExp']<row['Exp']:raise ValueError('修炼所需现金或经验不足。')
            before=self.p._snapshot(db)
            db.execute('UPDATE characters SET gold=gold-?,experience=experience-? WHERE id=?',(row['Cash'],row['Exp'],self.p.cid))
            db.execute('INSERT INTO native_hero_resist_levels VALUES(?,?,?,?) ON CONFLICT(character_id,role,skill_id) DO UPDATE SET level=excluded.level',(self.p.cid,row['Type'],iid,row['Grade']+1))
            db.execute('INSERT INTO native_hero_resist_daily VALUES(?,?,?) ON CONFLICT(character_id) DO UPDATE SET day=excluded.day,count=excluded.count',(self.p.cid,day,count+1))
            after=self.p._snapshot(db)
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',(min(before['hp'],after['hpmax']),min(before['mp'],after['mpmax']),self.p.cid))
        self.g.last_event=dict(action='hero_resistance_upgraded',skill=iid)
        return self.g.defer_packets(lambda:(*self.p.sync(),self.p.resistance_packet(1),self.upgrade_packet(iid)))
    def reset(self,chosen):
        from native946_progression import lua_data
        with self.p.transaction(write=False) as db:
            groups,levels=saved(db,self.p.cid);s=self.p._snapshot(db)
            if chosen==[-1,-1,-1]:
                self.g.hero_resist_reset=dict(groups=groups,levels=levels,expires=time()+300)
                return packet(1294,['number']*3+['string'],[groups.get(1,0),groups.get(2,0),groups.get(3,0),lua_data({k:'本机更改：现金保留等级，或免费降低所更改类别各技能1级。' for k in (1,2,3)})])
            quote=getattr(self.g,'hero_resist_reset',None)
            if not quote or quote['expires']<time() or quote['groups']!=groups or quote['levels']!=levels:raise ValueError('请重新打开人物修炼更改界面。')
            resolved={role:groups.get(role,0) if value==-1 else value for role,value in enumerate(chosen,1)}
            if any(v not in (0,1,2,3) for v in resolved.values()):raise ValueError('修炼类别无效。')
            selected=[v for v in resolved.values() if v]
            if len(selected)!=len(set(selected)):raise ValueError('主辅修炼类别不能重复。')
            if any(bool(resolved[r])!=bool(groups.get(r)) for r in resolved):raise ValueError('该界面只能更改已有主辅修类别。')
            changed=[r for r in resolved if resolved[r]!=groups.get(r,0)]
            if not changed:raise ValueError('未更改人物修炼类别。')
            for role in changed:self.eligible(s,role,groups,levels)
            cost=RULES['resist_reset_cash_per_level']*max(1,sum(v for (r,_),v in levels.items() if r in changed))
        self.g.dialog_actions=[dict(kind='hero_resist_reset',groups=resolved,mode=mode,cost=cost) for mode in ('cash','level')]
        return self.g.menu('更改人物修炼。本机现金方案保留修炼等级；免费方案将更改类别的各项修炼降低1级。',('支付'+str(cost)+'现金','免费更改并降低修炼等级'),self.p.cid)
    def execute(self,action):
        quote=getattr(self.g,'hero_resist_reset',None)
        if not quote or quote['expires']<time():raise ValueError('人物修炼更改会话已失效。')
        with self.p.transaction() as db:
            groups,levels=saved(db,self.p.cid)
            if groups!=quote['groups'] or levels!=quote['levels']:raise ValueError('人物修炼记录已变化，请重新打开界面。')
            before=self.p._snapshot(db);target=action['groups']
            if action['mode']=='cash':
                if before['gold']<action['cost']:raise ValueError('更改修炼所需现金不足。')
                db.execute('UPDATE characters SET gold=gold-? WHERE id=?',(action['cost'],self.p.cid))
            db.execute('DELETE FROM native_hero_resist_groups WHERE character_id=?',(self.p.cid,))
            db.execute('DELETE FROM native_hero_resist_levels WHERE character_id=?',(self.p.cid,))
            for role,group in target.items():
                if not group:continue
                db.execute('INSERT INTO native_hero_resist_groups VALUES(?,?,?)',(self.p.cid,role,group))
                for pos in range(1,5 if role==1 else 4):
                    old=levels.get((role,groups.get(role,0)*1000+pos),0)
                    value=max(0,old-int(action['mode']=='level' and group!=groups.get(role)))
                    if value:db.execute('INSERT INTO native_hero_resist_levels VALUES(?,?,?,?)',(self.p.cid,role,group*1000+pos,value))
            after=self.p._snapshot(db)
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',(min(before['hp'],after['hpmax']),min(before['mp'],after['mpmax']),self.p.cid))
        self.g.hero_resist_reset=None;self.g.dialog_open=False;self.g.dialog_actions=[];self.g.dialog_npc=None
        return self.g.defer_packets(lambda:(packet(1183,['string'],['dlg_npc_chat']),*self.p.sync(),self.p.resistance_packet(1)))
    def receive(self,mid,r):
        if mid==740:
            group,role=r.integer(),r.integer();r.finish();return self.choose(group,role)
        if mid==1471:
            flag,iid=r.integer(),r.integer();r.finish();return self.learn(flag,iid)
        if mid==1472:
            values=[r.integer() for _ in range(3)];r.finish();return self.reset(values)
        raise ValueError('人物修炼请求无效。')
