"""Owned item/guardian locks through the stock masked NPC input dialog."""
from hashlib import pbkdf2_hmac
from hmac import compare_digest
import secrets
from time import time,monotonic
from native946_codec import packet
from native946_codec import text_notice

DELAY=10*86400

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_object_locks (
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('item','summon')),object_id INTEGER NOT NULL,
      flag INTEGER NOT NULL CHECK(flag IN (1,2)),release_at INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(kind,object_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_inventory_security (
      owner TEXT PRIMARY KEY REFERENCES accounts(username),salt BLOB NOT NULL,digest BLOB NOT NULL,
      reset_at INTEGER NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS native_item_lock_cleanup AFTER DELETE ON inventory
      BEGIN DELETE FROM native_object_locks WHERE kind='item' AND object_id=OLD.id; END''')

def records(db,cid,kind='item'):
    return {r['object_id']:dict(lock=r['flag'],lock_block_time=r['release_at']) for r in
            db.execute('SELECT object_id,flag,release_at FROM native_object_locks WHERE character_id=? AND kind=?',(cid,kind))}

def require_unlocked(db,cid,iid):
    if db.execute("SELECT 1 FROM native_object_locks WHERE character_id=? AND kind='item' AND object_id=?",(cid,iid)).fetchone():
        raise ValueError('该物品已经加锁，请先在物品加锁面板解锁。')

def digest(code,salt):return pbkdf2_hmac('sha256',code.encode('ascii'),salt,120000)

class InventoryLocks:
    def __init__(self,game):self.g=game;self.p=game.progression
    def target(self,db,iid):
        from native946_inventory import EQUIP_PAGES
        from native946_mall_bags import pages
        item=db.execute('SELECT page FROM inventory WHERE character_id=? AND id=?',(self.p.cid,iid)).fetchone()
        if item and item['page'] in (*EQUIP_PAGES,*pages(db,self.p.cid,True),*range(25,30),*range(5800,5816),*range(7000,7004),*range(7100,7104),*range(1000,1050)):return 'item'
        from native946_guardian_chamber import AVAILABLE
        if db.execute('SELECT 1 FROM summons WHERE character_id=? AND id=? AND '+AVAILABLE,(self.p.cid,iid)).fetchone():return 'summon'
        raise ValueError('该物品或守护不属于当前角色，或已经移出物品栏。')
    def open(self,operation,iid=0):
        if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
        if operation not in (1,2,3,4):raise ValueError('物品加锁操作无效。')
        with self.p.transaction(write=False) as db:
            kind=self.target(db,iid) if operation!=4 else None
            row=db.execute('SELECT * FROM native_inventory_security WHERE owner=?',(self.p.owner,)).fetchone()
            reset=bool(row and row['reset_at'] and row['reset_at']<=time())
            creating=row is None or reset
        session=dict(id=secrets.randbelow(1<<29)+100000,operation=operation,object_id=iid,kind=kind,creating=creating,
                     until=monotonic()+180,scene=self.g.character['map_id'])
        self.g.inventory_lock_session=session
        labels=['设置加锁码（6至12位数字）','再次输入加锁码'] if creating else ['请输入物品加锁码']
        title='首次使用请设置本机账号物品加锁码。' if creating else '请验证物品加锁码后继续。'
        return packet(1023,[('array','npc_chat'),('array','string'),('array','number'),'number','number'],
                      [[dict(chat_msg=title,speaker=0)],labels,[1]*len(labels),session['id'],0])
    def reset(self):
        if self.g.battle is not None:raise ValueError('请先完成当前战斗。')
        with self.p.transaction() as db:
            row=db.execute('SELECT reset_at FROM native_inventory_security WHERE owner=?',(self.p.owner,)).fetchone()
            if not row:raise ValueError('尚未设置物品加锁码。')
            when=row['reset_at'] or int(time())+DELAY
            db.execute('UPDATE native_inventory_security SET reset_at=? WHERE owner=?',(when,self.p.owner))
        return text_notice('已申请清除加锁码，十天后可在物品加锁面板重新设置。')
    def matches(self,reader):
        from native946_login import MessageReader
        s=getattr(self.g,'inventory_lock_session',None)
        if not s:return False
        try:
            r=MessageReader(reader.data[reader.offset:]);count=r.take(1)[0]
            if count>2:return False
            for _ in range(count):r.string()
            return r.integer()==s['id']
        except ValueError:return False
    def input(self,r):
        count=r.take(1)[0]
        if count not in (0,1,2):raise ValueError('加锁码输入数量无效。')
        raw=[r.string() for _ in range(count)];session=r.integer();r.finish()
        s=getattr(self.g,'inventory_lock_session',None)
        if not s or session!=s['id'] or monotonic()>s['until'] or s['scene']!=self.g.character['map_id']:raise ValueError('物品加锁会话已失效，请重新打开。')
        if not count:
            self.g.inventory_lock_session=None
            return packet(1183,['string'],['dlg_npc_chat'])
        if count!=(2 if s['creating'] else 1) or any(not 6<=len(v)<=12 or not v.isdigit() or not v.isascii() for v in raw):
            raise ValueError('加锁码必须为6至12位数字。')
        values=[v.decode('ascii') for v in raw]
        if s['creating'] and values[0]!=values[1]:raise ValueError('两次输入的加锁码不一致。')
        failures,until=getattr(self.g,'_inventory_lock_failures',(0,0))
        if until>monotonic():raise ValueError('加锁码连续输入错误，请一分钟后重试。')
        with self.p.transaction() as db:
            row=db.execute('SELECT * FROM native_inventory_security WHERE owner=?',(self.p.owner,)).fetchone()
            creating=row is None or bool(row['reset_at'] and row['reset_at']<=time())
            if creating!=s['creating']:raise ValueError('加锁码状态已经变化，请重新打开。')
            if creating:
                salt=secrets.token_bytes(16)
                db.execute('INSERT INTO native_inventory_security VALUES(?,?,?,0) ON CONFLICT(owner) DO UPDATE SET salt=excluded.salt,digest=excluded.digest,reset_at=0',
                           (self.p.owner,salt,digest(values[0],salt)))
            elif not compare_digest(digest(values[0],row['salt']),row['digest']):
                failures=failures+1 if until==0 else 1
                self.g._inventory_lock_failures=(failures,monotonic()+60 if failures>=5 else 0)
                raise ValueError('物品加锁码不正确。')
            self.g._inventory_lock_failures=(0,0)
            if s['operation']==4:
                message='高级操作加锁码验证成功。';self.g._inventory_advanced_until=monotonic()+300
            else:
                kind=self.target(db,s['object_id'])
                if kind!=s['kind']:raise ValueError('加锁对象已经变化。')
                old=db.execute('SELECT flag,release_at FROM native_object_locks WHERE kind=? AND object_id=?',(kind,s['object_id'])).fetchone()
                if s['operation']==2:
                    if old and old['flag']==2 and (not old['release_at'] or old['release_at']>time()):
                        when=old['release_at'] or int(time())+DELAY
                        db.execute('UPDATE native_object_locks SET release_at=? WHERE kind=? AND object_id=?',(when,kind,s['object_id']))
                        message='时间交易锁已经申请解除，十天等待期结束后可再次验证解锁。'
                    else:
                        db.execute('DELETE FROM native_object_locks WHERE kind=? AND object_id=?',(kind,s['object_id']));message='解锁成功。'
                else:
                    flag=2 if s['operation']==3 else 1
                    if old and old['flag']==2 and flag==1:raise ValueError('时间交易锁不能改为普通锁，请先申请解锁。')
                    db.execute('INSERT INTO native_object_locks VALUES(?,?,?,?,0) ON CONFLICT(kind,object_id) DO UPDATE SET flag=excluded.flag',
                               (self.p.cid,kind,s['object_id'],flag))
                    message='时间交易锁设置成功。' if flag==2 else '加锁成功。'
        self.g.inventory_lock_session=None
        def reply():
            state=self.p.snapshot();row=next((i for i in state['items'] if i['id']==s['object_id']),None)
            flag=(row or {}).get('lock',0)
            extra=(packet(881,['number','number'],[flag,s['object_id']]),) if s['kind']=='item' else ()
            return (packet(1183,['string'],['dlg_npc_chat']),*self.p.item_packets(state),*self.g.summons.sync(),
                    *extra,
                    packet(1183,['string'],['dlg_lock_panel']),packet(3993,['string'],['dlg_lock_panel']),text_notice(message))
        return self.g.defer_packets(reply)
