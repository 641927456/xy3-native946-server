"""Opt-in loopback test controls. All actions run on the game socket's thread."""
from dataclasses import dataclass, field
from pathlib import Path
from queue import Full
from secrets import compare_digest, token_urlsafe
from threading import Event, Lock
from collections import OrderedDict
from copy import deepcopy
from time import monotonic
from uuid import uuid4
import re
import json

BACKGROUND_WIRE_VERSION = 21
HERO_PAGE_RELOAD_VERSION = 10


class ControlError(ValueError):
    def __init__(self, message, status=409, command_id=None, outcome=None):
        super().__init__(message)
        self.status = status
        self.command_id = command_id
        self.outcome = outcome


@dataclass
class Command:
    request: dict
    done: Event = field(default_factory=Event)
    result: dict | None = None
    command_id: str = field(default_factory=lambda:uuid4().hex)
    epoch: str | None = None
    created: float = field(default_factory=monotonic)
    deadline: float = field(default_factory=lambda:monotonic()+4)
    started: float | None = None
    finished: float | None = None
    outcome: str = 'queued'
    lock: Lock = field(default_factory=Lock)

    def begin(self, epoch):
        with self.lock:
            if self.outcome != 'queued':
                return False
            if self.epoch is not None and self.epoch != epoch:
                self._cancel('disconnected')
                return False
            if monotonic() >= self.deadline:
                self._cancel('expired')
                return False
            self.outcome = 'running'
            self.started = monotonic()
            return True

    def _cancel(self, reason):
        self.outcome = reason
        self.finished = monotonic()
        self.result = {'error':'Command was cancelled before execution', 'outcome':reason,
                       'command_id':self.command_id}
        self.done.set()

    def cancel_pending(self, reason='expired'):
        with self.lock:
            if self.outcome == 'queued':
                self._cancel(reason)
                return True
            return False

    def complete(self, result):
        with self.lock:
            self.result = result
            self.finished = monotonic()
            self.outcome = 'failed' if 'error' in result else 'completed'
            self.done.set()

    def receipt(self):
        with self.lock:
            return {'command_id':self.command_id, 'outcome':self.outcome,
                    'queue_ms':round(((self.started or self.finished or monotonic())-self.created)*1000,3),
                    'execution_ms':round((self.finished-self.started)*1000,3)
                    if self.finished is not None and self.started is not None else None}


# Existing HTTP submit() frames can still be running during a reload. Keep
# this identity stable so their isinstance checks cannot wrap a binding as if
# it were a raw Queue. Epochs and wake callbacks must survive code updates.
if 'Binding' not in globals():
    @dataclass
    class Binding:
        queue: object
        epoch: str = field(default_factory=lambda:uuid4().hex)
        wake: object = None


