#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, tempfile
from pathlib import Path
import jsonschema, yaml

HERE=Path(__file__).parent
OPTIONAL=('imports','applicability_facts','schema_authorities','schema_authority_links','checks','tests','assurances','semantic_reviews','links','sources','source_units','evidence_claims','external_exceptions')
SEMANTIC={'family','rule','assertion','guidance','definition','procedure','step','evidence-claim'}

def _schema():
 schema=json.loads((HERE/'standard-v6.schema.json').read_text(encoding='utf-8'))
 # The checked-in identity is repository-relative. Give jsonschema an absolute
 # runtime base so its legacy resolver keeps local fragment references local.
 schema['$id']=(HERE/'standard-v6.schema.json').resolve().as_uri()
 return schema
def atomic_write(path,data):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp): os.unlink(tmp)
def _maps(d):
 for k in OPTIONAL: d.setdefault(k,{})
 return d
def _index(items,errors):
 out={}
 def add(k,i,n):
  if i in out: errors.append(f'duplicate semantic id {i}')
  else: out[i]=(k,n)
 def walk(xs):
  for n in xs:
   add(n['kind'],n['id'],n)
   if n['kind']=='rule':
    for a in n['assertions']: add('assertion',f"{n['id']}#{a['id']}",a)
   if n['kind']=='procedure':
    for st in n['steps']: add('step',f"{n['id']}#{st['id']}",st)
   walk(n.get('children',[]))
 walk(items); return out
def _ancestry(items):
 out={}
 def walk(xs,parents):
  for n in xs:
   out[n['id']]=parents
   if n['kind']=='rule':
    for a in n['assertions']:out[f"{n['id']}#{a['id']}"]=parents+[n['id']]
   if n['kind']=='procedure':
    for st in n['steps']:out[f"{n['id']}#{st['id']}"]=parents+[n['id']]
   walk(n.get('children',[]),parents+[n['id']])
 walk(items,[]);return out
