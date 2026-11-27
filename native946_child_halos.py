"""Native seven-color auras stored per child with their expiry."""
from time import monotonic, time

from native946_role_systems import DATA, require, wire

RULES = {
    str(kind): {'op': 'child_halo', 'aura': int(aura)}
    for aura, entry in DATA['baby_aura'].items()
    for kind in entry['item_list']
}


def licenses(child):
    if 'halo_licenses' in child:
        return child['halo_licenses']
    active = child.get('halo', 0)
    return {str(active): 0} if active else {}


def active_halo(child, now=None):
    active = child.get('halo', 0)
    expires = licenses(child).get(str(active))
    if expires is None or (expires and expires <= (time() if now is None else now)):
        return 0
    return active


def panel(db, cid, child):
    from native946_child_fashion import worn_model

    now = int(time())
    active = active_halo(child, now)
    rows = [
        dict(halo=int(key), expire_time=expires or -1,
             is_expired=int(bool(expires) and expires <= now), is_using=int(int(key) == active))
        for key, expires in sorted(licenses(child).items(), key=lambda row: int(row[0]))
    ]
    return wire(2456, child['id'], child['name'], '本机养育', child['type'],
                rows, worn_model(db, cid, child))


def _item(db, cid, item_type, item_id=None):
    from native946_inventory_locks import require_unlocked
    from native946_mall_bags import pages

    rows = db.execute(
        'SELECT i.*,coalesce(e.expires,0) AS expires FROM inventory i '
        'LEFT JOIN cosmetic_expiry e ON e.item_id=i.id '
        'WHERE i.character_id=? AND i.type=? AND (? IS NULL OR i.id=?) ORDER BY i.id',
        (cid, item_type, item_id, item_id),
    )
    allowed = pages(db, cid)
    now = time()
    for row in rows:
        if row['page'] not in allowed or (row['expires'] and row['expires'] <= now):
            continue
        try:
            require_unlocked(db, cid, row['id'])
        except ValueError:
            if item_id is not None:
                raise
            continue
        return row
    raise ValueError('背包中没有可用的七色光道具。')


def choose(game, db, item_type, item_id=None):
    from native946_role_society import baby_list

    cid = game.progression.cid
    require(str(item_type) in RULES, '请选择七色光道具。')
    item = _item(db, cid, item_type, item_id)
    require(db.execute('SELECT 1 FROM native_babies WHERE character_id=?', (cid,)).fetchone(),
            '请先领养孩子。')
    reply = baby_list(db, cid, item_type=item_type, choose_child=True)
    game.baby_halo_choice = (item_type, item['id'], monotonic() + 120)
    return reply


def apply(game, db, child, item_type):
    from native946_cosmetics import DATA as CATALOG
    from native946_fashion import record_use
    from native946_mall_uses import consume

    pending = getattr(game, 'baby_halo_choice', None)
    require(pending and pending[0] == item_type and pending[2] > monotonic(),
            '请重新选择要使用的七色光道具。')
    cid = game.progression.cid
    item = _item(db, cid, item_type, pending[1])
    aura = RULES[str(item_type)]['aura']
    days = CATALOG['items'][str(item_type)]['days']
    owned = licenses(child)
    existing = owned.get(str(aura))
    now = int(time())
    require(existing != 0, '该孩子已永久获得此光环。')
    require(not days or existing is None or existing <= now, '该孩子仍拥有此光环的有效期。')
    consume(db, item)
    owned[str(aura)] = now + days * 86400 if days else 0
    child['halo_licenses'] = owned
    child['halo'] = aura
    record_use(db, cid, item_type)
    game.baby_halo_choice = None


def select(child, aura):
    owned = licenses(child)
    if aura:
        expires = owned.get(str(aura))
        require(expires is not None and (not expires or expires > time()),
                '该孩子尚未获得这个光环，或光环已经到期。')
    child['halo_licenses'] = owned
    child['halo'] = aura
