"""Native 946 appearance-mount services reached from the mall."""

import json
from pathlib import Path
from secrets import choice
from time import monotonic, time

from native946_codec import packet, text_notice

DATA = json.loads(Path(__file__).with_name('native946_mall_mounts_data.json').read_text('utf8'))
PAY_MOUNTS = frozenset(DATA['pay_mounts'])
SKILLS = {int(key): value for key, value in DATA['skills'].items()}
FIXED_DYES = {int(key): value for key, value in DATA['fixed_dyes'].items()}
ITEMS = {str(key): value['item'] for key, value in FIXED_DYES.items()}
LOCAL_RULES = DATA['local_rules']
RANDOM_DYES = {int(key): value for key, value in DATA['random_dye'].items()}
DYE_MATERIAL = LOCAL_RULES['dye_material']
DYE_SHORTCUT = LOCAL_RULES['dye_shortcut']
ITEMS[str(DYE_MATERIAL)] = dict(name='七色染', icon=2038, max_stack=20, days=0,
                                desc=f"可在坐骑浣色页使用。本机服效果持续{LOCAL_RULES['dye_days']}天。")
REPEL_KEY = 'mount_repel'
REFERENCE_WIRE_VERSION = 4
MOUNT_SHOPS = {957: (2001, 3407), 958: (2033, 1270)}
SUPPORTED_SKILLS = {73, 77, 82, 213, 301, 327, 347, *MOUNT_SHOPS}
WAREHOUSE_NPC = 3439
REQUESTS = {1751, 1831, 2299, 3231, 3565, 3566, 3567, 3682, 4002, 4003}
WRITES = {1751, 2299, 3566, 3682, 4002, 4003}


def feature_value(features, key):
    return features.get(key, {}).get('value', 0)


def put_feature(db, cid, key, value, expires=0):
    db.execute('''INSERT INTO cosmetic_features VALUES(?,?,?,?)
        ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value,expires=excluded.expires''',
        (cid, key, value, expires))


def equipped_colors(features, mount):
    kind = feature_value(features, f'mount_dye:{mount}')
    dye = FIXED_DYES.get(kind)
    if dye and dye['mount'] == mount and feature_value(features, f'mount_dye_unlocked:{kind}') == mount:
        return dye['rgb']
    row = features.get(f'mount_random_dye:{mount}')
    if row and mount in RANDOM_DYES:
        palette = RANDOM_DYES[mount]['palette']
        if 1 <= row['value'] <= len(palette):
            return palette[row['value'] - 1]
    return (0, 0, 0)


def known_skills(features):
    return sorted({skill for mount in PAY_MOUNTS if f'mount:{mount}' in features
                   if (skill := feature_value(features, f'mount_skill:{mount}')) in SKILLS})


def fixed_dye_reply(features, mount):
    current = feature_value(features, f'mount_dye:{mount}')
    available = [kind for kind, dye in FIXED_DYES.items() if dye['mount'] == mount and
                 feature_value(features, f'mount_dye_unlocked:{kind}') == mount]
    if current not in available:
        current = 0
    return packet(4000, ['number', 'number', ('array', 'byte'), ('array', 'number')],
                  [mount, current, [0, 0, 0], available])


