"""Native tackle collection, equipped baskets and server-owned fishing casts."""
import json
from secrets import SystemRandom
from time import time
from native946_codec import packet
from native946_codec import text_notice
from native946_inventory_features import DATA,RULES,owned,consume,give

FISH=DATA['fish'];PAGES=tuple(range(7000,7004));RNG=SystemRandom()
POINT_BASE=9_000_000

def point_id(scene,post):return POINT_BASE+scene*100+post

def is_point(nid):
    if type(nid) is not int or nid<POINT_BASE:return False
    scene,post=divmod(nid-POINT_BASE,100)
    return str(scene) in FISH['spot'] and 1<=post<=len(FISH['spot'][str(scene)]['PostTbl'])

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_fish_state (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      selected TEXT NOT NULL DEFAULT '{}',tools TEXT NOT NULL DEFAULT '{}',
      bait TEXT NOT NULL DEFAULT '{}',baskets TEXT NOT NULL DEFAULT '{}',
      level INTEGER NOT NULL DEFAULT 1,exp INTEGER NOT NULL DEFAULT 0,
      auto_until INTEGER NOT NULL DEFAULT 0,auto_flag INTEGER NOT NULL DEFAULT 0,
      atlas TEXT NOT NULL DEFAULT '[]',reward_step INTEGER NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_fishing_casts (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      scene_id INTEGER NOT NULL,post_id INTEGER NOT NULL,direction INTEGER NOT NULL,
      lease_until INTEGER NOT NULL,seat_until INTEGER NOT NULL,cast_until INTEGER NOT NULL DEFAULT 0,
      fish_type INTEGER NOT NULL DEFAULT 0,UNIQUE(scene_id,post_id))''')

def state(db,cid):
    row=db.execute('SELECT * FROM native_fish_state WHERE character_id=?',(cid,)).fetchone()
    result=dict(row) if row else dict(character_id=cid,selected='{}',tools='{}',bait='{}',baskets='{}',level=1,exp=0,auto_until=0,auto_flag=0,atlas='[]',reward_step=0)
    for key in ('selected','tools','bait','baskets','atlas'):result[key]=json.loads(result[key])
    return result

def save(db,s):
    keys=('selected','tools','bait','baskets','level','exp','auto_until','auto_flag','atlas','reward_step')
    values=[json.dumps(s[k]) if k in ('selected','tools','bait','baskets','atlas') else s[k] for k in keys]
    db.execute('INSERT INTO native_fish_state(character_id,'+','.join(keys)+') VALUES('+','.join('?' for _ in range(11))+') ON CONFLICT(character_id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in keys),(s['character_id'],*values))

def capacity(db,cid):
    s=state(db,cid);result={}
    for index,page in enumerate(PAGES,4):
        kind=str(s['selected'].get(str(index),0));meta=FISH['basket'].get(kind)
        if meta and s['baskets'].get(kind,0)>0:result[page]=meta['capacity']
    return result

def validate_position(db,cid,kind,page,slot):
    if page in PAGES:
        if str(kind) not in FISH['fishes']:raise ValueError('鱼篓只能存放鱼获。')
        if type(slot) is not int or not 1<=slot<=capacity(db,cid).get(page,0):raise ValueError('该鱼篓未装备或格子尚未开放。')

class Fishing:
    def __init__(self,game):self.g=game;self.p=game.progression
    def scene_packets(self,force=False):
        c=self.g.character;scene=c['map_id'];spot=FISH['spot'].get(str(scene),{})
        old=set() if force or getattr(self.g,'fish_marker_map',None)!=scene else set(getattr(self.g,'fish_markers',()))
        visible={i for i,pos in enumerate(spot.get('PostTbl',[]),1) if max(abs(c['x']-pos[0]),abs(c['y']-pos[1]))<=72}
        result=[packet(1604,['number'],[point_id(scene,i)]) for i in sorted(old-visible)]
        for i in sorted(visible-old):
            x,y,direction=spot['PostTbl'][i-1]
            # Client model catalog d99b32ec: 5115 is the original 鱼漂 model.
            result.append(packet(1589,['oci_npc'],[dict(id=point_id(scene,i),name='钓鱼点',title='',type=5115,
                class_type=1,x=x*20,y=y*20,dir=direction,layer=1,member_click=1)]))
        self.g.fish_markers=visible;self.g.fish_marker_map=scene
        return tuple(result)
    def look(self,nid):
        if not is_point(nid):raise ValueError('钓鱼点无效。')
        scene,post=divmod(nid-POINT_BASE,100);self.near(scene,post)
        with self.p.transaction(write=False) as db:
            seat=db.execute('SELECT * FROM native_fishing_casts WHERE scene_id=? AND post_id=? AND lease_until>?',(scene,post,int(time()))).fetchone()
            role=db.execute('SELECT role_id,level FROM characters WHERE id=?',(seat['character_id'],)).fetchone() if seat else None
            fisher=state(db,seat['character_id']) if seat else None
        return packet(3581,['number']*8,[int(seat is not None),seat['character_id'] if seat else 0,role['role_id'] if role else 0,
            role['level'] if role else 0,fisher['level'] if fisher else 0,scene,post,max(0,seat['seat_until']-int(time())) if seat else 0])
    def scene_fishers(self):
        c=self.g.character;result=[]
        with self.p.transaction(write=False) as db:
            rows=db.execute('SELECT f.* FROM native_fishing_casts f JOIN characters c ON c.id=f.character_id WHERE f.scene_id=? AND c.map_id=f.scene_id AND f.lease_until>? AND f.seat_until>? AND abs(c.x-?)<=96 AND abs(c.y-?)<=96',
                            (c['map_id'],int(time()),int(time()),c['x'],c['y'])).fetchall()
            for seat in rows:
                s=state(db,seat['character_id'])
                result.append(packet(3584,['number']*4,[seat['character_id'],s['selected'].get('2',0),s['selected'].get('1',0),seat['direction']]))
        return tuple(result)
    def publish(self,public,result,scene=None,point=None):
        service=getattr(self.g,'party_service',None)
        if service is not None:
            c=self.g.character;sid=c['map_id'] if scene is None else scene;position=(c['x'],c['y']) if point is None else point
            with service.lock:
                for other in service.games.values():
                    if other is not self.g and not other.battle and other.character['map_id']==sid and max(abs(other.character[k]-position[i]) for i,k in enumerate(('x','y')))<=96:
                        service.enqueue(other,*(public if isinstance(public,(list,tuple)) else (public,)))
        return result
    def parts(self,s):
        return ({int(k):v for k,v in s['tools'].items() if k in FISH['yugan'] and v>0},
                {int(k):v for k,v in s['tools'].items() if k in FISH['chair'] and v>0},
                {int(k):v for k,v in s['bait'].items() if v>0},
                {int(k):v for k,v in s['baskets'].items() if v>0})
    def panel(self):
        from native946_progression import lua_data
        with self.p.transaction(write=False) as db:s=state(db,self.p.cid)
        rod,chair,bait,baskets=self.parts(s);selected=s['selected']
        return packet(3572,['number']*3+['string']*5+['number']*5,
           [selected.get('1',0),selected.get('3',0),selected.get('2',0),lua_data([selected.get(str(i),0) for i in range(4,8)]),
            lua_data(rod),lua_data(chair),lua_data(bait),lua_data(baskets),s['level'],s['exp'],int(s['auto_flag'] and s['auto_until']>time()),max(0,s['auto_until']-int(time())),RULES['fish_level_exp']*s['level']])
    def refresh(self):
        from native946_progression import lua_data
        with self.p.transaction(write=False) as db:s=state(db,self.p.cid)
        rod,chair,bait,baskets=self.parts(s)
        return packet(3603,['string']*2,[lua_data(rod),lua_data(bait)])
    def register(self,iid):
        with self.p.transaction() as db:
            item=owned(db,self.p.cid,iid,fish=True);kind=item['type'];key=str(kind);s=state(db,self.p.cid)
            if key in FISH['yugan'] or key in FISH['chair']:
                s['tools'][key]=s['tools'].get(key,0)+100
                selected='1' if key in FISH['yugan'] else '2'
                if not s['selected'].get(selected):s['selected'][selected]=kind
            elif key in FISH['basket']:
                s['baskets'][key]=s['baskets'].get(key,0)+1
                empty=next((str(i) for i in range(4,8) if not s['selected'].get(str(i))),None)
                if empty:s['selected'][empty]=kind
            elif key in FISH['bait'] or key in FISH['fishes']:
                s['bait'][key]=s['bait'].get(key,0)+1
                if not s['selected'].get('3'):s['selected']['3']=kind
            elif kind in (47512,47513,47514):s['auto_until']=max(int(time()),s['auto_until'])+{47512:1,47513:7,47514:30}[kind]*86400
            elif 47465<=kind<=47470:
                which='1' if kind<=47467 else '2';target=str(s['selected'].get(which,0))
                if target=='0':raise ValueError('请先在钓鱼面板选中要修理的鱼竿或座椅。')
                grade=min(3,int(target)-47447) if which=='1' else min(3,int(target)-47452)
                maximum=(kind-47464) if which=='1' else kind-47467
                if grade>maximum:raise ValueError('该修理锤无法修理当前豪华度的钓具。')
                s['tools'][target]=s['tools'].get(target,0)+100
            else:raise ValueError('该道具不能收录为钓鱼工具。')
            consume(db,self.p.cid,iid,fish=True);save(db,s)
        self.g.last_event=dict(action='fishing_item_registered',type=kind)
        return self.g.defer_packets(lambda:(*self.p.attribute_packets(),*self.p.item_packets(),self.panel(),self.refresh()))
    def use(self,index,kind):
        if index not in range(1,8):raise ValueError('钓具槽位无效。')
        with self.p.transaction() as db:
            s=state(db,self.p.cid);key=str(kind)
            if db.execute('SELECT 1 FROM native_fishing_casts WHERE character_id=? AND cast_until>0',(self.p.cid,)).fetchone():raise ValueError('请先收竿再更换钓具。')
            if kind:
                group='tools' if index in (1,2) else 'bait' if index==3 else 'baskets'
                catalog=FISH['yugan'] if index==1 else FISH['chair'] if index==2 else None if index==3 else FISH['basket']
                if not s[group].get(key,0) or (catalog is not None and key not in catalog):raise ValueError('尚未收录该钓具，或数量/耐久不足。')
                if index>=4 and sum(v==kind for k,v in s['selected'].items() if k!=str(index) and k in ('4','5','6','7'))>=s['baskets'].get(key,0):raise ValueError('已装备同类鱼篓数量超过持有数量。')
            if index>=4:
                limit=FISH['basket'].get(key,{}).get('capacity',0)
                if db.execute('SELECT 1 FROM inventory WHERE character_id=? AND page=? AND slot>?',(self.p.cid,7000+index-4,limit)).fetchone():raise ValueError('请先取出超出新鱼篓容量的鱼获。')
            s['selected'][str(index)]=kind;save(db,s)
        return self.g.defer_packets(lambda:(*self.p.attribute_packets(),packet(3573,['number']*2,[index,kind]),self.refresh(),*self.p.item_packets()))
    def cast(self,db):
        now=int(time());s=state(db,self.p.cid);seat=db.execute('SELECT * FROM native_fishing_casts WHERE character_id=?',(self.p.cid,)).fetchone()
        if not seat or seat['lease_until']<=now or seat['seat_until']<=now:raise ValueError('请先占用附近的钓鱼点。')
        self.near(seat['scene_id'],seat['post_id'])
        if seat['cast_until']:raise ValueError('已经抛竿，请等待咬钩后收竿。')
        rod,chair,bait=(str(s['selected'].get(str(i),0)) for i in (1,2,3))
        if rod not in FISH['yugan'] or chair not in FISH['chair'] or s['tools'].get(rod,0)<1 or s['tools'].get(chair,0)<1 or s['bait'].get(bait,0)<1:raise ValueError('鱼竿、座椅或鱼饵不足，请补充后再抛竿。')
        if not capacity(db,self.p.cid):raise ValueError('请先装备鱼篓。')
        spot=FISH['spot'][str(seat['scene_id'])]
        candidates=[int(k) for k in spot['FishTbl'] if int(bait) in FISH['fishes'][k]['YuerTbl']]
        if not candidates:raise ValueError('当前鱼饵不适合这个钓鱼点。')
        selected=RNG.choice(candidates)
        # Native gear advertises +0/+10/+25%; absent base chance is a labelled local rule.
        grade=min(3,int(rod)-47447);chairgrade=min(3,int(chair)-47452)
        chance=min(100,RULES['fish_base_success']+{1:0,2:10,3:25}[grade]+{1:0,2:10,3:25}[chairgrade])
        if int(bait) in (int(k) for k in FISH['fishes']) and min(grade,chairgrade)<FISH['fishes'][bait]['Tag']+1:chance//=4
        if RNG.randrange(100)>=chance:selected=0
        s['tools'][rod]-=1;s['tools'][chair]-=1;s['bait'][bait]-=1;save(db,s)
        ready=now+RULES['fish_cast_seconds']
        db.execute('UPDATE native_fishing_casts SET cast_until=?,fish_type=?,lease_until=? WHERE character_id=?',(ready,selected,now+120,self.p.cid))
        self.g.fishing_wake=ready;self.g.fishing_bite_sent=False
        return (packet(3585,['number'],[self.p.cid]),self.refresh())
    def near(self,scene,post):
        spot=FISH['spot'].get(str(scene));c=self.g.character
        if not spot or c['map_id']!=scene or type(post) is not int or not 1<=post<=len(spot['PostTbl']):raise ValueError('钓鱼点不在当前场景。')
        pos=spot['PostTbl'][post-1]
        if max(abs(c['x']-pos[0]),abs(c['y']-pos[1]))>12:raise ValueError('请先走到钓鱼点附近。')
        return spot,pos
    def occupy(self,scene,post):
        from native946_progression import lua_data
        spot,pos=self.near(scene,post);now=int(time())
        with self.p.transaction() as db:
            s=state(db,self.p.cid)
            if s['level']<spot['Level']:raise ValueError('钓鱼段位不足。')
            if any(not s['selected'].get(str(i)) for i in (1,2,3)):raise ValueError('请先选用鱼竿、座椅和鱼饵。')
            db.execute('DELETE FROM native_fishing_casts WHERE lease_until<=?',(now,))
            occupied=db.execute('SELECT character_id FROM native_fishing_casts WHERE scene_id=? AND post_id=?',(scene,post)).fetchone()
            if occupied and occupied[0]!=self.p.cid:raise ValueError('这个钓鱼点已有人使用。')
            old=db.execute('SELECT * FROM native_fishing_casts WHERE character_id=?',(self.p.cid,)).fetchone()
            if old and old['cast_until']:raise ValueError('请先收竿或离开原钓鱼点。')
            db.execute('INSERT INTO native_fishing_casts VALUES(?,?,?,?,?,?,0,0) ON CONFLICT(character_id) DO UPDATE SET scene_id=excluded.scene_id,post_id=excluded.post_id,direction=excluded.direction,lease_until=excluded.lease_until,seat_until=excluded.seat_until,cast_until=0,fish_type=0',
                       (self.p.cid,scene,post,pos[2],now+120,now+600))
            if not self.g.grid.walkable(pos[0],pos[1]):raise ValueError('钓鱼座位当前不可到达。')
            self.g.characters.move(self.g.character['owner'],self.p.cid,scene,pos[0],pos[1])
            self.g.character.update(x=pos[0],y=pos[1]);self.g.last_path={(pos[0],pos[1])}
            rod,chair,bait,baskets=self.parts(s)
        self.g.fishing_wake=now+1;self.g.fishing_bite_sent=False
        result=(packet(1606,['number','word','word'],[self.p.cid,pos[0],pos[1]]),
                packet(3584,['number']*4,[self.p.cid,s['selected']['2'],s['selected']['1'],pos[2]]),
                packet(3594,['number']*2+['string']*2,[s['selected']['1'],s['selected']['3'],lua_data(rod),lua_data(bait)]))
        return self.g.defer_packets(lambda:self.publish(result[:2],result))
    def pull(self,db):
        now=int(time());seat=db.execute('SELECT * FROM native_fishing_casts WHERE character_id=?',(self.p.cid,)).fetchone()
        if not seat or not seat['cast_until']:raise ValueError('尚未抛竿。')
        self.near(seat['scene_id'],seat['post_id'])
        s=state(db,self.p.cid);caught=seat['fish_type'] if seat['cast_until']<=now<=seat['cast_until']+30 else 0;new=0
        if caught:
            give(db,self.p,caught,destinations=capacity(db,self.p.cid));new=int(caught not in s['atlas'])
            if new:s['atlas'].append(caught)
            s['exp']+=RULES['fish_catch_exp']
            while s['exp']>=RULES['fish_level_exp']*s['level'] and s['level']<RULES['fish_max_level']:
                s['exp']-=RULES['fish_level_exp']*s['level'];s['level']+=1
            if s['level']>=RULES['fish_max_level']:s['exp']=min(s['exp'],RULES['fish_level_exp']*s['level'])
        save(db,s)
        db.execute('UPDATE native_fishing_casts SET cast_until=0,fish_type=0,lease_until=? WHERE character_id=?',(now+120,self.p.cid))
        self.g.fishing_wake=now+2;self.g.fishing_bite_sent=False
        self.g.last_event=dict(action='fishing_retrieved',fish_type=caught,new_atlas=new)
        notices=(packet(3595,['number']*2,[caught,new]),) if caught else (text_notice('这次没有钓到鱼。'),)
        return (packet(3586,['number'],[self.p.cid]),*notices,*self.p.item_packets(),self.refresh())
    def leave(self):
        with self.p.transaction() as db:
            seat=db.execute('SELECT scene_id,post_id FROM native_fishing_casts WHERE character_id=?',(self.p.cid,)).fetchone()
            db.execute('DELETE FROM native_fishing_casts WHERE character_id=?',(self.p.cid,))
        self.g.fishing_wake=None;self.g.fishing_bite_sent=False
        result=(packet(3583,['number'],[self.p.cid]),packet(1183,['string'],['dlg_fishing_panel']))
        scene=seat['scene_id'] if seat else None
        pos=FISH['spot'][str(scene)]['PostTbl'][seat['post_id']-1][:2] if seat else None
        return self.g.defer_packets(lambda:self.publish(result[0],result,scene,pos))
    def atlas(self):
        from native946_progression import lua_data
        with self.p.transaction(write=False) as db:s=state(db,self.p.cid)
        return packet(3591,['string','number'],[lua_data(s['atlas']),s['reward_step']])
    def receive(self,mid,r):
        if self.g.battle is not None:raise ValueError('战斗中不能操作钓鱼。')
        if mid==3575:
            r.finish();return (*self.p.attribute_packets(),self.panel(),*self.scene_packets(),text_notice('右键收录钓具和鱼饵后选用，点击场景中的钓鱼点抛竿。本机规则：8秒咬钩，30秒内收竿，每次消耗1鱼饵和1点钓具耐久，成功增加10熟练。'))
        if mid==3576:
            index,kind=r.integer(),r.integer();r.finish();return self.use(index,kind)
        if mid==3588:
            scene,post=r.integer(),r.integer();r.finish();return self.occupy(scene,post)
        if mid==3574:
            flag=r.integer();r.finish()
            if flag not in (0,1):raise ValueError('自动钓鱼开关无效。')
            with self.p.transaction() as db:
                s=state(db,self.p.cid)
                if flag and s['auto_until']<=time():raise ValueError('请先使用寒江独钓增加自动钓鱼时间。')
                s['auto_flag']=flag;save(db,s)
            self.g.fishing_wake=int(time())+1
            return packet(3571,['number'],[flag])
        r.finish()
        if mid==3587:return self.leave()
        if mid==3593:return self.atlas()
        if mid==3592:
            with self.p.transaction() as db:
                s=state(db,self.p.cid);step=s['reward_step']
                if step>=4 or len(s['atlas'])<(step+1)*10:raise ValueError('当前没有可领取的图鉴奖励。')
                give(db,self.p,(47190,47221,47440,47395)[step]);s['reward_step']+=1;save(db,s)
            return self.g.defer_packets(lambda:(*self.p.item_packets(),self.atlas()))
        if mid in (3589,3590):
            with self.p.transaction() as db:
                result=self.cast(db) if mid==3589 else self.pull(db)
            return self.g.defer_packets(lambda:self.publish(result[0],result))
        raise ValueError('钓鱼操作无效。')
    def tick(self):
        now=int(time());wake=getattr(self.g,'fishing_wake',None)
        if wake is None:
            if getattr(self.g,'fishing_initialized',False):return ()
            self.g.fishing_initialized=True
            with self.p.transaction(write=False) as db:seat=db.execute('SELECT * FROM native_fishing_casts WHERE character_id=?',(self.p.cid,)).fetchone()
            if not seat:return ()
            wake=now;self.g.fishing_wake=wake
        if wake>now:return ()
        with self.p.transaction(write=False) as db:
            seat=db.execute('SELECT * FROM native_fishing_casts WHERE character_id=?',(self.p.cid,)).fetchone();s=state(db,self.p.cid)
        if not seat:self.g.fishing_wake=None;return ()
        try:
            self.near(seat['scene_id'],seat['post_id'])
            if seat['seat_until']<=now or seat['lease_until']<=now:return self.leave()
            auto=bool(s['auto_flag'] and s['auto_until']>now)
            self.g.fishing_wake=now+1
            if seat['cast_until']:
                if now>=seat['cast_until']:
                    if auto:
                        with self.p.transaction() as db:result=self.pull(db)
                        return self.publish(result[0],result)
                    if now>seat['cast_until']+30:
                        with self.p.transaction() as db:result=self.pull(db)
                        return self.publish(result[0],result)
                    if not getattr(self.g,'fishing_bite_sent',False):
                        self.g.fishing_bite_sent=True
                        # This client version's set_fish_bite callback only logs;
                        # the stock notice is the visible cue to press 收竿.
                        return (packet(3582,['number'],[seat['fish_type']]),text_notice('浮漂有动静了，请及时收竿。'))
            elif auto:
                with self.p.transaction() as db:result=self.cast(db)
                return self.publish(result[0],result)
            if now+60>=seat['lease_until']:
                with self.p.transaction() as db:db.execute('UPDATE native_fishing_casts SET lease_until=? WHERE character_id=?',(now+120,self.p.cid))
            return ()
        except ValueError as error:
            with self.p.transaction() as db:
                s=state(db,self.p.cid);s['auto_flag']=0;save(db,s)
            return (*self.leave(),text_notice(str(error)))
