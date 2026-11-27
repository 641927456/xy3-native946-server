"""Owned, transactional town services with native menus, bank and seller packets.

Medicine prices use the user-supplied reference package.
Medical restoration is explicitly free local testing, not official pricing.
"""
from pathlib import Path
from time import monotonic, time
import json
REFERENCE_WIRE_VERSION=24
from native946_codec import packet
from native946_codec import text_notice
from native946_pawnshop import NPC as PAWNSHOP_NPC, Pawnshop, schema as pawnshop_schema
from native946_trade import MAX_CASH

DATA=json.loads(Path(__file__).with_name('native946_npc_services_data.json').read_text('utf-8'))
DEPOSIT, WITHDRAW = 1, 2
BANK_UNLOCK_HINT = '取款前，请在物品栏点击“物品加锁”，再选择“高级操作”验证加锁码。'


def bank_balances(db, cid):
    row = db.execute('SELECT c.gold, COALESCE(b.balance, 0) FROM characters c '
                     'LEFT JOIN npc_bank_accounts b ON b.character_id=c.id WHERE c.id=?', (cid,)).fetchone()
    return tuple(row)


def bank_balance_packets(cash, savings):
    return (packet(695, ['number', 'number'], [34, cash]),
            packet(695, ['number', 'number'], [35, savings]),
            packet(694, [], []))
STORAGE_PAGE=DATA['storage_page']
NATIVE_STORAGE_PAGE=150  # Original COMMON_CONST.ITEMWARE_PAGE_START.
STORAGE_CAPACITY=DATA['storage_capacity']
# 946 ITEM[6723] and the official Saint Pearl exchange table agree on this reward.
# http://xy3.163.com/2009/10/9/4782_206337.html
SHENGZHU_NPC=(2002,1201)
WHITE_TIGER_PEARL=6723
REGRET_STONE=6183
REGRET_STONE_COST=85


