"""NPC planning request mapping and PDS-only context staging (candidate)."""
import copy
from dataclasses import asdict
import uuid
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from .document import DocumentBackend
from ..contracts import PROFILES


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def request(claim):
    claim.validate(PROFILES['document'])
    r=claim.parameters
    if set(r)=={'variation','stage','request','childKey','actor','parentId'}:
        if not isinstance(r['stage'],dict) or r['stage'].get('candidateCount')!=1 or r['stage'].get('id') not in ('overview','art','category-add','category-modify'):
            raise ValueError('DOCUMENT_ENVELOPE_STAGE_INVALID')
        r=r['request']
    if (r.get('schemaVersion')!='npc-category-request.v1' or r.get('projectId')!='binari'
            or r.get('mode') not in ('add','modify') or r.get('candidateCount',1)!=1
            or not isinstance(r.get('intent'),str) or not r['intent'].strip()
            or type(r.get('baseRevision')) is not int or str(r['baseRevision'])!=claim.expected_revision
            or not isinstance(r.get('npcId'),str) or not r['npcId']
            or r['mode']=='modify' and (not isinstance(r.get('categoryId'),str) or not r['categoryId'])):
        raise ValueError('DOCUMENT_NPC_REQUEST_INVALID')
    return r


class NPCContextStager:
    """Injected PDS HTTP callable only. No NGS/SQLite/Filex filesystem access."""
    def __init__(self, call, file_client=None, category_target=None): self.call,self.file_client,self.category_target=call,file_client,category_target
    def stage(self,claim,workspace,*,supporting_ids=()):
        r=request(claim); root=Path(workspace)
        if root.is_symlink() or not root.is_dir():raise ValueError('DOCUMENT_WORKSPACE_INVALID')
        ids=['common.npc-planning']
        if r['mode']=='modify':ids.append('unique.npc-category:'+r['categoryId'])
        for cid in supporting_ids:
            key='unique.npc-category:'+cid
            if key not in ids:ids.append(key)
        catalog=self.call('/api/worker-context/catalog?'+urlencode({'projectId':'binari','npcId':r['npcId'],'workerId':'npc-planning'}))
        if not isinstance(catalog,dict) or any(catalog.get(k)!=v for k,v in {'projectId':'binari','npcId':r['npcId'],'npcRevision':r['baseRevision']}.items()):raise ValueError('DOCUMENT_CONTEXT_REVISION')
        source=catalog.get('sourceVersion')
        if not isinstance(source,str) or not source:raise ValueError('DOCUMENT_CONTEXT_SOURCE')
        expected={c['id']:c.get('contentHash') for c in catalog.get('categories',[])}
        if not set(ids)<=expected.keys():raise ValueError('DOCUMENT_CONTEXT_MISSING')
        body={'projectId':'binari','npcId':r['npcId'],'npcRevision':r['baseRevision'],'sourceVersion':source,'categories':ids}
        result=self.call('/api/worker-context/read',body)
        if not isinstance(result,dict) or any(result.get(k)!=v for k,v in body.items() if k!='categories'):raise ValueError('DOCUMENT_CONTEXT_BINDING')
        rows=result.get('categories',[])
        if [c.get('id')for c in rows]!=ids:raise ValueError('DOCUMENT_CONTEXT_SCOPE')
        for row in rows:
            if digest(row.get('value'))!=row.get('contentHash') or row['contentHash']!=expected[row['id']]:raise ValueError('DOCUMENT_CONTEXT_HASH')
        value={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'resourceGrant':claim.resource_grant,'requestHash':digest(dict(claim.parameters)),
               'inputRefs':list(claim.input_refs),'expectedRevision':claim.expected_revision,
               'commonPlanning':rows[0]['value'],'targetCategory':rows[1]['value'] if r['mode']=='modify' else None,
               'supportingCharacterPlanning':[v['value'] for v in rows[2 if r['mode']=='modify' else 1:]],
               'sourceVersion':source}
        if self.file_client is not None:
            cid=r.get('categoryId') if r['mode']=='modify' else self.category_target(claim) if callable(self.category_target) else None
            if not isinstance(cid,str)or not cid:raise ValueError('DOCUMENT_ADMITTED_CATEGORY_ID_REQUIRED')
            def prepare_log(state):
                if not getattr(self.file_client,'context_diagnostics',False):return
                try:
                    import sys
                    print(json.dumps({'event':'DOCUMENT_WORKSPACE_PREPARATION','state':state,'requestId':claim.request_id}),file=sys.stderr,flush=True)
                except BaseException:pass
            prepare_log('started')
            try:raw,basis=self.file_client.download(r['npcId'],cid,r['baseRevision'],r['mode'])
            except BaseException:
                prepare_log('failed');raise
            prepare_log('completed')
            value['targetFile']={'basis':basis,'content':json.loads(raw)}
        path=root/'document-context.json'
        if path.exists() or path.is_symlink():raise ValueError('DOCUMENT_CONTEXT_EXISTS')
        with path.open('x') as f:json.dump(value,f,ensure_ascii=False)
        return path


