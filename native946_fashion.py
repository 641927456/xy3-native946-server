"""946 native fashion score, collection, tier rewards and sun/moon entry.

Rules are the classic client's own INFO_FASHION. The user's direct-mall
acquisition uses ordinary paid orders without marking original quest 3400 done.
"""
from pathlib import Path
from time import time
import json
from native946_codec import packet
from native946_codec import text_notice
REFERENCE_WIRE_VERSION=2

DATA=json.loads(Path(__file__).with_name('native946_fashion_data.json').read_text('utf8'))
RULES=DATA['fashion'];SUNMOON=DATA['sunmoon'];ITEMS=DATA['items']
REQUESTS={745,746,747,2606,3737,3738,3739,4013,4014,4015,4016,5407,5409,5410,5411,5412}
WRITES={745,747,2606,4013,4015,5411}

def normalized(kind):return kind-10000 if 65000<=kind<=69999 else kind

def record_use(db,cid,kind):
    kind=normalized(kind);meta=RULES['ItemConfig'].get(str(kind))
    if not meta or meta['FashionScore']<=0:return False
    return bool(db.execute('INSERT OR IGNORE INTO native_fashion_used VALUES(?,?,?,?)',
                           (cid,kind,meta['FashionScore'],int(time()))).rowcount)

def schema(db):
    existed=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_fashion_used'").fetchone())
    db.execute('''CREATE TABLE IF NOT EXISTS native_fashion_used (
      character_id INTEGER NOT NULL REFERENCES characters(id),type INTEGER NOT NULL,
      score INTEGER NOT NULL CHECK(score>0),created INTEGER NOT NULL,
      PRIMARY KEY(character_id,type))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_fashion_claims (
      character_id INTEGER NOT NULL REFERENCES characters(id),kind TEXT NOT NULL,
      tier INTEGER NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(character_id,kind,tier))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_fashion_styles (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id),style INTEGER NOT NULL,
      cycle INTEGER NOT NULL,start_time INTEGER NOT NULL)''')
    if not existed:
        # A worn item proves prior use; merely owning or buying one does not.
        for row in db.execute('SELECT character_id,type FROM inventory WHERE page=29').fetchall():
            record_use(db,row['character_id'],row['type'])

def score(db,cid):
    return db.execute('SELECT coalesce(sum(score),0) FROM native_fashion_used WHERE character_id=?',(cid,)).fetchone()[0]

def title(db,cid,key='fashion_title'):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_hero_title_state'").fetchone():
        column='attribute_title' if key=='fashion_real_title' else 'display_title'
        row=db.execute('SELECT t.name FROM native_hero_title_state s JOIN native_hero_titles t ON t.character_id=s.character_id AND t.name=s.'+column+' WHERE s.character_id=? AND t.hidden=0',(cid,)).fetchone()
        if row:return row[0]
        if db.execute('SELECT 1 FROM native_hero_title_state WHERE character_id=?',(cid,)).fetchone():return ''
    row=db.execute('SELECT value FROM cosmetic_features WHERE character_id=? AND key=?',(cid,key)).fetchone()
    if not row or not 1<=row[0]<=len(RULES['GradeConfig']):return ''
    if not db.execute("SELECT 1 FROM native_fashion_claims WHERE character_id=? AND kind='grade' AND tier=?",(cid,row[0])).fetchone():return ''
    return RULES['GradeConfig'][row[0]-1]['TitleName']

