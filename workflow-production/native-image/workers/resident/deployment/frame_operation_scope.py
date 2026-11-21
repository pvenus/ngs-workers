"""Read-only operation scope. OneStop owns orchestration, never the child lease."""
import json,re
from .request_selector import digest


def frame_operation_scope(common, row, body):
    rid = row['request_id']
    if row['parent_id'] is not None or common.db.execute('SELECT 1 FROM common_requests WHERE parent_id=?', (rid,)).fetchone():
        raise ValueError('RESIDENT_FRAME_STANDALONE_REQUIRED')
    if not common.db.execute("SELECT 1 FROM sqlite_master WHERE name='npc_one_stop_attempts'").fetchone():
        return {'kind': 'standalone', 'requestId': rid}
    matches = []
    candidates = common.db.execute('SELECT * FROM npc_one_stop_attempts WHERE child_id=? OR child_key=?', (rid, row['idempotency_key'])).fetchall()
    for attempt in candidates:
        parent = common.db.execute('SELECT * FROM npc_one_stop_requests WHERE request_id=?', (attempt['parent_id'],)).fetchone()
        if parent is None:
            raise ValueError('RESIDENT_FRAME_ONESTOP_PARENT_REQUIRED')
        expected = 'frames.req.' + digest([parent['actor'], attempt['child_key']])[:40]
        matches.append((attempt, parent, expected))
    if not matches: return {'kind': 'standalone', 'requestId': rid}
    if len(matches) != 1: raise ValueError('RESIDENT_FRAME_ONESTOP_AMBIGUOUS')
    a, parent, expected = matches[0]
    stage = a['stage_id']
    if row['idempotency_key'] != a['child_key'] or (stage not in ('frames_walk', 'frames_run') and re.fullmatch(r'frames_combat_(0|[1-9][0-9]*)',stage) is None) or a['channel'] != 'frames' or a['child_id'] != rid or expected != rid or a['child_key'] != a['parent_id'] + '.' + stage + '.' + str(a['attempt']):
        raise ValueError('RESIDENT_FRAME_ONESTOP_CHILD_BINDING')
    newest = common.db.execute('SELECT MAX(attempt) FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=?', (a['parent_id'], stage)).fetchone()[0]
    if newest != a['attempt']: raise ValueError('RESIDENT_FRAME_ONESTOP_SUPERSEDED')
    request = json.loads(parent['body']); p = common.row(a['parent_id'])
    if digest(request) != parent['input_hash'] or p['request_type'] != 'npc.workflow' or p['requester'] != parent['actor'] or row['requester'] != parent['actor'] or p['project_id'] != row['project_id']:
        raise ValueError('RESIDENT_FRAME_ONESTOP_OWNER')
    pose,preset='idle',stage.split('_',1)[1]
    if stage.startswith('frames_combat_'):
        index=int(stage.rsplit('_',1)[1]);presets=request.get('combatMotionPresets')
        if request.get('includeCombat') is not True or not isinstance(presets,list) or index>=len(presets):
            raise ValueError('RESIDENT_FRAME_ONESTOP_COMBAT_PRESET')
        pose,preset='combat',presets[index]
    expected_body = {'schemaVersion':'ngs.npc-animation-frames-request.v1', 'projectId':'binari', 'npcId':request['character']['npcId'], 'posePreset':pose, 'intent':request['intent'], 'presetId':preset}
    if body != expected_body: raise ValueError('RESIDENT_FRAME_ONESTOP_BODY')
    saved = common.db.execute('SELECT * FROM npc_one_stop_plans WHERE parent_id=?', (a['parent_id'],)).fetchone()
    if saved is None: raise ValueError('RESIDENT_FRAME_ONESTOP_PLAN')
    plan = json.loads(saved['plan'])
    from local.services.npc_one_stop.plan import validate
    validate(plan, request)
    if digest(plan) != saved['plan_hash']: raise ValueError('RESIDENT_FRAME_ONESTOP_PLAN')
    selected = next(s for s in plan['stages'] if s['id'] == stage)
    motion = 'animation_' + stage.split('_',1)[1]
    if motion not in selected['dependsOn']: raise ValueError('RESIDENT_FRAME_ONESTOP_DEPENDENCY')
    for dependency in selected['dependsOn']:
        dep = common.db.execute('SELECT * FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=? ORDER BY attempt DESC LIMIT 1', (a['parent_id'], dependency)).fetchone()
        receipt = json.loads(dep['receipt']) if dep and dep['receipt'] else {}
        if not dep or receipt.get('verified') is not True or receipt.get('requestId') != dep['child_id'] or receipt.get('stageId') != dependency or common.row(dep['child_id'])['state'] != 'completed':
            raise ValueError('RESIDENT_FRAME_ONESTOP_DEPENDENCY')
    motion_dep = common.db.execute('SELECT * FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=? ORDER BY attempt DESC LIMIT 1', (a['parent_id'], motion)).fetchone()
    original = common.db.execute('SELECT actor,body,publication FROM animation_intake_requests WHERE id=?', (motion_dep['child_id'],)).fetchone()
    publication = json.loads(original['publication']) if original and original['publication'] else {}
    animation_body = json.loads(original['body']) if original else {}
    if original is None or original['actor'] != parent['actor'] or publication.get('verified') is not True or publication.get('requestId') != motion_dep['child_id'] or any(animation_body.get(k) != body[k] for k in ('npcId','posePreset','presetId')):
        raise ValueError('RESIDENT_FRAME_ONESTOP_ANIMATION_PUBLICATION')
    return {'kind':'one-stop-frame-child', 'requestId':rid, 'orchestratorId':a['parent_id'], 'stageId':stage, 'attempt':a['attempt']}
