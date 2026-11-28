"""Local Guardian evaluation calibrated against the observed 947 score ranges."""
import json
from pathlib import Path

from native946_growth import guardian_growth, species
import native946_guardian_chamber as guardian_chamber


RULES = json.loads(Path(__file__).with_name('native946_guardian_score_rules.json').read_text('utf8'))
INITIAL_FIELDS = (('hp', 'hp_max'), ('mp', 'mp_max'), ('attack', 'att_max'), ('speed', 'dod_max'))
STAGES = ('rebirth', 'ascension', 'sanctification')


def base_score(pet):
    rules = RULES['base']
    native = species(pet['type'])
    ratios = [pet[f'initial_{attr}_adj'] * 1000 // native[field]
              for attr, field in INITIAL_FIELDS if native[field] > 0]
    initials = sum(ratios) * rules['points_at_full_initial_values'] // (1000 * len(ratios)) if ratios else 0
    return (pet['level'] * rules['points_per_level']
            + max(0, guardian_growth(pet) - rules['growth_baseline']) * rules['points_per_growth_unit']
            + initials + pet['amity'] // rules['amity_per_point']
            + pet['longgu'] * rules['points_per_longgu']
            + sum(pet[stage] * rules[f'points_per_{stage}'] for stage in STAGES))


def skill_score(db, pet):
    from native946_guardian_innate import records
    from native946_innate_training import jing_books, trained_records, visible_innate
    from native946_neidan_legacy import raw_list as pill_records
    from native946_skillbooks import effective_books

    sid = pet['id']
    upgrades=jing_books(db,sid)
    hidden={entry['base'] for entry in upgrades}
    learned = [row for row in effective_books(db, pet) if row['skill'] not in hidden]
    learned_ids = {row['skill'] for row in learned}
    training = trained_records(db, sid, pet)
    innate_records = records(dict(pet, innate_training=training,jing_books=upgrades), excluded=learned_ids)
    innate = visible_innate(db, dict(pet, innate_skills=innate_records))
    trained = [training[row['stunt_id']] for row in innate if row['stunt_id'] in training]
    pills = pill_records(db, sid)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='native_new_neidan'").fetchone():
        from native946_neidan import installed
        pills += [dict(row,rebirth=min(2,row['stage']),ascension=int(row['stage']==3)) for row in installed(db,sid)]
    developed = learned + trained + pills
    rules = RULES['skills']
    return ((len(innate) + len(learned) + len(pills)) * rules['points_per_skill']
            + sum(row['level'] for row in developed) * rules['half_points_per_level'] // 2
            + sum(row.get(stage, 0) * rules[f'points_per_{stage}'] for row in developed for stage in STAGES))


def item_score(db, item_id, item_type):
    from native946_guardian_equipment import ITEMS,LOCAL_PROFILES,SPECIAL

    item = ITEMS.get(str(item_type))
    if not item:
        return 0
    rules = RULES['equipment']
    score = rules['points_by_grade'][item['level']]
    if item_type in SPECIAL or item_type in (42037,42039):score=rules['sample_standard_top']
    elif item_type in LOCAL_PROFILES and item['level']==4:score=rules['sample_enhanced_top']
    if item.get('summon'):
        from native946_guardian_ornaments import state

        traits, _ = state(db, item_id)
        score += sum(traits.values()) * rules['points_per_ornament_refinement_stat']
    return score


def total(db, pet):
    from native946_guardian_equipment import equipped_items
    from native946_guardian_jade import equipped_jades,stones
    sid=pet['id']
    names={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE name IN ('native_guardian_jade','native_guardian_stones')")}
    rules=RULES['equipment']
    jade_score=(sum(jade['level']*rules['points_per_jade_level']+int(bool(jade['skill']))*rules['points_per_jade_skill']
                    for jade in equipped_jades(db,sid))
                if 'native_guardian_jade' in names else 0)
    stone_score=(sum(rules['points_per_old_stone'] for kind in stones(db,sid,0).values() if kind)
                 +sum(rules['points_per_star_stone'] for kind in stones(db,sid,1).values() if kind)
                 if 'native_guardian_stones' in names else 0)
    return (base_score(pet) + skill_score(db, pet)
            + sum(item_score(db, row['id'], row['type'])
                  for row in equipped_items(db, pet['character_id'], sid)) + jade_score + stone_score)


def highest_score(db, cid):
    # Progression can send attributes before the guardian subsystem creates its tables.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='summons'").fetchone():
        return 0
    pets = db.execute('SELECT * FROM summons WHERE character_id=? AND ' + guardian_chamber.AVAILABLE, (cid,)).fetchall()
    return max((total(db, dict(pet)) for pet in pets), default=0)
