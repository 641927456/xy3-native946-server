"""946 mall use flows with source-backed rewards and owned persistent licenses."""
from pathlib import Path
from time import time,monotonic
import json,secrets
from native946_codec import packet
from native946_codec import text_notice
from native946_mall_mounts import FIXED_DYES, REQUESTS as MOUNT_REQUESTS, WRITES as MOUNT_WRITES

DATA=json.loads(Path(__file__).with_name('native946_mall_uses_data.json').read_text('utf8'))
from native946_premium_items import RULES as PREMIUM_RULES,PENDING as PREMIUM_PENDING
from native946_child_halos import RULES as CHILD_HALO_RULES
DATA['rules'].update(PREMIUM_RULES)
DATA['rules'].update(CHILD_HALO_RULES)
DATA['child_halos'] = CHILD_HALO_RULES
DATA.setdefault('pending_rules',{}).update(PREMIUM_PENDING)
from native946_nice_ids import VOUCHERS as NICE_ID_VOUCHERS, CERTIFICATE as NICE_ID_CERTIFICATE
for _kind in NICE_ID_VOUCHERS:
    DATA['rules'][str(_kind)] = dict(op='number_voucher',local_rule='documented_nice_id_catalog')
DATA['rules'][str(NICE_ID_CERTIFICATE)] = dict(op='number_certificate',local_rule='documented_nice_id_catalog')
DATA['rules']['45850'] = dict(op='fudou',local_rule='low_price_appearance_coupon')
DATA['rules']['46782'] = dict(op='halo_refinement',local_rule='native_ranges_local_refinement_rolls')
DATA['rules']['46517'] = dict(op='fashion_refinement',local_rule='local_fashion_strengthening')
DATA['rules']['47580'] = dict(op='shenbing_ring',local_rule='native_ring_identity_local_base_stats')
DATA['rules']['47415'] = dict(op='jing_essence_pack')
DATA['rules']['47680'] = dict(op='guardian_ornament_panel')
DATA['rules']['48017'] = dict(op='guardian_ornament_panel')
DATA['rules'].update({str(kind): dict(op='fixed_mount_dye') for kind in FIXED_DYES})
from native946_mall_event_boxes import RULES as EVENT_BOX_RULES
DATA['rules'].update(EVENT_BOX_RULES)
for _kind, _rule in DATA['pending_rules'].items():
    if _rule.get('op') == 'guardian_appearance':
        DATA['rules'][_kind] = dict(
            op=_rule['op'], mode=_rule['mode'], rate=_rule['rate'],
            local_rule='uniform_distinct_appearance',
        )
REQUESTS={112,1496,2289,2296,2300,2302,2312,2314,3309,3310,3474,3475,3476,3683,4475,4476,3969,3971,4041,4042,238,239,240,280,2597,2598,2599}|MOUNT_REQUESTS
WRITES={2289,2300,2312,2314,3309,3310,3474,3475,3476,4475,3971,4042,238,239,2597,2599}|MOUNT_WRITES
MOUNT_ITEM_TYPES = {int(kind): rule['value'] for kind, rule in DATA['rules'].items() if rule['op'] == 'mount'}

def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS cosmetic_features (character_id INTEGER NOT NULL REFERENCES characters(id), key TEXT NOT NULL, value INTEGER NOT NULL, expires INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(character_id,key))')
    db.execute('CREATE TABLE IF NOT EXISTS cosmetic_use_receipts (id INTEGER PRIMARY KEY AUTOINCREMENT,character_id INTEGER NOT NULL REFERENCES characters(id),item_id INTEGER NOT NULL,type INTEGER NOT NULL,rewards TEXT NOT NULL,created INTEGER NOT NULL)')
    from native946_mall_guardians import schema as guardian_schema
    guardian_schema(db)
    from native946_mall_carving import schema as carving_schema
    carving_schema(db)
    from native946_mall_event_boxes import schema as box_schema
    box_schema(db)
    from native946_refinement import schema as refinement_schema
    refinement_schema(db)
    from native946_fashion_refinement import schema as fashion_refinement_schema
    fashion_refinement_schema(db)
    from native946_gear_sockets import schema as socket_schema
    from native946_jing_gems import schema as gem_schema
    from native946_guardian_ornaments import schema as ornament_schema
    socket_schema(db)
    gem_schema(db)
    ornament_schema(db)

