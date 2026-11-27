"""Scene entry stays consistent when another connection attaches mid-render."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from collections import deque
from unittest.mock import patch
import unittest

import native946_characters
import test_native946_wilderness_party as fixtures


class PartyConcurrencyTests(unittest.TestCase):
    setUp=fixtures.WildernessPartyTests.setUp

    def test_cached_service_upgrades_its_class_without_losing_connections_or_locks(self):
        from native946_party import service,PartyService
        class PreviousService:pass
        before=self.svc;lock=before.lock;games=before.games;battles=before.battles
        before.__class__=PreviousService
        try:
            refreshed=service(before.database)
            self.assertIs(refreshed,before);self.assertIs(type(refreshed),PartyService)
            self.assertIs(refreshed.lock,lock);self.assertIs(refreshed.games,games);self.assertIs(refreshed.battles,battles)
            self.assertTrue(callable(refreshed.refresh_run_speeds))
        finally:before.__class__=PartyService

    def overlap_attach(self,reader):
        entered=Event();attempted=Event();finished=Event()
        original=native946_characters.scene_actor_packet
        class Peer:pass
        other=Peer();other.character=deepcopy(self.b.character)
        other.character['id']=100001;other.battle=None
        def render(character):
            if not entered.is_set():
                entered.set();self.assertTrue(attempted.wait(2))
                # An unprotected iterator lets this mutation complete before
                # the next dict step; a protected snapshot releases it afterward.
                finished.wait(.05)
            return original(character)
        def attach():
            self.assertTrue(entered.wait(2));attempted.set()
            self.svc.attach(other);finished.set()
        with patch.object(native946_characters,'scene_actor_packet',render):
            with ThreadPoolExecutor(max_workers=2) as pool:
                writer=pool.submit(attach);result=reader(self.a);writer.result(timeout=2)
        self.assertIsInstance(result,tuple);self.assertTrue(entered.is_set());self.assertTrue(finished.is_set())
        self.assertIs(self.svc.games[100001],other)

    def test_scene_players_survives_concurrent_connection_attach(self):
        self.overlap_attach(self.svc.scene_players)

    def test_reconnect_packets_survives_concurrent_connection_attach(self):
        self.overlap_attach(self.svc.reconnect_packets)

    def test_broadcast_arriving_during_drain_is_preserved_for_next_poll(self):
        entered=Event();attempted=Event();finished=Event()
        class Inbox(deque):
            def __iter__(inbox):
                for value in super().__iter__():
                    yield value
                    if not entered.is_set():
                        entered.set();self.assertTrue(attempted.wait(2));finished.wait(.05)
        self.a._party_inbox=Inbox([b'first']);self.a._party_bytes=5
        def publish():
            self.assertTrue(entered.wait(2));attempted.set()
            self.svc.enqueue(self.a,b'second');finished.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer=pool.submit(publish);first=self.svc.drain(self.a);writer.result(timeout=2)
        self.assertEqual(first,(b'first',));self.assertEqual(self.a._party_bytes,6)
        self.assertEqual(self.svc.drain(self.a),(b'second',));self.assertEqual(self.a._party_bytes,0)
