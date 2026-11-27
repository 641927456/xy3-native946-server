"""Local catalog for the native 946 categorized shop panel."""

from collections import Counter
import json
from pathlib import Path
from time import monotonic

from native946_codec import packet


DATA = json.loads(Path(__file__).with_name('native946_classified_mall_data.json').read_text('utf8'))
# INFO_FESTIVAL_ICON.IconConfig[83] and the native SHIWAN_ITEM_MALL click callback.
ICON = 83
CATEGORIES = {int(category): tuple(items) for category, items in DATA['categories'].items()}
ITEM_CATEGORIES = {kind: category for category, items in CATEGORIES.items() for kind in items}
SALE_ROWS = ('array', 'shiwan_sale_info')
CART_ROWS = ('array', 'number')
CART_LIMIT = 21  # Native shiwan_mall_panel has goods1 through goods21.
REQUESTS = frozenset(range(3406, 3415))
WRITES = frozenset({3413, 3414})
SINGLE_QUOTE = 'classified_mall_single_quote'
CART_QUOTE = 'classified_mall_cart_quote'


class ClassifiedMall:
    def __init__(self, game):
        from native946_cosmetics import Cosmetics

        self.game = game
        self.cosmetics = Cosmetics(game)

    def price(self, kind, amount=1):
        from native946_cosmetics import SALE_GOODS

        product = SALE_GOODS.get(str(kind))
        if kind not in ITEM_CATEGORIES or not product or product.get('price_source') != 'native_yubo':
            raise ValueError('这件商品当前未在分类道具商城上架。')
        return self.cosmetics._quantity(kind, amount)['yubo']

    def rows(self, category):
        from native946_cosmetics import DATA as cosmetics_data, SALE_GOODS

        rows = []
        for kind in CATEGORIES.get(category, ()):
            product = SALE_GOODS.get(str(kind), {})
            if product.get('price_source') != 'native_yubo':
                continue
            price = product['yubo']
            rows.append(dict(type=kind, cls=category, pos=len(rows) + 1, cur_price=price,
                             ori_price=product.get('origin_price') or price,
                             name=cosmetics_data['items'][str(kind)]['name']))
        return rows

    def balance_packet(self, mid=3415):
        with self.game.progression.transaction(write=False) as db:
            yubo, _ = self.cosmetics.wallet(db)
        if mid == 3404:
            return packet(3404, ['number'] * 4 + ['string'],
                          [DATA['default_category'], 100, yubo, 0, '本机商城：按道具说明计算有效期'])
        return packet(3415, ['number', 'number'], [yubo, 0])

    def cart(self):
        return getattr(self.game, 'classified_mall_cart', ())

    def cart_packet(self):
        items = self.cart()
        return packet(3405, [CART_ROWS, 'number'], [items, sum(map(self.price, items))])

    def open(self):
        if self.game.battle is not None:
            raise ValueError('请先结束战斗再使用分类道具商城。')
        self.game.classified_mall_open = True
        self.game.classified_mall_unit_offer = None
        self.game.classified_mall_cart_quote = None
        return (self.balance_packet(3404), self.cart_packet())

    def buy(self, order, quote_slot):
        for kind, amount in order:
            self.price(kind, amount)
        reply = self.cosmetics.buy(order, quote_slot=quote_slot)
        if quote_slot == CART_QUOTE:
            self.game.classified_mall_cart = ()
        else:
            self.game.classified_mall_unit_offer = None
        return (reply, self.balance_packet(), self.cart_packet())

    def receive(self, mid, reader):
        if mid == 3409:
            reader.finish()
            return self.open()
        if self.game.battle is not None:
            raise ValueError('请先结束战斗再使用分类道具商城。')
        if not getattr(self.game, 'classified_mall_open', False):
            raise ValueError('请先打开分类道具商城。')
        if mid == 3411:
            category = reader.integer()
            reader.finish()
            return packet(3416, ['number', SALE_ROWS], [category, self.rows(category)])
        if mid == 3412:
            query = reader.string()
            reader.finish()
            if len(query) > 96:
                raise ValueError('搜索词太长。')
            query = query.decode('gb18030').strip()
            rows = [row for category in CATEGORIES for row in self.rows(category)
                    if query and (query in row['name'] or query == str(row['type']))]
            return packet(3417, [SALE_ROWS], [rows])
        if mid == 3407:
            kind = reader.integer()
            reader.finish()
            price = self.price(kind)
            # The trial panel changes the requested quantity client-side after this quote.
            # Preserve the unit price and consume this quote on the first purchase.
            self.game.classified_mall_unit_offer = (kind, price, monotonic() + 120)
            return packet(3402, ['number', 'number'], [kind, price])
        if mid == 3414:
            kind, amount = reader.integer(), reader.integer()
            reader.finish()
            price = self.price(kind, amount)
            offer = getattr(self.game, 'classified_mall_unit_offer', None)
            if not offer or offer[:2] != (kind, price) or offer[2] <= monotonic():
                raise ValueError('请重新打开商品购买窗口。')
            order = [(kind, amount)]
            self.cosmetics.quote(order, quote_slot=SINGLE_QUOTE)
            return self.buy(order, SINGLE_QUOTE)
        if mid in (3406, 3410):
            value = reader.integer()
            reader.finish()
            items = self.cart()
            if mid == 3406:
                self.price(value)
                if len(items) >= CART_LIMIT:
                    raise ValueError('购物车已满。')
                items = (*items, value)
            else:
                if not 1 <= value <= len(items):
                    raise ValueError('购物车商品位置已变化。')
                items = items[:value - 1] + items[value:]
            self.game.classified_mall_cart = items
            self.game.classified_mall_cart_quote = None
            return self.cart_packet()
        reader.finish()
        order = list(Counter(self.cart()).items())
        if mid == 3408:
            cost, _, _ = self.cosmetics.quote(order, quote_slot=CART_QUOTE)
            return packet(3403, ['number', 'number'], [len(self.cart()), cost])
        return self.buy(order, CART_QUOTE)
