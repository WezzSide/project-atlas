#!/usr/bin/env python3
"""ATLAS task-authoring policy engine (shared by the queue server and the node CLI).
evaluate(task, policy, open_tasks) -> decision dict {decision: ALLOW|DENY, code, reason, checks[], policy_version, policy_sha256, task_class, authority_reference}
Deterministic, prompt-independent. A DENY must never be published.
The policy document is validated before any task check; an unusable or unavailable policy is an explicit DENY, never an exception."""
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone

# Canonical identifier (task_id, parent_task_id, mission_id, idempotency_key): first character A-Z a-z 0-9, then A-Z a-z 0-9 . _ -,
# at most 128 characters, and no '..' anywhere. Matched on the raw value: nothing is decoded, trimmed or normalised first.
CANONICAL_ID_MAX=128
_CANONICAL_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,%d}'%(CANONICAL_ID_MAX-1))
_RFC3339=re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})')
EXECUTION_SUBJECTS=('SYNTHETIC','PRODUCTION')
CORE_FIELDS=('schema_version','task_id','mission_id','target_role','task_class','outcome','authority_reference','idempotency_key','success_condition','created_at','author_node','scope','evidence_requirements','priority')
LIMIT_KEYS=('priority_min','priority_max','max_outcome_chars','max_open_tasks_total','max_open_tasks_per_mission')

def canonical_id(v):
    return type(v) is str and _CANONICAL_ID.fullmatch(v) is not None and '..' not in v

def rfc3339(v):
    if type(v) is not str or not _RFC3339.fullmatch(v): return False
    try: datetime.fromisoformat(v.replace('Z','+00:00')); return True
    except ValueError: return False

def load_policy(path):
    """Never raises on content: a missing, unreadable or malformed file yields a policy carrying _load_error, which evaluate() denies."""
    try: raw=open(path,'rb').read()
    except OSError as e: return {'_load_error':('POLICY_UNAVAILABLE',f'policy file unreadable ({type(e).__name__})'),'_sha256':None}
    digest=hashlib.sha256(raw).hexdigest()
    try: p=json.loads(raw)
    except (ValueError,RecursionError) as e: return {'_load_error':('POLICY_INVALID',f'policy is not valid JSON ({type(e).__name__})'),'_sha256':digest}
    if not isinstance(p,dict): return {'_load_error':('POLICY_INVALID','policy must be a JSON object'),'_sha256':digest}
    # Keys starting with '_' belong to the engine (_load_error, _sha256); the file may not supply them.
    if any(k.startswith('_') for k in p): return {'_load_error':('POLICY_INVALID','reserved key in policy'),'_sha256':digest}
    p['_sha256']=digest; return p

def _strs(v,nonempty=False): return isinstance(v,list) and all(type(x) is str and x for x in v) and (bool(v) or not nonempty)
def _compiles(p):
    if type(p) is not str: return False
    try: re.compile(p); return True
    except (re.error,OverflowError,RecursionError,ValueError,TypeError): return False

def policy_problem(p):
    """(code, detail) for the first reason the policy document is unusable, or None. Wrong types make the policy unusable; they never widen it."""
    if not isinstance(p,dict): return ('POLICY_INVALID','policy must be an object')
    if '_load_error' in p:
        le=p['_load_error']
        if isinstance(le,(tuple,list)) and len(le)==2 and all(type(x) is str and x for x in le) and le[0] in ('POLICY_UNAVAILABLE','POLICY_INVALID'): return tuple(le)
        return ('POLICY_INVALID','malformed load error')
    for k in ('policy_id','policy_version','author_role'):
        if type(p.get(k)) is not str or not p[k]: return ('POLICY_INVALID',f'{k} must be a non-empty string')
    if not _strs(p.get('required_fields'),True): return ('POLICY_INVALID','required_fields must be a non-empty list of strings')
    if [f for f in CORE_FIELDS if f not in p['required_fields']]: return ('POLICY_INVALID',f'required_fields must include {sorted(set(CORE_FIELDS)-set(p["required_fields"]))}')
    if not _strs(p.get('optional_fields',[])): return ('POLICY_INVALID','optional_fields must be a list of strings')
    lim=p.get('limits')
    if not isinstance(lim,dict) or any(type(lim.get(k)) is not int for k in LIMIT_KEYS): return ('POLICY_INVALID',f'limits must give integer {", ".join(LIMIT_KEYS)}')
    if not _strs(p.get('denied_classes')): return ('POLICY_INVALID','denied_classes must be a list of strings')
    ac=p.get('allowed_classes')
    if not isinstance(ac,dict): return ('POLICY_INVALID','allowed_classes must be an object')
    for cls,spec in ac.items():
        if not isinstance(spec,dict) or not _strs(spec.get('target_roles'),True): return ('POLICY_INVALID',f'allowed_classes.{cls}.target_roles must be a non-empty list of strings')
        for flag in ('no_verdict_dictation','synthetic_only'):
            if flag in spec and type(spec[flag]) is not bool: return ('POLICY_INVALID',f'allowed_classes.{cls}.{flag} must be a boolean')
    for name in ('authority_registry','mission_registry'):
        reg=p.get(name)
        if not isinstance(reg,dict) or not all(isinstance(e,dict) and type(e.get('status')) is str for e in reg.values()): return ('POLICY_INVALID',f'{name} must map ids to objects with a string status')
    hd=p.get('hard_deny_patterns')
    if not isinstance(hd,list) or not all(isinstance(e,dict) and type(e.get('id')) is str and e['id'] and _compiles(e.get('re')) for e in hd): return ('POLICY_INVALID','hard_deny_patterns must be a list of {id, re} with compiling patterns')
    vd=p.get('verdict_dictation_patterns')
    if not isinstance(vd,list) or not all(_compiles(x) for x in vd): return ('POLICY_INVALID','verdict_dictation_patterns must be a list of compiling patterns')
    dd=p.get('dedupe')
    if not isinstance(dd,dict) or not _strs(dd.get('open_states'),True): return ('POLICY_INVALID','dedupe.open_states must be a non-empty list of strings')
    for flag in ('deny_if_same_key_blocked','deny_if_same_key_completed'):
        if flag in dd and type(dd[flag]) is not bool: return ('POLICY_INVALID',f'dedupe.{flag} must be a boolean')
    return None

