/**
 * Minimal Markdown for the assistant's answers: paragraphs, bullet and numbered lists,
 * headings and **bold**. It returns plain data (blocks of text segments) that the
 * template renders with t-esc, so model output is never interpreted as HTML.
 *
 * Kept free of Odoo imports so it can be unit-tested with Node (tests/js/).
 */

const BULLET = /^\s*[*-]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;

export function parseInline(text) {
    const parts = text.split("**");
    // An even number of parts means an unmatched "**": keep the last one literal.
    if (parts.length % 2 === 0) {
        const last = parts.pop();
        parts[parts.length - 1] += `**${last}`;
    }
    return parts
        .map((part, index) => ({ text: part, bold: index % 2 === 1 }))
        .filter((segment) => segment.text);
}

export function parseMarkdown(text) {
    const blocks = [];
    let current = null;
    for (const line of (text || "").split("\n")) {
        if (!line.trim()) {
            current = null;
            continue;
        }
        const bullet = line.match(BULLET);
        const numbered = bullet ? null : line.match(NUMBERED);
        if (bullet || numbered) {
            const ordered = Boolean(numbered);
            if (current?.type !== "list" || current.ordered !== ordered) {
                current = { type: "list", ordered, items: [] };
                blocks.push(current);
            }
            current.items.push(parseInline((bullet || numbered)[1]));
            continue;
        }
        const heading = line.match(HEADING);
        const segments = heading
            ? [{ text: heading[1].replaceAll("**", ""), bold: true }]
            : parseInline(line);
        if (current?.type !== "paragraph") {
            current = { type: "paragraph", lines: [] };
            blocks.push(current);
        }
        current.lines.push(segments);
    }
    return blocks;
}
