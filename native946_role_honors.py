"""Medal ownership/showcases and persistent native competition spaces."""
import json,re
from datetime import datetime,timedelta,timezone
from time import time
from native946_role_systems import DATA,state,save,wire,text,require,number,character

def year():return datetime.now(timezone(timedelta(hours=8))).year
def target(db,cid,host,uid):
    require(host in (0,1),'本机仅支持本服的赛事空间。');return character(db,uid or cid)
def results(db,cid):
    return [dict(r,value=json.loads(r['value'])) for r in db.execute('SELECT * FROM native_match_results WHERE character_id=? ORDER BY season DESC LIMIT 100',(cid,))]
def record_result(db,cid,match_type,season,event_id,value):
    """Trusted settlement API. Never called for an ordinary training battle."""
    require(match_type in DATA['match_space']['MatchConfig'],'赛事类型未定义。');number(season,1,999999,'赛事赛季')
    require(isinstance(value,dict) and all(k in value for k in ('Score','RankTxt','Time','Members')),'赛事结算字段不完整。')
    number(value['Score'],0,1000000,'赛事积分');value=dict(value,Year=year(),MatchType=match_type,Season=season,LikeCount=0,IsLike=0)
    db.execute('INSERT OR IGNORE INTO native_match_results VALUES(?,?,?,?,?)',(cid,match_type,season,str(event_id),json.dumps(value,ensure_ascii=False)))
def totals(db,cid):
    out={}
    for row in results(db,cid):
        value=row['value']
        if value.get('Year')!=year():continue
        key=row['match_type'];s=out.setdefault(key,dict(Score=0,Champion=0));s['Score']+=value['Score'];s['Champion']+=int(value.get('Rank')==1)
    return out
def match_summary(db,viewer,cid):
    return {k:dict(v,MatchType=k) for k,v in totals(db,cid).items()}
def game_rows(db,viewer,cid,kind):
    out={}
    for r in results(db,cid):
        if kind and r['match_type']!=kind:continue
        v=dict(r['value']);v['LikeCount']=db.execute('SELECT count(*) FROM native_match_likes WHERE target_id=? AND match_type=? AND season=?',(cid,r['match_type'],r['season'])).fetchone()[0]
        v['IsLike']=int(db.execute('SELECT 1 FROM native_match_likes WHERE character_id=? AND target_id=? AND match_type=? AND season=?',(viewer,cid,r['match_type'],r['season'])).fetchone() is not None)
        out[r['match_type']+'_'+str(r['season'])]=v
    return out

