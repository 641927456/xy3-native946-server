"""Owned guardian mall appearance effects from current 946 native config."""
import json
from secrets import choice, randbelow
from time import time
from native946_codec import packet
from native946_codec import text_notice

REQUESTS={3969,3971}
WRITES={3971}
COLOR_FIELDS=('color1','color2','color3')

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS cosmetic_guardian_appearance (
      summon_id INTEGER PRIMARY KEY REFERENCES summons(id) ON DELETE CASCADE,
      character_id INTEGER NOT NULL REFERENCES characters(id),
      color1 INTEGER NOT NULL DEFAULT 0,color2 INTEGER NOT NULL DEFAULT 0,color3 INTEGER NOT NULL DEFAULT 0,
      halo INTEGER NOT NULL DEFAULT 0,foot_halo INTEGER NOT NULL DEFAULT 0,
      color_on INTEGER NOT NULL DEFAULT 1,halo_on INTEGER NOT NULL DEFAULT 1,foot_on INTEGER NOT NULL DEFAULT 1,
      items TEXT NOT NULL DEFAULT '{}')''')

def saved(db,cid,sid):
    row=db.execute('SELECT * FROM cosmetic_guardian_appearance WHERE character_id=? AND summon_id=?',(cid,sid)).fetchone()
    return dict(row) if row else dict(color1=0,color2=0,color3=0,halo=0,foot_halo=0,color_on=1,halo_on=1,foot_on=1,items='{}')

def appearance(db,cid,sid):
    s=saved(db,cid,sid)
    return dict(color1=s['color1'] if s['color_on'] else 0,color2=s['color2'] if s['color_on'] else 0,
                color3=s['color3'] if s['color_on'] else 0,halo=s['halo'] if s['halo_on'] else 0,
                foot_halo=s['foot_halo'] if s['foot_on'] else 0)

def use_rule(kind):
    from native946_mall_uses import DATA

    key = str(kind)
    rule = DATA['rules'].get(key) or DATA['pending_rules'].get(key)
    return rule if rule and rule.get('op') == 'guardian_appearance' else None


def wardrobe_data(state):
    items = json.loads(state['items'])
    data = {}
    for key, mode, value in (
        ('Color', 'color', [state['color1'], state['color2'], state['color3']]),
        ('Halo', 'halo', state['halo']),
        ('FootHalo', 'foot', state['foot_halo']),
    ):
        owned = {int(kind): result for kind, result in items.get(mode, {}).items()}
        selected = next((kind for kind, result in reversed(owned.items()) if result == value), 0)
        data[key] = dict(Origin=value, CurItem=selected if state[mode + '_on'] else 0,
                         ItemList=owned)
    return data


def candidates(config, mode, item_type, pet_type):
    if mode == 'color':
        colors = config['ColorConfig'].get(str(pet_type), ())
        return tuple(dict.fromkeys(tuple(row['ColorData']) for row in colors))
    table = config['HaloConfig'] if mode == 'halo' else config['FootHaloConfig']
    return tuple(dict.fromkeys(table.get(str(item_type), {}).get('HaloDatas', ())))


class GuardianAppearance:
    def __init__(self,game):self.g=game;self.p=game.progression
    def _save(self,db,sid,**values):
        db.execute('INSERT OR IGNORE INTO cosmetic_guardian_appearance(summon_id,character_id) VALUES(?,?)',(sid,self.p.cid))
        db.execute('UPDATE cosmetic_guardian_appearance SET '+','.join(k+'=?' for k in values)+' WHERE summon_id=? AND character_id=?',(*values.values(),sid,self.p.cid))
    def panel(self,sid):
        from native946_progression import lua_data
        with self.p.transaction(write=False) as db:
            if sid:
                ids = [self.g.summons._owned(db, sid)['id']]
            else:
                from native946_guardian_chamber import AVAILABLE
                ids = [row['id'] for row in db.execute(
                    'SELECT id FROM summons WHERE character_id=? AND '+AVAILABLE+' ORDER BY id', (self.p.cid,))]
            if not ids:
                raise ValueError('请先获得一只守护。')
            data = {pet_id: wardrobe_data(saved(db, self.p.cid, pet_id)) for pet_id in ids}
        return packet(3970,['number','string'],[sid,lua_data(data)])
    def use(self, sid, item_id=None, *, item_type=None):
        from native946_inventory_locks import require_unlocked
        from native946_mall_bags import pages as bag_pages
        from native946_mall_uses import DATA, consume

        with self.p.transaction() as db:
            pet = self.g.summons._owned(db, sid)
            pages = bag_pages(db, self.p.cid)
            slots = ','.join('?' for _ in pages)
            query = ('SELECT i.* FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id '
                     'WHERE i.character_id=? AND i.page IN (' + slots + ') AND i.amount>0 '
                     'AND (e.expires IS NULL OR e.expires=0 OR e.expires>?) ')
            args = (self.p.cid, *pages, time())
            if item_id is not None:
                row = db.execute(query + 'AND i.id=?', (*args, item_id)).fetchone()
            else:
                row = db.execute(
                    query + "AND i.type=? AND NOT EXISTS (SELECT 1 FROM native_object_locks l "
                    "WHERE l.kind='item' AND l.object_id=i.id) ORDER BY i.id LIMIT 1",
                    (*args, item_type),
                ).fetchone()
            if row is None:
                raise ValueError('有效背包中没有可用的守护外观道具。')
            require_unlocked(db, self.p.cid, row['id'])
            rule = use_rule(row['type'])
            if rule is None:
                raise ValueError('该道具不能改变守护外观。')
            if rule['mode'] == 'color':
                allowed = DATA['guardian']['ColorItemConfig'].get(str(row['type']), {})
                if str(pet['type']) not in allowed:
                    raise ValueError('这只守护不适用该染色道具。')
            mode=rule['mode'];s=saved(db,self.p.cid,sid);items=json.loads(s['items']);values={}
            notice='守护外观已恢复。'
            if mode=='reset_color':
                if not any(s['color'+str(i)] for i in (1,2,3)):raise ValueError('此守护没有需要清除的染色。')
                values.update(color1=0,color2=0,color3=0,color_on=1);items.pop('color',None)
            elif mode=='reset_halo':
                if not s['halo'] and not s['foot_halo']:raise ValueError('此守护没有需要清除的光环。')
                values.update(halo=0,foot_halo=0,halo_on=1,foot_on=1);items.pop('halo',None);items.pop('foot',None)
            elif rule.get('local_rule')=='uniform_distinct_appearance':
                pool=candidates(DATA['guardian'],mode,row['type'],pet['type'])
                if not pool:
                    raise ValueError('该守护缺少可用的外观配置。')
                if randbelow(100)<rule['rate']:
                    result=choice(pool)
                    items.setdefault(mode,{})[str(row['type'])]=result
                    if mode=='color':
                        values.update(zip(COLOR_FIELDS,result))
                    else:
                        values['halo' if mode=='halo' else 'foot_halo']=result
                    values[mode+'_on']=1
                    notice='守护获得永久外观。成功率沿用原生说明，候选外观按本机规则等概率抽取。'
                else:
                    notice='守护外观未发生变化，气血、法力和忠诚已恢复。'
                stats=self.g.summons.stats(pet)
                db.execute('UPDATE summons SET hp=?,mp=?,honesty=100 WHERE id=? AND character_id=?',
                           (stats['hpmax'],stats['mpmax'],sid,self.p.cid))
            else:
                raise ValueError('该守护外观道具缺少有效结算配置。')
            consume(db,row)
            if values:
                self._save(db,sid,items=json.dumps(items),**values)
            receipt=dict(summon=sid,appearance=values)
            if rule.get('local_rule'):
                receipt['local_rule']=rule['local_rule']
            db.execute('INSERT INTO cosmetic_use_receipts(character_id,item_id,type,rewards,created) VALUES(?,?,?,?,?)',
                       (self.p.cid,row['id'],row['type'],json.dumps(receipt),int(time())))
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.g.summons.sync(),*self.g.sync_summon_scene(),self.panel(sid),text_notice(notice)))
    def receive(self,mid,r):
        if mid==3969:
            sid=r.integer();r.finish();return self.panel(sid)
        op,sid,kind=r.integer(),r.integer(),r.integer();r.finish()
        if op==4:
            return self.use(sid,item_type=kind)
        if op not in (1,2,3):raise ValueError('守护外观操作无效。')
        mode={1:'color',2:'halo',3:'foot'}[op]
        with self.p.transaction() as db:
            self.g.summons._owned(db,sid)
            s=saved(db,self.p.cid,sid);owned=json.loads(s['items']).get(mode,{})
            if kind and str(kind) not in owned:raise ValueError('此守护尚未获得该外观。')
            if not owned:raise ValueError('此守护尚未获得该类别的外观。')
            values={mode+'_on':int(bool(kind))}
            if kind:
                result=owned[str(kind)]
                if mode=='color':values.update(dict(zip(['color1','color2','color3'],result)))
                else:values['halo' if mode=='halo' else 'foot_halo']=result
            self._save(db,sid,**values)
        return self.g.defer_packets(lambda:(*self.g.summons.sync(),*self.g.sync_summon_scene(),self.panel(sid)))
