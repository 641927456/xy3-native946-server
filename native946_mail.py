"""Same-server player mail carried by the 946 friend-message interface."""
from time import time

from native946_codec import packet, text_notice
from native946_friends import BLOCKED, NORMAL, CUSTOM_GROUPS
from native946_storage import transaction


REQUESTS = frozenset(range(926, 931))
NORMAL_MESSAGE = 1
AUTOMATIC_MESSAGE = 7


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_player_mail (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        receiver INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        kind INTEGER NOT NULL,
        content TEXT NOT NULL,
        sent INTEGER NOT NULL,
        CHECK(sender <> receiver))''')
    db.execute('CREATE INDEX IF NOT EXISTS native_player_mail_recipient ON native_player_mail(receiver,kind,sender,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS native_player_mail_preferences (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        auto_reply TEXT NOT NULL DEFAULT '',
        deny_group INTEGER NOT NULL DEFAULT 0 CHECK(deny_group IN (0,1)))''')


def notice(sender, kind=NORMAL_MESSAGE):
    return packet(924, ['number', 'number'], [kind, sender])


def preferences(db, cid):
    saved = db.execute('SELECT auto_reply, deny_group FROM native_player_mail_preferences WHERE character_id=?',
                       (cid,)).fetchone()
    return tuple(saved) if saved is not None else ('', 0)


def initial_packets(game):
    cid = game.character['id']
    with transaction(game.characters.database) as db:
        auto, deny = preferences(db, cid)
        pending = db.execute('SELECT kind,sender FROM native_player_mail WHERE receiver=? GROUP BY kind,sender ORDER BY min(id)',
                             (cid,)).fetchall()
        return (packet(922, ['number', 'string'], [int(bool(auto)), auto]),
                packet(923, ['number'], [deny]), *(notice(sender, kind) for kind, sender in pending))


def message_text(raw):
    text = raw.decode('gb18030').strip()
    if not text or len(raw) > 1024 or any(not c.isprintable() and c != '\n' for c in text):
        raise ValueError('请填写有效的留言内容。')
    return text


def blocked(db, receiver, sender):
    return db.execute('SELECT 1 FROM native_contacts WHERE character_id=? AND target_id=? AND group_id=?',
                      (receiver, sender, BLOCKED)).fetchone() is not None


class Mail:
    def __init__(self, service, game):
        self.service = service
        self.cid = game.character['id']

    def receive(self, mid, reader):
        if mid == 926:
            enabled, raw = reader.integer(), reader.string()
            reader.finish()
            if enabled not in (0, 1):
                raise ValueError('自动回复状态无效。')
            text = message_text(raw) if enabled else ''
            with transaction(self.service.database, write=True) as db:
                db.execute('INSERT INTO native_player_mail_preferences(character_id,auto_reply) VALUES(?,?) '
                           'ON CONFLICT(character_id) DO UPDATE SET auto_reply=excluded.auto_reply', (self.cid, text))
            return (packet(922, ['number', 'string'], [enabled, text]),)
        if mid == 927:
            deny = reader.integer()
            reader.finish()
            if deny not in (0, 1):
                raise ValueError('群发留言设置无效。')
            with transaction(self.service.database, write=True) as db:
                db.execute('INSERT INTO native_player_mail_preferences(character_id,deny_group) VALUES(?,?) '
                           'ON CONFLICT(character_id) DO UPDATE SET deny_group=excluded.deny_group', (self.cid, deny))
            return (packet(923, ['number'], [deny]),)
        if mid == 929:
            kind, sender = reader.integer(), reader.integer()
            reader.finish()
            return self.read(kind, sender)

        group = None
        if mid == 928:
            group = reader.integer()
            if group != NORMAL and group not in CUSTOM_GROUPS:
                raise ValueError('请选择可群发留言的好友分组。')
            targets = ()
        else:
            targets = tuple(dict.fromkeys(reader.integer() for _ in range(reader.take(1)[0])))
        text, kind = message_text(reader.string()), reader.integer()
        links = reader.string() if mid == 930 else b''
        reader.finish()
        if kind != NORMAL_MESSAGE:
            raise ValueError('玩家只能发送普通留言。')
        if links not in (b'', b'{}'):
            raise ValueError('请先移除留言里的物品链接。')
        return self.send(text, targets, group)

    def send(self, text, targets, group):
        now = int(time())
        delivered = []
        with transaction(self.service.database, write=True) as db:
            if group is not None:
                targets = [row[0] for row in db.execute('SELECT target_id FROM native_contacts WHERE character_id=? AND group_id=?',
                                                        (self.cid, group))]
            for cid in targets:
                if cid == self.cid or not db.execute('SELECT 1 FROM characters WHERE id=?', (cid,)).fetchone():
                    continue
                auto, deny = preferences(db, cid)
                if blocked(db, cid, self.cid) or (group is not None and deny):
                    continue
                db.execute('INSERT INTO native_player_mail(sender,receiver,kind,content,sent) VALUES(?,?,?,?,?)',
                           (self.cid, cid, NORMAL_MESSAGE, text, now))
                delivered.append((cid, self.cid, NORMAL_MESSAGE))
                if auto and group is None and not blocked(db, self.cid, cid):
                    db.execute('INSERT INTO native_player_mail(sender,receiver,kind,content,sent) VALUES(?,?,?,?,?)',
                               (cid, self.cid, AUTOMATIC_MESSAGE, auto, now))
                    delivered.append((self.cid, cid, AUTOMATIC_MESSAGE))
        if not delivered:
            raise ValueError('没有可接收这条留言的角色。')
        for receiver, sender, kind in delivered:
            game = self.service.online(receiver)
            if game is not None:
                self.service.enqueue(game, notice(sender, kind))
        recipients = sum(kind == NORMAL_MESSAGE for _, _, kind in delivered)
        return (text_notice(f'留言已发送给{recipients}位角色。'),)

    def read(self, kind, sender):
        with transaction(self.service.database, write=True) as db:
            where = 'm.receiver=?'
            parameters = [self.cid]
            if kind != -1:
                where += ' AND m.kind=?'
                parameters.append(kind)
            if sender != -1:
                where += ' AND m.sender=?'
                parameters.append(sender)
            rows = db.execute('SELECT m.*,c.name FROM native_player_mail m JOIN characters c ON c.id=m.sender '
                              f'WHERE {where} ORDER BY m.id LIMIT 100', parameters).fetchall()
            db.executemany('DELETE FROM native_player_mail WHERE id=? AND receiver=?', ((row['id'], self.cid) for row in rows))
            pending = {(k, s) for k, s in db.execute('SELECT DISTINCT kind,sender FROM native_player_mail WHERE receiver=?',
                                                     (self.cid,))}
        replies = []
        for row in reversed(rows):
            key = row['kind'], row['sender']
            data = {'from': row['sender'], 'type': row['kind'], 'content': row['content'], 'name': row['name'], 'date': row['sent']}
            replies.append(packet(925, ['number', 'mail'], [int(key in pending), data]))
            pending.add(key)
        return (*reversed(replies), packet(3563, [], []))
