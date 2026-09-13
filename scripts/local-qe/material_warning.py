"""Prospective material-risk mapping; consumes an already validated Qwen response.

Does not change the model, its v1 response validator or earlier assessments.
Localized assertions are only candidates for separate semantic span review.
"""
from pathlib import Path
import hashlib
import json

POLICY_PATH = Path(__file__).with_name('qwen-material-warning-policy-v2.json')
VERSION = 'qwen-material-warning-policy-v2'


def classify(validated):
    if validated.get('status') != 'valid':
        return {'version': VERSION, 'status': 'unassessed', 'primaryWarning': None,
                'supplementaryMajorWarning': None, 'noMaterialRiskAsserted': False,
                'redCandidateIndices': [], 'semanticSpanPrecision': 'not_evaluated'}
    content = validated['assessment']
    issues, uncertainties, notes = (content[key] for key in ('semantic_issues', 'uncertainties', 'language_notes'))
    if not all(type(items) is list for items in (issues, uncertainties, notes)) or any(
            type(issue) is not dict or issue.get('severity') not in ('minor', 'major', 'critical') for issue in issues):
        raise ValueError('response_must_first_pass_strict_contract_validation')
    major_indices = [i for i, issue in enumerate(issues) if issue['severity'] in ('major', 'critical')]
    diagnostics = validated['span_diagnostics']
    located_targets = {item['index'] for item in diagnostics if item['collection'] == 'semantic_issues'
                       and item['side'] == 'translation' and item['status'] == 'unique'}
    warning = bool(major_indices or uncertainties)
    return {'version': VERSION, 'status': 'assessed_model_assertions', 'primaryWarning': warning,
            'supplementaryMajorWarning': bool(major_indices), 'noMaterialRiskAsserted': not warning,
            'majorCriticalAssertionCount': len(major_indices), 'minorAssertionCount': len(issues) - len(major_indices),
            'uncertaintyCount': len(uncertainties), 'languageNoteCount': len(notes),
            'unlocalizedMajorCriticalCount': sum(i not in located_targets for i in major_indices),
            'redCandidateIndices': [i for i in major_indices if i in located_targets],
            'semanticSpanPrecision': 'not_evaluated',
            'v1WholeParagraphReview': validated['whole_paragraph_review'],
            'allQuoteDiagnosticsCount': len(diagnostics),
            'allUnverifiedQuoteCount': sum(item['status'] != 'unique' for item in diagnostics)}


def identity():
    raw = POLICY_PATH.read_bytes()
    policy = json.loads(raw.decode('utf-8'))
    if policy['version'] != VERSION:
        raise ValueError('material_warning_policy_version_mismatch')
    return {'version': VERSION, 'policySha256': hashlib.sha256(raw).hexdigest(),
            'codeSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
