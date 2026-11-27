"""Native classic-946 guardian lottery, card warehouse and point mall."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from time import monotonic,time
from secrets import randbelow,choice
import json
from native946_codec import packet
from native946_codec import text_notice

REFERENCE_WIRE_VERSION=9
DATA=json.loads(Path(__file__).with_name('native946_guardian_lottery_data.json').read_text('utf8'))
INFO=DATA['native'];POLICY=DATA['local_policy'];CONFIG=dict(INFO['LotteryConfig']);SEASON=POLICY['season']
CONFIG.update(OnePriceFirst=0,OnePrice=0,TenPrice=0)
CONFIG['HelpContent']=CONFIG['HelpContent'].replace('每日首次单抽100玉帛，之后单抽200，十连2000。',
    '本机单抽、每日首抽和十连均为0玉帛。')
ITEMS=DATA['items'];ICON=95
REQUESTS={1390,3773,3774,3775,3776,3777,3778,3810}
WRITES={3773,3774,3776,3777}
SCORES={int(k):v for k,v in INFO['SummonScoreLevel'].items()}
POOLS=INFO['SummonPool'];SHOP={int(k):v for k,v in INFO['ScoreShop'].items()}
DRAW_POOLS={pool.get('RequestIndex',index):(index,pool) for index,pool in enumerate(POOLS,1)}
if len(DRAW_POOLS)!=len(POOLS):raise ValueError('Duplicate guardian lottery request index')
RARITY={level:sorted(k for k,v in SCORES.items() if v['Level']==level) for level in (1,2,3,4)}
EXPERIENCE_ITEM=POLICY['guaranteed_item']
SCORE_VOUCHER=48039
SCORE_VOUCHER_POINTS=8000
MAX_SCORE=2147483647


def lua(value):
    """Trusted numeric/boolean native tables; no input expressions are run."""
    if type(value) is bool:return 'true' if value else 'false'
    if type(value) is int:return str(value)
    if isinstance(value,(tuple,list)):return '{'+','.join(lua(v) for v in value)+'}'
    if isinstance(value,dict):
        rows=[]
        for k,v in value.items():
            if type(k) is int:key='['+str(k)+']'
            elif isinstance(k,str) and k.isascii() and k.isidentifier():key=k
            else:raise ValueError('Untrusted native lottery table key')
            rows.append(key+'='+lua(v))
        return '{'+','.join(rows)+'}'
    raise ValueError('Untrusted native lottery table value')


def notice(text):return text_notice(text)
def today():return datetime.now(timezone(timedelta(hours=8))).date().isoformat()
def has_table(db,name):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS native_guardian_lottery_state ('
        'character_id INTEGER NOT NULL REFERENCES characters(id),season TEXT NOT NULL,'
        'pity INTEGER NOT NULL DEFAULT 0 CHECK(pity BETWEEN 0 AND 399),total INTEGER NOT NULL DEFAULT 0 CHECK(total>=0),'
        "first_day TEXT NOT NULL DEFAULT '',score INTEGER NOT NULL DEFAULT 0 CHECK(score BETWEEN 0 AND 2147483647),"
        'flash_score INTEGER NOT NULL DEFAULT 0 CHECK(flash_score BETWEEN 0 AND 2147483647),PRIMARY KEY(character_id,season))')
    db.execute('CREATE TABLE IF NOT EXISTS native_guardian_lottery_cards ('
        'character_id INTEGER NOT NULL REFERENCES characters(id),season TEXT NOT NULL,type INTEGER NOT NULL,'
        'amount INTEGER NOT NULL CHECK(amount>=0),PRIMARY KEY(character_id,season,type))')
    db.execute('CREATE TABLE IF NOT EXISTS native_guardian_lottery_stock ('
        'season TEXT NOT NULL,type INTEGER NOT NULL,issued INTEGER NOT NULL CHECK(issued>=0),PRIMARY KEY(season,type))')
    db.execute('CREATE TABLE IF NOT EXISTS native_guardian_lottery_shop_counts ('
        'season TEXT NOT NULL,character_id INTEGER NOT NULL CHECK(character_id>=0),type INTEGER NOT NULL,'
        'amount INTEGER NOT NULL CHECK(amount>=0),PRIMARY KEY(season,character_id,type))')
    db.execute('CREATE TABLE IF NOT EXISTS native_guardian_lottery_orders ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT,character_id INTEGER NOT NULL REFERENCES characters(id),'
        'season TEXT NOT NULL,operation TEXT NOT NULL,result TEXT NOT NULL,created INTEGER NOT NULL)')
    if not has_table(db, 'native_guardian_lottery_account_points'):
        migrate_account_points(db)


def migrate_account_points(db):
    # Local policy: balances share an account per season. Draw and card state remains on the role.
    db.execute("""CREATE TABLE IF NOT EXISTS native_guardian_lottery_account_points (
        owner TEXT NOT NULL REFERENCES accounts(username), season TEXT NOT NULL,
        score INTEGER NOT NULL DEFAULT 0 CHECK(score>=0),
        flash_score INTEGER NOT NULL DEFAULT 0 CHECK(flash_score>=0),
        PRIMARY KEY(owner,season)
    )""")
    db.execute("""INSERT INTO native_guardian_lottery_account_points(owner,season,score,flash_score)
        SELECT c.owner,s.season,sum(s.score),sum(s.flash_score)
        FROM native_guardian_lottery_state s JOIN characters c ON c.id=s.character_id
        WHERE s.score<>0 OR s.flash_score<>0
        GROUP BY c.owner,s.season
        ON CONFLICT(owner,season) DO UPDATE SET score=score+excluded.score,
            flash_score=flash_score+excluded.flash_score""")
    db.execute('UPDATE native_guardian_lottery_state SET score=0,flash_score=0 '
               'WHERE (score<>0 OR flash_score<>0) AND character_id IN (SELECT id FROM characters)')


class GuardianLottery:
    def __init__(self,game):self.g=game;self.p=game.progression;self.s=game.summons

    def _state(self,db):
        state = dict(pity=0,total=0,first_day='',score=0,flash_score=0)
        if has_table(db, 'native_guardian_lottery_state'):
            row = db.execute('SELECT pity,total,first_day FROM native_guardian_lottery_state '
                             'WHERE character_id=? AND season=?', (self.p.cid,SEASON)).fetchone()
            if row:
                state.update(dict(row))
        if has_table(db, 'native_guardian_lottery_account_points'):
            wallet = db.execute('SELECT score,flash_score FROM native_guardian_lottery_account_points '
                                'WHERE owner=? AND season=?', (self.p.owner,SEASON)).fetchone()
            if wallet:
                state.update(dict(wallet))
        return state

    def _ensure(self,db):
        schema(db)
        db.execute('INSERT OR IGNORE INTO native_guardian_lottery_state(character_id,season) VALUES(?,?)',(self.p.cid,SEASON))
        db.execute('INSERT OR IGNORE INTO native_guardian_lottery_account_points(owner,season) VALUES(?,?)',
                   (self.p.owner,SEASON))

    def _cards(self,db):
        if not has_table(db,'native_guardian_lottery_cards'):return []
        return [dict(type=r['type'],amount=r['amount']) for r in db.execute(
            'SELECT type,amount FROM native_guardian_lottery_cards WHERE character_id=? AND season=? AND amount>0 ORDER BY type',(self.p.cid,SEASON))]

    def _stock(self,db):
        if not has_table(db,'native_guardian_lottery_stock'):return {}
        return {r['type']:r['issued'] for r in db.execute('SELECT type,issued FROM native_guardian_lottery_stock WHERE season=?',(SEASON,))}

    def _receipt(self,db,operation,result):
        return db.execute('INSERT INTO native_guardian_lottery_orders(character_id,season,operation,result,created) VALUES(?,?,?,?,?)',
            (self.p.cid,SEASON,operation,json.dumps(result,ensure_ascii=False),int(time()))).lastrowid

    def entry_packets(self, *, include_icon=True):
        if getattr(self.g,'guardian_lottery_icon_sent',False):return ()
        self.g.guardian_lottery_icon_sent=True
        # The shared native icon panel is replaced by the combined growth list.
        replies=[]
        if include_icon:replies.append(packet(1206,[('array','number'),('array','number'),('array','string')],[[ICON],[0],['']]))
        replies.append(packet(3809,['number'],[POLICY['end_time']]))
        return tuple(replies)

    def panel(self,*,announce=False):
        with self.p.transaction(write=False) as db:state=self._state(db);stock=self._stock(db)
        replies=(packet(3781,['number','number','string'],
            [int(state['first_day']!=today()),state['pity'],lua({k:stock.get(k,0) for k in RARITY[1]})]),)
        if announce:
            from native946_cosmetics import Cosmetics
            self.g.guardian_lottery_panel_until=monotonic()+900
            # Updating the icon runs the client's refresh callback, which
            # queries 3810 again. Never announce an icon from a panel reply.
            return (*Cosmetics(self.g).currency_packets(),notice(POLICY['note']),*replies)
        return replies

    def _ready(self):
        if self.g.battle is not None:raise ValueError('请先结束战斗，再办理守护天缘。')

    def _owned_reward_item(self,db,iid,kind):
        from native946_mall_bags import pages
        item=db.execute('SELECT i.*,coalesce(e.expires,0) expires FROM inventory i '
            'LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.id=? AND i.character_id=?',(iid,self.p.cid)).fetchone()
        if not item or item['type']!=kind or item['page'] not in pages(db,self.p.cid) or item['expires'] and item['expires']<=time():
            raise ValueError('背包中没有有效的'+ITEMS[str(kind)]['name']+'。')
        return item

    def use_score_voucher(self,iid):
        self._ready()
        from native946_mall_uses import consume
        with self.p.transaction() as db:
            item=self._owned_reward_item(db,iid,SCORE_VOUCHER)
            self._ensure(db)
            updated=db.execute('UPDATE native_guardian_lottery_account_points SET score=score+? '
                'WHERE owner=? AND season=? AND score<=?',
                (SCORE_VOUCHER_POINTS,self.p.owner,SEASON,MAX_SCORE-SCORE_VOUCHER_POINTS))
            if updated.rowcount!=1:raise ValueError('天缘积分已达上限，道具未消耗。')
            consume(db,item)
            balance=self._state(db)['score']
            self._receipt(db,'score_voucher',dict(item_id=iid,item_type=SCORE_VOUCHER,score=SCORE_VOUCHER_POINTS,balance=balance))
        # The native shop reply opens the window if it is closed. The next
        # shop query reads the updated balance from the same ledger.
        message=f'获得{SCORE_VOUCHER_POINTS}点天缘积分，当前余额{balance}点。'
        return self.g.defer_packets(lambda:(*self.p.item_packets(),notice(message)))

    def use_experience(self,sid,iid):
        from native946_neidan import MAX_SUMMON_EXPERIENCE
        self._ready()
        from native946_guardian_acquisition import GuardianAcquisition
        from native946_mall_uses import consume
        with self.p.transaction() as db:
            pet=self.s.get(sid)
            # _owned_item only accepts acquisition consumables, so inspect
            # this XP reward separately with the same bag/expiry/lock checks.
            from native946_inventory_locks import require_unlocked
            item=self._owned_reward_item(db,iid,EXPERIENCE_ITEM)
            require_unlocked(db,self.p.cid,iid)
            if pet['experience']+500000000>MAX_SUMMON_EXPERIENCE:raise ValueError('请先消化当前守护经验，再使用百年元婴丹；道具未消耗。')
            consume(db,item)
            self.s.add_experience(db,sid,500000000)
            GuardianAcquisition(self.g)._receipt(db,item,dict(op='guardian_lottery_xp',summon_id=sid,experience=500000000))
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.s.info_packets(sid),*self.s.sync(),notice('百年元婴丹已使用，守护获得500000000经验。')))

    def _after_draw(self,result):
        from native946_cosmetics import Cosmetics
        return (*Cosmetics(self.g).currency_packets(),*self.p.item_packets(),*self.panel(),
            packet(3770,['number','string','number'],[0,lua(result['cards']),0]))

    def draw(self,amount,pool_index,first):
        self._ready()
        if amount not in (1,10) or first not in (0,1):raise ValueError('守护天缘抽奖参数无效。')
        selected=DRAW_POOLS.get(pool_index)
        if selected is None:raise ValueError('守护天缘卡池已调整，请完全关闭并重开客户端后再抽取。')
        index,pool=selected
        if getattr(self.g,'guardian_lottery_panel_until',0)<monotonic():return (*self.panel(announce=True),notice('请在原生界面确认当前价格后再次抽取。'))
        signature=(amount,pool_index,first);last=getattr(self.g,'guardian_lottery_last_draw',None)
        with self.p.transaction() as db:
            self._ensure(db)
            if last and last['signature']==signature and last['until']>monotonic():
                row=db.execute('SELECT result FROM native_guardian_lottery_orders WHERE id=? AND character_id=? AND season=? AND operation=?',
                    (last['receipt'],self.p.cid,SEASON,'draw')).fetchone()
                if row:
                    result=json.loads(row[0]);return self.g.defer_packets(lambda:self._after_draw(result))
            state=self._state(db);discount=int(amount==1 and state['first_day']!=today())
            if first!=discount:return (*self.panel(announce=True),notice('首抽优惠状态已更新，未扣除玉帛，请重新确认。'))
            cost=CONFIG['OnePriceFirst'] if discount else CONFIG['OnePrice'] if amount==1 else CONFIG['TenPrice']
            from native946_cosmetics import spend_currency
            owned={r['type']:r['amount'] for r in self._cards(db)};stock=self._stock(db)
            # The native preview and server use the same four per-pool lists.
            # Keep the original shared tiers as a legacy-data fallback.
            purple=pool.get('PurpleSummonTbl',RARITY[3]);green=pool.get('GreenSummonTbl',RARITY[4])
            possible=set(pool['SummonTbl'])|set(purple)|set(green)|{
                k for k in pool['FlashSummonTbl'] if stock.get(k,0)<CONFIG['FlashCardMaxNum']}
            if any(owned.get(k,0)+amount>CONFIG['MaxOverLap'] for k in possible):
                raise ValueError('卡牌仓库有候选卡将超过99张，请先兑换或分解；未扣除玉帛。')
            spend_currency(db, self.p.owner, 'yubo', cost)
            from native946_guardian_acquisition import GuardianAcquisition
            # Reserve every guaranteed reward before drawing, so a full bag
            # cannot roll back an unwanted outcome into a free reroll.
            acquisition=GuardianAcquisition(self.g)
            reward_ids=[acquisition._give_item(db,POLICY['guaranteed_item']) for _ in range(amount)]
            cards=[];pity=state['pity']
            for _ in range(amount):
                roll=randbelow(POLICY['rarity_denominator'])
                gold=pity+1>=POLICY['pity'] or roll<POLICY['rarity_weights']['gold']
                if gold:
                    flash=[k for k in pool['FlashSummonTbl'] if stock.get(k,0)<CONFIG['FlashCardMaxNum']]
                    if flash and randbelow(POLICY['flash_conversion_denominator'])<POLICY['flash_conversion_weight']:
                        kind=choice(flash);stock[kind]=stock.get(kind,0)+1
                        db.execute('INSERT INTO native_guardian_lottery_stock VALUES(?,?,1) ON CONFLICT(season,type) DO UPDATE SET issued=issued+1',(SEASON,kind))
                    else:kind=choice(pool['SummonTbl'])
                    pity=0
                else:
                    kind=choice(purple if roll<POLICY['rarity_weights']['gold']+POLICY['rarity_weights']['purple'] else green);pity+=1
                cards.append(dict(type=kind,amount=1))
                db.execute('INSERT INTO native_guardian_lottery_cards VALUES(?,?,?,1) ON CONFLICT(character_id,season,type) DO UPDATE SET amount=amount+1',
                    (self.p.cid,SEASON,kind))
            db.execute('UPDATE native_guardian_lottery_state SET pity=?,total=total+?,first_day=? WHERE character_id=? AND season=?',
                (pity,amount,today() if discount else state['first_day'],self.p.cid,SEASON))
            result=dict(cards=cards,cost_yubo=cost,pool=index,draws=amount,pity_before=state['pity'],pity_after=pity,
                guaranteed_item=POLICY['guaranteed_item'],guaranteed_item_ids=reward_ids)
            receipt=self._receipt(db,'draw',result)
            self.g.guardian_lottery_last_draw=dict(signature=signature,receipt=receipt,until=monotonic()+1)
            self.g.guardian_lottery_panel_until=monotonic()+900
        return self.g.defer_packets(lambda:self._after_draw(result))

    def bag(self):
        self._ready()
        with self.p.transaction(write=False) as db:cards=self._cards(db);state=self._state(db)
        self.g.guardian_lottery_bag=dict(types=tuple(v['type'] for v in cards),until=monotonic()+300,used=False)
        return packet(3772,['string','number','number'],[lua(cards),state['score'],state['flash_score']])

    def _bag_type(self,index):
        session=getattr(self.g,'guardian_lottery_bag',None)
        if not session or session['used'] or session['until']<monotonic() or type(index) is not int or not 1<=index<=len(session['types']):
            raise ValueError('卡牌仓库选择已失效，请重新打开“我的卡牌”。')
        return session['types'][index-1]

    def _take_cards(self,db,kind,amount):
        if db.execute('UPDATE native_guardian_lottery_cards SET amount=amount-? WHERE character_id=? AND season=? AND type=? AND amount>=?',
            (amount,self.p.cid,SEASON,kind,amount)).rowcount!=1:raise ValueError('所选卡牌数量不足；本次未消耗。')

    def redeem(self,index):
        self._ready();kind=self._bag_type(index)
        with self.p.transaction() as db:
            self._ensure(db);self._take_cards(db,kind,1)
            receipt=self._receipt(db,'redeem',dict(guardian_type=kind))
            sid=self.s.acquire(kind,'guardian-lottery:'+str(receipt))
            db.execute('UPDATE native_guardian_lottery_orders SET result=? WHERE id=?',
                (json.dumps(dict(guardian_type=kind,summon_id=sid)),receipt))
            self.g.guardian_lottery_bag['used']=True
        return self.g.defer_packets(lambda:(*self.s.sync(),*self.g.sync_summon_scene(),
            packet(1183,['string'],['dlg_summon_lottery_bag']),packet(3808,['number'],[kind]),notice('卡牌已兑换为守护。')))

    def decompose(self,raw):
        self._ready()
        from native946_lua_values import table
        data=table(raw,limit=8000,nodes=512)
        if not data or set(data)!=set(range(1,len(data)+1)) or len(data)>64:raise ValueError('卡牌分解列表无效。')
        entries=[];seen=set()
        for row in data.values():
            if not isinstance(row,dict) or set(row)!={1,2}:raise ValueError('卡牌分解条目无效。')
            index,amount=row[1],row[2]
            if type(index) is not int or type(amount) is not int or not 1<=amount<=CONFIG['MaxOverLap'] or index in seen:raise ValueError('卡牌分解数量或索引无效。')
            kind=self._bag_type(index);seen.add(index);entries.append((kind,amount))
        with self.p.transaction() as db:
            self._ensure(db);state=self._state(db)
            score=sum(SCORES[k]['Value']*n for k,n in entries);flash=sum(SCORES[k].get('FlashValue',0)*n for k,n in entries)
            if state['score']+score>MAX_SCORE or state['flash_score']+flash>MAX_SCORE:raise ValueError('守护天缘积分已达上限；卡牌未消耗。')
            for kind,amount in entries:self._take_cards(db,kind,amount)
            db.execute('UPDATE native_guardian_lottery_account_points SET score=score+?,flash_score=flash_score+? '
                       'WHERE owner=? AND season=?', (score,flash,self.p.owner,SEASON))
            self._receipt(db,'decompose',dict(cards=entries,score=score,flash_score=flash));self.g.guardian_lottery_bag['used']=True
        # The original confirmation callback immediately requests 3778 again.
        return notice('卡牌分解完成，获得'+str(score)+'天缘积分、'+str(flash)+'闪卡积分。')

    def _shop_counts(self,db,cid):
        if not has_table(db,'native_guardian_lottery_shop_counts'):return {}
        return {r['type']:r['amount'] for r in db.execute('SELECT type,amount FROM native_guardian_lottery_shop_counts WHERE season=? AND character_id=?',(SEASON,cid))}

    def shop(self):
        self._ready()
        with self.p.transaction(write=False) as db:state=self._state(db);personal=self._shop_counts(db,self.p.cid);shared=self._shop_counts(db,0)
        self.g.guardian_lottery_shop_until=monotonic()+300
        return packet(3771,['number','number','string','string'],[state['score'],state['flash_score'],lua(personal),lua(shared)])

    def buy(self,kind,amount):
        self._ready();rule=SHOP.get(kind)
        if not rule or type(amount) is not int or not 1<=amount<=99:raise ValueError('天缘积分商店物品或数量无效。')
        if getattr(self.g,'guardian_lottery_shop_until',0)<monotonic():raise ValueError('请先打开原生天缘积分商店。')
        signature=(kind,amount);last=getattr(self.g,'guardian_lottery_last_buy',None)
        with self.p.transaction() as db:
            self._ensure(db)
            if last and last['signature']==signature and last['until']>monotonic():
                return self.g.defer_packets(lambda:(notice('该笔兑换已完成，没有重复扣除积分。'),self.shop()))
            personal=self._shop_counts(db,self.p.cid);shared=self._shop_counts(db,0)
            if 'Limit' in rule and personal.get(kind,0)+amount>rule['Limit']:raise ValueError('已达到本期个人兑换上限。')
            if 'AllServerLimit' in rule and shared.get(kind,0)+amount>rule['AllServerLimit']:raise ValueError('本期全服兑换数量已满。')
            score=rule.get('Value',0)*amount;flash=rule.get('FlashValue',0)*amount
            if db.execute('UPDATE native_guardian_lottery_account_points SET score=score-?,flash_score=flash_score-? '
                'WHERE owner=? AND season=? AND score>=? AND flash_score>=?',(score,flash,self.p.owner,SEASON,score,flash)).rowcount!=1:
                raise ValueError('天缘积分或闪卡积分不足；本次未扣除。')
            from native946_guardian_acquisition import GuardianAcquisition
            acquisition=GuardianAcquisition(self.g);ids=[acquisition._give_item(db,kind) for _ in range(amount)]
            for cid in (self.p.cid,0):db.execute('INSERT INTO native_guardian_lottery_shop_counts VALUES(?,?,?,?) '
                'ON CONFLICT(season,character_id,type) DO UPDATE SET amount=amount+excluded.amount',(SEASON,cid,kind,amount))
            self._receipt(db,'shop',dict(item_type=kind,amount=amount,item_ids=ids,score=score,flash_score=flash))
            self.g.guardian_lottery_last_buy=dict(signature=signature,until=monotonic()+1)
        return self.g.defer_packets(lambda:(*self.p.item_packets(),self.shop(),notice('天缘积分兑换成功。')))

    def receive(self,mid,reader):
        if mid==1390:
            icon=reader.integer();reader.finish()
            if icon!=ICON:raise ValueError('该请求不是守护天缘入口。')
            self._ready();return self.panel(announce=True)
        if mid==3810:
            # Background icon refresh only updates its countdown. Opening a
            # panel here creates an icon-query/open/refresh loop and makes
            # the original close button immediately reopen the same panel.
            reader.finish()
            return packet(3809,['number'],[POLICY['end_time']])
        if mid==3773:
            amount,pool,first=reader.integer(),reader.integer(),reader.integer();reader.finish();return self.draw(amount,pool,first)
        if mid==3774:
            kind,amount=reader.integer(),reader.integer();reader.finish();return self.buy(kind,amount)
        if mid==3775:reader.finish();return self.shop()
        if mid==3776:index=reader.integer();reader.finish();return self.redeem(index)
        if mid==3777:raw=reader.string().decode('gb18030');reader.finish();return self.decompose(raw)
        if mid==3778:reader.finish();return self.bag()
        raise ValueError('守护天缘协议无效。')
