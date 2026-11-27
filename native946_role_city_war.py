"""Native city knights, timed missions, alliances and bounded siege settlement."""
import json,secrets
from time import time
from native946_premium_items import license_validity
from native946_role_systems import DATA,state,save,wire,text,notice,require,number,character,pay,take,count,counter,day
from native946_role_society import city,initialize_city,buildings,build_info,own_building,save as save_state

NPC_CITY=900000001
CITY_TASK_LICENSE = 'city_quick_task'
KNIGHT_EXPERIENCE = {46634: 16000, 46988: 20000}
def schema(db):
    for sql in (
      'CREATE TABLE IF NOT EXISTS native_coc_knights(sid TEXT PRIMARY KEY,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,value TEXT NOT NULL)',
      'CREATE TABLE IF NOT EXISTS native_coc_alliances(id INTEGER PRIMARY KEY AUTOINCREMENT,leader_id INTEGER NOT NULL REFERENCES characters(id))',
      'CREATE TABLE IF NOT EXISTS native_coc_alliance_members(character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,alliance_id INTEGER NOT NULL REFERENCES native_coc_alliances(id) ON DELETE CASCADE,reward INTEGER NOT NULL DEFAULT 0)',
      'CREATE TABLE IF NOT EXISTS native_coc_alliance_invites(character_id INTEGER NOT NULL REFERENCES characters(id),alliance_id INTEGER NOT NULL REFERENCES native_coc_alliances(id) ON DELETE CASCADE,expires INTEGER NOT NULL,PRIMARY KEY(character_id,alliance_id))',
    ):db.execute(sql)
def meta(kind):return DATA['coc_knight_info'][number(kind,1,len(DATA['coc_knight_info']),'侠客类型')-1]
def knight_value(kind,sid):return dict(SId=sid,Type=kind,Grade=1,Exp=0,StarLevel=1,StarExp=0,Hp=100,Status=0,BuildingId=0,Mercenary=0,Name=meta(kind)['name'])
def knights(db,cid):return [json.loads(r[0]) for r in db.execute('SELECT value FROM native_coc_knights WHERE character_id=? ORDER BY sid',(cid,))]
def owned(db,cid,sid):
    require(isinstance(sid,str) and 1<=len(sid)<=64,'侠客编号无效。');r=db.execute('SELECT value FROM native_coc_knights WHERE character_id=? AND sid=?',(cid,sid)).fetchone();require(r,'侠客不属于当前角色。');return json.loads(r[0])
def put(db,cid,k):db.execute('UPDATE native_coc_knights SET value=? WHERE character_id=? AND sid=?',(json.dumps(k,ensure_ascii=False),cid,k['SId']))
def recruit(db,cid,kind):
    require(len(knights(db,cid))<50,'侠客携带数量已满。');sid=secrets.token_hex(12);k=knight_value(kind,sid);db.execute('INSERT INTO native_coc_knights VALUES(?,?,?)',(sid,cid,json.dumps(k,ensure_ascii=False)));return k
def initialize(db,cid):
    initialize_city(db,cid);s=state(db,cid,'knights',dict(started=0,pieces={},score=0,tasks={},power=0,records=[],auto_heal=0,hide_mail=0))
    for row in db.execute("SELECT sid,value FROM native_coc_knights WHERE value LIKE '%HomeOwner%'").fetchall():
        k=json.loads(row['value'])
        if k.get('HomeOwner') and k['HireEnd']<=time():
            home=k.pop('HomeOwner');k['HireEnd']=0;k['Mercenary']=1;k['BuildingId']=0;db.execute('UPDATE native_coc_knights SET character_id=?,value=? WHERE sid=?',(home,json.dumps(k,ensure_ascii=False),row['sid']))
    if not s['started']:
        for kind in (1,2,3):recruit(db,cid,kind)
        s['started']=1;save(db,cid,'knights',s)
    return s
def wire_knight(k):
    m=meta(k['Type']);v=dict(k,Icon=m['icon'],Skills=m['skill_ids'],SkillList=m['skill_ids'],Star=k['StarLevel'],Level=k['Grade'],Name=m['name'],SkillIds=m['skill_ids'],TaskId=0,IsDead=int(k['Hp']<=0))
    return v
