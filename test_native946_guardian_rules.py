from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_gameplay import NativeGameplay
from native946_codec import number
from native946_login import MessageReader
from native946_guardian_rules import DATA,PENDING_REQUESTS

def code(p):return int.from_bytes(p[1:3],'little') if p[0]==0 else p[0]

class GuardianOriginalRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'rules.sqlite3'
        LocalAccounts(path).register(dict(username='rule_owner',password='fixture-only-password'))
        self.characters=Characters(path);self.character=self.characters.create('rule_owner','原版规则核查',702)
        self.game=NativeGameplay(self.character,self.characters,lambda:())
        self.sid=self.game.summons.claim_starter()

    def test_all_unverified_actions_reply_without_resource_changes(self):
        before=self.game.progression.snapshot();pet=self.game.summons.get(self.sid)
        from native946_skillbooks import SKILLS
        # Some request IDs handle both implemented and unverified skills.
        unverified_stunt = next(kind for kind in range(10000, 10100) if kind not in SKILLS)
        from native946_innate_training import REQUESTS as TRAINING_REQUESTS
        for mid,contract in PENDING_REQUESTS.items():
            if mid==759 or mid in TRAINING_REQUESTS:continue
            args = [self.sid] + [unverified_stunt] * (len(contract['fields']) - 1)
            result=self.game.receive(mid,MessageReader(b''.join(number(v) for v in args)))
            replies=result if isinstance(result,tuple) else (result,)
            self.assertIn(98,[code(p) for p in replies],mid)
            self.assertIn('尚未核定',self.game.last_event['reason'])
        self.assertEqual(self.game.progression.snapshot(),before)
        self.assertEqual(self.game.summons.get(self.sid),pet)

    def test_species_catalogue_shows_configured_skills_without_learning_them(self):
        sid=self.game.summons.acquire(3374,'catalogue:fixture')
        with self.game.progression.transaction() as db:
            db.execute('UPDATE summons SET level=140,rebirth=3,ascension=1 WHERE id=?',(sid,))
        before=self.game.summons.get(sid)
        learned_before=self.game.summons.stunt_packet(sid)
        result=self.game.receive(759,MessageReader(number(sid)))
        self.assertEqual(code(result),720)
        reader=MessageReader(result[3:])
        self.assertEqual(reader.integer(),sid)
        skills=[reader.integer() for _ in range(reader.take(1)[0])]
        reader.finish()
        self.assertEqual(skills, [302, 366, 4140, 4908])
        self.assertEqual(self.game.summons.get(sid),before)
        self.assertEqual(self.game.summons.stunt_packet(sid),learned_before)

    def test_foreign_guardian_is_rejected_before_disclosing_rule_status(self):
        result=self.game.receive(759,MessageReader(number(self.sid+1)))
        self.assertIn('不属于',self.game.last_event['reason'])

    def test_malformed_request_is_rejected_before_any_resource_operation(self):
        before=self.game.progression.snapshot()
        self.game.receive(754,MessageReader(number(self.sid)+number(307)+b'\0'))
        self.assertEqual(self.game.progression.snapshot(),before)

    def test_catalog_retains_missing_metadata_and_does_not_invent_values(self):
        self.assertEqual(DATA['referenced_skills'],233)
        self.assertFalse(DATA['skills']['4904']['metadata_present'])
        self.assertFalse(DATA['skills']['4907']['metadata_present'])
        self.assertTrue(DATA['skills']['307']['numerical_parameters_from_server'])
        self.assertFalse(any(r['complete_effect_verified'] for r in DATA['skills'].values()))

    def test_ice_seal_keeps_verified_constraints_without_enabling_the_cast(self):
        from native946_guardian_magic import prepared

        rule=DATA['skills']['4151']
        limits=rule['verified_constraints']
        self.assertEqual((limits['success_rate_cap_percent'],limits['minimum_mana_percent']), (70,50))
        self.assertEqual(limits['mana_cost'],'all_current_mana')
        self.assertEqual((limits['target_scope'],limits['target_scope_source_item']),('multiple_enemies',46757))
        self.assertFalse(rule['complete_effect_verified'])
        pet=dict(innate_skills=[dict(stunt_id=4151)],mp=500,mpmax=500)
        with self.assertRaisesRegex(ValueError,'尚待核实'):
            prepared(pet,4151)
        self.assertEqual(pet['mp'],500)

if __name__=='__main__':unittest.main()
