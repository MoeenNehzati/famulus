#!/usr/bin/env python3
"""Validate standard-v6 documents and their repository-bound dependencies."""

from __future__ import annotations
import argparse, hashlib, json, os, tempfile
from pathlib import Path
import jsonschema, yaml

HERE=Path(__file__).parent
OPTIONAL=('imports','domain_facts','schema_authorities','schema_authority_links','checks','tests','assurances','semantic_reviews','links','sources','source_units','evidence_claims','external_exceptions')
SEMANTIC={'family','rule','assertion','guidance','definition','procedure','step','evidence-claim'}

def _schema():
 """Load the standard-v6 JSON Schema with its runtime identity.

 Intent
 ------
 Read the checked-in schema and replace its relative identity with an absolute file URI.

 Rationale
 ---------
 The absolute identity keeps local fragment resolution anchored to the checked-in schema.

 Pseudocode
 ----------
 - set schema = parsed standard-v6 schema
 - set schema_identity = absolute schema file URI
 - return schema

 Wraps
 -----
 - none
 """
 schema=json.loads((HERE/'standard-v6.schema.json').read_text(encoding='utf-8'))
 # The checked-in identity is repository-relative. Give jsonschema an absolute
 # runtime base so its legacy resolver keeps local fragment references local.
 schema['$id']=(HERE/'standard-v6.schema.json').resolve().as_uri()
 return schema
def _prepare_schema_validator():
 """Build one checked validator for the standard-v6 schema.

 Intent
 ------
 Select the declared JSON Schema implementation, check the schema once, and instantiate it.

 Rationale
 ---------
 A prepared instance avoids repeating validator selection and schema checking for every document.

 Pseudocode
 ----------
 - set schema = loaded standard-v6 schema
 - set validator_type = implementation selected for schema
 - if schema is invalid for validator_type:
   - raise schema error
 - return validator instance for schema

 Wraps
 -----
 - none

 InstantiationsFromRepo
 ----------------------
 ._schema:
   why:
     constructs: "Builds the absolute-identity schema document supplied to validator selection, checking, and construction."
 """
 schema=_schema(); validator_type=jsonschema.validators.validator_for(schema); validator_type.check_schema(schema)
 return validator_type(schema)
def _validate_with_prepared_schema(document,schema_validator):
 """Validate one document with preserved best-error selection.

 Intent
 ------
 Run a prepared validator while selecting the same best matching error as jsonschema.validate.

 Rationale
 ---------
 Preserving best-match selection keeps diagnostic messages and paths unchanged while reusing preparation.

 Pseudocode
 ----------
 - set error = best matching error from document validation
 - if error exists:
   - raise error
 - return none

 Wraps
 -----
 - none
 """
 error=jsonschema.exceptions.best_match(schema_validator.iter_errors(document))
 if error is not None:raise error
def atomic_write(path,data):
 """Replace one file atomically with the supplied bytes.

 Intent
 ------
 Write and synchronize a sibling temporary file before replacing the destination.

 Rationale
 ---------
 The temporary-file boundary prevents readers from observing a partial standards artifact.

 Pseudocode
 ----------
 - set temporary_file = sibling file created for destination
 - set temporary_file_bytes = data
 - set synchronized_file = flushed temporary file
 - set destination = temporary file replacement

 Wraps
 -----
 - none
 """
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp): os.unlink(tmp)
def _maps(d):
 """Populate optional standard-v6 mapping fields in place.

 Intent
 ------
 Ensure every optional mapping field exists before semantic validation traverses it.

 Rationale
 ---------
 Uniform empty mappings let later checks avoid field-specific missing-value branches.

 Pseudocode
 ----------
 - for optional_field in optional mapping fields:
   - set missing_field = empty mapping
 - return document

 Wraps
 -----
 - none
 """
 for k in OPTIONAL: d.setdefault(k,{})
 return d
def _fact_equal(left,right):
 """Compare two domain facts without numeric type coercion.

 Intent
 ------
 Require both identical Python types and equal values for imported fact compatibility.

 Rationale
 ---------
 Python otherwise treats values such as one and one-point-zero as equal despite schema-level type differences.

 Pseudocode
 ----------
 - set same_type = left type equals right type
 - set same_value = left equals right
 - return same type and same value

 Wraps
 -----
 - none
 """
 return type(left) is type(right) and left==right
