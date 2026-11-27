"""Owned spirit cards, formations and the native Tianji graph/workshop."""
import re
from time import time
from native946_role_systems import DATA,RULES,state,save,wire,text,notice,require,number,character,grade,pay,take,count,give

def ling(db,cid):return state(db,cid,'lingyou',dict(levels=[0]*5,exp=[0]*5,unlocked=[],effects={},summon_claimed=0))
def card(index,level):
    number(index,1,5,'五行');number(level,1,9,'灵佑卡等级');return index*100+level
def ling_panel(db,cid,index,s):
    lv=s['levels'][index-1];kind=dict(Level=lv,Exp=s['exp'][index-1],MaxExp=RULES['lingyou_level_exp']*(lv+1),NeedCash=RULES['lingyou_study_cash']*(lv+1),AddExp=RULES['lingyou_study_exp'])
    return wire(3337,index,text(kind),text({i:1 for i in range(1,10) if card(index,i) in s['unlocked']}),text([dict(IsValid=1,Desc='本机灵佑：已解锁 '+str(sum(card(index,i) in s['unlocked'] for i in range(1,10)))+' 张；卡片持续2小时。'),dict(IsValid=int(lv>=9),Desc='该系研习达到9级，可拓印全部该系灵佑卡。')]))
def lingyou_quick(game,db):
    cid=game.progression.cid;s=ling(db,cid);rows={str(cid):dict(IsLastChoose=1)}
    from native946_guardian_chamber import AVAILABLE
    for r in db.execute('SELECT id FROM summons WHERE character_id=? AND '+AVAILABLE,(cid,)):rows[str(r[0])]=dict(IsLastChoose=1)
    for target,v in s['effects'].items():
        if v['expires']>time() and target in rows:rows[target]['EffType']=v['card']
    return [wire(4430,text(rows),text({k:1 for k in s['unlocked']}))]
