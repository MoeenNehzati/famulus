    // Optional MathJax lifecycle for dynamic renderer content.
    let mathTypesetQueue = Promise.resolve();
    const mathGenerations = new WeakMap();

    function nextMathGeneration(element) {
      const generation = (mathGenerations.get(element) || 0) + 1;
      mathGenerations.set(element, generation);
      return generation;
    }

    function containsDelimitedMath(text, opening, closing) {
      let start = text.indexOf(opening);
      while (start >= 0) {
        const end = text.indexOf(closing, start + opening.length);
        if (end >= start + opening.length) return true;
        start = text.indexOf(opening, start + opening.length);
      }
      return false;
    }

    function containsMath(element) {
      const text = element.textContent || "";
      return containsDelimitedMath(text, "$$", "$$") ||
        containsDelimitedMath(text, "\\[", "\\]") ||
        containsDelimitedMath(text, "\\(", "\\)") ||
        containsDelimitedMath(text, "$", "$");
    }

    function clearMathBeforeMutation(element) {
      const hasMath = containsMath(element) || Boolean(element.querySelector("mjx-container"));
      nextMathGeneration(element);
      if (hasMath && window.MathJax && window.MathJax.typesetClear) {
        window.MathJax.typesetClear([element]);
      }
    }

    function typesetElement(element) {
      if (!window.MathJax || !window.MathJax.typesetPromise || !containsMath(element)) return;
      const generation = mathGenerations.get(element) || 0;
      mathTypesetQueue = mathTypesetQueue
        .catch(() => {})
        .then(() => {
          if (!element.isConnected || mathGenerations.get(element) !== generation) return;
          if (!containsMath(element)) return;
          return window.MathJax.typesetPromise([element]);
        });
    }

    window.officinaMathDiagnostics = async function () {
      if (window.MathJax?.startup?.promise) await window.MathJax.startup.promise;
      await mathTypesetQueue;
      return {
        unresolvedCommands: Object.keys(window.__unresolvedTeX || {}).sort(),
        mathErrorCount: document.querySelectorAll(
          'mjx-merror, merror, [data-mml-node="merror"]'
        ).length,
      };
    };
