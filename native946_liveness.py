"""Local daily activity scoring and rewards through the native 946 panel."""

from datetime import datetime, timedelta, timezone

from native946_role_systems import save, state


WIRE_VERSION = 1

# Local scoring. Completing the full school daily unlocks the first tier.
ACTIVITY_POINTS = {
    'school': (3, 60), 'sanjie': (2, 20), 'heaven': (1, 30),
    'ghost': (3, 150), 'king': (8, 160),
}
# Thresholds and the points/escort/bounty counts follow the 947 observations.
# The vitality and home rest amounts are the local rules for those rewards.
TIERS = {
    60: dict(points=5, energy=20, rest=1, escort=5, bounty=2),
    120: dict(points=5, energy=30, rest=1, escort=5, bounty=2),
    240: dict(points=5, energy=40, rest=1, escort=5, bounty=3),
    400: dict(points=5, energy=60, rest=1, escort=5, bounty=2),
}
SYSTEM = 'daily_liveness'
INITIAL_ENERGY = 100
DAY_ZONE = timezone(timedelta(hours=8))


def day_start():
    return datetime.now(DAY_ZONE).replace(hour=0, minute=0, second=0, microsecond=0)


def score(db, cid, day):
    start = int(day.timestamp())
    end = int((day + timedelta(days=1)).timestamp())
    # NPC settlements write both receipts together. Use the settlement time so
    # existing wins can count today and patrol's first fight cannot earn points.
    counts = db.execute(
        "SELECT q.kind,count(*) FROM battle_results b "
        "JOIN native_npc_quest_rewards r ON r.task_id=CAST(substr(b.battle_id,10) AS INTEGER) "
        "AND r.character_id=b.character_id "
        "JOIN native_npc_quests q ON q.id=r.task_id "
        "WHERE b.character_id=? AND b.settled_at>=? AND b.settled_at<? AND b.victory=1 "
        "AND b.battle_id='npcquest:'||r.task_id||':'||r.character_id GROUP BY q.kind",
        (cid, start, end),
    )
    return {kind: min(count * ACTIVITY_POINTS[kind][0], ACTIVITY_POINTS[kind][1])
            for kind, count in counts if kind in ACTIVITY_POINTS}


def total(by_activity):
    return min(sum(by_activity.values()), max(TIERS))


def balances(db, cid, day=None):
    balance = state(db, cid, SYSTEM, dict(points=0, energy=INITIAL_ENERGY, guide_done=False))
    stamp = (day if day is not None else day_start()).date().isoformat()
    if balance.get('day') != stamp:
        balance.update(day=stamp, claimed=[], rest=0, escort=0, bounty=0)
    return balance


def daily_claims(balance, day):
    return balance.get('claimed', []) if balance.get('day') == day.date().isoformat() else []


def award(db, cid, day, threshold):
    balance = balances(db, cid, day)
    claimed = daily_claims(balance, day)
    if threshold in claimed:
        return None, balance
    if total(score(db, cid, day)) < threshold:
        raise ValueError(f'今日活跃度达到{threshold}点才可领取这档奖励。')
    reward = TIERS[threshold]
    for key, amount in reward.items():
        balance[key] = balance.get(key, 0) + amount
    balance.update(claimed=[*claimed, threshold], guide_done=True)
    save(db, cid, SYSTEM, balance)
    return reward, balance
