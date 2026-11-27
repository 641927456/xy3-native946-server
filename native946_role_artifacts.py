"""Owned artifacts, native equipment slots and local spirit-box rules."""
import json
from pathlib import Path
from secrets import SystemRandom
from time import time

from native946_role_systems import state,save,wire,require,notice,number
from native946_role_battle import DATA, LIULI_LAMP, description

TYPES={kind:skill for skill,name in ((128,'绝情鞭'),(131,'金箍儿')) for kind in DATA['artifact_items'][name]}
REQUESTS={310,313,314,322}
ACTIVE_PAGE = 34
ACTIVE_GRIDS = range(4, 7)
STORAGE_PAGE = 1700
STORAGE_GRIDS = range(1, 25)
LAMP_ITEM = 47800
BOX_DATA = json.loads(Path(__file__).with_name('native946_role_artifacts_data.json').read_text('utf8'))
BOX_RULES = {int(kind): row for kind, row in BOX_DATA['boxes'].items()}
MODERN_ARTIFACTS = {int(kind): row for kind, row in BOX_DATA['artifacts'].items()}
REWARD_WEIGHTS = tuple((kind, row['weight']) for kind, row in MODERN_ARTIFACTS.items())
TOTAL_WEIGHT = sum(weight for _, weight in REWARD_WEIGHTS)


def item_progress(saved, item_id):
    # Existing inventory rows predate per-item artifact progress.
    return saved.get('item_progress', {}).get(str(item_id), {})


def lamp_grade(db, cid):
    item = db.execute('SELECT id FROM inventory WHERE character_id=? AND type=? AND page=? '
                      'AND slot BETWEEN 4 AND 6 AND amount=1 ORDER BY slot LIMIT 1',
                      (cid, LAMP_ITEM, ACTIVE_PAGE)).fetchone()
    if item is None:
        return 0
    return max(1, int(item_progress(state(db, cid, 'artifacts'), item['id']).get('grade', 1)))


def free_storage_grid(db, cid):
    occupied = {r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=?', (cid, STORAGE_PAGE))}
    grid = next((candidate for candidate in STORAGE_GRIDS if candidate not in occupied), 0)
    require(grid != 0, '法宝物品栏已满，请先整理。')
    return grid


def move_artifact(db, cid, item, page, slot, bags, withdrawal_bags):
    require(item['type'] in MODERN_ARTIFACTS and item['amount'] == 1, '此栏位需要单件已接入的法宝。')
    source, source_slot = item['page'], item['slot']
    require(source in (ACTIVE_PAGE, STORAGE_PAGE, *withdrawal_bags), '法宝不在可用物品栏。')
    from native946_inventory_locks import require_unlocked
    require_unlocked(db, cid, item['id'])

    grids = ACTIVE_GRIDS if page == ACTIVE_PAGE else STORAGE_GRIDS
    require(page in (ACTIVE_PAGE, STORAGE_PAGE, *bags), '目标法宝栏位无效。')
    if page == ACTIVE_PAGE:
        require(item['type'] == LAMP_ITEM, '这件法宝的战斗效果尚未接入。')
    if slot == -1 and page == STORAGE_PAGE:
        slot = free_storage_grid(db, cid)
    require(slot in grids, '目标法宝栏位无效。')
    if (source, source_slot) == (page, slot):
        return
    target = db.execute('SELECT id,type,amount FROM inventory WHERE character_id=? AND page=? AND slot=?',
                        (cid, page, slot)).fetchone()
    if target:
        require(target['type'] in MODERN_ARTIFACTS and target['amount'] == 1 and source in (ACTIVE_PAGE, STORAGE_PAGE, *bags),
                '目标位置已有其他道具。')
        if source == ACTIVE_PAGE:
            require(target['type'] == LAMP_ITEM, '这件法宝的战斗效果尚未接入。')
        require_unlocked(db, cid, target['id'])
    if page == ACTIVE_PAGE and source != ACTIVE_PAGE:
        require(not db.execute('SELECT 1 FROM inventory WHERE character_id=? AND type=? AND page=? AND slot<>?',
                               (cid, LAMP_ITEM, ACTIVE_PAGE, slot)).fetchone(), '同一法宝不能重复装备。')
    if target:
        db.execute('UPDATE inventory SET page=-1,slot=-1 WHERE id=?', (item['id'],))
        db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?', (source, source_slot, target['id']))
    db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?', (page, slot, item['id']))


def box_description(kind):
    rule = BOX_RULES[kind]
    pool = '、'.join(row['name'] for row in MODERN_ARTIFACTS.values())
    if len(set(weight for _, weight in REWARD_WEIGHTS)) == 1:
        odds = f'每件 {1 / len(REWARD_WEIGHTS):.4%}（1/{len(REWARD_WEIGHTS)}）'
    else:
        odds = '、'.join(f'{MODERN_ARTIFACTS[item]["name"]} {weight / TOTAL_WEIGHT:.4%}'
                        for item, weight in REWARD_WEIGHTS)
    return (f'#r#Y【本机开盒规则】#n 每次独立抽取一件{rule["generation"]}代法宝，'
            f'附带{rule["refine_count"]}条互不重复的精炼属性，无保底。'
            f'#r奖池：{pool}。#r单次概率：{odds}。')


def open_box(game, iid):
    from native946_inventory_features import owned, consume
    from native946_mall_event_boxes import pick

    require(game.battle is None, '请先完成当前战斗。')
    progression = game.progression
    cid = progression.cid
    with progression.transaction() as db:
        item = owned(db, cid, iid)
        rule = BOX_RULES.get(item['type'])
        require(rule is not None, '请选择可开启的灵宝。')
        expiry = db.execute('SELECT expires FROM cosmetic_expiry WHERE item_id=?', (iid,)).fetchone()
        require(not expiry or not expiry[0] or expiry[0] > time(), '该灵宝已过期。')
        slot = free_storage_grid(db, cid)
        kind = pick(REWARD_WEIGHTS)
        artifact = MODERN_ARTIFACTS[kind]
        traits = SystemRandom().sample(artifact['refine'], rule['refine_count'])
        consume(db, cid, iid)
        reward_id = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                               (cid, kind, STORAGE_PAGE, slot)).lastrowid
        saved = state(db, cid, 'artifacts')
        owned_ids = {str(r[0]) for r in db.execute('SELECT id FROM inventory WHERE character_id=?', (cid,))}
        progress = {key: value for key, value in saved.get('item_progress', {}).items() if key in owned_ids}
        progress[str(reward_id)] = dict(grade=1, generation=rule['generation'], refinements=traits)
        saved['item_progress'] = progress
        save(db, cid, 'artifacts', saved)

    result = notice(f'开出{rule["generation"]}代法宝「{artifact["name"]}」，已放入法宝物品栏。')
    return game.defer_packets(lambda: (*progression.item_packets(item_ids={iid, reward_id}), result))

