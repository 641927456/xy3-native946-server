"""Native child equipment pages, owned skill books and equipment workbench."""
from time import time
import secrets
from native946_role_systems import DATA,state,save,wire,text,notice,require,number,pay,take,count,give
REQUESTS=set(range(3077,3083))|{3088,3089,3090,3091,4178,4746}
BOOKS={v['item_type']:int(k) for k,v in DATA['baby_skill'].items() if v.get('item_type') and v.get('learnable')}
def pages(db,cid):
    import json
    return {json.loads(r['value']).get('page',1000+i):r['id'] for i,r in enumerate(db.execute('SELECT id,value FROM native_babies WHERE character_id=? ORDER BY id',(cid,)))}
def validate_position(db,cid,kind,page,slot):
    if not 1000<=page<=1049:return
    require(page in pages(db,cid),'孩子装备页未开启。');m=DATA['baby_equip'].get(str(kind));require(m and slot==m['grid'],'孩子装备部位不匹配。')
def equip(game,iid,bid=None):
    cid=game.progression.cid
    with game.progression.transaction(write=False) as db:
        row=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',(iid,cid)).fetchone();require(row and str(row['type']) in DATA['baby_equip'],'请选择自己的孩子装备。');owned_pages=pages(db,cid)
        if row['page'] in owned_pages:destination,slot=game.progression.free_bag_slot(game.progression._snapshot(db))
        else:
            if bid is None and len(owned_pages)>1:
                game.dialog_actions=[dict(kind='role_baby_equip',item_id=iid,baby_id=b) for b in owned_pages.values()]
                return game.menu('请选择穿戴该装备的孩子。',['孩子 '+str(b) for b in owned_pages.values()],cid)
            require(owned_pages,'请先领养孩子。');bid=bid or next(iter(owned_pages.values()));require(bid in owned_pages.values(),'孩子不属于当前角色。');destination=next(p for p,b in owned_pages.items() if b==bid);slot=DATA['baby_equip'][str(row['type'])]['grid']
    game.progression.move_item(iid,destination,slot)
    return game.defer_packets(lambda:(*game.progression.sync(),notice('孩子装备已更新。')))
def gear(db,cid,iid,bag_only=False):
    from native946_inventory_features import owned
    if bag_only:return owned(db,cid,iid)
    r=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=?',(iid,cid)).fetchone();require(r,'装备不属于当前角色。')
    from native946_mall_bags import pages as bags
    require(r['page'] in (*bags(db,cid),*pages(db,cid)),'装备不在可用背包或孩子装备页。')
    from native946_inventory_locks import require_unlocked
    require_unlocked(db,cid,iid);return dict(r)
def skill_rows(b):return b.setdefault('slots',{str(i):int(k) for i,k in enumerate(b['skills'],1)})
def skill_desc(key):return DATA['baby_skill'].get(str(key),{}).get('desc','')

def can_learn(b,key,replace_index=None):
    require(str(key) in DATA['baby_skill'],'没有此子女技能。')
    existing={k for i,k in skill_rows(b).items() if str(i)!=str(replace_index)}
    require(key not in existing,'该技能已经学过。')
    blocked=set(DATA['baby_skill'][str(key)].get('disable_by',[]))
    require(not (existing&blocked) and not any(key in DATA['baby_skill'].get(str(k),{}).get('disable_by',[]) for k in existing),'此技能与已有的高低阶技能冲突。')

def advanced_reply(b,index):
    key=skill_rows(b).get(str(index),0);lv=b['skills'].get(str(key),0);advanced=key+12 if 745<=key<=756 else 0
    item=DATA['baby_skill'][str(advanced)]['item_type'] if advanced else 0
    return wire(3047,b['id'],key,lv,0,(lv+1)*1000,item,advanced,lv if advanced else 0,0,(lv+1)*1000 if advanced else 0)
def skill_reply(b,index,op,add=0):
    key=skill_rows(b).get(str(index),0);lv=b['skills'].get(str(key),0)
    if op==1:return wire(3062,b['id'],index,key,skill_desc(key) or '请选择背包中已拥有的子女技能书。')
    if op==2:return wire(3063,b['id'],key,lv,0,(lv+1)*1000,add,skill_desc(key),skill_desc(key)+'#r本机技能培养：每本同技能书增加一级，上限10级。')
    if op==4:return advanced_reply(b,index)
    return wire(3061,b['id'],'本机遗忘消耗1本道德经。',key,lv,0,(lv+1)*1000,46863,1)
