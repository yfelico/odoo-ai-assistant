=================
AI Assistant Chat
=================

Chat with an AI business assistant from the Odoo top bar.

Ask in natural language about sales, stock, overdue invoices or orders, and ask
for quotations. The assistant answers from your Odoo data through a small set of
validated, read-only tools. Quotations are prepared as drafts and only created
when you press **Confirm**.

This module is the Odoo front end of the `Odoo AI Assistant
<https://github.com/yfelico/odoo-ai-assistant>`_ project; the assistant itself
runs as a separate API service.

Usage
=====

Click the magic-wand icon in the top bar to open the chat panel. The panel shows:

* the assistant's answers, with bold text and lists (model output is never
  interpreted as HTML);
* the tools it used to answer;
* for quotations, a draft card with the customer, lines and estimated untaxed
  total, a **Confirm** button and, once created, a link to the quotation.

Only users in the *Sales / User: Own Documents Only* group or above see the icon.

Configuration
=============

In **Settings > General Settings > Integrations > AI Assistant**:

* **Assistant API URL**: base URL of the API as reached from the Odoo server
  (default ``http://api:8000``, the service name inside docker compose).
* **Assistant API token**: the API's ``API_TOKEN``, if it has one.

Both are stored as system parameters (``ai_assistant_chat.api_url`` and
``ai_assistant_chat.api_token``).

How it works
============

The panel never calls the API from the browser. It calls two Odoo routes,
``/ai_assistant_chat/chat`` and ``/ai_assistant_chat/confirm``, which require a
logged-in sales user. The Odoo server forwards each request to the API with the
token, so the token never reaches the browser and no CORS setup is needed. When
a quotation is confirmed, the id of the confirming Odoo user is sent along and
recorded in the quotation's chatter note and in the assistant's audit log.

License
=======

LGPL-3. See the ``LICENSE`` file.
