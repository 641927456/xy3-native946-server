"""NPC routes and native skill learning for the local school service."""
from secrets import randbelow
from random import Random
from time import monotonic, time

from native946_codec import packet, screen_notice, text_notice
from native946_school_daily import DAILY_LIMIT, completion_packets, reward_packets
from native946_school_services import SERVICES as MASTER_LOCAL_SERVICES
from native946_role_growth import choose_school, learning_schools, school_choices, west_school
from native946_role_systems import save, state

# Scene and NPC IDs come from the 946 NPC catalogue. The two substitute
# instructors have explicit 回生系 and 蛊术系 titles.
MASTERS = {
    (2080, 2709): 1, (2030, 3101): 2, (2045, 3201): 3, (2001, 1041): 4,
    (2014, 3046): 5, (2023, 1702): 6, (2056, 2204): 7, (2012, 1904): 8,
    (2087, 3427): 9, (2085, 3426): 10, (2054, 1286): 11, (2057, 1287): 12,
    (2408, 6931): 15, (2408, 6932): 16, (2360, 6245): 17,
    (2361, 6311): 18, (2451, 8187): 19,
}
SCHOOL_LEADERS = {(2361, 6521): 18}
PRIMARY_MASTERS = {school:where for where,school in reversed(tuple(MASTERS.items()))}
BUZHOU_MASTER = PRIMARY_MASTERS[17]
LIZARD_MASTER = PRIMARY_MASTERS[18]
BUZHOU_MASTER_SERVICES = (
    ('师门任务', 'task_menu'),
    ('技能学习', 'learn'),
    ('请师父指点我的秉性', 'nature'),
    ('打坐修炼', 'meditation'),
    ('我要兑换修炼物品', 'practice_exchange'),
    ('捐献物品获得师门贡献', 'donate'),
    ('我要捐献6～10级作坊物品', 'donate_craft'),
    ('离开', 'leave'),
)
MASTERS.update({(2360,6313):15,(2360,6312):16})
SUBSTITUTE_INSTRUCTORS = {(2360,6313),(2360,6312)}
NATIVE_SCHOOL_DESCRIPTIONS = {
    1:'化生寺的弟子，最了解人心的脆弱之处，善于用佛法的奥义创造相应的幻相，使之迷惑。在他们强大的法力面前，敌人往往无法抑止自己的各种执念，陷于迷惘和混乱。',
    2:'方寸一派的绝技，是让人陷入梦境之中，从而丧失战斗力。此外，他们的长生延年之术也非常高深。孙悟空所学七十二变，其实质正是改变身形状态躲避灾厄的长生之术。',
    3:'掖庭宫之人皆为女子，虽不擅搏杀，却精通毒蛊之术。武才人派人从各地搜罗了许多天下奇毒，并着人精研炼毒之法。种种剧毒经美人之手施出，伤敌于无形，令人防不胜防。',
    4:'大唐官府的武技大多出自军阵，将军中许多厉害阵法化入武学之中，可以令敌人陷于进退两难之境，力无所施，困守自毙。',
    5:'瑶池弟子们都精通于驾驭雷电的威力来攻击敌人，这是各系仙法中单以威力而论最强的一系。此外，瑶池弟子还修习到一种由白鹤仙人创出的秘法，可以提高己方的仙法伤害。',
    6:'五庄观弟子善长以御风之术伤敌，道行高深者更能发挥出风系法术圜转不息的精髓，阵阵狂风可令敌人疲于应对。',
    7:'身为东海龙宫的弟子，自然擅长御水之术，水系法术不仅可以对敌人造成伤害，还能降低其体质，令其敌人的气血暂时难以回复到正常水平。',
    8:'普陀弟子所习净化之火，可焚尽世间一切邪念，尤其是心神合一时瞬间爆发出的无匹力量令敌人无可抵御。',
    9:'黑风洞的武艺和法术，精髓在于以快致胜，追求制敌先机。其独门法术可以大幅提高速度，以快打慢，令敌人处处受制。',
    10:'白骨洞的法术以强烈的怨念为源头，追求至高的攻击能力。白骨洞弟子修习的各种法术可以将攻击力提升到极高的境界，杀气纵横势不可挡。',
    11:'盘丝洞弟子皆为女子，并不追求自身的杀伤力，而是精于以盘丝法术保护自己和队友。这些奇术可以大幅提升抵御法术和物理攻击的能力。',
    12:'幽冥地府的法术擅长勾魂夺魄。这些法术施展出来，能够在战斗中震慑对手，同时损伤其气血和法力。',
    17:'不周山乃巫族遗民聚居之地，汇聚大地五行灵气，以吴回为师的不周山弟子拥有消除非自然状态的本领。',
    18:'蜥王殿原主人为妖族蜥王，巫族奢比尸领袖孤长月为了部族生存，杀死蜥王夺取了蜥王的力量，并将两股力量融合贯通后衍生出强大的巫力技能。',
}
SCHOOL_DESCRIPTIONS = dict(NATIVE_SCHOOL_DESCRIPTIONS)
SCHOOL_DESCRIPTIONS.update({
    15:'飞羽更是从武才人手中得到了无时镜，从此龙祗便在云中殿设立流光坛门派，为云中殿培养能人异士。擅长操控时间的流光坛男巫能预知到隐身者的所在，因此在隐身战局中，他们就是隐身者的最大克星。',
    16:'句芒部族的女巫还有独一无二的能力，她们华丽优美的吟唱，可以让敌人在无意中忘却了自己的招数，这往往给战局带来出奇制胜的效果。陌上岚的到来，令云中殿如虎添翼，飞羽在云中殿为陌上岚开辟了一处静谧的灵树巢居，名为“东岚居”。',
})
SKILLS = {school: tuple(range(first, first + 5)) for school, first in
          enumerate((21, 11, 1, 31, 51, 61, 71, 41, 101, 91, 81, 111), 1)}
