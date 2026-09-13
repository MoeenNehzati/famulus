#!/usr/bin/env python3
from pathlib import Path
import re
from collections import defaultdict
from contextlib import redirect_stdout
from io import StringIO
import argparse,yaml
def walk(xs,d=0):
 for x in xs:yield x,d;yield from walk(x.get('children',[]),d+1)
def idx(d):
 out={}
 for n,_ in walk(d['standards']):
  out[n['id']]=n
  if n['kind']=='rule':
   for a in n['assertions']:out[n['id']+'#'+a['id']]=a
 return out
def title(i):return i.split('#')[-1].split('.')[-1].replace('-',' ').title()
def _emit_default(d):
 print(f"<!-- Generated from {d['canonical_path']}; do not edit. -->")
 print('\n# '+d['title']+'\n\n'+d['purpose'])
 for n,depth in walk(d['standards']):
  heading='#'*min(depth+2,6)
  source_block=d.get('render_mode','semantic')=='source-faithful' and n['kind']!='family'
  if n['kind']=='family':
   print(f"\n{heading} {n['title']}")
   if n.get('summary'):print('\n'+n['summary'])
   if n.get('rationale'):print('\n**Rationale:** '+n['rationale'])
  elif n['kind']=='rule':
   if not source_block: print(f"\n{heading} {n.get('title',title(n['id']))}")
   if n.get('summary'):print('\n'+n['summary'])
   if n.get('rationale'):print('\n**Rationale:** '+n['rationale'])
   for a in n['assertions']:
    print(("\n" if source_block else "")+a['statement'] if source_block else f"- **{a['modality']}** — {a['statement']}")
    if a.get('rationale'):print(f"  - Rationale: {a['rationale']}")
  elif n['kind']=='procedure':
   print(f"\n{heading} {n['title']}")
   if n.get('summary'):print('\n'+n['summary'])
   print('\n**Steps**')
   for number,step in enumerate(n['steps'],1):
    print(f"\n{number}. {step['instruction']}")
    if step.get('rationale'):print(f"   - Rationale: {step['rationale']}")
    if step.get('verification'):print(f"   - Verification: {step['verification']}")
    if step.get('requires'):print(f"   - Requires: {', '.join(step['requires'])}")
   if n.get('invariants'):
    print('\n**Invariants**')
    for invariant in n['invariants']:print(f"- {invariant['statement']}")
   if n.get('completion_conditions'):
    print('\n**Completion conditions**')
    for condition in n['completion_conditions']:print(f"- {condition['statement']}")
   print(f"\n**Risk ({n['risk']['level']}):** {n['risk']['statement']}")
  elif n['kind']=='guidance':
   if source_block:
    if n['statement'].strip()!=f"# {d['title']}":
     print("\n"+n['statement'])
   else: print(f"\n{heading} {n.get('title',title(n['id']))}\n\n{n['statement']}")
  elif n['kind']=='definition':
   print(f"\n{heading} {n.get('term',title(n['id']))}\n\n{n['meaning']}")
def render_document(document: dict) -> str:
 stream=StringIO()
 with redirect_stdout(stream):
  _emit_default(document)
  items=idx(document);forward=defaultdict(list);inverse=defaultdict(list)
  for link in document.get('links',{}).values():
   if link['relation']!='remedied-by':continue
   forward[link['source']['ref']].append(link['target']['ref'])
   inverse[link['target']['ref']].append(link['source']['ref'])
  if forward or inverse:
   print('\n## Remedy relationships')
  for ref,targets in sorted(forward.items()):
   source=items.get(ref,{})
   print(f"- {source.get('title',title(ref))}")
   for target_ref in sorted(targets):
    target=items.get(target_ref,{})
    print(f"  - Remedies: {target.get('title',title(target_ref))}")
  for ref,sources in sorted(inverse.items()):
   target=items.get(ref,{})
   print(f"- {target.get('title',title(ref))}")
   for source_ref in sorted(sources):
    source=items.get(source_ref,{})
    print(f"  - Addresses: {source.get('title',title(source_ref))}")
 return stream.getvalue()
def reverse(d):
 auth=defaultdict(list);checks=defaultdict(list);tests=defaultdict(list);reviews=defaultdict(list)
 for k,x in d.get('schema_authority_links',{}).items():auth[x['semantic_item']['ref']].append((k,x))
 for k,x in d.get('assurances',{}).items():checks[x['assertion']['ref']].append((k,x))
 for k,t in d.get('tests',{}).items():
  for target in t['targets']:tests[target['target']['ref']].append((k,t))
 for k,r in d.get('semantic_reviews',{}).items():
  for c in r['coverage']:reviews[c['target']['ref']].append((k,r,c))
 return auth,checks,tests,reviews
def relevant_tests(ref,auth_links,assurances,tests):
 """Return tests relevant to an assertion, with the route made explicit."""
 out=[];seen=set()
 def add(test_id,label,via=None):
  key=(test_id,label,via)
  if key not in seen:seen.add(key);out.append((test_id,label,via))
 for test_id,_ in tests[ref]:add(test_id,'Direct behavior test')
 for assurance_id,assurance in assurances:
  check_ref=assurance['mechanism']['ref']
  for test_id,_ in tests[check_ref]:add(test_id,'Mechanical check test',f'{assurance_id} -> {check_ref}')
 for link_id,link in auth_links:
  authority_ref=link['authority']['ref']
  for test_id,_ in tests[authority_ref]:add(test_id,'Schema authority test',f'{link_id} -> {authority_ref}')
 return out
