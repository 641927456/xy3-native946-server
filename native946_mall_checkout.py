"""Source-verified passive vouchers and the local shop's Fu Dou policy."""
from time import time

TICKET_TYPES=(48112,58112)
WEDDING_TYPES=(46123,56123)


def fudou_eligible(kind):
    from native946_cosmetics import DATA as CATALOG, SALE_GOODS, sale_type
    key=str(sale_type(kind))
    return key in CATALOG['wearables'] and 0<SALE_GOODS.get(key,{}).get('yubo',0)<=500


def fudou_cost(orders):
    from native946_cosmetics import SALE_GOODS, sale_type
    if not all(fudou_eligible(kind) for kind,_ in orders):
        return 0
    return sum(SALE_GOODS[str(sale_type(kind))]['yubo']*amount for kind,amount in orders)

def rows(db,cid,kinds):
    from native946_mall_bags import pages
    normal=pages(db,cid)
    return list(db.execute('SELECT i.* FROM inventory i LEFT JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.character_id=? AND i.page IN ('+
                           ','.join('?' for _ in normal)+') AND i.type IN ('+','.join('?' for _ in kinds)+') AND (e.expires IS NULL OR e.expires=0 OR e.expires>?) ORDER BY coalesce(e.expires,0),i.id',(cid,*normal,*kinds,int(time()))))

def eligible(kind,catalog,uses):
    key=str(kind);meta=catalog['wearables'].get(key,{})
    if meta.get('kind') in {'GARMENT_HEAD','GARMENT_CLOTHES','GARMENT_SHOULDER','QIZHI','FLYPET','TEAMFLAG','SAYFRAME'}:return True
    if key in catalog['sets']:return all(eligible(i,catalog,uses) for i in catalog['sets'][key])
    rule=uses['rules'].get(key,{})
    return rule.get('op')=='mount' or key in uses.get('child_halos',{})

def plan(db,cid,orders,ticket):
    from native946_cosmetics import DATA as CATALOG, SALE_GOODS, sale_type
    from native946_mall_uses import DATA as USES
    if ticket not in (0,*TICKET_TYPES):raise ValueError('当前折扣券类型不受支持。')
    orders = [(sale_type(kind), amount) for kind, amount in orders]
    gross=sum(SALE_GOODS[str(k)]['yubo']*n for k,n in orders)
    eligible_cost=sum(SALE_GOODS[str(k)]['yubo']*n for k,n in orders if eligible(k,CATALOG,USES))
    tickets=rows(db,cid,TICKET_TYPES);count=min(eligible_cost//300,sum(r['amount'] for r in tickets)) if ticket else 0
    if ticket and not count:raise ValueError('没有有效的外观抵用券，或适用商品总额不足300玉帛。')
    wedding=rows(db,cid,WEDDING_TYPES)
    wedding_count=min(sum(n for k,n in orders if k in (55146,55167,65146,65167)),sum(r['amount'] for r in wedding))
    discount=count*100+min(sum(SALE_GOODS[str(k)]['yubo']*n for k,n in orders if k in (55146,55167,65146,65167)),wedding_count*300)
    return max(0,gross-discount),[(tickets,count),(wedding,wedding_count)]

def consume(db,groups):
    from native946_mall_uses import consume as consume_item
    for entries,count in groups:
        for row in entries:
            take=min(count,row['amount'])
            if take:consume_item(db,row,take);count-=take
            if not count:break
