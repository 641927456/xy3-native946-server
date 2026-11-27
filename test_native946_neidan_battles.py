"""Pill costs through the real shared-battle and training dispatch paths."""
import unittest

import test_native946_wilderness_party as wild_tests
from native946_neidan_legacy import LegacyNeidan


class NeidanBattleTests(unittest.TestCase):
    def setUp(self):
        self.f=wild_tests.WildernessPartyTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.pets={}
        for game in self.f.games:
            sid=game.summons.claim_starter();pills=LegacyNeidan(game.summons)
            pills.grant_test_kit()
            with game.progression.transaction(write=False) as db:
                iid=db.execute('SELECT n.item_id FROM legacy_neidan_items n JOIN inventory i ON i.id=n.item_id '
                               'WHERE i.character_id=? AND n.kind=211',(game.progression.cid,)).fetchone()[0]
            pills.use(sid,iid)
            self.pets[game.character['id']]=sid

    def test_two_players_share_battle_and_each_pay_once_without_proc(self):
        battle=self.f.battle(pet=True)
        self.f.fight(battle)
        for game in self.f.games:
            sid=self.pets[game.character['id']]
            before=game.summons.get(sid)
            self.assertEqual(before['neidan'][0]['energy'],5999)
            self.assertGreater(before['neidan'][0]['experience'],0)
            self.f.call(game,2184)
            game.summons.settle(sid,battle.id,0,0,False)
            self.assertEqual(game.summons.get(sid),before)

    def test_party_escape_charges_participants_without_victory_experience(self):
        self.f.battle(pet=True)
        self.f.call(self.f.a,2178,2)
        for game in self.f.games:
            row=game.summons.get(self.pets[game.character['id']])['neidan'][0]
            self.assertEqual((row['energy'],row['experience']),(5999,0))

    def test_training_protocol_completion_charges_once(self):
        game=self.f.a;sid=self.pets[game.character['id']]
        self.f.buff(game);game.begin_battle()
        for _ in range(10):
            if game.battle is None:break
            if game.battle['phase']=='input':
                self.f.call(game,2177,3,0)
                if game.battle['phase']=='input':self.f.call(game,2181,3,0)
            if game.battle and game.battle['phase']=='drawing':self.f.call(game,2184)
        self.assertIsNone(game.battle)
        before=game.summons.get(sid)
        self.assertEqual(before['neidan'][0]['energy'],5999)
        self.f.call(game,2184)
        self.assertEqual(game.summons.get(sid),before)

    def test_everyone_disconnects_charges_once_without_victory_rewards(self):
        battle=self.f.battle(pet=True)
        for game in self.f.games:self.f.svc.detach(game.character['id'])
        self.assertEqual(battle.state['phase'],'finished')
        battle.finish(False)
        for game in self.f.games:
            row=game.summons.get(self.pets[game.character['id']])['neidan'][0]
            self.assertEqual((row['energy'],row['experience']),(5999,0))


if __name__=='__main__':unittest.main()
