# Document Profile Schema

Use this file as the canonical schema for top-of-document TeX profile comments and for any skill that needs document-profile information.

## Canonical fields

- `Document type`
- `Field/subfield`
- `Purpose`
- `Audience`
- `Assumed background`
- `Target level of rigor/detail`
- `Expected document length`
- `Relationship to main paper or companion documents`

## Field notes

- `Document type`
  - the kind of document the reader is looking at, such as a journal paper, conference paper, research presentation, or research notes
- `Field/subfield`
  - the main disciplinary home and local subfield conventions the document is written in or for
- `Purpose`
  - why the document exists: for example, to present a research contribution, give a talk, record internal notes, explain an argument, or support a companion paper
- `Audience`
  - the intended readers or listeners, including any primary/secondary audience distinctions if they matter
- `Assumed background`
  - what prior knowledge, context, and familiarity can be assumed from the intended audience
- `Target level of rigor/detail`
  - how much formal detail, proof depth, motivation, or technical completeness the document is supposed to provide
- `Expected document length`
  - the relevant page, word, slide, or time constraint, if any
- `Relationship to main paper or companion documents`
  - how this document relates to a larger project, if relevant

## Canonical TeX block

```tex
% Document type:
% Field/subfield:
% Purpose:
% Audience:
% Assumed background:
% Target level of rigor/detail:
% Expected document length:
% Relationship to main paper or companion documents:
```

## Notes

- Keep this schema stable across documents and skills unless the user asks to change it.
- `Expected document length` may be left unspecified. That means there is no binding length constraint.
- `Relationship to main paper or companion documents` may be left blank when irrelevant.
- Infer fields only when the inference is reliable from the document or conversation. Otherwise ask the user.
- `Reader familiarity` is usually derived from `Audience` plus `Assumed background`; it does not need to be a separate canonical field unless the user wants it.

## Normalization

When mapping informal document labels to internal categories, use these defaults:

- `journal paper` -> `journal-article`
- `conference paper` -> `conference-article`
- `talk`, `slides`, or `presentation` -> `research-presentation`
- `note for self`, `note for coauthors`, or `technical note` -> `research-notes`

If there is no exact internal category, choose the closest functional baseline:

- `working paper` or `exposition of existing research` -> usually `journal-article`
- `technical companion` or `appendix` -> usually `research-notes`
