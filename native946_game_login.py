"""Authenticated native game handshake and new-account character listing."""

from time import time, monotonic

from native946_login import MessageReader, string_field
from local_accounts import AccountError


def message_id(value):
    return bytes([value]) if 0 < value < 256 else b"\x00" + value.to_bytes(2, "little")


class NativeGameLogin:
    lease_version=2
    character_store_version=1

    def __init__(self, source_port, tickets, number, configure, characters=None):
        self.source_port = source_port
        self.tickets = tickets
        self.number = number
        self.configure = configure
        self.checked = False
        self.public_pem = None
        self.username = None
        self.characters = characters
        self.current_character = None
        self.created_character = None
        self.gameplay = None

    def scene_packets(self):
        from struct import pack
        from native946_codec import packet
        from native946_characters import DEFAULT_RUN_SPEED, scene_actor_packet
        from native946_world import world
        character = self.current_character
        if self.gameplay is not None:
            from native946_cosmetics import Cosmetics
            Cosmetics(self.gameplay).refresh()
        scene=world().scene(character['map_id'])
        speed = character.get('run_speed', DEFAULT_RUN_SPEED)
        return (
            message_id(1597) + pack("<HHH", character["map_id"], character["x"], character["y"])
            + string_field(scene['scene_name']) + pack("<H", scene['map_id'])
            + self.number(0) + b"\x00" + self.number(8),
            scene_actor_packet(character),
            message_id(1585) + self.number(character["id"]) + self.number(character["id"]),
            *((packet(1621, ['number'] * 3, [character['id'], 15, speed]),)
              if speed != DEFAULT_RUN_SPEED else ()),
        )

    def refresh_character_store(self):
        """Keep the listener's existing store current after a module reload.

        The store owns the position writer and runtime caches. Upgrade its
        class in place instead of rebuilding it or dropping those objects.
        Protocol fixtures and other store implementations are left alone.
        """
        from native946_characters import Characters
        previous=type(self.characters)
        if (previous is not Characters and previous.__module__==Characters.__module__
                and previous.__name__==Characters.__name__):
            self.characters.__class__=Characters

    def account_packets(self):
        self.refresh_character_store()
        from native946_codec import character_list
        from native946_characters import player_info
        characters = self.characters.list(self.username,stage_fields=True) if self.characters is not None else []
        return (message_id(1010) + b"\x00" + self.number(len(characters)) + b"\x01",
                character_list([player_info(character) for character in characters]))

    def world_packets(self, character):
        from struct import pack
        from native946_codec import record
        from native946_characters import hero_info, scene_player
        initial=hero_info(character)
        if self.gameplay is not None:initial.update(self.gameplay.progression.attribute_values())
        return (
            message_id(893) + self.number(character["id"]),
            message_id(904) + self.number(0) + self.number(0),
            message_id(891),
            message_id(693) + record("hero", initial),
            *self.scene_packets(),
            *(self.gameplay.scene_objects() if self.gameplay is not None else ()),
            *(self.gameplay.sync_progression() if self.gameplay is not None else ()),
        )

    def enter_world(self,cid):
        self.refresh_character_store()
        from native946_sessions import LEASES
        from native946_storage import StorageBusy
        from native946_map import load_map
        from native946_gameplay import NativeGameplay
        try:
            if not any(row['id']==cid for row in self.characters.list(self.username)):
                raise AccountError(404,'找不到这个账号的角色。')
            LEASES.claim(self.characters.database,cid,self)
            character=self.characters.enter(self.username,cid)
            grid=load_map(character['map_id'])
            if not grid.walkable(character['x'],character['y']):
                x,y=grid.nearest_walkable(character['x'],character['y'])
                self.characters.move(self.username,cid,character['map_id'],x,y)
                character.update(x=x,y=y)
            # Construct before publishing the actor so a busy initialization
            # can retry without resetting a half-entered native scene.
            game=NativeGameplay(character,self.characters,self.scene_packets)
            game.source_port=self.source_port
            game.outbound_wake=getattr(self,'native_wake',None)
            self.current_character=character;self.gameplay=game
            game.party_service.refresh_run_speeds(game)
            game.auto_level()
            result=self.world_packets(character)
            result=(*result,*game.party_service.reconnect_packets(game))
            from native946_friends import Friends, presence_changed
            from native946_mail import initial_packets as mail_packets
            from native946_give import saved_settings
            result=(*result,*Friends(game).initial_packets(),*mail_packets(game),*saved_settings(game))
            from native946_newbie_rewards import begin as begin_newbie_rewards
            result=(*result,*begin_newbie_rewards(game))
            if not character['last_entered_at'] and not game.battle:
                from native946_campaign import CHAPTERS, current
                from native946_cinematics import NEW_CHARACTER_INTRO, play
                snapshot = game.progression.snapshot()
                run = current(snapshot)
                newbie = CHAPTERS['newbie']
                if (run and run['chapter'] == 'newbie' and run['state'] == 'active' and run['node'] == 0
                        and character['map_id'] == newbie['nodes'][0]['target']['scene']
                        and snapshot['task_preferences'].get(newbie['qid'], 1)):
                    result = (*result, *play(game, NEW_CHARACTER_INTRO, resume={'kind': 'new_character'}))
            self._pending_enter=None
            presence_changed(game)
            return result
        except StorageBusy:
            self.current_character=None;self.gameplay=None
            pending=getattr(self,'_pending_enter',None)
            self._pending_enter=(cid,pending[1] if pending is not None and pending[0]==cid else monotonic()+3)
            self._enter_retry_at=monotonic()+.05
            return None
        except AccountError as error:
            self._pending_enter=None
            LEASES.release_port(self.characters.database,self.source_port)
            return message_id(888)+self.number(1)+string_field(str(error))

    def tick(self):
        pending=getattr(self,'_pending_enter',None)
        if pending is None or monotonic()<self._enter_retry_at:return None
        cid,deadline=pending
        if monotonic()>=deadline:
            from native946_sessions import LEASES
            LEASES.release_port(self.characters.database,self.source_port)
            self._pending_enter=None
            return message_id(888)+self.number(1)+string_field('进入地图暂时繁忙，请稍后重试。')
        return self.enter_world(cid)

    def receive(self, payload):
        self.refresh_character_store()
        reader = MessageReader(payload)
        mid = reader.take(1)[0]
        if mid == 0:
            mid = int.from_bytes(reader.take(2), "little")
        if mid == 1015:
            reader.integer()  # Protocol table checksum.
            reader.string()  # Optional client metadata; empty in the observed request.
            challenge = reader.string()
            reader.finish()
            signature = self.tickets.sign_challenge(challenge)
            self.configure(self.source_port, game_public_key=self.tickets.public_pem)
            self.checked = True
            return message_id(1008) + self.number(1) + string_field(signature)
        if mid == 1017 and self.checked:
            self.public_pem = reader.string()
            reader.finish()
            return message_id(1009) + self.number(1) + self.number(int(time()))
        if mid == 1016 and self.checked and self.public_pem is not None:
            signed_ticket = reader.string()
            reader.take(1)  # Ticket kind.
            reader.finish()
            self.username = self.tickets.redeem(signed_ticket, self.public_pem)
            return self.account_packets()
        if self.username is None:
            return None
        if mid == 913 and self.characters is not None:
            name = reader.string().decode("gb18030")
            role = reader.integer()
            reader.integer()  # Native creation flag.
            count = reader.take(1)[0]
            if count > 32:
                raise ValueError("Too many native client information records")
            for _ in range(count):
                reader.string()
                reader.integer()
            reader.finish()
            try:
                self.created_character = self.characters.create(self.username, name, role,newbie=True)
            except AccountError as error:
                return message_id(888) + self.number(1) + string_field(str(error))
            return self.account_packets()
        if mid == 915 and self.characters is not None:
            cid = reader.integer()
            reader.finish()
            if self.gameplay is not None:
                # A retransmitted enter packet must not reset a live battle or
                # create another actor for an already connected character.
                return None
            return self.enter_world(cid)
        if mid == 2427:
            reader.take(1)
            reader.finish()
            from secrets import randbelow
            return message_id(2426) + string_field(f"初行者{randbelow(1000):03d}")
        if self.gameplay is not None:
            return self.gameplay.receive(mid, reader)
        return None
