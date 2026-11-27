"""All species birth skills reach native lists without inventing progression."""
from unittest.mock import patch
import unittest

from native946_codec import packet
from native946_growth import DATA as GROWTH
from native946_guardian_innate import records,SKILLS
from native946_guardian_magic import prepared
from native946_login import MessageReader
from native946_summons import Summons
import test_native946_skillbooks as basic
from test_native946_skillbooks import decode_record


class InnateTableTests(unittest.TestCase):
    def test_every_client_species_has_its_own_original_birth_skill(self):
        seen=set()
        for kind,meta in GROWTH['summon_species'].items():
            with self.subTest(kind=kind):
                entries=records(dict(type=int(kind)))
                self.assertEqual([r['stunt_id'] for r in entries],meta['sk0'])
                self.assertEqual([r['position'] for r in entries],list(range(1,len(entries)+1)))
                for entry in entries:
                    seen.add(entry['stunt_id'])
                    raw=packet(722,['number',('array','stunt_info')],[100001,[entry]])
                    reader=MessageReader(raw[3:]);self.assertEqual(reader.integer(),100001)
                    self.assertEqual(reader.take(1),b'\1')
                    self.assertEqual(decode_record(reader,'stunt_info'),entry);reader.finish()
                    self.assertNotIn('{',entry['desc'])
                    self.assertNotRegex(entry['desc'],r'%[0-9.]*[dfg]')
                    self.assertEqual(entry['cost'],'')
                    self.assertEqual(entry['grade'],-1)
        self.assertEqual(len(GROWTH['summon_species']),391)
        self.assertEqual(seen,set(SKILLS))
        self.assertEqual(len(seen),73)

    def test_snapshot_species_has_only_its_birth_skill_not_future_stages(self):
        entry,=records(dict(type=3374))
        self.assertEqual((entry['stunt_id'],entry['name'],entry['position']),(4906,'太岁震铃',1))
        self.assertNotIn(4908,[r['stunt_id'] for r in records(dict(type=3374))])


class InnateIntegrationTests(unittest.TestCase):
    setUp=basic.BasicBookTests.setUp
    item=basic.BasicBookTests.item
    send=basic.BasicBookTests.send

    def test_existing_and_new_guardians_show_skills_without_saved_book_rows(self):
        sid=self.s.acquire(3374,'innate:fixture')
        for ident,skill in ((self.sid,307),(sid,4906)):
            before=self.s.get(ident)
            for summons in (self.s,Summons(self.p)):
                wire=self.send(761,ident) if summons is self.s else summons.stunt_packet(ident)
                reader=MessageReader(wire[3:]);self.assertEqual(reader.integer(),ident)
                self.assertEqual(reader.take(1),b'\1')
                entry=decode_record(reader,'stunt_info')
                self.assertEqual((entry['stunt_id'],entry['position']),(skill,1))
            self.assertEqual(self.s.get(ident),before)
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_guardian_books').fetchone()[0],0)

    def test_legacy_duplicate_keeps_trained_record_and_restores_birth_on_drop(self):
        with self.p.transaction() as db:
            db.execute('INSERT INTO native_guardian_books(summon_id,skill,position,level,tier) VALUES(?,307,8,20,1)',(self.sid,))
        entries=self.b.records(self.sid)
        self.assertEqual([(r['stunt_id'],r['position'],r['grade']) for r in entries],[(307,8,20)])
        self.send(756,self.sid,307)
        entry,=self.b.records(self.sid)
        self.assertEqual((entry['stunt_id'],entry['position'],entry['grade']),(307,1,-1))

    def test_invalid_innate_training_and_drop_preserve_resources(self):
        sid=self.s.acquire(3374,'innate:pending')
        before=self.p.snapshot();pets=self.s.snapshot()
        for ident,skill in ((self.sid,4906),(sid,307)):
            for mid in (754,756):
                self.send(mid,ident,skill)
                self.assertIn('没有这个特技',self.g.last_event['reason'])
                self.assertEqual(self.p.snapshot(),before)
                self.assertEqual(self.s.snapshot(),pets)

    def test_innate_never_borrows_the_existing_book_combat_formula(self):
        pet=self.s.get(self.sid)
        with patch('native946_guardian_magic.values',side_effect=AssertionError('unverified formula')):
            with self.assertRaisesRegex(ValueError,'尚待核实'):
                prepared(pet,307)
        self.assertEqual(self.s.get(self.sid),pet)


if __name__=='__main__':unittest.main()