def evaluate_predicate(predicate,facts):
 """Evaluate an applicability predicate with three-valued logic.

 Intent
 ------
 Resolve fact, negation, conjunction, and disjunction predicates to true, false, or unknown.

 Rationale
 ---------
 Unknown propagation prevents missing repository facts from silently excluding applicable standards.

 Pseudocode
 ----------
 - if predicate selects one fact:
   - return fact comparison or unknown
 - if predicate is negated:
   - return inverted child state
 - set child_states = recursively evaluated predicates
 - return state selected by all or any semantics

 Wraps
 -----
 - none

 CallsFromRepo
 -------------
 ._fact_equal:
   why:
     computes: "Performs type-strict comparisons for fact equality and membership predicates."

 InstantiationsFromRepo
 ----------------------
 .evaluate_predicate:
   why:
     constructs: "Builds child predicate states used by negation, conjunction, and disjunction."
 """
 if 'fact' in predicate:
  fact=predicate['fact']
  if fact not in facts:return 'unknown'
  if 'equals' in predicate:return 'true' if _fact_equal(facts[fact],predicate['equals']) else 'false'
  return 'true' if any(_fact_equal(facts[fact],candidate) for candidate in predicate['in']) else 'false'
 if 'not' in predicate:
  state=evaluate_predicate(predicate['not'],facts)
  return {'true':'false','false':'true','unknown':'unknown'}[state]
 operator='all' if 'all' in predicate else 'any'
 states=[evaluate_predicate(child,facts) for child in predicate[operator]]
 if operator=='all':
  if 'false' in states:return 'false'
  return 'true' if all(state=='true' for state in states) else 'unknown'
 if 'true' in states:return 'true'
 return 'false' if all(state=='false' for state in states) else 'unknown'
def _index(items,errors):
 """Index semantic nodes and report duplicate identifiers.

 Intent
 ------
 Walk standards, assertions, steps, and children into one kind-and-node lookup.

 Rationale
 ---------
 One index supports consistent local and imported semantic reference checks.

 Pseudocode
 ----------
 - set semantic_index = empty mapping
 - for semantic_node in standards tree:
   - set identifier_entry = kind and node or duplicate error
 - return semantic index

 Wraps
 -----
 - none
 """
 out={}
 def add(k,i,n):
  """Add one semantic identifier to the current index.

  Intent
  ------
  Record a kind and node unless the identifier already exists.

  Rationale
  ---------
  Duplicate identifiers must produce findings without overwriting the first declaration.

  Pseudocode
  ----------
  - if identifier exists in semantic index:
    - set duplicate_finding = identifier
  - else:
    - set semantic_index_entry = kind and node

  Wraps
  -----
  - none
  """
  if i in out: errors.append(f'duplicate semantic id {i}')
  else: out[i]=(k,n)
 def walk(xs):
  """Walk semantic children into the current index.

  Intent
  ------
  Index each node plus rule assertions, procedure steps, and nested children.

  Rationale
  ---------
  Recursive traversal gives every supported semantic reference site one lookup surface.

  Pseudocode
  ----------
  - for node in semantic nodes:
    - set node_entry = kind identifier and node
    - set nested_entries = assertions steps and children
  - return none

  Wraps
  -----
  - none
  """
  for n in xs:
   add(n['kind'],n['id'],n)
   if n['kind']=='rule':
    for a in n['assertions']: add('assertion',f"{n['id']}#{a['id']}",a)
   if n['kind']=='procedure':
    for st in n['steps']: add('step',f"{n['id']}#{st['id']}",st)
   walk(n.get('children',[]))
 walk(items); return out
def _ancestry(items):
 """Build ancestor chains for semantic identifiers.

 Intent
 ------
 Walk the standards tree and record each node, assertion, and step parent chain.

 Rationale
 ---------
 Ancestor data supports checks that depend on semantic containment.

 Pseudocode
 ----------
 - set ancestry_index = empty mapping
 - for semantic_node in standards tree:
   - set identifier_ancestry = parent identifiers
 - return ancestry index

 Wraps
 -----
 - none
 """
 out={}
 def walk(xs,parents):
  """Walk semantic children while carrying parent identifiers.

  Intent
  ------
  Record ancestry for nodes, assertions, steps, and descendants.

  Rationale
  ---------
  Passing an immutable parent list keeps sibling ancestry independent.

  Pseudocode
  ----------
  - for node in semantic nodes:
    - set node_ancestry = parent identifiers
    - set descendant_ancestry = parent identifiers plus node identifier
  - return none

  Wraps
  -----
  - none
  """
  for n in xs:
   out[n['id']]=parents
   if n['kind']=='rule':
    for a in n['assertions']:out[f"{n['id']}#{a['id']}"]=parents+[n['id']]
   if n['kind']=='procedure':
    for st in n['steps']:out[f"{n['id']}#{st['id']}"]=parents+[n['id']]
   walk(n.get('children',[]),parents+[n['id']])
 walk(items,[]);return out
