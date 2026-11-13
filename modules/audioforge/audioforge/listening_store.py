"""Additive, transactional human listening records; never updates songs or tasks."""
from __future__ import annotations
import copy
import json
import math
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from .gateway_catalog import now, jt, safe_file, sha

LIMITS = dict(brief_fit=20, development=20, roles_texture=15, coherence=15,
              game_fit=20, perceptual_clarity=10)

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Candidate(StrictModel):
    generation_task_id: UUID
    song_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    artifact_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    audio_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

class GroupRequest(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    comparison_task_id: UUID
    human_listening_task_id: UUID
    candidates: list[Candidate] = Field(min_length=1, max_length=100)
    expected_revision: StrictInt = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)

class Criterion(StrictModel):
    score: float | None = None
    evidence: StrictStr | None = Field(default=None, max_length=10000)
    confidence: Literal['low', 'medium', 'high'] | None = None
    uncertainty: StrictStr | None = Field(default=None, max_length=10000)

    @field_validator('score', mode='before')
    @classmethod
    def numeric(cls, v):
        if v is not None and (type(v) not in (int, float) or not 0 <= v <= 20 or not math.isfinite(v) or v * 2 != int(v * 2)):
            raise ValueError('score must be a finite number in 0.5 increments')
        return v

class ReviewPatch(StrictModel):
    criteria: dict[str, Criterion] = Field(default_factory=dict)
    comment: StrictStr | None = Field(default=None, max_length=10000)
    listening_declaration: Literal['unknown', 'partial', 'full_self_reported'] | None = None
    playback_context: StrictStr | None = Field(default=None, max_length=10000)

    @field_validator('criteria')
    @classmethod
    def criteria_valid(cls, v):
        for k, c in v.items():
            if k not in LIMITS or (c.score is not None and c.score > LIMITS[k]):
                raise ValueError('unknown criterion or score above criterion maximum')
        return v

class Mutation(StrictModel):
    expected_group_revision: StrictInt = Field(ge=1)
    artifact_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    audio_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_revision: StrictInt = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    action: Literal['approve', 'reject', 'restore'] | None = None
    review: ReviewPatch | None = None
    reason: StrictStr | None = Field(default=None, max_length=10000)

class ListeningError(Exception):
    def __init__(self, status, detail, current=None):
        self.status, self.detail, self.current = status, detail, current

SCHEMA = '''
CREATE TABLE IF NOT EXISTS listening_schema(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS listening_groups(group_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS listening_group_history(group_id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(group_id,revision));
CREATE TABLE IF NOT EXISTS listening_current(group_id TEXT NOT NULL, song_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(group_id,song_id));
CREATE TABLE IF NOT EXISTS listening_events(event_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, song_id TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS listening_events_lookup ON listening_events(group_id,song_id);
CREATE TABLE IF NOT EXISTS listening_requests(scope TEXT NOT NULL, request_key TEXT NOT NULL, payload TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(scope,request_key));
INSERT OR IGNORE INTO listening_schema VALUES(1);
'''

