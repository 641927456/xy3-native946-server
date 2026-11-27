"""Playable cultivation routes assembled from 946 catalog, NPC and item records."""

from native946_cultivation import ADVANCEMENTS, LAO_JUN, MENG_PO, YU_RONG_WANG


TRIAL_RULES = {
    '912101': ('first',),
    '912201': ('second',),
    '111321': ('ascend', 'ascend_after_third'),
    '222400': ('sanctify',),
}
MEDICINES = {
    key: dict(name=name, icon=icon, desc='孟婆汤剧情物品。', max_stack=1)
    for key, name, icon in (('8379', '天不老', 5533), ('8380', '九圣散', 2009),
                            ('8381', '阴阳草', 6538), ('8382', '千年冰魄', 5709))
}
GUAN_YIN = (2009, 8110)
YUAN = (2002, 1201)
DEER = (2013, 3003)
SCORPION = (2256, 6018)
GUIDES = {DEER: '111321', LAO_JUN: '111321', YU_RONG_WANG: '222400'}


def promotion(key, before):
    return next((ADVANCEMENTS[name] for name in TRIAL_RULES[key]
                 if ADVANCEMENTS[name].before == before), None)


def chapters(stories):
    from native946_map import load_map
    from native946_world import world

    catalog = world()

    def contact(location):
        scene, npc = location
        row = catalog.npc(scene, npc)
        x, y = row['x'] // 20, row['y'] // 20
        arrival = load_map(scene).nearest_walkable(x, y)
        return dict(scene=scene, npc=npc, x=x, y=y, name=row['name'], shape=row['shape'], arrival=arrival)

    def step(npc, title, text, *, enemies=(), give=None, take=None):
        target = contact(npc)
        foes = [dict(name=name, shape=shape) for name, shape in enemies]
        battle = bool(foes)
        node = dict(title=title, text=text, target=target, action='battle' if battle else 'talk',
                    movie=0, give=give or {}, take=take or {}, source_type=2 if battle else 1,
                    reward=dict(experience=8000 if battle else 1000, gold=20 if battle else 5))
        if foes:
            node['enemy'] = foes[0]
            node['roster'] = foes
        return node

    def fight(npc, title, text, *foes, give=None):
        target = contact(npc)
        return step(npc, title, text, enemies=foes or ((target['name'], target['shape']),), give=give)

    def quest(key, offset, nodes):
        native = stories[int(key)]
        giver = native['locations'][0]
        for number, node in enumerate(nodes, 1):
            node['order'] = number
        return dict(id=key, qid=9462000 + offset, name=native['name'], metadata=native,
                    giver=contact((giver['scene'], giver['npc'])), previous=None, next=None,
                    nodes=nodes, completion_items=native['reward_list'])

    medicine = (2007, 1335)
    king = (2009, 2105)
    yu_ji = (2009, 2106)
    general = (2016, 2309)
    first = [
        step(MENG_PO, '问药', '向孟婆询问熬制汤药所需的三味药材。'),
        step(king, '霸王旧事', '向霸王探问药材的下落。'),
        fight(king, '霸王之试', '通过霸王的考验。'),
        step(yu_ji, '问诊', '向虞姬问明来由，再请孙思邈诊治。'),
        step(medicine, '千金方', '孙思邈请你去东海向分水将军打听千年冰魄。'),
        step(general, '东海问讯', '请分水将军指点北冥的去路。'),
        fight(general, '东海考验', '分水将军要先试一试你的身手。'),
        fight((2019, 2857), '取千年冰魄', '击败北冥镇守神兽，取回千年冰魄。',
              ('镇守神兽', 2050), ('星宿', 2036), give={'8382': 1}),
        step(medicine, '以冰魄入药', '将千年冰魄交给孙思邈，再去昆仑山寻阴阳草。', take={'8382': 1}),
        fight((2013, 3006), '昆仑采药', '通过仙鹤的考验，采得阴阳草。', give={'8381': 1}),
        step(yu_ji, '药成', '探望霸王和虞姬，取得天不老与九圣散。', give={'8379': 1, '8380': 1}),
        step(MENG_PO, '交天不老', '把天不老交给孟婆。', take={'8379': 1}),
        step(MENG_PO, '交九圣散', '把九圣散交给孟婆。', take={'8380': 1}),
        step(MENG_PO, '交阴阳草', '把阴阳草交给孟婆。', take={'8381': 1}),
        step(MENG_PO, '续来世', '备齐汤药后，在孟婆处确认一转。'),
    ]

    second = [step(GUAN_YIN, '观音指点', '请观音菩萨指点如何斩除六种心魔。')]
    for fault, shape in zip('骄躁痴狂色贪', (2012, 2067, 2013, 2096, 2378, 2076)):
        second.append(fight(GUAN_YIN, f'破{fault}障', f'在观音菩萨护持下战胜心魔·{fault}。',
                            (f'心魔·{fault}', shape)))
    second.append(step(MENG_PO, '涅磐重生', '斩尽六障后回地府向孟婆确认二转。'))

    heavenly = (
        ('金吒', 2106), ('木吒', 2063), ('哪吒', 2065), ('雷公', 2032), ('风伯', 2087),
        ('雨师', 4013), ('火德星君', 2069), ('托塔天王', 2064), ('二郎神', 2095), ('巨灵神', 2066),
    )
    fly = [step(YUAN, '袁天罡指路', '向袁天罡问明天劫，随后到昆仑山找九色鹿。'),
           step(DEER, '九色鹿引路', '请九色鹿开启渡劫试炼。')]
    for index, foe in enumerate(heavenly[:8], 1):
        roster = (foe, ('天兵', 2067)) if index == 8 else (foe,)
        fly.append(fight(DEER, f'天劫第{index}场', f'接受{foe[0]}的挑战。', *roster))
    fly += [fight(DEER, '众将合战', '击败守在最后一道天劫的十名天将。', *heavenly),
            step(LAO_JUN, '羽化飞升', '通过九场挑战后到兜率宫向太上老君确认飞升。')]

    holy = [
        step(SCORPION, '毒王迷踪', '和隐火蝎交谈，追查三毒王与飞羽的下落。'),
        fight((2256, 6019), '流火蛛拦路', '击败拦路的流火蛛。'),
        step((2256, 6021), '询问罗岚岚', '向罗岚岚打听修罗五行阵。'),
    ]
    for npc, element in zip(range(6022, 6027), '水火金木土'):
        holy.append(fight((2256, npc), f'破修罗{element}阵', f'击败镇守修罗{element}阵的阵灵。'))
    holy += [step(YU_RONG_WANG, '禺狨王指点', '禺狨王请你接受无名老者的封圣考验。'),
             fight((2256, 6027), '无名老者的考验', '战胜无名老者，取得参加封圣大典的资格。'),
             step(YU_RONG_WANG, '封圣大典', '返回禺狨王处确认封圣。')]

    return {row['id']: row for row in (quest('912101', 1, first), quest('912201', 2, second),
                                       quest('111321', 3, fly), quest('222400', 4, holy))}
