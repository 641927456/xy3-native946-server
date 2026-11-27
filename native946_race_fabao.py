"""946 race artifact panel and Blue Moon Nectar progression."""
from time import time

from native946_codec import packet, text_notice


QUEST = '125025'  # 四曼陀罗
MENTOR = (2024, 8044)  # 乌斯藏·黑熊精
# INFO_NPC locations associated with the four elements in INFO_TASK[125025].
GUIDE_SIGILS = ((2024,8050),(2416,8053),(2013,8056),(2439,8059))
YINYANG_STONE = 47934
NECTAR = 47936
NECTAR_EXPERIENCE = 42480
DAILY_LIMIT = 100
# INFO_RACE_FABAO.UpgradeConfig.Exp, indexed by current tier 1–9.
NEXT_TIER_EXPERIENCE = (7646400, 11469600, 15292800, 22939200, 34408800,
                        49701600, 64994400, 80287200, 95580000)
REQUESTS = {4460, 4461, 4784}
WRITES = {4460, 4784}
# Attribute indices from INFO_RACE_FABAO, using the existing stat units.
PASSIVE_KEYS = {1:'ReDisorder',2:'ReFaint',3:'ReSeal',4:'EquipRePoisonDamage',5:'ReThunder',
                6:'ReWater',7:'ReWind',8:'ReFire',9:'ReMpAbsorb',10:'ReGushu',11:'ReWenxin',12:'defense',
                13:'ReGeshan',14:'EquipReCruel',15:'EquipReFatal',16:'initiative',17:'attack',
                18:'hpmax',19:'mpmax',20:'AddDisorder',21:'AddSeal',22:'AddFaint',23:'EquipPoisonDamage',
                24:'AddFire',25:'AddWind',26:'AddThunder',27:'AddWater',28:'XQAddCureHpP',
                29:'AddXiaorongRate',30:'AddGushu',32:'RoleLineupAttackBuff',33:'RoleLineupSpeedBuff',
                34:'RoleLineupGuardBuff',35:'RoleLineupShock'}


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_race_fabao_guide (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        accepted_at INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_race_fabao (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        tier INTEGER NOT NULL DEFAULT 1 CHECK(tier BETWEEN 1 AND 10),
        experience INTEGER NOT NULL DEFAULT 0 CHECK(experience>=0),
        nectar_day TEXT NOT NULL DEFAULT '',
        nectar_count INTEGER NOT NULL DEFAULT 0 CHECK(nectar_count BETWEEN 0 AND 100),
        skill_yinyang INTEGER NOT NULL DEFAULT -1 CHECK(skill_yinyang IN (-1,0,1,2)))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_race_fabao_attributes (
        character_id INTEGER NOT NULL REFERENCES native_race_fabao(character_id) ON DELETE CASCADE,
        tier INTEGER NOT NULL CHECK(tier BETWEEN 1 AND 10),
        yinyang INTEGER NOT NULL CHECK(yinyang IN (1,2)),
        attr_index INTEGER NOT NULL CHECK(attr_index>0),
        attr_value INTEGER NOT NULL CHECK(attr_value>=0),
        PRIMARY KEY(character_id,tier))''')


def state(db, cid):
    from native946_training import state as cultivation

    level = db.execute('SELECT level FROM characters WHERE id=?', (cid,)).fetchone()[0]
    if not cultivation(db, cid)['sanctification'] or level < 180:
        raise ValueError('达到封圣180级后可找乌斯藏的黑熊精领取四曼陀罗。')
    if not db.execute('SELECT 1 FROM native_campaign_runs WHERE character_id=? AND chapter=? AND state=\'completed\'',
                      (cid, QUEST)).fetchone():
        raise ValueError('请先完成四曼陀罗，完成后解锁种族法宝。')
    saved = db.execute('SELECT * FROM native_race_fabao WHERE character_id=?', (cid,)).fetchone()
    attrs = db.execute('SELECT * FROM native_race_fabao_attributes WHERE character_id=? ORDER BY tier', (cid,)).fetchall()
    return (dict(saved) if saved else dict(tier=1, experience=0, nectar_day='', nectar_count=0, skill_yinyang=-1)), attrs


def panel(saved, attrs, day):
    from native946_progression import lua_data

    sides = {row['tier']: row['yinyang'] for row in attrs}
    values = {row['tier']: dict(Index=row['attr_index'], Value=row['attr_value']) for row in attrs}
    used = saved['nectar_count'] if saved['nectar_day'] == day else 0
    return packet(4458, ['number'] * 4 + ['string'] * 2,
                  [saved['tier'], saved['experience'], used, saved['skill_yinyang'], lua_data(sides), lua_data(values)])


def mentor_panel(game, nid, op='panel'):
    from native946_npc_services import NpcServices
    from native946_role_systems import day
    from native946_race_fabao_cultivation import pending_packet, _stove_ui

    npc=NpcServices(game).validate(nid)
    if (npc['scene_id'],nid)!=MENTOR:raise ValueError('请到乌斯藏找黑熊精。')
    with game.progression.transaction(write=False) as db:
        saved,attrs=state(db,game.character['id'])
        if op=='stove':return _stove_ui(db,game.character['id'])
        return (panel(saved,attrs,day()),*pending_packet(db,game.character['id']))


def guide_active(db,cid):
    return db.execute('''SELECT 1 FROM native_race_fabao_guide g WHERE g.character_id=?
        AND NOT EXISTS(SELECT 1 FROM native_campaign_runs c WHERE c.character_id=g.character_id
                       AND c.chapter=? AND c.state='completed')''',(cid,QUEST)).fetchone() is not None


def _guide_waypoints():
    from native946_world import world

    catalog=world()
    for scene,nid in GUIDE_SIGILS:
        npc=catalog.npc(scene,nid)
        if npc:
            label=f"{catalog.scene(scene)['scene_name']}·{npc['name']}"
            yield label,scene,nid,npc['x']//20,npc['y']//20


def guide_details(snapshot):
    from native946_tasks import STORIES, location

    if not snapshot.get('race_fabao_guide'):return None
    story=STORIES[int(QUEST)];target=location(story)
    clues='、'.join(f'{label}（{x},{y}）' for label,_,_,x,y in _guide_waypoints())
    return dict(title=story['name'],text='可向黑熊精查询四方真言灵符的位置。',
                description=story['desc_str']+'#r已知线索：'+clues,
                scene=target['scene'],x=target['x'],y=target['y'],npc=target['npc'])


def mentor_guide(game,nid,op,target=None):
    from native946_navigation import npc_auto_trace
    from native946_npc_services import NpcServices
    from native946_tasks import STORIES, eligibility

    services=NpcServices(game)
    npc=services.validate(nid)
    if (npc['scene_id'],nid)!=MENTOR:raise ValueError('请到乌斯藏找黑熊精。')
    p=game.progression
    if op in ('clues','trace'):
        with p.transaction(write=False) as db:
            if not guide_active(db,p.cid):raise ValueError('请先领取四曼陀罗。')
        choices=[]
        for label,scene,sigil,x,y in _guide_waypoints():
            if op=='trace' and sigil==target:return npc_auto_trace(scene,x,y,sigil,label)
            choices.append((label,dict(kind='race_fabao_guide',op='trace',target=sigil)))
        if op=='trace':raise ValueError('没有这条真言灵符线索。')
        return services._menu(nid,'请选择要查找的真言灵符。',choices)
    story=STORIES[int(QUEST)]
    with p.transaction() as db:
        if db.execute('SELECT 1 FROM native_campaign_runs WHERE character_id=? AND chapter=? AND state=\'completed\'',
                      (p.cid,QUEST)).fetchone():
            raise ValueError('四曼陀罗已经完成。')
        if op=='accept':
            eligible,reason=eligibility(story,p.snapshot())
            if not eligible:raise ValueError(reason)
            db.execute('INSERT OR IGNORE INTO native_race_fabao_guide VALUES(?,?)',(p.cid,int(time())))
            reply=text_notice('已领取四曼陀罗。可向黑熊精查询四方真言灵符的位置。')
        elif op=='cancel':
            db.execute('DELETE FROM native_race_fabao_guide WHERE character_id=?',(p.cid,))
            reply=text_notice('已放弃四曼陀罗引导。')
        else:raise ValueError('引导操作无效。')
    p._cached_snapshot=None
    return game.defer_packets(lambda:(reply,*p.task_packets()))


def effects(db, cid, character):
    if not character['sanctification'] or character['level']<180:
        return {}
    run=character['campaign']['runs'].get(QUEST,{})
    if run.get('state')!='completed':return {}
    result={};seen={}
    rows=db.execute('''SELECT a.attr_index,a.attr_value FROM native_race_fabao_attributes a
        JOIN native_race_fabao f USING(character_id)
        WHERE a.character_id=? AND a.tier<=f.tier ORDER BY a.attr_value DESC,a.tier''',(cid,))
    for index,amount in rows:
        if index not in PASSIVE_KEYS or seen.get(index,0)>=5:continue
        key=PASSIVE_KEYS[index]
        seen[index]=seen.get(index,0)+1
        result[key]=result.get(key,0)+amount
    return result


def _consume_material(db, cid, kind, amount):
    from native946_inventory_locks import records
    from native946_mall_checkout import rows
    from native946_mall_uses import consume

    locked = records(db, cid)
    owned = [row for row in rows(db, cid, (kind,)) if row['id'] not in locked]
    if sum(row['amount'] for row in owned) < amount:
        raise ValueError('可用物品栏内的种族法宝材料不足。')
    remaining = amount
    for row in owned:
        take = min(remaining, row['amount'])
        consume(db, row, take)
        remaining -= take
        if remaining == 0:
            break


def add_experience(db, cid, amount, day, saved, attrs):
    tier = saved['tier']
    if tier > len(NEXT_TIER_EXPERIENCE):
        raise ValueError('种族法宝已经达到最高层阶。')
    if not attrs or attrs[-1]['tier'] != tier:
        raise ValueError('请先激活当前层阶的两仪属性。')
    used = saved['nectar_count'] if saved['nectar_day'] == day else 0
    needed = NEXT_TIER_EXPERIENCE[tier-1] - saved['experience']
    maximum = min(DAILY_LIMIT-used, (needed+NECTAR_EXPERIENCE-1)//NECTAR_EXPERIENCE)
    if not 1 <= amount <= maximum:
        raise ValueError('使用数量超出今日可用次数或当前阶所需道行。')

    _consume_material(db, cid, NECTAR, amount)
    experience = saved['experience'] + amount * NECTAR_EXPERIENCE
    if experience >= NEXT_TIER_EXPERIENCE[tier-1]:
        tier += 1
        experience = 0
    db.execute('UPDATE native_race_fabao SET tier=?,experience=?,nectar_day=?,nectar_count=? WHERE character_id=?',
               (tier, experience, day, used+amount, cid))
    saved.update(tier=tier, experience=experience, nectar_day=day, nectar_count=used+amount)


def switch_skill(db, cid, saved, attrs):
    polarity = saved['skill_yinyang']
    if polarity not in (1, 2):
        yin = sum(row['yinyang'] == 1 for row in attrs)
        yang = sum(row['yinyang'] == 2 for row in attrs)
        if yin == yang:
            raise ValueError('当前没有阴阳主动技能可转换。')
        polarity = 1 if yin > yang else 2
    _consume_material(db, cid, YINYANG_STONE, 5)
    saved['skill_yinyang'] = 3-polarity
    db.execute('UPDATE native_race_fabao SET skill_yinyang=? WHERE character_id=?', (saved['skill_yinyang'], cid))


def receive(game, mid, reader):
    from native946_role_systems import day
    from native946_race_fabao_cultivation import pending_packet

    amount = reader.integer() if mid == 4460 else 0
    reader.finish()
    if game.battle:
        raise ValueError('请先结束战斗。')
    p = game.progression
    today = day()
    with p.transaction(write=mid in WRITES) as db:
        saved, attrs = state(db, p.cid)
        if mid == 4461:
            return (panel(saved, attrs, today),*pending_packet(db,p.cid))
        if mid == 4460:
            add_experience(db, p.cid, amount, today, saved, attrs)
        else:
            switch_skill(db, p.cid, saved, attrs)
        reply = panel(saved, attrs, today)
    return game.defer_packets(lambda: (*p.item_packets(), reply))