def receive(game,db,mid,a):
    cid=game.progression.cid
    if mid in (3088,3089,3090,3091,4178,4746):
        from native946_role_society import baby,save_baby,baby_property
        from native946_inventory_features import owned,consume
        b=baby(db,cid,a[0]);slots=skill_rows(b)
        if mid==4746:
            key=number(a[1],703,779,'子女技能');require(str(key) in b['skills'],'孩子尚未学会该技能。')
            return [wire(4745,b['id'],key,skill_desc(key),'本机培养：同技能书每本增加一级，上限10级。')]
        if mid==4178:
            index=number(a[1],1,6,'子女技能位');choice=number(a[2],0,1,'保留技能');pending=b.get('skill_pending')
            require(pending and pending['index']==index and pending['expires']>=time() and pending['old']==slots.get(str(index),0),'技能选择已失效，请重新灌注。')
            if choice:
                key=pending['new'];can_learn(b,key,index);old=slots.get(str(index));lv=b['skills'].pop(str(old),1);slots[str(index)]=key;b['skills'][str(key)]=lv
            b.pop('skill_pending',None);save_baby(db,cid,b);return [baby_property(db,cid,b),notice('已保留新技能。' if choice else '已保留原技能。')]
        if mid==3091:
            skill=number(a[1],1,99999,'子女技能');require(str(skill) in b['skills'],'孩子尚未学会该技能。');index=next((int(i) for i,k in slots.items() if k==skill),None);require(index,'技能位记录无效。');ids=a[2]
            require(1<=len(ids)<=20 and len(ids)==len(set(ids)),'请选择不重复的技能书。')
            for iid in ids:require(BOOKS.get(owned(db,cid,iid)['type'])==skill,'请使用同技能书培养。')
            return [skill_reply(b,index,2,len(ids)*1000)]
        index=number(a[1],1,6,'子女技能位')
        if mid==3090:return [skill_reply(b,index,number(a[2],1,4,'技能操作'))]
        if mid==3088:
            item=owned(db,cid,a[2]);key=BOOKS.get(item['type']);require(key,'请选择可学习的子女技能书。');require(not slots.get(str(index)),'请先遗忘该技能位已有技能。')
            return [wire(3062,b['id'],index,key,skill_desc(key))]
        op=number(a[2],1,5,'子女技能操作');ids=a[3];require(len(ids)<=20 and len(ids)==len(set(ids)),'技能书选择无效。')
        require(not b.get('skill_pending') or b['skill_pending']['expires']<time(),'请先确认上一次灌注的技能选择。')
        if op==1:
            require(not slots.get(str(index)) and len(ids)==1,'请选择一本技能书并使用空技能位。');item=owned(db,cid,ids[0]);key=BOOKS.get(item['type']);require(key,'请选择可学习的技能书。');can_learn(b,key,index);require(b['grade']>=index-1,'孩子年龄尚未解锁此技能位。');consume(db,cid,item['id']);slots[str(index)]=key;b['skills'][str(key)]=1
        elif op==2:
            key=slots.get(str(index));require(key and ids,'请先选择孩子已学技能和培养用技能书。');require(b['skills'][str(key)]+len(ids)<=10,'本次培养会超过技能等级上限。')
            for iid in ids:
                item=owned(db,cid,iid);require(BOOKS.get(item['type'])==key,'请使用同技能书培养。');consume(db,cid,iid)
            b['skills'][str(key)]+=len(ids)
        elif op==3:
            key=slots.get(str(index));require(key,'该技能位没有已学技能。');take(db,cid,46863);del slots[str(index)];b['skills'].pop(str(key),None)
        elif op==4:
            key=slots.get(str(index),0);require(745<=key<=756,'此技能不能继续升阶。');advanced=key+12;can_learn(b,advanced,index)
            require(not ids,'升阶材料由面板给出的技能书自动扣除。');take(db,cid,DATA['baby_skill'][str(advanced)]['item_type']);lv=b['skills'].pop(str(key));slots[str(index)]=advanced;b['skills'][str(advanced)]=lv
        elif op==5:
            require(len(ids)==1,'请选择一枚百年金丹。');item=owned(db,cid,ids[0]);require(item['type']==47778,'请使用百年金丹灌注。');require(b['grade']>=index-1,'孩子年龄尚未解锁此技能位。')
            candidates=[]
            for key in map(int,DATA['baby_skill']):
                if key==slots.get(str(index)):continue
                try:can_learn(b,key,index)
                except ValueError:continue
                candidates.append(key)
            require(candidates,'当前没有可灌注的新技能。');key=secrets.choice(candidates);consume(db,cid,item['id']);b['skill_pending']=dict(index=index,old=slots.get(str(index),0),new=key,expires=int(time())+600);save_baby(db,cid,b)
            return [wire(3062,b['id'],index,key,skill_desc(key)),notice('本机百年金丹从可学习且不冲突的原生技能中等概率灌注，请选择保留原技能或新技能。')]
        save_baby(db,cid,b);return [baby_property(db,cid,b),notice('孩子技能已保存。')]
    s=state(db,cid,'baby_equips',dict(items={},pending=None,disassemble=None))
    if mid in (3077,3078):
        ids=a[0];require(len(ids)<=20 and len(ids)==len(set(ids)),'装备选择无效。')
        if mid==3078 or (mid==3077 and a[1]==1):
            require(ids,'请选择孩子装备。')
            for iid in ids:
                item=gear(db,cid,iid,True);require(str(item['type']) in DATA['baby_equip'],'只能分解孩子装备。')
            if mid==3078:return [wire(3051,ids,[dict(type=46988,amount=len(ids))])]
            require(not s['disassemble'],'请先领取上次的分解产物。')
            for iid in ids:db.execute('DELETE FROM inventory WHERE id=? AND character_id=?',(iid,cid));s['items'].pop(str(iid),None)
            s['disassemble']=dict(amount=len(ids),end=int(time())+30);save(db,cid,'baby_equips',s)
            return [wire(3050,ids,[dict(type=46988,amount=len(ids))],30,30,dict(type=0,amount=0))]
        number(a[1],2,2,'分解操作');job=s['disassemble'];require(job and job['end']<=time(),'分解未完成或产物已领取。');give(db,game.progression,46988,job['amount']);s['disassemble']=None;save(db,cid,'baby_equips',s);return [wire(3050,[],[],0,0,dict(type=0,amount=0))]
    iid=a[0];item=gear(db,cid,iid);require(str(item['type']) in DATA['baby_equip'],'请选择孩子装备。');old=s['items'].get(str(iid),dict(level=0,exp=0,bonus={}));pending=s['pending']
    if mid in (3079,3081):
        operation=a[3] if mid==3079 else a[2];number(operation,0,2,'装备操作')
        if operation==2:
            require(old['level']<10 or mid==3081,'孩子装备已淬炼满级。')
            if mid==3079:require(a[1] in (46988,0) and a[2] in (46863,0),'请选择面板提供的本机材料。');take(db,cid,46988);take(db,cid,46863)
            else:require(a[1] in (46863,0),'请选择道德经。');take(db,cid,46863)
            pay(db,cid,10000);new=dict(old)
            if mid==3079:new['level']+=1
            else:new['bonus']={secrets.choice(('hpmax','mpmax','attack','defense','initiative')):secrets.randbelow(20)+1}
            pending=dict(item=iid,value=new,expires=int(time())+120,kind='forge' if mid==3079 else 'recast');s['pending']=pending;save(db,cid,'baby_equips',s)
        before='本机装备 '+str(old['level'])+'级 '+str(old['bonus']);after=''
        if pending and pending['item']==iid:after='本机装备 '+str(pending['value']['level'])+'级 '+str(pending['value']['bonus'])
        if mid==3079:return [wire(3052,iid,[dict(type=46988,amount=1)],[dict(type=46863,amount=1)],old['level'],0,(old['level']+1)*100,0,before,after)]
        return [wire(3053,iid,[dict(type=46863,amount=1)],old['level'],0,100,before,after)]
    if mid in (3080,3082):
        number(a[1],0,1,'装备结果选择');require(pending and pending['item']==iid and pending['expires']>=time() and pending['kind']==('forge' if mid==3080 else 'recast'),'装备操作结果已失效。')
        if a[1]:s['items'][str(iid)]=pending['value']
        s['pending']=None;save(db,cid,'baby_equips',s);return [notice('孩子装备结果已保存。')]
    raise ValueError('孩子装备操作无效。')
def bonuses(db,cid):
    import json
    worn=set()
    for row in db.execute('SELECT value FROM native_babies WHERE character_id=?',(cid,)):
        b=json.loads(row[0])
        if b.get('status') and not b.get('travel'):worn.add(b.get('page',1000))
    s=state(db,cid,'baby_equips',dict(items={}));result={}
    for row in db.execute('SELECT id,page,type FROM inventory WHERE character_id=?',(cid,)):
        if row['page'] not in worn or str(row['type']) not in DATA['baby_equip']:continue
        v=s['items'].get(str(row['id']),dict(level=0,bonus={}));result['defense']=result.get('defense',0)+5*(v['level']+1)
        for key,value in v['bonus'].items():result[key]=result.get(key,0)+value
    return result
