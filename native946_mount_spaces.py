"""Local private destinations for the original mount shortcut buttons."""

from time import monotonic

from native946_mall_mounts import LOCAL_RULES


SPACES = LOCAL_RULES['spaces']
SKILL_SPACES = {73: 'guild', 213: 'home'}
REFERENCE_WIRE_VERSION = 1


def space_kind(scene):
    return next((kind for kind, row in SPACES.items()
                 if row['scene_range'][0] <= scene <= row['scene_range'][1]), None)


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS native_mount_spaces (
        scene INTEGER PRIMARY KEY, kind TEXT NOT NULL, owner INTEGER NOT NULL,
        UNIQUE(kind,owner))''')
    db.execute('''CREATE TABLE IF NOT EXISTS native_mount_return (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        scene INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL)''')


def owner_for(db, kind, cid):
    if kind == 'home':
        return cid
    from native946_guilds import _membership
    member = _membership(db, cid)
    return member['guild_id'] if member else None


def may_enter(db, scene, kind, cid):
    row = db.execute('SELECT owner FROM native_mount_spaces WHERE scene=? AND kind=?',(scene,kind)).fetchone()
    return row is not None and row['owner'] == owner_for(db,kind,cid)


def return_route(db, cid):
    from native946_map import load_map
    from native946_world import world

    saved = db.execute('SELECT scene,x,y FROM native_mount_return WHERE character_id=?',(cid,)).fetchone()
    if saved and saved['scene'] in world().scenes and load_map(saved['scene']).walkable(saved['x'],saved['y']):
        return saved['scene'], (saved['x'],saved['y'])
    fallback = LOCAL_RULES['return_fallback']
    return fallback['scene'], fallback['arrival']


def transfer(game, kind):
    from native946_world import INSTANCE_SCENES, world

    current = game.character['map_id']
    previous = space_kind(current)
    cid = game.progression.cid
    if previous == kind:
        return exit_space(game, (current,kind,'mount'))
    if not previous and (current in INSTANCE_SCENES or current not in world().scenes):
        raise ValueError('请先离开当前活动场景。')
    if game.party_service.team(cid):
        raise ValueError('请先离开队伍再使用帮派或家园传送。')
    with game.progression.transaction() as db:
        schema(db)
        owner = owner_for(db,kind,cid)
        if owner is None:
            raise ValueError('请先加入帮派。')
        rule = SPACES[kind]
        row = db.execute('SELECT scene FROM native_mount_spaces WHERE kind=? AND owner=?',(kind,owner)).fetchone()
        if row:
            target = row['scene']
        else:
            first, last = rule['scene_range']
            target = db.execute('SELECT coalesce(max(scene),?)+1 FROM native_mount_spaces WHERE kind=?',
                                (first-1,kind)).fetchone()[0]
            if target > last or target in world().scene_catalog:
                raise ValueError('本机驻地暂时没有空余场景。')
            db.execute('INSERT INTO native_mount_spaces VALUES(?,?,?)',(target,kind,owner))
        if previous is None:
            db.execute('''INSERT INTO native_mount_return VALUES(?,?,?,?) ON CONFLICT(character_id)
                DO UPDATE SET scene=excluded.scene,x=excluded.x,y=excluded.y''',
                (cid,current,game.character['x'],game.character['y']))
        return game.change_scene(dict(to=target,arrival=rule['arrival'],id=f'mount_{kind}'),(current,target,'mount'))


def exit_portal(scene):
    kind = space_kind(scene)
    if kind:
        return {'id':'mount_space_exit','source':SPACES[kind]['exit'],'half_extent':2}


def exit_space(game, request_key):
    with game.progression.transaction(write=False) as db:
        target, point = return_route(db,game.progression.cid)
    return game.change_scene(dict(to=target,arrival=point,id='mount_space_exit'), request_key)


def check_guild_access(game):
    character = game.character
    scene = character['map_id']
    now = monotonic()
    if space_kind(scene) != 'guild' or now < getattr(game,'_guild_visit_checked_at',0):
        return ()
    with game.progression.transaction(write=False) as db:
        allowed = may_enter(db,scene,'guild',character['id'])
    if allowed:
        game._guild_visit_checked_at = now + 3
        return ()
    before = (scene,character['x'],character['y'])
    result = exit_space(game,(scene,'guild_membership','mount'))
    game.party_service.publish_relocation(game,before)
    return result


def check_login(characters, character):
    from native946_storage import transaction

    scene = character['map_id']
    kind = space_kind(scene)
    if kind is None:
        return
    cid = character['id']
    with transaction(characters.database,write=True) as db:
        schema(db)
        if may_enter(db,scene,kind,cid):
            return
        target,(x,y) = return_route(db,cid)
        revision = characters.change_scene(character['owner'],cid,scene,target,x,y,character.get('position_revision',0))
        character.update(map_id=target,x=x,y=y,position_revision=revision)
