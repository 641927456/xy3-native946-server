"""Native star realm, gold body, realm points and school talents."""
import json,re,secrets
from time import time
from native946_role_systems import DATA,RULES,state,save,wire,text,notice,require,number,character,grade,pay,take,count,counter

def gold(db,cid):return state(db,cid,'goldbody',dict(ranks=[0]*10,exp=0,forge={},eff_time=0,colors={}))
def color_packet(game,skill):
    with game.progression.transaction(write=False) as db:colors=gold(db,game.progression.cid)['colors']
    if str(skill) not in colors:return ()
    from native946_codec import packet
    # 3167 is queued as actor_skill_color inside the current native action.
    # The client forwards it to its own mulmagic renderer; no invented spell
    # IDs or replacement visual files are required.
    return (packet(3167,['number'],[colors[str(skill)]]),)
def jing(db,cid):return state(db,cid,'jing',dict(level=0,exp=0))
def talents(db,cid):return state(db,cid,'talent',dict(levels={},switches=0,zhenqi=0))
def school(db,cid):
    return int(state(db,cid,'school',{}).get('school',0))
def cfg():return DATA['school_talent_config']
def talent_levels(db,cid):
    sid=school(db,cid);rows=cfg()['SchoolTalentConfig'].get(str(sid),[]);s=talents(db,cid)
    return {m['SkillId']:s['levels'].get(str(m['TalentIndex']),0) for m in rows}


# Classic 3.0.904 publishes the level-10 bonuses by equipment attribute.
EQUIPMENT_TALENT_BONUSES = {
    7010: dict(cor=20, mag=20, hpmax=20, mpmax=20, strength=10, speed=10, attack=10, initiative=10),
    7013: dict(cor=10, mag=10, hpmax=10, mpmax=10, strength=20, speed=15, attack=20, initiative=15),
}


def boost_equipment(db,cid,core,direct,secondary):
    levels=talent_levels(db,cid);rate=max(levels.get(7010,0),levels.get(7013,0))*10
    full_ranks = [bonuses for skill, bonuses in EQUIPMENT_TALENT_BONUSES.items() if levels.get(skill,0)>=10]
    for mapping in (core,direct):
        for key,value in mapping.items():
            if mapping is core or key in ('hpmax','mpmax','attack','initiative'):
                per_mille=max((rate,*(bonuses.get(key,0)*10 for bonuses in full_ranks)))
                mapping[key]=value+value*per_mille//1000
    for skill,prefixes in ((7002,('AddSeal','AddDisorder','AddFaint','IgSeal','IgDisorder','IgFaint')),(7006,('AddFire','AddWater','AddWind','AddThunder','IgFire','IgWater','IgWind','IgThunder'))):
        rate=levels.get(skill,0)*10
        if rate:
            for key,value in secondary.items():
                if key.startswith(prefixes):secondary[key]=value+value*rate//1000
def school_choices(db,cid):
    c=character(db,cid)
    return {
        (1,1):[1,2,4], (1,2):[2,3,4],
        (2,1):[9,10,12], (2,2):[10,11,12],
        (3,1):[5,6,7], (3,2):[5,7,8],
        (7,1):[15,17,18], (7,2):[16,17,18],
    }.get((c['race'],c['sex']),[])

WEST_SECONDARY_SCHOOLS = {1:(4,2), 2:(10,12), 3:(7,5), 7:(17,18)}


def west_school(db,cid):
    from native946_training import state as training_state

    c=character(db,cid)
    stage=training_state(db,cid)
    if c['sex']!=1 or not (stage['ascension'] or stage['sanctification']):return 0
    school_id=c['race']*100+19
    return school_id if str(school_id) in cfg()['SchoolConfig'] else 0


def learning_schools(db,cid):
    sid=school(db,cid)
    if sid in (19,119,219,319,719):
        return (19,*WEST_SECONDARY_SCHOOLS[character(db,cid)['race']])
    return school_choices(db,cid)
def choose_school(game,index):
    require(game.battle is None,'请先结束战斗。');cid=game.progression.cid
    with game.progression.transaction() as db:
        require(index in school_choices(db,cid),'所选门派与当前种族不匹配。');require(school(db,cid)==0,'已经加入门派。');save(db,cid,'school',dict(school=index))
        out=talent_panel(db,cid,talents(db,cid))
    game.dialog_open=False
    return game.defer_packets(lambda:(out,*game.progression.sync()))