def medal_rows(db,cid):
    from native946_fashion import score
    fashion=score(db,cid);saved={r['medal_id']:dict(r) for r in db.execute('SELECT * FROM native_medals WHERE character_id=?',(cid,))};out=[]
    showcase=state(db,cid,'medals',dict(showcase=[]))['showcase'];now=int(time())
    for key,meta in DATA['medal']['MedalDefine'].items():
        mid=int(key);row=saved.get(mid,dict(progress=0,unlocked=0,expires=0));progress=row['progress'];maximum=1;unlocked=bool(row['unlocked']);expired=bool(row['expires'] and row['expires']<=now)
        if meta['type_key']=='hualidu':maximum=int(re.search(r'\d+',meta['obtain_desc'])[0]);progress=fashion;unlocked=progress>=maximum
        out.append(dict(medal_id=mid,type_key=meta['type_key'],star=meta['star'],quality=min(5,(meta['star']-1)//5+1),progress=min(progress,maximum),progress_max=maximum,is_unlocked=int(unlocked and not expired),is_expired=int(expired),unlock_time=row['unlocked'],remain_days=max(0,(row['expires']-now+86399)//86400) if row['expires'] else 0,is_showcased=int(mid in showcase)))
    return out
def sync_earned(db,cid):
    from native946_fashion import score
    current=score(db,cid);now=int(time())
    for key,meta in DATA['medal']['MedalDefine'].items():
        if meta['type_key']!='hualidu':continue
        threshold=int(re.search(r'\d+',meta['obtain_desc'])[0])
        if current>=threshold:db.execute('INSERT INTO native_medals VALUES(?,?,?,?,0) ON CONFLICT(character_id,medal_id) DO UPDATE SET progress=max(progress,excluded.progress),unlocked=CASE WHEN unlocked=0 THEN excluded.unlocked ELSE unlocked END',(cid,int(key),current,now))
def eligible(rows):
    best={}
    for r in rows:
        if r['is_unlocked'] and (r['type_key'] not in best or r['star']>best[r['type_key']]['star']):best[r['type_key']]=r
    return best
def showcase(db,cid,rows=None):
    rows=rows or medal_rows(db,cid);byid={r['medal_id']:r for r in rows};best=eligible(rows);saved=state(db,cid,'medals',dict(showcase=[]))['showcase'];out=[]
    for mid in saved:
        if mid not in byid:continue
        r=best.get(byid[mid]['type_key'])
        if r and r['medal_id'] not in out:out.append(r['medal_id'])
    return sorted(out,key=lambda k:(byid[k]['quality'],byid[k]['star']),reverse=True)[:3]
def medal_wall(db,cid):
    rows=medal_rows(db,cid);show=showcase(db,cid,rows);counts=[]
    for key,v in DATA['medal']['MedalTypes'].items():counts.append(dict(type_key=key,type_name=v['name'],total=sum(r['type_key']==key for r in rows),owned=sum(r['type_key']==key and r['is_unlocked'] for r in rows)))
    for r in rows:r['is_showcased']=int(r['medal_id'] in show)
    return wire(5321,cid,show,rows,counts,character(db,cid)['name'])

def receive(game,db,mid,a):
    cid=game.progression.cid
    if mid in (5323,5324,5326):
        sync_earned(db,cid)
        if mid==5324:
            slots=a[0];rows=medal_rows(db,cid);best=eligible(rows);allowed={r['medal_id']:r for r in best.values()}
            require(len(slots)==len(set(slots)) and len(slots)<=3 and all(k in allowed for k in slots),'请从每类已获得的最高星级勋章中选择，最多3枚。');require(len(slots)==min(3,len(allowed)),'请选满可用展示位。')
            slots.sort(key=lambda k:(allowed[k]['quality'],allowed[k]['star']),reverse=True);save(db,cid,'medals',dict(showcase=slots));return [wire(5322,0),medal_wall(db,cid),wire(5327,cid,text(slots))]
        c=target(db,cid,a[1],a[0])
        return [medal_wall(db,c['id'])] if mid==5323 else [wire(5327,c['id'],text(showcase(db,c['id'])))]
    host,uid=a[:2];c=target(db,cid,host,uid);uid=c['id']
    if mid==4569:
        summary=match_summary(db,cid,uid);score=sum(v['Score'] for v in summary.values());rank=0
        if score:
            scores=[sum(v['Score'] for v in totals(db,r[0]).values()) for r in db.execute('SELECT id FROM characters')];rank=1+sum(s>score for s in scores)
        likes=db.execute('SELECT count(*) FROM native_match_likes WHERE target_id=?',(uid,)).fetchone()[0];following=db.execute('SELECT count(*) FROM native_match_follows WHERE character_id=?',(uid,)).fetchone()[0];fans=db.execute('SELECT count(*) FROM native_match_follows WHERE target_id=?',(uid,)).fetchone()[0];is_follow=int(db.execute('SELECT 1 FROM native_match_follows WHERE character_id=? AND target_id=?',(cid,uid)).fetchone() is not None)
        return [wire(4562,host,uid,c['name'],rank,likes,following,fans,text(summary),score,is_follow)]
    if mid in (4565,4566):
        start=number(a[2],1,100000,'列表起点');end=number(a[3],start,start+49,'列表终点');column,other=('target_id','character_id') if mid==4565 else ('character_id','target_id')
        rows=db.execute('SELECT c.* FROM native_match_follows f JOIN characters c ON c.id=f.'+other+' WHERE f.'+column+'=? ORDER BY c.id LIMIT ? OFFSET ?',(uid,end-start+1,start-1)).fetchall()
        data=[dict(server_id=host,user_id=r['id'],name=r['name'],ServerId=host,UserId=r['id'],Name=r['name'],ShowIcon=r['role_id'],HostName='本机',Score=sum(v['Score'] for v in totals(db,r['id']).values())) for r in rows]
        return [wire(4558 if mid==4565 else 4559,host,uid,start,start+len(data)-1,text(data))]
    if mid in (4567,4568):
        kind=a[2];require(kind=='' or kind in DATA['match_space']['MatchConfig'],'赛事类型无效。');rows=game_rows(db,cid,uid,kind)
        if mid==4567:return [wire(4560,host,uid,kind,text(rows))]
        season=number(a[3],1,999999,'赛季');row=rows.get(kind+'_'+str(season),{})
        return [wire(4561,host,uid,kind,season,text(row.get('Versus',[])))]
    if mid==4570:
        cancel=number(a[2],0,1,'关注操作');require(uid!=cid,'不能关注自己。')
        if cancel:db.execute('DELETE FROM native_match_follows WHERE character_id=? AND target_id=?',(cid,uid))
        else:db.execute('INSERT OR IGNORE INTO native_match_follows VALUES(?,?)',(cid,uid))
        follows=db.execute('SELECT count(*) FROM native_match_follows WHERE character_id=?',(uid,)).fetchone()[0];fans=db.execute('SELECT count(*) FROM native_match_follows WHERE target_id=?',(uid,)).fetchone()[0]
        return [wire(4563,host,uid,cancel,follows,fans)]
    if mid==4571:
        kind,season,cancel=a[2:];number(cancel,0,1,'点赞操作');require(uid!=cid,'不能给自己的赛事点赞。');require(db.execute('SELECT 1 FROM native_match_results WHERE character_id=? AND match_type=? AND season=?',(uid,kind,season)).fetchone(),'该赛事记录不存在。')
        if cancel:db.execute('DELETE FROM native_match_likes WHERE character_id=? AND target_id=? AND match_type=? AND season=?',(cid,uid,kind,season))
        else:db.execute('INSERT OR IGNORE INTO native_match_likes VALUES(?,?,?,?)',(cid,uid,kind,season))
        new=db.execute('SELECT count(*) FROM native_match_likes WHERE target_id=? AND match_type=? AND season=?',(uid,kind,season)).fetchone()[0];total=db.execute('SELECT count(*) FROM native_match_likes WHERE target_id=?',(uid,)).fetchone()[0]
        return [wire(4564,host,uid,kind,season,cancel,new,total)]
    raise ValueError('赛事空间操作无效。')
