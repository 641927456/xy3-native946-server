"""Durable native item instances generated from the supplied reference tables."""
from pathlib import Path
from decimal import Decimal
INVENTORY_PAGE_VERSION=1
import json

REFERENCE_WIRE_VERSION=8

DATA=json.loads(Path(__file__).with_name('native946_reference_rules.json').read_text('utf8'))
EXTENDED=json.loads(Path(__file__).with_name('native946_extended_rules.json').read_text('utf8'))
DATA['crafting']['profiles'].update(EXTENDED['high_profiles'])
DATA['reward_map'].update(EXTENDED['reward_map'])
DATA['reward_metadata'].update(EXTENDED['reward_metadata'])
DATA['rare_equipment']=EXTENDED['rare_equipment']
DATA['version']=EXTENDED['version']
GROWTH_RULES=json.loads(Path(__file__).with_name('native946_growth_rules.json').read_text('utf8'))
DATA['crafting']['profiles'].update(GROWTH_RULES['high_profiles'])
COMPLETION=json.loads(Path(__file__).with_name('native946_gear_completion_data.json').read_text('utf8'))
VERIFIED_GEAR = json.loads(Path(__file__).with_name('native946_verified_gear_data.json').read_text('utf8'))
CBG_GEAR = json.loads(Path(__file__).with_name('native946_fairy_cbg.json').read_text('utf8'))
COMPLETION['items'].update(VERIFIED_GEAR['items'])
COMPLETION['items'].update(CBG_GEAR['items'])
COMPLETION['ring_variants'] = VERIFIED_GEAR['ring_variants']
DIVINE_RING_TYPES = frozenset((*COMPLETION['ring'].values(), *map(int, COMPLETION['ring_variants'])))
FAIRY_BASE = VERIFIED_GEAR['fairy']
FAIRY_BASE_BY_HELP = {row['help_id']: row for row in FAIRY_BASE.values()}
DATA['crafting']['profiles'].update(COMPLETION['ordinary_profiles'])
DATA['version']+=':'+GROWTH_RULES['version']
for _ref in EXTENDED['reward_map']:DATA['unresolved_reward_types'].pop(_ref,None)
CORE={'根骨':'cor','灵性':'mag','力量':'strength','敏捷':'speed'}
DIRECT={'最大气血':'hpmax','最大法力':'mpmax','攻击':'attack','速度':'initiative','防御':'defense'}
FLAT_SECONDARY={'命中':'AttValue','躲闪':'DodValue'}
ALIASES={'增加攻击':'攻击','增加气血':'最大气血','增加法力':'最大法力','增加速度':'速度',
         '增加根骨':'根骨','增加灵性':'灵性','增加力量':'力量','增加敏捷':'敏捷',
         '抗中毒几率':'抗中毒','连击几率':'连击率','反击几率':'反击率','狂暴几率':'狂暴率','致命几率':'致命率',
         '加强中毒几率':'加强中毒','加强蛊术伤害':'加强蛊术',
         '每回合回血':'回合回血','每回合回法':'回合回法','每回合自动回血':'回合回血','每回合自动回法':'回合回法',
         '物理反震几率':'反震率','物理反震程度':'反震程度','忽视抗混乱':'忽视抗混',
         '忽视抗封印':'忽视抗封','忽视抗昏睡':'忽视抗睡',
         **{f'加强{label}法伤害':f'加强{label}法' for label in '风雷水火'}}
FLAT_SECONDARY.update({'抗风法':'ReWind','抗火法':'ReFire','抗水法':'ReWater','抗雷法':'ReThunder'})
FLAT_SECONDARY.update({'回合回血':'EquipHpRegen','回合法力':'EquipMpRegen','回合回法':'EquipMpRegen',
                       '抗毒伤害':'EquipRePoisonDamage','抗浩然':'ReHaoran',
                       '抗小楼':'ReXiaolou','混乱反震伤害':'DisorderReactDegree',
                       '仙法抗性':'EquipElementResist','加强仙法伤害':'EquipElementDamage'})
