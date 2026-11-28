"""Daily content data and the 946 Three Realms Token exchange panels."""
import json
from pathlib import Path

from native946_codec import text_notice


DATA = json.loads(Path(__file__).with_name('native946_daily_content_data.json').read_text('utf8'))
ITEMS = DATA['items']
EXCHANGE=DATA['sanjie_exchange']


def exchange_balance(db,cid):
    row=db.execute('SELECT sanjie FROM native_npc_quest_wallet WHERE character_id=?',(cid,)).fetchone()
    return row[0] if row else 0


def exchange_goods(scene,nid,race):
    offered=EXCHANGE['npcs'].get(f'{scene}:{nid}')
    if offered is None:return None
    return [kind for kind in offered if
            EXCHANGE['goods'][str(kind)].get('equipment',{}).get('native_race',race)==race]


def exchange_menu(game,npc):
    nid=npc['npc_id']
    goods=exchange_goods(npc['scene_id'],nid,game.character['race'])
    if goods is None:return None
    with game.progression.transaction(write=False) as db:
        balance=exchange_balance(db,game.progression.cid)
    game.dialog_actions=[dict(kind='sanjie_exchange',item=kind) for kind in goods]
    game.dialog_actions.append(dict(kind='close_dialogue'))
    labels=[f'{ITEMS[str(kind)]["name"]}（{EXCHANGE["goods"][str(kind)]["price"]}枚三界令）' for kind in goods]
    return game.menu(f'你有{balance}枚三界令。请选择要兑换的物品。',[*labels,'离开'],nid)


def exchange(game,nid,kind):
    from native946_equipment import persist
    from native946_inventory_features import give
    from native946_npc_services import NpcServices

    npc=NpcServices(game).validate(nid)
    if kind not in (exchange_goods(npc['scene_id'],nid,game.character['race']) or ()):
        raise ValueError('此处没有该三界令商品。')
    progression=game.progression
    good=EXCHANGE['goods'][str(kind)]
    cost=good['price']
    with progression.transaction() as db:
        if not db.execute('UPDATE native_npc_quest_wallet SET sanjie=sanjie-? '
                          'WHERE character_id=? AND sanjie>=?',(cost,progression.cid,cost)).rowcount:
            raise ValueError('三界令不足。')
        profile=good.get('equipment')
        if profile is None:
            give(db,progression,kind)
        else:
            page,slot=progression.free_bag_slot(progression._snapshot(db))
            iid=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                           (progression.cid,kind,page,slot)).lastrowid
            persist(db,progression.cid,iid,profile,profile['properties'])
            db.execute('UPDATE native_equipment SET rule_version=? WHERE item_id=?',(EXCHANGE['version'],iid))
        balance=exchange_balance(db,progression.cid)
    progression._cached_snapshot=None
    name=ITEMS[str(kind)]['name']
    return game.defer_packets(lambda:(game.close_npc_dialogue(),*progression.item_packets(),
                                      text_notice(f'兑换获得{name}，花费{cost}枚三界令，剩余{balance}枚。')))
