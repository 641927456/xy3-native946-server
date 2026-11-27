"""Native production using client recipes and explicit supplied-reference rolls."""
from pathlib import Path
from time import monotonic
from time import time
from hashlib import sha256
from random import Random
from secrets import token_hex
import json

REFERENCE_WIRE_VERSION=11

from native946_codec import packet
from native946_codec import text_notice
from native946_workshops import DATA as WORKSHOPS
from native946_equipment import COMPLETION

DATA=json.loads(Path(__file__).with_name('native946_crafting_data.json').read_text('utf8'))
MAKE=DATA['protocols']['s_new_equip2_make']['wire_id']
REFRESH=DATA['protocols']['s_new_equip2_refresh']['wire_id']
MAKE_FTR=DATA['protocols']['s_new_equip2_make_ftr']['wire_id']
REFRESH_FTR=DATA['protocols']['s_new_equip2_refresh_ftr']['wire_id']
META=DATA['item_metadata']
GROWTH_DATA=json.loads(Path(__file__).with_name('native946_growth_rules.json').read_text('utf8'))
GROWTH=GROWTH_DATA['crafting']
META.update(GROWTH_DATA.get('item_metadata',{}))
for _kind,_meta in COMPLETION['items'].items():META.setdefault(_kind,_meta)
DATA['craft_rank_by_grade'].update(GROWTH['rank_by_grade'])
ACCESSORY_RANK={1001:8,1002:8,1003:9,1004:9}

def recipe_types(recipe):
    return [t for raw in recipe['equip_type_list'] for t in GROWTH_DATA.get('recipe_type_corrections',{}).get(str(raw),[raw])]


WORKSHOP_FORGE={(r['zuofang_id'],r['equip_grade'],kind):r for r in WORKSHOPS['recipes']
               if r['forge_cash'] for kind in recipe_types(r)}

def production_cash(recipe,message):
    from native946_equipment import DATA as EQUIPMENT
    fees=EQUIPMENT['crafting']
    if message==MAKE:return recipe['make_cash'] or fees['upgrade_cash']
    return GROWTH['recast_cash'].get(str(recipe['equip_grade']),fees['recast_cash'])

def validate_source(db,cid,source,recipe,message,instance):
    grade=recipe['equip_grade']
    if 16<=grade<1000 or grade>=1002 or (grade==1001 and message==REFRESH):
        expected=grade-1 if message==MAKE else grade
        if instance is None or instance['grade']!=expected:
            raise ValueError(f'请放入带有属性的{expected}级原装备。')
        if message==MAKE:
            predecessors=[row for row in WORKSHOPS['recipes'] if row['equip_grade']==expected
                          and row['equip_shape']==recipe['material_shape_1']]
            if not any(source['type'] in recipe_types(row) for row in predecessors):
                raise ValueError('原装备与当前升级配方不匹配。')
    # These separate item records are removed by a cascade when forging
    # replaces inventory identity. Reject before a paid customization is lost.
    protected=db.execute(
        """SELECT 1 FROM cosmetic_item_carvings WHERE character_id=? AND item_id=?
           UNION ALL SELECT 1 FROM native_equipment_attribute_sets WHERE character_id=? AND item_id=?
           UNION ALL SELECT 1 FROM native_refinement WHERE character_id=? AND item_id=?
           AND pending<>'{}' LIMIT 1""",
        (cid,source['id'],cid,source['id'],cid,source['id'])).fetchone()
    if protected:
        raise ValueError('该装备带有刻字、待确认的炼化或备用属性，暂不能升级或重铸。')


def extra_chain_reward_due(completed_rings):
    if type(completed_rings) is not int or not 1<=completed_rings<=DATA['task_chain']['rings']:
        raise ValueError('任务链环数无效。')
    return completed_rings in DATA['task_chain']['extra_item_reward_rings']


