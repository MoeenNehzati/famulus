"""One authenticated preparation for the existing certification Voyage."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import hashlib
from importlib.metadata import distributions
import io
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

from officina.blueprints.graph import BlueprintNode
from officina.blueprints.pooled import _render_pooled_review_nodes, pooled_review_node_ids, pooled_review_path
from officina.blueprints.template import load_schema, schema_validator
from officina.certification.hashing import (
    CertificationFacetHashState, NodeHashState,
    certification_target_postorder, expected_certifier_checks,
    resolve_certification_basis_paths,
)
from officina.certification.records import (
    certificate_entry_hash, certificate_public_key_root, load_active_certificate_key_id,
    load_certificate_signing_key, provision_certificate_signing_material,
    parse_certificate_log, sign_certificate_payload, verify_certificate_envelope,
)
from officina.certification.view import (
    CertificateCurrentnessReport, CertificateCurrentnessView,
    _evaluate_node_currentness, _resolve_certificate_dependencies,
    certificate_log_path, certificate_requires_renewal,
)
from officina.common.atomic_files import atomic_replace_bytes, read_regular_file_bytes
from officina.git.provenance import (
    _read_descriptor_safe_regular_file, capture_git_snapshot, git_ignored_paths, run_git,
)

from . import _node_certifier as certifier

DOMAIN = "node-certify.preparation/v2"


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _binding(data):
    return hashlib.sha256(_json({key: value for key, value in data.items() if key != "preparation"}).encode()).hexdigest()


def _generated(path):
    parts = path.parts
    return (
        parts[0] in {".git", ".worktrees", "_build"}
        or "__pycache__" in parts
        or (".certificates" in parts and path.suffix == ".jsonl")
        or (parts[:4] == ("skills", "node-certify", ".certificates", "public-keys")
            and (path.name == "active-key-id" or (path.suffix == ".pub" and len(path.stem) == 64
                and all(char in "0123456789abcdef" for char in path.stem))))
        or (path.name in {"certification.reckoning.json", "certification.reckoning.json.lock"}
            and parts[:4] == ("skills", "node-certify", "_build", "certification-voyages"))
    )


def input_fingerprint(repository):
    """Bind broad validator/discovery inputs, including untracked file membership.

    ponytail: conservatively hash repository inputs instead of maintaining a
    per-validator dependency graph; unrelated edits may require preparation again.
    Certificate logs are authenticated separately. Pooled reviews remain inputs
    and are published only after all issuance has completed.
    """
    index = run_git(repository, "ls-files", "--stage", "-z", check=True).stdout
    tracked = {
        Path(os.fsdecode(record.split(b"\t", 1)[1]))
        for record in index.split(b"\0") if record
    }
    paths = set(tracked)
    directories = []
    for directory, dirs, files in os.walk(repository, followlinks=False):
        parent = Path(directory)
        relative_parent = Path(os.path.relpath(directory, repository))
        dirs[:] = [name for name in dirs if not _generated(relative_parent / name)]
        for name in dirs:
            path = parent / name
            relative = relative_parent / name
            if path.is_symlink():
                paths.add(relative)
            elif ((name not in {"_build", ".certificates"} or relative_parent == Path("skills"))
                    and relative.parts[:4] not in {
                        ("skills", "node-certify", ".certificates", "public-keys"),
                        ("skills", "node-certify", "_build", "certification-voyages"),
                    }):
                directories.append((relative.as_posix(), stat.S_IMODE(path.stat().st_mode)))
        paths.update(relative_parent / name for name in files
                     if not _generated(relative_parent / name))
    digest = hashlib.sha256(index)
    digest.update(_json(sorted(directories)).encode())
    for relative in sorted(paths):
        path = repository / relative
        digest.update(os.fsencode(relative.as_posix()) + b"\0")
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            digest.update(b"missing\0")
            continue
        digest.update(str(stat.S_IMODE(metadata.st_mode)).encode() + b"\0")
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(os.fsencode(os.readlink(path)) + b"\0")
            resolved = path.resolve()
            if resolved.is_dir() or not resolved.exists():
                digest.update(b"directory\0" if resolved.is_dir() else b"missing\0")
                continue
            digest.update(read_regular_file_bytes(resolved, allowed_root=resolved.parent))
        else:
            content, _mode, problem = _read_descriptor_safe_regular_file(repository, relative.as_posix())
            if problem is not None or content is None:
                raise certifier.CertificationError(f"unsafe preparation input: {relative}")
            digest.update(content)
        digest.update(b"\0")
    ignored = [path.as_posix() for path in git_ignored_paths(repository)
               if not _generated(path) and not {"_build", ".certificates"}.intersection(path.parts)]
    runtime = [sys.executable, sys.version, sys.prefix,
               sorted(hashlib.sha256((dist.read_text("METADATA") or "").encode()).hexdigest() for dist in distributions()),
               {key: os.environ.get(key) for key in ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD")}]
    digest.update(_json([ignored, runtime]).encode())
    return digest.hexdigest()


def _scope_record(scope, repository):
    return {
        "tracked_paths": [path.relative_to(repository).as_posix() for path in scope.tracked_paths],
        "local_claims": dict(scope.local_claims), "identity": scope.identity,
    }


def _guard(repository, commit, scope, check=None):
    snapshot = capture_git_snapshot(repository)
    if snapshot is None or snapshot.repo_root != repository or snapshot.commit != commit:
        raise certifier.CertificationError("prepared certification HEAD changed")
    guard = certifier.RepositoryFreezeGuard(repo_root=repository, snapshot=snapshot, allow_non_atomic=False, scoped=True)
    guard.configure_inputs([repository / path for path in scope["tracked_paths"]], scope["local_claims"], scope_check=check)
    guard.configure_generated_outputs(public_key_root=certificate_public_key_root(repository), pooled_review_relatives=set())
    guard.capture_initial_state()
    return snapshot, guard


def _mechanical_checks(repository, states, node_ids, *, graph=None):
    """Run the existing graph group or one node's local group."""
    from officina.repository.checks.runner import run_suite

    validation_paths = certifier.mechanical_validation_paths(states, node_ids)
    # One node has only a few files; spawning eight workers costs more than the
    # local checks. Keep the shared graph group's existing parallel execution.
    jobs = 8 if graph is not None else 1
    group = "graph" if graph is not None else "local"
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        status = run_suite(repository, "validators", jobs=jobs, repository_view="working",
            prepared_graph=graph, graph_checks=graph is not None,
            validation_paths=validation_paths, validation_node_ids=node_ids)
    command = certifier.CommandResult("validators", [sys.executable, "repo_checks.py", "--suite", "validators", "--jobs", str(jobs), "--validator-group", group], status, stdout.getvalue(), stderr.getvalue(),
        validation_paths=validation_paths, validation_node_ids=tuple(node_ids))
    if not command.passed:
        raise certifier.CertificationError(f"mechanical certification checks failed:\n{command.stdout}{command.stderr}")
    return command.as_payload()


