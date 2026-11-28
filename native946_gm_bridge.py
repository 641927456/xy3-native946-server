"""Refresh an online character after a local maintenance tool edits its save."""
import sqlite3
from time import monotonic, time

from native946_codec import text_notice
from native946_storage import StorageBusy, transaction


def notify(database, cid, note=''):
    with transaction(database, write=True) as db:
        if not db.execute('SELECT 1 FROM characters WHERE id=?', (cid,)).fetchone():
            raise ValueError('没有这个角色。')
        db.execute("""CREATE TABLE IF NOT EXISTS gm_outbox (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          character_id INTEGER NOT NULL,
          kind TEXT NOT NULL DEFAULT 'refresh',
          note TEXT NOT NULL DEFAULT '',
          created INTEGER NOT NULL DEFAULT 0)""")
        db.execute('CREATE INDEX IF NOT EXISTS gm_outbox_character ON gm_outbox(character_id,id)')
        db.execute("INSERT INTO gm_outbox(character_id,kind,note,created) VALUES(?,'refresh',?,?)",
                   (cid,str(note),int(time())))


def poll(game, now=None):
    now=monotonic() if now is None else now
    if now<getattr(game,'_gm_next_poll',0):
        return ()
    game._gm_next_poll=now+0.5
    p=game.progression
    try:
        with p.transaction(write=False) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gm_outbox'").fetchone():
                return ()
            newest=db.execute("SELECT max(id) FROM gm_outbox WHERE character_id=? AND kind='refresh'",
                              (p.cid,)).fetchone()[0]
        if newest is None:
            return ()
        with game.operation(write=True):
            p._cached_snapshot=None
            replies=(*game.sync_progression(),text_notice('GM 已刷新你的属性、背包与守护。'))
            with p.transaction() as db:
                db.execute("DELETE FROM gm_outbox WHERE character_id=? AND kind='refresh' AND id<=?",
                           (p.cid,newest))
        return replies
    except (StorageBusy,sqlite3.Error,ValueError,KeyError):
        from native946_runtime import METRICS
        METRICS.increment('gm_refresh_deferred')
        return ()


if __name__=='__main__':
    import argparse
    from pathlib import Path

    parser=argparse.ArgumentParser(description='为指定本机角色排队刷新属性、背包和守护。')
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--character-id',required=True,type=int)
    args=parser.parse_args()
    if not args.database.is_file():
        parser.error('指定的本机存档不存在。')
    notify(args.database,args.character_id)
