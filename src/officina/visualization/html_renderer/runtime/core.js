    // ── Utilities ────────────────────────────────────────────────────────────

    function escapeHtml(text) {
      return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function selectorValue(value) {
      return CSS.escape(String(value));
    }

    const nodeElementIndex = new Map();

    function nodeElement(nodeId) {
      const key = String(nodeId);
      const cached = nodeElementIndex.get(key);
      if (cached?.isConnected) return cached;
      nodeElementIndex.delete(key);
      return null;
    }

    function applyPresentationOperation(operations, operation) {
      operations ? operations.push(operation) : operation();
    }

    function edgeElementsForNode(attribute, nodeId) {
      return edgeLayer.querySelectorAll(`[${attribute}="${selectorValue(nodeId)}"]`);
    }

    // MathJax 3 does not serialize repeated dynamic typesetting calls itself.
    // Keep one queue and discard requests for content replaced while waiting.
