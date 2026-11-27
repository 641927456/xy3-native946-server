"""Local battle rules for group sigils with documented 946 entry data."""
from native946_afei_expansion import AfeiBattle
from native946_codec import packet, text_notice
from native946_wilderness import ENEMY_BASE


def open_prize(game, iid):
    from secrets import choice
    from native946_equipment import COMPLETION, persist
    from native946_gear_services import matches
    from native946_inventory_features import consume, give, owned
    from native946_progression import DATA
    from native946_constellations import GROUP_PRIZES

    p = game.progression
    with p.transaction() as db:
        item = owned(db,p.cid,iid)
        rule = GROUP_PRIZES.get(str(item['type']), {})
        if 'contents' not in rule:
            raise ValueError('这个礼包没有可以发放的物品。')
        consume(db,p.cid,iid)
        if item['type'] == 47554:
            hero = p._snapshot(db)
            ring = COMPLETION['ring'][str(hero['race'])]
            candidates = [kind for kind in (*rule['contents'],ring)
                          if matches(COMPLETION['shenbing'][str(kind)][2],game.character)]
            kind = choice(candidates)
            part = COMPLETION['shenbing'][str(kind)][0]
            page,slot = p.free_bag_slot(hero)
            reward = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                                (p.cid,kind,page,slot)).lastrowid
            profile = dict(reference_id=kind,grade=1,slot=part,role='全部',need={},level=1,rebirth=0,
                           durability=1000,native_race=hero['race'],kind='神兵')
            persist(db,p.cid,reward,profile,{'最大气血':100,'命中':20,'攻击':60})
        else:
            kind = choice(rule['contents'])
            give(db,p,kind)
        name = DATA['items'][str(kind)]['name']
    p._cached_snapshot = None
    return game.defer_packets(lambda:(*p.item_packets(),text_notice(f'本服礼包已开出{name}。')))


def group_roster(kind, rule, reference, policy):
    variant = rule['form'] == 'variant'
    count = 3 if variant else 6
    names = [rule['star_name'], *(f'星卫{slot}' for slot in range(1, count))]
    if kind == 45097:
        names = [enemy['name'] for enemy in rule['opening_battle']['enemies']]
    elif kind == 45101:
        names = ['中军', *(f'{gate}门阵卫' for gate in '休生伤杜景死惊开')]
    roster = []
    for index, name in enumerate(names):
        base = reference[index % len(reference)]
        shape = policy['shayao_shapes'][index] if kind == 45097 else base['type']
        stats = dict(base['combat_stats'], hpmax=(policy['variant_hp'] if variant else policy['group_hp']),
                     attack=policy['group_attack'], defense=policy['group_defense'])
        if index == 0:
            stats['hpmax'] *= 2
        roster.append(dict(name=name, type=shape, guardian_type=0, combat_stats=stats))
    special = kind == 45097
    event = dict(name=rule['star_name'], level=rule.get('min_character_level', 50),
                 experience=policy['shayao_experience'] if special else rule['rank']*policy['rank_experience'],
                 cash=policy['shayao_cash'] if special else rule['rank']*policy['rank_cash'],
                 drops=policy['shayao_drops'] if special else policy['ordinary_drops'],
                 reference_encounter=0, star_sigil=kind, policy='constellation-local-v1')
    if special:
        event['enemy_layout'] = rule['opening_battle']['enemy_layout']
    return event, roster


class GroupBattle(AfeiBattle):
    def after_round(self, runtime):
        if not self.allies():
            return
        if self.event['star_sigil'] == 45101:
            self.check_gates(runtime)
        if self.event['star_sigil'] != 45097:
            return
        from native946_constellations import GROUP_POLICY, PENDING_GROUPS

        actors = {f['name']: f for f in runtime.fighters.values() if f['camp'] == 3}
        flags = self.state.setdefault('local_reinforcements', set())
        # Resolve the defeated Tianmeng's action before replacing its tomb.
        tianmeng = actors.get('天猛星')
        if tianmeng and tianmeng['hp'] <= 0 and 'counter' not in flags:
            flags.add('counter')
            weather = self.state.get('role_weather', {})
            if weather.get('until', 0) < runtime.round or weather.get('kind') == 0:
                targets = self.allies()
                runtime.out.append(text_notice('本服规则：晴日击倒天猛，天猛反击在场人物和守护。'))
                runtime.group(tianmeng, targets, 43)
                for target in targets:
                    damage = min(GROUP_POLICY['retaliation_cap'], target['hpmax']*GROUP_POLICY['retaliation_percent']//100)
                    runtime.damage(tianmeng, target, max(1, damage), 'fire', allow_protection=False, active=False)
                runtime.end()
        if not self.allies():
            return
        if runtime.round >= 2 or not self.enemies():
            self.reinforce(runtime, flags, '天威星', (10, 2, 1), GROUP_POLICY)
        if actors['地轴星']['hp'] <= 0:
            self.reinforce(runtime, flags, '地英星', (8, 7, 10), GROUP_POLICY)
        if actors['地幽星']['hp'] <= 0:
            observed = PENDING_GROUPS[45097]['observed_replacement_positions']
            slots = [r['slot'] for r in observed if r.get('name') == '天猛星' and 'slot' in r]
            self.reinforce(runtime, flags, '天猛星', (*slots, 7, 9, 10, 2, 3), GROUP_POLICY)
        healer = actors['地威星']
        if runtime.round % GROUP_POLICY['revive_round'] == 0 and healer['hp'] > 0 and 'revived' not in flags:
            fallen = sorted((f for f in actors.values() if f['hp'] <= 0 and not f.get('removed') and
                             f['name'] not in ('地轴星','地幽星','天猛星')),key=lambda f:f['slot'])
            for target in fallen:
                if runtime.revive(healer,target,target['hpmax']*GROUP_POLICY['revive_percent']//100):
                    flags.add('revived')
                    break

    def reinforce(self, runtime, flags, name, preferred, policy):
        if name in flags:
            return
        standing = {f['slot'] for f in self.enemies()}
        slot = next((n for n in (*preferred, *range(1,11)) if n not in standing), None)
        if slot is None:
            return
        for fighter in runtime.fighters.values():
            if fighter['camp'] == 3 and fighter['slot'] == slot and not fighter.get('removed'):
                fighter['removed'] = True
                runtime.out.append(packet(2137,['number'],[fighter['id']]))
        flags.add(name)
        fid = max(f['id'] for f in runtime.fighters.values() if f['id'] >= ENEMY_BASE and f['camp'] == 3)+1
        hp = policy['group_hp']
        enemy = dict(id=fid,name=name,type=policy['replacements'][name],guardian_type=0,kind='enemy',camp=3,
                     slot=slot,hp=hp,hpmax=hp,mp=3000,mpmax=3000,level=self.event['level'],attack=policy['group_attack'],
                     initiative=170,defense=policy['group_defense'],captured=False,removed=False,exit_on_defeat=False)
        runtime.fighters[fid] = enemy
        runtime.prepare(enemy)
        game = self.games[self.leader]
        runtime.out.append(game.fighter(fid,enemy['type'],name,3,2,slot,enemy['level'],mid=2128))
        runtime.bars(enemy,initialize=True)

    def check_gates(self, runtime):
        # Keep the local center formation up until all eight gates are broken.
        living = self.enemies()
        center = next((f for f in runtime.fighters.values() if f['camp'] == 3 and f['slot'] == 1),None)
        if center and center['hp'] <= 0 and living:
            if runtime.revive(center,center,center['hpmax']//4):
                runtime.out.append(text_notice('本服规则：八门未破，中军重新起身。'))
