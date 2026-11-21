"""Actual media-intake/PDS Request-side ports. No generation or ownership admit.

All paths and source types are pinned operator configuration, never payload paths.
Constructing this factory neither opens an intake DB nor creates service folders.
"""
import base64,json,hashlib
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from .domain_resolvers import DomainResolvers,ANIMATION,FRAMES
from .animation_batch_loop import AnimationBatchLoop
from ..adapters.ownership_guard import PinnedOwnershipGuardResolver
from ..adapters.animation_pose import binding as pose_binding
from ..adapters.animation_workspace import AnimationWorkspace
from ..adapters.frame_full_workspace import FullFrameWorkspace,FullFrameClient
from ..adapters.frame_workspace import HalfFrameWorkspace,digest
from ..adapters.pds_animation import AnimationPublicationClient
from ..backends.frame_postprocess import DIRECTIONS
from ..backends.image_music_workspace import safe_file
from ..contracts import Receipt


def existing_root(value):
    root=Path(value)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir() or root.resolve()!=root:
        raise ValueError('MEDIA_EXISTING_CANONICAL_ROOT_REQUIRED')
    return root


def animation_frame_domain_factory(sources,*,pds_url,animation_ownership_root=None,
        frame_ownership_root=None,animation_scratch_root=None,frame_scratch_root=None,
        admitted_request,preflight,client_factory=None,enabled_roles=('animation','postprocess'),ownership_lifecycle=None):
    if not callable(admitted_request) or not callable(preflight):
        raise ValueError('MEDIA_HANDOFF_PREFLIGHT_REQUIRED')
    if (not isinstance(enabled_roles,(tuple,list)) or not enabled_roles
            or len(set(enabled_roles))!=len(enabled_roles)
            or any(role not in ('animation','postprocess') for role in enabled_roles)):
        raise ValueError('MEDIA_ENABLED_ROLES_INVALID')
    definitions={'animation':('animation.generate',ANIMATION,'animation_contract','animation_intake_requests',animation_ownership_root,animation_scratch_root),
                 'postprocess':('frames.extract',FRAMES,'frame_contract','frame_intake_requests',frame_ownership_root,frame_scratch_root)}
    selected=[definitions[role] for role in enabled_roles]
    ownership={cap:existing_root(own) for cap,route,module,table,own,scratch in selected}
    scratch={cap:existing_root(folder) for cap,route,module,table,own,folder in selected}
    validators={route:sources.modules[module].validate for cap,route,module,table,own,folder in selected}
    tables={cap:table for cap,route,module,table,own,folder in selected}
    routes=tuple(row[1] for row in selected)
    capabilities=tuple(row[0] for row in selected)
    def build(common,bridge,model,terminal):
        def original(rid,capability,parameters=None):
            if capability not in tables:raise ValueError('MEDIA_CAPABILITY_INVALID')
            row=common.row(rid)
            expected=ANIMATION if capability=='animation.generate' else FRAMES
            is_parent=capability=='animation.generate' and row['request_type']=='npc.animation.generate-eight-directions'
            if is_parent:expected=('npc.animation.generate-eight-directions',ANIMATION[1],ANIMATION[2])
            refs=common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(rid,)).fetchall()
            refs=[r for r in refs if (row['request_type'],r['schema_version'],r['domain'])==expected]
            if len(refs)!=1 or refs[0]['reference_id']!=rid:raise ValueError('MEDIA_ORIGINAL_REFERENCE_REQUIRED')
            record=common.db.execute('SELECT * FROM '+tables[capability]+' WHERE id=?',(rid,)).fetchone()
            if not record or record['actor']!=row['requester']:raise ValueError('MEDIA_ORIGINAL_OWNER_MISMATCH')
            value=json.loads(record['body'])
            if digest(value)!=record['input_hash'] or record['input_hash']!=refs[0]['input_hash'] or parameters is not None and dict(parameters)!=value:
                raise ValueError('MEDIA_ORIGINAL_PAYLOAD_CHANGED')
            if is_parent:sources.modules['animation_batch'].validate_batch(value)
            else:validators[expected](value)
            return value
        def session_for_claim(claim,*,readonly=False):
            request=original(claim.request_id,claim.capability,claim.parameters)
            owner_id=claim.request_id
            current=common.row(claim.request_id)
            if current['parent_id']:
                parent=common.row(current['parent_id'])
                if claim.capability!='animation.generate' or parent['request_type']!='npc.animation.generate-eight-directions' or parent['requester']!=current['requester']:
                    raise ValueError('MEDIA_PARENT_SCOPE_UNSUPPORTED')
                parent_body=original(parent['request_id'],claim.capability)
                row=common.db.execute('SELECT state FROM animation_batch_state WHERE id=?',(parent['request_id'],)).fetchone()
                state=json.loads(row[0]) if row else {}
                expected={**parent_body,'direction':request['direction']}
                if 'seedMap'in state:expected['seed']=state['seedMap'][request['direction']]
                if (state.get('executionMode')!='external-children.v1' or request!=expected or claim.request_id!=parent['request_id']+'.'+request['direction']
                    or not any(step.get('requestId')==claim.request_id and step.get('direction')==request['direction'] for step in state.get('steps',[]))):
                    raise ValueError('MEDIA_BATCH_CHILD_MEMBERSHIP_CHANGED')
                owner_id=parent['request_id']
            root=ownership[claim.capability];path=root/(digest([request['npcId'],owner_id])+'.json')
            if path.is_symlink() or not path.is_file():raise ValueError('MEDIA_ORIGINAL_OWNERSHIP_PIN_REQUIRED')
            if readonly:return SimpleNamespace(path=path,npc=request['npcId'],rid=owner_id)
            # Existing path checked before original Session constructor's mkdir.
            if ownership_lifecycle is not None:return sources.session_type(root,request['npcId'],owner_id)
            return sources.session_type(root,request['npcId'],owner_id,
                call=lambda *a,**k:(_ for _ in ()).throw(ValueError('MEDIA_NEW_ADMISSION_FORBIDDEN')))
        pinned_guard=PinnedOwnershipGuardResolver(session_for_claim)
        def service_for(claim,session):
            if claim.capability=='frames.extract':
                return sources.modules['frame_service'].FrameRequests(common,session.path.parent.parent,None)
            return sources.modules['animation_service'].AnimationRequests(common,session.path.parent.parent,None,None)
        def guard(claim):
            if ownership_lifecycle is not None:
                session=session_for_claim(claim);service=service_for(claim,session)
                path=scratch[claim.capability]/digest([claim.request_id,claim.attempt_id]) if hasattr(claim,'attempt_id') else None
                ownership_lifecycle.ensure(service,session,kind='frame-job' if claim.capability=='frames.extract' else 'animation-job',path=path)
            return pinned_guard(claim)
        def client(claim):
            cls=AnimationPublicationClient if claim.capability=='animation.generate' else FullFrameClient
            provider=lambda:guard(claim)['guard']
            return client_factory(claim,provider) if client_factory else cls(pds_url,provider,claim.request_id)
        def settled_receipt_reader(claim):
            # Receipt-only port: original input/membership/pin, no admit/ensure,
            # no publish or active-lease resurrection after native release.
            if claim.capability!='animation.generate':raise ValueError('ANIMATION_SETTLED_ROUTE')
            session=session_for_claim(claim,readonly=True)
            state=json.loads(session.path.read_text());g=state.get('guard')
            if (not isinstance(g,dict) or set(g)!={'npcId','generationId','requestId','leaseToken'}
                or not g.get('leaseToken') or state.get('npcId')!=session.npc or state.get('requestId')!=session.rid
                or any(g[k]!=state.get(k) for k in ('npcId','requestId','generationId'))):raise ValueError('ANIMATION_SETTLED_PIN_CHANGED')
            provider=lambda:g
            port=client_factory(claim,provider) if client_factory else AnimationPublicationClient(pds_url,provider,claim.request_id)
            value=port.receipt(claim.request_id)
            if json.loads(session.path.read_text())!=state:raise ValueError('ANIMATION_SETTLED_PIN_CHANGED')
            return value
        def workspace(claim):
            original(claim.request_id,claim.capability,claim.parameters)
            cls=AnimationWorkspace if claim.capability=='animation.generate' else HalfFrameWorkspace if claim.parameters.get('scalePreset','full')=='half' else FullFrameWorkspace
            return cls(scratch[claim.capability],client(claim))
        def snapshot(row,payload):
            cap='animation.generate' if row['request_type']==ANIMATION[0] else 'frames.extract'
            hint=SimpleNamespace(request_id=row['request_id'],capability=cap,parameters=payload)
            port=client(hint);current=port.current()
            matches=[n for n in current['document']['characters'] if n['id']==payload['npcId']]
            if len(matches)!=1:raise ValueError('MEDIA_CURRENT_NPC_REQUIRED')
            npc=matches[0]
            if cap=='animation.generate':
                pose=pose_binding(npc,payload)
                motion=port.json('/v1/motion-presets/'+payload['presetId']+'/'+payload['direction'])
                if any(motion.get(k)!=payload[k] for k in ('posePreset','presetId','direction')) or motion.get('cycleCount')!=1:
                    raise ValueError('MEDIA_MOTION_BINDING_CHANGED')
                raw=base64.b64decode(motion['base64'],validate=True)
                if hashlib.sha256(raw).hexdigest()!=motion['sha256'] or len(raw)!=motion['bytes'] or motion['assetId']!=motion['sha256']+'.mp4':
                    raise ValueError('MEDIA_MOTION_BYTES_CHANGED')
                refs=['pds:asset:'+pose['referenceAssetId'],'pds:motion:'+motion['sha256']]
            elif payload.get('scalePreset','full')=='half':
                source=HalfFrameWorkspace(scratch[cap],port)._source(npc,payload)
                if set(source['directions'])!=set(DIRECTIONS):raise ValueError('MEDIA_FRAME_DIRECTIONS_REQUIRED')
                refs=sorted({'pds:asset:'+frame[key] for d in DIRECTIONS for frame in source['directions'][d]['frames'] for key in ('rawAssetId','alphaAssetId')})
            else:
                source=FullFrameWorkspace(scratch[cap],port)._sources(npc,payload)
                refs=['pds:asset:'+source[d]['assetId'] for d in DIRECTIONS]
            return {'projectId':payload['projectId'],'targetId':payload['npcId'],
                    'expectedRevision':str(current['revision']),'inputRefs':refs}
        resolvers=DomainResolvers(common,snapshot)
        def ready(row):
            if row.get('parent_id'):
                parent=dict(common.row(row['parent_id']))
                return parent['request_type']=='npc.animation.generate-eight-directions' and parent['state'] in ('queued','retry_wait') and admitted_request(parent) is True
            return admitted_request(row) is True
        selector=resolvers.selector(validators,ready,routes)
        def packet(claim):
            space=workspace(claim);folder=space._folder(claim)
            if not folder.exists():return space,None
            if folder.is_symlink() or json.loads(safe_file(folder,'claim.json').read_text())!=json.loads(json.dumps(asdict(claim))):
                raise ValueError('MEDIA_ATTEMPT_OWNER_CHANGED')
            path=folder/'publication-intent.json'
            if not path.exists() and not path.is_symlink():return space,None
            value=json.loads(safe_file(folder,'publication-intent.json').read_text())
            request=claim.parameters
            if any(value.get(k)!=v for k,v in {'requestId':claim.request_id,'projectId':request['projectId'],'npcId':request['npcId']}.items()):
                raise ValueError('MEDIA_PUBLICATION_TARGET_CHANGED')
            target=value['slot'] if claim.capability=='animation.generate' else value['manifest']
            for key in ('posePreset','presetId')+ (('direction','referenceType') if claim.capability=='animation.generate' else ()):
                if target.get(key)!=request[key]:raise ValueError('MEDIA_PUBLICATION_SLOT_CHANGED')
            if claim.capability=='frames.extract' and target.get('scalePreset','full')!=request.get('scalePreset','full'):
                raise ValueError('MEDIA_PUBLICATION_SCALE_CHANGED')
            for artifact in ([value['artifact']] if 'artifact' in value else value['artifacts']):
                raw=base64.b64decode(artifact['base64'],validate=True)
                if len(raw)!=artifact['bytes'] or hashlib.sha256(raw).hexdigest()!=artifact['sha256']:
                    raise ValueError('MEDIA_PUBLICATION_BYTES_CHANGED')
            return space,value
        def inspect(claim):
            space,value=packet(claim)
            if value is None:return None
            raw=space.client.receipt(claim.request_id)
            if raw is None:return None
            space.client.verify(value,raw)
            refs=(raw['assetId'],) if 'artifact' in value else tuple(a['sha256']+'.png' for a in value['artifacts'])
            return Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,refs,str(raw['revision']),True)
        def verify(claim,receipt):
            expected=inspect(claim)
            if expected is None or expected!=receipt:raise ValueError('MEDIA_PDS_RECEIPT_MISMATCH')
            space,intent=packet(claim)
            raw=space.client.receipt(claim.request_id);space.client.verify(intent,raw)
            if str(raw['revision'])!=receipt.revision:raise ValueError('MEDIA_PDS_REVISION_CHANGED')
            # Existing intake has no standalone mirror API: legacy work() writes
            # this exact publication column after PDS verification. Do the same
            # on the Request service, never forge its different workflow receipt.
            with bridge.transaction():
                original(claim.request_id,claim.capability,claim.parameters)
                table=tables[claim.capability]
                prior=common.db.execute('SELECT publication FROM '+table+' WHERE id=?',(claim.request_id,)).fetchone()[0]
                encoded=json.dumps(raw,ensure_ascii=False,sort_keys=True,separators=(',',':'))
                if prior is not None and json.loads(prior)!=raw:raise ValueError('MEDIA_INTAKE_PUBLICATION_CHANGED')
                common.db.execute('UPDATE '+table+' SET publication=?,error=NULL WHERE id=?',(encoded,claim.request_id))
            return {'sourceReceipt':raw,'publication':{'verified':True,'requestId':claim.request_id,'source':'filex','receipt':raw}}
        def resume(claim,expected_hash):
            space,value=packet(claim)
            if value is None or digest(value)!=expected_hash:raise ValueError('MEDIA_RESUME_EXACT_INTENT_REQUIRED')
            # Supervisor has proved execution terminal and reserved this exact hash.
            space.client.publish(value)
            return inspect(claim)
        def cleanup(claim):
            space=workspace(claim)
            return space.cleanup(claim,space._folder(claim))
        def initialize():
            present={r[0] for r in common.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not set(tables.values())<=present:raise ValueError('MEDIA_ORIGINAL_INTAKE_SCHEMA_REQUIRED')
        batch_loop=AnimationBatchLoop(sources,common,ownership['animation.generate'].parent,client,original,admitted_request) if 'animation.generate' in capabilities and {'animation_batch','animation_service'}<=sources.modules.keys() else None
        from .media_ownership_loop import MediaOwnershipLoop
        ownership_loop=MediaOwnershipLoop(common,tables,original,session_for_claim,service_for,ownership_lifecycle,admitted_request) if ownership_lifecycle is not None else None
        from .frame_no_post_site import reader as no_post_reader
        frame_no_post_snapshot=no_post_reader(pds_url=pds_url,original=original,session_for_claim=session_for_claim,workspace=workspace) if 'frames.extract'in capabilities else None
        return {'frame_no_post_snapshot':frame_no_post_snapshot,'settled_receipt_reader':settled_receipt_reader,'workspace_for_claim':workspace,'media_ownership_loop':ownership_loop,'animation_batch_loop':batch_loop,'capabilities':capabilities,'selector':selector,
                'verify_publication':verify,'guard_resolver':guard,'inspect_publication':inspect,
                'verify_recovery_publication':verify,'resume_publication':resume,'cleanup':cleanup,
                'initialize_schema':initialize,'preflight':preflight}
    return build


def postprocess_domain_factory(sources,*,pds_url,frame_ownership_root,frame_scratch_root,
        admitted_request,preflight,client_factory=None,ownership_lifecycle=None):
    """Frame-only pilot: no animation config, model, table, root or batch loop."""
    return animation_frame_domain_factory(sources,pds_url=pds_url,
        frame_ownership_root=frame_ownership_root,frame_scratch_root=frame_scratch_root,
        admitted_request=admitted_request,preflight=preflight,client_factory=client_factory,
        enabled_roles=('postprocess',),ownership_lifecycle=ownership_lifecycle)
