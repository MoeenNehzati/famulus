#!/usr/bin/env python3
"""Generate local HTML previews for selected Markdown docs under _build/."""
from __future__ import annotations

from pathlib import Path
import argparse
import subprocess
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = REPO_ROOT / "_build"

README_HEADER = """<base href="__BASE_HREF__">
<style>
:root {
  --bg: #f5f2ec;
  --paper: #fffdf9;
  --ink: #22303a;
  --muted: #5f6e79;
  --line: #d9d2c6;
  --accent: #305d73;
  --accent-soft: #e8eef1;
  --code-bg: #f3efe7;
}

html {
  background: var(--bg);
}

body {
  max-width: 960px;
  margin: 0 auto;
  padding: 2rem 1.1rem 3rem;
  color: var(--ink);
  font-family: "Avenir Next", "Segoe UI", system-ui, sans-serif;
  line-height: 1.6;
}

main,
body > :not(script):not(style):not(base) {
  box-sizing: border-box;
}

body::before {
  content: "";
  position: fixed;
  inset: 0;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.35), rgba(255, 255, 255, 0)),
    radial-gradient(circle at top left, rgba(48, 93, 115, 0.06), transparent 28rem);
  pointer-events: none;
}

h1, h2, h3 {
  color: #1f3441;
  line-height: 1.15;
  letter-spacing: -0.01em;
}

h1 {
  margin-top: 0;
  margin-bottom: 0.9rem;
  padding-bottom: 0.8rem;
  font-size: clamp(2rem, 4vw, 2.8rem);
  border-bottom: 1px solid var(--line);
}

h2 {
  margin-top: 2.4rem;
  margin-bottom: 0.7rem;
  font-size: 1.45rem;
}

h3 {
  margin-top: 1.6rem;
  margin-bottom: 0.45rem;
  font-size: 1.1rem;
}

p:first-of-type {
  font-size: 1.05rem;
  color: var(--muted);
}

a {
  color: var(--accent);
}

pre {
  overflow-x: auto;
  padding: 0.95rem 1rem;
  background: var(--code-bg);
  border: 1px solid var(--line);
  border-radius: 10px;
}

code {
  font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, Monaco, Consolas, monospace;
  font-size: 0.92em;
}

:not(pre) > code {
  background: var(--accent-soft);
  padding: 0.12rem 0.35rem;
  border-radius: 0.35rem;
}

img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1.3rem 0;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: white;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 1.25rem 0;
  background: var(--paper);
}

th, td {
  border: 1px solid var(--line);
  padding: 0.55rem 0.7rem;
  vertical-align: top;
}

th {
  background: #f2eee6;
  text-align: left;
}

blockquote {
  margin: 1rem 0;
  padding: 0.85rem 1rem;
  color: var(--muted);
  border-left: 3px solid #b8c7cf;
  background: rgba(255, 255, 255, 0.45);
}

.skills-tree {
  margin: 1.4rem 0 1.8rem;
  padding: 1rem 1.05rem;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 12px;
}

.skills-tree-title {
  font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, Monaco, Consolas, monospace;
  font-weight: 700;
  color: #31434e;
}

.skills-tree-subtitle {
  margin-top: 0.15rem;
  margin-bottom: 0.75rem;
  color: var(--muted);
  font-size: 0.95rem;
}

.skills-tree-block {
  margin: 0;
  padding: 0;
  background: transparent;
  color: var(--ink);
  line-height: 1.58;
}

.skills-tree-note {
  color: var(--muted);
}

@media (max-width: 720px) {
  body {
    padding: 1.25rem 0.8rem 2rem;
  }
}
</style>
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });

document.addEventListener('DOMContentLoaded', async () => {
  const selectors = [
    'pre > code.language-mermaid',
    'pre > code.mermaid',
    'pre.mermaid > code',
    'pre > code[class*="mermaid"]'
  ];
  const seen = new Set();
  for (const code of document.querySelectorAll(selectors.join(','))) {
    if (seen.has(code)) continue;
    seen.add(code);
    const div = document.createElement('div');
    div.className = 'mermaid';
    div.textContent = code.textContent;
    const pre = code.closest('pre');
    pre.replaceWith(div);
  }
  await mermaid.run({ querySelector: '.mermaid' });
});
</script>
"""

