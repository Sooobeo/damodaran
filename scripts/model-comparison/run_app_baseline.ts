/** Compare the real Argos application pipeline without opening an application DB. */
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const boundary = path.join(root, '.training/comparisons');
const hash = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
function option(args: string[], name: string) {
  const index = args.indexOf(name);
  requireValue(index >= 0 && args[index + 1] && !args[index + 1].startsWith('--'), `${name} is required`);
  return args[index + 1];
}
function inside(value: string) {
  const result = path.resolve(root, value);
  requireValue(result.startsWith(boundary + path.sep), 'Comparison path must stay in .training/comparisons');
  let cursor = result;
  while (cursor !== boundary) {
    if (fs.existsSync(cursor)) requireValue(!fs.lstatSync(cursor).isSymbolicLink(), 'Linked comparison paths are prohibited');
    cursor = path.dirname(cursor);
  }
  return result;
}
type InputRow = { id: string; source: string; context: string; sourceSha256: string; contextSha256: string };
type ReviewEvidence = { file: string; path: string; sha256: string };
function limitedBytes(file: string) {
  requireValue(fs.statSync(file).size <= 2 * 1024 * 1024, 'Comparison metadata exceeds its limit');
  const bytes = fs.readFileSync(file);
  requireValue(bytes.length <= 2 * 1024 * 1024, 'Comparison metadata exceeds its limit');
  return bytes;
}
export function readFrozenInput(value: string) {
  const input = inside(value);
  requireValue(path.basename(input) === 'dataset.jsonl', 'The comparison input must be dataset.jsonl');
  const inputBytes = limitedBytes(input), inputSha256 = hash(inputBytes);
  const manifestPath = inside(path.join(path.dirname(input), 'dataset-manifest.json'));
  const manifestBytes = limitedBytes(manifestPath), manifest = JSON.parse(manifestBytes.toString('utf8'));
  const rawRows = inputBytes.toString('utf8').split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
  requireValue(rawRows.length === 24 && rawRows.every(row => row && typeof row === 'object' && !Array.isArray(row)), 'Invalid comparison rows');
  const ids = rawRows.map(row => row.id);
  requireValue(manifest.version === 'finance-quality-20260910-v1' && manifest.status === 'frozen'
    && manifest.sourceType === 'assistant_authored_unreviewed' && manifest.humanReviewed === false
    && manifest.dataset?.file === path.basename(input) && manifest.dataset.sha256 === inputSha256
    && manifest.dataset.count === 24 && JSON.stringify(manifest.dataset.ids) === JSON.stringify(ids), 'Dataset is not the frozen comparison');
  requireValue(ids.every(id => typeof id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(id))
    && new Set(ids).size === ids.length, 'Invalid or duplicate comparison IDs');
  const rows: InputRow[] = rawRows.map(row => {
    const context = row.context === undefined ? '' : row.context;
    requireValue(row.split === 'exploratory_probe' && typeof row.source === 'string' && row.source.trim()
      && row.source.length <= 12000 && typeof context === 'string' && context.length <= 12000
      && hash(row.source) === row.sourceSha256, 'Invalid comparison source identity');
    const contextSha256 = hash(context);
    requireValue(row.contextSha256 === undefined || row.contextSha256 === contextSha256, 'Invalid comparison context identity');
    // Only this allowlist reaches the translation pipeline; reference answers stay out.
    return { id: row.id, source: row.source, context, sourceSha256: row.sourceSha256, contextSha256 };
  });
  requireValue(Array.isArray(manifest.reviewFiles) && manifest.reviewFiles.length > 0, 'Missing frozen review evidence');
  const names = new Set<string>();
  const reviewFiles: ReviewEvidence[] = manifest.reviewFiles.map((item: { file?: unknown; sha256?: unknown }) => {
    requireValue(item && typeof item.file === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(item.file)
      && !names.has(item.file.toLowerCase()) && typeof item.sha256 === 'string' && /^[a-f0-9]{64}$/.test(item.sha256), 'Invalid frozen review reference');
    names.add(item.file.toLowerCase());
    const reviewPath = inside(path.join(path.dirname(input), item.file));
    requireValue(hash(limitedBytes(reviewPath)) === item.sha256, 'Frozen review evidence changed');
    return { file: item.file, path: reviewPath, sha256: item.sha256 };
  });
  return { input, inputSha256, manifestPath, manifestSha256: hash(manifestBytes), reviewFiles, rows };
}
export function verifyFrozenInput(input: ReturnType<typeof readFrozenInput>) {
  const files = [[input.input, input.inputSha256], [input.manifestPath, input.manifestSha256],
    ...input.reviewFiles.map(item => [item.path, item.sha256])];
  requireValue(files.every(([file, expected]) => hash(limitedBytes(inside(file))) === expected), 'Frozen comparison evidence changed');
}

