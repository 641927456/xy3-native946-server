"""946's notification descriptor has a mandatory optimization number."""
import unittest
from native946_codec import packet
from native946_login import MessageReader

class NoticeWireTests(unittest.TestCase):
    def test_legacy_and_explicit_forms_both_have_exactly_two_fields(self):
        for text in ('', '购买成功。', '系统提示 #r第二行'):
            with self.subTest(text=text):
                legacy=packet(97,['string'],[text]);explicit=packet(97,['string','number'],[text,0])
                self.assertEqual(legacy,explicit)
                reader=MessageReader(legacy[1:]);self.assertEqual(reader.string().decode('gb18030'),text)
                self.assertEqual(reader.integer(),0);reader.finish()
    def test_explicit_optimization_flag_is_not_appended_twice(self):
        reader=MessageReader(packet(97,['string','number'],['原生提示',1])[1:])
        reader.string();self.assertEqual(reader.integer(),1);reader.finish()
    def test_single_field_notice_with_different_id_keeps_its_own_contract(self):
        reader=MessageReader(packet(99,['string'],['普通通知'])[1:])
        reader.string();reader.finish()
        with self.assertRaises(ValueError):packet(97,['string'],[])

if __name__=='__main__':unittest.main()
