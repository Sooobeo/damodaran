"""Write-once run artifacts and explicit bounded loopback HTTP evidence."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
BASE=ROOT/'.training/comparisons/input-preparation-v1/s4-generation'
MAX_RESPONSE_BYTES=2*1024*1024

class RunError(RuntimeError):pass
def require(value,code):
    if not value:raise RunError(code)
def utc():return datetime.now(timezone.utc).isoformat()
def packed(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for b in iter(lambda:stream.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def write_new(path,raw):
    path=Path(path)
    require(path.resolve().is_relative_to(ROOT) and not any(p.is_symlink() for p in (path,*path.parents)),'artifact_redirected')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
def write_json(path,value):write_new(path,packed(value))
def append_event(path,value):
    path=Path(path)
    require(path.resolve().is_relative_to(BASE) and not any(p.is_symlink() for p in (path,*path.parents)),'event_redirected')
    with path.open('ab') as stream:stream.write(packed(value));stream.flush();os.fsync(stream.fileno())
def new_run():
    require(not any(p.is_symlink() for p in (BASE,*BASE.parents)),'run_root_redirected')
    BASE.mkdir(parents=True,exist_ok=True)
    for i in range(1,1000):
        p=BASE/f'attempt-{i:03d}'
        try:p.mkdir();return p
        except FileExistsError:continue
    raise RunError('run_limit')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class LocalClient:
    """No automatic retry; original HTTP response body is saved before parsing."""
    def __init__(self,base,key,output):
        parsed=urllib.parse.urlsplit(base)
        require(parsed.scheme=='http' and parsed.hostname=='127.0.0.1' and parsed.port
            and not parsed.path and not parsed.query and not parsed.fragment and not parsed.username,'non_loopback_client')
        self.base,self.key,self.output=base,key,Path(output)
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self.sequence=0;self.last=None;self.completions=0

    def _read_body(self,response,path,state):
        """Persist each bounded chunk, including bytes returned with an error."""
        length=response.headers.get('Content-Length')
        valid_length=length is None or (length.isdecimal() and int(length)>=0)
        state['expectedBytes']=int(length) if length is not None and valid_length else None
        with Path(path).open('xb') as stream:
            state['bodyStarted']=True
            def save(chunk):
                if chunk:
                    state['chunks'].append(chunk);state['receivedBytes']+=len(chunk)
                    stream.write(chunk);stream.flush();os.fsync(stream.fileno())
            try:
                while state['receivedBytes']<=MAX_RESPONSE_BYTES:
                    size=min(65536,MAX_RESPONSE_BYTES+1-state['receivedBytes'])
                    chunk=response.read1(size)
                    if not chunk:
                        state['eof']=True;break
                    save(chunk)
            except http.client.IncompleteRead as error:
                save(error.partial[:MAX_RESPONSE_BYTES+1-state['receivedBytes']])
                raise
        require(valid_length,'invalid_content_length')
        state['bodyComplete']=state['eof'] and (state['expectedBytes'] is None or state['expectedBytes']==state['receivedBytes'])
        require(state['bodyComplete'],'incomplete_http_response_body')
        return b''.join(state['chunks'])

    def request(self,endpoint,payload=None,timeout=15):
        require(endpoint in ('/health','/props','/apply-template','/tokenize','/detokenize','/completion'),'endpoint_not_allowed')
        require(0<timeout<=1800,'request_timeout_contract')
        self.sequence+=1;prefix=f'{self.sequence:05d}-{endpoint[1:]}'
        relative=Path('http')/prefix
        body=None if payload is None else json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
        metadata={'sequence':self.sequence,'endpoint':endpoint,'method':'GET' if body is None else 'POST',
            'startedAt':utc(),'timeoutSeconds':timeout,'requestSha256':sha(body or b''),'authorizationRecorded':False}
        write_json(self.output/relative.with_suffix('.request.json'),{'metadata':metadata,'payload':payload})
        request=urllib.request.Request(self.base+endpoint,data=body,headers={
            'Content-Type':'application/json','Authorization':'Bearer '+self.key,'Origin':self.base})
        started=time.monotonic();raw=None;status=None;error_code=None
        body_state={'bodyStarted':False,'chunks':[],'receivedBytes':0,'expectedBytes':None,'eof':False,'bodyComplete':False}
        if endpoint=='/completion':self.completions+=1
        try:
            try:response=self.opener.open(request,timeout=timeout)
            except urllib.error.HTTPError as error:response=error
            with response:
                status=response.getcode()
                raw=self._read_body(response,self.output/relative.with_suffix('.response.bin'),body_state)
            require(raw is not None and len(raw)<=MAX_RESPONSE_BYTES,'response_size_limit')
            value=json.loads(raw.decode('utf-8'))
            require(isinstance(value,dict),'response_not_object')
            if status!=200:
                if endpoint=='/health' and status==503:raise urllib.error.URLError('health_not_ready')
                raise RunError('runtime_http_failure')
            require('error' not in value,'runtime_error_response')
            return value
        except BaseException as error:
            error_code=type(error).__name__
            raise
        finally:
            if body_state['bodyStarted']:raw=b''.join(body_state['chunks'])
            self.last={**metadata,'completedAt':utc(),'elapsedSeconds':round(time.monotonic()-started,6),
                'httpStatus':status,'responseBytes':len(raw) if raw is not None else None,
                'rawResponsePath':(self.output/relative.with_suffix('.response.bin')).relative_to(ROOT).as_posix() if raw is not None else None,
                'rawResponseSha256':sha(raw) if raw is not None else None,
                'responseCompleteWithinLimit':body_state['bodyComplete'] and raw is not None and len(raw)<=MAX_RESPONSE_BYTES,
                'expectedResponseBytes':body_state['expectedBytes'],'responseEofObserved':body_state['eof'],
                'transportOrProtocolErrorType':error_code,'automaticRetry':False}
            write_json(self.output/relative.with_suffix('.receipt.json'),self.last)
