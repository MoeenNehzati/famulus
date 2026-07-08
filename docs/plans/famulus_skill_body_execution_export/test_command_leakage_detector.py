#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import re
import textwrap
from collections import Counter, defaultdict
from pathlib import Path

from skill_body_execution_detector_v6 import detect_command_leakage

# Real public GitHub README/RST excerpts fetched through the GitHub connector.
# The harness is offline/reproducible: it embeds the excerpts so CI does not
# require internet access. Source URLs are documented in final report.
POSITIVE_SAMPLES = {
    "requests_install": """
Requests is available on PyPI:

```console
$ python -m pip install requests
```
""",
    "requests_clone_config": """
When cloning the Requests repository, you may need to add the `-c
fetch.fsck.badTimezone=ignore` flag to avoid an error:

```shell
git clone -c fetch.fsck.badTimezone=ignore https://github.com/psf/requests.git
```

You can also apply this setting to your global Git config:

```shell
git config --global fetch.fsck.badTimezone ignore
```
""",
    "flask_run": """
A Simple Example

```python
# save this as app.py
from flask import Flask
app = Flask(__name__)
```

```
$ flask run
  * Running on http://127.0.0.1:5000/ (Press CTRL+C to quit)
```
""",
    "pytest_execute": """
The ``pytest`` framework makes it easy to write small tests.

To execute it::

    $ pytest
    ============================= test session starts =============================
    collected 1 items
""",
    "node_verify_curl_gpgv": """
You can get a trusted keyring from nodejs/release-keys, e.g. using `curl`:

```bash
curl -fsLo "/path/to/nodejs-keyring.kbx" "https://github.com/nodejs/release-keys/raw/HEAD/gpg/pubring.kbx"
```

Then, you can verify the files you've downloaded locally:

```bash
curl -fsO "https://nodejs.org/dist/${VERSION}/SHASUMS256.txt.asc" \
&& gpgv --keyring="/path/to/nodejs-keyring.kbx" --output SHASUMS256.txt < SHASUMS256.txt.asc \
&& shasum --check SHASUMS256.txt --ignore-missing
```
""",
    "numpy_test_python_c": """
Testing:

NumPy requires `pytest` and `hypothesis`. Tests can then be run after installation with:

    python -c "import numpy, sys; sys.exit(numpy.test() is False)"
""",
    "pandas_install_pip_conda": """
Where to get it

```sh
# conda
conda install -c conda-forge pandas
```

```sh
# or PyPI
pip install pandas
```
""",
    "pandas_source_dev_install": """
Cython can be installed from PyPI:

```sh
pip install cython
```

In the `pandas` directory, execute:

```sh
pip install .
```

or for installing in development mode:

```sh
python -m pip install -ve . --no-build-isolation --config-settings editable-verbose=true
```
""",
    "sklearn_install_clone_test": """
If you already have a working installation of NumPy and SciPy,
the easiest way to install scikit-learn is using ``pip``::

    pip install -U scikit-learn

or ``conda``::

    conda install -c conda-forge scikit-learn

You can check the latest sources with the command::

    git clone https://github.com/scikit-learn/scikit-learn.git

After installation, you can launch the test suite:

    pytest sklearn
""",
    "homebrew_help_and_audit": """
First, please run `brew update` and run (and **read**) `brew doctor`.

A good starting point for contributing is:

- `brew tap --force homebrew/core` or `brew tap --force homebrew/cask`
- perform a strict audit on a package you use e.g. `brew audit --strict ffmpeg` for FFmpeg
- if no warnings, run `brew audit --strict` to run on all packages and pick one to fix
""",
    "kubernetes_build": """
If you want to build Kubernetes right away there are two options:

##### You have a working Go environment.

```
git clone https://github.com/kubernetes/kubernetes
cd kubernetes
make
```

##### You have a working Docker environment.

```
git clone https://github.com/kubernetes/kubernetes
cd kubernetes
make quick-release
```
""",
    "docker_compose": """
Once you have a Compose file, you can create and start your application with a
single command: `docker compose up`.

(might require making the downloaded file executable with `chmod +x`)

Lastly, run `docker compose up` and Compose will start and run your entire app.
""",
    "skill_specific_leakage": """
When certifying the skill, run scripts/check_skill.py with the target skill.
Then execute python3 skills/skill-health/scripts/health.py status --json.
If needed, run the following commanx chmod x before using the script.
""",
}

