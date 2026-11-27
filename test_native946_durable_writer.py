"""Commit acknowledgement, rollback isolation and transaction boundary tests."""
from pathlib import Path
from contextlib import closing
from tempfile import TemporaryDirectory
from threading import Thread,Event
from time import monotonic
import sqlite3,unittest

from native946_durable_writer import DurableWriter


class CommitFailure:
    def __init__(self,connection):self.connection=connection
    def __getattr__(self,name):return getattr(self.connection,name)
    def commit(self):raise sqlite3.OperationalError('forced commit failure')


class DurableWriterTests(unittest.TestCase):
    def setUp(self):
        if sqlite3.sqlite_version_info<(3,51,3):self.skipTest('Use native946_run.py for the patched WAL runtime')
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'batch.sqlite3'
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE saved(id INTEGER PRIMARY KEY,value INTEGER)')
        self.writer=DurableWriter(self.path,delay=.2);self.addCleanup(self.writer.close)
        self.writer.flush_idle=False

    def pending(self,count):
        with self.writer.condition:
            self.assertTrue(self.writer.condition.wait_for(lambda:len(self.writer.pending)==count,timeout=1))

    def flush(self):
        with self.writer.condition:
            self.writer.ready_at=monotonic()-1;self.writer.condition.notify_all()

    def reader(self):
        with closing(sqlite3.connect(self.path)) as db:return db.execute('SELECT id,value FROM saved ORDER BY id').fetchall()

    def actor(self,identity,done,errors):
        slot,db=self.writer.begin(monotonic()+1)
        try:
            db.execute('INSERT INTO saved VALUES(?,?)',(identity,identity*10))
        except BaseException:
            self.writer.abort(slot);raise
        try:self.writer.stage(slot)
        except BaseException as error:errors.append(str(error))
        finally:done.set()

    def test_success_is_not_acknowledged_until_the_shared_commit(self):
        errors=[];done=[Event(),Event()]
        threads=[Thread(target=self.actor,args=(i+1,done[i],errors)) for i in range(2)]
        for t in threads:t.start()
        self.pending(2)
        self.assertFalse(any(e.is_set() for e in done));self.assertEqual(self.reader(),[])
        self.flush()
        for t in threads:t.join(1);self.assertFalse(t.is_alive())
        self.assertEqual(errors,[]);self.assertEqual(self.reader(),[(1,10),(2,20)])

    def test_idle_writer_commits_without_waiting_for_the_batch_deadline(self):
        self.writer.flush_idle=True
        self.writer.delay=10
        done,errors=Event(),[]
        thread=Thread(target=self.actor,args=(1,done,errors))
        thread.start()
        try:
            self.assertTrue(done.wait(1))
            self.assertEqual(errors,[])
            self.assertEqual(self.reader(),[(1,10)])
        finally:
            self.flush()
            thread.join(1)

    def test_aborted_action_rolls_back_only_its_savepoint(self):
        done=Event();errors=[];thread=Thread(target=self.actor,args=(1,done,errors));thread.start();self.pending(1)
        slot,db=self.writer.begin(monotonic()+1)
        with self.assertRaises(sqlite3.IntegrityError):
            db.executemany('INSERT INTO saved VALUES(?,?)', ((2,20), (1,99)))
        self.writer.abort(slot)
        self.flush();thread.join(1)
        self.assertTrue(done.is_set());self.assertEqual(errors,[]);self.assertEqual(self.reader(),[(1,10)])

    def test_failed_commit_is_reported_to_every_participant_and_nothing_persists(self):
        self.writer.db=CommitFailure(self.writer.db)
        errors=[];done=[Event(),Event()]
        threads=[Thread(target=self.actor,args=(i+1,done[i],errors)) for i in range(2)]
        for t in threads:t.start()
        self.pending(2);self.flush()
        for t in threads:t.join(1);self.assertFalse(t.is_alive())
        self.assertEqual(errors,['forced commit failure','forced commit failure']);self.assertEqual(self.reader(),[])

    def test_gameplay_cannot_escape_the_savepoint_with_a_commit_or_script(self):
        for control in ('COMMIT','ROLLBACK','BEGIN IMMEDIATE','SAVEPOINT fake'):
            slot,db=self.writer.begin(monotonic()+1)
            with self.assertRaises(RuntimeError):db.execute(control)
            with self.assertRaises(RuntimeError):db.executemany(control, [()])
            self.writer.abort(slot)
        slot,db=self.writer.begin(monotonic()+1)
        with self.assertRaises(RuntimeError):db.executescript('INSERT INTO saved VALUES(1,10);')
        self.writer.abort(slot);self.assertEqual(self.reader(),[])

    def test_durability_and_foreign_keys_remain_enabled(self):
        self.assertEqual(self.writer.db.execute('PRAGMA synchronous').fetchone()[0],2)
        self.assertEqual(self.writer.db.execute('PRAGMA foreign_keys').fetchone()[0],1)

    def test_expired_admission_never_becomes_a_later_mutation(self):
        from native946_storage import StorageBusy
        held,_=self.writer.begin(monotonic()+1);errors=[]
        def blocked():
            try:self.writer.begin(monotonic()+.02)
            except StorageBusy:errors.append('expired')
        thread=Thread(target=blocked);thread.start();thread.join(.5)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,['expired'])
        self.writer.abort(held)
        self.assertEqual(len(self.writer.waiters),0);self.assertEqual(self.reader(),[])

    def test_implicit_database_rollback_fails_pending_actions_instead_of_acknowledging(self):
        done=Event();errors=[];thread=Thread(target=self.actor,args=(1,done,errors));thread.start();self.pending(1)
        slot,db=self.writer.begin(monotonic()+1);db.execute('INSERT INTO saved VALUES(2,20)')
        self.writer.db.rollback()  # Simulate SQLite auto-rollback after an I/O/full-disk error.
        self.writer.abort(slot);thread.join(1)
        self.assertTrue(done.is_set());self.assertEqual(len(errors),1);self.assertEqual(self.reader(),[])
        self.assertFalse(self.writer.pending);self.assertIsNone(self.writer.active)


if __name__=='__main__':unittest.main()
