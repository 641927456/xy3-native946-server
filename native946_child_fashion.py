"""Native baby-fashion selection and the two wardrobe views."""
import json
from time import time

from native946_role_systems import require, text, wire


# resource/configs.wdf info_shape records 401–406 name each base model.
SHAPE_SEX = {401: 1, 402: 2, 403: 1, 404: 1, 405: 2, 406: 2}
REQUESTS = {5261, 5263, 5264}


def is_fashion(item_type):
    from native946_mall_uses import DATA

    return DATA['rules'].get(str(item_type), {}).get('op') == 'child_fashion'


def children(db, cid):
    return [dict(json.loads(row['value']), id=row['id']) for row in db.execute(
        'SELECT id,value FROM native_babies WHERE character_id=? ORDER BY id', (cid,)
    )]


def profile(child):
    return dict(Id=child['id'], SId=child['id'], Name=child['name'],
                Type=child['type'], Level=child['grade'])


def fashions(db, cid, child):
    from native946_cosmetics import DATA as CATALOG, PAGES
    from native946_mall_uses import DATA

    result = {}
    for item_id in child.get('fashion_items', []):
        row = db.execute('SELECT type,page FROM inventory WHERE id=? AND character_id=?',
                         (item_id, cid)).fetchone()
        if row is None or row['page'] not in PAGES:
            continue
        key = str(row['type'])
        rule = DATA['rules'].get(key, {})
        if rule.get('op') != 'child_fashion':
            continue
        result[str(item_id)] = dict(
            Id=item_id, SId=str(item_id), ItemType=row['type'], Name=CATALOG['items'][key]['name'],
            IsWearing=int(child.get('fashion_worn') == item_id and
                          SHAPE_SEX.get(child['type']) == rule['sex'])
        )
    return result


def worn_model(db, cid, child):
    from native946_mall_uses import DATA

    value = fashions(db, cid, child).get(str(child.get('fashion_worn')))
    return DATA['rules'][str(value['ItemType'])]['model'] if value and value['IsWearing'] else 0


def closet(db, cid):
    rows = [dict(profile(child), Fashions=fashions(db, cid, child)) for child in children(db, cid)]
    return wire(5265, text(rows))


def panel(db, cid, child):
    from native946_mall_uses import DATA

    entries = fashions(db, cid, child)
    worn = next((entry for entry in entries.values() if entry['IsWearing']), None)
    model = DATA['rules'][str(worn['ItemType'])]['model'] if worn else 0
    return wire(5262, child['id'], str(child['id']), child['name'], worn['SId'] if worn else '',
                text(entries), '本机养育', child['type'], model)


def select_child(db, cid, item, rule):
    from native946_inventory_locks import require_unlocked

    require_unlocked(db, cid, item['id'])
    eligible = [profile(child) for child in children(db, cid)
                if SHAPE_SEX.get(child['type']) == rule['sex']]
    require(eligible, '没有符合这件时装性别要求的孩子。')
    return wire(5260, item['id'], text(eligible), item['type'])


def bind(db, cid, item_id, child, item_type):
    from native946_cosmetics import PAGES
    from native946_fashion import record_use
    from native946_inventory_locks import require_unlocked
    from native946_mall_bags import pages as bag_pages
    from native946_mall_uses import DATA

    item = db.execute('SELECT i.*,coalesce(e.expires,0) AS expires FROM inventory i '
                      'LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.id=? AND i.character_id=?',
                      (item_id, cid)).fetchone()
    rule = DATA['rules'].get(str(item_type), {})
    require(item and item['type'] == item_type and rule.get('op') == 'child_fashion'
            and item['page'] in bag_pages(db, cid), '请选择背包中尚未绑定的孩子时装。')
    require(item['amount'] == 1 and (not item['expires'] or item['expires'] > time()),
            '孩子时装已到期或数量无效。')
    require_unlocked(db, cid, item_id)
    require(SHAPE_SEX.get(child['type']) == rule['sex'], '时装与孩子性别不符。')
    owned = fashions(db, cid, child)
    require(all(DATA['rules'][str(entry['ItemType'])]['model'] != rule['model']
                for entry in owned.values()), '这个孩子已绑定同款时装。')
    occupied = {(row['page'], row['slot']) for row in db.execute(
        'SELECT page,slot FROM inventory WHERE character_id=?', (cid,))}
    place = next(((page, slot) for page in PAGES for slot in range(1, 25)
                  if (page, slot) not in occupied), None)
    require(place, '异宝匣已满，请腾出一个格子。')
    db.execute('UPDATE inventory SET page=?,slot=? WHERE id=? AND character_id=?', (*place, item_id, cid))
    child['fashion_items'] = [entry['Id'] for entry in owned.values()] + [item_id]
    child['fashion_worn'] = item_id
    record_use(db, cid, item_type)


def receive(game, db, mid, args):
    from native946_mall_uses import DATA
    from native946_role_society import baby, baby_buttons, save_baby

    cid = game.progression.cid
    if mid == 5261:
        item_id, baby_id, item_type = args
        child = baby(db, cid, baby_id)
        bind(db, cid, item_id, child, item_type)
    else:
        child = baby(db, cid, args[0])
        require(args[-1] in (0, 1), '孩子时装界面来源无效。')
        if mid == 5263:
            entry = fashions(db, cid, child).get(args[1])
            require(entry, '这件时装未绑定到所选孩子。')
            rule = DATA['rules'][str(entry['ItemType'])]
            require(SHAPE_SEX.get(child['type']) == rule['sex'], '时装与孩子性别不符。')
            child['fashion_worn'] = entry['Id']
        else:
            child['fashion_worn'] = 0
    save_baby(db, cid, child)
    result = [baby_buttons(db, cid, child)]
    if mid != 5261 and args[-1] == 0:
        result.append(panel(db, cid, child))
    result.append(closet(db, cid))
    return result
