import { Component, onPatched, onWillStart, useRef, useState } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { formatFloat } from "@web/core/utils/numbers";
import { useService } from "@web/core/utils/hooks";
import { parseMarkdown } from "@ai_assistant_chat/assistant_systray/markdown";

const SALES_GROUP = "sales_team.group_sale_salesman";

/**
 * Chat with the AI assistant from the top bar. Every request goes through the Odoo
 * server (/ai_assistant_chat/*), which forwards it to the assistant API.
 * Answers support minimal Markdown (markdown.js), rendered from plain data with t-esc:
 * model output is never interpreted as HTML.
 */
export class AssistantSystray extends Component {
    static template = "ai_assistant_chat.AssistantSystray";
    static components = { Dropdown };
    static props = {};

    setup() {
        this.action = useService("action");
        this.dropdown = useDropdownState();
        this.messagesRef = useRef("messages");
        this.inputRef = useRef("input");
        this.nextId = 1;
        this.state = useState({
            visible: false,
            messages: [],
            input: "",
            busy: false,
            conversationId: null,
            draft: null,
        });
        onWillStart(async () => {
            this.state.visible = await user.hasGroup(SALES_GROUP);
        });
        onPatched(() => {
            const el = this.messagesRef.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        });
    }

    formatAmount(value) {
        return formatFloat(value, { digits: [16, 2] });
    }

    addMessage(role, text, extra = {}) {
        this.state.messages.push({ id: this.nextId++, role, text, ...extra });
    }

    errorText(error) {
        return error?.data?.message || _t("Something went wrong. Try again.");
    }

    async send() {
        const message = this.state.input.trim();
        if (!message || this.state.busy) {
            return;
        }
        this.state.input = "";
        this.addMessage("user", message);
        this.state.busy = true;
        try {
            const reply = await rpc("/ai_assistant_chat/chat", {
                message,
                conversation_id: this.state.conversationId,
            });
            this.state.conversationId = reply.conversation_id;
            this.addMessage("assistant", reply.answer, {
                blocks: parseMarkdown(reply.answer),
                tools: reply.tool_calls.map((call) => call.name),
            });
            if (reply.pending_draft) {
                this.state.draft = { ...reply.pending_draft, status: "pending", error: null };
            }
        } catch (error) {
            this.addMessage("error", this.errorText(error));
        } finally {
            this.state.busy = false;
            this.inputRef.el?.focus();
        }
    }

    async confirmDraft() {
        const draft = this.state.draft;
        if (draft?.status !== "pending") {
            return;
        }
        draft.status = "confirming";
        draft.error = null;
        try {
            const created = await rpc("/ai_assistant_chat/confirm", { draft_id: draft.draft_id });
            Object.assign(draft, { status: "created", created });
        } catch (error) {
            Object.assign(draft, { status: "pending", error: this.errorText(error) });
        }
    }

    discardDraft() {
        this.state.draft = null;
    }

    openQuotation(quotationId) {
        this.dropdown.close();
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order",
            res_id: quotationId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    newConversation() {
        Object.assign(this.state, { messages: [], conversationId: null, draft: null, input: "" });
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            // send() handles its own errors.
            void this.send();
        }
    }
}

registry
    .category("systray")
    .add("ai_assistant_chat.AssistantSystray", { Component: AssistantSystray }, { sequence: 30 });