def _validate_document(document):
 d=_maps(copy_document(document)); e=[]; sem=_index(d['standards'],e); ancestry=_ancestry(d['standards'])
 def local(r,label):
  if r.get('document'):
   if r['document'] not in d['imports']: e.append(f"{label}: unknown import {r['document']}")
   return
  found=sem.get(r['ref'])
  if not found or found[0]!=r['kind']: e.append(f"{label}: dangling semantic {r['kind']} reference {r['ref']}")
 for uid,u in d['source_units'].items():
   if u['source']['ref'] not in d['sources']: e.append(f'source_units.{uid}: dangling source')
 for ident,(kind,node) in sem.items():
  for u in node.get('origin',{}).get('source_units',[]):
   if u['ref'] not in d['source_units']: e.append(f'{ident}.origin: dangling source unit')
  for r in node.get('origin',{}).get('derived_from',[]): local(r,f'{ident}.origin')
  if kind=='procedure':
   for field in ('invariants','completion_conditions'):
    ids=[x['id'] for x in node.get(field,[])]
    if len(ids)!=len(set(ids)):e.append(f'procedure {ident}.{field}: duplicate id')
   ids={x['id'] for x in node['steps']}
   graph={x['id']:x.get('requires',[]) for x in node['steps']}
   for sid,reqs in graph.items():
    for req in reqs:
     if req not in ids: e.append(f'procedure {ident}: dangling step requirement {req}')
   visiting=set(); done=set()
   def dfs(x):
    if x in visiting:return True
    if x in done:return False
    visiting.add(x)
    if any(y in graph and dfs(y) for y in graph[x]):return True
    visiting.remove(x);done.add(x);return False
   if any(dfs(x) for x in graph if x not in done):e.append(f'procedure {ident}: step dependency cycle')
 def status(n): return n.get('lifecycle','active'),n.get('resolution',{'state':'resolved'})
 for cid,c in d['evidence_claims'].items():
  sem[cid]=('evidence-claim',c)
  if c['artifact']['ref'] not in d['artifacts']:e.append(f'evidence_claims.{cid}.artifact: dangling artifact {c["artifact"]["ref"]}')
 for aid,a in d['schema_authorities'].items():
  artifact=d['artifacts'].get(a['artifact']['ref'])
  if not artifact:e.append(f'schema_authorities.{aid}.artifact: dangling artifact')
  elif artifact.get('format') != 'json' or 'schema' not in artifact.get('roles',[]):e.append(f'schema_authorities.{aid}.artifact: authority artifact must be a JSON Schema (format json, role schema)')
  if a['selector']['kind']=='json-pointer' and a['selector']['pointer']=='/':e.append(f'schema_authorities.{aid}.selector: JSON Pointer / does not mean whole schema; use whole-schema')
 for cid,c in d['checks'].items():
  if c['artifact']['ref'] not in d['artifacts']:e.append(f'checks.{cid}.artifact: dangling artifact {c["artifact"]["ref"]}')
 for lid,l in d['links'].items():
  local(l['source'],f'links.{lid}');local(l['target'],f'links.{lid}')
  if l['relation']=='remedied-by':
   source=l['source'];target=l['target']
   if source['kind']!='family':
    e.append(f'links.{lid}: remedied-by source must be a family')
   if target['kind'] not in {'family','procedure'}:
    e.append(f'links.{lid}: remedied-by target must be a family or procedure')
   if source.get('document') or 'skill-refactoring.diagnostic-signals' not in ancestry.get(source['ref'],[]):
    e.append(f'links.{lid}: remedied-by source must descend from diagnostic-signals')
   if target.get('document') or 'skill-refactoring.refactoring-moves' not in ancestry.get(target['ref'],[]):
    e.append(f'links.{lid}: remedied-by target must descend from refactoring-moves')
 for lid,l in d['schema_authority_links'].items():
  local(l['semantic_item'],f'schema_authority_links.{lid}'); a=d['schema_authorities'].get(l['authority']['ref']); ll,lr=status(l)
  if not a:e.append(f'schema_authority_links.{lid}: dangling authority')
 for aid,a in d['assurances'].items():
  local(a['assertion'],f'assurances.{aid}.assertion')
  if a['mechanism']['ref'] not in d['checks']:e.append(f'assurances.{aid}.mechanism: dangling check {a["mechanism"]["ref"]}')
 for tid,t in d['tests'].items():
  if t['artifact']['ref'] not in d['artifacts']:e.append(f'tests.{tid}.artifact: dangling artifact {t["artifact"]["ref"]}')
  for i,x in enumerate(t['targets']):
   target=x['target']; kind=target['kind']
   if kind=='check' and target['ref'] not in d['checks']:e.append(f'tests.{tid}.targets[{i}]: dangling check {target["ref"]}')
   elif kind=='schema-authority' and target['ref'] not in d['schema_authorities']:e.append(f'tests.{tid}.targets[{i}]: dangling schema authority {target["ref"]}')
   elif kind in SEMANTIC:local(target,f'tests.{tid}.targets[{i}]')
 for rid,r in d['semantic_reviews'].items():
  if r['instructions']['artifact']['ref'] not in d['artifacts']:e.append(f'semantic_reviews.{rid}.instructions.artifact: dangling artifact {r["instructions"]["artifact"]["ref"]}')
  seen=set()
  for c in r['coverage']:
   t=c['target']; kind=t['kind']
   if kind=='link':
    linked=d['links'].get(t['ref'])
    if not linked:e.append(f'semantic_reviews.{rid}: dangling link')
    elif linked['resolution']['state']=='unresolved' and (r['lifecycle']!='planned' or r['resolution']['state']!='unresolved'):e.append(f'semantic_reviews.{rid}: unresolved link requires planned unresolved review')
   elif kind=='source-unit':
    if t['ref'] not in d['source_units']:e.append(f'semantic_reviews.{rid}: dangling source unit')
   else:local(t,f'semantic_reviews.{rid}')
   for aspect in c['aspects']:
    sig=(kind,t['ref'],aspect)
    if sig in seen:e.append(f'semantic_reviews.{rid}.coverage: duplicate target aspect {sig}')
    seen.add(sig)
 return e

def copy_document(d):
 import copy; return copy.deepcopy(d)
