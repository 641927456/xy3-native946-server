"""946 cultivation costs/effects and the currently available Wuli spells.

Only server-owned literal data from the supplied classic client is used. New
resistance monetary costs marked IsYuanbao use the separate tongbao balance.
"""
import json
from pathlib import Path

from native946_codec import packet

DATA=json.loads(Path(__file__).with_name('native946_training_data.json').read_text(encoding='utf8'))
POINTS=('FaintPoint','GushuPoint','SpePoint','DisorderPoint','ThunderPoint','PoisonPoint',
        'AttPoint','DefPoint','DodPoint','SealPoint','WindPoint','WaterPoint','FirePoint')
POINT_EFFECTS={'FirePoint':('ReFire',10),'WaterPoint':('ReWater',8),'WindPoint':('ReWind',7),
 'ThunderPoint':('ReThunder',10),'DefPoint':('DefAll',10),'FaintPoint':('ReFaint',1),
 'PoisonPoint':('RePoison',1),'SealPoint':('ReSeal',1),'DisorderPoint':('ReDisorder',1),
 'DodPoint':('DodValue',1),'AttPoint':('AttValue',1),'SpePoint':('DodAll',1),'GushuPoint':('ReGushu',1)}


WULI_BASE_DAMAGE = {
    6100: (24, 6, 100),
    6101: (33, 10, 150),
    6102: (30, 8, 120),
    6103: (39, 13, 200),
    6104: (36, 12, 160),
}


