"""Native permanent food points and ginseng-fruit redistribution."""
from datetime import datetime,timezone,timedelta
from secrets import SystemRandom
from native946_codec import packet
from native946_codec import text_notice
FOODS=(46891,47001,47006,46985,46997)
FRUITS={46892:'cor',46893:'speed',46894:'mag',46895:'strength'}
ITEM_TYPES=set(FOODS)|set(FRUITS);NAMES=('cor','mag','strength','speed');RNG=SystemRandom()

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_hero_extra_attributes (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      cor INTEGER NOT NULL DEFAULT 0 CHECK(cor>=0),mag INTEGER NOT NULL DEFAULT 0 CHECK(mag>=0),
      strength INTEGER NOT NULL DEFAULT 0 CHECK(strength>=0),speed INTEGER NOT NULL DEFAULT 0 CHECK(speed>=0))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_hero_food_uses (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      year INTEGER NOT NULL,kind INTEGER NOT NULL,uses INTEGER NOT NULL CHECK(uses BETWEEN 0 AND 3),
      PRIMARY KEY(character_id,year,kind))''')

def year():return datetime.now(timezone(timedelta(hours=8))).year

def values(db,cid):
    row=db.execute('SELECT * FROM native_hero_extra_attributes WHERE character_id=?',(cid,)).fetchone()
    return {key:row[key] if row else 0 for key in NAMES}

def counters(db,cid):
    counts={r['kind']:r['uses'] for r in db.execute('SELECT kind,uses FROM native_hero_food_uses WHERE character_id=? AND year=?',(cid,year()))}
    return [counts.get(kind,0) for kind in FOODS]

class ExtraPoints:
    def __init__(self,game):self.g=game;self.p=game.progression
    def use(self,iid):
        from native946_inventory_features import owned,consume
        with self.p.transaction() as db:
            item=owned(db,self.p.cid,iid);kind=item['type'];before=self.p._snapshot(db)
            if kind not in ITEM_TYPES:raise ValueError('该道具不能改变永久附加属性。')
            db.execute('INSERT OR IGNORE INTO native_hero_extra_attributes(character_id) VALUES(?)',(self.p.cid,))
            current=values(db,self.p.cid)
            if kind in FOODS:
                count=counters(db,self.p.cid)[FOODS.index(kind)]
                if count>=3:raise ValueError('本年度该种精品食物已使用3个。')
                target=RNG.choice(NAMES)
                db.execute('UPDATE native_hero_extra_attributes SET '+target+'='+target+'+1 WHERE character_id=?',(self.p.cid,))
                db.execute('INSERT INTO native_hero_food_uses VALUES(?,?,?,?) ON CONFLICT(character_id,year,kind) DO UPDATE SET uses=excluded.uses',(self.p.cid,year(),kind,count+1))
            else:
                source=FRUITS[kind]
                if current[source]<1:raise ValueError('该项永久附加属性点不足，不能使用对应人参果。')
                target=RNG.choice([name for name in NAMES if name!=source])
                db.execute('UPDATE native_hero_extra_attributes SET '+source+'='+source+'-1,'+target+'='+target+'+1 WHERE character_id=?',(self.p.cid,))
            consume(db,self.p.cid,iid)
            after=self.p._snapshot(db)
            # Fruit may remove a point used to meet an equipment requirement.
            for item in after['items']:
                if item['page']==after['equipment_page']:self.p.validate_equipment(after,item,item['slot'],item['page'])
            db.execute('UPDATE progression SET hp=?,mp=? WHERE character_id=?',(min(before['hp'],after['hpmax']),min(before['mp'],after['mpmax']),self.p.cid))
        self.g.last_event=dict(action='hero_extra_point_changed',item_type=kind,attribute=target)
        from native946_hero_page import HeroPage
        return self.g.defer_packets(lambda:(*self.p.sync(),HeroPage(self.g).additive(1),text_notice('永久附加属性已更新。')))
