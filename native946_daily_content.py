"""Daily content data and the 946 Three Realms Token exchange panels."""
import json
from pathlib import Path

from native946_codec import packet


DATA = json.loads(Path(__file__).with_name('native946_daily_content_data.json').read_text('utf8'))
ITEMS = DATA['items']
STANDARD_EXCHANGE_NPCS = {(2001, 4101), (2006, 4220)}
ADVANCED_EXCHANGE_NPC = (2001, 5500)
CURRENCY_TOKEN = 2  # COMMON_CONST.CURRENCY_TOKEN in the 946 seller panel.


def exchange_menu(game, npc):
    key = (npc['scene_id'], npc['npc_id'])
    if key != ADVANCED_EXCHANGE_NPC and key not in STANDARD_EXCHANGE_NPCS:
        return None
    nid = npc['npc_id']
    from native946_activities import unavailable_npc_notice

    # The panels consume server-supplied goods. Keep the list empty until
    # the current server's offers and currency requirements are verified.
    if key == ADVANCED_EXCHANGE_NPC:
        close, notice = unavailable_npc_notice(game, '三界令高级兑换暂未开放。')
        return (close, packet(1694, ['number', ('array', 'advitemexc_item')], [nid, []]), notice)
    # The empty-list path in this panel already displays the native message.
    return (game.close_npc_dialogue(),
            packet(1686, ['number', 'number', ('array', 'excseller_item')], [nid, CURRENCY_TOKEN, []]))
