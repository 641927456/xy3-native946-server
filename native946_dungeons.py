"""Current original dungeon descriptions and owned native entrance navigation."""
from pathlib import Path
from time import time
import json

from native946_codec import packet,text_notice

DATA=json.loads(Path(__file__).with_name('native946_dungeon_catalog.json').read_text('utf8'))
VERIFIED_RULES=json.loads(Path(__file__).with_name('native946_dungeon_verified_rules.json').read_text('utf8'))
RECORDS={row['Index']:row for row in DATA['DungeonConfig']}
BY_NAME={row['Name']:row for row in RECORDS.values()}
ENTRANCES={int(key):value for key,value in DATA['entrances'].items()}
NPC_RECORDS={}
for key,targets in ENTRANCES.items():
    for target in targets:NPC_RECORDS.setdefault((target['scene'],target['npc']),[]).append(key)
PAGE_SIZE=7
TIANPENG=26
DAILY_ONCE_IDS=frozenset(BY_NAME[name]['Index'] for name in VERIFIED_RULES['daily_entry_revision']['names'])
PUZZLE_REQUEST=4168


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_dungeon_runs (
        instance_id TEXT NOT NULL,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        dungeon_id INTEGER NOT NULL,
        period_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('entered','completed','abandoned')),
        reset_at INTEGER NOT NULL,
        PRIMARY KEY(instance_id,character_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS native_dungeon_character_runs ON native_dungeon_runs(character_id,dungeon_id,reset_at)')
    daily_ids=','.join(map(str,sorted(DAILY_ONCE_IDS)))
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS native_dungeon_daily_entry '
               'ON native_dungeon_runs(character_id,dungeon_id,period_id) WHERE dungeon_id IN ('+daily_ids+')')
    db.execute('''CREATE TABLE IF NOT EXISTS native_tianpeng_puzzles (
        instance_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        scene_id INTEGER NOT NULL, clue TEXT NOT NULL,
        answer_a INTEGER NOT NULL CHECK(answer_a BETWEEN 1 AND 9),
        answer_b INTEGER NOT NULL CHECK(answer_b BETWEEN 1 AND 9),
        answer_c INTEGER NOT NULL CHECK(answer_c BETWEEN 1 AND 9),
        solved INTEGER NOT NULL DEFAULT 0 CHECK(solved IN (0,1)),
        PRIMARY KEY(instance_id,character_id),
        FOREIGN KEY(instance_id,character_id) REFERENCES native_dungeon_runs(instance_id,character_id) ON DELETE CASCADE)''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_dungeon_route_progress (
        instance_id TEXT NOT NULL, character_id INTEGER NOT NULL, stage INTEGER NOT NULL CHECK(stage>=0),
        PRIMARY KEY(instance_id,character_id),
        FOREIGN KEY(instance_id,character_id) REFERENCES native_dungeon_runs(instance_id,character_id) ON DELETE CASCADE)''')


def _dungeon_runs(db, cid, now):
    return db.execute('SELECT dungeon_id,state,reset_at FROM native_dungeon_runs '
                      'WHERE character_id=? AND reset_at>? ORDER BY reset_at DESC', (cid, now))


def _puzzle(game, db):
    return db.execute('''SELECT p.* FROM native_tianpeng_puzzles p
        JOIN native_dungeon_runs r USING(instance_id,character_id)
        WHERE r.character_id=? AND r.dungeon_id=? AND r.state='entered'
          AND r.reset_at>? AND p.scene_id=? AND p.solved=0
        ORDER BY r.reset_at DESC LIMIT 1''',
        (game.character['id'], TIANPENG, int(time()), game.character['map_id'])).fetchone()


def _puzzle_digits(raw):
    from native946_lua_values import table

    data=table(raw,limit=96,nodes=12)
    if set(data)!={1,2,3} or any(type(v) is not int or not 1<=v<=9 for v in data.values()):
        raise ValueError('机关答案无效。')
    return tuple(data[i] for i in (1,2,3))


