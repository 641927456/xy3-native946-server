"""946 formations: owned training, five-person battle seats and native effects."""
import re
from fractions import Fraction
from native946_codec import packet
from native946_role_systems import DATA,RULES,state,save,wire,text,notice,require,number,grade,pay,take,counter
from native946_role_systems import day as today

WIRE_VERSION=2
CATALOG=DATA['new_lineup'];BOOKS={6694,47973,47974}
LEARN_GRADES={1:1050,2:1080,3:1110,4:2050,5:2080,6:2110,7:2140}
PLAIN=re.compile(r'#c[0-9a-fA-F]{6}|#[GWRYgn]')
SCALAR=re.compile(r'\s*return function\(LineupLevel\) return (-?\d+(?:\.\d+)?)(?:\*math.max\(LineupLevel,0.5\))?(/10)? end\s*')
ALIASES={'物理防御':'defense','攻击':'attack','速度':'initiative','气血':'hpmax','法力':'mpmax',
 '命中':'AttValue','躲闪':'DodValue','强火':'AddFire','强水':'AddWater','强雷':'AddThunder','强风':'AddWind',
 '抗火':'ReFire','抗水':'ReWater','抗雷':'ReThunder','抗风':'ReWind','抗蛊术':'ReGushu','加强蛊术':'AddGushu',
 '加强消融':'AddXiaorongRate','加强再生':'RoleLineupHeal','治疗':'RoleLineupHeal','回合回血':'role_hp_regen',
 '伤害结果提升':'RoleLineupDamage','物理伤害结果提升':'RoleLineupPhysical','法术伤害结果提升':'RoleLineupSpell',
 '受到物理伤害结果减免':'RoleLineupPhysicalReduction','受到法术伤害结果减免':'RoleLineupSpellReduction',
 '被人法命中降低':'RoleLineupControlResist','速度提升':'RoleLineupSpeed','速度降低':'RoleLineupSpeed',
 '强毒伤害':'RoleLineupPoison','抗毒伤害':'RoleLineupPoisonReduction','强毒命中':'RoleLineupPoisonHit','加强中毒':'AddPoison',
 '抗毒命中':'RePoisonAtt','加强混乱':'AddDisorder','加强封印':'AddSeal','加强昏睡':'AddFaint',
 '抗混乱':'ReDisorder','抗混':'ReDisorder','抗封印':'ReSeal','抗昏睡':'ReFaint','抗睡':'ReFaint',
 '抗震慑':'ReMpAbsorb','加强震慑':'RoleLineupShock','加强加攻':'RoleLineupAttackBuff',
 '加强加速':'RoleLineupSpeedBuff','加强加防':'RoleLineupGuardBuff','反击率':'FanjiRate','狂暴率':'CruelRate',
 '致命率':'FatalRate','连击率':'RoleLineupCombo','抗浩然':'ReHaoran','抗隔山':'ReGeshan',
 '抗分光':'ReFenguang','抗小楼':'ReXiaolou','抗解体':'ReJieti'}
HUMAN={'AddDisorder','AddSeal','AddFaint','AddPoison','RoleLineupPoisonHit','ReDisorder','ReSeal','ReFaint','RePoisonAtt'}

def record(db,cid):return state(db,cid,'lineup',dict(levels={},practice={}))

def meta(index):
    require(type(index) is int and (index in LEARN_GRADES or 9<=index<=25),'请选择可学习的阵法。')
    return CATALOG['LineupLevelConfig'][str(index)]

def levels(db,cid):
    result={int(k):int(v) for k,v in record(db,cid)['levels'].items() if int(k) in (*LEARN_GRADES,*range(9,26)) and v>0}
    officer=state(db,cid,'official',{})
    wind=int(officer.get('skills',{}).get('8',0)) if officer.get('kind')==2000 and officer.get('level',0)>=12 else 0
    for index,need in ((23,1),(24,3),(25,5)):
        if wind>=need:result[index]=max(1,result.get(index,0))
        else:result.pop(index,None)
    return result

