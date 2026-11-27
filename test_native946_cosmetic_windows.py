"""Wardrobe equip/switch/remove must update appearance without opening windows."""
from collections import defaultdict
from pathlib import Path
import sys,unittest

from native946_codec import number
from native946_login import MessageReader,string_field
from native946_cosmetics import Cosmetics,DATA,WEAR_PAGE
from native946_game_login import NativeGameLogin,message_id
from native946_sessions import LEASES
import test_native946_npcs as fixtures

ROOT=Path(__file__).parent
# Native inventory updates plus silent appearance/collection/frame updates.
# Newly added replies require an explicit check of their client UI consumer.
SILENT_WARDROBE_MESSAGES={780,786,789,1620,2501,1621,1622,818,695,696,694,824,5414,5031}
OBSERVATIONS=[]


def message_ids(reply):
    replies=reply if isinstance(reply,(tuple,list)) else (reply,)
    return {p[0] or int.from_bytes(p[1:3],'little') for p in replies}


def categories(sex):
    groups=defaultdict(list)
    for kind,meta in sorted(DATA['wearables'].items(),key=lambda pair:int(pair[0])):
        if kind in DATA['goods'] and meta.get('sex',sex)==sex:
            groups[(meta['kind'],meta['slot'])].append(int(kind))
    return groups


class WardrobeWindowTests(unittest.TestCase):
    setUp=fixtures.NpcGameplayTests.setUp

    def send(self,kind,*args):
        return self.game.receive(kind,MessageReader(b''.join(number(a) for a in args)))

    def rows(self):return {i['id']:i for i in self.game.progression.snapshot()['items']}

    def add(self,kind):
        previous=set(self.rows())
        with self.game.progression.transaction() as db:Cosmetics(self.game)._add(db,kind)
        return next(item for iid,item in self.rows().items() if iid not in previous)

    def male(self):
        row=self.characters.create('npc_owner','外观检查男',113)
        login=NativeGameLogin(544,None,number,None,self.characters);login.username='npc_owner'
        self.addCleanup(lambda:LEASES.release_port(self.path,544))
        login.receive(message_id(915)+number(row['id']));self.game=login.gameplay

    def assert_silent(self,reply):
        replies=reply if isinstance(reply,tuple) else (reply,)
        ids=message_ids(reply)
        self.assertIn(2501,ids,'The native actor appearance must still refresh')
        self.assertLessEqual(ids,SILENT_WARDROBE_MESSAGES,
                             'Unexpected wardrobe reply needs its native window behavior reviewed')
        self.assertNotIn(4822,ids)
        for p in replies:
            if int.from_bytes(p[1:3],'little')==824:
                self.assertEqual(MessageReader(p[3:]).integer(),0,'Collection update must use silent mode')
        return sorted(ids)

    def exercise(self,sex,drag):
        if sex==1:self.male()
        groups=categories(sex)
        self.assertEqual(len({kind for kind,slot in groups}),15)
        self.assertEqual(len(groups),16)  # QIZHI has two real wearable slots.
        for (category,slot),kinds in groups.items():
            with self.subTest(sex=sex,category=category,slot=slot,drag=drag):
                self.assertGreaterEqual(len(kinds),2)
                first,second=self.add(kinds[0]),self.add(kinds[1])
                steps=[]
                for operation,item in [('equip',first),('switch',second),('remove',second)]:
                    current=self.rows()[item['id']]
                    if drag:
                        destination,target_slot=(25,-1) if operation=='remove' else (WEAR_PAGE,slot)
                        # Test both native drag/merge and swap entry points.
                        reply=self.send(803 if operation=='equip' else 795,
                                        current['page'],current['slot'],destination,target_slot)
                    else:reply=self.send(807,0,item['id'])
                    ids=self.assert_silent(reply)
                    current=self.rows()[item['id']]
                    self.assertEqual(current['page']==WEAR_PAGE,operation!='remove')
                    if operation!='remove':self.assertEqual(current['slot'],slot)
                    if operation=='switch':self.assertNotEqual(self.rows()[first['id']]['page'],WEAR_PAGE)
                    steps.append(dict(operation=operation,reply_ids=ids))
                OBSERVATIONS.append(dict(sex=sex,category=category,slot=slot,
                                         entry='drag_803_795' if drag else 'right_click_807',
                                         item_types=[first['type'],second['type']],steps=steps))

    def test_female_right_click_all_categories(self):self.exercise(2,False)
    def test_male_right_click_all_categories(self):self.exercise(1,False)
    def test_female_drag_all_categories(self):self.exercise(2,True)
    def test_male_drag_all_categories(self):self.exercise(1,True)

    def test_whole_catalog_has_no_skill_or_early_dispatch_type_collision(self):
        from native946_inventory_features import ITEM_TYPES
        from native946_guardian_acquisition import ITEM_TYPES as GUARDIAN_TYPES
        from native946_guardian_lottery import EXPERIENCE_ITEM
        wearables={int(kind) for kind in DATA['wearables'] if kind in DATA['goods']}
        other_routes=set(ITEM_TYPES)|set(GUARDIAN_TYPES)|{EXPERIENCE_ITEM}|{int(kind) for kind in DATA['skills']}
        self.assertTrue(wearables)
        self.assertFalse(wearables&other_routes,'A wearable must not dispatch to another item-use window')

    def test_avatar_frame_activation_selection_and_removal_are_silent(self):
        item=self.add(48278)
        reply=self.send(807,0,item['id'])
        ids=message_ids(reply)
        self.assertLessEqual(ids,SILENT_WARDROBE_MESSAGES|{98})  # Expected system-chat success text.
        self.assertIn(5031,ids)
        self.assertEqual(self.game.progression.attribute_values()['IconFrame'],1160)
        for frame in [0,1160,0]:
            reply=self.send(5032,frame)
            self.assertEqual(message_ids(reply),{695,694,5031})
            self.assertEqual(self.game.progression.attribute_values()['IconFrame'],frame)

    def test_child_closet_switch_and_remove_do_not_open_child_or_skill_window(self):
        import test_native946_mall_uses as mall_fixtures
        children=[mall_fixtures.MallUseTests.add_child(self,shape) for shape in [401,402]]
        items=[self.add(kind) for kind in [56537,56538]]
        for child,item in zip(children,items):
            reply=self.send(5261,item['id'],child,item['type'])
            ids=message_ids(reply)
            self.assertIn(5265,ids)
            self.assertFalse(ids&{4822,5262,3993,3658})
        for child,item in zip(children,items):
            removed=self.send(5264,child,1)
            self.assertIn(5265,message_ids(removed))
            self.assertFalse(message_ids(removed)&{4822,5262,3993,3658})
            reply=self.game.receive(5263,MessageReader(number(child)+string_field(str(item['id']))+number(1)))
            ids=message_ids(reply)
            self.assertIn(5265,ids)
            self.assertFalse(ids&{4822,5262,3993,3658})
        reply=self.send(5264,children[0],1)
        ids=message_ids(reply)
        self.assertIn(5265,ids)
        self.assertFalse(ids&{4822,5262,3993,3658})


class NativeWindowConsumerTests(unittest.TestCase):
    def test_real_silent_consumers_with_wardrobe_closed_and_open(self):
        sys.path.insert(0,str(ROOT/'analysis/wardrobe-window-audit-20261126'))
        from native_consumers import validate
        result=validate()
        self.assertTrue(result['passed'])
        self.assertEqual(result['normal_refresh_opened_windows'],[])
        self.assertEqual(result['positive_control_opened_windows'],['dlg_skill_skin_panel'])


if __name__=='__main__':unittest.main()