NEGATIVE_SAMPLES = {
    "django_prose": """
Django is a high-level Python web framework that encourages rapid development
and clean, pragmatic design.

All documentation is in the ``docs`` directory and online at
https://docs.djangoproject.com/en/stable/.

To run Django's test suite:

* Follow the instructions in the "Unit tests" section of
  ``docs/internals/contributing/writing-code/unit-tests.txt``.
""",
    "pytest_prose": """
The ``pytest`` framework makes it easy to write small tests, yet scales to
support complex functional testing for applications and libraries.

Python 3.10+ or PyPy3 are supported.

Please use the GitHub issue tracker to submit bugs or request features.
""",
    "sklearn_dependencies": """
scikit-learn requires:

- Python (>= 3.11)
- NumPy (>= 1.24.1)
- SciPy (>= 1.10.0)
- PytestMinVersion replace:: 7.1.2

For running examples, Matplotlib is required.
""",
    "pandas_prose": """
pandas is a Python package that provides fast, flexible, and expressive data structures.
Robust I/O tools support CSV, Excel files, databases, and HDF5.
Most development discussions take place on GitHub in this repo.
""",
    "rust_prose": """
Rust has advanced tooling including package manager and build tool Cargo,
auto-formatter rustfmt, linter Clippy and editor support rust-analyzer.
Read Installation from The Book.
""",
    "docker_prose": """
Docker Compose is a tool for running multi-container applications on Docker.
A Compose file is used to define how one or more containers are configured.
The Python version of Compose is available under the v1 branch.
""",
    "skill_allowed_interfaces": """
Use the doctor-check interface and use its JSON verdict for certification.
Only certify if the verdict says tests, validators, blueprint sync, dependency review,
and guideline review passed.
The health-status interface reports the current state.
""",
    "skill_outcome_language": """
The check interface must report that repository-state checks passed.
The certification report should include whether tests passed.
Implementation details belong in scripts and generated interface blocks.
""",
    "conceptual_tool_names": """
pytest is used for testing.
Git state matters for the repository.
Python scripts should be portable as implementation artifacts.
global Git config is a setting, not an instruction here.
""",
}


def dedent(s: str) -> str:
    return textwrap.dedent(s).strip() + "\n"


def remove_fences(md: str) -> str:
    return "\n".join(line for line in md.splitlines() if not re.match(r"^\s*```", line))


def strip_prompts(md: str) -> str:
    text = remove_fences(md)
    out = []
    for line in text.splitlines():
        out.append(re.sub(r"^(\s*)[$>]\s+", r"\1", line))
    return "\n".join(out)


def join_colon_to_next(md: str) -> str:
    lines = remove_fences(md).splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.rstrip().endswith(":") or line.rstrip().endswith("::"):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                out.append(line.rstrip(":") + ": " + lines[j].lstrip())
                i = j + 1
                continue
        if line.rstrip().endswith("\\") and i + 1 < len(lines):
            out.append(line.rstrip(" \\") + " " + lines[i + 1].lstrip())
            i += 2
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def paragraph_smear(md: str) -> str:
    paragraphs = re.split(r"\n\s*\n", remove_fences(md).strip())
    return "\n\n".join(" ".join(line.strip() for line in p.splitlines() if line.strip()) for p in paragraphs)


def all_one_line(md: str) -> str:
    return " ".join(line.strip() for line in remove_fences(md).splitlines() if line.strip())


def bulletize_commands(md: str) -> str:
    lines = strip_prompts(md).splitlines()
    out = []
    for line in lines:
        if looks_commandish(line):
            out.append("- " + line.strip())
        else:
            out.append(line)
    return "\n".join(out)


def blockquote_commands(md: str) -> str:
    lines = strip_prompts(md).splitlines()
    out = []
    for line in lines:
        if looks_commandish(line):
            out.append("> " + line.strip())
        else:
            out.append(line)
    return "\n".join(out)