class ListeningStore:
    def __init__(self, catalog, environment='production'):
        self.catalog, self.environment = catalog, environment
        # SQLite online backup captures a coherent database even with other services running.
        with closing(catalog.connect()) as db:
            installed = db.execute("SELECT 1 FROM sqlite_master WHERE name='listening_schema'").fetchone()
            if not installed:
                backup = Path(str(catalog.db_path) + '.before-listening-v1.sqlite3')
                if backup.exists():
                    backup = backup.with_name(backup.name + '.' + uuid.uuid4().hex)
                with closing(sqlite3.connect(backup)) as target:
                    db.backup(target)
            db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')

    def _group(self, db, gid):
        row = db.execute('SELECT body FROM listening_groups WHERE group_id=?', (gid,)).fetchone()
        if row is None:
            raise ListeningError(404, 'comparison group not found')
        return json.loads(row[0])

    def _identity(self, db, candidate):
        row = db.execute('SELECT artifact_id,file_sha256,file_name FROM songs WHERE song_id=?', (candidate['song_id'],)).fetchone()
        if row is None or row['artifact_id'] != candidate['artifact_id'] or (row['file_sha256'] is not None and row['file_sha256'] != candidate['audio_sha256']):
            return False
        try:
            path = safe_file(self.catalog.artifact_dir, row['artifact_id'], row['file_name'])
            return path.is_file() and sha(path) == candidate['audio_sha256']
        except (ValueError, OSError):
            return False

    def _replay(self, db, scope, key, payload):
        row = db.execute('SELECT payload,response FROM listening_requests WHERE scope=? AND request_key=?', (scope, key)).fetchone()
        if row:
            if row['payload'] != payload:
                raise ListeningError(409, 'idempotency key reused with a different payload')
            return json.loads(row['response'])

    def _remember(self, db, scope, key, payload, response):
        db.execute('INSERT INTO listening_requests VALUES(?,?,?,?)', (scope, key, payload, jt(response)))

    def save_group(self, request: GroupRequest, gid=None):
        data = request.model_dump(mode='json')
        payload = json.dumps(data, sort_keys=True, allow_nan=False)
        scope = 'group:' + (gid or 'create')
        with self.catalog.lock, closing(self.catalog.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            replay = self._replay(db, scope, request.idempotency_key, payload)
            if replay is not None:
                return replay
            previous = self._group(db, gid) if gid else None
            if request.expected_revision != (previous['group_revision'] if previous else 0):
                raise ListeningError(409, 'group revision conflict', previous)
            candidates = data['candidates']
            if any(len({c[k] for c in candidates}) != len(candidates) for k in ('song_id', 'artifact_id', 'generation_task_id')):
                raise ListeningError(422, 'candidates must have distinct song, artifact and generation task IDs')
            for c in candidates:
                if not self._identity(db, c):
                    raise ListeningError(409, 'candidate does not match current catalog and audio SHA', c)
            group = dict(group_id=gid or uuid.uuid4().hex, group_revision=request.expected_revision + 1,
                         title=request.title, comparison_task_id=data['comparison_task_id'],
                         human_listening_task_id=data['human_listening_task_id'], candidates=candidates,
                         comparison_ready=len(candidates) >= 3, updated_at=now())
            db.execute('INSERT OR REPLACE INTO listening_groups VALUES(?,?,?)', (group['group_id'], group['group_revision'], jt(group)))
            db.execute('INSERT INTO listening_group_history VALUES(?,?,?)', (group['group_id'], group['group_revision'], jt(group)))
            self._remember(db, scope, request.idempotency_key, payload, group)
            return group

    def groups(self):
        with closing(self.catalog.connect()) as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM listening_groups ORDER BY group_id')]

    def group(self, gid):
        with closing(self.catalog.connect()) as db:
            return self._group(db, gid)

    def _state(self, db, group, candidate):
        row = db.execute('SELECT body FROM listening_current WHERE group_id=? AND song_id=?', (group['group_id'], candidate['song_id'])).fetchone()
        if row:
            state = json.loads(row[0])
        else:
            state = dict(group_id=group['group_id'], group_revision=group['group_revision'], **candidate,
                         revision=0, review_decision='undecided', decision_event=None, source_event_id=None,
                         human_review=dict(schema_version='human_review/1.0', rubric_version='human-review-1.0',
                             criteria={k: dict(score=None, evidence=None, confidence=None, uncertainty=None) for k in LIMITS},
                             comment=None, listening_declaration='unknown', playback_context=None, review_revision=0,
                             updated_at=None, updated_by=None, source_event_id=None))
        state['stale'] = (state['group_revision'] != group['group_revision'] or
                          any(state[k] != candidate[k] for k in ('artifact_id', 'audio_sha256', 'generation_task_id')) or
                          not self._identity(db, candidate))
        h = state['human_review']
        h.update({k: state[k] for k in ('group_id', 'group_revision', 'song_id', 'artifact_id', 'audio_sha256')})
        scores = [c['score'] for c in h['criteria'].values()]
        h['total_score'] = sum(scores) if all(s is not None for s in scores) else None
        state['user_evaluated'] = not state['stale'] and bool(h['comment'] or any(v is not None and v != '' for c in h['criteria'].values() for v in c.values()))
        event = state.get('decision_event') or {}
        state['valid_approval'] = bool(not state['stale'] and state['review_decision'] == 'approved' and
                                       event.get('environment') == 'production' and event.get('actor_kind') == 'local_user' and
                                       event.get('source') == 'review_ui' and event.get('explicit_user_action') is True)
        state['effective_review_decision'] = 'undecided' if state['stale'] else state['review_decision']
        return state

    def _candidate(self, group, sid):
        candidate = next((c for c in group['candidates'] if c['song_id'] == sid), None)
        if candidate is None:
            raise ListeningError(404, 'song is not a member of this comparison group')
        return candidate

    def get(self, gid, sid, history=False):
        with closing(self.catalog.connect()) as db:
            group = self._group(db, gid)
            try:
                candidate = self._candidate(group, sid)
            except ListeningError:
                row = db.execute('SELECT body FROM listening_current WHERE group_id=? AND song_id=?', (gid, sid)).fetchone()
                if not history or row is None:
                    raise
                state = json.loads(row[0])
                state.update(stale=True, valid_approval=False, user_evaluated=False, effective_review_decision='undecided', removed_from_group=True)
            else:
                state = self._state(db, group, candidate)
            if history:
                state['history'] = [json.loads(r[0]) for r in db.execute('SELECT body FROM listening_events WHERE group_id=? AND song_id=? ORDER BY rowid', (gid, sid))]
            return state

    def tracks(self, gid, decision_filter='visible', include_archived=False):
        if decision_filter not in ('visible', 'rejected', 'all'):
            raise ListeningError(422, 'invalid decision filter')
        with closing(self.catalog.connect()) as db:
            group = self._group(db, gid)
            states = [self._state(db, group, c) for c in group['candidates']]
            rows = {r['song_id']: bool(r['archived']) for r in db.execute('SELECT song_id,archived FROM songs')}
            states = [s for s in states if include_archived or not rows.get(s['song_id'], True)]
            visible = [s for s in states if s['effective_review_decision'] != 'rejected']
            rejected = [s for s in states if s['effective_review_decision'] == 'rejected']
            selected = {'visible': visible, 'rejected': rejected, 'all': states}[decision_filter]
            return dict(group=group, tracks=selected, count=len(selected), counts=dict(all=len(states), visible=len(visible), rejected=len(rejected)))

    def mutate(self, gid, sid, request: Mutation, *, trusted_ui=False, source_request_id=None):
        if not trusted_ui or not source_request_id:
            raise ListeningError(403, 'human input requires a verified local review UI session; MCP agent writes are not human actions')
        if request.action is None and request.review is None:
            raise ListeningError(422, 'action or review is required')
        data = request.model_dump(mode='json', exclude_unset=True)
        payload = json.dumps(data, sort_keys=True, allow_nan=False)
        scope = 'song:' + gid + ':' + sid
        with self.catalog.lock, closing(self.catalog.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            replay = self._replay(db, scope, request.idempotency_key, payload)
            if replay is not None:
                return replay
            group = self._group(db, gid)
            candidate = self._candidate(group, sid)
            state = self._state(db, group, candidate)
            if (request.expected_group_revision != group['group_revision'] or
                request.artifact_id != candidate['artifact_id'] or request.audio_sha256 != candidate['audio_sha256'] or
                not self._identity(db, candidate)):
                raise ListeningError(409, 'group or audio identity conflict', state)
            if request.expected_revision != state['revision']:
                raise ListeningError(409, 'revision conflict', state)
            before = copy.deepcopy(state)
            # A changed group/identity starts fresh content; old content remains in event history.
            if state['stale']:
                state['human_review'] = dict(schema_version='human_review/1.0', rubric_version='human-review-1.0',
                    criteria={k: dict(score=None, evidence=None, confidence=None, uncertainty=None) for k in LIMITS},
                    comment=None, listening_declaration='unknown', playback_context=None,
                    review_revision=state['human_review']['review_revision'], updated_at=None, updated_by=None, source_event_id=None)
                state.update(candidate, group_revision=group['group_revision'], review_decision='undecided', decision_event=None)
            if request.review is not None:
                patch = request.review.model_dump(exclude_unset=True)
                for key, value in patch.pop('criteria', {}).items():
                    state['human_review']['criteria'][key].update(value)
                state['human_review'].update(patch)
                if state['human_review']['listening_declaration'] is None:
                    state['human_review']['listening_declaration'] = 'unknown'
            new_decision = {'approve': 'approved', 'reject': 'rejected', 'restore': 'undecided'}.get(request.action, state['review_decision'])
            review_changed = state['human_review'] != before['human_review']
            decision_changed = new_decision != before['review_decision'] or before['stale']
            if not review_changed and not decision_changed:
                self._remember(db, scope, request.idempotency_key, payload, state)
                return state
            event_id, stamp = uuid.uuid4().hex, now()
            state.update(revision=before['revision'] + 1, review_decision=new_decision, source_event_id=event_id, stale=False)
            if review_changed:
                state['human_review'].update(review_revision=before['human_review']['review_revision'] + 1,
                    updated_at=stamp, updated_by='local-review-user', source_event_id=event_id)
            # Persist the same complete evaluation snapshot returned by GET, including derived totals.
            h = state['human_review']
            h.update({k: state[k] for k in ('group_id', 'group_revision', 'song_id', 'artifact_id', 'audio_sha256')})
            scores = [c['score'] for c in h['criteria'].values()]
            h['total_score'] = sum(scores) if all(v is not None for v in scores) else None
            event = dict(event_id=event_id, received_at=stamp, actor_id='local-review-user', actor_kind='local_user',
                         source='review_ui', source_request_id=source_request_id, environment=self.environment,
                         explicit_user_action=True, group_id=gid, group_revision=group['group_revision'], **candidate,
                         revision_before=before['revision'], revision_after=state['revision'],
                         previous_state=before['review_decision'], new_state=new_decision,
                         supersedes_event_id=before['source_event_id'], reason=request.reason, action=request.action,
                         review_patch=data.get('review'), previous_human_review=before['human_review'], human_review=state['human_review'])
            if decision_changed:
                state['decision_event'] = {k: v for k, v in event.items() if k not in ('previous_human_review', 'human_review', 'review_patch')}
            db.execute('INSERT OR REPLACE INTO listening_current VALUES(?,?,?)', (gid, sid, jt(state)))
            db.execute('INSERT INTO listening_events VALUES(?,?,?,?)', (event_id, gid, sid, jt(event)))
            result = self._state(db, group, candidate)
            self._remember(db, scope, request.idempotency_key, payload, result)
            return result
