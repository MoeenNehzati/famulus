# Certification and Drift

This document proposes a graph-based model for describing, certifying, and checking the status of framework nodes.

## Nodes

A node is a logical unit of behavior. It consists of one or more content files, exactly one of which is its gateway, and it may depend on other nodes.

A node's content files are the files that together express its behavior. Its gateway is the single content file through which the framework discovers, interprets, or invokes the node.

There are four types of nodes: skills, LLM interfaces, machine interfaces, and behavior sources.

An LLM interface describes behavior that an LLM can perform. Its gateway is an instruction file, usually written in Markdown.

A skill is modeled separately because assistant hosts discover skills through `SKILL.md`. Every skill includes a default LLM interface whose gateway is that same `SKILL.md` file.

A machine interface is a machine-runnable module. Within this framework, it is invoked through the dispatcher (software shipped and installed as part of the framework) using its interface ID. Static checks prevent direct invocation where such invocation can be detected.

A behavior source is a collection of files that shapes another node's behavior but is not itself an interface. Examples include schemas, policies, configurations, user preferences, templates, and reference material. One of its content files serves as its gateway.

## Blueprints and the graph

Each node has a blueprint file: a structured, local description of that node in the framework's graph. It records the node's type, gateway, content, I/O, and other relevant properties, together with its outgoing dependency edges. The complete graph is obtained by combining these local blueprint files.

Certification operates on the dependency graph formed by these outgoing edges. In the remainder of this document, `graph` means this certification graph.

We write `x → y` when `x` depends on `y`. A dependency-first traversal therefore visits `y` before `x`.

When we speak of a node in isolation, we mean its content files and blueprint file, not its certificate or the nodes it depends on. The node hash of a node `x`, written `node_hash(x)`, is a cryptographic hash of these files. It does not include `x`'s certificate or any files belonging to its dependencies.

Every field in the blueprint contributes to the node hash. This deliberately makes changes to descriptions, I/O, permissions, ownership declarations, and other node properties visible to certification checks.

A node is committed when its content files and blueprint file are tracked by Git and match the repository's current commit. `is_committed(x)` returns whether node `x` satisfies this condition.

When the blueprint files accurately describe their nodes, the system's behavior and structure can be studied through the resulting graph. The graph documents a large LLM-assisted system composed of both code and text. It also lets the framework impose architectural restrictions on the graph. For example, the certification graph must be acyclic, and validators can detect cycles and forbidden edge patterns.

Blueprint files can be validated mechanically only in part. Schemas and static checks can verify paths and identifiers and flag some undeclared dependencies, such as references to files contained in another node. They cannot generally determine whether a blueprint file fully and accurately captures the behavior of its node. That determination requires LLM judgment and, in some cases, human review.

An LLM can review each node and determine whether its blueprint file accurately describes it. Repeating this review across the entire graph after every change would be expensive. A change to one node may also affect every node that depends on it. The framework therefore records successful reviews in certificate files, allowing unchanged results to be reused.

## Certification

Each node may have a current certificate file and an append-only certificate history. The current certificate is written after successful certification and is the only certificate used to determine the node's certification status. Earlier history entries are retained as evidence; they are not alternative candidates for `is_certified(x)`.

Each certificate consists of a canonical signed payload and a signature. The payload identifies the node and certifier and records `node_hash(x)` for the node `x`, `node_hash(d)` for each direct dependency `d`, the source commit containing the node's content and blueprint, `node_hash(skill-certifier)`, the source commit containing the certifier, the signing-key identity, and the hash of the preceding history entry. It may also include informational metadata such as `certified_at`. Every payload field is covered by the signature, so none of these values can be replaced without invalidating the certificate.

After signing a certificate, the certifier appends the complete entry—payload and signature—to the node's history. In the first entry, `previous_entry_hash` is null. In every later entry, it equals a cryptographic hash of the canonical encoding of the complete preceding entry, including that entry's payload and signature. Because the new signature covers `previous_entry_hash`, each entry recursively commits to all preceding signed payloads and signatures.

The current certificate and its history are certificate artifacts, not node content, and neither contributes to `node_hash(x)`. Appending a history entry therefore does not change the node hash or any dependent node hash. The history chain makes modification, removal from the middle, and reordering detectable within the retained history. Without an external anchor, the chain alone cannot prove that its latest entries were not removed; the separate current certificate remains authoritative for current status.

A node `x` is certified if its certificate is validly signed, its recorded node hash equals the current `node_hash(x)`, every direct dependency `d` is certified, and the recorded hash for `d` equals the current `node_hash(d)`. Otherwise, `x` is suspect.

A suspect node retains its certificate. Its certification can be recovered without running `certify` again if the conditions above become true again—for example, if a suspect dependency becomes certified with the same node hash previously recorded by `x`.

A blueprint audit is the semantic comparison between a node and its proposed blueprint. Certification is the larger operation that performs this audit, certifies dependencies, and signs the resulting certificate.

