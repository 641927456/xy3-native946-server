"""Classic 946 guild construction, using server-owned building quotes."""
import json
from bisect import bisect_right
from pathlib import Path
from time import time

from native946_codec import packet, text_notice


DATA=json.loads(Path(__file__).with_name('native946_guild_construction_data.json').read_text('utf8'))
REQUESTS={1113,1114,1115,1116,1117,1118,1142,2631}
WRITES={1113,1118,1142}
BUILDINGS=tuple(range(31,52))
HALL=33
TREASURY=34
CAPACITY_BONUS={33:100,36:20,37:20,38:20,39:20}
SCIENCE_THRESHOLDS=(500,4000,13500,32000,62500)


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_resources (
        guild_id INTEGER PRIMARY KEY REFERENCES native_guilds(id) ON DELETE CASCADE,
        fund INTEGER NOT NULL DEFAULT 0 CHECK(fund>=0),
        wood INTEGER NOT NULL DEFAULT 0 CHECK(wood>=0),
        stone INTEGER NOT NULL DEFAULT 0 CHECK(stone>=0),
        science INTEGER NOT NULL DEFAULT 0 CHECK(science>=0),
        science_grade INTEGER NOT NULL DEFAULT 0 CHECK(science_grade>=0),
        rice INTEGER NOT NULL DEFAULT 240 CHECK(rice>=0))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_buildings (
        guild_id INTEGER NOT NULL REFERENCES native_guilds(id) ON DELETE CASCADE,
        building_id INTEGER NOT NULL CHECK(building_id BETWEEN 31 AND 51),
        grade INTEGER NOT NULL CHECK(grade>=1),
        endure INTEGER NOT NULL CHECK(endure>=0),
        max_endure INTEGER NOT NULL CHECK(max_endure>0 AND max_endure>=endure),
        ready_at INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0 CHECK(disabled IN (0,1)),
        PRIMARY KEY(guild_id,building_id))''')
    if 'disabled' not in {row[1] for row in db.execute('PRAGMA table_info(native_guild_buildings)')}:
        db.execute('ALTER TABLE native_guild_buildings ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0 CHECK(disabled IN (0,1))')
        db.execute('UPDATE native_guild_buildings SET disabled=1 WHERE endure=0')
    db.execute('''CREATE TRIGGER IF NOT EXISTS native_guild_building_durability_insert
        AFTER INSERT ON native_guild_buildings WHEN NEW.endure=0 AND NEW.disabled=0
        BEGIN UPDATE native_guild_buildings SET disabled=1
        WHERE guild_id=NEW.guild_id AND building_id=NEW.building_id; END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS native_guild_building_durability_update
        AFTER UPDATE OF endure,max_endure ON native_guild_buildings
        WHEN NEW.endure=0 OR (OLD.disabled=1 AND 2*NEW.endure>=NEW.max_endure)
        BEGIN UPDATE native_guild_buildings SET disabled=(NEW.endure=0)
        WHERE guild_id=NEW.guild_id AND building_id=NEW.building_id; END''')
    # The client receives the quote from the server. No row means the original
    # cost/duration data for that operation has not been supplied.
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_build_costs (
        building_id INTEGER NOT NULL CHECK(building_id BETWEEN 31 AND 51),
        grade INTEGER NOT NULL CHECK(grade>=1),
        fund INTEGER NOT NULL CHECK(fund>=0), wood INTEGER NOT NULL CHECK(wood>=0),
        stone INTEGER NOT NULL CHECK(stone>=0), science INTEGER NOT NULL CHECK(science>=0),
        science_grade INTEGER NOT NULL CHECK(science_grade>=0),
        duration INTEGER NOT NULL CHECK(duration>=0), max_endure INTEGER NOT NULL CHECK(max_endure>0),
        PRIMARY KEY(building_id,grade))''')


def resources(db,guild):
    saved=db.execute('SELECT * FROM native_guild_resources WHERE guild_id=?',(guild,)).fetchone()
    held=dict(saved) if saved else dict(fund=0,wood=0,stone=0,science=0,rice=DATA['start_rice'])
    held['science_grade']=bisect_right(SCIENCE_THRESHOLDS,held['science'])
    return held


def _buildings(db,guild):
    return {r['building_id']:dict(r) for r in db.execute('SELECT * FROM native_guild_buildings WHERE guild_id=?',(guild,))}


def active(row,now):
    return bool(row and row['endure'] and not row['disabled'] and row['ready_at']<=now)


def member_capacity(buildings,base,now):
    bonus=sum(amount for building,amount in CAPACITY_BONUS.items() if active(buildings.get(building),now))
    lodging=buildings.get(45)
    if active(lodging,now):bonus+=10*lodging['grade']
    # The current member list fits a single native array.
    return min(254,base+bonus)


def fund_limit(buildings,now):
    treasury=buildings.get(TREASURY)
    if not treasury or treasury['ready_at']>now:return 0
    return 5_000_000+2_000_000*treasury['grade']


def _build_status(row,now):
    return int(row is not None and row['ready_at']>now)


