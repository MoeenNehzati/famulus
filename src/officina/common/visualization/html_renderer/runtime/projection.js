    // ── Bridge edge helpers ──────────────────────────────────────────────────

    function confidenceRank(value) {
      if (value === "Verified") return 3;
      if (value === "Likely") return 2;
      if (value === "Speculative") return 1;
      return 0;
    }

    function bridgeEdge(sourceId, targetId, hiddenPath, seedEdge) {
      const hiddenLabels = hiddenPath.map(id => entityMap.get(id)?.short_title || id);
      return {
        edge_id: `bridge_${sourceId}_${targetId}_${hiddenPath.join("_") || "direct"}`,
        source: sourceId,
        target: targetId,
        type: seedEdge.type,
        description: `Derived ${seedEdge.type} path across hidden nodes: ${hiddenLabels.join(" → ")}.`,
        confidence: seedEdge?.confidence || "Likely",
        evidence: `Bridge path: ${hiddenLabels.join(" → ")}`,
        bridge: true
      };
    }

    function edgeConstituents(edge) {
      return edge?.bundle && Array.isArray(edge.constituent_edges)
        ? edge.constituent_edges
        : edge ? [edge] : [];
    }

    function edgeSuppressedByCategorySet(edge, excludedTypes) {
      const constituents = edgeConstituents(edge);
      return constituents.length > 0 && constituents.every(constituent =>
        edgeCategorySetContains(String(constituent.type || "unknown"), excludedTypes)
      );
    }

    function bundleRenderedEdges(edges) {
      const groups = new Map();
      edges.forEach(edge => {
        const key = JSON.stringify([edge.source, edge.target]);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(edge);
      });
      return Array.from(groups.values()).map(group => {
        if (group.length === 1) return group[0];
        const types = Array.from(new Set(group.map(edge => String(edge.type || "unknown"))));
        const representedEdges = group.flatMap(edge => {
          const represented = edge.metadata?.represented_edges;
          return Array.isArray(represented) && represented.length
            ? represented
            : [{
                edge_id: edge.edge_id,
                source: edge.source,
                target: edge.target,
                type: edge.type,
              }];
        });
        const first = group[0];
        return {
          ...first,
          edge_id: `bundle_${first.edge_id}`,
          type: types.length === 1 ? types[0] : "relationship-bundle",
          label: `${group.length} relationships`,
          description: `${group.length} visible relationships share these endpoints.`,
          details: undefined,
          confidence: undefined,
          aggregate: false,
          bundle: true,
          bundle_types: types,
          constituent_edges: group,
          metadata: {
            represented_count: representedEdges.length,
            represented_edges: representedEdges,
          },
        };
      });
    }

    function computeVisibleEdges() {
      const rendered = new Map();
      function addRendered(edge) {
        const key = edge.aggregate
          ? `aggregate::${edge.source}->${edge.target}::${edge.type || "unknown"}`
          : String(edge.edge_id);
        const existing = rendered.get(key);
        if (!existing) { rendered.set(key, edge); return; }
        if (existing.aggregate && edge.aggregate) {
          const represented = [
            ...(existing.metadata?.represented_edges || []),
            ...(edge.metadata?.represented_edges || []),
          ];
          existing.metadata = {
            ...(existing.metadata || {}),
            represented_count: represented.length,
            represented_edges: represented,
          };
          return;
        }
        const existingScore = (existing.bridge ? 0 : 10) + confidenceRank(existing.confidence);
        const newScore = (edge.bridge ? 0 : 10) + confidenceRank(edge.confidence);
        if (newScore > existingScore) rendered.set(key, edge);
      }
      function collapsedRepresentative(nodeId) {
        let current = nodeId;
        let representative = nodeId;
        const seen = new Set();
        if (nodeHiddenByDetailLevel(nodeId)) {
          while (parentByNode.has(current) && !seen.has(current)) {
            seen.add(current);
            current = parentByNode.get(current);
            if (!nodeHiddenByDetailLevel(current)) {
              representative = current;
              break;
            }
          }
          seen.clear();
          current = nodeId;
        }
        while (parentByNode.has(current) && !seen.has(current)) {
          seen.add(current);
          const parent = parentByNode.get(current);
          if (collapsedContainers.has(parent)) representative = parent;
          current = parent;
        }
        return representative;
      }
      for (const edge of edgeData) {
        if (isHiddenEdgeType(edge)) continue;
        const source = collapsedRepresentative(edge.source);
        const target = collapsedRepresentative(edge.target);
        if (source === edge.source && target === edge.target) continue;
        if (source === target || isHiddenNode(source) || isHiddenNode(target)) continue;
        addRendered({
          ...edge,
          edge_id: `aggregate_${source}_${target}_${edge.type}_${edge.edge_id}`,
          source,
          target,
          aggregate: true,
          bridge: false,
          implicit: true,
          description: "Aggregated relationship between visible structural representatives.",
          metadata: {
            ...(edge.metadata || {}),
            represented_count: 1,
            represented_edges: [{
              source: edge.source,
              target: edge.target,
              type: edge.type,
              edge_id: edge.edge_id,
            }],
          },
        });
      }
      function traverse(sourceId, currentId, seedEdge, hiddenPath, seenHidden) {
        if (currentId === sourceId) return;
        if (!entityMap.has(currentId)) return;
        if (!isHiddenNode(currentId)) {
          if (hiddenPath.length === 0 && seedEdge && sourceId === seedEdge.source && currentId === seedEdge.target) {
            addRendered({...seedEdge, bridge: false});
          } else {
            addRendered(bridgeEdge(sourceId, currentId, hiddenPath, seedEdge));
          }
          return;
        }
        if (nodeHiddenByDetailLevel(currentId)) return;
        if (edgeCategoryCatalog.get(String(seedEdge?.type || ""))?.bridge_hidden_nodes !== true) return;
        if (seenHidden.has(currentId)) return;
        const nextSeen = new Set(seenHidden);
        nextSeen.add(currentId);
        const nextEdges = (outgoing.get(currentId) || []).filter(edge =>
          !isHiddenEdgeType(edge) && String(edge.type) === String(seedEdge.type)
        );
        if (nextEdges.length === 0) return;
        for (const outEdge of nextEdges) {
          traverse(sourceId, outEdge.target, seedEdge || outEdge, hiddenPath.concat(currentId), nextSeen);
        }
      }
      docData.entities.forEach(entity => {
        if (isHiddenNode(entity.id)) return;
        for (const outEdge of outgoing.get(entity.id) || []) {
          if (isHiddenEdgeType(outEdge)) continue;
          if (
            collapsedRepresentative(outEdge.source) !== outEdge.source ||
            collapsedRepresentative(outEdge.target) !== outEdge.target
          ) continue;
          traverse(entity.id, outEdge.target, outEdge, [], new Set());
        }
      });
      return bundleRenderedEdges(Array.from(rendered.values()));
    }

    function edgeColorForTarget(targetId) {
      const idx = nodeColorIndex.get(targetId) || 0;
      return edgePalette[idx % edgePalette.length];
    }

    function edgeStyleForType(edgeType) {
      const style = edgeType ? edgeStyleCatalog.get(String(edgeType)) : null;
      if (!style) return null;
      return style;
    }

    function nodeCategory(entity) {
      return String(entity.category || entity.type || "unknown");
    }

    function nodeStyle(entity) {
      if (entity.type === "corollary" && renderTypeOverrides[entity.id] && renderTypeOverrides[entity.id] !== "corollary") {
        const overrideType = renderTypeOverrides[entity.id];
        const overrideEntity = entityMap.get(overrideType);
        const overrideStyle = overrideEntity ? typeStyles[nodeCategory(overrideEntity)] : null;
        const fallback = typeStyles.corollary || { shape: "rect", color: "#566573" };
        return {
          shape: (overrideStyle && overrideStyle.shape) || fallback.shape || "rect",
          color: (overrideStyle && overrideStyle.color) || fallback.color || "#566573",
        };
      }
      const style = typeStyles[nodeCategory(entity)] || { shape: "rect", color: "#566573" };
      return { shape: style.shape, color: style.color };
    }

    function flattenLayoutNodes(nodes, offsetX, offsetY, out) {
      if (!nodes) {
        return;
      }
      (nodes || []).forEach((node) => {
        const positioned = {
          ...node,
          x: (node.x || 0) + (offsetX || 0),
          y: (node.y || 0) + (offsetY || 0),
        };
        out.push(positioned);
        if (node.children && node.children.length > 0) {
          flattenLayoutNodes(node.children, positioned.x, positioned.y, out);
        }
      });
    }

    function enforceContainmentLayout(visibleEntities) {
      const childrenByContainer = new Map();
      const visibleIds = new Set(visibleEntities.map(entity => entity.id));
      visibleEntities.forEach(entity => {
        const containerId = typeof entity.container === "string" ? entity.container.trim() : "";
        if (!containerId || !visibleIds.has(containerId)) return;
        const list = childrenByContainer.get(containerId) || [];
        list.push(entity.id);
        childrenByContainer.set(containerId, list);
      });

      const CHILDBOX_W = 210;
      const CHILDBOX_H = 68;
      const X_PAD = 14;
      const Y_PAD = 14;
      const HEADER_H = 52;
      const COL_GAP = 14;
      const ROW_GAP = 14;
      const measured = new Map();

      function measure(entityId, visiting = new Set()) {
        if (measured.has(entityId)) return measured.get(entityId);
        if (visiting.has(entityId)) return {width: CHILDBOX_W, height: CHILDBOX_H};
        const nextVisiting = new Set(visiting);
        nextVisiting.add(entityId);
        const children = Array.from(new Set(childrenByContainer.get(entityId) || [])).sort();
        if (!children.length) {
          const leaf = {width: CHILDBOX_W, height: CHILDBOX_H, children: [], columns: 0, colWidths: [], rowHeights: []};
          measured.set(entityId, leaf);
          return leaf;
        }
        const columns = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(children.length))));
        const rows = Math.ceil(children.length / columns);
        const childMeasures = children.map(childId => measure(childId, nextVisiting));
        const colWidths = Array(columns).fill(0);
        const rowHeights = Array(rows).fill(0);
        childMeasures.forEach((size, index) => {
          const column = index % columns;
          const row = Math.floor(index / columns);
          colWidths[column] = Math.max(colWidths[column], size.width);
          rowHeights[row] = Math.max(rowHeights[row], size.height);
        });
        const result = {
          width: Math.max(240, X_PAD * 2 + colWidths.reduce((a, b) => a + b, 0) + COL_GAP * Math.max(0, columns - 1)),
          height: HEADER_H + Y_PAD * 2 + rowHeights.reduce((a, b) => a + b, 0) + ROW_GAP * Math.max(0, rows - 1),
          children,
          columns,
          colWidths,
          rowHeights,
        };
        measured.set(entityId, result);
        return result;
      }

      function place(entityId, x, y) {
        const size = measure(entityId);
        const position = lastNodePositions.get(entityId) || {};
        position.x = x;
        position.y = y;
        position.width = size.width;
        position.height = size.height;
        lastNodePositions.set(entityId, position);
        if (!size.children.length) return;
        const columnOffsets = [];
        let nextX = x + X_PAD;
        size.colWidths.forEach(width => {
          columnOffsets.push(nextX);
          nextX += width + COL_GAP;
        });
        const rowOffsets = [];
        let nextY = y + HEADER_H + Y_PAD;
        size.rowHeights.forEach(height => {
          rowOffsets.push(nextY);
          nextY += height + ROW_GAP;
        });
        size.children.forEach((childId, index) => {
          place(childId, columnOffsets[index % size.columns], rowOffsets[Math.floor(index / size.columns)]);
        });
      }

      const roots = visibleEntities
        .filter(entity => !parentByNode.has(entity.id) || !visibleIds.has(parentByNode.get(entity.id)))
        .map(entity => entity.id);
      roots.forEach(rootId => {
        const position = lastNodePositions.get(rootId) || {x: 0, y: 0};
        place(rootId, position.x || 0, position.y || 0);
      });
    }

    function buildContainmentNode(entityMap, childrenByContainer, entityId, visiting) {
      const entity = entityMap.get(entityId);
      if (!entity) {
        return null;
      }
      if (visiting.has(entityId)) {
        return {
          id: entityId,
          width: 210,
          height: 68,
        };
      }
      visiting.add(entityId);

      const childIds = Array.from(childrenByContainer.get(entityId) || []).sort();
      const children = [];
      for (const childId of childIds) {
        const childNode = buildContainmentNode(entityMap, childrenByContainer, childId, visiting);
        if (childNode) {
          children.push(childNode);
        }
      }

      visiting.delete(entityId);
      const isContainer = children.length > 0;
      const width = isContainer ? Math.max(220, 210 + Math.min(children.length, 6) * 16) : 210;
      const height = isContainer ? Math.max(110, 70 + children.length * 70) : 68;

      const node = {
        id: entityId,
        width,
        height,
      };
      if (children.length > 0) {
        node.children = children;
      }
      return node;
    }

    function buildContainmentGraph(visibleEntities) {
      const entityMap = new Map(visibleEntities.map((entity) => [entity.id, entity]));
      const visibleIds = new Set(entityMap.keys());
      const childrenByContainer = new Map();
      const hasContainer = new Set();

      const registerChild = (parentId, childId) => {
        if (parentId === childId || !visibleIds.has(parentId) || !visibleIds.has(childId)) {
          return;
        }
        const childSet = childrenByContainer.get(parentId) || new Set();
        if (!childrenByContainer.has(parentId)) {
          childrenByContainer.set(parentId, childSet);
        }
        if (!childSet.has(childId)) {
          childSet.add(childId);
          hasContainer.add(childId);
        }
      };

      for (const entity of visibleEntities) {
        if (Array.isArray(entity.children)) {
          for (const childId of entity.children) {
            registerChild(entity.id, String(childId).trim());
          }
        }
        if (typeof entity.container === "string" && entity.container.trim()) {
          registerChild(entity.container.trim(), entity.id);
        }
      }

      const roots = visibleEntities
        .filter((entity) => !hasContainer.has(entity.id))
        .map((entity) => entity.id);

      const seen = new Set();
      const graphNodes = [];
      const visiting = new Set();

      for (const rootId of roots) {
        const built = buildContainmentNode(entityMap, childrenByContainer, rootId, visiting);
        if (built && !seen.has(built.id)) {
          graphNodes.push(built);
          seen.add(built.id);
        }
      }

      const missing = visibleEntities.filter((entity) => !seen.has(entity.id));
      for (const entity of missing) {
        const built = buildContainmentNode(entityMap, childrenByContainer, entity.id, visiting);
        if (built) {
          graphNodes.push(built);
          seen.add(built.id);
        }
      }

      return graphNodes;
    }