def validate_document(document: dict, root: Path) -> list[str]:
 """Validate in-memory content, resolving declared import artifacts below root.

 Call validate_file when import digests and imported-document semantics must also
 be checked; this entry point proves that each declared import is locatable from
 the repository root instead of silently ignoring its filesystem contract.
 """
 try:jsonschema.validate(document,_schema())
 except jsonschema.ValidationError as x:return [f'schema validation failed: {x.message}']
 errors=_validate_document(document); root=Path(root).resolve()
 for alias,decl in document.get('imports',{}).items():
  artifact=document.get('artifacts',{}).get(decl['artifact']['ref'])
  if not artifact:continue
  raw=Path(artifact['path'])
  if raw.is_absolute():
   errors.append(f'imports.{alias}: import path must be repository-relative');continue
  target=(root/raw).resolve()
  if not target.is_relative_to(root):
   errors.append(f'imports.{alias}: import path escapes repository root');continue
  if not target.is_file():errors.append(f'imports.{alias}: missing import file under root {root}')
 return errors
def _select(lines,selector):
 if selector['kind']!='line-range': return None,'unsupported source selector for portable verifier'
 a,b=selector['start'],selector['end']
 if a<1 or b<a or b>len(lines): return None,'selector out of range'
 return ('\n'.join(lines[a-1:b])+'\n').encode(),None
def _resolve_artifact_path(artifact, root, label):
 raw=Path(artifact['path'])
 if raw.is_absolute():return None,f'{label}: artifact path must be repository-relative'
 target=(root/raw).resolve()
 if not target.is_relative_to(root):return None,f'{label}: artifact path escapes repository root'
 if not target.is_file():return None,f'{label}: missing artifact file'
 return target,None
def _resolve_json_pointer(value,pointer):
 if pointer == '': return value,None
 if not pointer.startswith('/'): return None,'must start with /'
 current=value
 for encoded in pointer[1:].split('/'):
  token=encoded.replace('~1','/').replace('~0','~')
  try:
   if isinstance(current,list): current=current[int(token)]
   elif isinstance(current,dict): current=current[token]
   else:return None,f'cannot descend through {token!r}'
  except (KeyError,IndexError,ValueError):return None,f'missing token {token!r}'
 return current,None
