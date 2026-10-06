#!/usr/bin/env python3
"""ATLAS_DISTRIBUTED_MISSION_QUEUE v0.5.0 — durable pull/claim task queue for the Atlas fleet (VPS3 control plane).

Design invariants:
- Transport creates no authority: every task carries authority_reference; nodes still evaluate authority locally.
- Nodes PULL work for their role; claims are leases with expiry; expired leases return the task to READY.
- Idempotent: task_id is the idempotency key; duplicate submits are no-ops; duplicate completes are ignored.
- Receipts/evidence flow back and are stored append-only (never deleted).
- Bind is loopback by default; a non-loopback bind requires explicit owner authority (ATLAS_QUEUE_BIND).
- Node identity: static per-node bearer tokens (ATLAS_QUEUE_TOKENS_FILE: json {token: {"node":..,"roles":[..]}}).
"""
from __future__ import annotations
import hashlib, hmac, json, os, sqlite3, sys, threading, time, uuid
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
try:
    import atlas_task_policy as POL
except Exception: POL=None
def _required(name):
    v=os.environ.get(name)
    if not v: raise SystemExit(f'configuration error: {name} must be set by operator configuration (no built-in default)')
    return v
POLICY_FILE=_required('ATLAS_QUEUE_POLICY_FILE')
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DB=_required('ATLAS_QUEUE_DB')
BIND=os.environ.get('ATLAS_QUEUE_BIND','127.0.0.1'); PORT=int(_required('ATLAS_QUEUE_PORT'))
BINDS=[b.strip() for b in BIND.split(',') if b.strip()]
TOKENS_FILE=_required('ATLAS_QUEUE_TOKENS_FILE')
EVIDENCE_DIR=os.environ.get('ATLAS_QUEUE_EVIDENCE_DIR',os.path.join(os.path.dirname(DB),'evidence')); MAX_EVIDENCE_BYTES=int(os.environ.get('ATLAS_QUEUE_MAX_EVIDENCE_BYTES','262144'))
MISSION_STATES=('ACCEPTED','ACTIVE','WAITING_AUTHORITY','COMPLETED','BLOCKED','CANCELLED','REJECTED')
MAX_OPEN_OWNER_MISSIONS=int(os.environ.get('ATLAS_QUEUE_MAX_OPEN_OWNER_MISSIONS','5'))
DEFAULT_LEASE=int(os.environ.get('ATLAS_QUEUE_LEASE_SECONDS','3600')); MAX_ATTEMPTS=int(os.environ.get('ATLAS_QUEUE_MAX_ATTEMPTS','3'))
STATES=('READY','CLAIMED','COMPLETED','BLOCKED','FAILED','CANCELLED')
TASK_REQ={'schema_version','task_id','target_role','outcome','authority_reference','priority'}
AUTO_OPT={'parent_task_id','lease_seconds','policy'}
LOCK=threading.Lock()

def now(): return datetime.now(timezone.utc)
def iso(dt=None): return (dt or now()).isoformat(timespec='microseconds').replace('+00:00','Z')
def sha(s:str): return hashlib.sha256(s.encode()).hexdigest()

def db():
    c=sqlite3.connect(DB,isolation_level=None,timeout=10); c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=FULL'); return c

