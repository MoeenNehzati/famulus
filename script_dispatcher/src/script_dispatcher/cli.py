"""Legacy compatibility CLI entrypoint for the shared dispatcher."""

from officina.dispatcher.cli import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