PERCENT={'抗昏睡':'ReFaint','抗混乱':'ReDisorder','抗中毒':'RePoison','抗封印':'ReSeal',
         '抗火':'ReFire','抗风':'ReWind','抗水':'ReWater','抗雷':'ReThunder',
         '致命率':'FatalRate','狂暴率':'CruelRate','连击率':'DoubleRate','反击率':'FanjiRate','抗致命':'EquipReFatal',
         '抗狂暴几率':'EquipReCruel','加强人法':'AddControllerDegree',
         '加强昏睡':'AddFaint','加强混乱':'AddDisorder','加强封印':'AddSeal','加强蛊术':'AddGushu',
         '加强中毒':'EquipPoisonHit','加强毒伤害':'EquipPoisonDamage','加强消融':'AddXiaorongRate',
         '加强再生':'XQAddCureHpP','加强震慑':'RoleLineupShock','抗震慑':'ReMpAbsorb',
         '加强加攻':'RoleLineupAttackBuff','加强加防':'RoleLineupGuardBuff','加强加速':'RoleLineupSpeedBuff',
         '反震率':'EquipReflectChance','反震程度':'EquipReflectPower','仙法减伤':'ImmAbsorbDegree','物理减伤':'PhyAbsorbDegree',
         '火法反击几率':'DudsFireFanji','雷法反击几率':'DudsThunderFanji','风法反击几率':'DudsWindFanji',
         '混乱反震几率':'DisorderReactRate',
         **{f'加强{label}法':'Add'+key for label,key in zip('风雷水火',('Wind','Thunder','Water','Fire'))},
         **{f'忽视抗{label}':'Ig'+key for label,key in zip('睡混封风雷水火',('Faint','Disorder','Seal','Wind','Thunder','Water','Fire'))}}

# 946 F1 equipment help, configs.wdf 83a21957, indexed by the item's type.
CROWN_HELP = {7505: (131003, '女'), 7506: (131004, '男')}
FAIRY_FLAT_RESISTS = {
    '抗隔山': 'ReGeshan', '抗分光': 'ReFenguang', '抗解体': 'ReJieti',
}


def crown_stats(kind, grade):
    help_id, role = CROWN_HELP[kind]
    requirements = {'kind': '神兵', 'native_help': help_id, 'level': 30, 'rebirth': 0, '力量': 22 * grade}
    properties = {'防御': 150 * grade, '抗混乱': 2 * grade, '抗封印': 2 * grade,
                  '反震率': 8 * grade, '反震程度': 8 * grade}
    return role, requirements, properties