TEXT=('title','categoryName','content','rationale','evidenceSummary')
LISTS=('differences','assumptions','unresolved')
PROPOSAL={'type':'object','additionalProperties':False,'properties':{
    **{k:{'type':'string','minLength':1}for k in TEXT},
    **{k:{'type':'array','items':{'type':'string'}}for k in LISTS}},'required':list(TEXT+LISTS)}
SCHEMA={'type':'object','additionalProperties':False,'properties':{'proposals':{'type':'array','minItems':1,'maxItems':1,'items':PROPOSAL}},'required':['proposals']}


class NPCDocumentBackend:
    def __init__(self,binary,sha256,**options):
        self.binary,self.sha256,self.options=Path(binary),sha256,options
        self._delegate=None;self._execution_scope=None
    def _pin(self):
        if (not self.binary.is_absolute() or self.binary.is_symlink() or not self.binary.is_file()
                or not isinstance(self.sha256,str) or len(self.sha256)!=64
                or hashlib.sha256(self.binary.read_bytes()).hexdigest()!=self.sha256):
            raise ValueError('DOCUMENT_BINARY_PIN_MISMATCH')
    def load(self):self._pin()
    def unload(self):pass
    def execution_evidence(self,claim):
        if self._delegate is None or self._execution_scope!=digest(asdict(claim)):return None
        return copy.deepcopy(self._delegate.execution_evidence(claim))
    def execute(self,claim,workspace,cancel):
        # Preserve the real runner that owns joined evidence. Never reconstruct
        # terminal proof from output files or a previous process incarnation.
        self._delegate=None;self._execution_scope=None
        self._pin();r=request(claim);path=Path(workspace)/'document-context.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size>2097152:raise ValueError('DOCUMENT_CONTEXT_REQUIRED')
        context=json.loads(path.read_text())
        for k,v in {'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'resourceGrant':claim.resource_grant,'requestHash':digest(dict(claim.parameters)),
                    'inputRefs':list(claim.input_refs),'expectedRevision':claim.expected_revision}.items():
            if context.get(k)!=v:raise ValueError('DOCUMENT_CONTEXT_CLAIM_MISMATCH')
        target=context.get('targetCategory');artifact=context.get('targetFile')
        schema=copy.deepcopy(SCHEMA)
        if artifact:
            schema['properties']['fileContent']={'type':'string'}
            schema['required'].append('fileContent')
        def validate(value):
            if not isinstance(value,dict) or set(value)!=({'proposals','fileContent'} if artifact else {'proposals'}) or not isinstance(value['proposals'],list) or len(value['proposals'])!=1:raise ValueError('DOCUMENT_NPC_OUTPUT_INVALID')
            p=value['proposals'][0]
            if not isinstance(p,dict) or set(p)!=set(TEXT+LISTS):raise ValueError('DOCUMENT_NPC_OUTPUT_INVALID')
            if any(not isinstance(p[k],str) or not p[k].strip() for k in TEXT) or any(not isinstance(p[k],list) or any(not isinstance(v,str)for v in p[k])for k in LISTS):raise ValueError('DOCUMENT_NPC_OUTPUT_INVALID')
            if r['mode']=='modify' and (not isinstance(target,dict) or p['categoryName']!=target.get('name')):raise ValueError('DOCUMENT_NPC_CATEGORY_CHANGED')
            if artifact:
                candidate=json.loads(value['fileContent']);expected=copy.deepcopy(artifact['content'])
                cid=artifact['basis']['categoryId']
                if cid.startswith('field:'):
                    field=cid[6:]
                    expected[field]=json.loads(p['content']) if field in ('numericProfile','traits') else p['content'].splitlines() if field=='sourceRefs' else p['content']
                else:expected.update(name=p['categoryName'],content=p['content'])
                if candidate!=expected:raise ValueError('DOCUMENT_NPC_FILE_SCOPE')
        self._delegate=DocumentBackend(self.binary,output_schema=schema,validate_output=validate,intent_reader=lambda _:r['intent'],instruction="Write exactly one NPC planning proposal in Korean. Preserve the target category name when modifying. For overview preserve supplied identity and occupation verbatim; never infer occupation or gender. For art describe stable appearance, clothing and visual identity, not scene lighting or pose. Do not generate personality scores or put schema, IDs, provenance, workflow or rendering procedures in content. Common style is rendering guidance, not unique character facts. If targetFile exists, return fileContent as complete updated target JSON string, preserve IDs and unrelated fields exactly; change only category name/content or the specified field: target. Treat supporting overview and art as fixed canon. Report undefined facts in unresolved; do not invent them.",**self.options)
        self._execution_scope=digest(asdict(claim))
        return self._delegate.execute(claim,workspace,cancel)
