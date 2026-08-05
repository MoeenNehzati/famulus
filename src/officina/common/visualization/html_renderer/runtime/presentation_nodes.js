    // Generic first-class presentation nodes. A logical node can render as
    // several shell components when overlapping memberships split the canvas.
    const presentationNodeById = new Map(
      (docData.presentation_nodes || []).map(node => [String(node.id), node])
    );
    const presentationNodeControls = (docData.ui?.presentation_node_controls || [])
      .filter(control => control && Array.isArray(control.facets));
    const presentationFacetById = new Map();
    const presentationControlByFacet = new Map();
    presentationNodeControls.forEach(control => {
      (control.facets || []).forEach(facet => {
        presentationFacetById.set(String(facet.id), facet);
        presentationControlByFacet.set(String(facet.id), String(control.id));
      });
    });

    const activePresentationFacets = new Map(
      presentationNodeControls.map(control => {
        const configured = String(control.default_facet || "");
        const allowed = new Set((control.facets || []).map(facet => String(facet.id)));
        return [String(control.id), allowed.has(configured) ? configured : null];
      })
    );
    const selectedPresentationNodeIds = new Map(
      Array.from(presentationFacetById, ([facetId]) => [facetId, new Set()])
    );
    const hiddenPresentationNodes = new Set();
    const collapsedPresentationNodes = new Set();
    const presentationNodeOffsets = new Map();
    const presentationSelfOffsets = new Map();
    let selectedPresentationNodeId = null;
    let presentationNodeBounds = [];
    let presentationNodeComponents = [];
    let presentationGroupedNodeIds = new Set();
    let presentationRestoringManualPositions = false;
    let committedPresentationNodesState = null;
    let pendingPresentationHistoryBefore = null;
    const presentationControlElements = new Map();

    function snapshotPresentationNodesRenderState() {
      return {
        bounds: presentationNodeBounds,
        components: presentationNodeComponents,
        groupedNodeIds: presentationGroupedNodeIds,
        restoringManualPositions: presentationRestoringManualPositions,
      };
    }

    function restorePresentationNodesRenderState(snapshot) {
      presentationNodeBounds = snapshot.bounds;
      presentationNodeComponents = snapshot.components;
      presentationGroupedNodeIds = snapshot.groupedNodeIds;
      presentationRestoringManualPositions = snapshot.restoringManualPositions;
    }

    function activePresentationNodes() {
      const result = [];
      const controlled = new Set(
        presentationNodeControls.flatMap(control =>
          (control.facets || []).flatMap(facet => (facet.node_ids || []).map(String))
        )
      );
      presentationNodeById.forEach(node => {
        if (!controlled.has(String(node.id)) && node.presentation?.default_visibility === "visible") {
          result.push({...node, facetLabel: String(node.type || "Presentation")});
        }
      });
      presentationNodeControls.forEach(control => {
        const facetId = activePresentationFacets.get(String(control.id));
        const facet = presentationFacetById.get(String(facetId));
        if (!facet) return;
        const selected = selectedPresentationNodeIds.get(String(facet.id)) || new Set();
        (facet.node_ids || []).map(String).forEach(nodeId => {
          if (facet.activation === "multiple" && !selected.has(nodeId)) return;
          const node = presentationNodeById.get(nodeId);
          if (node) result.push({...node, facetLabel: String(facet.label || facet.id)});
        });
      });
      return result;
    }

    function renderedSubtree(rootId, renderedIds) {
      const result = [];
      const stack = [String(rootId)];
      while (stack.length) {
        const nodeId = stack.pop();
        if (!renderedIds.has(nodeId)) continue;
        result.push(nodeId);
        docData.entities.forEach(entity => {
          if (parentByNode.get(entity.id) === nodeId) stack.push(entity.id);
        });
      }
      return result;
    }

    function boundsForNodeIds(nodeIds) {
      const positions = nodeIds.map(nodeId => lastNodePositions.get(nodeId)).filter(Boolean);
      if (!positions.length) return null;
      const x = Math.min(...positions.map(position => position.x));
      const y = Math.min(...positions.map(position => position.y));
      const right = Math.max(...positions.map(position => position.x + position.width));
      const bottom = Math.max(...positions.map(position => position.y + position.height));
      return {x, y, width: right - x, height: bottom - y};
    }

    function moveRenderedSubtree(rootId, renderedIds, dx, dy) {
      renderedSubtree(rootId, renderedIds).forEach(nodeId => {
        const position = lastNodePositions.get(nodeId);
        if (position) lastNodePositions.set(nodeId, {...position, x: position.x + dx, y: position.y + dy});
      });
    }

    function applyPresentationNodesLayout(renderedEntities) {
      const previouslyGrouped = presentationGroupedNodeIds.size > 0;
      presentationGroupedNodeIds = new Set();
      presentationRestoringManualPositions = false;
      presentationNodeBounds = [];
      presentationNodeComponents = [];
      const nodes = activePresentationNodes();
      if (!nodes.length) {
        presentationRestoringManualPositions = previouslyGrouped;
        return;
      }

      const renderedIds = new Set(renderedEntities.map(entity => String(entity.id)));
      const eligibleRootIds = renderedEntities
        .filter(entity => !parentByNode.has(String(entity.id)))
        .map(entity => String(entity.id));
      const memberSets = nodes.map(node => new Set((node.member_ids || []).map(String)));
      const blocks = new Map();
      const ownerByNode = new Map();
      eligibleRootIds.forEach(rootId => {
        const nodeIds = renderedSubtree(rootId, renderedIds);
        nodeIds.forEach(nodeId => {
          if (ownerByNode.has(nodeId)) throw new Error(`presentation roots overlap at ${nodeId}`);
          ownerByNode.set(nodeId, rootId);
        });
        const bounds = boundsForNodeIds(nodeIds);
        if (bounds) blocks.set(rootId, {rootId, nodeIds, bounds});
      });
      if (!blocks.size) return;
      presentationGroupedNodeIds = new Set(ownerByNode.keys());

      const emptySignature = "0".repeat(nodes.length);
      const compartmentsBySignature = new Map();
      blocks.forEach(block => {
        const signature = memberSets.map(members => members.has(block.rootId) ? "1" : "0").join("");
        if (!compartmentsBySignature.has(signature)) compartmentsBySignature.set(signature, []);
        compartmentsBySignature.get(signature).push(block);
      });
      const entityOrder = new Map(docData.entities.map(entity => [String(entity.id), Number(entity.position || 0)]));
      const signatures = Array.from(compartmentsBySignature).sort(([left], [right]) => {
        if (left === emptySignature) return 1;
        if (right === emptySignature) return -1;
        return left.localeCompare(right);
      });
      const gap = 32;
      const padding = 46;
      const header = 58;
      const aspectRatio = Number(docData.ui?.layout?.aspect_ratio) || 1.6;
      const compartments = signatures.map(([signature, signatureBlocks]) => {
        signatureBlocks.sort((left, right) =>
          (entityOrder.get(left.rootId) || 0) - (entityOrder.get(right.rootId) || 0) ||
          left.rootId.localeCompare(right.rootId)
        );
        const columns = Math.max(1, Math.ceil(Math.sqrt(signatureBlocks.length * aspectRatio)));
        const rows = Math.ceil(signatureBlocks.length / columns);
        const columnWidths = Array(columns).fill(0);
        const rowHeights = Array(rows).fill(0);
        signatureBlocks.forEach((block, index) => {
          const column = index % columns;
          const row = Math.floor(index / columns);
          columnWidths[column] = Math.max(columnWidths[column], block.bounds.width);
          rowHeights[row] = Math.max(rowHeights[row], block.bounds.height);
        });
        const columnX = columnWidths.map((_, index) =>
          padding + columnWidths.slice(0, index).reduce((sum, width) => sum + width + gap, 0)
        );
        const rowY = rowHeights.map((_, index) =>
          header + rowHeights.slice(0, index).reduce((sum, height) => sum + height + gap, 0)
        );
        return {
          signature,
          placements: signatureBlocks.map((block, index) => ({
            block,
            x: columnX[index % columns],
            y: rowY[Math.floor(index / columns)],
          })),
          width: padding * 2 + columnWidths.reduce((sum, width) => sum + width, 0) + gap * Math.max(0, columns - 1),
          height: header + padding + rowHeights.reduce((sum, height) => sum + height, 0) + gap * Math.max(0, rows - 1),
        };
      });

      const columns = Math.max(1, Math.ceil(Math.sqrt(compartments.length * aspectRatio)));
      const columnWidths = Array(columns).fill(0);
      const rowHeights = Array(Math.ceil(compartments.length / columns)).fill(0);
      compartments.forEach((compartment, index) => {
        columnWidths[index % columns] = Math.max(columnWidths[index % columns], compartment.width);
        rowHeights[Math.floor(index / columns)] = Math.max(rowHeights[Math.floor(index / columns)], compartment.height);
      });
      const compartmentGap = gap * 3;
      const originX = columnWidths.map((_, index) =>
        54 + columnWidths.slice(0, index).reduce((sum, width) => sum + width + compartmentGap, 0)
      );
      const originY = rowHeights.map((_, index) =>
        54 + rowHeights.slice(0, index).reduce((sum, height) => sum + height + compartmentGap, 0)
      );
      compartments.forEach((compartment, compartmentIndex) => {
        const x = originX[compartmentIndex % columns];
        const y = originY[Math.floor(compartmentIndex / columns)];
        compartment.placements.forEach(placement => {
          const dx = x + placement.x - placement.block.bounds.x;
          const dy = y + placement.y - placement.block.bounds.y;
          placement.block.nodeIds.forEach(nodeId => {
            const position = lastNodePositions.get(nodeId);
            lastNodePositions.set(nodeId, {...position, x: position.x + dx, y: position.y + dy});
          });
        });
        nodes.forEach((node, nodeIndex) => {
          if (compartment.signature[nodeIndex] !== "1") return;
          const memberRootIds = compartment.placements
            .map(placement => placement.block.rootId)
            .filter(rootId => memberSets[nodeIndex].has(rootId));
          const inset = Math.min(nodeIndex * 20, 40);
          presentationNodeComponents.push({
            id: `${String(node.id)}::${compartment.signature}`,
            presentationNodeId: String(node.id),
            label: String(node.short_title || node.id),
            subtitle: node.facetLabel,
            colorIndex: Math.max(0, Number(node.position || nodeIndex)),
            tone: String(node.presentation?.tone || "subtle"),
            memberRootIds,
            inset,
            bounds: {
              x: x - inset, y: y - inset,
              width: compartment.width + inset * 2,
              height: compartment.height + inset * 2,
            },
          });
        });
      });

      // Persisted logical-node drags compose. If X belongs to A and B, moving A
      // moves X once and B's component is recomputed around its current members.
      nodes.forEach(node => {
        const offset = presentationNodeOffsets.get(String(node.id));
        if (!offset || (!offset.x && !offset.y)) return;
        (node.member_ids || []).map(String).forEach(rootId => {
          if (renderedIds.has(rootId)) moveRenderedSubtree(rootId, renderedIds, offset.x, offset.y);
        });
      });
      presentationNodeComponents.forEach(component => {
        const memberNodeIds = component.memberRootIds.flatMap(rootId => renderedSubtree(rootId, renderedIds));
        const memberBounds = boundsForNodeIds(memberNodeIds);
        if (memberBounds) component.bounds = {
          x: memberBounds.x - 46 - component.inset,
          y: memberBounds.y - 58 - component.inset,
          width: memberBounds.width + 92 + component.inset * 2,
          height: memberBounds.height + 104 + component.inset * 2,
        };
      });
      presentationNodeBounds = presentationNodeComponents
        .filter(component => !hiddenPresentationNodes.has(component.presentationNodeId))
        .map(component => component.bounds);
    }

    function showPresentationNodeDetails(nodeId) {
      const node = presentationNodeById.get(String(nodeId));
      if (!node || node.interaction?.inspectable === false) return;
      showEntityDetails(node);
    }

    function selectPresentationNode(nodeId) {
      const node = presentationNodeById.get(String(nodeId));
      if (!node || node.interaction?.selectable === false) return;
      const before = typeof graphStateSnapshot === "function" ? graphStateSnapshot() : null;
      selectedPresentationNodeId = String(nodeId);
      selectedNodeIds.clear(); selectedNodeId = null;
      renderPresentationNodes();
      syncSelectionPresentation();
      showPresentationNodeDetails(nodeId);
      saveViewerState();
      if (before && typeof recordGraphHistory === "function") {
        recordGraphHistory(before, graphStateSnapshot());
      }
    }

    function clearPresentationNodeSelection({render = true} = {}) {
      if (!selectedPresentationNodeId) return;
      selectedPresentationNodeId = null;
      if (render) renderPresentationNodes();
    }

    function reconcilePresentationNodeSelection() {
      if (!selectedPresentationNodeId) return;
      const activeIds = new Set(activePresentationNodes().map(node => String(node.id)));
      if (!activeIds.has(selectedPresentationNodeId) || hiddenPresentationNodes.has(selectedPresentationNodeId)) {
        clearPresentationNodeSelection();
        showSelectionDetails();
      }
    }

    function togglePresentationNodeHidden(nodeId) {
      const normalized = String(nodeId);
      if (!presentationNodeById.has(normalized)) return;
      const before = typeof graphStateSnapshot === "function" ? graphStateSnapshot() : null;
      if (hiddenPresentationNodes.has(normalized)) hiddenPresentationNodes.delete(normalized);
      else hiddenPresentationNodes.add(normalized);
      if (selectedPresentationNodeId === normalized && hiddenPresentationNodes.has(normalized)) selectedPresentationNodeId = null;
      refreshPresentationNodesControls();
      renderPresentationNodes();
      commitPresentationNodesState();
      saveViewerState();
      if (before && typeof recordGraphHistory === "function") recordGraphHistory(before, graphStateSnapshot());
    }

    function togglePresentationNodeCollapsed(nodeId) {
      const normalized = String(nodeId);
      const node = presentationNodeById.get(normalized);
      if (!node || node.interaction?.collapse_effect !== "self") return;
      const before = typeof graphStateSnapshot === "function" ? graphStateSnapshot() : null;
      if (collapsedPresentationNodes.has(normalized)) collapsedPresentationNodes.delete(normalized);
      else collapsedPresentationNodes.add(normalized);
      renderPresentationNodes();
      commitPresentationNodesState();
      saveViewerState();
      if (before && typeof recordGraphHistory === "function") recordGraphHistory(before, graphStateSnapshot());
    }

    function bindPresentationNodeInteractions(element, component) {
      const nodeId = component.presentationNodeId;
      const node = presentationNodeById.get(nodeId);
      if (!node) return;
      element.dataset.presentationNodeId = nodeId;
      element.setAttribute("aria-hidden", "false");
      element.setAttribute("pointer-events", "all");
      if (node.interaction?.selectable !== false) {
        element.setAttribute("tabindex", "0");
        element.setAttribute("role", "button");
      }
      element.addEventListener("click", event => {
        event.stopPropagation();
        selectPresentationNode(nodeId);
      });
      element.addEventListener("dblclick", event => {
        event.preventDefault(); event.stopPropagation();
        if (event.altKey) togglePresentationNodeCollapsed(nodeId);
        else togglePresentationNodeHidden(nodeId);
      });
      element.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault(); selectPresentationNode(nodeId);
        } else if (event.key === "Delete" || event.key === "Backspace") {
          event.preventDefault(); togglePresentationNodeHidden(nodeId);
        }
      });
      if (!["self", "members"].includes(node.interaction?.draggable)) return;
      element.addEventListener("mousedown", event => {
        if (event.button !== 0 || event.detail > 1) return;
        event.preventDefault(); event.stopPropagation();
        const startX = event.clientX;
        const startY = event.clientY;
        const offsetStore = node.interaction.draggable === "members"
          ? presentationNodeOffsets
          : presentationSelfOffsets;
        const initial = offsetStore.get(nodeId) || {x: 0, y: 0};
        const before = typeof graphStateSnapshot === "function" ? graphStateSnapshot() : null;
        const onMove = moveEvent => {
          const dx = (moveEvent.clientX - startX) / zoomLevel;
          const dy = (moveEvent.clientY - startY) / zoomLevel;
          document.querySelectorAll(`[data-presentation-node-id="${CSS.escape(nodeId)}"]`)
            .forEach(componentEl => componentEl.setAttribute("transform", `translate(${dx},${dy})`));
        };
        const onUp = async upEvent => {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          const dx = (upEvent.clientX - startX) / zoomLevel;
          const dy = (upEvent.clientY - startY) / zoomLevel;
          if (Math.hypot(dx, dy) < 2) return;
          offsetStore.set(nodeId, {x: initial.x + dx, y: initial.y + dy});
          if (node.interaction.draggable === "members") {
            await updateVisibilityFull({preserveManualPositions: true});
          } else {
            renderPresentationNodes();
          }
          commitPresentationNodesState(); saveViewerState();
          if (before && typeof recordGraphHistory === "function") {
            recordGraphHistory(before, graphStateSnapshot());
          }
        };
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
    }

    function presentationComponentDisplayBounds(component) {
      const collapsed = collapsedPresentationNodes.has(component.presentationNodeId);
      const base = collapsed
        ? {x: component.bounds.x, y: component.bounds.y, width: Math.min(220, component.bounds.width), height: 52}
        : component.bounds;
      const offset = presentationSelfOffsets.get(component.presentationNodeId) || {x: 0, y: 0};
      return {...base, x: base.x + offset.x, y: base.y + offset.y};
    }

    function renderPresentationNodes() {
      presentationNodeLayer.replaceChildren();
      const visibleComponents = presentationNodeComponents.filter(
        component => !hiddenPresentationNodes.has(component.presentationNodeId)
      );
      const renderedBounds = [];
      visibleComponents.forEach(component => {
        const color = fallbackColors[component.colorIndex % fallbackColors.length] || "#566573";
        const collapsed = collapsedPresentationNodes.has(component.presentationNodeId);
        const position = presentationComponentDisplayBounds(component);
        renderedBounds.push(position);
        const element = renderContainerShell({
          layer: presentationNodeLayer,
          id: component.id,
          label: component.label,
          subtitle: collapsed ? `${component.subtitle} · collapsed` : component.subtitle,
          position,
          style: {color, colors: [color]},
          tone: component.tone,
          className: `presentation-node-component${selectedPresentationNodeId === component.presentationNodeId ? " selected" : ""}${collapsed ? " collapsed" : ""}`,
        });
        bindPresentationNodeInteractions(element, component);
      });
      presentationNodeBounds = renderedBounds;
    }

    function serializePresentationNodesState() {
      return {
        activeFacets: Object.fromEntries(activePresentationFacets),
        selectedNodeIds: Object.fromEntries(
          Array.from(selectedPresentationNodeIds, ([facetId, values]) => [facetId, Array.from(values).sort()])
        ),
        hiddenNodes: Array.from(hiddenPresentationNodes).sort(),
        collapsedNodes: Array.from(collapsedPresentationNodes).sort(),
        offsets: Array.from(presentationNodeOffsets.entries()),
        selfOffsets: Array.from(presentationSelfOffsets.entries()),
        selectedNodeId: selectedPresentationNodeId,
      };
    }

    function commitPresentationNodesState() {
      committedPresentationNodesState = JSON.parse(JSON.stringify(serializePresentationNodesState()));
    }

    function rollbackPresentationNodesState() {
      restorePresentationNodesState(committedPresentationNodesState);
      refreshPresentationNodesControls();
    }

    function restorePresentationNodesState(payload) {
      presentationNodeControls.forEach(control => {
        const configured = String(control.default_facet || "");
        const allowed = new Set((control.facets || []).map(facet => String(facet.id)));
        activePresentationFacets.set(String(control.id), allowed.has(configured) ? configured : null);
      });
      selectedPresentationNodeIds.forEach(values => values.clear());
      hiddenPresentationNodes.clear(); collapsedPresentationNodes.clear();
      presentationNodeOffsets.clear(); presentationSelfOffsets.clear();
      selectedPresentationNodeId = null;
      if (!payload || typeof payload !== "object") return;
      // v6 migration: translate the former dimension/value view state.
      if (payload.activeDimension !== undefined) {
        const facetId = String(payload.activeDimension || "");
        const controlId = presentationControlByFacet.get(facetId);
        if (controlId) activePresentationFacets.set(controlId, facetId);
        Object.entries(payload.selectedValues || {}).forEach(([legacyFacetId, valueIds]) => {
          const allowed = new Set((presentationFacetById.get(legacyFacetId)?.node_ids || []).map(String));
          (Array.isArray(valueIds) ? valueIds : []).map(valueId => `${legacyFacetId}.${String(valueId)}`)
            .filter(nodeId => allowed.has(nodeId)).forEach(nodeId => selectedPresentationNodeIds.get(legacyFacetId)?.add(nodeId));
        });
        (payload.hiddenShells || []).map(String).forEach(shellId => {
          const separator = shellId.indexOf("::");
          const nodeId = separator >= 0 ? `${shellId.slice(0, separator)}.${shellId.slice(separator + 2)}` : shellId;
          if (presentationNodeById.has(nodeId)) hiddenPresentationNodes.add(nodeId);
        });
        return;
      }
      Object.entries(payload.activeFacets || {}).forEach(([controlId, facetId]) => {
        const control = presentationNodeControls.find(item => String(item.id) === controlId);
        if ((control?.facets || []).some(facet => String(facet.id) === String(facetId))) {
          activePresentationFacets.set(controlId, String(facetId));
        }
      });
      Object.entries(payload.selectedNodeIds || {}).forEach(([facetId, nodeIds]) => {
        const allowed = new Set((presentationFacetById.get(facetId)?.node_ids || []).map(String));
        (Array.isArray(nodeIds) ? nodeIds : []).map(String).filter(nodeId => allowed.has(nodeId))
          .forEach(nodeId => selectedPresentationNodeIds.get(facetId)?.add(nodeId));
      });
      (payload.hiddenNodes || []).map(String).filter(nodeId => presentationNodeById.has(nodeId)).forEach(nodeId => hiddenPresentationNodes.add(nodeId));
      (payload.collapsedNodes || []).map(String).filter(nodeId => presentationNodeById.has(nodeId)).forEach(nodeId => collapsedPresentationNodes.add(nodeId));
      (payload.offsets || []).forEach(([nodeId, offset]) => {
        if (presentationNodeById.has(String(nodeId)) && Number.isFinite(offset?.x) && Number.isFinite(offset?.y)) {
          presentationNodeOffsets.set(String(nodeId), {x: offset.x, y: offset.y});
        }
      });
      (payload.selfOffsets || []).forEach(([nodeId, offset]) => {
        if (presentationNodeById.has(String(nodeId)) && Number.isFinite(offset?.x) && Number.isFinite(offset?.y)) {
          presentationSelfOffsets.set(String(nodeId), {x: offset.x, y: offset.y});
        }
      });
      if (presentationNodeById.has(String(payload.selectedNodeId))) selectedPresentationNodeId = String(payload.selectedNodeId);
    }

    function clearPresentationNodesState() {
      restorePresentationNodesState(null);
      refreshPresentationNodesControls();
    }

    function refreshPresentationNodesControls() {
      presentationNodeControls.forEach(control => {
        const refs = presentationControlElements.get(String(control.id));
        if (!refs) return;
        const facetId = activePresentationFacets.get(String(control.id));
        refs.select.value = facetId || "";
        refs.values.replaceChildren();
        const facet = presentationFacetById.get(String(facetId));
        if (!facet) { refs.values.hidden = true; return; }
        refs.values.hidden = false;
        const choices = document.createElement(facet.activation === "multiple" ? "details" : "div");
        choices.className = "presentation-node-control-values";
        if (facet.activation === "multiple") {
          const summary = document.createElement("summary");
          const count = selectedPresentationNodeIds.get(String(facet.id))?.size || 0;
          summary.textContent = count ? `${count} selected` : `Choose ${String(facet.label).toLowerCase()}`;
          choices.appendChild(summary);
        }
        (facet.node_ids || []).map(String).forEach(nodeId => {
          const node = presentationNodeById.get(nodeId);
          if (!node) return;
          const row = document.createElement("label");
          row.className = "filter-option";
          row.dataset.presentationNode = nodeId;
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.checked = facet.activation === "multiple"
            ? selectedPresentationNodeIds.get(String(facet.id)).has(nodeId) &&
              !hiddenPresentationNodes.has(nodeId)
            : !hiddenPresentationNodes.has(nodeId);
          checkbox.addEventListener("change", () => mutatePresentationNodes(() => {
            if (facet.activation === "multiple") {
              const selected = selectedPresentationNodeIds.get(String(facet.id));
              if (checkbox.checked) {
                selected.add(nodeId);
                hiddenPresentationNodes.delete(nodeId);
              } else {
                selected.delete(nodeId);
              }
            } else if (checkbox.checked) hiddenPresentationNodes.delete(nodeId);
            else hiddenPresentationNodes.add(nodeId);
          }, {layout: facet.activation === "multiple"}));
          row.append(checkbox, document.createTextNode(String(node.short_title || node.id)));
          choices.appendChild(row);
        });
        refs.values.appendChild(choices);
      });
    }

    function mutatePresentationNodes(mutator, {layout = true} = {}) {
      const before = typeof graphStateSnapshot === "function" ? graphStateSnapshot() : null;
      mutator();
      reconcilePresentationNodeSelection();
      refreshPresentationNodesControls();
      if (layout) {
        if (!pendingPresentationHistoryBefore) pendingPresentationHistoryBefore = before;
        Promise.resolve(updateVisibilityFull({preserveManualPositions: true})).then(result => {
          if (result === null) return;
          saveViewerState();
          if (result === true && pendingPresentationHistoryBefore && typeof recordGraphHistory === "function") {
            recordGraphHistory(pendingPresentationHistoryBefore, graphStateSnapshot());
          }
          pendingPresentationHistoryBefore = null;
        });
      } else {
        renderPresentationNodes(); commitPresentationNodesState(); saveViewerState();
        if (before && typeof recordGraphHistory === "function") recordGraphHistory(before, graphStateSnapshot());
      }
    }

    presentationNodeControls.forEach((control, controlIndex) => {
      const section = document.createElement("section");
      section.className = "sidebar-section presentation-node-control-panel";
      section.dataset.sectionId = `presentation-node-control-${String(control.id)}`;
      const selectId = controlIndex ? `presentation-node-control-dimension-${controlIndex}` : "presentation-node-control-dimension";
      const valuesId = controlIndex ? `presentation-node-control-values-${controlIndex}` : "presentation-node-control-values";
      section.innerHTML = `
        <div class="drag-handle" draggable="true" title="Drag to reorder">⠿</div>
        <h2 class="section-heading">${escapeHtml(control.label || control.id)}</h2>
        <div class="filter-search-row detail-level-row">
          <label for="${escapeHtml(selectId)}">${escapeHtml(control.selector_label || "View by")}</label>
          <select id="${escapeHtml(selectId)}" class="filter-mode"></select>
        </div>
        <div id="${escapeHtml(valuesId)}" class="filter-facet" hidden></div>`;
      panelContent.insertBefore(section, filterSection);
      const select = section.querySelector("select");
      const values = section.querySelector(".filter-facet");
      presentationControlElements.set(String(control.id), {select, values});
      const noneOption = document.createElement("option");
      noneOption.value = ""; noneOption.textContent = "None"; select.appendChild(noneOption);
      (control.facets || []).forEach(facet => {
        const option = document.createElement("option");
        option.value = String(facet.id); option.textContent = String(facet.label || facet.id);
        select.appendChild(option);
      });
      select.addEventListener("change", () => mutatePresentationNodes(() => {
        const allowed = new Set((control.facets || []).map(facet => String(facet.id)));
        activePresentationFacets.set(String(control.id), allowed.has(select.value) ? select.value : null);
      }));
    });
    refreshPresentationNodesControls();