class LocalTestControl:
    wire_version = BACKGROUND_WIRE_VERSION
    command_timeout = 4

    def __init__(self, character_id, token_path):
        self.character_id = character_id
        self.token = token_urlsafe(32)
        self.sessions = {}
        self.lock = Lock()
        self.receipts = OrderedDict()
        Path(token_path).write_text(self.token, encoding="ascii")

    def ensure_runtime(self):
        # Upgrade the opt-in controller in place without rotating its token.
        with self.lock:
            if not hasattr(self,'receipts'):
                self.receipts = OrderedDict()
            for port,value in list(self.sessions.items()):
                if not isinstance(value,Binding):
                    self.sessions[port] = Binding(value.queue,value.epoch,value.wake) if hasattr(value,'queue') else Binding(value)

    def register(self, port, login, commands, wake=None):
        self.ensure_runtime()
        character = login.current_character if login else None
        if character and character["id"] == self.character_id:
            with self.lock:
                binding = self.sessions.get(port)
                if binding is None or binding.queue is not commands:
                    if binding is not None:
                        self._cancel_binding(binding)
                    binding = self.sessions[port] = Binding(commands)
                binding.wake = wake
                return binding.epoch
        self.unregister(port)

    def _cancel_binding(self, binding):
        for command in self.receipts.values():
            if command.epoch == binding.epoch:
                command.cancel_pending('disconnected')

    def unregister(self, port):
        with self.lock:
            binding = self.sessions.pop(port, None)
            if binding is not None:
                self._cancel_binding(binding)

    def submit(self, token, request):
        if not compare_digest(token, self.token):
            raise ValueError("Invalid local test token")
        self.ensure_runtime()
        if isinstance(request,dict) and request.get('action') == 'metrics':
            from native946_runtime import METRICS, RUNTIME_VERSION
            with self.lock:
                queues = [b.queue.qsize() for b in self.sessions.values()]
            writer=getattr(self,'position_writer',None)
            return {'runtime_version':RUNTIME_VERSION,'control_wire_version':self.__class__.wire_version,
                    'controller_runtime_ready':self.__class__ is LocalTestControl,'queue_depths':queues,
                    'position_writer':writer.snapshot() if writer is not None else None,**METRICS.snapshot()}
        if isinstance(request,dict) and request.get('action') == 'command_result':
            if not isinstance(request.get('command_id'),str):
                raise ValueError('Expected a command receipt ID')
            with self.lock:
                command = self.receipts.get(request.get('command_id'))
            if command is None:
                raise ControlError('Command receipt is unavailable',404)
            return {**command.receipt(), 'result':deepcopy(command.result)}
        if not isinstance(request, dict) or request.get("action") not in {
                'prepare_guardian_passive','restore_guardian_passive','abort_guardian_passive','prepare_battle_presentation','play_battle_presentation','finish_battle_presentation',
                'cultivation_fixture_start','cultivation_fixture_next','cultivation_fixture_restore',
                "status", "move", "navigate", "talk", "train", "attack", "escape", "reload", "refresh",
                "allocate", "upgrade", "learn", "item_move", "item_split", "item_use",
                "quest_accept", "npc_choice", "skill", "item_merge", "shortcut", "panel",
                "pet_status", "pet_rename", "pet_allocate", "pet_upgrade", "pet_info", "pet_attack", "pet_defend", "native_request", "prepare_companion", "clear_companion"}:
            raise ValueError("Unsupported local test command")
        with self.lock:
            if len(self.sessions) != 1:
                raise ValueError("Expected one connected test character")
            binding = next(iter(self.sessions.values()))
            request_id = request.get('request_id',uuid4().hex)
            if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',request_id):
                raise ValueError('Invalid command request ID')
            body = deepcopy({k:v for k,v in request.items() if k != 'request_id'})
            command = self.receipts.get(request_id)
            if command is not None:
                if command.epoch != binding.epoch or command.request != body:
                    raise ControlError('Request ID belongs to another command or session')
            else:
                while len(self.receipts) >= 256:
                    removable = next((key for key,value in self.receipts.items() if value.done.is_set()),None)
                    if removable is None:
                        raise ControlError('Command receipt capacity reached',503)
                    self.receipts.pop(removable)
                command = Command(body,command_id=request_id,epoch=binding.epoch,
                                  deadline=monotonic()+self.command_timeout)
                try:
                    binding.queue.put_nowait(command)
                except Full:
                    raise ControlError('Local test command queue is full',429) from None
                self.receipts[request_id] = command
                if binding.wake is not None:
                    binding.wake()
        if not command.done.wait(max(0,command.deadline-monotonic())):
            cancelled = command.cancel_pending()
            # A running transaction is not reported as a failed operation:
            # callers can query this receipt instead of repeating the mutation.
            raise ControlError('Command expired before execution' if cancelled else
                               'Command is still executing; query its receipt',
                               504 if cancelled else 202,command.command_id,command.outcome)
        return {**deepcopy(command.result), '_command':command.receipt()}