def need(index,level):
    if not index or level>=meta(index)['MaxLevel']:return 0
    return meta(index).get('Level'+str(level+1),[0])[0]

def panel(db,cid):
    s=record(db,cid)
    return wire(874,[dict(id=k,level=v,prac=s['practice'].get(str(k),0),nextprac=need(k,v),maxlevel=meta(k)['MaxLevel']) for k,v in sorted(levels(db,cid).items())])

def train_panel(db,cid,index):
    from native946_role_systems import character
    s=record(db,cid);lv=levels(db,cid).get(index,0);c=character(db,cid)
    left=50-(s.get('train_count',0) if s.get('train_day')==today() else 0)
    return wire(872,index,lv,s['practice'].get(str(index),0),need(index,lv),c['experience'],RULES['lineup_study_exp']*max(1,lv),c['gold'],RULES['lineup_study_cash']*max(1,lv),20,max(0,left))

def validate(db,cid,index):
    require(type(index) is int and (index==0 or index in levels(db,cid)),'队长尚未学会或已失去此阵法的使用条件。')

def reset_invalid_selection(db,team_id,leader):
    row=db.execute('SELECT lineup FROM native_teams WHERE id=?',(team_id,)).fetchone()
    if row and row['lineup'] and row['lineup'] not in levels(db,leader):db.execute('UPDATE native_teams SET lineup=0 WHERE id=?',(team_id,))

def notify_team(game):
    service=getattr(game,'party_service',None)
    if service:
        with service.lock:
            team=service.team(game.progression.cid)
            if team:service.publish(team)

def receive(game,db,mid,args):
    cid=game.progression.cid;s=record(db,cid);known=levels(db,cid)
    if mid==878:return [panel(db,cid)]
    if mid==877:
        row=db.execute('SELECT t.lineup,t.leader_id FROM native_teams t JOIN native_team_members m ON m.team_id=t.id WHERE m.character_id=?',(cid,)).fetchone()
        owner=row['leader_id'] if row else cid;owner_levels=levels(db,owner);owner_record=record(db,owner)
        selected=row['lineup'] if row and row['lineup'] in owner_levels else 0;lv=owner_levels.get(selected,0)
        return [wire(873,selected,lv,owner_record['practice'].get(str(selected),0),need(selected,lv),[0,*sorted(owner_levels)])]
    index=number(args[0],1,25,'阵法');m=meta(index);key=str(index);lv=known.get(index,0)
    if mid==875:
        require(not lv,'已经学会此阵法。')
        require(index<23,'官职阵法由武官风后技能解锁。')
        if index in LEARN_GRADES:require(grade(db,cid)>=LEARN_GRADES[index],'尚未达到该阵法的原生学习等级。')
        else:
            base=next((int(k) for k,v in CATALOG['LineupConfig'].items() if index in (v.get('advance_lineup1'),v.get('advance_lineup2')) and int(k) in LEARN_GRADES),None)
            require(base and known.get(base,0)>=meta(base)['MaxLevel'],'请先修满对应基础阵法。')
            pair=[CATALOG['LineupConfig'][str(base)]['advance_lineup'+str(n)] for n in (1,2)]
            require(not any(k in known for k in pair),'同一基础阵法只能选择一种进阶分支。')
        pay(db,cid,RULES['lineup_study_cash'],RULES['lineup_study_exp']);s['levels'][key]=1;s['practice'][key]=0
    elif mid==879:return [train_panel(db,cid,index)]
    elif mid==876:
        require(lv and lv<m['MaxLevel'],'阵法尚未学习或已满级。');mode=number(args[1],1,2,'修炼方式')
        if mode==1:
            if s.get('train_day')!=today():s.update(train_day=today(),train_count=0)
            require(s['train_count']<50,'今日阵法陪练次数已经用完。')
            pay(db,cid,RULES['lineup_study_cash']*lv,RULES['lineup_study_exp']*lv);s['train_count']+=1
        else:take(db,cid,47974 if m['Type']==2 and lv>=4 else 6694)
        s['practice'][key]=s['practice'].get(key,0)+20
    elif mid==880:
        require(lv and lv<m['MaxLevel'],'阵法尚未学习或已满级。');required=need(index,lv)
        require(s['practice'].get(key,0)>=required,'阵法熟练度不足。')
        material=m.get('Level'+str(lv+1),[0,0])
        if len(material)>1 and material[1]:take(db,cid,47973,material[1])
        s['practice'][key]-=required;s['levels'][key]=lv+1
    else:raise ValueError('阵法操作无效。')
    save(db,cid,'lineup',s)
    return [panel(db,cid),train_panel(db,cid,index)]

