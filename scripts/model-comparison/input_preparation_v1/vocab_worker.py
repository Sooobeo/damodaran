"""Explicit child-only tokenizer: pinned libllama ABI, vocabulary only, no context/decode."""
from __future__ import annotations
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT/'content/model-comparison/input-preparation-v1/audit-identity.json'
VERSION = 'hymt-vocab-only-ctypes-b10874-v1'
HEADER_SHA = '3d1b18eda626c1b9ecf5bda0798e65b974a8f70f30d56726610633a980cb4160'
SOURCE_MANIFEST = ROOT/'.training/verifications/input-preparation-v1-s2-tokenizer-source-20260913/manifest.json'

def require(value, code):
    if not value: raise ValueError(code)

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

class OverrideValue(C.Union):
    _fields_=[('val_i64',C.c_int64),('val_f64',C.c_double),('val_bool',C.c_bool),('val_str',C.c_char*128)]

class Override(C.Structure):
    _anonymous_=('value',)
    _fields_=[('tag',C.c_int),('key',C.c_char*128),('value',OverrideValue)]

class ModelParams(C.Structure):
    _fields_=[('devices',C.c_void_p),('tensor_buft_overrides',C.c_void_p),('n_gpu_layers',C.c_int32),
        ('split_mode',C.c_int),('load_mode',C.c_int),('lazy_mode',C.c_int),('main_gpu',C.c_int32),
        ('tensor_split',C.c_void_p),('progress_callback',C.c_void_p),('progress_callback_user_data',C.c_void_p),
        ('kv_overrides',C.POINTER(Override)),('vocab_only',C.c_bool),('check_tensors',C.c_bool),
        ('use_extra_bufts',C.c_bool),('no_host',C.c_bool),('no_alloc',C.c_bool),('load_mtp',C.c_bool)]

def abi_contract():
    require(C.sizeof(C.c_void_p)==8 and C.sizeof(ModelParams)==80 and C.sizeof(Override)==264,'unsupported_abi')
    expected={'devices':0,'tensor_buft_overrides':8,'n_gpu_layers':16,'split_mode':20,'load_mode':24,
        'lazy_mode':28,'main_gpu':32,'tensor_split':40,'progress_callback':48,
        'progress_callback_user_data':56,'kv_overrides':64,'vocab_only':72,'load_mtp':77}
    require(all(getattr(ModelParams,k).offset==v for k,v in expected.items()),'model_params_offset_mismatch')
    return {'modelParamsSize':80,'overrideSize':264,'offsets':expected,'headerSha256':HEADER_SHA}