class Fashion:
    def __init__(self,game):self.g=game;self.p=game.progression

    def state(self,db):
        used=[r[0] for r in db.execute('SELECT type FROM native_fashion_used WHERE character_id=? ORDER BY type',(self.p.cid,))]
        claims={(r[0],r[1]) for r in db.execute('SELECT kind,tier FROM native_fashion_claims WHERE character_id=?',(self.p.cid,))}
        grade=max((i for kind,i in claims if kind=='grade'),default=0)
        return score(db,self.p.cid),used,claims,grade

    def collection(self,mode=0):
        from native946_progression import lua_data
        with self.p.transaction(write=False) as db:_,used,claims,_=self.state(db)
        return packet(824,['number','string','string'],[mode,lua_data(used),lua_data(sorted(i for k,i in claims if k=='collect'))])

    def sync(self):
        with self.p.transaction(write=False) as db:total,_,_,_=self.state(db);selected=title(db,self.p.cid)
        return packet(695,['number','number'],[84,total]),packet(696,['number','string'],[7,selected]),packet(694,[],[]),self.collection()

    def titles(self):
        with self.p.transaction(write=False) as db:
            _,_,claims,_=self.state(db);selected=title(db,self.p.cid);real=title(db,self.p.cid,'fashion_real_title')
        owned=[v['TitleName'] for i,v in enumerate(RULES['GradeConfig'],1) if ('grade',i) in claims]
        return packet(700,['string','string',('array','string'),'string',('array','string'),('array','string')],[selected,real,owned,'',[],[]])

    def ranking(self):
        with self.p.transaction(write=False) as db:
            rows=db.execute('SELECT c.id,c.name,sum(u.score) AS total FROM native_fashion_used u JOIN characters c ON c.id=u.character_id GROUP BY c.id ORDER BY total DESC,c.id LIMIT ?',(DATA['rank']['RankLimit'],)).fetchall()
        return [[i,r['id'],r['id'],r['name'],r['total']] for i,r in enumerate(rows,1)]

    def rank_packet(self,ranktype,start=None,end=None):
        if ranktype!=18:raise ValueError('本机暂未接通此榜单。')
        from native946_progression import lua_data
        rows=self.ranking()
        if start is None:
            mine=next((row for row in rows if row[1]==self.p.cid),[])
            return packet(3733,['number','string','string'],[18,lua_data(mine),'{}'])
        if not 1<=start<=end<=DATA['rank']['RankLimit'] or end-start>=100:raise ValueError('排行榜查询范围无效。')
        selected=rows[start-1:end]
        return packet(3732,['number']*4+[('array','string')],[18,start,end,len(rows),[lua_data(row) for row in selected]])

    def set_title(self,name,active,real=False):
        if active not in (0,1):raise ValueError('称谓状态无效。')
        with self.p.transaction() as db:
            _,_,claims,_=self.state(db)
            tier=next((i for i,v in enumerate(RULES['GradeConfig'],1) if v['TitleName']==name and ('grade',i) in claims),None)
            if tier is None:raise ValueError('尚未解锁此华丽度称谓。')
            key='fashion_real_title' if real else 'fashion_title'
            if active:
                db.execute('INSERT INTO cosmetic_features VALUES(?,?,?,0) ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value,expires=0',(self.p.cid,key,tier))
            elif title(db,self.p.cid,key)==name:db.execute('DELETE FROM cosmetic_features WHERE character_id=? AND key=?',(self.p.cid,key))
        from native946_cosmetics import Cosmetics
        return self.g.defer_packets(lambda:(*Cosmetics(self.g).visual_packets(recreate=not real),self.titles()))

    def panel(self):
        with self.p.transaction(write=False) as db:_,_,_,grade=self.state(db)
        return (*self.sync(),packet(4012,['number'],[grade]))

    def _grant(self,db,rows):
        from native946_cosmetics import Cosmetics
        for kind,amount,*_ in rows:Cosmetics(self.g)._add(db,kind,amount)

    def unlock(self):
        with self.p.transaction() as db:
            total,_,_,grade=self.state(db);target=grade+1
            if target>len(RULES['GradeConfig']):raise ValueError('已解锁全部华丽度段位。')
            rule=RULES['GradeConfig'][target-1]
            if total<rule['FashionScore']:raise ValueError('华丽度不足，下一段位需要%d分。'%rule['FashionScore'])
            db.execute('INSERT INTO native_fashion_claims VALUES(?,?,?,?)',(self.p.cid,'grade',target,int(time())))
            self._grant(db,rule['ItemList'])
            from native946_hero_titles import sync_fashion_titles
            sync_fashion_titles(db,self.p.cid)
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.panel(),text_notice('已解锁华丽度段位：'+rule['TitleName']+'，永久称谓和奖励已保存。')))

    def collect(self,index):
        if not 1<=index<=len(RULES['CollectConfig']):raise ValueError('图鉴奖励档位无效。')
        rule=RULES['CollectConfig'][index-1]
        with self.p.transaction() as db:
            _,used,claims,_=self.state(db)
            if ('collect',index) in claims:raise ValueError('此档图鉴奖励已经领取。')
            if len(used)<rule['CollectCount']:raise ValueError('异宝图鉴收集数量不足。')
            db.execute('INSERT INTO native_fashion_claims VALUES(?,?,?,?)',(self.p.cid,'collect',index,int(time())))
            self._grant(db,[rule['Item']])
        return self.g.defer_packets(lambda:(*self.p.item_packets(),self.collection(),text_notice('图鉴收集奖励已发放。')))

    def sunmoon_panel(self):
        with self.p.transaction(write=False) as db:total,_,_,_=self.state(db)
        # No original server completion/purchase receipt exists in this build.
        # All six rows still reach the native preview with their correct locks.
        return packet(5408,['byte','byte','number','number',('array','pair_number')],
                      [0,0,0,total,[dict(key=i,value=0) for i in range(1,len(SUNMOON['RewardConfig'])+1)]])

    def style(self):
        with self.p.transaction(write=False) as db:
            row=db.execute('SELECT style,cycle,start_time FROM native_fashion_styles WHERE character_id=?',(self.p.cid,)).fetchone()
        return tuple(row) if row else (0,0,0)

    def full_suit(self,db):
        from native946_cosmetics import DATA as CATALOG
        reward=SUNMOON['RewardConfig'][0]
        kind=reward['MaleItemList' if self.g.character['sex']==1 else 'FemaleItemList'][0][0]
        expected=set(CATALOG['sets'][str(kind)])
        worn={normalized(r['type']) for r in db.execute('SELECT i.type FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.character_id=? AND i.page=29 AND (coalesce(e.expires,0)=0 OR e.expires>?)',(self.p.cid,int(time())))}
        return expected<=worn

    def style_sync(self):
        style,cycle,start=self.style()
        with self.p.transaction(write=False) as db:
            if not self.full_suit(db):style=cycle=start=0
        return packet(5414,['number','number','number','byte'],[start,cycle,self.p.cid,style])

    def set_style(self,style,cycle):
        if style not in (1,2) or cycle not in (0,*SUNMOON['BaseConfig']['CycleList']):raise ValueError('日月形态或轮播间隔无效。')
        with self.p.transaction() as db:
            if not self.full_suit(db):raise ValueError('请先穿戴网罗日月全套霓裳。')
            db.execute('INSERT INTO native_fashion_styles VALUES(?,?,?,?) ON CONFLICT(character_id) DO UPDATE SET style=excluded.style,cycle=excluded.cycle,start_time=excluded.start_time',(self.p.cid,style,cycle,int(time())))
        return self.g.defer_packets(lambda:(packet(5413,['byte','number'],[style,cycle]),self.style_sync()))

    def receive(self,mid,r):
        if mid in (5407,5409,5410):
            from native946_fashion_shop import FashionShop
            shop=FashionShop(self.g)
            if mid==5407:r.finish();return shop.open()
            index=r.integer();r.finish()
            if mid==5409:return shop.source(index)
            if index not in (1,2,3):raise ValueError('网罗日月操作无效。')
            return shop.open()
        if mid==3737:
            ranktype,start,end=r.integer(),r.integer(),r.integer();r.finish();return self.rank_packet(ranktype,start,end)
        if mid==3739:ranktype=r.integer();r.finish();return self.rank_packet(ranktype)
        if mid==3738:
            ranktype=r.integer();r.finish()
            if ranktype!=18:raise ValueError('本机暂未接通此榜单。')
            raise ValueError('华丽度月榜奖励尚未结算，暂不能领取。')
        if mid==746:r.finish();return self.titles()
        if mid in (745,747,2606):
            name=r.string().decode('gb18030');active=r.take(1)[0];r.finish()
            return self.set_title(name,1-active if mid==745 else active,real=mid==2606)
        if mid==4014:r.finish();return self.panel()
        if mid==4015:r.finish();return self.unlock()
        if mid==4013:index=r.integer();r.finish();return self.collect(index)
        if mid==4016:
            count=r.take(1)[0];kinds=[r.integer() for _ in range(count)];payment=r.integer();r.finish()
            if not kinds or len(kinds)>24 or payment not in (0,1,2):raise ValueError('图鉴购买参数无效。')
            from native946_cosmetics import Cosmetics,DATA as CATALOG
            if any(str(k) not in CATALOG['goods'] for k in kinds):raise ValueError('部分外观尚不支持直购。')
            # Preserve the ordinary native checkout and its quote confirmation.
            return Cosmetics(self.g).mall(kinds[0])
        if mid==5411:
            style=r.take(1)[0];cycle=r.integer();r.finish();return self.set_style(style,cycle)
        if mid==5412:
            r.finish();style,cycle,_=self.style()
            return packet(5413,['byte','number'],[style,cycle])
        raise ValueError('华丽度操作无效。')

