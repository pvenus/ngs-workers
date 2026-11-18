import hashlib,json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from workers.resident.contracts import Claim
from workers.resident.backends.document_npc import NPCContextStager,NPCDocumentBackend,digest,TEXT,LISTS

class NPCTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.work=self.root/'work';self.work.mkdir()
        self.claim=Claim('req','attempt','fence','document.edit',('pds:category',),{'schemaVersion':'npc-category-request.v1','projectId':'binari','npcId':'yeonwoo','mode':'modify','categoryId':'overview','baseRevision':3,'intent':'수정','candidateCount':1},'grant','3')
        self.rows=[{'id':'common.npc-planning','value':{'style':'original'}},{'id':'unique.npc-category:overview','value':{'name':'인물 개요','content':'before'}}]
        for row in self.rows:row['contentHash']=digest(row['value'])
        self.calls=[]
    def call(self,path,body=None):
        self.calls.append((path,body))
        return {'projectId':'binari','npcId':'yeonwoo','npcRevision':3,'sourceVersion':'filex-1','categories':self.rows}
    def stage(self):NPCContextStager(self.call).stage(self.claim,self.work)
    def backend(self):
        self.binary=self.root/'cli'
        p={**{k:'ok'for k in TEXT},**{k:[]for k in LISTS}};p['categoryName']='인물 개요'
        self.binary.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nPath(sys.argv[sys.argv.index("--output-last-message")+1]).write_text('+repr(json.dumps({'proposals':[p]}))+')\n');self.binary.chmod(0o700)
        return NPCDocumentBackend(self.binary,hashlib.sha256(self.binary.read_bytes()).hexdigest())
    def test_pds_context_and_official_request(self):
        self.stage();b=self.backend();b.load()
        result=b.execute(self.claim,self.work,Event())
        self.assertEqual(len(result),1);self.assertEqual(len(self.calls),2)
        self.assertEqual(json.loads((self.work/'document-context.json').read_text())['requestHash'],digest(self.claim.parameters))
    def test_pin_changed_after_load(self):
        self.stage();b=self.backend();b.load();self.binary.write_text('changed')
        with self.assertRaisesRegex(ValueError,'PIN'):b.execute(self.claim,self.work,Event())
    def test_wrong_revision(self):
        with self.assertRaisesRegex(ValueError,'REQUEST'):NPCContextStager(self.call).stage(replace(self.claim,expected_revision='4'),self.work)
        self.assertEqual(self.calls,[])
    def test_hash_mismatch(self):
        self.rows[0]['contentHash']='0'*64
        with self.assertRaisesRegex(ValueError,'HASH'):self.stage()
        self.assertFalse((self.work/'document-context.json').exists())
    def test_attempt_mismatch(self):
        self.stage();b=self.backend()
        with self.assertRaisesRegex(ValueError,'CLAIM'):b.execute(replace(self.claim,attempt_id='other'),self.work,Event())
    def test_context_scope_mismatch(self):
        self.rows.reverse()
        with self.assertRaisesRegex(ValueError,'SCOPE'):self.stage()
    def test_unsupported_request_rejected(self):
        wrong=replace(self.claim,parameters={**self.claim.parameters,'schemaVersion':'unknown'})
        with self.assertRaisesRegex(ValueError,'REQUEST'):NPCContextStager(self.call).stage(wrong,self.work)
        self.assertEqual(self.calls,[])
    def test_file_scope_preserves_unknown_fields(self):
        outer=self
        class Files:
            def download(self,*args):
                outer.assertEqual(args,('yeonwoo','overview',3,'modify'))
                return json.dumps({'id':'overview','name':'인물 개요','content':'before','unknown':7}).encode(),{'categoryId':'overview'}
        NPCContextStager(self.call,Files()).stage(self.claim,self.work)
        b=self.backend()
        script=self.binary.read_text().replace("'proposals':", "'proposals':")
        # Fixture emits an unauthorized whole-file replacement: reject, never publish.
        p={**{k:'ok' for k in TEXT},**{k:[] for k in LISTS}};p['categoryName']='인물 개요'
        value={'proposals':[p],'fileContent':json.dumps({'id':'overview','name':'인물 개요','content':'ok','unknown':8})}
        self.binary.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nPath(sys.argv[sys.argv.index("--output-last-message")+1]).write_text('+repr(json.dumps(value))+')\n')
        b=NPCDocumentBackend(self.binary,hashlib.sha256(self.binary.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError,'FILE_SCOPE'):b.execute(self.claim,self.work,Event())
