"""Native character-page queries and two independent allocation plans."""
from pathlib import Path
from time import time
import json
from native946_codec import packet
from native946_training import state as training_state
DATA=json.loads(Path(__file__).with_name('native946_character_page_data.json').read_text('utf8'))
RULES=DATA['local_rules'];WIRE_VERSION=1
REQUESTS={2093,3136,3780,1387,740,1471,1472,743,744,745,746,747,2606,2607,2608}
WRITES=REQUESTS-{2093,3136,746,2607}
NAMES=('cor','mag','strength','speed')

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_hero_page_state (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      active_plan INTEGER NOT NULL DEFAULT 1 CHECK(active_plan IN (1,2)),
      switch_count INTEGER NOT NULL DEFAULT 0 CHECK(switch_count>=0))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_point_plans (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      plan_id INTEGER NOT NULL CHECK(plan_id IN (1,2)),level INTEGER NOT NULL,
      cor INTEGER NOT NULL,mag INTEGER NOT NULL,strength INTEGER NOT NULL,speed INTEGER NOT NULL,
      points INTEGER NOT NULL CHECK(points>=0),PRIMARY KEY(character_id,plan_id))''')
    from native946_hero_resistance import schema as resistance_schema
    from native946_hero_titles import schema as title_schema
    resistance_schema(db);title_schema(db)
    from native946_hero_extra_points import schema as extra_schema
    extra_schema(db)

def active_plan(db,cid):
    row=db.execute('SELECT active_plan FROM native_hero_page_state WHERE character_id=?',(cid,)).fetchone()
    return row[0] if row else 1

def point_plan_visibility(sanctification):
    # The 946 c_open_two_points handler shows both buttons only for status 1.
    return packet(1265, ['byte'], [int(bool(sanctification))])


class HeroPage:
    def __init__(self,game):self.g=game;self.p=game.progression
    def raw(self,db):
        return dict(db.execute('SELECT p.cor,p.mag,p.strength,p.speed,p.points,c.level FROM progression p JOIN characters c ON c.id=p.character_id WHERE p.character_id=?',(self.p.cid,)).fetchone())
    def save_plan(self,db,index,values):
        db.execute('INSERT INTO native_point_plans VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(character_id,plan_id) DO UPDATE SET level=excluded.level,cor=excluded.cor,mag=excluded.mag,strength=excluded.strength,speed=excluded.speed,points=excluded.points',
                   (self.p.cid,index,values['level'],*[values[k] for k in NAMES],values['points']))
    def initialize(self,db):
        if db.execute('SELECT 1 FROM native_hero_page_state WHERE character_id=?',(self.p.cid,)).fetchone():return
        raw=self.raw(db);self.save_plan(db,1,raw)
        baseline=dict(raw)
        for k in NAMES:baseline[k]=raw['level']
        baseline['points']=raw['points']+sum(raw[k]-baseline[k] for k in NAMES)
        if baseline['points']<0:raise ValueError('人物基础属性点记录不完整，暂不能建立第二套加点。')
        self.save_plan(db,2,baseline)
        db.execute('INSERT INTO native_hero_page_state(character_id) VALUES(?)',(self.p.cid,))
    def signature(self,db):
        state=db.execute('SELECT active_plan,switch_count FROM native_hero_page_state WHERE character_id=?',(self.p.cid,)).fetchone()
        raw=self.raw(db)
        return (state[0],state[1],raw['level'],*(raw[k] for k in NAMES),raw['points'])
    def require_point_plans(self, db):
        if not training_state(db, self.p.cid)['sanctification']:
            raise ValueError('封圣后才能使用左辅右弼加点方案。')

    def quote_switch(self):
        with self.p.transaction() as db:
            self.require_point_plans(db)
            self.initialize(db);signature=self.signature(db)
            count=RULES['switch_book_base']+signature[1]*RULES['switch_book_increment']
        self.g.hero_point_quote=dict(signature=signature,cost=count,expires=time()+120)
        return packet(3779,['number'],[count])
    def switch(self):
        from native946_mall_bags import pages
        from native946_inventory_features import consume
        with self.p.transaction() as db:
            self.require_point_plans(db)
            quote=getattr(self.g,'hero_point_quote',None)
            if not quote or quote['expires']<time():raise ValueError('请先点击属性页的方案切换按钮查看消耗。')
            self.initialize(db)
            if self.signature(db)!=quote['signature']:raise ValueError('加点或切换次数已经变化，请重新查看切换消耗。')
            current=self.raw(db);old=active_plan(db,self.p.cid);target=3-old
            other=dict(db.execute('SELECT * FROM native_point_plans WHERE character_id=? AND plan_id=?',(self.p.cid,target)).fetchone())
            pool=current['points']+sum(current[k] for k in NAMES)
            delta=max(0,current['level']-other['level'])
            chosen={k:max(current['level'],other[k]+delta) for k in NAMES}
            if sum(chosen.values())>pool:chosen={k:current['level'] for k in NAMES}
            if sum(chosen.values())>pool:raise ValueError('当前基础属性总量异常，本次切换未执行。')
            candidates=[dict(r) for r in db.execute("SELECT i.* FROM inventory i LEFT JOIN native_object_locks l ON l.kind='item' AND l.object_id=i.id WHERE i.character_id=? AND i.type=45671 AND l.object_id IS NULL ORDER BY i.id",(self.p.cid,)) if r['page'] in pages(db,self.p.cid)]
            if sum(i['amount'] for i in candidates)<quote['cost']:raise ValueError('未加锁的紫斗全书数量不足。')
            remaining=quote['cost']
            for item in candidates:
                count=min(remaining,item['amount']);consume(db,self.p.cid,item['id'],count);remaining-=count
                if not remaining:break
            before=self.p._snapshot(db);self.save_plan(db,old,current)
            db.execute('UPDATE progression SET cor=?,mag=?,strength=?,speed=?,points=? WHERE character_id=?',(*[chosen[k] for k in NAMES],pool-sum(chosen.values()),self.p.cid))
            self.save_plan(db,target,dict(chosen,level=current['level'],points=pool-sum(chosen.values())))
            db.execute('UPDATE native_hero_page_state SET active_plan=?,switch_count=switch_count+1 WHERE character_id=?',(target,self.p.cid))
            # Every equipped item's requirements must survive the new allocation.
            after=self.p._snapshot(db)
            for item in after['items']:
                if item['page']==after['equipment_page']:self.p.validate_equipment(after,item,item['slot'],item['page'])
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',(min(before['hp'],after['hpmax']),min(before['mp'],after['mpmax']),self.p.cid))
        self.g.hero_point_quote=None;self.g.last_event=dict(action='hero_point_plan_changed',plan=target)
        return self.g.defer_packets(self.p.sync)
    def additive(self,mode):
        if mode not in (1,2):raise ValueError('附加属性查询类型无效。')
        from native946_hero_extra_points import values,counters
        with self.p.transaction(write=False) as db:
            extra=values(db,self.p.cid);counts=counters(db,self.p.cid)
        adds=[extra[k] for k in NAMES];maximum=sum(adds)+15-sum(counts)
        # Native hero_common formats exactly five yearly food counters.
        return packet(3135,['byte']+['number']*5+[('array','number')],[mode,*adds,maximum,counts])
    def receive(self,mid,r):
        if mid==2093:
            window=r.integer();r.finish()
            from native946_hero_extra_points import values
            with self.p.transaction(write=False) as db:
                raw=self.raw(db);extra=values(db,self.p.cid)
                sanctification=training_state(db,self.p.cid)['sanctification']
            return (packet(2091,['number']*4,[raw[k]+extra[k] for k in NAMES]),
                    packet(2092,['number',('array','ui_btnstatus')],[window,[]]),
                    point_plan_visibility(sanctification))
        if mid==3136:
            mode=r.take(1)[0];r.finish();return self.additive(mode)
        if mid in (743,744,745,746,747,2606,2607,2608):
            from native946_hero_titles import HeroTitles
            return HeroTitles(self.g).receive(mid,r)
        if self.g.battle is not None:raise ValueError('请先结束战斗再修改人物属性。')
        if mid==3780:r.finish();return self.quote_switch()
        if mid==1387:r.finish();return self.switch()
        from native946_hero_resistance import HeroResistance
        return HeroResistance(self.g).receive(mid,r)