def prepare(data, observation, scope, packets, initial_inputs):
    """Seal shared graph checks; local checks wait for their node's DFS turn."""
    repository = Path(data["reviewed_repository"])
    key = provision_certificate_signing_material(repository, secret_backend=None)
    before = initial_inputs
    order = tuple(data["node_order"])
    graph, states = observation.graph, observation.states
    renewal = tuple(node for node in order if certificate_requires_renewal(observation.currentness.nodes[node]))
    mechanical = _mechanical_checks(repository, states, renewal, graph=graph)
    basis = (observation.certification_basis_paths if hasattr(observation, "certification_basis_paths")
             else resolve_certification_basis_paths(repository))
    certifier.RouteSmokeAuditor(graph, states, repo_root=repository,
        certification_basis_paths=basis,
        certification_node_ids=renewal,
        schema_root=repository / "references/blueprint-schema", reuse_graph=True).require_stable_dependencies()
    nodes = {}
    for node_id in order:
        node, state = graph.nodes[node_id], states[node_id]
        if node_id in renewal:
            certifier._run_deterministic_check(certifier._build_gate_evidence(node_id, state,
                source_commit=data["reviewed_commit"], certifier_identity=observation.certifier_identity), graph=graph, states=states)
        payload = certifier._build_certificate_payload(repository, graph, states, node_id,
            source_commit=data["reviewed_commit"], key_id=key.key_id, previous_entry_hash=None,
            certifier_identity=observation.certifier_identity, checks=(), certified_at="")
        for field in ("key_id", "previous_entry_hash", "certified_at", "checks"):
            del payload[field]
        nodes[node_id] = {"payload": payload,
            "module_root": node.module_root.relative_to(repository).as_posix(),
            "declaration": _json(node.declaration)}
    reviews = {}
    for module_id in data["requested_targets"]:
        if graph.nodes[module_id].node_type != "module":
            continue
        selected = pooled_review_node_ids(graph, module_id)
        reviews[module_id] = list(selected)
    payload = {
        "domain": DOMAIN, "key_id": key.key_id, "binding": _binding(data),
        "repository_identity": [repository.stat().st_dev, repository.stat().st_ino],
        "inputs": before, "scope": _scope_record(scope, repository), "nodes": nodes,
        "packets": {target: _json(packet) for target, packet in packets.items()},
        "source_modules": {node: graph.source_modules[node] for node in order if node in graph.source_modules},
        "module_parents": dict(graph.module_parents), "reviews": reviews,
        "renewal_nodes": (data.get("preparation") or {}).get("payload", {}).get("renewal_nodes",
            [node for node in order if certificate_requires_renewal(observation.currentness.nodes[node])]),
        "mechanical": mechanical, "node_mechanical": None,
    }
    _snapshot, guard = _guard(repository, data["reviewed_commit"], payload["scope"])
    guard.require_frozen_inputs("after mechanical preparation")
    if input_fingerprint(repository) != before:
        raise certifier.CertificationError("repository inputs changed during mechanical preparation")
    return sign_certificate_payload(payload, key)