def receive_puzzle(game, reader):
    answer=_puzzle_digits(reader.string().decode('gb18030'))
    reader.finish()
    if game.battle:raise ValueError('请先结束战斗。')
    with game.progression.transaction() as db:
        puzzle=_puzzle(game,db)
        if puzzle is None:
            raise ValueError('当前没有可开启的天蓬问情机关。')
        if answer!=tuple(puzzle['answer_'+s] for s in 'abc'):
            raise ValueError('机关没有开启，请重新核对线索。')
        db.execute('UPDATE native_tianpeng_puzzles SET solved=1 WHERE instance_id=? AND character_id=?',
                   (puzzle['instance_id'], puzzle['character_id']))
    return packet(4162,[],[]),text_notice('机关已开启。')


def description(name):
    row=BY_NAME.get(name)
    if row is None:return None
    text=row['Desc'];daily=VERIFIED_RULES['daily_entry_revision']
    if name in daily['names']:
        # 945 supersedes the retained client's ten-minute reaccept wording.
        # Preserve the source catalogue and keep the corrected UI separate.
        text=text.replace('打开任务栏放弃任务，放弃后再领取需等待10分钟。',
            '中途放弃后，当天不能再次进入。')
        text+='#r每日参与规则：'+daily['display']
    return row['Name']+'#r'+text+(
        '#r本期原版大厅隐藏此项目。' if row.get('IsHide') else '')


def pages(text,limit=620):
    result=[];current=''
    for paragraph in text.split('#r'):
        if current and len(current)+len(paragraph)+2>limit:
            result.append(current);current=''
        current+=('#r' if current else '')+paragraph
    if current:result.append(current)
    return result or ['暂无资料。']


