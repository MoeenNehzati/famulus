# Third-party notices

Famulus is licensed under the MIT License. The following files distributed in
this repository retain their own upstream licenses.

## Eclipse Layout Kernel for JavaScript

- Upstream release: `elkjs` 0.10.0
- Source: <https://github.com/kieler/elkjs/tree/0.10.0>
- Source commit: `8a4fc2e11c1a184daa156cf563961bd860980c5f`
- License: Eclipse Public License 2.0
- License text: [`LICENSES/EPL-2.0.txt`](LICENSES/EPL-2.0.txt)
- Copyright: Kiel University and other ELK contributors
- Distributed files:
  - `src/officina/visualization/html_renderer/vendor/elk.bundled.js`
  - `src/officina/visualization/html_renderer/vendor/elk-worker.min.js`

Both distributed files are exact byte matches to their paths in the official
`elkjs-0.10.0.tgz` npm package. Their SHA-256 values are locked by
[`tests/test_vendored_asset_provenance.py`](tests/test_vendored_asset_provenance.py).

## MathJax 3.2.2

- Upstream release: `mathjax` 3.2.2
- Source: <https://github.com/mathjax/MathJax/tree/3.2.2>
- Source commit: `600692ad9d3552cc25f85510d5797bc942ecc9f7`
- License: Apache License 2.0
- License text: [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt)
- Copyright: The MathJax Consortium
- Distributed file:
  - `src/officina/visualization/html_renderer/vendor/mathjax-3.2.2-tex-svg-full.js`

The distributed file is an exact byte match to `es5/tex-svg.js` in the
official `mathjax-3.2.2.tgz` npm package. Its SHA-256 value is locked by
[`tests/test_vendored_asset_provenance.py`](tests/test_vendored_asset_provenance.py).
