"""Local additions using 946 NPC dialogue, inventory and battle entry points."""
import json
from pathlib import Path

from native946_codec import packet, text_notice


DATA = json.loads(Path(__file__).with_name('native946_daily_content_data.json').read_text('utf8'))
ITEMS = DATA['items']
EXCHANGE = DATA['sanjie_exchange']


def exchange_balance(db, cid):
    row = db.execute('SELECT sanjie FROM native_npc_quest_wallet WHERE character_id=?', (cid,)).fetchone()
    return row[0] if row else 0


def exchange_goods(scene, nid):
    return EXCHANGE['npcs'].get(f'{scene}:{nid}')


def exchange_menu(game, npc):
    goods = exchange_goods(npc['scene_id'], npc['npc_id'])
    if goods is None:
        return None
    with game.progression.transaction(write=False) as db:
        balance = exchange_balance(db, game.progression.cid)
    game.dialog_actions = [dict(kind='sanjie_exchange', item=kind) for kind in goods]
    labels = []
    for kind in goods:
        good = EXCHANGE['goods'][str(kind)]
        source = '本机' if good['price_source'] == '本机规则' else '阿飞'
        labels.append(f"{ITEMS[str(kind)]['name']}（{good['price']}积分，{source}）")
    text = f'三界令兑换。你有{balance}积分。#r商品采用946物品编号。兑换清单和价格采用阿飞参考实现及本机规则。'
    if not goods:
        text += '#r本机尚未开放高级兑换，请到长安找吕毅。'
    return game.menu(text, labels, npc['npc_id'])


def exchange(game, nid, kind):
    from native946_equipment import persist
    from native946_inventory_features import give
    from native946_npc_services import NpcServices

    npc = NpcServices(game).validate(nid)
    if kind not in (exchange_goods(npc['scene_id'], nid) or ()):
        raise ValueError('此处没有该三界令商品。')
    progression = game.progression
    good = EXCHANGE['goods'][str(kind)]
    cost = good['price']
    with progression.transaction() as db:
        if not db.execute('UPDATE native_npc_quest_wallet SET sanjie=sanjie-? '
                          'WHERE character_id=? AND sanjie>=?', (cost, progression.cid, cost)).rowcount:
            raise ValueError('三界令积分不足。')
        profile = good.get('equipment')
        if profile is None:
            give(db, progression, kind)
        else:
            page, slot = progression.free_bag_slot(progression._snapshot(db))
            iid = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                             (progression.cid, kind, page, slot)).lastrowid
            persist(db, progression.cid, iid, profile, profile['properties'])
            db.execute('UPDATE native_equipment SET rule_version=? WHERE item_id=?', (EXCHANGE['version'], iid))
        balance = exchange_balance(db, progression.cid)
    progression._cached_snapshot = None
    name = ITEMS[str(kind)]['name']
    return game.defer_packets(lambda: (packet(1183, ['string'], ['dlg_npc_chat']), *progression.item_packets(),
                                       text_notice(f'兑换获得{name}，花费{cost}积分，剩余{balance}积分。')))
