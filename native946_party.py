"""Native team protocol, persistent membership and serialized shared gameplay.

Socket writes stay on each connection's own thread. Cross-player changes pass
through this database's dispatcher lock; outgoing packets use bounded inboxes.
"""
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock,Lock
from time import monotonic,time
from math import ceil
from weakref import WeakValueDictionary
import json
import logging

from native946_codec import packet
from native946_codec import text_notice
from native946_characters import DEFAULT_RUN_SPEED
from native946_storage import transaction,StorageBusy
from native946_world import INSTANCE_SCENES

CONTRACTS=json.loads(Path(__file__).with_name('native946_party_data.json').read_text('utf8'))
REQUESTS={r['id']:r for r in CONTRACTS.values() if r.get('id') and '/s_' in r.get('path','')}
REFERENCE_WIRE_VERSION=22
# Both native roster renderers use 1 for the away seal. Leader identity
# comes from the packet's leader_id, independent of the member row's type.
PRESENT_STATUS, AWAY_STATUS = 0, 1
# Live class refresh must preserve online games, locks, teams and battles.
if '_SERVICES' not in globals():
    _SERVICES=WeakValueDictionary();_LOCK=Lock()

def schema(db):
    statements=[
        'CREATE TABLE IF NOT EXISTS native_teams (id INTEGER PRIMARY KEY AUTOINCREMENT,leader_id INTEGER NOT NULL REFERENCES characters(id),lineup INTEGER NOT NULL DEFAULT 0,cure_flag INTEGER NOT NULL DEFAULT 0,direct_ids TEXT NOT NULL DEFAULT \'[]\')',
        'CREATE TABLE IF NOT EXISTS native_team_members (character_id INTEGER PRIMARY KEY REFERENCES characters(id),team_id INTEGER NOT NULL REFERENCES native_teams(id) ON DELETE CASCADE,position INTEGER NOT NULL,away INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS native_encounter_rewards (battle_id TEXT NOT NULL,character_id INTEGER NOT NULL REFERENCES characters(id),payload TEXT NOT NULL,PRIMARY KEY(battle_id,character_id))',
        'CREATE TABLE IF NOT EXISTS native_pending_loot (battle_id TEXT NOT NULL,character_id INTEGER NOT NULL REFERENCES characters(id),type INTEGER NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),PRIMARY KEY(battle_id,character_id,type))',
    ]
    for sql in statements:db.execute(sql)
    if 'direct_ids' not in {r[1] for r in db.execute('PRAGMA table_info(native_teams)')}:
        db.execute("ALTER TABLE native_teams ADD COLUMN direct_ids TEXT NOT NULL DEFAULT '[]'")

def service(database):
    key=str(Path(database).resolve()).casefold()
    with _LOCK:
        found=_SERVICES.get(key)
        if found is None:found=_SERVICES[key]=PartyService(database)
        elif found.__class__ is not PartyService:found.__class__=PartyService
        return found

def numbers(mid,*values):return packet(mid,['number']*len(values),values)
def notice(text):return text_notice(text)
def packets(value):return () if value is None else value if isinstance(value,(tuple,list)) else (value,)

