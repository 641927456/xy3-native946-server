"""Owned native portrait IDs, licenses, slot unlocking and original selectors."""
from time import time,strftime,localtime
from native946_codec import packet
from native946_codec import text_notice
from native946_fashion_shop import FashionShop,PANEL_PRICES,owned_frames

MATERIALS={46258:15,46257:180}
# 946 photo/107x107 compares each four-expression set with its base role art.
ROLE_PORTRAIT_FIRST={
    101:9001, 102:9005, 103:9009, 104:9013,
    111:9017, 112:9021, 113:9025, 114:9029,
    201:9033, 202:9037, 203:9041, 204:9045,
    211:9049, 212:9053, 213:9057, 214:9061,
    301:9065, 302:9069, 303:9073, 304:9077,
    311:9081, 312:9085, 313:9089, 314:9093,
    701:9097, 702:9101, 703:9113, 711:9105, 712:9109, 713:9117,
}
ROLE_PORTRAITS={role:tuple(range(first,first+4)) for role,first in ROLE_PORTRAIT_FIRST.items()}
PORTRAIT_ROLES={icon:role for role,icons in ROLE_PORTRAITS.items() for icon in icons}
FLOWER_BOXES={46258:(55516,65516),46257:(55515,65515,55516,65516)}


def portrait_catalog():
    from native946_mall_uses import DATA

    catalog={}
    for item_type,rule in DATA['rules'].items():
        if rule['op'] in ('portrait','portrait_slot'):
            catalog.setdefault(rule['value'],[]).append(int(item_type))
    return {icon:sorted(items) for icon,items in catalog.items()}


def bag_item(db,cid,types):
    from native946_mall_bags import pages

    bag_pages=pages(db,cid)
    page_params=','.join('?' for _ in bag_pages)
    type_params=','.join('?' for _ in types)
    return db.execute(f'''SELECT i.* FROM inventory i
        LEFT JOIN cosmetic_expiry e ON e.item_id=i.id
        LEFT JOIN native_object_locks l ON l.kind='item' AND l.object_id=i.id
        WHERE i.character_id=? AND i.page IN ({page_params}) AND i.type IN ({type_params})
          AND (coalesce(e.expires,0)=0 OR e.expires>?) AND l.object_id IS NULL
        ORDER BY (coalesce(e.expires,0)=0),e.expires,i.id LIMIT 1''',
        (cid,*bag_pages,*types,int(time()))).fetchone()

def owned(db,cid):
    now=int(time())
    return {r['key']:dict(value=r['value'],expires=r['expires']) for r in db.execute(
        "SELECT key,value,expires FROM cosmetic_features WHERE character_id=? AND key LIKE 'portrait%' AND (expires=0 OR expires>?)",(cid,now))}

def current(db,cid,role_id=None):
    f=owned(db,cid);icon=f.get('portrait_current',{}).get('value',0)
    if not icon or f'portrait:{icon}' not in f:
        return 0
    required=PORTRAIT_ROLES.get(icon)
    if required is not None:
        if role_id is None:
            role_id=db.execute('SELECT role_id FROM characters WHERE id=?',(cid,)).fetchone()[0]
        if required!=role_id:
            return 0
    return icon

