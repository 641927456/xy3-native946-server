from copy import deepcopy
from contextlib import closing
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace
import socket
import sqlite3
import unittest
import subprocess
import sys
from unittest.mock import patch

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_control import Command, ControlError, LocalTestControl, state
from native946_gameplay import NativeGameplay
from native946_codec import number
from native946_login import MessageReader
from native946_runtime import AsyncRecorder, WakeableSocket
from native946_storage import StorageBusy, transaction
from native946_connections import enable_thread,close_thread
from native946_map import DIRECTIONS
from native946_login import string_field
from native946_game_login import NativeGameLogin, message_id
from struct import pack
from server import ConnectionHandler, LoopbackListener, UpdateHandler


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.control=LocalTestControl(1,Path(self.temp.name)/'token')
        self.queue=Queue(maxsize=8)
        self.game=SimpleNamespace(character={'id':1,'x':300,'y':210},battle=None,last_event=None)
        self.login=SimpleNamespace(current_character=self.game.character,gameplay=self.game)
        self.control.register(100,self.login,self.queue)
        self.handler=object.__new__(ConnectionHandler)

    def test_timed_out_queued_mutation_never_executes_later(self):
        self.control.command_timeout=.02
        with self.assertRaises(ControlError) as caught:
            self.control.submit(self.control.token,{'action':'attack','request_id':'expired'})
        self.assertEqual(caught.exception.status,504)
        with patch('native946_control.execute') as execute:
            self.handler.send_test_commands(self.control,self.login,self.queue,100)
        execute.assert_not_called()
        receipt=self.control.submit(self.control.token,{'action':'command_result','command_id':'expired'})
        self.assertEqual(receipt['outcome'],'expired')

    def test_running_timeout_retains_result_and_retry_does_not_repeat(self):
        self.control.command_timeout=.08
        finish=Event()
        epoch=self.control.sessions[100].epoch
        def worker():
            command=self.queue.get(timeout=1)
            if command.begin(epoch):
                finish.wait(1)
                command.complete({'applied':True})
        thread=Thread(target=worker);thread.start()
        try:
            with self.assertRaises(ControlError) as caught:
                self.control.submit(self.control.token,{'action':'attack','request_id':'running'})
            self.assertEqual(caught.exception.status,202)
        finally:
            finish.set();thread.join(1)
        reply=self.control.submit(self.control.token,{'action':'attack','request_id':'running'})
        self.assertTrue(reply['applied'])
        self.assertEqual(reply['_command']['outcome'],'completed')
        self.assertTrue(self.queue.empty())

    def test_replenished_diagnostic_queue_has_a_per_loop_limit(self):
        for _ in range(8):self.queue.put(Command({'action':'status'}))
        def replenish(*args):
            self.queue.put_nowait(Command({'action':'status'}))
        with patch('native946_control.execute',side_effect=replenish) as execute,patch('server.record'),patch('server.monotonic',return_value=0):
            self.handler.send_test_commands(self.control,self.login,self.queue,100)
        self.assertEqual(execute.call_count,2)
        self.assertEqual(self.queue.qsize(),8)

    def test_disconnect_cancels_pending_and_old_epoch_cannot_execute(self):
        epoch=self.control.sessions[100].epoch
        command=Command({'action':'attack'},epoch=epoch)
        self.control.receipts[command.command_id]=command
        self.control.unregister(100)
        self.control.register(100,self.login,Queue())
        self.assertEqual(command.outcome,'disconnected')
        self.assertFalse(command.begin(self.control.sessions[100].epoch))

    def test_port_replacement_and_character_change_cancel_old_commands(self):
        epoch=self.control.sessions[100].epoch
        command=Command({'action':'attack'},epoch=epoch)
        self.control.receipts[command.command_id]=command
        self.control.register(100,self.login,Queue())
        self.assertEqual(command.outcome,'disconnected')
        self.control.register(100,SimpleNamespace(current_character={'id':2}),Queue())
        self.assertNotIn(100,self.control.sessions)

    def test_full_queue_rejects_without_waiting(self):
        for _ in range(8):self.queue.put(Command({'action':'status'}))
        with self.assertRaises(ControlError) as caught:
            self.control.submit(self.control.token,{'action':'status'})
        self.assertEqual(caught.exception.status,429)

    def test_metrics_never_reads_player_database_or_queues(self):
        with patch('native946_control.state',side_effect=AssertionError('database state')):
            result=self.control.submit(self.control.token,{'action':'metrics'})
        self.assertEqual(result['queue_depths'],[0])
        self.assertTrue(self.queue.empty())

    def test_http_adopts_new_controller_before_checking_new_action_whitelist(self):
        from unittest.mock import Mock
        import native946_control
        class Legacy(native946_control.LocalTestControl):wire_version=0
        self.control.__class__=Legacy
        handler=object.__new__(UpdateHandler)
        handler.path='/local/native-test'
        handler.server=SimpleNamespace(native_test_control=self.control)
        handler.headers={'X-Local-Test-Token':self.control.token}
        handler.local_account_service=Mock()
        handler.read_credentials=Mock(return_value={'action':'metrics'})
        handler.send_json=Mock()
        handler.do_POST()
        status,result=handler.send_json.call_args.args
        self.assertEqual(status,200)
        self.assertTrue(result['controller_runtime_ready'])
        self.assertEqual(result['control_wire_version'],native946_control.BACKGROUND_WIRE_VERSION)

    def test_result_is_a_snapshot_not_a_mutable_battle_reference(self):
        self.game.battle={'round':1,'orders':{}}
        result=state(self.login)
        self.game.battle['orders'][1]={'damage':5}
        self.assertEqual(result['battle']['orders'],{})

    def test_reload_preserves_binding_identity_for_inflight_old_submit_frames(self):
        script='''from importlib import reload
from tempfile import TemporaryDirectory
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
import native946_control as module
with TemporaryDirectory() as temp:
    control=module.LocalTestControl(1,Path(temp)/'token')
    queue=Queue()
    control.register(100,SimpleNamespace(current_character={'id':1}),queue)
    binding=control.sessions[100]
    old_class=control.__class__
    reload(module)
    assert isinstance(binding,module.Binding)
    old_class.ensure_runtime(control)
    assert control.sessions[100].queue is queue
    assert control.sessions[100].epoch==binding.epoch
'''
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'isolated.sqlite3'
        LocalAccounts(self.path).register({'username':'owner946','password':'123456789'})
        self.characters=Characters(self.path)
        self.character=self.characters.create('owner946','排队测试',702)
        self.game=NativeGameplay(self.character,self.characters,lambda:(),practice_trainer=True)

    def test_snapshot_does_not_contend_with_an_existing_reserved_writer(self):
        writer=sqlite3.connect(self.path)
        try:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute('UPDATE characters SET gold=99')
            before=monotonic()
            self.assertEqual(self.game.progression.snapshot()['gold'],0)
            self.assertEqual(self.game.summons.snapshot(),[])
            self.assertLess(monotonic()-before,.2)
        finally:writer.rollback();writer.close()

    def test_read_transaction_cannot_modify_data(self):
        with self.assertRaises(sqlite3.OperationalError):
            with transaction(self.path) as db:
                db.execute('UPDATE characters SET gold=99')
        self.assertEqual(self.game.progression.snapshot()['gold'],0)

    def test_invalid_trailing_bytes_roll_back_already_validated_changes(self):
        before=self.game.progression.snapshot()
        result=self.game.receive(739,MessageReader(b''.join(number(n) for n in (1,0,0,0))+b'!'))
        self.assertEqual(result[0],98)
        self.assertEqual(self.game.progression.snapshot(),before)

    def test_busy_writer_gives_prompt_feedback_without_mutating_role(self):
        writer=sqlite3.connect(self.path)
        before=self.game.progression.snapshot()
        try:
            writer.execute('BEGIN IMMEDIATE')
            start=monotonic()
            result=self.game.receive(739,MessageReader(b''.join(number(n) for n in (1,0,0,0))))
            self.assertLess(monotonic()-start,.2)
            self.assertEqual(result[0],98)
            self.assertTrue(self.game.last_event['storage_busy'])
        finally:writer.rollback();writer.close()
        self.assertEqual(self.game.progression.snapshot(),before)

    def test_busy_commit_rolls_back_all_writes_and_packet_caches(self):
        reader=sqlite3.connect(self.path)
        before=self.game.progression.snapshot()
        known=set(self.game.progression.known_items)
        try:
            reader.execute('BEGIN')
            reader.execute('SELECT * FROM characters').fetchall()
            result=self.game.receive(739,MessageReader(b''.join(number(n) for n in (1,0,0,0))))
            self.assertEqual(result[0],98)
        finally:reader.rollback();reader.close()
        self.assertEqual(self.game.progression.snapshot(),before)
        self.assertEqual(self.game.progression.known_items,known)

    def test_battle_reward_and_pet_reward_roll_back_together(self):
        self.game.summons.claim_starter()
        self.game.begin_battle()
        self.game.battle.update(phase='drawing',victory=True,enemy_hp=0)
        battle=deepcopy(self.game.battle)
        before=self.game.progression.snapshot()
        with patch.object(self.game.summons,'settle',side_effect=StorageBusy('busy')):
            result=self.game.receive(2184,MessageReader(b''))
        self.assertEqual(result[0],98)
        self.assertEqual(self.game.battle,battle)
        self.assertEqual(self.game.progression.snapshot(),before)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM battle_results').fetchone()[0],0)

    def test_full_sync_uses_one_consistent_hero_snapshot(self):
        with patch.object(self.game.progression,'snapshot',wraps=self.game.progression.snapshot) as snapshot:
            self.game.progression.sync()
        self.assertEqual(snapshot.call_count,1)

    def deferred(self):
        self.characters.enable_deferred_positions()
        self.addCleanup(lambda:self.characters.position_writer.close(1))
        self.game.ensure_progression()
        return self.characters.position_writer

    def test_movement_remains_immediate_while_another_writer_holds_database(self):
        writer=self.deferred()
        start=(self.character['x'],self.character['y'])
        code,delta=next((code,delta) for code,delta in DIRECTIONS.items()
                        if abs(delta[0])+abs(delta[1])==1 and self.game.grid.walkable(start[0]+delta[0],start[1]+delta[1]))
        external=sqlite3.connect(self.path)
        try:
            external.execute('BEGIN IMMEDIATE')
            result=self.game.receive(1643,MessageReader(number(4)+pack('<HH',*start)+string_field(bytes([code]))))
            self.assertIsNone(result)
            self.assertEqual((self.character['x'],self.character['y']),(start[0]+delta[0],start[1]+delta[1]))
        finally:external.rollback();external.close()
        self.assertTrue(writer.flush(1))
        saved=self.characters.list('owner946')[0]
        self.assertEqual((saved['x'],saved['y']),(self.character['x'],self.character['y']))

    def test_coalescing_saves_latest_position_and_old_revision_cannot_overwrite(self):
        writer=self.deferred()
        cid=self.character['id']
        for i in range(80):writer.submit('owner946',cid,2001,300+i,210)
        self.assertLessEqual(writer.snapshot()['pending'],1)
        self.assertTrue(writer.flush(1))
        with transaction(self.path,write=True) as db:
            self.assertEqual(writer.apply(db,('owner946',cid),(2001,1,1,1)),0)
        saved=self.characters.list('owner946')[0]
        self.assertEqual((saved['x'],saved['y']),(379,210))

    def test_attribute_cache_invalidates_on_changes_and_failed_commit(self):
        self.deferred()
        initial=self.game.progression.snapshot()['cor']
        self.game.receive(739,MessageReader(b''.join(number(n) for n in (1,0,0,0))))
        self.assertEqual(self.game.progression.snapshot()['cor'],initial+1)
        self.game.receive(739,MessageReader(b''.join(number(n) for n in (1,0,0,0))+b'!'))
        self.assertEqual(self.game.progression.snapshot()['cor'],initial+1)

    def test_cached_read_panels_do_not_reopen_database_each_time(self):
        self.deferred()
        self.game.progression.snapshot()
        with patch('native946_storage.connect',side_effect=AssertionError('Unnecessary database query')):
            self.game.receive(742,MessageReader(b'\0'))
            self.game.receive(1985,MessageReader(b''))

    def test_thread_connection_reuse_keeps_transaction_boundaries_and_read_only(self):
        enable_thread()
        try:
            with transaction(self.path) as db:first=db.connection
            self.assertFalse(first.in_transaction)
            with transaction(self.path,write=True) as db:
                self.assertIs(db.connection,first)
                db.execute('UPDATE characters SET gold=5')
            with self.assertRaises(sqlite3.OperationalError):
                with transaction(self.path) as db:db.execute('UPDATE characters SET gold=99')
            self.assertFalse(first.in_transaction)
        finally:close_thread()
        self.assertEqual(self.game.progression.snapshot()['gold'],5)

    def login(self,port):
        login=NativeGameLogin(port,None,number,lambda *a,**k:None,self.characters)
        login.username='owner946'
        return login

    def test_duplicate_character_connection_cannot_replace_owner_or_reset_battle(self):
        first=self.login(100);second=self.login(101)
        enter=message_id(915)+number(self.character['id'])
        self.assertIsInstance(first.receive(enter),tuple)
        original=first.gameplay
        rejected=second.receive(enter)
        self.assertEqual(rejected[:3],message_id(888))
        self.assertIsNone(second.gameplay)
        self.assertIsNone(first.receive(enter))
        self.assertIs(first.gameplay,original)

    def test_busy_world_entry_retries_without_another_login_or_password(self):
        login=self.login(100)
        original=self.characters.enter
        attempts=[StorageBusy('busy'),None]
        def enter(*args):
            if attempts.pop(0) is not None:raise StorageBusy('busy')
            return original(*args)
        with patch.object(self.characters,'enter',side_effect=enter):
            self.assertIsNone(login.receive(message_id(915)+number(self.character['id'])))
            self.assertIsNone(login.gameplay)
            login._enter_retry_at=0
            self.assertIsInstance(login.tick(),tuple)
        self.assertIsNotNone(login.gameplay)
        self.assertEqual(login.username,'owner946')
        self.assertIsNone(login._pending_enter)

    def test_expired_entry_releases_pending_state_instead_of_waiting_forever(self):
        login=self.login(100)
        login._pending_enter=(self.character['id'],0)
        login._enter_retry_at=0
        self.assertEqual(login.tick()[:3],message_id(888))
        self.assertIsNone(login._pending_enter)

    def test_navigation_wire_uses_grid_xy_then_scene_and_npc(self):
        result=self.game.navigate_to_trainer()
        self.assertEqual(result[:3],message_id(2966))
        reader=MessageReader(result[3:])
        self.assertEqual([reader.integer() for _ in range(4)],
                         [*self.game.npc_position,self.character['map_id'],900001])
        self.assertEqual(reader.string().decode('gb18030'),'本机教头')
        reader.finish()

    def test_navigation_cannot_interrupt_native_battle(self):
        self.game.begin_battle()
        before=deepcopy(self.game.battle)
        with self.assertRaises(ValueError):self.game.navigate_to_trainer()
        self.assertEqual(self.game.battle,before)