def unlocked(db,cid,index,s):
    m=cfg()['TalentConfig'][index-1]
    if grade(db,cid)<m['UnlockUserGrade']:return False
    previous={2:[1],3:[2],4:[2],5:[3,4],6:[5],7:[6],8:[6]}.get(index,[])
    return not previous or max(s['levels'].get(str(n),0) for n in previous)>=m.get('UnlockPreGrade',0)
def talent_desc(school_id,index,level):
    entry=cfg()['SchoolTalentConfig'][str(school_id)][index-1]
    desc=cfg()['SkillDescConfig'].get(str(entry['SkillId']),{}).get('Desc','')
    return entry['TalentName']+' '+str(level)+'级#r'+desc+'#r本机培养：属性按等级线性成长；效果以服务器结算为准。'
def talent_infos(db,cid,s,sid):
    result={}
    for i in range(1,9):
        lv=s['levels'].get(str(i),0);other={3:4,4:3,7:8,8:7}.get(i,0)
        status=4 if lv>=10 else 3 if lv else 2 if unlocked(db,cid,i,s) else 1
        if other and s['levels'].get(str(other),0):status=1
        result[i]=dict(Status=status,Grade=lv,Desc=talent_desc(sid,i,lv),UnlockMsg=cfg()['TalentConfig'][i-1].get('UnlockMsg',''),SwitchIndex=other if lv else 0)
    return result
def talent_panel(db,cid,s):
    sid=school(db,cid);require(str(sid) in cfg()['SchoolConfig'],'尚未加入门派，请先拜师后修习门派天赋。')
    return wire(5019,sid,text(talent_infos(db,cid,s,sid)),character(db,cid)['experience'],s['zhenqi'],1)
def talent_upgrade(db,cid,index,s):
    sid=school(db,cid);require(str(sid) in cfg()['SchoolConfig'],'请先加入门派。');number(index,1,8,'天赋序号')
    lv=s['levels'].get(str(index),0)
    from native946_training import state as training_state
    return wire(5021,sid,index,lv,character(db,cid)['experience'],s['zhenqi'],training_state(db,cid)['tongbao']+character(db,cid)['gold'],talent_desc(sid,index,lv),talent_desc(sid,index,min(10,lv+1)))

def conditions(db,cid,row):
    from native946_hero_extra_points import values
    c=character(db,cid);t=talents(db,cid);o=state(db,cid,'official',{});l=state(db,cid,'lingyou',dict(unlocked=[]))
    f=state(db,cid,'lineup',dict(levels={}));g=gold(db,cid);facts=state(db,cid,'milestones',{})
    extra=sum(values(db,cid).values())
    children=[json.loads(r[0]) for r in db.execute('SELECT value FROM native_babies WHERE character_id=?',(cid,))]
    def check(desc):
        n=re.search(r'\d+',desc);n=int(n[0]) if n else 0
        if desc.startswith('飞升'):return grade(db,cid)>=12000+n
        if desc.startswith('封圣'):return grade(db,cid)>=112000+n
        if desc.startswith('人物附加'):return extra>=n
        if desc.startswith('灵佑卡'):return len(l['unlocked'])>=n
        if desc.startswith('子女悟性'):return max([b.get('wuxing',0) for b in children]+[0])>=n
        if desc.startswith('官职系统'):
            title=desc.removeprefix('官职系统达到');names=['预备役','从九品','正九品','从八品','正八品','从七品','正七品','从六品','正六品','从五品','正五品','从四品','正四品','从三品','正三品','从二品','正二品','从一品','正一品']
            return title in names and o.get('level',0)>=names.index(title)+1
        if desc.startswith('1个进阶阵法'):return max([v for k,v in f['levels'].items() if int(k)>=9]+[0])>=int(re.search(r'(\d+)级',desc)[1])
        if desc.startswith('六脉修炼'):
            levels=[r[0] for r in db.execute('SELECT level FROM cultivated_skills WHERE character_id=?',(cid,))]
            return len(levels)>=6 and min(levels)>=n
        if desc.startswith('门派天赋'):
            layers={'二':2,'四':4,'六':6};limit=next((v for k,v in layers.items() if k in desc),0)
            return all(max([t['levels'].get(str(i),0) for i,m in enumerate(cfg()['TalentConfig'],1) if m['TalentLayer']==layer]+[0])>=10 for layer in range(1,limit+1))
        if desc.startswith('金身解锁'):
            nums={'一':1,'五':5,'十':10};rank=next((v for k,v in nums.items() if k+'境' in desc),10)
            return g['ranks'][rank-1]>=int(re.search(r'(\d+)层',desc)[1])
        if desc.startswith('华丽度达到') and n:
            from native946_fashion import score
            return score(db,cid)>=n
        # Other systems publish their actual earned milestones to this ledger.
        # An absent fact remains locked rather than silently bypassing a gate.
        if desc.startswith('达到') and '10天' in desc:
            realm={'一':1,'三':3,'五':5,'七':7,'九':9};r=next((v for k,v in realm.items() if k+'境' in desc),99)
            reached=state(db,cid,'star',dict(reached={}))['reached'].get(str(r))
            return bool(reached) and reached<=int(time())-864000
        return bool(facts.get(desc,False))
    return [int(check(row.get('CheckCondDesc'+str(i),''))) for i in range(1,5)]
