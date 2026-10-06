#!/usr/bin/env python3
from __future__ import annotations
import fcntl, hashlib, json, os, subprocess, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
def _required(name):
    v=os.environ.get(name)
    if not v: raise SystemExit(f'configuration error: {name} must be set by operator configuration (no built-in default)')
    return v
ETC=Path(_required('ATLAS_AUTONOMY_ETC'))
VAR=Path(_required('ATLAS_AUTONOMY_VAR'))
ENABLE=ETC/'ENABLE_AUTONOMY'; ENV=ETC/'node.env'
sys.path.insert(0,os.environ.get('ATLAS_LIB_DIR') or os.path.dirname(os.path.abspath(__file__)))
try:
    import atlas_queue_client as Q
except Exception: Q=None
def qlog(m): print(f'queue: {m}',file=sys.stderr,flush=True)
def queue_pull(role_id,s=None):
    """Pull one task for this role from the distributed queue into the local inbox (lease-bound). Never raises. Honours failure backoff."""
    if not (Q and Q.configured()): return
    if s and s.get('status')=='EXECUTOR_FAILURE_BACKOFF' and s.get('next_due_at'):
        try:
            if utcnow()<datetime.fromisoformat(s['next_due_at'].replace('Z','+00:00')): return
        except Exception: pass
    try:
        lease=int(os.environ.get('ATLAS_EXECUTOR_TIMEOUT_SECONDS','3300'))+600; code,o=Q.claim(role_id,lease)
        if code!=200 or not o.get('task'): return
        t=o['task']; tid=t['task_id']; dest=VAR/'inbox'/f'{tid}.json'
        if dest.exists(): qlog(f'claimed {tid} already in inbox; keeping lease'); 
        atomic_json(VAR/'inbox'/f'{tid}.lease.json',{'task_id':tid,'lease_id':o['lease_id'],'lease_expires_at':o['lease_expires_at'],'envelope_sha256':o['envelope_sha256'],'attempt':o.get('attempt')})
        atomic_json(dest,t); qlog(f'claimed {tid} lease={o["lease_id"][:8]} exp={o["lease_expires_at"]}')
    except Exception as e: qlog(f'pull failed: {e}')
def queue_settle(task,task_path,receipt,result,cp_rc):
    """Report outcome for a queue-leased task. COMPLETED -> local file already moved to done/. Never raises."""
    if not (Q and Q.configured() and task): return
    lp=VAR/'inbox'/f"{task['task_id']}.lease.json"
    if not lp.exists(): return
    try:
        L=read_json(lp); tid=task['task_id']; lid=L['lease_id']
        if cp_rc==0 and result:
            if result.get('task_complete'): fs='COMPLETED'
            elif result.get('blocker_class') in ('AUTHORITY_BOUNDARY','EXTERNAL_WAIT','HARD_TECHNICAL_IMPASSE'): fs='BLOCKED'
            else:
                code,o=Q.renew(tid,lid,int(os.environ.get('ATLAS_EXECUTOR_TIMEOUT_SECONDS','3300'))+int(read_json(ETC/'mission.json')['progress_interval_seconds'])+600); qlog(f'renewed {tid}: {code} {o}'); return
        else: fs='FAILED'
        payload={'receipt':receipt,'result':{k:result.get(k) for k in ('status','summary','frontier','blocker_class','mission_terminal','task_complete','material_evidence')} if result else None}
        _queue_settle_send(tid,lid,payload,fs,L.get('envelope_sha256'),lp,task_path)
        queue_push_evidence(tid,receipt,result)
    except Exception as e: qlog(f'settle failed: {e}')