def open_preparation(data, envelope):
    """Verify the sealed projection and refresh live certificate evidence.

    Return None when broad inputs changed so the caller can reconstruct and
    compare the canonical scoped identity before preparing again.
    """
    repository = Path(data["reviewed_repository"])
    public_keys = certificate_public_key_root(repository)
    if not verify_certificate_envelope(envelope, public_keys):
        raise certifier.CertificationError("invalid certification preparation signature")
    payload = envelope["payload"]
    if set(payload) != {"domain", "key_id", "binding", "repository_identity", "inputs", "scope", "nodes", "packets", "source_modules", "module_parents", "reviews", "renewal_nodes", "mechanical", "node_mechanical"}:
        raise certifier.CertificationError("invalid certification preparation shape")
    if (payload["domain"] != DOMAIN or payload["binding"] != _binding(data)
            or payload["repository_identity"] != [repository.stat().st_dev, repository.stat().st_ino]
            or payload["key_id"] != load_active_certificate_key_id(public_keys)):
        raise certifier.CertificationError("certification preparation binding changed")
    _snapshot, guard = _guard(repository, data["reviewed_commit"], payload["scope"])
    if input_fingerprint(repository) != payload["inputs"]:
        return None
    nodes, states = {}, {}
    for node_id, record in payload["nodes"].items():
        claims = record["payload"]
        subject = claims["subject"]
        nodes[node_id] = BlueprintNode(node_id, subject["node_type"], subject["version"],
            repository / record["module_root"], repository / subject["blueprint_path"],
            repository / subject["gateway_path"], json.loads(record["declaration"]))
        states[node_id] = NodeHashState(claims["node_hash"], tuple(claims["input_manifest"]),
            tuple(claims["dependencies"]), claims["certification_basis_hash"],
            tuple(CertificationFacetHashState(f["id"], f["type"], f["local_hash"],
                tuple(f["input_manifest"]), tuple(f["dependencies"])) for f in claims["facets"]))
    templates = {target: json.loads(packet) for target, packet in payload["packets"].items()}
    interfaces = {task["id"]: SimpleNamespace(source_node_id=task["owner_node_id"])
                  for task in data["dag"]["nodes"] if task["owner_node_id"] is not None}
    graph = SimpleNamespace(nodes=nodes, source_interfaces=interfaces,
        source_modules=payload["source_modules"], module_parents=payload["module_parents"],
        module_children={node: tuple(child for child, parent in payload["module_parents"].items()
            if parent == node and child in nodes) for node in nodes})
    identity = next(iter(payload["nodes"].values()))["payload"]["certifier"]
    validator = schema_validator(load_schema(repository / "references/blueprint-schema/certificate.schema.json"))
    local = {}
    for node_id, node in nodes.items():
        local[node_id] = _evaluate_node_currentness(node, states[node_id], repo_root=repository,
            public_key_root=public_keys, certifier_identity=identity,
            expected_checks=expected_certifier_checks(), tracked_inputs_clean=True, validator=validator)
    report = CertificateCurrentnessReport(_resolve_certificate_dependencies(local, states))
    return SimpleNamespace(graph=graph, states=states, source_commit=data["reviewed_commit"],
        certifier_identity=identity, currentness=report, prepared_packets=templates,
        preparation=envelope, freeze_guard=guard)


def ensure_node_checks(data, observation, node_id):
    """Retain one authenticated local pass before auditing the active DFS node."""
    payload = observation.preparation["payload"]
    if observation.currentness.nodes[node_id].current:
        return
    if node_id not in payload["mechanical"]["validation_node_ids"]:
        raise certifier.CertificationError("node is outside prepared graph checks")
    receipt = payload["node_mechanical"]
    if receipt is not None and receipt["validation_node_ids"] == [node_id]:
        return
    repository = Path(data["reviewed_repository"])
    receipt = _mechanical_checks(repository, observation.states, (node_id,))
    observation.freeze_guard.require_frozen_inputs("after node mechanical checks")
    if input_fingerprint(repository) != payload["inputs"]:
        raise certifier.CertificationError("repository inputs changed during node mechanical checks")
    key = load_certificate_signing_key(certificate_public_key_root(repository), secret_backend=None)
    if key.key_id != payload["key_id"]:
        raise certifier.CertificationError("prepared signing key changed")
    observation.preparation = sign_certificate_payload({**payload, "node_mechanical": receipt}, key)
    observation.preparation_changed = True