def wuli_damage(skill, practice, attack, level, target_defense=0):
    """Original ROLE_MAG_FUNCTION base damage, before battle modifiers."""
    rule = WULI_BASE_DAMAGE.get(skill)
    if rule is None:
        raise ValueError('该技能的基础伤害尚未核定。')
    if any(type(x) is not int or x < 0 for x in (practice, attack, level, target_defense)):
        raise ValueError('技能计算参数无效。')
    practice_per_thousand, per_level, base = rule
    fixed = level * per_level + base - target_defense
    return max(1, fixed + (attack * 500 + practice * practice_per_thousand) // 1000)


def schema(db):
    # Called only by Progression initialization, outside its explicit action transaction.
    db.executescript('''
      CREATE TABLE IF NOT EXISTS cultivation_state (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id),
        earned_points INTEGER NOT NULL DEFAULT 0 CHECK(earned_points>=0),
        rebirth INTEGER NOT NULL DEFAULT 0, ascension INTEGER NOT NULL DEFAULT 0,
        sanctification INTEGER NOT NULL DEFAULT 0,
        tongbao INTEGER NOT NULL DEFAULT 0 CHECK(tongbao>=0));
      CREATE TABLE IF NOT EXISTS resistance_points (
        character_id INTEGER NOT NULL REFERENCES characters(id), attribute TEXT NOT NULL,
        value INTEGER NOT NULL CHECK(value BETWEEN 0 AND 115),
        PRIMARY KEY(character_id,attribute));
      CREATE TABLE IF NOT EXISTS cultivated_skills (
        character_id INTEGER NOT NULL REFERENCES characters(id), skill_id INTEGER NOT NULL,
        level INTEGER NOT NULL CHECK(level>=0), PRIMARY KEY(character_id,skill_id));
    ''')


def state(db,cid):
    row=db.execute('SELECT * FROM cultivation_state WHERE character_id=?',(cid,)).fetchone()
    return dict(row) if row else dict(earned_points=0,rebirth=0,ascension=0,sanctification=0,tongbao=0)


def effects(db,cid):
    result={}
    for row in db.execute('SELECT attribute,value FROM resistance_points WHERE character_id=?',(cid,)):
        key,factor=POINT_EFFECTS[row['attribute']]
        result[key]=result.get(key,0)+row['value']*factor
    for row in db.execute('SELECT skill_id,level FROM cultivated_skills WHERE character_id=?',(cid,)):
        if row['level']:
            for key,value in DATA['cultivation']['Skill%dData'%row['skill_id']][row['level']-1]['SkillEffect'].items():
                result[key]=result.get(key,0)+value
    return result


def allocate(progression,points):
    if len(points)!=13 or any(type(n) is not int or not 0<=n<=115 for n in points):
        raise ValueError('每项抗性点须为0至115的整数。')
    with progression.transaction() as db:
        available=state(db,progression.cid)['earned_points']
        if sum(points)>available:raise ValueError('可分配抗性点不足。')
        # The native payload contains absolute totals; replaying it cannot add points twice.
        for key,value in zip(POINTS,points):
            db.execute('INSERT INTO resistance_points VALUES(?,?,?) ON CONFLICT(character_id,attribute) '
                       'DO UPDATE SET value=excluded.value',(progression.cid,key,value))


def grade_number(snapshot,values):
    return snapshot['level']+1000*values['rebirth']+10000*values['ascension']+100000*values['sanctification']


def records(progression,snapshot=None):
    if snapshot is None:snapshot=progression.snapshot()
    saved=snapshot['cultivation_levels']
    current=grade_number(snapshot,snapshot['cultivation_state'])
    # Native dlg_hero_new_resist_skill uses 0=locked, 1=learnable, 2=learned.
    return [dict(skill_id=int(sid),name=meta['Name'],skill_grade=saved.get(int(sid),0),
                 status=2 if int(sid) in saved else 1 if current>=meta['LearnLevel'] else 0)
            for sid,meta in DATA['cultivation']['ResistantBaseConfig'].items()]


def list_packet(progression,snapshot=None):
    return packet(3418,[('array','new_resistant')],[records(progression,snapshot)])


def learn(progression,skill):
    meta=DATA['cultivation']['ResistantBaseConfig'].get(str(skill))
    if meta is None:raise ValueError('不存在此项抗性修炼。')
    with progression.transaction() as db:
        s=progression._snapshot(db)
        if grade_number(s,state(db,progression.cid))<meta['LearnLevel']:raise ValueError('尚未达到该抗性修炼的领悟等级。')
        db.execute('INSERT OR IGNORE INTO cultivated_skills VALUES(?,?,0)',(progression.cid,skill))


def upgrade(progression,skill,count):
    meta=DATA['cultivation']['ResistantBaseConfig'].get(str(skill))
    if meta is None or type(count) is not int or not 1<=count<=10:raise ValueError('抗性修炼次数须为1至10。')
    with progression.transaction() as db:
        s=progression._snapshot(db);values=state(db,progression.cid)
        if grade_number(s,values)<meta['LearnLevel']:raise ValueError('尚未达到该抗性修炼的领悟等级。')
        row=db.execute('SELECT level FROM cultivated_skills WHERE character_id=? AND skill_id=?',(progression.cid,skill)).fetchone()
        if row is None:raise ValueError('请先领悟该项修炼。')
        level=row['level'];maximum=min(meta['MaxLevel'],s['level']+meta['AddLevel'])
        if level+count>maximum:raise ValueError('超过该项修炼上限或角色等级加15级。')
        steps=DATA['cultivation']['Skill%dData'%skill][level:level+count]
        cash=sum(v['Cash'] for v in steps);exp=sum(v['Exp'] for v in steps)
        currency='tongbao' if meta['IsYuanbao'] else 'gold'
        balance=values['tongbao'] if meta['IsYuanbao'] else s['gold']
        if balance<cash or s['experience']<exp:
            raise ValueError(('通宝' if meta['IsYuanbao'] else '现金')+'或角色经验不足。')
        if meta['IsYuanbao']:
            db.execute('UPDATE cultivation_state SET tongbao=tongbao-? WHERE character_id=?',(cash,progression.cid))
        else:db.execute('UPDATE characters SET gold=gold-? WHERE id=?',(cash,progression.cid))
        db.execute('UPDATE characters SET experience=experience-? WHERE id=?',(exp,progression.cid))
        db.execute('UPDATE cultivated_skills SET level=level+? WHERE character_id=? AND skill_id=?',(count,progression.cid,skill))


def upgrade_packet(progression,skill):
    from native946_progression import lua_data
    meta=DATA['cultivation']['ResistantBaseConfig'].get(str(skill))
    if meta is None:raise ValueError('不存在此项抗性修炼。')
    with progression.transaction(write=False) as db:
        row=db.execute('SELECT level FROM cultivated_skills WHERE character_id=? AND skill_id=?',(progression.cid,skill)).fetchone()
        level=row['level'] if row else 0
    # Lists of formatted arguments are unserialized by the original war_data formatter.
    effect=DATA['cultivation']['Skill%dData'%skill][level-1]['SkillEffect'] if level else {}
    args=[effect.get(k,0)/(10 if percent else 1) for k,percent in zip(meta['EffectPos'],meta['SkillAddScale'])]
    return packet(3419,['number','number','string','number','string','string'],
                  [skill,level,'0',0,lua_data(args),''])
