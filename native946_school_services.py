"""Local rules for the five master services exposed in the classic menu."""
from native946_codec import packet, text_notice


# Only existing 946 attributes and items participate in the local rules.
RULES = {
    'source': '本服规则', 'nature_level': 20, 'nature_change_cost': 100,
    'natures': {0:'均衡', 1:'根骨', 2:'力量', 3:'灵性'},
    'practice_cost': 30, 'practice_gain': 100, 'zhenqi_cost': 30, 'zhenqi_gain': 20,
    'exchange': {6029:80, 4000:5, 4001:8}, 'donation': {8611:100},
    'craft_min': 6, 'craft_max': 10, 'craft_rate': 10,
    'flight_cost': 150, 'flight_max': 2,
}
SERVICES = ('nature', 'meditation', 'practice_exchange', 'donate', 'donate_craft')
CLOSE = packet(1183, ['string'], ['dlg_npc_chat'])


def donation_value(db, cid, item, craft):
    if not craft:
        return RULES['donation'].get(item['type'], 0)
    row = db.execute('SELECT grade,requirements FROM native_equipment WHERE character_id=? AND item_id=?',
                     (cid, item['id'])).fetchone()
    if not row or not RULES['craft_min'] <= row['grade'] <= RULES['craft_max']:
        return 0
    import json
    if json.loads(row['requirements']).get('kind'):
        return 0
    return row['grade']*RULES['craft_rate']


def menu(game, npc, text, choices):
    from native946_npc_services import NpcServices

    return NpcServices(game)._menu(npc, '本服规则。'+text,
                                   [(label,dict(kind='school', **action)) for label,action in choices], speaker=npc)


