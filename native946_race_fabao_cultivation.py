"""Race artifact cultivation backed by the original server's outcome tables."""
from collections import Counter
import json
from pathlib import Path
from secrets import randbelow
from time import time

from native946_codec import packet, text_notice


DATA=json.loads(Path(__file__).with_name('native946_race_fabao_cultivation_data.json').read_text('utf8'))
REQUESTS={4459,4462,4463,4464,4466,4470}
WRITES=REQUESTS-{4470}
TIME_BASE=1072886400  # TIME_TOOL.GetTimeBase in 946, UTC+8 natural-day origin.


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_race_fabao_roll_weights (
        action TEXT NOT NULL CHECK(action IN ('activate','reverse','forge','keep')),
        race INTEGER NOT NULL, tier INTEGER NOT NULL CHECK(tier BETWEEN 1 AND 10),
        attr_index INTEGER NOT NULL, attr_value INTEGER NOT NULL CHECK(attr_value>=0),
        weight INTEGER NOT NULL CHECK(weight>0),
        PRIMARY KEY(action,race,tier,attr_index,attr_value))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_race_fabao_forge_choices (
        character_id INTEGER PRIMARY KEY REFERENCES native_race_fabao(character_id) ON DELETE CASCADE,
        tier INTEGER NOT NULL, old_index INTEGER NOT NULL, old_value INTEGER NOT NULL,
        new_index INTEGER NOT NULL, new_value INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_race_fabao_stove (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        last_day INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_race_fabao_stove_outcomes (
        offering TEXT NOT NULL, rewards TEXT NOT NULL,
        weight INTEGER NOT NULL CHECK(weight>0), PRIMARY KEY(offering,rewards))''')


def _draw(rows):
    selector=randbelow(sum(row['weight'] for row in rows))
    for row in rows:
        selector-=row['weight']
        if selector<0:return row


def _available_rolls(db,action,race,tier,old):
    candidates=[]
    for row in db.execute('''SELECT * FROM native_race_fabao_roll_weights
        WHERE action=? AND race=? AND tier=? ORDER BY attr_index,attr_value''',(action,race,tier)):
        attr=DATA['attributes'].get(str(row['attr_index']))
        if not attr or race not in attr['RaceLimit'] or row['attr_value']>attr['AttrValue']:
            raise ValueError('种族法宝词条资料有误。')
        side=attr['YinYang']
        if action=='reverse' and side==old['yinyang']:continue
        if action in ('forge','keep'):
            if side!=old['yinyang']:continue
            # 3.0.946 excludes the exact previous index/value pair.
            if (row['attr_index'],row['attr_value'])==(old['attr_index'],old['attr_value']):continue
        candidates.append(dict(row,yinyang=side))
    if not candidates:raise ValueError('当前操作尚缺原版抽取权重，未扣除材料或现金。')
    return candidates


def _write_attr(db,cid,tier,index,value,side):
    db.execute('''INSERT INTO native_race_fabao_attributes(character_id,tier,yinyang,attr_index,attr_value)
        VALUES(?,?,?,?,?) ON CONFLICT(character_id,tier) DO UPDATE SET
        yinyang=excluded.yinyang,attr_index=excluded.attr_index,attr_value=excluded.attr_value''',
        (cid,tier,side,index,value))


def _result(mode,tier,old,new):
    return packet(4465,['number']*6,[mode,tier,old['attr_index'],old['attr_value'],new['attr_index'],new['attr_value']])


def pending_packet(db,cid):
    row=db.execute('SELECT * FROM native_race_fabao_forge_choices WHERE character_id=?',(cid,)).fetchone()
    if row is None:return ()
    return (packet(4465,['number']*6,[1,row['tier'],row['old_index'],row['old_value'],row['new_index'],row['new_value']]),)


def _cultivate(db,game,mid,args,saved,attrs):
    from native946_race_fabao import YINYANG_STONE, _consume_material
    from native946_refinement import debit

    cid=game.character['id']
    pending=db.execute('SELECT * FROM native_race_fabao_forge_choices WHERE character_id=?',(cid,)).fetchone()
    if mid==4464:
        choice=args[0]
        if choice not in (1,2):raise ValueError('洗炼保留选项无效。')
        if pending is None:raise ValueError('当前没有待确认的洗炼结果。')
        current=next((row for row in attrs if row['tier']==pending['tier']),None)
        if current is None or (current['attr_index'],current['attr_value'])!=(pending['old_index'],pending['old_value']):
            raise ValueError('当前层阶的属性已经变化。')
        if choice==2:
            _write_attr(db,cid,pending['tier'],pending['new_index'],pending['new_value'],current['yinyang'])
        db.execute('DELETE FROM native_race_fabao_forge_choices WHERE character_id=?',(cid,))
        return ()

    tier=saved['tier'] if mid==4459 else args[0]
    mode=args[1] if mid==4463 else 0
    if not 1<=tier<=saved['tier'] or mode not in (0,1):raise ValueError('层阶或洗炼模式无效。')
    current=next((row for row in attrs if row['tier']==tier),None)
    if (mid==4459)==(current is not None):raise ValueError('当前层阶的激活状态已变化。')
    if pending is not None and not (mid==4463 and mode==1 and pending['tier']==tier):
        raise ValueError('请先选择保留当前或新的洗炼结果。')
    recipe=DATA['activation'][tier-1]
    action='activate' if mid==4459 else 'reverse' if mid==4462 else 'keep' if mode else 'forge'
    chosen=_draw(_available_rolls(db,action,game.character['race'],tier,current))
    multiplier=2 if mode else 1
    _consume_material(db,cid,47935 if mid==4463 else YINYANG_STONE,
                      multiplier*recipe['ForgeCost' if mid==4463 else 'ActiveCost'])
    debit(db,cid,multiplier*recipe['ForgeCash' if mid==4463 else 'ActiveCash'])
    db.execute('INSERT OR IGNORE INTO native_race_fabao(character_id) VALUES(?)',(cid,))
    if mode:
        db.execute('''INSERT INTO native_race_fabao_forge_choices VALUES(?,?,?,?,?,?) ON CONFLICT(character_id)
            DO UPDATE SET tier=excluded.tier,old_index=excluded.old_index,old_value=excluded.old_value,
                          new_index=excluded.new_index,new_value=excluded.new_value''',
            (cid,tier,current['attr_index'],current['attr_value'],chosen['attr_index'],chosen['attr_value']))
    else:
        _write_attr(db,cid,tier,chosen['attr_index'],chosen['attr_value'],chosen['yinyang'])
    return (_result(mode,tier,current,chosen),) if mid==4463 else ()


def _stove_ui(db,cid):
    row=db.execute('SELECT last_day FROM native_race_fabao_stove WHERE character_id=?',(cid,)).fetchone()
    return packet(4469,['number'],[row[0] if row else 0])


def _stove(db,game,offer):
    from native946_cosmetics import Cosmetics
    from native946_inventory_locks import records
    from native946_mall_checkout import rows
    from native946_mall_uses import consume

    if (not offer or any(type(i) is not int or i<=0 or type(n) is not int or not 1<=n<=8 for i,n in offer.items())
            or sum(offer.values())>8):
        raise ValueError('请放入 1 至 8 件供品。')
    cid=game.character['id'];day=(int(time())-TIME_BASE)//86400+1
    old=db.execute('SELECT last_day FROM native_race_fabao_stove WHERE character_id=?',(cid,)).fetchone()
    if old and day<old[0]+DATA['stove_cooldown_days']:raise ValueError('不空醍醐处于冷却中。')
    locked=records(db,cid)
    owned={r['id']:r for r in rows(db,cid,tuple(DATA['stove_items'])) if r['id'] not in locked}
    offerings=Counter()
    for item_id,amount in offer.items():
        row=owned.get(item_id)
        if row is None or row['amount']<amount:raise ValueError('供品类型或数量已变化。')
        offerings[row['type']]+=amount
    signature=','.join(f'{kind}:{n}' for kind,n in sorted(offerings.items()))
    outcomes=db.execute('SELECT * FROM native_race_fabao_stove_outcomes WHERE offering=? ORDER BY rewards',
                        (signature,)).fetchall()
    if not outcomes:raise ValueError('该供品组合尚缺原版产出数据，未消耗供品。')
    picked=json.loads(_draw(outcomes)['rewards'])
    if (not isinstance(picked,list) or not picked or any(not isinstance(row,list) or len(row)!=2
            or type(row[0]) is not int or row[0] not in DATA['stove_reward_types']
            or type(row[1]) is not int or not 1<=row[1]<=10000 for row in picked)):
        raise ValueError('不空醍醐产出资料有误。')
    for item_id,amount in offer.items():consume(db,owned[item_id],amount)
    cosmetics=Cosmetics(game)
    for kind,count in picked:cosmetics._add(db,kind,count,backpack=True)
    db.execute('INSERT INTO native_race_fabao_stove VALUES(?,?) ON CONFLICT(character_id) DO UPDATE SET last_day=excluded.last_day',
               (cid,day))
    return (_stove_ui(db,cid),text_notice('供品已投入，产出已发到物品栏。'))


def receive(game,mid,reader):
    from native946_lua_values import table
    from native946_race_fabao import panel, state
    from native946_role_systems import day

    args=[reader.integer() for _ in range({4462:1,4463:2,4464:1}.get(mid,0))]
    offer=table(reader.string().decode('gb18030'),limit=240,nodes=32) if mid==4466 else None
    reader.finish()
    if game.battle:raise ValueError('请先结束战斗。')
    p=game.progression
    with p.transaction(write=mid in WRITES) as db:
        saved,attrs=state(db,p.cid)
        if mid==4470:return _stove_ui(db,p.cid)
        if mid==4466:reply=_stove(db,game,offer)
        else:
            effects=_cultivate(db,game,mid,args,saved,attrs)
            saved,attrs=state(db,p.cid)
            reply=(*effects,panel(saved,attrs,day())) if mid==4463 and args[1] else (panel(saved,attrs,day()),*effects)
    return game.defer_packets(lambda:(*p.item_packets(),*p.attribute_packets(),*reply))
