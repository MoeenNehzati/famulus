    // ── Utilities ────────────────────────────────────────────────────────────

    function escapeHtml(text) {
      return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    // MathJax 3 does not serialize repeated dynamic typesetting calls itself.
    // Keep one queue and discard requests for content replaced while waiting.