def star_panel(db,cid,s):
    rows=DATA['xingqiongzhijing']['LevelConfig'];n=min(s['level'],len(rows)-1)
    today=s.get('study',0) if s.get('study_day')==__import__('native946_role_systems').day() else 0
    return wire(5110,s['level'],s['exp'],conditions(db,cid,rows[n]),today)
def gold_panel(db,cid,s):return wire(3177,s['ranks'],max(0,s['eff_time']))
def gold_practice(db,cid,rank,s):
    number(rank,1,10,'金身境位');lv=s['ranks'][rank-1];race=str(character(db,cid)['race']);rows=DATA['god_body_buff_config']['info'].get(race,DATA['god_body_buff_config']['info']['1']);desc=rows[rank-1]['desc']
    cost=RULES['goldbody_practice_exp']*(lv+1)*rank
    return wire(3178,rank,lv,desc+'#r当前按满级效果的'+str(lv*10)+'%生效。',desc+'#r本机线性培养：下一级'+str(min(100,(lv+1)*10))+'%。',str(RULES['goldbody_practice_cash']*rank),str(RULES['goldbody_user_exp']*rank),'凝气经验 '+str(cost),s['exp'],cost)
def jing_panel(db,cid,s):
    raw=db.execute('SELECT cor,mag,strength,speed FROM progression WHERE character_id=?',(cid,)).fetchone()
    from native946_training import state as training_state
    resist={r['attribute']:r['value'] for r in db.execute('SELECT * FROM resistance_points WHERE character_id=?',(cid,))}
    # The original 946 panel (018d16f6/[0,0]) indexes these keys directly.
    attributes=[dict(key=key,value=value) for key,value in zip(('Cor','Mag','Str','Spe'),raw)]
    keys=('FirePoint','WaterPoint','WindPoint','ThunderPoint','FaintPoint','SealPoint',
          'DisorderPoint','PoisonPoint','GushuPoint','AbsorbPoint')
    resists=[dict(key=key,value=resist.get(key,0)) for key in keys]
    level=s['level']
    next_level=level+1 if level<RULES['jing_max_level'] else 0
    desc='本机升级消耗人物经验，每重增加4点人物属性和1点抗性。' if next_level else ''
    current=f'本机化境{level}重。配点栏显示角色当前基础配点。'
    return wire(2548,level,current,next_level,desc,character(db,cid)['experience'],
                RULES['jing_upgrade_exp']*next_level,str(level*RULES['jing_point_per_level']),
                attributes,str(training_state(db,cid)['earned_points']),resists)


JING_RESET_UNAVAILABLE='化境重悟暂未开放，原版重悟规则待核实。'

