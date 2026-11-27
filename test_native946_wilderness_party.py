from copy import deepcopy
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from time import monotonic
import json,unittest
from unittest.mock import patch

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import number,packet,text_notice
from native946_gameplay import NativeGameplay,TRAINER_ID
from native946_login import MessageReader
from native946_map import load_map
from native946_party import service
from native946_temp_inventory import PAGE as TEMP_PAGE, award, rows as temporary_items
from native946_storage import StorageBusy
from native946_wilderness import DATA,POLICY,WildBattle,moved,zone_at

def mid(p):return int.from_bytes(p[1:3],'little') if p[0]==0 else p[0]

class WildernessPartyTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'fixture.sqlite3';accounts=LocalAccounts(self.path)
        self.characters=Characters(self.path);self.games=[]
        for i in range(2):
            owner='wild'+str(i);accounts.register(dict(username=owner,password='fixture-password'))
            char=self.characters.create(owner,'野外测试'+str(i),702)
            g=NativeGameplay(char,self.characters,lambda:())
            g.source_port=30000+i;self.games.append(g)
        self.a,self.b=self.games;self.svc=self.a.party_service

    def call(self,g,kind,*args,tail=b''):
        result=g.receive(kind,MessageReader(b''.join(number(n) for n in args)+tail))
        return result if isinstance(result,tuple) else (result,) if result is not None else ()

    def team(self):
        self.call(self.a,2042,self.a.character['id'])
        self.call(self.a,2039,self.b.character['id'])
        self.call(self.b,2038,self.a.character['id'])
        self.assertEqual(len(self.svc.team(self.a.character['id'])['members']),2)

    def buff(self,g):
        with g.progression.transaction() as db:
            db.execute('UPDATE characters SET level=180 WHERE id=?',(g.character['id'],))
            db.execute('UPDATE progression SET cor=1000,mag=1000,strength=1000,speed=1000 WHERE character_id=?',(g.character['id'],))
        g.character['level']=180;s=g.progression.snapshot();g.progression.set_vitals(s['hpmax'],s['mpmax'])

    def battle(self,party=True,pet=False):
        if party:self.team()
        games=self.games if party else [self.a]
        for g in games:
            self.buff(g)
            if pet:g.summons.claim_starter()
        return WildBattle(self.a,games,DATA['zones'][0])

    def fight(self,battle):
        for _ in range(100):
            if battle.state['phase']=='finished':return
            if battle.state['phase']=='input':
                for actor in list(battle.allies()):
                    if battle.state['phase']!='input':break
                    game=battle.games[actor['owner']]
                    self.call(game,2181 if actor['kind']=='pet' else 2177,3,0)
            if battle.state['phase']=='drawing':
                for g in list(battle.games.values()):self.call(g,2184)
        self.fail('Battle did not finish')

    def test_wild_entry_and_exit_preserve_native_auto_panel(self):
        battle=self.battle(party=False)
        entry=battle.entry(self.a)
        ids=[mid(p) for p in entry]
        self.assertNotIn(5349,ids)
        self.assertFalse({98,100}.intersection(ids))
        self.assertLess(ids.index(2135),ids.index(2151))
        self.assertEqual(ids[ids.index(2151)+1:ids.index(2151)+3],[2703,2139])
        battle.finish(False)
        replies=self.svc.drain(self.a)
        self.assertNotIn(5349,[mid(p) for p in replies])
        self.assertIn(2146,[mid(p) for p in replies])

    def test_wrath_initialization_and_hit_updates_reach_each_team_member(self):
        from native946_role_battle import nums

        battle = self.battle()
        hero_ids = [game.character['id'] for game in self.games]
        initial = [nums(3458, cid, 250, 1000) for cid in hero_ids]
        for game in self.games:
            self.assertEqual([p for p in battle.entry(game) if mid(p) == 3458], initial)
            self.svc.drain(game)

        enemy, *others = battle.enemies()
        enemy.update(slot=1, skills=())
        for other in others:
            other['removed'] = True
        # Use the normal target rotation for a fixture with one live enemy.
        enemy.pop('enemy_spell_percent', None)
        hero = battle.state['fighters'][hero_ids[0]]
        hp_before = hero['hp']
        with patch('native946_book_combat.roll', return_value={'miss': False, 'damage': 50, 'effect': 0}):
            self.call(self.a, 2178, 1)
            member_replies = self.call(self.b, 2178, 1)
        gained = (hp_before - hero['hp']) * 1000 // hero['hpmax']
        self.assertGreater(gained, 0)
        incremented = 250 + gained
        for replies in (self.svc.drain(self.a), member_replies + self.svc.drain(self.b)):
            self.assertIn(nums(2158, hero_ids[0], 7, incremented), replies)
            self.assertNotIn(nums(2158, hero_ids[1], 7, incremented), replies)
        self.assertEqual([p for p in battle.entry(self.b) if mid(p) == 3458],
                         [nums(3458, hero_ids[0], incremented, 1000), initial[1]])

    def test_guardian_death_and_flash_complete_the_round_for_all_five_clients(self):
        from native946_guardian_magic import skills_packet
        from native946_role_battle import nums

        self.team()
        for i in range(2, 5):
            owner = f'wild{i}'
            LocalAccounts(self.path).register(dict(username=owner, password='fixture-password'))
            game = NativeGameplay(self.characters.create(owner, f'组队守护{i}', 702), self.characters, lambda: ())
            self.call(self.a, 2039, game.character['id'])
            self.call(game, 2038, self.a.character['id'])
            self.games.append(game)
        old_id = self.a.summons.acquire(3355, 'flash:active')
        new_id = self.a.summons.acquire(3356, 'flash:reserve')
        self.a.summons.status(old_id, 1)
        battle = WildBattle(self.a, self.svc.participants(self.a), DATA['zones'][0])
        owner = self.a.character['id']
        victim = battle.current_pet(owner)
        victim['hp'] = 1
        enemy, *others = battle.enemies()
        for other in others:
            other['removed'] = True
        enemy.pop('enemy_spell_percent', None)
        enemy['slot'] = battle.allies().index(victim) + 1

        for game in self.games:
            self.svc.drain(game)
            self.call(game, 2178, 1)
        with patch.object(battle.rng, 'randrange', return_value=0), patch(
            'native946_book_combat.roll', return_value=dict(miss=False, damage=100, effect=0)
        ):
            owner_replies = self.call(self.a, 2182, 1)
        self.assertEqual(battle.state['phase'], 'drawing')
        incoming = battle.current_pet(owner)
        self.assertEqual(incoming['id'], new_id)
        self.assertEqual(battle.state['used'][owner], {old_id, new_id})
        self.assertEqual(self.a.summons.get(old_id)['hp'], 0)
        for game in self.games:
            replies = (owner_replies if game is self.a else ()) + self.svc.drain(game)
            self.assertEqual([mid(p) for p in replies].count(2128), 1)
            self.assertIn(nums(2137, old_id), replies)
            self.assertIn(nums(2167), replies)
            self.assertEqual(nums(4206, new_id) in replies, game is self.a)
            if game is self.a:
                self.assertIn(skills_packet(incoming), replies)
        for game in self.games:
            self.call(game, 2184)
        self.assertEqual((battle.state['phase'], battle.state['round']), ('input', 2))
        self.assertIn(new_id, battle.required())
        self.assertNotIn(old_id, battle.required())

        # Let the server supply the last order, as it does for idle clients.
        # The same skill is now on the dying pet and its only reserve was used.
        incoming['hp'] = 1
        enemy['slot'] = battle.allies().index(incoming)
        enemy['initiative'] = 100000
        for game in self.games:
            self.call(game, 2178, 1)
        with patch('native946_book_combat.roll', return_value=dict(miss=False, damage=100, effect=0)):
            battle.tick(battle.state['deadline'] + 1)
        self.assertEqual(battle.state['phase'], 'drawing')
        self.assertEqual(self.a.summons.get(new_id)['hp'], 0)
        for game in self.games:
            replies = self.svc.drain(game)
            self.assertIn(nums(2167), replies)
            self.assertNotIn(2128, [mid(p) for p in replies])

    def test_wild_entry_retries_without_leaving_a_member_in_battle(self):
        self.team()
        zone = DATA['zones'][0]
        left, top, right, bottom = zone['bounds']
        x, y = (left + right) // 40, (top + bottom) // 40
        for game in self.games:
            game.character.update(map_id=zone['scene'], x=x, y=y)
            self.svc.drain(game)
        self.a._encounter_steps = POLICY['encounter_steps']
        self.b.dialog_open = True
        actions = self.b.dialog_actions = ['fixture']
        original = WildBattle.entry

        def entry(battle, game):
            if game is self.b:raise StorageBusy('busy second entry')
            return original(battle, game)

        before, after = (zone['scene'], x - 1, y), (zone['scene'], x, y)
        with patch.object(WildBattle, 'entry', entry), self.assertRaises(StorageBusy):
            moved(self.a, before, after)
        self.assertFalse(self.svc.battles)
        self.assertTrue(self.b.dialog_open)
        self.assertIs(self.b.dialog_actions, actions)
        for game in self.games:
            self.assertIsNone(game.battle)
            self.assertIsNone(getattr(game, '_wild_battle', None))
            self.assertFalse(self.svc.drain(game))
        moved(self.a, before, after)
        self.assertEqual(len(self.svc.battles), 2)
        for game in self.games:
            self.assertIn(2171, [mid(p) for p in self.svc.drain(game)])

    def test_flower_fruit_mountain_uses_one_background_for_team_and_reconnect(self):
        self.team()
        zone = next(z for z in DATA['zones'] if z['scene'] == 2011)
        x, y = (zone['bounds'][0] + zone['bounds'][2]) // 40, (zone['bounds'][1] + zone['bounds'][3]) // 40
        for game in self.games:
            game.character.update(map_id=2011, x=x, y=y)
            self.svc.drain(game)
        self.a._encounter_steps = POLICY['encounter_steps'] - 1
        moved(self.a, (2011, x - 1, y), (2011, x, y))
        for game in self.games:
            entry = next(p for p in self.svc.drain(game) if mid(p) == 2171)
            self.assertEqual(MessageReader(entry[3:]).integer(), 2207)
        entry = next(p for p in self.svc.reconnect_packets(self.b) if mid(p) == 2171)
        self.assertEqual(MessageReader(entry[3:]).integer(), 2207)

    def test_scene_error_cannot_lock_the_party_and_failed_refresh_can_retry(self):
        battle = self.battle()
        for game in self.games:self.svc.drain(game)
        with patch.object(self.a, 'restore_scene', side_effect=ValueError('broken scene')), \
                self.assertLogs('native946_party', level='ERROR'):
            leader = self.call(self.a, 2178, 2)
        member = self.svc.drain(self.b)
        for game, replies in zip(self.games, (leader, member)):
            end = replies.index(packet(2146, [], []))
            self.assertEqual(replies[end - 1], packet(2152, ['number'], [game.character['id']]))
            self.assertIsNone(game.battle)
        self.assertFalse(self.svc.battles)
        self.assertEqual(battle.state['phase'], 'finished')
        self.assertTrue(self.a._party_refresh_pending)
        self.assertIn(1590, [mid(p) for p in member])
        later = self.svc.tick(self.a, monotonic() + 2)
        self.assertIn(1590, [mid(p) for p in later])
        self.assertNotIn(2146, [mid(p) for p in later])
        self.assertFalse(self.a._party_refresh_pending)

    def test_wild_exit_shows_both_teammates_in_the_scene(self):
        battle = self.battle()
        for game in self.games:self.svc.drain(game)
        battle.finish(False)
        for game in self.games:
            self.assertEqual(sum(mid(p) == 1590 for p in self.svc.drain(game)), 1)

    def test_battle_successor_stays_in_the_fight_when_map_leader_is_outside(self):
        self.team()
        teammates = []
        for i in (2, 3):
            owner = f'wild{i}'
            LocalAccounts(self.path).register(dict(username=owner, password='fixture-password'))
            game = NativeGameplay(self.characters.create(owner, f'继任队员{i}', 702), self.characters, lambda: ())
            self.call(self.a, 2039, game.character['id'])
            self.call(game, 2038, self.a.character['id'])
            teammates.append(game)
        c, d = teammates
        self.b.character['x'] += 13
        games = self.svc.participants(self.a)
        self.assertEqual(games, [self.a, c, d])
        battle = WildBattle(self.a, games, DATA['zones'][0])
        self.svc.detach(self.a.character['id'])
        self.assertEqual(self.svc.team(c.character['id'])['leader_id'], self.b.character['id'])
        self.assertEqual(battle.leader, c.character['id'])
        self.svc.detach(c.character['id'])
        self.assertEqual(battle.leader, d.character['id'])
        self.assertIn(2146, [mid(p) for p in self.call(d, 2178, 2)])
        self.assertFalse(self.svc.battles)

    def test_late_order_is_ignored_and_round_advances_via_timeout(self):
        battle = self.battle()
        for game in self.games:self.svc.drain(game)
        self.call(self.b, 2178, 1)
        battle.state['deadline'] = monotonic()
        self.assertFalse(self.call(self.a, 2178, 1))
        self.assertNotIn(self.a.character['id'], battle.state['orders'])
        self.svc.tick(self.a)
        self.assertEqual(battle.state['phase'], 'drawing')
        self.assertEqual(battle.state['orders'][self.a.character['id']]['kind'], 'attack')
        self.assertEqual(battle.state['orders'][self.b.character['id']]['kind'], 'defend')

    def test_guardian_escape_uses_the_existing_team_leader_rule(self):
        battle=self.battle(pet=True)
        self.assertIn(98,[mid(p) for p in self.call(self.b,2182,2)])
        self.assertIs(self.svc.battles[self.b.character['id']],battle)
        self.call(self.a,2178,1)
        leader_replies=self.call(self.a,2182,2)
        self.assertIn(2146,[mid(p) for p in leader_replies])
        self.assertIn(2146,[mid(p) for p in self.svc.drain(self.b)])
        for game in self.games:
            self.assertIsNone(game.battle)

    def test_shared_human_control_keeps_the_defence_input_with_the_original_deadline(self):
        battle=self.battle()
        cid=self.a.character['id']
        battle.state['fighters'][cid]['controls']={'faint':1}
        deadline=battle.state['deadline']
        self.assertIn(2151,[mid(p) for p in battle.entry(self.a)])
        self.call(self.b,2178,1)
        self.assertEqual((battle.state['phase'],battle.state['deadline']),('input',deadline))
        self.call(self.a,2178,1)
        self.assertEqual(battle.state['phase'],'drawing')
        self.assertEqual(battle.state['orders'][cid]['kind'],'defend')

    def test_mixed_party_entry_shows_stick_slots_only_for_the_west_school_fighter(self):
        from native946_role_systems import save

        for game, school in ((self.a, 18), (self.b, 719)):
            with game.progression.transaction() as db:
                save(db, game.character['id'], 'school', {'school': school})
        battle = self.battle(pet=True)
        expected = packet(4830, ['number'] * 3, [self.b.character['id'], 0, 2])
        for viewer in self.games:
            self.assertEqual([p for p in battle.entry(viewer) if mid(p) == 4830], [expected])

    def test_native_friends_search_saved_groups_and_presence(self):
        from native946_friends import Friends
        from native946_login import string_field

        owner, target = self.a.character['id'], self.b.character['id']
        self.assertEqual(mid(self.call(self.a, 597, target)[0]), 582)
        self.assertEqual(mid(self.call(self.a, 598, tail=string_field(self.b.character['name']))[0]), 582)
        self.assertEqual(mid(self.call(self.a, 599, owner, 1, 0)[0]), 98)
        created = self.call(self.a, 604, tail=string_field('队友'))[0]
        group = MessageReader(created[3:]).integer()
        self.assertEqual([mid(p) for p in self.call(self.a, 599, target, 1, 0)], [583, 593])
        self.assertEqual(mid(self.call(self.a, 599, target, 1, 0)[0]), 98)
        self.assertEqual(mid(self.call(self.a, 600, target, tail=string_field('队长'))[0]), 590)
        self.assertEqual([mid(p) for p in self.call(self.a, 610, target, 1, group)], [592, 593])
        self.assertEqual(mid(self.call(self.a, 607, group, 1)[0]), 588)

        restored = NativeGameplay(self.a.character, Characters(self.path), lambda: ())
        restored_packets = Friends(restored).initial_packets()
        self.assertEqual([mid(p) for p in restored_packets], [586, 591, 588, 593])
        with restored.progression.transaction(write=False) as db:
            self.assertEqual(tuple(Friends(restored).contact(db, target)), (group, '队长'))
        self.call(self.b, 611, 0)
        self.assertIn(packet(593, ['number', 'number'], [target, 0]), self.svc.drain(restored))
        self.call(self.b, 611, 1)
        self.assertIn(packet(593, ['number', 'number'], [target, 1]), self.svc.drain(restored))
        self.svc.detach(target)
        self.assertIn(packet(593, ['number', 'number'], [target, 0]), self.svc.drain(restored))

        self.assertEqual([mid(p) for p in self.call(restored, 606, group)], [592, 587])
        self.assertEqual(mid(self.call(restored, 603, target)[0]), 585)
        self.assertEqual(Friends(restored).initial_packets(), (packet(591, [('array', 'friend_info')], [[]]),))

    def test_wild_defend_replaces_queued_normal_attack_without_duplicate_input_end(self):
        battle=self.battle();cid=self.a.character['id']
        self.call(self.a,2177,3,0);self.svc.drain(self.a)
        self.call(self.a,2178,1)
        replies=self.svc.drain(self.a)
        self.assertEqual(battle.state['orders'][cid]['kind'],'defend')
        self.assertNotIn(2152,[mid(p) for p in replies])
        self.call(self.b,2178,1)
        self.assertEqual(battle.state['phase'],'drawing')

    def test_shared_hero_protects_guardian_and_rejects_self_and_enemy(self):
        battle=self.battle(party=False,pet=True);cid=self.a.character['id']
        pet=battle.current_pet(cid);hero=battle.state['fighters'][cid]
        enemy=battle.enemies()[0]
        for other in battle.enemies()[1:]:other['removed']=True
        enemy.update(slot=2,attack=pet['defense']+40,AttValue=100)
        before=(hero['hp'],pet['hp'])
        for invalid in (cid,enemy['id']):
            self.assertIn(98,[mid(p) for p in self.call(self.a,2177,4,invalid)])
            self.assertFalse(battle.state['orders'])
        self.call(self.a,2177,4,pet['id'])
        self.assertEqual(battle.state['orders'][cid],dict(kind='protect',target=pet['id']))
        replies=(*self.call(self.a,2182,1),*self.svc.drain(self.a))
        self.assertIn(packet(2159,['number','number'],[pet['id'],cid]),replies)
        self.assertEqual((before[0]-hero['hp'],before[1]-pet['hp']),(10,10))

    def test_f5_shared_battle_displays_each_members_own_auto_fight_time(self):
        from native946_mall_uses import MallUses
        with self.a.progression.transaction() as db:
            uses=MallUses(self.a)
            uses.grant(db,'auto_advanced',55577,7)
            uses.grant(db,'auto_infinite',55579,1)
        self.battle()
        for game,days in ((self.a,7),(self.b,0)):
            reply=next(p for p in self.call(game,2757) if mid(p)==2756)
            reader=MessageReader(reply[3:]);self.assertEqual(reader.integer(),days)
            for _ in range(2):
                for _ in range(reader.take(1)[0]):
                    reader.string();reader.string();reader.integer()
            remaining=reader.integer();reader.finish()
            if days:
                self.assertGreater(remaining,86390)
            else:
                self.assertEqual(remaining,0)

    def test_create_invite_requires_recipient_consent(self):
        self.assertIn(text_notice('已发送组队邀请（1人）。'),self.call(self.a,2039,self.b.character['id']))
        self.assertIsNone(self.svc.team(self.b.character['id']))
        self.assertIn(packet(2020,['number','string'],[self.a.character['id'],self.a.character['name']]),self.svc.drain(self.b))
        self.assertIn(text_notice('已加入队伍。'),self.call(self.b,2038,self.a.character['id']))
        self.assertIn(text_notice(f"{self.b.character['name']}加入了队伍。"),self.svc.drain(self.a))
        team=self.svc.team(self.b.character['id'])
        self.assertEqual([m['away'] for m in team['members']],[0,0])
        self.assertIn(3228,[mid(p) for p in self.call(self.b,2054)])

    def test_guardian_input_is_followed_by_current_round_to_open_native_menu(self):
        battle=self.battle(party=False,pet=True)
        pet=battle.current_pet(self.a.character['id'])
        replies=(*self.call(self.a,2177,3,0),*self.svc.drain(self.a))
        ids=[mid(reply) for reply in replies]
        input_index=ids.index(2151)
        self.assertEqual(MessageReader(replies[input_index][3:]).integer(),pet['id'])
        self.assertEqual(ids[input_index+1:input_index+3],[2703,2139])
        self.assertEqual(MessageReader(replies[input_index+2][3:]).integer(),2)

    def test_rejected_guardian_spell_reopens_only_its_pending_input(self):
        from time import monotonic
        battle=self.battle(pet=True);cid=self.a.character['id']
        pet=battle.current_pet(cid);enemy=battle.enemies()[0]
        pet.update(mp=37,learned_books=[])
        self.call(self.a,2177,3,enemy['id'])
        self.svc.drain(self.b)
        deadline=battle.state['deadline']=monotonic()+12
        orders=deepcopy(battle.state['orders'])
        replies=self.call(self.a,2183,9,338,enemy['id'])
        self.assertEqual([mid(p) for p in replies],[98,5349,2151,2703,2139])
        self.assertEqual(MessageReader(replies[0][1:]).string().decode('gb18030'),'守护尚未学会此主动特技。')
        reader=MessageReader(replies[2][3:])
        self.assertEqual(reader.integer(),pet['id'])
        self.assertIn(reader.integer(),range(1,13))
        self.assertFalse(self.svc.drain(self.b))
        self.assertEqual((battle.state['orders'],battle.state['deadline'],pet['mp']),(orders,deadline,37))
        self.call(self.a,2182,1)
        self.assertEqual(battle.state['orders'][pet['id']]['kind'],'defend')

    def test_rejected_dead_target_reopens_hero_until_original_deadline(self):
        battle=self.battle();dead,alive=battle.enemies()[:2]
        dead['hp']=0;cid=self.a.character['id']
        self.svc.drain(self.a)
        replies=self.call(self.a,2177,3,dead['id'])
        self.assertEqual([mid(p) for p in replies],[98,5349,2151,2703,2139])
        self.assertEqual(MessageReader(replies[2][3:]).integer(),cid)
        self.assertFalse(battle.state['orders'])
        battle.state['deadline']=monotonic()-1
        self.assertFalse(self.call(self.a,2177,3,dead['id']))
        self.assertFalse(battle.state['orders'])
        self.assertIn(alive,battle.enemies())

    def test_low_mana_attacks_keep_the_selected_enemy_and_spend_no_mana(self):
        battle=self.battle(party=True,pet=True);cid=self.a.character['id']
        hero=battle.state['fighters'][cid];pet=battle.current_pet(cid)
        enemy=battle.enemies()[1]
        enemy.update(hp=10000,hpmax=10000)
        hero['mp']=1;pet.update(mp=37,innate_skills=[dict(stunt_id=4906,level=1)])
        self.call(self.a,2185,9,6100,enemy['id'])
        self.call(self.a,2183,9,4906,enemy['id'])
        for fid in (cid,pet['id']):
            self.assertEqual(battle.state['orders'][fid],dict(kind='attack',target=enemy['id'],skill=10,mana=0))
        self.call(self.b,2178,1);self.call(self.b,2182,1)
        attacks=[]
        for reply in self.svc.drain(self.a):
            if mid(reply)!=2157:continue
            reader=MessageReader(reply[3:]);attacker=reader.integer();target=reader.integer()
            if attacker in (cid,pet['id']):attacks.append((attacker,target))
        self.assertEqual(set(attacks),{(cid,enemy['id']),(pet['id'],enemy['id'])})
        self.assertEqual((hero['mp'],pet['mp']),(1,37))

    def test_shared_spells_recover_stale_enemy_slots_without_losing_the_orders(self):
        battle=self.battle(party=True,pet=True);cid=self.a.character['id']
        defeated,live=battle.enemies()[:2]
        defeated['hp']=0
        pet=battle.current_pet(cid)
        pet.update(mp=100,innate_skills=[dict(stunt_id=4906,level=1)])
        self.svc.drain(self.a)
        rejected=self.call(self.a,2185,9,6100,990001)
        self.assertIn(98,[mid(p) for p in rejected])
        self.assertNotIn(cid,battle.state['orders'])
        self.assertNotIn(98,[mid(p) for p in self.call(self.a,2185,9,6100,901005)])
        self.assertNotIn(98,[mid(p) for p in self.call(self.a,2183,9,4906,defeated['id'])])
        self.assertEqual((battle.state['orders'][cid]['skill_id'],battle.state['orders'][cid]['target']),
                         (6100,live['id']))
        self.assertEqual((battle.state['orders'][pet['id']]['guardian_skill'],battle.state['orders'][pet['id']]['target']),
                         (4906,live['id']))
        self.call(self.b,2178,1)
        replies=self.call(self.b,2182,1)
        self.assertEqual(mid(replies[-1]),2167)
        self.assertEqual(battle.state['phase'],'drawing')

    def test_mana_lost_after_submission_can_still_use_normal_attack(self):
        battle=self.battle(party=True,pet=True);cid=self.a.character['id']
        hero=battle.state['fighters'][cid];pet=battle.current_pet(cid)
        enemy=battle.enemies()[1]
        enemy.update(hp=10000,hpmax=10000)
        pet.update(mp=100,innate_skills=[dict(stunt_id=4906,level=1)])
        self.call(self.a,2185,9,6100,enemy['id'])
        self.call(self.a,2183,9,4906,enemy['id'])
        self.assertEqual(battle.state['orders'][cid]['skill_id'],6100)
        self.assertEqual(battle.state['orders'][pet['id']]['guardian_skill'],4906)
        hero['mp']=0;pet['mp']=0
        self.call(self.b,2178,1);self.call(self.b,2182,1)
        replies=self.svc.drain(self.a)
        hints=[MessageReader(p[1:]).string().decode('gb18030') for p in replies if mid(p)==98]
        self.assertCountEqual(hints,['人物法力不足，本次改用普通攻击。','守护法力不足，本次改用普通攻击。'])
        attacks=[]
        for reply in replies:
            if mid(reply)!=2157:continue
            reader=MessageReader(reply[3:]);attacker=reader.integer();target=reader.integer()
            if attacker in (cid,pet['id']):attacks.append((attacker,target))
        self.assertEqual(set(attacks),{(cid,enemy['id']),(pet['id'],enemy['id'])})
        self.assertEqual((hero['mp'],pet['mp']),(0,0))

    def test_drawing_wakes_processor_and_waits_for_round_end(self):
        battle=self.battle(party=False,pet=True)
        replies=(*self.call(self.a,2177,3,0),*self.svc.drain(self.a),
                 *self.call(self.a,2181,3,0),*self.svc.drain(self.a))
        ids=[mid(reply) for reply in replies]
        self.assertIn(2167,ids)
        self.assertEqual(ids[-1],2167)
        self.assertEqual(battle.state['phase'],'drawing')
        self.assertGreater(battle.state['deadline'] - monotonic(), 25)
        battle.tick(battle.state['deadline'] - 1)
        self.assertEqual((battle.state['phase'], battle.state['round']), ('drawing', 1))

    def test_seven_monster_round_waits_for_playback_and_two_survivors_prevent_victory(self):
        from native946_role_battle import nums

        self.team()
        for game in self.games:
            self.buff(game)
        self.a.summons.claim_starter()
        zone = zone_at(2269, 38, 48)
        zone = dict(zone, formations=(), fixed_roster=True, enemies=[zone['enemies'][0]] * 7)
        battle = WildBattle(self.a, self.games, zone)
        first_five = battle.enemies()[:5]
        survivors = battle.enemies()[5:]
        for enemy in first_five:
            enemy['hp'] = 1
        pet = battle.current_pet(self.a.character['id'])
        pet.update(initiative=1000, mp=100, innate_skills=[dict(stunt_id=4906, level=1)])
        self.call(self.a, 2178, 1)
        self.call(self.b, 2178, 1)
        self.call(self.a, 2183, 9, 4906, first_five[0]['id'])
        self.assertEqual([enemy['id'] for enemy in battle.enemies()], [enemy['id'] for enemy in survivors])
        self.assertFalse(battle.state['victory'])
        self.assertFalse(battle.state['settled'])
        self.call(self.a, 2184)
        battle.tick(battle.state['deadline'] - 1)
        self.assertEqual((battle.state['phase'], battle.state['round']), ('drawing', 1))
        self.call(self.b, 2184)
        self.assertEqual((battle.state['phase'], battle.state['round']), ('input', 2))
        for enemy in survivors:
            enemy['hp'] = 1
        for game in self.games:
            self.svc.drain(game)
        self.call(self.a, 2176, survivors[0]['id'])
        self.call(self.a, 2182, 1)
        with patch('native946_book_combat.roll', return_value={'miss': False, 'damage': 500, 'effect': 0}):
            self.call(self.b, 2176, survivors[1]['id'])
        replies = self.svc.drain(self.a)
        for enemy in survivors:
            self.assertLess(replies.index(nums(2142, enemy['id'], 3)), replies.index(nums(2167)))
        self.assertTrue(battle.state['victory'])
        self.assertEqual(battle.state['rewards'][self.a.character['id']]['kills'], sorted(f['id'] for f in first_five + survivors))
        self.call(self.a, 2184)
        battle.tick(battle.state['deadline'] - 1)
        self.assertEqual(battle.state['phase'], 'drawing')
        self.assertNotIn(2146, [mid(reply) for reply in self.svc.drain(self.b)])
        self.assertIn(2146, [mid(reply) for reply in self.call(self.b, 2184)])
        self.assertEqual(battle.state['phase'], 'finished')
        self.assertIn(2146, [mid(reply) for reply in self.svc.drain(self.a)])
        battle.tick(monotonic() + 600)
        self.assertFalse(self.svc.drain(self.a))

    def test_drawing_unblocks_after_unconfirmed_peer_disconnects(self):
        battle = self.battle()
        self.call(self.a, 2178, 1)
        self.call(self.b, 2178, 1)
        self.call(self.a, 2184)
        self.svc.detach(self.b.character['id'])
        self.svc.tick(self.a)
        self.assertEqual((battle.state['phase'], battle.state['round']), ('input', 2))

    def test_drawing_timeout_opens_next_round_without_accepting_stale_confirmation(self):
        battle = self.battle()
        for game in self.games:
            self.call(game, 2178, 1)
        self.call(self.a, 2184)
        battle.tick(battle.state['deadline'])
        self.assertEqual((battle.state['phase'], battle.state['round']), ('input', 2))
        b_id = self.b.character['id']
        self.assertEqual(battle.state['late_round_ends'], {b_id: 1})

        enemy = battle.enemies()[0]
        enemy.update(hp=100000, hpmax=100000)
        self.call(self.a, 2185, 9, 6100, enemy['id'])
        battle.tick(battle.state['deadline'])
        self.assertEqual(battle.state['orders'][self.a.character['id']]['skill_id'], 6100)
        self.assertEqual(battle.state['orders'][b_id]['skill'], 10)
        self.call(self.a, 2184)
        self.call(self.b, 2184)
        self.assertEqual((battle.state['phase'], battle.state['round']), ('drawing', 2))
        self.call(self.b, 2184)
        self.assertEqual((battle.state['phase'], battle.state['round']), ('input', 3))

    def test_each_round_ends_each_actor_input_once_per_native_connection(self):
        battle=self.battle(party=True,pet=True)
        for g in self.games:self.svc.drain(g)
        seen={g.character['id']:[] for g in self.games}
        for actor in list(battle.allies()):
            replies=self.call(battle.games[actor['owner']],2181 if actor['kind']=='pet' else 2177,3,0)
            for reply in replies:
                if mid(reply)==2152:seen[actor['owner']].append(MessageReader(reply[3:]).integer())
            for g in self.games:
                for reply in self.svc.drain(g):
                    if mid(reply)==2152:seen[g.character['id']].append(MessageReader(reply[3:]).integer())
        for ids in seen.values():
            self.assertEqual(len(ids),len(set(ids)))
            self.assertIn(self.a.character['id'],ids)

    def test_revert_reopens_owned_inputs_without_changing_peers_or_deadline(self):
        battle=self.battle(party=True,pet=True)
        aid,bid=self.a.character['id'],self.b.character['id']
        pet=battle.current_pet(aid)
        for game in self.games:self.svc.drain(game)
        self.call(self.a,2177,3,0)
        self.call(self.a,2181,3,0)
        self.call(self.b,2177,3,0)
        deadline=battle.state['deadline']
        peer=deepcopy(battle.state['orders'][bid])
        before=self.a.progression.snapshot()
        replies=self.call(self.a,3819)
        self.assertNotIn(aid,battle.state['orders'])
        self.assertNotIn(pet['id'],battle.state['orders'])
        self.assertEqual(battle.state['orders'][bid],peer)
        self.assertEqual(battle.state['deadline'],deadline)
        self.assertEqual(battle.state['input_ended'][aid],set())
        self.assertIn(3818,[mid(p) for p in replies])
        self.assertIn(2151,[mid(p) for p in replies])
        self.assertEqual([mid(p) for p in replies[-3:]],[2151,2703,2139])
        self.assertEqual(before,self.a.progression.snapshot())
        self.call(self.a,2177,3,0)
        self.assertIn(aid,battle.state['orders'])

    def test_revert_rejects_trailing_payload_and_does_not_reopen_drawing(self):
        battle=self.battle(party=True)
        aid=self.a.character['id']
        self.call(self.a,2177,3,0)
        before=deepcopy(battle.state)
        self.assertIn(98,[mid(p) for p in self.call(self.a,3819,1)])
        self.assertEqual(before,battle.state)
        self.call(self.b,2177,3,0)
        self.assertEqual(battle.state['phase'],'drawing')
        before=deepcopy(battle.state)
        replies=self.call(self.a,3819)
        self.assertNotIn(2151,[mid(p) for p in replies])
        self.assertEqual(before,battle.state)

    def test_revert_does_not_extend_expired_input(self):
        battle=self.battle(party=True)
        self.call(self.a,2177,3,0)
        before=deepcopy(battle.state)
        with patch('native946_wilderness.monotonic',return_value=battle.state['deadline']):
            replies=self.call(self.a,3819)
        self.assertNotIn(2151,[mid(p) for p in replies])
        self.assertEqual(before,battle.state)

    def test_revert_releases_medicine_reservation_without_consuming_inventory(self):
        battle=self.battle(party=True,pet=True)
        cid=self.a.character['id'];pet=battle.current_pet(cid)
        mine=next(i['id'] for i in self.a.progression.snapshot()['items'] if i['type']==4000)
        with self.a.progression.transaction() as db:
            db.execute('UPDATE inventory SET amount=1 WHERE id=?',(mine,))
        battle.state['fighters'][cid]['hp']-=100
        before=self.a.progression.snapshot()
        self.call(self.a,2185,8,mine,cid)
        self.assertEqual(battle.state['orders'][cid]['kind'],'medicine')
        self.call(self.a,3819)
        replies=self.call(self.a,2183,8,mine,cid)
        self.assertNotIn(888,[mid(p) for p in replies])
        self.assertEqual(battle.state['orders'][pet['id']]['kind'],'medicine')
        self.assertEqual(before,self.a.progression.snapshot())

    def test_mid_round_summon_is_ready_before_enemy_attack(self):
        battle=self.battle(party=True);cid=self.a.character['id']
        sid=self.a.summons.claim_starter()
        battle.state['fighters'][cid]['initiative']=100000
        for enemy in battle.enemies():enemy.update(initiative=0,slot=3,attack=1)
        self.call(self.a,2177,6,sid);self.call(self.b,2178,1)
        self.assertEqual(battle.state['phase'],'drawing')
        pet=battle.current_pet(cid)
        self.assertEqual(pet['id'],sid)
        self.assertIn('role_shields',pet);self.assertIn('role_cooldowns',pet)

    def test_forged_accept_and_expired_invite_do_not_join(self):
        self.call(self.a,2042,self.a.character['id'])
        self.assertIn(98,[mid(p) for p in self.call(self.b,2038,self.a.character['id'])])
        self.call(self.a,2039,self.b.character['id'])
        self.svc.invitations[(self.a.character['id'],self.b.character['id'])]=0
        self.call(self.b,2038,self.a.character['id'])
        self.assertIsNone(self.svc.team(self.b.character['id']))

    def test_application_approval_and_duplicate_delivery(self):
        leader_id=self.a.character['id'];first_id=self.b.character['id']
        owner='wild2';LocalAccounts(self.path).register(dict(username=owner,password='fixture-password'))
        third=NativeGameplay(self.characters.create(owner,'后续申请人',702),self.characters,lambda:())
        second_id=third.character['id']
        self.call(self.a,2042,leader_id)
        self.assertIn(text_notice(f"已向{self.a.character['name']}发送入队申请。"),self.call(self.b,2042,leader_id))
        alert=self.svc.drain(self.a)
        self.assertEqual(alert,(packet(2023,[],[]),text_notice(f"{self.b.character['name']}申请加入你的队伍。")))
        self.assertIn(text_notice('你已提交入队申请，请等待队长处理。'),self.call(self.b,2042,leader_id))
        self.assertEqual(self.svc.drain(self.a),())
        self.call(third,2042,leader_id)
        self.assertEqual(self.call(self.a,2043)[-1][3],2)
        approval=self.call(self.a,2041,first_id)
        self.assertIn(2026,[mid(p) for p in approval])
        self.assertIn(text_notice(f"{self.a.character['name']}同意了你的入队申请。"),self.svc.drain(self.b))
        remaining=self.call(self.a,2043)[-1]
        self.assertEqual(remaining[3],1)
        self.assertIn(third.character['name'].encode('gb18030'),remaining)
        self.assertEqual(approval[-1],remaining)
        self.assertEqual(self.call(self.a,2041,first_id),(remaining,))
        self.assertEqual(self.call(self.a,2041,second_id)[-1],packet(2022,[('array','team_player_info'),('array','byte')],[[],[]]))
        self.assertEqual([m['character_id'] for m in self.svc.team(leader_id)['members']],[leader_id,first_id,second_id])

    def test_reject_clear_and_expired_application_refresh_the_native_list(self):
        leader_id=self.a.character['id'];applicant_id=self.b.character['id']
        empty=packet(2022,[('array','team_player_info'),('array','byte')],[[],[]])
        self.call(self.a,2042,leader_id)
        for action,args in ((2047,(applicant_id,)),(2048,())):
            self.call(self.b,2042,leader_id)
            self.assertEqual(self.call(self.a,2043)[-1][3],1)
            replies=self.call(self.a,action,*args)
            self.assertEqual([p for p in replies if mid(p)==2022],[empty])
            self.assertIn(text_notice('已拒绝1条入队申请。'),replies)
            self.assertIn(text_notice(f"{self.a.character['name']}拒绝了你的入队申请。"),self.svc.drain(self.b))
        self.call(self.b,2042,leader_id)
        self.assertEqual(self.call(self.a,2043)[-1][3],1)
        self.svc.applications[leader_id,applicant_id]=0
        expired=self.call(self.a,2041,applicant_id)
        self.assertEqual(expired[0],empty)
        self.assertEqual([mid(p) for p in expired],[2022,98])
        self.assertIsNone(self.svc.team(applicant_id))

    def test_application_to_member_goes_to_leader_and_dismissal_retires_it(self):
        self.team()
        self.svc.drain(self.a)
        self.svc.drain(self.b)
        owner='wild2';LocalAccounts(self.path).register(dict(username=owner,password='fixture-password'))
        third=NativeGameplay(self.characters.create(owner,'其他申请人',702),self.characters,lambda:())
        leader,member=self.a.character['id'],self.b.character['id']
        self.call(third,2042,member)
        self.assertIn(packet(2023,[],[]),self.svc.drain(self.a))
        self.assertEqual(self.svc.drain(self.b),())
        self.assertIn(text_notice('队伍已解散。'),self.call(self.a,2049))
        self.assertIn(text_notice('队伍已解散。'),self.svc.drain(self.b))
        self.call(self.a,2042,leader)
        self.assertEqual(self.call(self.a,2043)[-1][3],0)

    def test_invalid_trailing_field_never_creates_team(self):
        self.call(self.a,2042,self.a.character['id'],tail=b'\0')
        self.assertIsNone(self.svc.team(self.a.character['id']))

    def test_nonleader_cannot_dismiss_or_invite(self):
        self.team();before=self.svc.team(self.a.character['id'])
        self.call(self.b,2049);self.call(self.b,2039,123456)
        self.assertEqual(self.svc.team(self.a.character['id']),before)

    def test_away_back_leader_transfer_and_leave(self):
        self.team();self.call(self.b,2044)
        self.assertEqual([g.character['id'] for g in self.svc.participants(self.a)],[self.a.character['id']])
        self.call(self.b,2045);self.call(self.a,2046,self.b.character['id'])
        self.assertEqual(self.svc.team(self.a.character['id'])['leader_id'],self.b.character['id'])
        self.call(self.b,2051)
        self.assertEqual(self.svc.team(self.a.character['id'])['leader_id'],self.a.character['id'])
        self.assertIsNone(self.svc.team(self.b.character['id']))

    def test_member_appearance_and_direct_delegation_use_native_packets(self):
        from native946_lua_values import table
        self.team()
        target=self.b.character['id']
        reply=next(p for p in self.call(self.a,4506,target) if mid(p)==4505)
        reader=MessageReader(reply[3:]);records=table(reader.string().decode('gb18030'));reader.finish()
        self.assertEqual(len(records),2)
        self.assertEqual({row['id'] for row in records.values()},{self.a.character['id'],target})
        self.assertTrue(all('hat_id' in row and 'suit_id' in row and 'weapon_idx' in row for row in records.values()))

        response=self.a.receive(4020,MessageReader(number(target)+b'\x01'))
        self.assertEqual(self.svc.team(self.a.character['id'])['direct_ids'],[target])
        direct=next(p for p in (*response,*self.svc.drain(self.a)) if mid(p)==4019)
        reader=MessageReader(direct[3:])
        self.assertEqual([reader.integer() for _ in range(reader.take(1)[0])],[target]);reader.finish()
        self.b.receive(4020,MessageReader(number(target)+b'\x00'))
        self.assertEqual(self.svc.team(self.a.character['id'])['direct_ids'],[target])
        from native946_party import PartyService
        self.assertEqual(PartyService(self.path).team(self.a.character['id'])['direct_ids'],[target])
        self.a.receive(4020,MessageReader(number(target)+b'\x00'))
        self.assertEqual(self.svc.team(self.a.character['id'])['direct_ids'],[])

    def test_leader_vote_uses_candidate_notification_and_unanimous_result(self):
        self.team()
        candidate=self.b.character['id']
        self.call(self.b,3578)
        notice=next(p for p in self.svc.drain(self.a) if mid(p)==3577)
        self.assertEqual(MessageReader(notice[3:]).string().decode('gb18030'),self.b.character['name'])
        response=self.call(self.a,3579,1)
        self.assertEqual(self.svc.team(self.a.character['id'])['leader_id'],candidate)
        self.assertIn(3580,[mid(p) for p in (*response,*self.svc.drain(self.a))])
        self.call(self.a,3578)
        self.svc.drain(self.b)
        self.call(self.b,3579,0)
        self.assertEqual(self.svc.team(self.a.character['id'])['leader_id'],candidate)
        self.assertFalse(self.svc.leader_votes)

    def test_membership_survives_service_reconstruction(self):
        self.team()
        from native946_party import PartyService
        restored=PartyService(self.path)
        self.assertEqual(restored.team(self.a.character['id']),self.svc.team(self.a.character['id']))

    def test_normal_gameplay_has_no_trainer_or_practice_quest(self):
        self.assertIsNone(self.a.trainer_packet())
        self.assertEqual(self.call(self.a,1034,TRAINER_ID),())
        self.assertIsNone(self.a.receive(1032,MessageReader(b'\x01'+number(TRAINER_ID))))
        self.a.progression.accept_quest()
        self.assertFalse(self.a.progression.task_records()[1]['其他'])
        self.assertEqual(self.a.summons.snapshot(),[])

    def test_each_open_species_uses_native_growth_and_owner_storage(self):
        self.buff(self.a)
        with self.a.progression.transaction() as db:
            db.execute('UPDATE cultivation_state SET rebirth=2 WHERE character_id=?',(self.a.character['id'],))
        categories=set()
        for key,native in DATA['species'].items():
            sid=self.a.summons.acquire(int(key),'catalog:'+key)
            pet=self.a.summons.get(sid);categories.add(native['kind'])
            self.assertGreater(pet['hpmax'],0,native['name']);self.assertEqual(pet['name'],native['name'])
            self.assertIn(pet['direction'],native['direction'])
            with self.a.progression.transaction() as db:db.execute('DELETE FROM summons WHERE id=?',(sid,))
        self.assertEqual(len(categories),12)

    def test_species_acquisition_replay_capacity_and_carry_grade(self):
        sid=self.a.summons.acquire(3197,'acquisition:test')
        self.assertEqual(self.a.summons.acquire(3197,'acquisition:test'),sid)
        gated=next(int(k) for k,r in DATA['species'].items() if r['take_grade_num']>1)
        with self.assertRaises(ValueError):self.a.summons.acquire(gated,'not-eligible')
        for i in range(5):self.a.summons.acquire(3197,'extra:'+str(i))
        with self.assertRaises(ValueError):self.a.summons.acquire(3197,'overflow')
        with self.assertRaises(ValueError):self.b.summons.get(sid)

    def test_shared_combat_waits_for_both_players(self):
        battle=self.battle()
        hp=[e['hp'] for e in battle.enemies()]
        self.call(self.a,2177,3,0)
        self.assertEqual([e['hp'] for e in battle.enemies()],hp)
        self.assertEqual(battle.state['phase'],'input')
        self.call(self.b,2177,3,0)
        self.assertEqual(battle.state['phase'],'drawing')

    def test_both_players_and_guardians_receive_one_durable_reward(self):
        battle=self.battle(pet=True)
        cash_before = {g.progression.cid: g.progression.snapshot()['gold'] for g in self.games}
        self.fight(battle)
        for g in self.games:
            s=g.progression.snapshot();self.assertGreater(s['experience'],0)
            self.assertEqual(s['gold'], cash_before[g.progression.cid])
            self.assertEqual(battle.state['rewards'][g.progression.cid]['gold'], 0)
            self.assertGreater(g.summons.snapshot()[0]['experience'],0)
            with g.progression.transaction(write=False) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM native_encounter_rewards WHERE battle_id=? AND character_id=?',(battle.id,g.character['id'])).fetchone()[0],1)
            previous=(s['experience'],s['gold']);self.call(g,2184);self.call(g,2177,3,0)
            s=g.progression.snapshot();self.assertEqual((s['experience'],s['gold']),previous)

    def test_double_time_affects_only_its_owner_and_guardian(self):
        from time import time
        from native946_double_time import HOUR, load, save

        battle = self.battle(pet=True)
        cid, other = self.a.progression.cid, self.b.progression.cid
        now = int(time())
        with self.a.progression.transaction() as db:
            value = load(db, cid, now)
            value['expires'] = now + HOUR
            save(db, cid, value)
        self.fight(battle)
        first, second = (battle.state['rewards'][c] for c in (cid, other))
        self.assertGreater(second['experience'], 0)
        self.assertEqual(first['experience'], 2 * second['experience'])
        self.assertEqual(first['guardian_experience'], 2 * second['guardian_experience'])
        with self.a.progression.transaction(write=False) as db:
            paid = db.execute('SELECT experience FROM battle_results WHERE battle_id=?',
                              ('wild:'+battle.id+':'+str(cid),)).fetchone()[0]
        self.assertEqual(paid, first['experience'])

    def test_killed_enemy_reward_cannot_advance_training_quest(self):
        self.a.progression.accept_quest();battle=self.battle(party=False)
        self.fight(battle)
        self.assertEqual(self.a.progression.snapshot()['quest']['state'],'active')

    def test_capture_is_exclusive_and_captured_units_award_no_kill_experience(self):
        battle=self.battle();battle.rng=Random(1)
        enemies=battle.enemies()
        for enemy in enemies[1:]:enemy['removed']=True;enemy['captured']=True
        target=enemies[0]['id']
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET level=31 WHERE id=?',(self.a.progression.cid,))
        enemies[0]['level']=42
        self.svc.drain(self.a)
        replies=self.call(self.a,2177,5,target)
        notices=[MessageReader(p[1:]).string().decode('gb18030') for p in replies if mid(p)==98]
        self.assertEqual(notices,['该守护等级超过当前可捕捉的上限。'])
        self.assertEqual(self.a.summons.snapshot(),[])
        enemies[0]['level']=41
        self.call(self.a,2177,5,target);self.call(self.b,2177,5,target)
        self.assertEqual(sum(len(g.summons.snapshot()) for g in self.games),1)
        self.assertTrue(battle.state['victory'])
        for g in self.games:self.call(g,2184)
        self.assertEqual(sum(g.progression.snapshot()['experience'] for g in self.games),0)

    def test_failed_resolution_rolls_back_every_player_and_outgoing_rewards(self):
        battle=self.battle()
        for enemy in battle.enemies()[1:]:enemy['removed']=True;enemy['captured']=True
        battle.enemies()[0]['hp']=1
        self.call(self.a,2177,3,0)
        before=deepcopy(battle.state);self.svc.drain(self.a);self.svc.drain(self.b)
        with patch.object(self.characters,'settle_battle',side_effect=RuntimeError('simulated failed commit path')):
            with self.assertRaises(RuntimeError):self.call(self.b,2177,3,0)
        self.assertEqual(battle.state,before)
        self.assertFalse(self.a._party_inbox);self.assertFalse(self.b._party_inbox)
        for g in self.games:self.assertEqual(g.progression.snapshot()['experience'],0)
        self.call(self.b,2177,3,0);self.fight(battle)
        self.assertGreater(self.a.progression.snapshot()['experience'],0)

    def test_escape_gives_no_reward_and_preserves_damage(self):
        battle=self.battle();hero=battle.state['fighters'][self.a.character['id']];hero['hp']-=7
        self.call(self.a,2178,2)
        self.assertIsNone(self.a.battle);self.assertIsNone(self.b.battle)
        self.assertEqual(self.a.progression.snapshot()['hp'],hero['hp'])
        self.assertEqual(self.a.progression.snapshot()['experience'],0)

    def fill_bag(self, game):
        with game.progression.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=?', (game.progression.cid,))
            db.executemany('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,5000,8,?,1)',
                           [(game.progression.cid, slot) for slot in range(1,25)])

    def test_wild_reward_with_full_bag_uses_native_temp_page_and_right_click(self):
        game = self.a
        p = game.progression
        self.fill_bag(game)
        battle = self.battle(party=False)
        for enemy in battle.enemies():
            enemy['hp'] = 0
        with patch.dict(POLICY, {'drops': [dict(type=4000, amount=2, weight=POLICY['drop_roll_denominator'])]}):
            with battle.atomic() as db:
                battle.settle(db, True)
                battle.settle(db, True)
        battle.finish(True)
        replies = self.svc.drain(game)
        texts = [MessageReader(reply[1:]).string().decode('gb18030')
                 for reply in replies if mid(reply) == 100]
        reward = battle.state['rewards'][game.character['id']]
        self.assertEqual(texts, [f'你得到了{reward["experience"]}点经验'])
        self.assertNotIn(783, [mid(reply) for reply in replies])
        with p.transaction(write=False) as db:
            temp, = temporary_items(db, p.cid)
        items = []
        for reply in replies:
            if mid(reply) == 786:
                reader = MessageReader(reply[3:])
                reader.take(1)  # can_trade
                items.append(tuple(reader.integer() for _ in range(5)))
        self.assertIn((4000, 0, temp['id'], TEMP_PAGE, 1), items)
        self.assertFalse(any(i['id']==temp['id'] for i in p.snapshot()['items']))
        self.call(game,810,TEMP_PAGE,1)
        with p.transaction() as db:
            self.assertEqual(temporary_items(db,p.cid)[0]['id'],temp['id'])
            db.execute('DELETE FROM inventory WHERE character_id=? AND page=8 AND slot=24',(p.cid,))
        self.assertIn(780,[mid(reply) for reply in self.call(game,810,TEMP_PAGE,1)])
        self.call(game,810,TEMP_PAGE,1)
        moved = next(i for i in p.snapshot()['items'] if i['id']==temp['id'])
        self.assertEqual((moved['page'],moved['slot'],moved['amount']),(8,24,temp['amount']))

    def test_wild_victory_shows_separate_notices_and_flies_delivered_items(self):
        game = self.a
        game.progression.item_packets()
        battle = self.battle(party=False)
        enemies = battle.enemies()
        for enemy in enemies:
            enemy['hp'] = 0
        with patch.dict(POLICY, {'drops': [dict(type=4000, amount=2, weight=POLICY['drop_roll_denominator'])]}):
            with battle.atomic() as db:
                battle.settle(db, True)
        battle.finish(True)
        replies = self.svc.drain(game)
        ids = [mid(reply) for reply in replies]
        self.assertLess(ids.index(2146), ids.index(1590))
        self.assertLess(ids.index(1590), ids.index(100))
        self.assertNotIn(98, ids)
        texts = []
        for reply in replies:
            if mid(reply) != 100:
                continue
            reader = MessageReader(reply[1:])
            texts.append(reader.string().decode('gb18030'))
            self.assertEqual(reader.integer(), 0)
            reader.finish()
        reward = battle.state['rewards'][game.character['id']]
        self.assertEqual(texts, [f'你得到了{reward["experience"]}点经验',
                                 f'你获得了#R回生丹*{2 * len(enemies)}#n'])
        animation, = (reply for reply in replies if mid(reply) == 783)
        reader = MessageReader(animation[3:])
        self.assertEqual((reader.integer(), reader.integer()), (4000, 2 * len(enemies)))
        reader.finish()
        marker, = (reply for reply in replies if mid(reply) == 790)
        item, = (i for i in game.progression.snapshot()['items'] if i['type'] == 4000)
        self.assertEqual(marker, packet(790, ['number', 'number'], [item['id'], 1]))
        self.assertLess(ids.index(789), ids.index(790))
        battle.finish(True)
        self.assertFalse(self.svc.drain(game))

    def test_wild_pending_reward_is_reported_as_local_system_feedback(self):
        battle = self.battle(party=False)
        cid = self.a.character['id']
        battle.state['rewards'] = {cid: {'loot_delivery': {'waiting': 2}}}
        reply, = battle.finish_messages(cid, True)
        self.assertEqual(reply, text_notice('摘星袋已满，剩余奖励已保留。'))

    def test_wild_experience_notice_names_guardian_and_uses_observed_scene_style(self):
        game = self.a
        battle = self.battle(party=False, pet=True)
        cid = game.character['id']
        guardian = next(iter(battle.state['participants'][cid].values()))['name']
        battle.state['rewards'] = {cid: {'experience': 180, 'guardian_experience': 225}}
        for scene, expected in (
            (2008, f'你得到了180点经验#r你的守护{guardian}得到了225经验'),
            (2024, f'您获得#G180#n经验#r您的#G{guardian}#n获得#G225#n经验'),
        ):
            with self.subTest(scene=scene):
                game.character['map_id'] = scene
                payload, = battle.finish_messages(cid, True)
                self.assertEqual(mid(payload), 100)
                reader = MessageReader(payload[1:])
                self.assertEqual(reader.string().decode('gb18030'), expected)
                self.assertEqual(reader.integer(), 0)
                reader.finish()

    def test_legacy_pending_rolls_into_available_slots_and_survives_relogin(self):
        game = self.a
        p = game.progression
        self.fill_bag(game)
        with p.transaction() as db:
            db.execute('INSERT INTO native_pending_loot VALUES(?,?,4001,5001)',('old-reward',p.cid))
        restored = NativeGameplay(dict(game.character), self.characters, lambda:())
        with p.transaction(write=False) as db:
            self.assertEqual(len(temporary_items(db,p.cid)),9)
            self.assertEqual(db.execute('SELECT amount FROM native_pending_loot WHERE character_id=?',(p.cid,)).fetchone()[0],501)
        self.call(restored,804)
        with p.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=? AND page=8 AND slot IN (23,24)',(p.cid,))
        self.call(restored,804)
        with p.transaction(write=False) as db:
            self.assertEqual(sum(row['amount'] for row in temporary_items(db,p.cid)),4001)
            self.assertEqual(db.execute('SELECT count(*) FROM native_pending_loot WHERE character_id=?',(p.cid,)).fetchone()[0],0)

    def test_temp_drag_and_discard_validate_ownership_and_destination(self):
        game = self.a
        p = game.progression
        self.fill_bag(game)
        with p.transaction() as db:
            award(db,p,'drag',{4000:3,4001:2})
            temp = temporary_items(db,p.cid)
            db.execute('DELETE FROM inventory WHERE character_id=? AND page=8 AND slot=24',(p.cid,))
        for cid, args in ((game,(TEMP_PAGE,1,8,1)), (game,(8,2,TEMP_PAGE,3)), (self.b,(TEMP_PAGE,1,8,24))):
            self.assertIn(98,[mid(reply) for reply in self.call(cid,803,*args)])
        self.assertIn(98,[mid(reply) for reply in self.call(self.b,798,temp[1]['id'])])
        self.call(game,803,TEMP_PAGE,1,8,24)
        self.call(game,798,temp[1]['id'])
        self.assertEqual(next(i['amount'] for i in p.snapshot()['items'] if i['id']==temp[0]['id']),3)
        with p.transaction(write=False) as db:
            self.assertFalse(temporary_items(db,p.cid))

    def test_disconnect_timer_preserves_short_relogin_and_expires_new_items_only(self):
        game = self.a
        p = game.progression
        self.fill_bag(game)
        with p.transaction() as db:
            award(db,p,'new',{4000:2})
            db.execute('INSERT INTO native_pending_loot VALUES(?,?,4001,1)',('legacy',p.cid))
        with patch('native946_temp_inventory.time',return_value=1000):
            self.svc.detach(p.cid)
        with patch('native946_temp_inventory.time',return_value=1899):
            restored = NativeGameplay(dict(game.character),self.characters,lambda:())
        with p.transaction(write=False) as db:
            self.assertEqual([item['type'] for item in temporary_items(db,p.cid)],[4000,4001])
        with patch('native946_temp_inventory.time',return_value=1900):
            self.svc.detach(p.cid)
        with patch('native946_temp_inventory.time',return_value=2800):
            NativeGameplay(dict(restored.character),self.characters,lambda:())
        with p.transaction(write=False) as db:
            self.assertEqual([item['type'] for item in temporary_items(db,p.cid)],[4001])

    def test_reconnect_drawing_replays_animation_and_processor_wakeup(self):
        battle=self.battle()
        self.call(self.a,2177,3,0);self.call(self.b,2177,3,0)
        restored=NativeGameplay(dict(self.b.character),self.characters,lambda:())
        replies=self.svc.reconnect_packets(restored)
        ids=[mid(reply) for reply in replies]
        self.assertIn(2167,ids)
        self.assertEqual(ids[ids.index(2167):],[2167])
        self.assertEqual(battle.state['phase'],'drawing')

    def test_reconnected_member_confirms_the_new_clients_playback(self):
        battle=self.battle()
        self.call(self.a,2178,1);self.call(self.b,2178,1)
        self.call(self.a,2184)
        self.svc.detach(self.a.character['id'])
        restored=NativeGameplay(dict(self.a.character),self.characters,lambda:())
        self.assertIn(2167,[mid(reply) for reply in self.svc.reconnect_packets(restored)])
        self.call(self.b,2184)
        self.assertEqual((battle.state['round'],battle.state['phase']),(1,'drawing'))
        self.call(restored,2184)
        self.assertEqual((battle.state['round'],battle.state['phase']),(2,'input'))

    def test_reconnect_announces_current_round_before_pending_guardian_menu(self):
        battle=self.battle(pet=True)
        aid=self.a.character['id']
        battle.state['round']=5
        self.call(self.a,2178,1)
        self.svc.detach(aid)
        restored=NativeGameplay(dict(self.a.character),self.characters,lambda:())
        replies=self.svc.reconnect_packets(restored)
        ids=[mid(reply) for reply in replies]
        self.assertLess(ids.index(2135),ids.index(2151))
        self.assertEqual(ids[ids.index(2151)+1:ids.index(2151)+3],[2703,2139])
        self.assertEqual(MessageReader(replies[ids.index(2135)][3:]).integer(),5)
        self.assertEqual(MessageReader(replies[ids.index(2151)][3:]).integer(),battle.current_pet(aid)['id'])

    def test_reconnect_waiting_team_member_drains_the_opening_effect_without_input(self):
        battle=self.battle()
        aid=self.a.character['id']
        self.call(self.a,2178,1)
        self.svc.detach(aid)
        restored=NativeGameplay(dict(self.a.character),self.characters,lambda:())
        replies=self.svc.reconnect_packets(restored)
        ids=[mid(reply) for reply in replies]
        self.assertNotIn(2151,ids)
        self.assertEqual(ids[ids.index(2135)+1:ids.index(2135)+3],[2703,2139])
        self.assertGreater(ids.index(3817),ids.index(2139))

    def test_disconnected_member_waits_until_original_input_deadline(self):
        from time import monotonic
        started=monotonic()
        with patch('native946_wilderness.monotonic',return_value=started):
            battle=self.battle(pet=True)
        self.assertEqual(battle.state['deadline'],started+45)
        aid,bid=(g.character['id'] for g in self.games)
        missing_pet=battle.current_pet(bid)['id']
        self.call(self.b,2178,1)
        self.svc.detach(bid)
        self.call(self.a,2178,1)
        self.assertEqual(battle.state['phase'],'input')
        replies=self.call(self.a,2182,1)
        self.assertNotIn(2167,[mid(reply) for reply in replies])
        deadline=battle.state['deadline']
        self.svc.tick(self.a,deadline-.001)
        self.assertEqual(battle.state['phase'],'input')
        self.assertNotIn(missing_pet,battle.state['orders'])
        self.svc.tick(self.a,deadline)
        self.assertEqual(battle.state['phase'],'drawing')
        self.assertEqual(battle.state['orders'][bid]['kind'],'defend')
        self.assertEqual(battle.state['orders'][missing_pet]['kind'],'attack')
        self.assertEqual(battle.state['orders'][aid]['kind'],'defend')

    def test_disconnect_keeps_member_and_reconnects_same_shared_battle(self):
        battle=self.battle();self.svc.detach(self.b.character['id'])
        restored=NativeGameplay(dict(self.b.character),self.characters,lambda:())
        self.assertIs(restored._wild_battle,battle)
        self.assertIs(battle.games[self.b.character['id']],restored)
        self.assertEqual(len(self.svc.team(self.a.character['id'])['members']),2)
        self.fight(battle)

    def test_all_disconnects_release_battle_without_free_rewards(self):
        battle=self.battle()
        for g in self.games:self.svc.detach(g.character['id'])
        self.assertFalse(self.svc.battles)
        self.assertEqual(battle.state['phase'],'finished')
        self.assertEqual(self.a.progression.snapshot()['experience'],0)

    def test_timeout_supplies_only_missing_orders(self):
        battle=self.battle();self.call(self.a,2178,1)
        battle.tick(battle.state['deadline']+1)
        self.assertEqual(battle.state['orders'][self.a.character['id']]['kind'],'defend')
        self.assertEqual(battle.state['phase'],'drawing')

    def test_batched_timeout_victory_persists_level_spells_and_allows_relogin(self):
        from native946_durable_writer import close
        from native946_progression import level_up_cost
        from native946_sqlite_runtime import configure_database
        from sys import getswitchinterval, setswitchinterval

        self.team()
        with self.a.progression.transaction() as db:
            for game in self.games:
                db.execute('UPDATE characters SET experience=? WHERE id=?',
                           (level_up_cost(1, 2) - 1, game.character['id']))
        zone = DATA['zones'][0]
        battle = WildBattle(self.a, self.games, dict(zone, fixed_roster=True, enemies=zone['enemies'][:1]))
        battle.enemies()[0]['hp'] = 1
        self.addCleanup(setswitchinterval, getswitchinterval())
        configure_database(self.path)
        self.addCleanup(close, self.path)

        with patch('native946_book_combat.roll', return_value={'miss': False, 'damage': 500, 'effect': 0}):
            self.svc.tick(self.a, battle.state['deadline'])
        self.assertTrue(battle.state['victory'])
        self.assertEqual(battle.state['phase'], 'drawing')
        for game in self.games:
            self.call(game, 2184)
        self.assertEqual(battle.state['phase'], 'finished')

        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM native_encounter_rewards WHERE battle_id=?',
                                        (battle.id,)).fetchone()[0], 2)
        for game in self.games:
            cid = game.character['id']
            self.svc.detach(cid)
            character = self.characters.list(game.character['owner'])[0]
            restored = NativeGameplay(character, self.characters, lambda: ())
            saved = restored.progression.snapshot()
            self.assertGreaterEqual(saved['level'], 2)
            self.assertEqual(saved['skills'][6101], 0)
            self.assertIsNone(restored.battle)

    def test_native_zone_bounds_are_used_and_cities_stay_safe(self):
        self.assertIsNone(zone_at(2001,300,210))
        z=DATA['zones'][0]
        self.assertEqual(zone_at(z['scene'],(z['bounds'][0]+19)//20,(z['bounds'][1]+19)//20)['id'],z['id'])
        self.assertIsNone(zone_at(z['scene'],0,0))

    def test_water_curtain_walk_starts_monkey_encounter_and_settles_once(self):
        from struct import pack
        from native946_login import string_field
        from native946_map import DIRECTIONS

        game = self.a
        cid = game.character['id']
        self.buff(game)
        game.summons.claim_starter()
        pet = game.summons.active()
        with game.progression.transaction() as db:
            db.execute('UPDATE characters SET map_id=2011,x=11,y=53 WHERE id=?', (cid,))
            db.execute('UPDATE learned_skills SET rank=10000 WHERE character_id=?', (cid,))
            db.execute('UPDATE learned_skills SET rank=9800 WHERE character_id=? AND skill_id=6100', (cid,))
            db.execute('UPDATE summons SET level=180,rebirth=2,ascension=1 WHERE id=?', (pet['id'],))
            db.execute('INSERT INTO native_guardian_books(summon_id,skill,position,expnext) VALUES(?,300,7,20000)',
                       (pet['id'],))
        game.character.update(map_id=2011, x=11, y=53)
        game.grid = load_map(2011)
        game._encounter_steps = POLICY['encounter_steps'] - 1
        self.assertIn(1025, [mid(p) for p in self.call(game, 1034, 5718)])
        game.receive(1032, MessageReader(b'\x01' + number(5718)))
        self.assertEqual(game.character['map_id'], 2266)
        self.assertIsNone(game.battle)

        path = game.grid.route((68, 26), (35, 38))
        by_delta = {delta: code for code, delta in DIRECTIONS.items()}
        for i, (start, end) in enumerate(zip(path, path[1:]), 1):
            direction = by_delta[(end[0] - start[0], end[1] - start[1])]
            replies = self.call(game, 1643, 4, tail=pack('<HH', *start) + string_field(bytes([direction])))
            if i < POLICY['encounter_steps']:
                self.assertIsNone(game.battle)
            else:
                break
        self.assertIn(2171, [mid(p) for p in replies])
        battle = game._wild_battle
        # The original client lays out the opposing slots in this vertical order.
        by_slot = {f['slot']: f['name'][-1] for f in battle.enemies()}
        roster = tuple(by_slot[slot] for slot in (4, 2, 1, 3, 5))
        self.assertIn(roster, (tuple('乙甲甲甲乙'), tuple('甲乙乙乙甲')))
        self.assertEqual([f['slot'] for f in battle.enemies()], [1, 2, 3, 4, 5])
        before = game.progression.snapshot()
        guardian_xp = game.summons.get(pet['id'])['experience']
        target = battle.enemies()[0]['id']
        self.assertIn(98, [mid(p) for p in self.call(game, 2177, 5, target)])
        self.call(game, 2185, 9, 6100, target)
        self.call(game, 2182, 1)
        self.fight(battle)

        after = game.progression.snapshot()
        self.assertEqual(after['experience'] - before['experience'], 45000)
        self.assertEqual(after['gold'], before['gold'])
        self.assertEqual(after['skills'][6100] - before['skills'][6100], 200)
        self.assertEqual(game.summons.get(pet['id'])['experience'] - guardian_xp, 48539)
        self.call(game, 2184)
        battle.state['settled'] = False
        with game.progression.transaction() as db:
            battle.settle(db, True)
            rows = db.execute('SELECT payload FROM native_encounter_rewards WHERE battle_id=?', (battle.id,)).fetchall()
            self.assertEqual(len(rows), 1)
            reward = json.loads(rows[0][0])
            self.assertEqual(reward['school_practice']['amount'], 200)
            self.assertEqual(reward['drops'], {})
            self.assertEqual(reward['guardian_experience_by_id'], {str(pet['id']): 48539})
            self.assertEqual(db.execute('SELECT experience FROM native_guardian_books WHERE summon_id=?',
                                        (pet['id'],)).fetchone()[0], 12000)
        self.assertEqual(game.progression.snapshot()['experience'], after['experience'])

    def test_water_curtain_randomizes_only_eligible_learned_spells(self):
        game = self.a
        self.buff(game)
        cid = game.character['id']
        battle = WildBattle(game, [game], zone_at(2266, 68, 26))
        with game.progression.transaction() as db:
            db.execute('DELETE FROM learned_skills WHERE character_id=?', (cid,))
            db.executemany('INSERT INTO learned_skills VALUES(?,?,?)', [
                (cid, 6100, 9999), (cid, 6107, 0), (cid, 6128, 10000), (cid, 21, 0), (cid, 9, 0),
            ])
            awards = set()
            for draw in range(20):
                battle.id = f'practice-fixture-{draw}'
                reward = battle.school_practice_reward(db, cid, 260)
                self.assertEqual(battle.school_practice_reward(db, cid, 260), reward)
                awards.add((reward['skill'], reward['amount']))
            self.assertEqual(awards, {(6100, 1), (6107, 260)})
            db.execute('UPDATE learned_skills SET rank=10000 WHERE character_id=?', (cid,))
            self.assertIsNone(battle.school_practice_reward(db, cid, 260))

    def test_water_curtain_trains_a_learned_school_stunt(self):
        from native946_school import LIZARD_MASTER, add_contribution
        from native946_world import world

        game = self.a
        self.buff(game)
        cid = game.character['id']
        scene, master = LIZARD_MASTER
        npc = world().npc(scene, master)
        arrival = load_map(scene).nearest_walkable(npc['x'] // 20, npc['y'] // 20)
        game.change_scene(dict(id=0, to=scene, arrival=arrival), None)

        for operation in ('join', 'learn'):
            self.assertIn(1025, [mid(p) for p in self.call(game, 1034, master)])
            choice = next(i for i, action in enumerate(game.dialog_actions, 1) if action['op'] == operation)
            game.receive(1032, MessageReader(bytes([choice]) + number(master)))
        with game.progression.transaction() as db:
            add_contribution(db, cid, 10)
            db.execute('UPDATE characters SET gold=100 WHERE id=?', (cid,))
        self.assertNotIn(6105, game.progression.snapshot()['skills'])
        self.assertIn(699, [mid(p) for p in self.call(game, 749, 6105, 1)])
        self.assertEqual(game.progression.snapshot()['skills'][6105], 1)
        with game.progression.transaction() as db:
            db.execute('UPDATE learned_skills SET rank=10000 WHERE character_id=? AND skill_id<>6105', (cid,))

        game.change_scene(dict(id=0, to=2269, arrival=(20, 56)), None)
        battle = WildBattle(game, [game], zone_at(2269, 20, 56))
        target, *others = battle.enemies()
        target['hp'] = 1
        for enemy in others:
            enemy['hp'] = 0
        self.call(game, 2185, 9, 6100, target['id'])
        self.assertTrue(battle.state['victory'])
        replies = self.call(game, 2184)
        notices = [MessageReader(p[1:]).string().decode('gb18030') for p in replies if mid(p) == 100]
        self.assertTrue(any('师门技能#G死生契阔#n获得#G320#n熟练度' in text for text in notices))
        self.assertEqual(game.progression.snapshot()['skills'][6105], 321)

        reader = MessageReader(next(p for p in replies if mid(p) == 699)[3:])
        self.assertEqual(reader.integer(), 18)
        for _ in range(reader.take(1)[0]):
            for _ in range(3):
                reader.integer()
        ranks = {}
        for _ in range(reader.take(1)[0]):
            reader.string()
            reader.string()
            practice = reader.integer()
            ranks[reader.integer()] = practice
        self.assertEqual(ranks[6105], 321)

    def test_water_curtain_floor_practice_and_team_scaling(self):
        from native946_role_battle import profile
        from native946_school import SCHOOL_SKILLS

        accounts = LocalAccounts(self.path)
        for i, role in enumerate((111, 211, 311), 2):
            owner = f'wild{i}'
            accounts.register(dict(username=owner, password='fixture-password'))
            character = self.characters.create(owner, f'水帘队员{i}', role)
            self.games.append(NativeGameplay(character, self.characters, lambda: ()))
        for game in self.games:
            self.buff(game)
        pet = self.a.summons.claim_starter()

        for scene, participants, enemy_count, practice, bonus in (
            (2266, 1, 5, 260, 0),
            (2267, 2, 5, 280, 5),
            (2268, 4, 5, 300, 15),
            (2269, 5, 7, 320, 20),
        ):
            with self.subTest(scene=scene, participants=participants):
                with self.a.progression.transaction() as db:
                    db.execute('UPDATE summons SET level=89,experience=0 WHERE id=?', (pet,))
                games = self.games[:participants]
                battle = WildBattle(self.a, games, zone_at(scene, 30, 30))
                self.assertEqual([f['slot'] for f in battle.enemies()], list(range(1, enemy_count + 1)))
                target, *others = battle.enemies()
                target['hp'] = 1
                for enemy in others:
                    enemy['hp'] = 0
                before = {g.character['id']: g.progression.snapshot() for g in games}
                for game in games[1:]:
                    self.call(game, 2178, 1)
                self.call(self.a, 2185, 9, 6100, target['id'])
                self.call(self.a, 2182, 1)
                self.assertTrue(battle.state['victory'])
                self.assertEqual(battle.state['rewards'][self.a.character['id']]['guardian_experience_by_id'],
                                 {str(pet): 24000 * (100 + bonus) // 100})
                acknowledgements = {}
                for game in games:
                    cid = game.character['id']
                    saved = game.progression.snapshot()
                    reward = battle.state['rewards'][cid]
                    self.assertEqual(saved['experience'] - before[cid]['experience'], 45000 * (100 + bonus) // 100)
                    trained = reward['school_practice']['skill']
                    self.assertIn(trained, SCHOOL_SKILLS)
                    self.assertIn(profile(trained)['race'], (0, saved['race']))
                    changes = {skill: rank - before[cid]['skills'][skill] for skill, rank in saved['skills'].items()
                               if rank != before[cid]['skills'][skill]}
                    self.assertEqual(changes, {trained: practice})
                    self.assertEqual(reward.get('team_experience_percent', 0), bonus)
                    acknowledgements[cid] = self.call(game, 2184)
                self.assertIsNone(self.a.battle)
                for game in games:
                    cid = game.character['id']
                    replies = (*acknowledgements[cid], *self.svc.drain(game))
                    popups = [MessageReader(p[1:]).string().decode('gb18030') for p in replies if mid(p) == 100]
                    self.assertEqual(len(popups), 1)
                    self.assertIn('熟练度', popups[0])
                    self.assertIn('经验', popups[0])
                    self.assertNotIn('组队经验加成', popups[0])
                    for now in (100, 106.49):
                        self.assertFalse(any(mid(p) == 100 for p in game.tick(now) or ()))
                    popups = [MessageReader(p[1:]).string().decode('gb18030')
                              for p in game.tick(106.5) or () if mid(p) == 100]
                    self.assertEqual(popups, [f'组队经验加成#G{bonus}%#n。'] if bonus else [])
                    self.assertFalse(any(mid(p) == 100 for p in game.tick(107) or ()))

    def test_water_curtain_rewards_each_guardian_at_its_own_level_after_switch(self):
        from native946_summons import LEVEL_EXPERIENCE_FACTOR

        game = self.a
        cid = game.character['id']
        self.buff(game)
        with game.progression.transaction() as db:
            db.execute('UPDATE characters SET level=80 WHERE id=?', (cid,))
        game.character['level'] = 80
        starter = game.summons.claim_starter()
        replacement = game.summons.acquire(3374, 'cave-level-test', level=30)
        with game.progression.transaction() as db:
            db.execute('UPDATE summons SET level=89 WHERE id=?', (starter,))
        before = {sid: game.summons.get(sid) for sid in (starter, replacement)}
        previous_exp = game.progression.snapshot()['experience']

        battle = WildBattle(game, [game], zone_at(2266, 68, 26))
        self.call(game, 2177, 6, replacement)
        self.assertEqual(battle.current_pet(cid)['id'], replacement)
        self.call(game, 2184)
        self.fight(battle)

        expected = {str(starter): 24000, str(replacement): 8089}
        self.assertEqual(game.progression.snapshot()['experience'] - previous_exp, 20000)
        self.assertEqual(battle.state['rewards'][cid]['guardian_experience_by_id'], expected)
        messages = next(p for p in battle.finish_messages(cid, True) if mid(p) == 100)
        text = MessageReader(messages[1:]).string().decode('gb18030')
        for sid, initial in before.items():
            saved = game.summons.get(sid)
            levels = range(initial['level'], saved['level'])
            earned = saved['experience'] - initial['experience'] + LEVEL_EXPERIENCE_FACTOR * sum(levels)
            self.assertEqual(earned, expected[str(sid)])
            self.assertIn(f"你的守护{initial['name']}得到了{earned}经验", text)

    def test_water_curtain_retreat_grants_no_rewards(self):
        game = self.a
        game.summons.claim_starter()
        before = game.progression.snapshot()
        guardian_xp = game.summons.active()['experience']
        battle = WildBattle(game, [game], zone_at(2266, 68, 26))
        self.call(game, 2178, 2)
        self.assertIsNone(game.battle)
        self.assertEqual(game.progression.snapshot()['experience'], before['experience'])
        self.assertEqual(game.progression.snapshot()['skills'], before['skills'])
        self.assertEqual(game.summons.active()['experience'], guardian_xp)
        with game.progression.transaction(write=False) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM native_encounter_rewards WHERE battle_id=?',
                                         (battle.id,)).fetchone())

    def test_follow_crosses_scene_and_cold_position_matches(self):
        self.team();target=2008;grid=load_map(target);x,y=grid.nearest_walkable(50,50)
        self.a.character.update(map_id=target,x=x,y=y);self.a.grid=grid
        self.svc.follow_team(self.svc.team(self.a.character['id']))
        self.assertEqual((self.b.character['map_id'],self.b.character['x'],self.b.character['y']),(target,x,y))
        cold=self.characters.list(self.b.character['owner'])[0]
        self.assertEqual((cold['map_id'],cold['x'],cold['y']),(target,x,y))

    def test_following_member_cannot_walk_away_until_away(self):
        from struct import pack
        from native946_login import string_field
        self.team();x,y=self.b.character['x'],self.b.character['y']
        r=self.b.receive(1643,MessageReader(number(4)+pack('<HH',x,y)+string_field(b'')))
        self.assertIn(1606,[mid(p) for p in r])
        self.call(self.b,2044)
        self.assertEqual(self.svc.team(self.a.character['id'])['members'][1]['away'],1)

    def test_late_world_movement_does_not_reposition_battle_fighters(self):
        from struct import pack
        from native946_login import string_field

        battle = self.battle()
        for game in self.games:
            with self.subTest(character=game.character['id']):
                self.svc.drain(game)
                cid = game.character['id']
                original = (game.character['x'], game.character['y'])
                pending = packet(2151, ['number'] * 3, [cid, 40, 0])
                self.svc.enqueue(game, pending)
                move = pack('<HH', *original) + string_field(b'')
                self.assertEqual(self.call(game, 1643, 5, tail=move), (pending,))
                self.assertEqual(self.call(game, 1643, 5, tail=move), ())
                self.assertEqual((game.character['x'], game.character['y']), original)
                saved = self.characters.list(game.character['owner'])[0]
                self.assertEqual((saved['x'], saved['y']), original)
                self.assertIs(game._wild_battle, battle)
                self.assertEqual(battle.state['phase'], 'input')

    def test_disconnected_leader_transfers_to_online_member(self):
        self.team()
        old, new = (game.character['id'] for game in self.games)
        self.svc.drain(self.b)
        self.svc.detach_port(self.a.source_port)
        team = self.svc.team(new)
        self.assertEqual(team['leader_id'], new)
        self.assertEqual(team['members'][0]['away'], 1)
        self.assertEqual(self.b.character['team_leader'], new)
        replies = self.svc.drain(self.b)
        change = packet(2028, ['number', 'number'], [new, old])
        remove = packet(1604, ['number'], [old])
        self.assertLess(replies.index(change), replies.index(remove))
        self.assertEqual([mid(p) for p in replies].count(3228), 1)
        self.assertEqual(self.call(self.b, 2043), (self.svc.application_list(new),))

    def test_disconnected_leader_retries_before_removing_scene_actor(self):
        self.team()
        old, new = (game.character['id'] for game in self.games)
        self.svc.drain(self.b)
        with patch('native946_lineups.reset_invalid_selection', side_effect=StorageBusy('fixture busy')):
            self.svc.detach_port(self.a.source_port)
        self.assertEqual(self.svc.team(new)['leader_id'], old)
        self.assertFalse({2028, 1604}.intersection(map(mid, self.svc.drain(self.b))))

        replies = list(map(mid, self.svc.tick(self.b)))
        self.assertLess(replies.index(2028), replies.index(1604))
        self.assertEqual(self.svc.team(new)['leader_id'], new)
        self.assertFalse({2028, 1604}.intersection(map(mid, self.svc.tick(self.b))))

    def test_disconnected_leader_retries_while_battle_is_active(self):
        battle = self.battle()
        old, new = (game.character['id'] for game in self.games)
        self.svc.drain(self.b)
        with patch('native946_lineups.reset_invalid_selection', side_effect=StorageBusy('fixture busy')):
            self.svc.detach_port(self.a.source_port)
        self.assertIsNone(self.svc.online(old))
        self.assertEqual(self.svc.team(new)['leader_id'], old)
        self.assertNotIn(2028, list(map(mid, self.svc.drain(self.b))))

        replies = self.svc.tick(self.b)
        self.assertIn(packet(2028, ['number', 'number'], [new, old]), replies)
        self.assertEqual(self.svc.team(new)['leader_id'], new)
        self.assertEqual(battle.leader, new)
        self.assertNotIn(2028, list(map(mid, self.svc.tick(self.b))))

    def test_first_member_to_reconnect_gets_leader_packets_when_team_was_offline(self):
        self.team()
        old, new = (game.character['id'] for game in self.games)
        self.svc.detach(self.b.character['id'])
        self.svc.detach(self.a.character['id'])
        self.assertEqual(self.svc.team(new)['leader_id'], old)

        self.svc.attach(self.b)
        replies = self.svc.reconnect_packets(self.b)
        self.assertEqual(replies[0], packet(2025, ['number'], [new]))
        self.assertEqual(self.svc.team(new)['leader_id'], new)

    def test_native_team_record_types_match_client_constants(self):
        self.team();team=self.svc.team(self.a.character['id'])
        leader,member=team['members']
        self.assertEqual([self.svc.member_record(m)['type'] for m in (leader,member)],[0,0])
        self.call(self.b,2044);team=self.svc.team(self.a.character['id'])
        self.assertEqual(self.svc.member_record(team['members'][1])['type'],1)
        self.call(self.b,2045)
        self.assertEqual(self.svc.member_record(self.svc.team(member['character_id'])['members'][1])['type'],0)
        self.svc.detach(member['character_id'])
        self.assertEqual(self.svc.member_record(member)['type'],1)
        self.svc.attach(self.b)
        self.assertEqual(self.svc.member_record(member)['type'],0)

    def test_shared_medicine_can_heal_ally_but_cannot_spend_ally_inventory(self):
        battle=self.battle();cid=self.a.character['id'];other=self.b.character['id']
        battle.state['fighters'][other]['hp']-=100
        mine=next(i['id'] for i in self.a.progression.snapshot()['items'] if i['type']==4000)
        theirs=next(i['id'] for i in self.b.progression.snapshot()['items'] if i['type']==4000)
        bad=self.call(self.a,2185,8,theirs,other)
        self.assertIn(98,[mid(p) for p in bad]);self.assertNotIn(cid,battle.state['orders'])
        self.call(self.a,2185,8,mine,other);self.call(self.b,2178,1)
        self.assertEqual(next(i['amount'] for i in self.a.progression.snapshot()['items'] if i['id']==mine),4)
        self.assertEqual(next(i['amount'] for i in self.b.progression.snapshot()['items'] if i['id']==theirs),5)

    def test_multiple_animation_acks_cannot_advance_unfinished_barrier(self):
        battle=self.battle();self.call(self.a,2178,1);self.call(self.b,2178,1)
        self.call(self.a,2184);self.call(self.a,2184)
        self.assertEqual(battle.state['phase'],'drawing');self.assertEqual(battle.state['round'],1)
        self.call(self.b,2184)
        self.assertEqual(battle.state['phase'],'input');self.assertEqual(battle.state['round'],2)

    def test_round_timeout_storage_busy_does_not_disconnect_players(self):
        from native946_storage import StorageBusy
        battle=self.battle();prior=deepcopy(battle.state)
        with patch.object(battle,'persist',side_effect=StorageBusy('busy')):
            self.svc.tick(self.a,battle.state['deadline']+1)
        self.assertEqual(battle.state,prior)
        self.assertTrue(self.svc.online(self.a.character['id']))

    def test_remote_back_uses_native_navigation_before_joining_follow(self):
        self.team();self.call(self.b,2044)
        old=dict(self.b.character);self.b.character['x']+=30
        replies=self.call(self.b,2045)
        self.assertIn(2024,[mid(p) for p in replies])
        team=self.svc.team(self.a.character['id'])
        self.assertEqual(team['members'][1]['away'],1)
        self.assertEqual(self.b.character['x'],old['x']+30)

    def test_geshan_targets_another_enemy_even_if_primary_has_fallen(self):
        from native946_neidan_legacy import effect
        battle=self.battle(pet=True);pet=battle.current_pet(self.a.character['id']);pet['level']=100
        pill=dict(kind=235,level=102,rebirth=2,energy=6000);pet['neidan']=[pill]
        first,second=battle.enemies()[:2];first['hp']=0
        before=second['hp'];expected=min(before,int(pet['attack']*effect(pill,pet)['ratio']/100))
        with patch.object(battle.rng,'random',return_value=0):replies=battle.passives(pet,first)
        self.assertEqual(second['hp'],before-expected)
        self.assertIn(2157,[mid(p) for p in replies])

if __name__=='__main__':unittest.main()
