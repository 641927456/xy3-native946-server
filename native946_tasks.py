"""Original 946 catalog, reconstructed main campaign and native task journal."""
from pathlib import Path
from time import time
import json

from native946_codec import packet
from native946_codec import text_notice
import native946_rookie_guide
import native946_task_recommendations
from importlib import reload as _reload

# The existing connection reloads TaskJournal in place. Keep its read-only
# guide responses on the same source version when that happens.
_reload(native946_rookie_guide)
_reload(native946_task_recommendations)

DATA = json.loads(Path(__file__).with_name('native946_story_data.json').read_text('utf8'))
STORIES = {int(k): v for k, v in DATA['stories'].items()}
STORY_NPCS = {}
for _tid, _story in STORIES.items():
    for _where in _story['locations']:
        STORY_NPCS.setdefault((_where['scene'], _where['npc']), []).append(_tid)
REQUESTS = {1986, 1981, 1982, 1990, 3102, 4181, 4344, 4345, 2805, 4346, 4347, 4348, 5438, 5444}
WRITES = {1394, 1981, 1982, 1990, 3102, 4181, 4347, 4348, native946_task_recommendations.CLAIM_REWARD}
PAGE_SIZE = 7
GAP = '缺少原版分阶段脚本、剧情战斗配置和奖励结算，暂不能接取或通关。'
STORY_PANEL = 'dlg_scnr_review_main'
STORY_CHILD_PANEL = 'dlg_scnr_review_story'


def story_panel_packets(show=True):
    """Only the native container may own/place the story content subpanel."""
    cleanup = (packet(1183, ['string'], [STORY_PANEL]),
               packet(1183, ['string'], [STORY_CHILD_PANEL]))
    return (*cleanup, packet(3993, ['string'], [STORY_PANEL])) if show else cleanup


def schema(db):
    from native946_campaign import schema as campaign_schema
    campaign_schema(db)
    db.execute('''CREATE TABLE IF NOT EXISTS native_task_preferences (
        character_id INTEGER NOT NULL REFERENCES characters(id),
        task_id INTEGER NOT NULL, tracked INTEGER NOT NULL CHECK(tracked IN (0,1)),
        PRIMARY KEY(character_id,task_id))''')


def preferences(db, cid):
    return {r['task_id']: r['tracked'] for r in db.execute(
        'SELECT task_id,tracked FROM native_task_preferences WHERE character_id=?', (cid,))}


def apply_preferences(snapshot, trace, listing):
    prefs = snapshot.get('task_preferences', {})
    trace[:] = [r for r in trace if prefs.get(r['id'], 1)]
    for category in listing.values():
        for tid, row in category.items():
            row['tr'] = prefs.get(tid, 1)


def grade_number(grade, rei=0, fly=0, feng=0):
    # Original GInfo:get_int_from_grade (native source 23e7df96, child 1).
    return grade + rei * 1000 + fly * 10000 + feng * 100000


def grade_label(value):
    feng, rest = divmod(value, 100000)
    fly, rest = divmod(rest, 10000)
    rei, grade = divmod(rest, 1000)
    return ('封圣' if feng else '飞升' if fly else str(rei) + '转') + str(grade) + '级'


def eligibility(story, snapshot, completed=()):
    roles = story['fit_role'].split('@')
    if roles != ['0'] and str(snapshot.get('role', snapshot.get('role_id'))) not in roles:
        return False, '当前角色不符合原版角色限制。'
    actual = grade_number(snapshot['level'], snapshot['rebirth'], snapshot['ascension'], snapshot['sanctification'])
    low = grade_number(*(story['min_' + k] for k in ('grade', 'rei', 'fly', 'feng')))
    high = grade_number(*(story['max_' + k] for k in ('grade', 'rei', 'fly', 'feng')))
    if actual < low:
        return False, '未达到原版领取等级：' + story['grade'] + '。'
    if actual > high:
        return False, '已超过原版领取等级范围。'
    if story['pre_task'] and story['pre_task'] not in completed:
        return False, '未完成前置剧情：' + story['pre_task'] + '。'
    return True, '已满足已知领取条件。'