def features(db,cid):
    return {r['key']:dict(value=r['value'],expires=r['expires']) for r in db.execute('SELECT * FROM cosmetic_features WHERE character_id=? AND (expires=0 OR expires>?)',(cid,int(time())))}

def owned_mount_items(saved, item_catalog):
    # Ordinary and trial licenses with the same permanent unlock both represent ownership.
    owned = set()
    for kind, mount in MOUNT_ITEM_TYPES.items():
        grant = saved.get(f'mount:{mount}')
        if grant and (kind == grant['value'] or (not grant['expires'] and not item_catalog[str(kind)]['days'])):
            owned.add(kind)
    return owned

def consume(db,item,count=1):
    from native946_inventory_locks import require_unlocked
    require_unlocked(db,item['character_id'],item['id'])
    if count<1 or item['amount']<count:raise ValueError('物品数量不足。')
    if item['amount']==count:db.execute('DELETE FROM inventory WHERE id=?',(item['id'],))
    else:db.execute('UPDATE inventory SET amount=amount-? WHERE id=?',(count,item['id']))

class MallUses:
    def __init__(self,game):self.g=game;self.p=game.progression
    def grant(self,db,key,value,days=0):
        row=db.execute('SELECT expires FROM cosmetic_features WHERE character_id=? AND key=?',(self.p.cid,key)).fetchone()
        if row is not None and row[0]==0:raise ValueError('已经永久拥有此功能，无需重复使用。')
        expiry=min(2147483647,max(int(time()),row[0] if row else 0)+days*86400) if days else 0
        db.execute('INSERT INTO cosmetic_features VALUES(?,?,?,?) ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value,expires=excluded.expires',(self.p.cid,key,value,expiry))
    def use(self,db,item,cosmetics):
        kind=str(item['type']);rule=DATA['rules'].get(kind)
        if rule is None:
            pending=DATA.get('pending_rules',{}).get(kind,{})
            if pending.get('op') not in ('guardian_appearance','trial_number_voucher'):
                raise ValueError(pending.get('reason','该物品的专用玩法仍缺少服务端规则，尚未接通；物品未消耗。'))
            rule=pending
        from native946_mall_bags import pages as bag_pages
        if item['page'] not in bag_pages(db,self.p.cid):raise ValueError('请先将道具放入有效背包。')
        op=rule['op']
        if op=='fixed_mount_dye':
            from native946_mall_mounts import apply_fixed_dye
            return apply_fixed_dye(self.g, FIXED_DYES[item['type']]['mount'], item['type'],
                                   unlock=True, source_item=item)
        if op=='jing_essence_pack':
            from native946_jing_gems import open_pack
            return open_pack(self.g,db,item)
        if op=='guardian_ornament_panel':
            from native946_guardian_ornaments import open_material
            return open_material(self.g,db,item)
        if op=='shenbing_ring':
            from native946_gear_services import open_ring
            return open_ring(self.g,db,item)
        if op=='halo_refinement':
            from native946_inventory_locks import require_unlocked
            from native946_refinement import Refinement
            require_unlocked(db,self.p.cid,item['id'])
            return Refinement(self.g).open_halo()
        if op=='fashion_refinement':
            from native946_fashion_refinement import FashionRefinement
            from native946_inventory_locks import require_unlocked
            require_unlocked(db,self.p.cid,item['id'])
            service=FashionRefinement(self.g)
            return (service.open(2),service.targets())
        if op=='number_certificate':
            from native946_nice_ids import NiceIds
            return NiceIds(self.g).activate(db,item)
        if op=='number_voucher':
            if not self.g.characters.trial_server or item['type']!=45844:
                raise ValueError('请到长安（333,105）找曾知，确认购买或续费时使用靓号购买券。')
            trial_rule=DATA['pending_rules']['45844']
            consume(db,item)
            updated=db.execute('UPDATE cultivation_state SET tongbao=tongbao+? WHERE character_id=?',
                               (trial_rule['tongbao'], self.p.cid))
            if updated.rowcount!=1:
                raise ValueError('通宝账户未就绪，靓号购买券已保留。')
            return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.p.attribute_packets(),
                                                 text_notice('已获得10万通宝。')))
        if op=='fudou':
            consume(db,item)
            db.execute('INSERT INTO mall_account_wallet(owner,coupon) VALUES(?,1) '
                       'ON CONFLICT(owner) DO UPDATE SET coupon=coupon+1',(self.p.owner,))
            return self.g.defer_packets(lambda:(*self.p.item_packets(),*cosmetics.currency_packets(),
                text_notice('已存入1颗福豆。福豆可按1:1兑换支持福豆支付的低价外观。')))
        if op=='anniversary_box':
            from native946_mall_event_boxes import open_anniversary
            return open_anniversary(self.g,db,item,cosmetics)
        if op=='rare_guardian_box':
            from native946_mall_event_boxes import open_rare_box
            return open_rare_box(self.g,db,item,cosmetics)
        if op=='beast_guardian_box':
            from native946_mall_event_boxes import open_beast_box
            return open_beast_box(self.g,db,item,cosmetics)
        if op=='expired_anniversary_registration':
            raise ValueError('云端之光纪念套卡的实物登记已于2015年9月2日截止。')
        if op=='child_halo':
            from native946_child_halos import choose
            return choose(self.g, db, item['type'], item['id'])
        if op=='child_fashion':
            from native946_child_fashion import select_child
            return select_child(db,self.p.cid,item,rule)
        if op=='knight_experience_panel':
            from native946_role_city_war import receive
            return tuple(receive(self.g, db, 2891, [0, 1]))
        if op in ('shared_medical', 'auto_medical', 'team_speed'):
            from native946_premium_items import use_convenience_license
            return use_convenience_license(self.g,db,item,rule)
        if op in ('portrait','portrait_slot','portrait_material'):
            from native946_mall_portraits import Portraits
            return Portraits(self.g).use(db,item,rule)
        if op=='carving':
            from native946_mall_carving import Carving
            return Carving(self.g).open()
        if op=='checkout_voucher':
            return text_notice('此券在购买适用商品时使用，请在原生购买确认框选择抵用券。物品未消耗。')
        if op=='bag_expand':
            from native946_mall_bags import Bags
            return Bags(self.g).choose(item['id'])
        if op=='guardian_appearance':
            from native946_inventory_locks import require_unlocked
            from native946_mall_guardians import GuardianAppearance
            require_unlocked(db,self.p.cid,item['id'])
            return GuardianAppearance(self.g).panel(0)
        if op=='material':
            from native946_npc_services import NpcServices
            NpcServices(self.g).validate(5657,'cosmetic')
            return self.weapon_open(5657,rule['mode'])
        if op=='random_bag':
            # Reserve every possible outcome before drawing, preventing a
            # full bag from becoming a free reroll of the random reward.
            from native946_cosmetics import PAGES,DATA as CATALOG
            occupied={(v['page'],v['slot']) for v in db.execute('SELECT page,slot FROM inventory WHERE character_id=?',(self.p.cid,))}
            def need(rows,wardrobe):return sum(n for kind,n in rows if (str(kind) in CATALOG['wearables'])==wardrobe)
            for wardrobe,pages in [(False,(8,)),(True,PAGES)]:
                required=need(rule['guaranteed'],wardrobe)+rule['draws']*max(need(rows,wardrobe) for rows in rule['candidates'])
                free=sum((page,slot) not in occupied for page in pages for slot in range(1,25))
                if free<required:raise ValueError('请为福袋的全部可能奖励预留足够的背包和异宝匣空格。')
            from collections import Counter
            rewards=Counter()
            for kind,n in rule['guaranteed']:rewards[kind]+=n
            for _ in range(rule['draws']):
                for kind,n in secrets.choice(rule['candidates']):rewards[kind]+=n
            consume(db,item)
            for kind,n in rewards.items():cosmetics._add(db,kind,n)
            db.execute('INSERT INTO cosmetic_use_receipts(character_id,item_id,type,rewards,created) VALUES(?,?,?,?,?)',
                       (self.p.cid,item['id'],item['type'],json.dumps(dict(rewards)),int(time())))
            names='、'.join(CATALOG['items'][str(k)]['name']+'×'+str(n) for k,n in rewards.items())
            return self.g.defer_packets(lambda:(*self.p.item_packets(),text_notice('本机等概率福袋：'+names)))
        consume(db,item)
        if op=='bundle':
            for kind,n in rule['rewards']:cosmetics._add(db,kind,n)
        elif op=='mount':
            from native946_cosmetics import DATA as CATALOG
            self.grant(db,'mount:'+str(rule['value']),item['type'],CATALOG['items'][kind]['days'])
        elif op=='emote':
            key='emote:'+str(rule['value'])+(':'+str(rule['members'][0]) if rule.get('one_by_one') else '')
            self.grant(db,key,item['type'],rule['days'])
        elif op=='namecard':
            from native946_cosmetics import DATA as CATALOG
            self.grant(db,'namecard:'+str(rule['value']),item['type'],CATALOG['items'][kind]['days'])
            db.execute("INSERT INTO cosmetic_features VALUES(?,'namecard_current',?,0) ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value",(self.p.cid,rule['value']))
        elif op=='bag_temp':self.grant(db,'bag_temp',1,rule['days'])
        elif op=='timed_feature':self.grant(db,rule['key'],1,rule['days'])
        elif op=='city_quick_task':
            from native946_role_city_war import CITY_TASK_LICENSE
            self.grant(db, CITY_TASK_LICENSE, item['type'], rule['days'])
        elif op=='totem':
            from native946_cosmetics import DATA as CATALOG
            key='totem:'+str(rule['value'])
            if key in features(db,self.p.cid):raise ValueError('已经拥有此图腾的有效使用期，无需重复消耗。')
            self.grant(db,key,item['type'],CATALOG['items'][kind]['days'])
            db.execute("INSERT INTO cosmetic_features VALUES(?,'totem_current',?,0) ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value",(self.p.cid,rule['value']))
        elif op=='point':
            from native946_shops import change_liuguang
            change_liuguang(db,self.p.cid,rule['value'])
        else:raise ValueError('道具规则无效。')
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.sync(),*((self.namecard(),) if op=='namecard' else ()),*((self.f5_info(),) if op=='timed_feature' else ()),*(cosmetics.visual_packets() if op=='totem' else ()),cosmetics.mall(refresh=True),text_notice('道具使用成功。')))
    def f5_info(self):
        from math import ceil
        with self.p.transaction(write=False) as db:f=features(db,self.p.cid)
        now=int(time());days=max(0,ceil((f.get('auto_advanced',{}).get('expires',now)-now)/86400))
        seconds=max(0,f.get('auto_infinite',{}).get('expires',now)-now)
        from native946_guardian_magic import combat_skills
        pet=self.g.battle.get('pet') if self.g.battle else self.g.summons.active()
        return packet(2756,['number',('array','attack_skill_info'),('array','attack_skill_info'),'number'],[days,self.p.combat_skills(),combat_skills(pet),seconds])
    def namecard(self):
        from native946_progression import lua_data
        with self.p.transaction(write=False) as db:f=features(db,self.p.cid)
        unlocked={int(k.split(':')[1]):1 for k in f if k.startswith('namecard:')}
        dated={int(k.split(':')[1]):v['expires'] for k,v in f.items() if k.startswith('namecard:') and v['expires']}
        selected=f.get('namecard_current',{}).get('value',0)
        if selected not in unlocked:selected=0
        c=self.g.character;s=self.p.snapshot()
        data=lua_data(dict(Unlock=unlocked,OutDate=dated,UseNameCard=selected))
        return packet(4473,['number','string','string','string','number','number','number','number','string','string','number'],
                      [c['id'],c['name'],'',str(s['rebirth'])+'转'+str(s['level'])+'级',c['role_id'],0,c['id'],0,'',data,selected])
    def sync(self):
        from native946_shops import liuguang_balance,liuguang_packets
        with self.p.transaction(write=False) as db:
            f=features(db,self.p.cid)
            balance=liuguang_balance(db,self.p.cid)
        from native946_progression import lua_data
        groups={}
        for key,row in sorted(f.items()):
            if not key.startswith('emote:'):continue
            rule=DATA['rules'].get(str(row['value']))
            if not rule:continue
            group=rule['value'];current=groups.setdefault(group,dict(members=set(),expires=0,whole=False))
            if rule.get('one_by_one'):current['members'].update(rule['members'])
            else:
                current['whole']=True
                current['expires']=max(current['expires'],row['expires']) if row['expires'] else -1
        emotes=[dict(groupid=k,timeout=v['expires'] if v['expires'] else -1,emotelist='' if v['whole'] else lua_data(sorted(v['members']))) for k,v in groups.items()]
        from native946_mall_bags import Bags
        from native946_mall_portraits import Portraits
        return (packet(1327,[('array','bigemoteinfo')],[emotes]),*liuguang_packets(balance),*Bags(self.g).sync(),*Portraits(self.g).sync())
    def mounts(self,mode=2302):
        from native946_mall_mounts import feature_value, known_skills
        from native946_premium_items import SPEED_KEY
        with self.p.transaction(write=False) as db:f=features(db,self.p.cid)
        owned=sorted(int(k.split(':')[1]) for k in f if k.startswith('mount:'))
        ride=f.get('ride',{}).get('value',0)
        if ride not in owned:ride=0
        # Native 946 interprets a negative timeout as unowned and zero as permanent.
        records=[dict(index=k,timeout=f['mount:'+str(k)]['expires'],effective=1,
                      skill=feature_value(f, f'mount_skill:{k}'),owntime=0) for k in owned]
        if mode==3231:return packet(3230,[('array','number')],[known_skills(f)])
        if mode==3683:
            from native946_mall_mounts import PAY_MOUNTS, SUPPORTED_SKILLS
            by_mount = {row['index']:row for row in records}
            entries = [by_mount.get(k,dict(index=k,timeout=-1,effective=0,skill=0,owntime=0))
                       for k in sorted(PAY_MOUNTS)]
            return packet(3681,[('array','pay_mount_list'),('array','number')],
                          [entries,sorted(SUPPORTED_SKILLS)])
        if mode==2296:return packet(2280,[('array','number'),'number'],[owned,ride])
        remaining = max(0, f.get(SPEED_KEY, {}).get('expires', 0) - int(time()))
        days = (remaining + 86399) // 86400
        all_mounts = [dict(key=k,value=int(k in owned)) for k in range(1,len(DATA['mounts'])+1)]
        return packet(2265,['number',('array','number'),'number',('array','pay_mount_list'),('array','pair_number'),'number'],[1,owned,ride,records,all_mounts,days])
    def weapon_open(self,nid,mode):
        from native946_cosmetics import Cosmetics
        Cosmetics(self.g).refresh();a=self.g.character['appearance'];role=str(self.g.character['role_id'])
        session=dict(npc=nid,scene=self.g.character['map_id'],until=monotonic()+180,mode=mode,equip_id=0,old=a['weapon_idx']*10+a['weapon_color'],pending=None,target=None)
        self.g.cosmetic_weapon=session
        if mode=='weapon_color':return packet(3308,['number','number','string','number'],[0,session['old'],'{}',-1])
        from native946_progression import lua_data
        rows=DATA['weapon'][role];pool=[v['wiwc'] for v in rows];names={v['wiwc']:v['name'] for v in rows}
        return packet(3472,['number']*3+['string']*2+['number']*2,[0,session['old'],session['old'],lua_data(pool),lua_data(names),-1,0])
    def weapon_session(self,equip_id,mode):
        s=getattr(self.g,'cosmetic_weapon',None)
        if not s or s['equip_id']!=equip_id or s['mode']!=mode or s['scene']!=self.g.character['map_id'] or s['until']<monotonic():raise ValueError('武器外观会话已失效，请重新与弦舞交谈。')
        if s['npc'] is not None:
            from native946_npc_services import NpcServices
            NpcServices(self.g).validate(s['npc'],'cosmetic')
        return s
    def material(self,db,kind):
        row=db.execute('SELECT * FROM inventory WHERE character_id=? AND page=8 AND type IN (?,?) ORDER BY id LIMIT 1',(self.p.cid,kind,kind+10000)).fetchone()
        if not row:raise ValueError('背包中没有所需材料。')
        consume(db,row)
    def receive(self,mid,r):
        from native946_cosmetics import Cosmetics
        c=Cosmetics(self.g)
        if mid in MOUNT_REQUESTS:
            from native946_mall_mounts import receive
            return receive(self.g,mid,r)
        if mid in (2597,2598,2599):
            from native946_mall_portraits import Portraits
            return Portraits(self.g).receive(mid,r)
        if mid in (238,239,240,280):
            from native946_mall_carving import Carving
            return Carving(self.g).receive(mid,r)
        if mid in (4041,4042):
            from native946_mall_bags import Bags
            values=[r.integer() for _ in range(3)];r.finish()
            return Bags(self.g).open(*values) if mid==4041 else Bags(self.g).confirm(*values)
        if mid in (3969,3971):
            from native946_mall_guardians import GuardianAppearance
            return GuardianAppearance(self.g).receive(mid,r)
        if mid==4476:r.finish();return self.namecard()
        if mid==4475:
            selected=r.integer();r.finish()
            with self.p.transaction() as db:
                if selected and 'namecard:'+str(selected) not in features(db,self.p.cid):raise ValueError('尚未激活该名片或已经到期。')
                db.execute("INSERT INTO cosmetic_features VALUES(?,'namecard_current',?,0) ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value",(self.p.cid,selected))
            return self.g.defer_packets(self.namecard)
        if mid==112:
            channel=r.integer();message=r.string();links=r.string();r.integer();r.finish()
            if channel not in (2,4):raise ValueError('目前接入当前和队伍聊天频道。')
            if not 1<=len(message)<=512 or links:raise ValueError('聊天内容为空、过长或包含尚未接入的物品链接。')
            text=message.decode('gb18030');service=self.g.party_service;hero=self.g.character
            if channel==4:
                team=service.team(hero['id'])
                if not team:raise ValueError('请先加入队伍。')
                members={m['character_id'] for m in team['members']}
                with service.lock:targets=[game for cid,game in service.games.items() if cid in members]
            else:
                with service.lock:targets=[game for game in service.games.values() if game.character['map_id']==hero['map_id'] and max(abs(game.character[k]-hero[k]) for k in ('x','y'))<=96]
            c.refresh();frame=hero['appearance']['sayframe']
            from native946_mall_portraits import current as current_portrait
            from native946_nice_ids import identity
            with self.p.transaction(write=False) as db:
                head=current_portrait(db,self.p.cid) or hero['role_id']
                showid=identity(db,self.p.cid)['showid']
            result=packet(95,['number']*2+['string']*3+['number']*4+['byte']+['number']*2+['byte']*2+['number']*2+['string']*2+['number','string'],
                          [channel,hero['id'],hero['name'],text,'',int(time()),0,frame,showid,int(showid!=hero['id']),0,0,0,0,head,0,'','本机',0,'{}'])
            for game in targets:
                if game is not self.g:service.enqueue(game,result)
            return result
        if mid==1496:r.finish();return self.sync()
        if mid in (2296,2302,3683):r.finish();return self.mounts(mid)
        if mid in (2300,2314,2289,2312):
            index=r.integer() if mid in (2300,2314) else 0;r.finish()
            with self.p.transaction() as db:
                if index and 'mount:'+str(index) not in features(db,self.p.cid):raise ValueError('尚未拥有该坐骑或已经到期。')
                db.execute("INSERT INTO cosmetic_features VALUES(?,'ride',?,0) ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value",(self.p.cid,index))
            return self.g.defer_packets(lambda:(*c.visual_packets(recreate=True),packet(2267,['number'],[index])))
        if mid in (3309,3310):
            equip,value=r.integer(),r.integer();r.finish();s=self.weapon_session(equip,'weapon_color')
            if mid==3309:
                if not 1<=value<=8 or s['pending'] is not None:raise ValueError('武器染色结果无效或已经生成。')
                with self.p.transaction() as db:self.material(db,47103)
                s['pending']=value
                return self.g.defer_packets(self.p.item_packets)
            if value not in (0,1) or s['pending'] is None:raise ValueError('请先完成一次原生武器染色。')
            if value:
                with self.p.transaction() as db:c._state(db,weapon_color=s['pending'])
            self.g.cosmetic_weapon=None
            return self.g.defer_packets(c.visual_packets)
        if mid in (3474,3475,3476):
            equip=r.integer();value=r.integer() if mid!=3475 else None;r.finish();s=self.weapon_session(equip,'weapons')
            rows=DATA['weapon'][str(self.g.character['role_id'])];pool=[v['wiwc'] for v in rows]
            if mid==3476:
                if value not in pool or s['pending'] is not None:raise ValueError('祈愿造型无效。')
                # Existing local selector is deterministic. Expose its actual
                # 100% rate through the stock client rather than invent rates.
                s['target']=value
                return (packet(3471,['number']*3,[equip,value,100]),text_notice('本机武器外观服务：幻化神铁×1，获得所选造型。'))
            if mid==3475:
                if s['target'] not in pool or s['pending'] is not None:raise ValueError('请先选择并祈愿一种造型。')
                with self.p.transaction() as db:self.material(db,47398)
                s['pending']=s['target'];names={v['wiwc']:v['name'] for v in rows}
                return self.g.defer_packets(lambda:(*self.p.item_packets(),packet(3473,['number','number','string','number','string','number'],[equip,s['old'],names.get(s['old'],'原造型'),s['pending'],names[s['pending']],1])))
            if value not in (0,1) or s['pending'] is None:raise ValueError('幻化确认无效。')
            if value:
                with self.p.transaction() as db:c._state(db,weapon_idx=s['pending']//10,weapon_color=s['pending']%10)
            self.g.cosmetic_weapon=None
            return self.g.defer_packets(c.visual_packets)
        raise ValueError('道具功能请求无效。')
