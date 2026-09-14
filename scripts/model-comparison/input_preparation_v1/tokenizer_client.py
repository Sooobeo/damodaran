"""Bounded, owned vocabulary-only Hy7 child. No HTTP server or generation API."""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'scripts/local-hymt'))
from process_owner import claim_process_owner, _ExtendedLimits

VERSION = 'input-preparation-v1-owned-vocabulary-client-v1'
CAP_BYTES = 1024**3

def require(value, code):
    if not value: raise ValueError(code)

def utc(): return datetime.now(timezone.utc).isoformat()
def digest(raw): return hashlib.sha256(raw).hexdigest()

def write_json(path, value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')

class MemoryStatus(C.Structure):
    _fields_=[('length',W.DWORD),('load',W.DWORD)]+[(n,C.c_ulonglong) for n in
        ('totalPhys','availPhys','totalPageFile','availPageFile','totalVirtual','availVirtual','availExtendedVirtual')]

class MemoryCounters(C.Structure):
    _fields_=[('cb',W.DWORD),('pageFaultCount',W.DWORD)]+[(n,C.c_size_t) for n in
        ('peakWorkingSetSize','workingSetSize','quotaPeakPagedPoolUsage','quotaPagedPoolUsage',
         'quotaPeakNonPagedPoolUsage','quotaNonPagedPoolUsage','pagefileUsage','peakPagefileUsage','privateUsage')]

def system_memory():
    api=C.WinDLL('kernel32',use_last_error=True);fn=api.GlobalMemoryStatusEx
    fn.argtypes=[C.POINTER(MemoryStatus)];fn.restype=W.BOOL
    result=MemoryStatus();result.length=C.sizeof(result)
    require(fn(C.byref(result)),'memory_status_failed')
    return {name:int(getattr(result,name)) for name in ('totalPhys','availPhys','totalPageFile','availPageFile')}

def process_probe(process):
    kernel=C.WinDLL('kernel32',use_last_error=True);psapi=C.WinDLL('psapi',use_last_error=True)
    kernel.GetProcessTimes.argtypes=[W.HANDLE,*([C.POINTER(W.FILETIME)]*4)];kernel.GetProcessTimes.restype=W.BOOL
    kernel.GetPriorityClass.argtypes=[W.HANDLE];kernel.GetPriorityClass.restype=W.DWORD
    times=[W.FILETIME() for _ in range(4)]
    require(kernel.GetProcessTimes(int(process._handle),*[C.byref(t) for t in times]),'process_times_failed')
    counters=MemoryCounters();counters.cb=C.sizeof(counters)
    psapi.GetProcessMemoryInfo.argtypes=[W.HANDLE,C.POINTER(MemoryCounters),W.DWORD]
    psapi.GetProcessMemoryInfo.restype=W.BOOL
    require(psapi.GetProcessMemoryInfo(int(process._handle),C.byref(counters),C.sizeof(counters)),'process_memory_failed')
    return {'pid':process.pid,'creationTime100ns':(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime,
        'priorityClass':int(kernel.GetPriorityClass(int(process._handle))),
        'workingSetBytes':int(counters.workingSetSize),'peakWorkingSetBytes':int(counters.peakWorkingSetSize),
        'privateBytes':int(counters.privateUsage),'peakCommitBytes':int(counters.peakPagefileUsage)}

class NativeTokenizer:
    """One process per explicit run; finally closes stdin and waits by owned handle."""
    def __init__(self, output:Path):
        self.output=Path(output);self.process=None;self.log=None;self.responses=queue.Queue()
        self.cache={};self.calls=[];self.started=time.monotonic();self.closed=False
        self.receipt={'version':VERSION,'startedAt':utc(),'generationCalls':0,'contextCreated':False,
                      'vocabularyOnly':True,'outputTokenReserve':4096,'contextSize':8192,
                      'maxPromptTokens':4095,'addSpecial':False,'parseSpecial':True}

    def __enter__(self):
        require(os.name=='nt' and sys.version_info[:2]==(3,11),'pinned_windows_python_required')
        memory=system_memory();require(memory['availPhys']>=2*1024**3 and memory['availPageFile']>=2*1024**3,'vocab_memory_unavailable')
        self.receipt['preflight']={'memory':memory,'processCommitCapBytes':CAP_BYTES,
            'reason':'Vocabulary metadata only (7.5 MB header, 128167 tokens); 1 GiB hard process commit cap, no tensor/context allocation.',
            'generationResourceProfile':'not_applicable_vocabulary_only','priorityClass':0x4000}
        owner=claim_process_owner()
        # Preserve the lifetime/anti-breakaway flags; cap each process, including
        # this small parent, before child creation. No unrelated process joins.
        limits=_ExtendedLimits();api=owner._api.kernel
        require(api.QueryInformationJobObject(owner._handle,9,C.byref(limits),C.sizeof(limits),None),'job_limit_query_failed')
        limits.BasicLimitInformation.LimitFlags|=0x100 # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        limits.ProcessMemoryLimit=CAP_BYTES
        require(api.SetInformationJobObject(owner._handle,9,C.byref(limits),C.sizeof(limits)),'job_memory_limit_failed')
        verified=_ExtendedLimits()
        require(api.QueryInformationJobObject(owner._handle,9,C.byref(verified),C.sizeof(verified),None)
            and verified.ProcessMemoryLimit==CAP_BYTES and verified.BasicLimitInformation.LimitFlags&0x100,'job_memory_limit_not_verified')
        owner.assert_owned()
        env={k:v for k,v in os.environ.items() if not k.upper().startswith(('LLAMA_','GGML_','GGUF_','HF_','HUGGINGFACE_','PYTHON'))}
        env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        self.log=(self.output/'tokenizer-native.stderr.log').open('xb')
        try:
            executable=str(Path(sys._base_executable).resolve())
            self.process=owner.spawn([executable,'-B','-X','utf8',str(Path(__file__).with_name('vocab_worker.py')),'--owned-worker'],
                cwd=str(ROOT),env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,creationflags=0x4000)
            self.receipt['processAtSpawn']=process_probe(self.process)
            self.receipt['pythonExecutable']=executable
            self.receipt['ownership']={'atomicJobAssignment':True,'verifiedChildMembership':True,'killOnOwnerExit':True,'stopByOwnedHandle':True}
            def read_lines():
                try:
                    for line in self.process.stdout:
                        if len(line)>4*1024*1024:raise ValueError('protocol_line_too_large')
                        self.responses.put(json.loads(line.decode('utf-8')))
                    self.responses.put(EOFError('native_stdout_closed'))
                except BaseException as error:self.responses.put(error)
            threading.Thread(target=read_lines,daemon=True).start()
            ready=self._receive(120)
            require(ready.get('event')=='ready' and ready.get('pid')==self.process.pid,'native_ready_identity')
            require(ready.get('generationCalls')==0 and ready.get('contextCreated') is False and ready.get('vocabularyOnly') is True,'native_not_vocab_only')
            self.receipt['ready']=ready;self.receipt['processAtReady']=process_probe(self.process)
            return self
        except BaseException:
            self.close(failed=True);raise

    def _receive(self,seconds):
        try:response=self.responses.get(timeout=seconds)
        except queue.Empty:raise TimeoutError('native_tokenizer_timeout') from None
        if isinstance(response,BaseException):raise response
        return response

    def tokens(self,text):
        require(not self.closed and self.process is not None,'tokenizer_not_active')
        require(time.monotonic()-self.started<=600,'tokenizer_run_deadline')
        raw=text.encode('utf-8') # refuse unpaired UTF16 surrogates, never normalize
        require(len(raw)<=300000 and '\0' not in text,'tokenizer_text_limit')
        key=digest(raw)
        if key in self.cache:
            require(self.cache[key]['text']==text,'tokenizer_cache_hash_collision')
            return list(self.cache[key]['tokens'])
        identifier=len(self.calls)+1
        line=(json.dumps({'id':identifier,'text':text},ensure_ascii=False)+'\n').encode('utf-8')
        self.process.stdin.write(line);self.process.stdin.flush()
        response=self._receive(30)
        require(response.get('id')==identifier and response.get('roundtrip') is True,'native_tokenizer_response')
        tokens=response.get('tokens');require(isinstance(tokens,list) and response.get('count')==len(tokens)
            and all(type(t)is int and 0<=t<128167 for t in tokens),'native_token_contract')
        self.calls.append({'id':identifier,'textSha256':key,'utf8Bytes':len(raw),'count':len(tokens),
            'tokensSha256':digest(json.dumps(tokens,separators=(',',':')).encode()),'roundtrip':True})
        self.cache[key]={'text':text,'tokens':tokens}
        require(self.log.tell()<16*1024*1024,'native_log_limit')
        return list(tokens)

    def count(self,text):return len(self.tokens(text))

    def close(self,failed=False):
        if self.closed:return
        self.closed=True;process=self.process;shutdown=None
        try:
            if process:
                try:
                    self.receipt['processAtClose']=process_probe(process)
                    if process.stdin and not process.stdin.closed:process.stdin.close()
                    if not failed:shutdown=self._receive(15)
                    process.wait(timeout=15)
                    if not failed:
                        require(shutdown=={'event':'closed','calls':len(self.calls),'generationCalls':0,'contextCreated':False},'native_shutdown_contract')
                        require(process.returncode==0,'native_nonzero_exit')
                finally:
                    if process.poll() is None:process.kill();process.wait(timeout=5)
                    for stream in (process.stdin,process.stdout):
                        if stream and not stream.closed:stream.close()
                    self.receipt['exitCode']=process.returncode
                    self.receipt['ownedProcessExited']=process.poll() is not None
        except BaseException:
            failed=True
            raise
        finally:
            if self.log:self.log.close()
            self.receipt.update(completedAt=utc(),elapsedSeconds=round(time.monotonic()-self.started,3),
                tokenizationCalls=len(self.calls),calls=self.calls,shutdown=shutdown,failed=failed)
            write_json(self.output/'tokenizer-receipt.json',self.receipt)

    def __exit__(self,kind,value,traceback):self.close(failed=kind is not None)
