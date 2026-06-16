---
name: make-tex-docstring
description: |
  Create or propose a top-of-document TeX comment block that records the document profile and intended use.

  Use when:
  - a TeX document is missing a top-of-document profile comment
  - the user wants to add or standardize a document docstring/header comment
  - another skill needs document-profile information and the file does not already state it clearly

  Do not use when:
  - the file already has a suitable top-of-document profile comment
  - the user wants substantive editing rather than document-profile metadata

  Success criteria:
  - identify or reliably infer the document profile
  - ask only for information that cannot be inferred safely
  - produce one canonical TeX comment block
  - keep the schema in one place and avoid ad hoc variations across skills
  - do not edit the file unless the user agrees
---

When this skill is used, begin with:

Skill: make-tex-docstring

Category: document-oriented

## 1. Goal

Your job is to create a short top-of-document TeX comment block that records the document profile.

Use this skill when a document-oriented task needs profile information and the source file does not already state it clearly near the top.

## 2. Canonical schema

Read `../references/document-profile-schema.md`.
Use that file as the canonical schema and TeX block format.
Do not create alternate schema variants here.

## 3. How to fill it

Identify or infer the fields from the shared schema, as reliably as possible.
Infer only when the inference is reliable from the document or conversation.
If important items cannot be inferred safely, ask the user.

## 4. Existing docstrings

Before proposing a new block, check whether the document already has a suitable top-of-document profile comment.

If it does:
- do not replace it mechanically
- only suggest revisions if information is missing, unclear, or inconsistent

## 5. Output

Start with:

- `Mode: Explore`
- `Skill: make-tex-docstring`

Keep the answer concise.

By default:
1. state whether a suitable docstring already exists
2. identify any missing or unclear fields
3. propose a canonical comment block
4. ask before inserting or editing the file

## 6. What not to do

Do not:
- edit the file automatically
- invent profile details that are not reasonably inferable
- create multiple schema variants across different documents
- turn the docstring into a long prose preface
