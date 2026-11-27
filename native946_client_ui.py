"""Bind NPC blank-area right click to 946's verified native release method.

Only the SHA-pinned isolated loopback client is eligible. No input synthesis,
injected code, credentials or server diagnostic controls are used.
The original archive and the native release implementation remain unchanged.
"""
from hashlib import sha256
import argparse
import json
from pathlib import Path
import struct
from time import monotonic, sleep

from native946_test_client import TestClient

NOOP_SIGNATURE='1305dcfcfa1caffdc1267a755af4f71f078438519684465bb171404279fa3235'
RELEASE_SIGNATURE='e60d8a3b38ee5632e3c8fead4624be9d505d9c7ff9c6d16f627fcb08329d6b67'


def signature(client,closure):
    words=struct.unpack('<24I',client.read(client.integer(closure+20),96))
    if words[1]&255!=9 or words[10]>20000 or words[11]>4096:
        raise ValueError('Unexpected native NPC callback prototype')
    constants=[]
    for i in range(words[11]):
        pointer,_,kind,_=struct.unpack('<4I',client.read(words[2]+i*16,16))
        if kind!=4:raise ValueError('Unexpected NPC callback constant type')
        length=client.integer(pointer+12)
        if not 0<length<4096:raise ValueError('Unexpected NPC callback string')
        constants.append(client.read(pointer+16,length).decode('ascii'))
    code=[]
    for i in range(words[10]):
        value=client.integer(words[3]+i*4)
        code.append([i,value&63,value>>6&255,value>>23&511,value>>14&511])
    return sha256(json.dumps(dict(constants=constants,code=code),ensure_ascii=True,separators=(',',':')).encode()).hexdigest()


def bind_npc_right_click(client):
    vector,end=struct.unpack('<II',client.read(client.base+0xDF1F1C,8))
    if not vector or (end-vector)//4<=1025:raise ValueError('NPC protocol registry not loaded')
    entry=client.integer(vector+1025*4)
    reference=client.integer(entry+36)
    app=client.integer(client.base+0xDF0CF0);state=client.integer(app+0x38)
    registry=client.integer(client.integer(state+0x10)+0x60)
    size=client.integer(registry+0x1C)
    if not 0<reference<=size<=65536:raise ValueError('NPC callback reference not loaded')
    slot=client.integer(registry+12)+(reference-1)*16
    if client.integer(slot+8)!=6:raise ValueError('NPC protocol callback is not a function')
    table=client.integer(client.integer(slot)+12)
    for key in ('import','_loaded','dlg_npc_chat','UNpcChat'):
        _,table,kind=client.table_field(table,key)
        if kind!=5:raise ValueError('NPC UI module is not loaded')
    target,old,kind=client.table_field(table,'on_rclick')
    _,release,release_kind=client.table_field(table,'release')
    if kind!=6 or release_kind!=6 or signature(client,release)!=RELEASE_SIGNATURE:
        raise ValueError('Native NPC release implementation differs from verified 946')
    if old==release:return dict(client_pid=client.pid,npc_right_click='native_release',changed=False)
    if signature(client,old)!=NOOP_SIGNATURE:raise ValueError('NPC right-click handler has already been customized')
    # Both closures are already reachable from this same class table. The
    # replacement is one checked 32-bit pointer, not an injected function.
    client.write_integer(target,old,release)
    return dict(client_pid=client.pid,npc_right_click='native_release',changed=True)


def connected_ports():
    """Loopback TCP peers only; TestClient independently verifies PID/path/hash."""
    import ctypes as ct
    from ctypes import wintypes as w
    import socket
    capacity=w.DWORD(256<<10);table=ct.create_string_buffer(capacity.value)
    result=ct.WinDLL('iphlpapi').GetExtendedTcpTable(table,ct.byref(capacity),False,2,5,0)
    if result:raise OSError(result,'Cannot read loopback peers')
    return [socket.ntohs(lp&0xFFFF) for state,local,lp,remote,rp,_ in
            (struct.unpack_from('<6I',table,4+24*i) for i in range(struct.unpack_from('<I',table)[0]))
            if state==5 and local==remote==0x0100007F and socket.ntohs(rp&0xFFFF)==19127]


def schedule_npc_ui(source_port,expected_pid):
    """Configure future game connections without delaying their socket thread."""
    from threading import Thread
    def worker():
        deadline=monotonic()+120
        while monotonic()<deadline:
            client=None
            try:
                client=TestClient(source_port)
                if client.pid!=expected_pid:return
                result=bind_npc_right_click(client)
                directory=Path(__file__).parent/'logs';directory.mkdir(exist_ok=True)
                temporary=directory/f'.npc-ui-{expected_pid}-{source_port}.json'
                temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
                temporary.replace(directory/'native-npc-right-click.json')
                return
            except (OSError,ValueError):pass
            finally:
                if client is not None:client.close()
            sleep(.5)
    Thread(target=worker,name='native-npc-ui-binding',daemon=True).start()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int)
    parser.add_argument('--wait',type=float,default=0);parser.add_argument('--receipt',type=Path)
    args=parser.parse_args();deadline=monotonic()+max(0,min(args.wait,300));last_error=None
    while True:
        for port in [args.port] if args.port else connected_ports():
            client=None
            try:
                client=TestClient(port);result=bind_npc_right_click(client)
                if args.receipt:args.receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
                print(json.dumps(result,ensure_ascii=False));return
            except (OSError,ValueError) as error:last_error=str(error)
            finally:
                if client is not None:client.close()
        if monotonic()>=deadline:raise RuntimeError(last_error or 'Verified local client has not connected')
        sleep(.5)


if __name__=='__main__':main()
