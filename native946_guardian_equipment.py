"""Native guardian artifact production and local persisted equip stats."""

from native946_codec import packet, text_notice
from native946_equipment import COMPLETION
from native946_gear_services import bench_access
from native946_refinement import Refinement, debit


REFERENCE_WIRE_VERSION=7
SPECIAL=range(42040,42050)
SPECIAL_UNLOCK=46518
ITEMS={**COMPLETION['guardians'],**{str(kind):dict(slot='特别版守护法宝',level=4) for kind in SPECIAL}}
REQUESTS={1929,1930,1931}
COST={1:20000,2:100000,3:400000}
BLUEPRINTS={45082:2,45083:3}
LOCAL_PROFILES={
    42023: dict(initiative=31,EquipElementResist=240,guardian_cold_bonus=220),
    42030: dict(mag=37,cor=37,mpmax=1000,hpmax=1000,EquipRePoisonDamage=600),
    42031: dict(AttValue=231,strength=87,defense=336,role_combo_count=1),
    42032: dict(mag=87,hpmax=500,ReSeal=250,guardian_4046_bonus=110,guardian_4539_bonus=770),
    42033: dict(strength=68,defense=1436,ReDisorder=100),
    42036: dict(hpmax=1000,cor=49,mag=49,ReFaint=580),
    42037: dict(cor=68,ReWenxin=150,PhyAbsorbDegree=300,ImmAbsorbDegree=192,guardian_fabao_hp=120),
    42039: dict(initiative=43,EquipRePoisonDamage=400,EquipReFatal=192,ImmAbsorbDegree=300),
    42041: dict(attack=120,ReDisorder=300),
    42045: dict(strength=50,ReSeal=240),
    42047: dict(cor=35,mag=35,hpmax=700,ReFaint=300,ReMpAbsorb=200),
    42048: dict(cor=50,PhyAbsorbDegree=100,ImmAbsorbDegree=100,ReWenxin=80,guardian_fabao_hp=60),
    42050: dict(cor=55,mag=55,hpmax=1100,ReFaint=700),
    42052: dict(cor=80,ReWenxin=150,PhyAbsorbDegree=325,ImmAbsorbDegree=205,guardian_fabao_hp=140),
}


def equipment_page(sid):
    return 4000+sid%1000


def ornament_shape(item_type):
    from native946_guardian_ornaments import ORDINARY, SHOP

    offer=ORDINARY.get(item_type) or SHOP.get(item_type)
    return offer['SummonShape'] if offer else ()


