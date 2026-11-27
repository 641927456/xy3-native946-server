"""Native three-plan editor; references preserve the owned item identity."""
import json
from copy import deepcopy
from native946_codec import packet
from native946_inventory import EQUIP_PAGES,EQUIP_SLOTS,active_page

PAGES=tuple(range(7100,7104))
REQUESTS={5201,5202,5205,5206}
WRITES=REQUESTS
WIRE_VERSION=1

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_equipment_plans (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      plan_id INTEGER NOT NULL CHECK(plan_id BETWEEN 1 AND 3),name TEXT NOT NULL,
      slots TEXT NOT NULL DEFAULT '{}',attrs TEXT NOT NULL DEFAULT '{}',
      PRIMARY KEY(character_id,plan_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_equipment_plan_state (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      active INTEGER NOT NULL DEFAULT 0 CHECK(active BETWEEN 0 AND 3))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_equipment_attribute_sets (
      item_id INTEGER NOT NULL REFERENCES inventory(id) ON DELETE CASCADE,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      set_index INTEGER NOT NULL CHECK(set_index IN (1,2)),properties TEXT NOT NULL,
      PRIMARY KEY(item_id,set_index))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_equipment_active_attributes (
      item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      set_index INTEGER NOT NULL CHECK(set_index IN (1,2)))''')

def records(db,cid):
    owned={r[0] for r in db.execute('SELECT id FROM inventory WHERE character_id=?',(cid,))}
    return [dict(PlanId=r['plan_id'],Name=r['name'],
                 Slots={int(k):dict(EquipId=v if v in owned else 0,JingMorePlan=json.loads(r['attrs']).get(str(v),1)) for k,v in json.loads(r['slots']).items()},
                 JingSuits=['','']) for r in db.execute('SELECT * FROM native_equipment_plans WHERE character_id=? ORDER BY plan_id',(cid,))]

def active_packet(db,cid):
    row=db.execute('SELECT active FROM native_equipment_plan_state WHERE character_id=?',(cid,)).fetchone()
    plans=records(db,cid) if row else []
    current=next((p for p in plans if p['PlanId']==row[0]),None) if row else None
    if current:
        actual={r['slot']:r['id'] for r in db.execute('SELECT id,slot FROM inventory WHERE character_id=? AND page=?',(cid,active_page(db,cid)))}
        expected={s:v['EquipId'] for s,v in current['Slots'].items() if v['EquipId']}
        return packet(5203,['number','string','number'],[current['PlanId'],current['Name'],int(actual!=expected)])
    page=active_page(db,cid);index=EQUIP_PAGES.index(page)+1
    return packet(5203,['number','string','number'],[index,'装备'+('一','二','三')[index-1],0])

class EquipmentPlans:
    def __init__(self,game):self.g=game;self.p=game.progression
    def initialize(self,db):
        for index,page in enumerate(EQUIP_PAGES,1):
            slots={s:0 for s in EQUIP_SLOTS}
            slots.update({r['slot']:r['id'] for r in db.execute('SELECT id,slot FROM inventory WHERE character_id=? AND page=?',(self.p.cid,page))})
            db.execute('INSERT OR IGNORE INTO native_equipment_plans(character_id,plan_id,name,slots) VALUES(?,?,?,?)',
                       (self.p.cid,index,'方案'+('一','二','三')[index-1],json.dumps(slots)))
        db.execute('INSERT OR IGNORE INTO native_equipment_plan_state(character_id,active) VALUES(?,?)',
                   (self.p.cid,EQUIP_PAGES.index(active_page(db,self.p.cid))+1))
    def get(self,db,index):
        if type(index) is not int or index not in (1,2,3):raise ValueError('装备方案编号无效。')
        return next(v for v in records(db,self.p.cid) if v['PlanId']==index)
    def open(self):
        from native946_progression import lua_data
        with self.p.transaction() as db:
            self.initialize(db);plans=records(db,self.p.cid)
            current=db.execute('SELECT active FROM native_equipment_plan_state WHERE character_id=?',(self.p.cid,)).fetchone()[0]
        return (packet(5200,['number','string'],[current,lua_data(plans)]),)
    def eligible(self,db,iid,slot=None):
        from native946_mall_bags import pages
        item=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',(iid,self.p.cid)).fetchone()
        if not item or item['page'] not in (*EQUIP_PAGES,*pages(db,self.p.cid,True),*PAGES):raise ValueError('方案中的装备已不属于当前角色或不在可用位置。')
        from native946_inventory_features import DATA
        native_slot=DATA['equip_slots'].get(str(item['type']))
        if native_slot not in EQUIP_SLOTS or (slot is not None and slot!=native_slot):raise ValueError('装备方案部位不匹配。')
        if item['amount']!=1:raise ValueError('堆叠物品不能存入装备方案。')
        return dict(item)
    def arrange(self,db,desired=None):
        """Plan a complete layout before moving; staging stays in this transaction."""
        from native946_mall_bags import pages
        plans=records(db,self.p.cid);cid=self.p.cid;page=active_page(db,cid)
        refs={v['EquipId'] for plan in plans for v in plan['Slots'].values() if v['EquipId']}
        items={r['id']:dict(r) for r in db.execute('SELECT * FROM inventory WHERE character_id=?',(cid,))}
        dest={} if desired is None else {v['EquipId']:(page,s) for s,v in desired['Slots'].items() if v['EquipId']}
        for iid in dest:self.eligible(db,iid)
        move=set(dest)
        for iid,item in items.items():
            if item['page'] in PAGES or (iid in refs and item['page']!=page) or (desired is not None and item['page']==page):move.add(iid)
        occupied={(v['page'],v['slot']) for i,v in items.items() if i not in move}|set(dest.values())
        for iid in sorted(move-set(dest)):
            candidates=PAGES if iid in refs else pages(db,cid)
            position=next(((p,s) for p in candidates for s in range(1,25) if (p,s) not in occupied),None)
            if position is None:raise ValueError('背包或装备方案存储区空间不足，请先整理物品。')
            dest[iid]=position;occupied.add(position)
        if desired is not None:
            final=self.p._snapshot(db)
            final['items']=[dict(v,page=dest[i][0],slot=dest[i][1]) if i in dest else dict(v) for i,v in items.items()]
            for s,v in desired['Slots'].items():
                if v['EquipId']:self.p.validate_equipment(final,items[v['EquipId']],s,page)
        changed={i:pos for i,pos in dest.items() if pos!=(items[i]['page'],items[i]['slot'])}
        for iid in changed:db.execute('UPDATE inventory SET page=-5201,slot=? WHERE id=? AND character_id=?',(iid,iid,cid))
        for iid,(p,s) in changed.items():db.execute('UPDATE inventory SET page=?,slot=? WHERE id=? AND character_id=?',(p,s,iid,cid))
    def save(self,data):
        allowed={'PlanId','Name','Slots','JingSuits'}
        if not data or set(data)-allowed:raise ValueError('装备方案字段无效。')
        index=data.get('PlanId')
        with self.p.transaction() as db:
            self.initialize(db);plan=self.get(db,index)
            if 'Name' in data:
                name=data['Name']
                if not isinstance(name,str) or not name.strip() or len(name.encode('gb18030'))>24 or any(ord(c)<32 or c in '#\\' for c in name):raise ValueError('方案名称应为1至12个汉字，不可包含控制符。')
                plan['Name']=name.strip()
            if 'Slots' in data:
                slots=data['Slots']
                if not isinstance(slots,dict) or set(slots)-set(EQUIP_SLOTS):raise ValueError('装备方案槽位无效。')
                seen=set()
                for s in EQUIP_SLOTS:
                    value=slots.get(s,{'EquipId':0})
                    if not isinstance(value,dict) or set(value)-{'EquipId','JingMorePlan'}:raise ValueError('装备槽位数据无效。')
                    iid=value.get('EquipId',0)
                    if type(iid) is not int or iid<0 or iid in seen:raise ValueError('方案存在重复或无效装备。')
                    if iid:self.eligible(db,iid,s);seen.add(iid)
                    plan['Slots'][s]={'EquipId':iid}
            db.execute('UPDATE native_equipment_plans SET name=?,slots=? WHERE character_id=? AND plan_id=?',
                       (plan['Name'],json.dumps({s:v['EquipId'] for s,v in plan['Slots'].items()}),self.p.cid,index))
            self.arrange(db)
        self.g.last_event=dict(action='equipment_plan_saved',plan_id=index)
        return self.refresh(index)
    def refresh(self,index):
        from native946_progression import lua_data
        def render():
            with self.p.transaction(write=False) as db:plan=self.get(db,index)
            return (*self.p.sync(),packet(5199,['string'],[lua_data(plan)]),packet(5230,['number','string'],[index,plan['Name']]))
        return self.g.defer_packets(render)
    def apply(self,index,confirm=False):
        from native946_progression import lua_data
        from native946_cosmetics import Cosmetics
        with self.p.transaction() as db:
            self.initialize(db);plan=self.get(db,index)
            for s,v in plan['Slots'].items():
                if v['EquipId']:self.eligible(db,v['EquipId'],s)
            if not confirm:
                self.g.equipment_plan_confirm=deepcopy(plan)
                names=[]
                for value in plan['Slots'].values():
                    if value['EquipId'] and value.get('JingMorePlan',1)!=self.attribute_index(db,value['EquipId']):
                        from native946_progression import DATA as ITEMS
                        names.append(ITEMS['items'][str(self.eligible(db,value['EquipId'])['type'])]['name'])
                return packet(5197,['number']*3+[('array','string')],[index,0,len(names),names])
            if getattr(self.g,'equipment_plan_confirm',None)!=plan:raise ValueError('装备方案已变更，请重新点击应用。')
            before=self.p._snapshot(db)
            for value in plan['Slots'].values():
                iid=value['EquipId'];target=value.get('JingMorePlan',1)
                if not iid or target==self.attribute_index(db,iid):continue
                row=db.execute('SELECT properties FROM native_equipment_attribute_sets WHERE item_id=? AND character_id=? AND set_index=?',(iid,self.p.cid,target)).fetchone()
                if not row:raise ValueError('装备属性预设已失效，请重新选择。')
                updated=db.execute('UPDATE native_equipment SET properties=? WHERE item_id=? AND character_id=?',(row[0],iid,self.p.cid)).rowcount
                if not updated:raise ValueError('装备没有可切换的属性记录。')
                db.execute('INSERT INTO native_equipment_active_attributes VALUES(?,?,?) ON CONFLICT(item_id) DO UPDATE SET set_index=excluded.set_index',(iid,self.p.cid,target))
            self.arrange(db,plan)
            db.execute('UPDATE native_equipment_plan_state SET active=? WHERE character_id=?',(index,self.p.cid))
            after=self.p._snapshot(db)
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',(min(before['hp'],after['hpmax']),min(before['mp'],after['mpmax']),self.p.cid))
        self.g.equipment_plan_confirm=None
        self.g.last_event=dict(action='equipment_plan_applied',plan_id=index)
        return self.g.defer_packets(lambda:(*self.p.sync(),*Cosmetics(self.g).visual_packets(),packet(5200,['number','string'],[index,lua_data(self.list_plans())])))
    def list_plans(self):
        with self.p.transaction(write=False) as db:return records(db,self.p.cid)
    def attribute_index(self,db,iid):
        row=db.execute('SELECT set_index FROM native_equipment_active_attributes WHERE item_id=? AND character_id=?',(iid,self.p.cid)).fetchone()
        return row[0] if row else 1
    def attribute_choices(self,db,index):
        from native946_equipment import records as instances,description
        plan=self.get(db,index);equipment=instances(db,self.p.cid);out=[]
        for value in plan['Slots'].values():
            iid=value['EquipId']
            sets={r['set_index']:json.loads(r['properties']) for r in db.execute('SELECT set_index,properties FROM native_equipment_attribute_sets WHERE item_id=? AND character_id=?',(iid,self.p.cid))}
            if set(sets)!={1,2} or iid not in equipment:continue
            instance=equipment[iid]
            out.append(dict(EquipId=iid,JingMorePlan=value.get('JingMorePlan',1),AttrInfo=[description(dict(instance,properties=sets[i])) for i in (1,2)]))
        return out
    def receive(self,mid,r):
        from native946_lua_values import table
        from native946_progression import lua_data
        if self.g.battle is not None:raise ValueError('战斗中不能编辑或应用装备方案。')
        if mid==5202:
            data=table(r.string().decode('gb18030'));r.finish();return self.save(data)
        if mid==5205:
            index=r.integer();ids=[r.integer() for _ in range(r.take(1)[0])];r.finish()
            if len(ids)>19 or len(set(ids))!=len(ids):raise ValueError('装备方案列表无效。')
            with self.p.transaction() as db:
                self.initialize(db);self.get(db,index)
                for iid in ids:
                    if iid:self.eligible(db,iid)
            # No fabricated set bonuses: only bonuses implemented by the equipment owner.
            return packet(5204,['number','string'],[index,lua_data(['',''])])
        if mid==5206:
            index=r.integer();attrs=table(r.string().decode('gb18030'),limit=3000,nodes=64);r.finish()
            with self.p.transaction() as db:
                self.initialize(db);plan=self.get(db,index);owned={v['EquipId'] for v in plan['Slots'].values()}
                current=db.execute('SELECT attrs FROM native_equipment_plans WHERE character_id=? AND plan_id=?',(self.p.cid,index)).fetchone()
                saved=json.loads(current[0]);available={v['EquipId'] for v in self.attribute_choices(db,index)}
                for iid,target in attrs.items():
                    if type(iid) is not int or iid not in owned or type(target) is not int or target not in (1,2):raise ValueError('装备属性方案无效。')
                    if iid not in available:raise ValueError('该装备没有已保存的左右两套境属性。')
                    saved[str(iid)]=target
                db.execute('UPDATE native_equipment_plans SET attrs=? WHERE character_id=? AND plan_id=?',(json.dumps(saved),self.p.cid,index))
            return self.refresh(index)
        op,arg=r.integer(),r.integer();r.finish()
        if op==1:return self.open()
        if op in (2,3):return self.apply(arg,confirm=op==3)
        with self.p.transaction() as db:
            self.initialize(db)
            if op==5:
                plan=self.get(db,arg)
                db.execute("UPDATE native_equipment_plans SET slots=?,attrs='{}' WHERE character_id=? AND plan_id=?",(json.dumps({s:0 for s in EQUIP_SLOTS}),self.p.cid,arg));self.arrange(db)
            elif op==6:
                item=self.eligible(db,arg)
                if item['page'] not in PAGES:raise ValueError('该装备不在方案存储区。')
                for plan in records(db,self.p.cid):
                    slots={s:(0 if v['EquipId']==arg else v['EquipId']) for s,v in plan['Slots'].items()}
                    db.execute('UPDATE native_equipment_plans SET slots=? WHERE character_id=? AND plan_id=?',(json.dumps(slots),self.p.cid,plan['PlanId']))
                self.arrange(db)
            elif op==7:
                self.get(db,arg)
                return packet(5198,['number','string'],[arg,lua_data(self.attribute_choices(db,arg))])
            else:raise ValueError('装备方案操作无效。')
        if op==5:return self.refresh(arg)
        return self.g.defer_packets(lambda:(*self.p.sync(),*self.open()))
