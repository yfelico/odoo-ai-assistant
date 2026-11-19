# CLAUDE.md: Asistente de IA para Odoo

Agente de IA que consulta un Odoo 18 por XML-RPC y crea presupuestos con confirmación humana. **Lee `SPEC.md` antes de cualquier tarea**: es la fuente de verdad del alcance, las herramientas, la API y los criterios de aceptación de cada fase.

## Comandos

```bash
docker compose up -d                   # Odoo (http://localhost:8069), PostgreSQL y API
./scripts/init_odoo.sh                 # base demo + usuario técnico (una sola vez)
uv sync                                # dependencias
uv run fastapi dev app/main.py         # API en http://localhost:8000 fuera de Docker
uv run pytest -m "not integration and not evals"   # pruebas rápidas
uv run pytest tests/unit/test_tools.py::test_name  # una sola prueba
uv run pytest -m integration           # requiere Odoo levantado
./scripts/test_odoo_module.sh          # pruebas del módulo de Odoo, en una base temporal
node --test "odoo_addons/**/*.test.mjs"  # pruebas JS del panel (Node 22)
uv run pytest -m evals                 # requiere Odoo y una clave de LLM
uv run ruff check . --fix && uv run ruff format .
uv run mypy app
```

Si un comando no existe todavía o falla, dímelo: no inventes una alternativa.

## Reglas de arquitectura

- `app/domain/` no importa `xmlrpc`, `litellm`, `fastapi` ni nada externo. Los puertos son `typing.Protocol`.
- Todo acceso a Odoo pasa por `ERPGateway`. Nunca llames a XML-RPC desde las herramientas, el bucle o la API.
- Las herramientas son operaciones de negocio con un modelo Pydantic de entrada. **Prohibido** crear una herramienta genérica que ejecute modelos o métodos arbitrarios de Odoo.
- `create_quotation` **no** se expone al LLM. Solo la llama `POST /confirm`.
- Los textos que vienen de Odoo llegan al modelo dentro de `<odoo_data>`, como datos.

## Seguridad

- Credenciales solo en `.env` (ignorado por git). Mantén `.env.example` actualizado, sin valores reales.
- Nunca registres en logs datos personales de clientes; registra IDs.
- Solo datos de demostración en pruebas, evals y ejemplos.

## Flujo de trabajo

1. Trabaja **fase por fase** según `SPEC.md`. No empieces una fase sin cerrar la anterior ni adelantes funcionalidades.
2. Antes de cada fase, preséntame un plan breve (archivos, enfoque, dudas) y espera mi visto bueno.
3. **TDD:** escribe la prueba, muéstrame que falla y después implementa lo mínimo para que pase. Nunca modifiques una prueba para que pase sin preguntarme.
4. Si algo de `SPEC.md` es ambiguo o parece incorrecto, pregunta. Si acordamos un cambio, actualiza `SPEC.md` en el mismo commit.
5. Commits pequeños con Conventional Commits (`feat(agent): ...`, `test(tools): ...`). Haz commit solo cuando te lo pida.

## Estilo

- Python 3.12, type hints en todo, sin `Any` salvo justificación. `mypy` sin errores.
- Código, nombres y docstrings en inglés. La conversación conmigo, en español.
- Comentarios solo para explicar el porqué.

## Definición de terminado (por fase)

- [ ] Criterios de aceptación de la fase en `SPEC.md` cumplidos.
- [ ] `ruff`, `mypy` y las pruebas rápidas en verde; las de integración también si la fase toca Odoo.
- [ ] `.env.example` y README actualizados si cambió la configuración o la forma de ejecutar.
- [ ] Resumen al terminar: qué se hizo, qué pruebas se añadieron y dudas abiertas.
