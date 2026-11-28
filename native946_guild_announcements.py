"""946 guild announcement history and remote tenet editing."""

from secrets import randbelow
from time import monotonic, time

from native946_codec import packet, text_notice
from native946_guilds import _membership, receive as guild_info
from native946_login import MessageReader


PUBLISH, EDIT_TENET, DELETE, HISTORY = 4972, 4973, 4974, 4981
REQUESTS = {PUBLISH, EDIT_TENET, DELETE, HISTORY}
WRITES = {DELETE}
INPUT = 1033
CLOSE = packet(1183, ['string'], ['dlg_npc_chat'])


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_guild_announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL REFERENCES native_guilds(id) ON DELETE CASCADE,
        author TEXT NOT NULL, office INTEGER NOT NULL, department INTEGER NOT NULL,
        posted INTEGER NOT NULL, body TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS native_guild_announcements_by_guild '
               'ON native_guild_announcements(guild_id,id DESC)')


def history(db, guild):
    from native946_progression import lua_data

    rows = []
    size = 2  # Outer Lua table braces; the native string has a 65,534-byte limit.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='native_guild_announcements'").fetchone():
        for row in db.execute('SELECT id,author,office,department,posted,body '
                              'FROM native_guild_announcements WHERE guild_id=? ORDER BY id DESC', (guild,)):
            entry = lua_data(dict(id=row['id'], UserName=row['author'], UserOffice=row['office'],
                                  Group=row['department'], Time=row['posted'], Msg=row['body']))
            size += len(entry.encode('gb18030')) + bool(rows)
            if size > 65534:
                break
            rows.append(entry)
    return packet(4970, ['string'], ['{' + ','.join(rows) + '}'])


def matches(game, reader):
    session = getattr(game, '_guild_text_session', None)
    if session is None:
        return False
    try:
        probe = MessageReader(reader.data[reader.offset:])
        count = probe.take(1)[0]
        if count > 1:
            return False
        if count:
            probe.string()
        return probe.integer() == session['token']
    except ValueError:
        return False


def receive(game, mid, reader):
    if game.battle:
        raise ValueError('请先结束战斗。')
    cid = game.character['id']
    session = None
    if mid == INPUT:
        count = reader.take(1)[0]
        if count not in (0, 1):
            raise ValueError('帮派输入格式无效。')
        raw = reader.string() if count else None
        token = reader.integer()
        session = getattr(game, '_guild_text_session', None)
        if not session or token != session['token'] or monotonic() >= session['until']:
            raise ValueError('帮派输入窗口已失效，请重新打开。')
        reader.finish()
        if raw is None:
            game._guild_text_session = None
            return CLOSE
        action = session['action']
        body = raw.decode('gb18030').strip()
        if (not body or '#' in body or any(not char.isprintable() for char in body)
                or (action == EDIT_TENET and len(body) > 250)
                or (action == PUBLISH and len(raw) > 500)):
            raise ValueError('请输入有效的帮派文字。')
    else:
        announcement = reader.integer() if mid == DELETE else None
        reader.finish()
        action = mid

    with game.progression.transaction(write=session is not None or action == DELETE) as db:
        member = _membership(db, cid)
        if member is None:
            raise ValueError('请先加入帮派。')
        guild = member['guild_id']
        if action == HISTORY:
            return history(db, guild)
        if member['master'] != cid or (session is not None and session['guild'] != guild):
            raise ValueError('只有当前帮派的帮主可以管理公告和宗旨。')
        if action == DELETE:
            schema(db)
            if not db.execute('DELETE FROM native_guild_announcements WHERE guild_id=? AND id=?',
                              (guild, announcement)).rowcount:
                raise ValueError('没有找到这条帮派公告。')
            return history(db, guild), text_notice('已删除帮派公告。')
        if session is None:
            token = randbelow(1 << 29) + (1 << 29)
            game._guild_text_session = dict(token=token, guild=guild, action=action, until=monotonic() + 180)
            label = '帮派公告' if action == PUBLISH else '帮派宗旨'
            return packet(1023, [('array', 'npc_chat'), ('array', 'string'), ('array', 'number'), 'number', 'number'],
                          [[dict(chat_msg=f'请输入{label}。', speaker=0)], [label], [0], token, 0])
        if action == PUBLISH:
            schema(db)
            db.execute('INSERT INTO native_guild_announcements(guild_id,author,office,department,posted,body) '
                       'VALUES(?,?,?,?,?,?)',
                       (guild, game.character['name'], member['office'], member['member_group'], int(time()), body))
            replies = (CLOSE, history(db, guild), text_notice('已发布帮派公告。'))
        else:
            db.execute('UPDATE native_guilds SET tenet=? WHERE id=?', (body, guild))
            replies = (CLOSE, guild_info(game, 1125, MessageReader(b'')), text_notice('已修改帮派宗旨。'))

    def finish():
        if game._guild_text_session is session:
            game._guild_text_session = None
        return replies

    return game.defer_packets(finish, fallback=replies)
