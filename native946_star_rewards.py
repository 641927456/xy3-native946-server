"""The 946 classic star-title equipment and achievement award tables."""

import json
from pathlib import Path


DATA = json.loads(Path(__file__).with_name('native946_star_rewards_data.json').read_text('utf8'))
EQUIPMENT = DATA['equipment']
ITEMS = DATA['items']
ACHIEVEMENTS = {int(k):row for k,row in DATA['achievements'].items()}
CATEGORY = '星宿装备'
VERSION = '946-star-title-v1'


def profile(title, race):
    rows = EQUIPMENT.get(title, {})
    return rows.get(str(race), rows.get('0'))


def issued(db, cid, title):
    return db.execute('SELECT 1 FROM native_star_equipment_claims WHERE character_id=? AND title=?',
                      (cid,int(title))).fetchone() is not None


def grant(db, p, title, reward):
    from native946_equipment import persist

    if issued(db,p.cid,title):
        raise ValueError('这项星宿称谓奖励已领取。')
    kind = reward['item']
    if 'count' in reward:
        from native946_inventory_features import give
        give(db,p,kind,reward['count'])
        db.execute('INSERT INTO native_star_equipment_claims VALUES(?,?,?,0)',(p.cid,int(title),kind))
        return f"{ITEMS[str(kind)]['name']}×{reward['count']}"
    hero = p._snapshot(db)
    page,slot = p.free_bag_slot(hero)
    iid = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                     (p.cid,kind,page,slot)).lastrowid
    # F1 describes the base requirement. The item description supplies its stage exemption.
    rule = dict(reference_id=kind,grade=0,slot=reward['slot'],role='全部',need=reward['need'],
                level=reward['level'],rebirth=0,level_exempt=reward['level_exempt'],kind=CATEGORY,
                native_race=hero['race'] if '0' not in EQUIPMENT[title] else 0,durability=DATA['durability'])
    persist(db,p.cid,iid,rule,reward['properties'])
    db.execute('UPDATE native_equipment SET rule_version=? WHERE item_id=?',(VERSION,iid))
    db.execute('INSERT INTO native_star_equipment_claims VALUES(?,?,?,?)',(p.cid,int(title),kind,iid))
    return ITEMS[str(kind)]['name']


def earned(db, cid):
    from native946_constellations import DATA as CONSTELLATIONS, completed, need

    steps = completed(db,cid,include_history=True)
    return {aid for aid,row in ACHIEVEMENTS.items()
            if not need(CONSTELLATIONS['titles'][row['title']],steps,only_natural=row['naturals'])}


def grant_experience(game, db, reward):
    from native946_constellations import migrate_history

    cid = game.progression.cid
    migrate_history(db,cid)
    prior = db.execute('SELECT experience FROM native_star_claims WHERE character_id=? AND title=?',
                       (cid,int(reward['title']))).fetchone()
    native_xp = int(reward['award_data'])
    if prior:
        native_xp = max(0,native_xp-prior['experience'])
    if native_xp:
        db.execute('UPDATE characters SET experience=experience+? WHERE id=?',(native_xp,cid))
        game.auto_level()
    return native_xp