def _queue_settle_send(tid,lid,payload,fs,esha,lp,task_path):
    pend=VAR/'state'/'pending-settlements'; pend.mkdir(parents=True,exist_ok=True); pp=pend/f'{tid}.json'
    try: code,o=Q.complete(tid,lid,payload,fs,esha)
    except Exception as e:
        # queue unreachable: persist the settlement and retry every tick (durable, idempotent on receipt hash)
        atomic_json(pp,{'task_id':tid,'lease_id':lid,'payload':payload,'final_status':fs,'envelope_sha256':esha,'task_path':str(task_path) if task_path else None,'first_failure_at':iso(),'error':str(e)[:200]})
        qlog(f'settlement for {tid} deferred (queue unreachable: {e}); persisted for retry'); return
    qlog(f'settled {tid} {fs}: {code} {o}'); pp.unlink(missing_ok=True)
    try:
        if code in (200,202):
            lp.unlink(missing_ok=True)
            if fs!='COMPLETED' and task_path and Path(task_path).exists(): os.replace(task_path,VAR/'done'/(Path(task_path).stem+f'.{fs.lower()}.json'))
        elif 400<=code<500:
            # non-retryable rejection: quarantine locally so we never busy-loop; queue keeps authoritative state
            qlog(f'settlement REJECTED for {tid} ({code}); quarantining local copy')
            if lp.exists(): os.replace(lp,VAR/'done'/f'{tid}.lease.settle-rejected.json')
            if task_path and Path(task_path).exists(): os.replace(task_path,VAR/'done'/f'{tid}.settle-rejected.json')
    except Exception as e: qlog(f'settle post-processing failed: {e}')
def queue_push_evidence(tid,receipt,result):
    """G5: push the cycle result + up to 8 referenced evidence files (<=256KB each, under work/evidence) to the queue's append-only evidence store. Never raises."""
    if not (Q and Q.configured()): return
    import re
    try:
        n=0; cyc=int(receipt.get('cycle',0)); rp=VAR/'results'/f'cycle-{cyc:08d}.json'
        if rp.exists(): code,o=Q.push_evidence(tid,str(rp),f'cycle-{cyc:08d}.result.json'); n+=code in (200,201)
        for item in (result or {}).get('material_evidence') or []:
            for m in re.findall('('+re.escape(str(VAR/'work/evidence'))+r'/[^\s,\'\"\)]+)',str(item)):
                p=Path(m)
                if p.is_file() and p.stat().st_size<=262144 and n<9:
                    code,o=Q.push_evidence(tid,str(p)); n+=code in (200,201)
        qlog(f'evidence pushed for {tid}: {n} file(s)')
    except Exception as e: qlog(f'evidence push failed: {e}')
def queue_retry_pending():
    if not (Q and Q.configured()): return
    pend=VAR/'state'/'pending-settlements'
    if not pend.exists(): return
    for pp in sorted(pend.glob('*.json')):
        try:
            P=read_json(pp); tp=P.get('task_path'); lp=VAR/'inbox'/f"{P['task_id']}.lease.json"
            _queue_settle_send(P['task_id'],P['lease_id'],P['payload'],P['final_status'],P.get('envelope_sha256'),lp,Path(tp) if tp else None)
        except Exception as e: qlog(f'pending settlement retry failed for {pp.name}: {e}')
def queue_heartbeat(s,role,mission):
    if not (Q and Q.configured()): return
    try: Q.heartbeat(role['role_id'],{'mission_id':mission['mission_id'],'status':s['status'],'frontier':str(s.get('frontier'))[:300],'cycle':s.get('cycle')})
    except Exception as e: qlog(f'heartbeat failed: {e}')
def utcnow(): return datetime.now(timezone.utc)
def iso(dt=None): return (dt or utcnow()).isoformat().replace('+00:00','Z')
def read_json(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def atomic_json(p,obj):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix(p.suffix+'.tmp'); t.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n',encoding='utf-8'); os.replace(t,p)
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
def load_env(p):
    p=Path(p)
    if not p.exists(): return
    try: text=p.read_text(encoding='utf-8')
    except OSError as e: print(f'node.env not readable ({e}); relying on systemd EnvironmentFile',file=sys.stderr); return
    for raw in text.splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1); os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))
