"""Platform-specific GitHub Actions matrix identities for repository checks."""

EXPECTED_MATRIX = (
    ("ubuntu-latest", "combined"),
    ("macos-latest", "validators"),
    ("macos-latest", "tests:shared"),
    ("macos-latest", "tests:performance"),
    ("windows-latest", "validators"),
    ("windows-latest", "tests:shared"),
    ("windows-latest", "tests:performance"),
    ("windows-latest", "tests:browser"),
)

WINDOWS_RUNNER = "windows-latest"
