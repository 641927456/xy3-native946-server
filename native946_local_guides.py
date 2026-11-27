"""Playable local quest routes for the mounted and proficiency guides."""
from native946_codec import packet, text_notice
from native946_navigation import task_trace_title
from native946_role_systems import save, state
from native946_world import world


MOUNT_QID = 929992
PRACTICE_QID = 929993
RULE = {'source':'本服规则', 'mount_level':60, 'mount_index':11, 'skill':6104,
        'proficiency':7000, 'skill_reward':6029, 'skill_reward_count':5}
CONTACTS = (
    (2089,3436,'打听汗血宝马'), (2001,1011,'打听宝马的去向'),
    (2006,2523,'请韩干鉴别宝马'), (2015,2404,'请增长天王查验'),
    (2089,3436,'领取汗血宝马'),
)
CLOSE = packet(1183, ['string'], ['dlg_npc_chat'])


def status(db, cid):
    return state(db,cid,'local_quest_guides',dict(mount_step=0,practice_claimed=False))


def details(snapshot):
    guides = snapshot.get('local_guides', {})
    result = {}
    step = guides.get('mount_step',0)
    if snapshot['level'] >= RULE['mount_level'] and step < len(CONTACTS):
        scene,nid,label = CONTACTS[step]
        npc = world().npc(scene,nid)
        name = npc['name']
        text = (f'去长安第一楼找#u#R{name}#n打听消息。' if step==0 else
                f'前往#R{world().scene(scene)["scene_name"]}#n找#u#R{name}#n办理「{label}」。')
        result[MOUNT_QID] = dict(title=task_trace_title('坐骑','汗血宝马'),text=text,
                                 description='本服规则。依次拜访联系人，领取原生汗血宝马。',
                                 scene=scene,npc=nid,x=npc['x']//20,y=npc['y']//20)
    skill = RULE['skill']
    if skill in snapshot['skills'] and not guides.get('practice_claimed'):
        from native946_school import PRIMARY_MASTERS, base_school

        if target := PRIMARY_MASTERS.get(base_school(snapshot['school'])):
            scene,nid = target
            npc = world().npc(scene,nid)
            text = f'将技能#R移山填海#n熟练度提升至#Y{RULE["proficiency"]}#n，获得惊喜奖励。'
            if snapshot['skills'][skill] >= RULE['proficiency']:
                text = f'熟练度已达#Y{RULE["proficiency"]}#n，找#u#R{npc["name"]}#n领取。'
            result[PRACTICE_QID] = dict(title='升级送大礼',text=text,
                                        description=f"本服规则。达标后找本门师父领{RULE['skill_reward_count']}个三千年蟠桃。",
                                        scene=scene,npc=nid,x=npc['x']//20,y=npc['y']//20)
    return result


def mount_option(game, location):
    if location not in {(scene,npc) for scene,npc,_ in CONTACTS}:
        return None
    snapshot = game.progression.snapshot()
    step = snapshot['local_guides']['mount_step']
    if snapshot['level'] < RULE['mount_level'] or step >= len(CONTACTS) or CONTACTS[step][:2] != location:
        return None
    return f'{CONTACTS[step][2]}（本服）',dict(kind='local_mount_guide',step=step)


def advance_mount(game, nid, expected):
    from native946_mall_uses import MallUses
    from native946_npc_services import NpcServices

    npc = NpcServices(game).validate(nid)
    p = game.progression
    with p.transaction() as db:
        progress = status(db,p.cid)
        step = progress['mount_step']
        if (step != expected or step >= len(CONTACTS) or CONTACTS[step][:2] != (npc['scene_id'],nid) or
                db.execute('SELECT level FROM characters WHERE id=?',(p.cid,)).fetchone()[0] < RULE['mount_level']):
            raise ValueError('当前汗血宝马任务步骤不可完成。')
        progress['mount_step'] += 1
        save(db,p.cid,'local_quest_guides',progress)
        finished = progress['mount_step'] == len(CONTACTS)
        if finished:
            db.execute('INSERT OR IGNORE INTO cosmetic_features VALUES(?,?,0,0)',
                       (p.cid,'mount:'+str(RULE['mount_index'])))
    p._cached_snapshot = None
    game.dialog_open = False
    return game.defer_packets(lambda:(CLOSE,*p.task_packets(),*((MallUses(game).mounts(),) if finished else ()),
                                      text_notice('本服规则。已领取汗血宝马。' if finished else '本服规则。坐骑任务已更新。')))


def practice_ready(snapshot):
    return (snapshot['skills'].get(RULE['skill'],0) >= RULE['proficiency'] and
            not snapshot.get('local_guides',{}).get('practice_claimed'))


def claim_practice(game, npc):
    from native946_inventory_features import give
    from native946_npc_quests import Quests

    Quests(game).validate_giver(npc,'school')
    p = game.progression
    with p.transaction() as db:
        if not practice_ready(p._snapshot(db)):
            raise ValueError('熟练度未达要求或奖励已领取。')
        give(db,p,RULE['skill_reward'],RULE['skill_reward_count'])
        progress = status(db,p.cid)
        progress['practice_claimed'] = True
        save(db,p.cid,'local_quest_guides',progress)
    p._cached_snapshot = None
    game.dialog_open = False
    return game.defer_packets(lambda:(CLOSE,*game.sync_progression(),text_notice('本服规则。已领取升级送大礼。')))
