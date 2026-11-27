"""Native literal formula oracle and atomic persisted cultivation checks."""
import ast
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'.test-deps'))
from hypothesis import given,settings,strategies as st
from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import number
from native946_gameplay import NativeGameplay,ENEMY_ID
from native946_login import MessageReader
from native946_progression import Progression
from native946_summons import Summons
import native946_training as training
import native946_neidan as neidan


def native_literal_formula(expression,values):
    """Read the original calculator string, without eval or arbitrary calls."""
    def visit(node):
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):return node.value
        if isinstance(node,ast.Name):return values[node.id]
        if isinstance(node,ast.BinOp):
            x,y=visit(node.left),visit(node.right)
            if isinstance(node.op,ast.Add):return x+y
            if isinstance(node.op,ast.Sub):return x-y
            if isinstance(node.op,ast.Mult):return x*y
            if isinstance(node.op,ast.Div):return x/y
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id=='math' and node.func.attr=='max':
            return max(visit(v) for v in node.args)
        raise ValueError('Outside native numeric fixture scope')
    return math.floor(visit(ast.parse(expression,mode='eval').body))


class SkillFormulaTests(unittest.TestCase):
    @settings(max_examples=500,derandomize=True,deadline=None,database=None)
    @given(skill=st.sampled_from([6100,6101]),practice=st.integers(0,25000),attack=st.integers(0,20000),
           level=st.integers(0,200),defense=st.integers(0,30000))
    def test_damage_matches_original_literal_formula(self,skill,practice,attack,level,defense):
        original=training.DATA['wuli_formulas'][str(skill)]['function1']
        self.assertEqual(training.wuli_damage(skill,practice,attack,level,defense),
                         native_literal_formula(original,dict(attall=attack,grade=level,defallb=defense,practice1=practice,practice2=practice)))

    def test_native_fixture_and_minimum(self):
        self.assertEqual(training.wuli_damage(6100,1,76,1),144)
        self.assertEqual(training.wuli_damage(6101,25000,2000,100,200),2775)
        self.assertEqual(training.wuli_damage(6100,0,0,0,99999),1)


class CultivationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'game.sqlite3'
        LocalAccounts(self.path).register({'username':'owner946','password':'123456789'})
        self.characters=Characters(self.path)
        self.character=self.characters.create('owner946','修炼测试',702)
        self.p=Progression(self.path,'owner946',self.character['id'])
        self.s=Summons(self.p)

    def seed(self,level=120,cash=1000000000,tongbao=1000000000,experience=100000000000):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=?,gold=?,experience=? WHERE id=?',(level,cash,experience,self.p.cid))
            db.execute('UPDATE cultivation_state SET tongbao=?,rebirth=2,ascension=1,sanctification=1 WHERE character_id=?',(tongbao,self.p.cid))

    def test_locked_gate_and_foreign_owner(self):
        before=self.p.snapshot()
        with self.assertRaisesRegex(ValueError,'领悟等级'):training.learn(self.p,1258)
        self.assertEqual(before,self.p.snapshot())
        with self.assertRaises(ValueError):Progression(self.path,'stranger',self.p.cid)

    def test_batch_upgrade_exact_native_cost_and_separate_wallet(self):
        self.seed();training.learn(self.p,1258);before=self.p.snapshot()
        training.upgrade(self.p,1258,3);after=self.p.snapshot()
        rows=training.DATA['cultivation']['Skill1258Data'][:3]
        with self.p.transaction(write=False) as db:
            wallet=training.state(db,self.p.cid)['tongbao']
        self.assertEqual(wallet,1000000000-sum(r['Cash'] for r in rows))
        self.assertEqual(before['gold'],after['gold'])
        self.assertEqual(after['cultivation']['HpPercent'],15)
        cold=Progression(self.path,'owner946',self.p.cid).snapshot()
        self.assertEqual(after['hpmax'],cold['hpmax'])

    def test_no_conversion_between_wallets_and_no_partial_batch(self):
        self.seed(tongbao=20000);training.learn(self.p,1258)
        before=self.p.snapshot()
        with self.assertRaisesRegex(ValueError,'通宝'):training.upgrade(self.p,1258,2)
        self.assertEqual(self.p.snapshot(),before)
        with self.p.transaction(write=False) as db:self.assertEqual(training.state(db,self.p.cid)['tongbao'],20000)

    def test_native_battle_secret_levels_update_hero_evaluation(self):
        from native946_game_login import message_id

        def evaluation(replies):
            for payload in replies:
                if payload[:3] == message_id(695):
                    reader = MessageReader(payload[3:])
                    if reader.integer() == 48:
                        value = reader.integer()
                        reader.finish()
                        return value
            self.fail('Missing native hero evaluation attribute')

        self.seed()
        game = NativeGameplay(self.character, self.characters, lambda: ())
        self.assertEqual(evaluation(game.progression.attribute_packets()), 0)
        for skill, count, expected in ((1258, 3, 12), (1256, 2, 20)):
            game.receive(3420, MessageReader(number(skill)))
            reply = game.receive(3421, MessageReader(number(skill) + number(count)))
            self.assertEqual(evaluation(reply), expected)

        with self.p.transaction() as db:
            db.execute('UPDATE learned_skills SET rank=1750 WHERE character_id=? AND skill_id=6100', (self.p.cid,))
        cold = Progression(self.path, 'owner946', self.p.cid)
        self.assertEqual(evaluation(cold.attribute_packets()), 20)

    def test_cash_and_exp_consumption(self):
        self.seed();training.learn(self.p,1256);before=self.p.snapshot()
        training.upgrade(self.p,1256,2);after=self.p.snapshot()
        self.assertEqual(before['gold']-after['gold'],100180)
        self.assertEqual(before['experience']-after['experience'],9550000)
        self.assertEqual(after['cultivation']['DecEnemyEscapeOdd'],4)

    def test_summon_hp_cultivation_and_cold_read(self):
        self.seed();sid=self.s.claim_starter();before=self.s.get(sid)
        training.learn(self.p,1261);training.upgrade(self.p,1261,2)
        after=Summons(Progression(self.path,'owner946',self.p.cid)).get(sid)
        self.assertEqual(after['hpmax']-before['hpmax'],200)

    def test_allocation_is_absolute_and_cannot_mint_points(self):
        attack=self.p.snapshot()['attack']
        with self.p.transaction() as db:db.execute('UPDATE cultivation_state SET earned_points=30 WHERE character_id=?',(self.p.cid,))
        points=[0]*13;points[6]=10;points[7]=20
        training.allocate(self.p,points);before=self.p.snapshot()
        training.allocate(self.p,points);self.assertEqual(before,self.p.snapshot())
        self.assertEqual(before['cultivation']['AttValue'],10)
        self.assertEqual(before['cultivation']['DefAll'],200)
        # AttValue is physical accuracy, not attack damage.
        self.assertEqual(before['attack'],attack)
        points[6]=11
        with self.assertRaisesRegex(ValueError,'不足'):training.allocate(self.p,points)
        self.assertEqual(before,self.p.snapshot())

    def test_trailing_packet_bytes_roll_back_spending(self):
        self.seed();training.learn(self.p,1258)
        game=NativeGameplay(self.character,self.characters,lambda:())
        before=self.p.snapshot()
        reply=game.receive(3421,MessageReader(number(1258)+number(1)+b'\x00'))
        self.assertEqual(reply[0],98)
        self.assertEqual(self.p.snapshot(),before)

    def test_battle_rejects_training_and_matches_skill_panel_damage(self):
        game=NativeGameplay(self.character,self.characters,lambda:());game.begin_battle()
        game.receive(3420,MessageReader(number(1258)))
        self.assertEqual(game.last_event['action'],'action_rejected')
        initial=game.battle['enemy_hp']
        game.receive(2185,MessageReader(number(9)+number(6100)+number(ENEMY_ID)))
        self.assertEqual(initial-game.battle['enemy_hp'],144)
        self.assertIn('144',self.p.skill_description(6100))

    def test_locked_high_slot_and_unknown_pill_requests_do_not_consume_items(self):
        sid=self.s.claim_starter()
        with self.p.transaction() as db:
            db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,91501,8,6,2)',(self.p.cid,))
        game=NativeGameplay(self.character,self.characters,lambda:())
        before=self.p.snapshot();item=next(v for v in before['items'] if v['type']==91501)
        game.receive(807,MessageReader(number(sid)+number(item['id'])))
        self.assertEqual(self.p.snapshot(),before)
        self.assertIn('槽位',game.last_event['reason'])
        for mid in (753,755,765,766,767,5227,5228,5229):
            game.receive(mid,MessageReader(number(sid)+number(999)))
            self.assertEqual(self.p.snapshot(),before)
        self.assertGreater(len(self.p.item_description(item['id'])),10)

    def test_two_writers_cannot_overspend_one_level_budget(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from native946_connections import close_thread
        self.seed(tongbao=20000);training.learn(self.p,1258)
        barrier=Barrier(2)
        def attempt():
            p=Progression(self.path,'owner946',self.p.cid)
            barrier.wait()
            try:training.upgrade(p,1258,1);return True
            except ValueError:return False
            finally:close_thread()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:attempt(),range(2)))
        self.assertEqual(sorted(results),[False,True])
        with self.p.transaction(write=False) as db:self.assertEqual(training.state(db,self.p.cid)['tongbao'],0)


class NeidanRulesTests(unittest.TestCase):
    def test_native_low_high_advancement_costs_and_returns(self):
        for kind in neidan.META:
            self.assertEqual([neidan.advance_cost(kind,n)[0] for n in range(1,5)],
                             [v*(2 if kind>=276 else 1) for v in (800000000,1200000000,1600000000,2000000000)])
            self.assertEqual([neidan.advance_cost(kind,n)[1] for n in range(1,5)],[1,2,3,4])
        self.assertEqual([neidan.split_return(n) for n in range(1,6)],[1,1,2,3,5])

    def test_slots_stage_and_resonance_are_distinct(self):
        self.assertEqual(neidan.slot_limits(0,0),(1,0));self.assertEqual(neidan.slot_limits(2,1),(3,1))
        self.assertEqual([neidan.resonance_slots(276,n) for n in range(1,6)],[0,0,1,2,3])
        self.assertEqual(neidan.effect_coefficient(236,5,[236],[236]),4)
        self.assertEqual(neidan.effect_coefficient(236,5,[237],[236]),3)

    def test_shield_breaker_matches_two_observed_ascended_first_rank_tooltips(self):
        for level, expected in ((168,'{9.50}'),(180,'{10.00}')):
            row = dict(kind=281,stage=3,level=level,advance=1)
            self.assertEqual(neidan.description(row), expected)


if __name__=='__main__':unittest.main()