def schema(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS native_equipment (
        item_id INTEGER PRIMARY KEY REFERENCES inventory(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id),reference_id INTEGER NOT NULL,
        grade INTEGER NOT NULL,slot INTEGER NOT NULL,role TEXT NOT NULL,
        requirements TEXT NOT NULL,properties TEXT NOT NULL,durability INTEGER NOT NULL,
        max_durability INTEGER NOT NULL,rule_version TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS native_equipment_owner ON native_equipment(character_id);
      CREATE TABLE IF NOT EXISTS native_craft_orders (
        character_id INTEGER NOT NULL REFERENCES characters(id),source_item INTEGER NOT NULL,
        signature TEXT NOT NULL,output_item INTEGER NOT NULL,operation TEXT NOT NULL,
        cash INTEGER NOT NULL,seed TEXT NOT NULL,created INTEGER NOT NULL,
        result TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(character_id,source_item));
    ''')
    if 'result' not in {row[1] for row in db.execute('PRAGMA table_info(native_craft_orders)')}:
        db.execute("ALTER TABLE native_craft_orders ADD COLUMN result TEXT NOT NULL DEFAULT '{}'")


def fairy_base(kind, grade, slot):
    base = FAIRY_BASE.get(str(kind)) or CBG_GEAR['fairy'].get(str(kind))
    if base and (base['grade'], base['slot']) == (grade, slot):
        return base
    return None


def apply_fairy_base(kind, base, requirements, properties, has_presets=False):
    requirements.pop('native_help', None)
    requirements.pop('cbg_fairy_type', None)
    # A listing proves one base combination. Preserve a different saved roll.
    if properties != base['properties'] and (has_presets or ('source' in base and properties)):
        return properties
    if 'help_id' in base:
        requirements['native_help'] = base['help_id']
    else:
        requirements['cbg_fairy_type'] = kind
    return base['properties'].copy()


def fairy_profile(requirements):
    if requirements.get('kind') == '仙器':
        return (FAIRY_BASE_BY_HELP.get(requirements.get('native_help'))
                or CBG_GEAR['fairy'].get(str(requirements.get('cbg_fairy_type'))))
    return None


def records(db,cid):
    result={}
    preset_items = None
    for row in db.execute('SELECT e.*,i.type AS inventory_type FROM native_equipment e JOIN inventory i ON i.id=e.item_id '
                          'AND i.character_id=e.character_id WHERE e.character_id=?',(cid,)):
        value=dict(row)
        kind = value.pop('inventory_type')
        for key in ('requirements','properties'):value[key]=json.loads(value[key])
        value['requirements'].pop('cbg_fairy_type', None)
        # Old saves keep their refinement, durability and wear conditions.
        if value['requirements'].get('kind') == '仙器':
            base = fairy_base(kind, value['grade'], value['slot'])
            if base:
                if preset_items is None:
                    preset_items = set()
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_equipment_attribute_sets'").fetchone():
                        preset_items = {r[0] for r in db.execute(
                            'SELECT DISTINCT item_id FROM native_equipment_attribute_sets WHERE character_id=?', (cid,))}
                value['properties'] = apply_fairy_base(kind, base, value['requirements'], value['properties'],
                                                       value['item_id'] in preset_items)
        result[value['item_id']]=value
    return result


def roll(profile,rng):
    # The reference assigns (rather than sums) a repeated base property.
    values={}
    for choices in profile['rows']:
        choice=rng.choice(choices);value=rng.randint(choice['low'],choice['high']);divisor=choice.get('divisor',1)
        values[choice['name']]=value if divisor==1 else value/divisor
    return values


def persist(db,cid,item_id,profile,properties):
    requirements=dict(profile['need'],level=profile['level'],rebirth=profile['rebirth'])
    for key in ('ascension','sanctification','native_race','native_help','kind','level_exempt'):
        if key in profile:requirements[key]=profile[key]
    role=profile['role']
    if (profile.get('kind') == '神兵' and profile['reference_id'] in CROWN_HELP
            and profile['slot'] == 1 and 1 <= profile['grade'] <= 5):
        role,requirements,properties=crown_stats(profile['reference_id'],profile['grade'])
    if profile.get('kind') == '仙器':
        item = db.execute('SELECT type FROM inventory WHERE id=? AND character_id=?', (item_id, cid)).fetchone()
        base = item and fairy_base(item[0], profile['grade'], profile['slot'])
        if base:
            properties = apply_fairy_base(item[0], base, requirements, properties)
    db.execute('INSERT INTO native_equipment VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (item_id,cid,profile['reference_id'],profile['grade'],profile['slot'],role,
         json.dumps(requirements,ensure_ascii=False),json.dumps(properties,ensure_ascii=False),
         profile['durability'],profile['durability'],DATA['version']))


def bonuses(items,instances,page=1,*,sockets=None):
    from native946_star_rewards import CATEGORY, DATA as STAR

    core={k:0 for k in CORE.values()};direct={k:0 for k in DIRECT.values()};percent={}
    for item in items:
        if item['page']!=page or item['id'] not in instances:continue
        instance=instances[item['id']]
        if not instance.get('durability',1):continue
        values=list(instance['properties'].items())
        requirements=instance.get('requirements', {})
        fairy = fairy_profile(requirements)
        flat_resists = FAIRY_FLAT_RESISTS if fairy else {}
        if requirements.get('kind')==CATEGORY or fairy:
            native_values=[]
            for name,value in values:
                name=STAR['aliases'].get(name,name)
                key = STAR['flat_effects'].get(name) or flat_resists.get(name)
                if key:
                    percent[key]=percent.get(key,0)+value
                else:
                    native_values.append((name,value))
            values=native_values
        if sockets:values.extend(sockets.get(item['id'],{}).items())
        for name,value in values:
            name=ALIASES.get(name,name)
            if name in CORE:core[CORE[name]]+=value
            elif name in DIRECT:direct[DIRECT[name]]+=value
            elif name in PERCENT:percent[PERCENT[name]]=percent.get(PERCENT[name],0)+int(Decimal(str(value))*10)
            elif name in FLAT_SECONDARY:percent[FLAT_SECONDARY[name]]=percent.get(FLAT_SECONDARY[name],0)+value
    return core,{key:round(value) for key,value in direct.items()},percent


def validate_wear(snapshot,item,slot,*,page=None):
    instance=snapshot['equipment'].get(item['id'])
    if instance is None or instance['slot']!=slot:raise ValueError('装备部位不匹配。')
    from native946_characters import ROLES
    role=ROLES[str(snapshot['role_id'])]['name']
    role_tags=set(instance['role'].replace('、',',').split(','))
    if not role_tags.intersection((role,'男' if snapshot['sex']==1 else '女','通用','全部','')):
        raise ValueError('装备角色要求不匹配。')
    need=instance['requirements']
    exemption=need.get('level_exempt')
    exempt=(exemption in ('一转','1转') and snapshot['rebirth']>=1
            or exemption in ('二转','2转') and snapshot['rebirth']>=2
            or exemption=='飞升' and snapshot['ascension']>=1)
    if (snapshot['level']<need['level'] and not exempt) or snapshot['rebirth']<need['rebirth']:raise ValueError('尚未满足装备的等级或转生要求。')
    if snapshot['ascension']<need.get('ascension',0):raise ValueError('该装备需要飞升后使用。')
    if snapshot['sanctification']<need.get('sanctification',0):raise ValueError('该装备需要封圣后使用。')
    if need.get('native_race') and snapshot['race']!=need['native_race']:raise ValueError('装备种族要求不匹配。')
    # Neither this item nor the old item in its destination slot can supply
    # the ability points required to put this item on.
    current={key:snapshot.get('base_'+key,snapshot[key]) for key in CORE.values()}
    page=snapshot.get('equipment_page',1) if page is None else page
    for other in snapshot['items']:
        if other['page']==page and other['slot']!=slot and other['id'] in snapshot['equipment']:
            for name,value in snapshot['equipment'][other['id']]['properties'].items():
                name=ALIASES.get(name,name)
                if name in CORE:current[CORE[name]]+=value
    for name,key in CORE.items():
        if current[key]<need.get(name,0):raise ValueError('尚未满足装备的'+name+'要求。')
    if need.get('balanced') and sum(value>=need['balanced'] for value in current.values())<2:
        raise ValueError('均衡装备需要两项基础能力达到'+str(need['balanced'])+'。')


def property_lines(properties, percent):
    return [f"【{name}】{value}{'%' if name in percent else ''}" for name,value in properties.items()]


def description(instance):
    lines=['【装备等级】'+str(instance['grade']),'【装备角色】'+instance['role']]
    need=instance['requirements']
    percent = (fairy_profile(need) or {}).get('percent', ())
    lines.extend(property_lines(instance['properties'], percent))
    lines.extend('【需要'+name+'】'+str(need[name]) for name in CORE if need.get(name,0))
    if need.get('level'):
        stage='封圣' if need.get('sanctification') else '飞升' if need.get('ascension') else ''
        lines.append(f"【等级要求】{stage}{need['level']}级")
    if need.get('balanced'):lines.append('【需要两项能力】'+str(need['balanced']))
    if need.get('kind'):lines.append('【装备种类】'+need['kind'])
    return '#r'.join(lines)
