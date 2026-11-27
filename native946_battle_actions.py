"""Native battle guardian controls and owned medicine orders.

This module extends the existing local practice battle. It does not invent
stunt/inner-pill numerical formulas or grant guardians/items to live players.
"""
from math import ceil
from time import monotonic

from native946_codec import packet
from native946_codec import text_notice
REFERENCE_WIRE_VERSION=12
BATTLE_ORDER_REQUESTS=frozenset({2176,2177,2178,2181,2182,2183,2185})


def numbers(mid,*values):return packet(mid,['number']*len(values),values)

def input_command_packet(actor,order,*,extra=False):
    kind=order['kind']
    skill=order.get('skill_id') or order.get('role_skill') or order.get('guardian_skill')
    if skill:
        command,icon=9,skill
    elif kind=='medicine':
        command,icon=8,order.get('display_item_type',0)
    elif kind=='summon':
        command,icon=6,order.get('display_summon_type',0)
    else:
        command,icon={'defend':1,'attack':3,'protect':4,'capture':5,'dismiss':7}[kind],0
    return numbers(4886 if extra else 3817,actor,command,icon)


# 946 consumes one processing credit at c_begin_pre_draw. Balance the
# menu wakeup so an incoming partial action cannot run before c_start_draw.
SHARED_INPUT_WAKEUP=(numbers(2703),numbers(2139,2))

# Grant processing credit before c_war_end pauses receipt. The queued exit
# runs after any pending actions and deaths on clients still playing the round.
QUEUED_BATTLE_EXIT = (numbers(2139, 2), numbers(2170))

def retry_input_packets(actor,deadline,*,shared=False):
    if actor is None or deadline is None:return ()
    remaining=ceil(deadline-monotonic())
    if remaining<=0:return ()
    # Cancel an invalid automatic order before restoring this actor's input.
    wakeup=SHARED_INPUT_WAKEUP if shared else (numbers(2139,2),)
    return (numbers(5349),numbers(2151,actor,remaining,0),*wakeup)


class DeferredPackets:
    def __init__(self,build,fallback=None):self.build=build;self.fallback=fallback