SKILLS.update({15: (6114, 6116, 6118, 6120, 6122), 16: tuple(range(6128, 6133)),
               17: tuple(range(6107, 6112)), 18: tuple(range(6100, 6105)),
               19: tuple(range(1701, 1706))})
EXTRA_SKILLS = {
    1:(26,6000), 2:(16,6002,6024), 3:(6,6004), 4:(36,6006,6026),
    5:(56,6016,6032), 6:(66,6018), 7:(76,6020,6034), 8:(46,6022),
    9:(106,6010), 10:(96,6014,6030), 11:(86,6012), 12:(116,6008,6028),
    15:(6124,6140), 16:(6133,6139), 17:(6112,6038,6141), 18:(6105,6036,6127),
    19:(1706,1710),
}
SCHOOL_SKILLS = frozenset(skill for catalog in (SKILLS,EXTRA_SKILLS)
                          for skills in catalog.values() for skill in skills)
# 946 configs.wdf b8c85c38 (info_hero_stunt_skill), indexed here by school.
FLIGHT_STUNTS = {
    1: 27, 2: 17, 3: 7, 4: 37, 5: 57, 6: 67, 7: 77, 8: 47,
    9: 107, 10: 97, 11: 87, 12: 117,
    15: 6136, 16: 6137, 17: 6138, 18: 6135, 19: 1711,
}
# 本服规则。二级飞行开放普通城镇的三个固定落点。
FLIGHT_POINTS = {2001:1139, 2008:5672, 2006:4220}
TASK_LEVEL = 20
TASK_LIMIT = DAILY_LIMIT
SUPPLY_TIERS = ((4000, 4001), (4002, 4003), (4004, 4005))
SUPPLY_MEDICINES = frozenset(item for tier in SUPPLY_TIERS for item in tier)
MEDICINE_SUPPLY_AMOUNT = 1
SUPPLY_UI = 'dlg_npc_give'
OBJECTIVE_NAMES = {'visit': '找人', 'supply': '寻物', 'patrol': '巡逻'}
# These expanding pools use the 946 locations corresponding to historical contacts.
VISIT_TIERS = (
    ((2001,1053),(2053,1284),(2003,2160),(2008,2008),(2008,2006)),
    ((2008,2004),(2008,2005)),
    ((2007,1339),(2007,1340)),
)
VISIT_TARGETS = frozenset(npc for group in VISIT_TIERS for npc in group)
PATROL_MAPS = {
    1:(2021,2080), 2:(2030,), 3:(2004,), 4:(2001,), 5:(2014,), 6:(2023,),
    7:(2016,), 8:(2012,), 9:(2087,), 10:(2085,), 11:(2054,), 12:(2009,),
    15:(2408,), 16:(2408,), 17:(2360,), 18:(2361,), 19:(2451,),
}
PATROL_SCENES = (frozenset(scene for maps in PATROL_MAPS.values() for scene in maps)
                 | frozenset(scene for scene,_ in MASTERS))
PATROL_RADIUS = 6


def draw_objective(level, rng, previous_supply=None):
    from native946_npc_services import DATA

    tier = 2 if level >= 100 else 1 if level >= 50 else 0
    weights = ((45,35,20),(35,30,35),(25,30,45))[tier]
    stock = DATA['medicine_shop']['goods']
    available = [item for group in SUPPLY_TIERS[:tier+1] for item in group if stock.get(str(item),0)>0]
    weights = (weights[0], weights[1] if available else 0, weights[2])
    objective = rng.choices(tuple(OBJECTIVE_NAMES),weights,k=1)[0]
    if objective == 'supply':
        different = [item for item in available if item != previous_supply]
        return objective, rng.choice(different or available), MEDICINE_SUPPLY_AMOUNT
    if objective == 'visit':
        return objective, rng.choice([npc for group in VISIT_TIERS[:tier+1] for npc in group]), 1
    return objective, None, 1


def supply_text(row):
    from native946_progression import DATA
    item = DATA['items'][str(row['supply_item'])]['name']
    return (f'带{row.get("supply_amount",1)}件{item}交给{row["name"]}。',
            '可向长安济世堂药店的华重生购买。备齐后回师父处选择交付寻物。')


def available_supply(db,cid,item):
    from native946_inventory_locks import require_unlocked
    from native946_mall_bags import pages

    allowed = pages(db,cid)
    result = []
    for row in db.execute('SELECT i.* FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id '
                          'WHERE i.character_id=? AND i.type=? AND (coalesce(e.expires,0)=0 OR e.expires>?) '
                          'ORDER BY i.page,i.slot',(cid,item,int(time()))):
        if row['page'] not in allowed:
            continue
        try:
            require_unlocked(db,cid,row['id'])
        except ValueError:
            continue
        result.append(row)
    return result


def base_school(value):
    return 19 if value in (119, 219, 319, 719) else value