def validate_file(path,root=None,cache=None,_stack=None):
 path=Path(path).resolve(); root=Path(root).resolve() if root else path.parent.resolve(); cache={} if cache is None else cache; stack=list(_stack or [])
 if path in stack:return ['import cycle: '+' -> '.join(map(str,stack+[path]))]
 if path in cache:return cache[path][1]
 try:d=yaml.safe_load(path.read_text(encoding='utf-8'))
 except Exception as x:return [f'cannot load document: {x}']
 try:jsonschema.validate(d,_schema())
 except jsonschema.ValidationError as x:return [f'schema validation failed: {x.message}']
 errors=_validate_document(d); d=_maps(d); stack.append(path)
 # A schema authority is not merely a named artifact: load the JSON Schema,
 # validate its schema vocabulary, and prove that any selected fragment exists.
 for aid,authority in d['schema_authorities'].items():
  artifact=d['artifacts'].get(authority['artifact']['ref'])
  if not artifact:continue
  target,problem=_resolve_artifact_path(artifact,root,f'schema_authorities.{aid}.artifact')
  if problem:
   errors.append(problem);continue
  try:schema_document=json.loads(target.read_text(encoding='utf-8'))
  except Exception as exc:
   errors.append(f'schema_authorities.{aid}.artifact: cannot load JSON Schema: {exc}');continue
  try:jsonschema.validators.validator_for(schema_document).check_schema(schema_document)
  except jsonschema.SchemaError as exc:
   errors.append(f'schema_authorities.{aid}.artifact: invalid JSON Schema: {exc.message}');continue
  selector=authority['selector']
  if selector['kind']=='json-pointer':
   _,problem=_resolve_json_pointer(schema_document,selector['pointer'])
   if problem:errors.append(f'schema_authorities.{aid}.selector: JSON Pointer does not resolve: {problem}')
 # Semantic-review instructions identify a real file and a label within it;
 # instruction_id is deliberately not interpreted as an executable interface.
 for rid,review in d['semantic_reviews'].items():
  artifact=d['artifacts'].get(review['instructions']['artifact']['ref'])
  if artifact:
   _,problem=_resolve_artifact_path(artifact,root,f'semantic_reviews.{rid}.instructions.artifact')
   if problem:errors.append(problem)
 # Verify sources and source units.
 source_bytes={}
 for sid,s in d['sources'].items():
  art=d['artifacts'].get(s['artifact']['ref'])
  if not art:continue
  target,problem=_resolve_artifact_path(art,root,f'sources.{sid}')
  if problem:errors.append(problem);continue
  data=target.read_bytes();source_bytes[sid]=(data,target)
  actual='sha256:'+hashlib.sha256(data).hexdigest()
  if actual!=s['digest']:errors.append(f'sources.{sid}.digest: source digest mismatch expected {s["digest"]} actual {actual}')
 for uid,u in d['source_units'].items():
  pair=source_bytes.get(u['source']['ref'])
  if not pair:continue
  lines=pair[0].decode().splitlines(); selected,err=_select(lines,u['selector'])
  if err:errors.append(f'source_units.{uid}: {err}');continue
  actual='sha256:'+hashlib.sha256(selected).hexdigest()
  if actual!=u['content_digest']:errors.append(f'source_units.{uid}.content_digest: source-unit digest mismatch expected {u["content_digest"]} actual {actual}')
 # Resolve imports with cache.
 imported={}
 for alias,decl in d['imports'].items():
  art=d['artifacts'].get(decl['artifact']['ref'])
  if not art:continue
  target,problem=_resolve_artifact_path(art,root,f'imports.{alias}')
  if problem:errors.append(problem);continue
  actual='sha256:'+hashlib.sha256(target.read_bytes()).hexdigest()
  if actual!=decl['digest']:errors.append(f'imports.{alias}: digest mismatch');continue
  try:child=yaml.safe_load(target.read_text(encoding='utf-8'))
  except Exception as exc:errors.append(f'imports.{alias}: cannot load imported document {target}: {exc}');continue
  try:jsonschema.validate(child,_schema())
  except jsonschema.ValidationError as exc:errors.append(f'imports.{alias}: schema validation failed at {exc.json_path}: {exc.message}');continue
  imported[alias]=_maps(child)
  for field in ('standard_version','revision'):
   if child[field]!=decl[field]:errors.append(f'imports.{alias}: {field} mismatch')
  if child['id']!=decl['standard_id']:errors.append(f'imports.{alias}: standard_id mismatch')
  errors.extend(f'imports.{alias}: {x}' for x in validate_file(target,root,cache,stack))
 # Every external semantic reference site uses one resolver.
 refs=[]
 for lid,l in d['links'].items():refs += [(f'links.{lid}.source',l['source']),(f'links.{lid}.target',l['target'])]
 sem=_index(d['standards'],[])
 sem.update({ident:('evidence-claim',claim) for ident,claim in d['evidence_claims'].items()})
 for ident,(_,node) in sem.items():
  refs += [(f'{ident}.origin.derived_from',r) for r in node.get('origin',{}).get('derived_from',[]) if r.get('document')]
 for rid,review in d['semantic_reviews'].items():
  refs += [(f'semantic_reviews.{rid}.coverage',c['target']) for c in review['coverage'] if c['target'].get('document')]
 for aid,a in d['assurances'].items():refs.append((f'assurances.{aid}.assertion',a['assertion']))
 for lid,l in d['schema_authority_links'].items():refs.append((f'schema_authority_links.{lid}.semantic_item',l['semantic_item']))
 for tid,t in d['tests'].items():
  refs += [(f'tests.{tid}.targets[{i}]',x['target']) for i,x in enumerate(t['targets']) if x['target']['kind'] in SEMANTIC]
 for xid,x in d['external_exceptions'].items():refs.append((f'external_exceptions.{xid}.target',x['target']))
 for label,r in refs:
  if not r.get('document'):continue
  child=imported.get(r['document'])
  if not child:errors.append(f'{label}: unresolved import alias document={r["document"]}');continue
  child_sem=_index(child['standards'],[])
  child_sem.update({ident:('evidence-claim',claim) for ident,claim in child['evidence_claims'].items()})
  found=child_sem.get(r['ref'])
  if not found:errors.append(f'{label}: dangling imported semantic document={r["document"]} ref={r["ref"]}')
  elif found and found[0]!=r['kind']:errors.append(f'{label}: wrong imported semantic kind document={r["document"]} ref={r["ref"]} expected={r["kind"]} actual={found[0]}')
 cache[path]=(d,errors);return errors

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('document');p.add_argument('--root');a=p.parse_args();errs=validate_file(a.document,a.root)
 if errs:print('\n'.join(errs));raise SystemExit(1)
 print('validation passed')
