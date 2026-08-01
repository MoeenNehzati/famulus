    // ── Visibility helpers ───────────────────────────────────────────────────

    function isHiddenNode(nodeId) {
      const category = nodeCategories.get(nodeId);
      if (hiddenTypes.has(category) || hiddenNodes.has(nodeId)) return true;
      let current = parentByNode.get(nodeId);
      const seen = new Set();
      while (current && !seen.has(current)) {
        if (collapsedContainers.has(current)) return true;
        seen.add(current);
        current = parentByNode.get(current);
      }
      return false;
    }

    // ── Removed nodes list ───────────────────────────────────────────────────

    function renderRemovedNodes() {
      const removedEntities = docData.entities
        .filter(e => hiddenNodes.has(e.id))
        .sort((a, b) => (a.position || 0) - (b.position || 0) || a.short_title.localeCompare(b.short_title));
      clearMathBeforeMutation(removedNodesEl);
      removedNodesEl.innerHTML = "";

      const hasFocusHidden = ancestorHiddenByFocus.size > 0;
      const hasRemoved = removedEntities.length > 0;

      if (!hasFocusHidden && !hasRemoved) { removedNodesEl.textContent = "None"; return; }

      // Ancestor-focus group (mode 2): one collapsed item for all focus-hidden nodes
      if (hasFocusHidden) {
        const count = ancestorHiddenByFocus.size;
        const focusEntity = focusNodeId ? entityMap.get(focusNodeId) : null;
        const focusLabel = focusEntity ? focusEntity.short_title : "selection";
        const groupItem = document.createElement("div");
        groupItem.className = "removed-item";
        groupItem.innerHTML = `
          <div><strong>Non-ancestors of ${escapeHtml(focusLabel)}</strong></div>
          <div class="removed-item-number">${count} node${count !== 1 ? "s" : ""} hidden — click to restore all</div>
        `;
        groupItem.addEventListener("click", () => {
          ancestorFocusMode = 0;
          focusNodeId = null;
          saveViewerState();
          applyAncestorFocus();
        });
        removedNodesEl.appendChild(groupItem);
      }

      // Individually double-click-hidden nodes
      removedEntities.forEach(entity => {
        const item = document.createElement("div");
        item.className = "removed-item";
        item.innerHTML = `
          <div><strong>${escapeHtml(entity.short_title)}</strong></div>
          <div class="removed-item-number">${escapeHtml(entity.ref || "")}</div>
        `;
        item.addEventListener("dblclick", () => {
          hiddenNodes.delete(entity.id);
          saveViewerState();
          updateVisibilityFast();
          rerouteIncidentEdgesFromCurrentPositions(entity.id);
        });
        item.addEventListener("click", () => {
          showEntityDetails(entity);
        });
        removedNodesEl.appendChild(item);
      });
      typesetElement(removedNodesEl);
    }

    // ── Ancestor focus ───────────────────────────────────────────────────────

    function collectAncestors(nodeId) {
      const keep = new Set();
      const stack = [nodeId];
      while (stack.length) {
        const current = stack.pop();
        if (!current || keep.has(current)) continue;
        keep.add(current);
        for (const edge of incoming.get(current) || []) stack.push(edge.source);
      }
      return keep;
    }

    function syncToolbar() {
      const hasSelection = !!selectedNodeId && !isHiddenNode(selectedNodeId);
      const hasFocus = !!focusNodeId && !isHiddenNode(focusNodeId);
      // Focus toggle
      focusToggle.disabled = !hasSelection && !hasFocus;
      if ((!hasSelection && !hasFocus) || ancestorFocusMode === 0) {
        focusToggle.textContent = "Highlight ancestors";
        focusToggle.classList.remove("active");
      } else if (ancestorFocusMode === 1) {
        focusToggle.textContent = "Hide non-ancestors";
        focusToggle.classList.add("active");
      } else {
        focusToggle.textContent = "Show full graph";
        focusToggle.classList.add("active");
      }
      // Delete node
      deleteNodeBtn.disabled = !hasSelection;
    }

    // Keep syncFocusToggle as an alias so existing callsites still work
    function syncFocusToggle() { syncToolbar(); }

    function applyAncestorFocus() {
      syncFocusToggle();
      ancestorHiddenByFocus.clear();
      const active = ancestorFocusMode > 0 && focusNodeId && !isHiddenNode(focusNodeId);
      const keep = active ? collectAncestors(focusNodeId) : null;

      document.querySelectorAll(".graph-node").forEach(nodeEl => {
        const nodeId = nodeEl.dataset.nodeId;
        if (active && !keep.has(nodeId)) {
          if (ancestorFocusMode === 1) {
            nodeEl.style.opacity = "0.18";
            nodeEl.style.display = "";
          } else {
            nodeEl.style.display = "none";
            ancestorHiddenByFocus.add(nodeId);
          }
        } else {
          nodeEl.style.opacity = "1";
          nodeEl.style.display = isHiddenNode(nodeId) ? "none" : "";
        }
      });

      document.querySelectorAll(".edge-path").forEach(pathEl => {
        const src = pathEl.dataset.sourceNodeId;
        const dst = pathEl.dataset.targetNodeId;
        const edgeTypeHidden = hiddenEdgeTypes.has(String(pathEl.dataset.edgeType || "unknown"));
        const endpointHidden = isHiddenNode(src) || isHiddenNode(dst);
        if (edgeTypeHidden || endpointHidden) {
          pathEl.style.display = "none";
          pathEl.style.opacity = "";
        } else if (active && ancestorFocusMode === 2 && (ancestorHiddenByFocus.has(src) || ancestorHiddenByFocus.has(dst))) {
          pathEl.style.display = "none";
          pathEl.style.opacity = "";
        } else if (active && ancestorFocusMode === 1 && (!keep.has(src) || !keep.has(dst))) {
          pathEl.style.opacity = "0.08";
          pathEl.style.display = "";
        } else {
          pathEl.style.opacity = "0.96";
          pathEl.style.display = "";
        }
        syncArrowheadForPath(pathEl);
      });

      renderRemovedNodes();
    }