def flight_stunt_id(school):
    return FLIGHT_STUNTS.get(base_school(school))


def flight_stunt_records(school, level=0):
    skill = flight_stunt_id(school)
    return [dict(skill_id=skill, level=level, cost=0)] if skill else []


def contribution(db, cid):
    return state(db, cid, 'school_training', {'contribution': 0})['contribution']


def add_contribution(db, cid, amount):
    save(db, cid, 'school_training', {'contribution': contribution(db, cid) + amount})


def practice_limit(snapshot):
    # 946 spell preview (7b1cf8c1): each rebirth and ascension adds 5000.
    return 10000 + 5000 * (snapshot['rebirth'] + snapshot['ascension'])


def level_spells(db, cid, role):
    from native946_role_battle import profile

    # The first five slots are the regular spells from info_school_skill.
    # Keep their existing learning levels and eligibility in the skill catalog.
    for branch in learning_schools(db, cid):
        for skill in SKILLS[branch]:
            meta = profile(skill)
            if (meta and meta['source'] == 'school' and role['level'] >= meta['level']
                    and meta['race'] in (0, role['race'])
                    and (not meta.get('sex') or meta['sex'] == role['sex'])):
                yield skill


def skill_rows(master, snapshot):
    from native946_role_battle import profile

    max_practice = practice_limit(snapshot)
    rows = []
    own_school = base_school(snapshot['school']) == master
    skills = SKILLS[master] + EXTRA_SKILLS[master] if own_school else SKILLS[master]
    for tier, skill in enumerate(skills, 1):
        meta = profile(skill)
        if not meta or meta['race'] not in (0, snapshot['race']) or snapshot['level'] < meta['level']:
            continue
        if meta.get('sex') and meta['sex']!=snapshot['sex']:
            continue
        if meta.get('schools') and snapshot['school'] not in meta['schools']:
            continue
        rank = snapshot['skills'].get(skill, 0)
        rows.append(dict(skill_id=skill, cur_value=rank, max_value=max_practice,
                         next_money=5 * tier, next_contribute=tier if tier > 1 else 0, next_pote=0))
    return rows


