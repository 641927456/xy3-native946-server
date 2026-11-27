"""Same-server contacts using the Classic 946 friend protocol."""
from native946_codec import packet, text_notice
from native946_nice_ids import identity
from native946_storage import StorageBusy, transaction


NORMAL = 1
BLOCKED = 2
TEMPORARY = 32
SYSTEM_GROUPS = {NORMAL, BLOCKED, TEMPORARY, 33, 34, 35, 36}
CUSTOM_GROUPS = range(3, TEMPORARY)
REQUESTS = frozenset({596, 597, 598, 599, 600, 602, 603, 604, 605, 606, 607, 608, 609, 610, 611})
WRITES = frozenset({599, 600, 603, 604, 605, 606, 607, 608, 610})


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_contacts (
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        target_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        group_id INTEGER NOT NULL,
        alias TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(character_id, target_id), CHECK(character_id <> target_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS native_contacts_target ON native_contacts(target_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS native_contact_groups (
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        group_id INTEGER NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0,1)),
        PRIMARY KEY(character_id, group_id))''')


def fields(mid, *values):
    return packet(mid, ['number'] * len(values), values)


def clean_text(raw, limit, *, allow_empty=False):
    text = raw.decode('gb18030').strip()
    if (not text and not allow_empty or len(raw) > limit
            or any(not char.isprintable() or char == '#' for char in text)):
        raise ValueError('名称或备注格式不正确。')
    return text


def visible(game):
    return int(game is not None and getattr(game, '_friend_visible', True))


def presence_changed(game):
    """Presence is advisory; a busy read must never interrupt login or logout."""
    service = game.party_service
    cid = game.character['id']
    try:
        with service.lock, transaction(service.database) as db:
            online = visible(service.online(cid))
            watchers = db.execute('SELECT character_id FROM native_contacts WHERE target_id=? AND group_id<>?',
                                  (cid, BLOCKED)).fetchall()
            for (viewer_id,) in watchers:
                viewer = service.online(viewer_id)
                if viewer is not None:
                    service.enqueue(viewer, fields(593, cid, online))
    except StorageBusy:
        return


class Friends:
    def __init__(self, game):
        self.game = game
        self.progression = game.progression
        self.service = game.party_service
        self.cid = game.character['id']

    def target(self, db, target_id, *, search=False):
        # The search box accepts a displayed nice ID as well as a character ID.
        if search:
            lease = db.execute('SELECT character_id FROM nice_id_leases WHERE showid=? AND active=1',
                               (target_id,)).fetchone()
            if lease and identity(db, lease[0])['showid'] == target_id:
                target_id = lease[0]
        result = db.execute('SELECT * FROM characters WHERE id=?', (target_id,)).fetchone()
        if result is None:
            raise ValueError('没有找到该角色。')
        return result

    def record(self, db, row, group=NORMAL, alias=''):
        from native946_mall_portraits import current as portrait
        from native946_role_growth import jing, school
        from native946_school import base_school
        from native946_training import state as training

        cid = row['id']
        stage = training(db, cid)
        return dict(id=cid, name=row['name'], shape=row['role_id'],
                    icon=portrait(db, cid) or row['role_id'], race=row['race'], sex=row['sex'],
                    school=base_school(school(db, cid)), level=row['level'],
                    rei=stage['rebirth'], fly=stage['ascension'], feng=stage['sanctification'],
                    jing=jing(db, cid)['level'], relation='0', group_id=group, alias=alias,
                    showid=identity(db, cid)['showid'])

    def initial_packets(self):
        with self.service.lock, self.progression.transaction(write=False) as db:
            groups = db.execute('SELECT group_id,name,hidden FROM native_contact_groups WHERE character_id=? ORDER BY group_id',
                                (self.cid,)).fetchall()
            contacts = db.execute('''SELECT c.*,f.group_id,f.alias FROM native_contacts f
                JOIN characters c ON c.id=f.target_id WHERE f.character_id=? ORDER BY f.target_id''', (self.cid,)).fetchall()
            records = [self.record(db, row, row['group_id'], row['alias']) for row in contacts]
            online = [fields(593, row['id'], 1) for row in contacts
                      if row['group_id'] != BLOCKED and visible(self.service.online(row['id']))]
            return (*(packet(586, ['number', 'string'], [group, name]) for group, name, _ in groups if name),
                    packet(591, [('array', 'friend_info')], [records]),
                    *(fields(588, group, 1) for group, _, hidden in groups if hidden), *online)

    def contact(self, db, target):
        row = db.execute('SELECT group_id,alias FROM native_contacts WHERE character_id=? AND target_id=?',
                         (self.cid, target)).fetchone()
        if row is None:
            raise ValueError('该角色不在当前好友列表。')
        return row

    def group(self, db, group):
        row = db.execute('SELECT name,hidden FROM native_contact_groups WHERE character_id=? AND group_id=?',
                         (self.cid, group)).fetchone()
        if group not in SYSTEM_GROUPS and row is None:
            raise ValueError('好友分组不存在。')
        return row

    def search(self, mid, reader, db):
        if mid == 597:
            target = reader.integer()
            reader.finish()
            row = self.target(db, target, search=True)
        else:
            name = clean_text(reader.string(), 14)
            reader.finish()
            row = db.execute('SELECT * FROM characters WHERE name=?', (name,)).fetchone()
            if row is None:
                return text_notice('没有找到该角色。')
        if row['id'] == self.cid:
            return text_notice('不能添加自己。')
        return packet(582, ['found_player'], [dict(row, level=str(row['level']), **identity(db, row['id']))])

    def receive(self, mid, reader):
        if mid == 596:
            for _ in range(6):
                reader.integer()
            reader.finish()
            return text_notice('按条件查找需要扣除体力，当前请用角色 ID 或名字查找。未扣除资源。')
        with self.progression.transaction(write=mid in WRITES) as db:
            if mid in (597, 598):
                return self.search(mid, reader, db)
            if mid == 602:
                ids = list(dict.fromkeys(reader.integer() for _ in range(reader.take(1)[0])))
                reader.finish()
                # The native reply uses adjacent (id, online) entries in a flat array.
                return tuple(packet(584, [('array', 'number')], [
                    [value for cid in ids[start:start+127] for value in (cid, visible(self.service.online(cid)))]]
                ) for start in range(0, len(ids), 127))
            if mid == 611:
                flag = reader.integer()
                reader.finish()
                if flag not in (0, 1):
                    raise ValueError('好友在线状态无效。')
                self.game._friend_visible = bool(flag)
                presence_changed(self.game)
                return fields(593, self.cid, flag)
            if mid in (604, 605, 606, 607, 608):
                return self.update_group(mid, reader, db)
            return self.update_contact(mid, reader, db)

    def update_contact(self, mid, reader, db):
        cid = reader.integer()
        if mid == 599:
            group = reader.integer()
            reader.integer()  # Native entry-point tag (why).
            reader.finish()
            if cid == self.cid:
                raise ValueError('不能添加自己。')
            self.group(db, group)
            if group in SYSTEM_GROUPS - {NORMAL, BLOCKED, TEMPORARY}:
                raise ValueError('请选择可添加好友的分组。')
            row = self.target(db, cid)
            saved = db.execute('SELECT group_id,alias FROM native_contacts WHERE character_id=? AND target_id=?',
                               (self.cid, cid)).fetchone()
            if saved:
                return text_notice('该角色已在列表中，可用移动分组调整。')
            if db.execute('SELECT count(*) FROM native_contacts WHERE character_id=?', (self.cid,)).fetchone()[0] >= 254:
                raise ValueError('好友列表已满。')
            db.execute('INSERT INTO native_contacts(character_id,target_id,group_id) VALUES(?,?,?)', (self.cid, cid, group))
            active = visible(self.service.online(cid)) if group != BLOCKED else 0
            return packet(583, ['friend_info'], [self.record(db, row, group)]), fields(593, cid, active)

        saved_group, alias = self.contact(db, cid)
        if mid == 600:
            alias = clean_text(reader.string(), 32, allow_empty=True)
            reader.finish()
            db.execute('UPDATE native_contacts SET alias=? WHERE character_id=? AND target_id=?', (alias, self.cid, cid))
        elif mid == 603:
            reader.finish()
            db.execute('DELETE FROM native_contacts WHERE character_id=? AND target_id=?', (self.cid, cid))
            return fields(585, cid)
        elif mid == 610:
            source, group = reader.integer(), reader.integer()
            reader.finish()
            self.group(db, group)
            if source != saved_group or group in SYSTEM_GROUPS - {NORMAL, BLOCKED, TEMPORARY}:
                raise ValueError('好友分组已变化或目标分组不可移动。')
            db.execute('UPDATE native_contacts SET group_id=? WHERE character_id=? AND target_id=?', (group, self.cid, cid))
            active = visible(self.service.online(cid)) if group != BLOCKED else 0
            return fields(592, cid, source, group), fields(593, cid, active)
        else:
            reader.finish()
        return packet(590, ['friend_info'], [self.record(db, self.target(db, cid), saved_group, alias)])

    def update_group(self, mid, reader, db):
        if mid == 604:
            name = clean_text(reader.string(), 32)
            reader.finish()
            used = {row[0] for row in db.execute('SELECT group_id FROM native_contact_groups WHERE character_id=?', (self.cid,))}
            group = next((group for group in CUSTOM_GROUPS if group not in used), None)
            if group is None:
                raise ValueError('自定义好友分组已满。')
            db.execute('INSERT INTO native_contact_groups(character_id,group_id,name) VALUES(?,?,?)', (self.cid, group, name))
            return packet(586, ['number', 'string'], [group, name])
        if mid == 605:
            reader.finish()
            hidden = db.execute('SELECT group_id FROM native_contact_groups WHERE character_id=? AND hidden=1', (self.cid,)).fetchall()
            db.execute('UPDATE native_contact_groups SET hidden=0 WHERE character_id=? AND hidden=1', (self.cid,))
            return tuple(fields(588, group, 0) for (group,) in hidden)

        group = reader.integer()
        self.group(db, group)
        if mid == 607:
            hidden = reader.integer()
            reader.finish()
            if hidden not in (0, 1):
                raise ValueError('分组显示状态无效。')
            db.execute('INSERT INTO native_contact_groups(character_id,group_id,hidden) VALUES(?,?,?) '
                       'ON CONFLICT(character_id,group_id) DO UPDATE SET hidden=excluded.hidden', (self.cid, group, hidden))
            return fields(588, group, hidden)
        if group in SYSTEM_GROUPS:
            raise ValueError('系统好友分组不能删除或改名。')
        if mid == 608:
            name = clean_text(reader.string(), 32)
            reader.finish()
            db.execute('UPDATE native_contact_groups SET name=? WHERE character_id=? AND group_id=?', (name, self.cid, group))
            return packet(589, ['number', 'string'], [group, name])
        reader.finish()
        params = self.cid, group
        members = db.execute('SELECT target_id FROM native_contacts WHERE character_id=? AND group_id=?', params).fetchall()
        db.execute('UPDATE native_contacts SET group_id=? WHERE character_id=? AND group_id=?', (NORMAL, *params))
        db.execute('DELETE FROM native_contact_groups WHERE character_id=? AND group_id=?', params)
        return (*(fields(592, cid, group, NORMAL) for (cid,) in members), fields(587, group))