def available(db,cid):
    from native946_mall_bags import pages
    usable=pages(db,cid)
    return {r['id']:dict(r,skill=TYPES[r['type']]) for r in db.execute('SELECT * FROM inventory WHERE character_id=?',(cid,)) if r['type'] in TYPES and r['page'] in usable}

def current(db,cid):
    s=state(db,cid,'artifacts',dict(slots=[0,0,0],essence=0));owned=available(db,cid)
    s['slots']=[iid if iid in owned else 0 for iid in s['slots']]
    return s,owned

def active(db,cid):
    s,owned=current(db,cid)
    return {owned[i]['skill'] for i in s['slots'] if i}

def panel(db,cid):
    s,owned=current(db,cid);positions=[owned[i]['skill'] if i else 0 for i in s['slots']]
    return wire(303,*positions,3,sorted({v['skill'] for v in owned.values()}),'本机已接入绝情鞭、金箍儿；选择天、地、人位置后生效。')

def receive(game,db,mid,a):
    cid=game.progression.cid;s,owned=current(db,cid)
    if mid==314:return [panel(db,cid)]
    if mid==313:
        iid = a[0]
        from native946_mall_bags import pages
        item = db.execute('SELECT type,page FROM inventory WHERE character_id=? AND id=?', (cid, iid)).fetchone()
        if item and item['type'] in MODERN_ARTIFACTS and item['page'] in (ACTIVE_PAGE, STORAGE_PAGE, *pages(db, cid)):
            progress = item_progress(s, iid)
            grade = max(1, int(progress.get('grade', 1)))
            text = (description(LIULI_LAMP, {'role_artifacts': {'琉璃灯': grade}}) if item['type'] == LAMP_ITEM
                    else MODERN_ARTIFACTS[item['type']]['desc'] + '#r本机已接入掉落和查看，战斗效果待接入。')
            traits = progress.get('jl_trait', '')
            if 'refinements' in progress:
                traits = '#r'.join(BOX_DATA['refinements'][str(key)] + ':本机已记录，效果待接入。'
                                   for key in progress['refinements'])
            details = dict(grade=grade, level=progress.get('generation', 2), last=progress.get('last', 0),
                           last_max=progress.get('last_max', 0), exp=progress.get('exp', 0),
                           exp_next=progress.get('exp_next', 0), jl_trait=traits,
                           base_trait=progress.get('base_trait', ''), desc=text)
            return [wire(3007, iid, details)]
        skill=number(a[0],128,131,'法宝');require(any(v['skill']==skill for v in owned.values()),'尚未拥有该法宝。')
        return [wire(302,dict(fabao_id=skill,grade=1,last=s['essence'],last_max=1000,exp='0',exp_next='1000',desc=DATA['native_skills'][str(skill)]['des']+'#r本机规则：主动施放消耗200怨气、10灵气；子女触发不扣怨气，每次消耗10灵气。'))]
    if mid==310:
        hp=db.execute('SELECT hp FROM progression WHERE character_id=?',(cid,)).fetchone()[0]
        require(hp>100,'补充灵气需100气血，且不可因此倒下。');require(s['essence']<1000,'法宝灵气已满。')
        db.execute('UPDATE progression SET hp=hp-100 WHERE character_id=?',(cid,));s['essence']=min(1000,s['essence']+100);save(db,cid,'artifacts',s)
        return [notice('已消耗100气血，补充100法宝灵气。'),panel(db,cid)]
    ids=a[0];require(len(ids)==3,'请选择天、地、人三个法宝位置。');require(len([k for k in ids if k])==len(set(k for k in ids if k)),'同一法宝不能重复装备。')
    by_skill={v['skill']:iid for iid,v in owned.items()};require(all(not k or k in by_skill for k in ids),'法宝不属于当前人物或不在可用背包。')
    from native946_inventory_locks import require_unlocked
    for key in ids:
        if key:require_unlocked(db,cid,by_skill[key])
    s['slots']=[by_skill[k] if k else 0 for k in ids];save(db,cid,'artifacts',s)
    return [panel(db,cid),notice('法宝位置已保存。')]

def use(game,iid):
    with game.progression.transaction(write=False) as db:
        owned=available(db,game.progression.cid);require(iid in owned,'法宝不在可用背包。');reply=panel(db,game.progression.cid)
    return reply

def spend(db,cid,skill):
    s,owned=current(db,cid)
    require(skill in {owned[i]['skill'] for i in s['slots'] if i},'请先在天、地、人位置装备对应法宝。')
    require(s['essence']>=10,'法宝灵气不足，请在法宝面板补充。');s['essence']-=10;save(db,cid,'artifacts',s)
