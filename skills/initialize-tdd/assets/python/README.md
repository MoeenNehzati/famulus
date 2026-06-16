# {{PROJECT_NAME}}

## Setup

```bash
./install.sh                       # creates venv at ./.venv and installs deps
source .venv/bin/activate
```

`.env` (runtime config) and `.testenv` (test-only config) are gitignored;
create them locally as needed (see `src/project/config.py` / `logger.py` for
the keys they read).

## Running tests

```bash
source .venv/bin/activate
pytest
```

## Project layout

```
{{PROJECT_NAME}}/
├── CLAUDE.md          # development workflow & conventions
├── src/project/       # application source
│   ├── config.py      # loads .env / .testenv
│   └── logger.py      # centralized logging (get_logger)
├── tests/             # unit & integration tests
└── logs/              # runtime logs (gitignored)
```

## Logging

Use the centralized logger everywhere:

```python
from project.logger import get_logger

log = get_logger(__name__)
log.info("hello")
```

Configure via `.env`:
- `LOG_LEVEL` (default `INFO`)
- `LOG_DIR` (default `logs`)

## Development workflow

See [CLAUDE.md](CLAUDE.md) for the staged TDD workflow used for all new
functionality (design → tests → implementation → docs).
