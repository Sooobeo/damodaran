"""Repair the upstream source-vocabulary metadata, preserving original weights."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
import sentencepiece as spm
from setup import ROOT, TRAINING, REPO, REVISION, WEIGHTS_HASH, digest

VERSION = 'separate-spm-vocab-v1'
SPM_HASHES = {
    'source.spm': '3d0591e65c49541d82f48df33d7b322c3d4ee7aa0ee8747f9a7f9355dbf22c95',
    'target.spm': '3d2aa641a0890d8966ab8703b109895a4e522713ce99b4a0192bfacb495bc97c',
}

def prepare():
    base = TRAINING / 'base-model'
    target = TRAINING / 'prepared-model'
    source_manifest = json.loads((base / 'base-manifest.json').read_text('utf-8'))
    if source_manifest['revision'] != REVISION or digest(base / 'model.safetensors') != WEIGHTS_HASH:
        raise ValueError('Pinned base model differs')
    for record in source_manifest['files']:
        path = base / record['name']
        if path.parent != base or digest(path) != record['sha256']:
            raise ValueError('Original model file integrity failed')
    for name, expected in SPM_HASHES.items():
        if digest(base / name) != expected:
            raise ValueError('Pinned SentencePiece model differs')
    if target.exists():
        manifest = json.loads((target / 'prepared-manifest.json').read_text('utf-8'))
        if manifest['preparationVersion'] != VERSION or manifest['baseWeightSha256'] != WEIGHTS_HASH:
            raise ValueError('Existing prepared model uses another configuration')
        for item in manifest['files']:
            path = target / item['name']
            if path.parent != target or digest(path) != item['sha256']:
                raise ValueError('Existing prepared model integrity failed')
        print(json.dumps({'prepared': str(target.relative_to(ROOT)), 'reused': True}))
        return
    staged = TRAINING / ('prepared-model.stage-' + uuid.uuid4().hex)
    staged.mkdir()
    for record in source_manifest['files']:
        shutil.copy2(base / record['name'], staged / record['name'])
    config = json.loads((base / 'config.json').read_text('utf-8'))
    vocabularies = {}
    for name, vocab_name in [('source.spm', 'vocab.json'), ('target.spm', 'target_vocab.json')]:
        tokenizer = spm.SentencePieceProcessor(model_file=str(base / name))
        vocab = {tokenizer.id_to_piece(index): index for index in range(tokenizer.get_piece_size())}
        if config['pad_token_id'] != tokenizer.get_piece_size():
            raise ValueError('Unexpected Marian padding token')
        vocab['<pad>'] = config['pad_token_id']
        vocabularies[vocab_name] = vocab
        (staged / vocab_name).write_text(json.dumps(vocab, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    original = json.loads((base / 'vocab.json').read_text('utf-8'))
    if vocabularies['target_vocab.json'] != original:
        raise ValueError('Original decoder vocabulary does not match pinned target SentencePiece')
    tokenizer_config = json.loads((base / 'tokenizer_config.json').read_text('utf-8'))
    tokenizer_config['separate_vocabs'] = True
    (staged / 'tokenizer_config.json').write_text(json.dumps(tokenizer_config, indent=2) + '\n', 'utf-8')
    files = [{'name': p.name, 'size': p.stat().st_size, 'sha256': digest(p)}
             for p in sorted(staged.iterdir()) if p.is_file()]
    manifest = {
        'schemaVersion': 1, 'preparationVersion': VERSION, 'baseRepository': REPO,
        'baseRevision': REVISION, 'baseWeightSha256': WEIGHTS_HASH,
        'weightTrainingPerformed': False, 'separateVocabs': True,
        'correction': 'Rebuild English encoder vocabulary from pinned source.spm IDs; retain Korean decoder IDs from target.spm and declare separate vocabularies.',
        'originalSourceIdMismatchCount': sum(original.get(piece) != index for piece, index in vocabularies['vocab.json'].items()),
        'references': ['https://github.com/Helsinki-NLP/OPUS-MT-train/issues/81',
                       'https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-ko/discussions/7'],
        'files': files,
    }
    (staged / 'prepared-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', 'utf-8')
    if digest(staged / 'model.safetensors') != WEIGHTS_HASH:
        raise ValueError('Preparation must not change model weights')
    os.replace(staged, target)
    print(json.dumps({'prepared': str(target.relative_to(ROOT)), 'weightTrainingPerformed': False,
                      'originalSourceIdMismatchCount': manifest['originalSourceIdMismatchCount']}))

if __name__ == '__main__':
    prepare()