class TransportTests(unittest.TestCase):
    def tcp_pair(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
        client=socket.create_connection(listener.getsockname(),timeout=1)
        peer,_=listener.accept();listener.close()
        peer.settimeout(1)
        wrapper=WakeableSocket(peer)
        self.addCleanup(client.close);self.addCleanup(peer.close);self.addCleanup(wrapper.close_waker)
        return client,wrapper

    def test_control_wakeup_does_not_wait_for_idle_poll_or_corrupt_bytes(self):
        client,peer=self.tcp_pair()
        result=[];started=Event()
        def receive():
            started.set()
            try:peer.recv(4096)
            except TimeoutError:result.append('woken')
        thread=Thread(target=receive);thread.start();started.wait(1)
        before=monotonic();peer.wake();thread.join(.2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result,['woken'])
        self.assertLess(monotonic()-before,.2)
        client.sendall(b'original-native-bytes')
        self.assertEqual(peer.recv(4096),b'original-native-bytes')
        self.assertEqual(peer.getsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY),1)

    def test_slow_logger_never_blocks_game_thread_and_is_bounded(self):
        entered=Event();release=Event()
        def sink(entry):entered.set();release.wait(1)
        with TemporaryDirectory() as temp:
            logger=AsyncRecorder(Path(temp)/'log',capacity=8,sink=sink)
            logger('first');entered.wait(1)
            before=monotonic()
            for _ in range(64):logger('diagnostic')
            self.assertLess(monotonic()-before,.2)
            self.assertLessEqual(logger.queue.qsize(),8)
            release.set()
            deadline=monotonic()+1
            while logger.queue.full() and monotonic()<deadline:Event().wait(.001)
            self.assertTrue(logger.flush())
            self.assertTrue(logger.close())

    def test_admission_limit_rejects_excess_connection_without_starting_handler(self):
        import socketserver
        entered=Event();release=Event()
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):entered.set();release.wait(1)
        class Limited(LoopbackListener):max_connections=1
        with Limited(('127.0.0.1',0),Handler) as server:
            thread=Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start()
            first=socket.create_connection(server.server_address,timeout=1)
            try:
                self.assertTrue(entered.wait(1))
                with socket.create_connection(server.server_address,timeout=1) as second:
                    self.assertEqual(second.recv(1),b'')
            finally:
                release.set();first.close();server.shutdown();thread.join(1)


if __name__=='__main__':unittest.main()
