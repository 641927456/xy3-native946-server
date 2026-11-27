"""Explicit original-rule gaps; unsupported requests never silently succeed."""
from pathlib import Path
import json

DATA=json.loads(Path(__file__).with_name('native946_guardian_rules_data.json').read_text('utf8'))
PENDING_REQUESTS={int(mid):row for mid,row in DATA['pending_requests'].items()}

def reject_unverified(game,mid,reader):
    contract=PENDING_REQUESTS[mid];values=[]
    for field in contract['fields']:
        if field[1]!='number' or len(field)>2:raise ValueError('未核实的守护请求格式。')
        values.append(reader.integer())
    reader.finish()
    if not values:raise ValueError('守护请求缺少归属编号。')
    game.summons.get(values[0])
    if game.battle is not None:raise ValueError('请先完成当前战斗。')
    title='守护特技' if mid in (754,756,759,763,768,770,771,772,773,774) else '守护阶段培养'
    raise ValueError(title+'的946原版效果或消耗规则尚未核定，本次未执行，经验、金钱和物品未扣除。')