def location(story):
    return next((n for n in story['locations'] if n['reachable']), None)


def story_description(story, snapshot):
    from native946_campaign import QUESTS,TRIALS,completed_names
    _, reason = eligibility(story, snapshot,completed_names(snapshot))
    status=GAP
    if str(story['id']) in QUESTS:
        row=snapshot.get('campaign',{}).get('runs',{}).get(str(story['id']))
        status=({'active':'进行中','completed':'已完成','available':'等待领取'}.get(row['state']) if row else '可在剧情联系人处查看领取条件')
        if str(story['id']) in TRIALS:
            reason = '本机领取等级：' + story['grade']
    return (story['desc_str'] + '#r#r原版领取等级：' + story['grade'] +
            '#r前置剧情：' + (story['pre_task'] or '无') +
            '#r原版奖励说明：' + (story['reward'] or '原表未列出') +
            '#r当前条件：' + reason + '#r剧情状态：' + status)


def active_details(snapshot, *, practice_trainer=False):
    from native946_progression import QUEST_ID, QUEST_NAME
    from native946_npc_quests import task_details as npc_task_details
    result = {}
    q = snapshot['quest']
    if practice_trainer and q['state'] in ('active', 'ready'):
        text = '击败训练木偶 0/1' if q['state'] == 'active' else '训练已完成，请向本机教头交付。'
        result[QUEST_ID] = dict(title=QUEST_NAME, text=text,
            description='完成一次基础训练，向本机教头领取120经验和25金钱。',
            scene=2001, x=302, y=210, npc=900001)
    for row in snapshot.get('npc_quests', []):
        if row['expires'] <= time():
            continue
        result[930000 + row['id']] = npc_task_details(row)
    chain=snapshot.get('task_chain')
    if chain:
        from native946_task_chain import task_text,QID_BASE,navigation_target
        t=navigation_target(chain)
        result[QID_BASE+chain['id']]=dict(title='任务链 第'+str(chain['ring'])+'/300环',text=task_text(chain),
            description='每环经验与通宝；300环额外现金与物品。',scene=t['scene'],x=t['x'],y=t['y'],npc=t['npc'])
    from native946_campaign import task_details
    result.update(task_details(snapshot))
    from native946_local_guides import details as local_guides
    result.update(local_guides(snapshot))
    from native946_school_daily import LIVENESS_GUIDE_ID, guide_details as school_guide_details
    school_guide = school_guide_details(snapshot)
    if school_guide:
        result[LIVENESS_GUIDE_ID] = school_guide
    from native946_race_fabao import QUEST as RACE_QUEST, guide_details
    guide=guide_details(snapshot)
    if guide:result[int(RACE_QUEST)]=guide
    return result


