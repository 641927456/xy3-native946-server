from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from native946_acceptance import cleanup,dispatch,late_commands,prepare_companion,clear_companion,decorate_packets
from native946_codec import packet

class AcceptanceTests(unittest.TestCase):
    def test_dispatch_uses_existing_gameplay_receiver_and_native_encoding(self):
        login=SimpleNamespace(receive=Mock(return_value='reply'))
        self.assertEqual(dispatch(login,dict(message_id=2177,values=[3,901000])),'reply')
        login.receive.assert_called_once_with(packet(2177,['number','number'],[3,901000]))

    def test_out_of_scope_or_malformed_requests_never_reach_game(self):
        login=SimpleNamespace(receive=Mock())
        for request in [dict(message_id=913,values=[]),dict(message_id=2177,values=[3]),
                        dict(message_id=2177,values=[True,0]),dict(message_id=752,values=[1,'x'*129]),
                        dict(message_id=1032,values=[256,900001])]:
            with self.assertRaises((ValueError,OverflowError)):dispatch(login,request)
        login.receive.assert_not_called()

    def test_late_attach_bridge_reuses_one_bounded_owner_thread_queue(self):
        handler=SimpleNamespace(server=SimpleNamespace(native_test_control=SimpleNamespace(character_id=1)),send_test_commands=Mock())
        login=SimpleNamespace(_acceptance_late_attach=True,current_character={'id':1})
        late_commands(handler,login,4321);queue=handler._acceptance_commands
        late_commands(handler,login,4321)
        self.assertIs(queue,handler._acceptance_commands);self.assertEqual(queue.maxsize,8)
        self.assertEqual(handler.send_test_commands.call_count,2)

    def test_startup_control_and_other_characters_do_not_change_queue_binding(self):
        handler=SimpleNamespace(server=SimpleNamespace(native_test_control=SimpleNamespace(character_id=1)),send_test_commands=Mock())
        for login in [None,SimpleNamespace(current_character={'id':1}),SimpleNamespace(_acceptance_late_attach=True,current_character={'id':2})]:
            late_commands(handler,login,4321)
        handler.send_test_commands.assert_not_called()
        self.assertFalse(hasattr(handler,'_acceptance_commands'))

    def test_companion_ticket_uses_only_current_owned_account_and_one_port(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        pem=rsa.generate_private_key(public_exponent=65537,key_size=1024).public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        ordinary=Mock(return_value={'ordinary':True})
        server=SimpleNamespace(native_test_control=SimpleNamespace(character_id=1),native_peer_configure=ordinary)
        login=SimpleNamespace(source_port=40001,current_character={'id':1},username='owned',gameplay=SimpleNamespace(battle=None),_acceptance_server=server,
                              tickets=SimpleNamespace(issue=Mock(return_value=b'ticket')),characters=SimpleNamespace(create=Mock(return_value={'id':2,'name':'组队验收'})))
        prepare_companion(login,{'source_port':40002,'public_key':pem})
        login.tickets.issue.assert_called_once_with('owned',pem.encode())
        login.characters.create.assert_called_once_with('owned','组队验收',702)
        self.assertTrue(server.native_peer_configure(40002)['owned_acceptance_companion'])
        self.assertTrue(server.native_peer_configure(40003)['ordinary'])
        with self.assertRaises(ValueError):prepare_companion(login,{'source_port':40004,'public_key':pem})
        clear_companion(login)
        self.assertTrue(server.native_peer_configure(40002)['ordinary'])
        self.assertEqual(login.tickets.issue.call_count,1)

    def test_invalid_companion_request_cannot_issue_ticket(self):
        login=SimpleNamespace(source_port=40001,tickets=SimpleNamespace(issue=Mock()))
        for request in [{'source_port':40001,'public_key':'x'},{'source_port':True,'public_key':'x'},{'source_port':40002,'public_key':'x'}]:
            with self.assertRaises(ValueError):prepare_companion(login,request)
        login.tickets.issue.assert_not_called()

    def test_cleanup_restores_state_and_detaches_acceptance_controller(self):
        server=SimpleNamespace(native_test_control=object())
        game=SimpleNamespace(
            battle=None,
            progression=SimpleNamespace(set_vitals=Mock()),
            summons=SimpleNamespace(set_vitals=Mock(),status=Mock(),active=Mock(return_value={'id':77})),
            party_service=SimpleNamespace(enqueue=Mock()),
            sync_progression=Mock(return_value=(packet(98,['string','string','number','number'],['','',1,0]),)),
        )
        login=SimpleNamespace(gameplay=game,_acceptance_server=server,_acceptance_late_attach=True,
                              _guardian_passive_restore=dict(hp=321,mp=123,fight=None,fixtures={77:(200,100)}),
                              _background_round=('fixture',1))
        cleanup(login,notify=True)
        game.progression.set_vitals.assert_called_once_with(321,123)
        game.summons.set_vitals.assert_called_once_with(77,200,100)
        game.summons.status.assert_called_once_with(77,2)
        game.party_service.enqueue.assert_called_once()
        self.assertFalse(login._acceptance_late_attach)
        self.assertIsNone(login._acceptance_server)
        self.assertIsNone(login._background_round)
        self.assertIsNone(server.native_test_control)
        self.assertFalse(hasattr(login,'_guardian_passive_restore'))
        cleanup(login)

    def test_background_input_ack_is_preserved_before_auto_panel_cancel(self):
        ack=packet(2152,['number'],[1]);ordinary=packet(2135,['number'],[1])
        game=SimpleNamespace(_background_round=('battle',1),battle={'id':'battle','round':1})
        self.assertEqual(decorate_packets(game,(ack,ordinary)),(ack,packet(5349,[],[]),ordinary))
        self.assertEqual(decorate_packets(SimpleNamespace(),ack),ack)
        game.battle={'id':'other','round':1}
        self.assertEqual(decorate_packets(game,ack),ack)

    def test_system_notification_always_contains_both_native_required_fields(self):
        from native946_login import MessageReader
        for payload,flag in [(packet(97,['string'],['野外战斗胜利']),0),(packet(97,['string','number'],['野外战斗胜利',9]),9)]:
            reader=MessageReader(payload[1:]);self.assertEqual(reader.string().decode('gb18030'),'野外战斗胜利')
            self.assertEqual(reader.integer(),flag);reader.finish()

if __name__=='__main__':unittest.main()
