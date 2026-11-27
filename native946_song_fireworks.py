"""Stock 璀璨烟花 panel and configurable local rewards for 流星落雨."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from time import time

from native946_codec import packet, text_notice
from native946_friends import BLOCKED, CUSTOM_GROUPS, NORMAL, Friends
from native946_inventory_features import consume
from native946_mall_bags import pages
from native946_nice_ids import ZH_TIME
from native946_progression import DATA
from native946_world import INSTANCE_SCENES


# The native panel uses levels 1–3 in this order (configs.wdf 83d8456a).
ITEM_TYPES = (46016, 46017, 46018)
OPEN_PANEL = 417
SEND_FIREWORK = 428
METEOR_TIER = 2
METEOR_ITEM = ITEM_TYPES[METEOR_TIER - 1]
# Native fireworks animation from the 946 风云庆典 panel and magic.wdf.
SCENE_EFFECT = 140037
CONFIG_PATH = Path(__file__).with_name('native946_song_fireworks_rules.json')
MAX_EXPERIENCE = (1 << 40) - 1
# Local claim on the existing 长安 NPC 全民抽奖使者.
CLAIM_NPC = (2001, 6733)


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_song_firework_claims (
        owner TEXT PRIMARY KEY NOT NULL REFERENCES accounts(username) ON DELETE CASCADE,
        claimed_at INTEGER NOT NULL)''')


def claim(game, npc):
    if (npc['scene_id'], npc['npc_id']) != CLAIM_NPC:
        raise ValueError('请到长安找全民抽奖使者领取。')
    now = datetime.now(ZH_TIME)
    expires = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    progress = game.progression
    with progress.transaction() as db:
        if not db.execute('INSERT INTO native_song_firework_claims VALUES(?,?) ON CONFLICT(owner) DO NOTHING',
                          (progress.owner, int(now.timestamp()))).rowcount:
            raise ValueError('本账号已经领过烟花·流星落雨。')
        page, slot = progress.free_bag_slot(progress._snapshot(db))
        item_id = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                             (progress.cid, METEOR_ITEM, page, slot)).lastrowid
        db.execute('INSERT INTO cosmetic_expiry VALUES(?,?)', (item_id, int(expires.timestamp())))
    return (packet(1183, ['string'], ['dlg_npc_chat']), *progress.item_packets(item_ids={item_id}),
            text_notice('已领取烟花·流星落雨，请在今天午夜前使用。'))


def local_rules():
    try:
        rules = json.loads(CONFIG_PATH.read_text(encoding='utf8'))
        experience = tuple(rules['experience'][role] for role in ('sender', 'friend', 'nearby'))
        radius = rules['nearby_radius_cells']
        if any(type(amount) is not int or not 1 <= amount <= MAX_EXPERIENCE for amount in experience):
            raise ValueError
        if radius is not None and (type(radius) is not int or radius < 0):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('流星落雨的本机经验或范围配置无效。') from None
    return (*experience, radius)


def open_panel(game, item_id):
    progression = game.progression
    with progression.transaction(write=False) as db:
        active_pages = pages(db, progression.cid)
        rows = [row for row in db.execute(
            'SELECT i.id,i.type,i.page,i.amount FROM inventory i '
            'LEFT JOIN cosmetic_expiry e ON e.item_id=i.id '
            'WHERE i.character_id=? AND i.type IN (?,?,?) AND (coalesce(e.expires,0)=0 OR e.expires>?) '
            'ORDER BY i.id', (progression.cid, *ITEM_TYPES, int(time())),
        ) if row['page'] in active_pages]
    clicked = next((row for row in rows if row['id'] == item_id), None)
    if clicked is None:
        raise ValueError('请从人物背包打开未到期的烟花。')
    meteors = [row for row in rows if row['type'] == METEOR_ITEM]
    amounts = [0, sum(row['amount'] for row in meteors), 0]
    reply = packet(OPEN_PANEL, [('array', 'number')], [amounts])
    # The native panel defaults to the highest available tier.
    selected = clicked if clicked['type'] == METEOR_ITEM else next(iter(meteors), None)
    game.song_firework_item = selected['id'] if selected else None
    return reply


def release(game, reader):
    tier, target = reader.integer(), reader.integer()
    wish_bytes = reader.string()
    reader.finish()
    if tier != METEOR_TIER:
        raise ValueError('本机璀璨烟花目前只开放流星落雨。')
    item_id = getattr(game, 'song_firework_item', None)
    if item_id is None:
        raise ValueError('请重新从背包打开烟花面板。')
    if game.battle:
        raise ValueError('请先完成当前战斗。')
    wish = wish_bytes.decode('gb18030').strip()
    if not wish or len(wish) > 30 or len(wish_bytes) > 60 or any(
            char == '#' or not char.isprintable() for char in wish):
        raise ValueError('请输入 30 字以内的有效祝福语。')

    sender_exp, friend_exp, nearby_exp, radius = local_rules()
    service = game.party_service
    progress = game.progression
    sender = game.character
    with progress.transaction() as db:
        friend_row = Friends(game).target(db, target, search=True)
        friend = service.online(friend_row['id'])
        if friend is game:
            raise ValueError('请选择其他玩家共赏烟花。')
        if friend is None:
            raise ValueError('共赏好友当前不在线。')
        contact = db.execute('SELECT group_id FROM native_contacts WHERE character_id=? AND target_id=?',
                             (progress.cid, friend_row['id'])).fetchone()
        if contact is None or (contact[0] != NORMAL and contact[0] not in CUSTOM_GROUPS):
            raise ValueError('请从你的好友中选择共赏对象。')
        if db.execute('SELECT 1 FROM native_contacts WHERE character_id=? AND target_id=? AND group_id=?',
                      (friend_row['id'], progress.cid, BLOCKED)).fetchone():
            raise ValueError('该好友当前无法接受共赏。')

        def same_map(peer):
            return sender['map_id'] not in INSTANCE_SCENES and peer.character['map_id'] == sender['map_id']

        receivers = {game: sender_exp, friend: friend_exp}
        for peer in tuple(service.games.values()):
            if peer in receivers or not peer._party_connected or not same_map(peer):
                continue
            distance = max(abs(peer.character[key] - sender[key]) for key in ('x', 'y'))
            if radius is None or distance <= radius:
                receivers[peer] = nearby_exp

        item = db.execute('SELECT i.type,coalesce(e.expires,0) AS expires FROM inventory i '
                          'LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.id=? AND i.character_id=?',
                          (item_id, progress.cid)).fetchone()
        if item is None or item['type'] != METEOR_ITEM:
            raise ValueError('原烟花已离开背包，请重新打开烟花面板。')
        if 0 < item['expires'] <= int(time()):
            raise ValueError('该烟花已到期。')
        consume(db, progress.cid, item_id)

        updates = {}
        for peer, amount in receivers.items():
            level, total, granted, upgraded = award(db, peer, amount)
            effect_at = sender if same_map(peer) or peer is game else peer.character
            effect = () if peer.battle else (packet(1599, ['number', 'number', ('array', 'number')],
                                                    [effect_at['x'], effect_at['y'], [SCENE_EFFECT]]),)
            if upgraded:
                attrs = peer.progression.attribute_packets()
                pets = peer.summons.sync() if peer.summons.reconcile_levels() else ()
                message = f'烟花·流星落雨为你带来 {granted} 点经验，已升至 {level} 级。'
            else:
                attrs = (packet(695, ['number', 'number'], [int(DATA['attributes']['Exp']), total]), packet(694, [], []))
                pets = ()
                message = f'烟花·流星落雨为你带来 {granted} 点经验。'
            updates[peer] = (level, total, (*effect, *attrs, *pets, text_notice(message)))
        announce = packet(416, ['number', 'string'],
                          [tier, f'{sender["name"]}邀{friend_row["name"]}共赏流星落雨：{wish}'])
        own_packets = (*progress.item_packets(item_ids={item_id}), announce, *updates[game][2])

    def deliver():
        game.song_firework_item = None
        for peer, (level, total, replies) in updates.items():
            peer.progression._cached_snapshot = None
            peer.character.update(level=level, experience=total)
            if peer is not game:
                extra = (announce,) if peer is friend and not peer.battle else ()
                service.enqueue(peer, *extra, *replies)
        game.last_event = {'action': 'song_firework_released', 'item_type': METEOR_ITEM,
                           'recipient_count': len(updates)}
        return own_packets

    return game.defer_packets(deliver, fallback=own_packets)


def award(db, peer, amount):
    character = peer.character
    parameters = (character['id'], character['owner'])
    row = db.execute('SELECT level,experience FROM characters WHERE id=? AND owner=?',
                     parameters).fetchone()
    if row is None:
        raise ValueError('参与角色的存档已变化，请重新燃放。')
    level, before = row
    if before > MAX_EXPERIENCE:
        raise ValueError('有角色的经验超出可同步范围，请检查存档。')
    granted = min(amount, MAX_EXPERIENCE - before)
    total = before + granted
    db.execute('UPDATE characters SET experience=? WHERE id=?', (total, character['id']))
    if not peer.battle and peer.progression.auto_level():
        updated = db.execute('SELECT level,experience FROM characters WHERE id=? AND owner=?', parameters).fetchone()
        return updated['level'], updated['experience'], granted, True
    return level, total, granted, False
