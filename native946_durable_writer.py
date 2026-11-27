"""Bounded savepoint batches; every caller waits for a durable FULL commit."""
from collections import deque
from pathlib import Path
from threading import Condition,Event,Lock,Thread
from time import monotonic
import sqlite3

from native946_runtime import METRICS
from native946_connections import database_key

if '_WRITERS' not in globals():
    _WRITERS = {}
    _REGISTRY = Lock()


class BatchView:
    """Application SQL cannot end or split the coordinator's transaction."""
    def __init__(self,connection):self.connection=connection

    @staticmethod
    def _check_sql(sql):
        first=sql.lstrip().split(None,1)[0].upper()
        if first in ('BEGIN','COMMIT','END','ROLLBACK','SAVEPOINT','RELEASE','VACUUM'):
            raise RuntimeError('Transaction control is owned by the durable writer')

    def execute(self,sql,*args):
        self._check_sql(sql)
        return self.connection.execute(sql,*args)

    def executemany(self, sql, parameters):
        self._check_sql(sql)
        return self.connection.executemany(sql, parameters)

    def executescript(self,*args):raise RuntimeError('Schema scripts cannot run inside a gameplay batch')


class Slot:
    def __init__(self,name):
        self.name=name;self.done=Event();self.error=None


class DurableWriter:
    flush_idle = True

    def __init__(self,database,delay=.002,limit=16):
        self.database=database;self.delay=delay;self.limit=limit
        self.condition=Condition();self.waiters=deque();self.pending=[]
        self.active=None;self.flushing=False;self.started=None;self.ready_at=None;self.sequence=0;self.stopping=False
        self.db=sqlite3.connect(database,timeout=.04,check_same_thread=False,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        if self.db.execute('PRAGMA journal_mode').fetchone()[0]!='wal':
            self.db.close();raise RuntimeError('Durable batching requires a local WAL database')
        self.db.execute('PRAGMA synchronous=FULL');self.db.execute('PRAGMA foreign_keys=ON')
        self.view=BatchView(self.db)
        self.thread=Thread(target=self._loop,name='native-durable-writer',daemon=True);self.thread.start()

    def _due(self):
        # With no queued writers there is nothing to batch. On Windows a
        # timed condition wait can consume a full scheduler tick even at 2 ms.
        return bool(self.pending and (self.stopping or (self.flush_idle and not self.waiters)
                    or len(self.pending)>=self.limit or monotonic()-self.ready_at>=self.delay))

    def _discard(self,error,slot=None):
        """An implicit SQLite rollback must never be acknowledged as a commit."""
        try:self.db.rollback()
        except BaseException:pass
        slots=self.pending+([slot] if slot is not None else [])
        self.pending=[];self.active=None;self.started=None;self.ready_at=None
        for pending in slots:pending.error=error;pending.done.set()
        self.condition.notify_all()

    def begin(self,deadline):
        from native946_storage import StorageBusy
        started=monotonic();ticket=object()
        with self.condition:
            self.waiters.append(ticket)
            while True:
                remaining=deadline-monotonic()
                if self.stopping or remaining<=0:
                    self.waiters.remove(ticket);self.condition.notify_all()
                    METRICS.increment('database_busy_rejected')
                    raise StorageBusy('当前存档繁忙，本次操作未执行，请稍后再试。')
                if self.waiters[0] is ticket and self.active is None and not self.flushing and not self._due():break
                self.condition.wait(remaining)
            self.waiters.popleft()
            try:
                if self.started is None:
                    self.db.execute(f'PRAGMA busy_timeout={max(1,int(remaining*1000))}')
                    self.db.execute('BEGIN IMMEDIATE');self.started=monotonic()
                elif not self.db.in_transaction:
                    raise RuntimeError('SQLite ended a pending gameplay batch unexpectedly')
                self.sequence+=1;slot=Slot('action_'+str(self.sequence))
                self.db.execute('SAVEPOINT '+slot.name);self.active=slot
            except BaseException as error:
                self._discard(error);raise
        METRICS.observe('db_writer_admission_wait',monotonic()-started)
        return slot,self.view

    def stage(self,slot):
        with self.condition:
            if self.active is not slot:raise RuntimeError('Unexpected active batch action')
            try:
                if not self.db.in_transaction:raise RuntimeError('SQLite rolled back the pending gameplay batch')
                self.db.execute('RELEASE SAVEPOINT '+slot.name)
            except BaseException as error:
                self._discard(error,slot);raise
            if not self.pending:self.ready_at=monotonic()
            self.pending.append(slot);self.active=None;self.condition.notify_all()
        # Never time out a staged operation: its final commit outcome must be
        # known before the caller may return success/failure or retry.
        slot.done.wait()
        if slot.error is not None:raise slot.error

    def abort(self,slot):
        with self.condition:
            if self.active is not slot:return
            try:
                if not self.db.in_transaction:raise RuntimeError('SQLite rolled back the pending gameplay batch')
                self.db.execute('ROLLBACK TO SAVEPOINT '+slot.name)
                self.db.execute('RELEASE SAVEPOINT '+slot.name)
            except BaseException as error:
                self._discard(error,slot);return
            self.active=None
            if not self.pending:
                self.db.rollback();self.started=None
            self.condition.notify_all()

    def _loop(self):
        while True:
            with self.condition:
                while self.active is not None or not self._due():
                    if self.stopping and not self.pending and self.active is None:return
                    wait=max(.0001,self.delay-(monotonic()-self.ready_at)) if self.pending else None
                    self.condition.wait(wait)
                slots=self.pending;self.pending=[];self.flushing=True
            error=None;started=monotonic()
            try:
                if not self.db.in_transaction:raise RuntimeError('SQLite ended a batch before its durable commit')
                self.db.commit()
                METRICS.increment('durable_batches_committed')
                METRICS.increment('durable_actions_committed',len(slots))
            except BaseException as caught:
                error=caught
                try:self.db.rollback()
                except BaseException:pass
            finally:METRICS.observe('db_batch_commit',monotonic()-started)
            with self.condition:
                self.started=None;self.ready_at=None;self.flushing=False
                for slot in slots:slot.error=error;slot.done.set()
                self.condition.notify_all()

    def close(self,timeout=2):
        with self.condition:self.stopping=True;self.condition.notify_all()
        self.thread.join(timeout)
        if self.thread.is_alive():raise RuntimeError('Durable writer still has unfinished actions')
        self.db.close()


def key(database):return database_key(database)

def register(database):
    with _REGISTRY:
        name=key(database)
        if name not in _WRITERS:_WRITERS[name]=DurableWriter(database)
        return _WRITERS[name]

def find(database):
    with _REGISTRY:return _WRITERS.get(key(database))

def refresh_registered_writers():
    """Update methods on the existing writer without replacing its worker or connection."""
    with _REGISTRY:
        for writer in _WRITERS.values():
            with writer.condition:
                writer.__class__ = DurableWriter
                writer.view = BatchView(writer.view.connection)
                writer.condition.notify_all()

def close(database):
    with _REGISTRY:writer=_WRITERS.pop(key(database),None)
    if writer is not None:writer.close()