def execute(game, npc, master, action):
    from native946_inventory_features import consume, give, owned
    from native946_npc_quests import Quests
    from native946_role_growth import school, talents
    from native946_role_systems import save, state
    from native946_school import SKILLS, add_contribution, base_school, contribution, practice_limit
    from native946_progression import DATA
    from native946_mall_bags import pages
    from native946_inventory_locks import records

    Quests(game).validate_giver(npc, 'school')
    p = game.progression
    cid = p.cid
    op = action['op']
    confirmed = op == 'school_service_confirm'
    with p.transaction(write=confirmed) as db:
        if base_school(school(db,cid)) != master:
            raise ValueError('请向本门师父办理师门服务。')
        snapshot = p._snapshot(db)
        balance = contribution(db,cid)
        nature_selected = state(db,cid,'school_nature',{'selected':bool(snapshot['disposition'])})['selected']
        prefix = f'当前师门贡献 {balance}。#r'
        if not confirmed:
            def confirmation(label, service, value):
                return label, dict(op='school_service_confirm', service=service, value=value)
            if op == 'nature':
                if snapshot['level'] < RULES['nature_level']:
                    raise ValueError(f"达到{RULES['nature_level']}级可选择秉性。")
                cost = RULES['nature_change_cost'] if nature_selected else 0
                choices = [confirmation(f'选择{label}（{cost}贡献）',op,value)
                           for value,label in RULES['natures'].items() if value != snapshot['disposition']]
                return menu(game,npc,prefix+'秉性会调整基础属性。当前为'+RULES['natures'].get(snapshot['disposition'],'原有秉性')+'。',choices)
            if op == 'meditation':
                cost = RULES['practice_cost']
                gain = RULES['practice_gain']
                from native946_role_battle import profile
                choices = [confirmation(f"修炼{profile(skill)['name']}（{cost}贡献，+{gain}熟练）",op,skill)
                           for skill in SKILLS[master] if skill in snapshot['skills'] and snapshot['skills'][skill] < practice_limit(snapshot)]
                choices.append(confirmation(f"调息（{RULES['zhenqi_cost']}贡献，+{RULES['zhenqi_gain']}真气）",op,0))
                if snapshot['flight_level'] < RULES['flight_max']:
                    choices.append(confirmation(f"提升飞行特技（{RULES['flight_cost']}贡献）",'flight',0))
                return menu(game,npc,prefix+'选择一项进行打坐修炼。',choices)
            if op == 'practice_exchange':
                choices = [confirmation(f"兑换{DATA['items'][str(kind)]['name']}（{cost}贡献）",op,kind)
                           for kind,cost in RULES['exchange'].items()]
                return menu(game,npc,prefix+'请选择要兑换的物品。',choices)
            available = pages(db,cid)
            locked = records(db,cid)
            choices = []
            for row in db.execute('SELECT * FROM inventory WHERE character_id=? ORDER BY page,slot',(cid,)):
                if row['page'] not in available or row['id'] in locked:
                    continue
                amount = donation_value(db,cid,row,op == 'donate_craft')
                if amount:
                    name = DATA['items'][str(row['type'])]['name']
                    choices.append(confirmation(f"捐献{name}（+{amount}贡献）",op,row['id']))
            return menu(game,npc,prefix+('选择背包中的可捐献物品。' if choices else '当前背包没有可捐献的物品。'),choices[:48])

        service, value = action['service'], action['value']
        cost = 0
        if service == 'nature':
            if snapshot['level'] < RULES['nature_level'] or value not in RULES['natures'] or value == snapshot['disposition']:
                raise ValueError('当前无法选择该秉性。')
            cost = RULES['nature_change_cost'] if nature_selected else 0
            db.execute('UPDATE progression SET disposition=? WHERE character_id=?',(value,cid))
            save(db,cid,'school_nature',{'selected':True})
            current = p._snapshot(db)
            db.execute('UPDATE progression SET hp=min(hp,?),mp=min(mp,?) WHERE character_id=?',
                       (current['hpmax'],current['mpmax'],cid))
            result = f"已选择{RULES['natures'][value]}秉性。"
        elif service == 'meditation' and value:
            rank = snapshot['skills'].get(value)
            if value not in SKILLS[master] or rank is None or rank >= practice_limit(snapshot):
                raise ValueError('当前法术无法通过打坐继续提升。')
            gain = min(RULES['practice_gain'],practice_limit(snapshot)-rank)
            cost = RULES['practice_cost']
            db.execute('UPDATE learned_skills SET rank=rank+? WHERE character_id=? AND skill_id=?',(gain,cid,value))
            result = f'熟练度增加{gain}。'
        elif service == 'meditation':
            current = talents(db,cid)
            cost, gain = RULES['zhenqi_cost'], RULES['zhenqi_gain']
            current['zhenqi'] += gain
            save(db,cid,'talent',current)
            result = f'真气增加{gain}。'
        elif service == 'flight':
            level = snapshot['flight_level']
            if level >= RULES['flight_max']:
                raise ValueError('飞行特技已达到本服上限。')
            cost = RULES['flight_cost']
            save(db,cid,'school_flight',dict(level=level+1))
            result = f'飞行特技提升至{level+1}级。1级可飞已学过法术的辅修门派，2级可飞指定城镇。'
        elif service == 'practice_exchange' and value in RULES['exchange']:
            cost = RULES['exchange'][value]
            give(db,p,value)
            result = f"已兑换{DATA['items'][str(value)]['name']}。"
        elif service in ('donate','donate_craft'):
            item = owned(db,cid,value)
            gain = donation_value(db,cid,item,service == 'donate_craft')
            if not gain:
                raise ValueError('该物品不符合捐献条件。')
            consume(db,cid,value)
            add_contribution(db,cid,gain)
            result = f'师门贡献增加{gain}。'
        else:
            raise ValueError('该师门选项已失效。')
        if cost > balance:
            raise ValueError('师门贡献不足。')
        if cost:
            add_contribution(db,cid,-cost)
    p._cached_snapshot = None
    return game.defer_packets(lambda:(CLOSE,*game.sync_progression(),text_notice('本服规则。'+result)))
