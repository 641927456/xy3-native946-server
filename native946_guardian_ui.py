"""Owned guardian UI queries and labels, using the native 946 consumers."""
import json,re
from pathlib import Path
from native946_codec import packet
from native946_guardian_jade import REQUESTS as CULTIVATION_REQUESTS
REFERENCE_WIRE_VERSION=13

DATA=json.loads(Path(__file__).with_name('native946_guardian_ui_data.json').read_text('utf8'))
PENDING={int(k):v for k,v in DATA['pending'].items() if int(k) not in CULTIVATION_REQUESTS}
DETAIL_REQUESTS={3148,3149,3150}
WRITES={4499,4500,4502}
REQUESTS={727,759,3024,3305,3401,4501,*WRITES,*PENDING}
DEFAULT_TAGS={1:{1:'物理',2:'法术',3:'高速',4:'血辅'},2:{},3:{}}


def schema(db):
    from native946_sixiang import schema as sixiang_schema
    sixiang_schema(db)
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_mark_names (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      category INTEGER NOT NULL CHECK(category IN (2,3)),slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 12),
      name TEXT NOT NULL,PRIMARY KEY(character_id,category,slot),UNIQUE(character_id,category,name))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_marks (
      summon_id INTEGER PRIMARY KEY REFERENCES summons(id) ON DELETE CASCADE,marks TEXT NOT NULL)''')


def tag_names(db,cid):
    result={k:dict(v) for k,v in DEFAULT_TAGS.items()}
    for r in db.execute('SELECT category,slot,name FROM native_guardian_mark_names WHERE character_id=?',(cid,)):
        result[r['category']][r['slot']]=r['name']
    return result


def marks(db,sid):
    row=db.execute('SELECT marks FROM native_guardian_marks WHERE summon_id=?',(sid,)).fetchone()
    return json.loads(row[0]) if row else ['','','']


def parse_marks(text):
    """Only three indexed or sequential Chinese label literals; never Lua code."""
    if isinstance(text,str):text=text.encode('gb18030')
    if not isinstance(text,bytes) or len(text)>512 or not text.strip().startswith(b'{') or not text.strip().endswith(b'}'):
        raise ValueError('守护标签格式无效。')
    body=text.strip()[1:-1];at=0;result=['','',''];used=set();implicit=1
    pattern=re.compile(rb'''\s*(?:\[\s*([1-3])\s*\]\s*=\s*)?(["'])''')
    while at<len(body) and body[at:].strip():
        match=pattern.match(body,at)
        if not match:raise ValueError('守护标签只接受原生标签文本。')
        index=int(match[1]) if match[1] else implicit
        if not match[1]:implicit+=1
        if index not in (1,2,3) or index in used:raise ValueError('守护标签类别重复或越界。')
        quote=match[2][0];at=match.end();value=bytearray()
        while at<len(body) and body[at]!=quote:
            char=body[at];at+=1
            if char==92:
                if at>=len(body):raise ValueError('守护标签转义不完整。')
                if 48<=body[at]<=57:
                    end=at
                    while end<len(body) and end-at<3 and 48<=body[end]<=57:end+=1
                    char=int(body[at:end]);at=end
                    if char>255:raise ValueError('守护标签转义越界。')
                elif body[at] in (34,39,92):char=body[at];at+=1
                else:raise ValueError('守护标签转义无效。')
            value.append(char)
        if at>=len(body):raise ValueError('守护标签引号不完整。')
        at+=1
        try:label=value.decode('gb18030')
        except UnicodeDecodeError:raise ValueError('守护标签编码无效。') from None
        if not re.fullmatch(r'[\u4e00-\u9fff]{0,2}',label):raise ValueError('标签仅支持一至两个汉字。')
        used.add(index);result[index-1]=label
        while at<len(body) and body[at:at+1].isspace():at+=1
        if at<len(body):
            if body[at] not in (44,59):raise ValueError('守护标签分隔符无效。')
            at+=1
    return result


def label_table_wire(value):
    # Lua 5.1 parses bytes. A GB18030 trail byte can itself be a backslash;
    # decimal byte escapes also cover these labels without changing their text.
    if isinstance(value,str):return '"'+''.join('\\'+f'{b:03d}' for b in value.encode('gb18030'))+'"'
    return '{'+','.join('['+str(k)+']='+label_table_wire(v) for k,v in value.items())+'}'


def mark_wire(values):
    return label_table_wire({i+1:v for i,v in enumerate(values) if v})


def carry_label(rank):
    rebirth,level=divmod(rank,1000)
    return (str(rebirth)+'转' if rebirth else '')+str(level)+'级'


class GuardianUI:
    def __init__(self,game):self.g=game;self.p=game.progression;self.s=game.summons

    def tags_packet(self):
        with self.p.transaction(write=False) as db:values=tag_names(db,self.p.cid)
        return packet(4498,['string'],[label_table_wire(values)])

    def information(self,sid):
        from native946_growth import species
        from native946_neidan_legacy import LegacyNeidan
        s=self.s.get(sid);native=species(s['type']);base=s['base_values']
        data=self.s.info_record(s)
        # This viewing protocol uses 1/10000 growth and 1/100 percent units;
        # c_summon uses 1/1000 for both growth and initial-value ratios.
        data.update(Grow=s['growth']*10,RealGrow=s['growth']*10,HighGrow=s['high_growth'],
            Hp=s['hpmax'],Mp=s['mpmax'],LongguNum=s['longgu'],RecordId=sid,Amity=s['amity'],
            Color1=s['color1'],Color2=s['color2'],Color3=s['color3'],WeaponColor=s['weapon_color'],
            Wildness=int(s['grant_key'].startswith('capture:')))
        for i,(label,field,maximum) in enumerate([('Hp','hp','hp_max'),('Mp','mp','mp_max'),
                                                ('AttAll','attack','att_max'),('DodAll','speed','dod_max')]):
            data['Base'+label]=base[i]*s['initial_'+field+'_adj']//1000
            data['Base'+label+'Max']=base[i]*native[maximum]//1000
            data['Perc'+{'Hp':'Hp','Mp':'Mp','AttAll':'Att','DodAll':'Dod'}[label]+'Adj']=s['initial_'+field+'_adj']*10
        pills=LegacyNeidan(self.s).records(sid)
        from native946_skillbooks import SkillBooks
        stunts,empty=SkillBooks(self.s).list_state(sid)
        return packet(684,['number','summon_info',('array','neidan_info'),('array','stunt_info'),
            ('array','stunt_pos_empty'),'string','string','string'],[sid,data,pills,stunts,empty,'{}','{}',''])

    def stunt_catalogue(self,sid):
        from native946_guardian_innate import learning_slots
        slots=learning_slots(self.s.get(sid))
        skills=list(dict.fromkeys(skill for choices in slots.values() for skill in choices))
        return packet(720,['number',('array','number')],[sid,skills])

    def receive(self,mid,r):
        if mid in DETAIL_REQUESTS:
            from native946_sixiang import Sixiang
            return Sixiang(self.g).receive(mid,r)
        if mid in PENDING:
            contract=PENDING[mid]
            for name,kind,*array in contract['fields']:
                count=r.take(1)[0] if array else 1
                if count>32:raise ValueError('守护请求的条目过多。')
                for _ in range(count):
                    value=r.integer() if kind=='number' else r.string()
                    if kind=='number' and 'summon' in name.lower() and 'id' in name.lower():self.s.get(value)
            r.finish()
            raise ValueError(contract['label']+'的946原版规则尚未接通，本次未执行，经验、金钱和物品未扣除。')
        if mid==3401:
            r.finish();return packet(3400,['number'],[1])  # Local service permits owned-guardian following.
        if mid==4501:
            r.finish();return self.tags_packet()
        if mid in (4499,4500):
            name=r.string().decode('gb18030') if mid==4499 else ''
            category,slot=r.integer(),r.integer();r.finish()
            if self.g.battle is not None:raise ValueError('战斗中不能编辑守护标签。')
            if category not in (2,3) or not 1<=slot<=12:raise ValueError('原生固定标签不能修改，或标签位置无效。')
            if mid==4499 and not re.fullmatch(r'[\u4e00-\u9fff]{1,2}',name):raise ValueError('标签仅支持一至两个汉字。')
            with self.p.transaction() as db:
                names=tag_names(db,self.p.cid);old=names[category].get(slot,'')
                if name and any(v==name and k!=slot for k,v in names[category].items()):raise ValueError('已有同名标签。')
                if name:db.execute('INSERT INTO native_guardian_mark_names VALUES(?,?,?,?) ON CONFLICT(character_id,category,slot) DO UPDATE SET name=excluded.name',(self.p.cid,category,slot,name))
                else:db.execute('DELETE FROM native_guardian_mark_names WHERE character_id=? AND category=? AND slot=?',(self.p.cid,category,slot))
                if old and old!=name:
                    from native946_guardian_chamber import AVAILABLE
                    for row in db.execute('SELECT m.summon_id,m.marks FROM native_guardian_marks m '
                                          'JOIN summons ON summons.id=m.summon_id WHERE summons.character_id=? AND '
                                          + AVAILABLE, (self.p.cid,)).fetchall():
                        values=json.loads(row['marks'])
                        if values[category-1]==old:
                            values[category-1]=name
                            db.execute('UPDATE native_guardian_marks SET marks=? WHERE summon_id=?',(json.dumps(values,ensure_ascii=False),row['summon_id']))
            return self.g.defer_packets(lambda:(self.tags_packet(),*self.s.sync()))
        sid=r.integer()
        text=r.string() if mid==4502 else None
        r.finish()
        if mid==759:return self.stunt_catalogue(sid)
        self.s.get(sid)
        if mid==727:return self.information(sid)
        if mid==3024:return packet(3019,['number']*3,[sid,-1,-1])
        if mid==3305:return packet(3304,['number']*5+[('array','number'),'number'],[0,0,0,0,0,[0]*5,sid])
        if mid==4502:
            if self.g.battle is not None:raise ValueError('战斗中不能修改守护标签。')
            values=parse_marks(text)
            with self.p.transaction() as db:
                names=tag_names(db,self.p.cid)
                if any(value and value not in names[i+1].values() for i,value in enumerate(values)):raise ValueError('请选择当前类别中存在的守护标签。')
                if any(values):db.execute('INSERT INTO native_guardian_marks VALUES(?,?) ON CONFLICT(summon_id) DO UPDATE SET marks=excluded.marks',(sid,json.dumps(values,ensure_ascii=False)))
                else:db.execute('DELETE FROM native_guardian_marks WHERE summon_id=?',(sid,))
            return self.g.defer_packets(lambda:self.s.sync())
        raise ValueError('守护界面请求无效。')
