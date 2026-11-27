"""Chinese item notifications must reach the original text renderer."""
import ast
from pathlib import Path
import unittest

from native946_codec import packet, text_notice
from native946_login import MessageReader
from test_native946_npc_services import mid


class TextNoticeTests(unittest.TestCase):
    def test_native_system_chat_contract_preserves_chinese_and_markup(self):
        for text in ('道具使用成功。', '流光溢彩：积分+50', '#G获得奖励#n#r第二行', ''):
            with self.subTest(text=text):
                payload = text_notice(text)
                self.assertEqual(mid(payload), 98)
                reader = MessageReader(payload[1:])
                self.assertEqual(reader.string().decode('gb18030'), text)
                self.assertEqual(reader.string(), b'')
                self.assertEqual((reader.integer(), reader.integer()), (1, 0))
                reader.finish()

    def test_graphic_protocol_remains_available_for_intentional_sprite_text(self):
        payload = packet(97, ['string', 'number'], ['123', 0])
        self.assertEqual(mid(payload), 97)

    def test_production_plain_text_cannot_reenter_sprite_notification(self):
        offenders = []
        for path in Path(__file__).parent.glob('native946*.py'):
            for node in ast.walk(ast.parse(path.read_text('utf8'))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'packet' and node.args
                        and isinstance(node.args[0], ast.Constant) and node.args[0].value == 97):
                    offenders.append((path.name, node.lineno))
        self.assertEqual(offenders, [])

    def test_liuguang_use_consumes_once_adds_exact_points_and_returns_text(self):
        from test_native946_mall_uses import MallUseTests
        fixture = MallUseTests(methodName='test_emoji_native_list_and_duplicate_permanent_preserved')
        fixture.setUp()
        try:
            points = 0
            for kind, gain in ((55634, 50), (55635, 800), (65634, 50), (65635, 800)):
                item = fixture.add(kind)
                reply = fixture.send(807, 0, item['id'])
                self.assertNotIn(97, [mid(p) for p in reply])
                notices = [p for p in reply if mid(p) == 98]
                self.assertEqual(len(notices), 1)
                self.assertEqual(MessageReader(notices[0][1:]).string().decode('gb18030'), '道具使用成功。')
                points += gain
                self.assertEqual(fixture.game.progression.attribute_values()['TimeCredit'], points)
                self.assertFalse(any(row['id'] == item['id'] for row in fixture.rows()))
                self.assertEqual(mid(fixture.send(807, 0, item['id'])), 98)
                self.assertEqual(fixture.game.progression.attribute_values()['TimeCredit'], points)
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
