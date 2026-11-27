"""Native eight-kind / nine-slot transformation collection and appearance."""
from time import time
from native946_codec import packet
from native946_codec import text_notice
from native946_inventory_features import DATA,RULES,consume,owned

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_transform_levels (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      kind INTEGER NOT NULL CHECK(kind BETWEEN 1 AND 8),level INTEGER NOT NULL DEFAULT 0,
      exp INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(character_id,kind))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_transform_cards (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      card_type INTEGER NOT NULL,PRIMARY KEY(character_id,card_type))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_transform_slots (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      kind INTEGER NOT NULL,slot INTEGER NOT NULL,card_type INTEGER NOT NULL,
      PRIMARY KEY(character_id,kind,slot))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_transform_state (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      shape INTEGER NOT NULL DEFAULT 0,expires INTEGER NOT NULL DEFAULT 0)''')

class Transform:
    def __init__(self,game):self.g=game;self.p=game.progression
    def info(self,db,kind):
        if type(kind) is not int or not 1<=kind<=8:raise ValueError('变化术种类无效。')
        row=db.execute('SELECT level,exp FROM native_transform_levels WHERE character_id=? AND kind=?',(self.p.cid,kind)).fetchone()
        level,exp=tuple(row) if row else (0,0)
        return dict(index=kind,Level=level,Exp=exp,MaxExp=RULES['transform_level_exp'],
                    NeedCash=RULES['transform_study_cash']*(level+1),AddExp=RULES['transform_study_exp'])
    def messages(self,kind):
        from native946_progression import lua_data
        with self.p.transaction(write=False) as db:
            info=self.info(db,kind);slots={s:0 for s in range(1,info['Level']//8+1)}
            slots.update({r['slot']:r['card_type'] for r in db.execute('SELECT slot,card_type FROM native_transform_slots WHERE character_id=? AND kind=?',(self.p.cid,kind)) if r['slot'] in slots})
        return (packet(3460,['string'],[lua_data(info)]),packet(3461,['number','string'],[kind,lua_data(slots)]))
    def slot(self,db,kind,slot):
        info=self.info(db,kind)
        if type(slot) is not int or not 1<=slot<=min(9,info['Level']//8):raise ValueError('该卡槽尚未解锁，每8级解锁一个卡槽。')
        return db.execute('SELECT card_type FROM native_transform_slots WHERE character_id=? AND kind=? AND slot=?',(self.p.cid,kind,slot)).fetchone()
    def learn(self,iid):
        with self.p.transaction() as db:
            item=owned(db,self.p.cid,iid);meta=DATA['transform_cards'][str(item['type'])]
            if not meta['kind']:raise ValueError('变身卡种类未定义。')
            if db.execute('SELECT 1 FROM native_transform_cards WHERE character_id=? AND card_type=?',(self.p.cid,item['type'])).fetchone():raise ValueError('已经收录过这张变身卡。')
            consume(db,self.p.cid,iid)
            db.execute('INSERT INTO native_transform_cards VALUES(?,?)',(self.p.cid,item['type']))
        self.g.last_event=dict(action='transform_card_learned',card_type=item['type'])
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.messages(meta['kind']),text_notice('已收录'+meta['card_name']+'，解锁卡槽后即可配置。')))
    def receive(self,mid,r):
        from native946_progression import lua_data
        if self.g.battle is not None:raise ValueError('战斗中不能学习或使用变化术。')
        if mid==3465:
            r.finish()
            return (*self.messages(1),text_notice('七十二变本机规则：每次学习提升1级，现金为1000×下一等级；每8级解锁一槽。变身按卡片标价扣现金，持续1小时。'))
        kind=r.integer()
        if mid==3466:r.finish();return self.messages(kind)
        arg=r.integer()
        if mid==3470:
            r.finish()
            if arg not in (1,2):raise ValueError('学习次数无效。')
            with self.p.transaction() as db:
                info=self.info(db,kind);count=min(1 if arg==1 else 10,RULES['transform_max_level']-info['Level'])
                if count<=0:raise ValueError('该变化术已达到72级。')
                cost=sum(RULES['transform_study_cash']*(info['Level']+i+1) for i in range(count))
                if db.execute('SELECT gold FROM characters WHERE id=?',(self.p.cid,)).fetchone()[0]<cost:raise ValueError('学习变化术所需现金不足。')
                db.execute('UPDATE characters SET gold=gold-? WHERE id=?',(cost,self.p.cid))
                db.execute('INSERT INTO native_transform_levels VALUES(?,?,?,0) ON CONFLICT(character_id,kind) DO UPDATE SET level=excluded.level,exp=0',(self.p.cid,kind,info['Level']+count))
            self.g.last_event=dict(action='transform_studied',kind=kind,count=count,cash=cost)
            return self.g.defer_packets(lambda:(*self.p.attribute_packets(),*self.messages(kind)))
        selected=r.integer() if mid==3468 else None;r.finish()
        with self.p.transaction() as db:
            row=self.slot(db,kind,arg)
            if mid==3467:
                learned={r[0] for r in db.execute('SELECT card_type FROM native_transform_cards WHERE character_id=?',(self.p.cid,))}
                selected_cards={r[0] for r in db.execute('SELECT card_type FROM native_transform_slots WHERE character_id=? AND kind=?',(self.p.cid,kind))}
                pool={int(k):(1 if int(k) in selected_cards else 2 if int(k) in learned else 0) for k,v in DATA['transform_cards'].items() if v['kind']==kind}
                return packet(3462,['number']*2+['string'],[kind,arg,lua_data(pool)])
            if mid==3468:
                meta=DATA['transform_cards'].get(str(selected))
                if not meta or meta['kind']!=kind or not db.execute('SELECT 1 FROM native_transform_cards WHERE character_id=? AND card_type=?',(self.p.cid,selected)).fetchone():raise ValueError('尚未收录该类变身卡。')
                if db.execute('SELECT 1 FROM native_transform_slots WHERE character_id=? AND kind=? AND card_type=? AND slot<>?',(self.p.cid,kind,selected,arg)).fetchone():raise ValueError('这张变身卡已经配置在另一个卡槽中。')
                if row and row[0]!=selected:
                    from native946_mall_bags import pages
                    removable=next((x for x in db.execute('SELECT id,page FROM inventory WHERE character_id=? AND type=47396 ORDER BY id',(self.p.cid,)) if x['page'] in pages(db,self.p.cid)),None)
                    if not removable:raise ValueError('更换已配置的变身卡需要一瓶卸妆水。')
                    consume(db,self.p.cid,removable['id'])
                db.execute('INSERT INTO native_transform_slots VALUES(?,?,?,?) ON CONFLICT(character_id,kind,slot) DO UPDATE SET card_type=excluded.card_type',(self.p.cid,kind,arg,selected))
            elif mid==3469:
                return packet(3463,['number']*2+['string'],[kind,arg,lua_data(DATA['transform_cards'].get(str(row[0]),{}) if row else {})])
            elif mid==3464:
                if not row:raise ValueError('请先为卡槽配置一张变身卡。')
                meta=DATA['transform_cards'][str(row[0])]
                if not meta['shape']:raise ValueError('该卡造型尚未在本机资源中对应，未扣除现金。')
                if db.execute('SELECT gold FROM characters WHERE id=?',(self.p.cid,)).fetchone()[0]<meta['trans_money']:raise ValueError('变化所需现金不足。')
                db.execute('UPDATE characters SET gold=gold-? WHERE id=?',(meta['trans_money'],self.p.cid))
                db.execute('INSERT INTO native_transform_state VALUES(?,?,?) ON CONFLICT(character_id) DO UPDATE SET shape=excluded.shape,expires=excluded.expires',(self.p.cid,meta['shape'],int(time())+RULES['transform_seconds']))
            else:raise ValueError('变化术操作无效。')
        if mid==3464:
            from native946_cosmetics import Cosmetics
            self.g.last_event=dict(action='transform_applied',kind=kind,slot=arg)
            return self.g.defer_packets(lambda:(*self.p.attribute_packets(),*Cosmetics(self.g).visual_packets(),packet(1183,['string'],['dlg_new72trans_main'])))
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.messages(kind)))