def init():
    os.makedirs(os.path.dirname(DB),exist_ok=True)
    with db() as c:
        cols={r[1] for r in c.execute('PRAGMA table_info(tasks)')} if c.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone() else None
        if cols is not None:
            for col in ('idempotency_key TEXT','mission_id TEXT','task_class TEXT','author_node TEXT','policy_decision TEXT'):
                if col.split()[0] not in cols: c.execute(f'ALTER TABLE tasks ADD COLUMN {col}')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS tasks(task_id TEXT PRIMARY KEY, target_role TEXT NOT NULL, priority INTEGER NOT NULL, state TEXT NOT NULL, idempotency_key TEXT, mission_id TEXT, task_class TEXT, author_node TEXT, policy_decision TEXT,
          envelope TEXT NOT NULL, envelope_sha256 TEXT NOT NULL, authority_reference TEXT NOT NULL, parent_task_id TEXT,
          submitted_by TEXT, submitted_at TEXT NOT NULL, claimed_by TEXT, lease_id TEXT, lease_expires_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
          completed_at TEXT, final_status TEXT, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS tasks_role_state ON tasks(target_role,state,priority DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS tasks_idem ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL;
        CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, node TEXT NOT NULL, lease_id TEXT,
          receipt TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, received_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS heartbeats(node TEXT PRIMARY KEY, role TEXT, payload TEXT, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, node TEXT, action TEXT NOT NULL, task_id TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS missions(mission_id TEXT PRIMARY KEY, source TEXT NOT NULL, submitted_by TEXT NOT NULL, sender_ref TEXT, title TEXT, text TEXT NOT NULL, text_sha256 TEXT NOT NULL, state TEXT NOT NULL, summary TEXT, envelope_id TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS mission_events(id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL, at TEXT NOT NULL, node TEXT NOT NULL, state TEXT, note TEXT);
        CREATE TRIGGER IF NOT EXISTS mission_events_no_update BEFORE UPDATE ON mission_events BEGIN SELECT RAISE(ABORT,'mission_events are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS mission_events_no_delete BEFORE DELETE ON mission_events BEGIN SELECT RAISE(ABORT,'mission_events are append-only'); END;
        CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL, node TEXT, severity TEXT NOT NULL, condition TEXT NOT NULL, subject TEXT, detail TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, count INTEGER NOT NULL, resolved_at TEXT, resolved_by TEXT);
        CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, node TEXT NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, received_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence BEGIN SELECT RAISE(ABORT,'evidence is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence BEGIN SELECT RAISE(ABORT,'evidence is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS receipts_no_update BEFORE UPDATE ON receipts BEGIN SELECT RAISE(ABORT,'receipts are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS receipts_no_delete BEFORE DELETE ON receipts BEGIN SELECT RAISE(ABORT,'receipts are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
        ''')

def audit(c,node,action,task_id=None,detail=None): c.execute('INSERT INTO audit(at,node,action,task_id,detail) VALUES(?,?,?,?,?)',(iso(),node,action,task_id,json.dumps(detail) if detail is not None else None))

def load_tokens():
    try: return json.load(open(TOKENS_FILE))
    except Exception: return {}

def expire_leases(c):
    for r in c.execute("SELECT task_id,lease_id,claimed_by,attempts FROM tasks WHERE state='CLAIMED' AND lease_expires_at<?",(iso(),)).fetchall():
        if int(r['attempts'])>=MAX_ATTEMPTS:   # abandoned repeatedly: dead-letter instead of endless re-claim
            c.execute("UPDATE tasks SET state='FAILED', final_status='DEAD_LETTER', completed_at=?, claimed_by=NULL, lease_id=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=? AND lease_id=?",(iso(),iso(),r['task_id'],r['lease_id'])); audit(c,r['claimed_by'],'dead_letter',r['task_id'],{'lease_id':r['lease_id'],'attempts':r['attempts']})
        else:
            c.execute("UPDATE tasks SET state='READY', claimed_by=NULL, lease_id=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=? AND lease_id=?",(iso(),r['task_id'],r['lease_id'])); audit(c,r['claimed_by'],'lease_expired',r['task_id'],{'lease_id':r['lease_id'],'attempts':r['attempts']})

def validate_task(o):
    if not isinstance(o,dict): return 'envelope must be object'
    if set(o)-TASK_REQ-{'parent_task_id','lease_seconds'}: return f'unexpected fields: {sorted(set(o)-TASK_REQ)}'
    if not TASK_REQ<=set(o): return f'missing fields: {sorted(TASK_REQ-set(o))}'
    if o['schema_version']!=1: return 'schema_version must be 1'
    if not isinstance(o['task_id'],str) or not o['task_id'] or len(o['task_id'])>128 or '/' in o['task_id']: return 'bad task_id'
    if not isinstance(o['priority'],int) or not 0<=o['priority']<=100: return 'priority 0..100'
    for k in ('target_role','outcome','authority_reference'):
        if not isinstance(o[k],str) or not o[k]: return f'{k} must be non-empty string'
    return None

class H(BaseHTTPRequestHandler):
    server_version='atlas-queue/0.1'
    def log_message(self,*a): pass
    def _send(self,code,obj):
        b=json.dumps(obj,sort_keys=True).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def _auth(self):
        ident=self._auth0()
        if not ident: return None
        allowed=ident.get('source_ips')
        if allowed and self.client_address[0] not in allowed:
            with LOCK, db() as c: audit(c,ident.get('node'),'auth_source_rejected',None,{'from':self.client_address[0]})
            return None
        return ident
    def _auth0(self):
        # tokens.json holds ONLY sha256 digests: {"sha256:<hex>": {"node":..,"roles":[..],"submit":bool}}; plaintext never at rest server-side
        tok=(self.headers.get('Authorization') or '').removeprefix('Bearer ').strip()
        if not tok: return None
        d='sha256:'+hashlib.sha256(tok.encode()).hexdigest(); ident=None
        for k,v in load_tokens().items():
            if hmac.compare_digest(k,d): ident=v
        return ident
    def _body(self):
        n=int(self.headers.get('Content-Length') or 0)
        if n<0 or n>1_000_000: raise ValueError('body length out of range')
        raw=self.rfile.read(n) if n else b''
        return json.loads(raw) if raw else {}
    def do_GET(self):
        u=urlparse(self.path); q=parse_qs(u.query)
        if u.path=='/health': return self._send(200,{'ok':True,'at':iso(),'bind':[f'{b}:{PORT}' for b in BINDS],'version':'0.5.0'})
        ident=self._auth()
        if not ident: return self._send(401,{'error':'unauthorized'})
        if ident.get('mission_intake') and not (u.path.startswith('/missions') or u.path in ('/fleet','/alerts')): return self._send(403,{'error':'intake identity has a restricted read scope'})
        with LOCK, db() as c:
            expire_leases(c)
            if u.path=='/tasks':
                role=q.get('role',[None])[0]; state=q.get('state',[None])[0]; mission=q.get('mission',[None])[0]; sql='SELECT task_id,target_role,priority,state,claimed_by,lease_expires_at,attempts,submitted_at,updated_at,final_status,idempotency_key,mission_id,task_class,author_node FROM tasks WHERE 1=1'; args=[]
                if role: sql+=' AND target_role=?'; args.append(role)
                if state: sql+=' AND state=?'; args.append(state)
                if mission: sql+=' AND mission_id=?'; args.append(mission)
                rows=[dict(r) for r in c.execute(sql+' ORDER BY priority DESC, submitted_at LIMIT 200',args)]
                return self._send(200,{'tasks':rows})
            if u.path.startswith('/tasks/'):
                tid=u.path.split('/',2)[2]; r=c.execute('SELECT * FROM tasks WHERE task_id=?',(tid,)).fetchone()
                if not r: return self._send(404,{'error':'not found'})
                rec=[dict(x) for x in c.execute('SELECT id,node,lease_id,receipt_sha256,received_at FROM receipts WHERE task_id=? ORDER BY id',(tid,))]
                return self._send(200,{'task':dict(r),'receipts':rec})
            if u.path=='/nodes': return self._send(200,{'nodes':[dict(r) for r in c.execute('SELECT node,role,at FROM heartbeats')]})
            if u.path=='/missions':
                st=q.get('state',[None])[0]; rows=[dict(r) for r in c.execute('SELECT mission_id,source,title,state,summary,cancel_requested,created_at,updated_at,text_sha256'+(',text' if not ident.get('mission_intake') else '')+' FROM missions'+(' WHERE state=?' if st else '')+' ORDER BY created_at DESC LIMIT 50',(st,) if st else ())]; return self._send(200,{'missions':rows})
            if u.path.startswith('/missions/'):
                mid=u.path.split('/')[2]; r=c.execute('SELECT * FROM missions WHERE mission_id=?',(mid,)).fetchone()
                if not r: return self._send(404,{'error':'not found'})
                ev=[dict(x) for x in c.execute('SELECT at,node,state,note FROM mission_events WHERE mission_id=? ORDER BY id',(mid,))]
                tk=[dict(x) for x in c.execute('SELECT task_id,target_role,task_class,state,final_status,attempts,updated_at FROM tasks WHERE mission_id=? ORDER BY submitted_at',(mid,))]
                return self._send(200,{'mission':dict(r),'events':ev,'tasks':tk})
            if u.path=='/alerts':
                inc=q.get('all',['0'])[0]=='1'; rows=[dict(r) for r in c.execute('SELECT id,fingerprint,node,severity,condition,subject,first_seen,last_seen,count,resolved_at FROM alerts'+('' if inc else ' WHERE resolved_at IS NULL')+' ORDER BY id DESC LIMIT 200')]; return self._send(200,{'alerts':rows})
            if u.path=='/evidence':
                tid=q.get('task_id',[None])[0]; rows=[dict(r) for r in c.execute('SELECT id,task_id,node,name,sha256,bytes,received_at FROM evidence'+(' WHERE task_id=?' if tid else '')+' ORDER BY id DESC LIMIT 500',(tid,) if tid else ())]; return self._send(200,{'evidence':rows})
            if u.path.startswith('/evidence/'):
                esha=u.path.split('/',2)[2]
                if not (len(esha)==64 and all(ch in '0123456789abcdef' for ch in esha)): return self._send(400,{'error':'bad sha'})
                fp=os.path.join(EVIDENCE_DIR,esha[:2],esha)
                if not os.path.exists(fp): return self._send(404,{'error':'not found'})
                import base64; return self._send(200,{'sha256':esha,'content_b64':base64.b64encode(open(fp,'rb').read()).decode()})
            if u.path.startswith('/lineage/'):
                tid=u.path.split('/',2)[2]; chain=[]; cur=tid; seen=set()
                while cur and cur not in seen:
                    seen.add(cur); r=c.execute('SELECT task_id,parent_task_id,mission_id,task_class,state,author_node FROM tasks WHERE task_id=?',(cur,)).fetchone()
                    if not r: break
                    chain.append(dict(r)); cur=r['parent_task_id']
                kids=[dict(r) for r in c.execute('SELECT task_id,parent_task_id,mission_id,task_class,state FROM tasks WHERE parent_task_id=?',(tid,))]
                return self._send(200,{'ancestry':chain,'children':kids})
            if u.path=='/fleet':
                summ={'nodes':[dict(r) for r in c.execute('SELECT node,role,at FROM heartbeats')],'open_tasks':[dict(r) for r in c.execute("SELECT task_id,target_role,state,task_class,mission_id,claimed_by,lease_expires_at,attempts FROM tasks WHERE state IN ('READY','CLAIMED')")],'terminal_counts':{r['state']:r['n'] for r in c.execute('SELECT state,count(*) n FROM tasks GROUP BY state')},'open_alerts':[dict(r) for r in c.execute('SELECT severity,condition,subject,node,count,last_seen FROM alerts WHERE resolved_at IS NULL ORDER BY id DESC LIMIT 50')],'missions':[dict(r) for r in c.execute("SELECT mission_id,title,state,summary,updated_at FROM missions ORDER BY created_at DESC LIMIT 10")],'at':iso()}
                return self._send(200,summ)
            if u.path=='/audit': return self._send(200,{'audit':[dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')]})
        return self._send(404,{'error':'no route'})
    def do_POST(self):
        u=urlparse(self.path); ident=self._auth()
        if not ident: return self._send(401,{'error':'unauthorized'})
        try: body=self._body()
        except Exception as e: return self._send(400,{'error':f'bad json: {e}'})
        node=ident.get('node','?'); roles=set(ident.get('roles',[])); can_submit=bool(ident.get('submit'))
        if ident.get('readonly'): return self._send(403,{'error':'read-only identity'})
        if ident.get('mission_intake') and not u.path.startswith('/missions'): return self._send(403,{'error':'intake identity may only create/cancel missions'})
        with LOCK, db() as c:
            expire_leases(c)
            if u.path=='/alert':               # node/owner alert push (dedup on fingerprint)
                fp=body.get('fingerprint') or sha(json.dumps({k:body.get(k) for k in ('severity','condition','subject')},sort_keys=True))[:32]
                if not body.get('condition') or body.get('severity') not in ('P0','P1','P2','P3','INFO'): return self._send(422,{'error':'need severity P0|P1|P2|P3|INFO and condition'})
                ex=c.execute('SELECT id,count FROM alerts WHERE fingerprint=? AND resolved_at IS NULL',(fp,)).fetchone()
                if ex: c.execute('UPDATE alerts SET count=count+1,last_seen=?,detail=? WHERE id=?',(iso(),json.dumps(body)[:4000],ex['id'])); return self._send(200,{'alert_id':ex['id'],'dedup':True,'count':ex['count']+1})
                c.execute('INSERT INTO alerts(fingerprint,node,severity,condition,subject,detail,first_seen,last_seen,count) VALUES(?,?,?,?,?,?,?,?,1)',(fp,node,body['severity'],body['condition'],str(body.get('subject',''))[:200],json.dumps(body)[:4000],iso(),iso())); aid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                audit(c,node,'alert',None,{'severity':body['severity'],'condition':body['condition'],'fingerprint':fp}); return self._send(201,{'alert_id':aid,'fingerprint':fp})
            if u.path=='/evidence':            # append-only evidence push bound to a task (sha256-verified, size-capped)
                import base64
                tid=body.get('task_id'); name=str(body.get('name',''))[:200]; b64=body.get('content_b64'); esha=body.get('sha256')
                if not (tid and name and b64 and esha): return self._send(422,{'error':'need task_id, name, sha256, content_b64'})
                try: blob=base64.b64decode(b64,validate=True)
                except Exception: return self._send(422,{'error':'bad base64'})
                if len(blob)>MAX_EVIDENCE_BYTES: return self._send(413,{'error':f'evidence exceeds {MAX_EVIDENCE_BYTES} bytes'})
                if hashlib.sha256(blob).hexdigest()!=esha: return self._send(422,{'error':'sha256 mismatch'})
                r=c.execute('SELECT target_role FROM tasks WHERE task_id=?',(tid,)).fetchone()
                if not r: return self._send(404,{'error':'task not found'})
                if r['target_role'] not in roles: return self._send(403,{'error':'identity not authorized for task role'})
                if c.execute('SELECT 1 FROM evidence WHERE task_id=? AND sha256=?',(tid,esha)).fetchone(): return self._send(200,{'idempotent':True,'sha256':esha})
                path=os.path.join(EVIDENCE_DIR,esha[:2]); os.makedirs(path,exist_ok=True); fp=os.path.join(path,esha)
                if not os.path.exists(fp):
                    with open(fp,'wb') as f: f.write(blob)
                c.execute('INSERT INTO evidence(task_id,node,name,sha256,bytes,received_at) VALUES(?,?,?,?,?,?)',(tid,node,name,esha,len(blob),iso())); audit(c,node,'evidence',tid,{'name':name,'sha256':esha,'bytes':len(blob)}); return self._send(201,{'sha256':esha,'bytes':len(blob)})
            if u.path=='/missions':            # owner-interface intake: creates a mission RECORD only (no tasks, no authority)
                if not ident.get('mission_intake'): return self._send(403,{'error':'identity may not submit missions'})
                text=str(body.get('text','')).strip(); eid=str(body.get('envelope_id','')).strip()
                if not text or len(text)>4000 or not eid or len(eid)>128: return self._send(422,{'error':'need text (1..4000 chars) and envelope_id'})
                ex=c.execute('SELECT mission_id,state FROM missions WHERE envelope_id=?',(eid,)).fetchone()
                if ex: return self._send(200,{'mission_id':ex['mission_id'],'state':ex['state'],'idempotent':True})
                n_open=c.execute("SELECT count(*) FROM missions WHERE source='owner-interface' AND state IN ('ACCEPTED','ACTIVE','WAITING_AUTHORITY')").fetchone()[0]
                if n_open>=MAX_OPEN_OWNER_MISSIONS: return self._send(429,{'error':f'{n_open} owner missions open (cap {MAX_OPEN_OWNER_MISSIONS}); complete or cancel one first'})
                seq=c.execute("SELECT count(*) FROM missions WHERE mission_id LIKE ?",('OWNER-M-'+now().strftime('%Y%m%d')+'-%',)).fetchone()[0]+1
                mid='OWNER-M-'+now().strftime('%Y%m%d')+f'-{seq:03d}'
                c.execute('INSERT INTO missions(mission_id,source,submitted_by,sender_ref,title,text,text_sha256,state,envelope_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(mid,'owner-interface',node,str(body.get('sender_ref',''))[:64],str(body.get('title',''))[:120],text,sha(text),'ACCEPTED',eid,iso(),iso()))
                c.execute('INSERT INTO mission_events(mission_id,at,node,state,note) VALUES(?,?,?,?,?)',(mid,iso(),node,'ACCEPTED','intake via owner interface'))
                audit(c,node,'mission_intake',None,{'mission_id':mid,'text_sha256':sha(text),'envelope_id':eid}); return self._send(201,{'mission_id':mid,'state':'ACCEPTED'})
            if u.path.startswith('/missions/') and u.path.endswith('/cancel'):
                if not ident.get('mission_intake'): return self._send(403,{'error':'identity may not cancel missions'})
                mid=u.path.split('/')[2]; r=c.execute('SELECT state FROM missions WHERE mission_id=?',(mid,)).fetchone()
                if not r: return self._send(404,{'error':'not found'})
                c.execute('UPDATE missions SET cancel_requested=1,updated_at=? WHERE mission_id=?',(iso(),mid)); c.execute('INSERT INTO mission_events(mission_id,at,node,state,note) VALUES(?,?,?,?,?)',(mid,iso(),node,r['state'],'cancel requested by owner'))
                audit(c,node,'mission_cancel_requested',None,{'mission_id':mid}); return self._send(200,{'mission_id':mid,'cancel_requested':True})
            if u.path.startswith('/missions/') and u.path.endswith('/state'):
                if 'ATLAS-EU-CONTROL-01' not in roles or ident.get('mission_intake'): return self._send(403,{'error':'only the control role updates mission state'})
                mid=u.path.split('/')[2]; st=body.get('state')
                if st not in MISSION_STATES: return self._send(422,{'error':f'state must be one of {MISSION_STATES}'})
                r=c.execute('SELECT state FROM missions WHERE mission_id=?',(mid,)).fetchone()
                if not r: return self._send(404,{'error':'not found'})
                if r['state'] in ('COMPLETED','CANCELLED','REJECTED'): return self._send(409,{'error':'mission is terminal'})
                c.execute('UPDATE missions SET state=?,summary=?,updated_at=? WHERE mission_id=?',(st,str(body.get('summary',''))[:2000],iso(),mid)); c.execute('INSERT INTO mission_events(mission_id,at,node,state,note) VALUES(?,?,?,?,?)',(mid,iso(),node,st,str(body.get('summary',''))[:2000]))
                audit(c,node,'mission_state',None,{'mission_id':mid,'state':st}); return self._send(200,{'mission_id':mid,'state':st})
            if u.path=='/alerts/resolve':
                if 'ATLAS-EU-CONTROL-01' not in roles: return self._send(403,{'error':'only the control role resolves alerts'})
                n=c.execute('UPDATE alerts SET resolved_at=?,resolved_by=? WHERE fingerprint=? AND resolved_at IS NULL',(iso(),node,body.get('fingerprint'))).rowcount; audit(c,node,'alert_resolved',None,{'fingerprint':body.get('fingerprint'),'n':n}); return self._send(200,{'resolved':n})
            if u.path=='/tasks':               # submit (idempotent on task_id)
                if not can_submit: return self._send(403,{'error':'identity may not submit'})
                autonomous='author_node' in body; policy_enforced=bool(ident.get('policy_enforced')); decision=None
                if policy_enforced and not autonomous: return self._send(403,{'error':'this identity may only submit policy-checked autonomous envelopes (author_node, task_class, ...); owner envelopes require the owner identity'})
                if autonomous:
                    if not POL or not os.path.exists(POLICY_FILE): return self._send(503,{'error':'policy engine unavailable; autonomous authoring fails closed'})
                    pol=POL.load_policy(POLICY_FILE)
                    for r in c.execute("SELECT mission_id,state,cancel_requested FROM missions"):
                        st='current' if r['state'] in ('ACCEPTED','ACTIVE','WAITING_AUTHORITY') and not r['cancel_requested'] else 'closed'
                        pol['mission_registry'][r['mission_id']]={'status':st,'role':'*','source':'owner-interface'}
                    open_tasks=[dict(r) for r in c.execute('SELECT task_id,idempotency_key,state,mission_id FROM tasks')]
                    decision=POL.evaluate({k:v for k,v in body.items() if k!='lease_seconds'},pol,open_tasks)
                    audit(c,node,'policy_decision',body.get('task_id'),{'decision':decision['decision'],'reason':decision['reason'],'task_class':decision.get('task_class'),'authority_reference':decision.get('authority_reference'),'policy_version':decision.get('policy_version'),'policy_sha256':decision.get('policy_sha256')})
                    if decision['decision']!='ALLOW': return self._send(422,{'error':'policy DENY','decision':decision})
                else:
                    err=validate_task(body)
                    if err: return self._send(422,{'error':err})
                env=json.dumps({k:v for k,v in body.items() if k!='lease_seconds'},sort_keys=True); esha=sha(env)
                ex=c.execute('SELECT envelope_sha256,state FROM tasks WHERE task_id=?',(body['task_id'],)).fetchone()
                if ex:
                    if ex['envelope_sha256']==esha: return self._send(200,{'task_id':body['task_id'],'state':ex['state'],'idempotent':True})
                    return self._send(409,{'error':'task_id exists with different envelope','existing_sha256':ex['envelope_sha256']})
                if body.get('idempotency_key') and c.execute('SELECT 1 FROM tasks WHERE idempotency_key=?',(body['idempotency_key'],)).fetchone(): return self._send(409,{'error':'idempotency_key already used','idempotency_key':body['idempotency_key']})
                c.execute('INSERT INTO tasks(task_id,target_role,priority,state,envelope,envelope_sha256,authority_reference,parent_task_id,submitted_by,submitted_at,updated_at,idempotency_key,mission_id,task_class,author_node,policy_decision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (body['task_id'],body['target_role'],body['priority'],'READY',env,esha,body['authority_reference'],body.get('parent_task_id'),node,iso(),iso(),body.get('idempotency_key'),body.get('mission_id'),body.get('task_class'),body.get('author_node'),json.dumps(decision) if decision else None))
                audit(c,node,'submit',body['task_id'],{'sha256':esha,'autonomous':autonomous,'task_class':body.get('task_class'),'policy':{'decision':decision['decision'],'policy_sha256':decision['policy_sha256']} if decision else None}); return self._send(201,{'task_id':body['task_id'],'state':'READY','envelope_sha256':esha,'policy_decision':decision})
            if u.path=='/claim':               # pull: highest-priority READY task for one of my roles
                role=body.get('role')
                if role not in roles: return self._send(403,{'error':'identity not authorized for role','roles':sorted(roles)})
                lease=int(body.get('lease_seconds') or DEFAULT_LEASE); lease=max(60,min(lease,6*3600))
                r=c.execute("SELECT * FROM tasks WHERE target_role=? AND state='READY' ORDER BY priority DESC, submitted_at LIMIT 1",(role,)).fetchone()
                if not r: return self._send(204,{}) if False else self._send(200,{'task':None})
                lid=str(uuid.uuid4()); exp=iso(now()+timedelta(seconds=lease))
                c.execute('BEGIN IMMEDIATE'); n=c.execute("UPDATE tasks SET state='CLAIMED',claimed_by=?,lease_id=?,lease_expires_at=?,attempts=attempts+1,updated_at=? WHERE task_id=? AND state='READY'",(node,lid,exp,iso(),r['task_id'])).rowcount
                if n!=1: c.execute('ROLLBACK'); return self._send(200,{'task':None,'note':'lost race; retry'})
                audit(c,node,'claim',r['task_id'],{'lease_id':lid,'expires':exp}); c.execute('COMMIT')
                return self._send(200,{'task':json.loads(r['envelope']),'envelope_sha256':r['envelope_sha256'],'lease_id':lid,'lease_expires_at':exp,'attempt':r['attempts']+1})
            if u.path=='/renew':
                r=c.execute('SELECT * FROM tasks WHERE task_id=? AND lease_id=? AND claimed_by=? AND state=?',(body.get('task_id'),body.get('lease_id'),node,'CLAIMED')).fetchone()
                if not r: return self._send(409,{'error':'no matching active lease'})
                exp=iso(now()+timedelta(seconds=max(60,min(int(body.get('lease_seconds') or DEFAULT_LEASE),6*3600))))
                c.execute('UPDATE tasks SET lease_expires_at=?,updated_at=? WHERE task_id=?',(exp,iso(),r['task_id'])); audit(c,node,'renew',r['task_id'],{'lease_id':r['lease_id'],'expires':exp}); return self._send(200,{'lease_expires_at':exp})
            if u.path=='/complete':            # receipt + terminal state; idempotent per (task,lease)
                tid=body.get('task_id'); lid=body.get('lease_id'); receipt=body.get('receipt'); status=body.get('final_status')
                if not (tid and lid and isinstance(receipt,dict) and status in ('COMPLETED','BLOCKED','FAILED')): return self._send(422,{'error':'need task_id, lease_id, receipt{}, final_status in COMPLETED|BLOCKED|FAILED'})
                r=c.execute('SELECT * FROM tasks WHERE task_id=?',(tid,)).fetchone()
                if not r: return self._send(404,{'error':'not found'})
                if r['target_role'] not in roles: return self._send(403,{'error':'identity not authorized for task role'})
                if body.get('envelope_sha256')!=r['envelope_sha256']: return self._send(409,{'error':'envelope_sha256 mismatch: settlement must bind exact task identity','expected':r['envelope_sha256']})
                rj=json.dumps(receipt,sort_keys=True); rsha=sha(rj)
                if c.execute('SELECT 1 FROM receipts WHERE task_id=? AND lease_id=? AND receipt_sha256=?',(tid,lid,rsha)).fetchone(): return self._send(200,{'idempotent':True,'state':r['state']})
                c.execute('INSERT INTO receipts(task_id,node,lease_id,receipt,receipt_sha256,received_at) VALUES(?,?,?,?,?,?)',(tid,node,lid,rj,rsha,iso()))
                if r['state']=='CLAIMED' and r['lease_id']==lid and r['claimed_by']==node:
                    new='COMPLETED' if status=='COMPLETED' else ('BLOCKED' if status=='BLOCKED' else ('READY' if r['attempts']<MAX_ATTEMPTS else 'FAILED'))
                    c.execute('UPDATE tasks SET state=?,final_status=?,completed_at=?,claimed_by=NULL,lease_id=NULL,lease_expires_at=NULL,updated_at=? WHERE task_id=?',(new,status,iso() if new!='READY' else None,iso(),tid))
                    audit(c,node,'complete',tid,{'final_status':status,'state':new,'receipt_sha256':rsha,'task_class':r['task_class'],'policy':json.loads(r['policy_decision'])['policy_sha256'] if r['policy_decision'] else None}); return self._send(200,{'state':new,'receipt_sha256':rsha})
                audit(c,node,'late_receipt',tid,{'receipt_sha256':rsha,'state':r['state']}); return self._send(202,{'state':r['state'],'note':'receipt stored; lease no longer active','receipt_sha256':rsha})
            if u.path=='/heartbeat':
                if body.get('role') not in roles: return self._send(403,{'error':'heartbeat role not bound to identity'})
                c.execute('INSERT INTO heartbeats(node,role,payload,at) VALUES(?,?,?,?) ON CONFLICT(node) DO UPDATE SET role=excluded.role,payload=excluded.payload,at=excluded.at',(node,body.get('role'),json.dumps(body)[:4000],iso()))
                return self._send(200,{'ok':True})
        return self._send(404,{'error':'no route'})

def main():
    init()
    for b in BINDS:
        if b in ('0.0.0.0','::','') : print('refusing wildcard bind (public exposure not authorized)',file=sys.stderr); return 78
        if b not in ('127.0.0.1','::1','localhost') and os.environ.get('ATLAS_QUEUE_NONLOOPBACK_AUTHORIZED')!='1':
            print(f'refusing non-loopback bind {b} without ATLAS_QUEUE_NONLOOPBACK_AUTHORIZED=1 (owner authority)',file=sys.stderr); return 78
    servers=[ThreadingHTTPServer((b,PORT),H) for b in BINDS]
    for srv in servers[1:]: threading.Thread(target=srv.serve_forever,daemon=True).start()
    print(f'atlas-queue 0.5.0 listening on {[f"{b}:{PORT}" for b in BINDS]} db={DB}',flush=True); servers[0].serve_forever()
if __name__=='__main__': raise SystemExit(main())
