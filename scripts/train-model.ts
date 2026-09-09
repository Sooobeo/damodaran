import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const [stage = 'train', ...args] = process.argv.slice(2);
const setup = stage === 'setup';
const python = setup ? (process.env.TRAINING_SETUP_PYTHON || (process.platform === 'win32' ? 'py' : 'python3'))
  : path.join(root, '.venv-training', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
if (!setup && !fs.existsSync(python)) {
  console.error('학습 환경을 먼저 설치하세요: npm run setup:training');
  process.exit(1);
}
const prefix = setup && python === 'py' ? ['-3.11'] : [];
const direct = ['setup', 'infer', 'test', 'export'].includes(stage);
const deploy = ['parity', 'register'].includes(stage);
const script = path.join(root, 'scripts/model-training', setup ? 'setup.py' : stage === 'infer' ? 'infer.py' : stage === 'export' ? 'export_model.py' : stage === 'test' ? 'test_training.py' : deploy ? 'deploy.py' : 'train.py');
const command = stage === 'test' ? ['-m', 'unittest', 'discover', '-s', path.join(root, 'scripts/model-training'), '-p', 'test_*.py', ...args]
  : [script, ...(direct ? [] : [stage]), ...args];
const child = spawn(python, [...prefix, '-X', 'utf8', '-u', ...command], {
  cwd: root, windowsHide: true, stdio: 'inherit',
  env: { ...process.env, OPENAI_API_KEY: '', PYTHONUTF8: '1', TOKENIZERS_PARALLELISM: 'false' },
});
child.on('error', error => { console.error(`학습 실행 실패: ${error.message}`); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