The `skill-certifier` skill produces accurate blueprint files and issues certificates through its `certify` operation. It recursively certifies dependencies, repairs the selected node's blueprint file when necessary, and writes and signs the node's certificate.

The `skill-drift` skill applies the read-only certification assessment. It compares the current graph with its certificates and reports each node as certified or suspect. It does not repair blueprint files or write certificates.

Certification and status reporting have a cooperative same-user authority
contract. The existing audit writer, renamed to `skill-certifier`, is the one
supported owner of blueprint repair, signing, and certificate writes.
`skill-drift` remains read-only and uses only the public verification key
through its supported code path. No broker, service identity, second writer,
or parallel signing path is introduced.

Public-key signatures preserve a verification-only API for `skill-drift` and
make drift, corruption, and changes outside the cooperative writer contract
detectable. The certifier reconstructs node and dependency hashes and performs
its checks internally before signing; it does not accept an LLM-supplied
certificate payload as already validated. User-only permissions, append-only
history, atomic no-follow writes, and post-write verification are retained as
defense-in-depth.

This is not a filesystem-enforced security boundary between same-UID
processes. A malicious process running as the same OS user may be able to
access signing material or certificate outputs and can therefore defeat the
cooperative contract. Signatures and currentness do not defend against that
attacker.

Before signing, `skill-certifier` constructs the complete canonical payload. The signature field itself is not part of that payload:

```text
write_signed_certificate(payload):
    assert is_committed(payload.node)
    assert (
        current_commit(payload.node.repository)
        == payload.source_commit
    )
    assert is_committed(skill-certifier)

    payload.certifier_node_hash = node_hash(skill-certifier)
    payload.certifier_source_commit = current_commit(
        skill-certifier.repository
    )
    payload.key_id = signing_key_id(private_key)

    previous_entry = last_complete_history_entry(payload.node)
    payload.previous_entry_hash = (
        null
        if previous_entry is missing
        else hash(canonical_encode(previous_entry))
    )

    signature = sign(private_key, canonical_encode(payload))
    entry = {
        "payload": payload,
        "signature": signature
    }

    append_and_sync(payload.node.history, entry)
    atomic_write(payload.node.certificate, entry)
```

If history append succeeds but writing the current certificate fails, the appended entry remains historical evidence but does not become authoritative. A retry may append a new entry linked to it. The certifier verifies both writes before reporting certification success.

Signature validation checks both the cryptographic signature and agreement with the current certifier node hash:

```text
valid_signature(certificate):
    signature_matches = verify(
        public_key,
        canonical_encode(certificate.payload),
        certificate.signature
    )

    certifier_matches = (
        certificate.certifier_node_hash
        == node_hash(skill-certifier)
    )

    return signature_matches and certifier_matches
```

Changing the certifier's content or blueprint therefore makes every previously issued certificate suspect. This does not create a hash cycle because `node_hash(skill-certifier)` excludes its certificate.

The certifier node hash is the complete certification basis in this model. It does not include the states of nodes on which `skill-certifier` depends.

## Algorithms

The algorithms below assume that the blueprint graph is acyclic. The implementation detects and reports cycles before applying them.

This gives rise to a postorder DFS algorithm for assessing whether a node is certified:

```text
is_certified(x):
    dependencies_certified = all(
        [is_certified(d) for d in x.blueprint.dependencies]
    )

    dependency_sets_match = (
        set(x.certificate.dependency_hashes)
        == set(x.blueprint.dependencies)
    )

    dependency_hashes_match = all(
        x.certificate.dependency_hashes[d] == node_hash(d)
        for d in x.blueprint.dependencies
    )

    certificate_signed = valid_signature(x.certificate)
    node_hash_matches = x.certificate.node_hash == node_hash(x)

    return (
        dependencies_certified
        and dependency_sets_match
        and dependency_hashes_match
        and certificate_signed
        and node_hash_matches
    )
```

By convention, a missing certificate, field, signature, or recorded dependency hash makes the corresponding expression false. The optional lookup `x.certificate?.node_hash` likewise produces a missing value when no previous certificate exists, and that value differs from every current node hash. The implementation also memoizes completed nodes and detects cycles, but the core algorithm remains the same.

Certification uses two blueprint-production passes. The first pass proposes a blueprint only to discover the dependencies that must be certified; it does not write that blueprint. After those dependencies are certified, the second pass produces the authoritative blueprint that may be approved and written. Certification proceeds only if both passes identify the same direct dependencies.

When `human_feedback=True`, the user approves each final blueprint produced during the recursive certification run, including blueprints for suspect dependencies.

When `human_feedback=False`, blueprint repairs are written automatically.

