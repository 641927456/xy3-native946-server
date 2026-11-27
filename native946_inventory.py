"""Persistent native inventory controls, including the three equipment pages."""
import sqlite3
from native946_codec import packet
from native946_codec import text_notice
from native946_consignment import REQUESTS as CARD_REQUESTS, receive as card_receive

EQUIP_PAGES=(1,3,4)
EQUIP_SLOTS=tuple(range(1,16))+(20,21,22,23)
WIRE_VERSION=11

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_inventory_state (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      equipment_page INTEGER NOT NULL DEFAULT 1 CHECK(equipment_page IN (1,3,4)))''')
    from native946_inventory_locks import schema as lock_schema
    lock_schema(db)
    from native946_inventory_features import schema as feature_schema
    feature_schema(db)
    from native946_temp_inventory import schema as temporary_inventory_schema
    temporary_inventory_schema(db)

def active_page(db,cid):
    try:row=db.execute('SELECT equipment_page FROM native_inventory_state WHERE character_id=?',(cid,)).fetchone()
    except sqlite3.OperationalError as error:
        if 'no such table: native_inventory_state' not in str(error):raise
        return 1
    return row[0] if row else 1

def page_packet(page,previous=None):
    return packet(777,['number','number'],[page if previous is None else previous,page])

def selector_packet(page):
    index=EQUIP_PAGES.index(page)+1
    return packet(5203,['number','string','number'],[index,'装备'+('一','二','三')[index-1],0])

def activate(progression,page):
    if type(page) is not int or page not in EQUIP_PAGES:raise ValueError('装备套装无效。')
    with progression.transaction() as db:
        before=progression._snapshot(db);old=before['equipment_page']
        for item in before['items']:
            if item['page']==page:progression.validate_equipment(before,item,item['slot'],page)
        db.execute('INSERT INTO native_inventory_state(character_id,equipment_page) VALUES(?,?) '
                   'ON CONFLICT(character_id) DO UPDATE SET equipment_page=excluded.equipment_page',(progression.cid,page))
        after=progression._snapshot(db)
        db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',
                   (min(before['hp'],after['hpmax']),min(before['mp'],after['mpmax']),progression.cid))
    return old

REQUESTS={798,799,2610,3758,3465,3575,4318,5201,5202,5205,5206}|CARD_REQUESTS

class InventoryButtons:
    def __init__(self,game):self.g=game;self.p=game.progression
    def selector(self):
        from native946_equipment_plans import EquipmentPlans
        return EquipmentPlans(self.g).open()
    def select(self,page):
        if self.g.battle is not None:raise ValueError('战斗中不能切换装备。')
        from native946_cosmetics import Cosmetics
        old=activate(self.p,page)
        self.g.dialog_open=False;self.g.dialog_actions=[];self.g.dialog_npc=None
        self.g.last_event=dict(action='equipment_page_changed',from_page=old,to_page=page)
        return self.g.defer_packets(lambda:(packet(1183,['string'],['dlg_npc_chat']),*self.p.sync(),*Cosmetics(self.g).visual_packets()))
    def receive(self,mid,r):
        from native946_inventory_features import REQUESTS as FEATURE_REQUESTS,receive as feature_receive
        if mid in FEATURE_REQUESTS:return feature_receive(self.g,mid,r)
        if mid in CARD_REQUESTS:return card_receive(self.g,mid,r)
        if mid==2610:
            r.finish();return packet(2609,[('array','number')],[[]])
        if mid==3758:
            iid=r.integer();count=r.take(1)[0]
            if count>32:raise ValueError('请求的装备额外信息过多。')
            keys=[r.string().decode('ascii') for _ in range(count)];r.finish()
            if any(len(k)>64 or not k.replace('_','').isalnum() for k in keys):raise ValueError('装备额外信息字段无效。')
            state=self.p.snapshot();item=next((i for i in state['items'] if i['id']==iid),None)
            if not item:raise ValueError('物品不属于当前角色。')
            from native946_progression import lua_data
            with self.p.transaction(write=False) as db:
                current=db.execute('SELECT set_index FROM native_equipment_active_attributes WHERE item_id=? AND character_id=?',(iid,self.p.cid)).fetchone()
                alternatives=db.execute('SELECT count(*) FROM native_equipment_attribute_sets WHERE item_id=? AND character_id=?',(iid,self.p.cid)).fetchone()[0]
                from native946_refinement import item_extras
                value=item_extras(db,self.p.cid,iid)
                if 'JingHoleInfo' in keys:
                    from native946_gear_sockets import extras
                    value.update(extras(db,iid))
            value['JingPlanIndex']=current[0] if current else 1 if alternatives==2 else 0
            return packet(3756,['number','string'],[iid,lua_data({k:value[k] for k in keys if k in value})])
        if mid in (798,799):
            iid=r.integer();r.finish()
            if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
            if mid==799:
                if not any(i['id']==iid for i in self.p.snapshot()['items']):raise ValueError('物品不属于当前角色。')
                return self.p.item_packets()
            from native946_mall_bags import pages
            from native946_inventory_locks import require_unlocked
            from native946_temp_inventory import PAGE, refill
            with self.p.transaction() as db:
                row=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',(iid,self.p.cid)).fetchone()
                if not row or row['page'] not in (*pages(db,self.p.cid,True),PAGE,*range(7000,7004)):raise ValueError('只能丢弃当前角色背包内的物品。')
                require_unlocked(db,self.p.cid,iid)
                db.execute('DELETE FROM inventory WHERE id=? AND character_id=?',(iid,self.p.cid))
                if row['page']==PAGE:refill(db,self.p)
            self.g.last_event={'action':'item_discarded','item_id':iid}
            return self.g.defer_packets(self.p.item_packets)
        raise ValueError('道具栏操作无效。')

from native946_inventory_features import REQUESTS as FEATURE_REQUESTS
REQUESTS.update(FEATURE_REQUESTS)
