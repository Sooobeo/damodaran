"""Install an isolated training environment and a pinned, public Marian model."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
TRAINING=ROOT/'.training'
REPO='Helsinki-NLP/opus-mt-tc-big-en-ko'
REVISION='ae8606b7b29a495f31ce679cee2007f536a3a5ce'
WEIGHTS_HASH='f7d6ccf642f1672e6b06d46bc406a3f12220b70603f6745dfbce5c097f8511c2'
FILES=['README.md','config.json','generation_config.json','model.safetensors','source.spm','target.spm','special_tokens_map.json','tokenizer_config.json','vocab.json']

def digest(file):
    h=hashlib.sha256()
    with file.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def request(url):
    return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Damodaran-Local-Training/1.0'}),timeout=120)

def model():
    destination=TRAINING/'base-model';destination.mkdir(parents=True,exist_ok=True)
    with request(f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true') as response:metadata=json.load(response)
    if metadata['sha']!=REVISION:raise ValueError('The pinned model revision differs')
    records={item['rfilename']:item for item in metadata['siblings']}
    manifest=[]
    for name in FILES:
        record=records[name];expected=record.get('lfs',{}).get('sha256');target=destination/name
        if name=='model.safetensors' and expected!=WEIGHTS_HASH:raise ValueError('Unexpected model weight hash')
        valid=target.exists() and target.stat().st_size==record['size'] and (not expected or digest(target)==expected)
        if not valid:
            part=target.with_name(target.name+'.part')
            with request(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}') as response,part.open('wb') as output:
                size=0;last=time.monotonic()
                while chunk:=response.read(1024*1024):
                    output.write(chunk);size+=len(chunk)
                    if time.monotonic()-last>10:print(json.dumps({'downloading':name,'bytes':size,'total':record['size']}),flush=True);last=time.monotonic()
            if part.stat().st_size!=record['size'] or (expected and digest(part)!=expected):raise ValueError('Downloaded model integrity check failed')
            os.replace(part,target)
        manifest.append({'name':name,'size':target.stat().st_size,'sha256':digest(target)})
        print(json.dumps({'verified':name,'bytes':target.stat().st_size}),flush=True)
    (destination/'base-manifest.json').write_text(json.dumps({'repository':REPO,'revision':REVISION,'license':'CC-BY-4.0','files':manifest},indent=2),'utf-8')

def environment(backend):
    directory=ROOT/'.venv-training';python=directory/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():subprocess.run([sys.executable,'-m','venv',str(directory)],check=True)
    subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','--no-compile',f'torch==2.8.0+{backend}','--index-url',f'https://download.pytorch.org/whl/{backend}'],check=True)
    subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','--no-compile','-r',str(ROOT/'scripts/model-training/requirements.txt')],check=True)
    lock=subprocess.check_output([str(python),'-m','pip','freeze'],text=True)
    (TRAINING/'requirements.lock.txt').write_text(lock,'utf-8')
    subprocess.run([str(python),'-m','pip','check'],check=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--model-only',action='store_true');parser.add_argument('--backend',choices=['xpu','cpu'],default='xpu');args=parser.parse_args()
    TRAINING.mkdir(exist_ok=True)
    lock=TRAINING/'setup.lock'
    with lock.open('x') as handle:handle.write(str(os.getpid()))
    try:
        if not args.model_only:environment(args.backend)
        model()
        if not args.model_only:
            python=ROOT/'.venv-training'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            subprocess.run([str(python),str(ROOT/'scripts/model-training/prepare_tokenizer.py')],check=True)
    finally:lock.unlink(missing_ok=True)

if __name__=='__main__':main()
