"""Opt-in, token-protected native acceptance on an existing owned connection."""
from pathlib import Path
from queue import Queue
import gc,json
from time import monotonic

ROOT=Path(__file__).resolve().parent
TYPES={
    1450:['byte'],1400:['number','byte','byte'],4310:[],
    1473:[],1474:['number'],1475:[],
    796:['number'],798:['number'],799:['number'],882:['number','number'],883:[],884:[],1441:['number'],2610:[],3758:['number',('array','string')],
    1448:[],1987:[],1988:[],1989:[],3705:['number'],3707:[],3742:[],
    1492:['number'],1493:['number'],1495:['number','string'],1985:[],1986:['number'],1980:['number'],
    1981:['number'],1982:[],1990:['number'],4344:[],4345:[],2805:['number'],4346:['number','number'],4347:['number'],4348:['number'],
    807:['number','number'],837:[],3663:['number','number'],3665:['number']*4,3667:[],
    3309:['number']*2,3310:['number']*2,3474:['number']*2,3475:['number'],3476:['number']*2,
    4824:[],2300:['number'],2289:[],2302:[],
    1034:['number'],1032:['byte','number'],1644:['word','word','word'],1645:['number','number'],
    729:['number'],732:['number'],733:['number'],734:['number'],750:['number','number'],752:['number','string'],
    1393:['number'],1394:['number'],
    759:['number'],760:['number'],761:['number'],762:['number'],764:['number']*5,775:['number'],
    2038:['number'],2039:['number'],2041:['number'],2042:['number'],2043:[],2044:[],2045:[],2046:['number'],
    2047:['number'],2048:[],2049:[],2050:['number'],2051:[],2053:['number'],2054:[],2055:['number'],2056:['number'],2057:['number']*2,
    2176:['number'],2177:['number']*2,2178:['number'],2181:['number']*2,2182:['number'],2183:['number']*3,2184:[],2185:['number']*3,2317:[],
}
from native946_guardian_rules import PENDING_REQUESTS
TYPES.update({mid:['number']*len(row['fields']) for mid,row in PENDING_REQUESTS.items()})
TYPES.update({727:['number'],3024:['number'],3305:['number'],3401:[],3978:['number'],4501:[],
              755:['number','number'],767:['number','number'],
              4499:['string','number','number'],4500:['number','number'],4502:['number','string'],
              952:['number'],3043:['number'],3148:['number'],3150:['number'],3969:['number'],
              1390:['number'],2574:[],2575:['number'],2576:['byte']})
from native946_skill6 import ARGUMENTS as SKILL6_ARGUMENTS
TYPES.update({mid:['number']*count for mid,count in SKILL6_ARGUMENTS.items() if mid != 1882})
TYPES[1882] = ['number', 'byte']

def dispatch(login,request):
    from native946_codec import packet
    kind=request.get('message_id');values=request.get('values')
    if type(kind) is not int or kind not in TYPES:raise ValueError('Request is outside native acceptance scope')
    fields=TYPES[kind]
    if not isinstance(values,list) or len(values)!=len(fields):raise ValueError('Native acceptance field count differs')
    for field,value in zip(fields,values):
        if field=='string':
            if not isinstance(value,str) or len(value.encode('gb18030'))>128:raise ValueError('Invalid native acceptance text')
        elif field==('array','string'):
            if not isinstance(value,list) or len(value)>32 or any(not isinstance(v,str) or len(v.encode('gb18030'))>128 for v in value):raise ValueError('Invalid native acceptance text list')
        elif type(value) is not int or not -1<=value<=100000000:raise ValueError('Invalid native acceptance number')
        elif field in ('byte','word') and not 0<=value<=(255 if field=='byte' else 65535):raise ValueError('Native acceptance field is outside its width')
    # The owned gameplay dispatcher performs ownership, range, resource,
    # membership, battle phase and malformed-field checks as usual.
    game=getattr(login,'gameplay',None)
    if game is not None and game.battle and kind in (2176,2177,2178,2181,2182,2183,2185):
        game._background_round=(game.battle['id'],game.battle['round'])
    return decorate_packets(game,login.receive(packet(kind,fields,values)))

def decorate_packets(game,reply):
    battle=getattr(game,'battle',None)
    if reply is None or not battle or getattr(game,'_background_round',None)!=(battle['id'],battle['round']):return reply
    from native946_codec import packet
    from native946_game_login import message_id
    result=[]
    for message in reply if isinstance(reply,tuple) else (reply,):
        result.append(message)
        if message[:3]==message_id(2152):result.append(packet(5349,[],[]))
    if battle.get('guardian_acceptance') and battle['phase']=='input' and any(message[:3]==message_id(2151) for message in result):
        if not any(message[:3]==message_id(2167) for message in result):
            battle['_guardian_input_draw_acks']=battle.get('_guardian_input_draw_acks',0)+1
            result.append(packet(2167,[],[]))
    return tuple(result)

def late_commands(handler,login,port):
    if login is None or not getattr(login,'_acceptance_late_attach',False):return
    control=getattr(handler.server,'native_test_control',None)
    if control is None or getattr(getattr(login,'current_character',None),'get',lambda *args:None)('id')!=control.character_id:return
    if not hasattr(handler,'_acceptance_commands'):handler._acceptance_commands=Queue(maxsize=8)
    handler.send_test_commands(control,login,handler._acceptance_commands,port)