async function main() {
const args = process.argv.slice(2);
requireValue(args.length === 4 && args.filter(arg => arg === '--input').length === 1
  && args.filter(arg => arg === '--output').length === 1, 'Use only --input and --output');
const frozen = readFrozenInput(option(args, '--input'));
const { inputSha256, rows } = frozen;
const output = inside(option(args, '--output'));
requireValue(!fs.existsSync(output), 'Preserve existing comparison outputs; choose a fresh output directory');
// Hash application code before importing it, then check it before and after use.
const codeFiles = ['scripts/model-comparison/run_app_baseline.ts', 'lib/translation/index.ts', 'lib/translation/local.ts',
  'lib/translation/runtime.ts', 'lib/translation/glossary.ts', 'lib/translation/memory.ts', 'lib/config.ts',
  'lib/db/index.ts', 'lib/sources/index.ts', 'scripts/local-translation/bridge.py', 'scripts/local-translation/runtime.py',
  'content/index.ts', 'content/translation-glossary.json', 'package-lock.json'];
const codeHashes = Object.fromEntries(codeFiles.map(file => [file, hash(fs.readFileSync(path.join(root, file)))]));
const verifyCode = () => requireValue(codeFiles.every(file => hash(fs.readFileSync(path.join(root, file))) === codeHashes[file]), 'Application pipeline changed during comparison');

// Dynamic imports follow the process-only environment overrides. No .env file
// is modified, no API key is used, and a DB access would fail in this empty path.
process.env.APP_ROOT = root;
process.env.TRANSLATION_PROVIDER = 'argos';
process.env.OPENAI_API_KEY = '';
process.env.DATA_DIR = path.join(output, 'unused-data-dir');
requireValue(process.env.NODE_ENV !== 'test', 'The real baseline cannot run with a test provider');
const { translationRuntime } = await import('../../lib/translation/runtime');
const { translateSnapshot } = await import('../../lib/translation/index');
const { closeLocalTranslator } = await import('../../lib/translation/local');
const { selectTranslationGlossary } = await import('../../lib/translation/glossary');
const { glossary } = await import('../../content/index');
const runtime = translationRuntime();
requireValue(runtime.provider === 'argos' && runtime.configured, 'The real Argos runtime must be installed');
const studyTerms = glossary.map(term => ({ id: term.id, term_en: term.termEn, term_ko: term.termKo,
  acronym: term.acronym ?? null, aliases_json: JSON.stringify(term.aliases), notes: term.notes ?? null, revision: term.revision }));
verifyFrozenInput(frozen);
verifyCode();
fs.mkdirSync(output, { recursive: true });
fs.writeFileSync(path.join(output, 'start.json'), JSON.stringify({ version: 1, startedAt: new Date().toISOString(),
  inputSha256, datasetManifestSha256: frozen.manifestSha256, reviewFiles: frozen.reviewFiles, codeHashes, runtime, inputCount: rows.length,
  profile: 'argos-app-pipeline', sourceType: 'assistant_authored_unreviewed', humanReviewed: false,
  glossaryApplied: true, numberFormulaProtectionApplied: true, translationMemoryApplied: false,
  contextCollected: true, contextPassedToModel: false, paidCalls: 0, appDeploymentPerformed: false }, null, 2) + '\n', { flag: 'wx' });
const resultPath = path.join(output, 'predictions.jsonl');
const descriptor = fs.openSync(resultPath, 'wx');
const results: Record<string, unknown>[] = [];
const started = performance.now();
let fatal: string | null = null;
let integrityVerified = false;
try {
  for (const row of rows) {
    const tick = performance.now();
    const selected = selectTranslationGlossary(row.source, row.context, studyTerms);
    let result: Record<string, unknown>;
    try {
      const value = await translateSnapshot({ blockId: row.id, sourceVersionId: 'independent-comparison',
        resourceId: 'assistant-authored-comparison', text: row.source, type: 'paragraph', structure: {},
        context: row.context, contextHash: hash(row.context), ...selected, cacheKey: 'not-used-no-db',
        provider: 'argos', model: runtime.identity, promptVersion: runtime.promptVersion });
      result = { id: row.id, sourceSha256: row.sourceSha256, contextSha256: hash(row.context), inputSha256,
        status: 'generated', translation: value.textKo, warnings: value.warnings,
        validationStatus: value.warnings.length ? 'needs_review' : 'passed',
        emptyOutput: !value.textKo.trim(), inputTokens: null, generatedTokens: null,
        outputLimitReached: false, modelIdentity: runtime.identity };
    } catch (error) {
      result = { id: row.id, sourceSha256: row.sourceSha256, contextSha256: hash(row.context), inputSha256,
        status: 'failed', translation: null, errorType: error instanceof Error ? error.name : 'UnknownError' };
    }
    result.generationSeconds = (performance.now() - tick) / 1000;
    results.push(result);
    fs.writeSync(descriptor, JSON.stringify(result) + '\n'); fs.fsyncSync(descriptor);
    console.log(JSON.stringify({ event: 'comparison-progress', id: row.id, status: result.status, seconds: result.generationSeconds }));
  }
  verifyFrozenInput(frozen);
  verifyCode();
  requireValue(translationRuntime().identity === runtime.identity, 'Runtime changed during comparison');
  requireValue(!fs.existsSync(process.env.DATA_DIR), 'Unexpected application data access');
  integrityVerified = true;
} catch (error) {
  fatal = error instanceof Error ? error.name : 'UnknownError';
} finally {
  fs.closeSync(descriptor);
  closeLocalTranslator();
}
const summary = { status: fatal || !integrityVerified ? 'failed' : results.length === rows.length && results.every(row => row.status === 'generated') ? 'complete' : 'partial',
  finishedAt: new Date().toISOString(), inputSha256, resultSha256: hash(fs.readFileSync(resultPath)),
  generatedCount: results.filter(row => row.status === 'generated').length, count: rows.length,
  warningRows: results.filter(row => row.validationStatus === 'needs_review').length,
  totalSeconds: (performance.now() - started) / 1000, errorType: fatal, integrityVerified,
  databaseOpened: fs.existsSync(path.join(process.env.DATA_DIR, 'library.sqlite')),
  translationMemoryApplied: false, humanReviewed: false, paidCalls: 0, appDeploymentPerformed: false };
fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(summary, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify(summary));
if (summary.status !== 'complete') process.exitCode = 1;
}

const entryPath = process.argv[1] ? path.resolve(process.argv[1]) : '';
const scriptPath = fileURLToPath(import.meta.url);
if ((process.platform === 'win32' ? entryPath.toLowerCase() === scriptPath.toLowerCase() : entryPath === scriptPath)) await main();