def ornament_appearance(db,cid,sid,kind):
    """Read the native display type and weapon color for the equipped ornament."""
    equipped=db.execute(
        "SELECT i.type FROM native_guardian_equipment e JOIN inventory i ON i.id=e.item_id "
        "WHERE e.summon_id=? AND e.category='ornament' AND i.character_id=?",
        (sid,cid),
    ).fetchone()
    if equipped and (mapping:=ornament_shape(equipped[0])):
        species,shape,*colors=mapping
        if species==kind:
            return shape, colors[0] if colors else 0
    return kind, 0


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_equipment (
      item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
      summon_id INTEGER NOT NULL REFERENCES summons(id) ON DELETE CASCADE,
      category TEXT NOT NULL,
      UNIQUE(summon_id,category)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_guardian_special_slot (
        summon_id INTEGER PRIMARY KEY REFERENCES summons(id) ON DELETE CASCADE)''')
    db.execute("UPDATE native_guardian_equipment SET category='artifact:1' WHERE category='artifact'")
    db.execute('''UPDATE inventory SET page=4000+(SELECT summon_id%1000 FROM native_guardian_equipment WHERE item_id=inventory.id),slot=12
                  WHERE id IN (SELECT item_id FROM native_guardian_equipment WHERE category='ornament')''')


def options(level=None):
    return [dict(ItemType=int(kind),ItemId=int(kind),CaiLiao=6791+meta['level'],YaoYin=6110,
                 NeedCash=COST[meta['level']],JingHuaNum=1) for kind,meta in ITEMS.items()
            if meta['level'] in COST and (level is None or level==meta['level'])]


def open_panel(game):
    pet=game.summons.active()
    if not pet:raise ValueError('请先选择要使用法宝的参战守护。')
    return packet(1924,[('array','summonequip_box_item'),'number'],[options(),pet['id']])


def top(kind):
    info=ITEMS[str(kind)]
    if info['level']!=3:return None
    return next((int(k) for k,v in ITEMS.items() if v['slot']==info['slot'] and v['level']==4),None)


def unlock_special(game,sid,iid):
    from native946_mall_uses import consume
    if game.battle:raise ValueError('请先完成战斗再开启法宝格。')
    p=game.progression
    with p.transaction() as db:
        game.summons._owned(db,sid)
        item=Refinement(game).item(db,iid)
        if item['type']!=SPECIAL_UNLOCK:raise ValueError('开启特别格需要万象浊灵。')
        if not db.execute('INSERT OR IGNORE INTO native_guardian_special_slot VALUES(?)',(sid,)).rowcount:
            raise ValueError('特别版法宝格已经开启。')
        consume(db,item)
    return game.defer_packets(lambda:(*p.item_packets(),*game.summons.info_packets(sid),text_notice('已开启特别版守护法宝格。')))


def upgrade_panel(game):
    rows=[dict(ItemType=target,material1=int(kind),material2=6794,material3=6110)
          for kind in ITEMS if (target:=top(int(kind))) is not None]
    return packet(1926,['number',('array','summonequip_upgrade_item')],[3,rows])


def blueprint(game,item):
    with game.progression.transaction(write=False) as db:
        row=Refinement(game).item(db,item)
        level=BLUEPRINTS.get(row['type'])
        if not level:raise ValueError('请选择有效的守护法宝图纸。')
        return packet(1925,['number',('array','summonequip_box_item')],[item,options(level)])


def use(game,sid,iid):
    p=game.progression
    if game.battle:raise ValueError('请先完成战斗再切换守护装备。')
    with p.transaction() as db:
        pet=game.summons._owned(db,sid)
        item=Refinement(game).item(db,iid,equipped=True)
        meta=ITEMS.get(str(item['type']))
        if not meta or item['amount']!=1:raise ValueError('请选择有效的守护装备。')
        ornament=bool(meta.get('summon'))
        category='ornament' if ornament else None
        target_page=equipment_page(sid)
        present=db.execute('SELECT summon_id,category FROM native_guardian_equipment WHERE item_id=?',(iid,)).fetchone()
        if present and present[0]!=sid:raise ValueError('请先从原守护身上卸下装备。')
        if present:category=present['category']
        if present and ornament and item['page']!=target_page:
            db.execute('UPDATE inventory SET page=?,slot=12 WHERE id=?',(target_page,iid))
            equipped=True
        elif present:
            db.execute('DELETE FROM native_guardian_equipment WHERE item_id=?',(iid,))
            if item['page']==target_page:
                page,slot=p.free_bag_slot()
                db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(page,slot,iid))
            equipped=False
        else:
            if ornament:
                mapping=ornament_shape(item['type'])
                if not mapping or mapping[0]!=pet['type']:
                    from native946_growth import species
                    if item['type']==43043 and species(pet['type'])['name']==meta['summon']:
                        raise ValueError('灼华桃仙需达到最终阶段才能佩戴稚巧音。')
                    raise ValueError('这件饰品只能由指定种类的守护装备。')
            else:
                if meta['level']==4 and not (pet['sanctification'] and pet['level']>=140):
                    raise ValueError('顶级守护法宝需要守护达到封圣140级。')
                if item['type'] in SPECIAL:
                    if not db.execute('SELECT 1 FROM native_guardian_special_slot WHERE summon_id=?',(sid,)).fetchone():
                        raise ValueError('请先使用万象浊灵开启特别版法宝格。')
                    category='artifact:5'
                else:
                    occupied={row['category']:row['type'] for row in db.execute('SELECT e.category,i.type FROM native_guardian_equipment e '
                              'JOIN inventory i ON i.id=e.item_id WHERE e.summon_id=?',(sid,))}
                    category=next((key for key,kind in occupied.items() if key.startswith('artifact:') and key!='artifact:5'
                                   and ITEMS.get(str(kind),{}).get('slot')==meta['slot']),None)
                    if category is None:
                        cap=4 if pet['sanctification'] else min(3,1+pet['rebirth'])
                        category=next((f'artifact:{i}' for i in range(1,cap+1) if f'artifact:{i}' not in occupied),None)
                    if category is None:raise ValueError('当前守护的普通法宝格已满。')
            previous=db.execute('SELECT e.item_id,i.page,i.slot FROM native_guardian_equipment e JOIN inventory i ON i.id=e.item_id '
                                'WHERE e.summon_id=? AND e.category=?',(sid,category)).fetchone()
            db.execute('DELETE FROM native_guardian_equipment WHERE summon_id=? AND category=?',(sid,category))
            db.execute('INSERT INTO native_guardian_equipment VALUES(?,?,?)',(iid,sid,category))
            if previous:
                db.execute('UPDATE inventory SET page=-1,slot=? WHERE id=?',(iid,iid))
                db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(item['page'],item['slot'],previous['item_id']))
            slot=12 if ornament else int(category.split(':')[1])
            db.execute('UPDATE inventory SET page=?,slot=? WHERE id=?',(target_page,slot,iid))
            equipped=True
    def render():
        ornament=category=='ornament'
        items=p.item_packets()
        guardian=game.summons.info_packets(sid,refresh_appearance=ornament)
        scene=game.sync_summon_scene() if ornament and getattr(game,'_shown_summon_id',None)==sid else ()
        return (*items,*guardian,*scene,text_notice('守护装备已穿戴。' if equipped else '守护装备已卸下。'))

    return game.defer_packets(render)


def equipped_items(db,cid,sid):
    from native946_mall_bags import pages
    available={*pages(db,cid),equipment_page(sid)}
    return [row for row in db.execute(
        'SELECT i.id,i.type,i.page FROM native_guardian_equipment e JOIN inventory i ON i.id=e.item_id '
        'WHERE e.summon_id=? AND i.character_id=?',(sid,cid))
        if row['page'] in available and str(row['type']) in ITEMS]


def effects(db,cid,sid):
    from native946_guardian_ornaments import state as ornament_state
    result={}
    for r in equipped_items(db,cid,sid):
        meta=ITEMS[str(r['type'])]
        grade=meta['level']
        for stat,value in LOCAL_PROFILES.get(r['type'],dict(hpmax=grade*40,attack=grade*16,defense=grade*8)).items():
            result[stat]=result.get(stat,0)+value
        if meta.get('summon'):
            for stat,value in ornament_state(db,r['id'])[0].items():result[stat]=result.get(stat,0)+value
    return result


def receive(game,mid,reader):
    from native946_cosmetics import Cosmetics
    from native946_mall_uses import consume
    ref=Refinement(game)
    p=ref.p
    if game.battle:raise ValueError('请先结束战斗再打造守护装备。')
    if mid==1931:
        stage,previous,material,medicine=(reader.integer() for _ in range(4))
        reader.finish()
        bench_access(game,'guardian')
        with p.transaction() as db:
            item=ref.item(db,previous)
            if stage!=3 or str(item['type']) not in ITEMS or not (result:=top(item['type'])):
                raise ValueError('需要可升为顶级的三级守护法宝。')
            if db.execute('SELECT 1 FROM native_guardian_equipment WHERE item_id=?',(previous,)).fetchone():
                raise ValueError('请先从守护身上卸下法宝。')
            inputs=[ref.item(db,material),ref.item(db,medicine)]
            if len({previous,material,medicine})!=3 or [r['type'] for r in inputs]!=[6794,6110]:
                raise ValueError('升级需要混元之璧与猛兽之力。')
            debit(db,p.cid,600000)
            for r in inputs:consume(db,r,1)
            db.execute('UPDATE inventory SET type=? WHERE character_id=? AND id=?',(result,p.cid,previous))
    else:
        count=2 if mid==1929 else 3
        ids=[]
        for _ in range(count):
            key,value=reader.integer(),reader.integer()
            if value!=1:raise ValueError('请每次放入一件所需材料。')
            ids.append(key)
        sid=reader.integer() if mid==1929 else 0
        result=reader.integer()
        reader.finish()
        if len(set(ids))!=count:raise ValueError('法宝材料不能重复使用。')
        if mid==1929:bench_access(game,'guardian')
        meta=ITEMS.get(str(result),{})
        grade=meta.get('level')
        if grade not in COST:raise ValueError('没有该法宝的生产配方。')
        with p.transaction() as db:
            if sid:game.summons._owned(db,sid)
            inputs=[ref.item(db,key) for key in ids]
            kinds={r['type'] for r in inputs}
            if kinds&{6110,6111,6112,6113}!={6110} or 6791+grade not in kinds:
                raise ValueError('请使用相应等级的玉石与猛兽之力。')
            if mid==1930 and not any(BLUEPRINTS.get(r['type'])==grade for r in inputs):
                raise ValueError('图纸等级与目标法宝不匹配。')
            if mid==1929 and not sid:raise ValueError('请选择自己的守护。')
            debit(db,p.cid,COST[grade])
            for r in inputs:consume(db,r,1)
            Cosmetics(game)._add(db,result,backpack=True)
    return game.defer_packets(lambda:(*p.item_packets(),*p.attribute_packets(),text_notice('守护法宝已完成。数值采用本机规则。')))
