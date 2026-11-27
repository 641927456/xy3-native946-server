"""Native task auto-trace adapter, verified against revision 946 callback 2966."""
from native946_codec import packet


def task_trace_title(tag, title):
    # The stock tracker formats titles in yellow and uses this prefix for configured tags.
    return f'#G【{tag}】#Y{title}'


def npc_auto_trace(scene_id,x,y,npc_id,name):
    # c_task_auto_trace calls begin_auto_pilot_to_click(x*20,y*20,name,
    # scene_id,nil,npc_id). Coordinates on the wire are grid cells.
    if any(type(value) is not int for value in (scene_id,x,y,npc_id)):
        raise ValueError('Navigation identifiers and coordinates must be integers')
    if not 0<scene_id<=65535 or not 0<=x<=65535 or not 0<=y<=65535 or npc_id<=0:
        raise ValueError('Navigation target is outside the native range')
    if not isinstance(name,str) or not name or len(name.encode('gb18030'))>128:
        raise ValueError('Invalid navigation target name')
    return packet(2966,['number']*4+['string'],[x,y,scene_id,npc_id,name])
