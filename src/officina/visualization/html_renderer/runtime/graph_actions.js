    // Atomic graph-state commands and explicit undo/redo history.

    const GRAPH_HISTORY_LIMIT = 100;
    const graphUndoStack = [];
    const graphRedoStack = [];
    let restoringGraphHistory = false;

    function sortedGraphSet(values) {
      return Array.from(values, String).sort();
    }

    function graphStateSnapshot() {
      return {
        hiddenNodes: sortedGraphSet(hiddenNodes),
        dimmedNodes: sortedGraphSet(dimmedNodes),
        selectedNodeIds: sortedGraphSet(selectedNodeIds),
        selectedNodeId,
        selectionSource,
        collapsedContainers: sortedGraphSet(collapsedContainers),
        detailLevel: filterState.detailLevel,
        excludedTypes: sortedGraphSet(filterState.excludedTypes),
        excludedKinds: sortedGraphSet(filterState.excludedKinds),
        excludedCategories: sortedGraphSet(filterState.excludedCategories),
        excludedEdgeTypes: sortedGraphSet(filterState.excludedEdgeTypes),
        presentationNodes: serializePresentationNodesState(),
      };
    }

    function graphStateKey(snapshot) {
      return JSON.stringify(snapshot);
    }

    function replaceGraphSet(target, values) {
      target.clear();
      (values || []).forEach(value => target.add(String(value)));
    }

    function validNodeIds(ids, {visibleOnly = false} = {}) {
      return Array.from(new Set(Array.from(ids || [], String))).filter(nodeId => (
        entityMap.has(nodeId) && (!visibleOnly || !isHiddenNode(nodeId))
      ));
    }

    function nodeIdsWithDescendants(ids) {
      const result = new Set(validNodeIds(ids));
      const stack = [...result];
      while (stack.length) {
        const parentId = stack.pop();
        docData.entities.forEach(entity => {
          if (parentByNode.get(entity.id) !== parentId || result.has(entity.id)) return;
          result.add(entity.id);
          stack.push(entity.id);
        });
      }
      return result;
    }

    function reconcileGraphState() {
      const effectivelyHidden = new Set(
        docData.entities.map(entity => entity.id).filter(isHiddenNode)
      );
      effectivelyHidden.forEach(nodeId => dimmedNodes.delete(nodeId));
      const remainingSelection = Array.from(selectedNodeIds).filter(
        nodeId => !effectivelyHidden.has(nodeId)
      );
      if (remainingSelection.length !== selectedNodeIds.size) {
        replaceNodeSelectionState(
          remainingSelection,
          effectivelyHidden.has(String(selectedNodeId)) ? remainingSelection.at(-1) || null : selectedNodeId,
          "explicit"
        );
      }
    }

    function renderGraphStateChange(renderMode, {preserveManualPositions = false} = {}) {
      rebuildRetainedOwners();
      syncSelectionPresentation();
      showSelectionDetails();
      if (renderMode === "full") {
        if (!preserveManualPositions) manualPositions.clear();
        hasFittedOnce = false;
        updateVisibilityFull();
      } else if (renderMode === "visibility") {
        updateVisibilityFast();
      } else {
        applyVisibilityPresentation();
      }
    }

    function syncGraphHistoryButtons() {
      visibilityUndoBtn.disabled = graphUndoStack.length === 0;
      visibilityRedoBtn.disabled = graphRedoStack.length === 0;
      visibilityUndoBtn.textContent = graphUndoStack.length ? `Undo (${graphUndoStack.length})` : "Undo";
      visibilityRedoBtn.textContent = graphRedoStack.length ? `Redo (${graphRedoStack.length})` : "Redo";
    }

    function recordGraphHistory(before, after) {
      if (restoringGraphHistory || graphStateKey(before) === graphStateKey(after)) return;
      graphUndoStack.push({before, after});
      if (graphUndoStack.length > GRAPH_HISTORY_LIMIT) graphUndoStack.shift();
      graphRedoStack.length = 0;
      syncGraphHistoryButtons();
    }

    function runGraphAction(
      mutator,
      {renderMode = "visibility", history = true, persist = true} = {}
    ) {
      const before = graphStateSnapshot();
      mutator();
      reconcileGraphState();
      const after = graphStateSnapshot();
      if (graphStateKey(before) === graphStateKey(after)) return false;
      renderGraphStateChange(renderMode);
      if (persist) saveViewerState();
      if (history) recordGraphHistory(before, after);
      return true;
    }

    function hideNodes(ids) {
      const roots = validNodeIds(ids, {visibleOnly: true});
      if (!roots.length) return false;
      const affected = nodeIdsWithDescendants(roots);
      return runGraphAction(() => {
        roots.forEach(nodeId => hiddenNodes.add(nodeId));
        affected.forEach(nodeId => dimmedNodes.delete(nodeId));
      });
    }

    function showNodes(ids) {
      const normalized = validNodeIds(ids);
      return runGraphAction(() => {
        normalized.forEach(nodeId => hiddenNodes.delete(nodeId));
      });
    }

    function dimNodes(ids) {
      const normalized = validNodeIds(ids, {visibleOnly: true});
      return runGraphAction(
        () => normalized.forEach(nodeId => dimmedNodes.add(nodeId)),
        {renderMode: "presentation"}
      );
    }

    function undimNodes(ids) {
      const normalized = validNodeIds(ids);
      return runGraphAction(
        () => normalized.forEach(nodeId => dimmedNodes.delete(nodeId)),
        {renderMode: "presentation"}
      );
    }

    function toggleDimNodes(ids) {
      const normalized = validNodeIds(ids, {visibleOnly: true});
      const shouldUndim = normalized.length > 0 && normalized.every(nodeId => dimmedNodes.has(nodeId));
      return shouldUndim ? undimNodes(normalized) : dimNodes(normalized);
    }

    function toggleContainerCollapsed(nodeId) {
      const normalized = String(nodeId);
      if (!entityMap.has(normalized) || !isContainerNode(normalized)) return false;
      return runGraphAction(() => {
        if (collapsedContainers.has(normalized)) collapsedContainers.delete(normalized);
        else collapsedContainers.add(normalized);
      }, {renderMode: "full"});
    }

    function restoreGraphSnapshot(snapshot) {
      const groupingBefore = serializePresentationNodesState();
      const groupingAfter = snapshot.presentationNodes || groupingBefore;
      const groupingLayoutKey = state => JSON.stringify({
        activeFacets: state.activeFacets || {},
        selectedNodeIds: state.selectedNodeIds || {},
        offsets: state.offsets || [],
        selfOffsets: state.selfOffsets || [],
      });
      const groupingLayoutChanged = groupingLayoutKey(groupingBefore) !== groupingLayoutKey(groupingAfter);
      const requiresLayout = (
        filterState.detailLevel !== snapshot.detailLevel ||
        groupingLayoutChanged ||
        graphStateKey({collapsed: sortedGraphSet(collapsedContainers)}) !==
          graphStateKey({collapsed: snapshot.collapsedContainers || []})
      );
      restoringGraphHistory = true;
      replaceGraphSet(hiddenNodes, snapshot.hiddenNodes);
      replaceGraphSet(dimmedNodes, snapshot.dimmedNodes);
      replaceGraphSet(collapsedContainers, snapshot.collapsedContainers);
      filterState.detailLevel = detailLevelRank.has(String(snapshot.detailLevel))
        ? String(snapshot.detailLevel)
        : defaultDetailLevel;
      replaceGraphSet(filterState.excludedTypes, snapshot.excludedTypes);
      replaceGraphSet(filterState.excludedKinds, snapshot.excludedKinds);
      replaceGraphSet(filterState.excludedCategories, snapshot.excludedCategories);
      replaceGraphSet(filterState.excludedEdgeTypes, snapshot.excludedEdgeTypes);
      restorePresentationNodesState(snapshot.presentationNodes);
      refreshPresentationNodesControls();
      replaceNodeSelectionState(
        (snapshot.selectedNodeIds || []).filter(nodeId => entityMap.has(String(nodeId))),
        snapshot.selectedNodeId,
        snapshot.selectionSource
      );
      filterUndoStack.length = 0;
      filterRedoStack.length = 0;
      searchEditStartSnapshot = null;
      reconcileGraphState();
      refreshFilterControls();
      syncLegendRows();
      renderGraphStateChange(
        requiresLayout ? "full" : "visibility",
        {preserveManualPositions: groupingLayoutChanged}
      );
      if (!requiresLayout) renderPresentationNodes();
      saveViewerState();
      restoringGraphHistory = false;
      syncGraphHistoryButtons();
    }

    function undoGraphAction() {
      const action = graphUndoStack.pop();
      if (!action) return;
      graphRedoStack.push(action);
      restoreGraphSnapshot(action.before);
    }

    function redoGraphAction() {
      const action = graphRedoStack.pop();
      if (!action) return;
      graphUndoStack.push(action);
      restoreGraphSnapshot(action.after);
    }

    document.addEventListener("keydown", event => {
      const tag = document.activeElement?.tagName?.toLowerCase();
      const editable = ["input", "textarea", "select"].includes(tag) || document.activeElement?.isContentEditable;
      if (editable || !(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      const undo = key === "z" && !event.shiftKey;
      const redo = (event.ctrlKey && key === "y") || (key === "z" && event.shiftKey);
      if (!undo && !redo) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (undo) undoGraphAction();
      else redoGraphAction();
    }, true);

    visibilityUndoBtn.addEventListener("click", undoGraphAction);
    visibilityRedoBtn.addEventListener("click", redoGraphAction);
    syncGraphHistoryButtons();