def apply_card(game,db,key,target,consume=True):
    cid=game.progression.cid;number(key,101,509,'灵佑卡');meta=DATA['lingyou_info'].get(str(key));require(meta,'灵佑卡不存在。')
    if meta['use_type']==1:require(target==cid,'此灵佑卡仅供人物使用。')
    else:
        from native946_guardian_chamber import AVAILABLE
        require(target!=cid and db.execute('SELECT 1 FROM summons WHERE id=? AND character_id=? AND '+AVAILABLE,(target,cid)).fetchone(),'请选择自己可用的守护。')
        from native946_inventory_locks import records
        require(target not in records(db,cid,'summon'),'请先解锁该守护。')
    s=ling(db,cid)
    if consume:
        if count(db,cid,meta['item_type']):take(db,cid,meta['item_type'])
        else:
            require(key in s['unlocked'],'请先拼出对应拓版或准备成品灵佑卡。');require(s['levels'][meta['type']-1]>=meta['level'],'五行研习等级不足。');take(db,cid,47155+(meta['level']-1)//3,RULES['lingyou_print_count'])
    s['effects'][str(target)]=dict(card=key,expires=int(time())+RULES['lingyou_seconds']);save(db,cid,'lingyou',s)
    return [notice(meta['name']+'已生效，持续2小时。')]

def lineup(db,cid):return state(db,cid,'lineup',dict(levels={},practice={}))
def line_meta(index):
    from native946_lineups import meta
    return meta(index)
def line_need(index,lv):
    from native946_lineups import need
    return need(index,lv)
def line_panel(db,cid,s):
    from native946_lineups import panel
    return panel(db,cid)
def line_train(db,cid,index,s):
    from native946_lineups import train_panel
    return train_panel(db,cid,index)
def validate_lineup(db,cid,index):
    from native946_lineups import validate
    return validate(db,cid,index)

def tianji(db,cid):return state(db,cid,'tianji',dict(points=[0]*6,total=[0]*6,exp=[0]*6,active=[],craft=None,choice=1))
def tianji_panel(s):
    return wire(854,s['points'],sum(s['total']),1+sum(s['total'])//20,0,dict(exp=s['exp'][0],exp_next=1000),text({str(k).zfill(3):1 for k in s['active']}),1,0,s['choice'])
def tianji_node(raw):
    require(isinstance(raw,str) and raw.isdigit() and len(raw)<=5,'天机点位无效。');key=str(int(raw));require(key in DATA['tianji_point'],'天机点位不存在。');return key,DATA['tianji_point'][key]
def node_cost(meta):
    match=re.search(r'#n(\d+)',meta['cost']);return int(match[1]) if match else 0
def validate_node(s,key):
    require(int(key) not in s['active'],'此天机已经激活。')
    if key!='1':require(any(str(n) in DATA['tianji_graph'].get(key,{}) or key in DATA['tianji_graph'].get(str(n),{}) for n in s['active']),'请先激活相连的前置天机。')
def recipes(s,zf):
    # The client only contains recipe names, not the server's economy. These
    # cash recipes are local and require the native enabling node.
    number(zf,1,5,'作坊类别')
    return [(kind,node) for kind,node in DATA['tianji_recipes'][str(zf)] if node in s['active']]
def formula(db,cid,s,zf,kind):
    require(any(k==kind for k,n in recipes(s,zf)),'尚未学会此配方。')
    return wire(845,zf,kind,[],99,'本机配方：现金1000/份，无额外材料。',0,RULES['tianji_craft_cash'],int(character(db,cid)['gold']>=RULES['tianji_craft_cash']))

def receive(game,db,mid,a):
    cid=game.progression.cid
    if mid in (3340,3341,3342,3343,3344,3345,3357,4431,4432):
        s=ling(db,cid)
        if mid==4431:return lingyou_quick(game,db)
        if mid==4432:
            from native946_lua_values import table
            selected=table(a[0]);require(isinstance(selected,dict) and len(selected)<=20,'灵佑守护列表无效。');results={}
            for sid,key in selected.items():
                target=int(sid);number(key,101,509,'灵佑卡');apply_card(game,db,key,target);results[str(target)]=key
            if a[1]:apply_card(game,db,a[1],cid);results[str(cid)]=a[1]
            return [wire(4429,text(results)),*lingyou_quick(game,db)]
        if mid==3357:
            require(len(s['unlocked'])>=45 and not s['summon_claimed'],'集齐45张拓版后可领取一次灵佑守护。')
            # dlg_lingyou_main root r2=4710 is captured by its reward photo.
            # SHAPE_INFO[3335] maps that photo/model to the native 猫妖.
            game.summons.acquire(3335,'lingyou-complete');s['summon_claimed']=1;save(db,cid,'lingyou',s)
        if mid in (3340,3341,3342,3344,3345):
            index=number(a[0],1,5,'五行')
            if mid==3345:
                style=number(a[1],1,2,'研习方式');require(s['levels'][index-1]<9,'该系研习已满级。')
                for _ in range(1 if style==1 else 10):
                    lv=s['levels'][index-1]
                    if lv>=9:break
                    pay(db,cid,RULES['lingyou_study_cash']*(lv+1));s['exp'][index-1]+=RULES['lingyou_study_exp']
                    if s['exp'][index-1]>=RULES['lingyou_level_exp']*(lv+1):s['exp'][index-1]-=RULES['lingyou_level_exp']*(lv+1);s['levels'][index-1]+=1
                save(db,cid,'lingyou',s)
            if mid in (3340,3341,3344):
                level=number(a[1],1,9,'卡片等级');key=card(index,level);meta=DATA['lingyou_info'][str(key)];locked=key not in s['unlocked'];material=47157+index if locked else 47155+(level-1)//3;needed=RULES['lingyou_puzzle_count']*level if locked else RULES['lingyou_print_count']
                if mid in (3340,3344):
                    require(s['levels'][index-1]>=level,'五行研习等级不足。')
                    if mid==3340:require(locked,'此拓版已经拼成。');take(db,cid,material,needed);s['unlocked'].append(key);save(db,cid,'lingyou',s)
                    else:require(not locked,'请先完成拓版拼图。');take(db,cid,material,needed);give(db,game.progression,meta['item_type'])
                return [ling_panel(db,cid,index,s),wire(3336 if locked else 3339,index,material,needed,count(db,cid,material),meta['desc'])]
            return [ling_panel(db,cid,index,s)]
        return [wire(3338,text(s['levels']),2 if s['summon_claimed'] else 1 if len(s['unlocked'])>=45 else 0)]
    if 875<=mid<=880:
        from native946_lineups import receive as formation_receive
        return formation_receive(game,db,mid,a)
    s=tianji(db,cid)
    if mid in (858,859):
        if mid==858:
            pay(db,cid,exp=RULES['tianji_exp_per_point']);require(sum(s['total'])<RULES['tianji_max_points'],'本机天机点已达到上限。');s['points'][0]+=1;s['total'][0]+=1
        else:
            from native946_inventory_features import owned,consume
            item=owned(db,cid,a[0]);amount=number(a[1],1,99,'培养数量');m=DATA['tianji_items'].get(str(item['type']));require(m,'此物品不能培养天机。');consume(db,cid,a[0],amount)
            for i,field in enumerate(('normal_pt','pt1','pt2','pt3','pt4','pt5')):
                s['exp'][i]+=m[field]*amount;gained=s['exp'][i]//1000;s['exp'][i]%=1000;s['total'][i]+=gained;s['points'][i]+=gained
            require(sum(s['total'])<=RULES['tianji_max_points'],'本次培养会超过天机点上限。')
        save(db,cid,'tianji',s)
    if mid==860:return [wire(846,s['points'][:5],s['total'][:5],[1+v//20 for v in s['total'][:5]],[dict(exp=v,exp_next=1000) for v in s['exp'][:5]])]
    if mid==864:return [wire(853,1+sum(s['total'])//20,0,sum(s['total']),s['points'][0],s['exp'][0],1000,0,0,99,1,1)]
    if mid in (863,856,857):
        key,meta=tianji_node(a[0]);cost=node_cost(meta);color=number(meta['color'],1,6,'天机颜色');validate_node(s,key)
        if mid==863:return [wire(851,str(int(key)).zfill(3),color,cost,s['points'][color-1])]
        chosen=number(a[1],1,6,'天机颜色');require(chosen==color,'点位颜色不匹配。')
        if mid==857:require(a[3]==color and a[4]==0,'此点位按面板所示颜色直接激活。')
        require(a[2]==cost and s['points'][color-1]>=cost,'所需天机点不足或消耗已经变化。');s['points'][color-1]-=cost;s['active'].append(int(key));save(db,cid,'tianji',s)
    if mid in (867,866,862,861):
        if mid==861:
            s['craft']=None;save(db,cid,'tianji',s);return [wire(848)]
        zf=a[0];options=recipes(s,zf)
        if mid==867:return [wire(847,zf,[DATA['items'][str(k)]['name'] for k,n in options],[k for k,n in options],[1]*len(options))]
        if mid==866:return [formula(db,cid,s,zf,a[1])]
        kind=a[1];amount=number(a[2],1,99,'制作数量');formula(db,cid,s,zf,kind);pay(db,cid,RULES['tianji_craft_cash']*amount);give(db,game.progression,kind,amount)
        return [wire(849,kind),wire(848),notice('天机作坊制作完成。')]
    return [tianji_panel(s)]

def effects(db,cid,target=None):
    result={};s=ling(db,cid);v=s['effects'].get(str(target or cid))
    if v and v['expires']>time():
        result.update(DATA['lingyou_effects'].get(str(v['card']),{}));m=DATA['lingyou_info'][str(v['card'])];desc=m['desc'];result['RoleLingElement']=m['type'];result['RoleLingElementValue']=m['level']*10
        for name,key,value in (('身轻如燕','RoleSpeedPercent',50),('法力无限','RoleMpPercent',300),('野兽之力','RolePhysicalPercent',30),('强控','RoleControl',30),('无限生机','RoleCureReceivedPercent',200),('仙兽再生','RoleCureReceivedPercent',200),('物理连击','RolePhysicalExtraHit',1),('强攻','RoleGroupPhysicalPercent',100),('破甲','RoleArmorBreak',1),('法术集中','RoleSpellConcentrate',1),('法术精通','RoleSpellMastery',30),('仙法连击','RoleSpellExtraHit',1)):
            if name in desc:result[key]=value
    if target:return result
    t=tianji(db,cid)
    if 5002 in t['active']:
        key,value=(('hpmax',2000),('mpmax',1500),('attack',300),('initiative',50))[t['choice']-1];result[key]=result.get(key,0)+value
    return result
