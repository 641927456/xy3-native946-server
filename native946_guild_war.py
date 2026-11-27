"""Classic 946 guild war registration and capture-the-flag deployment."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from time import time

from native946_codec import packet, text_notice


REQUESTS={3847,3860,4958,4975}
WRITES={3860,4958,4975}
DQ=6
WAR_TYPES={1:'群雄逐鹿',2:'临墟战场',3:'财神送祝福',4:'星辰争夺战',5:'矿脉争夺战',6:'帮派夺旗'}
WAR_NPCS={(2006,2525),(2006,2526)}
ZONE=timezone(timedelta(hours=8))
CROSS_WAR=2  # WAR_TYPE.SERVER_GANG_WAR in the 946 client.
MATCH_DURATION=3600
ENTRY_BEFORE=30*60
ENTRY_AFTER=15*60
MIN_PLAYER_LEVEL=80
MIN_MEMBERSHIP_DAYS=2


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_war_seasons (
        id INTEGER PRIMARY KEY,
        mode INTEGER NOT NULL CHECK(mode IN (1,2)),
        signup_at INTEGER NOT NULL, signup_until INTEGER NOT NULL,
        ends_at INTEGER NOT NULL CHECK(ends_at>signup_until AND signup_until>signup_at),
        fee INTEGER NOT NULL CHECK(fee>=0),
        cancel_refund INTEGER NOT NULL CHECK(cancel_refund BETWEEN 0 AND fee))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_war_signups (
        season_id INTEGER NOT NULL REFERENCES native_guild_war_seasons(id) ON DELETE CASCADE,
        guild_id INTEGER NOT NULL REFERENCES native_guilds(id) ON DELETE CASCADE,
        signed_by INTEGER NOT NULL REFERENCES characters(id),
        signed_at INTEGER NOT NULL,
        PRIMARY KEY(season_id,guild_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_war_fixtures (
        season_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
        fight_at INTEGER NOT NULL, fight_type INTEGER NOT NULL CHECK(fight_type BETWEEN 1 AND 6),
        width INTEGER NOT NULL CHECK(width>0), height INTEGER NOT NULL CHECK(height>0),
        PRIMARY KEY(season_id,guild_id,fight_at),
        FOREIGN KEY(season_id,guild_id) REFERENCES native_guild_war_signups(season_id,guild_id) ON DELETE CASCADE)''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_war_formations (
        season_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, fight_at INTEGER NOT NULL,
        guards TEXT NOT NULL, patron INTEGER NOT NULL CHECK(patron IN (0,101,102,103)),
        PRIMARY KEY(season_id,guild_id,fight_at),
        FOREIGN KEY(season_id,guild_id,fight_at) REFERENCES native_guild_war_fixtures(season_id,guild_id,fight_at) ON DELETE CASCADE)''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_war_training (
        guild_id INTEGER NOT NULL REFERENCES native_guilds(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        points TEXT NOT NULL,
        PRIMARY KEY(guild_id,character_id))''')


def _season(db,now):
    return db.execute('SELECT * FROM native_guild_war_seasons WHERE signup_at<=? AND ends_at>? '
                      'ORDER BY signup_at DESC LIMIT 1',(now,now)).fetchone()


def _fixture(db,season,guild,now):
    today=datetime.fromtimestamp(now,ZONE)
    start=int(today.replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
    return db.execute('SELECT * FROM native_guild_war_fixtures WHERE season_id=? AND guild_id=? '
                      'AND fight_at>=? AND fight_at<? ORDER BY fight_at LIMIT 1',
                      (season,guild,start,start+86400)).fetchone()


def _signed(db,season,guild):
    return db.execute('SELECT 1 FROM native_guild_war_signups WHERE season_id=? AND guild_id=?',
                      (season,guild)).fetchone() is not None


def _round_type(season,day):
    # INFO_GANG_WAR_CONFIG.GangWarHelpConfig, classic 946.
    weekday=day.weekday()
    if weekday==4:return 1
    if weekday==6:return 5
    if weekday==5 and season['mode']==CROSS_WAR:
        started=datetime.fromtimestamp(season['signup_at'],ZONE).date()
        week=(day-started).days//7
        return DQ if week%2 else 2
    return None


def _scheduled_type(season,now):
    today=datetime.fromtimestamp(now,ZONE).date()
    end=datetime.fromtimestamp(season['ends_at']-1,ZONE).date()
    for distance in range(min(6,(end-today).days)+1):
        kind=_round_type(season,today+timedelta(days=distance))
        if kind is not None:return kind
    return 1


def _main(db,member,season,now):
    from native946_progression import lua_data

    if season is None:
        return text_notice('当前没有开放的帮派竞赛赛季。常规服周五为群雄逐鹿，周日为矿脉争夺战。跨服赛季第1、3周周六为临墟战场，第2、4周周六为帮派夺旗。')
    guild=member['guild_id'];signed=_signed(db,season['id'],guild)
    count=db.execute('SELECT count(*) FROM native_guild_members WHERE guild_id=?',(guild,)).fetchone()[0]
    own=dict(OrgName=member['name'],OrgId=guild,OrgCount=count,Rank=0,Score=0,ServerName='')
    fixture=_fixture(db,season['id'],guild,now)
    war_type=fixture['fight_type'] if fixture else _scheduled_type(season,now)
    opening=season['signup_at']<=now<season['signup_until']
    can_sign=opening and member['grade']>=2 and member['master']==member['character_id']
    state=(2 if signed else 1) if can_sign else 0
    return packet(3845,['byte','byte','string','string','string','byte','byte','byte','byte'],
                  [season['mode'],war_type,WAR_TYPES[war_type],lua_data(own),lua_data({}),int(opening),state,0,0])


def _registration(db,member,season,now,op):
    from native946_guild_construction import resources

    if season is None or not season['signup_at']<=now<season['signup_until']:
        raise ValueError('当前不在本赛季的报名时段。')
    if member['master']!=member['character_id'] or member['grade']<2:
        raise ValueError('仅 2 级及以上帮派的帮主可以办理报名。')
    guild=member['guild_id'];registered=_signed(db,season['id'],guild)
    if op==1 and not registered:
        if resources(db,guild)['fund']<season['fee']:raise ValueError('帮派资金不足。')
        db.execute('INSERT OR IGNORE INTO native_guild_resources(guild_id) VALUES(?)',(guild,))
        db.execute('UPDATE native_guild_resources SET fund=fund-? WHERE guild_id=?',(season['fee'],guild))
        db.execute('INSERT INTO native_guild_war_signups VALUES(?,?,?,?)',
                   (season['id'],guild,member['character_id'],now))
    if op==2 and registered:
        db.execute('DELETE FROM native_guild_war_signups WHERE season_id=? AND guild_id=?',(season['id'],guild))
        db.execute('UPDATE native_guild_resources SET fund=fund+? WHERE guild_id=?',(season['cancel_refund'],guild))
    return _main(db,member,season,now)


def _formation(db,member,season,now,op,guards,patron):
    if season is None:raise ValueError('当前没有帮派竞赛安排。')
    guild=member['guild_id'];fixture=_fixture(db,season['id'],guild,now)
    if fixture is None or fixture['fight_type']!=DQ:
        raise ValueError('当前没有本帮的夺旗赛程。')
    lineup=(season['id'],guild,fixture['fight_at'])
    deadline=fixture['fight_at']-35*60  # 20:30 开赛，19:55 截止布阵。
    is_master=member['master']==member['character_id']
    can_edit=is_master and now<deadline
    if op==2:
        if not can_edit:raise ValueError('只有帮主可在夺旗战比赛日 19:55 前布阵。')
        counts=Counter();spots=set()
        for row in guards:
            x,y,kind,count=(row[k] for k in ('x','y','type','count'))
            if (not 0<=x<fixture['width'] or not 0<=y<fixture['height'] or kind not in (1,2,3)
                    or not 1<=count<=20 or (x,y) in spots or (0<=x<=60 and 150<=y<=190)):
                raise ValueError('守卫类型、位置或数量无效。')
            spots.add((x,y));counts[kind]+=count
        if any(v>20 for v in counts.values()) or sum(counts.values())>60 or patron not in (0,101,102,103):
            raise ValueError('守卫数量或守护神选择无效。')
        db.execute('''INSERT INTO native_guild_war_formations VALUES(?,?,?,?,?)
            ON CONFLICT(season_id,guild_id,fight_at) DO UPDATE SET guards=excluded.guards,patron=excluded.patron''',
            (*lineup,json.dumps(guards,separators=(',',':')),patron))
    saved=db.execute('SELECT guards,patron FROM native_guild_war_formations WHERE season_id=? AND guild_id=? AND fight_at=?',lineup).fetchone()
    return packet(4957,['byte','number',('array','orgfight2023_lineup'),'number'],
                  [int(can_edit),deadline,json.loads(saved['guards']) if saved else [],saved['patron'] if saved and is_master else 0])


def training_panel(game,db,member,now,points=None):
    from native946_guild_construction import DATA, _buildings, active

    guild=member['guild_id'];cid=member['character_id']
    hall=_buildings(db,guild).get(46)
    if not active(hall,now):
        raise ValueError('需要已建成且耐久有效的演武台。')
    tier=hall['grade']
    if not 1<=tier<=3:raise ValueError('演武台等级无效。')
    maximum,per_stat=50*tier,20+10*tier
    keys=list(DATA['FightResistConfig'])
    if points is not None:
        season=_season(db,now)
        match=_fixture(db,season['id'],guild,now) if season else None
        if match and match['fight_at']<=now<match['fight_at']+MATCH_DURATION:
            raise ValueError('帮战期间不能调整帮派锻体。')
        if len(points)!=len(keys) or any(not 0<=p<=per_stat for p in points) or sum(points)>maximum:
            raise ValueError('超过演武台等级允许分配的点数。')
        db.execute('INSERT INTO native_guild_war_training VALUES(?,?,?) ON CONFLICT(guild_id,character_id) '
                   'DO UPDATE SET points=excluded.points',(guild,cid,json.dumps(points)))
    row=db.execute('SELECT points FROM native_guild_war_training WHERE guild_id=? AND character_id=?',(guild,cid)).fetchone()
    points=json.loads(row[0]) if row else [0]*len(keys)
    if sum(points)>maximum or any(p>per_stat for p in points):
        points=[0]*len(keys)
    snapshot=game.progression.snapshot()
    current=[snapshot['cultivation'].get(DATA['FightResistConfig'][k]['Attr'],0) for k in keys]
    return packet(4963,['number','number',('array','number'),('array','number')],[maximum,per_stat,points,current])


def _preflight(game,db,member,season,now):
    if season is None or not _signed(db,season['id'],member['guild_id']):raise ValueError('本帮未报名本赛季。')
    if game.character['level']<MIN_PLAYER_LEVEL or now-member['joined']<MIN_MEMBERSHIP_DAYS*86400:
        raise ValueError('要求达到80级且已入帮至少2天。')
    match=_fixture(db,season['id'],member['guild_id'],now)
    if match is None:raise ValueError('本帮今日没有可进入的帮战场次。')
    if not match['fight_at']-ENTRY_BEFORE<=now<match['fight_at']+ENTRY_AFTER:
        raise ValueError('帮战进场时段为比赛日20:00至20:45。')
    raise ValueError('战场实例及周活跃度核销尚未接入，未扣除现金。')


def open_at_npc(game,nid,op):
    from native946_guilds import _membership
    from native946_npc_services import NpcServices

    npc=NpcServices(game).validate(nid)
    if op=='panel' and (npc['scene_id'],nid) not in WAR_NPCS:
        raise ValueError('请向帮战管理员查询。')
    now=int(time())
    with game.progression.transaction(write=False) as db:
        member=_membership(db,game.character['id'])
        if member is None:raise ValueError('请先加入帮派。')
        if op=='training':return training_panel(game,db,member,now)
        return _main(db,member,_season(db,now),now)


def receive(game,mid,reader):
    from native946_guilds import _membership

    guard_rows=[];patron=0;points=None
    op=reader.take(1)[0] if mid in (3860,4958) else 0
    if mid==4958:
        count=reader.take(1)[0]
        if count>60 or op not in (1,2) or (op==1 and count):raise ValueError('布阵操作无效。')
        for _ in range(count):
            guard_rows.append(dict(y=reader.integer(),type=reader.take(1)[0],count=reader.integer(),x=reader.integer()))
        patron=reader.integer()
    if mid==4975:
        count=reader.take(1)[0]
        if count!=13:raise ValueError('帮派锻体属性数量无效。')
        points=[reader.integer() for _ in range(count)]
    reader.finish()
    if game.battle:raise ValueError('请先结束战斗。')
    now=int(time())
    with game.progression.transaction(write=mid in WRITES and (mid!=4958 or op==2)) as db:
        member=_membership(db,game.character['id'])
        if member is None:raise ValueError('请先加入帮派。')
        season=_season(db,now)
        if mid==3847:return _main(db,member,season,now)
        if mid==4975:return training_panel(game,db,member,now,points)
        if mid==4958:return _formation(db,member,season,now,op,guard_rows,patron)
        if op==3:return _preflight(game,db,member,season,now)
        if op not in (1,2):raise ValueError('帮战报名操作无效。')
        return _registration(db,member,season,now,op)