def issue(data, observation, node_id):
    """Use the existing guarded append writer with authenticated static claims."""
    repository = Path(data["reviewed_repository"])
    payload = observation.preparation["payload"]
    if observation.currentness.nodes[node_id].current:
        return False
    for dependency in observation.states[node_id].dependency_hashes:
        if dependency["relation"] != "certified-under" and not observation.currentness.nodes[dependency["target"]].current:
            raise certifier.CertificationError("prepared signing requires current prerequisites")
    if "invalid-certificate-schema" in observation.currentness.nodes[node_id].concerns:
        raise certifier.CertificationError("invalid certificate history schema")
    key = load_certificate_signing_key(certificate_public_key_root(repository), secret_backend=None)
    if key.key_id != payload["key_id"]:
        raise certifier.CertificationError("prepared signing key changed")
    receipt = payload["node_mechanical"]
    if (node_id not in payload["mechanical"]["validation_node_ids"]
            or receipt is None or receipt["validation_node_ids"] != [node_id]
            or not receipt["passed"] or not payload["mechanical"]["passed"]):
        raise certifier.CertificationError("prepared signing requires this node's mechanical checks")
    prerequisites = {}
    for dependency in certification_target_postorder(observation.graph, observation.states, (node_id,)):
        if dependency == node_id:
            continue
        status = observation.currentness.nodes[dependency]
        if not status.current or status.certificate is None:
            raise certifier.CertificationError("prepared signing requires current prerequisites")
        prerequisites[dependency] = certificate_entry_hash(status.certificate)

    def unchanged():
        if payload["key_id"] != load_active_certificate_key_id(certificate_public_key_root(repository)):
            raise certifier.CertificationError("active signing key changed")
        if input_fingerprint(repository) != payload["inputs"]:
            raise certifier.CertificationError("prepared certification inputs changed during append")
        for dependency, identity in prerequisites.items():
            node = observation.graph.nodes[dependency]
            entries = parse_certificate_log(read_regular_file_bytes(certificate_log_path(node), allowed_root=node.module_root),
                certificate_public_key_root(repository))
            if certificate_entry_hash(entries[-1]) != identity:
                raise certifier.CertificationError("prerequisite certificate changed during append")

    snapshot, guard = _guard(repository, data["reviewed_commit"], payload["scope"], unchanged)
    certifier.CertificateBatchIssuer(repo_root=repository, graph=observation.graph, states=observation.states,
        node_order=(node_id,), snapshot=snapshot, public_key_root=certificate_public_key_root(repository),
        signing_key=key, certifier_identity=observation.certifier_identity, reviewed_commit=data["reviewed_commit"],
        certified_at=datetime.now().astimezone().isoformat(timespec="seconds"), allow_non_atomic=False,
        freeze_guard=guard, before_append=None, after_append=None,
        prepared_payloads={node_id: payload["nodes"][node_id]["payload"]}).issue_all()
    # The writer just checked the full scope and prerequisite identities after
    # append. Authenticate the new node with the same canonical verifier.
    fresh = _evaluate_node_currentness(observation.graph.nodes[node_id], observation.states[node_id],
        repo_root=repository, public_key_root=certificate_public_key_root(repository),
        certifier_identity=observation.certifier_identity, expected_checks=expected_certifier_checks(),
        tracked_inputs_clean=True,
        validator=schema_validator(load_schema(repository / "references/blueprint-schema/certificate.schema.json")))
    if not fresh.current:
        raise certifier.CertificationError("prepared post-write certificate verification failed")
    return True


def publish_reviews(data, observation, issued):
    """Render only after all certificates are current, using fresh identities."""
    repository = Path(data["reviewed_repository"])
    payload = observation.preparation["payload"]
    for module_id, selected in payload["reviews"].items():
        if not set(selected).intersection(set(issued) | set(payload["renewal_nodes"])):
            continue
        descriptors = []
        for node_id in selected:
            node = observation.graph.nodes[node_id]
            descriptors.append({"id": node_id, "node_type": node.node_type, "version": node.version,
                "blueprint_path": node.blueprint_path.relative_to(node.module_root).as_posix(),
                "gateway_path": node.gateway_path.relative_to(node.module_root).as_posix(), "declaration": node.declaration})
        module = observation.graph.nodes[module_id]
        text = _render_pooled_review_nodes(module_id, module.blueprint_path.relative_to(module.module_root).as_posix(),
            descriptors, CertificateCurrentnessView(observation.currentness))
        path = pooled_review_path(module.module_root)
        atomic_replace_bytes(path, text.encode(), allowed_root=module.module_root, mode=0o600)
        if read_regular_file_bytes(path, allowed_root=module.module_root) != text.encode():
            raise certifier.CertificationError("post-write pooled review changed")