def execute(login, request):
    """Return native replies without injecting input or invoking client code."""
    from native946_codec import number
    from native946_game_login import message_id
    from native946_login import MessageReader, string_field
    from native946_gameplay import ENEMY_ID, TRAINER_ID
    from struct import pack

    game = login.gameplay
    if game is None:
        raise ValueError("Test character is not in a map")
    action = request["action"]
    from native946_cultivation_acceptance import ACTIONS, run as cultivation_fixture
    if action in ACTIONS:
        return cultivation_fixture(login,request)
    if action == 'reload' and getattr(login, '_cultivation_fixture', None):
        raise ValueError('请先恢复真实存档再重载。')
    if action in ('prepare_battle_presentation','play_battle_presentation','finish_battle_presentation'):
        from native946_presentation_acceptance import run
        return run(login,request)
    if action in ('prepare_guardian_passive','restore_guardian_passive','abort_guardian_passive'):
        from native946_guardian_acceptance import run
        return run(login,request)
    if action=='prepare_companion':
        from native946_acceptance import prepare_companion
        return prepare_companion(login,request)
    if action=='clear_companion':
        from native946_acceptance import clear_companion
        return clear_companion(login)
    if action=='native_request':
        from native946_acceptance import dispatch
        return dispatch(login,request)
    if action == "status":
        return None
    if action == 'navigate':
        return game.navigate_to_trainer()
    if action == "reload":
        if game.battle is not None:raise ValueError('请先结束当前战斗，再更新原生验收版本。')
        if getattr(game,'party_service',None) is not None and game.party_service.battles:
            raise ValueError('仍有共享战斗正在使用当前规则，稍后再更新。')
        from importlib import reload
        from native946_hot_reload import reload_gameplay
        reload_gameplay(game.characters.database, (login,))
        from native946_runtime import install_live_adapter
        import server
        reload(server)
        install_live_adapter()
        if game.battle is None:
            return (*login.scene_packets(),*game.scene_objects(),*game.sync_progression())
        return game.sync_progression()
    if action == 'refresh':
        return game.sync_progression()
    if action == 'panel':
        name=request.get('name')
        from native946_tasks import STORY_PANEL,STORY_CHILD_PANEL,story_panel_packets
        if name in (STORY_PANEL,STORY_CHILD_PANEL):
            return story_panel_packets(request.get('show',True))
        if name not in ['dlg_hero_status','dlg_item_panel','dlg_newtask_task_panel','dlg_skill_panel_new','dlg_npc_chat','dlg_summon_status','dlg_eshop_panel','dlg_cloth_panel',
                        'dlg_newtask_query_panel','dlg_newtask_panel']:
            closable=['dlg_summon_skill','dlg_summon_resist','dlg_summon_info_new','dlg_summon_info',
                      'dlg_summon_xiukang','dlg_summon_stone','dlg_summon_9star','dlg_summon_stone_info',
                      'dlg_lianqi_panel','dlg_summon_calc','dlg_summon_jewelry_preview','dlg_canjun_select']
            if request.get('show',True) or name not in closable:raise ValueError('Panel is outside the local test scope')
        from native946_codec import packet
        return packet(3993 if request.get('show',True) else 1183,['string'],[name])
    if action == 'allocate':
        values=request.get('points')
        if not isinstance(values,list) or len(values)!=4 or any(type(n) is not int for n in values):
            raise ValueError('Expected four integer point increments')
        payload=message_id(739)+b''.join(number(n) for n in values)
    elif action=='upgrade':payload=message_id(748)
    elif action=='learn':payload=message_id(749)+number(request['skill'])+number(1)
    elif action=='item_move':
        row=next((i for i in game.progression.snapshot()['items'] if i['id']==request['item']),None)
        if row is None:raise ValueError('Item is not owned by test character')
        payload=message_id(803)+b''.join(number(n) for n in [row['page'],row['slot'],request['page'],request['slot']])
    elif action=='item_split':payload=message_id(805)+b''.join(number(n) for n in [request['item'],8,request['slot'],request['amount']])
    elif action=='item_use':payload=message_id(807)+number(game.character['id'])+number(request['item'])
    elif action=='item_merge':
        ids=request.get('items')
        if not isinstance(ids,list) or not 1<=len(ids)<=24:raise ValueError('Invalid item merge request')
        payload=message_id(802)+bytes([len(ids)])+b''.join(number(n) for n in ids)
    elif action=='shortcut':
        payload=message_id(1483)+number(request['skill'])+bytes([request['slot']])
    elif action=='quest_accept':payload=message_id(1492)+number(910001)
    elif action=='npc_choice':
        choice=request.get('choice')
        if type(choice) is not int or not 1<=choice<=255:raise ValueError('Invalid NPC choice')
        payload=message_id(1032)+bytes([choice])+number(TRAINER_ID)
    elif action=='skill':payload=message_id(2185)+number(9)+number(request['skill'])+number(ENEMY_ID)
    elif action=='pet_attack':payload=message_id(2181)+number(3)+number(ENEMY_ID)
    elif action=='pet_defend':payload=message_id(2182)+number(1)
    elif action in ('pet_status','pet_rename','pet_allocate','pet_upgrade','pet_info'):
        sid=request.get('pet')
        if type(sid) is not int:raise ValueError('Expected an owned guardian ID')
        game.summons.get(sid)
        if action=='pet_status':payload=message_id(750)+number(sid)+number(request['status'])
        elif action=='pet_rename':payload=message_id(752)+number(sid)+string_field(request['name'])
        elif action=='pet_allocate':
            values=request.get('points')
            if not isinstance(values,list) or len(values)!=4 or any(type(n) is not int for n in values):
                raise ValueError('Expected four integer point increments')
            payload=message_id(764)+number(sid)+b''.join(number(n) for n in values)
        elif action=='pet_upgrade':payload=message_id(775)+number(sid)
        else:payload=message_id(733)+number(sid)
    elif action == "move":
        x, y = request.get("x"), request.get("y")
        if type(x) is not int or type(y) is not int or not (0 <= x <= 65535 and 0 <= y <= 65535):
            raise ValueError("Movement needs integer grid coordinates")
        payload = message_id(1643) + number(4) + pack("<HH", x, y) + string_field(b"")
    elif action == "talk":
        payload = message_id(1034) + number(TRAINER_ID)
    elif action == "train":
        payload = message_id(1032) + b"\x01" + number(TRAINER_ID)
    elif action == "attack":
        payload = message_id(2177) + number(3) + number(ENEMY_ID)
    elif action == "escape":
        payload = message_id(2178) + number(2)
    origin = (game.character['x'],game.character['y'])
    if action in ('attack','skill','pet_attack','pet_defend') and game.battle:
        game._background_round=(game.battle['id'],game.battle['round'])
    reply = login.receive(payload)
    if reply is not None and action in ('attack','skill','pet_attack','pet_defend'):
        # Background protocol submission bypasses the client's input handler.
        # Preserve input acknowledgments: the native renderer needs them to
        # advance past each input menu. Cancel the no-operation auto panel
        # they can open in protocol-only testing. The matching round rejects
        # the resulting native default requests; real user input is unchanged.
        messages=reply if isinstance(reply,tuple) else (reply,)
        from native946_codec import packet
        wire_messages=[]
        for message in messages:
            wire_messages.append(message)
            if message[:3]==message_id(2152):wire_messages.append(packet(5349,[],[]))
        reply=tuple(wire_messages)
    if action == 'move' and game.last_event and game.last_event.get('action') == 'movement':
        # Explicit background-test movement must reach the client. Ordinary
        # s_hero_move packets never echo to the predicting local hero.
        from native946_codec import packet
        from native946_characters import DEFAULT_RUN_SPEED
        movement=packet(1608,['number','number','word','word','word','word'],
                        [game.character['id'],game.character.get('run_speed',DEFAULT_RUN_SPEED),*origin,*game.last_event['to']])
        extras=() if reply is None else reply if isinstance(reply,tuple) else (reply,)
        return (movement,*extras)
    return reply


