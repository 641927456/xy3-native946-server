"""Native temporary inventory pages and permanent bag/guardian expansion."""
from time import time,monotonic
from native946_codec import packet
from native946_codec import text_notice

def pages(db,cid,withdraw=False):
    result=[8]
    row=db.execute("SELECT expires FROM cosmetic_features WHERE character_id=? AND key='bag_temp'",(cid,)).fetchone()
    if row and (withdraw or not row[0] or row[0]>time()):result.append(15)
    row=db.execute("SELECT value FROM cosmetic_features WHERE character_id=? AND key='bag_item'",(cid,)).fetchone()
    result.extend(range(16,16+min(3,row[0] if row else 0)))
    return tuple(result)

def summon_limit(db,cid):
    row=db.execute("SELECT value FROM cosmetic_features WHERE character_id=? AND key='bag_summon'",(cid,)).fetchone()
    return 6+min(3,row[0] if row else 0)

class Bags:
    def __init__(self,game):self.g=game;self.p=game.progression
    def sync(self):
        with self.p.transaction(write=False) as db:
            temp=db.execute("SELECT expires FROM cosmetic_features WHERE character_id=? AND key='bag_temp'",(self.p.cid,)).fetchone()
            row=db.execute("SELECT value FROM cosmetic_features WHERE character_id=? AND key='bag_item'",(self.p.cid,)).fetchone()
            count=row[0] if row else 0;guardians=summon_limit(db,self.p.cid)-6
        expiry=temp[0] if temp else 0;status=0 if not temp else 1 if not expiry or expiry>time() else 2
        return tuple(packet(695,['number']*2,[attr,value]) for attr,value in [(46,status),(47,expiry),(79,count),(85,guardians)])+(packet(694,[],[]),)
    def choose(self,item_id):
        self.g.dialog_actions=[dict(kind='mall_use',op='bag_expand',target=target,item_id=item_id) for target in (1,3)]
        return self.g.menu('请选择永久百纳袋的用途。每种最多扩充三次，确认成功后消耗一个百纳袋。',
                           ['扩充一页物品栏','扩充一个守护携带栏'],self.p.cid)
    def open(self,target,page=0,line=0,item_id=0):
        if target not in (1,3):raise ValueError('当前百纳袋用于扩充物品栏或守护携带栏。')
        with self.p.transaction(write=False) as db:
            key='bag_item' if target==1 else 'bag_summon'
            row=db.execute('SELECT value FROM cosmetic_features WHERE character_id=? AND key=?',(self.p.cid,key)).fetchone();count=row[0] if row else 0
        if count>=3:raise ValueError('此类百纳袋已达到原生说明的三次扩容上限。')
        if target==1 and page not in (0,count+1):raise ValueError('扩容页已变化，请重新打开。')
        if target==3 and (page or line):raise ValueError('守护栏扩容参数无效。')
        self.g.cosmetic_extension=dict(target=target,page=page,line=line,item_id=item_id,until=monotonic()+180)
        return packet(4040,['number']*3+[('array','number'),'number'],[target,page,line,[55655,65655],1])
    def confirm(self,target,page,line):
        s=getattr(self.g,'cosmetic_extension',None)
        if not s or (target,page,line)!=(s['target'],s['page'],s['line']) or s['until']<monotonic():raise ValueError('扩容会话已失效，请重新打开。')
        from native946_mall_uses import consume
        with self.p.transaction() as db:
            normal=pages(db,self.p.cid)
            row=db.execute('SELECT i.*,coalesce(e.expires,0) expires FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.character_id=? AND i.page IN ('+','.join('?' for _ in normal)+') AND i.type IN (55655,65655) AND (?=0 OR i.id=?) ORDER BY i.id LIMIT 1',(self.p.cid,*normal,s['item_id'],s['item_id'])).fetchone()
            if row is None or row['expires'] and row['expires']<=time():raise ValueError('背包中没有有效的永久百纳袋。')
            key='bag_item' if target==1 else 'bag_summon';current=db.execute('SELECT value FROM cosmetic_features WHERE character_id=? AND key=?',(self.p.cid,key)).fetchone()
            count=current[0] if current else 0
            if count>=3:raise ValueError('扩容已达到上限。')
            consume(db,row)
            db.execute('INSERT INTO cosmetic_features VALUES(?,?,?,0) ON CONFLICT(character_id,key) DO UPDATE SET value=excluded.value',(self.p.cid,key,count+1))
        self.g.cosmetic_extension=None
        return self.g.defer_packets(lambda:(*self.p.item_packets(),*self.sync(),*self.g.summons.sync(),text_notice('百纳袋扩容成功。')))
