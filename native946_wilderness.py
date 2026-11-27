"""Native species/zones with an explicit local encounter and reward policy."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from math import ceil
from pathlib import Path
from random import Random
from secrets import token_hex
from time import monotonic, time
import json

from native946_codec import experience_notice_lines, packet, screen_notice, text_notice
from native946_storage import transaction

DATA=json.loads(Path(__file__).with_name('native946_wilderness_data.json').read_text('utf8'))
POLICY=DATA['policy'];ENEMY_BASE=901000;ENEMY_SLOTS=10
FORMATION_WIRE_VERSION=7
ARENA_PRACTICE_NPC = (2001, 7631)
# Native c_sys_notify closes after 120 ticks at 20 Hz. Allow for the socket poll.
TEAM_EXPERIENCE_POPUP_DELAY = 6.5

def numbers(mid,*values):return packet(mid,['number']*len(values),values)
def notice(text):return text_notice(text)
def carry_rank(hero):return hero.get('rebirth',0)*1000+hero['level']

def pending_team_experience_popup(game, now=None):
    pending = getattr(game, '_team_experience_popup', None)
    if pending is None:
        return ()
    deadline, reply = pending
    current = monotonic() if now is None else now
    if deadline is None:
        game._team_experience_popup = (current + TEAM_EXPERIENCE_POPUP_DELAY, reply)
    elif current >= deadline:
        del game._team_experience_popup
        return (reply,)
    return ()


def shared_input_wakeup():
    from native946_battle_actions import SHARED_INPUT_WAKEUP
    return SHARED_INPUT_WAKEUP

def encounter_zones(data):
    yield from data['zones']
    for local in data.get('local_zones', ()):
        zone = dict(local)
        floors = zone.pop('floor_variants', ())
        yield zone
        for floor in floors:
            yield dict(zone, scene=floor['scene'], scene_name=floor['scene_name'], bounds=floor['bounds'],
                       level_rewards=dict(zone['level_rewards'], school_practice=floor['school_practice']))


ZONES = tuple(encounter_zones(DATA))


def zone_at(scene,x,y):
    return next((z for z in ZONES if z['scene']==scene and z['bounds'][0]<=x*20<=z['bounds'][2] and z['bounds'][1]<=y*20<=z['bounds'][3]),None)

def level_experience(level, sample, team_bonus):
    base = level * sample['experience'] // sample['level']
    return base * (100 + team_bonus) // 100

def enter_shared_battle(games, build):
    """Prepare every peer's entry before publishing any battle packets."""
    missing = object()
    fields = ('battle', '_wild_battle', 'dialog_open', 'dialog_actions', 'dialog_npc')
    saved = [(g, g.party_service.battles.get(g.character['id']),
              {name: getattr(g, name, missing) for name in fields},
              g.character.get('appearance', missing)) for g in games]
    try:
        battle = build()
        replies = [(g, tuple(battle.entry(g))) for g in games]
    except BaseException:
        for g, previous, attributes, appearance in saved:
            cid = g.character['id']
            if previous is None:g.party_service.battles.pop(cid, None)
            else:g.party_service.battles[cid] = previous
            for name, value in attributes.items():
                if value is not missing:setattr(g, name, value)
                elif hasattr(g, name):delattr(g, name)
            if appearance is missing:g.character.pop('appearance', None)
            else:g.character['appearance'] = appearance
        raise
    for g, messages in replies:g.party_service.enqueue(g, *messages)
    return ()

def moved(game,before,after):
    if game.battle or before[0]!=after[0]:return ()
    from native946_npc_quests import Quests
    from native946_school import PATROL_SCENES
    if after[0] in PATROL_SCENES and before[1:]!=after[1:] and Quests(game).patrol_moved(*after):
        return ()
    zone=zone_at(*after)
    if zone is None:game._encounter_steps=0;return ()
    from native946_mall_mounts import repel_active
    if repel_active(game):
        game._encounter_steps=0
        return ()
    eligible=game.party_service.participants(game)
    if not eligible:return ()
    if not game.progression.snapshot()['hp']:return ()
    steps=max(abs(after[1]-before[1]),abs(after[2]-before[2]))
    if not steps:return ()
    game._encounter_steps=getattr(game,'_encounter_steps',0)+steps
    if game._encounter_steps<POLICY['encounter_steps'] or monotonic()<getattr(game,'_encounter_ready_at',0):return ()
    enter_shared_battle(eligible, lambda: WildBattle(game,eligible,zone))
    game._encounter_steps=0
    return ()