def state(login):
    game = login.gameplay
    from contextlib import nullcontext
    from native946_storage import transaction
    context=transaction(game.characters.database) if hasattr(game,'characters') else nullcontext()
    with context:
        result=deepcopy({"character_id": game.character["id"], "position": [game.character["x"], game.character["y"]],
                "battle": game.battle, "event": game.last_event,
                'progression': game.progression.snapshot() if hasattr(game,'progression') else None,
                'summons': game.summons.snapshot() if hasattr(game,'summons') else []})
        extra=login.__dict__.pop('_acceptance_reply',None)
        if extra is not None:result['acceptance']=extra
        if getattr(login, '_cultivation_fixture', None):
            from native946_skill6 import saved
            with game.progression.transaction(write=False) as db:
                result['fixture_skill6']=saved(db, game.character['id'])
            result['fixture_dialog']=deepcopy(game.dialog_actions)
        from sys import modules
        names={'native946_lineups':'WIRE_VERSION','native946_role_battle':'WIRE_VERSION',
               'native946_wilderness':'FORMATION_WIRE_VERSION','native946_inventory':'WIRE_VERSION',
               'native946_control':'HERO_PAGE_RELOAD_VERSION','native946_cosmetics':'REFERENCE_WIRE_VERSION',
               'native946_skill6':'WIRE_VERSION','native946_campaign':'REFERENCE_WIRE_VERSION',
               'native946_shops':'REFERENCE_WIRE_VERSION','native946_npc_services':'REFERENCE_WIRE_VERSION',
               'native946_innate_effects':'WIRE_VERSION','native946_innate_training':'WIRE_VERSION'}
        result['runtime_sources']={name:dict(path=getattr(modules[name],'__file__',None),version=getattr(modules[name],key,0)) for name,key in names.items() if name in modules}
        lineups=modules.get('native946_lineups');systems=modules.get('native946_role_systems')
        result['lineup_catalog_current']=bool(lineups and systems and lineups.CATALOG is systems.DATA['new_lineup'])
        cosmetics=modules.get('native946_cosmetics');refinement=modules.get('native946_refinement')
        result['refinement_catalog_current']=bool(cosmetics and refinement and cosmetics.REFINEMENT_DATA is refinement.DATA and 'gear_items' in refinement.DATA)
        shops=modules.get('native946_shops')
        if shops is not None:
            data=shops.DATA
            result['shop_catalog']=dict(merchants=len(data['merchants']),cash_sellers=sum(bool(m['goods']) for m in data['merchants'].values()),
                cash_offers=sum(len(m['goods']) for m in data['merchants'].values()),special_offers=len(data['specmall']['item_list']),
                medicine_effects=len(data.get('medicine_effects',{})),
                ignore_open_delay=data['server'].get('ignore_open_delay',False),hide_activity_time=data['server'].get('hide_activity_time',False))
        return result


if __name__ == "__main__":
    import argparse
    from urllib.request import Request, urlopen
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["status", "metrics", "command_result", "move", "navigate", "talk", "train", "attack", "escape", "reload", "refresh",
                    "allocate", "upgrade", "learn", "item_move", "item_split", "item_use", "quest_accept", "npc_choice", "skill",
                    "item_merge", "shortcut", "panel", "pet_status", "pet_rename", "pet_allocate", "pet_upgrade", "pet_info", "pet_attack", "pet_defend", "native_request"])
    p.add_argument("coordinates", type=int, nargs="*")
    p.add_argument('--json', dest='json_data')
    args = p.parse_args()
    body = {"action": args.action}
    if args.json_data:
        body.update(json.loads(args.json_data))
    if args.action == "move":
        if len(args.coordinates) != 2:
            p.error("move needs X Y")
        body.update(zip(("x", "y"), args.coordinates))
    token = Path(__file__).with_name(".native-test-token").read_text(encoding="ascii")
    request = Request("http://127.0.0.1:18127/local/native-test", json.dumps(body).encode(),
                      {"Content-Type": "application/json", "X-Local-Test-Token": token})
    with urlopen(request, timeout=6) as response:
        print(response.read().decode())
