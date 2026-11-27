"""Source-backed native guardian items, without trainer acquisition shortcuts."""
from pathlib import Path
from time import time,monotonic
import json
from native946_codec import packet
from native946_codec import text_notice

REFERENCE_WIRE_VERSION=7
DATA=json.loads(Path(__file__).with_name('native946_guardian_acquisition_data.json').read_text('utf8'))
ITEMS=DATA['items']
from native946_guardian_lottery import ITEMS as LOTTERY_ITEMS
ITEMS.update(LOTTERY_ITEMS)
ITEM_TYPES={int(k) for field in ('named_eggs','choice_eggs','choice_guardians','essence_exchanges','fragment_exchanges','random_rules','pending') for k in DATA.get(field,{})}
REQUESTS={868,870,4424}
WRITES=REQUESTS


def notice(text):return text_notice(text)


class GuardianAcquisition:
    def __init__(self,game):self.g=game;self.p=game.progression;self.s=game.summons

    def _owned_item(self,db,iid):
        from native946_mall_bags import pages
        row=db.execute('SELECT i.*,coalesce(e.expires,0) expires FROM inventory i '
            'LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.id=? AND i.character_id=?',
            (iid,self.p.cid)).fetchone()
        if not row or row['type'] not in ITEM_TYPES or row['page'] not in pages(db,self.p.cid):
            raise ValueError('守护道具不在当前角色的有效背包中。')
        if row['expires'] and row['expires']<=time():raise ValueError('此守护道具已经到期。')
        from native946_inventory_locks import require_unlocked
        require_unlocked(db,self.p.cid,iid)
        return dict(row)

    def _receipt(self,db,item,reward):
        return db.execute('INSERT INTO cosmetic_use_receipts(character_id,item_id,type,rewards,created) VALUES(?,?,?,?,?)',
            (self.p.cid,item['id'],item['type'],json.dumps(reward,ensure_ascii=False),int(time()))).lastrowid

    def _finish(self,text):
        self.g.guardian_egg_session=None
        self.g.dialog_open=False;self.g.dialog_actions=[];self.g.dialog_npc=None
        return (*self.p.item_packets(),*self.s.sync(),*self.g.sync_summon_scene(),
                packet(1183,['string'],['dlg_npc_chat']),notice(text))

    def use(self,iid):
        if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
        with self.p.transaction() as db:
            item=self._owned_item(db,iid);key=str(item['type'])
            rule=DATA['named_eggs'].get(key)
            if rule:
                from native946_mall_uses import consume
                receipt=self._receipt(db,item,dict(op='guardian_egg_pending'))
                sid=self.s.acquire(rule['guardian_type'],'guardian-egg:'+str(receipt))
                consume(db,item)
                db.execute('UPDATE cosmetic_use_receipts SET rewards=? WHERE id=?',
                    (json.dumps(dict(op='guardian_egg',summon_id=sid,guardian_type=rule['guardian_type'])),receipt))
                name=self.s.get(sid)['name']
                return self.g.defer_packets(lambda:self._finish('已获得守护：'+name+'。'))
            fragment=DATA.get('fragment_exchanges',{}).get(key)
            if fragment:
                return self.exchange_fragments(db,item,fragment)
            essence=DATA.get('essence_exchanges',{}).get(key)
            if essence:
                if essence['direct']:return self.exchange_essence(db,item,essence)
                return packet(3993,['string'],['dlg_lianqi_panel'])
            random_rule=DATA.get('random_rules',{}).get(key)
            if random_rule:
                self.g.guardian_egg_session=dict(item_id=iid,item_type=item['type'],mode='random',until=monotonic()+180)
                text=ITEMS[key]['name']+'#r'+random_rule['local_rule']+'#r使用消耗'+str(random_rule['count'])+'个；候选共'+str(len(random_rule['guardians']))+'种。'
                if random_rule.get('substitution'):text+='#r'+random_rule['substitution']
                return self._menu(text,[('确认使用',dict(op='random_guardian',item_id=iid)),('取消，保留道具',dict(op='cancel'))],self.p.cid)
            guardian_choice=DATA.get('choice_guardians',{}).get(key)
            choice=DATA['choice_eggs'].get(key)
            if not choice and not guardian_choice:raise ValueError(DATA['pending'].get(key,{}).get('reason','此守护道具的发放规则尚未核定，物品未消耗。'))
        # Selection itself never consumes the box; confirm rechecks ownership,
        # expiry, locks and item type under the ordinary write transaction.
        if guardian_choice:
            from native946_wilderness import DATA as WILD
            self.g.guardian_egg_session=dict(item_id=iid,item_type=item['type'],guardians=tuple(guardian_choice['guardians']),until=monotonic()+180)
            actions=[(WILD['species'][str(kind)]['name'],dict(op='select_guardian',item_id=iid,guardian_type=kind)) for kind in guardian_choice['guardians']]
            title='请选择要兑换的守护。'
        else:
            self.g.guardian_egg_session=dict(item_id=iid,item_type=item['type'],items=tuple(choice['items']),until=monotonic()+180)
            actions=[('领取'+ITEMS[str(kind)]['name'],dict(op='select_egg',item_id=iid,reward_type=kind)) for kind in choice['items']]
            title='请选择一项道具。'
        actions.append(('取消选择，保留道具',dict(op='cancel')))
        return self._menu((guardian_choice or choice)['name']+'：'+title+'确认后才消耗当前道具。',actions,self.p.cid)

    def random_guardian(self,iid):
        from secrets import choice
        from native946_wilderness import DATA as WILD,carry_rank
        session=getattr(self.g,'guardian_egg_session',None)
        if not session or session.get('mode')!='random' or session['item_id']!=iid or session['until']<monotonic():
            raise ValueError('随机守护确认已失效，请重新使用道具。')
        if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
        with self.p.transaction() as db:
            item=self._owned_item(db,iid);rule=DATA['random_rules'].get(str(item['type']))
            if not rule or item['type']!=session['item_type']:raise ValueError('随机守护道具已变化；物品未消耗。')
            rank=carry_rank(self.p._snapshot(db))
            candidates=[kind for kind in rule['guardians'] if rank>=WILD['species'][str(kind)]['take_grade_num']]
            if not candidates:raise ValueError('尚未达到候选守护的携带等级；物品未消耗。')
            if db.execute('SELECT count(*) FROM summons WHERE character_id=?',(self.p.cid,)).fetchone()[0]>=self.s.carry_limit():
                raise ValueError('守护携带栏已满；物品未消耗。')
            # Validate all material/capacity constraints before the draw. The
            # eligible pool itself is explicit local policy, avoiding free
            # rerolls caused by drawing an uncarryable species.
            self._consume_material(db,item,rule['count'])
            kind=choice(candidates);receipt=self._receipt(db,item,dict(op='guardian_random_pending'))
            sid=self.s.acquire(kind,'guardian-random:'+str(receipt))
            db.execute('UPDATE cosmetic_use_receipts SET rewards=? WHERE id=?',
                (json.dumps(dict(op='guardian_random',summon_id=sid,guardian_type=kind,cost=rule['count'],
                    distribution=rule['distribution'],eligible_pool=candidates)),receipt))
            name=self.s.get(sid)['name']
        return self.g.defer_packets(lambda:self._finish('本服等概率抽取成功，已获得守护：'+name+'。'))

    def exchange_fragments(self,db,item,rule):
        self._consume_material(db,item,rule['count'])
        created=self._give_item(db,rule['reward_type'])
        self._receipt(db,item,dict(op='guardian_fragment_exchange',cost=rule['count'],
            item_id=created,type=rule['reward_type'],amount=1))
        return self.g.defer_packets(lambda:self._finish('已用十枚超级守护蛋碎片合成一枚超级守护蛋。'))

    def _consume_material(self,db,item,count,item_ids=None):
        from native946_mall_uses import consume
        from native946_mall_bags import pages
        from native946_inventory_locks import require_unlocked
        normal=pages(db,self.p.cid);available=[]
        # Start with the clicked stack; other valid unlocked stacks may complete
        # the original material recipe. Locked/expired/storage rows do not.
        rows=db.execute('SELECT i.*,coalesce(e.expires,0) expires FROM inventory i '
            'LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.character_id=? AND i.type=? '
            'ORDER BY (i.id=?) DESC,i.id',(self.p.cid,item['type'],item['id'])).fetchall()
        for row in rows:
            if row['page'] not in normal or row['expires'] and row['expires']<=time() or item_ids is not None and row['id'] not in item_ids:continue
            try:require_unlocked(db,self.p.cid,row['id'])
            except ValueError:continue
            available.append(row)
        if sum(r['amount'] for r in available)<count:
            raise ValueError('需要集齐'+str(count)+'个未锁定的有效'+ITEMS[str(item['type'])]['name']+'；物品未消耗。')
        remaining=count
        for row in available:
            take=min(remaining,row['amount']);consume(db,row,take);remaining-=take
            if not remaining:break

    def exchange_essence(self,db,item,rule,item_ids=None):
        self._consume_material(db,item,rule['count'],item_ids)
        receipt=self._receipt(db,item,dict(op='guardian_essence_pending'))
        sid=self.s.acquire(rule['guardian_type'],'guardian-essence:'+str(receipt))
        db.execute('UPDATE cosmetic_use_receipts SET rewards=? WHERE id=?',
            (json.dumps(dict(op='guardian_essence',cost=rule['count'],summon_id=sid,guardian_type=rule['guardian_type'])),receipt))
        name=self.s.get(sid)['name']
        return self.g.defer_packets(lambda:self._finish('元神蕴华合成成功，已获得守护：'+name+'。'))

    def redeem_guardian(self,iid,guardian_type,session_required=False):
        if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
        if session_required:
            session=getattr(self.g,'guardian_egg_session',None)
            if not session or session['item_id']!=iid or session['until']<monotonic() or guardian_type not in session.get('guardians',()):
                raise ValueError('守护兑换选择已失效，请重新使用道具。')
        from native946_mall_uses import consume
        with self.p.transaction() as db:
            item=self._owned_item(db,iid);rule=DATA['choice_guardians'].get(str(item['type']))
            if not rule or guardian_type not in rule['guardians']:raise ValueError('该活动兑换券不包含所选守护；物品未消耗。')
            receipt=self._receipt(db,item,dict(op='guardian_card_pending'))
            sid=self.s.acquire(guardian_type,'guardian-card:'+str(receipt))
            consume(db,item)
            db.execute('UPDATE cosmetic_use_receipts SET rewards=? WHERE id=?',
                (json.dumps(dict(op='guardian_card',summon_id=sid,guardian_type=guardian_type)),receipt))
            name=self.s.get(sid)['name']
        return self.g.defer_packets(lambda:self._finish('活动兑换成功，已获得守护：'+name+'。'))

    def receive(self,mid,reader):
        if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
        if mid==4424:
            kind=reader.integer();guardian_type=reader.integer();reader.finish()
            if str(kind) not in DATA['choice_guardians']:raise ValueError('此闪卡的兑换名单尚未核定；物品未消耗。')
            with self.p.transaction(write=False) as db:
                # The native flashcard request carries a type, not an item ID.
                # Resolve it only against this character's valid unlocked bag.
                rows=db.execute('SELECT id FROM inventory WHERE character_id=? AND type=? ORDER BY id',(self.p.cid,kind)).fetchall()
                iid=None
                for row in rows:
                    try:self._owned_item(db,row['id']);iid=row['id'];break
                    except ValueError:continue
                if iid is None:raise ValueError('背包中没有可用的限定闪卡；物品未消耗。')
            return self.redeem_guardian(iid,guardian_type)
        if mid in (868,870):
            sid1=reader.integer();count=reader.take(1)[0]
            ids=[reader.integer() for _ in range(count)]
            sid2=reader.integer() if mid==868 else 0;reader.finish()
            # These three native recipes list essence as their sole material.
            # A displayed host must belong to the actor and is never consumed.
            if sid1:self.s.get(sid1)
            if sid2:raise ValueError('元神蕴华配方不需要第二只守护，请先移出；本次未消耗。')
            if not ids or len(ids)!=len(set(ids)):raise ValueError('炼气材料列表无效；本次未消耗。')
            with self.p.transaction() as db:
                items=[self._owned_item(db,iid) for iid in ids];kind=items[0]['type']
                rule=DATA['essence_exchanges'].get(str(kind))
                if not rule or any(r['type']!=kind for r in items):raise ValueError('该炼气配方尚未核定；本次未消耗。')
                return self.exchange_essence(db,items[0],rule,set(ids))
        raise ValueError('守护获取请求无效。')

    def _give_item(self,db,reward_type):
        from native946_mall_bags import pages
        from native946_inventory_locks import require_unlocked
        normal=pages(db,self.p.cid)
        limit=min(500,ITEMS[str(reward_type)]['max_stack'])
        if limit>1:
            rows=db.execute('SELECT i.*,coalesce(e.expires,0) expires FROM inventory i '
                'LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.character_id=? AND i.type=? '
                'AND i.amount<? ORDER BY i.id',(self.p.cid,reward_type,limit)).fetchall()
            for row in rows:
                if row['page'] not in normal or row['expires']:continue
                try:require_unlocked(db,self.p.cid,row['id'])
                except ValueError:continue
                db.execute('UPDATE inventory SET amount=amount+1 WHERE id=?',(row['id'],))
                return row['id']
        used={(r['page'],r['slot']) for r in db.execute('SELECT page,slot FROM inventory WHERE character_id=?',(self.p.cid,))}
        slot=next(((page,n) for page in normal for n in range(1,25) if (page,n) not in used),None)
        if slot is None:raise ValueError('背包已满，请整理后重试；本次物品未消耗。')
        return db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
            (self.p.cid,reward_type,*slot)).lastrowid

    def select(self,iid,reward_type):
        if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
        session=getattr(self.g,'guardian_egg_session',None)
        if not session or session['item_id']!=iid or session['until']<monotonic() or reward_type not in session.get('items',()):
            raise ValueError('守护蛋选择已失效，请重新使用道具。')
        from native946_mall_uses import consume
        with self.p.transaction() as db:
            item=self._owned_item(db,iid)
            rule=DATA['choice_eggs'].get(str(item['type']))
            if item['type']!=session['item_type'] or not rule or reward_type not in rule['items']:
                raise ValueError('守护蛋内容已变化，请重新打开。')
            # An unstacked box releases its own slot. A remaining stack needs
            # another free normal-bag slot; any failure rolls the consume back.
            consume(db,item)
            created=self._give_item(db,reward_type)
            self._receipt(db,item,dict(op='guardian_egg_choice',item_id=created,type=reward_type,amount=1))
        name=ITEMS[str(reward_type)]['name']
        return self.g.defer_packets(lambda:self._finish('已获得'+name+'，可在背包中继续使用。'))

    def _menu(self,text,choices,nid):
        self.g.dialog_actions=[dict(kind='guardian_acquisition',**action) for _,action in choices]
        return self.g.menu(text,[name for name,_ in choices],nid)

    def execute(self,callback,action):
        if callback!=self.p.cid:raise ValueError('守护蛋选择会话不匹配。')
        op=action['op']
        if op=='cancel':
            self.g.guardian_egg_session=None;self.g.dialog_open=False;self.g.dialog_actions=[];self.g.dialog_npc=None
            return packet(1183,['string'],['dlg_npc_chat'])
        if op=='select_egg':
            return self.select(action['item_id'],action['reward_type'])
        if op=='select_guardian':
            return self.redeem_guardian(action['item_id'],action['guardian_type'],True)
        if op=='random_guardian':return self.random_guardian(action['item_id'])
        raise ValueError('守护获取操作无效。')