class School:
    def __init__(self, game):
        self.game = game
        self.progression = game.progression

    def menu(self, npc, *, tasks_only=False):
        from native946_npc_quests import Quests, day_key

        nid = npc['npc_id']
        key = (npc['scene_id'],nid)
        master = MASTERS.get(key,SCHOOL_LEADERS.get(key))
        snapshot = self.progression.snapshot()
        owned = base_school(snapshot['school'])
        if key == BUZHOU_MASTER and not tasks_only:
            from native946_npc_services import NpcServices

            services = BUZHOU_MASTER_SERVICES
            if master != owned:
                # 947 只采集了本门弟子的菜单。其他角色沿用本机学法和拜师资格。
                with self.progression.transaction(write=False) as db:
                    compatible = master in learning_schools(db, self.progression.cid)
                entry = ('技能学习', 'learn') if owned else ('拜入门派', 'join')
                if not compatible:
                    entry = ('查看门派要求', 'requirements')
                services = (entry, ('离开', 'leave'))
            return NpcServices(self.game)._menu(nid, '想知道，飞的滋味吗？', [
                (label, dict(kind='school', op=op)) for label, op in services
            ], speaker=nid)
        if key == LIZARD_MASTER and master == owned and not tasks_only:
            from native946_npc_services import NpcServices

            # Use the local services with a separate task submenu. The 947
            # capture of Wu Hui does not establish Shi Yi's personal greeting.
            return NpcServices(self.game)._menu(nid, npc['title'] + ' ' + npc['name'], [
                (label, dict(kind='school', op=op)) for label, op in (
                    ('师门任务', 'task_menu'), ('技能学习', 'learn'), ('查看门派天赋', 'talent'),
                    *((label, op) for label, op in BUZHOU_MASTER_SERVICES if op in MASTER_LOCAL_SERVICES),
                    ('离开', 'leave'))
            ], speaker=nid)
        if key in SCHOOL_LEADERS:
            text = '蜥王殿传功师父石羿负责拜师、传功和师门任务。'
            choices = [('前往传功师父石羿',dict(kind='school',op='master'))]
            if master == owned:
                choices.append(('查看门派天赋',dict(kind='school',op='talent')))
            from native946_npc_services import NpcServices
            return NpcServices(self.game)._menu(nid,text,choices,speaker=nid)
        with self.progression.transaction(write=False) as db:
            compatible = master in learning_schools(db, self.progression.cid)
            west = west_school(db,self.progression.cid) if master==19 else 0
            amount = contribution(db, self.progression.cid)
            progress = db.execute("SELECT accepted,completed FROM native_npc_activity_progress "
                                  "WHERE character_id=? AND kind='school' AND day=?",
                                  (self.progression.cid, day_key())).fetchone() if master == owned else None
        choices = []
        text = SCHOOL_DESCRIPTIONS.get(master,'请选择师门事务。')
        if key == LIZARD_MASTER:
            text = '师门任务' if tasks_only else npc['title'] + ' ' + npc['name']
        if owned and key != LIZARD_MASTER:
            text += '#r当前师门贡献：' + str(amount)
        if master==19 and owned!=19:
            if west:
                choices.append(('拜入魔西天',dict(kind='school',op='west_offer')))
            else:
                text += '#r魔西天收飞升或封圣的男性角色。'
        if not owned and compatible and key not in SUBSTITUTE_INSTRUCTORS:
            choices.append(('拜入门派', dict(kind='school', op='join')))
        if not tasks_only and owned and (master == owned or compatible):
            choices.append(('学习和修习法术', dict(kind='school', op='learn')))
        if master == owned:
            from native946_local_guides import practice_ready
            if key == PRIMARY_MASTERS[master] and practice_ready(snapshot):
                choices.append(('领取升级送大礼（本服）',dict(kind='school',op='practice_reward')))
            if not tasks_only:
                choices.append(('查看门派天赋', dict(kind='school', op='talent')))
                if key == PRIMARY_MASTERS[master]:
                    choices.extend((label,dict(kind='school',op=op)) for label,op in BUZHOU_MASTER_SERVICES
                                   if op in MASTER_LOCAL_SERVICES)
            accepted, completed = tuple(progress) if progress else (0, 0)
            text += f'#r今日已领取{accepted}/{TASK_LIMIT}环，完成{completed}环。'
            current = Quests(self.game).active('school')
            if current:
                if current['supply_item']:
                    text += '#r' + ' '.join(supply_text(current))
                    if (npc['scene_id'],nid)==(current['scene'],current['npc']):
                        choices.append(('交付寻物', dict(kind='school', op='supply', task=current['id'])))
                    else:
                        choices.append(('前往交付师父',dict(kind='npc_quest',op='navigate',task=current['id'])))
                    choices.append(('前往济世堂药店',dict(kind='school',op='supply_shop',task=current['id'])))
                else:
                    from native946_npc_quests import task_details
                    text += '#r' + task_details(current)['text']
                    label={'visit':'前往拜访名单上的联系人','patrol':'前往巡逻区域'}.get(current['school_objective'],'前往师门任务目标')
                    choices.append((label,dict(kind='npc_quest',op='navigate',task=current['id'])))
                choices.append(('取消师门任务',dict(kind='npc_quest',op='cancel',task=current['id'])))
            elif accepted >= TASK_LIMIT:
                text += '#r今日领取次数已用完。'
            elif snapshot['level'] >= TASK_LEVEL:
                choices.append(('领取师门任务', dict(kind='school', op='task')))
            else:
                text += f'#r达到{TASK_LEVEL}级后可领取师门任务。'
            if getattr(self.game, '_auto_school', None):
                choices.append(('停止自动师门', dict(kind='school', op='auto_stop')))
            elif snapshot['level'] >= TASK_LEVEL and (current or accepted < TASK_LIMIT):
                choices.append(('开启自动师门', dict(kind='school', op='auto_start')))
            choices.append(('了解师门任务', dict(kind='school', op='task_rules')))
        if not choices:
            if key in SUBSTITUTE_INSTRUCTORS and compatible:
                choices.append(('前往拜访掌门',dict(kind='school',op='master')))
            else:
                choices.append(('查看门派要求', dict(kind='school', op='requirements')))
        from native946_npc_services import NpcServices
        return NpcServices(self.game)._menu(nid, text, choices, speaker=nid if key == LIZARD_MASTER else 0)

    def execute(self, nid, action):
        from native946_npc_quests import Quests
        from native946_npc_services import NpcServices

        npc = NpcServices(self.game).validate(nid)
        key = (npc['scene_id'],nid)
        master = MASTERS.get(key,SCHOOL_LEADERS.get(key))
        if master is None:
            raise ValueError('当前 NPC 不办理师门事务。')
        if action['op'] == 'join':
            if key in SCHOOL_LEADERS:
                raise ValueError('请向传功师父石羿办理拜师。')
            if key in SUBSTITUTE_INSTRUCTORS or master==19:
                raise ValueError('请向对应掌门办理拜师。')
            return choose_school(self.game, master)
        if action['op']=='master':
            return self.navigate_to_master(master)
        if key in SCHOOL_LEADERS and action['op'] in ('task','supply','supply_shop','learn'):
            raise ValueError('请向传功师父石羿办理传功和师门任务。')
        if action['op']=='west_offer' or action['op']=='west_join':
            if master!=19:
                raise ValueError('请找魔西天的六耳悟空。')
            return self.admit_west(nid,confirm=action['op']=='west_join')
        if action['op'] == 'requirements':
            self.game.dialog_actions = []
            text = ('魔西天收飞升或封圣的男性角色。' if master==19 else
                    '请先到符合角色条件的门派拜师，再向自己的师父领取任务。')
            return self.game.menu(text,npc_id=nid)
        if action['op'] == 'task':
            return Quests(self.game).accept('school', master, giver=nid)
        if key in (BUZHOU_MASTER, LIZARD_MASTER) and action['op'] == 'leave':
            self.game.dialog_open = False
            self.game.dialog_actions = []
            self.game.dialog_npc = None
            return packet(1183, ['string'], ['dlg_npc_chat'])
        if key in (BUZHOU_MASTER, LIZARD_MASTER) and action['op'] == 'task_menu':
            Quests(self.game).validate_giver(nid, 'school')
            return self.menu(npc, tasks_only=True)
        if action['op'] in (*MASTER_LOCAL_SERVICES, 'school_service_confirm'):
            if key != PRIMARY_MASTERS[master]:
                raise ValueError('请向本门师父办理。')
            from native946_school_services import execute
            return execute(self.game,nid,master,action)
        if action['op'] == 'practice_reward':
            if key != PRIMARY_MASTERS[master]:
                raise ValueError('请向本门师父领奖。')
            from native946_local_guides import claim_practice
            return claim_practice(self.game,nid)
        if action['op'] in ('auto_start', 'auto_stop', 'task_rules'):
            quests = Quests(self.game)
            quests.validate_giver(nid, 'school')
            if action['op'] == 'task_rules':
                self.game.dialog_actions = []
                return self.game.menu(f'拜师后达到{TASK_LEVEL}级可领取师门任务。每天最多领取{TASK_LIMIT}次，'
                                      '每十环为一轮。巡逻需胜两场。自动师门可在完成后继续领下一环。', npc_id=nid)
            if action['op'] == 'auto_start':
                from native946_npc_quests import day_key
                if self.progression.snapshot()['level'] < TASK_LEVEL:
                    raise ValueError(f'达到{TASK_LEVEL}级后可开启自动师门。')
                if not self.game.party_service.participants(self.game):
                    raise ValueError('跟随队长时请先暂离队伍，再开启自动师门。')
                self.game._auto_school = {'day': day_key(), 'master': key, 'branch': master,
                                          'route': None, 'next': monotonic()}
                text = '进入自动师门模式。当前任务完成后，将返回师父处领取下一环。'
            else:
                self.game._auto_school = None
                text = '已结束自动师门。'
            self.game.dialog_open = False
            self.game.dialog_actions = []
            self.game.dialog_npc = None
            return packet(1183, ['string'], ['dlg_npc_chat']), screen_notice(text)
        if action['op'] in ('supply','supply_shop'):
            quests = Quests(self.game)
            quests.validate_giver(nid,'school')
            with self.progression.transaction(write=False) as db:
                row = self.supply_task(db,action['task'])
                if action['op']=='supply':
                    if (npc['scene_id'],nid)!=(row['scene'],row['npc']):
                        raise ValueError('请向发放这项寻物的师父交付。')
                    count = sum(item['amount'] for item in available_supply(db,self.progression.cid,row['supply_item']))
            if action['op']=='supply_shop':
                return self.supply_shop()
            session_id = randbelow((1<<30)-1)+1
            self.game.npc_service_session = dict(kind='school_supply',npc=nid,scene=row['scene'],
                                                 expires=monotonic()+180,id=session_id,task=row['id'])
            self.game.dialog_open = False
            self.game.dialog_actions = []
            self.game.dialog_npc = None
            return (packet(1183,['string'],['dlg_npc_chat']),
                    packet(3100,['number','byte','string',('array','number'),('array','number'),('array','number'),'number'],
                           [nid,1,supply_text(row)[0],[row['supply_item']],[row['supply_amount']],[count],session_id]))
        if action['op'] == 'talent':
            from native946_role_growth import school, talent_panel, talents
            cid = self.progression.cid
            with self.progression.transaction(write=False) as db:
                if base_school(school(db, cid)) != master:
                    raise ValueError('请向本门派师父查看天赋。')
                return (packet(1183, ['string'], ['dlg_npc_chat']), talent_panel(db, cid, talents(db, cid)))
        if action['op'] != 'learn':
            raise ValueError('师门选项已失效。')
        with self.progression.transaction(write=False) as db:
            snapshot = self.progression._snapshot(db)
            self.validate_learning(db, master, snapshot)
            response = self.learning_packet(db, master, snapshot)
        self.game.npc_service_session = dict(kind='school', npc=nid, scene=npc['scene_id'], expires=monotonic()+120)
        self.game.dialog_open = False
        self.game.dialog_actions = []
        self.game.dialog_npc = None
        return (packet(1183, ['string'], ['dlg_npc_chat']), response)

    def validate_learning(self, db, master, snapshot):
        owned = base_school(snapshot['school'])
        if not owned or (owned != master and master not in learning_schools(db, self.progression.cid)):
            raise ValueError('请先拜师，再向相符的门派师父学习。')

    def navigate_to_master(self, school):
        from native946_map import load_map
        from native946_navigation import npc_auto_trace
        from native946_world import world

        scene,nid=PRIMARY_MASTERS[school]
        npc=world().npc(scene,nid)
        x,y=load_map(scene).nearest_walkable(npc['x']//20,npc['y']//20)
        return packet(1183,['string'],['dlg_npc_chat']),npc_auto_trace(scene,x,y,nid,npc['name'])

    def admit_west(self,nid,*,confirm):
        from native946_npc_quests import active_records
        from native946_npc_services import NpcServices
        from native946_role_battle import profile
        from native946_role_growth import WEST_SECONDARY_SCHOOLS, school, talent_panel, talents

        cid=self.progression.cid
        with self.progression.transaction(write=confirm) as db:
            target=west_school(db,cid)
            current=school(db,cid)
            if not target or base_school(current)==19:
                raise ValueError('仅飞升或封圣且尚未加入魔西天的男性角色可以办理。')
            if any(row['kind']=='school' for row in active_records(db,cid)):
                raise ValueError('请先完成或取消当前师门任务，再拜入魔西天。')
            allowed=set(EXTRA_SKILLS[19])
            for branch in (19,*WEST_SECONDARY_SCHOOLS[target//100]):
                allowed.update(SKILLS[branch])
            if target//100!=7:
                allowed.update((6100,6101))  # Local training skills for non-巫 characters.
            forgotten=[row[0] for row in db.execute('SELECT skill_id FROM learned_skills WHERE character_id=? ORDER BY skill_id',(cid,))
                       if row[0] in SCHOOL_SKILLS and row[0] not in allowed]
            if not confirm:
                text='首次拜入免费。确认后，魔西天成为你的主门派。'
                if forgotten:
                    text+='#r将遗忘以下法术：'+'、'.join(profile(skill)['name'] for skill in forgotten)
                return NpcServices(self.game)._menu(nid,text,[
                    ('确认拜入魔西天',dict(kind='school',op='west_join')),
                ])
            for skill in forgotten:
                db.execute('DELETE FROM learned_skills WHERE character_id=? AND skill_id=?',(cid,skill))
                db.execute('DELETE FROM skill_shortcuts WHERE character_id=? AND skill_id=?',(cid,skill))
            db.execute('DELETE FROM skill_shortcuts WHERE character_id=? AND skill_id=?',
                       (cid, flight_stunt_id(current)))
            save(db,cid,'school',dict(school=target))
            reply=talent_panel(db,cid,talents(db,cid))
        self.game.npc_service_session=None
        self.game.dialog_open=False
        return self.game.defer_packets(lambda:(packet(1183,['string'],['dlg_npc_chat']),reply,*self.game.sync_progression()))

    def fly_home(self,requested=None,*,stunt=None):
        snapshot = self.progression.snapshot()
        owned=snapshot['school']
        branch=base_school(owned)
        destinations = [branch] if branch in PRIMARY_MASTERS else []
        if destinations and snapshot['flight_level']:
            with self.progression.transaction(write=False) as db:
                secondary = learning_schools(db,self.progression.cid)
            destinations.extend(p for p in secondary if p not in destinations and any(
                skill in snapshot['skills'] for skill in SKILLS.get(p,())))
        if stunt is not None:
            if stunt != flight_stunt_id(owned):
                raise ValueError('尚未学习此飞行特技。')
            if self.game.battle:
                raise ValueError('战斗中不能使用师门飞行。')
        if requested is None:
            return packet(1763,[('array','number'),'number'],[destinations,1])
        if requested == owned:
            requested = branch
        if requested not in destinations:
            raise ValueError('当前飞行特技没有解锁该门派。')
        return self.fly_to(*PRIMARY_MASTERS[requested], requested)

    def fly_points(self, scene, index=None):
        from native946_progression import lua_data
        from native946_world import world

        nid = FLIGHT_POINTS.get(scene)
        snapshot = self.progression.snapshot()
        allowed = nid and flight_stunt_id(snapshot['school']) and snapshot['flight_level'] >= 2
        if index is not None:
            if index != 1 or not allowed:
                raise ValueError('本服飞行特技尚未解锁该落点。')
            return self.fly_to(scene,nid,index)
        npc = world().npc(scene,nid) if allowed else None
        # dlg_current_map derives the index from ipairs and reads x/y as a pair.
        points = [[npc['x'],npc['y']]] if npc else []
        return packet(1762,['number','string',('array','number')],[scene,lua_data(points),[2] if points else []])

    def fly_to(self, scene, nid, destination):
        from native946_map import load_map
        from native946_world import world

        if self.game.battle:
            raise ValueError('战斗中不能使用师门飞行。')
        catalog=world()
        origin=self.game.character['map_id']
        if not self.flight_origin_available():
            raise ValueError('当前场景不能使用师门飞行。')
        npc=catalog.npc(scene,nid)
        arrival=load_map(scene).nearest_walkable(npc['x']//20,npc['y']//20)
        if (origin,self.game.character['x'],self.game.character['y'])==(scene,*arrival):return ()
        replies = self.game.change_scene(dict(id='school-flight',to=scene,arrival=arrival),
                                         (origin,scene,destination,'school-flight'))
        return (*replies,*self.progression.skill_packets())

    def flight_origin_available(self):
        from native946_world import world

        catalog = world()
        origin = self.game.character['map_id']
        return origin in catalog.scenes and bool(catalog.portals.get(origin)
                                                or any(scene == origin for scene,_ in catalog.transports))

    def supply_shop_target(self):
        from native946_npc_services import DATA
        from native946_map import load_map
        from native946_world import world

        shop = DATA['medicine_shop']
        scene,nid = shop['scene'],shop['npc_id']
        npc = world().npc(scene,nid)
        x,y = load_map(scene).nearest_walkable(npc['x']//20,npc['y']//20)
        return scene,x,y,nid,npc['name']

    def supply_shop(self):
        from native946_navigation import npc_auto_trace

        return npc_auto_trace(*self.supply_shop_target())

    def supply_task(self, db, task_id):
        from native946_npc_quests import active_records
        from native946_role_growth import school

        cid = self.progression.cid
        row = next((row for row in active_records(db,cid) if row['id']==task_id and row['supply_item']),None)
        if not row or row['branch']!=base_school(school(db,cid)):
            raise ValueError('当前没有这项可以办理的师门寻物。')
        return row

    def submit_supply(self, session_id, option):
        from native946_npc_services import NpcServices

        session = NpcServices(self.game)._session('school_supply')
        if session_id!=session['id'] or option!=1:
            raise ValueError('交物选项或会话已失效。')
        return self.deliver_supply(session['task'],session['scene'],session['npc'])

    def deliver_supply(self, task_id, scene, nid):
        from native946_npc_quests import Quests
        from native946_mall_uses import consume

        Quests(self.game).validate_giver(nid,'school')
        cid = self.progression.cid
        with self.progression.transaction() as db:
            row = self.supply_task(db,task_id)
            if (scene,nid)!=(row['scene'],row['npc']):
                raise ValueError('请向发放这项寻物的师父交付。')
            items = available_supply(db,cid,row['supply_item'])
            remaining = row['supply_amount']
            if sum(item['amount'] for item in items)<remaining:
                raise ValueError('可用物品栏没有足够的上交物品。')
            for item in items:
                amount = min(remaining,item['amount'])
                consume(db,item,amount)
                remaining -= amount
                if not remaining:
                    break
            value = self.settle_errand(db,row,'supply')
        self.game.npc_service_session = None
        return self.errand_reply('supply',SUPPLY_UI,value)

    def buy_auto_supply(self, task_id, nid):
        from native946_npc_services import NpcServices
        from native946_shops import Shops

        npc = NpcServices(self.game).validate(nid,'medicine')
        with self.progression.transaction() as db:
            row = self.supply_task(db, task_id)
            item = row['supply_item']
            have = sum(stock['amount'] for stock in available_supply(db,self.progression.cid,item))
            missing = row['supply_amount'] - have
            if missing <= 0:
                return ()
            if item not in SUPPLY_MEDICINES:
                raise ValueError('该任务物品需要手动准备。')
            Shops(self.game).purchase_ordinary(db,npc,'medicine',item,missing)
        self.progression._cached_snapshot = None
        return (*self.progression.attribute_packets(),*self.progression.item_packets(),
                screen_notice(f'已购买师门寻物所缺的物品，共{missing}件。'))

    def settle_errand(self, db, row, objective):
        from native946_npc_quests import reward, settle_reward

        snapshot = self.progression._snapshot(db)
        if row['branch']!=base_school(snapshot['school']):
            raise ValueError('当前角色门派与任务不符。')
        value = reward('school',snapshot['level'],row['ring'],Random(row['token']),row['branch'])
        receipt = f'school-{objective}:{row["id"]}'
        pet = self.game.summons.active()
        if pet:
            self.game.summons.settle(pet['id'],receipt,pet['hp'],pet['mp'],True,experience=value['guardian_experience'])
            value['guardian_names'] = (pet['name'],)
        else:
            value['guardian_experience'] = 0
        settle_reward(db,self.game,row,row['ring'],receipt,value)
        return value

    def errand_reply(self, objective, panel, value):
        panels = ('dlg_npc_chat', panel) if self.game.dialog_open and panel != 'dlg_npc_chat' else (panel,)
        self.game.dialog_open = False
        self.game.dialog_actions = []
        self.game.dialog_npc = None
        return self.game.defer_packets(lambda:(
            *(packet(1183,['string'],[name]) for name in panels),*self.game.sync_progression(),
            *reward_packets(OBJECTIVE_NAMES[objective],value,value.get('guardian_names',())),
            *completion_packets(value), *self.after_task(value)))

    def after_task(self, value):
        auto = getattr(self.game, '_auto_school', None)
        if value.get('school_day_done'):
            if auto:
                self.game._auto_school = None
                return (screen_notice('#Y当日师门已全部完成,结束自动师门#n'),)
            return (screen_notice('#Y当日师门已全部完成#n'),)
        if auto:
            auto['route'] = None
            auto['next'] = monotonic() + 1.5
        branch = auto['branch'] if auto else base_school(self.progression.snapshot()['school'])
        master = PRIMARY_MASTERS.get(branch)
        if master and self.game.character['map_id'] != master[0] and self.flight_origin_available():
            return (screen_notice('#Y您已经完成任务，用门派师父传授的“#G飞行技能#Y”可以快速飞回门派，'
                                  f'每天可领取#G{TASK_LIMIT}#Y次任务#n'),)
        return ()

    def close_auto_dialog(self):
        if not self.game.dialog_open:
            return ()
        self.game.dialog_open = False
        self.game.dialog_actions = []
        self.game.dialog_npc = None
        return (packet(1183, ['string'], ['dlg_npc_chat']),)

    def auto_tick(self, now=None):
        from native946_npc_quests import day_key

        auto = getattr(self.game, '_auto_school', None)
        if not auto or self.game.battle is not None or getattr(self.game,'_party_refresh_pending',False):
            return ()
        now = monotonic() if now is None else now
        if now < auto['next']:
            return ()
        auto['next'] = now + 1
        role = self.progression.snapshot()
        if (auto['day'] != day_key() or base_school(role['school']) != auto['branch'] or role['hp'] <= 0
                or not self.game.party_service.participants(self.game)):
            self.game._auto_school = None
            return (screen_notice('自动师门已结束，请向本门派师父重新开启。'),)
        try:
            return self.advance_auto_school(auto, now)
        except ValueError as error:
            self.game._auto_school = None
            return (screen_notice(f'自动师门已结束。{error}'),)

    def advance_auto_school(self, auto, now):
        from native946_map import load_map
        from native946_navigation import npc_auto_trace
        from native946_npc_quests import Quests
        from native946_world import world

        quests = Quests(self.game)
        row = quests.active('school')
        character = self.game.character
        previous = ()
        returning = False
        if row and row['school_objective'] == 'patrol':
            if self.game.dialog_open:
                return ()
            row, previous = quests.relocate_auto_patrol(row, *auto['master'])

        def at_target(target, radius):
            scene,x,y,_,_ = target
            return (character['map_id'] == scene and
                    max(abs(character['x']-x), abs(character['y']-y)) <= radius)

        if row:
            target = (row['scene'], row['x'], row['y'], row['npc'], row['name'])
            key = ('task', row['id'], bool(row['patrol_first_battle']))
            objective = row['school_objective']
            if objective == 'patrol' and at_target(target,PATROL_RADIUS):
                entry = quests.start(row)
                return (*previous,*entry,*self.game.party_service.drain(self.game))
            if objective == 'visit' and at_target(target,12):
                action = dict(kind='npc_quest', op='visit', task=row['id'])
                if not self.game.dialog_open:
                    reply = quests.visit_menu(row['npc'])
                    if reply is None:
                        raise ValueError('找不到本环指定的联系人。')
                    return (reply,)
                if self.game.dialog_npc == row['npc'] and action in self.game.dialog_actions:
                    return quests.execute(row['npc'],action)
                return ()
            if objective == 'supply':
                with self.progression.transaction(write=False) as db:
                    have = sum(item['amount'] for item in available_supply(db,self.progression.cid,row['supply_item']))
                if have < row['supply_amount']:
                    if row['supply_item'] not in SUPPLY_MEDICINES:
                        raise ValueError('该任务物品需要手动准备。')
                    target = self.supply_shop_target()
                    key = ('buy',row['id'])
                    if at_target(target,12) and self.auto_dialog_matches(target[3],'medicine'):
                        updates = self.buy_auto_supply(row['id'],target[3])
                        return (*self.close_auto_dialog(),*updates)
                elif at_target(target,12) and self.auto_dialog_matches(row['npc'],'school'):
                    return self.deliver_supply(row['id'],row['scene'],row['npc'])
                else:
                    returning = True
        else:
            with self.progression.transaction(write=False) as db:
                progress = db.execute("SELECT accepted FROM native_npc_activity_progress WHERE character_id=? "
                                      "AND kind='school' AND day=?", (self.progression.cid,auto['day'])).fetchone()
            if progress and progress['accepted'] >= TASK_LIMIT:
                self.game._auto_school = None
                return (screen_notice('今日师门领取次数已用完，结束自动师门。'),)
            scene, nid = auto['master']
            npc = world().npc(scene,nid)
            x,y = load_map(scene).nearest_walkable(npc['x']//20,npc['y']//20)
            target = (scene,x,y,nid,npc['name'])
            key = ('master', scene, nid)
            if at_target(target,12) and self.auto_dialog_matches(nid,'school'):
                reply = quests.accept('school', auto['branch'], giver=nid)
                auto['route'] = None
                return (*self.close_auto_dialog(),*reply)
            returning = True
        if returning and not self.game.dialog_open and (target[0],target[3]) == PRIMARY_MASTERS.get(auto['branch']):
            if not at_target(target,12) and self.flight_origin_available():
                from native946_codec import number
                from native946_login import MessageReader

                auto['route'] = None
                reply = self.game.receive(1765,MessageReader(number(auto['branch'])))
                return (reply,) if isinstance(reply,bytes) else reply or ()
        if self.game.dialog_open or (auto['route'] == key and now < auto.get('route_retry',0)):
            return previous
        auto['route'] = key
        auto['route_retry'] = now + 30
        return (*previous,npc_auto_trace(*target))

    def auto_dialog_matches(self, nid, kind):
        if not self.game.dialog_open:
            return True
        if self.game.dialog_npc != nid:
            return False
        return any(isinstance(action,dict) and action.get('kind') == kind
                   for action in self.game.dialog_actions)

    def learning_packet(self, db, master, snapshot):
        return packet(701, [('array', 'learn_skill_info'), 'number'],
                      [skill_rows(master, snapshot), contribution(db, self.progression.cid)])

    def learn(self, skill, times):
        from native946_npc_services import NpcServices

        session = NpcServices(self.game)._session('school')
        master = MASTERS[(session['scene'], session['npc'])]
        with self.progression.transaction() as db:
            cid = self.progression.cid
            snapshot = self.progression._snapshot(db)
            self.validate_learning(db, master, snapshot)
            row = next((row for row in skill_rows(master, snapshot) if row['skill_id'] == skill), None)
            if row is None:
                raise ValueError('未达到法术的学习等级，或此师父不传授该法术。')
            rank = row['cur_value']
            remaining = row['max_value'] - rank
            if times not in (1, 10) or remaining <= 0:
                raise ValueError('当前只能单次或批量修习，且不可超过熟练度上限。')
            times = min(times, remaining)
            gold = times * row['next_money']
            points = times * row['next_contribute']
            if snapshot['gold'] < gold or contribution(db, cid) < points:
                raise ValueError('金钱或师门贡献不足。')
            db.execute('UPDATE characters SET gold=gold-? WHERE id=?', (gold, cid))
            add_contribution(db, cid, -points)
            db.execute('INSERT INTO learned_skills VALUES(?,?,?) ON CONFLICT(character_id,skill_id) '
                       'DO UPDATE SET rank=excluded.rank', (cid, skill, rank + times))
            response = self.learning_packet(db, master, self.progression._snapshot(db))
        return self.game.defer_packets(lambda: (*self.progression.sync(), response, text_notice('法术修习已保存。')))