class Crafting:
    def __init__(self,game):self.g=game;self.p=game.progression

    def open(self,wid,*,from_npc=False):
        from native946_workshops import Workshops
        if self.g.battle:raise ValueError('请先结束战斗再操作装备。')
        if type(wid) is not int or str(wid) not in WORKSHOPS['workshops']:raise ValueError('作坊编号无效。')
        if not from_npc and getattr(self.g,'workshop_query_until',0)<monotonic():raise ValueError('请先打开作坊界面。')
        record=next((r for r in Workshops(self.g).records() if r['Id']==wid),None)
        if not record:raise ValueError('请先加入对应的装备作坊。')
        result=packet(220,['number',('array','byte'),'number','number','number','number','number'],
                      [1,[1,2],wid,record['Level'],0,self.p.cid,0])
        self.g.workshop_production_session=dict(workshop=wid,expires=monotonic()+600)
        return (packet(1183,['string'],['dlg_npc_chat']),result)

    def quote(self,message,equip,material,medicine,owner,mode=0,*,production=False):
        from native946_workshops import Workshops
        if message not in (MAKE,REFRESH):raise ValueError('装备操作类型无效。')
        if self.g.battle:raise ValueError('战斗中不能加工装备。')
        session=getattr(self.g,'workshop_production_session',None)
        if not session or session['expires']<monotonic():raise ValueError('请先打开对应作坊的生产界面。')
        if owner not in (0,self.p.cid):raise ValueError('当前未开放委托其他玩家作坊加工。')
        if message==REFRESH and mode!=0:raise ValueError('该重铸保留模式尚未核实，未执行加工。')
        stones=[material] if type(material) is int else material
        if not isinstance(stones,(list,tuple)) or not 1<=len(stones)<=255:raise ValueError('请放入配方所需的宝玉材料。')
        ids=(equip,*stones,medicine)
        if any(type(i) is not int or i<=0 for i in ids) or len(set(ids))!=len(ids):raise ValueError('装备和材料必须分别放入不同的物品。')
        with self.p.transaction(write=False) as db:
            from native946_mall_bags import pages
            bag_pages=pages(db,self.p.cid)
            rows={r['id']:dict(r) for r in db.execute('SELECT * FROM inventory WHERE character_id=? AND id IN ('+','.join('?' for _ in ids)+')',(self.p.cid,*ids))
                  if r['page'] in bag_pages}
            money=db.execute('SELECT gold FROM characters WHERE id=?',(self.p.cid,)).fetchone()[0]
            from native946_inventory_locks import require_unlocked
            for iid in rows:require_unlocked(db,self.p.cid,iid)
        if len(rows)!=len(ids):raise ValueError('装备或材料不在当前角色的随身背包中。')
        source=rows[equip];drug=rows[medicine];meta=META.get(str(source['type']))
        if not meta or source['amount']!=1:raise ValueError('请放入单件原装备或首饰胚料。')
        wid=session['workshop'];record=next((r for r in Workshops(self.g).records() if r['Id']==wid),None)
        if not record:raise ValueError('你尚未加入对应的装备作坊。')
        if message==MAKE:
            candidates=[r for r in WORKSHOPS['recipes'] if r['zuofang_id']==wid and r['material_shape_1']==meta['icon']
                        and (meta['btype']==5 or r['equip_grade']==1001 and meta['name']==r['material_base_name'])]
        else:
            candidates=[r for r in WORKSHOPS['recipes'] if r['zuofang_id']==wid and source['type'] in recipe_types(r)]
        if len(candidates)!=1:raise ValueError('此装备与当前作坊的已核定配方不匹配。')
        recipe=candidates[0];grade=recipe['equip_grade']
        required_rank=ACCESSORY_RANK.get(grade,DATA['craft_rank_by_grade'].get(str(grade)))
        if required_rank is None:raise ValueError('该等级的当前生产段位要求尚未核实。')
        if record['Level']<required_rank:raise ValueError('作坊段位不足，需要'+str(required_rank)+'段。')
        req=recipe['make_items' if message==MAKE else 'recast_items']
        drug_amount=recipe['make_medicine_num' if message==MAKE else 'recast_medicine_num']
        if len(req)!=1 or any(rows[i]['type']!=req[0][0] for i in stones) or sum(rows[i]['amount'] for i in stones)<req[0][1]:
            raise ValueError('宝玉类型或数量与原生配方不符。')
        if drug['type'] not in recipe['medicines'] or drug['amount']<drug_amount:raise ValueError('药引类型或数量与原生配方不符。')
        cash=production_cash(recipe,message)
        if money<cash:raise ValueError('随身金钱不足。')
        profile=None
        if production:
            from native946_equipment import DATA as REFERENCE
            index=WORKSHOPS['recipes'].index(recipe)
            attribute=REFERENCE['crafting']['medicine_attributes'].get(str(drug['type']))
            profiles=REFERENCE['crafting']['profiles'].get(str(index),{})
            profile=profiles.get(attribute) or profiles.get('通用')
            if drug['type']==6114:profile=profiles.get('均衡') or profiles.get('通用')
            if grade>=1000:profile=COMPLETION['accessories'].get(str(index))
            if profile is None:raise ValueError('此配方或药引在参考端中没有完整属性数据，当前未扣除材料。')
        required=req[0][1];materials=[]
        for i in stones:
            amount=min(required,rows[i]['amount'])
            if amount:materials.append(dict(item=i,type=rows[i]['type'],amount=amount))
            required-=amount
        materials.append(dict(item=medicine,type=drug['type'],amount=drug_amount))
        return dict(operation='upgrade' if message==MAKE else 'recast',workshop=wid,result=recipe['result_name'],
                    grade=recipe['equip_grade'],required_rank=required_rank,source_item=equip,
                    materials=materials,
                    cash=cash,refinement_cash=recipe['forge_cash'],generation_verified=bool(production and profile),
                    resource_consumption=False,profile=profile)

    def produce(self,message,equip,material,medicine,owner,mode=0):
        from native946_equipment import persist,roll,records,description,DATA as REFERENCE
        from native946_progression import DATA as ITEMS
        from native946_gear_sockets import rows as socket_rows, preserve as preserve_sockets
        from native946_refinement import state as refinement_state, save as save_refinement, describe as describe_refinement
        stones=[material] if type(material) is int else material
        if not isinstance(stones,(list,tuple)):raise ValueError('材料列表无效。')
        signature=sha256(json.dumps([message,equip,sorted(stones),medicine,owner,mode],separators=(',',':')).encode()).hexdigest()
        with self.p.transaction() as db:
            previous=db.execute('SELECT * FROM native_craft_orders WHERE character_id=? AND source_item=?',(self.p.cid,equip)).fetchone()
            if previous:
                if previous['signature']!=signature:raise ValueError('原装备已经加工，不能重复使用。')
                result=json.loads(previous['result'])
                visible=any(i['id']==previous['output_item'] for i in self.p._snapshot(db)['items'])
                result_packet=(packet(result['message'],['number','number','number','string','string'],
                               [previous['output_item'],0,0,result['old_desc'],result['new_desc']]),) if visible and result else ()
                inventory=self.p.item_packets()
                cash=db.execute('SELECT gold FROM characters WHERE id=?',(self.p.cid,)).fetchone()[0]
                notice=text_notice('该加工已经完成，本次没有重复扣款。')
                essential=(*inventory,*result_packet,packet(695,['number','number'],[34,cash]),notice)
                return self.g.defer_packets(lambda:(*essential,*self.p.attribute_packets()),fallback=essential)
            q=self.quote(message,equip,stones,medicine,owner,mode,production=True)
            original=db.execute('SELECT id,page,slot,type FROM inventory WHERE id=? AND character_id=?',(equip,self.p.cid)).fetchone()
            original_type=original['type']
            old_instance=records(db,self.p.cid).get(equip)
            recipe=next(row for row in WORKSHOPS['recipes'] if row['zuofang_id']==q['workshop']
                        and row['equip_grade']==q['grade'] and row['result_name']==q['result'])
            validate_source(db,self.p.cid,original,recipe,message,old_instance)
            refinement=refinement_state(db,self.p.cid,equip)
            sockets=socket_rows(db,equip)
            old_desc=ITEMS['items'][str(original_type)]['name']+'#r'+ITEMS['items'][str(original_type)]['desc']
            if old_instance:old_desc+='#r'+description(old_instance)
            seed=token_hex(24);rng=Random(seed)
            if isinstance(q['profile'],list):
                profiles=q['profile']
                if old_instance and q['grade']>=16:
                    race=old_instance['requirements'].get('native_race',0)
                    same_race=[profile for profile in profiles if profile.get('native_race',0)==race]
                    if message==REFRESH and race and not same_race:
                        raise ValueError('该种族装备暂无所选方向的重铸配方。')
                    profiles=same_race or profiles
                q['profile']=dict(rng.choice(profiles))
            if q['profile'].get('preserve_source_durability'):
                if not old_instance or old_instance['grade'] not in (14,15,16,17):
                    raise ValueError('此高等级装备需有完整的原装备属性实例，本次未扣除材料。')
                q['profile']['durability']=old_instance['max_durability']
            properties=roll(q['profile'],rng)
            for row in q['materials']:
                item=db.execute('SELECT amount FROM inventory WHERE id=? AND character_id=?',(row['item'],self.p.cid)).fetchone()
                if not item or item['amount']<row['amount']:raise ValueError('材料位置或数量已经变化。')
                if item['amount']==row['amount']:db.execute('DELETE FROM inventory WHERE id=?',(row['item'],))
                else:db.execute('UPDATE inventory SET amount=amount-? WHERE id=?',(row['amount'],row['item']))
            db.execute('DELETE FROM inventory WHERE id=?',(equip,))
            iid=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                           (self.p.cid,q['profile']['native_type'],original['page'],original['slot'])).lastrowid
            persist(db,self.p.cid,iid,q['profile'],properties)
            if refinement['attempts'] or refinement['traits']:
                save_refinement(db,self.p.cid,iid,refinement)
            if sockets:preserve_sockets(db,iid,sockets)
            if q['profile'].get('preserve_source_durability'):
                db.execute('UPDATE native_equipment SET durability=min(?,max_durability) WHERE item_id=?',
                           (old_instance['durability'],iid))
            new_instance=records(db,self.p.cid)[iid]
            new_desc=q['result']+'#r'+ITEMS['items'][str(q['profile']['native_type'])]['desc']+'#r'+description(new_instance)
            if refinement['traits']:
                details='#r'+describe_refinement(refinement)
                old_desc+=details
                new_desc+=details
            contract='c_new_equip2_make_result' if message==MAKE else 'c_new_equip2_refresh_nokeep_result'
            result=dict(message=REFERENCE['crafting']['result_protocols'][contract]['wire_id'],old_desc=old_desc,new_desc=new_desc)
            db.execute('UPDATE characters SET gold=gold-? WHERE id=?',(q['cash'],self.p.cid))
            db.execute('UPDATE native_workshops SET practice=practice+? WHERE character_id=? AND workshop_id=?',
                       (GROWTH['practice_per_craft'],self.p.cid,q['workshop']))
            db.execute('INSERT INTO native_craft_orders VALUES(?,?,?,?,?,?,?,?,?)',
                       (self.p.cid,equip,signature,iid,q['operation'],q['cash'],seed,int(time()),json.dumps(result,ensure_ascii=False)))
            # Build the inventory delta while its transaction is still pinned.
            # The result callback needs the new item to exist client-side even
            # if optional attribute/tooltip reads are busy after committing.
            inventory=self.p.item_packets()
            cash=db.execute('SELECT gold FROM characters WHERE id=?',(self.p.cid,)).fetchone()[0]
            essential=(*inventory,packet(result['message'],['number','number','number','string','string'],[iid,0,0,old_desc,new_desc]),
                       packet(695,['number','number'],[34,cash]))
        self.g.last_event=dict(action='equipment_produced',character_id=self.p.cid,item_id=iid,
                              operation=q['operation'],cash=q['cash'],rule_source=REFERENCE['version'])
        notice=text_notice('加工完成：'+q['result']+'，消耗'+str(q['cash'])+'现金。')
        return self.g.defer_packets(lambda:(*essential,*self.p.attribute_packets(),self.p.item_description(iid),notice),
                                   fallback=(*essential,notice))

    def preview(self,*args):
        from native946_workshops import Workshops
        quote=self.quote(*args)
        lines=['配方：'+quote['result'],'需要作坊段位：'+str(quote['required_rank'])+'段']
        for row in quote['materials']:lines.append(META[str(row['type'])]['name']+'×'+str(row['amount']))
        lines.append(('升级现金：' if quote['operation']=='upgrade' else '重铸现金：')+str(quote['cash']))
        lines.append('材料核对通过。本次仅预览配方，未扣除物品和金钱。')
        self.g.last_event=dict(action='craft_formula_checked',character_id=self.p.cid,workshop=quote['workshop'],
                               operation=quote['operation'],resource_consumption=False)
        return Workshops.tip('配方核对','#r'.join(lines))

    def workshop_forge(self,nid,wid,action):
        from native946_equipment import records
        from native946_mall_bags import pages
        from native946_refinement import Refinement, debit, describe, save, state
        from native946_workshops import Workshops

        workshop=Workshops(self.g)
        service=Refinement(self.g)
        cid=self.p.cid
        op=action['op']
        confirmed=op=='forge_confirm'
        with self.p.transaction(write=confirmed) as db:
            member=db.execute('SELECT rank FROM native_workshops WHERE character_id=? AND workshop_id=?',(cid,wid)).fetchone()
            if not member:raise ValueError('请先加入对应的装备作坊。')
            equipment=records(db,cid)
            if op=='forge_list':
                page=action.get('page',0)
                backpack=pages(db,cid)
                owned=[]
                for row in db.execute('SELECT * FROM inventory WHERE character_id=? ORDER BY page,slot',(cid,)):
                    instance=equipment.get(row['id'])
                    if not instance or row['page'] not in backpack or row['amount']!=1:continue
                    recipe=WORKSHOP_FORGE.get((wid,instance['grade'],row['type']))
                    if recipe:
                        place=f"背包{backpack.index(row['page'])+1}第{row['slot']}格"
                        owned.append((f"{recipe['result_name']}（{place}）",
                                      dict(kind='workshop',op='forge_quote',wid=wid,item=row['id'])))
                if not owned:raise ValueError('请把本作坊可炼化的装备放在背包中。')
                options=owned[page*8:(page+1)*8]
                for label,index in (('上一页',page-1),('下一页',page+1)):
                    if 0<=index and index*8<len(owned):
                        options.append((label,dict(kind='workshop',op='forge_list',wid=wid,page=index)))
                return workshop._menu(nid,'选择要炼化的装备。装备原有附加炼化属性会被覆盖。',options)
            iid=action['item']
            item=service.item(db,iid)
            instance=equipment.get(iid)
            recipe=WORKSHOP_FORGE.get((wid,instance['grade'],item['type'])) if instance else None
            if not recipe or item['amount']!=1:raise ValueError('该装备没有可用的原生作坊炼化配方。')
            required=DATA['craft_rank_by_grade'][str(instance['grade'])]
            if member['rank']<required:raise ValueError(f'作坊段位不足，需要{required}段。')
            forge_cash=recipe['forge_cash']
            value=state(db,cid,iid)
            if value['pending']:raise ValueError('请先确认尚未保存的炼化结果。')
            if not confirmed:
                text=f"炼化{recipe['result_name']}需要{forge_cash}现金，天之痕与血玲珑各1个。本机规则。"
                return workshop._menu(nid,text,[('确认炼化',dict(kind='workshop',op='forge_confirm',wid=wid,
                                                           item=iid,attempts=value['attempts']))])
            if action['attempts']!=value['attempts']:raise ValueError('装备的炼化状态已变化，请重新选择。')
            service.take(db,6128,1)
            service.take(db,6046,1)
            debit(db,cid,forge_cash)
            value.update(traits=service.roll_gear('ordinary',instance['slot']),expires=0,lucky=0,
                         attempts=value['attempts']+1)
            save(db,cid,iid,value)
            db.execute('UPDATE native_workshops SET practice=practice+? WHERE character_id=? AND workshop_id=?',
                       (GROWTH['practice_per_craft'],cid,wid))
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.p.attribute_packets(),workshop.info_packet(),
            self.p.item_description(iid),text_notice('本机作坊炼化完成。#r'+describe(value))))
