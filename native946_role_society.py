"""Local officer careers, child development and native city construction.

All mutating operations use the caller's transaction and real owned resources.
Missing server-only prices are disclosed in the data file and panel text.
"""
import json,secrets
from time import time
from native946_role_systems import DATA,RULES,state,save,wire,text,notice,require,number,character,grade,pay,take,count,give,counter

OFFICIAL_NAMES=['未入仕','预备役','从九品','正九品','从八品','正八品','从七品','正七品','从六品','正六品','从五品','正五品','从四品','正四品','从三品','正三品','从二品','正二品','从一品','正一品']
OFFICIAL_ATTRS={'气血':('hpmax',100),'法力':('mpmax',100),'攻击':('attack',20),'防御':('defense',50),'速度':('initiative',2),'抗混乱':('ReDisorder',5),'抗封印':('ReSeal',5)}
SKILL_NAMES={1:'坚守',2:'文心',3:'法印',4:'忠勇',5:'霸临',6:'玄思',7:'凝力',8:'风后',9:'文官风华',10:'武官风华',11:'文官赤心',12:'武官赤心',100:'文官特技',200:'通晓'}
def official(db,cid):return state(db,cid,'official',dict(kind=0,level=0,merit=0,prestige=0,skills={},bars={},attributes=[],task=None,hide_title=0,hide_follower=0))
def officer_attributes(s):return {k:(s['level']*OFFICIAL_ATTRS[k][1] if k in s['attributes'] else 0) for k in OFFICIAL_ATTRS}
def official_info(db,cid,s):
    equipped=[s['bars'].get(str(i),0) for i in range(1,4)];special=100 if s['kind']==1000 else 200
    return wire(639,s['merit'],max(1,s['level'])*100,1 if s['level']<19 else 0,s['level'],min(19,s['level']+1),OFFICIAL_NAMES[s['level']],text(officer_attributes(s)),equipped,[SKILL_NAMES.get(k,'未装备') for k in equipped],['本机官职能力 '+str(s['skills'].get(str(k),0))+'阶' for k in equipped],special,SKILL_NAMES[special],'官职升阶后解锁。',s['kind'],0,0,[],s['hide_title'],s['hide_follower'],s['prestige'],max(1,s['level'])*4)
def officer_skill(s,key):
    desc=('四品以上武官，风后达到1、3、5级时分别解锁火羽涅槃阵、风后云方阵、玄剑诛仙阵。' if key==8 else '本机通晓：自身所在阵位效果每级提高5%。' if key==200 else '本机官职培养，每阶提供相应属性。')
    lv=s['skills'].get(str(key),0);return dict(skill_id=key,name=SKILL_NAMES[key],level=lv,can_up=int(lv<5),desc=desc,cur_level_desc=str(lv)+'阶',next_level_desc=str(min(5,lv+1))+'阶')
