#!/usr/bin/env node
const fs = require("fs");
const ELK = require("./vendor/elk.bundled.js");
const CANVAS_MARGIN = 56;

const [specPath, outPath] = process.argv.slice(2);
if (!specPath || !outPath) {
  console.error("usage: render_graph_with_elk.cjs <spec.json> <out.svg>");
  process.exit(2);
}

const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));

function withDefaults(style = {}) {
  return {
    stroke: style.stroke || "#64748b",
    fill: style.fill || "#ffffff",
    text: style.text || "#0f172a",
  };
}

function elkDirection(direction) {
  return direction === "RIGHT" ? "RIGHT" : "DOWN";
}

function toElkNode(item) {
  return {
    id: item.id,
    width: item.width || 150,
    height: item.height || 52,
    labels: item.label ? [{ text: item.label }] : [],
    _kind: "node",
    _style: withDefaults(item.style),
    _label: item.label || "",
  };
}

function buildElkGraph(spec) {
  const children = [];

  function collectLeaves(item) {
    if (item.kind === "node") {
      children.push(toElkNode(item));
      return;
    }
    for (const child of item.children || []) collectLeaves(child);
  }

  for (const child of spec.children || []) collectLeaves(child);

  return {
    id: "root",
    children,
    edges: (spec.edges || []).map((edge, idx) => ({
      id: `e${idx}`,
      sources: [edge.source],
      targets: [edge.target],
      layoutOptions: edge.hidden
        ? {
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.priority.direction": "1",
          }
        : {
            "elk.edgeRouting": "ORTHOGONAL",
          },
      _hidden: !!edge.hidden,
      _bidirectional: !!edge.bidirectional,
    })),
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": elkDirection(spec.direction),
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.spacing.nodeNode": "34",
      "elk.layered.spacing.nodeNodeBetweenLayers": "54",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.padding": "[top=12,left=12,bottom=12,right=12]",
    },
  };
}

function esc(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function flatten(node, out = []) {
  out.push(node);
  for (const child of node.children || []) {
    flatten(child, out);
  }
  return out;
}

function gatherGroupBoxes(item, nodeBoxes, depth = 0, out = []) {
  if (item.kind !== "group") return out;
  const padding = item.padding || { top: 44, right: 18, bottom: 18, left: 18 };
  const childBoxes = [];
  for (const child of item.children || []) {
    if (child.kind === "group") {
      gatherGroupBoxes(child, nodeBoxes, depth + 1, out);
      const nested = out.findLast((entry) => entry.id === child.id);
      if (nested) childBoxes.push(nested);
    } else {
      const box = nodeBoxes.get(child.id);
      if (box) childBoxes.push(box);
    }
  }
  if (!childBoxes.length) return out;
  const minX = Math.min(...childBoxes.map((box) => box.x));
  const minY = Math.min(...childBoxes.map((box) => box.y));
  const maxX = Math.max(...childBoxes.map((box) => box.x + box.width));
  const maxY = Math.max(...childBoxes.map((box) => box.y + box.height));
  out.push({
    id: item.id,
    x: minX - padding.left,
    y: minY - padding.top,
    width: maxX - minX + padding.left + padding.right,
    height: maxY - minY + padding.top + padding.bottom,
    _kind: "group",
    _label: item.label || "",
    _style: withDefaults(item.style),
    _depth: depth,
  });
  return out;
}

function pathForSection(section) {
  const points = [section.startPoint, ...(section.bendPoints || []), section.endPoint];
  return points.map((point, idx) => `${idx === 0 ? "M" : "L"}${point.x} ${point.y}`).join(" ");
}

function renderNode(node, parts) {
  const style = withDefaults(node._style);
  const x = node.x || 0;
  const y = node.y || 0;
  const width = node.width || 0;
  const height = node.height || 0;
  const radius = node._kind === "group" ? 18 : 14;

  parts.push(`<g id="${esc(node.id)}" data-kind="${esc(node._kind || "node")}">`);
  parts.push(
    `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${radius}" ry="${radius}" fill="${style.fill}" stroke="${style.stroke}" stroke-width="${node._kind === "group" ? 2.4 : 1.8}"/>`
  );

  if (node._label) {
    const lines = String(node._label).split("\n");
    const fontSize = node._kind === "group" ? 14 : 12;
    const fontWeight = node._kind === "group" ? 600 : 500;
    const lineHeight = fontSize + 3;
    const startY =
      node._kind === "group"
        ? y + 26
        : y + height / 2 - ((lines.length - 1) * lineHeight) / 2 + 4;
    const anchor = node._kind === "group" ? "middle" : "middle";
    const textX = x + width / 2;
    parts.push(
      `<text x="${textX}" y="${startY}" text-anchor="${anchor}" font-family="Inter, Segoe UI, sans-serif" font-size="${fontSize}" font-weight="${fontWeight}" fill="${style.text}" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke">`
    );
    lines.forEach((line, idx) => {
      const dy = idx === 0 ? 0 : lineHeight;
      parts.push(`<tspan x="${textX}" dy="${idx === 0 ? 0 : dy}">${esc(line)}</tspan>`);
    });
    parts.push(`</text>`);
  }

  parts.push(`</g>`);
}

function renderEdge(edge, parts) {
  if (edge._hidden) return;
  for (const section of edge.sections || []) {
    parts.push(
      `<path d="${pathForSection(section)}" fill="none" stroke="#64748b" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"${edge._bidirectional ? ' marker-start="url(#arrow-start)" marker-end="url(#arrow-end)"' : ' marker-end="url(#arrow-end)"'}/>`
    );
  }
}

async function main() {
  const elk = new ELK();
  const laidOut = await elk.layout(buildElkGraph(spec));
  const leafNodes = flatten(laidOut).filter((item) => item.id !== "root");
  const nodeBoxes = new Map(leafNodes.map((node) => [node.id, node]));
  const groups = [];
  for (const child of spec.children || []) gatherGroupBoxes(child, nodeBoxes, 0, groups);
  groups.sort((a, b) => a._depth - b._depth);
  const width = Math.ceil((laidOut.width || 0) + CANVAS_MARGIN * 2);
  const height = Math.ceil((laidOut.height || 0) + CANVAS_MARGIN * 2);

  const parts = [];
  parts.push(`<?xml version="1.0" encoding="UTF-8"?>`);
  parts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`
  );
  parts.push(`<defs>
    <marker id="arrow-end" markerWidth="10" markerHeight="8" refX="8" refY="4" orient="auto" markerUnits="strokeWidth">
      <path d="M0 0 L8 4 L0 8 Z" fill="#64748b"/>
    </marker>
    <marker id="arrow-start" markerWidth="10" markerHeight="8" refX="2" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth">
      <path d="M8 0 L0 4 L8 8 Z" fill="#64748b"/>
    </marker>
  </defs>`);
  parts.push(`<g transform="translate(${CANVAS_MARGIN} ${CANVAS_MARGIN})">`);
  parts.push(`<g id="edges">`);
  for (const edge of laidOut.edges || []) renderEdge(edge, parts);
  parts.push(`</g>`);
  parts.push(`<g id="groups">`);
  for (const group of groups) renderNode(group, parts);
  parts.push(`</g>`);
  parts.push(`<g id="nodes">`);
  for (const node of leafNodes) renderNode(node, parts);
  parts.push(`</g>`);
  parts.push(`</g>`);
  parts.push(`</svg>`);

  fs.writeFileSync(outPath, parts.join(""), "utf8");
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
