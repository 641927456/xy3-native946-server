"""Treasure map instances awarded by the existing NPC quest system."""
from functools import lru_cache
from random import Random

from native946_codec import packet, text_notice
from native946_daily_content import DATA
from native946_afei_expansion import DATA as AFEI
from native946_inventory_features import consume, give, owned
from native946_npc_quests import spawn_position
from native946_world import world


RULE = DATA['treasure']
ITEM = RULE['item']
GIVER = tuple(RULE['giver'])
ADVANCED = AFEI['advanced_treasure']
SUPER = RULE['super']
MAP_TYPES = frozenset((ITEM, ADVANCED['item'], SUPER['item']))


@lru_cache(maxsize=256)
def destination(iid):
    # The destination belongs to the durable inventory instance, including
    # after relogging, moving the item to storage, or rolling back a dig.
    rng = Random(f"ordinary-map-v1:{iid}")
    scene = rng.choice(RULE['maps'])
    return (scene, *spawn_position(scene, rng))


def location(iid):
    scene, x, y = destination(iid)
    return f'宝藏地点：{world().scene(scene)["scene_name"]}（{x}，{y}）。'


def dig(game, iid):
    if game.battle:
        raise ValueError('请先结束战斗再挖宝。')
    p = game.progression
    scene, x, y = destination(iid)
    role = game.character
    with p.transaction() as db:
        item = owned(db, p.cid, iid)
        if item['type'] not in MAP_TYPES or item['amount'] != 1:
            raise ValueError('这张藏宝图的物品状态无效。')
        distance_squared = (x - role['x']) ** 2 + (y - role['y']) ** 2
        if role['map_id'] != scene or distance_squared > RULE['radius'] ** 2:
            return text_notice(location(iid) + '请在目标附近再次右键使用。')
        advanced = item['type'] == ADVANCED['item']
        superior = item['type'] == SUPER['item']
        table = SUPER if superior else ADVANCED if advanced else RULE
        drops = table['drops']
        seed = 'super-dig-v1' if superior else 'advanced-dig-v1' if advanced else 'ordinary-dig-v1'
        kind = Random(f'{seed}:{iid}').choice(drops)
        consume(db, p.cid, iid)
        give(db, p, kind)
        cash = table.get('cash',0)
        if cash:
            db.execute('UPDATE characters SET gold=gold+? WHERE id=?', (cash, p.cid))
    p._cached_snapshot = None
    name = (AFEI if advanced else DATA)['items'][str(kind)]['name']
    text = f'挖到{name}。四级灵石奖池参考阿飞。' if advanced else f'挖到{name}和{cash}现金。奖励为本服规则。'
    return game.defer_packets(lambda: (*p.item_packets(), *p.attribute_packets(), text_notice(text)))


def exchange_menu(game, npc):
    if (npc['scene_id'], npc['npc_id']) != tuple(ADVANCED['giver']):
        return None
    count = ADVANCED['ordinary_cost']
    game.dialog_actions = [dict(kind='advanced_treasure'), dict(kind='advanced_treasure', superior=True)]
    return game.menu(f"本服规则。{count}张普通藏宝图换1张高级藏宝图，{SUPER['advanced_cost']}张高级图换1张超级图。",
                     ['兑换高级藏宝图','兑换超级藏宝图'], npc['npc_id'])


def exchange_advanced(game, nid, *, superior=False):
    from native946_inventory_locks import records
    from native946_mall_bags import pages
    from native946_npc_services import NpcServices

    npc = NpcServices(game).validate(nid)
    if (npc['scene_id'], nid) != tuple(ADVANCED['giver']):
        raise ValueError('请到江湖百晓生处兑换。')
    p = game.progression
    with p.transaction() as db:
        available = pages(db, p.cid)
        locked = records(db, p.cid)
        source, source_name, count, result = ((ADVANCED['item'],'高级藏宝图',SUPER['advanced_cost'],SUPER['item'])
                                             if superior else (ITEM,'普通藏宝图',ADVANCED['ordinary_cost'],ADVANCED['item']))
        maps = [row['id'] for row in db.execute(
            'SELECT id,page,amount FROM inventory WHERE character_id=? AND type=? ORDER BY id', (p.cid, source))
            if row['page'] in available and row['id'] not in locked and row['amount'] == 1][:count]
        if len(maps) != count:
            raise ValueError(f'需要{count}张可用的{source_name}。')
        for iid in maps:
            consume(db, p.cid, iid)
        give(db, p, result)
    p._cached_snapshot = None
    name = '超级藏宝图' if superior else '高级藏宝图'
    return game.defer_packets(lambda: (packet(1183, ['string'], ['dlg_npc_chat']), *p.item_packets(), text_notice(f'已兑换{name}。')))
