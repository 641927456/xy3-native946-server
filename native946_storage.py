"""Short, composable SQLite transactions owned by the game dispatch thread."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from time import monotonic
from collections import deque
from threading import Condition,Lock
from weakref import WeakValueDictionary
import sqlite3

from native946_runtime import METRICS
from native946_connections import connect,database_key


BUSY_SECONDS = .04
_CURRENT = ContextVar('native946_db_transaction',default=None)
_GATES=WeakValueDictionary()
_GATES_LOCK=Lock()


class StorageBusy(ValueError):
    pass


class _WriterGate:
    """Fair, bounded admission; SQLite busy retries otherwise let writers starve."""
    def __init__(self):
        self.condition=Condition();self.waiters=deque();self.running=False

    def acquire(self,deadline):
        ticket=object();started=monotonic()
        with self.condition:
            self.waiters.append(ticket)
            while True:
                remaining=deadline-monotonic()
                if remaining<=0:
                    self.waiters.remove(ticket);self.condition.notify_all()
                    METRICS.increment('database_busy_rejected')
                    raise StorageBusy('当前存档繁忙，本次操作未执行，请稍后再试。')
                if not self.running and self.waiters[0] is ticket:break
                self.condition.wait(remaining)
            self.waiters.popleft();self.running=True
        METRICS.observe('db_writer_admission_wait',monotonic()-started)

    def release(self):
        with self.condition:
            self.running=False;self.condition.notify_all()


def _writer_gate(key):
    with _GATES_LOCK:
        gate=_GATES.get(key)
        if gate is None:gate=_GATES[key]=_WriterGate()
        return gate


@contextmanager
def transaction(database, *, write=False,batchable=False):
    key = database_key(database)
    current = _CURRENT.get()
    if current is not None:
        old_key, db, writable = current[:3]
        if old_key != key or (write and not writable):
            raise RuntimeError('Invalid cross-database or read-to-write nested transaction')
        yield db
        return
    if write and batchable:
        from native946_durable_writer import find
        writer=find(database)
        if writer is not None:
            started=monotonic();slot=None;token=None;staged=False
            try:
                slot,db=writer.begin(started+BUSY_SECONDS)
                token=_CURRENT.set((key,db,True,set()))
                body_started=monotonic()
                yield db
                METRICS.observe('db_body_write',monotonic()-body_started)
                staged=True;writer.stage(slot)
            except sqlite3.OperationalError as error:
                if slot is not None:writer.abort(slot)
                if getattr(error,'sqlite_errorcode',0)&255 in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):
                    METRICS.increment('database_busy_rejected')
                    raise StorageBusy('当前存档繁忙，本次操作未执行，请稍后再试。') from None
                raise
            except BaseException:
                if slot is not None:writer.abort(slot)
                raise
            finally:
                if token is not None:_CURRENT.reset(token)
                METRICS.observe('db_transaction_write',monotonic()-started)
            return
    started = monotonic()
    db = connect(database,timeout=BUSY_SECONDS)
    db.row_factory = sqlite3.Row
    token = None
    gate=None
    try:
        if write:
            candidate=_writer_gate(key)
            candidate.acquire(started+BUSY_SECONDS)
            gate=candidate
            remaining=max(1,int((started+BUSY_SECONDS-monotonic())*1000))
            db.execute(f'PRAGMA busy_timeout={remaining}')
        else:db.execute(f'PRAGMA busy_timeout={int(BUSY_SECONDS*1000)}')
        db.execute('PRAGMA foreign_keys=ON')
        if not write:
            db.execute('PRAGMA query_only=ON')
        before = monotonic()
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        METRICS.observe('db_begin_write' if write else 'db_begin_read',monotonic()-before)
        token = _CURRENT.set((key,db,write,set()))
        body_started=monotonic()
        yield db
        METRICS.observe('db_body_write' if write else 'db_body_read',monotonic()-body_started)
        commit_started=monotonic()
        db.commit()
        if write:METRICS.observe('db_commit',monotonic()-commit_started)
    except sqlite3.OperationalError as error:
        db.rollback()
        if getattr(error,'sqlite_errorcode',0) & 255 in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):
            METRICS.increment('database_busy_rejected')
            raise StorageBusy('当前存档繁忙，本次操作未执行，请稍后再试。') from None
        raise
    except BaseException:
        db.rollback()
        raise
    finally:
        if token is not None:
            _CURRENT.reset(token)
        try:db.close()
        finally:
            if gate is not None:gate.release()
        METRICS.observe('db_transaction_write' if write else 'db_transaction_read',monotonic()-started)
