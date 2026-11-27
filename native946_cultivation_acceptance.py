"""Disposable cultivation fixture on the existing token-scoped client socket."""

from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from time import monotonic


ACTIONS = {'cultivation_fixture_start', 'cultivation_fixture_next', 'cultivation_fixture_restore'}


def run(login, request):
    from local_accounts import LocalAccounts
    from native946_campaign import TRIALS, stage
    from native946_characters import Characters
    from native946_cultivation_quests import promotion
    from native946_gameplay import NativeGameplay
    from native946_role_systems import save

    action = request['action']
    game = login.gameplay
    binding = getattr(login, '_cultivation_fixture', None)
    if action == 'cultivation_fixture_start':
        if binding or game.battle or game.party_service.team(game.progression.cid):
            raise ValueError('请在单人场景中开启独立验收存档。')
        temp = TemporaryDirectory(prefix='native946-cultivation-', ignore_cleanup_errors=True)
        path = Path(temp.name) / 'fixture.sqlite3'
        user = 'culfixture'
        LocalAccounts(path).register(dict(username=user, password=token_urlsafe(8)[:9]))
        characters = Characters(path)
        character = characters.create(user, '修行验收', 702)
        actor_id = game.character['id']
        if character['id'] != actor_id:
            with characters.connect() as db:
                db.execute('UPDATE characters SET id=? WHERE id=?', (actor_id, character['id']))
            character = characters.enter(user, actor_id)
        fixture = NativeGameplay(character, characters, login.scene_packets)
        fixture.source_port = game.source_port
        fixture.outbound_wake = getattr(game, 'outbound_wake', None)
        with fixture.progression.transaction() as db:
            save(db, actor_id, 'school', dict(school=18))
        fixture.progression.known_items = set(game.progression.known_items)
        login._cultivation_fixture = (temp, login.characters, login.current_character, game)
        login.characters, login.current_character, login.gameplay = characters, character, fixture
        login._acceptance_reply = dict(isolated_fixture=True, actor_id=actor_id)
        return enter_fixture(login)

    if binding is None:
        raise ValueError('当前连接尚未开启独立修行验收。')
    if action == 'cultivation_fixture_restore':
        temp, characters, character, original = binding
        if game.battle:
            game._wild_battle.finish(False)
        original.progression.known_items = set(game.progression.known_items)
        original.progression._known_item_positions = {}
        login.characters, login.current_character, login.gameplay = characters, character, original
        del login._cultivation_fixture
        temp.cleanup()
        login._acceptance_reply = dict(original_session_restored=True, actor_id=original.character['id'])
        return enter_fixture(login)

    if game.battle:
        raise ValueError('请先完成当前验收战斗。')
    p = game.progression
    snapshot = p.snapshot()
    before = (snapshot['rebirth'], snapshot['ascension'], snapshot['sanctification'])
    current = stage(snapshot, True)
    if current:
        quest, node, row = current
        target, index = node['target'], row['node']
        level = promotion(quest['id'], before).level if index + 1 == len(quest['nodes']) else snapshot['level']
    else:
        quest = next((entry for key, entry in TRIALS.items() if promotion(key, before)), None)
        if not quest:
            login._acceptance_reply = dict(finished=True)
            return None
        target, index = quest['giver'], -1
        level = quest['metadata']['min_grade']
    if level > snapshot['level']:
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=? WHERE id=?', (level, p.cid))
            db.execute('UPDATE progression SET cor=?,mag=?,strength=?,speed=? WHERE character_id=?', (*([level]*4), p.cid))
        character = p.snapshot()
        p.set_vitals(character['hpmax'], character['mpmax'])
    login._acceptance_reply = dict(chapter=quest['id'], node=index, npc=target['npc'], scene=target['scene'],
                                   action='accept' if index < 0 else node['action'])
    route = dict(id='cultivation-fixture', to=target['scene'], arrival=target['arrival'])
    return game.change_scene(route, ('cultivation-fixture', target['scene'], target['npc'], monotonic()))


def enter_fixture(login):
    from native946_codec import packet

    # Reload the rendered world on the authenticated socket. The database on the
    # server and the existing login authorization remain scoped to this connection.
    return (packet(1183, ['string'], ['dlg_npc_chat']), *login.world_packets(login.current_character))
