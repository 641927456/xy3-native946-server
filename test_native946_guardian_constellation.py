"""Guardian storefront, star talismans and titles through native NPC requests."""
from random import Random
import unittest
from unittest.mock import patch

from native946_afei_society import CHAMBER
from native946_combat_control import blocked
from native946_constellations import BASE, Constellations, PENDING_GROUPS, SIGILS, credit
from native946_inventory_features import give
from native946_npc_quests import spawn_position
import test_native946_afei_expansion as fixtures
from test_native946_npc_services import mid


class GuardianConstellationTests(unittest.TestCase):
    Fixtures = fixtures.AfeiExpansionTests
    setUp = Fixtures.setUp
    call = Fixtures.call
    buff = Fixtures.buff
    fight = Fixtures.fight
    position = Fixtures.position
    near = Fixtures.near
    label = Fixtures.label
    choose = Fixtures.choose
    gold = Fixtures.gold
    items = Fixtures.items

    def click(self, g, nid, label, menu=None):
        return self.choose(g,nid,self.label(g,nid,label,look=menu is None,replies=menu or ()))

    def test_shayao_sigil_preserves_failed_entry_and_uses_local_battle(self):
        from native946_trade import Trade

        game = self.a
        player = game.progression
        kind = 45097
        rule = PENDING_GROUPS[kind]
        self.buff(game)
        self.position(game,2008,*spawn_position(2008,Random(2)))
        with player.transaction() as db:
            give(db,player,kind,2)
            db.execute('UPDATE characters SET level=141 WHERE id=?',(player.cid,))
        iid = self.items(game,kind)[0][0]

        def use(expect):
            replies = self.call(game,807,player.cid,iid)
            self.assertIn(expect.encode('gb18030'),next(p for p in replies if mid(p)==98))
            self.assertEqual([mid(p) for p in replies],[98])
            self.assertEqual(self.items(game,kind),[(iid,2)])

        use('请前往长安')
        x,y = spawn_position(rule['map'],Random(3))
        self.assertNotEqual((x,y),(552,251))
        self.position(game,rule['map'],x,y)
        use('人物须满142级')
        with player.transaction() as db:
            db.execute('UPDATE characters SET level=142 WHERE id=?',(player.cid,))
            credit(db,player.cid,dict(step=0,rank=14),False)
        use('需先击败15级或更高的星宿')
        with player.transaction() as db:
            credit(db,player.cid,dict(step=0,rank=15),True)
            Trade.validate(db,player.cid,0,[dict(id=iid,type=kind,amount=2)])
        replies = self.call(game,807,player.cid,iid)
        self.assertIn(2966,[mid(p) for p in replies])
        self.assertEqual(self.items(game,kind),[(iid,1)])
        with player.transaction(write=False) as db:
            summon = db.execute('SELECT * FROM native_star_summons').fetchone()
            self.assertEqual((summon['scene'],summon['x'],summon['y']),(2001,552,251))
            self.assertIsNone(db.execute('SELECT 1 FROM native_star_credit WHERE step=?',(rule['step'],)).fetchone())
        self.position(game,2001,552,251)
        self.click(game,8_800_000+summon['id'],'挑战星符召唤的星宿')
        battle = self.svc.battles[player.cid]
        self.assertEqual(battle.zone['enemy_layout'],7)
        self.assertEqual([f['name'] for f in battle.enemies()],
                         [f['name'] for f in rule['opening_battle']['enemies']])
        # Force the first clear to exercise the local reinforcement ordering.
        for enemy in battle.enemies():
            enemy['hp'] = 0
        battle.resolve()
        self.assertFalse(battle.state['victory'])
        self.assertEqual({f['name']:f['slot'] for f in battle.enemies()},
                         {'天威星':10,'地英星':8,'天猛星':7})
        self.call(game,2184)
        for enemy in battle.enemies():
            enemy['hp'] = 0
        battle.resolve()
        self.assertTrue(battle.state['victory'])
        with player.transaction(write=False) as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM native_star_credit WHERE step=?',(rule['step'],)).fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM native_encounter_rewards WHERE battle_id=?',
                                        (battle.afei_key,)).fetchone()[0],1)

    def test_guardian_sale_preserves_id_progress_and_appearance_and_keeps_sellers_reserve(self):
        scene,npc = CHAMBER
        for g in self.games:
            self.buff(g)
            self.near(g,scene,npc)
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=1000')
        sid = self.a.summons.acquire(3197,'market-fixture')
        self.a.summons.status(sid,2)
        with self.a.progression.transaction() as db:
            db.execute('UPDATE summons SET experience=74,amity=120 WHERE id=?',(sid,))
            db.execute('INSERT INTO native_guardian_marks VALUES(?,?)',(sid,'["物理","",""]'))
            db.execute('INSERT INTO native_guardian_books(summon_id,skill,position,level,experience) VALUES(?,300,7,2,15)',(sid,))
            db.execute('INSERT INTO cosmetic_guardian_appearance(summon_id,character_id,halo) VALUES(?,?,?)',
                       (sid,self.a.progression.cid,873))
            db.execute("INSERT INTO cosmetic_features VALUES(?,?,80,0)",(self.a.progression.cid,f'reserve:{sid}:hp'))
            page,slot = self.a.progression.free_bag_slot(self.a.progression._snapshot(db))
            iid = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                             (self.a.progression.cid,42000,page,slot)).lastrowid
        self.call(self.a,807,sid,iid)
        root = self.click(self.a,npc,'守护商铺')
        self.click(self.a,npc,'开设守护商铺',root)
        root = self.click(self.a,npc,'守护商铺')
        selection = self.click(self.a,npc,'选择守护上架',root)
        pricing = self.click(self.a,npc,'猴儿精',selection)
        self.click(self.a,npc,'100现金',pricing)
        self.assertNotIn(sid,{row['id'] for row in self.a.summons.snapshot()})
        with self.assertRaises(ValueError):
            self.a.summons.rename(sid,'别名')

        root = self.click(self.b,npc,'守护商铺')
        selection = self.click(self.b,npc,'浏览在售守护',root)
        detail = self.click(self.b,npc,'猴儿精',selection)
        self.label(self.b,npc,'确认购买',look=False,replies=detail)
        root = self.click(self.b,npc,'守护商铺')
        self.assertIn(1812,[mid(p) for p in self.click(self.b,npc,'打开守护商铺列表',root)])
        shop = self.a.progression.cid
        self.assertEqual([mid(p) for p in self.call(self.b,1845,shop,0)],[1810,1819])
        self.assertEqual([mid(p) for p in self.call(self.b,1841,shop,1)],[1819])
        self.call(self.b,1839,shop,sid,1,1)
        self.assertEqual(self.gold(self.b),1000)
        self.assertIn(1819,[mid(p) for p in self.call(self.b,1839,shop,sid,1,100)])
        self.call(self.b,1839,shop,sid,1,100)
        self.assertEqual(self.gold(self.b),900)
        self.assertEqual(self.b.summons.get(sid)['experience'],74)
        self.assertEqual(self.b.summons.get(sid)['amity'],120)
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_guardian_shop_sales').fetchone()[0],1)
            self.assertEqual(tuple(db.execute('SELECT skill,level,experience FROM native_guardian_books WHERE summon_id=?',(sid,)).fetchone()),(300,2,15))
            self.assertEqual(db.execute('SELECT character_id FROM cosmetic_guardian_appearance WHERE summon_id=?',(sid,)).fetchone()[0],self.b.progression.cid)
            self.assertEqual(db.execute('SELECT character_id FROM cosmetic_features WHERE key=?',(f'reserve:{sid}:hp',)).fetchone()[0],self.a.progression.cid)
            self.assertEqual(db.execute('SELECT marks FROM native_guardian_marks WHERE summon_id=?',(sid,)).fetchone()[0],'["物理","",""]')
            self.assertEqual(tuple(db.execute('SELECT i.character_id,e.summon_id FROM inventory i '
                'JOIN native_guardian_equipment e ON e.item_id=i.id WHERE i.id=?',(iid,)).fetchone()),
                (self.b.progression.cid,sid))
        with self.assertRaisesRegex(ValueError,'已经转售'):
            self.a.summons.acquire(3197,'market-fixture')
        self.click(self.a,npc,'取出守护商铺收入',self.click(self.a,npc,'守护商铺'))
        self.assertEqual(self.gold(self.a),1089)

    def test_guardian_shop_rejects_equipped_jade_and_allows_withdrawal(self):
        scene,npc = CHAMBER
        seller,buyer = self.a,self.b
        owner = seller.progression.cid
        for game in (seller,buyer):
            self.buff(game)
            self.near(game,scene,npc)
        sid = seller.summons.acquire(3197,'market-jade-fixture')
        seller.summons.status(sid,2)
        with seller.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=1000')
            db.execute('UPDATE characters SET level=120 WHERE id=?',(owner,))
            db.execute('UPDATE cultivation_state SET sanctification=1 WHERE character_id=?',(owner,))
            page,slot = seller.progression.free_bag_slot(seller.progression._snapshot(db))
            iid = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,47659,?,?,1)',
                             (owner,page,slot)).lastrowid
            db.execute('INSERT INTO native_guardian_jade(item_id,main,bonus) VALUES(?,?,?)',(iid,'[[1,1]]','[]'))
        self.assertIn(3944,[mid(p) for p in self.call(seller,3947,sid,1,iid)])
        self.click(seller,npc,'开设守护商铺',self.click(seller,npc,'守护商铺'))
        selection = self.click(seller,npc,'选择守护上架',self.click(seller,npc,'守护商铺'))
        notice = '请先卸下宝玉'.encode('gb18030')
        self.assertIn(notice,next(p for p in self.click(seller,npc,'猴儿精',selection) if mid(p)==98))
        with seller.progression.transaction() as db:
            self.assertIsNone(db.execute('SELECT id FROM native_guardian_shop_stock WHERE summon_id=?',(sid,)).fetchone())
            db.execute('INSERT INTO native_guardian_shop_stock(owner,summon_id,price) VALUES(?,?,100)',(owner,sid))

        seller_gold = self.gold(seller)
        self.click(buyer,npc,'打开守护商铺列表',self.click(buyer,npc,'守护商铺'))
        self.assertEqual([mid(p) for p in self.call(buyer,1845,owner,0)],[1810,1819])
        self.assertIn(notice,next(p for p in self.call(buyer,1839,owner,sid,1,100) if mid(p)==98))
        self.assertEqual((self.gold(seller),self.gold(buyer)),(seller_gold,1000))
        with seller.progression.transaction(write=False) as db:
            self.assertIsNone(db.execute('SELECT listing FROM native_guardian_shop_sales WHERE summon_id=?',(sid,)).fetchone())
            self.assertEqual(db.execute('SELECT character_id FROM summons WHERE id=?',(sid,)).fetchone()[0],owner)
            self.assertEqual(tuple(db.execute('SELECT i.character_id,j.summon_id FROM inventory i '
                'JOIN native_guardian_jade j ON j.item_id=i.id WHERE i.id=?',(iid,)).fetchone()),(owner,sid))

        with seller.progression.transaction() as db:
            db.execute('UPDATE summons SET level=101,cor=101,mag=101,strength=101,speed=101,points=404 WHERE id=?', (sid,))
        selection = self.click(seller,npc,'查看或撤回守护',self.click(seller,npc,'守护商铺'))
        self.click(seller,npc,'取回该守护',self.click(seller,npc,'猴儿精',selection))
        restored = seller.summons.get(sid)
        self.assertEqual((restored['level'], restored['experience'], restored['points']), (100, 4000, 400))
        self.assertIn(3944,[mid(p) for p in self.call(seller,3951,sid,1)])
        selection = self.click(seller,npc,'选择守护上架',self.click(seller,npc,'守护商铺'))
        self.click(seller,npc,'100现金',self.click(seller,npc,'猴儿精',selection))
        self.assertEqual([mid(p) for p in self.call(buyer,1845,owner,0)],[1810,1819])
        self.assertIn(1819,[mid(p) for p in self.call(buyer,1839,owner,sid,1,100)])
        self.assertEqual(self.gold(buyer),900)
        self.assertEqual(buyer.summons.get(sid)['id'],sid)
        with seller.progression.transaction(write=False) as db:
            self.assertIsNone(db.execute('SELECT id FROM native_guardian_shop_stock WHERE summon_id=?',(sid,)).fetchone())
            self.assertEqual(tuple(db.execute('SELECT seller,buyer,price,proceeds FROM native_guardian_shop_sales '
                'WHERE summon_id=?',(sid,)).fetchone()),(owner,buyer.progression.cid,100,90))
            self.assertEqual(tuple(db.execute('SELECT i.character_id,j.summon_id FROM inventory i '
                'JOIN native_guardian_jade j ON j.item_id=i.id WHERE i.id=?',(iid,)).fetchone()),(owner,None))

    def test_local_group_chains_rewards_into_native_bags(self):
        from native946_constellations import GROUPS, Constellations, MASTER, unlock
        from native946_star_rewards import profile

        game = self.a
        p = game.progression
        self.buff(game)
        with p.transaction() as db:
            for kind in (47554,47605):
                give(db,p,kind)
            for kind in (45097,45098,45099,45100,45101,45102):
                credit(db,p.cid,GROUPS[kind],True)
            unlock(db,p.cid)
        for kind, book in ((47554,0),(47605,47589)):
            iid = self.items(game,kind)[0][0]
            with patch('secrets.choice',side_effect=lambda choices: book or choices[0]):
                reply = self.call(game,807,p.cid,iid)
            self.assertTrue(any(mid(packet)==786 for packet in reply))
            self.assertFalse(self.items(game,kind))
        self.assertEqual(sum(amount for _,amount in self.items(game,47589)),1)
        self.near(game,*MASTER)
        for title in ('20','23'):
            result = Constellations(game).execute(MASTER[1],dict(op='claim_gear',title=title))
            self.assertTrue(result)
            reward = profile(title,game.character['race'])
            self.assertEqual(sum(amount for _,amount in self.items(game,reward['item'])),reward.get('count',1))
            with self.assertRaisesRegex(ValueError,'已领取'):
                Constellations(game).execute(MASTER[1],dict(op='claim_gear',title=title))

    def test_sigil_wrong_scene_does_not_spend_and_summoner_has_first_right(self):
        self.buff(self.a)
        self.buff(self.b)
        kind = 6766
        rule = SIGILS[kind]
        with self.a.progression.transaction() as db:
            give(db,self.a.progression,kind,2)
        iid = self.items(self.a,kind)[0][0]
        self.call(self.a,807,self.a.progression.cid,iid)
        self.assertEqual(self.items(self.a,kind),[(iid,2)])
        with self.a.progression.transaction() as db:
            db.execute('UPDATE progression SET cor=30000,strength=5000,speed=3000 WHERE character_id=?',
                       (self.a.progression.cid,))
        hero = self.a.progression.snapshot()
        self.a.progression.set_vitals(hero['hpmax'],hero['mpmax'])
        self.position(self.a,rule['map'],*spawn_position(rule['map'],Random(1)))
        with patch('native946_constellations.time',return_value=1_800_000_000):
            reply = self.call(self.a,807,self.a.progression.cid,iid)
            self.assertIn(1589,[mid(p) for p in reply])
            self.assertEqual(self.items(self.a,kind),[(iid,1)])
            self.call(self.a,807,self.a.progression.cid,iid)
            self.assertEqual(self.items(self.a,kind),[(iid,1)])
            row = Constellations(self.a).rows(72)[0]
            self.position(self.b,row['scene'],row['x'],row['y'])
            nid = BASE+row['id']
            self.click(self.b,nid,'挑战星符')
            self.assertNotIn(self.b.progression.cid,self.svc.battles)
            self.click(self.a,nid,'挑战星符')
            cid = self.a.progression.cid
            battle = self.svc.battles[cid]
            for _ in range(40):
                if battle.state['phase']=='finished':
                    break
                if battle.state['phase']=='input':
                    if cid not in battle.required():
                        battle.tick(battle.state['deadline']+1)
                    elif blocked(battle.state['fighters'][cid],battle.state['round']):
                        self.call(self.a,2178,1)
                    else:
                        self.call(self.a,2177,3,0)
                if battle.state['phase']=='drawing':
                    self.call(self.a,2184)
            self.assertEqual(Constellations(self.a).rows(72),[])
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(tuple(db.execute('SELECT step,mode FROM native_star_credit WHERE step=54').fetchone()),(54,1))

    def test_high_sigil_gate_and_exclusive_expiry(self):
        kind,nid = 6656,None
        rule = SIGILS[kind]
        self.buff(self.a)
        self.buff(self.b)
        self.position(self.a,rule['map'],*spawn_position(rule['map'],Random(2)))
        cid = self.a.progression.cid
        with self.a.progression.transaction() as db:
            give(db,self.a.progression,kind)
        iid = self.items(self.a,kind)[0][0]
        self.call(self.a,807,cid,iid)
        self.assertEqual(self.items(self.a,kind),[(iid,1)])
        with self.a.progression.transaction() as db:
            for g in self.games:
                credit(db,g.progression.cid,dict(rank=10,step=0),False)
        with patch('native946_constellations.time',return_value=1_800_000_000) as clock:
            self.call(self.a,807,cid,iid)
            row = Constellations(self.a).rows(72)[0]
            nid = BASE+row['id']
            self.position(self.b,row['scene'],row['x'],row['y'])
            self.click(self.b,nid,'挑战星符')
            self.assertNotIn(self.b.progression.cid,self.svc.battles)
            clock.return_value = row['exclusive_until']
            self.click(self.b,nid,'挑战星符')
            self.assertIn(self.b.progression.cid,self.svc.battles)
            self.svc.battles[self.b.progression.cid].finish(False)
            clock.return_value = row['ends']
            self.assertEqual(Constellations(self.a).rows(72),[])

    def test_title_restores_prior_wins_and_only_master_can_issue_one_reward(self):
        from native946_achievements import claims
        from native946_star_rewards import profile

        self.buff(self.a)
        cid = self.a.progression.cid
        with self.a.progression.transaction() as db:
            for rank in (2,3,4):
                credit(db,cid,dict(rank=rank,step=0),False)
            db.execute('INSERT INTO native_encounter_rewards VALUES(?,?,?)',('star:2008:42',cid,'{}'))
            db.execute('INSERT INTO native_star_claims VALUES(?,?,?)',(cid,1,10000))
        panel = self.call(self.a,3483,cid)
        self.assertIn(b'[2300101]=1',panel[0])
        with self.a.progression.transaction(write=False) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM native_star_import WHERE character_id=?',(cid,)).fetchone())
        scene,npc = 2001,4285
        self.near(self.a,scene,npc)
        listing = self.click(self.a,npc,'查看星宿称谓历程')
        detail = self.click(self.a,npc,'地煞斗士（已达成）',listing)
        self.assertTrue(all(x.get('op')!='claim_gear' for x in self.a.dialog_actions))
        self.assertIn(1025,[mid(p) for p in detail])
        self.assertIn(700,[mid(p) for p in self.call(self.a,746)])
        scene,npc = 2015,4236
        self.near(self.a,scene,npc)
        listing = self.click(self.a,npc,'查看星宿称谓历程')
        detail = self.click(self.a,npc,'地煞斗士（已达成）',listing)
        option = self.label(self.a,npc,'领取称谓装备',look=False,replies=detail)
        old_xp = self.a.progression.snapshot()['experience']
        self.choose(self.a,npc,option)
        self.choose(self.a,npc,option)
        self.assertEqual(self.a.progression.snapshot()['experience'],old_xp)
        self.call(self.a,3482,2300101)
        self.call(self.a,3482,2300101)
        self.assertEqual(self.a.progression.snapshot()['experience'],old_xp+10000)
        race = self.a.character['race']
        reward = profile('1',race)
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(tuple(db.execute('SELECT title,experience FROM native_star_claims WHERE character_id=?',(cid,)).fetchone()),(1,10000))
            self.assertEqual(db.execute('SELECT count(*) FROM native_hero_titles WHERE character_id=? AND name=?',(cid,'地煞斗士')).fetchone()[0],1)
            self.assertEqual(tuple(db.execute('SELECT title,item_type FROM native_star_equipment_claims WHERE character_id=?',(cid,)).fetchone()),(1,reward['item']))
            self.assertIn(2300101,claims(db,cid))
        (iid,_), = self.items(self.a,reward['item'])
        state = self.a.progression.snapshot()
        self.a.progression.move_item(iid,state['equipment_page'],2)
        state = self.a.progression.snapshot()
        self.assertEqual(state['equipment'][iid]['properties'],reward['properties'])
        self.assertEqual(state['equipment'][iid]['rule_version'],'946-star-title-v1')
        self.assertGreaterEqual(state['mpmax'],500)

    def test_title_bag_full_keeps_reward_pending_and_requires_wear_level(self):
        from native946_equipment import validate_wear
        from native946_mall_bags import pages
        from native946_star_rewards import profile

        cid = self.a.progression.cid
        with self.a.progression.transaction() as db:
            for rank in (1,2,3,4):
                credit(db,cid,dict(rank=rank,step=0),False)
            occupied = {tuple(row) for row in db.execute('SELECT page,slot FROM inventory WHERE character_id=?',(cid,))}
            for page in pages(db,cid):
                for slot in range(1,25):
                    if (page,slot) not in occupied:
                        last = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,?,?,1)',
                                          (cid,page,slot)).lastrowid
        scene,npc = 2015,4236
        self.near(self.a,scene,npc)
        def claim():
            listing = self.click(self.a,npc,'查看星宿称谓历程')
            detail = self.click(self.a,npc,'地煞斗士（已达成）',listing)
            return self.click(self.a,npc,'领取称谓装备',detail)
        claim()
        with self.a.progression.transaction() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM native_star_equipment_claims WHERE character_id=?',(cid,)).fetchone())
            db.execute('DELETE FROM inventory WHERE id=?',(last,))
        claim()
        reward = profile('1',self.a.character['race'])
        (iid,_), = self.items(self.a,reward['item'])
        s = self.a.progression.snapshot()
        item = next(i for i in s['items'] if i['id']==iid)
        with self.assertRaisesRegex(ValueError,'等级'):
            validate_wear(s,item,2)
        validate_wear(dict(s,rebirth=1),item,2)