def warehouse_rows(db, cid):
    return [dict(row, page=NATIVE_STORAGE_PAGE) for row in db.execute('''
        SELECT i.*,coalesce(l.flag,0) AS lock,coalesce(l.release_at,0) AS lock_block_time
        FROM inventory i LEFT JOIN native_object_locks l
          ON l.kind='item' AND l.object_id=i.id AND l.character_id=i.character_id
        WHERE i.character_id=? AND i.page=? ORDER BY i.slot''', (cid, STORAGE_PAGE))]


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS npc_bank_accounts (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id),
      balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0));''')
    pawnshop_schema(db)
    from native946_workshops import schema as workshop_schema
    workshop_schema(db)
    from native946_npc_quests import schema as quest_schema
    quest_schema(db)
    from native946_task_chain import schema as chain_schema
    chain_schema(db)
    from native946_nice_ids import schema as nice_id_schema
    nice_id_schema(db)


def service_kind(npc):
    if (npc['scene_id'],npc['npc_id'])==PAWNSHOP_NPC:return 'pawnshop'
    if (npc['scene_id'],npc['npc_id']) in ((2092,4755),(2221,4545),(2436,7630)):return 'gear_bench'
    from native946_school import MASTERS, SCHOOL_LEADERS
    from native946_nice_ids import NPC as NICE_ID_NPC
    from native946_refinement import GEAR_NPCS
    if (npc['scene_id'],npc['npc_id']) in MASTERS or (npc['scene_id'],npc['npc_id']) in SCHOOL_LEADERS:return 'school'
    if (npc['scene_id'],npc['npc_id'])==SHENGZHU_NPC:return 'shengzhu'
    if (npc['scene_id'],npc['npc_id'])==NICE_ID_NPC:return 'nice_id'
    if (npc['scene_id'],npc['npc_id']) in GEAR_NPCS:return 'gear_refinement'
    from native946_workshops import actor,task_giver
    if actor(npc):return 'workshop'
    if task_giver(npc):return 'task_rules'
    text=npc.get('title','')+' '+npc.get('desc','')
    if '异宝斋' in text or '染色工人' in text:return 'cosmetic'
    if '钱庄' in text:return 'bank'
    if '仓库' in text and npc['npc_type']==5:return 'storage'
    if npc['npc_type']==6:return 'medical'
    if npc['scene_id']==DATA['medicine_shop']['scene'] and npc['npc_id']==DATA['medicine_shop']['npc_id']:return 'medicine'
    from native946_shops import merchant
    if merchant(npc):return 'merchant'


class NpcServices:
    def __init__(self,game):self.g=game;self.p=game.progression

    def validate(self,nid,kind=None):
        from native946_world import world
        c=self.g.character;npc=world().npc(c['map_id'],nid)
        if self.g.battle:raise ValueError('请先结束战斗。')
        if not npc or max(abs(npc['x']//20-c['x']),abs(npc['y']//20-c['y']))>12:
            raise ValueError('请走近对应的城镇 NPC 再办理。')
        if kind is not None and service_kind(npc)!=kind:raise ValueError('该 NPC 不提供这项服务。')
        return npc

    def menu(self,npc):
        from native946_song_fireworks import CLAIM_NPC
        if (npc['scene_id'],npc['npc_id']) == CLAIM_NPC:
            return self._menu(npc['npc_id'],'本机可免费领取一枚烟花·流星落雨，每个账号限领一次，当天有效。',
                              [('领取烟花·流星落雨', dict(kind='song_firework_claim'))])
        from native946_afei_society import chamber_menu, marriage_menu
        from native946_constellations import npc_menu as constellation_menu
        for menu in (chamber_menu,marriage_menu,constellation_menu):
            reply=menu(self.g,npc)
            if reply is not None:return reply
        from native946_daily_content import exchange_menu
        exchange = exchange_menu(self.g, npc)
        if exchange is not None:
            return exchange
        from native946_ordinary_treasure import exchange_menu as treasure_menu
        treasure = treasure_menu(self.g,npc)
        if treasure is not None:return treasure
        from native946_guild_war import WAR_NPCS
        if (npc['scene_id'],npc['npc_id']) in WAR_NPCS:
            return self._menu(npc['npc_id'],'帮派竞赛',
                              [('查看帮战赛程',dict(kind='guild_war',op='panel')),
                               ('帮派锻体',dict(kind='guild_war',op='training'))])
        from native946_guilds import FOUNDING_CASH, MIN_LEVEL as GUILD_LEVEL, RECRUITER
        if (npc['scene_id'], npc['npc_id']) == RECRUITER:
            return self._menu(npc['npc_id'],f'达到{GUILD_LEVEL}级后可申请加入或建立帮派。缴纳{FOUNDING_CASH}现金后帮派立即成立。',
                              [(label,dict(kind='guild_intro',op=operation)) for label,operation in
                               [('建立帮派','create'),('查看可加入的帮派','list')]])
        from native946_race_fabao import MENTOR
        if (npc['scene_id'],npc['npc_id'])==MENTOR:
            from native946_race_fabao import QUEST
            from native946_tasks import STORIES

            state=self.p.snapshot()
            choices=[('打开种族法宝',dict(kind='race_fabao_mentor'))]
            if state['campaign']['runs'].get(QUEST,{}).get('state')!='completed':
                op='cancel' if state.get('race_fabao_guide') else 'accept'
                label='放弃四曼陀罗引导' if op=='cancel' else '领取四曼陀罗引导'
                choices.insert(0,(label,dict(kind='race_fabao_guide',op=op)))
                if op=='cancel':
                    choices.insert(0,('查询四方真言灵符',dict(kind='race_fabao_guide',op='clues')))
            else:
                choices.append(('不空醍醐',dict(kind='race_fabao_mentor',op='stove')))
            return self._menu(npc['npc_id'],STORIES[int(QUEST)]['desc_str'],choices)
        if npc.get('name')=='崩将军':
            from native946_lineups import npc_menu
            return npc_menu(self.g,npc)
        from native946_role_society import role_actor,npc_menu
        if role_actor(npc):return npc_menu(self.g,npc)
        from native946_npc_quests import Quests
        quests=Quests(self.g).menu(npc)
        if quests is not None:return quests
        from native946_cultivation import GIVERS, menu as cultivation_menu
        if (npc['scene_id'],npc['npc_id']) in GIVERS:
            result=cultivation_menu(self.g,npc)
            if result is not None:return result
        kind=service_kind(npc);nid=npc['npc_id']
        if kind=='school':
            from native946_school import School
            return School(self.g).menu(npc)
        if kind=='shengzhu':
            return self._menu(nid,'圣珠可还原为相应宝物。',[('圣珠还原',{'kind':'shengzhu','op':'open'})])
        if kind=='nice_id' and not self.g.characters.trial_server:
            return self._menu(nid,'可办理靓号选号、收藏和续费。号码和价格来自官网历史记录。',[('我要极品靓号',{'kind':'nice_id','op':'open'})])
        if kind=='gear_bench':
            label={(2221,4545):'打造守护法宝',(2436,7630):'境宝石镶嵌、合成与兑换'}.get((npc['scene_id'],nid),'首饰镶嵌与开孔')
            choices=[(label,dict(kind=kind,op='open'))]
            if (npc['scene_id'],nid)==(2221,4545):
                choices.append(('升级守护法宝',dict(kind=kind,op='guardian_upgrade')))
                choices.extend((label,dict(kind='guardian_ornaments',op=op)) for label,op in (
                    ('臻彩文钱兑换专属守护饰品','ornaments'),('煌色饰品兑换','ornament_color'),
                    ('守护饰品精炼','ornament_refine'),('本机购买乾坤灵石（5000现金）','ornament_stone'),
                    ('本机购买20个钧天玉（20万现金）','ornament_token')))
            if (npc['scene_id'],nid)==(2436,7630):
                choices.append(('本机购买100个宝石精粹（5000现金）',dict(kind=kind,op='gem_essence')))
            return self._menu(nid,'请选择装备服务。',choices)
        if kind=='gear_refinement':
            from native946_refinement import GEAR_NPCS, SUPPLY_CASH
            gear=GEAR_NPCS[npc['scene_id'],nid]
            choices=[('装备炼化',dict(kind=kind,op='forge'))]
            if gear!='jing':choices.append(('分解装备',dict(kind=kind,op='disassemble')))
            if gear=='xianqi':choices.append(('本机仙器升阶',dict(kind=kind,op='fairy_list',page=0)))
            if gear=='shenbing':
                choices.extend((label,dict(kind=kind,op='transition',branch=branch)) for label,branch in
                               (('本机神兵兑换与升阶','shenbing'),('本机铸造圣武','holy')))
            if gear=='jing':
                choices.extend([('本机境装备转换与升品',dict(kind=kind,op='transition',branch='jing')),
                                ('境宝石镶嵌',dict(kind=kind,op='jing_gems'))])
            choices.append((f'购买本机炼化辅料（{SUPPLY_CASH}现金）',dict(kind=kind,op='supplies')))
            from native946_shops import merchant
            if merchant(npc):
                if merchant(npc)['goods']:choices.append(('购买商品',dict(kind='merchant',op='open')))
                choices.append(('查看售卖目录',dict(kind='merchant',op='catalog')))
            return self._menu(nid,'炼化会消耗精魂、炼化图鉴、血玲珑、现金和炼化活力。',choices)
        if kind=='cosmetic':
            from native946_cosmetics import Cosmetics
            return Cosmetics(self.g).menu(npc)
        if kind in ('workshop','task_rules'):
            from native946_workshops import Workshops
            return Workshops(self.g).menu(npc)
        if kind=='bank':
            return self._menu(nid,'客官，您要存钱还是取钱？#r'+BANK_UNLOCK_HINT,
                              [('我想存钱',dict(kind=kind,op=DEPOSIT)),('我想取钱',dict(kind=kind,op=WITHDRAW))])
        if kind=='pawnshop':
            return Pawnshop(self).menu()
        if kind=='medical':
            from native946_premium_items import PREMIUM_MEDICAL_TERMS
            return self._menu(nid,'普通医疗免费。队长用共济丸共享时，受治疗的成员各自支付本机费用。'+PREMIUM_MEDICAL_TERMS,
                              [('治疗人物',{'kind':'medical','op':'hero'}),('治疗守护',{'kind':'medical','op':'summons'}),
                               ('治疗人物和守护',{'kind':'medical','op':'all'})])
        if kind=='storage':
            return self._menu(nid,'暂时不用的东西，可以存放在这里。',
                              [('打开仓库',{'kind':'storage','op':'open'})])
        if kind=='medicine':
            return self._menu(nid,npc['title'] + ' ' + npc['name'],
                              [('购买药品',{'kind':'medicine','op':'open'})],speaker=nid)
        if kind=='merchant':
            from native946_shops import Shops
            return Shops(self.g).menu(npc)

    def _menu(self,nid,text,choices,*,speaker=0):
        self.g.dialog_actions=[value for label,value in choices]
        return self.g.menu(text,[label for label,value in choices],nid,speaker=speaker)

    def execute(self,nid,action):
        if action['kind']=='song_firework_claim':
            from native946_song_fireworks import claim
            return claim(self.g,self.validate(nid))
        if action['kind']=='constellation':
            from native946_constellations import Constellations
            return Constellations(self.g).execute(nid,action)
        if action['kind']=='guardian_shop':
            from native946_guardian_chamber import GuardianChamber
            return GuardianChamber(self.g).execute(nid,action)
        if action['kind']=='afei_shop':
            from native946_afei_society import Chamber
            return Chamber(self.g).execute(nid,action)
        if action['kind']=='afei_marriage':
            from native946_afei_society import Marriage
            return Marriage(self.g).execute(nid,action['op'])
        if action['kind']=='advanced_treasure':
            from native946_ordinary_treasure import exchange_advanced
            return exchange_advanced(self.g,nid,superior=action.get('superior',False))
        if action['kind']=='sanjie_exchange':
            from native946_daily_content import exchange
            return exchange(self.g, nid, action['item'])
        if action['kind']=='guild_war':
            from native946_guild_war import open_at_npc
            return open_at_npc(self.g,nid,action['op'])
        if action['kind']=='race_fabao_guide':
            from native946_race_fabao import mentor_guide
            return mentor_guide(self.g,nid,action['op'],action.get('target'))
        if action['kind']=='race_fabao_mentor':
            from native946_race_fabao import mentor_panel
            return mentor_panel(self.g,nid,action.get('op','panel'))
        if action['kind']=='guild_intro':
            from native946_guilds import introduction
            return introduction(self.g,nid,action['op'])
        if action['kind']=='cultivation':
            from native946_cultivation import advance
            return advance(self.g,self.validate(nid),action['op'])
        if action['kind']=='lineup_npc':
            from native946_lineups import npc_execute
            return npc_execute(self.g,nid,action)
        if action['kind']=='school':
            from native946_school import School
            return School(self.g).execute(nid,action)
        if action['kind']=='role_system':
            from native946_role_society import npc_execute
            return npc_execute(self.g,nid,action)
        if action['kind']=='task_chain':
            from native946_task_chain import TaskChain
            return TaskChain(self.g).execute(nid,action)
        if action['kind']=='npc_quest':
            from native946_npc_quests import Quests
            return Quests(self.g).execute(nid,action)
        kind=action['kind']
        if kind=='guardian_ornaments':
            from native946_guardian_ornaments import npc as ornament_npc
            return ornament_npc(self.g,nid,action)
        if kind=='gear_bench':
            from native946_gear_services import open_bench
            self.validate(nid)
            return open_bench(self.g,nid,action['op'])
        if kind=='merchant':
            from native946_shops import Shops,merchant
            npc=self.validate(nid)
            if not merchant(npc):raise ValueError('这个 NPC 没有商店目录。')
            if action['op']=='menu':return Shops(self.g).menu(npc)
            if action['op']=='open':return Shops(self.g).open_ordinary(nid)
            return Shops(self.g).ordinary_catalog(nid,action.get('page',0))
        self.validate(nid,kind)
        if kind=='nice_id' and action['op']=='open':
            from native946_nice_ids import NiceIds
            return NiceIds(self.g).open()
        if kind=='gear_refinement':
            from native946_refinement import GEAR_NPCS, Refinement
            scene=self.g.character['map_id']
            gear=GEAR_NPCS[scene,nid]
            service=Refinement(self.g)
            if action['op']=='fairy_list' and gear=='xianqi':return service.fairy_upgrade_menu(nid,action['page'])
            if action['op']=='fairy_upgrade' and gear=='xianqi':return service.upgrade_fairy(action['item'])
            if action['op']=='supplies':return service.supplies(gear)
            if action['op'] in ('transition','transition_do'):
                from native946_gear_services import progression_menu, transition
                if (gear,action['branch']) not in (('shenbing','shenbing'),('shenbing','holy'),('jing','jing')):
                    raise ValueError('装备转换服务不匹配。')
                if action['op']=='transition_do':return transition(self.g,action)
                return progression_menu(self.g,nid,action['branch'],action.get('page',0))
            self.g.npc_service_session=dict(kind=kind,npc=nid,scene=scene,expires=monotonic()+600)
            if gear=='jing' and action['op']=='jing_gems':
                return (packet(1183,['string'],['dlg_npc_chat']),packet(3525,[],[]))
            return service.open_gear(gear,action['op'])
        if kind=='shengzhu' and action['op']=='open':
            self.g.npc_service_session=dict(kind=kind,npc=nid,scene=SHENGZHU_NPC[0],expires=monotonic()+120)
            goods=[dict(pcount=REGRET_STONE_COST,type=REGRET_STONE,ptype=WHITE_TIGER_PEARL,id=1,cate=1)]
            return (packet(1183,['string'],['dlg_npc_chat']),packet(1679,['number',('array','advitemexc_item')],[nid,goods]))
        if kind=='cosmetic':
            from native946_cosmetics import Cosmetics
            result=Cosmetics(self.g).execute(nid,action)
            if action['op'] in ('mall','closet','weapons','weapon_color','skins','dye_all','dye_part'):
                messages=result if isinstance(result,(tuple,list)) else (result,)
                return (*messages,packet(1183,['string'],['dlg_npc_chat']))
            return result
        if kind in ('workshop','task_rules'):
            from native946_workshops import Workshops
            return Workshops(self.g).execute(nid,action)
        if kind=='bank':
            operation=action['op']
            close=packet(1183,['string'],['dlg_npc_chat'])
            if operation==WITHDRAW and not self.bank_withdrawal_unlocked():
                return close,text_notice(BANK_UNLOCK_HINT)
            with self.p.transaction(write=False) as db:
                balances=bank_balances(db,self.p.cid)
            self.g.npc_service_session=dict(kind=kind,npc=nid,scene=self.g.character['map_id'],operation=operation,expires=monotonic()+120)
            return (close,*bank_balance_packets(*balances),packet(1165,['number'],[operation]))
        if kind=='pawnshop':
            return Pawnshop(self).open(action['op'])
        if kind=='medical':
            from native946_premium_items import heal_at_existing_npc
            return heal_at_existing_npc(self.g,action['op'],nid)
        if kind=='medicine':
            from native946_shops import Shops
            return Shops(self.g).open_medicine(nid)
        if kind=='storage':
            if action['op']=='open':
                self.g.npc_service_session=dict(kind=kind,npc=nid,scene=self.g.character['map_id'],expires=monotonic()+600)
                return (packet(1183,['string'],['dlg_npc_chat']),*self.storage_view(nid))

    def _session(self,kind,nid=None):
        s=getattr(self.g,'npc_service_session',None)
        if not s or s['kind']!=kind or s['expires']<monotonic() or s['scene']!=self.g.character['map_id']:
            raise ValueError('办理窗口已失效，请与对应 NPC 重新交谈。')
        if nid is not None and s['npc']!=nid:raise ValueError('商店 NPC 不匹配。')
        service = {'school_supply':'school','guardian_shop_window':None}.get(kind,kind)
        self.validate(s['npc'],service)
        return s

    def restore_pearl(self,nid,reward,category):
        self._session('shengzhu',nid)
        if (reward,category)!=(REGRET_STONE,1):raise ValueError('此圣珠还原项目无法办理。')
        from native946_inventory_locks import require_unlocked
        from native946_mall_bags import pages
        from native946_mall_uses import consume
        from native946_guardian_acquisition import GuardianAcquisition
        with self.p.transaction() as db:
            rows=db.execute('SELECT i.* FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id '
                'WHERE i.character_id=? AND i.type=? AND (coalesce(e.expires,0)=0 OR e.expires>?) ORDER BY i.id',
                (self.p.cid,WHITE_TIGER_PEARL,int(time()))).fetchall()
            allowed_pages=pages(db,self.p.cid)
            available=[]
            for row in rows:
                if row['page'] not in allowed_pages:continue
                try:require_unlocked(db,self.p.cid,row['id'])
                except ValueError:continue
                available.append(row)
            if sum(row['amount'] for row in available)<REGRET_STONE_COST:
                raise ValueError('需要85枚有效且未锁定的圣珠之白虎。')
            remaining=REGRET_STONE_COST
            for row in available:
                amount=min(remaining,row['amount'])
                consume(db,row,amount)
                remaining-=amount
                if not remaining:break
            GuardianAcquisition(self.g)._give_item(db,REGRET_STONE)
        self.g.npc_service_session=None
        return self.g.defer_packets(lambda:(*self.p.item_packets(),text_notice('圣珠还原成功，获得悔梦石。')))

    def bank_withdrawal_unlocked(self):
        return getattr(self.g,'_inventory_advanced_until',0)>monotonic()

    def bank(self,operation,amount):
        session=self._session('bank')
        if operation!=session['operation'] or operation not in (DEPOSIT,WITHDRAW) or type(amount) is not int or not 0<amount<=MAX_CASH:
            raise ValueError('存取类型或金额无效。')
        # The native panel closes as soon as it submits a positive amount.
        # The outer gameplay transaction restores the session on a storage retry.
        self.g.npc_service_session=None
        deposit=operation==DEPOSIT
        if not deposit and not self.bank_withdrawal_unlocked():
            return text_notice(BANK_UNLOCK_HINT)
        with self.p.transaction() as db:
            cash,savings=bank_balances(db,self.p.cid)
            if amount>(cash if deposit else savings):
                return text_notice('随身现金不足。' if deposit else '钱庄存款不足。')
            delta=amount if deposit else -amount
            cash,savings=cash-delta,savings+delta
            if max(cash,savings)>MAX_CASH:
                return text_notice('存取后的余额超出可显示范围，请减少金额。')
            db.execute('UPDATE characters SET gold=? WHERE id=?',(cash,self.p.cid))
            db.execute('INSERT INTO npc_bank_accounts(character_id,balance) VALUES(?,?) '
                       'ON CONFLICT(character_id) DO UPDATE SET balance=excluded.balance',(self.p.cid,savings))
        return (*bank_balance_packets(cash,savings),text_notice(f'您已{("存入" if deposit else "取出")}{amount}两现金。'))

    def buy(self,nid,kind,amount):
        from native946_shops import Shops
        return Shops(self.g).buy_medicine(nid,kind,amount)

    def storage_view(self,nid):
        from native946_progression import lua_data

        with self.p.transaction(write=False) as db:
            rows=warehouse_rows(db,self.p.cid)
            known=getattr(self.p,'_known_item_positions',{})
            unlisted={i['id'] for i in rows if known.get(i['id'])!=(NATIVE_STORAGE_PAGE,i['slot'])}
            initial=self.p.item_packets(item_ids=unlisted)
            items=self.p.item_records(db,rows)
            extra=[dict(id=row['id'],endure=row['durability']) for row in db.execute('''
                SELECT i.id,e.durability FROM native_equipment e JOIN inventory i
                  ON i.id=e.item_id AND i.character_id=e.character_id
                WHERE i.character_id=? AND i.page=? ORDER BY i.slot''', (self.p.cid,STORAGE_PAGE))]
        # Native c_itemware_open: NPC, first page, page count, item_info[],
        # serialized extra metadata, ITEM_STORE_HOUSE=1, cells per page.
        return (*initial,packet(839,['number','number','number',('array','item_info'),'string','number','number'],
                               [nid,NATIVE_STORAGE_PAGE,1,items,lua_data(extra),1,STORAGE_CAPACITY]))

    def storage_transfer(self,item_id,nid,direction,page,slot):
        from native946_inventory_locks import records
        from native946_mall_bags import pages
        from native946_inventory_features import DATA as features

        self._session('storage',nid)
        if direction not in (0,1,2) or not -1 <= slot <= STORAGE_CAPACITY:
            raise ValueError('仓库移动方向、页面或格子无效。')
        target=STORAGE_PAGE if page==NATIVE_STORAGE_PAGE else page
        changed={item_id}
        with self.p.transaction() as db:
            active=pages(db,self.p.cid)
            withdraw=pages(db,self.p.cid,True)
            row=db.execute('SELECT * FROM inventory WHERE character_id=? AND id=?',(self.p.cid,item_id)).fetchone()
            allowed=(withdraw if direction==0 else (STORAGE_PAGE,))
            legal_target=page in active if direction==1 else page==NATIVE_STORAGE_PAGE
            if not row or row['page'] not in allowed or not legal_target:
                raise ValueError('物品不在指定的道具栏或仓库中。')

            occupied={i['slot']:i for i in db.execute('SELECT * FROM inventory WHERE character_id=? AND page=? ORDER BY slot',
                                                     (self.p.cid,target))}
            equipped={i[0] for i in db.execute('SELECT item_id FROM native_equipment WHERE character_id=?',(self.p.cid,))}
            maximum=self.p.stack_limit(dict(row),{'equipment':equipped})
            locks=records(db,self.p.cid)
            timed={i[0] for i in db.execute('SELECT e.item_id FROM cosmetic_expiry e JOIN inventory i ON i.id=e.item_id '
                                          'WHERE i.character_id=? AND e.expires<>0',(self.p.cid,))}
            glyphs={}
            if str(row['type']) in features['glyph_slots']:
                glyphs={i['item_id']:i['level'] for i in db.execute('SELECT item_id,level FROM native_glyph_item_levels '
                                                                 'WHERE character_id=?',(self.p.cid,))}
            def can_stack(other):
                return (maximum>1 and other['id']!=item_id and other['type']==row['type'] and other['id'] not in equipped
                        and locks.get(other['id'])==locks.get(item_id) and item_id not in timed and other['id'] not in timed
                        and glyphs.get(other['id'],features['local_rules']['glyph_default_level'])==
                            glyphs.get(item_id,features['local_rules']['glyph_default_level']))

            remaining=row['amount']
            automatic=slot in (-1,0)
            targets=occupied.values() if automatic else (occupied[slot],) if slot in occupied else ()
            for other in targets:
                if not can_stack(other):continue
                add=min(remaining,max(0,maximum-other['amount']))
                if add:
                    db.execute('UPDATE inventory SET amount=amount+? WHERE id=?',(add,other['id']))
                    changed.add(other['id'])
                    remaining-=add
                if not remaining:break

            if not remaining:
                db.execute('DELETE FROM inventory WHERE id=?',(item_id,))
            else:
                if automatic:
                    slot=next((n for n in range(1,STORAGE_CAPACITY+1) if n not in occupied),None)
                other=occupied.get(slot)
                if other and other['id']==item_id:
                    return None
                if other and not can_stack(other):
                    if row['page'] not in (*active,STORAGE_PAGE):
                        raise ValueError('到期道具栏只能取出物品。')
                    db.execute('UPDATE inventory SET page=-1,slot=-1 WHERE id=?',(item_id,))
                    db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(row['page'],row['slot'],other['id']))
                    changed.add(other['id'])
                if slot is None or other and can_stack(other):
                    if remaining==row['amount']:
                        raise ValueError('道具栏空间不足。' if direction==1 else '仓库空间不足。')
                    slot=row['slot']
                    target=row['page']
                db.execute('UPDATE inventory SET amount=?,page=?,slot=? WHERE id=?',(remaining,target,slot,item_id))
        return self.g.defer_packets(lambda:self.p.item_packets(item_ids=changed))

    def storage_permission(self,nid,page_type,one_key=False):
        self._session('storage',nid)
        if page_type!=1:raise ValueError('仓库类型无效。')
        return packet(3815 if one_key else 3235,[],[])

    def storage_search(self, bag_type, item_types):
        from native946_mall_bags import pages
        from native946_progression import lua_data

        if bag_type not in (1,2):raise ValueError('道具搜索栏类型无效。')
        warehouse_open=False
        if bag_type==2:
            self._session('storage')
            warehouse_open=True
        wanted=set(item_types)
        result={}
        with self.p.transaction(write=False) as db:
            bag=pages(db,self.p.cid,True)
            rows=[item for item in self.p.snapshot()['items'] if item['page'] in bag and item['type'] in wanted]
            if warehouse_open:
                rows.extend(item for item in warehouse_rows(db,self.p.cid) if item['type'] in wanted)
            for item in self.p.item_records(db,rows):
                page=item['page']
                in_warehouse=page==NATIVE_STORAGE_PAGE
                label='仓库1' if in_warehouse else f'物品栏{bag.index(page)+1}'
                tabs=result.setdefault(2 if in_warehouse else 1,{})
                grids=tabs.setdefault(page,dict(Name=label,Grids={}))['Grids']
                grids[item['grid']]=dict(Id=item['id'],Type=item['type'],Amount=item['amount'],NoTrade=1-item['can_trade'])
        return packet(4511,['number','string'],[bag_type,lua_data(result)])

    def storage_search_open(self, bag_type, page):
        from native946_mall_bags import pages

        if bag_type not in (1,2):raise ValueError('道具搜索栏类型无效。')
        locate=packet(4510,['number','number'],[bag_type,page])
        if page==NATIVE_STORAGE_PAGE:
            session=self._session('storage')
            return (*self.storage_view(session['npc']),locate)
        with self.p.transaction(write=False) as db:
            if page not in pages(db,self.p.cid,True):raise ValueError('道具搜索目标页未开启。')
        return locate