def command_only(md: str) -> str:
    commands = extract_commandish_lines(md)
    return "\n".join(commands) if commands else strip_prompts(md)


def embedded_no_prompt(md: str) -> str:
    commands = extract_commandish_lines(md)
    if not commands:
        return paragraph_smear(md)
    return "\n".join(f"Before certification {cmd} and then continue." for cmd in commands)


def inline_commands(md: str) -> str:
    commands = extract_commandish_lines(md)
    if not commands:
        return paragraph_smear(md)
    return "\n".join(f"The instruction is `{cmd}` in the prose." for cmd in commands)


def typoed_context(md: str) -> str:
    commands = extract_commandish_lines(md)
    if not commands:
        return paragraph_smear(md)
    return "\n".join(f"Run the following commanx {cmd}" for cmd in commands)


def remove_line_breaks_near_commands(md: str) -> str:
    text = strip_prompts(md)
    text = re.sub(r"\n\s*\n", "\n", text)
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and (looks_commandish(lines[i]) or looks_commandish(lines[i + 1])):
            out.append(lines[i].strip() + " " + lines[i + 1].strip())
            i += 2
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def whitespace_noise(md: str) -> str:
    rng = random.Random(20260708)
    lines = strip_prompts(md).splitlines()
    out = []
    for line in lines:
        s = line.strip()
        if looks_commandish(s):
            s = re.sub(r"\s+", lambda _: " " * rng.randint(1, 5), s)
            s = " " * rng.randint(0, 4) + s
        out.append(s)
    return "\n".join(out)


MUTATORS = {
    "original": lambda x: x,
    "defenced": remove_fences,
    "strip_prompts": strip_prompts,
    "join_colon_to_next": join_colon_to_next,
    "paragraph_smear": paragraph_smear,
    "all_one_line": all_one_line,
    "bulletize_commands": bulletize_commands,
    "blockquote_commands": blockquote_commands,
    "command_only": command_only,
    "embedded_no_prompt": embedded_no_prompt,
    "inline_commands": inline_commands,
    "typoed_context": typoed_context,
    "remove_line_breaks_near_commands": remove_line_breaks_near_commands,
    "whitespace_noise": whitespace_noise,
}


COMMAND_START_RE = re.compile(
    r"^\s*(?:[$>]\s*)?(?:python3?|bash|sh|zsh|pytest|git|uvx?|node|npm|npx|pip|pipx|conda|make|cargo|go|ruby|chmod|curl|wget|gpgv|shasum|brew|docker|docker-compose|kubectl|cd)\b",
    re.I,
)
INLINE_COMMAND_RE = re.compile(r"`([^`]+)`")