def dye_packet(game, mount):
    from native946_mall_uses import DATA as MALL_DATA, features
    from native946_progression import lua_data

    if not 1 <= mount <= len(MALL_DATA['mounts']):
        raise ValueError('坐骑编号无效。')
    with game.progression.transaction(write=False) as db:
        current = features(db, game.progression.cid)
    if any(dye['mount'] == mount for dye in FIXED_DYES.values()):
        return fixed_dye_reply(current, mount)
    row = current.get(f'mount_random_dye:{mount}')
    seconds = max(0, row['expires'] - int(time())) if row else 0
    return packet(3564, ['number', 'number', 'string', 'number', ('array', 'byte')],
                  [mount, 0, lua_data([DYE_MATERIAL]) if mount in RANDOM_DYES else '{}',
                   (seconds + 86399) // 86400, equipped_colors(current, mount)])


def dye_refresh(game, mount):
    from native946_cosmetics import Cosmetics

    return (*game.progression.item_packets(), dye_packet(game, mount),
            *Cosmetics(game).visual_packets(recreate=True))


def expire_dye(game):
    expires = game.character.get('appearance', {}).get('_mount_dye_expires',0)
    if expires and expires <= time():
        from native946_cosmetics import Cosmetics
        return Cosmetics(game).visual_packets(recreate=True)


def apply_random_dye(game, mount):
    from native946_mall_uses import features
    from native946_refinement import Refinement

    if game.battle:
        raise ValueError('战斗中无法修改坐骑配色。')
    if mount not in RANDOM_DYES:
        raise ValueError('这只坐骑无法进行随机浣色。')
    rule = RANDOM_DYES[mount]
    cid = game.progression.cid
    with game.progression.transaction() as db:
        current = features(db, cid)
        if f'mount:{mount}' not in current:
            raise ValueError('尚未拥有该坐骑或坐骑已经到期。')
        previous = tuple(equipped_colors(current, mount))
        options = [index for index, rgb in enumerate(rule['palette'], 1) if tuple(rgb) != previous]
        if not options:
            raise ValueError('该坐骑没有其他可用配色。')
        Refinement(game).take(db, DYE_MATERIAL, rule['cost'])
        expires = int(time()) + LOCAL_RULES['dye_days'] * 86400
        put_feature(db, cid, f'mount_random_dye:{mount}', choice(options), expires)
    return game.defer_packets(lambda: dye_refresh(game, mount))


def repel_active(game):
    expiry = getattr(game, '_mount_repel_until', None)
    if expiry is None:
        with game.progression.transaction(write=False) as db:
            row = db.execute('SELECT expires FROM cosmetic_features WHERE character_id=? AND key=?',
                             (game.progression.cid, REPEL_KEY)).fetchone()
        expiry = game._mount_repel_until = row[0] if row else 0
    return expiry > time()


def apply_fixed_dye(game, mount, kind, *, unlock, source_item=None):
    from native946_mall_uses import consume, features
    from native946_refinement import Refinement

    if game.battle:
        raise ValueError('战斗中无法修改坐骑配色。')
    dye = FIXED_DYES.get(kind)
    if not any(row['mount'] == mount for row in FIXED_DYES.values()) or (
            (unlock or kind) and (not dye or dye['mount'] != mount)):
        raise ValueError('染色道具与坐骑不匹配。')
    cid = game.progression.cid
    with game.progression.transaction() as db:
        current = features(db, cid)
        if f'mount:{mount}' not in current:
            raise ValueError('尚未拥有该坐骑或坐骑已经到期。')
        if unlock:
            if feature_value(current, f'mount_dye_unlocked:{kind}') == mount:
                raise ValueError('该配色已解锁，染色道具已保留。')
            if source_item is None:
                Refinement(game).take(db, kind, dye['cost'])
            else:
                consume(db, source_item, dye['cost'])
            put_feature(db, cid, f'mount_dye_unlocked:{kind}', mount)
        elif kind and feature_value(current, f'mount_dye_unlocked:{kind}') != mount:
            raise ValueError('尚未解锁该配色。')
        put_feature(db, cid, f'mount_dye:{mount}', kind)

    return game.defer_packets(lambda: dye_refresh(game, mount))


def teach(game, mount, skill):
    from native946_mall_uses import MallUses, features
    from native946_shops import change_liuguang, liuguang_balance, liuguang_packets

    if game.battle:
        raise ValueError('战斗中无法学习坐骑技能。')
    if mount not in PAY_MOUNTS or skill not in SKILLS:
        raise ValueError('灵兽或坐骑技能无效。')
    if skill not in SUPPORTED_SKILLS:
        raise ValueError('该技能的实际服务尚未接通，未扣除流光声望。')
    cid = game.progression.cid
    with game.progression.transaction() as db:
        current = features(db, cid)
        if f'mount:{mount}' not in current:
            raise ValueError('尚未拥有该灵兽或灵兽已经到期。')
        if feature_value(current, f'mount_skill:{mount}') or skill in known_skills(current):
            raise ValueError('该灵兽已有技能或该技能已经学会。')
        change_liuguang(db, cid, -SKILLS[skill]['price'])
        put_feature(db, cid, f'mount_skill:{mount}', skill)
        balance = liuguang_balance(db, cid)
    return game.defer_packets(lambda: (MallUses(game).mounts(3683), MallUses(game).mounts(3231),
                                      *liuguang_packets(balance), text_notice(f"已学会{SKILLS[skill]['name']}。")))


def use_skill(game, skill):
    from native946_mall_uses import features
    from native946_mount_spaces import SKILL_SPACES, space_kind, transfer

    if game.battle:
        raise ValueError('战斗中无法使用此坐骑技能。')
    p = game.progression
    target_space = SKILL_SPACES.get(skill)
    if target_space and space_kind(game.character['map_id']) == target_space:
        # Leaving remains available if a temporary mount expired inside.
        return transfer(game,target_space)
    with p.transaction(write=skill in (77, 82)) as db:
        if skill not in known_skills(features(db, p.cid)):
            raise ValueError('尚未拥有启用该技能的灵兽。')
        if skill == 77:
            until = int(time()) + LOCAL_RULES['repel_seconds']
            put_feature(db, p.cid, REPEL_KEY, 1, until)
        if skill == 82:
            from native946_premium_items import _restore
            _restore(game, db, 'all', paid=False)
    if target_space:
        return transfer(game,target_space)
    if skill == 77:
        def refresh_repel():
            game._mount_repel_until = until
            game._encounter_steps = 0
            return text_notice(f"快捷慑妖已生效，持续{LOCAL_RULES['repel_seconds'] // 60}分钟。")
        return game.defer_packets(refresh_repel)
    if skill == 301:
        from native946_npc_services import NpcServices
        game.npc_service_session = dict(kind='storage', npc=WAREHOUSE_NPC, source='pay_mount_warehouse',
                                        scene=game.character['map_id'], expires=monotonic() + 600)
        return NpcServices(game).storage_view(WAREHOUSE_NPC)
    if skill == 327:
        from native946_afei_society import Chamber
        return Chamber(game).open_remote()
    if skill == 82:
        return game.defer_packets(lambda: (*p.attribute_packets(), *game.summons.sync(),
                                            text_notice('随身治疗已恢复气血和法力。')))
    if skill == 347:
        return packet(1303, ['number', 'number'], [60, 0])
    if skill in MOUNT_SHOPS:
        from native946_shops import Shops
        return Shops(game).open_mount_shop(skill)
    raise ValueError('此坐骑技能的实际服务尚未接通。')


def receive(game, mid, reader):
    if mid == 1751:
        owner = reader.integer()
        reader.finish()
        if owner != game.progression.cid:
            raise ValueError('本机家园仅限角色本人进入。')
        return use_skill(game, 213)
    if mid == 1831:
        reader.finish()
        return use_skill(game, 327)
    if mid == 3231:
        reader.finish()
        from native946_mall_uses import MallUses
        return MallUses(game).mounts(mid)
    if mid == 3567:
        reader.finish()
        from native946_cosmetics import Cosmetics
        return Cosmetics(game).single_buy_offer(DYE_MATERIAL, 1)
    first = reader.integer()
    second = reader.integer() if mid in (3682, 4002, 4003) else None
    reader.finish()
    if mid == 2299:
        return use_skill(game, first)
    if mid == 3682:
        return teach(game, first, second)
    if mid == 3565:
        return dye_packet(game, first)
    if mid == 3566:
        return apply_random_dye(game, first)
    return apply_fixed_dye(game, first, second, unlock=mid == 4002)
