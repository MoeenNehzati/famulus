# Test-Suite Performance

Mechanical checks become expensive when preparation is repeated for every check.
Design the suite around the lifetime of prepared data, not around the number of
assertions.

## Principles

- Profile first. Separate data preparation, individual checks, test-framework
  startup, and repository-runner overhead.
- Prepare shared data once at the narrowest correct scope. Use a module-scoped
  fixture when every test in one module consumes the same immutable preparation;
  use session scope only when the data is valid across the whole test session.
- Make dependencies explicit. Tests receive fixtures as arguments, so pytest can
  construct each fixture once, cache it for its scope, and release it afterward.
- Do not introduce a fixture when repetition occurs inside one test item. Prepare
  that machinery once at the enclosing function or scan boundary instead.
- Keep tests separate when separate failures are useful. Shared preparation does
  not require combining assertions into one large test.
- Cache prepared machinery as well as parsed data. Schema validators,
  configuration, and other reusable interpreters should not be rebuilt inside a
  per-file loop.
- Reuse only behaviorally equivalent preparation. Do not broaden a check or hide
  ordering and error-boundary changes merely to obtain a cache hit.
- Stop optimizing when suite logic is no longer the bottleneck. Framework,
  subprocess, and staged-repository setup are shared runner concerns.

## Example: user-document validation

The user-document validator originally rebuilt the skill catalog for each
document check. Catalog construction reread every skill blueprint and rebuilt
the configured module-schema validator repeatedly.

The validator now exposes separate pytest items that depend on one module-scoped
fixture:

```python
@pytest.fixture(scope="module")
def skill_catalog(repo_root: Path) -> list[SkillInfo]:
    return load_catalog(repo_root)


def test_domain_coverage(repo_root: Path, skill_catalog: list[SkillInfo]):
    return _validate_domain_coverage(repo_root, skill_catalog)
```

Catalog construction also prepares one module-blueprint loader and reuses its
schema-validator cache across all skills. The checks remain independent, but
their immutable preparation runs once. Runtime fell from about 9.8 seconds to
about 0.8 seconds; rendering the three documents now takes less than one
millisecond, so further work belongs at the shared runner layer.

## Example: boundary scanning

The cross-skill boundary validator rebuilt path regexes for every source line and
other skill, causing about 3.95 million escapes and searches. Compiling three
repository-wide matchers once per validation reduced direct runtime from about
6.7 seconds to 0.1 seconds. A fixture was unnecessary because the repeated work
was inside one validator item.