def _text(task):
    parts=[str(task.get(k,'')) for k in ('outcome','scope','success_condition','evidence_requirements')]
    return ' '.join(json.dumps(x) if not isinstance(x,str) else x for x in parts)

def evaluate(task, policy, open_tasks=None, now=None):
    now=now or datetime.now(timezone.utc); checks=[]; open_tasks=open_tasks or []
    pol=policy if isinstance(policy,dict) else {}
    def deny(code,reason):
        checks.append({'check':code,'ok':False,'detail':reason})
        return {'decision':'DENY','code':code,'reason':f'{code}: {reason}','checks':checks,'policy_id':pol.get('policy_id'),'policy_version':pol.get('policy_version'),'policy_sha256':pol.get('_sha256'),'task_class':task.get('task_class') if isinstance(task,dict) else None,'authority_reference':task.get('authority_reference') if isinstance(task,dict) else None,'evaluated_at':now.isoformat(timespec='seconds')}
    def ok(code,detail=''): checks.append({'check':code,'ok':True,'detail':detail})
    bad=policy_problem(policy)
    if bad: return deny(*bad)
    ok('POLICY_VALID')
    if not isinstance(task,dict): return deny('SCHEMA','task envelope must be an object')
    req=set(policy['required_fields']); opt=set(policy.get('optional_fields',[]))
    missing=sorted(req-set(task)); extra=sorted(set(task)-req-opt)
    if missing: return deny('SCHEMA',f'missing fields {missing}')
    if extra: return deny('SCHEMA',f'unexpected fields {extra}')
    if task.get('schema_version')!=1: return deny('SCHEMA','schema_version must be 1')
    for k in ('task_id','mission_id','target_role','task_class','outcome','authority_reference','idempotency_key','success_condition','created_at'):
        if not isinstance(task.get(k),str) or not task[k].strip(): return deny('SCHEMA',f'{k} must be a non-empty string')
    for k in ('task_id','mission_id','idempotency_key'):
        if not canonical_id(task[k]): return deny('SCHEMA',f'{k} is not a canonical identifier')
    if task.get('parent_task_id') is not None and not canonical_id(task['parent_task_id']): return deny('SCHEMA','parent_task_id is not a canonical identifier')
    if not rfc3339(task['created_at']): return deny('SCHEMA','created_at must be an RFC 3339 timestamp with Z or a numeric offset')
    if 'execution_subject' in task and task['execution_subject'] not in EXECUTION_SUBJECTS: return deny('SCHEMA',f'execution_subject must be one of {list(EXECUTION_SUBJECTS)}')
    if not isinstance(task.get('scope'),(str,dict,list)) or not task['scope']: return deny('SCHEMA','scope must be non-empty')
    if not isinstance(task.get('evidence_requirements'),(str,list)) or not task['evidence_requirements']: return deny('SCHEMA','evidence_requirements must be non-empty')
    if not isinstance(task.get('priority'),int) or not policy['limits']['priority_min']<=task['priority']<=policy['limits']['priority_max']: return deny('SCHEMA','priority out of range')
    if len(task['outcome'])>policy['limits']['max_outcome_chars']: return deny('SCHEMA','outcome too long')
    ok('SCHEMA')
    if task['author_node']!=policy['author_role']: return deny('AUTHOR',f"author_node must be {policy['author_role']}")
    ok('AUTHOR')
    cls=task['task_class']
    if cls in policy['denied_classes']: return deny('CLASS_DENIED',f'{cls} is never autonomously authorized -> AUTHORITY_BOUNDARY')
    if cls not in policy['allowed_classes']: return deny('CLASS_UNKNOWN',f'unknown task_class {cls}')
    ok('CLASS_ALLOWED',cls)
    spec=policy['allowed_classes'][cls]
    if task['target_role'] not in spec['target_roles']: return deny('ROLE_FOR_CLASS',f"{task['target_role']} not allowed for {cls}")
    ok('ROLE_FOR_CLASS')
    auth=policy['authority_registry'].get(task['authority_reference'])
    if not auth: return deny('AUTHORITY_MISSING',f"authority {task['authority_reference']} not in registry")
    if auth.get('status')!='current': return deny('AUTHORITY_NOT_CURRENT',f"authority status {auth.get('status')}")
    vu=auth.get('valid_until')
    if vu:
        try:
            if now>datetime.fromisoformat(vu.replace('Z','+00:00')): return deny('AUTHORITY_EXPIRED',f'valid_until {vu}')
        except Exception: return deny('AUTHORITY_EXPIRED','unparseable valid_until')
    if auth.get('subject') and auth['subject']!=task['target_role'] and task['authority_reference']!='AUTH-VPS3-TASK-AUTHORING-001': return deny('AUTHORITY_SCOPE',f"authority subject {auth['subject']} != target_role {task['target_role']}")
    ok('AUTHORITY_CURRENT')
    m=policy['mission_registry'].get(task['mission_id'])
    if not m: return deny('MISSION_UNKNOWN',task['mission_id'])
    if m.get('status')!='current': return deny('MISSION_SUPERSEDED',f"mission status {m.get('status')}")
    ok('MISSION_CURRENT')
    text=_text(task)
    for hd in policy['hard_deny_patterns']:
        if re.search(hd['re'],text,re.I|re.S): return deny('HARD_DENY',f"{hd['id']} matched -> AUTHORITY_BOUNDARY")
    ok('HARD_DENY_SCAN')
    if spec.get('no_verdict_dictation'):
        for pat in policy['verdict_dictation_patterns']:
            if re.search(pat,text,re.I|re.S): return deny('VERDICT_DICTATION','verification task must not prescribe PASS')
        ok('NO_VERDICT_DICTATION')
    # The author's structured declaration decides; task text is not consulted. It makes the classification explicit and auditable, not proven.
    if spec.get('synthetic_only') and task.get('execution_subject')!='SYNTHETIC': return deny('SYNTHETIC_REQUIRED',f'{cls} requires execution_subject SYNTHETIC')
    dd=policy['dedupe']; key=task['idempotency_key']
    for t in open_tasks:
        if t.get('idempotency_key')==key:
            st=t.get('state')
            if st in dd['open_states']: return deny('DUPLICATE_OPEN',f"open task {t.get('task_id')} already covers idempotency_key {key}")
            if dd.get('deny_if_same_key_blocked') and st=='BLOCKED': return deny('DUPLICATE_BLOCKED',f"{t.get('task_id')} with same key is BLOCKED (authority boundary) — do not regenerate")
            if dd.get('deny_if_same_key_completed') and st=='COMPLETED': return deny('ALREADY_DONE',f"{t.get('task_id')} with same key already COMPLETED — newer evidence makes this unnecessary")
        if t.get('task_id')==task['task_id']: return deny('TASK_ID_EXISTS','task identity is immutable; use a new task_id/version')
    ok('DEDUPE')
    lim=policy['limits']; opn=[t for t in open_tasks if t.get('state') in dd['open_states']]
    if len(opn)>=lim['max_open_tasks_total']: return deny('LIMIT_TOTAL',f"{len(opn)} open tasks >= {lim['max_open_tasks_total']}")
    if len([t for t in opn if t.get('mission_id')==task['mission_id']])>=lim['max_open_tasks_per_mission']: return deny('LIMIT_MISSION','open task cap for mission reached')
    ok('LIMITS')
    return {'decision':'ALLOW','code':'ALLOW','reason':'all checks passed','checks':checks,'policy_id':policy.get('policy_id'),'policy_version':policy.get('policy_version'),'policy_sha256':policy.get('_sha256'),'task_class':cls,'authority_reference':task['authority_reference'],'evaluated_at':now.isoformat(timespec='seconds')}
