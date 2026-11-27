"""Player-to-player gifts through the original 946 give panel."""

from secrets import randbelow
from time import monotonic
from weakref import ref

from native946_codec import packet, text_notice
from native946_mail import blocked
from native946_role_systems import save, state
from native946_storage import transaction
from native946_trade import MAX_CASH, Trade

REQUESTS = frozenset((629, 630, 631, 632, 1490))
ITEM_SLOTS = 3
GIVE_DISTANCE = 20  # Reference server checks 400 pixels, 946 uses 20 pixels per cell.
SESSION_SECONDS = 300
# Field order in common/protocol/tbl/sys_cfg.tbl.
SETTINGS_FIELDS = (
    'showskillshortcut', 'showhalo', 'friend', 'view_remarksname', 'autoplaynext',
    'showitemshortcut', 'show2ndweapon', 'team', 'teamplatformremind', 'teamscheme',
    'show_buff_frame', 'pk', 'child', 'show3d', 'preferownhalo', 'viewzixia',
    'autoplaysong', 'reject', 'diffequipshow', 'trade', 'mail', 'newequipshow',
    'tasktrace', 'give',
)


def saved_settings(game):
    with transaction(game.characters.database) as db:
        settings = state(db, game.character['id'], 'system_settings')
    if not settings:
        return ()
    return (packet(1318, ['number'] * len(SETTINGS_FIELDS), [settings[name] for name in SETTINGS_FIELDS]),)


def require_accepting(db, sender, target):
    if not state(db, target, 'system_settings', {'give': 1})['give'] or blocked(db, target, sender):
        raise ValueError('对方当前无法接收你的给予。')


def peer_for(service, game, target):
    peer = service.online(target)
    if peer is game:
        raise ValueError('不能给予自己。')
    if peer is None or game.battle or peer.battle:
        raise ValueError('双方须在线并处于非战斗状态。')
    source, destination = game.character, peer.character
    distance = sum((source[axis] - destination[axis]) ** 2 for axis in ('x', 'y'))
    if source['map_id'] != destination['map_id'] or distance > GIVE_DISTANCE ** 2:
        raise ValueError('请走近对方再给予。')
    return peer


def receive(service, game, mid, reader):
    cid = game.character['id']
    if mid == 1490:
        settings = {name: reader.integer() for name in SETTINGS_FIELDS}
        reader.finish()
        if settings['give'] not in (0, 1):
            raise ValueError('接受给予选项无效。')
        with transaction(service.database, write=True) as db:
            save(db, cid, 'system_settings', settings)
        return ()
    if mid == 632:
        reader.finish()
        game._give_session = None
        return ()

    target = reader.integer()
    if mid == 630:
        reader.finish()
        peer = peer_for(service, game, target)
        with transaction(service.database) as db:
            require_accepting(db, cid, target)
        token = randbelow((1 << 31) - 1) + 1
        game._give_session = token, target, ref(peer), monotonic() + SESSION_SECONDS
        return packet(628, ['number'] * 6 + ['string'],
                      [target, 1, 0, 1, ITEM_SLOTS, token, '给予'])

    cash = reader.integer()
    selected = []
    if mid == 629:
        count = reader.take(1)[0]
        if count > ITEM_SLOTS:
            raise ValueError('一次最多给予三格物品。')
        selected = [(reader.integer(), reader.integer()) for _ in range(count)]
    else:
        reader.integer()
    token = reader.integer()
    reader.finish()

    session = getattr(game, '_give_session', None)
    if not session or session[:2] != (token, target) or session[3] <= monotonic():
        raise ValueError('给予窗口已失效，请再次选择对方。')
    # The client's confirm action closes the panel. Each panel can submit once.
    game._give_session = None
    peer = peer_for(service, game, target)
    if peer is not session[2]():
        raise ValueError('对方已重新连接，请再次打开给予窗口。')
    if mid == 631:
        raise ValueError('当前本机守护均未开放交易，无法给予。')
    if not 0 <= cash <= MAX_CASH or not (cash or selected):
        raise ValueError('请选择要给予的物品或输入有效现金。')
    if len({iid for iid, _ in selected}) != len(selected):
        raise ValueError('给予清单里有重复物品。')
    with transaction(service.database) as db:
        items = []
        for iid, amount in selected:
            row = db.execute('SELECT type FROM inventory WHERE id=? AND character_id=?', (iid, cid)).fetchone()
            if row is None:
                raise ValueError('要给予的物品已不在当前背包。')
            items.append(dict(id=iid, type=row['type'], amount=amount))
    updates = Trade(service, game).settle(peer, {cid: (cash, items), target: (0, [])}, gift=True)
    service.enqueue(peer, *updates[target], text_notice(f"收到{game.character['name']}给予的物品或现金。"))
    return (*updates[cid], text_notice(f"已将所选物品或现金给予{peer.character['name']}。"))
