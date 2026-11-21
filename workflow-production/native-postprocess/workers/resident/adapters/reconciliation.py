"""Request-service recovery only. Never invokes a generation backend.

Callbacks are trusted, bounded service ports: inspect_publication returns a
verified Receipt or None (None means UNKNOWN, never certified absence), cleanup
only removes the exact original attempt scratch. A stopped execution proof is
mandatory before a new FINALIZE lease is acquired. Unknowns keep their grants.
"""
from dataclasses import asdict
import json,secrets
from threading import Event
from ..contracts import Claim,Receipt

class ReconciliationSupervisor:
    def __init__(self,bridge,inspect_publication,cleanup,*,owner='resident-reconciler',poll_seconds=5,lease_seconds=60,model_authority=None,verify_recovery_publication=None,resume_publication=None):
        if not callable(inspect_publication) or not callable(cleanup) or poll_seconds<=0 or lease_seconds<10:
            raise ValueError('RECONCILIATION_CONFIG_INVALID')
        self.bridge,self.common=bridge,bridge.common
        self.inspect_publication,self.cleanup=inspect_publication,cleanup
        self.owner,self.poll_seconds,self.lease_seconds=owner,poll_seconds,lease_seconds
        self.stop=Event()
        self.model_authority=model_authority
        self.verify_recovery_publication=verify_recovery_publication
        self.resume_publication=resume_publication
        self.last_error=None
    def initialize_schema(self):
        with self.bridge.transaction():
            self.common.db.execute('''CREATE TABLE IF NOT EXISTS resident_reconciliations(
                grant_id TEXT PRIMARY KEY,state TEXT NOT NULL,next_at REAL NOT NULL,
                owner_token TEXT,owner_until REAL,recovery_json TEXT,receipt_json TEXT,
                checks INTEGER NOT NULL DEFAULT 0,last_code TEXT)''')
    def _claim(self,raw):
        value=json.loads(raw);value['input_refs']=tuple(value['input_refs']);return Claim(**value)
    def _owned(self,grant,token):
        row=self.common.db.execute('SELECT * FROM resident_reconciliations WHERE grant_id=?',(grant,)).fetchone()
        if not row or row['owner_token']!=token or row['owner_until']<=self.common.clock():raise ValueError('RECONCILIATION_OWNER_LOST')
        return row
    def _begin(self,grant):
        with self.bridge.transaction():
            now=self.common.clock()
            self.common.db.execute("INSERT OR IGNORE INTO resident_reconciliations(grant_id,state,next_at) VALUES(?,'pending',0)",(grant,))
            row=self.common.db.execute('SELECT * FROM resident_reconciliations WHERE grant_id=?',(grant,)).fetchone()
            if row['state']=='resolved' or row['next_at']>now or row['owner_until'] is not None and row['owner_until']>now:return None
            token=secrets.token_hex(16)
            self.common.db.execute('UPDATE resident_reconciliations SET owner_token=?,owner_until=?,checks=checks+1 WHERE grant_id=?',(token,now+self.lease_seconds,grant))
            return token
    def _pause(self,grant,token,code,resolved=False):
        with self.bridge.transaction():
            row=self._owned(grant,token)
            delay=min(300,self.poll_seconds*2**min(row['checks'],6))
            self.common.db.execute('UPDATE resident_reconciliations SET state=?,next_at=?,owner_token=NULL,owner_until=NULL,last_code=? WHERE grant_id=?',
                ('resolved' if resolved else 'pending',self.common.clock()+delay,code,grant))
    def candidates(self):
        with self.bridge.transaction():
            rows=self.common.db.execute('''SELECT g.* FROM resident_resource_grants g
                JOIN common_requests r ON r.request_id=g.request_id
                LEFT JOIN resident_publications p ON p.grant_id=g.grant_id
                LEFT JOIN resident_reconciliations x ON x.grant_id=g.grant_id
                WHERE (x.state IS NULL OR x.state!='resolved') AND
                (g.released=0 OR p.grant_id IS NOT NULL OR r.state IN ('sync_pending','blocked'))
                ORDER BY g.request_id,g.attempt_id''').fetchall()
            return [dict(r) for r in rows]
    def _same_execution(self,c,row):
        rec=self.common.db.execute('SELECT recovery_json FROM resident_reconciliations WHERE grant_id=?',(c.resource_grant,)).fetchone()
        recovery=json.loads(rec[0]) if rec and rec[0] else None
        if str(row['fence'])==c.fencing_token:
            lease=self.bridge._lease(c,allow_released=True)
            return not row['worker'] or (row['worker']==lease['workerId'] and row['nonce']==lease['nonce'])
        if recovery and row['fence']==recovery['fence']:
            return not row['worker'] or (row['worker']==recovery['workerId'] and row['nonce']==recovery['nonce'])
        return False
    def _eligible(self,c):
        row=self.common.row(c.request_id)
        if not self._same_execution(c,row):return False
        if row['state'] in ('completed','sync_pending','blocked'):return True
        if row['state'] not in ('processing','synchronizing'):return False
        # Do not race a healthy producer; explicit execution_unknown or expiry only.
        if row['lease_until'] is None or row['lease_until']<=self.common.clock():return True
        return self.common.db.execute("SELECT 1 FROM common_events WHERE request_id=? AND kind='execution_unknown' AND event_key=?",(c.request_id,'resident.execution-unknown.'+c.attempt_id)).fetchone() is not None
    def recover_models(self):
        authority=self.model_authority
        if authority is None:return
        from .model_residency import ModelLease
        with authority.transaction():
            rows=authority.common.db.execute('SELECT lease_json FROM resident_model_leases WHERE released=0 AND expires_at<=?',(authority.common.clock(),)).fetchall()
        for row in rows:
            lease=ModelLease(**json.loads(row[0]))
            try:authority.release(lease)  # Trusted unload proof is rechecked by authority.
            except Exception:pass  # Still charged; retry on the next tick, never force release.
    def tick(self):
        self.recover_models()
        results=[]
        for grant in self.candidates():
            c=self._claim(grant['claim_json'])
            with self.bridge.transaction():
                if not self._eligible(c):continue
            token=self._begin(c.resource_grant)
            if token is None:continue
            try:results.append((c.request_id,self._recover(c,token)))
            except Exception:
                # Preserve all data/capacity. Do not put secrets or raw errors in DB.
                try:self._pause(c.resource_grant,token,'RECOVERY_CHECK_FAILED')
                except ValueError:pass
                results.append((c.request_id,'unknown'))
        return results
    def _recover(self,c,token):
        if self.bridge.verify_stopped(c) is not True:
            self._pause(c.resource_grant,token,'EXECUTION_STILL_UNCONFIRMED');return 'unknown'
        with self.bridge.transaction():
            saved=self.common.db.execute('SELECT receipt_json FROM resident_publication_checkpoints WHERE grant_id=?',(c.resource_grant,)).fetchone()
        if saved:
            raw=json.loads(saved[0]);raw['asset_refs']=tuple(raw['asset_refs']);found=Receipt(**raw)
        else:found=self.inspect_publication(c)
        if found is None and self.resume_publication is not None:
            with self.bridge.transaction():
                self._owned(c.resource_grant,token)
                if not self._eligible(c) or self.bridge.verify_stopped(c) is not True:raise ValueError('RECOVERY_SOURCE_REPLACED')
                reservation=self.common.db.execute('SELECT publication_hash,state FROM resident_publications WHERE grant_id=?',(c.resource_grant,)).fetchone()
                replay_hash=reservation['publication_hash'] if reservation and reservation['state'] in ('prepared','committed') else None
            if replay_hash is not None:
                found=self.resume_publication(c,replay_hash)  # Same immutable operation, never generation.
        if not isinstance(found,Receipt):
            self._pause(c.resource_grant,token,'PUBLICATION_STILL_UNCONFIRMED');return 'unknown'
        found.validate(c)
        proof=(self.verify_recovery_publication(c,found) if not saved and self.verify_recovery_publication is not None
               else self.bridge.publication_proof(c,found))
        if (not isinstance(proof,dict) or proof.get('publication',{}).get('verified') is not True
                or proof.get('publication',{}).get('requestId')!=c.request_id
                or proof.get('sourceReceipt',{}).get('requestId')!=c.request_id):raise ValueError('RECOVERY_PUBLICATION_UNVERIFIED')
        with self.bridge.transaction():
            self._owned(c.resource_grant,token)
            row=self.common.row(c.request_id)
            if not self._eligible(c):raise ValueError('RECOVERY_SOURCE_REPLACED')
            if self.bridge.verify_stopped(c) is not True:raise ValueError('RECOVERY_STOP_PROOF_CHANGED')
            reservation=self.common.db.execute('SELECT * FROM resident_publications WHERE grant_id=?',(c.resource_grant,)).fetchone()
            if reservation:
                raw=proof.get('reservedReceipt')
                if reservation['state']=='prepared':self.bridge.finish_publication(c,reservation['publication_hash'],raw)
                elif raw!=json.loads(reservation['receipt_json']):raise ValueError('RECOVERY_RECEIPT_MISMATCH')
            saved=self.common.db.execute('SELECT receipt_json FROM resident_reconciliations WHERE grant_id=?',(c.resource_grant,)).fetchone()[0]
            canonical=json.dumps(asdict(found),sort_keys=True)
            if saved is not None and saved!=canonical:raise ValueError('RECOVERY_RECEIPT_CHANGED')
            self.common.db.execute('UPDATE resident_reconciliations SET receipt_json=? WHERE grant_id=?',(canonical,c.resource_grant))
            # Recovery-discovered proof also must survive cleanup/crash. The original
            # execution lease may be expired; source fence+owner+PDS proof were
            # verified above by this service-only path, never a worker shortcut.
            self.bridge._save_checkpoint(c,found,proof)
            if row['state']!='completed':
                # New finalization fence, not a forged old timestamp or generation retry.
                if row['state'] not in ('processing','synchronizing','blocked','sync_pending'):
                    raise ValueError('RECOVERY_STATE_UNSUPPORTED')
                recovery=self.common.claim(c.request_id,self.owner,'resident.reconcile.claim.'+c.resource_grant+'.'+str(row['revision']),
                    stage='finalize',lease_seconds=self.lease_seconds,states=(row['state'],))
                self.common.db.execute('UPDATE resident_reconciliations SET recovery_json=? WHERE grant_id=?',(json.dumps(recovery),c.resource_grant))
            else:recovery=None
        try:clean=self.cleanup(c) is True
        except Exception:clean=False
        with self.bridge.transaction():
            self._owned(c.resource_grant,token)
            # No late observer may release a slot after the source execution was replaced.
            if self.bridge.verify_stopped(c) is not True:raise ValueError('RECOVERY_STOP_PROOF_CHANGED')
            if recovery:
                self.common.transition(c.request_id,'resident.reconcile.finish.'+c.resource_grant+'.'+str(recovery['fence']),
                    'completed' if clean else 'sync_pending',stage='finalize',lease=recovery,
                    receipt=proof['sourceReceipt'],publication=proof['publication'],
                    details={'reconciled':True,'sourceAttemptId':c.attempt_id,'sourceFence':c.fencing_token,'cleanupDone':clean,'generationCalls':0})
            self.bridge.release_resource(c)
            self._pause(c.resource_grant,token,'RECOVERED' if clean else 'CLEANUP_PENDING',resolved=clean)
        return 'completed' if clean else 'cleanup_pending'
    def serve(self):
        while not self.stop.is_set():
            try:
                self.tick();self.last_error=None
            except Exception:
                self.last_error='RECONCILIATION_TICK_FAILED'  # Retry reads; never release on errors.
            self.stop.wait(self.poll_seconds)
