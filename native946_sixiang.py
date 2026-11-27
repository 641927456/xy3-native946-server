"""Native 946 four-symbols detail panels backed by the client's SoulAttrConfig."""
import json
from pathlib import Path

from native946_codec import packet

DATA = json.loads(Path(__file__).with_name('native946_sixiang_data.json').read_text('utf8'))
SOUL_ATTR = {int(realm): rows for realm, rows in DATA['SoulAttrConfig'].items()}
DETAIL_REQUESTS = {3148, 3149, 3150}
EXPERIENCE_ITEM = 48176


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_sixiang (
      summon_id INTEGER PRIMARY KEY REFERENCES summons(id) ON DELETE CASCADE,
      realm INTEGER NOT NULL DEFAULT 0 CHECK(realm BETWEEN 0 AND 9),
      stage INTEGER NOT NULL DEFAULT 0 CHECK(stage BETWEEN 0 AND 2),
      experience INTEGER NOT NULL DEFAULT 0 CHECK(experience>=0),
      forge_count INTEGER NOT NULL DEFAULT 0 CHECK(forge_count>=0))''')


def saved(db, sid):
    row = db.execute('SELECT realm,stage,experience,forge_count FROM native_sixiang WHERE summon_id=?', (sid,)).fetchone()
    return dict(row) if row is not None else dict(realm=0, stage=0, experience=0, forge_count=0)


def entries(state):
    current = SOUL_ATTR[state['realm']][state['stage']]
    if state['stage'] < 2:
        following = SOUL_ATTR[state['realm']][state['stage'] + 1]
    elif state['realm'] < 9:
        following = SOUL_ATTR[state['realm'] + 1][0]
    else:
        following = current
    return current, following


class Sixiang:
    def __init__(self, game):
        self.g = game
        self.p = game.progression
        self.s = game.summons

    def state(self, sid):
        with self.p.transaction(write=False) as db:
            self.s._owned(db, sid)
            return saved(db, sid)

    def power_detail(self, sid):
        state = self.state(sid)
        current, following = entries(state)
        descriptions = [current['Tip' + str(index)] for index in range(1, 5)]
        following_descriptions = [following['Tip' + str(index)] for index in range(1, 5)]
        return packet(3144, ['number', 'number', ('fixed_array', 'number', 4),
                             ('fixed_array', 'string', 4), ('fixed_array', 'string', 4), 'number'],
                      [sid, state['realm'], [state['stage']] * 4, descriptions, following_descriptions,
                       state['forge_count']])

    def receive(self, mid, reader):
        sid = reader.integer()
        reader.finish()
        if mid == 3150:
            return self.power_detail(sid)
        state = self.state(sid)
        current, following = entries(state)
        snapshot = self.p.snapshot()
        with self.p.transaction(write=False) as db:
            item_count = db.execute('SELECT coalesce(sum(amount),0) FROM inventory WHERE character_id=? AND type=?',
                                    (self.p.cid, EXPERIENCE_ITEM)).fetchone()[0]
        if mid == 3149:
            return packet(3143, ['number'] * 6,
                          [snapshot['gold'], state['realm'], state['experience'],
                           current['TotalNeedExp'], following['TotalNeedExp'], EXPERIENCE_ITEM])
        return (packet(3142, ['number', 'number', 'number', 'string', 'number', 'number', ('fixed_array', 'number', 4)],
                       [sid, current['TotalNeedExp'], state['experience'], current['Tip1'],
                        snapshot['gold'], item_count, [state['stage']] * 4]),
                packet(3143, ['number'] * 6,
                       [snapshot['gold'], state['realm'], state['experience'],
                        current['TotalNeedExp'], following['TotalNeedExp'], EXPERIENCE_ITEM]))
