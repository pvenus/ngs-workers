"""Read only original, completed formal OneStop retry evidence. No retry creation."""
import json
from .request_selector import digest


def approved_retries(common,parent):
    tables={r[0]for r in common.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'npc_one_stop_retry_requests'not in tables:return {}
    result={}
    rows=common.db.execute('SELECT * FROM npc_one_stop_retry_requests WHERE parent_id=? ORDER BY rowid',(parent['requestId'],)).fetchall()
    for row in rows:
        request=dict(common.row(row['id']))
        if request['state']!='completed':continue
        if (request['request_type']!='npc.retry-one-stop' or request['source']!='npc-one-stop-retry'
            or request['requester']!=parent['actor'] or row['actor']!=parent['actor']
            or request['idempotency_key']!=row['command_key']):
            raise ValueError('WORKFLOW_RETRY_REQUEST_CHANGED')
        proof=json.loads(row['proof'])
        if (proof.get('parentRequestId')!=parent['requestId'] or proof.get('actor')!=parent['actor']
            or proof.get('inputHash')!=parent['requestHash'] or proof.get('planHash')!=parent['planHash']):
            raise ValueError('WORKFLOW_RETRY_PARENT_CHANGED')
        refs=common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(row['id'],)).fetchall()
        if (len(refs)!=1 or refs[0]['domain']!='npc-one-stop-retry' or refs[0]['schema_version']!='ngs.npc-one-stop-retry.v1'
            or refs[0]['reference_id']!=row['id'] or refs[0]['input_hash']!=digest(proof)):
            raise ValueError('WORKFLOW_RETRY_REF_CHANGED')
        journal=common.db.execute('SELECT * FROM npc_one_stop_retries WHERE parent_id=? AND command_key=?',(parent['requestId'],row['command_key'])).fetchone()
        if journal is None or journal['stage_id']!=proof.get('stageId'):raise ValueError('WORKFLOW_RETRY_JOURNAL_REQUIRED')
        receipt=json.loads(request['publication_receipt']) if request['publication_receipt'] else None
        journal_hash=digest(dict(journal))
        expected={'requestId':row['id'],'parentRequestId':parent['requestId'],
                  'verified':True,'modelCalls':0,'retryJournalHash':journal_hash}
        # Common intentionally stores only its bounded public receipt summary.
        # Compare its digest to the exact original retry worker result, rather
        # than assuming journal fields survive receipt projection.
        if receipt!={'requestId':row['id'],'verified':True,'receiptHash':digest(expected)}:
            raise ValueError('WORKFLOW_RETRY_RECEIPT_CHANGED')
        attempt=common.db.execute('SELECT * FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=? AND attempt=?',(parent['requestId'],journal['stage_id'],journal['attempt'])).fetchone()
        if attempt is None or attempt['child_key']!=parent['requestId']+'.'+journal['stage_id']+'.'+str(journal['attempt']):
            raise ValueError('WORKFLOW_RETRY_ATTEMPT_CHANGED')
        # Original retry worker has already verified release/notAccepted or
        # same-child observation. The completion receipt pins its exact journal.
        result[journal['stage_id']]={'retryRequestId':row['id'],'attempt':journal['attempt'],
            'childKey':attempt['child_key'],'childId':attempt['child_id'],
            'proofHash':journal_hash}
    return result
