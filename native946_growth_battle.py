from pathlib import Path
from time import time
import json

from native946_codec import packet, screen_notice, text_notice
from native946_anniversary_cards import ICON as ANNIVERSARY_CARDS_ICON
from native946_characters import ROLES
from native946_classified_mall import ICON as CLASSIFIED_MALL_ICON
from native946_equipment import COMPLETION, persist
from native946_guardian_lottery import ICON as GUARDIAN_LOTTERY_ICON

DATA = json.loads(Path(__file__).with_name("native946_growth_battle_data.json").read_text("utf-8"))
LEVELS = DATA["levels"]
FIRST_CHARGE = DATA["first_charge"]
# Use the existing local starter gear durability for these equipment instances.
FIRST_CHARGE_DURABILITY = COMPLETION["starter"]["5022"]["durability"]
# Keep the existing zero-based growth-gift receipts distinct from this tab.
FIRST_CHARGE_INDEX = -1
ICON = 17
ICONS = (GUARDIAN_LOTTERY_ICON, ICON, ANNIVERSARY_CARDS_ICON, CLASSIFIED_MALL_ICON)
ICON_LIST_VERSION = 3
REQUESTS = {2574, 2575, 2576}
WRITES = REQUESTS

from native946_progression import DATA as PROGRESSION_DATA
from native946_shops import ITEMS as SHOP_ITEMS
for _kind, _meta in DATA["items"].items():
    PROGRESSION_DATA["items"].setdefault(_kind, _meta)
    SHOP_ITEMS.setdefault(_kind, _meta)


def schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS native_growth_claims(
          character_id INTEGER NOT NULL REFERENCES characters(id),
          reward_index INTEGER NOT NULL,
          claimed_at INTEGER NOT NULL,
          PRIMARY KEY(character_id,reward_index));
        CREATE TABLE IF NOT EXISTS native_growth_state(
          character_id INTEGER PRIMARY KEY REFERENCES characters(id),
          open_flag INTEGER NOT NULL DEFAULT 0);
    """)


def eligible(row, snapshot):
    required = row["requires"]
    return (snapshot["level"] >= required["level"]
            and snapshot["rebirth"] >= required["rebirth"]
            and snapshot["ascension"] >= required["ascension"]
            and snapshot["sanctification"] >= required["sanctification"])


class GrowthBattle:
    def __init__(self, game):
        self.g = game
        self.p = game.progression

    def panel(self):
        with self.p.transaction(write=False) as db:
            claimed = {row[0] for row in db.execute(
                "SELECT reward_index FROM native_growth_claims WHERE character_id=?", (self.p.cid,))}
            state = db.execute("SELECT open_flag FROM native_growth_state WHERE character_id=?",
                               (self.p.cid,)).fetchone()
            snapshot = self.p._snapshot(db)
            statuses = [1 if row["index"] in claimed else 2 if eligible(row, snapshot) else 0
                        for row in LEVELS]
            flag = state[0] if state else 0
        return packet(2573, [("array", "number"), "byte"], [statuses, flag])

    def entry_packets(self):
        if getattr(self.g, "growth_battle_icon_version", 0) == ICON_LIST_VERSION:
            return ()
        self.g.growth_battle_icon_version = ICON_LIST_VERSION
        # The native panel replaces all of its server icons on each list.
        return packet(1206, [("array", "number"), ("array", "number"), ("array", "string")],
                      [list(ICONS), [0] * len(ICONS), [""] * len(ICONS)]),

    def claim_first_charge(self):
        if self.g.battle:
            return text_notice("请先结束战斗再领取炎龙套装。")
        with self.p.transaction() as db:
            if db.execute("SELECT 1 FROM native_growth_claims WHERE character_id=? AND reward_index=?",
                          (self.p.cid, FIRST_CHARGE_INDEX)).fetchone():
                return screen_notice("该角色已领取炎龙套装。", optimize_display=True)
            snapshot = self.p._snapshot(db)
            name = ROLES[str(snapshot["role_id"])]["name"]
            pieces = [(int(kind), item) for kind, item in FIRST_CHARGE["equipment"].items()
                      if name in item["roles"] or item["race"] == snapshot["race"]]
            if sorted(item["slot"] for _, item in pieces) != [1, 2, 3, 4, 5]:
                raise ValueError("当前角色的炎龙套装配置不完整。")
            granted = set()
            for kind, item in pieces:
                page, slot = self.p.free_bag_slot(snapshot)
                item_id = db.execute(
                    "INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)",
                    (self.p.cid, kind, page, slot)).lastrowid
                profile = dict(reference_id=kind, grade=item["grade"], slot=item["slot"],
                               role=",".join(item["roles"]) or "全部", native_race=item["race"],
                               level=FIRST_CHARGE["wear_level"], rebirth=0, need={},
                               durability=FIRST_CHARGE_DURABILITY)
                # The classic catalog defines the pieces and wear slots. It has no combat rolls.
                persist(db, self.p.cid, item_id, profile, {})
                snapshot["items"].append({"page": page, "slot": slot})
                granted.add(item_id)
            db.execute("INSERT INTO native_growth_claims VALUES(?,?,?)",
                       (self.p.cid, FIRST_CHARGE_INDEX, int(time())))
        self.g.last_event = {"action": "growth_first_charge_claimed"}
        notice = screen_notice("已领取炎龙套装，共5件，80级可穿戴。")
        return self.g.defer_packets(lambda: (*self.p.item_packets(item_ids=granted), notice),
                                    fallback=notice)

    def receive(self, mid, reader):
        if mid == 2574:
            reader.finish()
            return self.claim_first_charge()
        if mid == 2576:
            flag = reader.take(1)[0]
            reader.finish()
            if flag not in (0, 1):
                raise ValueError("成长提示开关无效。")
            with self.p.transaction() as db:
                db.execute("INSERT INTO native_growth_state VALUES(?,?) ON CONFLICT(character_id) "
                           "DO UPDATE SET open_flag=excluded.open_flag", (self.p.cid, flag))
            return self.panel()
        if mid != 2575:
            return None
        position = reader.integer()
        reader.finish()
        if type(position) is not int or not 1 <= position <= len(LEVELS):
            raise ValueError("没有这个成长奖励档位。")
        if self.g.battle:
            return self.panel(), text_notice("请先结束战斗再领取成长奖励。")
        # The native Lua button sends a 1-based position. Saved claims use row indexes.
        row = LEVELS[position - 1]
        index = row["index"]
        message = None
        with self.p.transaction() as db:
            if db.execute("SELECT 1 FROM native_growth_claims WHERE character_id=? AND reward_index=?",
                          (self.p.cid, index)).fetchone():
                message = "该档成长奖励已经领取。"
            else:
                snapshot = self.p._snapshot(db)
                if not eligible(row, snapshot):
                    message = "达到" + row["label"] + "后可领取。"
                else:
                    from native946_shops import Shops
                    for kind in row["items"]:
                        Shops(self.g).grant(db, kind, 1)
                    db.execute("INSERT INTO native_growth_claims VALUES(?,?,?)", (self.p.cid, index, int(time())))
        if message:
            return self.panel(), text_notice(message)
        self.g.last_event = {"action": "growth_reward_claimed", "index": index}
        return self.g.defer_packets(lambda: (
            self.panel(), *self.p.item_packets(),
            text_notice("已领取" + row["label"] + "成长奖励；" + DATA["quantity_rule"]),
        ))
