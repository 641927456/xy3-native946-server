"""Local per-item scoring and the 947 observed equipped sum."""

from decimal import Decimal
from pathlib import Path
from time import time
import json

from native946_equipment import ALIASES, CORE, DIRECT
from native946_refinement import DIRECT as FORGE_DIRECT


RULES = json.loads(Path(__file__).with_name('native946_equipment_score_rules.json').read_text('utf8'))
DEFAULT_WEIGHT = Decimal(RULES['default_property_weight'])
WEIGHTS = {name: Decimal(weight) for name, weight in RULES['property_weights'].items()}
PROPERTY_NAMES = {**CORE, **DIRECT, **FORGE_DIRECT}


def item_score(instance, starter, gems, traits):
    if instance is not None:
        grade = instance['grade']
        base = instance['properties']
    elif starter is not None:
        grade = RULES['starter_grade']
        base = starter[1]
    else:
        return 0

    score = grade * RULES['points_per_grade']
    for name, amount in (*base.items(), *gems.items(), *(row[:2] for row in traits)):
        if amount > 0:
            name = ALIASES.get(name, name)
            name = PROPERTY_NAMES.get(name, name)
            score += Decimal(str(amount)) * WEIGHTS.get(name, DEFAULT_WEIGHT)
    return int(score)


def equipped_total(db, cid, snapshot, starter_gear):
    from native946_gear_sockets import worn_properties

    page = snapshot['equipment_page']
    equipped = [item for item in snapshot['items'] if item['page'] == page]
    if not equipped:
        return 0
    gems = worn_properties(db, cid, page)
    traits = {row['item_id']: json.loads(row['traits']) for row in db.execute(
        'SELECT r.item_id,r.traits FROM native_refinement r JOIN inventory i ON i.id=r.item_id '
        'WHERE r.character_id=? AND i.character_id=r.character_id AND i.page=? AND (r.expires=0 OR r.expires>?)',
        (cid, page, int(time())),
    )}
    return sum(item_score(snapshot['equipment'].get(item['id']), starter_gear.get(item['type']),
                          gems.get(item['id'], {}), traits.get(item['id'], ())) for item in equipped)
