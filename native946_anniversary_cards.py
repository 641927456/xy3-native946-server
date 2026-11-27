"""Original 946 collection and reward panels for the anniversary cards."""

import json
from pathlib import Path

from native946_codec import packet, text_notice
from native946_guardian_channels import EVENT, EVENT_ROWS, GuardianChannels


# COMMON_CONST.FESTIVAL_ICON.ANNIVERSARY2017 and INFO_FESTIVAL_ICON.IconConfig.
ICON = 72
REQUESTS = frozenset({3155, 3156, 3158, 3159, 3161})
SCORE_TYPES = frozenset({1, 2, 3})
ITEMS = json.loads(Path(__file__).with_name('native946_anniversary_cards_items.json').read_text('utf8'))
CARD_ITEMS = frozenset(map(int, ITEMS))
# Classic maintenance notice effective 2019-12-11, item 9.
CARD_USE_MIN_LEVEL = 100
# Group size from the classic INFO_ANNIVERSARY_2017_CARD_INFO table.
GROUP_TOTALS = {2008: 5, 2009: 5, 2010: 7, 2011: 7, 2012: 6,
                2013: 8, 2014: 9, 2015: 9, 2016: 9, 2017: 13}
PREVIEW = '卡牌奖励仅供预览。页面显示原版基础积分价，本周实际报价与抽签规则尚未接入。'


def reject_card_use(level):
    if level < CARD_USE_MIN_LEVEL:
        raise ValueError('角色达到100级后才能使用周年庆纪念卡，卡片未消耗。')
    raise ValueError('纪念卡的原版积分数值尚未核实，卡片未消耗。')


def exchange_preview(game, score_type):
    channels = GuardianChannels(game)
    with game.progression.transaction(write=False) as db:
        balance = channels.balance(db, score_type)

    # The native client enables purchase based on the remaining quantities.
    # Use zero availability while the live price and stock rules are unknown.
    rows = [
        f'{{item_type={row["item_type"]},need_score={row["item_score"]},'
        f'total_limit_num={row["weekly_num"]},server_left_num=0,person_left_num=0,'
        'in_draw_duration=false,need_lucky_draw=false,lucky_draw_total_num=0,is_in_lucky_draw_queue=false}'
        for row in EVENT['rows'] if row['score_type'] == score_type
    ]
    catalog = '{' + ','.join(rows) + '}'
    return packet(3157, ['number', 'number', 'string', 'number'], [score_type, balance, catalog, 1]), text_notice(PREVIEW)


def receive(game, mid, reader):
    if mid == 3156:
        reader.finish()
        # There are no prior card-unlock records in the local save format.
        rows = ','.join(f'{{group_id={group},num=0,total={total}}}' for group, total in GROUP_TOTALS.items())
        return packet(3154, ['string'], ['{' + rows + '}'])
    if mid == 3155:
        group = reader.integer()
        reader.finish()
        if group not in GROUP_TOTALS:
            raise ValueError('卡牌分组无效。')
        # The stock client already has the card descriptions and locked previews.
        return packet(3153, ['number', 'string'], [group, '{}'])

    score_type = reader.integer()
    item_type = reader.integer() if mid in (3158, 3161) else None
    reader.finish()
    if score_type not in SCORE_TYPES or (item_type is not None and (score_type, item_type) not in EVENT_ROWS):
        raise ValueError('卡牌积分兑换项目无效。')
    if mid == 3159:
        return exchange_preview(game, score_type)
    if mid == 3161:
        return text_notice('卡牌抽签尚未开放。')
    return text_notice('卡牌奖励兑换暂未开放，积分未扣除。')
