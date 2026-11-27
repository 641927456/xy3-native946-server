"""Mall event boxes using original reward tables and explicit local odds."""

import json
from secrets import choice, randbelow
from time import time

from native946_codec import text_notice
from native946_equipment import DATA as EQUIPMENT

ANNIVERSARY_BOX = 46577
ANNIVERSARY_KEYS = (55613,65613)
REGISTRATION = 46578
DREAM_STONE = 6183
DREAM_STONE_PERCENT = 5
RARE_BOX_WEIGHTS = ((46525,1),(47575,80),(47580,50),(47692,120),(47576,749))
# The 946 box lists classes. Historical published rates are used for the
# overlapping classes. The remaining mass is split locally across materials.
BEAST_BOX_WEIGHTS = ((46525,130),(45662,66000),(0,12000),(6214,92000),
                     (6723,50000),(46809,30000),(6035,349870),(46980,400000))

# Names, icons and stack sizes from 946 info_item.lua, amounts from the
# 2015-08-12 maintenance notice. These rewards remain nontradeable.
ANNIVERSARY_REWARDS = {
    REGISTRATION: (1,'云端之光资料片纪念套卡登记函',2067,1,'纪念套卡实物登记已于2015年9月2日截止。'),
    46572: (3,'灵宝幻神卡',2034,99,'右键变身为灵宝幻神。'),
    46570: (3,'女娲元灵卡',2034,99,'右键变身为女娲元灵。'),
    46574: (5,'境悟重生',7512,99,'用于守护化境属性重悟。'),
    46544: (5,'仙器精魂',7696,999,'用于仙器炼化。'),
    46212: (5,'梵天孔雀圣羽',7443,100,'用于时装雕饰。'),
    46546: (10,'炼化图鉴',7698,99,'用于神兵、仙器和圣武炼化。'),
    46549: (10,'濯阳露',7700,99,'用于装备重铸。'),
}
ITEMS = {str(kind):dict(name=name,icon=icon,max_stack=stack,desc=desc)
         for kind,(_,name,icon,stack,desc) in ANNIVERSARY_REWARDS.items()}
ITEMS[str(DREAM_STONE)] = dict(name='悔梦石',icon=2082,max_stack=1,
                             desc='用于变更仙器种类或重铸部分境装备。')
ITEMS.update({
    '47580': dict(name='随机一级神兵戒指',icon=6666,max_stack=1,desc='可获得随机一件一级神兵戒指。'),
    '47692': dict(name='附魂天珠',icon=8344,max_stack=1,desc='用于对天品境装备附魂。'),
    '47576': dict(name='金乌灵石',icon=8223,max_stack=10,desc='用于境装备套装点化。'),
    '47100': dict(name='兽元丹',icon=5235,max_stack=99999,desc='对守护使用，获得200000经验。'),
    '45662': dict(name='开卷有益',icon=2311,max_stack=10,desc='可开出三界谱收录用书籍。'),
    '6723': dict(name='圣珠之白虎',icon=6855,max_stack=5000,desc='可用于圣珠兑换。'),
})
BEAST_EQUIPMENT = tuple(row for row in EQUIPMENT['rare_equipment'].values() if row['grade']==1)
for _kind in (6214,*(row['native_type'] for row in BEAST_EQUIPMENT)):
    ITEMS[str(_kind)] = EQUIPMENT['reward_metadata'][str(_kind)]
RULES = {str(kind):dict(op='anniversary_box',local_rule='five_percent_dream_stone',
                       dependent_contents='实物登记已截止。部分产物仍依赖各自的原版玩法。')
         for kind in (ANNIVERSARY_BOX,*ANNIVERSARY_KEYS)}
RULES[str(REGISTRATION)] = dict(op='expired_anniversary_registration')
for kind in (70632,80632):
    RULES[str(kind)] = dict(op='rare_guardian_box',local_rule='rare_box_local_weights',
                           dependent_contents='神兵戒指的分发、境装备附魂及套装点化仍待各自系统。')
for kind,uses in ((70482,1),(80482,1),(70483,20),(80483,20)):
    RULES[str(kind)] = dict(op='beast_guardian_box',uses=uses,local_rule='beast_box_local_pool',
                           dependent_contents='开卷有益的三界谱收录依赖三界谱系统。')
for kind in (55519,65519):
    RULES[str(kind)] = dict(
        op='random_bag',local_rule='uniform_thanksgiving_rewards',
        guaranteed=[[55174,1]],
        candidates=[[[reward,1]] for reward in (55228,55497,55248,55406,55371,55322,55140,55141,55142)],
        draws=2,
    )


