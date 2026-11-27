"""Native equipment sources and paid equipment transitions."""

from time import monotonic

from native946_codec import packet, text_notice
from native946_equipment import COMPLETION, CROWN_HELP, crown_stats, persist, records
from native946_refinement import Refinement, debit, state


REFERENCE_WIRE_VERSION=2
BENCHES={(2039,1276):'smith',(2092,4755):'jewels',(2221,4545):'guardian',(2436,7630):'realm_gems'}
RING_BOX=47580


def matches(role,character):
    from native946_characters import ROLES
    value=ROLES[str(character['role_id'])]['name']
    gender='男' if character['sex']==1 else '女'
    race={1:'人',2:'妖',3:'仙',7:'巫'}[character['race']]
    return bool(set(role.split(',')).intersection(('', '全部','通用',value,gender,gender+race,race+'族')))


def open_ring(game,db,item):
    from native946_cosmetics import Cosmetics
    from native946_mall_uses import consume

    service=Refinement(game)
    item=service.item(db,item['id'])
    kind=COMPLETION['ring'][str(game.character['race'])]
    consume(db,item,1)
    iid=Cosmetics(game)._add(db,kind,backpack=True)
    profile=dict(reference_id=kind,grade=1,slot=12,role='全部',need={},level=1,rebirth=0,durability=1000,
                 native_race=game.character['race'],kind='神兵')
    persist(db,service.p.cid,iid,profile,{'最大气血':100,'命中':20,'攻击':60})
    return game.defer_packets(lambda:(*service.p.item_packets(),text_notice('获得当前种族的一级神兵戒指。基础数值按本机规则。')))


def plan(game,item,instance,op):
    kind=str(item['type'])
    grade,slot=instance['grade'],instance['slot']
    requirements=instance['requirements']
    category=requirements.get('kind')
    if op=='shenbing' and not category and 15<=grade<=17:
        choices=[int(key) for key,(part,race,role) in COMPLETION['shenbing'].items()
                 if part==slot and race in (0,game.character['race']) and matches(role,game.character)]
        return (min(choices),4,'神兵',46545,3,100000) if choices else None
    if op=='shenbing' and category=='神兵' and kind in COMPLETION['shenbing'] and grade==4:
        if slot==12:return None
        step=COMPLETION['shenbing_cost'][3]
        return (int(kind),5,'神兵',step['Type'],1,step['Cash'])
    if op=='shenbing' and category=='神兵' and slot==12 and kind in COMPLETION['shenbing'] and 1<=grade<4:
        step=COMPLETION['ring_cost'][grade-1]
        return (int(kind),grade+1,'神兵',step['Type'],1,step['Cash'])
    if op=='holy' and grade>=4 and kind in COMPLETION['sw_map']:
        return (COMPLETION['sw_map'][kind],grade,'圣武',45161,1,200000)
    if op=='jing':
        if kind in COMPLETION['jing']:rank=COMPLETION['jing'][kind]['rank']+1
        elif not category and (grade>=16 and grade<1000 or grade>=1002):rank=1
        else:return None
        choices=[int(k) for k,v in COMPLETION['jing'].items() if v['part']==slot and v['rank']==rank
                 and matches(v['role'],game.character)]
        return (min(choices),rank,'境装备',47222,rank,rank*100000) if choices else None
    return None


def progression_menu(game,nid,op,page=0):
    from native946_npc_services import NpcServices
    from native946_mall_bags import pages
    from native946_progression import DATA as ITEMS
    p=game.progression
    options=[]
    with p.transaction(write=False) as db:
        allowed=pages(db,p.cid)
        saved=records(db,p.cid)
        for item in db.execute('SELECT * FROM inventory WHERE character_id=? ORDER BY page,slot',(p.cid,)):
            instance=saved.get(item['id'])
            if not instance or item['page'] not in allowed or item['amount']!=1:continue
            target=plan(game,item,instance,op)
            if target is None:continue
            output,rank,kind,material,count,cash=target
            name=ITEMS['items'][str(output)]['name']
            materials=COMPLETION['holy_ring_materials'] if op=='holy' and instance['slot']==12 else {str(material):count}
            costs='、'.join(f"{ITEMS['items'][key]['name']}×{quantity}" for key,quantity in materials.items())
            place=f"背包{allowed.index(item['page'])+1}第{item['slot']}格"
            options.append((f'{name} · {place} · {costs}及{cash}现金',
                            dict(kind='gear_refinement',op='transition_do',branch=op,item=item['id'],quote=target)))
    if not options:raise ValueError('请把符合部位、品阶和角色条件的装备放入背包。')
    visible=options[page*8:(page+1)*8]
    for label,index in (('上一页',page-1),('下一页',page+1)):
        if 0<=index and index*8<len(options):
            visible.append((label,dict(kind='gear_refinement',op='transition',branch=op,page=index)))
    return NpcServices(game)._menu(nid,'本机装备升阶服务。选择装备后结算，装备的已确认词条和原物品位置保留。',visible)


