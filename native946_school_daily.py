"""Daily school milestone and the stock achievement and activity UI entries."""
from native946_codec import packet, screen_notice, text_notice


DAILY_LIMIT = 20
ACHIEVEMENT_ID = 1220603
LIVENESS_GUIDE_ID = 929991
GUIDE_TITLE = '初入江湖[主线]'
# info_achieve_new, 946 configs.wdf 777f02da. This is the separately
# claimed achievement award, independent of a task's settlement.
ACHIEVEMENT_REWARD = {
    'name': '亲传弟子', 'achieve_value': 3, 'award_type': 2,
    'award_data': '11@13@300,12@13@300,21@103@300,22@83@300,31@73@300,32@73@300,71@6109@300,72@6109@300',
}


def earned_on(db, cid):
    return db.execute('SELECT earned_day FROM native_school_milestones WHERE character_id=?', (cid,)).fetchone()


def record_completion(db, cid, day):
    """Record the daily crossing and the character's first achievement."""
    progress = db.execute("SELECT day,completed FROM native_npc_activity_progress "
                          "WHERE character_id=? AND kind='school'", (cid,)).fetchone()
    if not progress or progress['day'] != day:
        return {}
    milestone = {'school_round_done': progress['completed'] % 10 == 0}
    if progress['completed'] != DAILY_LIMIT:
        return milestone
    earned = db.execute('INSERT OR IGNORE INTO native_school_milestones VALUES(?,?)', (cid, day)).rowcount
    return {**milestone, 'school_day_done': True, 'school_achievement_earned': bool(earned)}


def completion_packets(value):
    replies = []
    if value.get('school_round_done'):
        replies.append(screen_notice('#G恭喜你完成了一轮师门任务。#n'))
    if value.get('school_achievement_earned'):
        replies.extend((packet(5, ['number'], [ACHIEVEMENT_ID]), packet(7, ['number'], [1]),
                        screen_notice('#G恭喜你达成殊勋「亲传弟子」。#n')))
    if value.get('school_day_done'):
        replies.append(screen_notice('当日师门任务已全部完成（20/20）。#r打开任务 → 今日活跃，查看活跃目标和奖励。'))
    return tuple(replies)

def reward_packets(objective, value, guardian_names=()):
    lines = [f'您获得#G{value["experience"]}#n经验']
    if value['guardian_experience']:
        lines.extend(f'您的#G{name}#n获得#G{value["guardian_experience"]}#n经验'
                     for name in guardian_names)
    return (screen_notice('#r'.join(lines)),
            text_notice(f'师门{objective}完成。金钱+{value["gold"]}，师门贡献+10。'))


def guide_details(snapshot):
    from native946_campaign import NEWBIE_VERSION, carter_target

    carter = carter_target(snapshot)
    if carter:
        text = f'在长安拜访{carter["name"]}。'
        return dict(title=GUIDE_TITLE,text=text,description=text,npc_name=carter['name'],
                    **{key:carter[key] for key in ('scene','npc','x','y')})
    if not snapshot.get('school_milestone'):
        run=snapshot.get('campaign',{}).get('runs',{}).get('newbie')
        if not run or run['version']!=NEWBIE_VERSION or run['state']!='completed':
            return None
        from native946_school import PRIMARY_MASTERS, base_school
        from native946_world import world

        scene,npc=PRIMARY_MASTERS.get(base_school(snapshot['school']),(2001,1139))
        giver=world().npc(scene,npc)
        if giver is None:
            return None
        completed=snapshot.get('school_completed_today',0)
        text=(f'找到门派师傅{giver["name"]}完成{DAILY_LIMIT}次师门任务，当前已完成{completed}/{DAILY_LIMIT}次师门任务。'
              if snapshot['school'] else '向师门传送仙询问路线并拜师，随后完成20次师门任务。')
        trace_text=text.replace(f'{completed}/{DAILY_LIMIT}', f'#G{completed}/{DAILY_LIMIT}#n', 1)
        return dict(title=GUIDE_TITLE,text=text,trace_text=trace_text,description=text,npc_name=giver['name'],
                    scene=scene,npc=npc,x=giver['x']//20,y=giver['y']//20)
    if snapshot.get('liveness_guide_done'):
        return None
    text = '打开任务->今日活跃界面，达成活跃目标，领取一次活跃度奖励。'
    return dict(title=GUIDE_TITLE, text=text,
                trace_text=text.replace('任务->今日活跃界面', '#G任务->今日活跃界面#n', 1),
                description='选择本条任务可打开今日活跃。达到60点即可领取第一档奖励。',
                scene=0, x=0, y=0, npc=0)
