pytest_plugins = ("pytester",)


def test_fixture_probe_records_serial_module_fixture(pytester, monkeypatch, tmp_path) -> None:
    probe_dir = tmp_path / "probe"
    pytester.makepyfile(
        """
        import pytest
        @pytest.fixture(scope="module")
        def shared():
            return object()
        @pytest.fixture
        def consumer(shared):
            return shared
        def test_one(consumer): pass
        def test_two(consumer): pass
        """
    )
    monkeypatch.setenv("OFFICINA_FIXTURE_PROBE_DIR", str(probe_dir))
    monkeypatch.setenv("OFFICINA_FIXTURE_PROBE_RUN_ID", "serial/run")
    monkeypatch.setenv("OFFICINA_FIXTURE_PROBE_TASK_ID", "tests:shared")

    result = pytester.runpytest("-p", "test_support.fixture_probe", "-q")

    result.assert_outcomes(passed=2)
    records = [
        __import__("json").loads(line)
        for path in probe_dir.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    shared = [record for record in records if record["fixture"] == "shared"]
    assert len(shared) == 1
    assert shared[0]["run_id"] == "serial/run"
    assert shared[0]["task_id"] == "tests:shared"


def test_fixture_probe_counts_module_fixture_per_observed_xdist_worker(
    pytester, monkeypatch, tmp_path
) -> None:
    probe_dir = tmp_path / "probe"
    pytester.makepyfile(
        """
        import pytest
        @pytest.fixture(scope="module")
        def shared():
            return object()
        @pytest.fixture
        def consumer(shared):
            return shared
        def test_one(consumer): pass
        def test_two(consumer): pass
        """
    )
    monkeypatch.setenv("OFFICINA_FIXTURE_PROBE_DIR", str(probe_dir))
    monkeypatch.setenv("OFFICINA_FIXTURE_PROBE_RUN_ID", "xdist")
    monkeypatch.setenv("OFFICINA_FIXTURE_PROBE_TASK_ID", "tests:shared")

    result = pytester.runpytest("-p", "test_support.fixture_probe", "-n", "2", "--dist", "worksteal", "-q")

    result.assert_outcomes(passed=2)
    records = [
        __import__("json").loads(line)
        for path in probe_dir.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    consumer_workers = {
        record["worker_id"]
        for record in records
        if record["fixture"] == "consumer"
        and record["node_id"].split("::")[-1] in {"test_one", "test_two"}
    }
    shared_workers = {
        record["worker_id"] for record in records if record["fixture"] == "shared"
    }
    assert shared_workers == consumer_workers
    assert sum(record["fixture"] == "shared" for record in records) == len(
        consumer_workers
    )
