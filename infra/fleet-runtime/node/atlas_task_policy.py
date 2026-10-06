#!/usr/bin/env python3
"""ATLAS task-authoring policy engine (shared by the queue server and the node CLI).
evaluate(task, policy, open_tasks) -> decision dict {decision: ALLOW|DENY, reason, checks[], policy_version, policy_sha256, task_class, authority_reference}
Deterministic, prompt-independent. A DENY must never be published."""
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone

def load_policy(path):
    raw=open(path,'rb').read(); p=json.loads(raw); p['_sha256']=hashlib.sha256(raw).hexdigest(); return p

def _text(task):
    parts=[str(task.get(k,'')) for k in ('outcome','scope','success_condition','evidence_requirements')]
    return ' '.join(json.dumps(x) if not isinstance(x,str) else x for x in parts)

def evaluate(task, policy, open_tasks=None, now=None):
    now=now or datetime.now(timezone.utc); checks=[]; open_tasks=open_tasks or []
    def deny(code,reason):
        checks.append({'check':code,'ok':False,'detail':reason})
        return {'decision':'DENY','reason':f'{code}: {reason}','checks':checks,'policy_id':policy.get('policy_id'),'policy_version':policy.get('policy_version'),'policy_sha256':policy.get('_sha256'),'task_class':task.get('task_class') if isinstance(task,dict) else None,'authority_reference':task.get('authority_reference') if isinstance(task,dict) else None,'evaluated_at':now.isoformat(timespec='seconds')}
    def ok(code,detail=''): checks.append({'check':code,'ok':True,'detail':detail})
    if not isinstance(task,dict): return deny('SCHEMA','task envelope must be an object')
    req=set(policy['required_fields']); opt=set(policy.get('optional_fields',[]))
    missing=sorted(req-set(task)); extra=sorted(set(task)-req-opt)
    if missing: return deny('SCHEMA',f'missing fields {missing}')
    if extra: return deny('SCHEMA',f'unexpected fields {extra}')
    if task.get('schema_version')!=1: return deny('SCHEMA','schema_version must be 1')
    for k in ('task_id','mission_id','target_role','task_class','outcome','authority_reference','idempotency_key','success_condition','created_at'):
        if not isinstance(task.get(k),str) or not task[k].strip(): return deny('SCHEMA',f'{k} must be a non-empty string')
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
    if spec.get('synthetic_only') and not re.search(r'synthetic|isolated|fixture|canary',text,re.I): return deny('SYNTHETIC_REQUIRED','SAFE_CANARY must name a synthetic/isolated subject')
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
    return {'decision':'ALLOW','reason':'all checks passed','checks':checks,'policy_id':policy.get('policy_id'),'policy_version':policy.get('policy_version'),'policy_sha256':policy.get('_sha256'),'task_class':cls,'authority_reference':task['authority_reference'],'evaluated_at':now.isoformat(timespec='seconds')}
