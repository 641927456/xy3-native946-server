"""Native equipment inscriptions with verified fixed/custom material costs."""
from time import time,monotonic
import json,re
from native946_codec import packet,SCHEMAS
from native946_codec import text_notice

def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS cosmetic_item_carvings(item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,character_id INTEGER NOT NULL REFERENCES characters(id),data TEXT NOT NULL)')

def stored(db,cid,item_id):
    row=db.execute('SELECT data FROM cosmetic_item_carvings WHERE character_id=? AND item_id=?',(cid,item_id)).fetchone()
    return json.loads(row[0]) if row else {}

class Carving:
    def __init__(self,game):self.g=game;self.p=game.progression
    def open(self):
        self.g.cosmetic_carve=dict(item=0,until=monotonic()+180)
        return packet(222,[],[])
    def session(self,item=0):
        s=getattr(self.g,'cosmetic_carve',None)
        if not s or s['until']<monotonic() or item and item!=s['item']:raise ValueError('刻字会话已失效，请重新使用大唐烙玉。')
        return s
    def target(self,db,item_id):
        from native946_progression import GEAR
        from native946_equipment import records
        from native946_mall_bags import pages
        row=db.execute('SELECT * FROM inventory WHERE character_id=? AND id=?',(self.p.cid,item_id)).fetchone()
        if row is None or row['page'] not in (1,*pages(db,self.p.cid)) or row['type'] not in GEAR and item_id not in records(db,self.p.cid):raise ValueError('请选择当前角色携带的真实装备。')
        from native946_inventory_locks import require_unlocked
        require_unlocked(db,self.p.cid,item_id)
        return row
    def data(self,ctype,content=''):
        from native946_mall_uses import DATA
        from native946_tasks import grade_number
        if ctype:
            if not 1<=ctype<=len(DATA['carving']['strings']):raise ValueError('原生刻字语句编号无效。')
            content=''
        else:
            emotes=re.findall(r'#[0-9]{1,3}',content)
            if not content or len(content.encode('gb18030'))>40 or len(emotes)>2 or '#' in re.sub(r'#[0-9]{1,3}','',content) or any(ord(c)<32 for c in content):raise ValueError('自定义刻字最多40字节、两个原生表情，不能包含控制符。')
        s=self.p.snapshot();state=s['cultivation_state']
        return dict(level_num=grade_number(s['level'],state['rebirth'],state['ascension'],state['sanctification']),eff=0,ctype=ctype,cname=self.g.character['name'],content=content,ctime=int(time()))
    def confirm(self,item_id,ctype,category=0,content=''):
        from native946_mall_uses import DATA,consume
        from native946_mall_checkout import rows
        self.session(item_id)
        if ctype==-1:raise ValueError('原版随机刻字的隐藏语句概率尚未核定；本次未消耗烙玉。')
        data=self.data(ctype,content)
        if ctype and (not 1<=category<=len(DATA['carving']['categories']) or ctype not in DATA['carving']['categories'][category-1]['string_ids']):raise ValueError('刻字语句与原生类别不匹配。')
        cost=DATA['carving']['strings'][ctype-1]['price'] if ctype else 1
        with self.p.transaction() as db:
            self.target(db,item_id);materials=rows(db,self.p.cid,(55174,65174))
            if sum(row['amount'] for row in materials)<cost:raise ValueError('大唐烙玉数量不足。')
            left=cost
            for row in materials:
                take=min(left,row['amount']);consume(db,row,take);left-=take
                if not left:break
            db.execute('INSERT INTO cosmetic_item_carvings VALUES(?,?,?) ON CONFLICT(item_id) DO UPDATE SET data=excluded.data',(item_id,self.p.cid,json.dumps(data,ensure_ascii=False)))
        self.g.cosmetic_carve=None
        return self.g.defer_packets(lambda:(*self.p.item_packets(),self.p.item_description(item_id),text_notice('装备刻字成功。')))
    def receive(self,mid,r):
        self.session()
        if mid==280:
            item,kind,op=r.integer(),r.integer(),r.integer();r.finish()
            if op==2:self.g.cosmetic_carve=None;return None
            if op!=1:raise ValueError('选择刻字装备操作无效。')
            from native946_mall_checkout import rows
            with self.p.transaction(write=False) as db:
                row=self.target(db,item)
                if row['type']!=kind:raise ValueError('装备类型已变化。')
                count=sum(v['amount'] for v in rows(db,self.p.cid,(55174,65174)))
            self.g.cosmetic_carve=dict(item=item,until=monotonic()+180)
            return packet(223,['number']*4,[item,kind,55174,count])
        item=r.integer();self.session(item)
        if mid==240:
            info={}
            for name,kind in SCHEMAS['carve_info']:
                info[name]=r.string().decode('gb18030') if kind=='string' else r.take(1)[0] if kind=='byte' else r.integer()
            r.finish();data=self.data(info['ctype'],info['content'])
            with self.p.transaction(write=False) as db:self.target(db,item)
            return self.p.item_description(item,carve_preview=data)
        if mid==239:content=r.string().decode('gb18030');r.finish();return self.confirm(item,0,content=content)
        ctype,category=r.integer(),r.integer();r.finish();return self.confirm(item,ctype,category)