def coefficient(db,cid):
    t=state(db,cid,'tianji',{});o=state(db,cid,'official',{})
    return (120 if 1002 in t.get('active',[]) else 100),100+(int(o.get('skills',{}).get('200',0))*5 if o.get('kind')==2000 else 0)

def seat_effects(index,lv,seat,enhance=100,officer=100):
    m=CATALOG['LineupConfig'].get(str(index),{});result={}
    desc=m.get('desc_'+str(seat),'');funcs=m.get('func_'+str(seat),'').split(';')
    lines=desc.split('#r')
    for line,func in zip(lines,funcs):
        if not func.strip():continue
        match=SCALAR.fullmatch(func);require(match,'阵法原生公式尚未支持：'+func)
        amount=Fraction(match[1])*(max(Fraction(lv),Fraction(1,2)) if 'LineupLevel,0.5' in func else 1)
        if match[2]:amount/=10
        amount=amount*enhance*officer/10000
        clean=PLAIN.sub('',line);label=clean.split('%',1)[0];key=ALIASES.get(label)
        require(key,'阵法属性尚未映射：'+label)
        if '％' in clean or key in HUMAN:amount*=10
        if label=='速度降低':amount=-abs(amount)
        result[key]=result.get(key,0)+int(amount)
    return result

def restrain(own,other):
    a=CATALOG['LineupConfig'].get(str(own),{}).get('style',0);b=CATALOG['LineupConfig'].get(str(other),{}).get('style',0)
    value=CATALOG['KeTypeConfig'].get(str(a),{}).get(str(b),0)
    if a==b==2:value=CATALOG['KeZhiConfig'].get(str(own),{}).get(str(other),0)
    return value*40  # Native viewer specifies +/-4% and +/-8%.

