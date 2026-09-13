// Shared DTO only: no database, model files or server imports in the reader.
export type QualityCategory = 'quantity_formula' | 'semantic_role' | 'negation_condition' | 'word_sense' | 'omission' | 'general_low_confidence';
export type QualitySpan = { start: number; end: number; text: string };
export type QualityFinding = {
  category: QualityCategory; severity: 'warning' | 'major' | 'critical'; reason: string;
  detector: 'rule' | 'qe'; ruleId?: string; target: QualitySpan | null; source: QualitySpan | null;
};
export type QualityAssessment = {
  id: string; status: 'queued' | 'running' | 'completed' | 'unavailable' | 'failed' | 'cancelled' | 'stale';
  risk: 'review' | 'no_findings' | 'unknown'; sourceHash: string; translationHash: string;
  modelIdentity: string; calibrationVersion: string; score: number | null; findings: QualityFinding[];
  message: string; createdAt: string; jobId?: string; scope: 'block';
};