def state_load():
    p=VAR/'state/state.json'
    if p.exists(): return read_json(p)
    return {'schema_version':1,'status':'OBSERVE_ONLY','cycle':0,'consecutive_failures':0,'cycle_times':[],'mission_terminal':False,'frontier':'BOOTSTRAP','last_cycle_at':None,'last_success_at':None,'next_due_at':None}
def state_save(s): atomic_json(VAR/'state/state.json',s)
def heartbeat(s,role,mission):
    atomic_json(VAR/'state/heartbeat.json',{'schema_version':1,'node_role':role['role_id'],'mission_id':mission['mission_id'],'status':s['status'],'frontier':s['frontier'],'mission_terminal':s['mission_terminal'],'at':iso()}); queue_heartbeat(s,role,mission)
def next_task(role_id):
    inbox=VAR/'inbox'; inbox.mkdir(parents=True,exist_ok=True); items=[]
    for p in inbox.glob('*.json'):
        try:
            o=read_json(p)
            if o.get('target_role')==role_id and not (VAR/'state'/'pending-settlements'/f"{o.get('task_id')}.json").exists(): items.append((int(o.get('priority',50)),p,o))
        except Exception: pass
    if not items: return None
    items.sort(key=lambda x:(-x[0],x[1].name)); return items[0][1],items[0][2]
def due(s,m,has_task):
    if has_task: return True
    nd=s.get('next_due_at')
    if nd:
        try:
            if utcnow()<datetime.fromisoformat(nd.replace('Z','+00:00')): return False
        except Exception: pass
    return bool(m.get('continuous',True))
def rate_allowed(s):
    maxph=int(os.environ.get('ATLAS_MAX_CYCLES_PER_HOUR','4')); cutoff=utcnow()-timedelta(hours=1); kept=[]
    for t in s.get('cycle_times',[]):
        try:
            if datetime.fromisoformat(t.replace('Z','+00:00'))>=cutoff: kept.append(t)
        except Exception: pass
    s['cycle_times']=kept; return len(kept)<maxph
