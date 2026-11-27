"""Realm gem inventory levels and original 946 workshop requests."""

from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import re

from native946_codec import packet, text_notice


REFERENCE_WIRE_VERSION=1
DATA=json.loads(Path(__file__).with_name('native946_special_equipment_data.json').read_text('utf8'))
RULES={int(key):row for key,row in DATA['gems'].items()}
TYPES=tuple(sorted({key//100 for key in RULES}))
ESSENCE=47411
ESSENCE_PACK=47415
TRADEABLE=(*TYPES,ESSENCE)
REQUESTS={3526,3527,3531,3533,3535,4516,4517}
WRITES={3527,3531,4516}
ATTRIBUTE_NAMES={'气血':'最大气血','法力':'最大法力','降低仙法伤害':'仙法减伤',
                 '降低物理伤害':'物理减伤','抗致命几率':'抗致命'}
ATTRIBUTES={}
for _key,_rule in RULES.items():
    _values={}
    for _text in _rule['gem_add'].split('@'):
        _match=re.fullmatch(r'([^=+]+)=?\+([\d.]+)%?',_text)
        if not _match:raise ValueError(f'无效的境宝石词条 {_key}: {_text}')
        _name=ATTRIBUTE_NAMES.get(_match[1],_match[1])
        _value=Decimal(_match[2])
        _values[_name]=int(_value) if _value==int(_value) else _value
    ATTRIBUTES[_key]=_values


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_jing_gem_levels (
      item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
      level INTEGER NOT NULL CHECK(level BETWEEN 2 AND 15)
    )''')


def level(db,iid):
    row=db.execute('SELECT level FROM native_jing_gem_levels WHERE item_id=?',(iid,)).fetchone()
    return row[0] if row else 1


def put(game,db,kind,grade):
    from native946_cosmetics import Cosmetics
    if kind not in TYPES or not 1<=grade<=15:raise ValueError('宝石种类或等级无效。')
    iid=Cosmetics(game)._add(db,kind,backpack=True)
    if grade>1:db.execute('INSERT INTO native_jing_gem_levels VALUES(?,?)',(iid,grade))
    return iid


def bag(db,cid):
    from native946_inventory_locks import records
    from native946_mall_checkout import rows
    locked=records(db,cid)
    return [(row,level(db,row['id'])) for row in rows(db,cid,TYPES) if row['id'] not in locked]


def query(cid,db,kind=3519):
    from native946_progression import lua_data
    owned=bag(db,cid)
    values=({row['id']:grade for row,grade in owned}
            if kind==4515 else dict(Counter(row['type']*100+grade for row,grade in owned)))
    return packet(kind,['string'],[lua_data(values)])


def gem_session(game):
    from native946_gear_services import bench_access
    from native946_refinement import Refinement
    session=getattr(game,'npc_service_session',None)
    if session and session['kind']=='gear_bench':
        bench_access(game,'realm_gems')
    else:Refinement(game).gear_session('jing')


def detail(db,item):
    return RULES[item['type']*100+level(db,item['id'])]['desc']


def return_packets(game,confirmation,message):
    p=game.progression
    def render():
        with p.transaction(write=False) as db:
            panel=(query(p.cid,db),query(p.cid,db,4515))
        return (*p.item_packets(),*p.attribute_packets(),*confirmation,*panel,text_notice(message))
    return game.defer_packets(render)


def receive(game,mid,reader):
    from native946_mall_bags import pages
    from native946_mall_uses import consume
    from native946_refinement import Refinement, debit
    if game.battle:raise ValueError('请先结束战斗再操作境宝石。')
    if mid!=3533:gem_session(game)
    p=game.progression
    service=Refinement(game)
    if mid in (3526,4517):
        reader.finish()
        with p.transaction(write=False) as db:return query(p.cid,db,4515 if mid==4517 else 3519)
    first=reader.integer()
    second=reader.integer() if mid!=3533 else 0
    count=reader.integer() if mid==3527 else 1
    reader.finish()
    if mid==3533:
        with p.transaction(write=False) as db:
            item=service.item(db,first)
            if item['type'] not in TYPES:raise ValueError('请选择有效的境宝石。')
            return packet(3532,['number','number'],[item['type'],level(db,first)])
    if mid==3535:
        with p.transaction(write=False) as db:
            quantity=sum(row['amount'] for row,grade in bag(db,p.cid)
                         if row['type']==first and grade==second)
        return packet(3534,['number'],[quantity])
    if mid in (3527,3531) and (first not in TYPES or not 1<=second<=15):
        raise ValueError('宝石种类或等级无效。')
    confirmation=()
    with p.transaction() as db:
        if mid==3527:
            rule=RULES.get(first*100+second)
            if not rule or rule['change_need_count']<=0:
                raise ValueError('该境宝石等级不开放精粹兑换。')
            if not 1<=count<=len(pages(db,p.cid))*24:raise ValueError('兑换数量超出背包容量。')
            service.take(db,ESSENCE,rule['change_need_count']*count)
            debit(db,p.cid,rule['change_need_money']*count)
            for _ in range(count):put(game,db,first,second)
            confirmation=(packet(3520,[],[]),)
            message=f'兑换境宝石{count}颗。'
        elif mid==3531:
            rule=RULES.get(first*100+second+1)
            if not rule or rule['need_id']!=first*100+second or not 1<=second<15:
                raise ValueError('该境宝石已满级或等级无效。')
            inputs=[row for row,grade in bag(db,p.cid) if row['type']==first and grade==second]
            if len(inputs)<rule['need_num']:raise ValueError('同种同级境宝石不足。')
            debit(db,p.cid,rule['compose_cost'])
            for row in inputs[:rule['need_num']]:consume(db,row,1)
            put(game,db,first,second+1)
            confirmation=(packet(3524,['number'],[1]),)
            message=f'境宝石合成成功，当前{second+1}级。'
        else:
            original=service.item(db,first)
            material=service.item(db,second)
            grade=level(db,first)
            target=material['type']
            if original['type'] not in TYPES or grade<11 or target not in TYPES or target==original['type']:
                raise ValueError('请选择11级以上的境宝石以及另一种境宝石。')
            # The 946 conversion tab requires one target-color gem two levels below the source.
            if level(db,second)!=grade-2:raise ValueError(f'需要一颗{grade-2}级目标种类宝石。')
            consume(db,original,1)
            consume(db,material,1)
            put(game,db,target,grade)
            message=f'境宝石转换成功，保留{grade}级。'
    return return_packets(game,confirmation,message)


def open_pack(game,db,item):
    from native946_cosmetics import Cosmetics
    from native946_mall_uses import consume
    from native946_refinement import Refinement
    service=Refinement(game)
    owned=service.item(db,item['id'])
    consume(db,owned,1)
    Cosmetics(game)._add(db,ESSENCE,100,backpack=True)
    return game.defer_packets(lambda:(*service.p.item_packets(),text_notice('已获得境·宝石精粹100个。')))