class BattleActions:
    def retry_battle_input(self,mid):
        battle=self.battle
        if mid not in BATTLE_ORDER_REQUESTS or not battle or battle['phase']!='input':return ()
        actor=battle.get('input_actor')
        if actor in battle.get('orders',{}):return ()
        return retry_input_packets(actor,battle.get('input_deadline'))

    @staticmethod
    def needs_render(result):
        return isinstance(result,DeferredPackets) or (isinstance(result,(tuple,list)) and any(BattleActions.needs_render(v) for v in result))

    def persist_battle_vitals(self):
        """Bounds come from this battle's server-owned participant snapshots."""
        battle=self.battle;pet=battle.get('pet')
        with self.progression.transaction() as db:
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',
                       (min(battle['hpmax'],max(0,battle['hp'])),min(battle['mpmax'],max(0,battle['mp'])),self.progression.cid))
            if pet:
                self.summons.set_vitals(pet['id'],pet['hp'],pet['mp'])

    def defer_packets(self,build,fallback=None):
        return DeferredPackets(build,fallback) if getattr(self,'_serialize_after_commit',False) else build()

    @staticmethod
    def render_packets(result):
        if isinstance(result,DeferredPackets):return BattleActions.render_packets(result.build())
        if isinstance(result,(tuple,list)):
            packets=[]
            for value in result:
                rendered=BattleActions.render_packets(value)
                if isinstance(rendered,(tuple,list)):packets.extend(rendered)
                elif rendered is not None:packets.append(rendered)
            return tuple(packets)
        return result

    @staticmethod
    def fallback_packets(result):
        if isinstance(result,DeferredPackets):
            return result.fallback() if callable(result.fallback) else result.fallback or ()
        if isinstance(result,(tuple,list)):
            packets=[]
            for value in result:
                fallback=BattleActions.fallback_packets(value)
                if isinstance(fallback,(tuple,list)):packets.extend(fallback)
                elif fallback is not None:packets.append(fallback)
            return tuple(packets)
        return result

    def memory_only_battle_input(self,mid,reader):
        """Classify collection-only inputs; every resolution keeps one write transaction."""
        from native946_login import MessageReader
        try:
            probe=MessageReader(reader.data[reader.offset:])
            if mid==1032:
                choice=probe.take(1)[0];callback=probe.integer();probe.finish()
                from native946_gameplay import TRAINER_ID
                actions=getattr(self,'dialog_actions',[])
                return (self.dialog_open and callback==TRAINER_ID and 1<=choice<=len(actions)
                        and actions[choice-1] in ('train','learn','summons','viewskills','view_summons'))
            if mid not in (2176,2177,2178,2181,2182,2183,2184,2185):return False
            battle=self.battle
            if mid==2184:
                probe.finish()
                # A fully controlled round advances automatically and may
                # settle DoTs or child actions inside this same request.
                return not battle or battle['phase']!='drawing'
            count=3 if mid in (2183,2185) else 2 if mid in (2177,2181) else 1
            values=[probe.integer() for _ in range(count)];probe.finish()
            if not battle:return False
            if mid in (2178,2182) and values[0]==2:return False
            if battle['phase']!='input':return True
            if mid==2178 and values[0]==7:return False
            if mid==2177 and values[0]==6:return False
            cid=self.character['id'];pet=battle.get('pet');orders=battle.get('orders',{})
            actor=pet['id'] if mid in (2181,2182,2183) and pet else cid
            from native946_role_battle import solo_required
            return bool(set(solo_required(self))-set(orders)-{actor})
        except (ValueError,IndexError):
            return False

    def order_actor(self,pet=False):
        battle=self.battle
        if not battle or battle['phase']!='input':raise ValueError('当前不在战斗指令阶段。')
        if pet:
            actor=battle.get('pet')
            if not actor or actor['hp']<=0:raise ValueError('当前没有可操作的参战守护。')
            return actor['id']
        return self.character['id']

    def queue_protection(self,target,*,pet=False):
        from native946_role_battle import protect_order,solo_fighters
        actor=self.order_actor(pet)
        fighters=solo_fighters(self)
        return self.queue_order(actor,protect_order(fighters[actor],target,fighters))

    def queue_order(self,actor_id,order,*,replace=False):
        battle=self.battle;orders=battle.setdefault('orders',{})
        if actor_id in orders and not replace:return None
        from native946_combat_control import blocked,can_select_order
        from native946_role_battle import solo_fighters
        actor=solo_fighters(self)[actor_id]
        if not can_select_order(actor,battle['round']):
            raise ValueError('该战斗单位当前不能行动。')
        if blocked(actor,battle['round']) and order['kind']!='defend':
            raise ValueError('该单位正处于人法控制状态，当前只能防御。')
        # Native input UI opens after the previous draw. A valid next-round
        # order can resynchronize a client that omitted its old confirmation.
        if getattr(self, '_background_round', None) != (battle['id'], battle['round']):
            battle.pop('late_round_ends', None)
        orders[actor_id]=order
        preview=input_command_packet(actor_id,order)
        cid=self.character['id'];pet=battle.get('pet')
        if orders.get(cid,{}).get('kind') in ('summon','dismiss'):
            # Replacing/recalling a guardian consumes the hero's local
            # practice turn and cancels the outgoing guardian's pending turn.
            orders={cid:orders[cid]};battle['orders']=orders
        else:
            from native946_role_battle import solo_required
            required=solo_required(self)
            waiting=next((fid for fid in required if fid not in orders),None)
            if waiting is not None:
                acknowledgments=self.end_input(actor_id)
                remaining=self.begin_input(waiting,new_round=False)
                self.last_event={'action':'battle_awaiting_order','fighter_id':waiting}
                return (preview,*acknowledgments,numbers(2151,waiting,remaining,0),numbers(2139,2))
        result=self.resolve_round()
        if isinstance(result,DeferredPackets):
            return DeferredPackets(lambda: (preview,*result.build()),
                                   lambda: (preview,*self.fallback_packets(result)))
        return (preview,*result)

    def battle_call_list(self):
        self.order_actor()
        pets=self.summons.snapshot();battle=self.battle;used=battle.get('participants',{})
        current=battle.get('pet');fighting=[current['id']] if current else []
        records=[dict(ctrl_lingfu=0,summon_id=s['id'],summon_honest=s['honesty'],ctrl_zuoqi=0,
                      summon_type=s['type'],summon_name=s['name']) for s in pets]
        # Native panel overlays can_fight by the parallel byte array, then
        # applies its own current/previous-participant restrictions.
        flags=[int(s['hp']>0 and s['honesty']>=50 and s['id'] not in used and s['id'] not in fighting) for s in pets]
        return packet(2274,[('array','zq_ctrl_summon'),('array','number'),'number',('array','byte')],
                      [records,fighting,0,flags])

    def change_battle_guardian(self,sid=None):
        cid=self.order_actor()
        if cid in self.battle.get('orders',{}):return None
        current=self.battle.get('pet')
        if sid is None:
            if current is None:raise ValueError('当前没有可以召还的守护。')
            order=dict(kind='dismiss',defend=False)
        else:
            pet=self.summons.get(sid)
            if sid in self.battle.get('participants',{}) or (current and current['id']==sid):
                raise ValueError('该守护本场战斗已经出战，不能再次召唤。')
            if pet['hp']<=0:raise ValueError('请先恢复守护气血。')
            if pet['honesty']<50:raise ValueError('守护健康度不足，不能出战。')
            order=dict(kind='summon',summon_id=sid,display_summon_type=pet['type'],defend=False)
        return self.queue_order(cid,order)

    def apply_guardian_order(self,order):
        battle=self.battle;replies=[];old=battle.get('pet')
        if old:
            self.summons.set_vitals(old['id'],old['hp'],old['mp'])
            replies.append(numbers(2137,old['id']))
        if order['kind']=='dismiss':
            battle['pet']=None
            replies.extend((numbers(4206,0),text_notice('守护已召还。')))
        else:
            pet=self.summons.get(order['summon_id'])
            battle['pet']=pet;battle.setdefault('participants',{})[pet['id']]=pet
            from native946_guardian_magic import skills_packet
            fighter=self.fighter(pet['id'],pet['type'],pet['name'],4,3,6,pet['level'],mid=2128)
            replies.extend((numbers(4206,pet['id']),fighter,self.battle_vitals_packet(pet),
                            skills_packet(pet),
                            text_notice(pet['name']+'已进入战斗。')))
        return replies

    def settle_battle_guardians(self,victory):
        battle=self.battle
        pets=battle.get('participants')
        if pets is None:
            current=battle.get('pet');pets={current['id']:current} if current else {}
        for pet in pets.values():
            self.summons.settle(pet['id'],battle['id'],pet['hp'],pet['mp'],victory)

    def medicine_target(self,target):
        battle=self.battle;cid=self.character['id'];pet=battle.get('pet')
        if target==cid:
            return battle,self.character['name']
        if pet and pet['id']==target:return pet,pet['name']
        raise ValueError('药品只能用于当前在场的己方角色或守护。')

    def queue_medicine(self,item_id,target,*,pet=False):
        actor_id=self.order_actor(pet)
        if actor_id in self.battle.get('orders',{}):return None
        if target==0:target=actor_id
        from native946_role_battle import solo_fighters,validate_item
        fighters=solo_fighters(self)
        with self.progression.transaction(write=False) as db:
            item=validate_item(db,self,fighters[actor_id],item_id,target,fighters,self.battle['round'])
            reserved=sum(o.get('kind')=='medicine' and o.get('item_id')==item_id
                         for o in self.battle.get('orders',{}).values())
            if item['amount']<=reserved:raise ValueError('该药品数量不足，已被本回合的另一条指令使用。')
        return self.queue_order(actor_id,dict(kind='medicine',item_id=item_id,target=target,
                                              display_item_type=item['type'],defend=False))

    def apply_medicine_order(self,order):
        from native946_progression import DATA, medicine_effects

        subject,name=self.medicine_target(order['target'])
        with self.progression.transaction() as db:
            item=db.execute('SELECT * FROM inventory WHERE id=? AND character_id=? AND page=8',
                            (order['item_id'],self.progression.cid)).fetchone()
            effects=medicine_effects(item['type']) if item else ()
            if not effects:
                return [text_notice('药品已不在背包，本回合未使用药品。')]
            restored=[]
            for attr,maximum,recovery in effects:
                actual=min(recovery,max(0,subject[maximum]-subject[attr]))
                if actual:subject[attr]+=actual;restored.append(str(actual)+('点气血' if attr=='hp' else '点法力'))
            if restored:
                if item['amount']==1:db.execute('DELETE FROM inventory WHERE id=?',(item['id'],))
                else:db.execute('UPDATE inventory SET amount=amount-1 WHERE id=?',(item['id'],))
            text=(name+'使用'+DATA['items'][str(item['type'])]['name']+'，恢复'+'、'.join(restored)+'。'
                  if restored else name+'当前状态已满，未消耗药品。')
        # These are native queued power-bar updates. No unrelated spell or
        # ordinary melee animation is substituted for a medicine action.
        return [self.battle_vitals_packet(subject,order['target']),text_notice(text)]

    def battle_vitals_packet(self,subject,target=None):
        if target is None:target=subject['id']
        return numbers(2148,target,subject['hp'],subject.get('hp_limit',subject['hpmax']),subject['hpmax'],
                       subject['mp'],subject['mpmax'],subject['mpmax'],0,100,0)