SCAFFOLDING_HEADER = """<base href="__BASE_HREF__">
<style>
:root {
  --ink: #14213d;
  --muted: #4f5d75;
  --paper: #fffdf8;
  --line: #d8cfc2;
  --accent: #0f766e;
  --accent-2: #c2410c;
  --card: rgba(255, 252, 245, 0.88);
  --shadow: 0 18px 48px rgba(20, 33, 61, 0.12);
}

html {
  background:
    radial-gradient(circle at top left, rgba(15, 118, 110, 0.10), transparent 26rem),
    radial-gradient(circle at top right, rgba(194, 65, 12, 0.12), transparent 22rem),
    linear-gradient(180deg, #f7f2e8 0%, #fffdf8 42%, #f7f3ec 100%);
}

body {
  max-width: 980px;
  margin: 0 auto;
  padding: 2.5rem 1.2rem 4rem;
  color: var(--ink);
  font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
  line-height: 1.68;
}

body::before {
  content: "";
  position: fixed;
  inset: 1rem;
  border: 1px solid rgba(20, 33, 61, 0.07);
  border-radius: 28px;
  pointer-events: none;
}

h1, h2, h3 {
  font-family: "Avenir Next Condensed", "Gill Sans", "Trebuchet MS", sans-serif;
  letter-spacing: 0.01em;
  line-height: 1.1;
}

h1 {
  margin: 0 0 1rem;
  padding: 1.4rem 1.5rem 1rem;
  font-size: clamp(2.3rem, 4vw, 3.4rem);
  color: #102542;
  background: linear-gradient(135deg, rgba(255,255,255,0.82), rgba(246,241,232,0.92));
  border: 1px solid rgba(20, 33, 61, 0.08);
  border-radius: 24px;
  box-shadow: var(--shadow);
}

h2 {
  margin-top: 2.6rem;
  margin-bottom: 0.7rem;
  font-size: 1.5rem;
  color: #143b38;
}

h3 {
  margin-top: 1.8rem;
  margin-bottom: 0.5rem;
  font-size: 1.12rem;
  color: #7c2d12;
}

p, li, td, th {
  font-size: 1.03rem;
}

p:first-of-type {
  margin-top: 0.6rem;
  color: var(--muted);
  font-size: 1.08rem;
}

a {
  color: var(--accent);
}

code {
  font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, Monaco, Consolas, monospace;
  font-size: 0.92em;
  background: rgba(15, 118, 110, 0.08);
  padding: 0.1rem 0.32rem;
  border-radius: 0.3rem;
}

pre {
  overflow-x: auto;
  padding: 1rem 1.1rem;
  background: #f5efe6;
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.7);
}

pre code {
  background: transparent;
  padding: 0;
}

img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1.4rem auto 0.55rem;
  border-radius: 20px;
  border: 1px solid rgba(20, 33, 61, 0.08);
  box-shadow: 0 18px 40px rgba(20, 33, 61, 0.14);
  background: white;
}

.graph-caption {
  margin-top: 0.2rem;
  margin-bottom: 1.4rem;
  text-align: center;
  color: var(--muted);
  font-size: 0.95rem;
  font-style: italic;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 1.2rem 0 1.7rem;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 18px;
  overflow: hidden;
  box-shadow: var(--shadow);
}

th, td {
  padding: 0.75rem 0.9rem;
  border: 1px solid rgba(216, 207, 194, 0.85);
  vertical-align: top;
}

th {
  text-align: left;
  background: linear-gradient(180deg, rgba(15, 118, 110, 0.12), rgba(15, 118, 110, 0.05));
  color: #143b38;
  font-family: "Avenir Next Condensed", "Gill Sans", "Trebuchet MS", sans-serif;
  letter-spacing: 0.02em;
}

ul, ol {
  padding-left: 1.35rem;
}

li::marker {
  color: var(--accent-2);
}

blockquote {
  margin: 1.4rem 0;
  padding: 0.9rem 1rem;
  background: rgba(194, 65, 12, 0.06);
  border-left: 4px solid rgba(194, 65, 12, 0.52);
  border-radius: 0 16px 16px 0;
}

hr {
  border: 0;
  height: 1px;
  margin: 2rem 0;
  background: linear-gradient(90deg, transparent, rgba(20,33,61,0.18), transparent);
}

@media (max-width: 720px) {
  body {
    padding: 1.25rem 0.8rem 2.5rem;
  }

  h1 {
    padding: 1rem 1rem 0.8rem;
  }

  table, thead, tbody, tr, th, td {
    display: block;
  }

  thead {
    display: none;
  }

  tr + tr {
    border-top: 1px solid var(--line);
  }

  td {
    border: 0;
    border-top: 1px solid rgba(216, 207, 194, 0.75);
  }

  td:first-child {
    border-top: 0;
    font-weight: 700;
    color: #143b38;
    padding-bottom: 0.3rem;
  }
}
</style>
"""


def _pandoc_exists() -> bool:
    return subprocess.run(
        ["pandoc", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _base_href(path: Path) -> str:
    href = path.resolve().as_uri()
    return href if href.endswith("/") else href + "/"


def _with_base_href(header_html: str, base_path: Path) -> str:
    return header_html.replace("__BASE_HREF__", _base_href(base_path), 1)


def _render_preview(
    markdown_path: Path,
    output_path: Path,
    header_html: str,
    metadata_key: str,
    metadata_value: str,
) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output_path.with_suffix(output_path.suffix + ".tmp")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".html", delete=False) as header_file:
        header_file.write(header_html)
        header_path = Path(header_file.name)
    try:
        subprocess.run(
            [
                "pandoc",
                str(markdown_path),
                "--from=gfm",
                "--to=html5",
                "--standalone",
                "--metadata",
                f"{metadata_key}={metadata_value}",
                f"--include-in-header={header_path}",
                f"--output={tmp_output}",
            ],
            check=True,
        )
        tmp_output.replace(output_path)
    finally:
        header_path.unlink(missing_ok=True)


def generate_readme_preview() -> Path:
    output_path = BUILD_DIR / "README-preview.html"
    _render_preview(
        REPO_ROOT / "README.md",
        output_path,
        _with_base_href(README_HEADER, REPO_ROOT),
        "title",
        "Famulus README preview",
    )
    return output_path


def generate_scaffolding_preview() -> Path:
    output_path = BUILD_DIR / "scaffolding-preview.html"
    _render_preview(
        REPO_ROOT / "docs/scaffolding/README.md",
        output_path,
        _with_base_href(SCAFFOLDING_HEADER, REPO_ROOT / "docs/scaffolding"),
        "pagetitle",
        "Famulus scaffolding preview",
    )
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=("readme", "scaffolding", "all"),
        default="all",
        help="Which preview to generate (default: all)",
    )
    args = parser.parse_args()

    if not _pandoc_exists():
        print("error: pandoc is required to generate previews", file=sys.stderr)
        return 1

    generated: list[Path] = []
    if args.target in {"readme", "all"}:
        generated.append(generate_readme_preview())
    if args.target in {"scaffolding", "all"}:
        generated.append(generate_scaffolding_preview())

    for path in generated:
        print(path.relative_to(REPO_ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
