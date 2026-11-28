"""百年后的来客与神器试炼的对话、传送、战斗和角色进度。"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from random import Random
from secrets import token_hex
from time import monotonic, time

from native946_codec import packet, screen_notice, text_notice
from native946_role_systems import state, save, day
from native946_wilderness import WildBattle


def numbers(mid, *values):
    return packet(mid, ['number'] * len(values), values)

SYSTEM = 'field_trial'
DEFAULTS = dict(runs={}, visitor_rewards=0, relic_rewards=0, relic_stars=0,
                relic_claimed=[], relic_week='', high_day='',
                visitor_high=0, relic_high=0)

VISITOR_GIVER = (2001, 7510)      # 长安·苍穹月
RELIC_GIVER = (2001, 6859)        # 长安·罗炎
VISITOR_TARGET = (2431, 7511)     # 骊山·苍穹月
RELIC_TARGET = (2384, 6809)       # 狮驼岭·散逸的阴阳之气

VISITOR = dict(
    key='visitor', name='百年后的来客', giver=VISITOR_GIVER,
    scene=2431, arrival=(110, 140), target=VISITOR_TARGET,
    target_cell=(111, 127), target_name='苍穹月', scene_name='骊山',
)
RELIC = dict(
    key='relic', name='神器试炼', giver=RELIC_GIVER,
    scene=2384, arrival=(16, 7), target=RELIC_TARGET,
    target_cell=(16, 7), target_name='散逸的阴阳之气', scene_name='狮驼岭',
)
TRIALS = {VISITOR['key']: VISITOR, RELIC['key']: RELIC}

# 百年后的来客：真服是连续三段（骊山 → 骊山古道 → 百年前的长安城），
# 每段与本图苍穹月对话后开打，打完自动送进下一段。
VISITOR_STAGES = [
    dict(scene=2431, npc=7511, arrival=(110, 140), cell=(111, 127), scene_name='骊山',
         brief='百年前的大唐，便是从这里开始崩坏的。少侠既来了，就先接下这一阵。',
         enemies=[('突厥刀将', 3129), ('突厥弩将', 3128), ('士兵', 2082), ('天兵', 2067)]),
    dict(scene=2432, npc=7517, arrival=(110, 111), cell=(121, 96), scene_name='五原',
         brief='追兵已追到古道。再挡他们一次，后面的路才走得下去！',
         enemies=[('天兵', 2067), ('天将', 2068), ('士兵', 2082), ('突厥刀将', 3129)]),
    dict(scene=2433, npc=7518, arrival=(270, 90), cell=(285, 77), scene_name='潼关',
         brief='前面便是百年前的长安城。最后一战，随我来！',
         enemies=[('天将', 2068), ('御鬼圣君', 3230), ('天兵', 2067), ('突厥弩将', 3128)]),
]
VISITOR_TARGETS = {(stage['scene'], stage['npc']): index
                   for index, stage in enumerate(VISITOR_STAGES)}

# 【刷怪】真服副本里不是点一下苍穹月就开打：场上会先刷出一批巡守妖兵，
# 走到怪身边点它才进入战斗，清完这一段的妖兵才轮得到与本图苍穹月交手。
# 怪物位置由「角色 + 段数」确定性生成，不写进存档；存档只记已清掉的怪
# （run['dead']），因此刷新场景包不会触发写事务，重登也不会让打过的怪复活。
FIELD_MOB_BASE = 8_750_000
FIELD_MOB_STRIDE = 100
VISITOR_MOBS = 5
MOB_MIN_GAP = 3
MOB_COUNT = {'简单': 2, '普通': 3, '困难': 3, '挑战': 4}   # 单场小怪战敌人数量

# 神器试炼：狮驼岭的散逸阴阳之气会引来守护怨灵（真怪造型）。
RELIC_ENEMIES = [('小钻风', 3279), ('小疾风', 3202), ('青冥妖', 3177),
                 ('鬼蜥', 3270), ('巡山妖', 3201)]

# 难度 → 战斗强度/怪物数量（真服难度只影响敌人与奖励，不改角色属性）
DIFFICULTY_POWER = {'简单': 1.0, '普通': 1.2, '困难': 1.35, '挑战': 1.7}
DIFFICULTY_LEVEL = {'简单': 0, '普通': 6, '困难': 10, '挑战': 16}
DIFFICULTY_COUNT = {'简单': 3, '普通': 4, '困难': 4, '挑战': 5}

VISITOR_DAILY_HIGH = 10          # 每日高额奖励次数（百年后的来客）
RELIC_DAILY_HIGH = 30            # 每日高额奖励次数（神器试炼，真服写明 30 次）
RELIC_BOX_STARS = (10, 20, 30)   # 每周★门槛：各可领一次嘉奖礼盒
# 掉落必须用「真实物品编号」（progression.DATA['items'] 里的 type），
# 不能直接抄任务池 2001~2014 —— 那是参考物编号，只有经过 reward_map
# 才能换成真实物品；直接当 type 用会在 stack_limit 里 KeyError 掉线。
QUEST_ITEM_POOL = [45161, 6110, 6111, 6112, 6113, 6109, 6106, 6107, 6108, 6128]
RARE_ITEM = 6035

# 真服对话：苍穹月（截图逐字，正文为空，七行全是可点选项）
VISITOR_LABELS = ['尽管交给我吧', '不知做些什么', '我想过去看一看',
                  '我想查询队员获得的高额奖励次数', '狮山美景（挂机）',
                  '兑换限定守护猫妖', '我还没准备好，稍后再来']
# 真服难度窗：简单 / 困难 / 挑战
VISITOR_DIFFICULTIES = [
    ('简单', '简单'),
    ('困难（达到封圣120级可挑战）', '困难'),
    ('挑战（达到封圣185级可挑战）', '挑战'),
]
# 真服罗炎对话正文（截图逐字）
RELIC_TEXT = ('传闻云鹏王的法宝阴阳瓶被孙悟空用救命毫毛钻破，狮驼岭上常见许多吸收了阴阳之气的神石，'
              '是铸造和培养绝世神器的宝物。众位少侠可组队结伴前往寻找，必定有所收获！'
              '【领取双倍时间后可以获得双倍经验奖励】（角色等级达到130级后击杀怪物有机会获得'
              '绝世神器合成、培养道具。）')
RELIC_LABELS = ['前往狮驼岭试炼', '领取嘉奖礼盒（需飞升140级）', '查看队员完成次数', '如何进行试炼']
# 真服难度：以图为准（110-139 / 140-159 / 160 以上）
RELIC_DIFFICULTIES = [
    ('简单（适合110-139级玩家）', '简单'),
    ('普通（适合140-159级玩家）', '普通'),
    ('困难（适合160级以上玩家）', '困难'),
]
# 真服「如何进行试炼」说明（截图逐字，数字绿色高亮）
RELIC_HELP = (
    '●本玩法最低参与等级为转生#G110#n级，队伍人数需至少#G3#n人；#r'
    '●每天可获得#G30#n次高额奖励；#r'
    '●每场战斗分为简单难度（适合#G110-139#n级玩家）、普通难度（适合#G140-159#n级玩家）、'
    '困难难度（适合#G160#n级以上玩家）；#r'
    '●每周每场战斗首次击杀可获得一个#G★#n标记，累积达到#G10★、20★、30★#n时可分别领取一个'
    '嘉奖礼盒，领奖和击杀标记每周一#G0#n点清空。')


def _menu(game, nid, text, choices):
    game.dialog_actions = [action for _, action in choices]
    return game.menu(text, [label for label, _ in choices], nid)


def _giver(game, nid):
    key = (game.character['map_id'], nid)
    if key == VISITOR_GIVER: return VISITOR
    if key == RELIC_GIVER: return RELIC
    return None


def is_mob(nid):
    return FIELD_MOB_BASE <= nid < FIELD_MOB_BASE + len(VISITOR_STAGES) * FIELD_MOB_STRIDE


@lru_cache(maxsize=8)
def _stage_cells(index):
    """本段任务点周围可达的格子（用于刷怪落点）。"""
    from native946_map import load_map
    from native946_npc_quests import encounter_cells
    stage = VISITOR_STAGES[index]
    anchor = load_map(stage['scene']).nearest_walkable(*stage['cell'])
    return encounter_cells(stage['scene'], anchor)


def _spawn(index, seed):
    """按段数确定性刷出一批妖兵：分布在任务点附近，彼此至少隔 3 格。"""
    stage = VISITOR_STAGES[index]
    cells = list(_stage_cells(index))
    rng = Random('%s:%s' % (seed, stage['scene']))
    rng.shuffle(cells)
    picked = []
    for cell in cells:
        if all(max(abs(cell[0] - x), abs(cell[1] - y)) >= MOB_MIN_GAP for x, y in picked):
            picked.append(cell)
        if len(picked) >= VISITOR_MOBS:
            break
    while len(picked) < VISITOR_MOBS:
        picked.append(cells[len(picked) % len(cells)])
    mobs = []
    for position, (x, y) in enumerate(picked):
        name, shape = stage['enemies'][position % len(stage['enemies'])]
        mobs.append(dict(id=FIELD_MOB_BASE + index * FIELD_MOB_STRIDE + position,
                         x=x, y=y, name=name, shape=shape))
    return mobs


def _visitor_run(game):
    return (_progress(game).get('runs') or {}).get('visitor')


def _mobs(game):
    """返回 (段号, 本场景里还没被打掉的妖兵)。不在本段场景时返回空列表。"""
    if game.character['map_id'] not in {scene for scene,_ in VISITOR_TARGETS}:
        return 0,[]
    run = _visitor_run(game) or {}
    if not run:
        return 0, []
    index = int(run.get('stage', 0))
    if index >= len(VISITOR_STAGES):
        return index, []
    if VISITOR_STAGES[index]['scene'] != game.character['map_id']:
        return index, []
    dead = {int(n) for n in (run.get('dead') or [])}
    return index, [m for m in _spawn(index, run.get('id',game.progression.cid))
                   if m['id'] not in dead]


class FieldTrials:
    """副本内任务怪的场景包：刷出、移动视野内补发、清掉后删除。"""

    def __init__(self, game):
        self.g = game

    def scene_packets(self, force=False):
        game = self.g
        previous = set() if force else set(getattr(game, '_field_mob_visible', ()))
        _, rows = _mobs(game)
        current = {mob['id'] for mob in rows}
        replies = [numbers(1604, nid) for nid in sorted(previous - current)]
        for mob in rows:
            if force or mob['id'] not in previous:
                replies.append(packet(1589, ['oci_npc'], [dict(
                    id=mob['id'], name=mob['name'], title='百年后的来客', type=mob['shape'],
                    class_type=1, x=mob['x'] * 20, y=mob['y'] * 20, dir=4, layer=1,
                    member_click=1)]))
        game._field_mob_visible = current
        return tuple(replies)

    def tick(self):
        now = monotonic()
        if now < getattr(self.g, '_field_mob_poll_at', 0):
            return ()
        self.g._field_mob_poll_at = now + 5
        return self.scene_packets()


def mob_look(game, nid):
    """点场景里的任务怪：近距离才可出手。"""
    index, rows = _mobs(game)
    mob = next((m for m in rows if m['id'] == nid), None)
    if mob is None:
        raise ValueError('这名妖兵已经离开了。')
    c = game.character
    if max(abs(mob['x'] - c['x']), abs(mob['y'] - c['y'])) > 12:
        return game.menu('请走近一些再出手。', npc_id=nid)
    stage = VISITOR_STAGES[index]
    game.dialog_actions = [dict(kind='field_trial', op='mob_fight', mob=nid),
                           dict(kind='field_trial', op='close')]
    return game.menu(mob['name'] + '在' + stage['scene_name'] + '一带巡守，挡住去路。',
                     ['出手', '离开'], nid)


def _progress(game):
    with game.progression.transaction(write=False) as db:
        return _fresh(state(db, game.progression.cid, SYSTEM, DEFAULTS))


def _team_counts(game, trial):
    cid=game.progression.cid
    key=trial['key']
    daily=VISITOR_DAILY_HIGH if trial is VISITOR else RELIC_DAILY_HIGH
    with game.progression.transaction(write=False) as db:
        team=game.party_service.team(cid,db)
        members=team['members'] if team and team['leader_id']==cid else [game.character]
        lines=[]
        for member in members:
            value=_fresh(state(db,member['id'],SYSTEM,DEFAULTS))
            name=member['name'].replace('#','＃')
            left=value[key+'_high']
            line=f'{name}  今日已获#G{daily-left}#n次高额奖励，剩余#G{left}#n次。'
            if trial is RELIC:
                line+=f'累计完成#G{value["relic_rewards"]}#n次，本周#G{value["relic_stars"]}#n★。'
            lines.append(line)
    return '#r'.join(lines)


def _week():
    now = datetime.now(timezone(timedelta(hours=8)))
    year, week, _ = now.isocalendar()
    return '%d-W%02d' % (year, week)


def _fresh(value):
    """每日高额次数与每周★标记的滚动重置（调用方决定是否落盘）。"""
    today = day()
    if value.get('high_day') != today:
        value['high_day'] = today
        value['visitor_high'] = VISITOR_DAILY_HIGH
        value['relic_high'] = RELIC_DAILY_HIGH
    stamp = _week()
    if value.get('relic_week') != stamp:
        value['relic_week'] = stamp
        value['relic_stars'] = 0
        value['relic_claimed'] = []
    return value


def _games(game,trial,difficulty):
    pairs=VISITOR_DIFFICULTIES if trial is VISITOR else RELIC_DIFFICULTIES
    if difficulty not in {mode for _,mode in pairs}:
        raise ValueError('无法选择该难度。')
    team=game.party_service.team(game.progression.cid)
    games=game.party_service.participants(game)
    if not team or team['leader_id']!=game.progression.cid or len(games)<3:
        raise ValueError('需要至少3人到齐，由队长发起。')
    active={row['character_id'] for row in team['members'] if not row['away']
            and game.party_service.online(row['character_id']) is not None}
    if active!={g.progression.cid for g in games}:
        raise ValueError('请等参战队友到齐。')
    for g in games:
        player=g.progression.snapshot()
        if trial is RELIC:
            level={'简单':110,'普通':140,'困难':160}[difficulty]
            advance=player['rebirth'] or player['ascension'] or player['sanctification']
        else:
            level={'简单':110,'困难':120,'挑战':185}[difficulty]
            advance=difficulty=='简单' or player['sanctification']
        if player['level']<level or not advance:
            raise ValueError(g.character['name']+'尚未达到该难度的参与条件。')
    return games

def _require_run(games,trial,run,*,mob=None):
    for g in games:
        member=_progress(g)['runs'].get(trial['key'],{})
        if not run.get('id') or member.get('id')!=run['id'] or member['stage']!=run['stage']:
            raise ValueError('队员任务不同步，请回发布使处重新领取。')
        if trial is VISITOR:
            dead=member.get('dead',[])
            if (mob is None and len(dead)<VISITOR_MOBS) or (mob is not None and mob['id'] in dead):
                raise ValueError('队员的巡守妖兵进度不同步。')

def _save_run(game, trial, difficulty):
    games=_games(game,trial,difficulty)
    run_id=token_hex(16)
    with game.progression.transaction() as db:
        for member in games:
            cid=member.progression.cid
            value = _fresh(state(db, cid, SYSTEM, DEFAULTS))
            value['runs'][trial['key']] = dict(id=run_id,difficulty=difficulty, scene=trial['scene'],
                                               npc=trial['target'][1], x=trial['target_cell'][0],
                                               y=trial['target_cell'][1], name=trial['target_name'],
                                               stage=0, dead=[], day=day())
            save(db, cid, SYSTEM, value)


def _transfer(game, trial, difficulty):
    from native946_map import load_map
    x, y = load_map(trial['scene']).nearest_walkable(*trial['arrival'])
    reply = game.change_scene(dict(id=-trial['scene'], to=trial['scene'], arrival=(x, y)),
                              ('field_trial', trial['scene']))
    notice = text_notice(
        '#G' + trial['name'] + '#n（' + difficulty + '）已开始。前往#R' + trial['scene_name']
        + '#n（' + str(trial['target_cell'][0]) + '，' + str(trial['target_cell'][1])
        + '）寻找#R' + trial['target_name'] + '#n。')
    return (*reply, notice)


def _stage_transfer(game, stage, request):
    from native946_map import load_map
    x, y = load_map(stage['scene']).nearest_walkable(*stage['arrival'])
    return game.change_scene(dict(id=-stage['scene'], to=stage['scene'], arrival=(x, y)),
                             ('field_trial', request))


def menu(game, npc):
    """命中两个发布使或副本内任务点 NPC 时返回真服对话，否则 None 交给原逻辑。"""
    key = (npc['scene_id'], npc['npc_id'])
    nid = npc['npc_id']
    if key not in (VISITOR_GIVER, RELIC_GIVER, RELIC_TARGET, *VISITOR_TARGETS):
        return None
    if key == VISITOR_GIVER:
        actions = [dict(kind='field_trial', op='visitor_accept'),
                   dict(kind='field_trial', op='visitor_rules'),
                   dict(kind='field_trial', op='visitor_look'),
                   dict(kind='field_trial', op='visitor_count'),
                   dict(kind='field_trial', op='visitor_idle'),
                   dict(kind='field_trial', op='visitor_cat'),
                   dict(kind='field_trial', op='close')]
        game.dialog_actions = actions
        return game.menu('', VISITOR_LABELS, nid)
    if key == RELIC_GIVER:
        actions = [dict(kind='field_trial', op='relic_accept'),
                   dict(kind='field_trial', op='relic_box'),
                   dict(kind='field_trial', op='relic_count'),
                   dict(kind='field_trial', op='relic_help')]
        game.dialog_actions = actions
        return game.menu(RELIC_TEXT, RELIC_LABELS, nid)
    if key in VISITOR_TARGETS or key == RELIC_TARGET:
        return _target_menu(game, key, nid)
    return None


def _target_menu(game, key, nid):
    close = ('离开', dict(kind='field_trial', op='close'))
    back = ('返回长安', dict(kind='field_trial', op='back'))
    runs = _progress(game).get('runs') or {}
    if key in VISITOR_TARGETS:
        index = VISITOR_TARGETS[key]
        run = runs.get('visitor')
        if not run:
            return _menu(game, nid, '你还没有接下百年后的来客。请先回长安找苍穹月。',
                         [close, back])
        stage = int(run.get('stage', 0))
        if stage >= len(VISITOR_STAGES):
            return _menu(game, nid, '这一趟百年因果已经了结。可以回长安复命，或再走一遍。',
                         [back, ('再走一趟', dict(kind='field_trial', op='restart',
                                                  difficulty=run.get('difficulty') or '简单'))])
        if stage > index:
            return _menu(game, nid, '这一段的差事已经了结了，去下一处找苍穹月吧。',
                         [back, close])
        if stage < index:
            return _menu(game, nid, '还没轮到这里。先去' + VISITOR_STAGES[stage]['scene_name']
                         + '找苍穹月。', [back, close])
        data = VISITOR_STAGES[index]
        dead = {int(n) for n in (run.get('dead') or [])}
        left = max(0, VISITOR_MOBS - len(dead))
        if left:
            text = ('（' + run['difficulty'] + '难度）' + data['brief']
                    + '#r此地尚有#G' + str(left) + '#n名妖兵未清，先清掉他们再来。')
            return _menu(game, nid, text,
                         [('查看妖兵方位', dict(kind='field_trial', op='mob_trace')), back])
        text = '（' + run['difficulty'] + '难度）' + data['brief']
        return _menu(game, nid, text,
                     [('迎战', dict(kind='field_trial', op='fight')), back])
    run = runs.get('relic')
    if not run:
        return _menu(game, nid, '你还没有接下神器试炼。请先回长安找罗炎。', [close])
    text = '一团散逸的阴阳之气在此盘旋，正是铸造绝世神器的宝物。'
    text += '#r（' + run['difficulty'] + '难度）上前收取，便会引来守护的怨灵。'
    return _menu(game, nid, text,
                 [('收取阴阳之气', dict(kind='field_trial', op='fight')), close])


def _difficulty_menu(game, trial, nid):
    if trial['key'] == 'visitor':
        text = '请选择任务难度，高难度任务将获得高额奖励'
        pairs = VISITOR_DIFFICULTIES
    else:
        text = '请选择任务难度，高难度任务将获得高额奖励'
        pairs = RELIC_DIFFICULTIES
    return _menu(game, nid, text,
                 [(label, dict(kind='field_trial', op='pick', trial=trial['key'], difficulty=difficulty))
                  for label, difficulty in pairs])


def execute(game, nid, action):
    trial = _giver(game, nid)
    op = action['op']
    if op == 'close':
        return game.close_npc_dialogue()
    if game.battle:raise ValueError('请先结束当前战斗。')
    if op == 'back':
        # 骊山没有地面传送门，任务点对话里保留一条回长安的退路，避免被困。
        from native946_map import load_map
        x, y = load_map(2001).nearest_walkable(573, 67)
        return game.change_scene(dict(id=-2001, to=2001, arrival=(x, y)), ('field_trial', 'back'))
    if op == 'mob_fight':
        return _start_mob_fight(game, nid)
    if op == 'mob_trace':
        index, rows = _mobs(game)
        if not rows:
            raise ValueError('此地的妖兵已经清完了。')
        c = game.character
        mob = min(rows, key=lambda m: abs(m['x'] - c['x']) + abs(m['y'] - c['y']))
        from native946_navigation import npc_auto_trace
        game.dialog_open = False
        game.dialog_actions = []
        return (packet(1183, ['string'], ['dlg_npc_chat']),
                npc_auto_trace(c['map_id'], mob['x'], mob['y'], mob['id'], mob['name']))
    if op in ('visitor_accept', 'relic_accept'):
        if trial is None: raise ValueError('请与活动发布使对话。')
        return _difficulty_menu(game, trial, nid)
    if op == 'pick':
        target = TRIALS.get(action.get('trial') or '')
        if target is None or target is not trial: raise ValueError('活动不存在。')
        _save_run(game, target, action.get('difficulty') or '简单')
        return _transfer(game, target, action.get('difficulty') or '简单')
    if op == 'restart':
        difficulty = action.get('difficulty') or '简单'
        _save_run(game, VISITOR, difficulty)
        return _transfer(game, VISITOR, difficulty)
    if op == 'visitor_rules':
        if trial is not VISITOR: raise ValueError('请在苍穹月处查询。')
        text = ('百年前的大唐遭逢大难，我与同伴自百年之后逆行而来。'
                '少侠若肯相助，可从简单、困难、挑战三档中择一而行。'
                '#r简单档门槛#G110#n级；困难需#G封圣120#n级，挑战需#G封圣185#n级。'
                '#r每日高额奖励次数有限，难度越高奖励越丰厚。')
        return _menu(game, nid, text, [('我明白了', dict(kind='field_trial', op='visitor_accept')),
                                       ('离开', dict(kind='field_trial', op='close'))])
    if op == 'visitor_look':
        if trial is not VISITOR: raise ValueError('请在苍穹月处查询。')
        from native946_world import world

        c=game.character
        scene=c['map_id']
        route=world().npc_route(scene,nid,1,(c['x'],c['y']),c)
        return game.change_scene(route,(scene,nid,1,'npc'))
    if op == 'visitor_count':
        if trial is not VISITOR: raise ValueError('请在苍穹月处查询。')
        text = _team_counts(game,VISITOR)
        return _menu(game, nid, text, [('返回', dict(kind='field_trial', op='visitor_accept')),
                                       ('离开', dict(kind='field_trial', op='close'))])
    if op == 'visitor_idle':
        return _menu(game, nid, '狮山美景暂未开放。',
                     [('离开', dict(kind='field_trial', op='close'))])
    if op == 'visitor_cat':
        return _menu(game, nid, '限定守护猫妖的兑换暂未开放。',
                     [('离开', dict(kind='field_trial', op='close'))])
    if op == 'relic_help':
        return _menu(game, nid, RELIC_HELP, [('返回', dict(kind='field_trial', op='relic_accept')),
                                             ('离开', dict(kind='field_trial', op='close'))])
    if op == 'relic_box':
        from native946_temp_inventory import award
        cid = game.progression.cid
        snapshot=game.progression.snapshot()
        if snapshot['level']<140 or not (snapshot['ascension'] or snapshot['sanctification']):
            raise ValueError('达到飞升140级才可领取嘉奖礼盒。')
        with game.progression.transaction() as db:
            value = _fresh(state(db, cid, SYSTEM, DEFAULTS))
            stars = int(value.get('relic_stars', 0) or 0)
            claimed = [int(n) for n in (value.get('relic_claimed') or [])]
            ready = [n for n in RELIC_BOX_STARS if stars >= n and n not in claimed]
            if ready:
                tier = ready[0]
                claimed.append(tier)
                value['relic_claimed'] = claimed
                save(db, cid, SYSTEM, value)
                amount = 1 + RELIC_BOX_STARS.index(tier)
                award(db, game.progression, f'relic-box:{cid}:{value["relic_week"]}:{tier}', {RARE_ITEM: amount})
                text = ('你领取了#G' + str(tier) + '★#n嘉奖礼盒，获得龙之骨 ×#G' + str(amount)
                        + '#n。#r本周击杀标记：#G' + str(stars) + '#n★。')
                game.progression._cached_snapshot=None
                return game.defer_packets(lambda:(*game.progression.item_packets(),
                    _menu(game, nid, text, [('返回', dict(kind='field_trial', op='relic_accept')),
                                            ('离开', dict(kind='field_trial', op='close'))])))
            text = ('嘉奖礼盒需飞升#G140#n级，并在每周击杀标记累积到#G10★、20★、30★#n时领取。'
                    '#r本周击杀标记：#G' + str(stars) + '#n★。')
            if all(n in claimed for n in RELIC_BOX_STARS):
                text += '#r本周三档嘉奖礼盒都已领取，下周一#G0#n点重置。'
            else:
                need = next(n for n in RELIC_BOX_STARS if n not in claimed)
                text += '#r还差#G' + str(max(0, need - stars)) + '#n★可领取下一档嘉奖礼盒。'
        return _menu(game, nid, text, [('返回', dict(kind='field_trial', op='relic_accept')),
                                       ('离开', dict(kind='field_trial', op='close'))])
    if op == 'relic_count':
        if trial is not RELIC: raise ValueError('请在罗炎处查询。')
        text = _team_counts(game,RELIC)
        return _menu(game, nid, text, [('返回', dict(kind='field_trial', op='relic_accept')),
                                       ('离开', dict(kind='field_trial', op='close'))])
    if op == 'fight':
        return _start_fight(game, nid)
    raise ValueError('这个选项暂时不可用。')


def _start_fight(game, nid):
    key = (game.character['map_id'], nid)
    runs = _progress(game).get('runs') or {}
    if key in VISITOR_TARGETS:
        index = VISITOR_TARGETS[key]
        run = runs.get('visitor')
        if not run:
            raise ValueError('请先回长安找苍穹月接取百年后的来客。')
        if int(run.get('stage', 0)) != index:
            raise ValueError('这一段已经打过了，请去找下一处的苍穹月。')
        trial, stage, difficulty = VISITOR, index, run.get('difficulty') or '简单'
    elif key == RELIC_TARGET:
        run = runs.get('relic')
        if not run:
            raise ValueError('请先回长安找罗炎接取神器试炼。')
        trial, stage, difficulty = RELIC, 0, run.get('difficulty') or '简单'
    else:
        raise ValueError('这里没有可以迎战的目标。')
    from native946_npc_quests import enter_quest_battle
    games=_games(game,trial,difficulty)
    _require_run(games,trial,run)
    return enter_quest_battle(game, games,
                              lambda: FieldTrialBattle(game, games, trial, difficulty, stage))


def _start_mob_fight(game, nid):
    """与场景里的任务怪开战：小怪战不推进段数，也不消耗高额次数。"""
    index, rows = _mobs(game)
    mob = next((m for m in rows if m['id'] == nid), None)
    if mob is None:
        raise ValueError('这名妖兵已经离开了。')
    c=game.character
    if max(abs(mob['x']-c['x']),abs(mob['y']-c['y']))>12:
        raise ValueError('请走近一些再出手。')
    run = _visitor_run(game) or {}
    difficulty = run.get('difficulty') or '简单'
    games=_games(game,VISITOR,difficulty)
    _require_run(games,VISITOR,run,mob=mob)
    from native946_npc_quests import enter_quest_battle
    return enter_quest_battle(
        game, games,
        lambda: FieldTrialBattle(game, games, VISITOR, difficulty, index, mob=mob))


# ------------------------------------------------------------------ 战斗与结算

def battle_reward(trial, difficulty, level, *, high, rng):
    """单场活动战斗的结算值：经验/现金/守护经验/道具，高额次数翻倍经验。"""
    power = DIFFICULTY_POWER.get(difficulty, 1.0)
    experience = int(level * level * 3 * power)
    if high:
        experience *= 2
    gold = int(level * (3 + 2 * power))
    drops = {}
    chance = {'简单': 50, '普通': 70, '困难': 85, '挑战': 100}.get(difficulty, 60)
    if rng.randint(1, 100) <= chance:
        amount = 1 if difficulty in ('简单', '普通') else rng.randint(1, 2)
        drops[rng.choice(QUEST_ITEM_POOL)] = amount
    rare = {'困难': 4, '挑战': 8}.get(difficulty, 0)
    if rare and rng.randint(1, 100) <= rare:
        drops[RARE_ITEM] = drops.get(RARE_ITEM, 0) + 1
    return dict(victory=True, experience=experience, gold=gold,
                guardian_experience=experience // 2, drops=drops, high=bool(high))


def reward_text(value):
    lines = ['您获得#G' + str(value.get('experience', 0)) + '#n经验']
    if value.get('guardian_experience'):
        lines.append('守护获得#G' + str(value['guardian_experience']) + '#n经验')
    if value.get('gold'):
        lines.append('您获得#G' + str(value['gold']) + '#n两现金')
    delivery = value.get('loot_delivery') or {}
    if delivery.get('bag'):
        lines.append('物品已放入道具栏。')
    if delivery.get('temporary'):
        lines.append('道具栏已满，物品已放入摘星袋。')
    if delivery.get('waiting'):
        lines.append('摘星袋已满，剩余奖励已保留。')
    if not value.get('high') and not value.get('mob'):
        lines.append('#r今日高额奖励次数已用完，本次为普通奖励。')
    return '#r'.join(lines)


class FieldTrialBattle(WildBattle):
    """百年后的来客 / 神器试炼 的活动战斗：固定阵容，胜利后在同一事务内写进度。"""

    def __init__(self, leader, games, trial, difficulty, stage=0, mob=None):
        self.trial = trial
        self.mob = mob
        self.difficulty = difficulty if difficulty in DIFFICULTY_POWER else '简单'
        self.stage = max(0, min(int(stage or 0), len(VISITOR_STAGES) - 1))
        level = max(1, max(g.progression.snapshot()['level'] for g in games))
        power = DIFFICULTY_POWER[self.difficulty]
        enemy_level = max(10, level + DIFFICULTY_LEVEL.get(self.difficulty, 0))
        pool = (VISITOR_STAGES[self.stage]['enemies']
                if trial['key'] == 'visitor' else RELIC_ENEMIES)
        if mob:
            count = MOB_COUNT.get(self.difficulty, 3)
        else:
            count = DIFFICULTY_COUNT.get(self.difficulty, 3)
        # 场景里的巡逻妖兵比本图的头目弱一档，清怪不该比决战还难。
        scale = 0.75 if mob else 1.0
        templates = []
        for index in range(count):
            name, shape = pool[index % len(pool)]
            hp = int((60 + enemy_level * 30) * power * scale)
            templates.append(dict(
                type=shape, name=name, guardian_type=0,
                combat_stats=dict(hpmax=hp, mpmax=120,
                                  attack=int((2 + enemy_level * 2) * power * scale),
                                  initiative=int(1 + enemy_level * 2),
                                  defense=enemy_level // 6)))
        zone = dict(scene_name=trial['name'] + ('·' + mob['name'] if mob else ''),
                    levels=[enemy_level, enemy_level],
                    enemies=templates, fixed_roster=True,
                    entry_notice=trial['name'] + '（' + self.difficulty + '）战斗开始。')
        super().__init__(leader, games, zone)

    def settle(self, db, victory):
        if self.state['settled']:
            return
        from native946_temp_inventory import award
        from native946_double_time import experience_rates

        now=int(time())
        for cid, g in self.games.items():
            if not db.execute('INSERT OR IGNORE INTO native_role_system_events VALUES(?,?,?,?)',
                              (cid,SYSTEM,self.id,now)).rowcount:
                continue
            value = _fresh(state(db, cid, SYSTEM, DEFAULTS))
            high = False
            if victory and not self.mob:
                key = 'visitor_high' if self.trial['key'] == 'visitor' else 'relic_high'
                left = int(value.get(key, 0) or 0)
                if left > 0:
                    value[key] = left - 1
                    high = True
            rng = Random('%s:%s:%s' % (self.id, cid, self.difficulty))
            reward = (battle_reward(self.trial, self.difficulty,
                                    g.progression.snapshot()['level'], high=high, rng=rng)
                      if victory else dict(victory=False, experience=0, gold=0,
                                           guardian_experience=0, drops={}, high=False))
            if victory and self.mob:
                # 巡逻怪只给基础收益（不占高额次数），免得刷小怪把高额次数刷空。
                reward['experience'] = max(1, reward['experience'] // 3)
                reward['guardian_experience'] = max(1, reward['experience'] // 2)
                reward['gold'] = max(1, reward['gold'] // 2)
                reward['mob'] = True
            pet_experience=reward['guardian_experience']
            neidan_rate=1
            if victory and self.trial is RELIC:
                hero_rate, guardian_rate, neidan_rate=experience_rates(db,cid,now)
                reward['experience']=reward['experience']*hero_rate//100
                reward['guardian_experience']=pet_experience*guardian_rate
            run = value['runs'].get(self.trial['key'])
            if victory and run:
                if self.mob:
                    dead = [int(n) for n in (run.get('dead') or [])]
                    if self.mob['id'] not in dead:
                        dead.append(self.mob['id'])
                    run['dead'] = dead
                elif self.trial['key'] == 'visitor':
                    run['stage'] = int(run.get('stage', 0)) + 1
                    run['dead'] = []
                    if run['stage'] >= len(VISITOR_STAGES):
                        value['visitor_rewards'] = int(value.get('visitor_rewards', 0)) + 1
                else:
                    value['relic_rewards'] = int(value.get('relic_rewards', 0)) + 1
                    value['relic_stars'] = int(value.get('relic_stars', 0)) + 1
            save(db, cid, SYSTEM, value)
            g.characters.settle_battle(g.character['owner'], cid,
                                       'field:%s:%s' % (self.id, cid), victory,
                                       experience=reward['experience'], gold=reward['gold'],
                                       advance_quest=False)
            g.auto_level()
            for pet in self.state['participants'][cid].values():
                g.summons.settle(pet['id'], self.id, pet['hp'], pet['mp'], victory,
                                 experience=reward['guardian_experience'],neidan_experience=pet_experience*neidan_rate,
                                 stunt_experience=pet_experience)
            if reward['drops']:
                reward['loot_delivery'] = award(
                    db, g.progression, self.id, reward['drops'],
                    offline=self.service.online(cid) is None)
            self.state.setdefault('rewards', {})[cid] = reward
        self.state['settled'] = True

    def finish_messages(self, cid, victory):
        g = self.games[cid]
        value = self.state.get('rewards', {}).get(cid, {})
        if not victory:
            return (text_notice('已离开' + self.trial['name'] + '战斗，本次没有结算奖励。'),)
        packets = [screen_notice(reward_text(value))]
        if self.mob:
            # 打死一只就从场上抹掉一只，并把剩余数量告诉玩家。
            visible = set(getattr(g, '_field_mob_visible', ()))
            visible.discard(self.mob['id'])
            g._field_mob_visible = visible
            packets.append(numbers(1604, self.mob['id']))
            run = (_progress(g).get('runs') or {}).get('visitor') or {}
            left = max(0, VISITOR_MOBS - len({int(n) for n in (run.get('dead') or [])}))
            if left:
                packets.append(text_notice('已清掉' + self.mob['name'] + '，此地还剩'
                                           + str(left) + '名妖兵。'))
            else:
                packets.append(text_notice('此地的妖兵已经清完，去苍穹月处复命。'))
            return tuple(packets)
        if self.trial['key'] == 'visitor':
            run = (_progress(g).get('runs') or {}).get('visitor') or {}
            stage = int(run.get('stage', 0))
            if stage < len(VISITOR_STAGES):
                data = VISITOR_STAGES[stage]
                packets.extend(_stage_transfer(g, data, data['scene']))
                packets.append(_dialog(g, data['npc'], data['brief']))
                return tuple(packets)
            last = VISITOR_STAGES[-1]
            packets.append(_dialog(g, last['npc'],
                                   '受你相助，百年前的这场大难算是化解了。回长安复命吧。'))
            return tuple(packets)
        packets.append(_dialog(g, RELIC_TARGET[1],
                               '阴阳之气已化作一团灵光落入你手中。'
                               '狮驼岭上还有许多神石，可再来试炼。'))
        return tuple(packets)


def _dialog(game, npc, text):
    game.dialog_open = True
    game.dialog_npc = npc
    game.dialog_actions = [dict(kind='field_trial', op='close')]
    return game.menu(text, ['离开'], npc_id=npc)
