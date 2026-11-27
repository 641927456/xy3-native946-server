"""Bounded scheduling and measurements for the local native server.

This module stays loaded across gameplay reloads; sockets, receipts and the
asynchronous logger must not be recreated by a gameplay update.
"""
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Full, Queue
import select
import socket
from threading import Event, Lock, Thread
from time import monotonic
import json


RUNTIME_VERSION = 1
COMMAND_BATCH = 2
COMMAND_BUDGET_SECONDS = .002


class Measurements:
    def __init__(self):
        self.lock = Lock()
        self.samples = defaultdict(lambda: deque(maxlen=1024))
        self.counts = Counter()
        self.frames = {}

    def observe(self, name, seconds):
        with self.lock:
            self.samples[name if name in self.samples or len(self.samples) < 128 else 'other'].append(seconds * 1000)

    def increment(self, name, amount=1):
        with self.lock:
            self.counts[name] += amount

    def network_event(self, event, details):
        port = details.get('source_port')
        now = monotonic()
        with self.lock:
            if event == 'tcp_native_frame':
                if len(self.frames) < 128 or port in self.frames:
                    self.frames[port] = (now, details.get('message_id'))
                self.counts['native_frames'] += 1
            elif event == 'tcp_native_reply':
                start = self.frames.pop(port, None)
                if start is not None:
                    self.samples['native_frame_to_first_reply_ms'].append((now-start[0])*1000)
                self.counts['native_replies'] += 1
            elif event.startswith(('tcp_peer_', 'tcp_idle_', 'tcp_disconnected', 'tcp_local_')):
                self.frames.pop(port, None)

    def snapshot(self):
        with self.lock:
            copied = {name:list(values) for name,values in self.samples.items()}
            counts = dict(self.counts)
        result = {}
        for name, values in copied.items():
            values.sort()
            if values:
                result[name] = {'samples':len(values), 'p50_ms':round(values[(len(values)-1)//2],3),
                                'p95_ms':round(values[min(len(values)-1, int(len(values)*.95))],3),
                                'p99_ms':round(values[min(len(values)-1, int(len(values)*.99))],3),
                                'max_ms':round(values[-1],3)}
        return {'counters':counts, 'latency':result}


METRICS = Measurements()


class AsyncRecorder:
    def __init__(self, path, capacity=4096, sink=None):
        self.path = Path(path)
        self.queue = Queue(maxsize=capacity)
        self.sink = sink
        self.thread = Thread(target=self._run, name='native-log-writer', daemon=True)
        self.thread.start()

    def __call__(self, event, **details):
        METRICS.network_event(event, details)
        entry = {'utc':datetime.now(timezone.utc).isoformat(), 'event':event, **details}
        # High-frequency trace data cannot consume the reserved error capacity.
        if event in {'tcp_received','tcp_native_frame','tcp_native_reply'} and self.queue.qsize() >= self.queue.maxsize*7//8:
            METRICS.increment('log_trace_dropped')
            return
        try:
            self.queue.put_nowait(entry)
        except Full:
            METRICS.increment('log_queue_full')

    def _run(self):
        while True:
            entry = self.queue.get()
            if entry is None:
                return
            if isinstance(entry, Event):
                entry.set()
                continue
            try:
                if self.sink is not None:
                    self.sink(entry)
                else:
                    # Only this worker touches the file. Producers never wait
                    # for filesystem access or JSON serialization.
                    with self.path.open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(entry, ensure_ascii=False)+'\n')
            except Exception:
                METRICS.increment('log_write_errors')

    def flush(self, timeout=1):
        marker = Event()
        try:
            self.queue.put_nowait(marker)
        except Full:
            return False
        return marker.wait(timeout)

    def close(self, timeout=1):
        try:
            self.queue.put_nowait(None)
        except Full:
            return False
        self.thread.join(timeout)
        return not self.thread.is_alive()


class WakeableSocket:
    """Wake an existing recv loop without interrupting or replacing its TCP stream."""
    def __init__(self, transport):
        self.transport = transport
        self.reader, self.writer = socket.socketpair()
        self.reader.setblocking(False)
        self.writer.setblocking(False)
        self.closed = False
        transport.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def __getattr__(self, name):
        return getattr(self.transport, name)

    def wake(self):
        try:
            self.writer.send(b'\x01')
        except (BlockingIOError, OSError):
            pass

    def recv(self, size, *args):
        ready, _, _ = select.select([self.transport, self.reader], [], [], self.transport.gettimeout())
        if self.reader in ready:
            try:
                while self.reader.recv(4096):
                    pass
            except BlockingIOError:
                pass
        if self.transport in ready:
            return self.transport.recv(size, *args)
        # The old connection loop already treats this as an opportunity to
        # process commands/timers; it keeps its original overall idle deadline.
        raise TimeoutError()

    def close_waker(self):
        if not self.closed:
            self.closed = True
            self.reader.close()
            self.writer.close()


_RECORDER = None


def install_live_adapter():
    """Install validated helper methods on the active server, on its owner thread.

    No active handle() frame, login objects, tokens or TCP stream are replaced.
    New connections use the updated handle(); the current loop picks up helper
    method changes on its next iteration.
    """
    import sys
    active = sys.modules.get('__main__')
    expected = Path(__file__).with_name('server.py').resolve()
    if not active or Path(getattr(active,'__file__','')).resolve() != expected:
        return False
    import server as template
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = active.record if isinstance(active.record,AsyncRecorder) else AsyncRecorder(active.LOG_FILE)
    active.record = template.record = _RECORDER
    template.LOG_FILE = active.LOG_FILE
    for name in ('setup','finish','send_test_commands','send_gameplay_tick','handle'):
        setattr(active.ConnectionHandler, name, getattr(template.ConnectionHandler,name))
    active.UpdateHandler.do_POST = template.UpdateHandler.do_POST
    active.UpdateHandler.setup = template.UpdateHandler.setup
    for name in ('request_queue_size','max_connections','process_request','process_request_thread'):
        setattr(active.LoopbackListener, name, getattr(template.LoopbackListener,name))
    # The original HTTP listener remains open; only future admission uses the
    # bounded subclass. Do not touch another embedded HTTP server.
    import gc
    from http.server import ThreadingHTTPServer
    for obj in gc.get_objects():
        if (isinstance(obj,ThreadingHTTPServer) and
                getattr(obj,'RequestHandlerClass',None) is active.UpdateHandler and
                obj.server_address == ('127.0.0.1',18127)):
            obj.__class__ = template.LocalHTTPServer
            obj.socket.listen(128)
    return True


def prepare_handler(handler, control, login, commands, port):
    from native946_control import LocalTestControl
    request = getattr(handler,'request',None)
    if request is not None and hasattr(request,'recv') and not isinstance(request, WakeableSocket):
        handler.request = WakeableSocket(handler.request)
    if control.__class__ is not LocalTestControl:
        control.__class__ = LocalTestControl
    control.ensure_runtime()
    wake = handler.request.wake if isinstance(getattr(handler,'request',None),WakeableSocket) else None
    control.register(port, login, commands, wake)
    server = getattr(handler,'server',None)
    if server is not None and not getattr(server,'_backlog_configured',False):
        server.socket.listen(128)
        server._backlog_configured = True