def authoring_section(role):
    pp=ETC/'task-authoring-policy.json'
    if not pp.exists(): return ''
    try:
        import hashlib; raw=pp.read_bytes(); pol=json.loads(raw); psha=hashlib.sha256(raw).hexdigest()
        if pol.get('author_role')!=role['role_id']: return ''
        missing=[]
        try:
            if Q and Q.configured():
                code,o=Q.call('GET','/tasks'); keys={t.get('idempotency_key') for t in o.get('tasks',[])} if code==200 else set()
                missing=[io for io in pol.get('initial_authorized_objectives',[]) if io['idempotency_key'] not in keys]
        except Exception: pass
        classes='\n'.join(f"  - {k}: target_roles={v['target_roles']}"+(' (no verdict dictation)' if v.get('no_verdict_dictation') else '')+(' (synthetic only)' if v.get('synthetic_only') else '') for k,v in pol['allowed_classes'].items())
        init='\n'.join(f"  - idempotency_key={io['idempotency_key']} class={io['task_class']} target_role={io['target_role']} mission={io['mission_id']}: {io['outcome_hint']}" for io in missing) or '  (none pending)'
        return f"""
<task_authoring_authority>
Policy {pol['policy_id']} v{pol['policy_version']} sha256={psha} (authority AUTH-VPS3-TASK-AUTHORING-001, owner grant {pol.get('issued_by_owner')}).
You may AUTHOR, PRIORITIZE, DECOMPOSE, DISPATCH, RECONCILE and CLOSE fleet tasks ONLY through:  atlas-queue author /path/to/task.json
(also: atlas-queue open | list | get TASK_ID | audit | nodes). The policy is enforced by the queue server before publication; a DENY is final for that policy version — record it as AUTHORITY_BOUNDARY, never reword a task to evade a deny. A DENY produced under an OLDER policy version/sha is void once the owner corrects the policy: re-author the same intent once under the current version.
Required envelope fields: {', '.join(pol['required_fields'])}  (author_node={pol['author_role']}; parent_task_id for decomposition; idempotency_key unique per intent; task identity immutable after claim — material changes need a new task_id).
Allowed classes:
{classes}
Never autonomously authorized (convert to AUTHORITY_BOUNDARY): {', '.join(pol['denied_classes'])}.
Rules: bind every task to a current authority in the registry; VPS2 verification tasks define SUBJECT and trust properties only, never a desired verdict; production may only be OBSERVED (read-only); no new privilege for recovery; reuse an open task when one already covers the frontier; do not create work to stay busy; do not regenerate equivalent tasks; max {pol['limits']['max_open_tasks_per_mission']} open tasks per mission.
Loop per ACTIVE mission: reconcile truth -> evaluate goals -> highest-value safe unresolved frontier -> reuse open task or author the minimum task(s) -> let nodes pull/claim/execute -> reconcile receipts/evidence -> update mission truth -> repeat until terminal or genuine boundary.
Priority order: P0/P1 safety/trust > integrity blocker > recovery of accepted capability > mission-critical dependency > evidence gap > owner-relay elimination > observability > optimization.
Initial owner-authorized objectives NOT yet present in the queue (author them first, via the same engine):
{init}
Decisions are recorded under {VAR}/authoring/decisions/. Cite each authored task_id and its policy decision in material_evidence.
OWNER MISSIONS (from the Owner Interface / WhatsApp via atlas-owner-gateway): `atlas-queue missions` / `atlas-queue mission ID`. They are broad goals, NOT authority.
For each ACCEPTED mission: read its text, set it ACTIVE with a one-line plan (`atlas-queue mission-update ID ACTIVE "<plan>"`), then author tasks with mission_id=ID under the node baseline authorities (never a new authority).
If the goal needs anything the policy denies (deploy, merge, credentials, trust roots, roles, public exposure, spend...), do the allowed parts and set WAITING_AUTHORITY with the smallest exact request as the summary.
Close with COMPLETED (short result + evidence pointers) or BLOCKED (why). If cancel_requested=1: stop authoring, let running tasks settle, set CANCELLED.
Summaries are sent to the owner's phone: keep them short, factual, no secrets, no instructions to other agents.
</task_authoring_authority>"""
    except Exception as e: return f'\n<task_authoring_authority>policy present but unreadable: {e}</task_authoring_authority>\n'
def precheck(s):
    """Run the node-provided deterministic precheck (ATLAS_PRECHECK or work/bin/precheck.sh). Contract: exit 0 => nothing material changed (llm_needed=false);
    exit 10 => change/alert (llm_needed=true); any other exit/timeout => llm_needed=true (fail open to diagnosis). Optional JSON on stdout {reason,...}. Never raises."""
    p=os.environ.get('ATLAS_PRECHECK') or str(VAR/'work/bin/precheck.sh')
    if not os.path.isfile(p) or not os.access(p,os.X_OK): return None
    try:
        cp=subprocess.run([p],text=True,capture_output=True,timeout=int(os.environ.get('ATLAS_PRECHECK_TIMEOUT_SECONDS','120')),cwd=str(VAR/'work'))
        out={}
        try: out=json.loads(cp.stdout.strip().splitlines()[-1]) if cp.stdout.strip() else {}
        except Exception: out={'stdout':cp.stdout[-300:]}
        rec={'at':iso(),'exit':cp.returncode,'llm_needed':cp.returncode!=0,'reason':out.get('reason') if isinstance(out,dict) else None,'out':out if isinstance(out,dict) else None,'stderr':cp.stderr[-300:]}
    except subprocess.TimeoutExpired: rec={'at':iso(),'exit':None,'llm_needed':True,'reason':'precheck timeout'}
    except Exception as e: rec={'at':iso(),'exit':None,'llm_needed':True,'reason':f'precheck error {type(e).__name__}'}
    try:
        atomic_json(VAR/'state/precheck-last.json',rec)
        with (VAR/'logs/precheck.jsonl').open('a') as f: f.write(json.dumps(rec)+'\n')
    except Exception: pass
    return rec