def receive(game,db,mid,a):
    cid=game.progression.cid
    if mid in (5111,5112):
        s=state(db,cid,'star',dict(level=0,exp=0));config=DATA['xingqiongzhijing'];rows=config['LevelConfig']
        if mid==5112:
            op=number(a[0],1,3,'星穹操作');require(s['level']<len(rows),'已达到最高星穹境界。');row=rows[s['level']]
            require(grade(db,cid)>=config['BaseConfig']['GradeLimit'],'星穹之境需达到飞升150级。')
            if op in (1,2):
                times=1 if op==1 else 10;require(s['exp']<row['NeedExp'],'研习已满，请提升境界。')
                times=min(times,(row['NeedExp']-s['exp']+config['BaseConfig']['AddExp']-1)//config['BaseConfig']['AddExp'])
                from native946_role_systems import day
                left=config['BaseConfig']['DailyAddExpLimit']-(s.get('study',0) if s.get('study_day')==day() else 0);times=min(times,left);require(times>0,'今日星穹研习次数已用完。')
                counter(s,'study',config['BaseConfig']['DailyAddExpLimit'],times)
                pay(db,cid,exp=config['BaseConfig']['NeedUserExp']*times,fund=config['BaseConfig']['NeedFund']*times);s['exp']=min(row['NeedExp'],s['exp']+times*config['BaseConfig']['AddExp'])
            else:
                require(s['exp']>=row['NeedExp'],'星穹研习经验不足。');require(all(conditions(db,cid,row)),'尚未满足面板列出的突破条件。')
                for kind,amount in row['NeedItemList']:take(db,cid,kind,amount)
                s['level']+=1;s['exp']=0;s.setdefault('reached',{})[str(s['level'])]=int(time())
            save(db,cid,'star',s)
        return [star_panel(db,cid,s)]
    if mid in (2549,2550,2551,2799):
        s=jing(db,cid)
        if mid==2550:
            number(a[0],1,2,'重悟类型')
            raise ValueError(JING_RESET_UNAVAILABLE)
        if mid==2799:
            raise ValueError('道境无边兑换暂未开放，原版兑换条件及价格待核实。')
        if mid==2551:
            from native946_cultivation import SANCTIFICATION_LIMIT
            from native946_training import state as training_state

            require(training_state(db,cid)['sanctification'] and character(db,cid)['level']>=SANCTIFICATION_LIMIT,
                    '化境需封圣185级。')
            require(s['level']<RULES['jing_max_level'],'化境已达最高重。')
            cost=RULES['jing_upgrade_exp']*(s['level']+1);pay(db,cid,exp=cost);s['level']+=1
            db.execute('UPDATE progression SET points=points+? WHERE character_id=?',(RULES['jing_point_per_level'],cid))
            db.execute('UPDATE cultivation_state SET earned_points=earned_points+? WHERE character_id=?',(RULES['jing_resist_per_level'],cid));save(db,cid,'jing',s)
        return [jing_panel(db,cid,s)]
    if mid==5022:
        op,index=a;number(op,1,6,'天赋操作');s=talents(db,cid);sid=school(db,cid)
        if op==1 and sid==0:
            choices=school_choices(db,cid);require(choices,'当前种族没有门派天赋配置。')
            game.dialog_actions=[dict(kind='role_school',school=k) for k in choices]
            return [game.menu('请先拜师。选择一个门派可前往拜访师父。',[cfg()['SchoolConfig'][str(k)]['Name'] for k in choices],cid)]
        require(str(sid) in cfg()['SchoolConfig'],'尚未加入门派，请先拜师。')
        if op==2:return [talent_upgrade(db,cid,index,s)]
        if op==3:
            number(index,1,8,'天赋序号');other={3:4,4:3,7:8,8:7}.get(index);require(other is not None,'该天赋没有分支。')
            current=index if s['levels'].get(str(index),0) else other;target=other if current==index else index
            lv=s['levels'].get(str(current),0);require(lv>0,'请先激活该层天赋。')
            return [wire(5020,sid,text(dict(TalentIndex=current,Grade=lv,SkillDesc=talent_desc(sid,current,lv))),text(dict(TalentIndex=target,Grade=lv,SkillDesc=talent_desc(sid,target,lv))),min(5,s['switches']+1))]
        if op==4:
            number(index,1,8,'天赋序号');require(unlocked(db,cid,index,s),'尚未满足天赋等级或前置条件。');lv=s['levels'].get(str(index),0);require(lv<10,'天赋已满级。')
            other={3:4,4:3,7:8,8:7}.get(index);require(not other or not s['levels'].get(str(other),0),'该层已选择另一分支，请使用分支切换。')
            row=cfg()['UpgradeConfig'][cfg()['TalentConfig'][index-1]['UpgradeIndex']-1][lv]
            require(s['zhenqi']>=row['NeedZhenQi'],'真气不足，实际战斗结算可积累真气。');pay(db,cid,exp=row['NeedExp'],fund=row['NeedFund']);s['zhenqi']-=row['NeedZhenQi'];s['levels'][str(index)]=lv+1
        if op==5:
            number(index,1,8,'天赋序号');other={3:4,4:3,7:8,8:7}.get(index);require(other and s['levels'].get(str(other),0)>0 and not s['levels'].get(str(index),0),'天赋分支状态已变化。')
            pay(db,cid,fund=cfg()['SwitchConfig'][min(4,s['switches'])]['NeedFund']);s['switches']+=1;s['levels'][str(index)]=s['levels'].pop(str(other))
        if op==6:
            require(any(s['levels'].values()),'没有可重置的天赋。');pay(db,cid,fund=RULES['talent_reset_fund'])
            # Refund exactly the paid native resources; retain the reset fee.
            cash_exp=qi=fund=0
            for key,lv in s['levels'].items():
                for row in cfg()['UpgradeConfig'][cfg()['TalentConfig'][int(key)-1]['UpgradeIndex']-1][:lv]:cash_exp+=row['NeedExp'];qi+=row['NeedZhenQi'];fund+=row['NeedFund']
            db.execute('UPDATE characters SET experience=experience+? WHERE id=?',(cash_exp,cid));db.execute('UPDATE cultivation_state SET tongbao=tongbao+? WHERE character_id=?',(fund,cid));s['zhenqi']+=qi;s['levels']={}
        save(db,cid,'talent',s)
        return [talent_panel(db,cid,s),*([talent_upgrade(db,cid,index,s)] if op==4 else [])]
    s=gold(db,cid)
    if mid in (3181,3182):
        rank=number(a[0],1,10,'金身境位')
        if mid==3181:
            require(grade(db,cid)>=2101,'金身修炼需二转101级。');lv=s['ranks'][rank-1];require(lv<10,'该境已满级。');require(rank==1 or s['ranks'][rank-2]==10,'请先修满上一境。')
            cost=RULES['goldbody_practice_exp']*(lv+1)*rank;require(s['exp']>=cost,'凝气经验不足，可使用凝气天书或参与实际战斗积累。');pay(db,cid,RULES['goldbody_practice_cash']*rank,RULES['goldbody_user_exp']*rank);s['exp']-=cost;s['ranks'][rank-1]+=1;save(db,cid,'goldbody',s)
        return [gold_panel(db,cid,s),gold_practice(db,cid,rank,s)]
    if mid in (3183,3184):
        learned=[r[0] for r in db.execute('SELECT skill_id FROM learned_skills WHERE character_id=? ORDER BY skill_id',(cid,))][:4]
        if mid==3183:
            from native946_lua_values import table
            data=table(a[0]);require(isinstance(data,(dict,list)),'技能颜色请求无效。');rows=list(data.values()) if isinstance(data,dict) else data
            require(len(rows)<=4,'技能颜色数量无效。')
            for row in rows:
                require(isinstance(row,dict) and row.get('SkillId') in learned,'技能不属于当前角色。');selection=number(row.get('Select'),0,min(3,sum(s['ranks'])//20),'技能颜色');s['colors'][str(row['SkillId'])]=selection
            save(db,cid,'goldbody',s)
        return [wire(3179,text([dict(SkillId=k,IndexList=list(range(min(3,sum(s['ranks'])//20)+1)),MenuList=['原色','金色','蓝色','紫色'][:min(3,sum(s['ranks'])//20)+1],NowIndex=s['colors'].get(str(k),0)) for k in learned]))]
    if mid in (4290,4291,4292,4293):
        config=DATA['goldbody_forge']['BaseConfig'];pending=s.get('pending')
        if mid==4290:
            lock=number(a[0],0,10,'锁定金身');require(min(s['ranks'])==10,'金身全部满级后才可洗髓。');require(not lock or str(lock) in s['forge'],'锁定词条已不存在。')
            take(db,cid,46988,config['LockForgeCostItem'] if lock else config['ForgeCostItem']);cost=config['LockForgeCostExp'] if lock else config['ForgeCostExp'];require(s['exp']>=cost,'凝气经验不足。');s['exp']-=cost
            rolled={str(lock):s['forge'][str(lock)]} if lock else {};amount=(2 if secrets.randbelow(1000)>=config['ForgeThreeOdds'] else 3)
            choices=[i for i in range(1,11) if i!=lock]
            for _ in range(amount-len(rolled)):
                i=choices.pop(secrets.randbelow(len(choices)));rolled[str(i)]=secrets.randbelow(4)+1
            s['pending']=dict(value=rolled,expires=int(time())+120,lock=lock);save(db,cid,'goldbody',s)
            return [wire(4288,text({int(k):v for k,v in s['forge'].items()}),text({int(k):v for k,v in rolled.items()}),lock)]
        if mid==4291:
            number(a[0],1,2,'洗髓选择');require(pending and pending['expires']>=time(),'洗髓结果已失效。')
            if a[0]==1:s['forge']=pending['value']
            s.pop('pending',None)
        if mid==4293:
            number(a[0],1,2,'充能方式');require(bool(s['forge']),'请先洗髓。');now=int(time());require(max(now,s['eff_time'])+7*86400<=now+config['EffDayMax']*86400,'充能将超过60天上限。')
            if a[0]==1:take(db,cid,46988,config['EffCostItemNums'])
            else:require(s['exp']>=config['EffCostExpNums'],'凝气经验不足。');s['exp']-=config['EffCostExpNums']
            s['eff_time']=max(now,s['eff_time'])+config['EffOnceTime']*86400
        if mid!=4292:save(db,cid,'goldbody',s)
        return [wire(4289,count(db,cid,46988),s['exp'],s['eff_time'],text({int(k):v for k,v in s['forge'].items()}))]
    return [gold_panel(db,cid,s)]

def effects(db,cid):
    result={};s=gold(db,cid);race=str(character(db,cid)['race']);rows=DATA['goldbody_endpoints'].get(race,DATA['goldbody_endpoints']['1'])
    for i,lv in enumerate(s['ranks']):
        boost=DATA['goldbody_forge']['ForgeConfig'][s['forge'][str(i+1)]-1]['AttDegree'] if s['eff_time']>time() and str(i+1) in s['forge'] else 0
        for key,value in rows[i].items():result[key]=result.get(key,0)+value*lv*(1000+boost)//10000
    star=state(db,cid,'star',dict(level=0,exp=0))
    if star['level']:
        for k,v in DATA['xingqiongzhijing']['LevelConfig'][star['level']-1]['AttrList'].items():result[k]=result.get(k,0)+v
    t=talents(db,cid);sid=school(db,cid)
    if str(sid) in cfg()['SchoolTalentConfig']:
        for index,lv in t['levels'].items():
            skill=cfg()['SchoolTalentConfig'][str(sid)][int(index)-1]['SkillId']
            # Numeric fallback is local: defensive talents gain 1% per grade.
            if skill in (7004,7008,7011,7016):
                for key in ('ReWind','ReThunder','ReWater','ReDisorder','ReSeal','ReFaint'):result[key]=result.get(key,0)+10*lv
            elif skill==7003:
                strength=db.execute('SELECT strength FROM progression WHERE character_id=?',(cid,)).fetchone()[0]
                result['hpmax']=result.get('hpmax',0)+strength*lv;result['attack']=result.get('attack',0)+strength*lv//10
            elif skill==7043:result['RoleIgnoreDefense']=lv*10
            elif skill==7015:result['RoleElementReduction']=lv*10
            elif skill==7012:
                speed=db.execute('SELECT speed FROM progression WHERE character_id=?',(cid,)).fetchone()[0]
                result['ReGushu']=result.get('ReGushu',0)+speed*lv//10
            elif skill==7132:result['initiative']=result.get('initiative',0)+lv*5
            elif skill==7401:
                cor=db.execute('SELECT cor FROM progression WHERE character_id=?',(cid,)).fetchone()[0]
                for key in ('ReDisorder','ReSeal','ReFaint'):result[key]=result.get(key,0)+cor*lv//100
            elif skill==7441:result['RoleAttackBuffScale']=lv*10
    return result
