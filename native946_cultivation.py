"""Character level limits and settlement for the existing cultivation trials."""

from dataclasses import dataclass

from native946_training import state


@dataclass(frozen=True)
class Advancement:
    name: str
    npc: tuple[int, int]
    before: tuple[int, int, int]
    level: int
    after: tuple[int, int, int]
    start_level: int | None
    points: int
    resistance: int

    @property
    def is_rebirth(self):
        return self.after[0] > self.before[0]


MENG_PO = (2009, 2121)
LAO_JUN = (2018, 3093)
YU_RONG_WANG = (2256, 6017)
WEST_LIANG_RULER = (2237, 5041)
FLIGHT_BREAKTHROUGH = '222374'
FLIGHT_LIMIT = 160
SANCTIFICATION_LIMIT = 185
STAGE_LIMITS = (100, 120, 140, 140)  # User-specified local caps and confirmation levels.
# 946 help 101603–101605 and 110192 provide the point awards.
# The 2007 character-rebirth guide and 2009 flight notice give the start levels.
ADVANCEMENTS = {
    'first': Advancement('一转', MENG_PO, (0, 0, 0), STAGE_LIMITS[0], (1, 0, 0), 30, 60, 30),
    'second': Advancement('二转', MENG_PO, (1, 0, 0), STAGE_LIMITS[1], (2, 0, 0), 45, 60, 46),
    'ascend': Advancement('飞升', LAO_JUN, (2, 0, 0), STAGE_LIMITS[2], (2, 1, 0), 120, 60, 65),
    'ascend_after_third': Advancement('飞升', LAO_JUN, (3, 0, 0), STAGE_LIMITS[3], (2, 1, 0), 120, 60, 65),
    'sanctify': Advancement('封圣', YU_RONG_WANG, (2, 1, 0), 165, (2, 1, 1), None, 60, 45),
    'breakthrough': Advancement('凝气化神', WEST_LIANG_RULER, (2, 1, 0), FLIGHT_LIMIT, (2, 1, 0), None, 0, 0),
}
GIVERS = {rule.npc for rule in ADVANCEMENTS.values()}


def level_cap(snapshot):
    if snapshot['sanctification']:
        return SANCTIFICATION_LIMIT
    if snapshot['ascension']:
        run = snapshot.get('campaign', {}).get('runs', {}).get(FLIGHT_BREAKTHROUGH, {})
        return 180 if run.get('state') == 'completed' else FLIGHT_LIMIT
    return STAGE_LIMITS[min(3, max(0, snapshot['rebirth']))]


def menu(game, npc):
    cid = game.progression.cid
    with game.progression.transaction(write=False) as db:
        saved = state(db, cid)
        level = db.execute('SELECT level FROM characters WHERE id=?', (cid,)).fetchone()['level']
    location = npc['scene_id'], npc['npc_id']
    if location == WEST_LIANG_RULER:
        snapshot = game.progression.snapshot()
        if level >= FLIGHT_LIMIT and saved['ascension'] and not saved['sanctification'] and level_cap(snapshot) == FLIGHT_LIMIT:
            game.dialog_actions = []
            return game.menu('飞升160级可向西梁国主领取凝气化神（本机替代试炼）。', [], npc['npc_id'])
        return None
    stage = (saved['rebirth'], saved['ascension'], saved['sanctification'])
    rule = next((rule for rule in ADVANCEMENTS.values() if rule.npc == location and rule.before == stage), None)
    if rule is None:
        return None
    from native946_campaign import TRIALS
    from native946_cultivation_quests import promotion

    chapter = next(c for key, c in TRIALS.items() if promotion(key, stage) == rule)
    game.dialog_actions = []
    return game.menu(f'请向{chapter["giver"]["name"]}领取{chapter["name"]}。'
                     f'完成试炼并达到{rule.level}级后可确认{rule.name}，当前{level}级。', [], npc['npc_id'])


def settle(progression, db, snapshot, rule):
    """Settle once, inside the final trial node's existing transaction."""
    if snapshot['level'] < rule.level:
        raise ValueError(f'达到{rule.level}级后才能确认{rule.name}。')
    cid = progression.cid
    changed = db.execute('UPDATE cultivation_state SET rebirth=?,ascension=?,sanctification=?,'
                         'earned_points=earned_points+? WHERE character_id=? '
                         'AND rebirth=? AND ascension=? AND sanctification=?',
                         (*rule.after, rule.resistance, cid, *rule.before)).rowcount
    if not changed:
        raise ValueError('当前修行阶段已改变，无法重复办理修行。')
    if rule.is_rebirth:
        db.execute('UPDATE learned_skills SET rank=0 WHERE character_id=? AND rank<>0', (cid,))
    if rule.start_level is None:
        if not rule.points:
            return
        db.execute('UPDATE progression SET points=points+? WHERE character_id=?', (rule.points, cid))
        from native946_skill6 import learn_primary

        learn_primary(db, cid, snapshot['school'])
        return

    level = rule.start_level
    raw = db.execute('SELECT cor,mag,strength,speed,points FROM progression WHERE character_id=?', (cid,)).fetchone()
    extra = max(0, sum(raw) - 8 * snapshot['level'])
    db.execute('UPDATE characters SET level=?,experience=0 WHERE id=?', (level, cid))
    db.execute('UPDATE progression SET cor=?,mag=?,strength=?,speed=?,points=? WHERE character_id=?',
               (level, level, level, level, 4 * level + extra + rule.points, cid))
    updated = progression._snapshot(db)
    db.execute('UPDATE progression SET hp=min(hp,?),mp=min(mp,?) WHERE character_id=?',
               (updated['hpmax'], updated['mpmax'], cid))