```text
certify(
    x,
    human_feedback=False,
    repair_dependents=False
):
    assert (
        x == skill-certifier
        or is_certified(skill-certifier)
    ), """
    Certification stopped because skill-certifier is suspect.
    Certify skill-certifier explicitly before certifying other nodes.
    """

    if is_certified(x):
        return

    previous_node_hash = x.certificate?.node_hash
    discovered_blueprint = produce_blueprint(x)

    for d in discovered_blueprint.dependencies:
        if not is_certified(d):
            certify(d, human_feedback=human_feedback)

    final_blueprint = produce_blueprint(x)

    assert (
        set(final_blueprint.dependencies)
        == set(discovered_blueprint.dependencies)
    ), """
    Certification stopped: x remains suspect because its dependencies
    differed between blueprint passes. Clarify x's content so its direct
    dependencies are unambiguous, then run certify(x) again.
    """

    if human_feedback:
        get_user_approval(final_blueprint)

    write(x.blueprint, final_blueprint)

    assert is_committed(x), """
    Certification stopped because x is not committed.
    Commit x's content and blueprint, then run certify(x) again.
    """

    source_commit = current_commit(x.repository)
    current_node_hash = node_hash(x)

    write_signed_certificate(
        node=x,
        node_hash=current_node_hash,
        dependency_hashes={
            d: node_hash(d)
            for d in final_blueprint.dependencies
        },
        source_commit=source_commit,
        certified_at=now()
    )

    if (
        repair_dependents
        and current_node_hash != previous_node_hash
    ):
        for y in direct_dependents(x):
            if not is_certified(y):
                certify(
                    y,
                    human_feedback=human_feedback,
                    repair_dependents=True
                )
```

By default, `certify(x)` certifies `x` and any suspect dependencies required by it. It does not modify nodes that depend on `x`. If certification changes `node_hash(x)`, those dependents may become suspect because their certificates record the previous hash.

If certifying `x` changes `node_hash(x)`, calling `certify(x, repair_dependents=True)` also certifies direct dependents made suspect by that change. This repair propagates further only when certifying a dependent changes that dependent's own node hash. If only its certificate changes, propagation stops. It does not repair other consumers of dependencies changed recursively while certifying `x`; `certify_all(G)` handles those graph-wide effects. This prevents certificate metadata changes from causing unnecessary audits throughout the graph.

In the graph-level algorithms, `G` may be any subgraph constructed from the repository's blueprint files. The certification status of every node in `G` can be reported in dependency-first order:

```text
certification_statuses(G):
    return {
        x: (
            "certified"
            if is_certified(x)
            else "suspect"
        )
        for x in dependency_first_order(G)
    }
```

The entire graph can then be certified in the same order:

```text
certify_all(G, human_feedback=False):
    failures = {}

    for x in dependency_first_order(G):
        try:
            certify(x, human_feedback=human_feedback)
        except CertificationError as error:
            failures[x] = error

    G = read_blueprint_graph()
    statuses = certification_statuses(G)

    return {
        "statuses": statuses,
        "failures": failures
    }
```

`certify_all(G)` continues after a node fails so that independent nodes can still be certified. Its result reports both the final status of each node and the failures that prevented certification.

## Differences from the current design

This document proposes a simpler certification model and does not yet describe the implemented system. The main proposed changes are:

| Current design | Proposed design | Consequence |
| --- | --- | --- |
| `skill-audit` | `skill-certifier` | Certification may repair a blueprint before issuing its certificate; blueprint audit names only the semantic comparison. |
| `skill-drift.machine.drift-status` and audit-current/audit-stale | `is_certified(x)`, `certification_statuses(G)`, and certified/suspect | Status describes whether retained certification currently applies and may recover without rewriting the certificate. |
| Health records | Current certificate files plus append-only signed certificate histories | Current status remains node-local while every issued signature is retained as evidence. |
| Recursive certified-health hashes and timestamp-based currentness rules | Local `node_hash(x)` plus exact hashes of direct dependencies | Transitive dependency state is not folded into a node's local identity; `certified_at` is informational only. |
| Certification reads the existing authored blueprint | Two-pass certification may repair the blueprint | The first pass discovers dependencies; the second produces the authoritative blueprint after dependencies are certified. |
| Certification may inspect dirty local inputs but cannot stamp them | `is_committed(x)` gates certificate writing and the certificate records `source_commit` | Every certified node can be recovered from the commit named by its certificate; unrelated dirty files do not block certification. |
| Target-only certification | Optional `repair_dependents` and graph-wide `certify_all(G)` | Callers choose whether to repair only the target closure, effects on direct dependents, or the entire graph. |
| Shared-key authentication | Public-key signatures bound to `node_hash(skill-certifier)`, the certifier source commit, and the preceding history entry | `skill-certifier` is the sole cooperative writer and `skill-drift` uses the public key through its supported path. Signatures detect drift, corruption, and out-of-contract changes, but do not protect signing material or outputs from a malicious same-UID process. |
| Tests and validators participate in certification health gates | Routine tests remain in ordinary validation workflows | The core certification-status algorithm focuses on blueprint accuracy and direct dependency agreement. |

These changes require corresponding updates to skill names, interface IDs, blueprint dependencies, schemas, certificate formats, permission declarations, policy hashes, documentation, tests, and installed-skill migration behavior before they can replace the current implementation.
