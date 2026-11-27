"""Native one-key equipment glyph application with durable item-scoped effects."""
from time import time
from native946_codec import packet
from native946_codec import text_notice
from native946_inventory_features import DATA,RULES,consume,owned

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_temporary_glyphs (
      item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      level INTEGER NOT NULL,percent INTEGER NOT NULL,expires INTEGER NOT NULL,
      effects TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_glyph_item_levels (
      item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 10))''')

def effects(db,cid,items,page):
    import json
    active={v['id'] for v in items if v['page']==page};result={}
    for row in db.execute('SELECT item_id,effects FROM native_temporary_glyphs WHERE character_id=? AND expires>?',(cid,int(time()))):
        if row['item_id'] not in active:continue
        for key,value in json.loads(row['effects']).items():result[key]=result.get(key,0)+value
    return result

def description(db,cid,iid):
    import json
    row=db.execute('SELECT * FROM native_temporary_glyphs WHERE character_id=? AND item_id=? AND expires>?',(cid,iid,int(time()))).fetchone()
    if not row:return ''
    names={'attack':'攻击','defense':'防御'}
    return '本机附符：'+str(row['level'])+'级，'+ '、'.join(names.get(k,k)+'+'+str(v) for k,v in json.loads(row['effects']).items())+'，剩余'+str(max(1,(row['expires']-int(time()))//60))+'分钟'

class QuickGlyphs:
    def __init__(self,game):self.g=game;self.p=game.progression
    def info(self,db):
        from native946_inventory import active_page
        result={}
        for item in db.execute('SELECT id,slot FROM inventory WHERE character_id=? AND page=?',(self.p.cid,active_page(db,self.p.cid))):
            row=db.execute('SELECT * FROM native_temporary_glyphs WHERE character_id=? AND item_id=? AND expires>?',(self.p.cid,item['id'],int(time()))).fetchone()
            if row:result[item['slot']]=dict(Level=row['level'],Percent=row['percent'],Desc=description(db,self.p.cid,item['id']))
        return result
    def open(self):
        from native946_progression import lua_data
        with self.p.transaction(write=False) as db:info=self.info(db)
        return (packet(4316,['string'],[lua_data(info)]),text_notice('附符本机规则：优先消耗背包中相应部位的强化符文；无等级记录的符文按8级处理，持续2小时。1至5部位增加等级×10攻击，6至10部位增加等级×10防御。缺少符文时不自动购买。'))
    def use(self,iid):
        with self.p.transaction(write=False) as db:owned(db,self.p.cid,iid)
        return self.open()
    def receive(self,mid,r):
        from native946_progression import lua_data
        from native946_inventory import active_page
        from native946_mall_bags import pages
        from native946_inventory_locks import records
        import json
        if self.g.battle is not None:raise ValueError('战斗中不能进行附符。')
        if mid==4318:r.finish();return self.open()
        slots=[r.integer() for _ in range(r.take(1)[0])];level=r.integer();op=r.take(1)[0];r.finish()
        if not slots or len(slots)>10 or len(set(slots))!=len(slots) or any(s not in range(1,11) for s in slots):raise ValueError('附符部位无效。')
        if op not in (1,2) or (op==1 and not 1<=level<=RULES['glyph_max_level']):raise ValueError('附符等级或操作无效。')
        with self.p.transaction() as db:
            equipped={i['slot']:dict(i) for i in db.execute('SELECT * FROM inventory WHERE character_id=? AND page=?',(self.p.cid,active_page(db,self.p.cid)))}
            if any(s not in equipped for s in slots):raise ValueError('选中的部位没有穿戴装备，请重新选择。')
            if op==1:
                candidates={};selected={};locks=records(db,self.p.cid);bag=pages(db,self.p.cid)
                runes=db.execute('SELECT i.*,coalesce(l.level,?) AS glyph_level FROM inventory i LEFT JOIN native_glyph_item_levels l ON l.item_id=i.id AND l.character_id=i.character_id WHERE i.character_id=? ORDER BY glyph_level,i.id',
                                 (RULES['glyph_default_level'],self.p.cid)).fetchall()
                for rune in runes:
                    slot=DATA['glyph_slots'].get(str(rune['type']))
                    if slot in slots and rune['page'] in bag and rune['id'] not in locks and rune['glyph_level']>=level:candidates.setdefault(slot,[]).append(rune)
                for s in slots:
                    if not candidates.get(s):raise ValueError('所选部位缺少等级不低于'+str(level)+'级的强化符文；本次未扣除物品。')
                    selected[s]=candidates[s][0]
                for s in slots:
                    rune=selected[s];consume(db,self.p.cid,rune['id'])
                    bonus={'attack':level*RULES['glyph_attack_per_level']} if s<=5 else {'defense':level*RULES['glyph_defense_per_level']}
                    db.execute('INSERT INTO native_temporary_glyphs VALUES(?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET level=excluded.level,percent=excluded.percent,expires=excluded.expires,effects=excluded.effects',
                               (equipped[s]['id'],self.p.cid,level,100,int(time())+RULES['glyph_seconds'],json.dumps(bonus)))
            else:
                for s in slots:db.execute('DELETE FROM native_temporary_glyphs WHERE item_id=? AND character_id=?',(equipped[s]['id'],self.p.cid))
        self.g.last_event=dict(action='equipment_glyphs_applied' if op==1 else 'equipment_glyphs_cleared',slots=slots,level=level)
        def render():
            with self.p.transaction(write=False) as db:info=self.info(db)
            return (*self.p.sync(),packet(4315,[('array','number'),'number','string','byte'],[slots,level,lua_data(info),op]))
        return self.g.defer_packets(render)