def open_anniversary(game, db, item, cosmetics):
    from native946_inventory_locks import require_unlocked
    from native946_mall_checkout import rows
    from native946_mall_uses import consume

    counterpart = ANNIVERSARY_KEYS if item['type']==ANNIVERSARY_BOX else (ANNIVERSARY_BOX,)
    required = None
    for row in rows(db,game.progression.cid,counterpart):
        try:
            require_unlocked(db,game.progression.cid,row['id'])
        except ValueError:
            continue
        required = row
        break
    if required is None:
        raise ValueError('开启需要一件有效且未锁定的周年礼箱和对应钥匙。')
    consume(db,item)
    consume(db,required)
    rewards = {}
    for kind,(amount,*_) in ANNIVERSARY_REWARDS.items():
        cosmetics._add(db,kind,amount,backpack=True)
        rewards[kind] = amount
    # Allocate the optional reward before rolling, so a full bag cannot be
    # used to filter which of the two outcomes is allowed to commit.
    optional = cosmetics._add(db,DREAM_STONE,backpack=True)
    if randbelow(100)<DREAM_STONE_PERCENT:
        rewards[DREAM_STONE]=1
        message='周年礼箱已打开，另获悔梦石1枚。悔梦石按本机5%掉落。'
    else:
        db.execute('DELETE FROM inventory WHERE id=?',(optional,))
        message='周年礼箱已打开。悔梦石按本机5%掉落。'
    db.execute('INSERT INTO cosmetic_use_receipts(character_id,item_id,type,rewards,created) VALUES(?,?,?,?,?)',
               (game.progression.cid,item['id'],item['type'],json.dumps(rewards),int(time())))
    return game.defer_packets(lambda:(*game.progression.item_packets(),text_notice(message)))


def open_rare_box(game, db, item, cosmetics):
    from native946_guardian_acquisition import GuardianAcquisition, ITEMS as GUARDIAN_ITEMS
    from native946_mall_uses import consume

    kind=pick(RARE_BOX_WEIGHTS)
    # Each candidate needs at most the one bag slot freed by this one-use box.
    consume(db,item)
    acquisition=GuardianAcquisition(game)
    if kind in (46525,47575):
        acquisition._give_item(db,kind)
        name=GUARDIAN_ITEMS[str(kind)]['name']
    else:
        cosmetics._add(db,kind,backpack=True)
        name=ITEMS[str(kind)]['name']
    acquisition._receipt(db,item,dict(op='rare_guardian_box',reward=kind,count=1,local_rule=True))
    return game.defer_packets(lambda:(*game.progression.item_packets(),text_notice('本机奇珍宝箱开奖：'+name+'。')))


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS cosmetic_beast_box_remaining (
        item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
        uses INTEGER NOT NULL CHECK(uses>0)
    )''')


def remaining_uses(db,item):
    rule=RULES[str(item['type'])]
    row=db.execute('SELECT uses FROM cosmetic_beast_box_remaining WHERE item_id=?',(item['id'],)).fetchone()
    return row[0] if row else rule['uses']


def pick(weights):
    roll=randbelow(sum(weight for _,weight in weights))
    for kind,weight in weights:
        if roll<weight:
            return kind
        roll-=weight
    raise ValueError('宝箱权重配置无效。')


def open_beast_box(game, db, item, cosmetics):
    from native946_equipment import persist
    from native946_guardian_acquisition import GuardianAcquisition
    from native946_inventory_locks import require_unlocked
    from native946_mall_bags import pages
    from native946_mall_uses import consume
    from native946_progression import DATA as PROGRESSION

    cid=game.progression.cid
    require_unlocked(db,cid,item['id'])
    left=remaining_uses(db,item)-1
    if left:
        db.execute('INSERT INTO cosmetic_beast_box_remaining VALUES(?,?) '
                   'ON CONFLICT(item_id) DO UPDATE SET uses=excluded.uses',(item['id'],left))
    else:
        consume(db,item)
    cosmetics._add(db,47100,30,backpack=True)
    occupied={(row[0],row[1]) for row in db.execute('SELECT page,slot FROM inventory WHERE character_id=?',(cid,))}
    if not any((page,slot) not in occupied for page in pages(db,cid) for slot in range(1,25)):
        raise ValueError('请为珍兽宝箱的额外奖品留一个空格。')

    kind=pick(BEAST_BOX_WEIGHTS)
    acquisition=GuardianAcquisition(game)
    if kind==0:
        profile=choice(BEAST_EQUIPMENT)
        kind=profile['native_type']
        reward=cosmetics._add(db,kind,backpack=True)
        persist(db,cid,reward,profile,profile['fixed_properties'])
    elif kind in (46525,46809,6035,46980):
        acquisition._give_item(db,kind)
    else:
        cosmetics._add(db,kind,backpack=True)
    name=PROGRESSION['items'][str(kind)]['name']
    acquisition._receipt(db,item,dict(op='beast_guardian_box',rewards={47100:30,kind:1},remaining=left,local_rule=True))
    return game.defer_packets(lambda:(*game.progression.item_packets(),
        text_notice(f'本机珍兽箱开出兽元丹30个和{name}1份，剩余{left}次。')))