def _validate_document(document):
 """Validate standard-v6 semantic references and relationships.

 Intent
 ------
 Check identifiers, artifacts, relationships, evidence, lifecycle state, and local references after schema validation.

 Rationale
 ---------
 Separating semantic checks from schema checks keeps each diagnostic layer explicit and ordered.

 Pseudocode
 ----------
 - set document_maps = normalized optional mappings
 - set semantic_index = indexed standards
 - set findings = semantic relationship and reference checks
 - return findings

 Wraps
 -----
 - none

 CallsFromRepo
 -------------
 .copy_document:
   why:
     computes: "Provides an isolated mutable document before optional mappings are inserted."

 InstantiationsFromRepo
 ----------------------
 ._ancestry:
   why:
     constructs: "Builds the ancestry index currently computed alongside the semantic lookup; later checks do not consume it."
 ._index:
   why:
     constructs: "Builds the semantic lookup and duplicate-identifier findings used by reference checks."
 ._maps:
   why:
     constructs: "Populates optional mappings required by the semantic-check loops."
 """
 d=_maps(copy_document(document)); e=[]; sem=_index(d['standards'],e); ancestry=_ancestry(d['standards'])
 def local(r,label):
  """Validate one local semantic reference.

  Intent
  ------
  Accept declared import aliases or require a matching local kind-and-identifier entry.

  Rationale
  ---------
  All local reference sites must use the same dangling-reference rule.

  Pseudocode
  ----------
  - if reference names imported document:
    - set unknown_import_finding = absent alias
    - return none
  - set local_finding = missing or wrong-kind semantic reference

  Wraps
  -----
  - none
  """
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
 def status(n):
  """Return lifecycle and resolution values with defaults.

  Intent
  ------
  Supply active and resolved defaults when a node omits explicit lifecycle metadata.

  Rationale
  ---------
  Central defaults keep lifecycle comparisons consistent across link checks.

  Pseudocode
  ----------
  - set lifecycle = declared lifecycle or active
  - set resolution = declared resolution or resolved
  - return lifecycle and resolution

  Wraps
  -----
  - none
  """
  return n.get('lifecycle','active'),n.get('resolution',{'state':'resolved'})
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
   if source['kind'] not in {'family','rule','assertion'}:
    e.append(f'links.{lid}: remedied-by source must be a family, rule, or assertion')
   if target['kind']!='procedure':
    e.append(f'links.{lid}: remedied-by target must be a procedure')
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
 """Deep-copy one standard document before normalization.

 Intent
 ------
 Create an independent mutable document for optional-map insertion and semantic checks.

 Rationale
 ---------
 Callers must not observe validation-time normalization mutations.

 Pseudocode
 ----------
 - set copied_document = deep copy of input document
 - return copied document

 Wraps
 -----
 - none
 """
 import copy; return copy.deepcopy(d)
