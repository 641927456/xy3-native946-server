"""Local NPC advancement using the existing character cultivation record."""

from dataclasses import dataclass

from native946_codec import packet, text_notice
from native946_training import state


@dataclass(frozen=True)
class Advancement:
    name: str
    npc: tuple[int, int]
    before: tuple[int, int, int]
    level: int
    after: tuple[int, int, int]


MENG_PO = (2009, 2121)
LAO_JUN = (2018, 3093)
YU_RONG_WANG = (2256, 6017)
ADVANCEMENTS = {
    'first': Advancement('一转', MENG_PO, (0, 0, 0), 100, (1, 0, 0)),
    'second': Advancement('二转', MENG_PO, (1, 0, 0), 120, (2, 0, 0)),
    'third': Advancement('三转（本机可选）', MENG_PO, (2, 0, 0), 140, (3, 0, 0)),
    'ascend': Advancement('飞升', LAO_JUN, (2, 0, 0), 140, (2, 1, 0)),
    'ascend_after_third': Advancement('飞升', LAO_JUN, (3, 0, 0), 140, (2, 1, 0)),
    'sanctify': Advancement('封圣', YU_RONG_WANG, (2, 1, 0), 165, (2, 1, 1)),
}
GIVERS = {rule.npc for rule in ADVANCEMENTS.values()}


def level_cap(snapshot):
    from native946_cultivation_quests import TRIAL_RULES

    campaign_cap = 183 if any(key not in TRIAL_RULES for key in snapshot.get('campaign', {}).get('runs', {})) else 100
    if snapshot['sanctification']:
        return max(campaign_cap, 185)
    if snapshot['ascension']:
        return max(campaign_cap, 180)
    return max(campaign_cap, (100, 122, 142, 142)[min(3, max(0, snapshot['rebirth']))])


def menu(game, npc):
    cid = game.progression.cid
    with game.progression.transaction(write=False) as db:
        saved = state(db, cid)
        level = db.execute('SELECT level FROM characters WHERE id=?', (cid,)).fetchone()['level']
    stage = (saved['rebirth'], saved['ascension'], saved['sanctification'])
    available = [(key, rule) for key, rule in ADVANCEMENTS.items()
                 if rule.npc == (npc['scene_id'], npc['npc_id']) and rule.before == stage]
    if not available:
        return None
    text = '本机人物修行。办理后保留人物等级、经验和加点。'
    choices = []
    for key, rule in available:
        text += f'#r{rule.name}需要达到{rule.level}级，当前{level}级。'
        if key == 'third':
            text += '#r三转为本机可选阶段，飞升后使用原生飞升身份。也可直接从二转飞升。'
        if level >= rule.level:
            choices.append((f'确认{rule.name}', dict(kind='cultivation', op=key)))
    game.dialog_actions = [action for _, action in choices]
    return game.menu(text, [label for label, _ in choices], npc['npc_id'])


def advance(game, npc, op):
    rule = ADVANCEMENTS.get(op)
    if rule is None or rule.npc != (npc['scene_id'], npc['npc_id']):
        raise ValueError('请前往对应的修行 NPC。')
    p = game.progression
    with p.transaction() as db:
        from native946_campaign import current, snapshot

        if current({'campaign': snapshot(db, p.cid)}, True):
            raise ValueError('请完成当前修行任务后，由剧情联系人办理。')
        level = db.execute('SELECT level FROM characters WHERE id=?', (p.cid,)).fetchone()['level']
        if level < rule.level:
            raise ValueError(f'当前等级不足，{rule.name}需要达到{rule.level}级。')
        changed = db.execute('UPDATE cultivation_state SET rebirth=?,ascension=?,sanctification=? '
                             'WHERE character_id=? AND rebirth=? AND ascension=? AND sanctification=?',
                             (*rule.after, p.cid, *rule.before)).rowcount
        if not changed:
            raise ValueError('当前修行阶段已改变，请重新交谈。')
        if rule.after[2]:
            from native946_role_growth import school
            from native946_skill6 import learn_primary
            learn_primary(db, p.cid, school(db, p.cid))
    p._cached_snapshot = None
    return (packet(1183, ['string'], ['dlg_npc_chat']),
            game.defer_packets(lambda: (*game.sync_progression(), text_notice(f'已完成{rule.name}。'))))
