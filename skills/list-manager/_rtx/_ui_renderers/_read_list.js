const columns = ["title", "state", "deadline", "created", "id"];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function collectEntries(value, entries = []) {
  if (Array.isArray(value)) {
    value.forEach((item) => collectEntries(item, entries));
  } else if (value && typeof value === "object") {
    if (typeof value.id === "string" && typeof value.title === "string") {
      entries.push(value);
    }
    ["categories", "entries", "children"].forEach((key) =>
      collectEntries(value[key], entries),
    );
  }
  return entries;
}

export function render(value) {
  const entries = collectEntries(value);
  if (!entries.length) return "";

  const visible = columns.filter((column) =>
    entries.some((entry) => entry[column] != null),
  );
  const head = visible.map((column) => `<th scope="col">${column}</th>`).join("");
  const body = entries
    .map(
      (entry) =>
        `<tr>${visible
          .map((column) => `<td>${escapeHtml(entry[column])}</td>`)
          .join("")}</tr>`,
    )
    .join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
