import unittest
import json
import tempfile
from pathlib import Path
from collections import Counter
from unittest.mock import patch
import dataset_v5 as d


class CanonicalAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = [
            {'id':'A','source':'value','target':'가치','aliases':[]},
            {'id':'B','source':'equity value','target':'주주가치','aliases':['EV']},
            {'id':'C','source':'market capitalization','target':'시가총액','aliases':[]},
        ]

    def test_longest_phrase_and_acronym_case_and_word_boundaries(self):
        result = d.catalog_matches('Equity value, VALUE and EV; ev, evaluated.', self.catalog)
        self.assertEqual(result,[{'id':'B','source':'Equity value','target':'주주가치'},
                                {'id':'A','source':'VALUE','target':'가치'}])
        self.assertEqual(d.catalog_matches('ev and evaluated',self.catalog),[])

    def test_missing_source_annotation_and_reference_spelling_are_rejected(self):
        row = {'id':'fixture','split':'dev','domain':'finance','humanReviewed':False,
               'primaryTermId':'B','source':'Equity value differs from market capitalization.',
               'target':'주주가치는 시가총액과 다르다.','terms':[{'id':'B','source':'Equity value','target':'주주가치'}]}
        result=d.validate_rows([row],self.catalog,'dev')
        self.assertIn('source-term-coverage:C',result['issues'][0]['issues'])
        row['terms']=d.catalog_matches(row['source'],self.catalog)
        self.assertTrue(d.validate_rows([row],self.catalog,'dev')['passed'])
        row['target']='주주의 가치는 총액과 다르다.'
        self.assertFalse(d.validate_rows([row],self.catalog,'dev')['passed'])

    def test_ordinary_financial_injection_and_corrupt_hash_are_rejected(self):
        row=d.enrich({'id':'fixture','split':'dev','domain':'general','humanReviewed':False,
                      'source':'The national capital has 7 museums.','target':'국가의 수도에는 박물관이 7개 있다.',
                      'terms':[],'forbiddenTerms':[{'target':'자본','reason':'A city, not financing.'}]})
        self.assertTrue(d.validate_rows([row],self.catalog,'dev')['passed'])
        row['target']='국가의 자본에는 박물관이 8개 있다.'
        issues=d.validate_rows([row],self.catalog,'dev')['issues'][0]['issues']
        self.assertIn('forbidden-term-in-reference',issues)
        self.assertIn('numeral-mismatch',issues)
        self.assertIn('targetSha256-mismatch',issues)

    def test_ampersand_acronym_requires_exact_case(self):
        catalog=[{'id':'A','source':'depreciation and amortization','target':'depreciation','aliases':['D&A']}]
        self.assertEqual(d.catalog_matches('d&a',catalog),[])
        self.assertEqual(d.catalog_matches('D&A',catalog)[0]['id'],'A')


