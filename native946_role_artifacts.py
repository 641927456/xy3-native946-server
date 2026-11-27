"""Owned native artifact positions used by child combat triggers."""
from native946_role_systems import state,save,wire,require,notice,number
from native946_role_battle import DATA

TYPES={kind:skill for skill,name in ((128,'绝情鞭'),(131,'金箍儿')) for kind in DATA['artifact_items'][name]}
REQUESTS={310,313,314,322}

def available(db,cid):
    from native946_mall_bags import pages
    usable=pages(db,cid)
    return {r['id']:dict(r,skill=TYPES[r['type']]) for r in db.execute('SELECT * FROM inventory WHERE character_id=?',(cid,)) if r['type'] in TYPES and r['page'] in usable}

def current(db,cid):
    s=state(db,cid,'artifacts',dict(slots=[0,0,0],essence=0));owned=available(db,cid)
    s['slots']=[iid if iid in owned else 0 for iid in s['slots']]
    return s,owned

def active(db,cid):
    s,owned=current(db,cid)
    return {owned[i]['skill'] for i in s['slots'] if i}

def panel(db,cid):
    s,owned=current(db,cid);positions=[owned[i]['skill'] if i else 0 for i in s['slots']]
    return wire(303,*positions,3,sorted({v['skill'] for v in owned.values()}),'本机已接入绝情鞭、金箍儿；选择天、地、人位置后生效。')

def receive(game,db,mid,a):
    cid=game.progression.cid;s,owned=current(db,cid)
    if mid==314:return [panel(db,cid)]
    if mid==313:
        skill=number(a[0],128,131,'法宝');require(any(v['skill']==skill for v in owned.values()),'尚未拥有该法宝。')
        return [wire(302,dict(fabao_id=skill,grade=1,last=s['essence'],last_max=1000,exp='0',exp_next='1000',desc=DATA['native_skills'][str(skill)]['des']+'#r本机规则：主动施放消耗200怨气、10灵气；子女触发不扣怨气，每次消耗10灵气。'))]
    if mid==310:
        hp=db.execute('SELECT hp FROM progression WHERE character_id=?',(cid,)).fetchone()[0]
        require(hp>100,'补充灵气需100气血，且不可因此倒下。');require(s['essence']<1000,'法宝灵气已满。')
        db.execute('UPDATE progression SET hp=hp-100 WHERE character_id=?',(cid,));s['essence']=min(1000,s['essence']+100);save(db,cid,'artifacts',s)
        return [notice('已消耗100气血，补充100法宝灵气。'),panel(db,cid)]
    ids=a[0];require(len(ids)==3,'请选择天、地、人三个法宝位置。');require(len([k for k in ids if k])==len(set(k for k in ids if k)),'同一法宝不能重复装备。')
    by_skill={v['skill']:iid for iid,v in owned.items()};require(all(not k or k in by_skill for k in ids),'法宝不属于当前人物或不在可用背包。')
    from native946_inventory_locks import require_unlocked
    for key in ids:
        if key:require_unlocked(db,cid,by_skill[key])
    s['slots']=[by_skill[k] if k else 0 for k in ids];save(db,cid,'artifacts',s)
    return [panel(db,cid),notice('法宝位置已保存。')]

def use(game,iid):
    with game.progression.transaction(write=False) as db:
        owned=available(db,game.progression.cid);require(iid in owned,'法宝不在可用背包。');reply=panel(db,game.progression.cid)
    return reply

def spend(db,cid,skill):
    s,owned=current(db,cid)
    require(skill in {owned[i]['skill'] for i in s['slots'] if i},'请先在天、地、人位置装备对应法宝。')
    require(s['essence']>=10,'法宝灵气不足，请在法宝面板补充。');s['essence']-=10;save(db,cid,'artifacts',s)
