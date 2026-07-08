"""Local repo browser and rendered-doc server for Famulus."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
import mimetypes
import subprocess


TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".sh",
    ".css",
    ".html",
    ".js",
}


@dataclass(frozen=True)
class RepoBrowserConfig:
    repo_root: Path
    host: str = "127.0.0.1"
    port: int = 8765


def run_server(config: RepoBrowserConfig) -> None:
    """Serve a lightweight GitHub-style repo browser rooted at repo_root."""

    class Handler(RepoBrowserHandler):
        repo_root = config.repo_root.resolve()

    httpd = ThreadingHTTPServer((config.host, config.port), Handler)
    print(f"Serving {config.repo_root} at http://{config.host}:{config.port}/")
    httpd.serve_forever()


class RepoBrowserHandler(SimpleHTTPRequestHandler):
    repo_root: Path

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.repo_root), **kwargs)

    def do_GET(self) -> None:
        self._dispatch(send_body=True)

    def do_HEAD(self) -> None:
        self._dispatch(send_body=False)

    def _dispatch(self, send_body: bool) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        if route in {"", "/"}:
            self._send_html(self._render_home(), send_body=send_body)
            return
        if route.startswith("/browse/"):
            rel = route[len("/browse/") :]
            self._handle_browse(rel, send_body=send_body)
            return
        if route.startswith("/raw/"):
            self.path = "/" + route[len("/raw/") :]
            return super().do_GET() if send_body else super().do_HEAD()
        return super().do_GET() if send_body else super().do_HEAD()

    def _handle_browse(self, rel: str, *, send_body: bool) -> None:
        path = self._safe_path(rel)
        if path is None or not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "Path not found")
            return
        if path.is_dir():
            self._send_html(self._render_directory(path), send_body=send_body)
            return
        if path.suffix.lower() == ".md":
            self._send_html(self._render_markdown(path), send_body=send_body)
            return
        if self._is_text_file(path):
            self._send_html(self._render_text_file(path), send_body=send_body)
            return
        self.path = "/" + str(path.relative_to(self.repo_root)).replace("\\", "/")
        return super().do_GET() if send_body else super().do_HEAD()

    def _safe_path(self, rel: str) -> Path | None:
        rel = unquote(rel).lstrip("/")
        candidate = (self.repo_root / rel).resolve()
        try:
            candidate.relative_to(self.repo_root)
        except ValueError:
            return None
        return candidate

    def _send_html(self, html: str, *, send_body: bool = True) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _render_home(self) -> str:
        featured = [
            ("README", "/browse/README.md", "Main landing page"),
            ("General workflows", "/browse/docs/user/general.md", "Planning, wrap-up, lists, calendar, weather"),
            ("Research workflows", "/browse/docs/user/research.md", "Dependency graphs and research review tools"),
            ("System workflows", "/browse/docs/user/system.md", "Automation and infrastructure tools"),
            ("Contributor guide", "/browse/docs/contributors/README.md", "Skill-extension and maintainer entrypoint"),
            ("Skill index", "/browse/docs/skills.md", "Generated full skill inventory"),
            ("README preview", "/_build/README-preview.html", "Rendered preview HTML"),
        ]
        top_dirs = self._top_level_rows()
        cards = "\n".join(
            f'<a class="card" href="{href}"><strong>{escape(label)}</strong><span>{escape(desc)}</span></a>'
            for label, href, desc in featured
        )
        rows = "\n".join(top_dirs)
        return self._page(
            "Famulus Repo Browser",
            f"""
<section class="hero">
  <h1>Famulus</h1>
  <p>A local browser for the repo, rendered docs, and generated previews.</p>
</section>
<section>
  <h2>Featured Pages</h2>
  <div class="cards">{cards}</div>
</section>
<section>
  <h2>Repository Root</h2>
  <table class="listing">
    <thead><tr><th>Name</th><th>Type</th><th>Modified</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>
""",
        )

    def _render_directory(self, path: Path) -> str:
        rel = path.relative_to(self.repo_root)
        crumbs = self._breadcrumbs(rel)
        rows = []
        if rel.parts:
            parent = rel.parent
            parent_href = "/" if str(parent) == "." else f"/browse/{quote(parent.as_posix())}"
            rows.append(
                f'<tr><td><a href="{parent_href}">..</a></td><td>parent</td><td></td></tr>'
            )
        for child in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            href = f"/browse/{quote(child.relative_to(self.repo_root).as_posix())}"
            file_type = "dir" if child.is_dir() else child.suffix.lstrip(".") or "file"
            modified = datetime.fromtimestamp(child.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            rows.append(
                f"<tr><td><a href=\"{href}\">{escape(child.name)}</a></td>"
                f"<td>{escape(file_type)}</td><td>{modified}</td></tr>"
            )
        return self._page(
            f"{rel.as_posix() or '/'}",
            f"""
<nav class="breadcrumbs">{crumbs}</nav>
<h1>{escape(rel.as_posix() or "/")}</h1>
<table class="listing">
  <thead><tr><th>Name</th><th>Type</th><th>Modified</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
""",
        )

    def _render_markdown(self, path: Path) -> str:
        rel = path.relative_to(self.repo_root)
        raw_href = f"/raw/{quote(rel.as_posix())}"
        body = self._pandoc_html(path)
        return self._page(
            rel.as_posix(),
            f"""
<nav class="breadcrumbs">{self._breadcrumbs(rel)}</nav>
<div class="file-actions"><a href="{raw_href}">Raw file</a></div>
<article class="doc-body">
{body}
</article>
""",
        )

    def _render_text_file(self, path: Path) -> str:
        rel = path.relative_to(self.repo_root)
        raw_href = f"/raw/{quote(rel.as_posix())}"
        text = path.read_text(encoding="utf-8", errors="replace")
        return self._page(
            rel.as_posix(),
            f"""