class TaskJournal:
    def __init__(self, game):
        self.g = game
        self.p = game.progression

    def receive(self, mid, reader):
        if mid in (1393, 1394):
            from native946_newbie_rewards import receive
            return True, receive(self.g, mid, reader)
        if mid == 1410:
            guide = reader.integer()
            value = reader.string()
            reader.finish()
            if guide == 100 and value == b'0' and getattr(self.g, 'new_character_welcome_pending', False):
                from native946_campaign import CHAPTERS, current
                run = current(self.p.snapshot())
                self.g.new_character_welcome_pending = False
                scene = CHAPTERS['newbie']['giver']['scene']
                if (run and (run['chapter'], run['node'], run['state']) == ('newbie', 0, 'active')
                        and self.g.character['map_id'] == scene and not self.g.battle):
                    return True, packet(1222, ['number', 'number'], [101, 1])
            return True, None
        if mid==1458:
            script=reader.integer();reader.finish()
            from native946_cinematics import finish
            return True,self.g.defer_packets(lambda:finish(self.g,script))
        if mid==1980:
            from native946_login import MessageReader
            from native946_campaign import BY_QID
            probe=MessageReader(reader.data[reader.offset:]);qid=probe.integer();probe.finish()
            if qid in BY_QID:
                reader.integer();reader.finish()
                raise ValueError('剧情进度会长期保存；如需隐藏任务，请使用取消追踪。')
        handled, reply = native946_task_recommendations.TaskRecommendations(self.g).receive(mid, reader)
        if handled:
            return True, reply
        from native946_rookie_guide import RookieGuide
        handled, reply = RookieGuide(self.g).receive(mid, reader)
        if handled:
            return True, reply
        if mid in (1492, 1493):
            # Let the existing training-task handler retain its own contract.
            from native946_login import MessageReader
            probe = MessageReader(reader.data[reader.offset:])
            tid = probe.integer(); probe.finish()
            if tid not in STORIES:
                return False, None
            reader.integer(); reader.finish()
            from native946_campaign import Campaign,QUESTS
            if str(tid) in QUESTS and mid==1492:
                campaign=Campaign(self.g);chapter=QUESTS[str(tid)]
                if not campaign.near(chapter['giver']):
                    from native946_navigation import npc_auto_trace
                    t=chapter['giver']
                    return True,npc_auto_trace(t['scene'],t['x'],t['y'],t['npc'],t['name'])
                campaign.accept(str(tid))
                return True,campaign.refreshed('已领取'+chapter['name']+'，目标已记入任务日志。')
            story = STORIES[tid]
            _, reason = eligibility(story, self.p.snapshot())
            raise ValueError(story['name'] + '：' + reason + GAP)
        if mid == 1495:
            from native946_login import MessageReader
            probe = MessageReader(reader.data[reader.offset:])
            event = probe.integer(); probe.string(); probe.finish()
            if event != 2:  # Original COMMON_CONST.UI_EVENT.QUERY_TASK.
                return False, None
            reader.integer(); reader.string(); reader.finish()
            from native946_progression import lua_data
            from native946_campaign import QUESTS,TRIALS,Campaign
            s=self.p.snapshot();runs=s.get('campaign',{}).get('runs',{})
            doing=[int(k) for k,r in runs.items() if k!='newbie' and r['state']=='active']
            done=[dict(c=QUESTS[k]['name']) for k,r in runs.items() if k in QUESTS and k!='newbie' and r['state']=='completed']
            # The native panel consumes active IDs, completed-name records,
            # and unavailable IDs. Never use the request text as task state.
            unavailable=[]
            if runs:
                for tid in STORIES:
                    if str(tid) in TRIALS and str(tid) in runs:
                        continue
                    c=QUESTS.get(str(tid))
                    if c is None or not Campaign(self.g).eligibility(c,s)[0]:unavailable.append(tid)
            return True, packet(1326, ['number', 'string'], [event, lua_data([doing, done, unavailable])])
        if mid not in REQUESTS:
            return False, None
        if mid == 3102:
            from native946_school import School
            session_id, option = reader.integer(), reader.integer()
            reader.finish()
            session = getattr(self.g, 'npc_service_session', None)
            if session and session['kind'] == 'campaign_delivery':
                from native946_campaign import Campaign
                return True, Campaign(self.g).submit_delivery(session_id, option)
            return True, School(self.g).submit_supply(session_id, option)
        if mid == 4181:
            from native946_campaign import BIOPIC_INDEX, BIOPIC_PIECES, Campaign
            operation, entry = reader.integer(), reader.integer()
            reader.finish()
            if entry == BIOPIC_INDEX and operation in (1, 11):
                return True, Campaign(self.g).biopic(operation)
            if operation == 2 and entry in BIOPIC_PIECES:
                return True, packet(4179, ['number'] * 3, [entry, 0, 0])
            raise ValueError('当前只开放境·广目天王的特殊绘卷。')
        if mid == 4344:
            reader.finish()
            from native946_campaign import review_packet
            return True,review_packet(self.p.snapshot())
        if mid == 4345:
            reader.finish()
            from native946_campaign import Campaign
            return True, Campaign(self.g).review_rewards()
        if mid in (4347, 4348):
            from native946_campaign import Campaign
            reward_id = reader.integer()
            reader.finish()
            return True, Campaign(self.g).claim_review_reward('score' if mid == 4347 else 'chapter', reward_id)
        if mid==4346:
            review_id,order=reader.integer(),reader.integer();reader.finish()
            if self.g.battle:
                raise ValueError('请先结束战斗再回顾剧情。')
            from native946_campaign import REVIEW_CHAPTERS,Campaign
            chapter=REVIEW_CHAPTERS.get(review_id)
            if chapter is None:raise ValueError('没有这段剧情回顾。')
            row=self.p.snapshot().get('campaign',{}).get('runs',{}).get(chapter['id'])
            if not row or not 1<=order<=min(row['node'],len(chapter['nodes'])):raise ValueError('请先完成这段剧情，再进行回顾。')
            node=chapter['nodes'][order-1]
            clip=Campaign(self.g).movie(node) if self.g.character['map_id']==node['target']['scene'] else ()
            return True,(text_notice(node['title']+'#r'+node['text']),clip)
        if mid == 5438:
            from native946_campaign import Campaign
            task_id = reader.integer()
            reader.finish()
            return True, Campaign(self.g).retry(task_id)
        if mid == 5444:
            from native946_campaign import story_items_packet
            head_task_id = reader.integer()
            reader.finish()
            if head_task_id not in STORIES:
                raise ValueError('没有这章剧情的道具资料。')
            return True, story_items_packet(head_task_id, self.p.snapshot())
        if mid == 2805:
            reader.integer()
            reader.finish()
            raise ValueError('剧情进度长期保存，暂不支持重置。')
        tid = None if mid == 1982 else reader.integer()
        reader.finish()
        snapshot = self.p.snapshot()
        details = active_details(snapshot, practice_trainer=getattr(self.g, 'practice_trainer', False))
        if mid == 1986:
            from native946_school_daily import LIVENESS_GUIDE_ID
            if tid == LIVENESS_GUIDE_ID and tid in details and not details[tid]['npc']:
                return True, (self.detail(tid,snapshot), packet(1259,[],[]))
            return True, self.detail(tid, snapshot)
        if mid != 1982 and tid not in details:
            if tid in STORIES:
                raise ValueError(STORIES[tid]['name'] + '：' + GAP)
            raise ValueError('当前没有这项可追踪的任务，任务可能已经完成、取消或超时。')
        with self.p.transaction() as db:
            for qid in details if mid == 1982 else (tid,):
                db.execute('INSERT INTO native_task_preferences VALUES(?,?,?) '
                    'ON CONFLICT(character_id,task_id) DO UPDATE SET tracked=excluded.tracked',
                    (self.p.cid, qid, int(mid == 1990)))
        def render():
            messages = self.p.task_packets()
            if mid == 1981:
                return (packet(1967, ['number'], [tid]), *messages)
            return messages
        return True, self.g.defer_packets(render)

    def detail(self, tid, snapshot=None):
        from native946_campaign import TRIALS
        from native946_progression import lua_data
        snapshot = self.p.snapshot() if snapshot is None else snapshot
        trial = TRIALS.get(str(tid))
        row = active_details(snapshot, practice_trainer=getattr(self.g, 'practice_trainer', False)).get(trial['qid'] if trial else tid)
        if row is not None:
            data = dict(t=row['text'], d=row['description'], x=row['x'], y=row['y'],
                        z=row['scene'], NpcId=row['npc'], auto_path=0)
        elif tid in STORIES:
            story = STORIES[tid]; target = location(story)
            data = dict(t=story['name'] + '（原版剧情资料）', d=story_description(story, snapshot),
                x=target['x'] if target else 0, y=target['y'] if target else 0,
                z=target['scene'] if target else 0, NpcId=story['npc_id'] if target else 0, auto_path=0)
        else:
            raise ValueError('没有这项任务的详情。')
        return packet(1971, ['number', 'string'], [tid, lua_data(data)])

    def _menu(self, nid, text, choices):
        self.g.dialog_actions = [dict(kind='story', **action) for _, action in choices]
        return self.g.menu(text, [label for label, _ in choices], nid)

    def attach_menu(self, npc, reply):
        """Append a story entry without changing transport/service indices."""
        from native946_login import MessageReader
        from native946_game_login import message_id
        from native946_campaign import BY_NPC
        from native946_school import BUZHOU_MASTER, LIZARD_MASTER, SCHOOL_LEADERS
        key=(npc['scene_id'],npc['npc_id'])
        protected_service = ('school' if key in (BUZHOU_MASTER,LIZARD_MASTER) or key in SCHOOL_LEADERS
                             else 'medicine' if key == (2034,1271) else None)
        if protected_service and any(isinstance(action, dict) and action.get('kind') == protected_service
                                     for action in self.g.dialog_actions):
            return reply
        has_catalog = bool(STORY_NPCS.get(key))
        has_story = has_catalog or key in BY_NPC
        from native946_local_guides import mount_option
        local_mount = mount_option(self.g,key)
        if not has_story and local_mount is None:
            return reply
        if not isinstance(reply, bytes) or not reply.startswith(message_id(1025)):
            return reply
        reader = MessageReader(reply[3:])
        chats = [dict(chat_msg=reader.string().decode('gb18030'), speaker=reader.integer())
                 for _ in range(reader.take(1)[0])]
        options = [reader.string().decode('gb18030') for _ in range(reader.take(1)[0])]
        callback, flag = reader.integer(), reader.integer(); reader.finish()
        if len(options) != len(self.g.dialog_actions) or len(options) >= 252:
            return reply
        if has_catalog:
            self.g.dialog_actions.append(dict(kind='story', op='catalog', section='npc', page=0))
            options.append('原版剧情资料与领取条件')
        if has_story:
            self.g.dialog_actions.append(dict(kind='campaign',op='hub'))
            options.append('新手与主线剧情进度')
        if local_mount and len(options)<254:
            label, action = local_mount
            self.g.dialog_actions.append(action)
            options.append(label)
        self.g.dialog_open = True
        return packet(1025, [('array', 'npc_chat'), ('array', 'string'), 'number', 'number'],
                      [chats, options, callback, flag])

    def catalog(self, nid, section=None, page=0):
        if section is None:
            return self._menu(nid, '原版剧情和任务目录。剧情资料来自当前946客户端；缺少原版脚本的内容会明确标出。', [
                ('继续新手与主线剧情',dict(op='campaign')),
                ('主线剧情', dict(op='catalog', section='1', page=0)),
                ('支线剧情', dict(op='catalog', section='2', page=0)),
                ('角色及其他剧情', dict(op='catalog', section='other', page=0)),
                ('原版任务活动与领取人', dict(op='catalog', section='activities', page=0)),
                ('打开原生剧情查询', dict(op='native')),
                ('活动日历与全部任务', dict(op='calendar'))])
        if section == 'activities':
            rows = [(int(k), r) for k, r in DATA['activity_guides'].items()]
        else:
            allowed = STORY_NPCS.get((self.g.character['map_id'], nid), []) if section == 'npc' else None
            rows = [(tid, row) for tid, row in STORIES.items() if tid in allowed] if allowed is not None else [
                (tid, row) for tid, row in STORIES.items() if
                (row['category'] not in (1, 2) if section == 'other' else str(row['category']) == section)]
        rows.sort(key=lambda pair: (pair[1].get('min_grade', 0), pair[0]))
        if type(page) is not int or not 0 <= page < max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE):
            raise ValueError('目录页码无效。')
        choices = [(row['tips'] if section == 'activities' else row['name'] + '（' + row['grade'] + '）',
                    dict(op='guide' if section == 'activities' else 'detail', task=tid))
                   for tid, row in rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
        if page:
            choices.append(('上一页', dict(op='catalog', section=section, page=page - 1)))
        if (page + 1) * PAGE_SIZE < len(rows):
            choices.append(('下一页', dict(op='catalog', section=section, page=page + 1)))
        choices.append(('剧情与任务总目录', dict(op='catalog')))
        if section == 'npc':
            choices.append(('其他事务', dict(op='service')))
        text = ('原版活动指南；是否可领取以已接通的对应NPC业务为准。' if section == 'activities' else
                '原版剧情资料；' + GAP)
        return self._menu(nid, text + '#r第' + str(page + 1) + '页，共' + str(len(rows)) + '项。', choices)

    def validate_source(self, nid):
        from native946_world import world
        from native946_gameplay import TRAINER_ID
        c = self.g.character
        if self.g.battle:
            raise ValueError('请先结束当前战斗。')
        pos = self.g.practice_npc_position() if nid == TRAINER_ID else None
        if pos is None:
            npc = world().npc(c['map_id'], nid)
            if npc is not None:
                pos = (npc['x'] // 20, npc['y'] // 20)
        if pos is None or max(abs(pos[0] - c['x']), abs(pos[1] - c['y'])) > 12:
            raise ValueError('请走近刚才交谈的NPC。')

    def execute(self, nid, action):
        from native946_world import world
        self.validate_source(nid)
        op = action['op']
        if op == 'catalog':
            return self.catalog(nid, action.get('section'), action.get('page', 0))
        if op=='campaign':
            from native946_campaign import Campaign
            return Campaign(self.g).hub(nid)
        if op == 'service':
            return self.g.world_npc_look(nid, include_story=False)
        if op == 'native':
            return (packet(1183, ['string'], ['dlg_npc_chat']),
                    *story_panel_packets())
        if op == 'calendar':
            from native946_activities import Activities
            return Activities(self.g).root()
        if op == 'detail':
            story = STORIES[action['task']]; target = location(story)
            choices = [('前往原版领取人', dict(op='navigate', task=story['id']))] if target else []
            from native946_campaign import QUESTS
            if str(story['id']) in QUESTS:choices.insert(0,('查看剧情与修行进度',dict(op='campaign')))
            choices.append(('返回剧情与任务目录', dict(op='catalog')))
            return self._menu(nid, story['name'] + '#r' + story_description(story, self.p.snapshot()), choices)
        if op == 'guide':
            row = DATA['activity_guides'][str(action['task'])]
            npc = world().npc(row['scene'], row['npcid'])
            choices = [('前往任务领取人', dict(op='guide_navigate', task=action['task']))] if npc and row['scene'] in world().scenes else []
            choices.append(('返回任务活动目录', dict(op='catalog', section='activities', page=0)))
            return self._menu(nid, row['tips'] + '#r原版等级范围：' + grade_label(row['min_grade']) + '—' + grade_label(row['max_grade']) +
                '#r领取人：' + (npc.get('name', '') if npc else str(row['npcid'])) +
                '#r这是原版活动指引；完整规则和开放状态以对应NPC业务为准。', choices)
        if op in ('navigate', 'guide_navigate'):
            from native946_navigation import npc_auto_trace
            if op == 'navigate':
                target = location(STORIES[action['task']])
            else:
                row = DATA['activity_guides'][str(action['task'])]
                npc = world().npc(row['scene'], row['npcid'])
                target = dict(scene=row['scene'], npc=row['npcid'], x=npc['x'] // 20,
                              y=npc['y'] // 20, name=npc.get('name', str(row['npcid']))) if npc else None
            if not target:
                raise ValueError('该领取人尚无已接通的原版地图入口。')
            return npc_auto_trace(target['scene'], target['x'], target['y'], target['npc'], target['name'])
        raise ValueError('剧情目录选项无效。')
