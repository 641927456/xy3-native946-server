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

    def test_drawing_wakes_processor_and_has_bounded_confirmation_timeout(self):
        from time import monotonic
        battle=self.battle(party=False,pet=True)
        replies=(*self.call(self.a,2177,3,0),*self.svc.drain(self.a),
                 *self.call(self.a,2181,3,0),*self.svc.drain(self.a))
        ids=[mid(reply) for reply in replies]
        self.assertIn(2167,ids)
        self.assertEqual(ids[-1],2167)
        self.assertEqual(battle.state['phase'],'drawing')
        self.assertLessEqual(battle.state['deadline']-monotonic(),15)

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
        self.fight(battle)
        for g in self.games:
            s=g.progression.snapshot();self.assertGreater(s['experience'],0)
            self.assertGreater(g.summons.snapshot()[0]['experience'],0)
            with g.progression.transaction(write=False) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM native_encounter_rewards WHERE battle_id=? AND character_id=?',(battle.id,g.character['id'])).fetchone()[0],1)
            previous=(s['experience'],s['gold']);self.call(g,2184);self.call(g,2177,3,0)
            s=g.progression.snapshot();self.assertEqual((s['experience'],s['gold']),previous)

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
        self.assertTrue(any('已放入摘星袋'.encode('gb18030') in reply for reply in replies))
        self.assertEqual(sum(mid(reply) == 100 for reply in replies), 1)
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

    def test_wild_victory_shows_one_native_notice_and_flies_delivered_items(self):
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
        notice, = (reply for reply in replies if mid(reply) == 100)
        reader = MessageReader(notice[1:])
        content = reader.string().decode('gb18030')
        self.assertIn('人物经验', content)
        self.assertIn('物品已放入道具栏。', content)
        self.assertNotIn('守护经验', content)
        self.assertEqual(reader.integer(), 0)
        reader.finish()
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

    def test_native_zone_bounds_are_used_and_cities_stay_safe(self):
        self.assertIsNone(zone_at(2001,300,210))
        z=DATA['zones'][0]
        self.assertEqual(zone_at(z['scene'],(z['bounds'][0]+19)//20,(z['bounds'][1]+19)//20)['id'],z['id'])
        self.assertIsNone(zone_at(z['scene'],0,0))

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

    def test_disconnected_leader_transfers_to_online_member(self):
        self.team();self.svc.detach(self.a.character['id'])
        team=self.svc.team(self.b.character['id'])
        self.assertEqual(team['leader_id'],self.b.character['id'])
        self.assertEqual(team['members'][0]['away'],1)

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