class Dungeons:
    def __init__(self,game):self.g=game

    def row(self,key):
        if type(key) is not int or key not in RECORDS:raise ValueError('副本编号无效。')
        return RECORDS[key]

    def menu(self,text,choices,nid):
        from native946_activities import Activities
        return Activities(self.g)._menu(text,choices,nid)

    def listing(self,page=0,nid=None,section='all'):
        from native946_activities import CALLBACK
        if nid is None:nid=CALLBACK
        if section not in ('all','npc'):raise ValueError('副本目录分类无效。')
        ids=NPC_RECORDS.get((self.g.character['map_id'],nid),()) if section=='npc' else RECORDS
        rows=[RECORDS[key] for key in ids]
        if type(page) is not int or not 0<=page<max(1,(len(rows)+PAGE_SIZE-1)//PAGE_SIZE):
            raise ValueError('副本目录页码无效。')
        choices=[(row['Name']+('（本期隐藏）' if row.get('IsHide') else ''),
            dict(op='dungeon_detail',dungeon=row['Index'])) for row in rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]]
        if page:choices.append(('上一页',dict(op='dungeon_list',page=page-1,section=section)))
        if (page+1)*PAGE_SIZE<len(rows):choices.append(('下一页',dict(op='dungeon_list',page=page+1,section=section)))
        if section=='all' and page==0 and nid==CALLBACK:
            choices.insert(0,('本机副本',dict(op='local_dungeon_list')))
        choices.append(('返回活动与任务总目录',dict(op='root')))
        return self.menu('副本与秘境：第'+str(page+1)+'页，共'+str(len(rows))+'项。#r'
            '可查看原版副本资料。大厅首页的「本机副本」提供可通关的单人挑战。',choices,nid)

    def detail(self,key,nid):
        row=self.row(key)
        choices=[('前往'+target['name'],dict(op='dungeon_navigate',dungeon=key,target=index))
            for index,target in enumerate(ENTRANCES[key])]
        if row.get('Rewards'):choices.append(('原版奖励展示',dict(op='dungeon_rewards',dungeon=key,page=0)))
        if row['Name'] in DATA['legacy_background']:
            choices.append(('背景故事与旧版说明',dict(op='dungeon_background',dungeon=key,page=0)))
        from native946_activities import RECORDS as ACTIVITIES
        activity=next((r for r in ACTIVITIES.values() if r['name']==row['Name']),None)
        if activity:choices.append(('更多活动资料与剧情',dict(op='detail',id=activity['id'])))
        choices.append(('返回副本与秘境目录',dict(op='dungeon_list',page=(key-1)//PAGE_SIZE)))
        text=description(row['Name'])
        if key in (25,26):
            text+='#r原入口已接本机战斗和奖励流程。原版逐战规则待接通。'
        else:
            text+='#r#r接入状态：完整副本流程、战斗与奖励结算待接通。'
        if key in DAILY_ONCE_IDS or key==TIANPENG:
            with self.g.progression.transaction(write=False) as db:
                if key in DAILY_ONCE_IDS and any(r['dungeon_id']==key for r in _dungeon_runs(db,self.g.character['id'],int(time()))):
                    text+='#r本轮次数已使用，普通和困难共用次数。'
                if key==TIANPENG and _puzzle(self.g,db) is not None:
                    choices.insert(0,('查看奇异点机关',dict(op='dungeon_puzzle')))
        if not ENTRANCES[key]:text+='#r此入口为动态入口或坐标入口，请按上方原版说明查找。'
        return self.menu(text,choices,nid)

    def document(self,key,page,nid,background=False):
        row=self.row(key);op='dungeon_background' if background else 'dungeon_rewards'
        if background:
            old=DATA['legacy_background'].get(row['Name'])
            if old is None:raise ValueError('没有这项副本的旧版背景资料。')
            title='背景故事与旧版说明'
            text='旧版百科资料；当前参与条件请以前页的副本专用说明为准。#r'+old['intro']
        else:
            title='原版奖励展示'
            text='以下为原版展示清单，尚未接通实际掉落和结算。#r'+'#r'.join(
                DATA['item_metadata'][str(kind)]['name']+'：'+(DATA['item_metadata'][str(kind)].get('desc') or '')
                for kind,_ in row.get('Rewards',[]))
        documents=pages(text)
        if type(page) is not int or not 0<=page<len(documents):raise ValueError('副本资料页码无效。')
        choices=[]
        if page:choices.append(('上一页',dict(op=op,dungeon=key,page=page-1)))
        if page+1<len(documents):choices.append(('下一页',dict(op=op,dungeon=key,page=page+1)))
        choices.append(('返回副本详情',dict(op='dungeon_detail',dungeon=key)))
        return self.menu(row['Name']+'·'+title+' '+str(page+1)+'/'+str(len(documents))+'#r'+documents[page],choices,nid)

    def navigate(self,key,index):
        self.row(key);targets=ENTRANCES[key]
        if type(index) is not int or not 0<=index<len(targets):raise ValueError('副本领取人无效。')
        target=targets[index]
        from native946_world import world
        npc=world().npc(target['scene'],target['npc'])
        if npc is None or (npc['x']//20,npc['y']//20)!=(target['x'],target['y']):
            raise ValueError('副本入口位置已变化，请重新核对。')
        from native946_navigation import npc_auto_trace
        return packet(1183,['string'],['dlg_npc_chat']),npc_auto_trace(
            target['scene'],target['x'],target['y'],target['npc'],target['name'])

    def execute(self,nid,action):
        if self.g.battle:raise ValueError('请先结束当前战斗。')
        op=action['op']
        if op=='dungeon_list':return self.listing(action.get('page',0),nid,action.get('section','all'))
        if op=='dungeon_detail':return self.detail(action['dungeon'],nid)
        if op=='dungeon_navigate':return self.navigate(action['dungeon'],action['target'])
        if op=='dungeon_puzzle':
            from native946_progression import lua_data

            with self.g.progression.transaction(write=False) as db:
                puzzle=_puzzle(self.g,db)
                if puzzle is None:raise ValueError('当前没有可开启的天蓬问情机关。')
                clue=lua_data(list(_puzzle_digits(puzzle['clue'])))
                return packet(4164,['string'],[clue]),packet(4163,[],[])
        if op in ('dungeon_rewards','dungeon_background'):
            return self.document(action['dungeon'],action.get('page',0),nid,op=='dungeon_background')
        raise ValueError('副本操作无效。')


def hall_reply(game):
    owned={}
    with game.progression.transaction(write=False) as db:
        for row in _dungeon_runs(db,game.character['id'],int(time())):
            record=RECORDS.get(row['dungeon_id'])
            if record and row['state']=='completed':
                owned.setdefault(record['Name'],dict(Status=2,DungeonName=record['Name'],ResetTime=row['reset_at']))
    # The native hall builds the catalogue locally. This reply only refreshes progress.
    return packet(1970,[('array','dungeon_task')],[list(owned.values())])