def configure(battle):
    if 'formation' in battle.state:return
    fighters=battle.state['fighters'];cid=battle.leader
    with battle.games[cid].progression.transaction(write=False) as db:
        team=battle.service.team(cid,db);members=team['members'] if team else []
        full=len(members)==5 and team['leader_id']==cid and all(not m['away'] and m['character_id'] in battle.games for m in members)
        selected=team['lineup'] if full and team['lineup'] in levels(db,cid) else 0
        lv=levels(db,cid).get(selected,0) if selected else 0
        enemy=int(battle.zone.get('lineup',0));enemy_lv=int(battle.zone.get('lineup_level',1)) if enemy else 0
        require(str(enemy) in CATALOG['LineupConfig'] and 0<=enemy_lv<=10,'敌方阵法配置无效。')
        frozen={};enemy_rows={};coefs={}
        for f in fighters.values():
            own=f['camp']==4;index,rank=(selected,lv) if own else (enemy,enemy_lv)
            if own:
                enhance,official=coefficient(db,f['owner']);slot=f['slot']
                coefs[f['owner']]=(enhance,official)
                if f['kind']=='hero':frozen[slot]=dict(Type=f['type'],JGSAddCof=(enhance-100)/100,GuanZhiCof=official/100)
            else:
                enhance=official=100;slot=f['slot']
                if slot<=5:enemy_rows[slot]=dict(Type=f['type'],JGSAddCof=0,GuanZhiCof=1)
            f['role_lineup_index']=index;f['role_lineup_level']=rank;f['RoleLineupRestrain']=restrain(index,enemy if own else selected)
            if not index:continue
            for key,value in seat_effects(index,rank,slot,enhance,official).items():f[key]=f.get(key,0)+value
            if f.get('RoleLineupSpeed'):f['initiative']=max(0,f['initiative']*(1000+f['RoleLineupSpeed'])//1000)
        battle.state['formation']=dict(own=selected,level=lv,enemy=enemy,enemy_level=enemy_lv,rows=frozen,enemy_rows=enemy_rows,leader=cid,full=full)
        battle.state['formation']['coefs']=coefs

def summon_enter(battle,pet):
    frozen=battle.state.get('formation',{});index=frozen.get('own',0)
    rank=frozen.get('level',0);enhance,officer=frozen.get('coefs',{}).get(pet['owner'],(100,100))
    pet['role_lineup_index']=index;pet['role_lineup_level']=rank;pet['RoleLineupRestrain']=restrain(index,frozen.get('enemy',0))
    if not index:return
    for key,value in seat_effects(index,rank,pet['slot'],enhance,officer).items():pet[key]=pet.get(key,0)+value
    if pet.get('RoleLineupSpeed'):pet['initiative']=max(0,pet['initiative']*(1000+pet['RoleLineupSpeed'])//1000)

def battle_info(battle,game):
    f=battle.state.get('formation',{});cid=game.character['id']
    enhance,officer=f.get('coefs',{}).get(cid,(100,100))
    # The entry packet selects a layout on the client. Combat bonuses still use the configured formation.
    enemy_layout=battle.zone.get('enemy_layout',f.get('enemy',0))
    return dict(enemy=f.get('enemy_level',0)*10000+enemy_layout,own=f.get('level',0)*10000+f.get('own',0),
                level=f.get('level',0),leader=battle.games[battle.leader].character['name'],enhance=enhance,officer=officer)

def battle_packets(battle):
    f=battle.state.get('formation',{})
    own={u['slot'] for u in battle.state['fighters'].values() if u.get('kind')=='hero' and not u.get('removed')}
    enemy={u['slot'] for u in battle.state['fighters'].values() if u.get('camp')==3 and not u.get('removed')}
    rows={k:v for k,v in f.get('rows',{}).items() if k in own}
    enemy_rows={k:v for k,v in f.get('enemy_rows',{}).items() if k in enemy}
    return (wire(4603,0,f.get('own',0),f.get('level',0),text(rows),f.get('enemy',0),f.get('enemy_level',0),text(enemy_rows),[],[]),)

def begin_round(runtime):
    for camp in sorted({f['camp'] for f in runtime.fighters.values()}):
        alive=[f for f in runtime.living(camp) if f.get('kind')!='shadow']
        if not alive:continue
        index=next((f.get('role_lineup_index',0) for f in alive),0)
        rank=next((f.get('role_lineup_level',0) for f in alive),0)
        if index not in (24,25) or runtime.rng.randrange(1000)>=min(500,rank*50):continue
        if index==24:
            if len(alive)<5:continue
            runtime.grant_invisibility(runtime.rng.choice(alive),'formation',runtime.round)
        else:
            enemies=[f for f in runtime.living() if f['camp']!=camp and f.get('kind')!='shadow']
            if len(enemies)<5:continue
            target=runtime.rng.choice(enemies)
            for key in ('entangle','jin_gu'):target['controls'][key]=max(target['controls'].get(key,0),runtime.round)
            runtime.out.extend((packet(2130,['number']*3,[target['id'],52,0]),packet(2130,['number']*3,[target['id'],53,0])))

def end_round(runtime):
    if runtime.round%2==0:return
    for target in list(runtime.fighters.values()):
        if target.get('kind')=='shadow' or target.get('role_lineup_index')!=23 or target['hp']>0 or target.get('removed') or target.get('role_death_round')!=runtime.round:continue
        if runtime.rng.randrange(1000)>=min(500,target.get('role_lineup_level',0)*50):continue
        if target.get('no_revive_until',0)>=runtime.round:continue
        from native946_role_battle import STATUS,BUFF_STATUS,SHIELD_STATUS
        for key in list(target['controls']):
            if key in STATUS:runtime.out.append(packet(2138,['number']*2,[target['id'],STATUS[key]]))
        for b in target['role_buffs']:target[b['key']]=target.get(b['key'],0)-b['amount']
        for sid in set((*BUFF_STATUS.values(),*SHIELD_STATUS.values(),106,167)):runtime.out.append(packet(2138,['number']*2,[target['id'],sid]))
        target['controls'].clear();target['role_buffs'].clear();target['role_shields'].clear();target['role_dots']=[];target.pop('role_gushu',None)
        for key in ('role_control_info','role_forbidden','role_no_item_until','role_no_summon_until','role_poison_icon'):target.pop(key,None)
        runtime.clear_invisibility(target)
        target['hp_limit']=min(target.get('hp_limit',target['hpmax']),target['hpmax']);target['mp']=min(target['mp'],target['mpmax'])
        runtime.revive(target,target,max(1,target['hpmax']//4))

def settle(battle,db,victory):
    f=battle.state.get('formation',{});index=f.get('own',0)
    if not victory or not index or not f.get('full'):return
    from time import time
    for cid in battle.games:
        lv=levels(db,cid).get(index,0)
        if not lv or lv>=meta(index)['MaxLevel']:continue
        if not db.execute('INSERT OR IGNORE INTO native_role_system_events VALUES(?,?,?,?)',(cid,'lineup-battle',battle.id,int(time()))).rowcount:continue
        s=record(db,cid);s['practice'][str(index)]=s['practice'].get(str(index),0)+1;save(db,cid,'lineup',s)

def use_book(game,iid,index=None):
    from native946_inventory_features import owned,consume
    from native946_npc_services import NpcServices
    require(game.battle is None,'请先结束战斗。');cid=game.progression.cid
    with game.progression.transaction() as db:
        item=owned(db,cid,iid);kind=item['type'];require(kind in BOOKS,'请选择阵法修炼书。')
        choices={k:v for k,v in levels(db,cid).items() if v<meta(k)['MaxLevel'] and (kind==6694 and not (meta(k)['Type']==2 and v>=4) or kind in (47973,47974) and meta(k)['Type']==2 and v>=4)}
        require(choices,'没有符合此书使用条件的已学阵法。')
        if index is None:
            return NpcServices(game)._menu(cid,'请选择要提升20点熟练度的阵法。',[(CATALOG['LineupConfig'][str(k)]['name'],dict(kind='lineup_item',item_id=iid,index=k)) for k in choices])
        require(index in choices,'阵法修炼条件已变化。');consume(db,cid,iid);s=record(db,cid);s['practice'][str(index)]=s['practice'].get(str(index),0)+20;save(db,cid,'lineup',s)
    return game.defer_packets(lambda:(packet(1183,['string'],['dlg_npc_chat']),panel_packets(game),*game.progression.sync(),notice('获得20点阵法熟练度。')))

def panel_packets(game):
    with game.progression.transaction(write=False) as db:return panel(db,game.progression.cid)

def npc_menu(game,npc):
    from native946_npc_services import NpcServices
    return NpcServices(game)._menu(npc['npc_id'],'阵法学习等级、进阶前置和材料沿用原生条件。本机陪练每次消耗现金10000×等级、经验100000×等级，获得20熟练，每日50次。',
        [('学习、查看和修炼阵法',dict(kind='lineup_npc',op='open')),('七十二变',dict(kind='lineup_npc',op='transform'))])

def npc_execute(game,nid,action):
    from native946_npc_services import NpcServices
    npc=NpcServices(game).validate(nid);require(npc.get('name')=='崩将军','请到花果山找崩将军。')
    if action['op']=='transform':
        from native946_transform import Transform
        return (packet(1183,['string'],['dlg_npc_chat']),*Transform(game).messages(1),packet(3993,['string'],['dlg_new72trans_main']))
    return (packet(1183,['string'],['dlg_npc_chat']),panel_packets(game))