def transition(game,action):
    service=Refinement(game)
    p=service.p
    iid=action['item']
    with p.transaction() as db:
        item=service.item(db,iid)
        old=records(db,p.cid).get(iid)
        approved=plan(game,item,old,action['branch']) if old and item['amount']==1 else None
        if approved is None or approved!=action['quote']:
            raise ValueError('装备状态已变化，请重新选择。')
        info=state(db,p.cid,iid)
        from native946_gear_sockets import existing
        if approved[2]=='境装备' and old['requirements'].get('kind')!='境装备' and existing(db,iid):
            raise ValueError('请选择尚未开孔的装备转为境装备。')
        if info['pending'] or db.execute(
            'SELECT 1 FROM native_equipment_attribute_sets WHERE item_id=?',(iid,)).fetchone():
            raise ValueError('请先处理待确认炼化或备用属性。')
        new,grade,category,material,amount,cash=approved
        if category=='圣武' and old['slot']==12:
            for kind,count in COMPLETION['holy_ring_materials'].items():service.take(db,int(kind),count)
        else:service.take(db,material,amount)
        debit(db,p.cid,cash)
        requirements=dict(old['requirements'],kind=category)
        if category=='境装备':requirements['sanctification']=max(1,requirements.get('sanctification',0))
        import json
        db.execute('UPDATE inventory SET type=? WHERE character_id=? AND id=?',(new,p.cid,iid))
        if category=='神兵' and new in CROWN_HELP:
            role,requirements,properties=crown_stats(new,grade)
            db.execute('UPDATE native_equipment SET reference_id=?,grade=?,role=?,requirements=?,properties=? '
                       'WHERE item_id=? AND character_id=?',
                       (new,grade,role,json.dumps(requirements,ensure_ascii=False),
                        json.dumps(properties,ensure_ascii=False),iid,p.cid))
        else:
            db.execute('UPDATE native_equipment SET grade=?,requirements=? WHERE item_id=?',
                       (grade,json.dumps(requirements,ensure_ascii=False),iid))
    return game.defer_packets(lambda:(*p.item_packets(),*p.attribute_packets(),p.item_description(iid),
                                      text_notice('本机装备升阶已完成。')))


def open_bench(game,nid,action):
    from native946_npc_services import NpcServices
    c=game.character
    kind=BENCHES.get((c['map_id'],nid))
    if kind is None:raise ValueError('请到对应的装备服务 NPC。')
    game.npc_service_session=dict(kind='gear_bench',scene=c['map_id'],npc=nid,expires=monotonic()+600)
    close=packet(1183,['string'],['dlg_npc_chat'])
    if kind=='guardian':
        from native946_guardian_equipment import open_panel, upgrade_panel
        return (close,upgrade_panel(game) if action=='guardian_upgrade' else open_panel(game))
    if kind=='realm_gems':
        if action=='gem_essence':
            from native946_jing_gems import ESSENCE
            from native946_cosmetics import Cosmetics
            p=game.progression
            with p.transaction() as db:
                debit(db,p.cid,5000)
                Cosmetics(game)._add(db,ESSENCE,100,backpack=True)
            return game.defer_packets(lambda:(*p.item_packets(),*p.attribute_packets(),text_notice('本机补给已发放境·宝石精粹100个。')))
        return (close,packet(3525,[],[]))
    if kind=='jewels':
        return (close,packet(215,[],[]))
    if action=='ordinary_sockets':
        return (close,packet(208,[],[]),packet(209,['number'],[3000]))
    return (close,text_notice('本机装备修理每点耐久收取10现金。选中装备后使用原生修理操作。'))


def bench_access(game,kind):
    service=getattr(game,'npc_service_session',None)
    if not service or service['kind']!='gear_bench' or service['expires']<=monotonic():
        raise ValueError('请先打开对应的装备服务。')
    from native946_npc_services import NpcServices
    npc=NpcServices(game).validate(service['npc'])
    if (npc['scene_id']!=service['scene'] or BENCHES.get((npc['scene_id'],npc['npc_id']))!=kind):
        raise ValueError('请到对应的装备服务 NPC。')