def skill_info(s):
    bars=[dict(skill_id=s['bars'].get(str(i),0),can_operate=int(s['level']>=i*2-1),desc='官职'+str(i*2-1)+'阶解锁') for i in range(1,4)]
    return wire(654,min(3,(s['level']+1)//2),bars,s['kind'],[officer_skill(s,k) for k in (1,2,3,9,11)],[officer_skill(s,k) for k in (4,5,7,8,10,12)])
def promotion(db,cid,s):
    c=character(db,cid);lv=max(1,s['level']);return wire(651,s['level'],min(19,s['level']+1),c['gold'],RULES['official_upgrade_cash']*lv,s['merit'],100*lv,str(c['experience']),RULES['official_upgrade_exp']*lv,int(s['level']<19),s['prestige'],4*lv)
def task_list(s):
    task=s.get('task');desc='本机巡查：在任意野外战斗中获胜3次后再次点击领取功绩。'
    if task:desc='巡查进度 '+str(task['kills'])+'/'+str(task['need'])+'，完成后点击交付。'
    return wire(656,text([dict(Key='local_patrol',TaskName='交付巡查' if task else '领取巡查',LeftCount=1,TaskDesc=desc)]))
def posts(db,cid,available=False,page=1):
    where='taken_by IS NULL AND done=0 AND character_id<>?' if available else 'character_id=?'
    rows=db.execute('SELECT p.*,c.name,c.level FROM native_official_posts p JOIN characters c ON c.id=p.character_id WHERE '+where+' ORDER BY p.id LIMIT 50 OFFSET ?',(cid,(page-1)*50 if available else 0)).fetchall()
    result=[dict(task_id=r['id'],name=r['name'],showid=r['character_id'],level=str(r['level']),reward=r['reward'],left_times=0 if r['done'] else r['amount'],left_time=0) for r in rows]
    return wire(646,result,page,max(1,page)) if available else wire(647,result,sum(r['done'] for r in rows),0,10)
def receive_official(game,db,mid,a):
    cid=game.progression.cid;s=official(db,cid)
    if mid==673:
        number(a[0],1,6,'官职页');return [wire(644,a[0]),official_info(db,cid,s),*([wire(643,0)] if not s['kind'] else [])]
    if mid==671:
        require(a[0] in (1000,2000),'官职方向无效。');require(not s['kind'],'已入仕，请使用重选官职方向。');require(grade(db,cid)>=100,'官职需达到100级。');s['kind']=a[0];s['level']=1
    elif mid==678:
        require(s['kind'] in (1000,2000),'请先入仕。');pay(db,cid,50000);s['kind']=3000-s['kind'];s['bars']={}
    elif mid==660:
        s['promotion_quote']=dict(level=s['level'],expires=int(time())+120);save(db,cid,'official',s);return [promotion(db,cid,s)]
    elif mid==669:
        q=s.get('promotion_quote');require(q and q['level']==s['level'] and q['expires']>=time(),'升官考核已变化，请重新查看消耗。');require(s['kind'] and s['level']<19,'请先入仕或已达到最高官阶。');lv=max(1,s['level']);require(s['merit']>=100*lv and s['prestige']>=4*lv,'功绩或威望不足。');pay(db,cid,RULES['official_upgrade_cash']*lv,RULES['official_upgrade_exp']*lv);s['merit']-=100*lv;s['prestige']-=4*lv;s['level']+=1;s.pop('promotion_quote',None)
    elif mid==672:
        page=number(a[0],1,6,'官职页')
        if page==2:
            values=list(officer_attributes(s).values());return [wire(659,s['level'],OFFICIAL_NAMES[s['level']],text(officer_attributes(s)),list(OFFICIAL_ATTRS),[dict(level=i,eval_type=1,**{'var'+str(k+1):values[k] for k in range(7)}) for i in range(1,s['level']+1)])]
        if page==3:return [skill_info(s)]
        if page==4:return [wire(655,s['kind'],officer_skill(s,100),officer_skill(s,200))]
        if page>=5:return [task_list(s)]
        return [official_info(db,cid,s)]
    elif mid==661:
        choices=a[0]+a[1];require(1<=len(choices)<=7 and len(set(choices))==len(choices) and all(k in OFFICIAL_ATTRS for k in choices),'请选择面板提供的不重复官职属性。');require(not s['attributes'],'已经选过官职属性，请使用属性重置。');s['attributes']=choices
    elif mid==670:
        require(a[0]=='local_patrol','官职任务已变化，请重新打开任务列表。');require(s['kind'],'请先入仕。')
        if not s['task']:counter(s,'tasks',RULES['official_daily_tasks']);s['task']=dict(kills=0,need=3,post=0)
        else:
            task=s['task'];require(task['kills']>=task['need'],'巡查尚未完成。');s['merit']+=RULES['official_task_merit'];s['prestige']+=4
            reward=RULES['official_task_cash']
            if task.get('post'):
                post=db.execute('SELECT * FROM native_official_posts WHERE id=? AND taken_by=? AND done=0',(task['post'],cid)).fetchone();require(post,'悬赏任务已结算。');reward+=post['reward']*post['amount'];db.execute('UPDATE native_official_posts SET done=1 WHERE id=?',(post['id'],))
            db.execute('UPDATE characters SET gold=gold+? WHERE id=?',(reward,cid));s['task']=None
    elif mid==675:
        amount=number(a[0],1,10,'悬赏数量');reward=number(a[1],1,10000000,'悬赏单价');counter(s,'posts',10);pay(db,cid,amount*reward);db.execute('INSERT INTO native_official_posts(character_id,amount,reward) VALUES(?,?,?)',(cid,amount,reward))
    elif mid==674:
        require(s['task'] is None,'请先完成已领取任务。');post=db.execute('SELECT * FROM native_official_posts WHERE id=? AND character_id=? AND taken_by IS NULL AND done=0',(a[0],a[1])).fetchone();require(post and post['character_id']!=cid,'悬赏已领取或不存在。');require(a[2] in ('','local_patrol'),'悬赏标记无效。');db.execute('UPDATE native_official_posts SET taken_by=? WHERE id=?',(cid,post['id']));s['task']=dict(kills=0,need=3*post['amount'],post=post['id'])
    elif mid==676:
        post=db.execute('SELECT * FROM native_official_posts WHERE id=? AND character_id=? AND taken_by IS NULL AND done=0',(a[0],cid)).fetchone();require(post,'已被领取的悬赏不能撤回。');db.execute('UPDATE characters SET gold=gold+? WHERE id=?',(post['reward']*post['amount'],cid));db.execute('DELETE FROM native_official_posts WHERE id=?',(post['id'],))
    elif mid==666:return [posts(db,cid,True,number(a[0],1,10000,'悬赏页'))]
    elif mid in (667,668):return [posts(db,cid)]
    elif mid in (681,664):
        key=number(a[0],1,200,'官职技能');require(key in SKILL_NAMES and ((s['kind']==1000 and key in (1,2,3,9,11,100)) or (s['kind']==2000 and key in (4,5,7,8,10,12,200))),'技能不属于当前官职方向。');lv=s['skills'].get(str(key),0);need=(lv+1)*100
        if mid==681:
            c=character(db,cid);return [wire(658,officer_skill(s,key),s['merit'],need,c['gold'],10000*(lv+1),str(c['experience']),100000*(lv+1),int(lv<5))]
        require(s['level']>=lv+1 and lv<5 and s['merit']>=need,'官职等级、功绩不足或技能已满阶。');pay(db,cid,10000*(lv+1),100000*(lv+1));s['merit']-=need;s['skills'][str(key)]=lv+1
    elif mid==665:
        slot=number(a[0],1,3,'技能位');key=number(a[1],0,200,'官职技能');require(s['level']>=slot*2-1,'该技能位尚未解锁。');require(not key or (key in SKILL_NAMES and s['skills'].get(str(key),0)>0),'尚未习得该官职技能。');require(not key or key not in [v for k,v in s['bars'].items() if k!=str(slot)],'同一技能不能重复装备。');s['bars'][str(slot)]=key
    elif mid==2419:s['hide_title']=number(a[0],0,1,'隐藏称谓');s['hide_follower']=number(a[1],0,1,'隐藏随从')
    elif mid in (679,663,680):
        if mid==679:return [wire(649,text(s['attributes']),text([k for k in OFFICIAL_ATTRS if k not in s['attributes']]),text([]),text([]),100000,10000,10)]
        if mid==663:
            old,new=a;require(old in s['attributes'] and new in OFFICIAL_ATTRS and new not in s['attributes'],'可替换属性已变化。');require(s['merit']>=10,'功绩不足。');pay(db,cid,10000,100000);s['merit']-=10;s['attributes'][s['attributes'].index(old)]=new
        else:
            from native946_lua_values import table
            selected=table(a[1]);require(isinstance(selected,(dict,list)),'属性选择无效。');choices=list(selected.values()) if isinstance(selected,dict) else selected;require(len(choices)==len(s['attributes']) and len(set(choices))==len(choices) and all(k in OFFICIAL_ATTRS for k in choices),'属性选择无效。');pay(db,cid,10000,100000);s['attributes']=choices
    elif mid in (677,662,682):
        lv=number(a[0],1,s['level'],'评鉴官阶')
        if mid==677:
            c=character(db,cid);return [wire(653,1,lv,s['merit'],10,c['gold'],10000,str(c['experience']),100000,1,text(officer_attributes(s)),text(officer_attributes(s)),1,1,text(officer_attributes(s)))]
        if mid==662:
            require(s['merit']>=10,'评鉴需要10功绩。');pay(db,cid,10000,100000);s['merit']-=10;s['recheck']=dict(level=lv,bonus=secrets.randbelow(3)+1,expires=int(time())+120)
        else:
            pending=s.get('recheck');require(pending and pending['level']==lv and pending['expires']>=time(),'评鉴结果已失效。');number(a[1],1,2,'评鉴选择')
            if a[1]==1:s['bonus']=max(s.get('bonus',0),pending['bonus'])
            s.pop('recheck',None)
    else:raise ValueError('官职操作参数无效。')
    save(db,cid,'official',s)
    if mid in (671,678) and not s['attributes']:return [official_info(db,cid,s),wire(641,s['kind'],text(list(OFFICIAL_ATTRS)),text(list(OFFICIAL_ATTRS)))]
    return [official_info(db,cid,s),*([task_list(s)] if mid==670 else []),*([skill_info(s)] if mid in (664,665) else [])]

def baby_defaults(cid):return dict(name='小宝',icon=401,type=401,grade=0,exp=0,wuxing=0,qinmi=0,status=0,main=1,qizhi=0,neili=0,naili=0,moyi=0,title=0,halo=0,skills={},travel=None,records=[])
def baby(db,cid,bid):
    row=db.execute('SELECT * FROM native_babies WHERE id=? AND character_id=?',(bid,cid)).fetchone();require(row,'孩子不属于当前角色。');return dict(id=row['id'],**json.loads(row['value']))
def save_baby(db,cid,b):
    value={k:v for k,v in b.items() if k!='id'};db.execute('UPDATE native_babies SET value=? WHERE id=? AND character_id=?',(json.dumps(value,ensure_ascii=False),b['id'],cid))
def baby_list(db,cid,selected=0,item_type=0,*,choose_child=False):
    from native946_child_fashion import children, fashions
    rows=children(db,cid)
    selected_index = next((i for i,r in enumerate(rows,1) if r['id']==selected), 0 if choose_child else int(bool(rows)))
    return wire(3054,[r['icon'] for r in rows],[r['id'] for r in rows],selected_index,item_type,[int(bool(fashions(db,cid,r))) for r in rows])
def baby_property(db,cid,b):
    from native946_role_battle import child_values
    b=dict(b,**child_values(b))
    skills=[dict(skill_id=int(k),name=DATA['baby_skill'].get(k,{}).get('name','孩子技能'),desc=DATA['baby_skill'].get(k,{}).get('desc',''),grade=lv) for k,lv in b['skills'].items()]
    return wire(3057,b['id'],b['name'],b['icon'],str(b['grade'])+'岁',character(db,cid)['name'],0,b['exp'],1000*(b['grade']+1),b['qinmi'],b['main'],str(b[['qizhi','neili','naili','moyi'][b['main']-1]]),b['wuxing'],(b['grade']+1)*1000,str(b['qizhi']),str(b['neili']),str(b['naili']),str(b['moyi']),skills,[1]*len(skills),'本机养育',b['title'],0,0,b.get('page',1000),0,0)
def baby_buttons(db,cid,b):
    from native946_child_fashion import worn_model
    from native946_child_halos import active_halo
    return wire(3048,b['id'],b['type'],b['name'],'本机养育',active_halo(b),list(range(1,12)),[1]*11,b['status'] or 3,worn_model(db,cid,b))
def baby_study(b):return wire(3064,b['id'],[46863],b['wuxing'],(b['grade']+1)*1000,'本机：每次道德经增加1000悟性；私塾研习增加100悟性，消耗10000现金。',list(map(int,b['skills'])),list(b['skills'].values()),[1]*len(b['skills']),[],46863,RULES['baby_study_item_wuxing'])
def baby_travel_finish(db,cid,b):
    t=b.get('travel')
    if t and t['end']<=time():
        b['exp']+=1000;b['qinmi']+=10;b['records'].append(dict(time=int(time()),map_id=t['map'],name='本机闯荡',index=len(b['records'])+1,special=0,desc='闯荡归来，获得1000成长经验、10亲密。'));b['records']=b['records'][-100:];b['travel']=None;save_baby(db,cid,b)
def adopt(game,db):
    cid=game.progression.cid;require(grade(db,cid)>=1030,'本机领养需一转30级。');require(db.execute('SELECT count(*) FROM native_babies WHERE character_id=?',(cid,)).fetchone()[0]<4,'最多养育4个孩子。');pay(db,cid,RULES['baby_adopt_cash']);b=baby_defaults(cid)
    from native946_role_baby_tools import pages
    b['page']=next(p for p in range(1000,1050) if p not in pages(db,cid))
    b['id']=db.execute('INSERT INTO native_babies(character_id,value) VALUES(?,?)',(cid,json.dumps(b,ensure_ascii=False))).lastrowid
    return [baby_list(db,cid,b['id']),baby_buttons(db,cid,b),baby_property(db,cid,b),notice('本机领养完成，消耗100000现金。')]
def receive_baby(game,db,mid,a):
    from native946_child_halos import RULES as HALOS, apply, choose, panel as halo_panel, select

    cid=game.progression.cid
    if mid==3083 and str(a[0]) in HALOS:
        return [choose(game, db, a[0])]
    if mid==3083:return [baby_list(db,cid,item_type=a[0]),notice('本机育儿：可到天姥处领养；私塾、闯荡和成长记录均独立保存。')]
    if mid==3073:
        from native946_child_fashion import children, fashions
        number(a[0],1,3,'孩子装备页')
        rows=children(db,cid)
        return [wire(3055,[r['icon'] for r in rows],[r['id'] for r in rows],
                     [r.get('page',1000+i) for i,r in enumerate(rows)],
                     [int(bool(fashions(db,cid,r))) for r in rows])]
    b=baby(db,cid,a[0]);baby_travel_finish(db,cid,b)
    if mid==3072:
        name=a[1];require(1<=len(name.encode('gb18030'))<=12 and all(c.isalnum() or '\u4e00'<=c<='\u9fff' for c in name),'孩子名仅可包含汉字、字母和数字，最多6个汉字。');pay(db,cid,10000);b['name']=name
    elif mid==3074:require(a[1] in (401,402,403,404,405,406),'孩子造型无效。');b['icon']=a[1];b['type']=a[1]
    elif mid==3087:
        status=number(a[1],0,3,'孩子状态')
        # The client sends 3 for resting. Older saves and requests use 0.
        status=0 if status==3 else status
        require(not b['travel'] or status==0,'闯荡中的孩子不能随行。')
        if status:
            for row in db.execute('SELECT id,value FROM native_babies WHERE character_id=? AND id<>?',(cid,b['id'])).fetchall():
                v=json.loads(row['value']);v['status']=0;db.execute('UPDATE native_babies SET value=? WHERE id=?',(json.dumps(v,ensure_ascii=False),row['id']))
        b['status']=status
    elif mid==3092:
        require(not b['travel'],'请等待孩子闯荡归来。')
        if a[1]:require(a[1]==46863,'请选择道德经。');take(db,cid,46863);b['wuxing']+=RULES['baby_study_item_wuxing']
        else:pay(db,cid,RULES['baby_study_cash']);b['wuxing']+=RULES['baby_study_wuxing']
        key=['qizhi','neili','naili','moyi'][b['main']-1];b[key]+=10;b['qinmi']+=1
    elif mid==3098:
        require(not b['travel'],'请先完成闯荡。');require(b['grade']<RULES['baby_max_grade'],'孩子已成年。');need=1000*(b['grade']+1);use_item=number(a[1],0,1,'成长方式')
        if use_item:take(db,cid,46863,b['grade']+1)
        else:require(b['exp']>=need and b['wuxing']>=need,'成长经验或悟性不足。');b['exp']-=need
        b['grade']+=1
    elif mid in (3095,3096):
        dest=number(a[1],1,12,'闯荡地图');number(a[2],1,3,'闯荡类型')
        if mid==3096:return [wire(3068,b['id'],dest,'本机闯荡耗时10分钟，返回后增加成长经验和亲密。',0,0,len(b['records']),100,'每次增加1000成长经验、10亲密。',RULES['baby_travel_cash'],a[2])]
        require(not b['travel'] and not b['status'],'请先让孩子休息，且不可重复派出。');counter(b,'travel_day_count',10);pay(db,cid,RULES['baby_travel_cash']);b['travel']=dict(map=dest,end=int(time())+RULES['baby_travel_seconds'])
    elif mid==3097:
        page=number(a[1],1,100,'闯荡记录页');return [wire(3069,b['id'],page,max(1,(len(b['records'])+9)//10),len(b['records']),100,'本机闯荡记录',[dict(r,desc=r['desc']) for r in reversed(b['records'])][(page-1)*10:page*10])]
    elif mid in (3088,3089,3090,3091):
        index=number(a[1],1,6,'孩子技能位')
        keys=sorted(map(int,DATA['baby_skill']));skill=keys[(index-1)%len(keys)]
        if mid==3090:return [wire(3062,b['id'],index,skill,'本机：消耗1本道德经学习此技能；提升消耗随等级递增。')]
        op=number(a[2],1,3,'孩子技能操作') if mid==3089 else 1
        if op==3:
            require(str(skill) in b['skills'],'此技能尚未学习。');take(db,cid,46863);del b['skills'][str(skill)]
        else:
            lv=b['skills'].get(str(skill),0);require(lv<10,'技能已满级。');require(b['grade']>=index-1,'孩子年龄不足以使用该技能位。')
            if mid==3088:
                from native946_inventory_features import owned
                require(owned(db,cid,a[2])['type']==46863,'请选择道德经。')
            take(db,cid,46863,lv+1);b['skills'][str(skill)]=lv+1
    elif mid in (3093,3094):
        title=number(a[1],1,6,'孩子称谓');require(b['grade']>=title*3 and b['wuxing']>=title*1000,'尚未达到称谓的年龄与悟性条件。')
        if mid==3093:b['title']=title
        else:return [wire(3065,b['id'],title,'本机称谓需'+str(title*3)+'岁、'+str(title*1000)+'悟性。','增加随行孩子的属性加成。',0,'')]
    elif mid in (2457,2458):
        if mid==2458:
            require(str(a[1]) in DATA['baby_aura'], '光环不存在。')
        select(b, a[1] if mid==2458 else 0)
        save_baby(db,cid,b)
        return [baby_buttons(db,cid,b), halo_panel(db,cid,b)]
    elif mid==3075 and str(a[1]) in HALOS:
        apply(game, db, b, a[1])
        save_baby(db,cid,b)
        from native946_fashion import Fashion
        return [baby_buttons(db,cid,b), halo_panel(db,cid,b), *Fashion(game).sync()]
    elif mid==3099:require(not b['travel'],'闯荡不能通过中止卖艺提前完成。')
    elif mid in (3075,3076,3084,3086):
        if mid==3086:return [wire(3059,b['id'],'随行时：每100气质增加8速度；每100内力增加250法力；每100耐力增加50攻击、162防御；每100墨意增加250气血。')]
        if mid==3076:
            button=number(a[1],1,11,'育儿按钮')
            if button in (2,3):return [wire(3070,b['id'],character(db,cid)['role_id'],b['icon'],46863,b['grade']+1,['成长经验达到'+str((b['grade']+1)*1000),'悟性达到'+str((b['grade']+1)*1000),'完成私塾研习','完成闯荡','也可消耗道德经替代成长条件','本机养育成长'],1,1,int(b['exp']>=(b['grade']+1)*1000 and b['wuxing']>=(b['grade']+1)*1000))]
            if button==6:return [baby_study(b)]
            if button==5:return [wire(3056,b['id'],len(b['records']),100,'本机闯荡10分钟/次，完成后记录独立保存。',1)]
            if button==7:return [wire(3066,b['id'],b['icon'],0,'本机称谓','按年龄和悟性逐级解锁。',['初学','勤学','博识','才俊','名士','贤达'],[int(b['grade']>=(i+1)*3 and b['wuxing']>=(i+1)*1000) for i in range(6)])]
            if button==9:return [halo_panel(db,cid,b)]
            if button==11:
                from native946_child_fashion import panel
                return [panel(db,cid,b)]
    else:raise ValueError('育儿操作无效。')
    save_baby(db,cid,b)
    return [baby_list(db,cid,b['id']),baby_buttons(db,cid,b),baby_property(db,cid,b),*([baby_study(b)] if mid==3092 else []),notice('孩子正在闯荡，完成时间已保存。') if mid==3095 else notice('育儿资料已保存。')]

def city(db,cid):return state(db,cid,'city',dict(resource=0,claimed=0,seal_level=0,seal_exp=0,seal_forge=0,traits={}))
def buildings(db,cid):return [dict(r) for r in db.execute('SELECT * FROM native_coc_buildings WHERE character_id=? ORDER BY id',(cid,))]
def building_meta(kind):return DATA['coc_building'][number(kind,1,len(DATA['coc_building']),'建筑类型')-1]
def cells(kind,pos):
    m=building_meta(kind);x,y=divmod(number(pos,1,999999,'建筑位置'),1000);return {(x+a)*1000+y+b for a in range(m['x']) for b in range(m['y'])}
def position(db,cid,kind,pos,skip=0):
    covered=cells(kind,pos);require(all(str(k) in DATA['coc_buildable'] for k in covered),'建筑位置超出可建造范围。')
    for b in buildings(db,cid):
        if b['id']!=skip:require(not covered.intersection(cells(b['kind'],b['position'])),'建筑不能重叠。')
def build_info(b):return dict(Type=b['kind'],Pos=b['position'],Lev=b['level'],EndTime=b['ready'],End=b['ready'],Status=int(b['ready']>time()),ResourceTime=b['harvest'],Knight=0)
def initialize_city(db,cid):
    s=city(db,cid)
    if not s['claimed']:
        require(grade(db,cid)>=100,'攻城拔寨需达到100级。')
        # The starter grant is a once-only city setup, never a panel reward.
        s['resource']=RULES['coc_initial_resource'];s['claimed']=1
        pos=next((int(k) for k in DATA['coc_buildable'] if all(str(c) in DATA['coc_buildable'] for c in cells(1,int(k)))),None);require(pos,'没有合法的初始城池位置。')
        db.execute('INSERT INTO native_coc_buildings(character_id,kind,position,level,harvest) VALUES(?,?,?,1,?)',(cid,1,pos,int(time())));save(db,cid,'city',s)
    finish_city(db,cid);return s
def finish_city(db,cid):db.execute('UPDATE native_coc_buildings SET level=level+1,ready=0 WHERE character_id=? AND ready>0 AND ready<=?',(cid,int(time())))
def city_layer(db,cid):return wire(2829,cid,text({b['id']:build_info(b) for b in buildings(db,cid)}))
def own_building(db,cid,bid):
    b=db.execute('SELECT * FROM native_coc_buildings WHERE id=? AND character_id=?',(bid,cid)).fetchone();require(b,'建筑不属于当前角色。');return dict(b)
def receive_city(game,db,mid,a):
    cid=game.progression.cid
    if mid==2860:
        uid=a[0] or cid;character(db,uid)
        if uid==cid:s=initialize_city(db,cid)
        else:s=city(db,uid);finish_city(db,uid)
        return [city_layer(db,uid),wire(2830,s['resource'])]
    s=initialize_city(db,cid)
    if mid in (2854,2855,2856,2857,2858):
        if mid==2858:
            need=1000*(s['seal_level']+1);require(s['resource']>=need,'升级城主之印的凝元不足。');require(s['seal_level']<30,'城主之印已满级。');s['resource']-=need;s['seal_level']+=1
        elif mid==2854:
            require(s['seal_level']>0,'请先培养城主之印。');require(s['resource']>=1000,'凝元不足。');s['resource']-=1000;s['seal_forge']+=1
        elif mid==2856:
            pay(db,cid,10000);s['pending_traits']=dict(attack=secrets.randbelow(100)+1,defense=secrets.randbelow(100)+1)
        elif mid==2857:
            number(a[0],1,2,'重铸选择');require('pending_traits' in s,'没有待选择的重铸结果。')
            if a[0]==1:s['traits']=s['pending_traits']
            s.pop('pending_traits',None)
        save(db,cid,'city',s)
        if mid==2856:return [wire(2827,10000,0,text(s['traits']),text(s['pending_traits']))]
        return [wire(2826,s['seal_level'],s['seal_exp'],1000*(s['seal_level']+1),s['seal_forge'],'本机城主之印每级增加20攻击、20防御。',0,0,0,text(s['traits']),''),wire(2830,s['resource'])]
    if mid==2863:
        kind,pos=a;meta=building_meta(kind);position(db,cid,kind,pos);rows=buildings(db,cid);main=max([b['level'] for b in rows if b['kind']==1]+[0]);require(main>=meta['unlock_level'],'白曜殿等级不足。');limit=max([meta.get('limit'+str(lv),0) for lv in range(main+1)]+[0]);require(sum(b['kind']==kind for b in rows)<limit,'同类建筑已达数量上限。');require(s['resource']>=meta['cost'],'凝元不足。');s['resource']-=meta['cost'];end=int(time())+RULES['coc_upgrade_seconds'];bid=db.execute('INSERT INTO native_coc_buildings(character_id,kind,position,ready,harvest) VALUES(?,?,?,?,?)',(cid,kind,pos,end,int(time()))).lastrowid;save(db,cid,'city',s);return [wire(2832,bid,kind,pos,end),wire(2830,s['resource'])]
    b=own_building(db,cid,a[0]);meta=building_meta(b['kind'])
    if mid==2851:return [wire(2824,b['id'],text(dict(NeedMoney=RULES['coc_upgrade_resource']*(b['level']+1),NeedTime=RULES['coc_upgrade_seconds']*(b['level']+1),Desc=meta.get('effect'+str(min(30,b['level']+1)),''),Cost=RULES['coc_upgrade_resource']*(b['level']+1),Time=RULES['coc_upgrade_seconds']*(b['level']+1))))]
    if mid==2864:
        require(not b['ready'] and b['level']<30,'建筑正在建造或已满级。');main=max([r['level'] for r in buildings(db,cid) if r['kind']==1]+[0]);require(b['kind']==1 or b['level']<main,'请先提升白曜殿。');cost=RULES['coc_upgrade_resource']*(b['level']+1);require(s['resource']>=cost,'凝元不足。');s['resource']-=cost;db.execute('UPDATE native_coc_buildings SET ready=? WHERE id=?',(int(time())+RULES['coc_upgrade_seconds']*(b['level']+1),b['id']))
    elif mid==2850:
        require(b['ready']>time(),'该建筑没有需要加速的建造。');pay(db,cid,max(1,b['ready']-int(time()))*RULES['coc_acceleration_cash_per_second']);db.execute('UPDATE native_coc_buildings SET ready=0,level=level+1 WHERE id=?',(b['id'],))
    elif mid==2859:require(not b['ready'],'建造时间尚未结束。')
    elif mid==2852:
        require(b['kind']!=1,'白曜殿不能拆除。');require(not b['ready'],'建造中的建筑不能拆除。');db.execute('DELETE FROM native_coc_buildings WHERE id=?',(b['id'],));return [wire(2825,b['id'])]
    elif mid==2862:require(not b['ready'],'请等待建造完成再移动。');position(db,cid,b['kind'],a[1],b['id']);db.execute('UPDATE native_coc_buildings SET position=? WHERE id=?',(a[1],b['id']))
    elif mid==2861:
        require(b['level']>0 and not b['ready'],'请等待建筑完成。');ticks=(int(time())-b['harvest'])//RULES['coc_harvest_seconds'];require(ticks>0,'资源尚未产出。');boost_end=s.get('double',{}).get(str(b['id']),0);boost_ticks=max(0,min(int(time()),boost_end)-b['harvest'])//RULES['coc_harvest_seconds'];gain=(min(ticks,1440)+2*min(ticks,boost_ticks,1440))*RULES['coc_harvest_per_level']*b['level'];s['resource']+=gain;db.execute('UPDATE native_coc_buildings SET harvest=? WHERE id=?',(int(time()),b['id']))
    elif mid==2853:pay(db,cid,10000);s.setdefault('double',{})[str(b['id'])]=int(time())+3600
    else:raise ValueError('城池操作无效。')
    save(db,cid,'city',s);updated=own_building(db,cid,b['id'])
    return [wire(2835,b['id'],text(build_info(updated))),wire(2830,s['resource']),*([wire(2834,b['id'],updated['ready'])] if mid==2864 else []),*([wire(2828,b['id'])] if mid in (2850,2859) else [])]

def receive(game,db,mid,args):
    if mid in range(660,683) or mid==2419:return receive_official(game,db,mid,args)
    if mid in range(2850,2865):return receive_city(game,db,mid,args)
    return receive_baby(game,db,mid,args)

def effects(db,cid):
    result={};s=official(db,cid)
    for label,value in officer_attributes(s).items():
        key=OFFICIAL_ATTRS[label][0];result[key]=result.get(key,0)+value
    for sid in s['bars'].values():
        lv=s['skills'].get(str(sid),0)
        for key,value in ({1:{'defense':100},2:{'mpmax':100},3:{'ReSeal':5},4:{'attack':20},5:{'hpmax':100},7:{'attack':20},9:{'ReFaint':5},10:{'ReDisorder':5},11:{'initiative':2},12:{'defense':100}}.get(sid,{})).items():result[key]=result.get(key,0)+lv*value
    for row in db.execute('SELECT value FROM native_babies WHERE character_id=?',(cid,)):
        b=json.loads(row[0])
        if b['status'] and not b['travel']:
            from native946_role_battle import child_values
            b=dict(b,**child_values(b))
            result['hpmax']=result.get('hpmax',0)+b['moyi']*250//100;result['mpmax']=result.get('mpmax',0)+b['neili']*250//100;result['attack']=result.get('attack',0)+b['naili']//2;result['defense']=result.get('defense',0)+b['naili']*162//100;result['initiative']=result.get('initiative',0)+b['qizhi']*8//100
    c=city(db,cid)
    for k,v in c['traits'].items():result[k]=result.get(k,0)+v
    for k in ('attack','defense'):result[k]=result.get(k,0)+20*c['seal_level']
    from native946_role_baby_tools import bonuses
    for k,v in bonuses(db,cid).items():result[k]=result.get(k,0)+v
    return result

def role_actor(npc):
    if npc.get('name')=='天姥':return 'baby'
    if npc.get('name') in ('官职助理','预备役助理'):return 'official'
def npc_menu(game,npc):
    from native946_npc_services import NpcServices
    kind=role_actor(npc)
    if kind=='baby':return NpcServices(game)._menu(npc['npc_id'],'本机育儿：一转30级后，可花100000现金领养。',[('领养孩子（100000现金）',dict(kind='role_system',op='adopt')),('查看养育资料',dict(kind='role_system',op='baby'))])
    if kind=='official':return NpcServices(game)._menu(npc['npc_id'],'请选择官职事务。',[('官职资料',dict(kind='role_system',op='official')),('官职巡查任务',dict(kind='role_system',op='task')),('完成本机升官考核',dict(kind='role_system',op='promotion'))])
def npc_execute(game,nid,action):
    from native946_npc_services import NpcServices
    npc=NpcServices(game).validate(nid);kind=role_actor(npc);op=action['op'];cid=game.progression.cid
    require((kind=='baby' and op in ('adopt','baby')) or (kind=='official' and op in ('official','task','promotion')),'该NPC不办理此事务。')
    with game.progression.transaction() as db:
        if op=='adopt':out=adopt(game,db)
        elif op=='baby':out=[baby_list(db,cid)]
        else:
            s=official(db,cid)
            if op=='promotion':
                require(s['kind'] and s['level']<19,'请先入仕或已达到最高官阶。');lv=max(1,s['level']);require(s['merit']>=100*lv and s['prestige']>=4*lv,'功绩或威望不足。');pay(db,cid,RULES['official_upgrade_cash']*lv,RULES['official_upgrade_exp']*lv);s['merit']-=100*lv;s['prestige']-=4*lv;s['level']+=1;save(db,cid,'official',s)
            out=[task_list(s)] if op=='task' else [wire(644,1),official_info(db,cid,s),*([wire(643,0)] if not s['kind'] else [])]
    return game.defer_packets(lambda:(*out,*game.progression.sync()))
