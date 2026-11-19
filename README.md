# Odoo AI Assistant

AI agent that answers business questions over Odoo 18 and drafts quotations with human confirmation. See [SPEC.md](SPEC.md).

## Quick start

Requirements: Docker with Compose, [uv](https://docs.astral.sh/uv/) and Git Bash (on Windows).

```bash
cp .env.example .env          # set POSTGRES_PASSWORD and LLM_API_KEY
docker compose up -d          # Odoo (http://localhost:8069), PostgreSQL and the API (http://localhost:8000)
./scripts/init_odoo.sh        # once: creates the "demo" database and the technical user + API key
curl http://localhost:8000/health
```

`init_odoo.sh` writes `ODOO_API_KEY` into `.env` (running it again rotates the key), generates `API_TOKEN` if it is empty, and installs the `ai_assistant_chat` Odoo module paired with the API.

### Chat inside Odoo

Log in to Odoo (`admin` / `admin` on the demo database) and click the magic-wand icon in the top bar. The chat panel shows the tools the assistant used and, for quotations, a draft card with a **Confirm** button; the created quotation can be opened from the panel. Only users in the Sales group see the icon.

The panel talks to Odoo, and the Odoo server forwards each request to the API with the token, so the token never reaches the browser. The API URL and token are in **Settings > General Settings > Integrations > AI Assistant**.

You can also chat at http://localhost:8000/ (it asks for `API_TOKEN` once per tab) or call the API directly:

```bash
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_TOKEN" \
  -d '{"message": "¿Qué productos están por debajo del stock mínimo?"}'
```

Ask for a quotation ("Crea un presupuesto para Azure Interior con 10 Office Chair") and the assistant prepares a draft without touching Odoo; the page shows it with a **Confirm** button, which calls `POST /confirm` to create the quotation.

The model is any [LiteLLM](https://docs.litellm.ai/docs/providers) model string in `LLM_MODEL` (default: Gemini), with the provider key in `LLM_API_KEY`. A free Gemini key is available from Google AI Studio. After changing `.env`, recreate the API container: `docker compose up -d --force-recreate api`.

If port 8069 is already taken (e.g. by another Odoo), set `ODOO_PORT` and `ODOO_URL` in `.env`, for example `ODOO_PORT=8018` and `ODOO_URL=http://localhost:8018`. Inside Docker the API always reaches Odoo at `http://odoo:8069`.

## Known limitations

- The technical user relies on standard Odoo groups (Sales: all documents, Inventory: user, Accounting: read-only). They grant read access to everything the agent needs but still allow some writes.
- Sales amounts are the orders' untaxed totals, which match the company currency only in single-currency databases such as the demo.
- Sales periods are UTC calendar days with an inclusive end date. "Today" (for overdue invoices and relative periods) is also the UTC date.
- Conversation histories and quotation drafts live in the API process memory: they are lost on restart and not shared between workers.
- A draft's total is an estimate at list price, without taxes; Odoo applies the customer's pricelist and taxes when the quotation is created.
- If Odoo times out while creating a quotation, it may have been created anyway; retrying the confirmation could then create a duplicate.
- Quotations confirmed from Odoo are created by the technical user; the chatter note and the audit trail record the id of the Odoo user who confirmed.
- The chat panel keeps the conversation only while the page is open; reloading Odoo starts a new one.
- Integration tests and the prompt-injection eval create real orders in the demo database and cancel them afterwards (the technical user cannot delete them).
- Estimated costs use LiteLLM's public list prices; free tiers cost nothing, and prices change over time.

## Development

```bash
uv sync
uv run fastapi dev app/main.py                     # API outside Docker
uv run pytest -m "not integration and not evals"   # fast tests
uv run pytest -m integration                       # requires Odoo running
./scripts/test_odoo_module.sh                      # Odoo module tests, in a throwaway database
node --test "odoo_addons/**/*.test.mjs"            # JS unit tests of the chat panel (Node 22)
uv run pytest -m evals                             # requires Odoo and LLM_API_KEY; calls the real model
uv run ruff check . --fix && uv run ruff format .
uv run mypy app
```

### Evals

`tests/evals/cases.yaml` holds the eval catalogue: questions about sales, inventory, invoicing, orders, customers, quotations and safety (including a prompt injection hidden in an order line). Expected figures are computed live from Odoo, so the cases survive the demo data moving with the install date, and grading is deterministic.

```bash
uv run pytest -m evals                              # every case with LLM_MODEL
uv run pytest -m evals -k ventas                    # one category
EVAL_MODELS=gemini/gemini-3.5-flash-lite,gemini/gemini-3.1-flash-lite uv run pytest -m evals
```

Each run writes a report to `tests/evals/reports/` (accuracy overall and per category, mean and p95 latency, tokens and estimated cost per model). `EVAL_REQUESTS_PER_MINUTE` (default 5) keeps each model under free-tier quotas; time spent waiting for quota is not counted as latency. The latest reference report is in [docs/evals-report.md](docs/evals-report.md).

CI (GitHub Actions) runs ruff, mypy, the fast tests and the JS unit tests on every push; integration tests and evals need Odoo and an LLM key, so they run locally.