class WildBattle:
    charge_guardian_participation_on_retreat = True

    def __init__(self,leader,games,zone):
        from native946_role_battle import INPUT_SECONDS
        from native946_world import world
        self.service=leader.party_service;self.database=leader.characters.database
        self.id=token_hex(16);self.rng=Random(self.id);self.zone=deepcopy(zone)
        if 'battle_map' not in self.zone:
            self.zone['battle_map'] = world().battle_map(leader.character['map_id'])
        self.games={g.character['id']:g for g in games};self.leader=leader.character['id']
        self.state=dict(phase='input',round=1,orders={},input_ended={cid:set() for cid in self.games},acks=set(),fighters={},participants={},used={},deadline=monotonic()+INPUT_SECONDS,victory=False,settled=False)
        for position,g in enumerate(games,1):
            cid=g.character['id'];s=g.progression.snapshot()
            if s['hp']<=0:raise ValueError('队伍中有角色气血不足，无法进入野外战斗。')
            hero=dict(s,id=cid,name=g.character['name'],type=g.character['role_id'],owner=cid,kind='hero',camp=4,slot=position)
            self.state['fighters'][cid]=hero;self.state['participants'][cid]={};self.state['used'][cid]=set()
            pet=g.summons.active()
            if pet and pet['hp']>0 and pet['honesty']>=50:self.add_pet(g,pet,position+5)
        if formations := zone.get('formations'):
            roster = self.rng.choice(formations)
            if len(games) == POLICY['max_team_size']:
                roster = (*roster, *zone.get('full_team_extra_enemies', ()))
            templates = ((zone['enemies'][index], self.rng.randint(*zone['levels'])) for index in roster)
        elif zone.get('fixed_roster'):
            templates = ((enemy, self.rng.randint(*zone['levels'])) for enemy in zone['enemies'])
        else:
            count=min(ENEMY_SLOTS,max(1,len(games)+self.rng.randrange(2)))
            templates = ((self.rng.choice(zone['enemies']), self.rng.randint(*zone['levels'])) for _ in range(count))
        for i, (template, level) in enumerate(templates):
            pet_type=template['guardian_type']
            # Server NPC combat coefficients are not in the client tables.
            # Use the published local policy; captured guardians independently
            # retain their real native species/growth calculations.
            scale=POLICY['npc_stats']
            stats=dict(hpmax=scale['hp_base']+level*scale['hp_per_level'],
                       mpmax=scale['mp_base']+level*scale['mp_per_level'],
                       attack=scale['attack_base']+level*scale['attack_per_level'],
                       initiative=scale['speed_base']+level*scale['speed_per_level'],defense=level//5)
            stats.update(template.get('combat_stats',{}))
            fid=ENEMY_BASE+i
            self.state['fighters'][fid]=dict(stats,id=fid,type=template['type'],name=template['name'],guardian_type=pet_type,
                                             hp=stats['hpmax'],mp=stats['mpmax'],level=level,
                                             kind='enemy',camp=3,slot=i+1,removed=False,captured=False,
                                             exit_on_defeat=type(self) is WildBattle or zone.get('immediate_defeat',False))
        # No actor is published until the full roster has been validated.
        from native946_lineups import configure as configure_lineup
        configure_lineup(self)
        from native946_zhuziguo_bell import configure
        configure(self)
        from native946_race_fabao_battle import initialize
        initialize(self.state,self.state['fighters'],self.games)
        for cid,g in self.games.items():
            self.service.battles[cid]=self;g._wild_battle=self
            g.dialog_open=False;g.dialog_actions=[];g.dialog_npc=None
        self.project()

    def add_pet(self,game,pet,slot):
        cid=game.character['id'];fid=pet['id']
        actor=dict(pet,id=fid,owner=cid,kind='pet',camp=4,slot=slot,removed=False)
        from native946_lineups import summon_enter
        summon_enter(self,actor)
        self.state['fighters'][fid]=actor;self.state['participants'][cid][fid]=actor;self.state['used'][cid].add(fid)

    def current_pet(self,cid):
        return next((f for f in self.state['fighters'].values() if f['kind']=='pet' and f['owner']==cid and not f.get('removed')),None)

    def enemies(self):return [f for f in self.state['fighters'].values() if f['kind']=='enemy' and f['hp']>0 and not f.get('removed')]
    def allies(self):return [f for f in self.state['fighters'].values() if f['camp']==4 and f['hp']>0 and not f.get('removed')]
    def required(self):
        from native946_combat_control import can_select_order
        return {f['id'] for f in self.allies() if f['kind'] in ('hero','pet') and can_select_order(f,self.state['round'])}

    def action_count(self,fid):
        from native946_zhuziguo_bell import count
        return count(self.state,fid)

    def actor_orders(self,fid):
        from native946_zhuziguo_bell import orders
        return orders(self.state,fid)

    def needed_inputs(self):
        needed=self.required()
        for owner in self.games:
            if any(o['kind'] in ('summon','dismiss') for o in self.actor_orders(owner)):
                pet=self.current_pet(owner)
                if pet:needed.discard(pet['id'])
        return needed

    def input_complete(self,fid):return len(self.actor_orders(fid))>=self.action_count(fid)

    def missing_inputs(self):
        return {fid for fid in self.needed_inputs() if not self.input_complete(fid)}

    def pending_actor(self,cid,*,missing=None):
        if missing is None:missing=self.missing_inputs()
        pet=self.current_pet(cid)
        return next((fid for fid in (cid,pet['id'] if pet else None) if fid in missing),None)

    def remaining_input_seconds(self):
        return max(1,ceil(self.state['deadline']-monotonic()))

    def project(self):
        for cid,g in self.games.items():
            h=self.state['fighters'][cid];pet=self.current_pet(cid)
            enemy=self.enemies();target=enemy[0] if enemy else None
            g.battle=dict(kind='wild',id=self.id,phase=self.state['phase'],round=self.state['round'],
                          hp=h['hp'],hpmax=h['hpmax'],mp=h['mp'],mpmax=h['mpmax'],pet=deepcopy(pet),
                          enemy_hp=sum(e['hp'] for e in enemy),enemy_hpmax=sum(f['hpmax'] for f in self.state['fighters'].values() if f['kind']=='enemy'),
                          target_id=target['id'] if target else 0,orders=deepcopy(self.state['orders']),victory=self.state['victory'],
                          enemies=[deepcopy(f) for f in self.state['fighters'].values() if f['kind']=='enemy'],
                          participants=deepcopy(self.state['participants'][cid]),team_members=list(self.games),input_deadline=self.state['deadline'])
            if self.state.get('zhuziguo_bell'):
                bell=self.state['zhuziguo_bell']
                g.battle.update(zhuziguo_bell={key:bell[key] for key in ('energy','armed','active_round')},
                              extra_orders=deepcopy(self.state['extra_orders']))

    def project_orders(self):
        for game in self.games.values():
            game.battle['orders']=deepcopy(self.state['orders'])
            if self.state.get('zhuziguo_bell'):
                game.battle['extra_orders']=deepcopy(self.state['extra_orders'])

    def broadcast(self,*replies):
        for g in self.games.values():self.service.enqueue(g,*replies)

    def entry(self,game):
        from native946_cosmetics import Cosmetics
        Cosmetics(game).refresh();cid=game.character['id'];pet=self.current_pet(cid)
        from native946_lineups import battle_info,battle_packets
        formation=battle_info(self,game)
        result=[packet(1183,['string'],['dlg_npc_chat']),packet(1183,['string'],['dlg_summon_status']),
                packet(1183,['string'],['dlg_skill_panel_new']),
                packet(2171,['number','number','number','byte','number','string','number','number','number',('array','weapon2_skill')],
                       [self.zone['battle_map'],formation['enemy'],formation['own'],0,
                        formation['level'],formation['leader'],0,formation['enhance'],formation['officer'],[]])]
        result.extend(battle_packets(self))
        for f in self.state['fighters'].values():
            if f.get('removed'):continue
            if f['kind']=='shadow':continue
            result.append(game.fighter(f['id'],f['type'],f['name'],f['camp'],1 if f['kind']=='hero' else 3 if f['kind']=='pet' else 2,f['slot'],f['level']))
            if f['kind']=='hero':
                from native946_role_battle import fighter_entry
                result.append(numbers(2148,f['id'],f['hp'],f.get('hp_limit',f['hpmax']),f['hpmax'],f['mp'],f['mpmax'],f['mpmax'],f.get('role_anger',6),10,0))
                result.extend(fighter_entry(f,self.id))
            else:result.append(game.battle_vitals_packet(f))
        for f in self.state['fighters'].values():
            if f.get('kind')=='shadow' and not f.get('removed') and f['hp']>0:
                result.append(packet(2129,['number','number','string','string','number','number','number'],[f['id'],f['shadow_owner'],f['name'],'魔影',f['type'],0,0]))
                result.append(game.battle_vitals_packet(f))
        from native946_role_battle import control_status_packets
        for f in self.state['fighters'].values():
            if not f.get('removed'):result.extend(control_status_packets(f,self.state['round']))
        result.extend(Cosmetics(game).enter_effects([f['id'] for f in self.state['fighters'].values() if f['kind']=='hero']))
        result.extend((numbers(4206,pet['id'] if pet else 0),game.progression.combat_skills_packet()))
        if pet:
            from native946_guardian_magic import skills_packet
            result.append(skills_packet(pet))
        from native946_zhuziguo_bell import entry as bell_entry
        result.extend(bell_entry(self.state))
        from native946_race_fabao_battle import view as artifact_view
        result.extend(artifact_view(self.state,cid))
        result.append(numbers(2135,self.state['round']))
        if self.state['phase']=='input':
            actor=self.pending_actor(cid)
            if actor:
                result.append(numbers(2151,actor,self.remaining_input_seconds(),0))
            # c_begin_bout also queues the round effect, which needs its own wakeup.
            result.extend(shared_input_wakeup())
            from native946_battle_actions import input_command_packet
            for fid,fighter in self.state['fighters'].items():
                if fighter.get('owner')!=cid or fighter.get('removed'):
                    continue
                for slot,order in enumerate(self.actor_orders(fid)):
                    result.append(input_command_packet(fid,order,extra=bool(slot)))
        entry_text = self.entry_notice()
        if entry_text:
            result.append(notice(entry_text))
        return tuple(result)

    def entry_notice(self):
        return self.zone.get('entry_notice')

    @contextmanager
    def atomic(self):
        saved=deepcopy(self.state);random_state=self.rng.getstate()
        characters={cid:deepcopy(g.character) for cid,g in self.games.items()}
        known={cid:(g.progression.known_items, g.progression._known_item_positions,
                    getattr(g.progression,'_known_item_quantities',None)) for cid,g in self.games.items()}
        try:
            with transaction(self.database,write=True,batchable=True) as db:
                for g in self.games.values():g.characters.pin_position(db,g.character);g.progression._cached_snapshot=None
                yield db
        except BaseException:
            self.state=saved;self.rng.setstate(random_state)
            for cid,g in self.games.items():
                p=g.progression
                p.known_items,p._known_item_positions,p._known_item_quantities=known[cid]
                p._cached_snapshot=None
                g.character.clear();g.character.update(characters[cid])
            self.project();raise
        self.project()

    def _actor(self,cid,pet=False):
        actor=self.current_pet(cid) if pet else self.state['fighters'][cid]
        if actor is None or actor['hp']<=0 or actor.get('removed'):raise ValueError('该战斗单位当前不能行动。')
        return actor

    def target(self,fid):
        enemies=self.enemies()
        if fid==0:return enemies[0] if enemies else None
        enemy=next((f for f in enemies if f['id']==fid),None)
        if enemy is None:raise ValueError('请选择当前仍在场的敌方目标。')
        return enemy

    def recover_spell_target(self,actor,target):
        # F5 and manual casts use the same packet. An old encounter slot is
        # identifiable by the range the local server reserves for enemies.
        if len(self.games)<2 or not ENEMY_BASE<=target<ENEMY_BASE+ENEMY_SLOTS:
            return target
        selected=self.state['fighters'].get(target)
        if selected and (selected['kind']!='enemy' or (selected['hp']>0 and not selected.get('removed'))):
            return target
        from native946_innate_effects import has as has_innate
        reveal=actor.get('role_reveal') or (actor['kind']=='pet' and has_innate(actor,4733))
        return next((enemy['id'] for enemy in self.enemies() if not enemy.get('invisible') or reveal),target)

    def receive(self,game,mid,reader):
        cid=game.character['id'];state=self.state
        from native946_race_fabao_battle import COMMAND, submit, view
        if mid==COMMAND:
            submit(state,state['fighters'],cid,reader)
            for other,peer in self.games.items():
                updates=view(state,other) if other!=cid else ()
                if updates:self.service.enqueue(peer,*updates)
            return view(state,cid)
        count=0 if mid in (2175,2184,2317,2757,3819,4891) else 3 if mid in (2183,2185) else 2 if mid in (2177,2181) else 1
        values=[reader.integer() for _ in range(count)];reader.finish()
        if mid==4891:
            from native946_zhuziguo_bell import toggle
            return toggle(self,cid)
        if mid==2175:
            from native946_role_battle import weather_reply
            return weather_reply(state)
        if mid==3819:
            if state['phase']!='input' or monotonic()>=state['deadline']:return ()
            owned={f['id'] for f in state['fighters'].values() if f.get('owner')==cid}
            pet=self.current_pet(cid)
            actor=next((fid for fid in [cid,pet['id'] if pet else None] if fid in self.required()),None)
            if actor is None:return ()
            for fid in owned:
                state['orders'].pop(fid,None);state.get('extra_orders',{}).pop(fid,None)
            state['input_ended'][cid].clear()
            game._background_round=None
            self.project_orders()
            return (numbers(3818,0),numbers(5349),
                    numbers(2151,actor,self.remaining_input_seconds(),0),*shared_input_wakeup())
        if mid==2757:
            if getattr(game,'_background_round',None)==(self.id,state['round']):return numbers(5349)
            from native946_mall_uses import MallUses
            return MallUses(game).f5_info()
        if mid==2317:
            pets=game.summons.snapshot();pet=self.current_pet(cid)
            records=[dict(summon_id=s['id'],summon_type=s['type'],summon_name=s['name'],summon_honest=s['honesty']) for s in pets]
            flags=[int(s['hp']>0 and s['honesty']>=50 and s['id'] not in state['used'][cid]) for s in pets]
            return packet(2274,[('array','zq_ctrl_summon'),('array','number'),'number',('array','byte')],[records,[pet['id']] if pet else [],0,flags])
        if mid==2184:
            late = state.get('late_round_ends', {})
            if late.get(cid, 0):
                late[cid] -= 1
                return ()
            if state['phase']!='drawing':return ()
            state['acks'].add(cid);self.advance_if_ready();return ()
        if state['phase']!='input':return ()
        if mid in (2177,2181) and values[1]==0 and getattr(game,'_background_round',None)==(self.id,state['round']):
            return numbers(5349)
        if mid in (2178,2182) and values[0]==2:
            if cid!=self.leader:raise ValueError('组队战斗由队长带队撤退。')
            if mid==2182:self._actor(cid,pet=True)
            with self.atomic() as db:
                self.persist()
                if self.charge_guardian_participation_on_retreat:
                    self.settle_guardians_on_exit()
                self.recover_after_battle(db)
            self.finish(False);return ()
        if monotonic()>=state['deadline']:return ()
        actor=self._actor(cid,mid in (2181,2182,2183))
        from native946_combat_control import blocked,can_select_order
        if not can_select_order(actor,state['round']):
            raise ValueError('该战斗单位当前不能行动。')
        if blocked(actor,state['round']) and not (mid in (2178,2182) and values[0]==1):
            if mid in (2177,2181) and values==[3,0]:return ()
            raise ValueError('该单位正处于人法控制状态，当前只能防御。')
        submitted=self.actor_orders(actor['id'])
        replace=(mid in (2178,2182) and values[0]==1 and len(submitted)==1 and
                 submitted[0].get('kind')=='attack' and not submitted[0].get('skill_id') and
                 not submitted[0].get('guardian_skill') and submitted[0].get('skill',10)==10)
        if self.input_complete(actor['id']) and not replace:return ()
        order=dict(kind='attack',target=0,skill=10,mana=0)
        fallback_notice=None
        if mid==2176:order['target']=values[0]
        elif mid in (2177,2181):
            operation,target=values
            if operation==3:order['target']=target
            elif operation==4:
                from native946_role_battle import protect_order
                order=protect_order(actor,target,state['fighters'])
            elif mid==2177 and operation==5:order=dict(kind='capture',target=target)
            elif mid==2177 and operation==6:
                if actor.get('role_no_summon_until',0)>=state['round']:raise ValueError('当前处于禁止召唤状态。')
                pet=game.summons.get(target)
                if target in state['used'][cid] or pet['hp']<=0 or pet['honesty']<50:raise ValueError('该守护本场不能召唤。')
                if any(o['kind']=='summon' for o in self.actor_orders(cid)):raise ValueError('本回合已经提交召唤指令。')
                order=dict(kind='summon',summon_id=target,display_summon_type=pet['type'])
            else:raise ValueError('该战斗操作无效。')
        elif mid in (2178,2182):
            if values[0]==1:order=dict(kind='defend')
            elif mid==2178 and values[0]==7:
                if not self.current_pet(cid) and not any(o['kind']=='summon' for o in self.actor_orders(cid)):raise ValueError('当前没有可以召还的守护。')
                order=dict(kind='dismiss')
            else:raise ValueError('该战斗操作无效。')
        else:
            operation,item,target=values
            if operation==8:
                from native946_role_battle import validate_item
                target=target or actor['id']
                with game.progression.transaction(write=False) as db:row=validate_item(db,game,actor,item,target,state['fighters'],state['round'])
                from native946_zhuziguo_bell import all_orders
                reserved=sum(o.get('kind')=='medicine' and o.get('item_id')==item and o.get('owner')==cid for o in all_orders(state))
                if reserved>=row['amount']:raise ValueError('该药品数量不足。')
                order=dict(kind='medicine',item_id=item,target=target,owner=cid,display_item_type=row['type'])
            elif operation==9:
                from native946_guardian_magic import InsufficientGuardianMana,prepared
                from native946_role_battle import InsufficientHeroMana,validate_order,profile,FRIENDLY
                friendly=actor['kind']=='hero' and (profile(item) or {}).get('family') in FRIENDLY
                if not friendly:target=self.recover_spell_target(actor,target)
                try:
                    if actor['kind']=='pet':
                        v=prepared(actor,item)
                        order=dict(kind='attack',target=target,skill=item,guardian_skill=item,mana=v['mana'])
                    else:
                        if not target:target=actor['id'] if friendly else (self.enemies()[0]['id'] if self.enemies() else 0)
                        order=validate_order(actor,item,target,state['fighters'])
                except (InsufficientGuardianMana,InsufficientHeroMana):
                    selected=state['fighters'].get(target)
                    order['target']=0 if selected and selected['camp']==actor['camp'] else target
                    label='守护' if actor['kind']=='pet' else '人物'
                    fallback_notice=notice(label+'法力不足，本次改用普通攻击。')
            else:raise ValueError('当前守护支持普通攻击、防御、用药和已接入的内丹被动。')
        if order['kind'] in ('attack','capture') and not order.get('skill_id'):
            enemy=self.target(order['target']);order['target']=enemy['id']
            if order['kind']=='capture':
                kind=enemy['guardian_type'];native=DATA['species'].get(str(kind))
                if native is None:raise ValueError('该敌方单位不是可捕捉守护。')
                hero = game.progression.snapshot()
                if carry_rank(hero)<native['take_grade_num']:raise ValueError('尚未达到该守护的携带等级。')
                if self.keeps_captured_guardian():
                    from native946_summons import guardian_level_cap
                    if enemy['level'] > guardian_level_cap(hero['level']):
                        raise ValueError('该守护等级超过当前可捕捉的上限。')
                    if len(game.summons.snapshot())>=game.summons.carry_limit():raise ValueError('守护携带栏已满。')
        prior=deepcopy(state)
        if getattr(game, '_background_round', None) != (self.id, state['round']):
            state.get('late_round_ends', {}).pop(cid, None)
        slot=len(self.actor_orders(actor['id']))
        if replace:state['orders'][actor['id']]=order
        elif slot:state['extra_orders'].setdefault(actor['id'],[]).append(order)
        else:state['orders'][actor['id']]=order
        if order['kind'] in ('summon','dismiss'):
            old=self.current_pet(cid)
            if old:state['orders'].pop(old['id'],None)
        try:
            from native946_battle_actions import input_command_packet
            preview=input_command_packet(actor['id'],order,extra=bool(slot))
            viewers=self.games if state.get('zhuziguo_bell') else (cid,)
            missing=self.missing_inputs()
            if not missing:
                self.resolve(input_previews={viewer: preview for viewer in viewers})
            else:
                for viewer in viewers:
                    self.service.enqueue(self.games[viewer],preview)
                if self.input_complete(actor['id']) and actor['id'] not in state['input_ended'][cid]:
                    self.service.enqueue(game,numbers(2152,actor['id']))
                    state['input_ended'][cid].add(actor['id'])
                waiting=self.pending_actor(cid,missing=missing)
                if waiting:self.service.enqueue(game,numbers(2151,waiting,self.remaining_input_seconds(),0),
                                                 *shared_input_wakeup())
                self.project_orders()
        except BaseException:self.state=prior;self.project();raise
        return (fallback_notice,) if fallback_notice else ()

    def action(self,actor,target,skill,damage,*,defend=False,mana=0,miss=False,effect=0):
        owner=actor.get('owner',self.leader);g=self.games[owner]
        return g.native_action(actor['id'],target['id'],skill,damage,target['hp'],defend=defend,mp=actor['mp'] if mana else None,
                               miss=miss,effect=effect)

    def medicine(self,actor,order,db):
        from native946_progression import MEDICINE_EFFECTS
        cid=actor['owner'];g=self.games[cid];target=self.state['fighters'].get(order['target'])
        if not target or target['hp']<=0 or target.get('removed'):return [notice('用药目标已离场，本回合未扣除药品。')]
        row=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=? AND page=8',(order['item_id'],cid)).fetchone()
        if row is None or row['type'] not in MEDICINE_EFFECTS:return [notice('药品已不可用，本回合未扣药。')]
        attr,maximum,amount=MEDICINE_EFFECTS[row['type']]
        amount=min(amount,target[maximum]-target[attr])
        if amount<=0:return [notice('目标状态已满，本回合未扣药。')]
        target[attr]+=amount
        if row['amount']==1:db.execute('DELETE FROM inventory WHERE id=?',(row['id'],))
        else:db.execute('UPDATE inventory SET amount=amount-1 WHERE id=?',(row['id'],))
        return [g.battle_vitals_packet(target),notice(target['name']+'恢复了'+str(amount)+('点气血。' if attr=='hp' else '点法力。'))]

    def pet_order(self,hero,order,local):
        cid=hero['id'];g=self.games[cid];pet=self.current_pet(cid);result=[]
        if order['kind']=='summon':
            try:incoming=g.summons.get(order['summon_id'])
            except ValueError:return [notice('该守护已不可召唤，本次行动未召唤。')]
            if (hero.get('role_summon_round')==self.state['round'] or incoming['id'] in self.state['used'][cid]
                    or incoming['hp']<=0 or incoming['honesty']<50):
                return [notice('该守护本次行动不能召唤。')]
        elif not pet:return [notice('当前没有可以召还的守护。')]
        if pet:pet['removed']=True;result.append(numbers(2137,pet['id']))
        if order['kind']=='summon':
            self.add_pet(g,incoming,hero['slot']+5);hero['role_summon_round']=self.state['round']
            actor=self.current_pet(cid)
            result.append(g.fighter(actor['id'],actor['type'],actor['name'],4,3,actor['slot'],actor['level'],mid=2128))
            result.append(g.battle_vitals_packet(actor))
            from native946_guardian_magic import skills_packet
            result.append(skills_packet(actor))
            local[cid].append(numbers(4206,actor['id']))
        else:local[cid].append(numbers(4206,0))
        return result

    def passives(self,pet,target):
        from native946_neidan_legacy import effect
        result=[]
        for row in pet.get('neidan',[]):
            if row['kind'] not in (211,212,213,214,231,235):continue
            value=effect(row,pet)
            if not value['enabled'] or self.rng.random()*100>=value['chance']:continue
            subject=next((e for e in self.enemies() if e['id']!=target['id']),None) if row['kind']==235 else target
            if subject is None or subject['hp']<=0:continue
            damage=(int(pet['attack']*value['ratio']/100) if row['kind']==235 else
                    value.get('damage',int(subject.get('mpmax',0)*value.get('ratio',0)/100)))
            hit=min(max(0,int(damage)),subject['hp'])
            if hit:subject['hp']-=hit;result.extend(self.action(pet,subject,row['kind'],hit))
        return result

    def counter(self,pet,target,used=None):
        from native946_combat_control import blocked
        if blocked(pet,self.state['round']):return []
        from native946_neidan_legacy import effect
        from native946_book_combat import counter_ready,roll
        replies=[]
        if used is not None and target['hp']>0 and counter_ready(pet,used.get(pet['id'],0),self.rng,attacker=target):
            used[pet['id']]=used.get(pet['id'],0)+1
            outcome=roll(pet,target,self.rng);hit=min(outcome['damage'],target['hp']);target['hp']-=hit
            replies.extend(self.action(pet,target,10,hit,miss=outcome['miss'],effect=outcome['effect']))
        for row in pet.get('neidan',[]):
            if row['kind']!=233:continue
            value=effect(row,pet)
            if not value['enabled'] or self.rng.random()*100>=value['chance']:continue
            for _ in range(value['counter_count']):
                hit=min(max(1,pet['attack']-target['defense']),target['hp'])
                if not hit:break
                target['hp']-=hit;replies.extend(self.action(pet,target,10,hit))
        return replies

    def persist(self):
        for cid,g in self.games.items():
            hero=self.state['fighters'][cid];g.progression.set_vitals(hero['hp'],hero['mp'])
            for pet in self.state['participants'][cid].values():g.summons.set_vitals(pet['id'],pet['hp'],pet['mp'])

    def keeps_captured_guardian(self):
        return True

    def school_practice_reward(self, db, cid, amount):
        from native946_role_battle import owned_profile
        from native946_school import SCHOOL_SKILLS, practice_limit

        actor = self.state['fighters'][cid]
        cap = practice_limit(actor)
        ranks = {row['skill_id']: row['rank'] for row in db.execute(
            'SELECT skill_id,rank FROM learned_skills WHERE character_id=? AND rank<? ORDER BY skill_id',
            (cid, cap))}
        learner = dict(actor, skills=ranks)
        choices = []
        for skill, rank in ranks.items():
            if skill not in SCHOOL_SKILLS:
                continue
            try:
                meta = owned_profile(learner, skill)
            except ValueError:
                continue
            choices.append(dict(skill=skill, name=meta['name'], amount=min(amount, cap - rank)))
        if choices:
            return Random(f'{self.id}:{cid}:school_practice').choice(choices)
        return None

    def settle(self,db,victory):
        if self.state['settled']:return
        from native946_double_time import experience_rates
        from native946_temp_inventory import award
        killed=[f for f in self.state['fighters'].values() if f['kind']=='enemy' and f['hp']==0 and not f['captured']]
        levels=sum(f['level'] for f in killed) if victory else 0
        rule = self.zone.get('level_rewards')
        team_bonus = (len(self.games) - 1) * rule.get('team_experience_percent_per_member', 0) if rule and victory else 0
        now = int(time())
        for cid,g in self.games.items():
            participants = self.state['participants'][cid]
            guardian_awards = {}
            if rule:
                xp = level_experience(self.state['fighters'][cid]['level'], rule['hero'], team_bonus) if victory else 0
                if victory:
                    guardian_awards = {str(pet['id']): level_experience(pet['level'], rule['guardian'], team_bonus)
                                       for pet in participants.values()}
                pet_xp = 0
                gold = 0
            else:
                xp=levels*POLICY['hero_exp_per_enemy_level'];gold=levels*POLICY['gold_per_enemy_level']
                pet_xp=levels*POLICY['guardian_exp_per_enemy_level']
            hero_rate, guardian_rate, neidan_rate = experience_rates(db, cid, now) if victory else (100, 1, 1)
            xp = xp * hero_rate // 100
            drops = {}
            for enemy in killed if victory and not rule else []:
                roll=int.from_bytes(sha256((self.id+':'+str(cid)+':'+str(enemy['id'])).encode()).digest()[:8],'little')%POLICY['drop_roll_denominator']
                for drop in POLICY['drops']:
                    if roll<drop['weight']:drops[drop['type']]=drops.get(drop['type'],0)+drop['amount'];break
                    roll-=drop['weight']
            reward=dict(victory=bool(victory),experience=xp,gold=gold,guardian_experience=pet_xp * guardian_rate if participants else 0,drops=drops,kills=[e['id'] for e in killed],policy=rule['policy'] if rule else POLICY['name'])
            if rule:
                reward.update(guardian_experience=sum(guardian_awards.values()) * guardian_rate,
                              guardian_experience_by_id={sid: amount * guardian_rate for sid, amount in guardian_awards.items()})
            if team_bonus:
                reward['team_experience_percent'] = team_bonus
            practice = None
            stunt_exp = None
            if rule and victory:
                practice = self.school_practice_reward(db, cid, rule['school_practice'])
                if practice:
                    reward['school_practice'] = practice
                stunt_exp = rule['guardian_stunt_experience']
            inserted=db.execute('INSERT OR IGNORE INTO native_encounter_rewards VALUES(?,?,?)',(self.id,cid,json.dumps(reward,ensure_ascii=False))).rowcount
            if inserted:
                g.characters.settle_battle(g.character['owner'],cid,'wild:'+self.id+':'+str(cid),victory,experience=xp,gold=gold,advance_quest=False)
                g.auto_level()
                if practice:
                    db.execute('UPDATE learned_skills SET rank=rank+? WHERE character_id=? AND skill_id=?',
                               (practice['amount'], cid, practice['skill']))
                for pet in participants.values():
                    base = guardian_awards.get(str(pet['id']), pet_xp)
                    g.summons.settle(pet['id'],self.id,pet['hp'],pet['mp'],victory,
                                     experience=base * guardian_rate, neidan_experience=base * neidan_rate,
                                     stunt_experience=base if stunt_exp is None else stunt_exp)
                if drops:
                    reward['loot_delivery'] = award(db, g.progression, self.id, drops, offline=self.service.online(cid) is None)
                    db.execute('UPDATE native_encounter_rewards SET payload=? WHERE battle_id=? AND character_id=?',
                               (json.dumps(reward,ensure_ascii=False),self.id,cid))
            else:
                reward = json.loads(db.execute(
                    'SELECT payload FROM native_encounter_rewards WHERE battle_id=? AND character_id=?',
                    (self.id, cid)).fetchone()[0])
            self.state.setdefault('rewards',{})[cid]=reward
        self.state['settled']=True

    def resolve(self,*,input_previews=None):
        from native946_role_battle import resolve_shared
        return resolve_shared(self,input_previews=input_previews)

    def advance_if_ready(self, *, drawing_timed_out=False):
        online={cid for cid in self.games if self.service.online(cid)}
        if not online:
            self.finish(self.state['victory']);return
        waiting = online - self.state['acks']
        if waiting and not drawing_timed_out:return
        if self.state['victory'] or self.state.get('defeat'):self.finish(self.state['victory']);return
        if waiting:
            # 2184 carries no round number. Consume these older confirmations
            # in order, even if they arrive during the next round's playback.
            late = self.state.setdefault('late_round_ends', {})
            for cid in waiting:
                late[cid] = late.get(cid, 0) + 1
        from native946_role_battle import INPUT_SECONDS,input_status_packets
        self.state.update(phase='input',round=self.state['round']+1,orders={},input_ended={cid:set() for cid in self.games},acks=set(),deadline=monotonic()+INPUT_SECONDS)
        status_packets=input_status_packets(self.state['fighters'],self.state['round'])
        from native946_zhuziguo_bell import next_round
        bell_packets=next_round(self.state)
        from native946_race_fabao_battle import next_round as artifact_next_round, view as artifact_view
        artifact_next_round(self.state)
        required=self.required()
        if not required:self.state['deadline']=monotonic()
        self.project();self.broadcast(numbers(2135,self.state['round']),*status_packets,*bell_packets)
        for cid,g in self.games.items():
            updates=artifact_view(self.state,cid)
            if updates:self.service.enqueue(g,*updates)
            pet=self.current_pet(cid);actor=next((f for f in [cid,pet['id'] if pet else None] if f in required),None)
            if actor:self.service.enqueue(g,numbers(2151,actor,self.remaining_input_seconds(),0),*shared_input_wakeup())

    def tick(self,now=None):
        phase = self.state['phase']
        now = monotonic() if now is None else now
        if phase == 'drawing':
            deadline = self.state['deadline']
            self.advance_if_ready(drawing_timed_out=deadline is not None and now >= deadline)
            return
        if phase != 'input':
            return
        if now<self.state['deadline']:return
        prior=deepcopy(self.state)
        from native946_combat_control import blocked
        for actor_id in self.required():
            if blocked(self.state['fighters'][actor_id],self.state['round']):continue
            self.state['orders'].setdefault(actor_id,dict(kind='attack',target=0,skill=10,mana=0))
            if self.action_count(actor_id)>1:
                extra=self.state['extra_orders'].setdefault(actor_id,[])
                if not extra:extra.append(deepcopy(self.state['orders'][actor_id]))
        # Assign timeout targets on the server, never trust client zero IDs.
        from native946_zhuziguo_bell import all_orders
        for order in all_orders(self.state):
            if order['kind']=='attack' and order['target']==0:order['target']=self.enemies()[0]['id']
        try:self.resolve()
        except BaseException:self.state=prior;self.project();raise

    def finish_messages(self,cid,victory):
        if not victory:
            return (notice('已离开野外战斗。'),)
        reward = self.state.get('rewards', {}).get(cid, {})
        participants = self.state['participants'].get(cid, {})
        by_guardian = reward.get('guardian_experience_by_id')
        if by_guardian is None:
            guardian_awards = [(pet['name'], reward.get('guardian_experience', 0)) for pet in participants.values()]
        else:
            guardian_awards = [(pet['name'], by_guardian.get(str(pet['id']), 0)) for pet in participants.values()]
        # Retail recordings show different experience text in 傲来国 and 乌斯藏.
        lines = experience_notice_lines(reward.get('experience', 0), guardian_awards,
                                        highlighted=self.games[cid].character['map_id'] == 2024)
        if practice := reward.get('school_practice'):
            lines.append(f'师门技能#G{practice["name"]}#n获得#G{practice["amount"]}#n熟练度。')
        replies = [screen_notice('#r'.join(lines))] if lines else []
        delivery = reward.get('loot_delivery', {})
        from native946_progression import DATA as progression_data
        for kind, amount in delivery.get('bag_items', {}).items():
            name = progression_data['items'][str(kind)]['name']
            replies.extend((screen_notice(f'你获得了#R{name}*{amount}#n'),
                            numbers(783, int(kind), amount)))
        # An item update on page 5500 already shows the stock 摘星袋 indicator.
        if delivery.get('waiting'):
            replies.append(notice('摘星袋已满，剩余奖励已保留。'))
        if not replies and not delivery.get('temporary'):
            replies.append(screen_notice('野外战斗胜利。'))
        return tuple(replies)

    def close_battle(self, cooldown):
        from native946_battle_actions import QUEUED_BATTLE_EXIT

        drawing = self.state['phase'] == 'drawing'
        self.state['phase']='finished'
        ready_at = monotonic() + cooldown
        for cid,g in self.games.items():
            self.service.battles.pop(cid,None)
            g._wild_battle=None;g.battle=None;g._shown_summon_id=None
            g._encounter_ready_at=ready_at;g._encounter_steps=0
        for cid,g in self.games.items():
            if drawing and cid not in self.state['acks']:
                self.service.enqueue(g, *QUEUED_BATTLE_EXIT)
            else:
                # Forced exit replays the last queued command. Input-end
                # preserves the credit needed to process do_war_end.
                self.service.enqueue(g, numbers(2152, cid), numbers(2146))

    def settle_guardians_on_exit(self):
        if self.state['settled']:
            return
        for cid, game in self.games.items():
            for pet in self.state['participants'][cid].values():
                game.summons.settle(pet['id'], self.id, pet['hp'], pet['mp'], False, experience=0)
        self.state['settled'] = True

    def recover_after_battle(self, db):
        from native946_premium_items import heal_after_battle
        from native946_reserve_food import restore_after_battle

        heal_after_battle(db, self.games, service=self.service, leader=self.leader)
        for cid, game in self.games.items():
            guardian = self.current_pet(cid)
            restore_after_battle(db, game, guardian['id'] if guardian else None)

    def finish(self,victory):
        if self.state['phase']=='finished':return
        # Disconnect-only exits still owe guardian participation costs.
        if not self.state['settled']:
            with self.atomic():
                self.settle_guardians_on_exit()
        # Animation ACKs cannot re-award settlement; leaving grants no victory XP.
        self.close_battle(POLICY['encounter_cooldown_seconds'])
        for cid,g in self.games.items():
            reward=self.state.get('rewards',{}).get(cid,{})
            if self.state.get('zhuziguo_bell'):self.service.enqueue(g,numbers(4888,0))
            self.service.refresh_battle_scene(g)
            self.service.enqueue(g,*self.finish_messages(cid,victory))
            if victory and (team_bonus := reward.get('team_experience_percent')):
                g._team_experience_popup = (None, screen_notice(f'组队经验加成#G{team_bonus}%#n。'))
            g.last_event=dict(action='wild_battle_finished',battle_id=self.id,victory=victory,reward=reward)


class ArenaPracticeBattle(WildBattle):
    def __init__(self, leader, games, npc):
        # 阿飞参考端，战斗 888，战斗怪物类:取测试战斗。
        stats = dict(hpmax=10_000_000, mpmax=10_000_000, attack=1, mag=1, defense=0, initiative=720)
        targets = [dict(name=f'测试怪{slot}', type=npc['shape'], guardian_type=0, combat_stats=stats)
                   for slot in range(1, 9)]
        super().__init__(leader, games, dict(scene_name=npc['name'], levels=[150, 150],
                                             enemies=targets, fixed_roster=True))
        for target in self.enemies():
            target['mp'] = 100_000
        # Keep normal battle costs and skip victory awards and guardian participation settlement.
        self.state['settled'] = True

    def finish_messages(self, cid, victory):
        return (notice('伤害测试已结束。'),)
