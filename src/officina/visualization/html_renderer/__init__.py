"""Browser assets for the ELK HTML renderer.

This package assembles the HTML, CSS, JavaScript, and vendored runtime inputs used by graph rendering.

Includes
--------
``README.md``
    Provides detailed package-specific operational documentation.
``__init__.py``
    Documents this package and its owned files.
``assets.py``
    Loads and assembles the HTML template, styles, runtime modules, and vendored scripts.
``dependencies.py``
    Declares renderer asset ordering and verifies vendored dependency provenance.
``page.html``
    Provides the static HTML shell populated by the renderer.
``runtime/bootstrap.js``
    Implements the browser runtime's bootstrap behavior.
``runtime/controls.js``
    Implements the browser runtime's controls behavior.
``runtime/core.js``
    Implements the browser runtime's core behavior.
``runtime/edge_presentation.js``
    Implements the browser runtime's edge presentation behavior.
``runtime/filtering.js``
    Implements the browser runtime's filtering behavior.
``runtime/geometry.js``
    Implements the browser runtime's geometry behavior.
``runtime/graph_actions.js``
    Implements the browser runtime's graph actions behavior.
``runtime/inspector.js``
    Implements the browser runtime's inspector behavior.
``runtime/interactions.js``
    Implements the browser runtime's interactions behavior.
``runtime/layout.js``
    Implements the browser runtime's layout behavior.
``runtime/legend.js``
    Implements the browser runtime's legend behavior.
``runtime/math_typesetter.js``
    Implements the browser runtime's math typesetter behavior.
``runtime/node_renderer.js``
    Implements the browser runtime's node renderer behavior.
``runtime/presentation_nodes.js``
    Implements the browser runtime's presentation nodes behavior.
``runtime/projection.js``
    Implements the browser runtime's projection behavior.
``runtime/render_pipeline.js``
    Implements the browser runtime's render pipeline behavior.
``runtime/selection.js``
    Implements the browser runtime's selection behavior.
``runtime/sidebar_layout.js``
    Implements the browser runtime's sidebar layout behavior.
``runtime/viewer_state.js``
    Implements the browser runtime's viewer state behavior.
``runtime/visibility.js``
    Implements the browser runtime's visibility behavior.
``vendor/elk-worker.min.js``
    Implements the browser runtime's elk-worker.min behavior.
``vendor/elk.bundled.js``
    Implements the browser runtime's elk.bundled behavior.
``vendor/mathjax-3.2.2-tex-svg.js``
    Implements the browser runtime's mathjax-3.2.2-tex-svg behavior.
``viewer.css``
    Defines the graph canvas, controls, inspector, legend, and responsive layout styles.
"""