class Portraits:
    def __init__(self,game):self.g=game;self.p=game.progression

    def require_role(self,icon):
        role=PORTRAIT_ROLES.get(icon)
        if role is not None and role!=self.g.character['role_id']:
            raise ValueError('该头像仅限对应角色使用。')

    def _activate(self,db,icon,days,source):
        self.require_role(icon)
        key='portrait:'+str(icon)
        previous=db.execute('SELECT expires FROM cosmetic_features WHERE character_id=? AND key=?',
                            (self.p.cid,key)).fetchone()
        if previous is not None and previous['expires']==0:
            raise ValueError('该头像已永久激活。')
        now=int(time())
        base=max(now,previous['expires']) if previous is not None else now
        until=base+days*86400
        for key,value,expires in [(key,source,until),('portrait_current',icon,0)]:
            db.execute('INSERT INTO cosmetic_features VALUES(?,?,?,?) ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value,expires=excluded.expires',(self.p.cid,key,value,expires))

    def use(self,db,item,rule):
        from native946_mall_uses import consume,features
        op=rule['op'];icon=rule.get('value',0)
        if op=='portrait_material':return self.panel()
        self.require_role(icon)
        if op=='portrait':self._activate(db,icon,rule['days'],item['type'])
        elif op=='portrait_slot':
            key='portrait_slot:'+str(icon)
            if key in features(db,self.p.cid):raise ValueError('已经开启此头像格子，物品未消耗。')
            db.execute('INSERT INTO cosmetic_features VALUES(?,?,?,0) ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value,expires=0',(self.p.cid,key,item['type']))
        else:raise ValueError('头像道具规则无效。')
        consume(db,item)
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.sync(),text_notice('头像已更换。' if op=='portrait' else '头像格子已开启，请在原生头像界面使用花颜激活。')))

    def panel(self):
        with self.p.transaction(write=False) as db:
            f=owned(db,self.p.cid)
            frames=owned_frames(db,self.p.cid)
        existing={int(k.split(':')[1]) for k in f if k.startswith(('portrait:','portrait_slot:'))}
        role=self.g.character['role_id']
        role_icons=ROLE_PORTRAITS.get(role,())
        shared=(existing | portrait_catalog().keys())-PORTRAIT_ROLES.keys()-{role}
        icons=(*role_icons,*sorted(shared))
        desc=[]
        for icon in icons:
            license=f.get('portrait:'+str(icon))
            if license is None:
                desc.append('')
            elif license['expires']:
                desc.append(strftime('%Y-%m-%d %H:%M:%S',localtime(license['expires'])))
            else:
                desc.append('永久有效')
        frame_ids=sorted(frames)
        frame_desc=[strftime('%Y-%m-%d %H:%M:%S',localtime(frames[frame])) if frames[frame] else '' for frame in frame_ids]
        return packet(2596,[('array','number'),('array','string'),'number','number',('array','number'),('array','string'),('array','number')],
                      [icons,desc,46258,46257,frame_ids,frame_desc,sorted(PANEL_PRICES)])

    def shop(self,items,message):
        from native946_cosmetics import Cosmetics,SALE_GOODS,sale_type

        offer=next((sale_type(kind) for kind in items if str(sale_type(kind)) in SALE_GOODS),None)
        if offer is None:
            raise ValueError('该头像道具暂未上架。')
        shop=Cosmetics(self.g)
        return self.g.defer_packets(lambda:(text_notice(message),*shop.currency_packets(),shop.mall(offer)))

    def flower_offer(self,kind):
        from native946_cosmetics import Cosmetics

        offer=Cosmetics(self.g).single_buy_offer(FLOWER_BOXES[kind][0],1)
        return (text_notice('购买礼盒后，请右键打开，再用花颜激活头像。'),offer)

    def missing_portrait(self,db,icon,state):
        if icon in PORTRAIT_ROLES or f'portrait_slot:{icon}' in state:
            raise ValueError('请选中此头像，使用普通花颜激活15天或百炼花颜激活180天。')
        items=portrait_catalog().get(icon)
        if items is None:
            raise ValueError('该头像不可用。')
        if bag_item(db,self.p.cid,items) is not None:
            raise ValueError('请先右键使用背包中对应的头像道具。')
        return self.shop(items,'请在异宝阁购买对应的头像道具，购买后右键使用。')

    def sync(self,panel=False,force=True):
        with self.p.transaction(write=False) as db:
            f=owned(db,self.p.cid);icon=current(db,self.p.cid,self.g.character['role_id'])
        old=getattr(self.g,'_portrait_value',0)
        result=[]
        if force or old!=icon:result.extend((packet(695,['number','number'],[56,icon]),packet(694,[],[])))
        if old!=icon:
            service=getattr(self.g,'party_service',None)
            if service is not None:
                team=service.team(self.p.cid)
                if team:service.publish(team)
        if panel:result.append(self.panel())
        # Publish/read operations can report StorageBusy. Keep the old clock
        # until all succeed so an expiry update is retried on the next tick.
        self.g._portrait_value=icon;self.g.character['qicon']=icon
        self.g._portrait_deadline=min((r['expires'] for k,r in f.items() if k.startswith('portrait:') and r['expires']),default=0)
        return tuple(result)

    def tick(self):
        deadline=getattr(self.g,'_portrait_deadline',None)
        if deadline is not None and (deadline==0 or time()<deadline):return ()
        from native946_storage import StorageBusy
        # c_qicon_open_ui opens a panel if the player closed it. An idle expiry
        # must update attributes/team portraits without reopening that window.
        try:return self.sync(force=False)
        except StorageBusy:return ()

    def receive(self,mid,r):
        if mid==2598:r.finish();return self.panel()
        if mid==2599:
            icon,frame=r.integer(),r.integer();r.finish()
            self.require_role(icon)
            wardrobe=FashionShop(self.g)
            if icon==self.g.character['role_id']:icon=0
            with self.p.transaction() as db:
                state=owned(db,self.p.cid)
                if icon and f'portrait:{icon}' not in state:
                    return self.missing_portrait(db,icon,state)
                bought=wardrobe.select_frame(db,frame,allow_purchase=True)
                db.execute("INSERT INTO cosmetic_features VALUES(?,'portrait_current',?,0) ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value",(self.p.cid,icon))
            def reply():
                result=(*wardrobe.frame_packets(),*self.sync())
                if bought:
                    from native946_shops import liuguang_balance,liuguang_packets
                    with self.p.transaction(write=False) as db:
                        balance=liuguang_balance(db,self.p.cid)
                    result+=(*liuguang_packets(balance),text_notice('头像框已购买并启用。'))
                return (*result,self.panel())
            return self.g.defer_packets(reply)
        if mid!=2597:raise ValueError('头像请求无效。')
        icon,kind=r.integer(),r.integer();r.finish()
        if kind not in MATERIALS:raise ValueError('头像激活材料无效。')
        # The native activation request reserves zero for the explicit local
        # purchase buttons. The original default portrait sends its role ID.
        if icon==0:return self.flower_offer(kind)
        self.require_role(icon)
        base=icon==self.g.character['role_id']
        with self.p.transaction() as db:
            from native946_mall_uses import consume
            state=owned(db,self.p.cid)
            license=state.get(f'portrait:{icon}')
            if license is not None and license['expires']==0:
                raise ValueError('该头像已永久激活。')
            if not base and icon not in PORTRAIT_ROLES and f'portrait_slot:{icon}' not in state:
                return self.missing_portrait(db,icon,state)
            item=bag_item(db,self.p.cid,(kind,))
            if item is None:
                if bag_item(db,self.p.cid,FLOWER_BOXES[kind]) is not None:
                    raise ValueError('请先右键打开背包中的花颜礼盒，再激活头像。')
                return self.flower_offer(kind)
            if base:raise ValueError('默认头像永久可用，请先选择要激活的头像。')
            self._activate(db,icon,MATERIALS[kind],kind);consume(db,item)
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.sync(panel=True),text_notice('头像已激活并启用。')))