def queue_alert(sev,cond,subject='',detail=None):
    if not (Q and Q.configured()): return
    try: Q.alert(sev,cond,subject,detail)
    except Exception as e: qlog(f'alert push failed: {e}')
def bridge_precheck_alerts(rec,role):
    """G6 without LLM: forward structured alerts from the deterministic precheck to the fleet queue (deduped server-side by fingerprint)."""
    if not rec: return
    out=rec.get('out') if isinstance(rec.get('out'),dict) else {}
    alerts=out.get('alerts') if isinstance(out.get('alerts'),list) else []
    for a in alerts[:10]:
        if isinstance(a,dict) and a.get('condition'): queue_alert(a.get('severity','P3'),str(a['condition'])[:120],str(a.get('subject',role['role_id']))[:200],{'from':'precheck','reason':a.get('reason')})
    if rec.get('exit')==10 and not alerts: queue_alert('P3','precheck_change',role['role_id'],{'reason':rec.get('reason')})
    if rec.get('exit') not in (0,10,None) : queue_alert('P2','precheck_error',role['role_id'],{'exit':rec.get('exit'),'reason':rec.get('reason')})
def policy_state(role):
    """Deterministic 'material change' signals the precheck cannot see: policy sha changed, or an owner-seeded objective is not yet in the queue."""
    pp=ETC/'task-authoring-policy.json'
    if not pp.exists(): return None
    try:
        import hashlib; raw=pp.read_bytes(); pol=json.loads(raw); psha=hashlib.sha256(raw).hexdigest()
        if pol.get('author_role')!=role['role_id']: return None
        missing=[]
        if Q and Q.configured():
            code,o=Q.call('GET','/tasks'); keys={t.get('idempotency_key') for t in o.get('tasks',[])} if code==200 else None
            if keys is not None: missing=[io['idempotency_key'] for io in pol.get('initial_authorized_objectives',[]) if io['idempotency_key'] not in keys]
        owner=[]
        if Q and Q.configured():
            code,o=Q.call('GET','/missions')
            if code==200: owner=[m for m in o.get('missions',[]) if m.get('source')=='owner-interface' and (m.get('state')=='ACCEPTED' or m.get('cancel_requested')) and m.get('state') not in ('COMPLETED','CANCELLED','REJECTED')]
        return {'policy_sha256':psha,'pending_objectives':missing,'pending_owner_missions':[m['mission_id'] for m in owner]}
    except Exception as e: qlog(f'policy_state failed: {e}'); return None
def continuous_goals():
    p=ETC/'contracts/GLOBAL-CONTINUOUS-GOALS.json'
    if not p.exists(): return ''
    try: return '\n<global_continuous_goals>\n'+json.dumps(read_json(p),indent=2)+'\n</global_continuous_goals>\n'
    except Exception: return ''
