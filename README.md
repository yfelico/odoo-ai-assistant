# Odoo AI Assistant

AI agent that answers business questions over Odoo 18 and drafts quotations with human confirmation. See [SPEC.md](SPEC.md).

## Quick start

Requirements: Docker with Compose, [uv](https://docs.astral.sh/uv/) and Git Bash (on Windows).

```bash
cp .env.example .env          # set POSTGRES_PASSWORD
docker compose up -d          # Odoo (http://localhost:8069), PostgreSQL and the API (http://localhost:8000)
./scripts/init_odoo.sh        # once: creates the "demo" database and the technical user + API key
curl http://localhost:8000/health
```

`init_odoo.sh` writes `ODOO_API_KEY` into `.env`. Running it again rotates the key.

If port 8069 is already taken (e.g. by another Odoo), set `ODOO_PORT` and `ODOO_URL` in `.env`, for example `ODOO_PORT=8018` and `ODOO_URL=http://localhost:8018`. Inside Docker the API always reaches Odoo at `http://odoo:8069`.

## Known limitations

- The technical user relies on standard Odoo groups (Sales: all documents, Inventory: user, Accounting: read-only). They grant read access to everything the agent needs but still allow some writes.
- Sales amounts are the orders' untaxed totals, which match the company currency only in single-currency databases such as the demo.
- Sales periods are UTC calendar days with an inclusive end date.

## Development

```bash
uv sync
uv run fastapi dev app/main.py                     # API outside Docker
uv run pytest -m "not integration and not evals"   # fast tests
uv run pytest -m integration                       # requires Odoo running
uv run ruff check . --fix && uv run ruff format .
uv run mypy app
```