def _details(db,member,buildings,building,now):
    current=buildings.get(building)
    grade=current['grade'] if current else 0
    fund=resources(db,member['guild_id'])
    quote=db.execute('SELECT * FROM native_guild_build_costs WHERE building_id=? AND grade=?',
                     (building,grade+1)).fetchone()
    maximum=len(DATA['BuildConfig'][str(building)])
    status='未建' if grade==0 else '建设中' if current['ready_at']>now else f'{grade}级'
    if quote is None and grade<maximum:
        return text_notice('该建筑的原版施工费用尚未接入。')
    required_level=DATA['required_guild_level'][str(building)]
    values=[building,DATA['BuildDesc'][str(building)]['name'],status,current['endure'] if current else 0]
    for name in ('fund','wood','stone','science_grade','science'):
        values.extend((quote[name] if quote else 0,fund[name]))
    return packet(1068,['number','string','string']+['number']*12,[*values,required_level])


def _listing(member,buildings,now):
    is_owner=int(member['master']==member['character_id'])
    records=[dict(id=building,endure=row['endure'],grade=row['grade'],maxendure=row['max_endure'],
                  status=_build_status(row,now)) for building,row in buildings.items()]
    busy=any(row['ready_at']>now for row in buildings.values())
    return packet(1069,['number']*6+[('array','org_building_info')],
                  [is_owner,int(busy),member['grade'],0,0,0,records])


def _place(db,member,buildings,building,now,*,upgrade):
    guild=member['guild_id']
    current=buildings.get(building)
    if (current is not None)!=upgrade:raise ValueError('该建筑状态已变化。')
    if any(row['ready_at']>now for row in buildings.values()):
        raise ValueError('已有建筑在建设中。')
    if member['grade']<DATA['required_guild_level'][str(building)]:
        raise ValueError('帮派等级不足。')
    if upgrade and not active(buildings.get(HALL),now):
        raise ValueError('聚义厅尚未建成或耐久不足。')
    grade=current['grade']+1 if current else 1
    if str(grade) not in DATA['BuildConfig'][str(building)]:raise ValueError('该建筑已达到最高等级。')
    quote=db.execute('SELECT * FROM native_guild_build_costs WHERE building_id=? AND grade=?',(building,grade)).fetchone()
    if quote is None:raise ValueError('该建筑原版施工费用尚缺，未扣除帮派资源。')
    db.execute('INSERT OR IGNORE INTO native_guild_resources(guild_id) VALUES(?)',(guild,))
    held=resources(db,guild)
    keys=('fund','wood','stone','science')
    if held['science_grade']<quote['science_grade'] or any(held[key]<quote[key] for key in keys):
        raise ValueError('帮派资源或科技等级不足。')
    db.execute('UPDATE native_guild_resources SET fund=fund-?,wood=wood-?,stone=stone-?,science=science-? WHERE guild_id=?',
               (*(quote[key] for key in keys),guild))
    db.execute('''INSERT INTO native_guild_buildings (guild_id,building_id,grade,endure,max_endure,ready_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,building_id)
        DO UPDATE SET grade=excluded.grade,endure=excluded.endure,max_endure=excluded.max_endure,ready_at=excluded.ready_at''',
        (guild,building,grade,quote['max_endure'],quote['max_endure'],now+quote['duration']))


def receive(game,mid,reader):
    from native946_guilds import _membership

    building=reader.integer() if mid in (1113,1114,1115,1116,1118,2631) else None
    if mid in (1113,2631):reader.integer()
    reader.finish()
    if building is not None and building not in BUILDINGS:raise ValueError('帮派建筑编号无效。')
    if game.battle:raise ValueError('请先结束战斗。')
    now=int(time())
    with game.progression.transaction(write=mid in WRITES) as db:
        member=_membership(db,game.character['id'])
        if member is None:raise ValueError('请先加入帮派。')
        guild=member['guild_id'];buildings=_buildings(db,guild)
        if mid==1117:return _listing(member,buildings,now)
        if mid==1116:return _details(db,member,buildings,building,now)
        if mid in (1114,1115,2631):
            raise ValueError('拆除和维修的原版结算尚缺，未更改建筑或扣料。')
        if member['master']!=game.character['id']:raise ValueError('只有帮主可以办理这项建设。')
        if mid==1142:
            grade=member['grade']
            if grade>=3:raise ValueError('帮派已经达到最高等级。')
            if any((key not in buildings or buildings[key]['ready_at']>now) for key in BUILDINGS
                   if DATA['required_guild_level'][str(key)]<=grade):
                raise ValueError('需要先建成当前等级已解锁的全部建筑。')
            if not active(buildings.get(HALL),now):raise ValueError('请先将聚义厅维修到至少一半耐久。')
            db.execute('UPDATE native_guilds SET grade=grade+1 WHERE id=?',(guild,))
            return _listing(dict(member,grade=grade+1),buildings,now),text_notice('帮派已升级。')
        _place(db,member,buildings,building,now,upgrade=mid==1118)
        return _listing(member,_buildings(db,guild),now)
