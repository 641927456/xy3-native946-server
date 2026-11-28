"""Growth economy acceptance on disposable accounts, without desktop input."""
from copy import deepcopy
from random import Random
from time import monotonic
import json,unittest
from unittest.mock import patch
from native946_codec import number
from native946_login import MessageReader
from native946_task_chain import TaskChain,ChainBattle,templates,payout,active,RULE,QID_BASE
from native946_crafting import Crafting,MAKE,MAKE_FTR,REFRESH_FTR,META
from native946_workshops import DATA as WORKSHOPS,Workshops
from native946_equipment import persist,records,DATA as EQUIPMENT,validate_wear,bonuses
from native946_skillbooks import SkillBooks,BOOKS,SKILLS,raw_list
from native946_guardian_magic import prepared,cast,control_chance,combat_skills
from native946_progression import Progression
from test_native946_npc_services import mid
import test_native946_npcs as fixtures

class GrowthFlowTests(unittest.TestCase):
    setUp=fixtures.NpcGameplayTests.setUp
    approach=fixtures.NpcGameplayTests.approach
    def send(self,message,*args):return self.game.receive(message,MessageReader(b''.join(number(n) for n in args)))
    def mature(self,level=170):
        p=self.game.progression
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=?,gold=10000000,experience=10000000 WHERE id=?',(level,p.cid))
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1,sanctification=1 WHERE character_id=?',(p.cid,))
            db.execute('UPDATE progression SET cor=1000,mag=1000,strength=1000,speed=1000 WHERE character_id=?',(p.cid,))
        s=p.snapshot();p.set_vitals(s['hpmax'],s['mpmax'])
    def put(self,kind,amount=1):
        with self.game.progression.transaction() as db:
            used={r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.game.progression.cid,))}
            slot=next(i for i in range(1,25) if i not in used)
            return db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,?,?)',(self.game.progression.cid,kind,slot,amount)).lastrowid
    def choose(self,op,nid):
        self.send(1034,nid)
        index=next(i for i,a in enumerate(self.game.dialog_actions,1) if isinstance(a,dict) and a.get('kind')=='task_chain' and a['op']==op)
        return self.game.receive(1032,MessageReader(bytes([index])+number(nid)))
    def chain(self):return TaskChain(self.game)
    def accept(self):
        self.mature();self.approach(*RULE['giver']);self.choose('accept',RULE['giver'][1]);return self.chain().current()

    def test_native_menu_accept_visit_item_donation_freeze_and_cold_restore(self):
        row=self.accept();self.assertEqual((row['ring'],row['task']['kind']),(1,'visit'))
        self.approach(row['task']['scene'],row['task']['npc']);self.choose('complete',row['task']['npc'])
        row=self.chain().current();self.assertEqual(row['ring'],2)
        with self.game.progression.transaction(write=False) as db:before=dict(db.execute('SELECT * FROM cultivation_state WHERE character_id=?',(self.game.progression.cid,)).fetchone())
        item=self.put(row['task']['item'],2);self.approach(row['task']['scene'],row['task']['npc'])
        value=self.chain().complete(row['id'],2,donate=True);after=self.chain().current()
        self.assertEqual(after['submitted_value'],row['task']['value']*2)
        self.assertEqual(value,self.chain().complete(row['id'],2,donate=True))
        self.assertEqual(self.chain().current()['ring'],3)
        self.approach(*RULE['giver']);self.choose('freeze',RULE['giver'][1]);saved=self.chain().current()
        self.assertEqual(saved['state'],'frozen')
        p=Progression(self.path,'npc_owner',self.game.progression.cid)
        self.assertEqual(p.snapshot()['task_chain'],saved)
        self.choose('resume',RULE['giver'][1]);self.assertEqual(self.chain().current()['state'],'active')
        self.assertTrue(any(mid(p)==1973 for p in self.game.progression.task_packets()))

    def test_full_300_rings_one_final_reward_and_replay_safety(self):
        row=self.accept();run=row['id'];count=0;cash_before=self.game.progression.snapshot()['gold']
        for ring in range(1,301):
            row=self.chain().current();self.assertEqual(row['ring'],ring)
            if row['task']['kind']=='item':self.put(row['task']['item'],2)
            value=self.chain().complete(run,ring,battle_id='fixture:'+str(ring),captured=True,donate=True)
            if value['drops']:count+=1;self.assertEqual(ring,300)
            self.assertEqual(value,self.chain().complete(run,ring,battle_id='replay',captured=True,donate=True))
        self.assertEqual(count,1);self.assertIsNone(self.chain().current())
        with self.game.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_chain_rewards WHERE run_id=?',(run,)).fetchone()[0],300)
            saved=dict(db.execute('SELECT * FROM native_chain_runs WHERE id=?',(run,)).fetchone())
        self.assertEqual(saved['state'],'completed');self.assertEqual(value['quality'],2)
        self.assertEqual(self.game.progression.snapshot()['gold'],cash_before+value['gold'])

    def test_invalid_cross_owner_stale_expired_and_cancel_do_not_reward(self):
        row=self.accept();p=self.game.progression
        with self.assertRaises(ValueError):self.chain().complete(row['id']+1,1)
        self.game.character['x']=0;self.game.character['y']=0
        with self.assertRaises(ValueError):self.chain().execute(RULE['giver'][1],dict(op='freeze',run=row['id']))
        with p.transaction() as db:db.execute('UPDATE native_chain_runs SET expires=0 WHERE id=?',(row['id'],))
        with self.assertRaises(ValueError):self.chain().complete(row['id'],1)
        self.send(1980,QID_BASE+row['id']);self.assertIsNone(self.chain().current())
        with self.assertRaises(ValueError):self.chain().accept()
        with p.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM native_chain_rewards').fetchone()[0],0)

    def test_real_chain_battle_and_capture_settlement_uses_native_commands(self):
        initial=self.accept();p=self.game.progression;cid=p.cid
        for ring in (3,6,50):
            with p.transaction() as db:
                db.execute('UPDATE native_chain_runs SET ring=?,task=? WHERE id=?',(ring,json.dumps(templates(initial['seed'],ring)),initial['id']))
            row=self.chain().current();battle=ChainBattle(self.game,row)
            for enemy in battle.enemies()[1:]:battle.state['fighters'].pop(enemy['id'])
            self.assertIn(2171,[mid(v) for v in battle.entry(self.game)])
            # Preserve the actual native receive/resolve/capture paths, while
            # making the fixture short and deterministic.
            for enemy in battle.enemies():enemy.update(hp=1,attack=1,initiative=1)
            hero=battle.state['fighters'][cid];hero.update(attack=100000,initiative=100000)
            if ring==6:
                for seed in range(100):
                    if Random(seed).randrange(100)<80:battle.rng.seed(seed);break
                request=MessageReader(number(5)+number(battle.enemies()[0]['id']))
                with patch.object(self.game.summons,'carry_limit',return_value=0), \
                     patch.object(self.game.summons,'acquire',side_effect=AssertionError('Task capture must not create a pet')):
                    battle.receive(self.game,2177,request)
            else:battle.receive(self.game,2176,MessageReader(number(0)))
            self.assertTrue(battle.state['chain_completed'])
            self.assertEqual(self.chain().current()['ring'],ring+1)
            with p.transaction(write=False) as db:
                self.assertFalse(db.execute('SELECT 1 FROM summons WHERE character_id=? AND grant_key LIKE ?',(cid,'capture:'+battle.id+':%')).fetchone())
            battle.finish(True)

    def test_all_high_recipes_generate_exact_native_instances_and_stage_checks(self):
        self.mature();p=self.game.progression
        for grade in (16,17):
            recipe=next(r for r in WORKSHOPS['recipes'] if r['equip_grade']==grade and '火薇' in r['tips'])
            index=WORKSHOPS['recipes'].index(recipe);profile=EQUIPMENT['crafting']['profiles'][str(index)]['力量'][0]
            source_recipe=next((i,r) for i,r in enumerate(WORKSHOPS['recipes']) if r['equip_grade']==grade-1 and r['equip_shape']==recipe['material_shape_1'])
            oldprofiles=EQUIPMENT['crafting']['profiles'][str(source_recipe[0])]['力量'];source=deepcopy(oldprofiles[0]);source['durability']=1000
            wid=recipe['zuofang_id']
            with p.transaction() as db:
                db.execute('DELETE FROM inventory WHERE character_id=?',(p.cid,))
                db.execute('INSERT OR REPLACE INTO native_workshops(character_id,workshop_id,rank) VALUES(?,?,9)',(p.cid,wid))
                iid=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,1,1)',(p.cid,source['native_type'])).lastrowid
                persist(db,p.cid,iid,source,{'攻击':100})
                db.execute('UPDATE native_equipment SET durability=271 WHERE item_id=?',(iid,))
            mat=self.put(recipe['make_items'][0][0],2);drug=self.put(6110,2)
            self.send(3597);self.game.receive(2488,MessageReader(bytes([wid])))
            before=p.snapshot()['gold']
            body=number(iid)+bytes([1])+number(mat)+number(drug)+number(0)
            result=self.game.receive(MAKE_FTR,MessageReader(body));self.assertIn(5471,[mid(v) for v in result])
            made=self.game.last_event['item_id'];s=p.snapshot();instance=s['equipment'][made]
            self.assertEqual((instance['grade'],instance['requirements']['sanctification'],instance['max_durability'],instance['durability']),(grade,1,1000,271))
            self.assertEqual(before-s['gold'],recipe['make_cash']);self.assertEqual(instance['item_id'],made)
            item=next(i for i in s['items'] if i['id']==made)
            validate_wear(s,item,instance['slot'])
            with self.assertRaises(ValueError):validate_wear(dict(s,sanctification=0),item,instance['slot'])
            self.assertEqual(p.snapshot()['gold'],s['gold'])
            p.move_item(made,1,instance['slot'],expected_page=8)
            self.assertGreater(p.snapshot()['attack'],s['attack'])
        high=[r for r in WORKSHOPS['recipes'] if r['equip_grade'] in (16,17)]
        self.assertEqual(len(high),72)
        self.assertTrue(all(str(WORKSHOPS['recipes'].index(r)) in EQUIPMENT['crafting']['profiles'] for r in high))

    def test_workshop_study_advance_is_costed_and_no_repeat_version_rebuild(self):
        self.mature();p=self.game.progression
        with p.transaction() as db:db.execute('INSERT INTO native_workshops(character_id,workshop_id) VALUES(?,1)',(p.cid,))
        before=p.snapshot();Workshops(self.game).study(1);after=p.snapshot()
        self.assertEqual((before['gold']-after['gold'],before['experience']-after['experience']),(5000,1000))
        Workshops(self.game).study(1,advance=True)
        self.assertEqual(Workshops(self.game).records()[0]['Level'],1)
        original=self.game.progression
        self.game.ensure_progression();self.game.ensure_progression();self.assertIs(self.game.progression,original)

    def test_every_grade16_17_recipe_really_produces_and_recasts_once(self):
        from time import monotonic
        from native946_crafting import REFRESH,GROWTH,recipe_types
        self.mature();p=self.game.progression
        for recipe in (r for r in WORKSHOPS['recipes'] if r['equip_grade'] in (16,17)):
            grade=recipe['equip_grade'];wid=recipe['zuofang_id']
            prior_index=next(i for i,r in enumerate(WORKSHOPS['recipes']) if r['equip_grade']==grade-1 and r['equip_shape']==recipe['material_shape_1'])
            src_profiles=EQUIPMENT['crafting']['profiles'][str(prior_index)]
            source=deepcopy(next(iter(src_profiles.values()))[0]);source['durability']=1000
            direction=next(iter(EQUIPMENT['crafting']['profiles'][str(WORKSHOPS['recipes'].index(recipe))]))
            drug_type={'力量':6110,'根骨':6111,'敏捷':6112,'灵性':6113,'通用':6110}[direction]
            with p.transaction() as db:
                db.execute('DELETE FROM inventory WHERE character_id=?',(p.cid,))
                db.execute('UPDATE characters SET gold=10000000 WHERE id=?',(p.cid,))
                db.execute('INSERT OR REPLACE INTO native_workshops(character_id,workshop_id,rank) VALUES(?,?,9)',(p.cid,wid))
                item=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,8,1,1)',(p.cid,source['native_type'])).lastrowid
                persist(db,p.cid,item,source,{'攻击':1})
            material=self.put(recipe['make_items'][0][0],2);medicine=self.put(drug_type,2)
            self.game.workshop_production_session=dict(workshop=wid,expires=monotonic()+600)
            with self.subTest(grade=grade,name=recipe['result_name']):
                Crafting(self.game).produce(MAKE,item,material,medicine,0);made=self.game.last_event['item_id']
                self.assertIn(p.snapshot()['equipment'][made]['grade'],(16,17))
                with p.transaction() as db:db.execute('UPDATE inventory SET type=? WHERE id=?',(recipe['recast_items'][0][0],material))
                Crafting(self.game).produce(REFRESH,made,material,medicine,0);recast=self.game.last_event['item_id']
                s=p.snapshot();self.assertEqual(s['gold'],10000000-recipe['make_cash']-GROWTH['recast_cash'][str(grade)])
                self.assertIn(next(i['type'] for i in s['items'] if i['id']==recast),recipe_types(recipe))

    def test_native_book_learning_and_group_spell_has_one_actor_multiple_targets(self):
        self.mature();s=self.game.summons
        # Use a native eligible kind, not a free live character/pet grant.
        from native946_wilderness import DATA as GROWTH
        for item in (6230,6231,6232,6233,6599,6600,6601,6603,6604,6605):
            rule=BOOKS[item];kind=next(int(k) for k,v in GROWTH['species'].items() if v['kind'] in rule['allowed_kinds'])
            sid=s.acquire(kind,'fixture:book:'+str(item),level=100)
            iid=self.put(item);SkillBooks(s).learn(sid,iid)
            with self.game.progression.transaction() as db:db.execute('UPDATE native_guardian_books SET level=60 WHERE summon_id=?',(sid,))
            pet=s.get(sid)
            self.assertEqual(pet['learned_books'][0]['skill'],rule['skill'])
            self.assertTrue(SkillBooks(s).packet(sid))
            if rule.get('active'):
                pet['mp']=pet['mpmax'];v=prepared(pet,rule['skill']);old=pet['mp']
                targets=[dict(id=901000+i,hp=10000,ReFire=0) for i in range(4)]
                result=cast(self.game,pet,targets,901001,rule['skill'],Random(800))
                self.assertEqual(pet['mp'],old-v['mana']);self.assertEqual(sum(t['hp']<10000 for t in targets),3)
                begin=next(p for p in result if mid(p)==2157);r=MessageReader(begin[3:]);self.assertEqual([r.integer(),r.integer()],[sid,901001]);self.assertEqual(r.take(1),b'\3')
                self.assertTrue(combat_skills(pet));self.assertEqual(v['proficiency'],15000)
            else:
                self.assertTrue(any(pet.get(k,0)>0 for k in rule['coefficients']))
                self.assertLess(control_chance(800,pet,next(k for k in ('seal','faint','disorder') if pet.get({'seal':'book_reseal','faint':'book_refaint','disorder':'book_redisorder'}[k],0))),800)
            with self.game.progression.transaction() as db:db.execute('DELETE FROM summons WHERE id=?',(sid,))

    def test_property_names_change_real_derived_stats_and_native_resistance_units(self):
        items=[dict(id=1,page=1)]
        instance={1:dict(properties={'增加攻击':120,'增加气血':80,'增加速度':20,'抗风法':140,'抗封印':30,'致命几率':12})}
        for metadata in ({}, {'requirements': {'level': 50}}):
            with self.subTest(metadata=metadata):
                _,direct,resist=bonuses(items,{1:dict(instance[1],**metadata)})
                self.assertEqual((direct['attack'],direct['hpmax'],direct['initiative']),(120,80,20))
                self.assertEqual((resist['ReWind'],resist['ReSeal'],resist['FatalRate']),(140,300,120))

        from native946_star_rewards import CATEGORY, profile
        star = profile('7', 3)
        self.assertEqual(star['item'], 13261)
        equipment = {1: dict(requirements={'kind': CATEGORY}, properties=star['properties'])}
        core, _, resist = bonuses(items, equipment)
        self.assertEqual((core['mag'], resist['AttValue'], resist['AddFire'], resist['AddWind']), (18,80,90,75))

    def test_supply_replay_only_charges_missing_items_in_unlocked_bags(self):
        row=self.accept();p=self.game.progression
        self.chain().complete(row['id'],1)
        current=self.chain().current()
        wanted=current['task']['item']
        with p.transaction() as db:
            db.execute("INSERT INTO cosmetic_features(character_id,key,value) VALUES(?,'bag_item',1)",(p.cid,))
            db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,16,1,1)',(p.cid,wanted))
        self.approach(*RULE['giver'])
        before=p.snapshot()['gold']
        action=dict(op='supply',run=row['id'],ring=2,quantity=2)
        for _ in range(2):self.chain().execute(RULE['giver'][1],action)
        self.assertEqual(p.snapshot()['gold'],before-current['task']['value'])
        self.assertEqual(sum(i['amount'] for i in p.snapshot()['items'] if i['type']==wanted),2)
        self.chain().complete(row['id'],2,donate=True)
        self.assertFalse(any(i['type']==wanted for i in p.snapshot()['items']))

    def test_journal_points_to_accessible_cell_and_to_giver_while_frozen(self):
        from native946_task_chain import navigation_target
        from native946_world import world
        from native946_map import load_map
        row=self.accept()
        npc=world().npc(2011,2601)
        row['task'].update(scene=2011,npc=2601,x=npc['x']//20,y=npc['y']//20,name=npc['name'])
        self.assertFalse(load_map(2011).walkable(row['task']['x'],row['task']['y']))
        target=navigation_target(row)
        self.assertTrue(load_map(2011).walkable(target['x'],target['y']))
        frozen=navigation_target(dict(row,state='frozen'))
        self.assertEqual([frozen['scene'],frozen['npc']],RULE['giver'])

    def test_native_book_use_in_open_bag_and_experience_cap_debits_only_shortfall(self):
        self.mature();p=self.game.progression;s=self.game.summons;sid=s.claim_starter()
        item=self.put(6230)
        with p.transaction() as db:
            db.execute("INSERT INTO cosmetic_features(character_id,key,value) VALUES(?,'bag_item',1)",(p.cid,))
            db.execute('UPDATE inventory SET page=16,slot=1 WHERE id=?',(item,))
            db.execute('UPDATE summons SET level=100,experience=100000 WHERE id=?',(sid,))
        reply=self.send(807,sid,item)
        self.assertIn(722,[mid(v) for v in reply])
        with p.transaction() as db:
            db.execute('UPDATE native_guardian_books SET level=99,experience=998,expnext=1000 WHERE summon_id=?',(sid,))
        self.send(754,sid,336)
        pet=s.get(sid)
        self.assertEqual(pet['experience'],99998)
        self.assertEqual((pet['learned_books'][0]['level'],pet['learned_books'][0]['experience']),(100,0))

    def test_training_spell_native_dispatch_not_only_helper(self):
        self.mature();p=self.game.progression;s=self.game.summons;sid=s.claim_starter();iid=self.put(6230)
        with p.transaction() as db:db.execute('UPDATE summons SET level=100 WHERE id=?',(sid,))
        SkillBooks(s).learn(sid,iid);s.rest()
        self.game.begin_battle();self.send(2178,1)
        replies=self.send(2183,9,336,900002)
        self.assertIn(2157,[mid(v) for v in replies]);self.assertEqual(self.game.battle['phase'],'drawing')

    def test_full_bag_keeps_final_reward_and_claim_does_not_repay_cash(self):
        row=self.accept();p=self.game.progression
        with p.transaction() as db:
            db.execute('UPDATE native_chain_runs SET ring=300,task=? WHERE id=?',(json.dumps(templates(row['seed'],300)),row['id']))
            db.execute('DELETE FROM inventory WHERE character_id=?',(p.cid,))
            db.executemany('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,99)',[(p.cid,i) for i in range(1,25)])
        value=self.chain().complete(row['id'],300,battle_id='fixture:bag')
        with p.transaction(write=False) as db:self.assertTrue(db.execute('SELECT 1 FROM native_chain_pending').fetchone())
        cash=p.snapshot()['gold']
        with p.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=? AND slot=1 AND page=8',(p.cid,))
            self.chain().deliver(db);self.chain().deliver(db)
        with p.transaction(write=False) as db:
            self.assertFalse(db.execute('SELECT 1 FROM native_chain_pending').fetchone())
            self.assertEqual(db.execute('SELECT sum(amount) FROM inventory WHERE character_id=? AND type=?',(p.cid,int(next(iter(value['drops']))))).fetchone()[0],next(iter(value['drops'].values())))
        self.assertEqual(p.snapshot()['gold'],cash)

    def test_item_delivery_rollback_and_simultaneous_replays(self):
        from concurrent.futures import ThreadPoolExecutor
        row=self.accept();p=self.game.progression
        with ThreadPoolExecutor(max_workers=3) as pool:
            outcomes=list(pool.map(lambda _:self.chain().complete(row['id'],1),range(3)))
        self.assertEqual(outcomes,[outcomes[0]]*3)
        current=self.chain().current();self.put(current['task']['item'],1)
        before=p.snapshot();real=self.characters.settle_battle
        def fail_after_write(*args,**kwargs):real(*args,**kwargs);raise RuntimeError('injected settlement failure')
        with patch.object(self.characters,'settle_battle',side_effect=fail_after_write):
            with self.assertRaises(RuntimeError):self.chain().complete(row['id'],2)
        after=p.snapshot();self.assertEqual(after['items'],before['items']);self.assertEqual(after['gold'],before['gold'])
        self.assertEqual(self.chain().current()['ring'],2)

    def test_existing_tier_one_book_migration_preserves_every_saved_column(self):
        from native946_skillbooks import schema
        self.mature();sid=self.game.summons.claim_starter();p=self.game.progression
        with p.transaction() as db:
            db.execute('DROP TABLE native_guardian_books')
            db.execute('''CREATE TABLE native_guardian_books (
                summon_id INTEGER NOT NULL REFERENCES summons(id) ON DELETE CASCADE,
                skill INTEGER NOT NULL CHECK(skill BETWEEN 300 AND 309),position INTEGER NOT NULL,
                level INTEGER NOT NULL,rebirth INTEGER NOT NULL,experience INTEGER NOT NULL,
                expnext INTEGER NOT NULL,tier INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(summon_id,skill),UNIQUE(summon_id,position))''')
            saved=[(sid,300,7,12,0,17,999,0),(sid,307,8,25,0,33,1234,1)]
            db.executemany('INSERT INTO native_guardian_books VALUES(?,?,?,?,?,?,?,?)',saved)
            schema(db);schema(db)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM native_guardian_books ORDER BY skill')],
                             [(*row,0,0) for row in saved])
            db.execute('INSERT INTO native_guardian_books(summon_id,skill,position,level,rebirth,experience,expnext,tier) '
                       'VALUES(?,365,9,1,0,0,40,1)',(sid,))

    def test_native_control_buffs_change_actions_and_resistance_prevents_them(self):
        self.mature();cid=self.game.progression.cid
        for resistance in (0,1000):
            row=dict(id=1,ring=150,level=170,task=templates('control',150),seed='control')
            battle=ChainBattle(self.game,row)
            for enemy in battle.enemies()[1:]:battle.state['fighters'].pop(enemy['id'])
            enemy=battle.enemies()[0];enemy.update(initiative=100000,hp=100000,hpmax=100000,
                                                 control_policy=dict(interval=3,base_per_mille=1000,duration=2))
            hero=battle.state['fighters'][cid];hero.update(initiative=1,cultivation={'ReSeal':resistance})
            battle.receive(self.game,2176,MessageReader(number(enemy['id'])))
            self.assertEqual('seal' in hero.get('controls',{}),not bool(resistance))
            self.assertEqual(enemy['hp']==100000,not bool(resistance))
            if not resistance:
                self.assertEqual(battle.required(),{cid})
                from native946_storage import StorageBusy
                battle.state['acks']={cid}
                battle.advance_if_ready()
                self.assertEqual((battle.state['round'],battle.state['phase']),(2,'input'))
                deadline=battle.state['deadline']
                self.assertGreater(deadline,monotonic())
                self.send(2176,enemy['id'])
                self.assertIn('当前只能防御',self.game.last_event['reason'])
                with patch.object(battle,'resolve',side_effect=StorageBusy('fixture busy')):
                    with self.assertRaises(StorageBusy):battle.tick(deadline)
                self.assertEqual(battle.state['deadline'],deadline)
                battle.tick(deadline)
                self.assertEqual(battle.state['phase'],'drawing')
                hero=battle.state['fighters'][cid]
                enemy=battle.enemies()[0]
                battle.state.update(round=3,phase='input',orders={cid:dict(kind='attack',skill=10,target=enemy['id'],mana=0)})
                battle.resolve();self.assertNotIn('seal',hero['controls']);self.assertLess(enemy['hp'],100000)
            battle.finish(False)

    def test_expired_chain_battle_ends_without_reward_or_stuck_resolution(self):
        row=self.accept();p=self.game.progression
        with p.transaction() as db:
            db.execute('UPDATE native_chain_runs SET ring=3,task=?,expires=0 WHERE id=?',(json.dumps(templates(row['seed'],3)),row['id']))
        current=self.chain().current();battle=ChainBattle(self.game,current)
        for enemy in battle.enemies():enemy['hp']=0
        with battle.atomic() as db:battle.settle(db,True)
        self.assertFalse(battle.state['chain_completed']);self.assertTrue(battle.state['settled'])
        with p.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM native_chain_rewards').fetchone()[0],0)
        battle.finish(True);self.assertIsNone(self.game.battle)

if __name__=='__main__':unittest.main()