<nav class="breadcrumbs">{self._breadcrumbs(rel)}</nav>
<div class="file-actions"><a href="{raw_href}">Raw file</a></div>
<h1>{escape(rel.as_posix())}</h1>
<pre class="code-view">{escape(text)}</pre>
""",
        )

    def _pandoc_html(self, path: Path) -> str:
        try:
            result = subprocess.run(
                ["pandoc", str(path), "--from=gfm", "--to=html5", "--standalone"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            text = path.read_text(encoding="utf-8", errors="replace")
            return f"<pre class=\"code-view\">{escape(text)}</pre>"

        html = result.stdout
        body_start = html.find("<body>")
        body_end = html.rfind("</body>")
        if body_start >= 0 and body_end > body_start:
            return html[body_start + len("<body>") : body_end]
        return html

    def _is_text_file(self, path: Path) -> bool:
        if path.suffix.lower() in TEXT_SUFFIXES:
            return True
        guessed, _ = mimetypes.guess_type(path.name)
        return bool(guessed and guessed.startswith("text/"))

    def _top_level_rows(self) -> list[str]:
        rows: list[str] = []
        for child in sorted(self.repo_root.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            href = f"/browse/{quote(child.relative_to(self.repo_root).as_posix())}"
            kind = "dir" if child.is_dir() else child.suffix.lstrip(".") or "file"
            modified = datetime.fromtimestamp(child.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            rows.append(
                f"<tr><td><a href=\"{href}\">{escape(child.name)}</a></td>"
                f"<td>{escape(kind)}</td><td>{modified}</td></tr>"
            )
        return rows

    def _breadcrumbs(self, rel: Path) -> str:
        parts = [('<a href="/">Famulus</a>')]
        accum = Path()
        for part in rel.parts:
            accum /= part
            parts.append(f'<a href="/browse/{quote(accum.as_posix())}">{escape(part)}</a>')
        return " / ".join(parts)

    def _page(self, title: str, body: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} | Famulus</title>
  <style>
    :root {{
      --bg: #f5f2ec;
      --paper: #fffdf9;
      --ink: #24313a;
      --muted: #64717a;
      --line: #d8d0c3;
      --accent: #305d73;
      --accent-soft: #edf2f4;
      --code: #f3efe7;
    }}
    html {{ background: var(--bg); }}
    body {{
      margin: 0;
      color: var(--ink);
      font: 16px/1.6 "Avenir Next", "Segoe UI", system-ui, sans-serif;
    }}
    .shell {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 1.4rem 1rem 2rem;
    }}
    .hero {{
      margin-bottom: 1.5rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--line);
    }}
    h1, h2, h3 {{
      line-height: 1.15;
      color: #1e3441;
      letter-spacing: -0.01em;
    }}
    h1 {{ margin: 0 0 0.45rem; font-size: clamp(2rem, 4vw, 2.6rem); }}
    h2 {{ margin-top: 1.8rem; font-size: 1.3rem; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .breadcrumbs {{
      margin-bottom: 1rem;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 0.9rem;
    }}
    .card {{
      display: block;
      padding: 0.9rem 1rem;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 10px;
    }}
    .card strong {{
      display: block;
      margin-bottom: 0.25rem;
      color: #223744;
    }}
    .card span {{
      color: var(--muted);
      font-size: 0.94rem;
    }}
    .listing {{
      width: 100%;
      border-collapse: collapse;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
    }}
    .listing th, .listing td {{
      padding: 0.62rem 0.75rem;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    .listing th {{
      background: #f1ece4;
    }}
    .listing tr:last-child td {{
      border-bottom: 0;
    }}
    .doc-body {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 1.2rem 1.25rem 1.5rem;
    }}
    .doc-body h1:first-child {{
      font-size: 2rem;
      margin-top: 0;
      padding-bottom: 0.7rem;
      border-bottom: 1px solid var(--line);
    }}
    .doc-body h2 {{ margin-top: 2rem; font-size: 1.32rem; }}
    .doc-body h3 {{ margin-top: 1.4rem; font-size: 1.08rem; }}
    .doc-body p:first-of-type {{ color: var(--muted); font-size: 1.02rem; }}
    .doc-body img {{
      max-width: 100%;
      height: auto;
      display: block;
      margin: 1rem 0;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: white;
    }}
    pre, .code-view {{
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      padding: 0.95rem 1rem;
      background: var(--code);
      border: 1px solid var(--line);
      border-radius: 10px;
      font: 13px/1.55 "IBM Plex Mono", "SFMono-Regular", Menlo, Monaco, Consolas, monospace;
    }}
    code {{
      font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, Monaco, Consolas, monospace;
      font-size: 0.92em;
      background: var(--accent-soft);
      padding: 0.1rem 0.32rem;
      border-radius: 0.32rem;
    }}
    pre code {{
      background: transparent;
      padding: 0;
    }}
    .doc-body table {{
      width: 100%;
      border-collapse: collapse;
      margin: 1rem 0;
      background: white;
    }}
    .doc-body th, .doc-body td {{
      border: 1px solid var(--line);
      padding: 0.55rem 0.7rem;
      text-align: left;
      vertical-align: top;
    }}
    .file-actions {{
      margin-bottom: 0.8rem;
    }}
    @media (max-width: 720px) {{
      .shell {{ padding: 1rem 0.75rem 1.5rem; }}
      .doc-body {{ padding: 0.9rem 0.95rem 1.1rem; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    {body}
  </div>
</body>
</html>
"""
