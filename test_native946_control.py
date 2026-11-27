from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from native946_control import Command, LocalTestControl, execute
from server import ConnectionHandler
from native946_codec import packet
from server import native_receive_block


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = LocalTestControl(1, Path(self.temp.name) / 'token')

    def test_wrong_token_never_queues_an_action(self):
        queue = Queue()
        self.control.register(100, SimpleNamespace(current_character={'id': 1}), queue)
        with self.assertRaises(ValueError):
            self.control.submit('wrong', {'action': 'attack'})
        self.assertTrue(queue.empty())

    def test_other_characters_are_not_controllable(self):
        self.control.register(100, SimpleNamespace(current_character={'id': 2}), Queue())
        with self.assertRaises(ValueError):
            self.control.submit(self.control.token, {'action': 'attack'})

    def test_duplicate_native_sessions_are_not_selected_arbitrarily(self):
        for port in (100, 101):
            self.control.register(port, SimpleNamespace(current_character={'id': 1}), Queue())
        with self.assertRaises(ValueError):
            self.control.submit(self.control.token, {'action': 'train'})

    def test_arbitrary_protocols_are_not_accepted(self):
        with self.assertRaises(ValueError):
            self.control.submit(self.control.token, {'action': 'send_raw', 'data': '00'})

    def test_socket_thread_status_does_not_conflict_with_event_logging(self):
        handler = object.__new__(ConnectionHandler)
        game = SimpleNamespace(character={'id':1, 'x':300, 'y':210}, battle=None, last_event=None)
        login = SimpleNamespace(current_character=game.character, gameplay=game)
        queue = Queue()
        command = Command({'action':'status'})
        queue.put(command)
        with patch('server.record') as log:
            handler.send_test_commands(self.control, login, queue, 100)
        self.assertTrue(command.done.is_set())
        self.assertEqual(command.result['position'], [300,210])
        self.assertEqual(log.call_args.args[0], 'native_background_test')
        self.assertEqual(log.call_args.kwargs['command_id'], command.command_id)
        self.assertNotIn('result',log.call_args.kwargs)

    def test_bad_test_command_does_not_escape_game_socket_thread(self):
        handler = object.__new__(ConnectionHandler)
        game = SimpleNamespace(character={'id':1, 'x':300, 'y':210}, battle=None, last_event=None)
        login = SimpleNamespace(current_character=game.character, gameplay=game)
        queue = Queue()
        command = Command({'action':'move', 'x':-1, 'y':0})
        queue.put(command)
        handler.send_test_commands(self.control, login, queue, 100)
        self.assertTrue(command.done.is_set())
        self.assertIn('error', command.result)

    def test_socket_thread_delivers_timeout_without_a_background_command(self):
        handler=object.__new__(ConnectionHandler)
        handler.request=SimpleNamespace(sendall=Mock())
        reply=packet(2152,['number'],[1])
        game=SimpleNamespace(tick=Mock(return_value=(reply,)),last_event={'action':'battle_input_timeout'})
        with patch('server.record') as record:
            handler.send_gameplay_tick(SimpleNamespace(gameplay=game),100)
        game.tick.assert_called_once_with()
        handler.request.sendall.assert_called_once_with(native_receive_block(reply))
        self.assertEqual(record.call_args.kwargs['action'],'battle_input_timeout')
        self.assertIsNone(game.last_event)

    def test_background_orders_do_not_trigger_native_no_operation_handler(self):
        ack=packet(2152,['number'],[1]);draw=packet(2167,[],[])
        game=SimpleNamespace(character={'id':1,'x':306,'y':211},last_event=None,battle={'id':'test','round':1})
        login=SimpleNamespace(gameplay=game,receive=Mock(return_value=(ack,draw)))
        for action in ['attack','skill','pet_attack','pet_defend']:
            with self.subTest(action=action):
                self.assertEqual(execute(login,{'action':action,'skill':6100}),(ack,packet(5349,[],[]),draw))
                self.assertEqual(game._background_round,('test',1))


if __name__ == '__main__':
    unittest.main()
