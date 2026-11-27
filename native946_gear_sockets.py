"""Owned equipment sockets for the 946 ordinary, jewelry and realm panels."""

from re import search
from secrets import randbelow

from native946_codec import packet, text_notice
from native946_equipment import COMPLETION, records
from native946_jing_gems import ATTRIBUTES, DATA as JING, RULES, gem_session, level, put
from native946_refinement import Refinement, debit


REFERENCE_WIRE_VERSION=1
REQUESTS={256,257,263,264,265,270,271,3528,3529,3530,3821}
WRITES={256,257,264,265,271,3528,3530,3821}
PARTS={'护身符':10,'腰带':6,'右手镯':9,'护心镜':7,'左手镯':8}
ALIASES={'气血':'最大气血','法力':'最大法力','降低仙法伤害':'仙法减伤','降低物理伤害':'物理减伤'}


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_gear_sockets (
      item_id INTEGER NOT NULL REFERENCES inventory(id) ON DELETE CASCADE,
      position INTEGER NOT NULL,category TEXT NOT NULL,gem INTEGER NOT NULL DEFAULT 0,
      property TEXT NOT NULL DEFAULT '',amount INTEGER NOT NULL DEFAULT 0,
      gem_level INTEGER NOT NULL DEFAULT 1 CHECK(gem_level BETWEEN 1 AND 15),
      PRIMARY KEY(item_id,position)
    )''')
    if 'gem_level' not in {r[1] for r in db.execute('PRAGMA table_info(native_gear_sockets)')}:
        db.execute('ALTER TABLE native_gear_sockets ADD COLUMN gem_level INTEGER NOT NULL DEFAULT 1 '
                   'CHECK(gem_level BETWEEN 1 AND 15)')


def rows(db,iid):
    return {r['position']:r for r in db.execute('SELECT * FROM native_gear_sockets WHERE item_id=?',(iid,))}


def properties(db,iid):
    values={}
    for r in rows(db,iid).values():
        for name,amount in affixes(r).items():values[name]=values.get(name,0)+amount
    return values


def affixes(row):
    if not row['gem']:return {}
    if row['category']=='jing':
        return ATTRIBUTES.get(row['gem']*100+row['gem_level'],{})
    return {row['property']:row['amount']}


def worn_properties(db,cid,page):
    values={}
    for r in db.execute('SELECT s.* FROM native_gear_sockets s '
                        'JOIN inventory i ON i.id=s.item_id WHERE i.character_id=? AND i.page=? AND s.gem>0',(cid,page)):
        attrs=values.setdefault(r['item_id'],{})
        for name,amount in affixes(r).items():attrs[name]=attrs.get(name,0)+amount
    return values


def extras(db,iid):
    sockets=[r for r in rows(db,iid).values() if r['category']=='jing']
    opened=bool(sockets)
    return {'JingHoleInfo':{'JingGemHole':int(opened),'SocketString':
                            [f"第{r['position']}孔：{str(r['gem_level'])+'级'+COMPLETION['items'][str(r['gem'])]['name'] if r['gem'] else '空'}"
                             for r in sockets]}}


def existing(db,iid):
    return bool(db.execute('SELECT 1 FROM native_gear_sockets WHERE item_id=?',(iid,)).fetchone())


def preserve(db,target,old_sockets):
    db.executemany('INSERT INTO native_gear_sockets VALUES(?,?,?,?,?,?,?)',[
        (target,r['position'],r['category'],r['gem'],r['property'],r['amount'],r['gem_level']) for r in old_sockets.values()])


def jewel(kind):
    desc=COMPLETION['ordinary_gems'].get(str(kind),'')
    part=search(r'镶嵌在(?:作坊制作的)?#G([^#]+)#n',desc)
    effect=search(r'(?:可提升其|可提升其能力)#G([\u4e00-\u9fff]+)(\d+)%?(?:[—–-](\d+)%?)?',desc)
    if not part or not effect or part[1] not in PARTS:return None
    name=ALIASES.get(effect[1],effect[1])
    return PARTS[part[1]],name,int(effect[2]),int(effect[3] or effect[2])


def view(iid,category,sockets,capacity=0):
    if category=='ordinary':
        info=[dict(stype=r['gem'],sdesc=r['property'],cate=int(bool(r['gem']))) for r in sockets.values()]
        return packet(211,[('array','equipsocketinfo')],[info])
    if category=='jewel':
        holes=[dict(status=2 if pos in sockets and sockets[pos]['gem'] else int(pos in sockets),
                    gem_type=sockets[pos]['gem'] if pos in sockets else 0,need_lingqi=1,
                    desc=sockets[pos]['property'] if pos in sockets else '') for pos in (1,2)]
        return packet(216,['number','number',('array','jewerly_hole')],[iid,capacity,holes])
    values=[]
    for pos in (1,2):
        gem=sockets[pos]['gem'] if pos in sockets else 0
        values.extend((gem,sockets[pos]['gem_level'] if gem else 0))
    return packet(3522,['number']*4,values)


def receive(game,mid,reader):
    from native946_gear_services import bench_access
    from native946_mall_uses import consume
    source=Refinement(game)
    p=source.p
    if game.battle:raise ValueError('请先结束战斗再操作装备。')
    iid=reader.integer()
    operation=pos=gem_id=0
    if mid==271:operation,pos=reader.take(2);gem_id=reader.integer()
    if mid==3530:gem_id,pos=reader.integer(),reader.integer()
    if mid==3528:pos=reader.integer()
    supplied=[reader.integer() for _ in range(reader.take(1)[0])] if mid==264 else []
    reader.finish()
    category='jing' if mid>=3500 else 'jewel' if mid in (270,271) else 'ordinary'
    changed=mid in WRITES
    with p.transaction(write=changed) as db:
        item=source.item(db,iid,equipped=True)
        instance=records(db,p.cid).get(iid)
        if not instance:raise ValueError('请选择有实际属性的装备。')
        if mid in (256,257):
            bench_access(game,'smith')
            cost=(instance['max_durability']-instance['durability'])*10
            if cost<=0:raise ValueError('装备耐久已满。')
            debit(db,p.cid,cost)
            db.execute('UPDATE native_equipment SET durability=max_durability WHERE item_id=?',(iid,))
            return game.defer_packets(lambda:(*p.attribute_packets(),p.item_description(iid),text_notice('装备修理完成。')))
        if category=='jing':
            limits=JING['socket_rules'].get(str(item['type']))
            if not limits:raise ValueError('请选择原生境装备。')
        elif category=='jewel':
            if instance['grade']<1001:raise ValueError('请选择作坊首饰。')
        elif instance['grade']>=1000 or instance['requirements'].get('kind'):
            raise ValueError('此装备需在专用镶嵌界面操作。')
        sockets=rows(db,iid)
        capacity=db.execute('SELECT gold FROM characters WHERE id=?',(p.cid,)).fetchone()[0]//3000 if category=='jewel' else 0
        if any(r['category']!=category for r in sockets.values()):
            raise ValueError('已有其他类型的装备孔位。')
        if not changed:return view(iid,category,sockets,capacity)
        if category=='jing':gem_session(game)
        else:bench_access(game,'jewels' if category=='jewel' else 'smith')
        if mid in (264,265,3821) or mid==271 and operation==1:
            if mid==3821:
                if sockets:raise ValueError('境装备已经开孔。')
                debit(db,p.cid,1000000)
                positions=(1,2)
            else:
                positions=(pos,) if mid==271 else (1,)
                if any(k not in (1,2) or k in sockets for k in positions):
                    raise ValueError('孔位已经开启或不存在。')
                debit(db,p.cid,3000)
                if len(supplied)>1:raise ValueError('普通装备只受理一颗开孔宝石。')
            db.executemany('INSERT INTO native_gear_sockets(item_id,position,category) VALUES(?,?,?)',
                           [(iid,k,category) for k in positions])
            if mid==264 and supplied:gem_id,pos=supplied[0],1
        if mid==3528:
            current=sockets.get(pos)
            if not current or not current['gem']:raise ValueError('该孔没有境宝石。')
            put(game,db,current['gem'],current['gem_level'])
            db.execute("UPDATE native_gear_sockets SET gem=0,property='',amount=0,gem_level=1 WHERE item_id=? AND position=?",(iid,pos))
        if mid in (3530,) or mid==271 and operation in (2,3) or mid==264 and supplied:
            current=rows(db,iid).get(pos)
            if not current or bool(current['gem'])!=(mid==271 and operation==3):
                raise ValueError('孔位状态发生变化，请重新选择。')
            gem=source.item(db,gem_id)
            grade=1
            if category=='jing':
                allowed=limits['gem_item_ids'].split(',')
                if str(gem['type']) not in allowed:raise ValueError('这颗境宝石不适合该部位。')
                grade=level(db,gem_id)
                if grade>int(limits['gem_level_desc'].removesuffix('级')):
                    raise ValueError('宝石等级超过当前境装备的镶嵌上限。')
                debit(db,p.cid,RULES[gem['type']*100+grade]['inlay_cost'])
                attr,amount='',0
            elif category=='jewel':
                spec=jewel(gem['type'])
                if spec is None or spec[0]!=instance['slot']:raise ValueError('宝石与首饰部位不匹配。')
                _,attr,low,high=spec
                amount=low+randbelow(high-low+1)
            else:
                if not 1500<=gem['type']<=1524:raise ValueError('请选择普通微瑕宝石。')
                color,quality=divmod(gem['type']-1500,5)
                attr=('攻击','最大气血','防御','最大法力','速度')[color]
                amount=(quality+1)*(1 if color==4 else 5)
            consume(db,gem,1)
            db.execute('UPDATE native_gear_sockets SET gem=?,property=?,amount=?,gem_level=? WHERE item_id=? AND position=?',
                       (gem['type'],attr,amount,grade,iid,pos))
        if mid==271 and operation not in (1,2,3):raise ValueError('首饰操作无效。')
        if category=='jewel' and operation==1:capacity-=1
        current=view(iid,category,rows(db,iid),capacity)
    extra=packet(3825,[],[]) if mid==3821 else packet(3521,['number'],[pos]) if mid==3528 else (
        packet(3523,['number']*3,[pos,gem['type'],grade]) if mid==3530 else None)
    return game.defer_packets(lambda:(*p.item_packets(),*p.attribute_packets(),p.item_description(iid),
                packet(3757,['number'],[iid]),*((extra,) if extra else ()),current,text_notice('装备孔位已保存。')))