def prompt_text(contract,goals,role,mission,authority,state,task,result_path):
    task_txt=json.dumps(task,indent=2) if task else 'No queued task. Derive the highest-value safe next action from the mission.'
    return f"""ATLAS AUTONOMOUS MISSION CYCLE

You inherit and MUST obey the global contract. Treat it as constraints, not a procedural script.

<global_contract>
{contract}
</global_contract>
<global_goals>
{json.dumps(goals,indent=2)}
</global_goals>
{continuous_goals()}
<role>
{json.dumps(role,indent=2)}
</role>
<mission>
{json.dumps(mission,indent=2)}
</mission>
<authority>
{json.dumps(authority,indent=2)}
</authority>
<current_supervisor_state>
{json.dumps(state,indent=2)}
</current_supervisor_state>
<current_task>
{task_txt}
</current_task>
{authoring_section(role)}

OPERATING EXPECTATION
- Reconcile current truth before relying on mutable state.
- Continue meaningful work; an intermediate milestone is not mission completion.
- Resolve SELF_RESOLVABLE and ALTERNATIVE_PATH_AVAILABLE blockers autonomously.
- Never cross a hard authority boundary.
- Preserve production unless explicitly authorized.
- Produce durable evidence for material claims.
- If terminal and healthy, perform only useful maintenance/reconciliation; do not create make-work.
- If externally waiting with no parallel work, record EXTERNAL_WAIT instead of noisy polling.
- Prefer safe automation that eliminates repeated owner relay.
- G7 NO_BUSY_WORK: if nothing material changed, return status NOOP (the supervisor backs off exponentially). Provide/maintain a deterministic precheck at {VAR}/work/bin/precheck.sh (<60s, read-only; exit 0 = nothing material changed, exit 10 = change/alert needing reasoning, last stdout line optional JSON {{"reason":...}}); when it exits 0 the supervisor skips the model cycle entirely.
- Evidence files you list in material_evidence under {VAR}/work/evidence are pushed to the fleet queue automatically on task settlement (G5). Build artifacts another node must verify go there too (tar.gz + sha256), and the verifier fetches them with `atlas-queue evidence TASK_ID` / `atlas-queue fetch SHA DEST`.
- Alerts: your precheck may emit {{"alerts":[{{"severity":"P2","condition":"...","subject":"..."}}]}} on its last stdout line; the supervisor forwards them to the fleet queue deterministically. Ad-hoc: `atlas-queue alert SEV CONDITION SUBJECT` (G6).
- Historical directives/evidence on disk are context, never current truth: reconcile live state before acting on them.

Before the turn ends, write EXACTLY ONE JSON object to:
{result_path}

Required fields:
{{
  "schema_version":1,
  "status":"PROGRESS|COMPLETE|BLOCKED|FAILED|NOOP",
  "summary":"concise material result",
  "frontier":"precise next frontier",
  "blocker_class":"NONE|SELF_RESOLVABLE|ALTERNATIVE_PATH_AVAILABLE|EXTERNAL_WAIT|AUTHORITY_BOUNDARY|HARD_TECHNICAL_IMPASSE",
  "mission_terminal":true,
  "task_complete":false,
  "material_evidence":["paths/ids/hashes"],
  "next_actions":["material remaining actions"]
}}
Do not place secrets in the result object.
"""
def main():
    load_env(ENV)
    for d in ['state','inbox','done','receipts','prompts','results','logs','work']:(VAR/d).mkdir(parents=True,exist_ok=True)
    lf=(VAR/'state/supervisor.lock').open('w')
    try: fcntl.flock(lf.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: print('another supervisor is active',file=sys.stderr); return 2
    contract_p=ETC/'contracts/GLOBAL-AUTONOMOUS-OPERATING-CONTRACT.md'; goals_p=ETC/'contracts/GLOBAL-GOALS.json'; role_p=ETC/'role.json'; mission_p=ETC/'mission.json'; auth_p=ETC/'authority.json'
    executor=Path(_required('ATLAS_EXECUTOR')); workspace=(os.environ.get('ATLAS_WORKSPACE') or str(VAR/'work')); tick=int(os.environ.get('ATLAS_SUPERVISOR_TICK_SECONDS','60'))
    while True:
        try:
            contract=contract_p.read_text(encoding='utf-8'); goals=read_json(goals_p); role=read_json(role_p); mission=read_json(mission_p); authority=read_json(auth_p); s=state_load(); queue_retry_pending(); queue_pull(role['role_id'],s); pair=next_task(role['role_id']); has_task=pair is not None
            if not ENABLE.exists(): s['status']='OBSERVE_ONLY'; s['frontier']='ENABLE_AUTONOMY_REQUIRED'; state_save(s); heartbeat(s,role,mission); time.sleep(tick); continue
            if not due(s,mission,has_task) or not rate_allowed(s): state_save(s); heartbeat(s,role,mission); time.sleep(tick); continue
            if not has_task and not s.get('mission_terminal'):
                pc=precheck(s); bridge_precheck_alerts(pc,role); ps=policy_state(role); material=None
                if ps:
                    if ps['pending_objectives']: material=f"pending owner objectives {ps['pending_objectives']}"
                    elif ps.get('pending_owner_missions'): material=f"owner missions awaiting intake/cancel {ps['pending_owner_missions']}"
                    elif s.get('last_policy_sha256') and s['last_policy_sha256']!=ps['policy_sha256']: material='policy changed'
                if material: qlog(f'model cycle required: {material}')
                if pc and pc.get('llm_needed') is False and not material:
                    # deterministic code answered: nothing material changed -> no model cycle (G7). Re-check at progress cadence, LLM at most every maintenance interval.
                    s['precheck_skips']=int(s.get('precheck_skips',0))+1; s['status']=s.get('status') if s.get('status') in ('MAINTAINING','EXTERNAL_WAIT','AUTHORITY_BOUNDARY') else 'QUIET'
                    last_llm=s.get('last_cycle_at'); force=False
                    try: force=bool(last_llm) and utcnow()-datetime.fromisoformat(last_llm.replace('Z','+00:00'))>timedelta(seconds=int(mission['maintenance_interval_seconds']))
                    except Exception: pass
                    if not force:
                        s['next_due_at']=iso(utcnow()+timedelta(seconds=int(mission['progress_interval_seconds']))); s['frontier']=f"PRECHECK_QUIET:{pc.get('reason','no change')}"[:300]; state_save(s); heartbeat(s,role,mission); time.sleep(tick); continue
            if not executor.exists(): s['status']='EXECUTOR_UNAVAILABLE'; s['frontier']=f'EXECUTOR_MISSING:{executor}'; state_save(s); heartbeat(s,role,mission); time.sleep(max(tick,300)); continue
            task_path,task=pair if pair else (None,None); cycle=int(s.get('cycle',0))+1; result_path=VAR/'results'/f'cycle-{cycle:08d}.json'; prompt_path=VAR/'prompts'/f'cycle-{cycle:08d}.md'; prompt=prompt_text(contract,goals,role,mission,authority,s,task,result_path); prompt_path.write_text(prompt,encoding='utf-8')
            ps2=policy_state(role); s['last_policy_sha256']=ps2['policy_sha256'] if ps2 else s.get('last_policy_sha256')
            started=utcnow(); s['status']='ACTIVE'; s['cycle']=cycle; s['cycle_times']=s.get('cycle_times',[])+[iso(started)]; s['last_cycle_at']=iso(started); s['frontier']='EXECUTOR_RUNNING'; state_save(s); heartbeat(s,role,mission)
            xenv={k:v for k,v in os.environ.items() if not k.startswith('ATLAS_QUEUE_')}; cp=subprocess.run([str(executor),str(prompt_path),str(result_path),workspace],text=True,capture_output=True,env=xenv,timeout=int(os.environ.get('ATLAS_EXECUTOR_TIMEOUT_SECONDS','3300')),check=False)
            out=VAR/'logs'/f'cycle-{cycle:08d}.stdout'; err=VAR/'logs'/f'cycle-{cycle:08d}.stderr'; out.write_text(cp.stdout or '',encoding='utf-8'); err.write_text(cp.stderr or '',encoding='utf-8')
            result=None; result_error=None
            if result_path.exists():
                try: result=read_json(result_path)
                except Exception as e: result_error=f'invalid result JSON:{e}'
            else: result_error='executor did not create cycle result'
            receipt_obj={'schema_version':1,'cycle':cycle,'started_at':iso(started),'completed_at':iso(),'role_id':role['role_id'],'mission_id':mission['mission_id'],'task_id':task.get('task_id') if task else None,'executor_exit_code':cp.returncode,'contract_sha256':sha(contract_p),'goals_sha256':sha(goals_p),'role_sha256':sha(role_p),'mission_sha256':sha(mission_p),'authority_sha256':sha(auth_p),'task_sha256':sha(task_path) if task_path else None,'prompt_sha256':sha(prompt_path),'stdout_sha256':sha(out),'stderr_sha256':sha(err),'result_sha256':sha(result_path) if result_path.exists() else None,'result_error':result_error}; atomic_json(VAR/'receipts'/f'cycle-{cycle:08d}.json',receipt_obj)
            if cp.returncode==0 and result:
                s['consecutive_failures']=0; s['last_success_at']=iso(); s['mission_terminal']=bool(result.get('mission_terminal')); s['frontier']=str(result.get('frontier','UNKNOWN')); bc=result.get('blocker_class','NONE')
                if result.get('status')=='FAILED' or bc=='HARD_TECHNICAL_IMPASSE': queue_alert('P1','cycle_failed_or_impasse',role['role_id'],{'cycle':cycle,'frontier':str(result.get('frontier'))[:200]})
                if s['mission_terminal']: s['status']='MAINTAINING'
                elif bc=='EXTERNAL_WAIT': s['status']='EXTERNAL_WAIT'
                elif bc=='AUTHORITY_BOUNDARY': s['status']='AUTHORITY_BOUNDARY'
                else:s['status']='ACTIVE'
                if task_path and result.get('task_complete'): os.replace(task_path,VAR/'done'/task_path.name)
                no_task=next_task(role['role_id']) is None
                quiet=bool(result.get('mission_terminal')) or (result.get('blocker_class') in ('EXTERNAL_WAIT','AUTHORITY_BOUNDARY') and no_task) or (result.get('status')=='NOOP' and no_task)
                fh=hashlib.sha256(str(result.get('frontier','')).strip().lower().encode()).hexdigest()[:16]
                s['idle_streak']=int(s.get('idle_streak',0))+1 if (no_task and not result.get('task_complete') and (fh==s.get('last_frontier_hash') or result.get('status')=='NOOP')) else 0
                s['last_frontier_hash']=fh
                prog=int(mission['progress_interval_seconds']); maint=int(mission['maintenance_interval_seconds'])
                interval=maint if quiet else min(maint,prog*(2**min(int(s['idle_streak']),6)))   # G7: unchanged frontier => exponential quiet backoff
                s['next_due_at']=iso(utcnow()+timedelta(seconds=interval)); s['last_interval_seconds']=interval
            else:
                fails=int(s.get('consecutive_failures',0))+1; s['consecutive_failures']=fails; s['status']='EXECUTOR_FAILURE_BACKOFF'; s['frontier']=result_error or f'EXECUTOR_EXIT_{cp.returncode}'; s['next_due_at']=iso(utcnow()+timedelta(seconds=min(3600,60*(2**min(fails,6)))))
                queue_alert('P1' if fails>=3 else 'P2','executor_failure',role['role_id'],{'cycle':cycle,'exit':cp.returncode,'error':result_error,'consecutive':fails})
            queue_settle(task,task_path,receipt_obj,result,cp.returncode); state_save(s); heartbeat(s,role,mission)
        except subprocess.TimeoutExpired:
            s=state_load(); s['status']='EXECUTOR_FAILURE_BACKOFF'; s['frontier']='EXECUTOR_TIMEOUT'; s['consecutive_failures']=int(s.get('consecutive_failures',0))+1; s['next_due_at']=iso(utcnow()+timedelta(minutes=10)); state_save(s)
        except Exception as e:
            s=state_load(); s['status']='EXECUTOR_FAILURE_BACKOFF'; s['frontier']=f'SUPERVISOR_ERROR:{type(e).__name__}'; state_save(s); print(f'supervisor error:{e}',file=sys.stderr)
        time.sleep(tick)
if __name__=='__main__': raise SystemExit(main())
