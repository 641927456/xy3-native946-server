"""Original themed cinematics, separate from unavailable instance completion."""
from pathlib import Path
import json

DATA=json.loads(Path(__file__).with_name('native946_dungeon_dialogue_data.json').read_text('utf8'))
CATALOG=DATA['dungeons']


class DungeonDialogues:
    def __init__(self,game):self.g=game

    def menu(self,row,nid):
        clips=CATALOG.get(row['name'])
        if not clips:raise ValueError('没有这项活动的剧情过场。')
        choices=[(str(i+1)+'. '+clip['title'],dict(op='dungeon_dialogue_play',id=row['id'],clip=i))
                 for i,clip in enumerate(clips)]
        choices.append(('返回副本详情',dict(op='detail',id=row['id'])))
        from native946_activities import Activities
        return Activities(self.g)._menu(row['name']+'·剧情对话回放#r'
            '以下为客户端中的相关原版过场。副本流程待接通，回放不改变任务进度和奖励次数。',choices,nid)

    def play(self,row,index,nid):
        clips=CATALOG.get(row['name'])
        if not clips or type(index) is not int or not 0<=index<len(clips):raise ValueError('剧情过场不存在。')
        from native946_cinematics import play
        return play(self.g,clips[index]['script'],resume=dict(kind='dungeon_dialogue',activity=row['id'],npc=nid))
