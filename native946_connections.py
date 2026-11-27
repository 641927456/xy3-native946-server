"""Owner-thread SQLite connection reuse without retaining read transactions."""
from pathlib import Path
from threading import local
from functools import lru_cache
from os import fspath
from os.path import abspath
import sqlite3

from native946_runtime import METRICS


_LOCAL=local()


@lru_cache(maxsize=256)
def _database_key(path):return str(Path(path).resolve()).casefold()

def database_key(database):return _database_key(abspath(fspath(database)))


class BorrowedConnection:
    def __init__(self,connection):
        object.__setattr__(self,'connection',connection)

    def __getattr__(self,name):return getattr(self.connection,name)

    def __setattr__(self,name,value):setattr(self.connection,name,value)

    def execute(self,sql,*args):
        if sql=='BEGIN IMMEDIATE':
            self.connection.execute('PRAGMA query_only=OFF')
        return self.connection.execute(sql,*args)

    def close(self):
        # transaction() has already committed/rolled back. Keep only the
        # connection and prepared statements; never keep a reader snapshot.
        if self.connection.in_transaction:self.connection.rollback()


def enable_thread():_LOCAL.enabled=True


def connect(database,**kwargs):
    if not getattr(_LOCAL,'enabled',False):return sqlite3.connect(database,**kwargs)
    if not hasattr(_LOCAL,'connections'):_LOCAL.connections={}
    key=database_key(database)
    connection=_LOCAL.connections.get(key)
    if connection is None:
        connection=sqlite3.connect(database,**kwargs)
        connection.execute('PRAGMA synchronous=FULL')
        _LOCAL.connections[key]=connection
        METRICS.increment('sqlite_connections_opened')
    else:METRICS.increment('sqlite_connections_reused')
    return BorrowedConnection(connection)


def close_thread():
    for connection in getattr(_LOCAL,'connections',{}).values():connection.close()
    _LOCAL.connections={};_LOCAL.enabled=False


class _SQLiteAdapter:
    connect=staticmethod(connect)
    def __getattr__(self,name):return getattr(sqlite3,name)


def install_legacy_storage_adapter():
    # Update one module global instead of reloading ContextVars used by active
    # transactions. Fresh processes call connect() directly from the source.
    import native946_storage
    native946_storage.sqlite3=_SQLiteAdapter()