class PublishedDatasetTests(unittest.TestCase):
    def setUp(self):
        self.original_root=d.train.WORK_ROOT.resolve()
        self.temp=tempfile.TemporaryDirectory(prefix='unit-v5-publication-',dir=self.original_root)
        self.root=Path(self.temp.name).resolve()
        self.catalog=[{'id':'C'+str(i),'source':'topic '+self.word(i),'target':'meaning'+self.word(i),'aliases':[]}
                      for i in range(54)]
        self.write_json('term-catalog.json',{'terms':self.catalog})
        self.catalog_hash=d.train.sha256(self.root/'term-catalog.json')
        self.patch=patch.object(d,'CATALOG_SHA256',self.catalog_hash)
        self.patch.start()
        self.write_json('annotation-policy.json',{'catalogSha256':self.catalog_hash})
        authored=[self.finance('new',term,index) for term in self.catalog for index in range(12)]
        authored += [self.general('new',index) for index in range(80)]
        splits={'train-a':authored[:364],'train-b':authored[364:]}
        for split in ('dev','test'):
            splits[split]=[self.finance(split,term,index) for term in self.catalog for index in range(2)]
            splits[split] += [self.general(split,index) for index in range(32)]
        records={}
        for name,values in splits.items():
            split='train' if name.startswith('train-') else name
            values=[d.enrich({**row,'split':split}) for row in values]
            splits[name]=values
            self.write_rows(name+'-candidate.jsonl',values)
            self.write_rows(name+'-reviewed.jsonl',values)
            review={'reviewerType':'assistant','humanReviewed':False,'completed':True,'sourceGrounded':True,
                    'inputSha256':d.train.sha256(self.root/(name+'-candidate.jsonl')),
                    'outputSha256':d.train.sha256(self.root/(name+'-reviewed.jsonl')),
                    'rowCount':len(values),'reviewedIDs':[r['id'] for r in values]}
            self.write_json(name+'-second-review.json',review)
            self.write_json(name+'-review-receipt.json',{'reviewerType':'assistant','humanReviewed':False,
                'completed':True,'outputSha256':review['outputSha256'],'rowCount':len(values),
                'evidenceFiles':{name+'-candidate.jsonl':review['inputSha256'],
                                 name+'-second-review.json':d.train.sha256(self.root/(name+'-second-review.json'))}})
            _,records[name]=d.reviewed_input(name,split,self.catalog,self.root)
        self.data={'train':splits['train-a']+splits['train-b'],'dev':splits['dev'],'test':splits['test']}
        self.paths={name:self.root/(name+'.jsonl') for name in self.data}
        for name,values in self.data.items():self.write_rows(name+'.jsonl',values)
        self.manifest={'version':d.PUBLICATION_VERSION,'humanReviewed':False,'catalogSha256':self.catalog_hash,
            'annotationPolicySha256':d.train.sha256(self.root/'annotation-policy.json'),
            'codeFiles':{name:d.train.sha256(Path(d.__file__).with_name(name)) for name in d.PUBLICATION_CODE_FILES},
            'validation':{'passed':True,'issues':[],'historicHoldoutIssues':[]},'reviewedInputs':records,
            'files':{name:{'sha256':d.train.sha256(self.paths[name]),'count':len(values),
                           'domains':dict(Counter(r['domain'] for r in values)),
                           'sourceWords':sum(len(r['source'].split()) for r in values)} for name,values in self.data.items()}}
        self.write_json('dataset-manifest.json',self.manifest)

    @staticmethod
    def word(index):return chr(97+index//26)+chr(97+index%26)

    def finance(self,prefix,term,index):
        return {'id':prefix+'-'+term['id']+'-'+str(index),'domain':'finance','humanReviewed':False,
                'primaryTermId':term['id'],'source':term['source']+' sample '+self.word(index),
                'target':term['target']+' sample','terms':[{'id':term['id'],'source':term['source'],'target':term['target']}],
                'forbiddenTerms':[]}

    def general(self,prefix,index):
        return {'id':prefix+'-general-'+str(index),'domain':'general','humanReviewed':False,'terms':[],
                'source':'An ordinary story '+self.word(index),'target':'An ordinary story',
                'forbiddenTerms':[{'target':'financialinjection','reason':'Synthetic ordinary context.'}]}

    def write_json(self,name,value):
        (self.root/name).write_text(json.dumps(value),'utf-8')

    def write_rows(self,name,values):
        (self.root/name).write_text(''.join(json.dumps(r)+'\n' for r in values),'utf-8')

    def tearDown(self):
        self.patch.stop()
        assert self.root.is_relative_to(self.original_root) and self.root.name.startswith('unit-v5-publication-')
        self.temp.cleanup()

    def test_full_reviewed_publication_passes_without_models(self):
        result=d.verify_published_dataset(self.paths,self.root)
        self.assertEqual(result['manifestSha256'],d.train.sha256(self.root/'dataset-manifest.json'))

    def test_unreviewed_path_and_changed_dependency_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'final published'):
            d.verify_published_dataset({**self.paths,'dev':self.root/'dev-candidate.jsonl'},self.root)
        self.manifest['codeFiles']['dataset_v5.py']='changed'
        self.write_json('dataset-manifest.json',self.manifest)
        with self.assertRaisesRegex(ValueError,'dependency code'):
            d.verify_published_dataset(self.paths,self.root)

    def test_empty_review_evidence_is_not_a_completed_review(self):
        receipt=d.read_json(self.root/'dev-review-receipt.json')
        receipt['evidenceFiles']={}
        self.write_json('dev-review-receipt.json',receipt)
        with self.assertRaisesRegex(ValueError,'required input/review evidence'):
            d.reviewed_input('dev','dev',self.catalog,self.root)

    def test_rehashed_final_reference_must_still_match_reviewed_input(self):
        self.data['dev'][0]['target']='unreviewed change'
        self.write_rows('dev.jsonl',self.data['dev'])
        self.manifest['files']['dev']['sha256']=d.train.sha256(self.paths['dev'])
        self.write_json('dataset-manifest.json',self.manifest)
        with self.assertRaisesRegex(ValueError,'differ from their reviewed input'):
            d.verify_published_dataset(self.paths,self.root)

    def test_existing_sealed_test_schema_is_verified_without_rewriting_it(self):
        review=d.read_json(self.root/'test-second-review.json')
        legacy={k:review[k] for k in ('humanReviewed','sourceGrounded','inputSha256','outputSha256')}
        legacy.update({'schemaVersion':'finance-v5-test-second-review-v1',
            'reviewer':'independent_assistant_review_agent_v5_test_review','status':'pass_after_prefreeze_corrections',
            'coverage':{'rowCount':140},'reviewScope':{'reviewedRowIds':review['reviewedIDs']}})
        self.write_json('test-second-review.json',legacy)
        receipt=d.read_json(self.root/'test-review-receipt.json')
        receipt['evidenceFiles']['test-second-review.json']=d.train.sha256(self.root/'test-second-review.json')
        self.write_json('test-review-receipt.json',receipt)
        _,record=d.reviewed_input('test','test',self.catalog,self.root)
        self.manifest['reviewedInputs']['test']=record
        self.write_json('dataset-manifest.json',self.manifest)
        self.assertTrue(d.verify_published_dataset(self.paths,self.root))

    def test_source_grounded_alternate_financial_sense_keeps_primary_term(self):
        catalog=[{'id':'FV','source':'future value','target':'미래가치','aliases':[]},
                 {'id':'RR','source':'reinvestment rate','target':'재투자율','aliases':[]}]
        row={'id':'fixture','split':'train','domain':'finance','humanReviewed':False,
             'primaryTermId':'FV','source':'The future value of savings depends on the reinvestment rate.',
             'target':'저축의 미래가치는 재투자 금리에 달려 있다.',
             'terms':[{'id':'FV','source':'future value','target':'미래가치'}],
             'excludedTermIds':{'RR':'Interest earned on reinvested savings, not the corporate investment fraction.'},
             'forbiddenTerms':[{'target':'재투자율','reason':'A different financial concept is intended.'}]}
        self.assertTrue(d.validate_rows([row],catalog,'train')['passed'])
        row['target']='저축의 미래가치는 재투자율에 달려 있다.'
        self.assertIn('forbidden-term-in-reference',d.validate_rows([row],catalog,'train')['issues'][0]['issues'])


if __name__=='__main__':
    unittest.main()
