"""Convenience effects with verified lifetimes and local balance values."""
from time import time
from contextlib import nullcontext

from native946_codec import packet, text_notice

SHARED_MEDICAL_KEY = 'medical_shared'
AUTO_MEDICAL_KEY = 'medical_after_battle'
SPEED_KEY = 'move_speed'
PILL_RUN_SPEED = 5
AUTO_MEDICAL_BLESSING = 1008
AUTO_MEDICAL_POSITION = 98
AUTO_MEDICAL_TIME_REQUEST = 39
HERO_MEDICAL_FEE = 2
SUMMON_MEDICAL_FEE = 1
PREMIUM_MEDICAL_TERMS = f'人物{HERO_MEDICAL_FEE}金钱，守护合计{SUMMON_MEDICAL_FEE}金钱。状态已满免收，余额不足时跳过治疗。'
AUTO_MEDICAL_DAYS = {70491: 1, 80491: 1, 70492: 7, 80492: 7}
SPEED_DAYS = {55228: 7, 65228: 7, 56659: 1, 66659: 1}
RULES = {str(kind): dict(op='shared_medical', days=7,
    source='946 native description; xy3.163.com/2015/6/30/6355_536393.html',
    pricing='local per-recipient medical fees')
    for kind in (55580, 65580)}
RULES.update({str(kind): dict(
    op='auto_medical', days=days,
    source='946 native description; https://xy3.163.com/2023/12/26/6355_1128430.html',
    pricing='local per-recipient medical fees',
) for kind, days in AUTO_MEDICAL_DAYS.items()})
RULES.update({str(kind): dict(
    op='team_speed', days=days, speed=PILL_RUN_SPEED,
    source='946 native description; team rules in official notices; run_speed is locally balanced',
) for kind, days in SPEED_DAYS.items()})
PENDING = {}
for _types, _reason in (
    ((55617, 55618, 65617, 65618), '此自行丹对应的2016年春节灵猴闹春活动已结束，道具已保留。'),
    ((55633, 65632, 65633), '百年后的来客尚未开放任务结算及自动接取；道具未消耗。'),
):
    PENDING.update({str(kind): dict(reason=_reason) for kind in _types})


def license_validity(db, cid, now=None, *, key=SHARED_MEDICAL_KEY):
    now = int(time()) if now is None else now
    row = db.execute('SELECT expires FROM cosmetic_features WHERE character_id=? AND key=?',
                     (cid, key)).fetchone()
    return row[0] if row and row[0] > now else 0


def _auto_medical_ttl(seconds):
    return packet(38, ['number', 'number', 'byte', 'number'],
                  [AUTO_MEDICAL_BLESSING, AUTO_MEDICAL_POSITION, 1, seconds])


def sync_auto_medical(game, *, force=False):
    now = int(time())
    with game.progression.transaction(write=False) as db:
        expiry = license_validity(db, game.progression.cid, now, key=AUTO_MEDICAL_KEY)
    previous = getattr(game, '_auto_medical_expiry', 0)
    game._auto_medical_expiry = expiry
    if expiry and (force or expiry != previous):
        return (packet(37, ['number'] * 3, [1, AUTO_MEDICAL_BLESSING, AUTO_MEDICAL_POSITION]),
                _auto_medical_ttl(expiry - now))
    if previous and not expiry:
        return (packet(37, ['number'] * 3, [0, AUTO_MEDICAL_BLESSING, AUTO_MEDICAL_POSITION]),)
    return ()


def expire_auto_medical(game):
    expiry = getattr(game, '_auto_medical_expiry', 0)
    if game.battle is None and expiry and expiry <= time():
        from native946_storage import StorageBusy
        try:
            return sync_auto_medical(game)
        except StorageBusy:
            pass
    return ()


def auto_medical_time(game, reader):
    effect, position = reader.integer(), reader.integer()
    reader.finish()
    from native946_experience_charm import BLESSING, POSITION, expires, remaining_packet
    if (effect, position) == (BLESSING, POSITION):
        with game.progression.transaction(write=False) as db:
            return remaining_packet(expires(db, game.progression.cid) - int(time()))
    if (effect, position) != (AUTO_MEDICAL_BLESSING, AUTO_MEDICAL_POSITION):
        return None
    with game.progression.transaction(write=False) as db:
        now = int(time())
        expiry = license_validity(db, game.progression.cid, now, key=AUTO_MEDICAL_KEY)
    return _auto_medical_ttl(max(0, expiry - now))


def _team_views(game):
    service = getattr(game, 'party_service', None)
    if service is None:
        return ()
    with service.lock:
        team = service.team(game.character['id'])
        if not team:
            return ()
        # Read all payloads before publishing any, so a busy read is retryable.
        updates = [(other, service.view(other, team)) for row in team['members']
                   if (other := service.online(row['character_id'])) is not None]
        own = ()
        for other, payload in updates:
            if other is game:
                own = payload
            else:
                service.enqueue(other, *payload)
        return own


