"""Prepare a NEW CPU environment and immutable model files; never loads a model."""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from common import (CANDIDATE, ENCODER_ID, ENCODER_REVISION, MODEL_ID,
                    MODEL_REVISION, MODEL_SHA, QE_ROOT, ROOT, VERSION,
                    file_record, read_json, sha_file, write_json)


def fetch(url):
    if os.name == 'nt':
        content = subprocess.check_output(['curl.exe', '--tlsv1.2', '--tls-max', '1.2',
            '--fail', '--location', '--silent', '--show-error', '--retry', '2', '--retry-all-errors',
            '--retry-delay', '2', '--connect-timeout', '30', '--max-time', '120', url])
        return io.BytesIO(content)
    request = urllib.request.Request(url, headers={'User-Agent': 'MoonModaran-local-qe-setup/1'})
    return urllib.request.urlopen(request, timeout=90)


def download(repo, revision, names, destination):
    with fetch(f'https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true') as response:
        metadata = json.load(response)
    if metadata['sha'] != revision or metadata.get('gated'):
        raise RuntimeError('revision_mismatch_or_gated_model')
    record_path = destination / 'publisher-metadata.json'
    if not record_path.exists():
        write_json(record_path, metadata)
    siblings = {item['rfilename']: item for item in metadata['siblings']}
    records = []
    for name in names:
        item = siblings[name]
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = path.with_suffix(path.suffix + '.partial')
            # A previous partial file is not a valid model. Resume by explicit HTTP Range.
            offset = temporary.stat().st_size if temporary.exists() else 0
            headers = {'User-Agent': 'MoonModaran-local-qe-setup/1'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            request = urllib.request.Request(f'https://huggingface.co/{repo}/resolve/{revision}/{name}', headers=headers)
            print(json.dumps({'event': 'download', 'file': name, 'bytes': item.get('size'), 'resumeBytes': offset}), flush=True)
            if os.name == 'nt':
                subprocess.run(['curl.exe', '--tlsv1.2', '--tls-max', '1.2', '--fail', '--location',
                    '--silent', '--show-error', '--retry', '2', '--retry-all-errors', '--retry-delay', '2',
                    '--connect-timeout', '30', '--max-time', '1800', '--continue-at', '-',
                    '--output', str(temporary), f'https://huggingface.co/{repo}/resolve/{revision}/{name}'], check=True)
            for attempt in range(0 if os.name == 'nt' else 3):
                offset = temporary.stat().st_size if temporary.exists() else 0
                headers = {'User-Agent': 'MoonModaran-local-qe-setup/1'}
                if offset:
                    headers['Range'] = f'bytes={offset}-'
                request = urllib.request.Request(f'https://huggingface.co/{repo}/resolve/{revision}/{name}', headers=headers)
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        append = offset > 0 and response.status == 206
                        if append and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                            raise RuntimeError('unexpected_download_range')
                        with temporary.open('ab' if append else 'wb') as stream:
                            while chunk := response.read(1024 * 1024):
                                stream.write(chunk)
                    break
                except (OSError, urllib.error.URLError):
                    if attempt == 2:
                        raise
                    print(json.dumps({'event': 'retry_transient_download', 'file': name, 'attempt': attempt + 1}), flush=True)
                    time.sleep(2 * (attempt + 1))
            if temporary.stat().st_size != item['size']:
                raise RuntimeError('download_size_mismatch:' + name)
            expected = item.get('lfs', {}).get('sha256')
            if expected and sha_file(temporary) != expected:
                raise RuntimeError('download_hash_mismatch:' + name)
            temporary.rename(path)
        if path.stat().st_size != item['size']:
            raise RuntimeError('existing_size_mismatch:' + name)
        expected = item.get('lfs', {}).get('sha256')
        if expected and sha_file(path) != expected:
            raise RuntimeError('existing_hash_mismatch:' + name)
        records.append(file_record(path))
    return records


def environment():
    directory = ROOT / '.venv-qe'
    python = directory / 'Scripts' / 'python.exe'
    QE_ROOT.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(directory)], check=True)
    marker = directory / '.gitignore'
    if not marker.exists():
        marker.write_text('*\n', encoding='utf-8')
    report = QE_ROOT / 'install-packages.json'
    if not report.exists():
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        'torch==2.8.0+cpu', '--index-url', 'https://download.pytorch.org/whl/cpu',
                        '--report', str(QE_ROOT / 'install-torch.json')], check=True)
        pinned = ROOT / 'scripts/local-qe/requirements.windows.lock.txt'
        package_args = ['--require-hashes', '--extra-index-url', 'https://download.pytorch.org/whl/cpu', '-r', str(pinned)] if pinned.exists() else ['-r', str(ROOT / 'scripts/local-qe/requirements.txt')]
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        *package_args, '--report', str(report)], check=True)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    freeze = subprocess.check_output([str(python), '-m', 'pip', 'freeze'], text=True)
    lock_path = QE_ROOT / 'requirements.lock.txt'
    if lock_path.exists() and lock_path.read_text(encoding='utf-8') != freeze:
        raise RuntimeError('existing_environment_lock_mismatch')
    if not lock_path.exists():
        lock_path.write_text(freeze, encoding='utf-8', newline='\n')
    records = [file_record(path) for path in (lock_path, QE_ROOT / 'install-torch.json', report, ROOT / 'scripts/local-qe/requirements.txt')]
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment-only', action='store_true')
    parser.add_argument('--model-only', action='store_true')
    args = parser.parse_args()
    env_records = [] if args.model_only else environment()
    if args.environment_only:
        return
    manifest_path = CANDIDATE / 'manifest.json'
    if manifest_path.exists():
        print(json.dumps({'status': 'already_prepared', 'manifest': str(manifest_path)}))
        return
    model_files = download(MODEL_ID, MODEL_REVISION, ['LICENSE', 'README.md', 'hparams.yaml', 'checkpoints/model.ckpt'], CANDIDATE / 'model')
    encoder_files = download(ENCODER_ID, ENCODER_REVISION,
                             ['README.md', 'config.json', 'sentencepiece.bpe.model', 'tokenizer.json', 'tokenizer_config.json'],
                             CANDIDATE / 'encoder')
    if not env_records:
        env_records = [file_record(QE_ROOT / name) for name in ('requirements.lock.txt', 'install-torch.json', 'install-packages.json') if (QE_ROOT / name).exists()]
    write_json(manifest_path, {'schemaVersion': 1, 'version': VERSION, 'status': 'prepared_unregistered',
        'modelId': MODEL_ID, 'modelRevision': MODEL_REVISION, 'modelHash': MODEL_SHA,
        'modelFiles': model_files, 'encoderId': ENCODER_ID, 'encoderRevision': ENCODER_REVISION,
        'encoderFiles': encoder_files, 'environmentFiles': env_records,
        'checkpointPath': (CANDIDATE / 'model/checkpoints/model.ckpt').relative_to(ROOT).as_posix(),
        'encoderPath': (CANDIDATE / 'encoder').relative_to(ROOT).as_posix(),
        'referenceRequired': False, 'contextUsed': False, 'spanSupport': False,
        'license': 'Apache-2.0', 'gated': False, 'scoreMeaning': 'raw ranking score; not error probability',
        'sourceLanguage': 'en', 'targetLanguage': 'ko', 'device': 'cpu', 'dtype': 'float32',
        'actualModelLoaded': False, 'qualityGatePassed': False, 'appRegistered': False})
    print(json.dumps({'status': 'prepared_unregistered', 'manifest': str(manifest_path)}))


if __name__ == '__main__':
    main()