def update(cid,k):return wire(2849,cid,text(wire_knight(k)))
def listing(db,cid,page=1):
    rows=knights(db,cid);page=number(page,1,10,'侠客页');return wire(2845,cid,len(rows),page,max(1,(len(rows)+7)//8),text([wire_knight(k) for k in rows[(page-1)*8:page*8]]),1)
def task_info(db,cid,s,bid):
    b=own_building(db,cid,bid);t=s['tasks'].get(str(bid),dict(selected=[],end=0,status=0));rows=[owned(db,cid,k) for k in t['selected']];remaining=max(0,t['end']-int(time()))
    now = int(time())
    free_seconds = max(0, license_validity(db, cid, now, key=CITY_TASK_LICENSE) - now)
    return wire(2847,bid,max(1,b['level']),[],2 if t['end'] and not remaining else 1 if t['end'] else 0,text([wire_knight(k) for k in rows]),[],sum(k['Grade']*k['StarLevel'] for k in rows),[0]*4,[100,10,100],'本机侠客任务：600秒，奖励100经验、10建造度和100凝元。',600,0,remaining,10,0,free_seconds)
def busy(s,sid):return any(sid in t['selected'] and t['end'] for t in s['tasks'].values())
def alliance(db,cid):
    row=db.execute('SELECT a.*,m.reward FROM native_coc_alliances a JOIN native_coc_alliance_members m ON m.alliance_id=a.id WHERE m.character_id=?',(cid,)).fetchone();return dict(row) if row else None
def alliance_panel(db,cid):
    a=alliance(db,cid);rows=[]
    if a:
        for r in db.execute('SELECT c.*,m.reward FROM native_coc_alliance_members m JOIN characters c ON c.id=m.character_id WHERE m.alliance_id=?',(a['id'],)):
            rows.append(dict(UserId=r['id'],Id=r['id'],Name=r['name'],Grade=r['level'],Icon=r['role_id'],IsLeader=int(r['id']==a['leader_id']),Reward=r['reward']))
    return wire(2839,a['id'] if a else 0,text(rows))
def native_buildings(db,target,cid):
    if target==NPC_CITY:
        own=buildings(db,cid);return {100000+i:dict(Type=b['kind'],Pos=b['position'],Lev=max(1,b['level']),EndTime=0,Status=0) for i,b in enumerate(own[:5],1)}
    require(target!=cid,'不能进攻自己的城池。');character(db,target);return {b['id']:build_info(b) for b in buildings(db,target)}
def fight_data(db,cid,target):
    enemies=native_buildings(db,target,cid);require(enemies,'目标没有可攻打的城池。');rows=[k for k in knights(db,cid) if k['Hp']>0 and not k['Mercenary']]
    require(rows,'请先准备可出战的侠客。');c=character(db,cid);target_info=dict(icon=c['role_id'],id=target,name='本机演武城' if target==NPC_CITY else character(db,target)['name'],level='本机城池',gang='',server='本机')
    return wire(2929,target,text(enemies),text([wire_knight(k) for k in rows]),100,text(target_info),text({b['id']:build_info(b) for b in buildings(db,cid)}),0)
def siege_budget(db,cid,target,seconds):
    # Revision 946 sends a final destruction list, not individual placements.
    # Bound the accepted destruction by owned knight DPS and elapsed time.
    attack=0
    for k in knights(db,cid):
        if k['Hp']<=0 or k['Mercenary']:continue
        m=DATA['coc_battle_knight'][k['Type']-1];attack+=(m['att_coef_1']*k['Grade']+m['att_coef_2']+m['att_coef_3']*k['StarLevel'])*1000//max(500,m['att_interval'])
    return attack*max(0,min(180,seconds)-3)

def receive(game,db,mid,a):
    cid=game.progression.cid;s=initialize(db,cid);c=city(db,cid)
    if mid==2871:
        hours=number(a[1],1,24,'雇佣时长');row=db.execute('SELECT * FROM native_coc_knights WHERE sid=?',(a[0],)).fetchone();require(row and row['character_id']!=cid,'请选择其他城主发布的佣兵。');k=json.loads(row['value']);require(k['Mercenary'] and not k.get('HomeOwner'),'该佣兵已经被雇佣。');pay(db,cid,hours*1000);db.execute('UPDATE characters SET gold=gold+? WHERE id=?',(hours*900,row['character_id']));k.update(HomeOwner=row['character_id'],HireEnd=int(time())+hours*3600,Mercenary=0,BuildingId=0);db.execute('UPDATE native_coc_knights SET character_id=?,value=? WHERE sid=?',(cid,json.dumps(k,ensure_ascii=False),a[0]));return [update(cid,k),listing(db,cid)]
    if mid==2891:
        target=a[0] or cid;character(db,target);return [listing(db,target,a[1])]
    if mid in (2865,2866,2867,2871,2872,2874,2881,2882,2883,2884,2896,2922,2938):
        k=owned(db,cid,a[0]);require(not busy(s,k['SId']),'侠客执行任务中。')
        require(not k.get('HomeOwner') or mid in (2883,2884,2938),'雇佣侠客只能参与攻城，不能转让、培养或流放。')
        if mid==2883:
            return [wire(2837, k['SId'],
                         [dict(key=kind, value=experience) for kind, experience in KNIGHT_EXPERIENCE.items()],
                         [dict(key=kind, value=count(db, cid, kind)) for kind in KNIGHT_EXPERIENCE])]
        if mid==2884:return [wire(2838,k['SId'],k['Type'],k['StarExp'],1000*(k['StarLevel']+1),k['StarLevel'],min(5,k['StarLevel']+1),[],[],text(knights(db,cid)),[dict(key=46988,value=20000)],k['Grade'],[dict(key=46988,value=count(db,cid,46988))])]
        if mid in (2865,2867):
            kind = a[1]
            if mid==2865:
                require(kind in KNIGHT_EXPERIENCE, '请选择可增加侠客经验的道具。')
                experience = KNIGHT_EXPERIENCE[kind]
            else:
                require(kind==46988,'本机侠客升星使用凝气天书。')
                experience = KNIGHT_EXPERIENCE[46988]
            amount = number(a[2], 1, 99, '培养数量')
            take(db, cid, kind, amount)
            field='Exp' if mid==2865 else 'StarExp';level='Grade' if mid==2865 else 'StarLevel';cap=100 if mid==2865 else 5;k[field]+=amount*experience
            while k[level]<cap and k[field]>=1000*(k[level]+1):k[field]-=1000*(k[level]+1);k[level]+=1
        elif mid==2866:
            other=owned(db,cid,a[1]);require(other['SId']!=k['SId'] and not busy(s,other['SId']) and not other['BuildingId'] and not other.get('HomeOwner'),'请选择空闲的自有侠客。');k['StarExp']+=other['StarExp']+other['StarLevel']*1000;db.execute('DELETE FROM native_coc_knights WHERE sid=?',(other['SId'],))
        elif mid==2874:
            require(not k['BuildingId'] and not k['Mercenary'],'请先召回侠客。');s['pieces'][str(k['Type'])]=s['pieces'].get(str(k['Type']),0)+5;db.execute('DELETE FROM native_coc_knights WHERE sid=?',(k['SId'],));save(db,cid,'knights',s);return [wire(2836,cid,k['SId']),listing(db,cid)]
        elif mid==2872:
            own_building(db,cid,a[1]);require(not any(v['BuildingId']==a[1] for v in knights(db,cid)),'该建筑已经有驻守侠客。');k['BuildingId']=a[1]
        elif mid in (2881,2896):k['BuildingId']=0
        elif mid==2882:require(not k['BuildingId'],'请先取消驻守。');k['Mercenary']=1
        elif mid==2922:k['Mercenary']=0
        elif mid==2871:
            hours=number(a[1],1,24,'雇佣时长');require(k['Mercenary'],'该侠客未参与佣兵。');pay(db,cid,hours*1000);k['HireEnd']=int(time())+hours*3600
        elif mid==2938:require(k['Hp']<100,'侠客无需治疗。');pay(db,cid,(100-k['Hp'])*10);k['Hp']=100
        put(db,cid,k);save(db,cid,'knights',s);return [update(cid,k),listing(db,cid)]
    if mid in (2886,2869,2921,2893,2897,2875,3536,2877,2894):
        if mid==2894:
            rows=buildings(db,cid);return [wire(2848,[b['id'] for b in rows],[b['kind'] for b in rows],[b['level'] for b in rows],[int(s['tasks'].get(str(b['id']),{}).get('end',0)>0) for b in rows],[10]*len(rows))]
        bid=a[0];b=own_building(db,cid,bid);t=s['tasks'].setdefault(str(bid),dict(selected=[],end=0,status=0))
        if mid==2886:return [wire(2840,bid,text([wire_knight(k) for k in knights(db,cid) if not busy(s,k['SId'])]))]
        if mid==2869:
            require(not t['end'],'任务已经开始。');slot=number(a[1],1,4,'侠客位');k=owned(db,cid,a[2]);require(not busy(s,k['SId']) and k['Hp']>0 and not k.get('HomeOwner'),'请选择空闲的自有侠客。');require(k['SId'] not in t['selected'],'不能重复选择侠客。')
            if slot<=len(t['selected']):t['selected'][slot-1]=k['SId']
            else:t['selected'].append(k['SId'])
        elif mid==2921:require(not t['end'],'任务已经开始。');t['selected']=[k['SId'] for k in knights(db,cid) if not busy(s,k['SId']) and k['Hp']>0 and not k.get('HomeOwner')][:4]
        elif mid==2897:
            require(not t['end'] and t['selected'],'请先选择空闲侠客。');counter(s,'task_count',10);t['end']=int(time())+600
        elif mid in (2875,3536):
            now = int(time())
            require(t['end'] > now, '任务已完成或尚未开始。')
            if license_validity(db, cid, now, key=CITY_TASK_LICENSE):
                stamp = day()
                if s.get('free_task_day') != stamp:
                    s['free_task_day'] = stamp
                    s['free_task_buildings'] = []
                used = s['free_task_buildings']
                # The same native request also handles paid acceleration.
                # The licensed shortcut assumes a free completion.
                require(bid not in used, '今天已免费完成过该建筑的任务，请等待当前任务结束。')
                used.append(bid)
            else:
                pay(db, cid, (t['end'] - now) * 10)
            t['end'] = now
        elif mid==2877:
            require(t['end'] and t['end']<=time(),'任务尚未结束或奖励已经领取。')
            for sid in t['selected']:
                k=owned(db,cid,sid);k['Exp']+=100;put(db,cid,k)
            c['resource']+=100;s['score']+=10;t['end']=0;t['selected']=[];save(db,cid,'city',c);db.execute('UPDATE native_coc_alliance_members SET reward=reward+10 WHERE character_id=?',(cid,))
        save(db,cid,'knights',s);return [task_info(db,cid,s,bid),wire(2830,c['resource'])]
    if mid in (2873,2889,2892,2895):
        types=list(range(1,len(DATA['coc_knight_info'])+1))
        if mid==2889:return [wire(2843,[dict(key=k,value=s['pieces'].get(str(k),0)) for k in types],s['score'])]
        if mid==2892:return [wire(2846,types,[20]*len(types),[s['pieces'].get(str(k),0) for k in types])]
        kind=number(a[0],1,len(types),'侠客碎片')
        if mid==2873:
            amount=number(a[1],1,100,'碎片数量');require(s['score']>=10*amount,'侠客积分不足。');s['score']-=10*amount;s['pieces'][str(kind)]=s['pieces'].get(str(kind),0)+amount
        else:require(s['pieces'].get(str(kind),0)>=20,'本机招募需要20片对应侠客碎片。');s['pieces'][str(kind)]-=20;recruit(db,cid,kind)
        save(db,cid,'knights',s);return [listing(db,cid),wire(2843,[dict(key=k,value=s['pieces'].get(str(k),0)) for k in types],s['score'])]
    if mid==2888:return [wire(2842,text([wire_knight(k) for k in knights(db,cid)]))]
    if mid==2887:
        rows=[json.loads(r[0]) for r in db.execute('SELECT value FROM native_coc_knights WHERE character_id<>? LIMIT 200',(cid,)) if json.loads(r[0]).get('Mercenary') and not json.loads(r[0]).get('HomeOwner')][:50];return [wire(2841,len(rows),text(rows),c['resource'])]
    if mid in (2868,2870,2876,2878,2879,2880,2885,2890):
        member=alliance(db,cid)
        if mid==2885 and not member:
            invitations=db.execute('SELECT alliance_id FROM native_coc_alliance_invites WHERE character_id=? AND expires>? ORDER BY alliance_id LIMIT 10',(cid,int(time()))).fetchall()
            if invitations:
                game.dialog_actions=[dict(kind='role_alliance',alliance=r[0]) for r in invitations]
                return [game.menu('请选择要加入的同盟。',['加入同盟 '+str(r[0]) for r in invitations],cid)]
        if mid==2870:
            require(not member,'已加入同盟。');pay(db,cid,50000);aid=db.execute('INSERT INTO native_coc_alliances(leader_id) VALUES(?)',(cid,)).lastrowid;db.execute('INSERT INTO native_coc_alliance_members VALUES(?,?,0)',(cid,aid))
        elif mid==2879:
            require(member and member['leader_id']==cid,'只有盟主可以邀请。');require(len(a[0])<=20,'邀请数量过多。')
            for uid in set(a[0]):
                require(uid!=cid and not alliance(db,uid),'对方已加入同盟。');character(db,uid);db.execute('INSERT INTO native_coc_alliance_invites VALUES(?,?,?) ON CONFLICT(character_id,alliance_id) DO UPDATE SET expires=excluded.expires',(uid,member['id'],int(time())+86400))
        elif mid==2878:
            invite=db.execute('SELECT * FROM native_coc_alliance_invites WHERE character_id=? AND alliance_id=? AND expires>?',(cid,a[0],int(time()))).fetchone();require(invite,'邀请已失效。');number(a[1],0,1,'邀请选择')
            if a[1]:require(not member,'已加入其他同盟。');require(db.execute('SELECT count(*) FROM native_coc_alliance_members WHERE alliance_id=?',(a[0],)).fetchone()[0]<5,'同盟已满。');db.execute('INSERT INTO native_coc_alliance_members VALUES(?,?,0)',(cid,a[0]))
            db.execute('DELETE FROM native_coc_alliance_invites WHERE character_id=? AND alliance_id=?',(cid,a[0]))
        elif mid==2868:
            require(member and member['leader_id']==cid and db.execute('SELECT 1 FROM native_coc_alliance_members WHERE character_id=? AND alliance_id=?',(a[0],member['id'])).fetchone(),'请选择同盟成员移交盟主。');db.execute('UPDATE native_coc_alliances SET leader_id=? WHERE id=?',(a[0],member['id']))
        elif mid==2880:
            require(member,'尚未加入同盟。')
            if member['leader_id']==cid:require(db.execute('SELECT count(*) FROM native_coc_alliance_members WHERE alliance_id=?',(member['id'],)).fetchone()[0]==1,'请先移交盟主。');db.execute('DELETE FROM native_coc_alliances WHERE id=?',(member['id'],))
            else:db.execute('DELETE FROM native_coc_alliance_members WHERE character_id=?',(cid,))
        elif mid==2876:
            require(member and member['reward']>0,'没有待领取的同盟任务奖励。');c['resource']+=member['reward'];save(db,cid,'city',c);db.execute('UPDATE native_coc_alliance_members SET reward=0 WHERE character_id=?',(cid,))
        elif mid==2890:
            page=number(a[0],1,255,'邀请页');rows=db.execute('SELECT id,name,role_id,level FROM characters WHERE id<>? ORDER BY id LIMIT 10 OFFSET ?',(cid,(page-1)*10)).fetchall();return [wire(2844,page,page,5,text([dict(UserId=r['id'],Name=r['name'],Icon=r['role_id'],Grade=r['level']) for r in rows]))]
        return [alliance_panel(db,cid),wire(2830,c['resource'])]
    if mid==2940:return [wire(2932,s['power'],0,s['hide_mail'],s['auto_heal'])]
    if mid in (5129,3743):s['auto_heal' if mid==5129 else 'hide_mail']=number(a[0],0,1,'提示设置');save(db,cid,'knights',s);return [wire(2932,s['power'],0,s['hide_mail'],s['auto_heal'])]
    if mid==2944:
        number(a[0],0,2,'搜索方式');pay(db,cid,100);s['targets']=[NPC_CITY]+[r[0] for r in db.execute('SELECT DISTINCT character_id FROM native_coc_buildings WHERE character_id<>? LIMIT 9',(cid,))];save(db,cid,'knights',s)
        return [wire(2931,100,text({uid:dict(Id=uid,Name='本机演武城' if uid==NPC_CITY else character(db,uid)['name'],Power=100) for uid in s['targets']}),[100]*len(s['targets']))]
    if mid in (2945,2946,2943):
        target=a[0]
        if mid==2943:
            index=number(target,1,len(s['records']),'复仇记录');target=s['records'][-index]['target']
        require(target in s.get('targets',[]) or any(r['target']==target for r in s['records']),'请先搜索目标。')
        if mid in (2946,2943):
            if s.get('siege') and s['siege']['start']+600<time():s.pop('siege',None)
            require(not s.get('siege'),'请先结束当前攻城。');counter(s,'sieges',10);s['siege']=dict(target=target,start=int(time()),id=secrets.token_hex(12),buildings=native_buildings(db,target,cid));save(db,cid,'knights',s)
            if mid==2946:return [notice('攻城开始。')]
        return [fight_data(db,cid,target)]
    if mid in (2937,3217):
        if mid==3217:
            # Client-reported casualties cannot create/upgrade owned knights.
            return []
        run=s.get('siege');require(run and run['target']==a[0],'攻城会话不存在或已经结算。')
        elapsed=int(time())-run['start'];require(0<=elapsed<=600,'攻城时长无效，请重新开始。')
        from native946_lua_values import table
        raw=table(a[1]);require(isinstance(raw,(dict,list)) and len(raw)<=100,'建筑结算数据无效。');ids=[]
        for key,value in (raw.items() if isinstance(raw,dict) else enumerate(raw,1)):
            bid=int(value.get('Id',value.get('id',key))) if isinstance(value,dict) else int(key) if isinstance(raw,dict) else int(value)
            require(str(bid) in run['buildings'] or bid in run['buildings'],'不能结算战斗外的建筑。');ids.append(bid)
        require(len(ids)==len(set(ids)),'不能重复结算建筑。');hp=0
        require(not ids or elapsed>=5,'攻城时长不足以破坏建筑。')
        for bid in ids:
            b=run['buildings'].get(str(bid),run['buildings'].get(bid));m=DATA['coc_battle_building'][b['Type']-1];hp+=m['hp_coef_1']*b['Lev']+m['hp_coef_2']
        require(hp<=siege_budget(db,cid,run['target'],elapsed),'上报破坏量超过当前侠客与战斗时长允许的范围。')
        reward=100*len(ids);c['resource']+=reward;s['power']+=10*len(ids);s['score']+=len(ids);s['records'].append(dict(target=run['target'],time=int(time()),reward=reward,destroyed=len(ids)));s['records']=s['records'][-100:];s.pop('siege',None)
        for k in knights(db,cid):k['Hp']=max(0,k['Hp']-10);put(db,cid,k)
        save(db,cid,'city',c);save(db,cid,'knights',s);return [wire(2934,str(reward),'',str(10*len(ids))),wire(2830,c['resource'])]
    if mid==2942:return [wire(2933,text(s['records']))]
    if mid in (2939,2941):
        if mid==2941:
            cost=sum((100-k['Hp'])*10 for k in knights(db,cid));pay(db,cid,cost)
            for k in knights(db,cid):k['Hp']=100;put(db,cid,k)
        rows=[dict(SId=k['SId'],Type=k['Type'],Name=k['Name'],Hp=k['Hp'],Cost=(100-k['Hp'])*10) for k in knights(db,cid) if k['Hp']<100];return [wire(2930,text(rows),sum(r['Cost'] for r in rows))]
    if mid==3138:
        require(s['records'],'请先完成一次攻城。');counter(s,'sieges',10);pay(db,cid,1000);reward=min(100,s['records'][-1]['reward']);c['resource']+=reward;save(db,cid,'city',c);save(db,cid,'knights',s);return [wire(2934,str(reward),'','0'),wire(2830,c['resource'])]
    if mid==2919:
        typ=number(a[0],1,3,'城建榜');page=number(a[1],1,100,'排行页');values=[]
        for r in db.execute('SELECT DISTINCT character_id FROM native_coc_buildings'):
            uid=r[0];v=state(db,uid,'knights',dict(power=0));values.append((uid,v['power'] if typ==1 else sum(b['level'] for b in buildings(db,uid))))
        values.sort(key=lambda v:(-v[1],v[0]));rank=next((i for i,v in enumerate(values,1) if v[0]==cid),0);return [wire(2918,typ,rank,max(1,(len(values)+9)//10),page,[text(dict(Id=uid,Name=character(db,uid)['name'],Score=score)) for uid,score in values[(page-1)*10:page*10]],str(next((v for uid,v in values if uid==cid),0)),0,'本机榜单无额外周期奖')]
    if mid==2936:return [notice('当前本机城建榜没有待领取的周期奖励。')]
    raise ValueError('攻城侠客操作无效。')

def accept_alliance(game,aid):
    require(game.battle is None,'请先结束战斗。')
    with game.progression.transaction() as db:out=receive(game,db,2878,[aid,1])
    game.dialog_open=False
    return game.defer_packets(lambda:(*out,*game.progression.sync()))
