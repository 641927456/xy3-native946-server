"""Bounded, coalesced position persistence; gameplay positions stay in memory."""
from threading import Condition, Thread
from time import monotonic

from native946_runtime import METRICS
from native946_storage import StorageBusy, transaction


class PositionWriter:
    def __init__(self,database,interval=.05,capacity=256):
        self.database=database
        self.interval=interval
        self.capacity=capacity
        self.condition=Condition()
        self.pending={}
        self.revisions={}
        self.inflight={}
        self.stopping=False
        self.last_success=monotonic()
        with transaction(database) as db:
            for row in db.execute('SELECT owner,id,position_revision FROM characters'):
                self.revisions[(row['owner'],row['id'])]=row['position_revision']
        self.thread=Thread(target=self._run,name='native-position-writer',daemon=True)
        self.thread.start()

    def revision(self,owner,cid,baseline=0):
        with self.condition:
            key=(owner,cid)
            value=max(baseline,self.revisions.get(key,0))+1
            self.revisions[key]=value
            return value

    def submit(self,owner,cid,map_id,x,y,baseline=0):
        with self.condition:
            key=(owner,cid)
            if self.stopping or (key not in self.pending and len(self.pending)>=self.capacity):
                raise StorageBusy('位置保存队列繁忙，本次移动未接收。')
            revision=self.revision(owner,cid,baseline)
            if key in self.pending:METRICS.increment('position_updates_coalesced')
            self.pending[key]=(map_id,x,y,revision)
            self.condition.notify_all()
            return revision

    @staticmethod
    def apply(db,key,value):
        owner,cid=key
        map_id,x,y,revision=value
        return db.execute('UPDATE characters SET x=?,y=?,position_revision=? '
                          'WHERE owner=? AND id=? AND map_id=? AND position_revision<?',
                          (x,y,revision,owner,cid,map_id,revision)).rowcount

    def _run(self):
        from native946_connections import enable_thread,close_thread
        enable_thread()
        try:self._loop()
        finally:close_thread()

    def _loop(self):
        while True:
            with self.condition:
                while not self.pending and not self.stopping:self.condition.wait()
                if self.stopping and not self.pending:return
                until=monotonic()+self.interval
                while not self.stopping and monotonic()<until:
                    self.condition.wait(max(0,until-monotonic()))
                batch=self.pending;self.pending={};self.inflight=batch
            retry=False
            try:
                with transaction(self.database,write=True) as db:
                    for key,value in batch.items():self.apply(db,key,value)
                METRICS.increment('position_batches_saved')
                self.last_success=monotonic()
            except Exception:
                # Retain the latest value for retry, without blocking sockets.
                # No older retry can overwrite a newer committed position.
                METRICS.increment('position_batch_retries')
                retry=True
            with self.condition:
                if retry:
                    for key,value in batch.items():
                        if key not in self.pending or self.pending[key][3]<value[3]:self.pending[key]=value
                self.inflight={}
                self.condition.notify_all()
                if retry:self.condition.wait(self.interval)

    def flush(self,timeout=1):
        until=monotonic()+timeout
        with self.condition:
            self.condition.notify_all()
            while self.pending or self.inflight:
                remaining=until-monotonic()
                if remaining<=0:return False
                self.condition.wait(remaining)
        return True

    def close(self,timeout=1):
        with self.condition:
            self.stopping=True;self.condition.notify_all()
        self.thread.join(timeout)
        return not self.thread.is_alive()

    def flush_key(self,owner,cid,timeout=.15):
        key=(owner,cid);until=monotonic()+timeout
        with self.condition:
            self.condition.notify_all()
            while key in self.pending or key in self.inflight:
                remaining=until-monotonic()
                if remaining<=0:return False
                self.condition.wait(remaining)
        return True

    def snapshot(self):
        with self.condition:
            return {'pending':len(self.pending),'inflight':len(self.inflight),
                    'seconds_since_success':round(monotonic()-self.last_success,3),
                    'thread_alive':self.thread.is_alive()}
