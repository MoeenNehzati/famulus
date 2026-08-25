# Inference From Random Restarts frozen benchmark

This ignored directory contains local source evidence and adjudicated results used with the inquisitive-inventory Rutter for the appendix of *Inference From Random Restarts* (arXiv:2602.13450v2). None of these paper-specific artifacts belong in the committed skill.

## Contents

- `paper.pdf` is a 44-page PDF compiled with `latexmk -pdf` from the exact v2 source archive.
- `appendix/` contains the four source files in the frozen annotation scope.
- `results/inventory-gold.json` is the local inventory-stage truth. It conforms to
  `../../schemas/inventory.schema.json`, retains all 27 explicit proof environments plus two source-visible proof sketches, and uses
  `supports -> proof -> proves -> result` topology.
- `results/semantic-gold.json` is the local proof-free semantic truth. It conforms to
  `../../schemas/semantic-graph.schema.json` and the normalized semantic profile.
- `results/final-gold.json` is the local canonical final graph compiled from semantic
  gold. It satisfies the math payload and shared renderer contracts and is the expected
  whole-pipeline output.
- `gold-provenance/adjudication-base-v1.json` and `gold-provenance/adjudication-overlay-v2.json` are
  the authenticated adjudication inputs from which the three stage artifacts
  were derived; they are provenance records, not additional gold interfaces.
- `gold-provenance/source-aliases-v1.json` maps the frozen gold source names to
  the entrypoint-relative source names emitted by the inventory chunker. It
  reconciles source identity for debug projection without modifying frozen gold.

The legacy iterator database and authenticated worker fragments are experiment evidence, not gold or paper source, and remain outside this immutable asset bundle.

## Provenance

The source was extracted from `arXiv-2602.13450v2.tar.gz`. Its SHA-256 is `3ae8dac2d58786a80761999a80d0304e19af74d7ee2132d714e3a7a043b7ab96`, matching `source.archive_sha256` in the base annotation.

| File | SHA-256 |
| --- | --- |
| `paper.pdf` | `d50fcfed855bec61ec39e01b8668562dff52f872b7676ca40c77e54a2a56ceea` |
| `appendix/appendix-dynamics.tex` | `07edff84dbf11191fb84831285444e8a29d6543c9520f12fdbfff4d5348b775e` |
| `appendix/appendix-prevalence.tex` | `ec6c3c60fb26e8942ca0dd3ca2b1b01145f231c257f74a47ad9a5336d50b4775` |
| `appendix/appendix-bayes.tex` | `d6d7f93ab04f9dd8db09610b1daa298e77222e86a743605bde5b4d553ec62753` |
| `appendix/appendix-application.tex` | `980ac1e0c9f31bb50b2a7ad367c3e510354611bd52b9b45a2258852d5c348546` |
| `results/inventory-gold.json` | `09adf77e931ad828a44d07e426884e7e6f2741699ff944d881b4f6218d337a91` |
| `results/semantic-gold.json` | `a94eac7d5e86e2f7b021e78f3fd6c98f23db18438ce6a3f88bee786537c1fd59` |
| `results/final-gold.json` | `5855f5a301956b2d94dfedd3f5001a02f3efdcafb86f3288bacc4518d7b70dcb` |
| `gold-provenance/adjudication-base-v1.json` | `817b73e8a3652fd4869b7b6abe8b8ff367360080028b5809c6777eb8e5588258` |
| `gold-provenance/adjudication-overlay-v2.json` | `e631215b4c212c416b51d19e60aa891d8a7a5670e30826ca2b6ec757a630778b` |
| `gold-provenance/source-aliases-v1.json` | `52c1513182beaadd6cea93f6d37a6b1f24937b7a50615bc45c6381b1bc504c47` |

Do not overwrite these files in place. A revised annotation or source snapshot should be added as a new versioned bundle with its own provenance record.
