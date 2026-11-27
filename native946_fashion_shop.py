"""Direct native-mall acquisition requested by the user, including frame use."""
from pathlib import Path
from time import time
import json
from native946_codec import packet
from native946_codec import text_notice

DATA=json.loads(Path(__file__).with_name('native946_fashion_shop_data.json').read_text('utf8'))
REQUESTS={5032};WRITES={5032}
PANEL_PRICES={int(frame):price for frame,price in DATA['portrait_panel']['prices'].items()}
JING_LEVELS={int(frame):level for frame,level in DATA['portrait_panel']['jing_levels'].items()}

def owned_frames(db,cid,now=None):
    from native946_role_growth import jing
    if now is None:now=int(time())
    level=jing(db,cid)['level']
    frames={frame:0 for frame,required in JING_LEVELS.items() if level>=required}
    for key,expires in db.execute("SELECT key,expires FROM cosmetic_features WHERE character_id=? AND key LIKE 'iconframe:%' AND (expires=0 OR expires>?)",(cid,now)):
        frame=int(key.partition(':')[2])
        frames.setdefault(frame,expires)
    return frames

def current_frame(db,cid,frames=None):
    row=db.execute("SELECT value FROM cosmetic_features WHERE character_id=? AND key='iconframe_current'",(cid,)).fetchone()
    if not row or not row[0]:return 0
    if frames is None:frames=owned_frames(db,cid)
    return row[0] if row[0] in frames else 0

class FashionShop:
    def __init__(self,game):self.g=game;self.p=game.progression

    def body_type(self):return DATA['male_set' if self.g.character['sex']==1 else 'female_set']

    def open(self,kind=None):
        from native946_cosmetics import Cosmetics
        cs=Cosmetics(self.g);kind=self.body_type() if kind is None else kind
        cs._quantity(kind,1)
        return (packet(1183,['string'],['dlg_fashion_main']),packet(1183,['string'],['dlg_fashion_diancang']),
                *cs.currency_packets(),cs.mall(kind))

    def source(self,index):
        if not 1<=index<=len(DATA['tiers']):raise ValueError('时装购买入口无效。')
        return self.open(self.body_type() if index==1 else DATA['tiers'][index-1])

    def menu(self,nid):
        from native946_npc_services import NpcServices
        from native946_cosmetics import DATA as CATALOG
        kinds=[DATA['male_set'],DATA['female_set'],*DATA['tiers'][1:]]
        choices=[(CATALOG['items'][str(k)]['name']+'（%d玉帛）'%CATALOG['goods'][str(k)]['yubo'],dict(kind='cosmetic',op='fashion_shop_item',item_type=k)) for k in kinds]
        return NpcServices(self.g)._menu(nid,'网罗日月系列可直接在本机异宝阁购买，请选择商品。',choices)

    def frame_packets(self):
        from native946_progression import lua_data
        with self.p.transaction(write=False) as db:
            owned=owned_frames(db,self.p.cid)
            selected=current_frame(db,self.p.cid,owned)
        return packet(695,['number','number'],[57,selected]),packet(694,[],[]),packet(5031,['string'],[lua_data(sorted(owned))])

    def select_frame(self,db,frame,*,allow_purchase=False):
        bought=False
        if frame and frame not in owned_frames(db,self.p.cid):
            price=PANEL_PRICES.get(frame) if allow_purchase else None
            if price is None:raise ValueError('尚未拥有此头像框。')
            from native946_shops import change_liuguang
            change_liuguang(db,self.p.cid,-price)
            db.execute('''INSERT INTO cosmetic_features VALUES(?,?,?,0)
                ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value,expires=0''',
                (self.p.cid,f'iconframe:{frame}',frame))
            bought=True
        db.execute("""INSERT INTO cosmetic_features VALUES(?,'iconframe_current',?,0)
            ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value,expires=0""",(self.p.cid,frame))
        return bought

    def use_frame(self,db,item):
        from native946_mall_uses import consume
        from native946_mall_bags import pages
        frame=DATA['frames'][str(item['type'])]
        if item['page'] not in pages(db,self.p.cid):raise ValueError('请先将头像框放入有效背包。')
        key='iconframe:'+str(frame)
        if frame in owned_frames(db,self.p.cid):raise ValueError('已拥有此头像框，物品未消耗。')
        consume(db,item)
        from native946_fashion import record_use
        record_use(db,self.p.cid,item['type'])
        db.execute('''INSERT INTO cosmetic_features VALUES(?,?,?,0)
            ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value,expires=0''',
            (self.p.cid,key,item['type']))
        self.select_frame(db,frame)
        from native946_fashion import Fashion
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.frame_packets(),*Fashion(self.g).sync(),text_notice('头像框已永久解锁并启用。')))

    def receive(self,mid,r):
        frame=r.integer();r.finish()
        with self.p.transaction() as db:
            self.select_frame(db,frame)
        return self.g.defer_packets(self.frame_packets)