def validate_document(document: dict, root: Path) -> list[str]:
 """Validate one in-memory standard-v6 document.

 Intent
 ------
 Apply schema validation, semantic validation, and import path checks relative to a repository root.

 Rationale
 ---------
 This public boundary preserves stable ordered findings for callers that already parsed a document.

 Pseudocode
 ----------
 - set schema_finding = standard-v6 validation result
 - set semantic_findings = document relationship checks
 - set import_findings = bounded root-relative import checks
 - return findings

 Wraps
 -----
 - none

 CallsFromRepo
 -------------
 ._schema:
   why:
     computes: "Provides the standard-v6 schema passed to jsonschema validation."

 InstantiationsFromRepo
 ----------------------
 ._validate_document:
   why:
     constructs: "Builds the ordered semantic findings appended before import path checks."
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
 """Select bytes for one portable line-range source unit.

 Intent
 ------
 Validate inclusive one-based line bounds and encode the selected lines with a final newline.

 Rationale
 ---------
 Digest verification requires deterministic bytes and rejects unsupported selector kinds.

 Pseudocode
 ----------
 - if selector is not line range:
   - return unsupported selector finding
 - if bounds are invalid:
   - return range finding
 - return selected UTF-8 bytes

 Wraps
 -----
 - none
 """
 if selector['kind']!='line-range': return None,'unsupported source selector for portable verifier'
 a,b=selector['start'],selector['end']
 if a<1 or b<a or b>len(lines): return None,'selector out of range'
 return ('\n'.join(lines[a-1:b])+'\n').encode(),None
def _resolve_artifact_path(artifact, root, label):
 """Resolve one safe repository-relative artifact path.

 Intent
 ------
 Reject absolute, escaping, or missing artifact paths before filesystem access.

 Rationale
 ---------
 All artifact consumers require the same repository containment boundary.

 Pseudocode
 ----------
 - if artifact path is absolute:
   - return absolute-path finding
 - set target = resolved repository path
 - if target escapes root or is missing:
   - return bounded artifact finding
 - return target

 Wraps
 -----
 - none
 """
 raw=Path(artifact['path'])
 if raw.is_absolute():return None,f'{label}: artifact path must be repository-relative'
 target=(root/raw).resolve()
 if not target.is_relative_to(root):return None,f'{label}: artifact path escapes repository root'
 if not target.is_file():return None,f'{label}: missing artifact file'
 return target,None
def _resolve_json_pointer(value,pointer):
 """Resolve one JSON Pointer against a loaded value.

 Intent
 ------
 Decode pointer tokens and traverse mappings or arrays with bounded missing-token errors.

 Rationale
 ---------
 Schema-authority selectors need deterministic pointer resolution without uncaught lookup failures.

 Pseudocode
 ----------
 - if pointer selects whole value:
   - return value
 - for token in decoded pointer tokens:
   - set current_value = mapped or indexed child
 - return resolved value or bounded finding

 Wraps
 -----
 - none
 """
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
def validate_file(path,root=None,cache=None,_stack=None,_schema_validator=None):
 """Validate one standard-v6 file and its imported graph.

 Intent
 ------
 Load a root document, apply schema and semantic checks, verify artifacts and digests, and recurse through imports.

 Rationale
 ---------
 The function preserves ordered contextual findings while sharing only caller-scoped schema preparation.

 Pseudocode
 ----------
 - set schema_validator = supplied prepared validator or newly prepared validator
 - if path is cyclic or cached:
   - return contextual findings
 - set document = parsed standard file
 - set findings = schema semantic artifact source and import checks
 - return findings

 Wraps
 -----
 - none

 CallsFromRepo
 -------------
 .validate_file:
   why:
     computes: "Recursively produces findings for each validated imported document."
 ._validate_with_prepared_schema:
   why:
     computes: "Raises the best matching schema error for root and imported documents."
 ._prepare_schema_validator:
   why:
     computes: "Provides a checked schema validator when the caller did not supply one."
 ._fact_equal:
   why:
     computes: "Compares inherited domain facts without numeric type coercion."

 InstantiationsFromRepo
 ----------------------
 ._index:
   why:
     constructs: "Builds local and imported semantic lookups for external-reference resolution."
 ._maps:
   why:
     constructs: "Populates optional mappings on root and imported documents before traversal."
 ._select:
   why:
     constructs: "Builds deterministic source-unit bytes used for content-digest verification."
 ._resolve_artifact_path:
   why:
     constructs: "Builds bounded repository artifact paths for authorities, sources, reviews, and imports."
 ._resolve_json_pointer:
   why:
     constructs: "Builds selected schema-authority fragments or bounded pointer findings."
 ._validate_document:
   why:
     constructs: "Builds the root document's schema-independent semantic findings."
 """
 path=Path(path).resolve(); root=Path(root).resolve() if root else path.parent.resolve(); cache={} if cache is None else cache; stack=list(_stack or []); schema_validator=_prepare_schema_validator() if _schema_validator is None else _schema_validator
 if path in stack:return ['import cycle: '+' -> '.join(map(str,stack+[path]))]
 if path in cache:return cache[path][1]
 try:d=yaml.safe_load(path.read_text(encoding='utf-8'))
 except Exception as x:return [f'cannot load document: {x}']
 try:_validate_with_prepared_schema(d,schema_validator)
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
  try:_validate_with_prepared_schema(child,schema_validator)
  except jsonschema.ValidationError as exc:errors.append(f'imports.{alias}: schema validation failed at {exc.json_path}: {exc.message}');continue
  child=_maps(child);imported[alias]=child
  for field in ('standard_version','revision'):
   if child[field]!=decl[field]:errors.append(f'imports.{alias}: {field} mismatch')
  if child['id']!=decl['standard_id']:errors.append(f'imports.{alias}: standard_id mismatch')
  for fact,value in child['domain_facts'].items():
   if fact not in d['domain_facts']:
    errors.append(f'imports.{alias}.domain_facts: missing inherited fact {fact}')
   elif not _fact_equal(d['domain_facts'][fact],value):
    errors.append(f'imports.{alias}.domain_facts: conflicting fact {fact}')
  errors.extend(f'imports.{alias}: {x}' for x in validate_file(target,root,cache,stack,schema_validator))
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
