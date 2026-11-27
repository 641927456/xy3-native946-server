"""Classic fashion strengthening, decoration and powder conversion."""

from secrets import choice, randbelow

from native946_codec import packet, text_notice
from native946_refinement import DATA, Refinement, debit, describe, save, state


REFERENCE_WIRE_VERSION=1
ITEMS=DATA['fashion_items']
UPGRADES={kind:recipe for kind,recipe in DATA['info_fashion_upgrade_info'].items()
          if ITEMS[kind]['name'].startswith('熙阳融雪·')}
PART_SLOT={'头冠':20,'服装':21,'肩饰':22}
PARTS=tuple(PART_SLOT)
REQUESTS={2517,2518,2519,2520,2523,2524,2697,2698,2699}
WRITES={2517,2518,2523,2697}
MATERIALS=(46207,46208,46209)
FEATHERS=(46210,46211,46212)
POWDER=46517
LEVEL_MAX=10
LEVEL_CHANCE=(100,100,100,100,100,80,70,60,50,40)


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_fashion_strength (
        item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id),
        level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 10)
    )''')


def level(db,cid,iid):
    row=db.execute('SELECT level FROM native_fashion_strength WHERE item_id=? AND character_id=?',(iid,cid)).fetchone()
    return row[0] if row else 0


def role_name(character):
    from native946_characters import ROLES
    return ROLES[str(character['role_id'])]['name']


def validate_wear(character,item,slot):
    meta=ITEMS[str(item['type'])]
    if item['amount']!=1 or PART_SLOT[meta['slot']]!=slot or meta['role']!=role_name(character):
        raise ValueError('时装的角色或部位要求不匹配。')


def cosmetic_type(kind,sex):
    meta=ITEMS[str(kind)]
    offset=8 if str(kind) in UPGRADES else 0
    return 56001+offset+(4 if sex==2 else 0)+PARTS.index(meta['slot'])


class FashionRefinement:
    def __init__(self,game):
        self.game=game
        self.p=game.progression
        self.refine=Refinement(game)

    def open(self,tab):
        return packet(2510,['number','number'],[tab,1])

    def items(self):
        from native946_mall_bags import pages
        from native946_inventory import EQUIP_PAGES
        from native946_cosmetics import PAGES
        with self.p.transaction(write=False) as db:
            allowed=(*pages(db,self.p.cid),*PAGES,*EQUIP_PAGES)
            rows=[r['id'] for r in db.execute('SELECT id,type,page FROM inventory WHERE character_id=?',(self.p.cid,))
                  if str(r['type']) in ITEMS and r['page'] in allowed]
        return packet(2508,[('array','number')],[rows])

    def owned(self,db,iid):
        item=self.refine.item(db,iid,appearance=True,equipped=True)
        if str(item['type']) not in ITEMS or item['amount']!=1:
            raise ValueError('请选择一件有效的沁水晴岚或熙阳融雪时装。')
        return item

    def targets(self):
        role=role_name(self.game.character)
        available=[dict(target_equip_type=int(kind),need_item_type1=next(
                            source for source in row['materials'].values() if ITEMS[str(source)]['role']==role),
                        need_item_amount1=1,need_item_type2=POWDER,need_item_amount2=1)
                   for kind,row in UPGRADES.items() if ITEMS[kind]['role']==role]
        return packet(2695,[('array','fashion_up2secondsuit')],[available])

    def powder(self,source,target,commit):
        from native946_mall_bags import pages
        cid=self.p.cid
        with self.p.transaction(write=commit) as db:
            item=self.owned(db,source)
            target_info=ITEMS.get(str(target))
            recipe=UPGRADES.get(str(target))
            if not recipe or target_info['role']!=role_name(self.game.character):
                raise ValueError('目标时装不适合当前角色。')
            if item['type'] not in recipe['materials'].values() or level(db,cid,source)<5:
                raise ValueError('需要强化至少+5、部位相同的原生时装。')
            if item['page'] not in pages(db,cid):
                raise ValueError('请先将待升级的时装放入随身背包。')
            info=state(db,cid,source)
            if info['pending']:raise ValueError('请先完成待确认的雕饰。')
            detail=f"强化+{level(db,cid,source)}#r"+describe(info)
            before=ITEMS[str(item['type'])]['name']+'#r'+detail
            after=target_info['name']+'#r'+detail
            if not commit:return packet(2696,['string','string'],[before,after])
            self.refine.take(db,POWDER,1)
            db.execute('UPDATE inventory SET type=? WHERE character_id=? AND id=?',(target,cid,source))
        return self.game.defer_packets(lambda:(*self.p.item_packets(),self.items(),
            packet(2696,['string','string'],[before,after]),text_notice('时装升级成功，强化与雕饰保留。')))

    def strengthen(self,iid,flags=None):
        cid=self.p.cid
        with self.p.transaction(write=flags is not None) as db:
            item=self.owned(db,iid)
            grade=level(db,cid,iid)
            if grade>=LEVEL_MAX:raise ValueError('时装已经强化到+10。')
            costs=[grade+1,1,1]
            cost=1000*(grade+1)
            chance=LEVEL_CHANCE[grade]
            if flags is None:
                self.game.fashion_strength_quote=(iid,grade)
                return packet(2511,['number','number',('fixed_array','number',3),('fixed_array','number',3),
                                    'number','number','string','string','string'],
                              [iid,grade,MATERIALS,costs,cost,chance,'本机时装强化规则',
                               self.strength_desc(grade),self.strength_desc(grade+1)])
            protect,guarantee=flags
            if any(flag not in (0,1) for flag in flags) or (guarantee and not protect):
                raise ValueError('强化保护选项无效。')
            if getattr(self.game,'fashion_strength_quote',None)!=(iid,grade):
                raise ValueError('请重新选择装备，查看当前强化材料。')
            self.refine.take(db,MATERIALS[0],costs[0])
            if protect:self.refine.take(db,MATERIALS[1],costs[1])
            if guarantee:self.refine.take(db,MATERIALS[2],costs[2])
            debit(db,cid,cost)
            success=guarantee or randbelow(100)<chance
            next_grade=grade+1 if success else grade if protect or grade<5 else grade-1
            db.execute('INSERT INTO native_fashion_strength VALUES(?,?,?) ON CONFLICT(item_id) '
                       'DO UPDATE SET level=excluded.level',(iid,cid,next_grade))
        self.game.fashion_strength_quote=None
        return self.game.defer_packets(lambda:(*self.p.item_packets(),*self.p.attribute_packets(),self.items(),
            self.p.item_description(iid),text_notice(('强化成功' if success else '强化未成功')+f'，当前+{next_grade}。')))

    @staticmethod
    def strength_desc(grade):
        return f'强化+{grade}#r本机加成：气血+{grade*10}，防御+{grade*2}'

    def decorations(self,iid,material):
        if material not in FEATHERS:raise ValueError('请选择原生雕饰材料。')
        cid=self.p.cid
        with self.p.transaction() as db:
            self.owned(db,iid)
            value=state(db,cid,iid)
            if value['pending']:raise ValueError('请先选择上一回的雕饰结果。')
            self.refine.take(db,material,1)
            tier=FEATHERS.index(material)+1
            choices=['DudsHpMax','DudsMpMax','DudsDefAll','DudsAttAll','DudsDodAll']
            traits=[]
            for _ in range(3+randbelow(tier)):
                key=choice(choices);choices.remove(key)
                traits.append([key,1+randbelow(tier*2),tier])
            value['pending']=dict(fashion=traits)
            save(db,cid,iid,value)
            result=packet(2506,['number','number','number','string','string'],
                          [iid,material,1,describe(value),describe(dict(traits=traits,expires=0))])
        return self.game.defer_packets(lambda:(*self.p.item_packets(),result))

    def choose_decoration(self,iid,op):
        cid=self.p.cid
        if op not in (0,1):raise ValueError('雕饰选择无效。')
        with self.p.transaction() as db:
            self.owned(db,iid)
            value=state(db,cid,iid)
            if 'fashion' not in value['pending']:raise ValueError('没有待确认的雕饰结果。')
            if op:value['traits']=value['pending']['fashion']
            value['pending']={}
            save(db,cid,iid,value)
        return self.game.defer_packets(lambda:(*self.p.attribute_packets(),self.p.item_description(iid),
                                                text_notice('时装雕饰已保存。')))

    def exchange(self,part):
        from native946_equipment import persist
        from native946_mall_bags import pages
        from native946_cosmetics import PAGES
        from native946_mall_uses import consume
        from native946_cosmetics import Cosmetics
        role=role_name(self.game.character)
        sex=self.game.character['sex']
        cid=self.p.cid
        kind=next((int(k) for k,v in ITEMS.items() if k not in UPGRADES and v['role']==role and v['slot']==part),None)
        if not kind:raise ValueError('此角色没有对应的原生沁水晴岚时装。')
        source=cosmetic_type(kind,sex)
        with self.p.transaction() as db:
            allowed=(*PAGES,*pages(db,cid))
            choices=db.execute('SELECT * FROM inventory WHERE character_id=? AND type IN (?,?) ORDER BY id',
                               (cid,source,source+10000)).fetchall()
            original=next((r for r in choices if r['page'] in allowed),None)
            if not original:raise ValueError('请先把对应的永久沁水晴岚外观放入背包或异宝匣。')
            original=self.refine.item(db,original['id'],appearance=True)
            if original['expires'] or db.execute(
                'SELECT 1 FROM native_refinement WHERE item_id=? UNION ALL '
                'SELECT 1 FROM cosmetic_item_carvings WHERE item_id=?',(original['id'],original['id'])).fetchone():
                raise ValueError('限时、有刻字或已炼化的外观不能兑换。')
            consume(db,original)
            item=Cosmetics(self.game)._add(db,kind,backpack=True)
            profile=dict(reference_id=kind,grade=1,slot=PART_SLOT[part],role=role,need={},level=1,rebirth=0,durability=100)
            persist(db,cid,item,profile,{})
        return self.game.defer_packets(lambda:(*self.p.item_packets(),text_notice('已用同部位沁水晴岚外观兑换原生时装。')))

    def receive(self,mid,reader):
        if mid in (2519,2520,2698):
            reader.finish()
            if mid==2519:return packet(2507,[('fixed_array','number',3),('fixed_array','number',3)],[FEATHERS,[1,1,1]])
            return self.items() if mid==2520 else self.targets()
        iid=reader.integer()
        if mid in (2517,2697,2699):
            other=reader.integer();reader.finish()
            if mid==2517:return self.decorations(iid,other)
            return self.powder(other,iid,True) if mid==2697 else self.powder(iid,other,False)
        if mid==2518:
            op=reader.take(1)[0];reader.finish()
            return self.choose_decoration(iid,op)
        flags=list(reader.take(2)) if mid==2523 else None
        reader.finish()
        return self.strengthen(iid,flags)
