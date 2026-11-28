"""Classic-946 workshop membership, native recipes and verified reference production."""
from pathlib import Path
from time import monotonic
import json

REFERENCE_WIRE_VERSION=10

from native946_codec import packet
from native946_codec import text_notice

DATA=json.loads(Path(__file__).with_name('native946_workshop_data.json').read_text('utf8'))
ACTORS={(row['scene'],row[key]):(row,'leader' if key=='leader' else 'tool')
        for row in DATA['workshops'].values() for key in ('leader','tool')}
TASK_GIVERS={(r['scene'],r['npc']):r for r in DATA['task_givers']}


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS native_workshops (
      character_id INTEGER NOT NULL REFERENCES characters(id),
      workshop_id INTEGER NOT NULL CHECK(workshop_id BETWEEN 1 AND 7),
      rank INTEGER NOT NULL DEFAULT 0 CHECK(rank BETWEEN 0 AND 9),
      practice INTEGER NOT NULL DEFAULT 0 CHECK(practice>=0),
      achievement INTEGER NOT NULL DEFAULT 0 CHECK(achievement>=0),
      PRIMARY KEY(character_id,workshop_id));''')


def actor(npc):return ACTORS.get((npc['scene_id'],npc['npc_id']))
def task_giver(npc):return TASK_GIVERS.get((npc['scene_id'],npc['npc_id']))


class Workshops:
    def __init__(self,game):self.g=game;self.p=game.progression

    def records(self):
        with self.p.transaction(write=False) as db:
            return [dict(Master='',Great=r['achievement'],Id=r['workshop_id'],Level=r['rank'],Skill='',Practice=r['practice'])
                    for r in db.execute('SELECT * FROM native_workshops WHERE character_id=? ORDER BY workshop_id',(self.p.cid,))]

    def info_packet(self):return packet(3596,[('array','zuofang_equip')],[self.records()])

    def open(self):
        if self.g.battle:raise ValueError('请先结束战斗再查看作坊。')
        response=self.info_packet()
        self.g.workshop_query_until=monotonic()+600
        return response

    def join(self,wid,*,from_npc=False):
        if self.g.battle:raise ValueError('战斗中不能加入作坊。')
        if type(wid) is not int or str(wid) not in DATA['workshops']:raise ValueError('不存在这个装备作坊。')
        if not from_npc and getattr(self.g,'workshop_query_until',0)<monotonic():
            raise ValueError('请先打开作坊界面。')
        row=DATA['workshops'][str(wid)]
        with self.p.transaction() as db:
            level=db.execute('SELECT level FROM characters WHERE id=?',(self.p.cid,)).fetchone()[0]
            if level<row['join_level']:raise ValueError('人物达到20级才能加入装备作坊。')
            created=db.execute('INSERT OR IGNORE INTO native_workshops(character_id,workshop_id) VALUES(?,?)',(self.p.cid,wid)).rowcount
        self.g.workshop_query_until=monotonic()+600
        return self.g.defer_packets(lambda:(packet(1183,['string'],['dlg_npc_chat']),self.info_packet(),
                    text_notice('已加入'+row['name']+'。' if created else '你已经加入'+row['name']+'。')))

    def menu(self,npc):
        entry=actor(npc)
        if entry:
            row,role=entry;wid=row['id'];choices=[('查看装备作坊',dict(kind='workshop',op='status',wid=wid)),
                                                ('查看原生配方',dict(kind='workshop',op='recipes',wid=wid)),
                                                ('了解作坊规则',dict(kind='workshop',op='rules',wid=wid))]
            if role=='leader':
                choices.insert(0,('加入'+row['name'],dict(kind='workshop',op='join',wid=wid)))
                choices.extend([('研习技艺（5000现金、1000经验）',dict(kind='workshop',op='study',wid=wid)),
                                ('提升作坊段位',dict(kind='workshop',op='advance',wid=wid))])
            choices.append(('打开生产界面',dict(kind='workshop',op='production',wid=wid)))
            choices.append(('普通装备炼化（本机）',dict(kind='workshop',op='forge_list',wid=wid)))
            return self._menu(npc['npc_id'],row['description'],choices)
        giver=task_giver(npc)
        if giver:
            return self._menu(npc['npc_id'],npc.get('desc') or giver['name'],[('了解任务规则',dict(kind='task_rules',npc=npc['npc_id']))])

    def study(self,wid,*,advance=False):
        from native946_crafting import GROWTH
        with self.p.transaction() as db:
            row=db.execute('SELECT * FROM native_workshops WHERE character_id=? AND workshop_id=?',(self.p.cid,wid)).fetchone()
            if not row:raise ValueError('请先加入对应作坊。')
            s=self.p._snapshot(db)
            if advance:
                rank=row['rank']+1
                if rank>9:raise ValueError('已经达到作坊最高段位。')
                # Retain published client stage gates where available; later
                # production stages are the explicit growth-flow extension.
                eligible=(s['level']>=20 if rank<=3 else
                          s['level']>=120 and (s['rebirth']>=2 or s['ascension']>=1) if rank==4 else
                          s['level']>=120 and s['ascension']>=1 if rank==5 else
                          s['level']>=140 and s['ascension']>=1 if rank in (6,7) else
                          s['level']>=(160 if rank==8 else 170) and s['sanctification']>=1)
                if not eligible:raise ValueError('人物阶段不足；4段需二转120、5段需飞升120、6至7段需飞升140、8段需封圣160、9段需封圣170。')
                practice=rank*GROWTH['advance_practice_per_rank'];cash=rank*rank*GROWTH['advance_cash_per_rank_squared']
                if row['practice']<practice:raise ValueError('熟练不足，需要'+str(practice)+'点熟练。')
                if s['gold']<cash:raise ValueError('现金不足，需要'+str(cash)+'现金。')
                db.execute('UPDATE characters SET gold=gold-? WHERE id=?',(cash,self.p.cid))
                db.execute('UPDATE native_workshops SET rank=?,practice=practice-? WHERE character_id=? AND workshop_id=?',(rank,practice,self.p.cid,wid))
                text='作坊已提升至'+str(rank)+'段。'
            else:
                if s['gold']<GROWTH['study_cash'] or s['experience']<GROWTH['study_experience']:raise ValueError('研习需要5000现金和1000人物经验。')
                db.execute('UPDATE characters SET gold=gold-?,experience=experience-? WHERE id=?',(GROWTH['study_cash'],GROWTH['study_experience'],self.p.cid))
                db.execute('UPDATE native_workshops SET practice=practice+? WHERE character_id=? AND workshop_id=?',(GROWTH['study_practice'],self.p.cid,wid))
                text='研习完成，作坊熟练增加100点。'
        return self.g.defer_packets(lambda:(self.info_packet(),*self.p.attribute_packets(),text_notice(text)))

    def _menu(self,nid,text,choices):
        self.g.dialog_actions=[action for label,action in choices]
        return self.g.menu(text,[label for label,action in choices],nid)

    def execute(self,nid,action):
        # The outer NPC dispatcher validates map, distance and actor kind.
        if action['kind']=='task_rules':
            giver=TASK_GIVERS.get((self.g.character['map_id'],nid))
            if not giver or action['npc']!=nid:raise ValueError('任务领取人不匹配。')
            text=giver['description']
            if giver['npc']==2089:
                text+='#r#r当前任务链每环获得通宝，满300环才有额外物品奖励。额外物品品质与累计提交物品价值有关。'
            return self.tip(giver['name'],text+'#r#r当前暂不能领取，尚未开放任务结算。')
        wid=action['wid'];row=DATA['workshops'][str(wid)]
        if action['op']=='join':
            from native946_world import world
            npc=world().npc(self.g.character['map_id'],nid)
            if actor(npc)!=(row,'leader'):raise ValueError('请找对应的坊主加入作坊。')
            return self.join(wid,from_npc=True)
        if action['op'] in ('study','advance'):
            from native946_world import world
            if actor(world().npc(self.g.character['map_id'],nid))!=(row,'leader'):raise ValueError('请找对应坊主研习技艺。')
            return self.study(wid,advance=action['op']=='advance')
        if action['op'] in ('forge_list','forge_quote','forge_confirm'):
            from native946_crafting import Crafting
            from native946_world import world
            current=actor(world().npc(self.g.character['map_id'],nid))
            if current is None or current[0]['id']!=wid:raise ValueError('当前 NPC 与炼化作坊不匹配。')
            return Crafting(self.g).workshop_forge(nid,wid,action)
        if action['op']=='status':return (packet(1183,['string'],['dlg_npc_chat']),self.open())
        if action['op']=='production':
            from native946_world import world
            npc=world().npc(self.g.character['map_id'],nid)
            if actor(npc) is None or actor(npc)[0]['id']!=wid:raise ValueError('当前 NPC 与生产作坊不匹配。')
            from native946_crafting import Crafting
            return Crafting(self.g).open(wid,from_npc=True)
        if action['op']=='rules':
            from native946_crafting import GROWTH
            return self.tip(row['name'],f'人物达到20级可加入作坊。3至9段依次开放11至17级装备加工。'
                            f'#r每次制作或重铸增加{GROWTH["practice_per_craft"]}熟练。'
                            f'研习花费{GROWTH["study_cash"]}现金和{GROWTH["study_experience"]}经验，增加{GROWTH["study_practice"]}熟练。'
                            f'#r升段消耗目标段位×{GROWTH["advance_practice_per_rank"]}熟练和目标段位平方×{GROWTH["advance_cash_per_rank_squared"]}现金。'
                            '#r4段需二转120级，5段需飞升120级，6至7段需飞升140级，8段需封圣160级，9段需封圣170级。'
                            '#r具体装备、材料及现金请查看对应配方。')
        if action['op']=='recipes':
            grades=sorted({r['equip_grade'] for r in DATA['recipes'] if r['zuofang_id']==wid})
            return self._menu(nid,'请选择装备级别：',[(f'{grade-1000}级首饰' if grade>=1000 else f'{grade}级装备',
                                dict(kind='workshop',op='grade',wid=wid,grade=grade)) for grade in grades])
        if action['op']=='grade':
            rows=[(i,r) for i,r in enumerate(DATA['recipes']) if r['zuofang_id']==wid and r['equip_grade']==action['grade']]
            return self._menu(nid,'请选择要查询的配方：',[(r['result_name'],dict(kind='workshop',op='recipe',wid=wid,index=i)) for i,r in rows])
        if action['op']=='recipe':
            recipe=DATA['recipes'][action['index']]
            if recipe['zuofang_id']!=wid:raise ValueError('配方与作坊不匹配。')
            from native946_crafting import MAKE, REFRESH, production_cash
            text=recipe['tips']+'#r#r【原装备】'+recipe['material_base_name']
            for label,key in [('打造材料','make_items'),('重铸材料','recast_items')]:
                text+='#r【'+label+'】'+('、'.join(DATA['materials'][str(t)]['name']+'×'+str(n) for t,n in recipe[key]) or '无')
            text+='#r【可用药引】'+'、'.join(DATA['materials'][str(t)]['name'] for t in recipe['medicines'])
            text+='#r【打造药引数量】'+str(recipe['make_medicine_num'])+'#r【重铸药引数量】'+str(recipe['recast_medicine_num'])
            text+=f'#r【升级现金】{production_cash(recipe,MAKE)}#r【重铸现金】{production_cash(recipe,REFRESH)}'
            text+='#r#r这是配方查询；当前不进行材料扣除或装备生成。'
            return self.tip(recipe['result_name'],text)
        raise ValueError('这个作坊选项不可用。')

    @staticmethod
    def tip(title,text):return (packet(1183,['string'],['dlg_npc_chat']),packet(227,['string','string'],[title,text]))

    def unavailable(self,operation):
        raise ValueError(operation+'的原版结算规则尚未核实，当前操作未执行，材料和金钱未扣除。')
