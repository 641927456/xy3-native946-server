"""946 guild recruitment, founding and member management."""
from datetime import datetime, timedelta, timezone
from time import time

from native946_codec import packet, text_notice


RECRUITER = (2052, 3443)  # 唐人茶楼·智黎华
REFERENCE_WIRE_VERSION = 1
MIN_LEVEL = 50
MASTER, FELLOW, APPLICANT, ALL = 2, 19, 20, 26
CORE_GROUP=21
# 946 INFO_ORG.GangHelpConfig establishes the fee. This server founds immediately.
# Starting capacity remains sourced from the supplied reference server.
FOUNDING_CASH=2000
FOUNDING_CAPACITY=40
DISBAND_SECONDS=24*3600
REQUESTS = {1107, 1108, 1109, 1119, 1125, 1126, 1129, 1130, 1131, 1135, 1136, 1137, 1140}
WRITES = {1107, 1108, 1109, 1119, 1135, 1136}


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_guilds (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, tenet TEXT NOT NULL,
        founder INTEGER NOT NULL REFERENCES characters(id),
        master INTEGER NOT NULL REFERENCES characters(id),
        grade INTEGER NOT NULL CHECK(grade>=1),
        capacity INTEGER NOT NULL CHECK(capacity BETWEEN 1 AND 254),
        underfull_since INTEGER NOT NULL DEFAULT 0)''')
    if 'underfull_since' not in {row[1] for row in db.execute('PRAGMA table_info(native_guilds)')}:
        db.execute('ALTER TABLE native_guilds ADD COLUMN underfull_since INTEGER NOT NULL DEFAULT 0')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_members (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        guild_id INTEGER NOT NULL REFERENCES native_guilds(id) ON DELETE CASCADE,
        joined INTEGER NOT NULL,
        office INTEGER NOT NULL DEFAULT 19, member_group INTEGER NOT NULL DEFAULT 19,
        can_receive INTEGER NOT NULL DEFAULT 0 CHECK(can_receive IN (0,1)),
        can_remove INTEGER NOT NULL DEFAULT 0 CHECK(can_remove IN (0,1)))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_applications (
        guild_id INTEGER NOT NULL REFERENCES native_guilds(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        submitted INTEGER NOT NULL,
        declaration TEXT NOT NULL,
        PRIMARY KEY(guild_id,character_id))''')
    db.execute('CREATE TABLE IF NOT EXISTS native_guild_sequence (singleton INTEGER PRIMARY KEY CHECK(singleton=1),last_id INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO native_guild_sequence VALUES(1,0)')
    _upgrade_waiting_foundings(db,int(time()))


def _membership(db, cid):
    return db.execute('SELECT m.*,g.master,g.name,g.tenet,g.grade,g.capacity '
                      'FROM native_guild_members m JOIN native_guilds g ON g.id=m.guild_id '
                      'WHERE m.character_id=? AND (g.underfull_since=0 OR g.underfull_since>?)',
                      (cid,int(time())-DISBAND_SECONDS)).fetchone()


def _organizations(db):
    return db.execute('''SELECT g.*,c.name master_name,f.name founder_name,
        (SELECT count(*) FROM native_guild_members m WHERE m.guild_id=g.id) member_count
        FROM native_guilds g JOIN characters c ON c.id=g.master JOIN characters f ON f.id=g.founder
        WHERE g.underfull_since=0 OR g.underfull_since>?
        ORDER BY g.id''',(int(time())-DISBAND_SECONDS,))


def _member_count_changed(db, guild, now):
    count=db.execute('SELECT count(*) FROM native_guild_members WHERE guild_id=?',(guild,)).fetchone()[0]
    db.execute('UPDATE native_guilds SET underfull_since=CASE WHEN ?>=20 THEN 0 '
               'WHEN underfull_since=0 THEN ? ELSE underfull_since END WHERE id=?',(count,now,guild))


def _remove_expired_guilds(db, now):
    db.execute('DELETE FROM native_guilds WHERE underfull_since>0 AND underfull_since<=?',(now-DISBAND_SECONDS,))


def _create_guild(db,guild,name,tenet,founder,now):
    db.execute('INSERT INTO native_guilds(id,name,tenet,founder,master,grade,capacity) VALUES(?,?,?,?,?,1,?)',
               (guild,name,tenet,founder,founder,FOUNDING_CAPACITY))
    db.execute('INSERT INTO native_guild_members(character_id,guild_id,joined,office,member_group) VALUES(?,?,?,?,?)',
               (founder,guild,now,MASTER,CORE_GROUP))
    db.execute('DELETE FROM native_guild_applications WHERE character_id=?',(founder,))
    db.execute('UPDATE native_guild_sequence SET last_id=max(last_id,?) WHERE singleton=1',(guild,))