def looks_commandish(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    # Avoid conceptual prose and dependency bullets.
    if re.match(r"^(?:Python|pytest|node|npm|pip)\s*\(?\s*(?:>=|<=|==|>|<|~=)", s):
        return False
    if re.match(r"^(?:Git|git)\s+(?:state|repository|config|history)\b", s):
        return False
    if re.match(r"^Python\s+(?:scripts?|packages?|modules?|versions?)\b", s):
        return False
    if re.match(r"^Docker\s+Compose\s+(?:is|was|supports|uses)\b", s):
        return False
    if re.search(r"\b(?:is|are|was|were|requires|supports|framework|package|module|tool)\b", s, re.I) and not re.search(r"\b(?:install|clone|config --|run `|run \$|doctor|audit|\-m|\-c|\+x)\b", s, re.I):
        return False
    if COMMAND_START_RE.search(s):
        return True
    if re.search(r"\b(?:python3?|pytest|git|pip|conda|brew|docker|curl|chmod)\b\s+(?:--?|\./|/|https?://|[A-Za-z0-9_.-]+\.(?:py|sh|txt|asc|kbx)|install|clone|config|run|doctor|audit|compose|\+x)", s, re.I):
        return True
    return False


def extract_commandish_lines(md: str) -> list[str]:
    text = remove_fences(md)
    found: list[str] = []
    for line in text.splitlines():
        s = re.sub(r"^\s*[$>]\s+", "", line.strip())
        s = re.sub(r"^\s*[-*+]\s+", "", s)
        if looks_commandish(s):
            # Remove obvious output lines that sometimes follow prompts.
            if s.startswith("*") or s.startswith("="):
                continue
            found.append(s.rstrip("\\").strip())
    # Inline code commands are common in READMEs, but ignore bare conceptual
    # tool names like `pytest`.
    for m in INLINE_COMMAND_RE.finditer(md):
        inner = m.group(1).strip()
        if len(inner.split()) > 1 and looks_commandish(inner):
            found.append(inner)
    # Deduplicate in order.
    out = []
    seen = set()
    for cmd in found:
        if cmd and cmd not in seen:
            out.append(cmd)
            seen.add(cmd)
    return out


def run() -> dict:
    rows = []
    corpus = {**POSITIVE_SAMPLES, **NEGATIVE_SAMPLES}
    for name, text in corpus.items():
        expected_positive = name in POSITIVE_SAMPLES
        for mut_name, mut in MUTATORS.items():
            mutated = mut(dedent(text))
            findings = detect_command_leakage(mutated, strip_generated=True)
            rows.append({
                "sample": name,
                "variant": mut_name,
                "expected_positive": expected_positive,
                "finding_count": len(findings),
                "rules": dict(sorted(Counter(f.rule for f in findings).items())),
                "first_matches": [f"L{f.line}:{f.rule}:{f.match}" for f in findings[:8]],
                "mutated_excerpt": mutated[:500],
            })

    positives = [r for r in rows if r["expected_positive"]]
    negatives = [r for r in rows if not r["expected_positive"]]
    misses = [r for r in positives if r["finding_count"] == 0]
    false_positives = [r for r in negatives if r["finding_count"] > 0]
    rule_counts = Counter()
    for r in rows:
        rule_counts.update(r["rules"])

    summary = {
        "positive_samples": len(POSITIVE_SAMPLES),
        "negative_samples": len(NEGATIVE_SAMPLES),
        "variants_per_sample": len(MUTATORS),
        "total_cases": len(rows),
        "positive_variants_detected": sum(r["finding_count"] > 0 for r in positives),
        "positive_variants_total": len(positives),
        "negative_variants_flagged": len(false_positives),
        "negative_variants_total": len(negatives),
        "misses": [{k: r[k] for k in ("sample", "variant", "mutated_excerpt")} for r in misses],
        "false_positives": [{k: r[k] for k in ("sample", "variant", "first_matches", "mutated_excerpt")} for r in false_positives],
        "rule_counts": dict(sorted(rule_counts.items())),
    }
    return {"summary": summary, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", default="/mnt/data/command_leakage_test_results_v6.json")
    parser.add_argument("--txt-out", default="/mnt/data/command_leakage_test_results_v6.txt")
    args = parser.parse_args()
    result = run()
    Path(args.json_out).write_text(json.dumps(result, indent=2), encoding="utf-8")

    s = result["summary"]
    lines = [
        "Command Leakage Detector Stress Test v6",
        "========================================",
        "",
        f"positive samples: {s['positive_samples']}",
        f"negative samples: {s['negative_samples']}",
        f"variants per sample: {s['variants_per_sample']}",
        f"total cases: {s['total_cases']}",
        f"positive variants detected: {s['positive_variants_detected']}/{s['positive_variants_total']}",
        f"negative variants flagged:   {s['negative_variants_flagged']}/{s['negative_variants_total']}",
        f"rule counts: {s['rule_counts']}",
        "",
        "MISSES",
        "------",
    ]
    if not s["misses"]:
        lines.append("none")
    else:
        for m in s["misses"]:
            lines.append(f"- {m['sample']} / {m['variant']}: {m['mutated_excerpt'][:160]!r}")
    lines += ["", "FALSE POSITIVES", "---------------"]
    if not s["false_positives"]:
        lines.append("none")
    else:
        for fp in s["false_positives"][:30]:
            lines.append(f"- {fp['sample']} / {fp['variant']}: {fp['first_matches']}")
    Path(args.txt_out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not s["misses"] and not s["false_positives"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
