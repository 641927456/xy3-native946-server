import unittest
from unittest.mock import patch

from native946_guardian_jade import equipped_jades, stones
from native946_neidan import installed
from native946_role_systems import save
from test_native946_skillbooks import BasicBookTests


def message_ids(result):
    packets=(result,) if isinstance(result,bytes) else result
    return [int.from_bytes(packet[1:3],'little') for packet in packets]


class GuardianCultivationTests(unittest.TestCase):
    setUp=BasicBookTests.setUp
    item=BasicBookTests.item
    send=BasicBookTests.send

    def test_new_neidan_install_advance_refresh_settle_and_return(self):
        before=self.s.get(self.sid)
        item=self.item(91001,2)
        self.assertIn(716,message_ids(self.send(807,self.sid,item)))
        self.assertGreater(self.s.get(self.sid)['mpmax'],before['mpmax'])
        self.assertIn(5224,message_ids(self.send(5228,self.sid,236)))
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET experience=? WHERE id=?',(800000000,self.sid))
        self.assertIn(5224,message_ids(self.send(5227,self.sid,236)))
        with self.p.transaction(write=False) as db:
            self.assertEqual(installed(db,self.sid)[0]['advance'],2)
        self.assertEqual(self.s.get(self.sid)['experience'],0)
        self.s.settle(self.sid,'new-pill-test',100,30,True)
        self.s.settle(self.sid,'new-pill-test',100,30,True)
        with self.p.transaction(write=False) as db:
            self.assertEqual(installed(db,self.sid)[0]['energy'],5995)
        energy=self.item(6128,1)
        self.assertIn(716,message_ids(self.send(807,self.sid,energy)))
        with self.p.transaction(write=False) as db:
            self.assertEqual(installed(db,self.sid)[0]['energy'],6000)
        self.assertIn(716,message_ids(self.send(755,self.sid,236)))
        with self.p.transaction(write=False) as db:
            self.assertEqual(installed(db,self.sid),[])
            self.assertEqual(db.execute('SELECT sum(amount) FROM inventory WHERE character_id=? AND type=91001',(self.p.cid,)).fetchone()[0],1)

    def test_pill_bag_and_installed_skill_names_follow_separate_946_tables(self):
        from native946_login import MessageReader
        from test_native946_skillbooks import decode_record

        with self.p.transaction() as db:
            db.execute('UPDATE summons SET ascension=1 WHERE id=?',(self.sid,))
        for item,kind,bag_name,skill_name in ((91009,244,'低级内丹·定神思','守灵台'),
                                             (91010,245,'低级内丹·守灵台','定神思'),
                                             (91508,283,'高级内丹·潜形匿迹','潜龙在渊')):
            with self.subTest(item=item):
                iid=self.item(item,1)
                self.assertTrue(any(bag_name.encode('gb18030') in msg for msg in self.p.item_packets(item_ids={iid})))
                replies=self.send(807,self.sid,iid)
                reader=MessageReader(next(msg[3:] for msg in replies if msg[1:3]==(716).to_bytes(2,'little')))
                self.assertEqual(reader.integer(),self.sid)
                self.assertEqual(reader.take(1),b'\x01')
                record=decode_record(reader,'neidan_info')
                reader.finish()
                self.assertEqual((record['neidan_id'],record['name']),(kind,skill_name))
                self.assertIn(716,message_ids(self.send(755,self.sid,kind)))

    def test_new_neidan_stops_at_corrected_caps_and_uses_them_for_stage_changes(self):
        from native946_neidan import grow, threshold

        self.send(807, self.sid, self.item(91001, 1))
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET rebirth=2,ascension=1 WHERE id=?', (self.sid,))
        for stage, cap in enumerate((100, 120, 140, 180)):
            with self.subTest(stage=stage):
                self.assertEqual(grow(dict(stage=stage, level=cap-1, experience=0),
                                      threshold(cap-1, stage)+1)['level'], cap)
                with self.p.transaction() as db:
                    db.execute('UPDATE native_new_neidan SET stage=?,level=? WHERE summon_id=?',
                               (stage, cap-1, self.sid))
                request = 767 if stage < 2 else 766
                if stage < 3:
                    self.send(request, self.sid, 236)
                    self.assertIn('阶段', self.g.last_event['reason'])
                with self.p.transaction() as db:
                    db.execute('UPDATE native_new_neidan SET level=? WHERE summon_id=?', (cap, self.sid))
                self.send(753, self.sid, 236)
                self.assertIn('上限', self.g.last_event['reason'])
                if stage < 3:
                    self.assertIn(716, message_ids(self.send(request, self.sid, 236)))
                    with self.p.transaction(write=False) as db:
                        row = installed(db, self.sid)[0]
                    self.assertEqual((row['stage'], row['level'], row['experience']), (stage+1, 1, 0))

    def test_resonance_preview_and_confirmation_charge_once(self):
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET ascension=1,experience=800000000 WHERE id=?',(self.sid,))
        material=self.item(91506,2)
        self.send(807,self.sid,material)
        self.send(807,self.sid,self.item(91001,1))
        with self.p.transaction() as db:
            db.execute('UPDATE native_new_neidan SET advance=3 WHERE summon_id=? AND kind=281',(self.sid,))
        with patch('native946_neidan.choice',return_value=236):
            self.assertIn(5225,message_ids(self.send(5226,self.sid,281,1,0)))
        self.send(5226,self.sid,281,1,0)
        self.assertIn('确认或放弃',self.g.last_event['reason'])
        self.assertEqual(self.s.get(self.sid)['experience'],0)
        self.assertIn(5225,message_ids(self.send(5226,self.sid,281,1,1)))
        from native946_login import MessageReader
        preview=MessageReader(self.send(5228,self.sid,236)[3:])
        for _ in range(6):preview.integer()
        self.assertEqual(preview.string(),b'{18,18,1,1}')
        self.assertEqual(preview.string(),b'{27,18,2,1}')
        preview.finish()
        with self.p.transaction(write=False) as db:
            high=next(row for row in installed(db,self.sid) if row['kind']==281)
            self.assertNotIn('pending',high['resonance'])

    def test_jade_forge_inlay_upgrade_and_remove(self):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=120,gold=1000000 WHERE id=?',(self.p.cid,))
            db.execute('UPDATE cultivation_state SET sanctification=1 WHERE character_id=?',(self.p.cid,))
        self.item(47655,1)
        self.item(47664,1)
        self.item(47663,2)
        self.assertIn(3981,message_ids(self.send(3946,1)))
        iid=next(item['id'] for item in self.p.snapshot()['items'] if item['type']==47659)
        before=self.s.get(self.sid)['score']
        self.assertIn(3944,message_ids(self.send(3947,self.sid,1,iid)))
        from native946_login import MessageReader
        reader=MessageReader(self.send(3978,self.sid)[3:])
        self.assertEqual(reader.integer(),self.sid)
        self.assertEqual(reader.take(1),b'\1')
        self.assertEqual(reader.integer(),1)
        reader.finish()
        self.assertGreater(self.s.get(self.sid)['score'],before)
        for _ in range(2):self.assertIn(3944,message_ids(self.send(3958,self.sid,1,1)))
        with self.p.transaction(write=False) as db:
            self.assertEqual(equipped_jades(db,self.sid)[0]['level'],2)
        self.assertIn(3944,message_ids(self.send(3951,self.sid,1)))
        self.assertEqual(self.s.get(self.sid)['score'],before)

    def test_old_stones_unlock_nine_stars_and_apply_bonuses(self):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET gold=1000000 WHERE id=?',(self.p.cid,))
            save(db,self.p.cid,'tianji',{'active':[103]})
        ordinary=self.item(1058,10)
        star=self.item(42354,1)
        self.send(950,self.sid,star,1)
        self.assertIn('开孔条件',self.g.last_event['reason'])
        for slot in range(1,11):
            self.assertIn(3040,message_ids(self.send(3042,self.sid,ordinary,slot)))
        defense=self.s.get(self.sid)['defense']
        self.assertIn(948,message_ids(self.send(950,self.sid,star,1)))
        self.assertEqual(self.s.get(self.sid)['defense'],defense+120)
        with self.p.transaction(write=False) as db:
            self.assertEqual(len(stones(db,self.sid,0)),10)
            self.assertEqual(stones(db,self.sid,1),{1:42354})

    def test_local_combat_jade_debuff_and_elemental_penetration(self):
        from native946_role_battle import Runtime
        from test_native946_innate_effects import AlwaysProc, fighter

        pet=fighter(1,4,(4628,))
        pet.update(mag=694,jade_skills={13:600,15:200})
        pet['innate_skills'][0].update(grade=180,fly=1)
        target=fighter(2,3)
        target['ImmAbsorbDegree']=300
        hero=fighter(3,4)
        hero['kind']='hero'
        runtime=Runtime({actor['id']:actor for actor in (pet,target,hero)}, {}, 1, AlwaysProc(), {})
        self.assertEqual(runtime.owner_jade_bonus(hero,13),416)
        self.assertEqual(runtime.damage(pet,target,1000,'fire'),721)
        for _ in range(4):
            runtime.apply_jade_spell_debuff(pet,[target])
        self.assertEqual(target['role_heal_reduce'],300)
        self.assertEqual(runtime.heal(target,target,1000),700)
        pet['hp']=0
        self.assertEqual(runtime.owner_jade_bonus(hero,13),0)

    def test_zhongling_stays_within_water_fire_panel_cap(self):
        from native946_innate_effects import ACTIVE, target_count
        from test_native946_innate_effects import fighter

        pet=fighter(1,4,(4539,4762))
        pet['mpmax']=55785
        self.assertEqual(target_count(pet,4539,ACTIVE[4539]),5)
        pet['mpmax']=30000
        self.assertEqual(target_count(pet,4539,ACTIVE[4539]),5)
        pet['innate_skills'].pop()
        self.assertEqual(target_count(pet,4539,ACTIVE[4539]),4)

    def test_selected_tai_sui_uses_native_description_arguments(self):
        from native946_innate_training import schema
        from native946_login import MessageReader
        from test_native946_skillbooks import decode_record

        sid=self.s.acquire(3374,'cultivation:description')
        with self.p.transaction() as db:
            schema(db)
            db.execute('UPDATE summons SET strength=1429,level=180,cor=1,rebirth=2,ascension=1 WHERE id=?',(sid,))
            db.execute('INSERT INTO native_innate_training '
                       '(summon_id,skill,level,rebirth,ascension,sanctification,experience) VALUES(?,?,?,?,?,?,?)',
                       (sid,4906,180,2,1,0,0))
        reply=self.send(761,sid)
        reader=MessageReader(reply[3:])
        self.assertEqual(reader.integer(),sid)
        self.assertEqual(reader.take(1),b'\1')
        skill=decode_record(reader,'stunt_info')
        self.assertEqual(skill['stunt_id'],4906)
        self.assertEqual(skill['desc'],'{152.88,7281,7281,7281}')
        self.assertEqual(skill['cost'],'{100}')

    def test_equipped_jade_blocks_guardian_sale_until_removed(self):
        from native946_guardian_chamber import validate_stock

        sid=self.s.acquire(3268,'cultivation:jade-sale')
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=120,gold=1000000 WHERE id=?',(self.p.cid,))
            db.execute('UPDATE cultivation_state SET sanctification=1 WHERE character_id=?',(self.p.cid,))
        self.item(47655,1)
        self.item(47664,1)
        self.send(3946,1)
        iid=next(item['id'] for item in self.p.snapshot()['items'] if item['type']==47659)
        self.send(3947,sid,1,iid)
        with self.p.transaction(write=False) as db:
            with self.assertRaisesRegex(ValueError,'卸下.*宝玉'):
                validate_stock(db,self.p.cid,self.s._owned(db,sid))
        self.send(3951,sid,1)
        with self.p.transaction(write=False) as db:
            validate_stock(db,self.p.cid,self.s._owned(db,sid))
        self.assertIn(iid,[item['id'] for item in self.p.snapshot()['items']])

    def test_spell_piercing_book_syncs_and_teaches_with_native_item(self):
        sid=self.s.acquire(3268,'cultivation:pierce-book')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=102,ascension=1 WHERE id=?',(sid,))
        iid=self.item(47591,2)
        self.assertTrue(self.p.item_packets())
        self.assertIn(722,message_ids(self.send(807,sid,iid)))
        self.assertIn(4628,[entry['stunt_id'] for entry in self.s.get(sid)['innate_skills']])
        self.send(807,sid,iid)
        self.assertIn('已拥有',self.g.last_event['reason'])
        self.assertEqual(next(item['amount'] for item in self.p.snapshot()['items'] if item['id']==iid),1)

    def test_guangze_can_learn_echo_and_dragon_war(self):
        sid=self.s.acquire(3352,'cultivation:guangze')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=120,rebirth=2,ascension=1 WHERE id=?',(sid,))
        for skill in (4618,4619):
            self.assertIn(722,message_ids(self.send(763,sid,skill)))
        entries={row['stunt_id']:row for row in self.s.get(sid)['innate_skills']}
        self.assertTrue({4618,4619}<=entries.keys())

    def test_echo_charges_once_and_dragon_can_revive_once(self):
        from native946_innate_effects import cast
        from native946_role_battle import Runtime
        from test_native946_innate_effects import AlwaysProc, fighter

        pet=fighter(1,4,(346,4618,4619))
        for entry in pet['innate_skills']:
            entry.update(grade=180,fly=1)
        target=fighter(2,3)
        runtime=Runtime({1:pet,2:target},{},1,AlwaysProc(),{})
        mana=pet['mp']
        with patch.object(runtime,'damage',wraps=runtime.damage) as damage:
            self.assertTrue(cast(runtime,self.g,pet,2,346))
        self.assertEqual([call.args[2] for call in damage.call_args_list],[1200,1200,720,720])
        self.assertEqual(pet['mp'],mana-120)
        for number in range(1,6):
            runtime.round=number
            runtime.begin_round()
        self.assertEqual(pet['role_dragon_rounds'],4)
        self.assertEqual(runtime.damage(pet,target,1000,'fire'),1600)
        runtime.damage(target,pet,100000,'physical')
        self.assertEqual(pet['hp'],pet['hpmax'])
        self.assertEqual(pet['role_dragon_rounds'],4)
        runtime.damage(target,pet,100000,'physical')
        self.assertEqual(pet['hp'],0)

    def test_bell_followup_requires_an_active_kill(self):
        from native946_role_battle import Runtime
        from test_native946_innate_effects import AlwaysProc, fighter

        pet=fighter(1,4,(4908,))
        dying=fighter(2,3)
        dying.update(hp=10,role_dots=[dict(kind='poison',source=1,skill=4615,damage=100,until=3)])
        target=fighter(3,3)
        other=fighter(4,3)
        runtime=Runtime({1:pet,2:dying,3:target,4:other},{},2,AlwaysProc(),{})
        with patch.object(runtime,'attack',wraps=runtime.attack) as attacks:
            runtime.begin_round()
            self.assertEqual(dying['hp'],0)
            attacks.assert_not_called()
            runtime.damage(pet,target,100000,'physical')
            self.assertEqual(attacks.call_count,1)
            self.assertIs(attacks.call_args.args[1],other)

    def test_jing_book_and_special_artifact_use_real_item_requests(self):
        from native946_innate_training import schema

        sid=self.s.acquire(3268,'cultivation:jing')
        with self.p.transaction() as db:
            schema(db)
            db.execute('UPDATE summons SET rebirth=2,ascension=1,level=150 WHERE id=?',(sid,))
            db.execute('INSERT INTO native_innate_training(summon_id,skill,level,experience,rebirth,ascension,sanctification) '
                       'VALUES(?,302,183,15,2,1,1)',(sid,))
        self.assertIn(722,message_ids(self.send(807,sid,self.item(46585,1))))
        row, = self.s.get(sid)['innate_skills']
        self.assertEqual((row['stunt_id'],row['fly'],row['feng'],row['grade']),(4147,1,0,150))
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET sanctification=1,level=185 WHERE id=?',(sid,))
        row, = self.s.get(sid)['innate_skills']
        self.assertEqual((row['stunt_id'],row['fly'],row['feng'],row['grade'],row['stuntexp']),(4147,1,1,183,15))
        self.send(756,sid,4147)
        self.assertIn(302,[row['stunt_id'] for row in self.s.get(sid)['innate_skills']])
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=140,sanctification=1 WHERE id=?',(self.sid,))
        ordinary=[self.item(kind,1) for kind in (42030,42031,42032,42036)]
        special=self.item(42041,1)
        self.send(807,self.sid,special)
        self.assertIn('万象浊灵',self.g.last_event['reason'])
        self.assertIn(706,message_ids(self.send(807,self.sid,self.item(46518,1))))
        for iid in [*ordinary,special]:self.assertIn(706,message_ids(self.send(807,self.sid,iid)))
        pet=self.s.get(self.sid)
        self.assertEqual(pet['guardian_4046_bonus'],110)
        self.assertEqual(pet['guardian_4539_bonus'],770)
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_guardian_equipment WHERE summon_id=?',(self.sid,)).fetchone()[0],5)