class PartyService:
    def __init__(self,database):
        self.database=database;self.lock=RLock();self.games=WeakValueDictionary()
        self.invitations={};self.applications={};self.battles={};self.pending_follow=set();self.pending_follow_relocations=set();self.leader_votes={}

    def attach(self,game):
        with self.lock:
            cid=game.character['id'];self.games[cid]=game
            game.party_service=self;game._party_inbox=deque();game._party_bytes=0;game._party_connected=True
            active=self.battles.get(cid)
            if active:
                active.games[cid]=game;game._wild_battle=active;active.project()

    def scene_players(self,game):
        from native946_characters import scene_player,scene_actor_packet
        with self.lock:
            c=game.character
            if c['map_id'] in INSTANCE_SCENES:return ()
            return tuple(scene_actor_packet(other.character) for other in self.games.values()
                         if other is not game and getattr(other,'_party_connected',False) and not other.battle
                         and other.character['map_id']==c['map_id'] and max(abs(other.character[k]-c[k]) for k in ('x','y'))<=96)

    def reconnect_packets(self,game):
        with self.lock:return self._reconnect_packets(game)

    def publish_relocation(self,game,before,*,exclude=()):
        """Refresh observers after an instant move, including same-map warps."""
        from native946_characters import scene_actor_packet
        with self.lock:
            c=game.character
            for other in tuple(self.games.values()):
                if other is game or other.character['id'] in exclude or other.battle or not getattr(other,'_party_connected',False):continue
                o=other.character
                is_near=(c['map_id'] not in INSTANCE_SCENES and o['map_id']==c['map_id']
                         and max(abs(o[k]-c[k]) for k in ('x','y'))<=96)
                old_distance=max(abs(o['x']-before[1]),abs(o['y']-before[2]))
                if before[0] not in INSTANCE_SCENES and o['map_id']==before[0] and (before[0]!=c['map_id'] or is_near or old_distance<=96):
                    self.enqueue(other,numbers(1604,c['id']))
                if is_near:self.enqueue(other,scene_actor_packet(c))

    def _reconnect_packets(self,game):
        team=self.team(game.character['id']);result=[]
        if team:
            cid=game.character['id'];leader=team['leader_id']
            result.append(numbers(2025,leader) if cid==leader else numbers(2026,leader,cid))
            result.extend(self.view(game,team));self.publish(team)
        battle=self.battles.get(game.character['id'])
        if battle:
            result.extend(battle.entry(game))
            if battle.state['phase']=='drawing':
                battle.state['acks'].discard(game.character['id'])
                result.append(numbers(2167))
        from native946_characters import scene_actor_packet
        for other in self.games.values():
            if (game.character['map_id'] not in INSTANCE_SCENES and other is not game
                    and other.character['map_id']==game.character['map_id'] and not other.battle):
                self.enqueue(other,scene_actor_packet(game.character))
        return tuple(result)

    def enqueue(self,game,*replies):
        with self.lock:return self._enqueue(game,*replies)

    def _enqueue(self,game,*replies):
        if not getattr(game,'_party_connected',False):return
        inbox=game._party_inbox
        # Never silently discard battle/reward packets for a slow peer.
        size=sum(len(p) for p in replies)
        if game._party_bytes+size>2*1024*1024:
            game._party_overflow=True;return
        inbox.extend(replies);game._party_bytes+=size
        wake=getattr(game,'outbound_wake',None)
        if wake:wake()

    def drain(self,game):
        with self.lock:
            if getattr(game,'_party_overflow',False):raise ConnectionError('队伍消息积压，连接需要恢复。')
            result=tuple(game._party_inbox);game._party_inbox.clear();game._party_bytes=0;return result

    def team(self,cid,db=None):
        if db is None:
            with transaction(self.database) as db:return self.team(cid,db)
        row=db.execute('SELECT t.* FROM native_teams t JOIN native_team_members m ON m.team_id=t.id WHERE m.character_id=?',(cid,)).fetchone()
        if row is None:return None
        members=[dict(r) for r in db.execute('SELECT m.*,c.* FROM native_team_members m JOIN characters c ON c.id=m.character_id WHERE team_id=? ORDER BY position,character_id',(row['id'],))]
        owned={m['character_id'] for m in members}
        direct_ids=json.loads(row['direct_ids']) if 'direct_ids' in row.keys() else []
        return dict(row,direct_ids=[member for member in direct_ids if member in owned],members=members)

    def online(self,cid):
        g=self.games.get(cid)
        return g if g is not None and getattr(g,'_party_connected',False) else None

    def refresh_run_speeds(self, game, *, force_self=False):
        from native946_premium_items import PILL_RUN_SPEED, SPEED_KEY, license_validity
        cid = game.character['id']
        with self.lock:
            with transaction(self.database, write=False) as db:
                team = self.team(cid, db)
                members = [(other, row['away']) for row in team['members']
                           if (other := self.online(row['character_id'])) is not None] if team else [(game, False)]
                now = int(time())
                licensed = {other.character['id'] for other, _ in members
                            if license_validity(db, other.character['id'], now, key=SPEED_KEY)}
            shared = any(not away and other.character['id'] in licensed for other, away in members)
            checked_until = monotonic() + 1
            own = []
            for actor, away in members:
                c = actor.character
                speed = PILL_RUN_SPEED if c['id'] in licensed or (shared and not away) else DEFAULT_RUN_SPEED
                actor._run_speed_check_at = checked_until
                changed = c.get('run_speed', DEFAULT_RUN_SPEED) != speed
                if not changed and not (force_self and actor is game):
                    continue
                c['run_speed'] = speed
                if actor.battle:
                    continue
                update = numbers(1621, c['id'], 15, speed)
                if not changed:
                    own.append(update)
                    continue
                for peer in tuple(self.games.values()):
                    if (peer.battle or peer.character['map_id'] != c['map_id'] or
                            max(abs(peer.character[k] - c[k]) for k in ('x', 'y')) > 96):
                        continue
                    if peer is game:
                        own.append(update)
                    else:
                        self.enqueue(peer, update)
            return tuple(own)

    def stable(self,team):
        if any(self.battles.get(m['character_id']) or (self.online(m['character_id']) and self.online(m['character_id']).battle) for m in team['members']):
            raise ValueError('请在队伍战斗结束后再调整队伍。')

    def member_record(self,row):
        cid=row['character_id'];g=self.online(cid)
        c=g.character if g else row
        s=g.progression.snapshot() if g else row
        from native946_mall_portraits import current as current_portrait
        from native946_nice_ids import identity
        from native946_premium_items import AUTO_MEDICAL_KEY, SPEED_KEY, license_validity
        from native946_training import state as cultivation
        from native946_role_growth import jing
        with transaction(self.database,write=False) as db:
            head=current_portrait(db,cid) or c['role_id']
            showid=identity(db,cid)['showid']
            medical=license_validity(db,cid)
            auto_medical=license_validity(db,cid,key=AUTO_MEDICAL_KEY)
            speed=license_validity(db,cid,key=SPEED_KEY)
            stage=s if g else cultivation(db,cid)
            jing_stage=s['jing_stage'] if g else jing(db,cid)['level']
        return dict(c.get('appearance',{}),id=cid,showid=showid,name=c['name'],icon=c['role_id'],show_icon=head,race=c['race'],
                    level=s['level'],position=row['position'],type=AWAY_STATUS if row['away'] or g is None else PRESENT_STATUS,
                    rei=stage['rebirth'],fly=stage['ascension'],feng=stage['sanctification'],jing=jing_stage,server_name='本机',
                    autocure_validity=medical,autoheal_validity=auto_medical,speed_validity=speed)

    def view(self,game,team=None):
        from native946_progression import lua_data
        team=self.team(game.character['id']) if team is None else team
        if team is None:return (packet(3228,['number','string',('array','team_player_info'),'string','number','number',('array','number'),'number','number','string','number'],[0,'{}',[],'{}',0,0,[],0,0,'{}',1]),)
        records=[self.member_record(dict(m,position=i)) for i,m in enumerate(team['members'],1)]
        pos=lua_data({m['character_id']:i for i,m in enumerate(team['members'],1)})
        from native946_lineups import levels
        from native946_role_systems import state
        with transaction(self.database,write=False) as db:
            known=levels(db,team['leader_id'])
            tasks=state(db,team['leader_id'],'team_platform').get('tasks',[])
        selected=team['lineup'] if team['lineup'] in known else 0
        result=[packet(3228,['number','string',('array','team_player_info'),'string','number','number',('array','number'),'number','number','string','number'],
                       [selected,lua_data({0:0,**known}),records,pos,team['leader_id'],team['cure_flag'],tasks,0,0,'{}',1])]
        desc=[]
        for row,record in zip(team['members'],records):
            g=self.online(row['character_id']);s=g.progression.snapshot() if g else {}
            desc.append(dict(id=record['id'],name=record['name'],icon=record['show_icon'],grade=record['level'],race=record['race'],type=record['type'],
                             hp=s.get('hp',0),hpmax=s.get('hpmax',1),hpmin=s.get('hpmax',1),mp=s.get('mp',0),mpmax=s.get('mpmax',1),mpmin=s.get('mpmax',1),
                             autocure_leftday=max(0,ceil((record['autocure_validity']-time())/86400))))
        result.append(packet(2029,[('array','team_desc'),'number'],[desc,team['leader_id']]))
        result.append(packet(4019,[('array','number')],[team['direct_ids']]))
        return tuple(result)

    def appearance(self,game):
        from native946_progression import lua_data
        team=self.team(game.character['id'])
        if team is None:raise ValueError('你还没有加入队伍。')
        records=[self.member_record(dict(m,position=i)) for i,m in enumerate(team['members'],1)]
        return packet(4505,['string'],[lua_data(records)])

    def publish(self,team,extras=()):
        first=None
        for row in team['members']:
            game=self.online(row['character_id'])
            if game:
                if first is None:first=game
                game.character['team_leader']=0 if row['away'] else team['leader_id']
                self.enqueue(game,*extras,*self.view(game,team))
        if first is not None:
            speed_updates = self.refresh_run_speeds(first)
            if speed_updates:self.enqueue(first,*speed_updates)

    def _create(self,db,cid):
        tid=db.execute('INSERT INTO native_teams(leader_id) VALUES(?)',(cid,)).lastrowid
        db.execute('INSERT INTO native_team_members VALUES(?,?,1,0)',(cid,tid))
        return self.team(cid,db)

    def _join(self,db,team,cid):
        if self.team(cid,db):raise ValueError('该角色已经在队伍中。')
        self.stable(team)
        if len(team['members'])>=5:raise ValueError('队伍已满，最多5人。')
        if self.online(cid) is None:raise ValueError('该角色目前不在线。')
        if self.online(cid).battle:raise ValueError('该角色正在战斗。')
        leader=self.online(team['leader_id']);other=self.online(cid)
        close=leader and other.character['map_id']==leader.character['map_id'] and max(abs(other.character[k]-leader.character[k]) for k in ('x','y'))<=12
        slot=next((n for n in range(1,6) if n not in {m['position'] for m in team['members']}),max(m['position'] for m in team['members'])+1)
        db.execute('INSERT INTO native_team_members VALUES(?,?,?,?)',(cid,team['id'],slot,int(not close)))
        return self.team(team['leader_id'],db)

    def application_list(self,leader):
        rows=[]
        for owner,applicant in self.applications:
            if owner==leader and (other:=self.online(applicant)) is not None:
                row=dict(other.character,character_id=applicant,position=0,away=0)
                rows.append(self.member_record(row))
        return packet(2022,[('array','team_player_info'),('array','byte')],[rows,[0]*len(rows)])

    def receive(self,game,mid,reader):
        contract=REQUESTS[mid];args=[]
        for field in contract['fields']:
            count=reader.take(1)[0] if len(field)>2 else None
            parse=reader.integer if field[1]=='number' else lambda:reader.take(1)[0]
            args.append([parse() for _ in range(count)] if count is not None else parse())
        reader.finish();cid=game.character['id'];now=monotonic()
        self.invitations={k:v for k,v in self.invitations.items() if v>now}
        self.applications={k:v for k,v in self.applications.items() if v>now}
        # Read-only team queries do not acquire the database writer.
        if mid in (2043,2053,2054,2052,4506):
            team=self.team(cid)
            if mid==4506:return (self.appearance(game),)
            if mid==2043:
                if not team or team['leader_id']!=cid:raise ValueError('只有队长可以处理入队申请。')
                return (self.application_list(cid),)
            if mid==2053:
                row=next((r for r in (team or {}).get('members',[]) if r['character_id']==args[0]),None)
                if row is None:raise ValueError('该角色不在你的队伍中。')
                return (packet(2034,['team_player_info'],[self.member_record(row)]),)
            if mid==2052:return (packet(2033,['byte'],[0]),)
            return self.view(game)
        if game.battle:raise ValueError('请先结束当前战斗。')
        # Stage notifications and ephemeral consent state until durable commit.
        outgoing=[];refresh=None;departed=[];retired_leader=None
        invitations=dict(self.invitations);applications=dict(self.applications)
        try:
            with transaction(self.database,write=True,batchable=True) as db:
                team=self.team(cid,db)
                if mid==2042:
                    target=args[0]
                    if target==cid:
                        if team is not None:
                            return (notice('你已经在队伍中。'),)
                        team=self._create(db,cid);refresh=team
                        outgoing.extend(((game,numbers(2025,cid)),(game,notice('队伍已创建。'))))
                    else:
                        if team:raise ValueError('请先退出当前队伍。')
                        target_team=self.team(target,db)
                        if target_team is None:raise ValueError('对方尚未创建队伍。')
                        leader=target_team['leader_id'];other=self.online(leader)
                        if other is None:raise ValueError('目标队伍的队长不在线。')
                        if len(target_team['members'])>=5:raise ValueError('目标队伍已满。')
                        if (leader,cid) in applications:raise ValueError('你已提交入队申请，请等待队长处理。')
                        if sum(owner==leader for owner,_ in applications)>=254:raise ValueError('对方的入队申请列表已满。')
                        applications[(leader,cid)]=now+60
                        outgoing.extend(((other,numbers(2023)),(other,notice(f"{game.character['name']}申请加入你的队伍。")),
                                         (game,notice(f"已向{other.character['name']}发送入队申请。"))))
                elif mid in (2039,2040):
                    targets=args[0] if mid==2040 else [args[0]]
                    targets=list(dict.fromkeys(targets))
                    if not targets or len(targets)>5:raise ValueError('邀请人数无效。')
                    if team is None:team=self._create(db,cid)
                    if team['leader_id']!=cid:raise ValueError('只有队长可以邀请队员。')
                    self.stable(team)
                    if len(team['members'])>=5:raise ValueError('队伍已满。')
                    for target in targets:
                        other=self.online(target)
                        if other is None or target==cid or self.team(target,db):raise ValueError('受邀角色不在线或已经组队。')
                        if other.battle:raise ValueError('受邀角色正在战斗。')
                        invitations[(cid,target)]=now+60
                        outgoing.append((other,packet(2020,['number','string'],[cid,game.character['name']])))
                    outgoing.append((game,numbers(2025,cid)));refresh=team
                    outgoing.append((game,notice(f'已发送组队邀请（{len(targets)}人）。')))
                elif mid in (2038,2041):
                    if mid==2038:
                        leader=args[0]
                        if invitations.get((leader,cid),0)<=now:raise ValueError('邀请已失效，请重新邀请。')
                        team=self.team(leader,db)
                        if team is None or team['leader_id']!=leader:raise ValueError('队长已经改变，邀请失效。')
                        target=cid;invitations.pop((leader,cid),None)
                    else:
                        if team is None or team['leader_id']!=cid:raise ValueError('只有队长可以批准入队。')
                        target=args[0]
                        if applications.pop((cid,target),None) is None:
                            replies=(self.application_list(cid),)
                            if any(m['character_id']==target for m in team['members']):return replies
                            return (*replies,notice('入队申请已失效。'))
                    team=self._join(db,team,target);refresh=team
                    applications={key:expires for key,expires in applications.items() if key[1]!=target}
                    invitations={key:expires for key,expires in invitations.items() if key[1]!=target}
                    joined=self.online(target)
                    joined_name=joined.character['name']
                    for m in team['members']:
                        other=self.online(m['character_id'])
                        if other:
                            outgoing.append((other,numbers(2026,team['leader_id'],target)))
                            message='已加入队伍。' if other is joined else f'{joined_name}加入了队伍。'
                            if mid==2041 and other is joined:message=f"{game.character['name']}同意了你的入队申请。"
                            outgoing.append((other,notice(message)))
                elif mid in (2047,2048):
                    if not team or team['leader_id']!=cid:raise ValueError('只有队长可以拒绝入队申请。')
                    declined=[key for key in applications if key[0]==cid and (mid==2048 or key[1]==args[0])]
                    for key in declined:
                        applications.pop(key)
                        other=self.online(key[1])
                        if other:outgoing.append((other,notice(f"{game.character['name']}拒绝了你的入队申请。")))
                    message=f'已拒绝{len(declined)}条入队申请。' if declined else '没有待处理的入队申请。'
                    outgoing.append((game,notice(message)))
                elif mid in (2049,2050,2051):
                    self.leader_votes.pop(team['id'],None) if team else None
                    if team is None:raise ValueError('你还没有加入队伍。')
                    self.stable(team);leader=team['leader_id']
                    if mid in (2049,2050) and leader!=cid:raise ValueError('只有队长可以解散队伍或请离队员。')
                    if mid==2049 or (mid==2051 and len(team['members'])==1):
                        db.execute('DELETE FROM native_team_members WHERE team_id=?',(team['id'],));db.execute('DELETE FROM native_teams WHERE id=?',(team['id'],))
                        retired_leader=leader
                        for m in team['members']:
                            other=self.online(m['character_id'])
                            if other:
                                departed.append(other)
                                outgoing.extend(((other,numbers(2031,leader)),(other,notice('队伍已解散。' if mid==2049 else '已离开队伍。'))))
                    else:
                        target=args[0] if mid==2050 else cid
                        if target==leader and mid==2050:raise ValueError('不能将队长请离自己的队伍。')
                        removed=next((m for m in team['members'] if m['character_id']==target),None)
                        if removed is None:raise ValueError('该角色不在队伍中。')
                        db.execute('DELETE FROM native_team_members WHERE character_id=?',(target,))
                        for m in team['members']:
                            other=self.online(m['character_id'])
                            if other:
                                if m['character_id']==target:
                                    departed.append(other)
                                    message='你已被请离队伍。' if mid==2050 else '已离开队伍。'
                                else:
                                    message=f"{removed['name']}已被请离队伍。" if mid==2050 else f"{removed['name']}离开了队伍。"
                                outgoing.extend(((other,numbers(2032,leader,target)),(other,notice(message))))
                        if target==leader:
                            retired_leader=leader
                            new=next(m['character_id'] for m in team['members'] if m['character_id']!=target)
                            db.execute('UPDATE native_teams SET leader_id=? WHERE id=?',(new,team['id']))
                            from native946_lineups import reset_invalid_selection
                            reset_invalid_selection(db,team['id'],new)
                            for m in team['members']:
                                other=self.online(m['character_id'])
                                if other and other is not game:outgoing.append((other,numbers(2028,new,leader)))
                        refresh=self.team(next(m['character_id'] for m in team['members'] if m['character_id']!=target),db)
                elif mid in (2044,2045):
                    if not team:raise ValueError('你还没有加入队伍。')
                    self.stable(team)
                    if cid==team['leader_id']:raise ValueError('队长不能暂离，请先移交队长。')
                    leader=self.online(team['leader_id'])
                    if mid==2045 and (leader is None):raise ValueError('队长目前不在线，请稍后归队。')
                    close=leader is not None and leader.character['map_id']==game.character['map_id'] and max(abs(leader.character[k]-game.character[k]) for k in ('x','y'))<=12
                    if mid==2045 and not close:
                        c=leader.character;outgoing.append((game,numbers(2024,c['map_id'],c['x'],c['y'])))
                        outgoing.append((game,notice('正在前往队长处，到达后归队。')))
                    else:
                        db.execute('UPDATE native_team_members SET away=? WHERE character_id=?',(int(mid==2044),cid));refresh=self.team(cid,db)
                        outgoing.append((game,notice('已暂离队伍。' if mid==2044 else '已归队，开始跟随队长。')))
                elif mid==3578:
                    if not team or team['leader_id']==cid:raise ValueError('只有队伍成员可以发起队长投票。')
                    if len(team['members'])<2:raise ValueError('队伍至少需要两名成员才能投票。')
                    self.stable(team)
                    self.leader_votes[team['id']]=dict(candidate=cid,votes={cid:1})
                    for m in team['members']:
                        other=self.online(m['character_id'])
                        if other:outgoing.append((other,packet(3577,['string'],[game.character['name']])))
                elif mid==3579:
                    if not team:raise ValueError('你还没有加入队伍。')
                    vote=self.leader_votes.get(team['id'])
                    if vote is None:raise ValueError('当前没有正在进行的队长投票。')
                    if not any(m['character_id']==cid for m in team['members']):raise ValueError('你已经离开当前队伍。')
                    result=args[0]
                    if result not in (0,1):raise ValueError('投票结果无效。')
                    vote['votes'][cid]=result
                    if result==0 or len(vote['votes'])==len(team['members']):
                        self.leader_votes.pop(team['id'],None)
                        passed=all(value==1 for value in vote['votes'].values())
                        if passed:
                            retired_leader=team['leader_id']
                            db.execute('UPDATE native_teams SET leader_id=? WHERE id=?',(vote['candidate'],team['id']))
                            from native946_lineups import reset_invalid_selection
                            reset_invalid_selection(db,team['id'],vote['candidate'])
                        for m in team['members']:
                            other=self.online(m['character_id'])
                            if other:
                                outgoing.append((other,packet(3580,[],[])))
                                if passed:outgoing.append((other,numbers(2028,vote['candidate'],retired_leader)))
                                outgoing.append((other,notice('队长投票已通过。' if passed else '队长投票未通过，队长保持不变。')))
                        refresh=self.team(cid,db)
                    else:
                        outgoing.append((game,notice('投票已记录，等待其他成员投票。')))
                elif mid==2046:
                    self.leader_votes.pop(team['id'],None) if team else None
                    if not team or team['leader_id']!=cid:raise ValueError('只有队长可以移交队长。')
                    self.stable(team);target=args[0]
                    successor=self.online(target)
                    if target==cid or successor is None or not any(m['character_id']==target and not m['away'] for m in team['members']):
                        raise ValueError('请选择已归队的其他在线队员。')
                    db.execute('UPDATE native_teams SET leader_id=? WHERE id=?',(target,team['id']))
                    retired_leader=cid
                    from native946_lineups import reset_invalid_selection
                    reset_invalid_selection(db,team['id'],target);refresh=self.team(cid,db)
                    for m in refresh['members']:
                        other=self.online(m['character_id'])
                        if other:
                            outgoing.extend(((other,numbers(2028,target,cid)),(other,notice(f"{successor.character['name']}已成为队长。"))))
                elif mid in (2055,3129):
                    if not team or team['leader_id']!=cid:raise ValueError('只有队长可以修改队伍设置。')
                    if mid==2055:
                        self.stable(team)
                        from native946_role_arts import validate_lineup
                        validate_lineup(db,cid,args[0])
                        outgoing.append((game,notice('阵法设置已保存，满五名归队队员共同参战后生效。' if args[0] else '已切换为无阵。')))
                    if mid==3129:
                        if args[0] not in (0,1):raise ValueError('队伍设置无效。')
                        outgoing.append((game,notice('已开启队伍医疗。' if args[0] else '已关闭队伍医疗。')))
                    column='lineup' if mid==2055 else 'cure_flag'
                    db.execute('UPDATE native_teams SET '+column+'=? WHERE id=?',(args[0],team['id']));refresh=self.team(cid,db)
                elif mid==2056:
                    if not team or team['leader_id']!=cid:raise ValueError('只有队长可以召集队员。')
                    other=self.online(args[0])
                    if not other or not any(m['character_id']==args[0] for m in team['members']):raise ValueError('队员不在线。')
                    outgoing.append((other,numbers(2027)))
                    outgoing.append((game,notice(f"已邀请{other.character['name']}归队。")))
                elif mid==2057:
                    if not team or team['leader_id']!=cid:raise ValueError('只有队长可以调整位置。')
                    self.stable(team)
                    rows={m['character_id']:m for m in team['members']}
                    if any(t not in rows for t in args):raise ValueError('位置调整目标不在队伍中。')
                    for target,source in zip(args,reversed(args)):db.execute('UPDATE native_team_members SET position=? WHERE character_id=?',(rows[source]['position'],target))
                    refresh=self.team(cid,db)
                    outgoing.append((game,notice('队伍位置已调整。')))
                elif mid==4020:
                    if not team or team['leader_id']!=cid:raise ValueError('只有队长可以设置委托指挥。')
                    self.stable(team)
                    target,direct=args
                    if target==cid or target not in {m['character_id'] for m in team['members']}:raise ValueError('请选择要委托指挥的其他队员。')
                    if direct not in (0,1):raise ValueError('委托指挥设置无效。')
                    direct_ids=[m for m in team['direct_ids'] if m!=target]
                    if direct:direct_ids.append(target)
                    db.execute('UPDATE native_teams SET direct_ids=? WHERE id=?',(json.dumps(direct_ids),team['id']))
                    refresh=self.team(cid,db)
                    outgoing.append((game,notice('已设置委托指挥。' if direct else '已取消委托指挥。')))
                else:raise ValueError('该队伍扩展功能尚未开放。')
            if retired_leader is not None:
                invitations={key:expires for key,expires in invitations.items() if key[0]!=retired_leader}
                applications={key:expires for key,expires in applications.items() if key[0]!=retired_leader}
            self.invitations=invitations;self.applications=applications
            if mid in (2038,2041):self.leader_votes.pop(refresh['id'],None)
            for other,reply in outgoing:
                if other:self.enqueue(other,reply)
            for other in departed:
                other.character['team_leader']=0
                speed_updates=self.refresh_run_speeds(other)
                if speed_updates:self.enqueue(other,*speed_updates)
            if refresh:self.publish(refresh)
            # The 946 client only redraws the application cards on a full list.
            if mid in (2041,2047,2048):self.enqueue(game,self.application_list(cid))
            if refresh and mid in (2038,2041,2045):
                try:self.follow_team(refresh)
                except StorageBusy:self.pending_follow.add(refresh['id'])
            return ()
        except BaseException:raise

    def follow_team(self,team,*,relocate=False):
        if not team:return
        leader=self.online(team['leader_id'])
        if not leader or leader.battle:return
        relocate=relocate or team['id'] in getattr(self,'pending_follow_relocations',set())
        followers=[self.online(m['character_id']) for m in team['members'] if m['character_id']!=team['leader_id'] and not m['away']]
        followers=[g for g in followers if g is not None and not g.battle]
        # Follow updates commit together; a failed transaction publishes none.
        from native946_map import load_map
        updates=[]
        with transaction(self.database,write=True,batchable=True):
            for g in followers:
                old=deepcopy(g.character);c=leader.character
                revision=g.characters.change_scene(g.character['owner'],g.character['id'],g.character['map_id'],c['map_id'],c['x'],c['y'],g.character.get('position_revision',0))
                updates.append((g,old,revision))
        for g,old,revision in updates:
            c=leader.character;g.character.update(map_id=c['map_id'],x=c['x'],y=c['y'],position_revision=revision)
            g.grid=load_map(c['map_id']);g.npc_position=g.practice_npc_position()
            g.progression._cached_snapshot=None;g.last_path={(c['x'],c['y'])}
            g._arrival_guard=(c['x'],c['y']);g._last_transition=None
            g.dialog_open=False;g.dialog_npc=None;g.dialog_actions=[];g.npc_service_session=None
        # Apply every follower position before serializing any scene. Each
        # refreshed client must see the whole team in the new coordinate frame.
        refreshed={g.character['id'] for g,old,_ in updates if relocate or old['map_id']!=g.character['map_id']}
        for g,old,_ in updates:
            c=g.character
            if c['id'] in refreshed:
                g._world_visible=set();g._world_bucket=None;g._shown_summon_id=None
                self.enqueue(g,*g.restore_scene(),*g.scene_objects(),*g.sync_progression())
            else:
                self.enqueue(g,packet(1608,['number','number','word','word','word','word'],[g.character['id'],g.character.get('run_speed',DEFAULT_RUN_SPEED),old['x'],old['y'],c['x'],c['y']]),*g.sync_world_npcs())
        for g,old,_ in updates:
            if g.character['id'] in refreshed:
                self.publish_relocation(g,(old['map_id'],old['x'],old['y']),exclude=refreshed)
        self.pending_follow.discard(team['id'])
        getattr(self,'pending_follow_relocations',set()).discard(team['id'])

    def participants(self,game):
        team=self.team(game.character['id'])
        if team is None:return [game]
        if team['leader_id']!=game.character['id']:
            row=next(m for m in team['members'] if m['character_id']==game.character['id'])
            return [game] if row['away'] else []
        result=[];c=game.character
        for row in team['members']:
            other=self.online(row['character_id'])
            if row['away'] or not other or other.battle:continue
            if other.character['map_id']==c['map_id'] and max(abs(other.character[k]-c[k]) for k in ('x','y'))<=12:result.append(other)
        return result

    def detach(self,cid):
        with self.lock:
            game = self.online(cid)
            if game is not None:
                from native946_trade import Trade
                Trade(self,game).close()
                game._party_connected = False
                game._auto_ghost_until = 0
                from native946_temp_inventory import close_session
                try:
                    close_session(game.progression)
                except StorageBusy:
                    from native946_runtime import METRICS
                    METRICS.increment('temp_inventory_disconnect_busy')
                remove = numbers(1604, cid)
                # Login advertises this actor to the whole scene.
                for other in tuple(self.games.values()):
                    if (game.character['map_id'] not in INSTANCE_SCENES and other is not game
                            and not other.battle and other.character['map_id'] == game.character['map_id']):
                        self._enqueue(other, remove)
                from native946_friends import presence_changed
                presence_changed(game)
            team=self.team(cid)
            if cid in getattr(self,'match_entries',{}):
                from native946_team_platform import Platform
                Platform(self).cancel(cid)
            if team:
                self.repair_leader(team);team=self.team(cid);self.publish(team)
            battle=self.battles.get(cid)
            if battle:
                remaining = [owner for owner in battle.games if self.online(owner)]
                if not remaining:battle.finish(False)
                elif battle.leader not in remaining:
                    map_leader = team['leader_id'] if team else None
                    battle.leader = map_leader if map_leader in remaining else remaining[0]

    def detach_port(self,port):
        with self.lock:
            for cid,game in list(self.games.items()):
                if getattr(game,'source_port',None)==port:self.detach(cid)

    def repair_leader(self,team):
        if not team or self.online(team['leader_id']):return
        successor=next((m['character_id'] for m in team['members'] if not m['away'] and self.online(m['character_id'])),None)
        if successor is None:return
        old=team['leader_id']
        try:
            with transaction(self.database,write=True,batchable=True) as db:
                changed=db.execute('UPDATE native_teams SET leader_id=? WHERE id=? AND leader_id=?',(successor,team['id'],old)).rowcount
                if changed:
                    db.execute('UPDATE native_team_members SET away=1 WHERE character_id=?',(old,))
                    from native946_lineups import reset_invalid_selection
                    reset_invalid_selection(db,team['id'],successor)
        except StorageBusy:return
        if changed:
            self.invitations={key:expires for key,expires in self.invitations.items() if key[0]!=old}
            self.applications={key:expires for key,expires in self.applications.items() if key[0]!=old}
            self.publish(self.team(successor),extras=(numbers(2028,successor,old),))

    def refresh_battle_scene(self, game):
        if self.online(game.character['id']) is not game:return
        try:
            refresh = (*game.restore_scene(), *game.scene_objects(), *game.sync_progression())
        except Exception as error:
            failure = (type(error), str(error))
            if not isinstance(error, StorageBusy) and getattr(game, '_party_refresh_error', None) != failure:
                logging.getLogger(__name__).exception('Battle scene refresh failed for character %s', game.character['id'])
            game._party_refresh_error = failure
            game._party_refresh_pending = True
            game._party_refresh_at = monotonic() + 1
            return
        self.enqueue(game, *refresh)
        game._party_refresh_pending = False
        game._party_refresh_error = None
        game._party_refresh_at = 0

    def tick(self,game,now=None):
        with self.lock:
            cid=game.character['id']
            battle=self.battles.get(cid)
            trade=getattr(self,'trades',{}).get(cid)
            if trade is not None:
                from native946_trade import Trade
                closed=Trade(self,game).maintain(trade)
                if closed:self._enqueue(game,*closed)
            try:
                if getattr(self,'match_entries',None) and monotonic()>=getattr(self,'match_tick_at',0):
                    from native946_team_platform import Platform
                    self.match_tick_at=monotonic()+1
                    Platform(self).match()
                if monotonic()>=getattr(game,'_run_speed_check_at',0):
                    speed_updates = self.refresh_run_speeds(game)
                    if speed_updates:self.enqueue(game,*speed_updates)
                if battle:battle.tick(now)
                else:
                    team=self.team(game.character['id'])
                    if team:
                        self.repair_leader(team)
                        if team['id'] in self.pending_follow:
                            relocations=getattr(self,'pending_follow_relocations',set())
                            self.follow_team(team,relocate=team['id'] in relocations)
                    if (getattr(game,'_party_refresh_pending',False)
                            and (monotonic() if now is None else now)>=getattr(game,'_party_refresh_at',0)):
                        self.refresh_battle_scene(game)
            except StorageBusy:pass
            return self.drain(game)

    def dispatch(self,game,mid,reader,local):
        # Independent panels/training keep their original per-session path.
        # Contact requests also share presence with login and detach.
        from native946_friends import REQUESTS as FRIEND_REQUESTS
        from native946_mail import REQUESTS as MAIL_REQUESTS, Mail
        from native946_trade import REQUESTS as TRADE_REQUESTS, Trade
        from native946_give import REQUESTS as GIVE_REQUESTS, receive as give
        from native946_team_platform import REQUESTS as PLATFORM_REQUESTS, Platform
        from native946_team_lottery import REQUESTS as LOTTERY_REQUESTS, receive as lottery
        from native946_song_fireworks import SEND_FIREWORK
        if (mid not in REQUESTS and mid not in FRIEND_REQUESTS and mid not in MAIL_REQUESTS and mid not in TRADE_REQUESTS
                and mid not in GIVE_REQUESTS and mid not in PLATFORM_REQUESTS and mid not in LOTTERY_REQUESTS
                and mid not in (SEND_FIREWORK,1032,1034,1643,1644,1645,1764,1765,5259,5438)
                and game.character['id'] not in self.battles and game.character['id'] not in getattr(self,'trades',{})):
            return local(mid,reader)
        with self.lock:
            cid=game.character['id'];team=self.team(cid) if mid in (1643,1644,1645,1764,1765,1032) else None
            if mid in (1643,1644,1645,1764,1765) and team and team['leader_id']!=cid:
                member=next(m for m in team['members'] if m['character_id']==cid)
                if not member['away']:
                    return (notice('正在跟随队长，请先暂离队伍再单独行动。'),packet(1606,['number','word','word'],[cid,game.character['x'],game.character['y']]))
            try:
                if mid in REQUESTS:result=self.receive(game,mid,reader)
                elif mid in MAIL_REQUESTS:result=Mail(self,game).receive(mid,reader)
                elif mid in TRADE_REQUESTS:result=Trade(self,game).receive(mid,reader)
                elif mid in GIVE_REQUESTS:result=give(self,game,mid,reader)
                elif mid in PLATFORM_REQUESTS:result=Platform(self).receive(game,reader)
                elif mid in LOTTERY_REQUESTS:result=lottery(mid,reader)
                elif cid in self.battles and mid in (2175,2176,2177,2178,2181,2182,2183,2184,2185,2317,2757,3819,4891,5259):result=self.battles[cid].receive(game,mid,reader)
                else:
                    before=(game.character['map_id'],game.character['x'],game.character['y'])
                    previous_transition=getattr(game,'_last_transition',None)
                    result=local(mid,reader)
                    after=(game.character['map_id'],game.character['x'],game.character['y'])
                    if before!=after:
                        transition=getattr(game,'_last_transition',None)
                        relocate=before[0]!=after[0] or (transition is not None and transition!=previous_transition)
                        if relocate:self.publish_relocation(game,before)
                        try:self.follow_team(team,relocate=relocate)
                        except StorageBusy:
                            if team:
                                self.pending_follow.add(team['id'])
                                if relocate:
                                    if not hasattr(self,'pending_follow_relocations'):self.pending_follow_relocations=set()
                                    self.pending_follow_relocations.add(team['id'])
                        for other in self.games.values():
                            if (after[0] not in INSTANCE_SCENES and not relocate and other is not game
                                    and not other.battle and other.character['map_id']==after[0] and before[0]==after[0]):
                                speed=game.character.get('run_speed',DEFAULT_RUN_SPEED)
                                self.enqueue(other,packet(1608,['number','number','word','word','word','word'],[cid,speed,before[1],before[2],after[1],after[2]]))
                        if mid==1643:
                            from native946_wilderness import moved
                            extra=moved(game,before,after)
                            if extra:result=(*packets(result),*packets(extra))
                pending=self.drain(game)
                return (*pending,*packets(result)) if pending else result
            except (ValueError,StorageBusy) as error:
                game.last_event={'action':'action_rejected','reason':str(error)}
                pending=self.drain(game);rejected=text_notice(str(error))
                battle=self.battles.get(cid);retry=()
                if battle and battle.state['phase']=='input':
                    from native946_battle_actions import BATTLE_ORDER_REQUESTS,retry_input_packets
                    if mid in BATTLE_ORDER_REQUESTS:
                        retry=retry_input_packets(battle.pending_actor(cid),battle.state['deadline'],shared=True)
                return (*pending,rejected,*retry) if pending or retry else rejected
