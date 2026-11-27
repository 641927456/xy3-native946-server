"""Pinned, project-local SQLite for native 946; never replace Python's DLLs."""
from pathlib import Path
from contextlib import closing
import ctypes,hashlib,os,sys

ROOT=Path(__file__).resolve().parent
DLL=ROOT/'vendor/sqlite-3.51.3-win-x64/sqlite3.dll'
SHA256='0f5f59edc42c2138249d0386b6417125e4e4dd31706c4f4636441de45cb7dfa1'
SOURCE_ID='2026-03-13 10:38:09 737ae4a34738ffa0c3ff7f9bb18df914dd1cad163f28fd6b6e114a344fe6d618'
_LIBRARY=None


def bootstrap(strict=False):
    global _LIBRARY
    existing=sys.modules.get('sqlite3')
    if existing is not None:
        if existing.sqlite_version=='3.51.3':return True
        if strict:raise RuntimeError('Start a fresh process with native946_run.py before importing sqlite3.')
        return False
    if os.name!='nt' or ctypes.sizeof(ctypes.c_void_p)!=8:
        if strict:raise RuntimeError('The pinned runtime requires 64-bit Windows Python.')
        return False
    if hashlib.sha256(DLL.read_bytes()).hexdigest()!=SHA256:raise RuntimeError('Project SQLite DLL hash mismatch')
    library=ctypes.WinDLL(str(DLL))
    library.sqlite3_libversion.restype=ctypes.c_char_p
    library.sqlite3_sourceid.restype=ctypes.c_char_p
    if library.sqlite3_libversion()!=b'3.51.3' or library.sqlite3_sourceid().decode()!=SOURCE_ID:
        raise RuntimeError('Unexpected project SQLite version/source identity')
    _LIBRARY=library
    import sqlite3
    if sqlite3.sqlite_version!='3.51.3':raise RuntimeError('Python did not bind the pinned SQLite DLL')
    return True


def configure_database(database):
    bootstrap(strict=True)
    import sqlite3
    # Keep an encoder thread from monopolizing the GIL while a different
    # socket's short database action holds the writer slot.
    sys.setswitchinterval(.001)
    with closing(sqlite3.connect(database)) as db,db:
        mode=db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        db.execute('PRAGMA synchronous=FULL')
        synchronous=db.execute('PRAGMA synchronous').fetchone()[0]
    if mode!='wal' or synchronous!=2:raise RuntimeError('Could not enable durable local WAL mode')
    from native946_durable_writer import register
    register(database)
    return dict(version=sqlite3.sqlite_version,source_id=SOURCE_ID,journal_mode=mode,synchronous=synchronous,
                thread_switch_seconds=sys.getswitchinterval())