def _upgrade_waiting_foundings(db,now):
    legacy={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('native_guild_founding','native_guild_responses')")}
    if 'native_guild_founding' not in legacy:return
    reserved=db.execute('SELECT coalesce(max(id),0) FROM native_guild_founding').fetchone()[0]
    db.execute('UPDATE native_guild_sequence SET last_id=max(last_id,?) WHERE singleton=1',(reserved,))
    drafts=db.execute('''SELECT f.id,f.name,f.tenet,f.founder FROM native_guild_founding f
        JOIN characters c ON c.id=f.founder WHERE f.expires>?
        AND NOT EXISTS(SELECT 1 FROM native_guilds g WHERE g.id=f.id OR g.name=f.name)
        AND NOT EXISTS(SELECT 1 FROM native_guild_members m WHERE m.character_id=f.founder)
        ORDER BY f.id''',(now,)).fetchall()
    for guild,name,tenet,founder in drafts:
        _create_guild(db,guild,name,tenet,founder,now)
        if 'native_guild_responses' in legacy:
            db.execute('DELETE FROM native_guild_responses WHERE guild_id=?',(guild,))
        db.execute('DELETE FROM native_guild_founding WHERE id=?',(guild,))


def _near_recruiter(game):
    from native946_npc_services import NpcServices

    npc=NpcServices(game).validate(RECRUITER[1])
    if npc['scene_id']!=RECRUITER[0]:raise ValueError('请前往唐人茶楼找智黎华。')


def _found(db, cid, raw_name, raw_tenet, now):
    from native946_refinement import debit

    name=raw_name.decode('gb18030');tenet=raw_tenet.decode('gb18030').strip()
    if not 4<=len(raw_name)<=14 or not all(ch.isalnum() for ch in name):
        raise ValueError('帮派名称须为 4 至 14 字节的文字或字母数字。')
    if not tenet or len(tenet)>250 or '#' in tenet or any(not ch.isprintable() for ch in tenet):
        raise ValueError('请填写有效的帮派宗旨。')
    level=db.execute('SELECT level FROM characters WHERE id=?',(cid,)).fetchone()[0]
    if level<MIN_LEVEL or _membership(db,cid):raise ValueError('50 级且尚未加入帮派才能申请建帮。')
    if db.execute('SELECT 1 FROM native_guilds WHERE name=?',(name,)).fetchone():
        raise ValueError('这个帮派名称已被使用。')
    number=db.execute('SELECT max(id) FROM (SELECT id FROM native_guilds UNION ALL SELECT last_id AS id FROM native_guild_sequence)').fetchone()[0]+1
    debit(db,cid,FOUNDING_CASH)
    _create_guild(db,number,name,tenet,cid,now)
    return text_notice('帮派已成立，你已成为帮主。')


def introduction(game, nid, action):
    from native946_npc_services import NpcServices

    npc = NpcServices(game).validate(nid)
    if (npc['scene_id'], nid) != RECRUITER:
        raise ValueError('请前往唐人茶楼找智黎华。')
    if action=='create':
        return packet(1071,[],[])
    if action!='list':raise ValueError('帮派引荐操作无效。')
    with game.progression.transaction(write=False) as db:
        rows = [dict(id=g['id'], tenet=g['tenet'], name=g['name'], grade=g['grade'],
                     master_name=g['master_name'], master_id=g['master'], count=g['member_count'])
                for g in _organizations(db)][:254]
    return packet(1065, [('array', 'org_data')], [rows])


def _notify_after_commit(game, replies, recipients, message):
    def deliver():
        service = game.party_service
        with service.lock:
            for cid in recipients:
                other = service.online(cid)
                if other is not None:
                    service.enqueue(other, text_notice(message))
        return replies
    return game.defer_packets(deliver, fallback=replies)


def _apply(db, game, guild, declaration):
    cid = game.character['id']
    level = db.execute('SELECT level FROM characters WHERE id=?', (cid,)).fetchone()[0]
    if level < MIN_LEVEL:
        raise ValueError('达到50级才能申请加入帮派。')
    if _membership(db, cid):
        raise ValueError('你已经加入帮派。')
    if db.execute('SELECT 1 FROM native_guilds WHERE id=?', (guild,)).fetchone() is None:
        raise ValueError('该帮派不存在。')
    inserted = db.execute('INSERT INTO native_guild_applications VALUES(?,?,?,?) '
                          'ON CONFLICT(guild_id,character_id) DO NOTHING', (guild, cid, int(time()), declaration))
    if not inserted.rowcount:
        return text_notice('你已提交入帮申请，请等待处理。')
    recipients = [row[0] for row in db.execute('''SELECT m.character_id FROM native_guild_members m
        JOIN native_guilds g ON g.id=m.guild_id
        WHERE g.id=? AND (m.character_id=g.master OR m.can_receive<>0)''', (guild,))]
    return _notify_after_commit(game, text_notice('入帮申请已提交。'), recipients,
                                f"{game.character['name']}申请加入帮派。")


def _member_list(db, game, membership, list_id):
    from native946_nice_ids import identity

    guild = membership['guild_id']
    if list_id == APPLICANT:
        if not (membership['master'] == membership['character_id'] or membership['can_receive']):
            raise ValueError('你没有查看入帮申请的权限。')
        rows = db.execute('SELECT c.*,a.submitted joined,a.declaration,20 office,20 member_group '
                          'FROM native_guild_applications a JOIN characters c ON c.id=a.character_id '
                          'WHERE a.guild_id=? ORDER BY a.submitted,c.id LIMIT 254', (guild,))
    else:
        rows = db.execute('SELECT c.*,m.joined,m.office,m.member_group,\'\' declaration '
                          'FROM native_guild_members m JOIN characters c ON c.id=m.character_id '
                          'WHERE m.guild_id=? AND (?=? OR m.member_group=?) ORDER BY m.office,c.id LIMIT 254',
                          (guild, list_id, ALL, list_id))
    out = []
    for r in rows:
        cid = r['id']
        online = game.party_service.online(cid) is not None
        out.append(dict(id=cid, name=r['name'], grade=r['level'], icon=r['role_id'], race=r['race'], sex=r['sex'],
                        office=r['office'], group=r['member_group'], declaration=r['declaration'], join_time=r['joined'],
                        showid=identity(db, cid)['showid'], offline_time='在线' if online else '离线'))
    return packet(1084, ['number', ('array', 'org_member_info')], [list_id, out])


def receive(game, mid, reader):
    cid = game.character['id']
    if game.battle:
        raise ValueError('请先结束战斗。')
    target = reader.integer() if mid in (1107, 1108, 1109, 1131, 1135, 1136, 1137, 1140) else 0
    extra = reader.integer() if mid in (1109,1135) else 0
    appointment = reader.integer() if mid == 1109 else 0
    declaration = reader.string().decode('gb18030') if mid == 1107 else ''
    if mid == 1119:
        name,tenet=reader.string(),reader.string()
    reader.finish()
    if mid==1137:
        return text_notice('现在缴费即可直接建立帮派。')
    if mid==1119:
        _near_recruiter(game)
        now=int(time())
        with game.progression.transaction() as db:
            _remove_expired_guilds(db,now)
            reply=_found(db,cid,name,tenet,now)
        return game.defer_packets(lambda:(*game.progression.attribute_packets(),reply))
    if mid == 1107 and (len(declaration) > 250 or '#' in declaration or any(not c.isprintable() for c in declaration)):
        raise ValueError('入帮说明无效。')
    with game.progression.transaction(write=mid in WRITES) as db:
        if mid in WRITES:_remove_expired_guilds(db,int(time()))
        if mid == 1107:
            return _apply(db, game, target, declaration)
        if mid == 1108:
            other = _membership(db, target)
            if other is None:
                raise ValueError('该角色还没有加入帮派。')
            return _apply(db, game, other['guild_id'], '')
        if mid == 1140:
            found = db.execute('SELECT tenet FROM native_guilds WHERE id=? AND (underfull_since=0 OR underfull_since>?)',
                               (target,int(time())-DISBAND_SECONDS)).fetchone()
            if found is None:
                raise ValueError('该帮派不存在。')
            return packet(1092, ['number', 'string'], [target, found[0]])
        member = _membership(db, cid)
        if member is None:
            raise ValueError('请到唐人茶楼找智黎华申请加入帮派。')
        guild = member['guild_id']
        leader = member['master'] == cid
        office = MASTER if leader else member['office']
        accept = leader or member['can_receive']
        remove = leader or member['can_remove']
        if mid == 1125:
            from native946_guild_construction import _buildings, fund_limit, member_capacity, resources

            detail = next(g for g in _organizations(db) if g['id'] == guild)
            held=resources(db,guild)
            buildings=_buildings(db,guild)
            now=int(time())
            info = dict(detail,creater_name=detail['founder_name'],
                        member_max_count=member_capacity(buildings,detail['capacity'],now),
                        fund=held['fund'],max_fund=fund_limit(buildings,now),
                        science=held['science'],science_grade=held['science_grade'])
            core=[dict(row) for row in db.execute('''SELECT c.id,c.name,c.role_id icon,m.office title
                    FROM native_guild_members m JOIN characters c ON c.id=m.character_id
                    WHERE m.guild_id=? AND m.member_group=? ORDER BY m.office,c.id''',(guild,CORE_GROUP))]
            return packet(3506, ['org_detail', ('array', 'string'), ('array', 'org_core_member'),
                                 ('array', 'org_resource_info'), 'number'], [info, [], core, [], 0])
        if mid == 1126:
            return packet(1078, ['number']*3, [office, accept, remove])
        if mid == 1129:
            joined = datetime.fromtimestamp(member['joined'], timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
            return packet(1082, ['string', 'string'] + ['number']*8,
                          [member['name'], joined, office, member['member_group'], 0, 0, 0, 0, 0, 0])
        if mid == 1130:
            return packet(1083, ['byte']*4, [0, int(remove), int(leader), int(accept)])
        if mid == 1131:
            if target not in range(FELLOW, ALL+1):
                raise ValueError('帮派成员分组无效。')
            return _member_list(db, game, member, target)
        if mid == 1109 or (mid==1135 and target==4):
            if not leader:raise ValueError('只有帮主可以任免帮派职位。')
            recipient=target if mid==1109 else extra
            other=_membership(db,recipient)
            if other is None or other['guild_id']!=guild or recipient==cid:
                raise ValueError('请选择本帮其他成员。')
            new_office,new_group=(appointment,extra) if mid==1109 else (FELLOW,FELLOW)
            counts={3:1,4:1,5:1,6:2}
            if new_office in counts and new_group==CORE_GROUP:
                used=db.execute('SELECT count(*) FROM native_guild_members WHERE guild_id=? AND office=? AND character_id<>?',
                                (guild,new_office,recipient)).fetchone()[0]
                if used>=counts[new_office]:raise ValueError('该职位人数已满。')
            elif (new_office,new_group)==(FELLOW,FELLOW):
                pass
            elif new_office==MASTER and new_group==CORE_GROUP:
                db.execute('UPDATE native_guilds SET master=? WHERE id=?',(recipient,guild))
                db.execute('UPDATE native_guild_members SET office=?,member_group=?,can_receive=0,can_remove=0 WHERE character_id=?',
                           (FELLOW,FELLOW,cid))
            else:
                raise ValueError('该职位任命暂未开放。')
            db.execute('UPDATE native_guild_members SET office=?,member_group=?,can_receive=0,can_remove=0 WHERE character_id=?',
                       (new_office,new_group,recipient))
            replies = (_member_list(db,game,member,ALL),text_notice('帮派职位已更新。'))
            return _notify_after_commit(game, replies, [recipient], f"你在{member['name']}的职位已更新。")
        if mid == 1136 and target == 1:
            if leader:
                raise ValueError('请先移交帮主职位。')
            db.execute('DELETE FROM native_guild_members WHERE character_id=?', (cid,))
            _member_count_changed(db,guild,int(time()))
            return text_notice('已离开帮派。')
        if mid != 1135 or target not in (1, 2, 3):
            raise ValueError('该帮派管理操作暂未开放。')
        applicant = extra
        if target in (1, 2):
            if not accept:
                raise ValueError('你没有处理入帮申请的权限。')
            change = db.execute('DELETE FROM native_guild_applications WHERE guild_id=? AND character_id=?', (guild, applicant))
            if not change.rowcount:
                raise ValueError('入帮申请已不存在。')
            if target == 1:
                from native946_guild_construction import _buildings, member_capacity

                count = db.execute('SELECT count(*) FROM native_guild_members WHERE guild_id=?', (guild,)).fetchone()[0]
                if count >= member_capacity(_buildings(db,guild),member['capacity'],int(time())) or _membership(db, applicant):
                    raise ValueError('帮派已满，或该角色已加入帮派。')
                db.execute('INSERT INTO native_guild_members(character_id,guild_id,joined) VALUES(?,?,?)',
                           (applicant, guild, int(time())))
                db.execute('DELETE FROM native_guild_applications WHERE character_id=?', (applicant,))
                _member_count_changed(db,guild,int(time()))
            approved = target == 1
            replies = (_member_list(db, game, member, APPLICANT), text_notice('已批准入帮申请。' if approved else '已拒绝入帮申请。'))
            result = '批准' if approved else '拒绝'
            return _notify_after_commit(game, replies, [applicant], f"{member['name']}已{result}你的入帮申请。")
        if not remove or applicant in (member['master'], cid):
            raise ValueError('你没有驱逐该成员的权限。')
        other = _membership(db, applicant)
        if other is None or other['guild_id'] != guild or (not leader and other['office'] != FELLOW):
            raise ValueError('不能驱逐该成员。')
        db.execute('DELETE FROM native_guild_members WHERE character_id=? AND guild_id=?', (applicant, guild))
        _member_count_changed(db,guild,int(time()))
        replies = (packet(1072, ['number', 'number'], [other['member_group'], applicant]), text_notice('已请离该帮派成员。'))
        return _notify_after_commit(game, replies, [applicant], f"你已被请离{member['name']}。")
