    // ── Declarative edge projection ──────────────────────────────────────────

    function confidenceRank(value) {
      if (value === "Verified") return 3;
      if (value === "Likely") return 2;
      if (value === "Speculative") return 1;
      return 0;
    }

    function compositionTransitions(cause, leftType, rightType) {
      const key = JSON.stringify([cause, leftType, rightType]);
      return relationTransitions.get(key) || [];
    }

    function derivedProjectionEdge(sourceId, targetId, state) {
      const hiddenLabels = state.omittedNodes.map(id => entityMap.get(id)?.short_title || id);
      return {
        edge_id: `projection_${sourceId}_${targetId}_${state.type}`,
        source: sourceId,
        target: targetId,
        type: state.type,
        description: `Derived ${state.type} relationship across omitted nodes: ${hiddenLabels.join(" → ")}.`,
        confidence: "Likely",
        implicit: true,
        derived: true,
        metadata: {
          projection: {
            fidelity: state.fidelity,
            rule_ids: state.ruleIds,
            omitted_nodes: state.omittedNodes,
            omission_causes: state.causes,
            represented_edge_ids: state.representedEdges.map(edge => edge.edge_id),
            witness_path: state.witnessPath.concat(targetId),
            witnesses: [{
              canonical_edge_ids: state.representedEdges.map(edge => edge.edge_id),
              omitted_nodes: state.omittedNodes,
              omission_causes: state.causes,
              witness_path: state.witnessPath.concat(targetId),
              fidelity: state.fidelity,
              transitions: state.transitions,
            }],
          },
          represented_count: state.representedEdges.length,
          represented_edges: state.representedEdges.map(edge => ({
            edge_id: edge.edge_id,
            source: edge.source,
            target: edge.target,
            type: edge.type,
          })),
        },
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
        if ((existing.aggregate && edge.aggregate) || (existing.derived && edge.derived)) {
          const represented = [
            ...(existing.metadata?.represented_edges || []),
            ...(edge.metadata?.represented_edges || []),
          ];
          const uniqueRepresented = Array.from(
            new Map(represented.map(item => [String(item.edge_id), item])).values()
          );
          const witnesses = [
            ...(existing.metadata?.projection?.witnesses || []),
            ...(edge.metadata?.projection?.witnesses || []),
          ];
          const uniqueWitnesses = Array.from(new Map(witnesses.map(witness => [
            JSON.stringify(witness.canonical_edge_ids || witness.witness_path || []), witness,
          ])).values());
          existing.metadata = {
            ...(existing.metadata || {}),
            represented_count: uniqueRepresented.length,
            represented_edges: uniqueRepresented,
            ...(existing.derived ? {projection: {
              ...(existing.metadata?.projection || {}),
              fidelity: uniqueWitnesses.some(witness => witness.fidelity === "exact") ? "exact" : "degraded",
              witnesses: uniqueWitnesses,
            }} : {}),
          };
          return;
        }
        const existingScore = (existing.derived ? 0 : 10) + confidenceRank(existing.confidence);
        const newScore = (edge.derived ? 0 : 10) + confidenceRank(edge.confidence);
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
          derived: false,
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
      function traverse(sourceId, currentId, state, seenStates) {
        if (currentId === sourceId) return;
        if (!entityMap.has(currentId)) return;
        if (!isHiddenNode(currentId)) {
          if (state.omittedNodes.length === 0) {
            addRendered({...state.representedEdges[0], derived: false});
          } else {
            addRendered(derivedProjectionEdge(sourceId, currentId, state));
          }
          return;
        }
        const cause = nodeOmissionCause(currentId);
        if (!cause) return;
        const stateKey = JSON.stringify([currentId, state.type, state.fidelity]);
        if (seenStates.has(stateKey)) return;
        const nextSeen = new Set(seenStates);
        nextSeen.add(stateKey);
        const nextEdges = (outgoing.get(currentId) || []).filter(edge => !isHiddenEdgeType(edge));
        if (nextEdges.length === 0) return;
        for (const outEdge of nextEdges) {
          const transitions = compositionTransitions(cause, state.type, String(outEdge.type));
          for (const transition of transitions) {
            if (outEdge.target === sourceId) continue;
            const fidelity = state.fidelity === "degraded" || transition.fidelity === "degraded"
              ? "degraded"
              : "exact";
            traverse(sourceId, outEdge.target, {
              type: transition.resultType,
              omittedNodes: state.omittedNodes.concat(currentId),
              causes: state.causes.concat(cause),
              representedEdges: state.representedEdges.concat(outEdge),
              witnessPath: state.witnessPath.concat(currentId),
              ruleIds: state.ruleIds.concat(transition.ruleId),
              fidelity,
              transitions: state.transitions.concat({
                rule_id: transition.ruleId,
                fidelity: transition.fidelity,
                left_type: state.type,
                right_type: String(outEdge.type),
                result_type: transition.resultType,
                omitted_node: currentId,
              }),
            }, nextSeen);
          }
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
          const canonicalTargetCause = nodeOmissionCause(outEdge.target);
          const declaredProjectionTarget = String(outEdge.projection_target || "");
          const projectionTargetCause = declaredProjectionTarget
            ? nodeOmissionCause(declaredProjectionTarget)
            : null;
          const followsProjectionTarget = Boolean(
            canonicalTargetCause &&
            projectionTargetCause &&
            entityMap.has(declaredProjectionTarget) &&
            declaredProjectionTarget !== outEdge.target
          );
          traverse(entity.id, followsProjectionTarget ? declaredProjectionTarget : outEdge.target, {
            type: String(outEdge.type),
            omittedNodes: followsProjectionTarget ? [outEdge.target] : [],
            causes: followsProjectionTarget ? [canonicalTargetCause] : [],
            representedEdges: [outEdge],
            witnessPath: followsProjectionTarget ? [entity.id, outEdge.target] : [entity.id],
            ruleIds: [],
            fidelity: "exact",
            transitions: [],
          }, new Set());
        }
      });
      const projected = Array.from(rendered.values()).filter(edge => !isHiddenEdgeType(edge));
      const byEndpoints = new Map();
      projected.forEach(edge => {
        const key = JSON.stringify([edge.source, edge.target]);
        if (!byEndpoints.has(key)) byEndpoints.set(key, []);
        byEndpoints.get(key).push(edge);
      });
      const retained = projected.filter(edge => {
        const peers = byEndpoints.get(JSON.stringify([edge.source, edge.target])) || [];
        const fidelityRank = candidate => candidate.derived && candidate.metadata?.projection?.fidelity === "degraded" ? 0 : 1;
        const dominator = peers.find(peer => {
          if (peer === edge) return false;
          const sameType = String(peer.type) === String(edge.type);
          const strongerType = (subsumedTypesByType.get(String(peer.type)) || new Set()).has(String(edge.type));
          const noWorseFidelity = fidelityRank(peer) >= fidelityRank(edge);
          const strict = strongerType || fidelityRank(peer) > fidelityRank(edge);
          return (sameType || strongerType) && noWorseFidelity && strict;
        });
        if (!dominator) return true;
        dominator.metadata = {
          ...(dominator.metadata || {}),
          suppressed_relationships: [
            ...(dominator.metadata?.suppressed_relationships || []),
            edge,
          ],
        };
        return false;
      });
      return bundleRenderedEdges(retained);
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
          colors: (overrideStyle && overrideStyle.colors) || fallback.colors,
        };
      }
      const style = typeStyles[nodeCategory(entity)] || { shape: "rect", color: "#566573" };
      return { shape: style.shape, color: style.color, colors: style.colors };
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

    function fitContainersAroundDescendants(visibleEntities) {
      const visibleIds = new Set(visibleEntities.map(entity => entity.id));
      const childrenByContainer = new Map();
      visibleEntities.forEach(entity => {
        const parentId = typeof entity.container === "string" ? entity.container.trim() : "";
        if (!parentId || !visibleIds.has(parentId)) return;
        const children = childrenByContainer.get(parentId) || [];
        children.push(entity.id);
        childrenByContainer.set(parentId, children);
      });

      const fitted = new Set();
      const fitting = new Set();
      function fit(containerId) {
        if (fitted.has(containerId) || fitting.has(containerId)) return;
        fitting.add(containerId);
        const childIds = childrenByContainer.get(containerId) || [];
        childIds.forEach(childId => fit(childId));
        const container = lastNodePositions.get(containerId);
        const children = childIds.map(childId => lastNodePositions.get(childId)).filter(Boolean);
        if (container && children.length) {
          const metrics = containerLayoutMetrics(containerId);
          const left = Math.min(...children.map(child => child.x - metrics.sidePadding));
          const top = Math.min(...children.map(child => child.y - metrics.headerPadding));
          const right = Math.max(...children.map(child => child.x + child.width + metrics.sidePadding));
          const bottom = Math.max(...children.map(child => child.y + child.height + metrics.bottomPadding));
          container.x = left;
          container.y = top;
          container.width = right - left;
          container.height = bottom - top;
        }
        fitting.delete(containerId);
        fitted.add(containerId);
      }

      childrenByContainer.forEach((_, containerId) => fit(containerId));
    }

    function buildContainmentNode(entityMap, childrenByContainer, entityId, visiting) {
      const entity = entityMap.get(entityId);
      if (!entity) {
        return null;
      }
      if (visiting.has(entityId)) {
        const fallbackDimensions = defaultNodeDimensions(entityId);
        return {
          id: entityId,
          width: fallbackDimensions.width,
          height: fallbackDimensions.height,
        };
      }
      visiting.add(entityId);

      const childIds = Array.from(childrenByContainer.get(entityId) || []).sort((leftId, rightId) => {
        const left = entityMap.get(leftId);
        const right = entityMap.get(rightId);
        const leftPosition = Number.isFinite(left?.position) ? left.position : Number.MAX_SAFE_INTEGER;
        const rightPosition = Number.isFinite(right?.position) ? right.position : Number.MAX_SAFE_INTEGER;
        return leftPosition - rightPosition || leftId.localeCompare(rightId);
      });
      const children = [];
      for (const childId of childIds) {
        const childNode = buildContainmentNode(entityMap, childrenByContainer, childId, visiting);
        if (childNode) {
          children.push(childNode);
        }
      }

      visiting.delete(entityId);
      const isContainer = children.length > 0;
      const dimensions = defaultNodeDimensions(entityId, {container: isContainer});
      const width = dimensions.width;
      const height = dimensions.height;

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
        .sort((left, right) => left.position - right.position || left.id.localeCompare(right.id))
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
