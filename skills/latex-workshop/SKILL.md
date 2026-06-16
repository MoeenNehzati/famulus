---
name: latex-workshop
description: Follow VS Code LaTeX Workshop build behavior for TeX/LaTeX documents. Use when a user wants to compile, rebuild, or troubleshoot a LaTeX document and the build should match LaTeX Workshop settings, recipes, and output-directory conventions. Prefer workspace-level VS Code LaTeX Workshop config and fall back to user-level config only when the workspace does not override it. If direct reconstruction of the LaTeX Workshop build is not possible, fall back to a manual latexmk command consistent with the discovered settings.
---

Skill: latex-workshop

Category: document-oriented

## Goal

Behave the way VS Code LaTeX Workshop is configured to behave for the target
LaTeX document.

Do not assume that outputs belong beside the source file, that `latexmk` is the
intended tool, or that `_build` is the correct outdir unless the effective
LaTeX Workshop configuration implies that.

## Workflow

1. Resolve the effective LaTeX Workshop configuration.
- Check workspace-level config first:
  - `.vscode/settings.json`
  - any `*.code-workspace` file in the repo
- Look for relevant LaTeX Workshop settings, especially:
  - `latex-workshop.latex.outDir`
  - `latex-workshop.latex.recipes`
  - `latex-workshop.latex.tools`
- If no workspace-level override exists, check user-level VS Code settings:
  - Linux: `~/.config/Code/User/settings.json`
  - macOS: `~/Library/Application Support/Code/User/settings.json`
  - Windows: `%APPDATA%\\Code\\User\\settings.json`
- Workspace-level config overrides user-level config.
- If the outdir contains `%DIR%`, resolve it relative to the directory of the
  target `.tex` file.

2. Reconstruct the LaTeX Workshop build behavior.
- Prefer the configured recipe/tool behavior when it is readable from settings.
- If no recipe/tool information is available, use the effective outdir setting
  and a conservative `latexmk` fallback.
- If there is a repo script or Makefile that clearly matches the configured
  LaTeX Workshop workflow, it is fine to use that instead.

3. Compile with the effective behavior.
- Typical fallback when an outdir is configured:
```bash
latexmk -pdf -outdir="<resolved-outdir>" -interaction=nonstopmode -halt-on-error "<file>.tex"
```
- Typical fallback when no outdir is configured:
```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error "<file>.tex"
```

4. Read artifacts from the effective output location.
- Inspect logs, PDFs, and bibliography artifacts in the resolved output
  directory, not next to the source file unless the effective config implies
  that.
- If an earlier compile was run in the wrong location, say so plainly and switch
  to the configured location for later runs.

5. Report the source of truth.
- State whether the effective behavior came from workspace config, user config,
  or a fallback.
- If fallback behavior was used because LaTeX Workshop settings were incomplete,
  say exactly what was missing.

## Retrieval Commands

- Workspace-level search:
```bash
rg -n 'latex-workshop\\.latex\\.(outDir|recipes|tools)' .vscode *.code-workspace
```
- User-level search on Linux:
```bash
rg -n 'latex-workshop\\.latex\\.(outDir|recipes|tools)' ~/.config/Code/User/settings.json
```

## Notes

- There is usually no direct terminal command that means "invoke LaTeX Workshop
  itself." In practice, use the discovered LaTeX Workshop settings to
  reconstruct the intended build behavior.
- Treat user mentions of VS Code or LaTeX Workshop as a signal to inspect these
  settings before compiling.
- Prefer matching LaTeX Workshop behavior over ad hoc shell habits.
