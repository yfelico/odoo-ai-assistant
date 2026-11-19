// Unit tests of the chat panel's Markdown parser. Run with: node --test odoo_addons/
import assert from "node:assert/strict";
import { test } from "node:test";

import { parseMarkdown } from "../../static/src/assistant_systray/markdown.js";

const plain = (text) => ({ text, bold: false });
const bold = (text) => ({ text, bold: true });

test("plain text is one paragraph", () => {
    assert.deepEqual(parseMarkdown("Hola"), [{ type: "paragraph", lines: [[plain("Hola")]] }]);
});

test("double asterisks mark bold text", () => {
    assert.deepEqual(parseMarkdown("Total: **2.000,00** sin impuestos"), [
        {
            type: "paragraph",
            lines: [[plain("Total: "), bold("2.000,00"), plain(" sin impuestos")]],
        },
    ]);
});

test("unmatched double asterisks stay as text", () => {
    assert.deepEqual(parseMarkdown("a ** b"), [{ type: "paragraph", lines: [[plain("a ** b")]] }]);
});

test("consecutive lines stay in one paragraph; blank lines split paragraphs", () => {
    assert.deepEqual(parseMarkdown("uno\ndos\n\ntres"), [
        { type: "paragraph", lines: [[plain("uno")], [plain("dos")]] },
        { type: "paragraph", lines: [[plain("tres")]] },
    ]);
});

test("bullet lines with * or - form one list", () => {
    assert.deepEqual(parseMarkdown("Productos:\n* **Office Lamp**: 0\n- Flipover: 0"), [
        { type: "paragraph", lines: [[plain("Productos:")]] },
        {
            type: "list",
            ordered: false,
            items: [
                [bold("Office Lamp"), plain(": 0")],
                [plain("Flipover: 0")],
            ],
        },
    ]);
});

test("numbered lines form an ordered list", () => {
    assert.deepEqual(parseMarkdown("1. Gemini Furniture\n2) Lumber Inc"), [
        {
            type: "list",
            ordered: true,
            items: [[plain("Gemini Furniture")], [plain("Lumber Inc")]],
        },
    ]);
});

test("a change of list kind starts a new list", () => {
    const blocks = parseMarkdown("* a\n1. b");
    assert.deepEqual(
        blocks.map((block) => [block.type, block.ordered]),
        [
            ["list", false],
            ["list", true],
        ]
    );
});

test("headings become bold paragraphs", () => {
    assert.deepEqual(parseMarkdown("### Resumen\ntexto"), [
        { type: "paragraph", lines: [[bold("Resumen")], [plain("texto")]] },
    ]);
});

test("markup is returned as text, never interpreted", () => {
    assert.deepEqual(parseMarkdown("<img src=x onerror=alert(1)> **<b>x</b>**"), [
        {
            type: "paragraph",
            lines: [[plain("<img src=x onerror=alert(1)> "), bold("<b>x</b>")]],
        },
    ]);
});

test("empty or blank text has no blocks", () => {
    assert.deepEqual(parseMarkdown(""), []);
    assert.deepEqual(parseMarkdown(" \n\n "), []);
});
