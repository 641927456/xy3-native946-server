"""Native viewing records, persisted labels, and no-charge unavailable actions."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys,unittest
sys.path.insert(0,str(Path(__file__).parent/'.test-deps'))
from hypothesis import given,settings,strategies as st
from lupa.lua51 import LuaRuntime
from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_gameplay import NativeGameplay
from native946_guardian_ui import GuardianUI,PENDING,parse_marks,mark_wire
from native946_codec import SCHEMAS,packet,number
from native946_login import MessageReader
from native946_summons import Summons


def read_record(reader,name):
    result={}
    for key,kind in SCHEMAS[name]:
        result[key]=(reader.string().decode('gb18030') if kind=='string' else
                     reader.take(1)[0] if kind=='byte' else reader.integer())
    return result


class GuardianUITests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'ui.sqlite3'
        LocalAccounts(path).register(dict(username='guardian_ui',password='fixture-only-password'))
        self.characters=Characters(path);self.char=self.characters.create('guardian_ui','界面验证',702)
        self.g=NativeGameplay(self.char,self.characters,lambda:());self.s=self.g.summons
        self.sid=self.s.claim_starter();self.ui=GuardianUI(self.g)

    def send(self,mid,types,values):return self.g.receive(mid,MessageReader(packet(mid,types,values)[3:]))

    def test_info_button_opens_current_native_information_consumer(self):
        result=self.send(727,['number'],[self.sid]);self.assertEqual(result[:3],packet(684,[],[]))
        r=MessageReader(result[3:]);self.assertEqual(r.integer(),self.sid);data=read_record(r,'summon_info')
        s=self.s.get(self.sid)
        self.assertEqual((data['Name'],data['Hp'],data['Mp']),(s['name'],s['hpmax'],s['mpmax']))
        self.assertEqual(data['SummonScore'], s['score'])
        self.assertEqual((data['Grow'],data['RealGrow']),(10200,10200))
        self.assertEqual((data['BaseHp'],data['PercHpAdj']),(48,3000))
        self.assertEqual(data['RecordId'],self.sid)
        self.assertEqual(r.take(1),b'\0')
        self.assertEqual(r.take(1),b'\1')
        innate=read_record(r,'stunt_info')
        self.assertEqual((innate['stunt_id'],innate['position'],innate['name']),(307,1,'一箭穿心'))
        self.assertEqual(r.take(1),b'\x0b')
        self.assertEqual([read_record(r,'stunt_pos_empty') for _ in range(11)],
                         [dict(can_do=0,level=1 if 8<=i<=11 else 99 if i==12 else 0,position=i)
                          for i in (*range(2,6),*range(7,14))])
        self.assertEqual([r.string() for _ in range(3)],[b'{}',b'{}',b'']);r.finish()

    def test_special_skill_grid_reports_locked_and_unlearned_slots(self):
        sid=self.s.acquire(3374,'special-grid:fixture')
        with self.g.progression.transaction() as db:
            db.execute('UPDATE summons SET level=52 WHERE id=?',(sid,))
        before=self.s.get(sid)
        result=self.send(761,['number'],[sid])
        self.assertEqual(result[:3],packet(722,[],[]))
        r=MessageReader(result[3:]);self.assertEqual(r.integer(),sid)
        self.assertEqual(r.take(1),b'\1')
        skill=read_record(r,'stunt_info')
        self.assertEqual((skill['position'],skill['stunt_id']),(1,4906))
        empty={row['position']:(row['can_do'],row['level']) for row in
               (read_record(r,'stunt_pos_empty') for _ in range(r.take(1)[0]))}
        self.assertEqual(empty,{2:(1,0),3:(0,0),4:(0,0),5:(0,0),7:(1,0),
                                8:(0,1),9:(0,1),10:(0,1),11:(0,1),12:(0,99),13:(0,0)})
        self.assertEqual(r.take(2),b'\0\0')
        self.assertEqual(r.integer(),0);r.finish()
        self.assertEqual(self.s.get(sid),before)

        with self.g.progression.transaction() as db:
            db.execute('UPDATE summons SET rebirth=2,level=120,sanctification=1 WHERE id=?',(sid,))
        self.send(763,['number','number'],[sid,4908])
        from native946_skillbooks import SkillBooks
        skills,slots=SkillBooks(self.s).list_state(sid)
        self.assertEqual([(row['position'],row['stunt_id']) for row in skills],[(1,4906),(5,4908)])
        self.assertNotIn(5,{row['position'] for row in slots})
        self.assertIn(dict(position=12,level=99,can_do=1),slots)

    def test_resistance_fields_use_view_units_and_correct_trade_policy(self):
        r=MessageReader(self.s.resistance_packet(self.sid)[3:]);self.assertEqual(r.integer(),self.sid)
        data=read_record(r,'summon_resist');r.finish()
        self.assertEqual((data['ShowGrow'],data['NoTrade'],data['Kind'],data['CanTakeGrade']),
                         (10200,1,'新手守护','0级'))

    def test_guardian_queries_return_complete_native_empty_state_packets(self):
        before=self.s.snapshot()
        progression=self.g.progression.snapshot()
        for mid,expected in [(3024,packet(3019,['number']*3,[self.sid,-1,-1])),
            (3305,packet(3304,['number']*5+[('array','number'),'number'],[0,0,0,0,0,[0]*5,self.sid])),
            (3978,packet(3977,['number',('array','number')],[self.sid,[]]))]:
            self.assertEqual(self.send(mid,['number'],[self.sid]),expected)
        pet=next(row for row in before if row['id']==self.sid)
        from native946_growth import species
        slots=[dict(type=0,desc='') for _ in range(9)]
        stone_panel=packet(948,['number','string',*(['number']*6),('array','new_stone_info'),'number','byte'],
                           [self.sid,pet['name'],pet['type'],species(pet['type'])['race'],0,0,0,10,slots,0,0])
        self.assertEqual(self.send(952,['number'],[self.sid]),(stone_panel,))
        self.assertEqual(self.send(3401,[],[]),packet(3400,['number'],[1]))
        self.assertEqual(self.s.snapshot(),before)
        self.assertEqual(self.g.progression.snapshot(),progression)

    def test_labels_persist_rename_delete_and_reopen_without_touching_stats(self):
        self.send(4499,['string','number','number'],['内丹',2,1])
        self.send(4502,['number','string'],[self.sid,'{[2]="内丹",[1]="物理"}'])
        stored=Summons(self.g.progression).get(self.sid)
        self.assertEqual(parse_marks(stored['mark']),['物理','内丹',''])
        self.send(4499,['string','number','number'],['法宠',2,1])
        self.assertEqual(parse_marks(self.s.get(self.sid)['mark']),['物理','法宠',''])
        self.send(4500,['number','number'],[2,1])
        self.assertEqual(parse_marks(self.s.get(self.sid)['mark']),['物理','',''])
        self.send(4502,['number','string'],[self.sid,'{}'])
        self.assertEqual(self.s.get(self.sid)['mark'],'{}')
        self.assertEqual(self.s.get(self.sid)['level'],stored['level'])

    def test_label_rejections_are_atomic_and_check_ownership(self):
        before=self.s.snapshot()
        invalid=[(4502,['number','string'],[self.sid,'{[1]="未知"}']),
                 (4502,['number','string'],[self.sid+1,'{}']),
                 (4499,['string','number','number'],['标签',1,1]),
                 (4499,['string','number','number'],['#R',2,1])]
        for mid,types,values in invalid:
            self.send(mid,types,values);self.assertEqual(self.g.last_event['action'],'action_rejected')
            self.assertEqual(self.s.snapshot(),before)
        self.g.receive(4499,MessageReader(packet(4499,['string','number','number'],['内丹',2,1])[3:]+b'X'))
        self.assertEqual(self.g.last_event['action'],'action_rejected')
        self.assertNotIn('内丹',self.ui.tags_packet().decode('gb18030',errors='replace'))

    def test_unknown_cultivation_and_stone_rules_never_consume_resources(self):
        before=self.g.progression.snapshot();pet=self.s.get(self.sid)
        for mid,contract in PENDING.items():
            types=[];values=[]
            for name,kind,*array in contract['fields']:
                types.append(('array',kind) if array else kind)
                values.append([] if array else self.sid if 'summon' in name.lower() and 'id' in name.lower() else 0)
            self.send(mid,types,values)
            self.assertEqual(self.g.last_event['action'],'action_rejected',mid)
            self.assertTrue(self.g.last_event['reason'],mid)
        self.assertEqual(self.g.progression.snapshot(),before);self.assertEqual(self.s.get(self.sid),pet)


LABEL=st.text(alphabet=st.characters(min_codepoint=0x4e00,max_codepoint=0x9fff),min_size=0,max_size=2)
class MarkLiteralProperties(unittest.TestCase):
    @settings(max_examples=150,deadline=None)
    @given(st.lists(LABEL,min_size=3,max_size=3))
    def test_server_wire_roundtrips_and_matches_independent_lua_reader(self,values):
        encoded=mark_wire(values);self.assertEqual(parse_marks(encoded),values)
        lua=LuaRuntime(encoding='gb18030')
        read=lua.eval('function(s) local f=assert(loadstring("return "..s));setfenv(f,{});return f() end')
        result=read(encoded)
        self.assertEqual([result[i] or '' for i in range(1,4)],values)

    @settings(max_examples=150,deadline=None)
    @given(st.lists(LABEL,min_size=3,max_size=3),st.permutations([1,2,3]))
    def test_index_order_and_sparse_slots_do_not_change_labels(self,values,order):
        lua=LuaRuntime(encoding='gb18030');quote=lua.eval('function(s) return string.format("%q",s) end')
        encoded='{'+','.join(f'[{i}]='+quote(values[i-1]) for i in order)+'}'
        self.assertEqual(parse_marks(encoded),values)

    def test_code_trailing_data_duplicate_categories_and_oversized_labels_rejected(self):
        for value in ['{};error("x")','{[1]="物理",[1]="法术"}','{[4]="物理"}',
                      '{"三个字"}','{(function()end)()}','{[1]="#R"}','{"物理" "法术"}']:
            with self.assertRaises(ValueError):parse_marks(value)


if __name__=='__main__':unittest.main()