def use_convenience_license(game, db, item, rule):
    from native946_mall_uses import MallUses, consume
    key, name = {
        'shared_medical': (SHARED_MEDICAL_KEY, '共济丸共享治疗'),
        'auto_medical': (AUTO_MEDICAL_KEY, '笑忘入梦饮战后自动治疗'),
        'team_speed': (SPEED_KEY, '神速丹加速'),
    }[rule['op']]
    days = rule['days']
    MallUses(game).grant(db, key, item['type'], days)
    consume(db, item)
    def render():
        items = game.progression.item_packets()
        effect = ()
        if key == SPEED_KEY:
            effect = game.party_service.refresh_run_speeds(game, force_self=True)
        elif key == AUTO_MEDICAL_KEY:
            effect = sync_auto_medical(game)
        terms = '' if key == SPEED_KEY else '本机医疗费用：'+PREMIUM_MEDICAL_TERMS
        return (*items, *_team_views(game), *effect, text_notice(f'{name}有效期已增加{days}天。'+terms))

    return game.defer_packets(render)


def medical_targets(game, db):
    service = getattr(game, 'party_service', None)
    team = service.team(game.character['id'], db) if service else None
    if not team or team['leader_id'] != game.character['id']:
        return (game,)
    if not any(not row['away'] and service.online(row['character_id']) is not None and license_validity(db, row['character_id'])
               for row in team['members']):
        return (game,)
    return tuple(other for row in team['members']
                 if not row['away'] and (other := service.online(row['character_id'])) is not None and other.battle is None)


def _restore(game, db, op, *, paid):
    state = game.progression._snapshot(db) if op in ('hero', 'all') else None
    hero = bool(state and (state['hp'] < state['hpmax'] or state['mp'] < state['mpmax']))
    summons = op in ('summons', 'all') and any(
        pet['hp'] < pet['hpmax'] or pet['mp'] < pet['mpmax'] for pet in game.summons.snapshot())
    fee = (HERO_MEDICAL_FEE * hero + SUMMON_MEDICAL_FEE * summons) if paid else 0
    if fee and not db.execute('UPDATE characters SET gold=gold-? WHERE id=? AND gold>=?',
                              (fee, game.progression.cid, fee)).rowcount:
        return None
    if hero:
        game.progression.set_vitals(state['hpmax'], state['mpmax'])
    if summons:
        game.summons.rest()
    return fee


def heal_after_battle(db, games, *, service=None, leader=None):
    """Restore eligible participants in the same transaction as battle settlement."""
    now = int(time())
    recipients = {cid for cid, game in games.items()
                  if (service is None or service.online(cid) is game)
                  and license_validity(db, cid, now, key=AUTO_MEDICAL_KEY)}
    if not recipients:
        return
    if service is not None and len(games) > 1:
        team = service.team(leader, db)
        if team:
            present = {row['character_id'] for row in team['members'] if not row['away']
                       and row['character_id'] in games
                       and games[row['character_id']] is service.online(row['character_id'])}
            if recipients & present and any(license_validity(db, cid, now) for cid in present):
                recipients.update(present)
    for cid in sorted(recipients):
        _restore(games[cid], db, 'all', paid=True)


def heal_at_existing_npc(game, op, npc_id):
    """Charge each recipient only for the selected premium treatment."""
    if op not in ('hero', 'summons', 'all'):
        raise ValueError('治疗类型无效。')
    service = getattr(game, 'party_service', None)
    with service.lock if service else nullcontext():
        with game.progression.transaction() as db:
            targets = medical_targets(game, db)
            shared = len(targets) > 1
            results = [(other, _restore(other, db, op, paid=shared)) for other in targets]

    def render():
        updates = []
        for other, fee in results:
            if fee is None:
                payload = (text_notice('余额不足，本次共济丸共享治疗已跳过。'),)
            else:
                payload = (*other.progression.attribute_packets(), *other.summons.sync())
                if fee:
                    payload = (*payload, text_notice(f'共济丸共享治疗花费{fee}金钱。'))
            updates.append((other, payload))
        own = ()
        for other, payload in updates:
            if other is game:
                own = payload
            else:
                game.party_service.enqueue(other, *payload)
        skipped = sum(fee is None for _, fee in results)
        message = '已经恢复气血和法力。'
        if shared:
            message = f'共济丸共享治疗已结算，{len(results)-skipped}人完成，{skipped}人余额不足。'
        return (*own, game.menu(message, npc_id=npc_id))

    return game.defer_packets(render)
