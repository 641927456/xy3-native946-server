"""Native owned titles: display, independent attribute activation and hiding."""
import json
from native946_codec import packet
REFERENCE_WIRE_VERSION=2

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_hero_titles (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      name TEXT NOT NULL,style INTEGER NOT NULL DEFAULT 0,description TEXT NOT NULL DEFAULT '',
      effects TEXT NOT NULL DEFAULT '{}',custom INTEGER NOT NULL DEFAULT 0,
      hidden INTEGER NOT NULL DEFAULT 0,award_item INTEGER NOT NULL DEFAULT 0,
      reward_claimed INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(character_id,name))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_hero_title_state (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      display_title TEXT NOT NULL DEFAULT '',attribute_title TEXT NOT NULL DEFAULT '')''')
    sync_fashion_titles(db)

def sync_fashion_titles(db,cid=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_fashion_claims'").fetchone():return
    from native946_fashion import RULES
    query="SELECT character_id,tier FROM native_fashion_claims WHERE kind='grade'"
    rows=db.execute(query+(' AND character_id=?' if cid is not None else ''),(cid,) if cid is not None else ()).fetchall()
    for row in rows:
        if not 1<=row['tier']<=len(RULES['GradeConfig']):continue
        rule=RULES['GradeConfig'][row['tier']-1]
        db.execute('INSERT OR IGNORE INTO native_hero_titles(character_id,name,description) VALUES(?,?,?)',
                   (row['character_id'],rule['TitleName'],'华丽度段位永久称谓，已解锁%d分档位。'%rule['FashionScore']))
    for character_id in {row['character_id'] for row in rows}:
        if db.execute('SELECT 1 FROM native_hero_title_state WHERE character_id=?',(character_id,)).fetchone():continue
        selected={r['key']:r['value'] for r in db.execute("SELECT key,value FROM cosmetic_features WHERE character_id=? AND key IN ('fashion_title','fashion_real_title')",(character_id,))}
        def name(key):
            tier=selected.get(key,0)
            if not 1<=tier<=len(RULES['GradeConfig']):return ''
            value=RULES['GradeConfig'][tier-1]['TitleName']
            return value if db.execute('SELECT 1 FROM native_hero_titles WHERE character_id=? AND name=?',(character_id,value)).fetchone() else ''
        display,real=name('fashion_title'),name('fashion_real_title')
        if display or real:db.execute('INSERT INTO native_hero_title_state VALUES(?,?,?)',(character_id,display,real))

def current(db,cid):
    row=db.execute('SELECT s.display_title,s.attribute_title,t.style,t.hidden FROM native_hero_title_state s LEFT JOIN native_hero_titles t ON t.character_id=s.character_id AND t.name=s.display_title WHERE s.character_id=?',(cid,)).fetchone()
    return dict(title=row['display_title'] if row and row['style'] is not None and not row['hidden'] else '',title_style=row['style'] or 0 if row else 0)

def effects(db,cid):
    row=db.execute('SELECT t.effects FROM native_hero_title_state s JOIN native_hero_titles t ON t.character_id=s.character_id AND t.name=s.attribute_title WHERE s.character_id=? AND t.hidden=0',(cid,)).fetchone()
    return json.loads(row[0]) if row else {}

class HeroTitles:
    def __init__(self,game):self.g=game;self.p=game.progression
    def rows(self,db):return [dict(r) for r in db.execute('SELECT * FROM native_hero_titles WHERE character_id=? ORDER BY name',(self.p.cid,))]
    def get(self,db,name):
        row=db.execute('SELECT * FROM native_hero_titles WHERE character_id=? AND name=?',(self.p.cid,name)).fetchone()
        if not row:raise ValueError('尚未获得该称谓。')
        return dict(row)
    def panel(self):
        with self.p.transaction(write=False) as db:
            rows=self.rows(db);s=db.execute('SELECT * FROM native_hero_title_state WHERE character_id=?',(self.p.cid,)).fetchone()
            display=s['display_title'] if s else '';real=s['attribute_title'] if s else ''
            active=next((r for r in rows if r['name']==real and not r['hidden']),None)
            affect=active['description'] if active else ''
        return packet(700,['string','string',('array','string'),'string',('array','string'),('array','string')],
                      [display,real,[r['name'] for r in rows],affect,[],[r['name'] for r in rows if r['hidden']]])
    def detail(self,name):
        with self.p.transaction(write=False) as db:row=self.get(db,name)
        return packet(2605,['string','number','string','string','number','string','number','string','number','number'],
          [row['name'],row['style'],'已获得',row['description'],row['award_item'],'',0,'',0,int(bool(row['reward_claimed']) or not row['award_item'])])
    def refresh(self):
        from native946_cosmetics import Cosmetics
        def build():
            with self.p.transaction(write=False) as db:shown=current(db,self.p.cid)
            return (*self.p.sync(),packet(696,['number','string'],[7,shown['title']]),self.panel(),*Cosmetics(self.g).visual_packets(recreate=True))
        return self.g.defer_packets(build)
    def receive(self,mid,r):
        if mid==746:r.finish();return self.panel()
        name=r.string().decode('gb18030')
        flag=r.take(1)[0] if mid in (745,747,2606) else None;r.finish()
        if mid==2607:return self.detail(name)
        if self.g.battle is not None:raise ValueError('战斗中不能更改称谓。')
        if flag is not None and flag not in (0,1):raise ValueError('称谓操作无效。')
        with self.p.transaction() as db:
            db.execute('INSERT OR IGNORE INTO native_hero_title_state(character_id) VALUES(?)',(self.p.cid,))
            before=self.p._snapshot(db)
            if mid==743:
                if not name.strip() or len(name.encode('gb18030'))>24 or any(ord(c)<32 or c=='#' for c in name):raise ValueError('自定义称谓应为1至12个汉字，不可包含控制符。')
                if sum(bool(row['custom']) for row in self.rows(db))>=20:raise ValueError('已达到本机自定义称谓数量上限。')
                if db.execute('SELECT 1 FROM native_hero_titles WHERE character_id=? AND name=?',(self.p.cid,name)).fetchone():raise ValueError('已经拥有该称谓。')
                db.execute('INSERT INTO native_hero_titles(character_id,name,description,custom) VALUES(?,?,?,1)',(self.p.cid,name,'自定义称谓，无额外属性。'))
            else:
                row=self.get(db,name)
                if mid==744:
                    if not row['custom']:raise ValueError('只能删除自定义称谓。')
                    db.execute('DELETE FROM native_hero_titles WHERE character_id=? AND name=?',(self.p.cid,name))
                    db.execute("UPDATE native_hero_title_state SET display_title=CASE WHEN display_title=? THEN '' ELSE display_title END,attribute_title=CASE WHEN attribute_title=? THEN '' ELSE attribute_title END WHERE character_id=?",(name,name,self.p.cid))
                elif mid==745:
                    db.execute('UPDATE native_hero_titles SET hidden=? WHERE character_id=? AND name=?',(int(not flag),self.p.cid,name))
                    if not flag:db.execute("UPDATE native_hero_title_state SET display_title=CASE WHEN display_title=? THEN '' ELSE display_title END,attribute_title=CASE WHEN attribute_title=? THEN '' ELSE attribute_title END WHERE character_id=?",(name,name,self.p.cid))
                elif mid in (747,2606):
                    if row['hidden'] and flag:raise ValueError('请先取消隐藏该称谓。')
                    column='display_title' if mid==747 else 'attribute_title'
                    db.execute('UPDATE native_hero_title_state SET '+column+'=? WHERE character_id=?',(name if flag else '',self.p.cid))
                elif mid==2608:
                    if not row['award_item'] or row['reward_claimed']:raise ValueError('该称谓没有待领取的奖励。')
                    from native946_inventory_features import give
                    give(db,self.p,row['award_item'])
                    db.execute('UPDATE native_hero_titles SET reward_claimed=1 WHERE character_id=? AND name=?',(self.p.cid,name))
                else:raise ValueError('称谓操作无效。')
            after=self.p._snapshot(db)
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',(min(before['hp'],after['hpmax']),min(before['mp'],after['mpmax']),self.p.cid))
        self.g.last_event=dict(action='hero_title_changed',request=mid,name=name)
        return self.refresh()