def ancestors(d):
 out={}
 def visit(nodes,parents):
  for n in nodes:
   out[n['id']]=parents
   if n['kind']=='rule':
    for a in n['assertions']:out[n['id']+'#'+a['id']]=parents+[n['id']]
   visit(n.get('children',[]),parents+[n['id']])
 visit(d['standards'],[]);return out
def item(d):
 print('# Item coverage view');auth,checks,tests,reviews=reverse(d);parents=ancestors(d)
 for n,_ in walk(d['standards']):
  if n['kind']!='rule':continue
  for a in n['assertions']:
   ref=n['id']+'#'+a['id'];print(f"\n## {title(ref)}\n- Modality: {a['modality']}\n- Statement: {a['statement']}")
   if auth[ref]:
    for _,x in auth[ref]:print(f"- Schema authority: {x['authority']['ref']} ({x['relation']}; {x['strength']})")
   else:print('- Schema authority: none declared')
   if checks[ref]:
    print('- Mechanical checks and declared assurance coverage:')
    for k,x in checks[ref]:
     print(f"  - {x['mechanism']['ref']} via {k}")
     print(f"    - Strength: {x['strength']}")
     print(f"    - Aspects: {', '.join(x['aspects'])}")
     print(f"    - Description: {x['coverage_description']}")
     print(f"    - Limitation: {x.get('limitation','none declared')}")
   else:print('- Mechanical checks and declared assurance coverage: none declared')
   routed_tests=relevant_tests(ref,auth[ref],checks[ref],tests)
   if routed_tests:
    print('- Relevant tests:')
    for test_id,label,via in routed_tests:
     print(f"  - {label}: {test_id}"+(f" (via {via})" if via else ''))
   else:print('- Relevant tests: none declared')
   if not checks[ref]:
    print('- Uncovered mechanical remainder: entire assertion; no declared mechanism')
   else:
    limitations=[x.get('limitation') for _,x in checks[ref] if x.get('limitation')]
    if limitations:print('- Uncovered mechanical remainder: '+'; '.join(limitations))
    else:print('- Uncovered mechanical remainder: not derivable; declared aspects are not a closed coverage set')
   relevant=list(reviews[ref])
   for parent in parents[ref]:
    relevant.extend(x for x in reviews[parent] if x[2].get('scope')=='node-and-descendants')
   print('- Semantic-review remainder: '+(', '.join(f"{k} ({', '.join(r['reviewer_kinds'])}: {', '.join(c['aspects'])})" for k,r,c in relevant) if relevant else 'none declared'))
def diagnostic(d):
 print('# Evidence and conflicts');items=idx(d)
 def describe(ref):
  if ref['kind']=='evidence-claim':return d.get('evidence_claims',{}).get(ref['ref'],{}).get('statement',ref['ref'])
  node=items.get(ref['ref'],{});return node.get('statement') or node.get('meaning') or node.get('summary') or ref['ref']
 for k,l in d.get('links',{}).items():
  if l['source']['kind']!='evidence-claim' and l['target']['kind']!='evidence-claim':continue
  print(f"- {k}: {l['relation']} ({l['resolution']['state']})\n  - Source claim: {describe(l['source'])}\n  - Target claim: {describe(l['target'])}\n  - Note: {l['resolution'].get('note','')}")
def authority(d):
 print('# Schema authority view');links=defaultdict(list)
 for x in d.get('schema_authority_links',{}).values():links[x['authority']['ref']].append(x)
 for k,a in d.get('schema_authorities',{}).items():
  sel=a['selector'];where=sel['kind']+((' '+sel['pointer']) if sel['kind']=='json-pointer' else '')
  print(f"## {k}\n- Schema artifact: {d['artifacts'][a['artifact']['ref']]['path']}\n- Selector: {where}")
  for x in links[k]:print(f"- {x['relation']} {x['semantic_item']['ref']}: {x['coverage_description']}")
def review(d):
 print('# Semantic review view')
 for k,r in d.get('semantic_reviews',{}).items():
  artifact=r['instructions']['artifact']['ref']; path=d['artifacts'].get(artifact,{}).get('path',artifact)
  print(f"## {k}\n- reviewers: {', '.join(r['reviewer_kinds'])}\n- instructions: {path} / {r['instructions']['instruction_id']}\n- lifecycle: {r['lifecycle']}; resolution: {r['resolution']['state']}\n- question: {r['question']}\n- procedure: {r['procedure']}")
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--view',choices=['default','item','diagnostic','schema-authority','semantic-review'],default='default');p.add_argument('file');a=p.parse_args();d=yaml.safe_load(Path(a.file).read_text())
 if a.view=='default':print(render_document(d),end='')
 else:{'item':item,'diagnostic':diagnostic,'schema-authority':authority,'semantic-review':review}[a.view](d)
