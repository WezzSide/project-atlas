#!/usr/bin/env python3
"""Node-side client for ATLAS_DISTRIBUTED_MISSION_QUEUE. Used by the supervisor (import) and as a CLI.
Env: ATLAS_QUEUE_URL (e.g. http://127.0.0.1:<port>), ATLAS_QUEUE_TOKEN_FILE (0600 file with bearer token)."""
import json, os, sys, urllib.request, urllib.error
def _node_env():
    d={}
    try:
        for line in open(os.environ.get('ATLAS_NODE_ENV_FILE') or os.path.join(os.environ['ATLAS_AUTONOMY_ETC'],'node.env')):
            line=line.strip()
            if line and not line.startswith('#') and '=' in line: k,v=line.split('=',1); d[k.strip()]=v.strip()
    except Exception: pass
    return d
def _cfg():
    ne=_node_env(); url=os.environ.get('ATLAS_QUEUE_URL') or ne.get('ATLAS_QUEUE_URL'); tf=os.environ.get('ATLAS_QUEUE_TOKEN_FILE') or ne.get('ATLAS_QUEUE_TOKEN_FILE')
    if not url or not tf: return None,None
    try: tok=open(tf).read().strip()
    except Exception: return None,None
    return url.rstrip('/'),tok
def call(method,path,body=None,timeout=15):
    url,tok=_cfg()
    if not url: raise RuntimeError('queue not configured')
    req=urllib.request.Request(url+path,method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Authorization':'Bearer '+tok,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r: return r.status,json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e: return e.code,json.loads(e.read() or b'{}')
def configured(): return _cfg()[0] is not None
def claim(role,lease_seconds=3600): return call('POST','/claim',{'role':role,'lease_seconds':lease_seconds})
def renew(task_id,lease_id,lease_seconds=3600): return call('POST','/renew',{'task_id':task_id,'lease_id':lease_id,'lease_seconds':lease_seconds})
def complete(task_id,lease_id,receipt,final_status,envelope_sha256=None): return call('POST','/complete',{'task_id':task_id,'lease_id':lease_id,'receipt':receipt,'final_status':final_status,'envelope_sha256':envelope_sha256})
def heartbeat(role,payload): return call('POST','/heartbeat',{'role':role,**payload})
def submit(task): return call('POST','/tasks',task)
def alert(severity,condition,subject='',detail=None,fingerprint=None): return call('POST','/alert',{'severity':severity,'condition':condition,'subject':subject,'detail':detail,'fingerprint':fingerprint})
def push_evidence(task_id,path,name=None):
    import base64,hashlib
    blob=open(path,'rb').read(); return call('POST','/evidence',{'task_id':task_id,'name':name or os.path.basename(path),'sha256':hashlib.sha256(blob).hexdigest(),'content_b64':base64.b64encode(blob).decode()},timeout=30)
def fetch_evidence(sha,dest):
    import base64
    code,o=call('GET',f'/evidence/{sha}')
    if code==200: open(dest,'wb').write(base64.b64decode(o['content_b64']))
    return code
def author(task_path,policy_path=None,decisions_dir=None):
    """Policy-checked autonomous authoring: evaluate locally (fail closed), record decision, submit (server re-evaluates)."""
    import hashlib,datetime
    for d in (os.environ.get('ATLAS_LIB_DIR',''),os.path.dirname(os.path.abspath(__file__))):
        if not d: continue
        if d not in sys.path: sys.path.append(d)
    import atlas_task_policy as POL
    policy_path=policy_path or os.environ.get('ATLAS_TASK_POLICY_FILE')
    decisions_dir=decisions_dir or os.environ.get('ATLAS_AUTHORING_DECISIONS_DIR')
    if not policy_path or not decisions_dir: raise SystemExit('configuration error: ATLAS_TASK_POLICY_FILE and ATLAS_AUTHORING_DECISIONS_DIR must be set by operator configuration (no built-in default)')
    task=json.load(open(task_path)); pol=POL.load_policy(policy_path)
    task.setdefault('author_node',pol['author_role']); task.setdefault('created_at',datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z'))
    code,o=call('GET','/tasks'); open_tasks=o.get('tasks',[]) if code==200 else []
    d=POL.evaluate(task,pol,open_tasks); rec={'task_id':task.get('task_id'),'idempotency_key':task.get('idempotency_key'),'client_decision':d,'server_response':None}
    if d['decision']=='ALLOW':
        code,resp=submit(task); rec['server_response']={'http':code,'body':resp}
        if code not in (200,201): d={'decision':'DENY','reason':f'server rejected: {resp.get("error")} {resp.get("decision",{}).get("reason","") if isinstance(resp.get("decision"),dict) else ""}'}; rec['client_decision']=d
    try:
        os.makedirs(decisions_dir,exist_ok=True); ts=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        open(os.path.join(decisions_dir,f"{ts}-{str(task.get('task_id','?'))[:64]}.json"),'w').write(json.dumps(rec,indent=2,sort_keys=True))
    except Exception as e: print(f'warning: could not record decision: {e}',file=sys.stderr)
    return d,rec
if __name__=='__main__':
    a=sys.argv[1:]
    if not a or a[0] not in ('submit','author','list','get','claim','audit','nodes','health','open','alerts','alert','resolve','evidence','fetch','lineage','fleet','missions','mission','mission-update'): print('usage: atlas-queue author task.json | submit task.json (owner identity) | list [role] | open | get TASK_ID | lineage TASK_ID | claim ROLE | audit | nodes | fleet | health | alerts [all] | alert SEV CONDITION [SUBJECT] | resolve FINGERPRINT | evidence [TASK_ID] | fetch SHA DEST',file=sys.stderr); sys.exit(2)
    if a[0]=='author':
        d,rec=author(a[1]); print(json.dumps({'decision':d['decision'],'reason':d['reason'],'task_id':rec['task_id'],'server':rec['server_response']},indent=2)); sys.exit(0 if d['decision']=='ALLOW' else 3)
    if a[0]=='open': code,o=call('GET','/tasks'); print(json.dumps([t for t in o.get('tasks',[]) if t['state'] in ('READY','CLAIMED')],indent=2)); sys.exit(0)
    if a[0]=='alerts': code,o=call('GET','/alerts'+('?all=1' if len(a)>1 else '')); print(json.dumps(o,indent=2)); sys.exit(0 if code<400 else 1)
    if a[0]=='alert': code,o=alert(a[1],a[2],a[3] if len(a)>3 else ''); print(json.dumps(o)); sys.exit(0 if code<400 else 1)
    if a[0]=='resolve': code,o=call('POST','/alerts/resolve',{'fingerprint':a[1]}); print(json.dumps(o)); sys.exit(0 if code<400 else 1)
    if a[0]=='evidence': code,o=call('GET','/evidence'+(f'?task_id={a[1]}' if len(a)>1 else '')); print(json.dumps(o,indent=2)); sys.exit(0 if code<400 else 1)
    if a[0]=='fetch': code=fetch_evidence(a[1],a[2]); print(code); sys.exit(0 if code==200 else 1)
    if a[0]=='lineage': code,o=call('GET',f'/lineage/{a[1]}'); print(json.dumps(o,indent=2)); sys.exit(0 if code<400 else 1)
    if a[0]=='missions': code,o=call('GET','/missions'+(f'?state={a[1]}' if len(a)>1 else '')); print(json.dumps(o,indent=2)); sys.exit(0 if code<400 else 1)
    if a[0]=='mission': code,o=call('GET',f'/missions/{a[1]}'); print(json.dumps(o,indent=2)); sys.exit(0 if code<400 else 1)
    if a[0]=='mission-update': code,o=call('POST',f'/missions/{a[1]}/state',{'state':a[2],'summary':a[3] if len(a)>3 else ''}); print(json.dumps(o)); sys.exit(0 if code<400 else 1)
    if a[0]=='fleet': code,o=call('GET','/fleet'); print(json.dumps(o,indent=2)); sys.exit(0 if code<400 else 1)
    if a[0]=='health': print(json.dumps(call('GET','/health')[1])); sys.exit(0)
    if a[0]=='submit': code,o=submit(json.load(open(a[1])))
    elif a[0]=='list': code,o=call('GET','/tasks'+(f'?role={a[1]}' if len(a)>1 else ''))
    elif a[0]=='get': code,o=call('GET',f'/tasks/{a[1]}')
    elif a[0]=='claim': code,o=claim(a[1])
    elif a[0]=='audit': code,o=call('GET','/audit')
    else: code,o=call('GET','/nodes')
    print(json.dumps(o,indent=2)); sys.exit(0 if code<400 else 1)