def cleanup(login, *, notify=False):
    if login is None:return
    game=getattr(login,'gameplay',None)
    saved=getattr(login,'_guardian_passive_restore',None)
    if saved is not None and game is not None:
        game.progression.set_vitals(saved['hp'],saved['mp'])
        for sid,vitals in saved['fixtures'].items():game.summons.set_vitals(sid,*vitals)
        if saved['fight'] is not None:game.summons.status(saved['fight'],1)
        else:
            active=game.summons.active()
            if active:game.summons.status(active['id'],2)
        if notify:game.party_service.enqueue(game,*game.sync_progression())
    if saved is not None:del login._guardian_passive_restore
    login._acceptance_late_attach=False
    login._background_round=None
    server=getattr(login,'_acceptance_server',None)
    if server is not None:
        server.native_test_control=None
        for obj in gc.get_objects():
            if hasattr(type(obj),'serve_forever') and getattr(obj,'server_address',None)==('127.0.0.1',18127):
                obj.native_test_control=None
    login._acceptance_server=None


def activate(active,cid):
    """Install only the existing opt-in controller and a socket-thread bridge."""
    from native946_control import LocalTestControl
    from native946_sessions import LEASES
    from native946_runtime import install_live_adapter
    if Path(active.__file__).resolve()!=ROOT/'server.py':raise ValueError('Target is not this local service')
    game_servers=[obj for obj in gc.get_objects() if hasattr(type(obj),'serve_forever') and getattr(obj,'server_address',None)==('127.0.0.1',19127) and hasattr(obj,'characters')]
    http_servers=[obj for obj in gc.get_objects() if hasattr(type(obj),'serve_forever') and getattr(obj,'server_address',None)==('127.0.0.1',18127)]
    if len(game_servers)!=1 or len(http_servers)!=1:raise ValueError('Local service listeners are ambiguous')
    server=game_servers[0];http=http_servers[0]
    if not server.native_local_login or Path(server.characters.database).resolve()!=ROOT/'client-946-accounts.sqlite3':raise ValueError('Service is outside the isolated 946 project')
    with LEASES.lock:
        reference=LEASES.owners.get((LEASES.database_key(server.characters.database),cid));owner=reference() if reference else None
    if owner is None or owner.gameplay is None:raise ValueError('Owned test character is not online')
    control=getattr(server,'native_test_control',None)
    if control is not None and control.character_id!=cid:raise ValueError('Another acceptance scope is active')
    if control is None:control=LocalTestControl(cid,ROOT/'.native-test-token')
    # A connection admitted with the controller already enabled has its own
    # command queue. A second late queue would rotate its epoch every tick.
    if not hasattr(owner,'_acceptance_late_attach'):
        owner._acceptance_late_attach=owner.source_port not in control.sessions
    owner._acceptance_server=server
    server.native_test_control=http.native_test_control=control
    if not install_live_adapter():raise RuntimeError('Live adapter was not installed')
    wake=getattr(owner,'native_wake',None) or getattr(owner.gameplay,'outbound_wake',None)
    if wake:wake()
    result=dict(passed=True,character_id=cid,source_port=owner.source_port,forced_disconnect=False,
                original_socket_preserved=True,keyboard_mouse_input=False,controller_scope='one owned local 946 character')
    (ROOT/'analysis/guardian-completion-native-20261126/live-attach-receipt.json').write_text(json.dumps(result,indent=2)+'\n','utf8')
    return result

def prepare_companion(login,request):
    """One own-account protocol peer; a private controller grants a short lease."""
    from base64 import b64encode
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    port=request.get('source_port');pem=request.get('public_key')
    if type(port) is not int or not 1024<=port<=65535 or port==login.source_port:raise ValueError('Invalid companion port')
    if not isinstance(pem,str) or len(pem)>2048:raise ValueError('Invalid companion public key')
    key=serialization.load_pem_public_key(pem.encode('ascii'))
    if not isinstance(key,rsa.RSAPublicKey) or key.key_size!=1024:raise ValueError('Companion key must match the native fixture protocol')
    server=getattr(login,'_acceptance_server',None)
    if server is None or server.native_test_control.character_id!=login.current_character['id']:raise ValueError('No scoped native acceptance session')
    if login.gameplay.battle:raise ValueError('Prepare the companion outside battle')
    peers=getattr(server,'_acceptance_peer_ports',{})
    peers={p:row for p,row in peers.items() if row[1]>monotonic()}
    if peers and port not in peers:raise ValueError('One acceptance companion is already registered')
    ticket=login.tickets.issue(login.username,pem.encode('ascii'))
    companion=login.characters.create(login.username,'组队验收',702)
    peers[port]=(companion['id'],monotonic()+30)
    server._acceptance_peer_ports=peers
    if not hasattr(server,'_acceptance_original_configure'):
        from native946_test_client import configure_loopback_client
        server._acceptance_original_configure=getattr(server,'native_peer_configure',configure_loopback_client)
        def configure(source_port,**kwargs):
            row=server._acceptance_peer_ports.get(source_port)
            if row is not None and row[1]>monotonic():return {'owned_acceptance_companion':True}
            return server._acceptance_original_configure(source_port,**kwargs)
        server.native_peer_configure=configure
    login._acceptance_reply=dict(character_id=companion['id'],character_name=companion['name'],
                                 encrypted_ticket=b64encode(ticket).decode('ascii'),existing_account_used=True)
    return None

def clear_companion(login):
    server=getattr(login,'_acceptance_server',None)
    if server is None:raise ValueError('No scoped acceptance service')
    server._acceptance_peer_ports={}
    if login.gameplay.battle is None:login.gameplay._background_round=None
    login._acceptance_reply=dict(companion_admission_removed=True)
    return None