class Vocab:
    def __init__(self):
        require(os.name=='nt','windows_only');self.model=None
        self.abi=abi_contract()
        header=SOURCE_MANIFEST.parent/'llama.h'
        require(sha(header)==HEADER_SHA,'pinned_header_changed')
        audit=json.loads(AUDIT.read_text('utf-8'));files=audit['files']
        model=next(f for f in files if f['category']=='modelFiles')
        self.model_path=ROOT/model['path']
        require(sha(self.model_path)==model['sha256'],'model_hash_changed')
        runtime_files=[f for f in files if f['category']=='runtimeFiles']
        self.runtime=(ROOT/next(f['path'] for f in runtime_files if f['path'].endswith('/llama.dll'))).parent
        for f in runtime_files:require(sha(ROOT/f['path'])==f['sha256'],'runtime_hash_changed')
        self.dll_dir=os.add_dll_directory(str(self.runtime))
        self.lib=C.CDLL(str(self.runtime/'llama.dll'))
        lib=self.lib
        signatures={
          'llama_model_default_params':([],ModelParams),
          'llama_backend_init':([],None),'llama_backend_free':([],None),
          'llama_model_load_from_file':([C.c_char_p,ModelParams],C.c_void_p),
          'llama_model_free':([C.c_void_p],None),'llama_model_get_vocab':([C.c_void_p],C.c_void_p),
          'llama_tokenize':([C.c_void_p,C.c_char_p,C.c_int32,C.POINTER(C.c_int32),C.c_int32,C.c_bool,C.c_bool],C.c_int32),
          'llama_detokenize':([C.c_void_p,C.POINTER(C.c_int32),C.c_int32,C.c_char_p,C.c_int32,C.c_bool,C.c_bool],C.c_int32),
          'llama_vocab_is_eog':([C.c_void_p,C.c_int32],C.c_bool)}
        for name in ['bos','eos','eot','n_tokens']:signatures['llama_vocab_'+name]=([C.c_void_p],C.c_int32)
        for name in ['get_add_bos','get_add_eos']:signatures['llama_vocab_'+name]=([C.c_void_p],C.c_bool)
        for name,(args,result) in signatures.items():
            fn=getattr(lib,name);fn.argtypes=args;fn.restype=result
        lib.llama_backend_init()
        params=lib.llama_model_default_params()
        params.vocab_only=True;params.n_gpu_layers=0;params.check_tensors=False;params.load_mtp=False
        self.devices=(C.c_void_p*1)(None);params.devices=C.cast(self.devices,C.c_void_p)
        self.overrides=(Override*5)()
        settings=[('tokenizer.ggml.eos_token_id',0,127960),('tokenizer.ggml.eot_token_id',0,127967),
                  ('tokenizer.ggml.add_bos_token',2,False),('tokenizer.ggml.add_eos_token',2,False)]
        for o,(key,tag,value) in zip(self.overrides,settings):
            o.key=key.encode();o.tag=tag
            if tag==0:o.val_i64=value
            else:o.val_bool=value
        params.kv_overrides=self.overrides
        self.model=lib.llama_model_load_from_file(str(self.model_path).encode('utf-8'),params)
        require(self.model,'vocab_load_failed');self.vocab=lib.llama_model_get_vocab(self.model)
        self.identity={k:int(getattr(lib,'llama_vocab_'+k)(self.vocab)) for k in ['bos','eos','eot','n_tokens']}
        self.identity.update(addBos=bool(lib.llama_vocab_get_add_bos(self.vocab)),addEos=bool(lib.llama_vocab_get_add_eos(self.vocab)))
        require(self.identity=={'bos':127958,'eos':127960,'eot':127967,'n_tokens':128167,'addBos':False,'addEos':False},'vocab_identity_mismatch')
        self.identity['eogIds']=[i for i in range(128167) if lib.llama_vocab_is_eog(self.vocab,i)]
        require(self.identity['eogIds']==[127957,127960,127967],'eog_mismatch')
        specials={3:'$',127957:'<|endoftext|>',127958:'<|startoftext|>',127960:'<|eos|>',127961:'<|pad|>',127962:'<|extra_0|>',127967:'<|extra_5|>'}
        for token,text in specials.items():require(self.tokenize(text)==[token],'special_id_mismatch')
        require(self.detokenize([3])=='$','dollar_roundtrip')

    def tokenize(self,text):
        require(isinstance(text,str) and '\0' not in text,'invalid_text')
        raw=text.encode('utf-8');require(len(raw)<=300000,'text_too_large')
        needed=self.lib.llama_tokenize(self.vocab,raw,len(raw),None,0,False,True)
        require(needed<=0,'token_size_contract');size=-needed
        output=(C.c_int32*max(1,size))()
        count=self.lib.llama_tokenize(self.vocab,raw,len(raw),output,size,False,True)
        require(count==size,'token_count_mismatch');return list(output)[:count]

    def detokenize(self,tokens):
        require(isinstance(tokens,list) and len(tokens)<=100000 and all(type(t)is int and 0<=t<128167 for t in tokens),'invalid_tokens')
        array=(C.c_int32*len(tokens))(*tokens)
        needed=self.lib.llama_detokenize(self.vocab,array,len(tokens),None,0,False,True)
        require(needed<=0,'detokenize_size_contract');size=-needed
        buf=C.create_string_buffer(size+1)
        count=self.lib.llama_detokenize(self.vocab,array,len(tokens),buf,size,False,True)
        require(count==size,'detokenize_count_mismatch');return buf.raw[:count].decode('utf-8')

    def close(self):
        if self.model:self.lib.llama_model_free(self.model);self.model=None
        self.lib.llama_backend_free();self.dll_dir.close()

def main():
    require(sys.argv[1:]==['--owned-worker'],'explicit_worker_flag_required')
    sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8',line_buffering=True)
    vocab=None;calls=0
    try:
        vocab=Vocab()
        print(json.dumps({'event':'ready','version':VERSION,'pid':os.getpid(),'vocabularyOnly':True,'contextCreated':False,
            'generationCalls':0,'vocab':vocab.identity,'abi':vocab.abi,'modelSha256':'58b3ad55dd6f6fa08c695cddc34fb5f8f708a844f78ae10508071914b0ed67c0'}))
        for line in sys.stdin:
            request=json.loads(line)
            require(set(request)=={'id','text'},'request_schema')
            tokens=vocab.tokenize(request['text']);calls+=1
            require(vocab.detokenize(tokens)==request['text'],'text_roundtrip_mismatch')
            print(json.dumps({'id':request['id'],'tokens':tokens,'count':len(tokens),'roundtrip':True}))
    finally:
        if vocab:vocab.close()
    print(json.dumps({'event':'closed','calls':calls,'generationCalls':0,'contextCreated':False}))

if __name__=='__main__':main()
