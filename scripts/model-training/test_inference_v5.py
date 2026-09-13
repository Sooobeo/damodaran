import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import infer_v5 as api


class V5InferenceIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.original_root=api.v5.WORK_ROOT.resolve()
        self.temp=tempfile.TemporaryDirectory(prefix='unit-v5-inference-',dir=self.original_root)
        self.root=Path(self.temp.name).resolve()
        self.run=self.root/'runs'/'fixture'
        self.model=self.run/'model'
        self.selected=self.run/'checkpoints'/'step-1'
        self.parent=self.root/'parent'
        for path in (self.model,self.selected/'model',self.parent):path.mkdir(parents=True)
        self.files={}
        for name in api.v5.MODEL_FILES:
            original=('parent-'+name).encode()
            changed=b'new-model' if name=='model.safetensors' else original
            (self.parent/name).write_bytes(original)
            (self.model/name).write_bytes(changed)
            (self.selected/'model'/name).write_bytes(changed)
            self.files[name]=api.infer.sha256(self.parent/name)
        self.lineage={'originalBase':{'model':api.v5.ORIGINAL_MODEL_ID,'revision':api.v5.ORIGINAL_REVISION,
                                     'weightSha256':api.v5.ORIGINAL_WEIGHT_SHA256},'parentSelectedStep':218}
        self.manifest={'runId':'fixture','scriptVersion':api.v5.VERSION,'scriptSha256':api.infer.sha256(Path(api.v5.__file__)),
            'dependencyFiles':api.v5.dependency_hashes(),
            'baseModel':api.v5.ORIGINAL_MODEL_ID,'baseRevision':api.v5.ORIGINAL_REVISION,
            'basePath':api.infer.relative(self.parent),'baseFiles':self.files,'originalBase':self.lineage['originalBase'],
            'baselineRole':'fixture-parent','lineage':self.lineage,'datasets':{'test':{'sha256':'synthetic-sealed-test'}},
            'datasetPublication':{'manifestSha256':'synthetic-publication'},'config':{},'gate':api.v5.POLICY,
            'selectedCheckpoint':api.infer.relative(self.selected)}
        self.manifest['identitySha256']=api.v5.canonical_hash({k:self.manifest[k] for k in api.IDENTITY_KEYS})
        self.weight=api.infer.sha256(self.model/'model.safetensors')
        self.training={'runId':'fixture','baseWeightFileSha256':api.v5.PARENT_WEIGHT_SHA256,
            'completedUpdates':1,'weightEvidence':{'changedTensorCount':1},'modelPath':api.infer.relative(self.model),
            'selectedStep':1,'trainedWeightFileSha256':self.weight,'devGate':{'passed':True}}
        self.write(self.run/'manifest.json',self.manifest)
        self.write(self.run/'training-summary.json',self.training)
        self.write(self.selected/'checkpoint.json',{'step':1,'modelSha256':self.weight})
        inventory=api.v5.model_inventory(self.run,self.manifest,self.training)
        stamp={'modelInventory':inventory,'modelInventorySha256':api.v5.canonical_hash(inventory)}
        self.manifest.update(stamp)
        self.training.update(stamp)
        self.write(self.run/'manifest.json',self.manifest)
        self.write(self.run/'training-summary.json',self.training)
        self.patches=[patch.object(api.v5,'WORK_ROOT',self.root),
            patch.object(api.v5.engine,'WORK_ROOT',self.root),
            patch.object(api.v5,'verify_parent',return_value=(self.parent,self.files,self.lineage)),
            patch.object(api.infer,'validate_tokenizer_files',return_value={})]
        for item in self.patches:item.start()

    def write(self,path,value):path.write_text(json.dumps(value),'utf-8')

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        assert self.root.is_relative_to(self.original_root) and self.root.name.startswith('unit-v5-inference-')
        self.temp.cleanup()

    def test_completed_unconsumed_model_remains_experimental(self):
        result=api.verified_model('fixture')
        self.assertEqual(result['weightHash'],self.weight)
        self.assertFalse(result['promotionEligible'])
        self.assertFalse(result['testWasEvaluated'])
        self.assertFalse((self.run/'evaluation-summary.json').exists())

    def test_checkpoint_and_tokenizer_changes_are_rejected(self):
        (self.model/'config.json').write_bytes(b'changed')
        with self.assertRaisesRegex(api.infer.InferenceError,'model/tokenizer integrity'):
            api.verified_model('fixture')
        (self.model/'config.json').write_bytes((self.selected/'model/config.json').read_bytes())
        for path in (self.model,self.selected/'model'):(path/'source.spm').write_bytes(b'changed together')
        with self.assertRaisesRegex(api.infer.InferenceError,'runtime inventory'):
            api.verified_model('fixture')

    def test_config_or_evaluation_identity_change_is_rejected(self):
        self.manifest['config']={'beams':99}
        self.write(self.run/'manifest.json',self.manifest)
        with self.assertRaisesRegex(api.infer.InferenceError,'frozen policy'):api.verified_model('fixture')
        self.manifest['config']={}
        self.write(self.run/'manifest.json',self.manifest)
        evaluation={'runId':'fixture','trainedWeightFileSha256':self.weight,
                    'baseWeightFileSha256':'wrong-parent','gatePolicy':api.v5.POLICY,
                    'modelPath':api.infer.relative(self.model),'promotionEligible':True,
                    'devGate':{'passed':True},'testGate':{'passed':True},'testDataSha256':'synthetic-sealed-test'}
        self.write(self.run/'evaluation-summary.json',evaluation)
        with self.assertRaisesRegex(api.infer.InferenceError,'evaluation identity'):api.verified_model('fixture')
        evaluation['baseWeightFileSha256']=api.v5.PARENT_WEIGHT_SHA256
        self.write(self.run/'evaluation-summary.json',evaluation)
        ledger=api.v5.engine.test_ledger(self.manifest)
        ledger.parent.mkdir(parents=True)
        self.write(ledger,{'status':'complete','runId':'fixture','modelSha256':self.weight,
                          'testDataSha256':'synthetic-sealed-test','summaryPath':api.infer.relative(self.run/'evaluation-summary.json')})
        self.assertTrue(api.verified_model('fixture')['promotionEligible'])

    def test_two_matching_but_changed_runtime_configs_are_rejected(self):
        for folder in (self.model,self.selected/'model'):
            (folder/'generation_config.json').write_text('{"decoder_start_token_id":0}','utf-8')
        with self.assertRaisesRegex(api.infer.InferenceError,'runtime inventory'):
            api.verified_model('fixture')

    def test_evaluation_cannot_claim_other_test_or_unfinished_ledger(self):
        evaluation={'runId':'fixture','trainedWeightFileSha256':self.weight,
                    'baseWeightFileSha256':api.v5.PARENT_WEIGHT_SHA256,'gatePolicy':api.v5.POLICY,
                    'modelPath':api.infer.relative(self.model),'promotionEligible':True,
                    'devGate':{'passed':True},'testGate':{'passed':True},'testDataSha256':'other-test'}
        self.write(self.run/'evaluation-summary.json',evaluation)
        with self.assertRaisesRegex(api.infer.InferenceError,'evaluation identity'):
            api.verified_model('fixture')
        evaluation['testDataSha256']='synthetic-sealed-test'
        self.write(self.run/'evaluation-summary.json',evaluation)
        with self.assertRaisesRegex(api.infer.InferenceError,'completion ledger'):
            api.verified_model('fixture')


if __name__=='__main__':unittest.main()
