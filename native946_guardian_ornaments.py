"""Original guardian ornament shop and refinement, with local material sources."""

import json
from secrets import SystemRandom
from time import monotonic

from native946_codec import packet, text_notice
from native946_equipment import COMPLETION
from native946_jing_gems import DATA
from native946_refinement import Refinement, debit


REFERENCE_WIRE_VERSION=1
ORDINARY_OPEN={3935:3930,4096:4091}
ORDINARY_BUY={3933:3930,4094:4091}
REQUESTS={3962,3963,4694,*ORDINARY_OPEN,*ORDINARY_BUY}
WRITES={3962,4694,*ORDINARY_BUY}
GUARDIAN_BENCH=(2221,4545)
REFINE=DATA['refine']
STONE=int(next(iter(REFINE['NeedMatrials'])))
TOKEN=48017
SHOP={int(k):v for k,v in DATA['yuhua_shop'].items()}
ORDINARY={int(k):v for k,v in DATA['ordinary_shop'].items()}
LOCAL_SUPPLIES={'ornament_stone':(STONE,1,5000),'ornament_token':(TOKEN,20,200000)}
UNLIMITED_COINS=999999999
TRAITS={'cor':'根骨','mag':'灵性','strength':'力量','speed':'敏捷'}


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_ornament_refinement (
      item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
      traits TEXT NOT NULL DEFAULT '{}', pending TEXT NOT NULL DEFAULT '{}'
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_ornament_coins (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      balance INTEGER NOT NULL CHECK(balance>=0)
    )''')


def state(db,iid):
    row=db.execute('SELECT traits,pending FROM native_guardian_ornament_refinement WHERE item_id=?',(iid,)).fetchone()
    return (json.loads(row[0]),json.loads(row[1])) if row else ({},{})


def describe(traits):
    return '#r'.join(f'{TRAITS[name]}+{amount}' for name,amount in traits.items())


def tooltip(db,iid):
    return describe(state(db,iid)[0])


def panel(iid=0,traits=None,pending=None):
    return packet(3961,['number','string','string'],[iid,describe(traits or {}),describe(pending or {})])


def open_shop(game):
    game.ornament_shop_until=('color',monotonic()+600)
    return packet(3993,['string'],['dlg_yuhua_mall'])


def open_material(game,db,item):
    row=Refinement(game).item(db,item['id'])
    return open_shop(game) if row['type']==TOKEN else panel()


def balance(db,cid):
    return UNLIMITED_COINS


def open_ordinary(game,reply):
    game.ornament_shop_until=(reply,monotonic()+600)
    return packet(reply,['number'],[UNLIMITED_COINS])


def npc(game,nid,action):
    from native946_cosmetics import Cosmetics
    from native946_npc_services import NpcServices
    service=NpcServices(game)
    actual=service.validate(nid)
    if (actual['scene_id'],nid)!=GUARDIAN_BENCH:raise ValueError('请到伶俐虫处办理守护饰品。')
    op=action['op']
    if op=='ornaments':return (packet(1183,['string'],['dlg_npc_chat']),open_ordinary(game,3930))
    if op=='ornament_refine':return (packet(1183,['string'],['dlg_npc_chat']),panel())
    if op=='ornament_color':return (packet(1183,['string'],['dlg_npc_chat']),open_shop(game))
    p=game.progression
    if op not in LOCAL_SUPPLIES:raise ValueError('无效的饰品操作。')
    kind,count,cash=LOCAL_SUPPLIES[op]
    with p.transaction() as db:
        debit(db,p.cid,cash)
        Cosmetics(game)._add(db,kind,count,backpack=True)
    return game.defer_packets(lambda:(*p.item_packets(),*p.attribute_packets(),text_notice('本机兑换完成，请查看背包。')))


def receive(game,mid,reader):
    from native946_cosmetics import Cosmetics
    if game.battle:raise ValueError('请先结束战斗再操作守护饰品。')
    if mid in ORDINARY_OPEN:
        reader.finish()
        return open_ordinary(game,ORDINARY_OPEN[mid])
    iid=reader.integer()
    op=reader.integer() if mid==3962 else 0
    reader.finish()
    service=Refinement(game)
    p=service.p
    if mid==4694 or mid in ORDINARY_BUY:
        reply=ORDINARY_BUY.get(mid)
        panel_id,expires=getattr(game,'ornament_shop_until',(None,0))
        if expires<=monotonic() or panel_id!=(reply or 'color'):
            raise ValueError('请先打开对应的守护饰品兑换界面。')
        offer=(ORDINARY if reply else SHOP).get(iid)
        if not offer:raise ValueError('该物品不在当前饰品兑换目录。')
        cost=offer['Cost']
        with p.transaction() as db:
            if reply:
                updated=(packet(reply,['number'],[UNLIMITED_COINS]),)
            else:
                service.take(db,TOKEN,cost)
                updated=()
            Cosmetics(game)._add(db,iid,backpack=True)
        name=COMPLETION['items'][str(iid)]['name']
        return game.defer_packets(lambda:(*p.item_packets(),*updated,text_notice(f'兑换成功，获得{name}。')))
    if mid==3963 and iid==0:return panel()
    with p.transaction(write=mid==3962) as db:
        item=service.item(db,iid,equipped=True)
        if item['amount']!=1 or not COMPLETION['guardians'].get(str(item['type']),{}).get('summon'):
            raise ValueError('请选择自己的专属守护饰品。')
        traits,pending=state(db,iid)
        if mid==3963:return panel(iid,traits,pending)
        if op==1:
            if pending:raise ValueError('请先确认上一次的饰品精炼结果。')
            service.take(db,STONE,REFINE['NeedMatrials'][str(STONE)])
            debit(db,p.cid,REFINE['NeedCash'])
            rng=SystemRandom()
            pending={name:rng.randint(10,25) for name in rng.sample(tuple(TRAITS),2)}
        elif op in (2,3) and pending:
            if op==3:traits=pending
            pending={}
        else:raise ValueError('饰品没有可确认的精炼结果。')
        db.execute('INSERT INTO native_guardian_ornament_refinement VALUES(?,?,?) ON CONFLICT(item_id) DO UPDATE '
                   'SET traits=excluded.traits,pending=excluded.pending',
                   (iid,json.dumps(traits),json.dumps(pending)))
        bound=db.execute('SELECT summon_id FROM native_guardian_equipment WHERE item_id=?',(iid,)).fetchone()

    def render():
        guardian=game.summons.info_packets(bound[0]) if bound and op==3 else ()
        notice=(text_notice('两条词条的种类和数值按本机规则等概率抽取。'),) if op==1 else ()
        return (*p.item_packets(),*p.attribute_packets(),p.item_description(iid),panel(iid,traits,pending),*guardian,*notice)
    return game.defer_packets(render)
