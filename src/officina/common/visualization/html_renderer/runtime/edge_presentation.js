/*
 * Metadata-driven edge presentation for the generic graph viewer.
 *
 * Relation type answers what an edge means and remains the authority for its
 * ordinary color and dash. Representation metadata answers why one rendered
 * path stands for additional underlying structure:
 *
 * - aggregate: visible endpoints summarize edges attached to hidden detail;
 * - same_type_bundle: one path carries several edges of one relation type;
 * - mixed_type_bundle: one path carries edges of different relation types.
 *
 * This fragment owns every visual consequence of those states. Render and
 * layout stages call applyEdgeMetadataPresentation when creating a path;
 * geometry calls syncEdgeMetadataPresentationGeometry after rerouting; the
 * legend calls createEdgePresentationLegendIcon. Keeping those operations
 * together prevents graph strokes and their explanatory legend from drifting.
 *
 * SVG gradients and filters are graph-local resources. Their deterministic
 * ids let full renders replace prior definitions, while derived-edge removal
 * calls removeEdgePresentationResources to avoid accumulating transient defs.
 */

    function edgeMetadataPresentationIds(edge) {
      const states = [];
      if (edge && edge.aggregate) states.push("aggregate");
      if (edge && edge.bundle) {
        const bundleTypes = new Set(
          (edge.bundle_types || edgeConstituents(edge).map(item => item.type || edge.type))
            .map(value => String(value || "unknown"))
        );
        states.push(bundleTypes.size > 1 ? "mixed_type_bundle" : "same_type_bundle");
      }
      return states;
    }

    /** Resolve cumulative metadata presentation without replacing semantics. */
    function resolveEdgeMetadataPresentation(edge) {
      const stateIds = edgeMetadataPresentationIds(edge);
      const style = {};
      stateIds.forEach(stateId => {
        const configured = edgeMetadataStyleCatalog.get(stateId)?.style || {};
        if (configured.stroke_width != null) {
          style.stroke_width = Math.max(Number(style.stroke_width || 0), Number(configured.stroke_width));
        }
        if (stateId === "aggregate") Object.assign(style, {
          halo_width: configured.halo_width,
          halo_color: configured.halo_color,
          halo_opacity: configured.halo_opacity,
        });
        if (stateId === "mixed_type_bundle") Object.assign(style, {
          outline_width: configured.outline_width,
          outline_color: configured.outline_color,
          outline_opacity: configured.outline_opacity,
          transition_color: configured.transition_color,
          mixed_gradient: true,
        });
      });
      return {stateIds, style};
    }

    function edgePresentationResourceToken(edge) {
      const source = String(edge.edge_id || `${edge.source}:${edge.target}:${edge.type}`);
      let hash = 2166136261;
      for (let index = 0; index < source.length; index += 1) {
        hash ^= source.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return (hash >>> 0).toString(36);
    }

    /** Remove gradient/filter defs owned by one path before replacement. */
    function removeEdgePresentationResources(path) {
      String(path.dataset.edgePresentationResources || "").split(" ").filter(Boolean).forEach(id => {
        document.getElementById(id)?.remove();
      });
      path.dataset.edgePresentationResources = "";
      path.__edgePresentationGradient = null;
    }

    function mixedEdgeConstituentColors(edge) {
      const types = Array.from(new Set(
        (edge.bundle_types || edgeConstituents(edge).map(item => item.type || edge.type))
          .map(value => String(value || "unknown"))
      )).sort();
      return types.map(type => {
        const style = edgeStyleForType(type);
        return (style && (style.stroke || style.color)) || "#64748b";
      });
    }

    /** Add stable color bands separated by a narrow neutral transition. */
    function addGradientStops(gradient, colors, transitionColor) {
      colors.forEach((color, index) => {
        const end = (index + 1) / colors.length;
        const addStop = (offset, stopColor) => {
          const stop = createSvgElement("stop");
          stop.setAttribute("offset", `${Math.max(0, Math.min(1, offset)) * 100}%`);
          stop.setAttribute("stop-color", stopColor);
          gradient.appendChild(stop);
        };
        if (index === 0) addStop(0, color);
        addStop(end - 0.025, color);
        if (index < colors.length - 1) {
          addStop(end, transitionColor);
          addStop(end + 0.025, colors[index + 1]);
        }
      });
    }

    function createEdgePresentationGradient(path, edge, style) {
      const id = `edge-mixed-gradient-${edgePresentationResourceToken(edge)}`;
      document.getElementById(id)?.remove();
      const gradient = createSvgElement("linearGradient");
      gradient.id = id;
      gradient.setAttribute("gradientUnits", "userSpaceOnUse");
      const colors = mixedEdgeConstituentColors(edge);
      addGradientStops(gradient, colors, style.transition_color || "#f8fafc");
      svgEl.querySelector("defs").appendChild(gradient);
      path.__edgePresentationGradient = gradient;
      path.dataset.edgeArrowColor = colors[colors.length - 1] || "#334155";
      return id;
    }

    /** Build one filter containing every outer halo/outline in paint order. */
    function createEdgePresentationFilter(path, edge, style) {
      const effects = [];
      if (style.halo_width) effects.push({
        width: style.halo_width,
        color: style.halo_color || "#64748b",
        opacity: style.halo_opacity ?? 0.22,
      });
      if (style.outline_width) effects.push({
        width: style.outline_width,
        color: style.outline_color || "#334155",
        opacity: style.outline_opacity ?? 0.32,
      });
      if (!effects.length) return null;
      const id = `edge-presentation-filter-${edgePresentationResourceToken(edge)}`;
      document.getElementById(id)?.remove();
      const filter = createSvgElement("filter");
      filter.id = id;
      filter.setAttribute("x", "-30%"); filter.setAttribute("y", "-30%");
      filter.setAttribute("width", "160%"); filter.setAttribute("height", "160%");
      const merge = createSvgElement("feMerge");
      effects.forEach((effect, index) => {
        const radius = Math.max(0.5, (Number(effect.width) - Number(style.stroke_width || 2)) / 2);
        const morphology = createSvgElement("feMorphology");
        morphology.setAttribute("in", "SourceAlpha");
        morphology.setAttribute("operator", "dilate");
        morphology.setAttribute("radius", String(radius));
        morphology.setAttribute("result", `expanded${index}`);
        const flood = createSvgElement("feFlood");
        flood.setAttribute("flood-color", effect.color);
        flood.setAttribute("flood-opacity", String(effect.opacity));
        flood.setAttribute("result", `color${index}`);
        const composite = createSvgElement("feComposite");
        composite.setAttribute("in", `color${index}`);
        composite.setAttribute("in2", `expanded${index}`);
        composite.setAttribute("operator", "in");
        composite.setAttribute("result", `effect${index}`);
        const node = createSvgElement("feMergeNode");
        node.setAttribute("in", `effect${index}`);
        filter.appendChild(morphology); filter.appendChild(flood); filter.appendChild(composite);
        merge.appendChild(node);
      });
      const sourceNode = createSvgElement("feMergeNode");
      sourceNode.setAttribute("in", "SourceGraphic");
      merge.appendChild(sourceNode);
      filter.appendChild(merge);
      svgEl.querySelector("defs").appendChild(filter);
      return id;
    }

    /** Keep a user-space gradient aligned with the routed path endpoints. */
    function syncEdgeMetadataPresentationGeometry(path) {
      const gradient = path.__edgePresentationGradient;
      if (!gradient || !path.isConnected) return;
      try {
        const length = path.getTotalLength();
        const start = path.getPointAtLength(0);
        const end = path.getPointAtLength(length);
        gradient.setAttribute("x1", String(start.x)); gradient.setAttribute("y1", String(start.y));
        gradient.setAttribute("x2", String(end.x)); gradient.setAttribute("y2", String(end.y));
      } catch (_error) {
        // Detached or temporarily empty paths synchronize after their next route update.
      }
    }

    /** Apply semantic stroke first, then the bounded metadata presentation. */
    function applyEdgeMetadataPresentation(path, edge, semanticStyle, fallbackStroke) {
      removeEdgePresentationResources(path);
      const presentation = resolveEdgeMetadataPresentation(edge);
      const resourceIds = [];
      let stroke = (semanticStyle && (semanticStyle.stroke || semanticStyle.color)) || fallbackStroke;
      let dash = semanticStyle?.dash;
      if (presentation.style.mixed_gradient) {
        const gradientId = createEdgePresentationGradient(path, edge, presentation.style);
        resourceIds.push(gradientId);
        stroke = `url(#${gradientId})`;
        dash = null;
      }
      const filterId = createEdgePresentationFilter(path, edge, presentation.style);
      if (filterId) resourceIds.push(filterId);
      path.setAttribute("stroke", stroke);
      if (dash) path.setAttribute("stroke-dasharray", dash);
      else path.removeAttribute("stroke-dasharray");
      path.style.strokeWidth = presentation.style.stroke_width != null
        ? String(presentation.style.stroke_width)
        : "";
      path.style.filter = filterId ? `url(#${filterId})` : "";
      path.__edgeBaseStrokeWidth = path.style.strokeWidth;
      path.__edgeBaseFilter = path.style.filter;
      path.dataset.edgePresentationResources = resourceIds.join(" ");
      path.dataset.edgePresentations = presentation.stateIds.join(" ");
      return presentation;
    }

    /* Legend samples use identical widths, colors, transitions, and outlines. */
    let edgePresentationLegendGradientCounter = 0;
    function createEdgePresentationLegendIcon(stateId, rule, representativeEdge = null) {
      const style = representativeEdge
        ? resolveEdgeMetadataPresentation(representativeEdge).style
        : (rule.style || {});
      const sampleType = legendEdgeTypes[0]?.edgeType;
      const semanticStyle = sampleType ? edgeStyleForType(sampleType) : {stroke: "#2563eb", dash: "10 5"};
      const icon = createEdgeLegendIcon("edge-presentation", semanticStyle);
      const line = icon.querySelector("path");
      if (line && style.stroke_width != null) line.style.strokeWidth = String(style.stroke_width);
      if (stateId === "aggregate" && line) {
        const halo = line.cloneNode(false);
        halo.classList.add("edge-presentation-outline");
        halo.removeAttribute("stroke-dasharray");
        halo.setAttribute("stroke", style.halo_color || "#64748b");
        halo.setAttribute("stroke-opacity", String(style.halo_opacity ?? 0.22));
        halo.style.strokeWidth = String(style.halo_width || 10);
        icon.insertBefore(halo, line);
      }
      if (stateId === "mixed_type_bundle" && line) {
        const outline = line.cloneNode(false);
        outline.classList.add("edge-presentation-outline");
        outline.removeAttribute("stroke-dasharray");
        outline.setAttribute("stroke", style.outline_color || "#334155");
        outline.setAttribute("stroke-opacity", String(style.outline_opacity ?? 0.32));
        outline.style.strokeWidth = String(style.outline_width || 8);
        icon.insertBefore(outline, line);
        const colors = representativeEdge
          ? mixedEdgeConstituentColors(representativeEdge)
          : [edgePalette[0] || "#2563eb", edgePalette[1] || "#059669"];
        const defs = createSvgElement("defs");
        const gradient = createSvgElement("linearGradient");
        gradient.id = `edge-presentation-legend-gradient-${edgePresentationLegendGradientCounter++}`;
        gradient.setAttribute("gradientUnits", "userSpaceOnUse");
        gradient.setAttribute("x1", "3"); gradient.setAttribute("y1", "10");
        gradient.setAttribute("x2", "29"); gradient.setAttribute("y2", "10");
        addGradientStops(gradient, colors, style.transition_color || "#f8fafc");
        defs.appendChild(gradient);
        icon.insertBefore(defs, icon.firstChild);
        line.removeAttribute("stroke-dasharray");
        line.setAttribute("stroke", `url(#${gradient.id})`);
        const arrow = icon.querySelector("polygon");
        if (arrow) arrow.setAttribute("fill", colors[colors.length - 1]);
      }
      return icon;
    }
