# CLAUDE.md — SHERPY (backend del agente)

Contexto para Claude. Este repo es **solo el backend del agente de IA**. Léelo entero antes de tocar código.

## Qué es

Agente de IA de **asesoría financiera personal** para la hackathon de **Sherpas**. Para cada familia:

1. **Obtiene el contexto** de su caso con los datos de Sherpas.
2. **Hace los cálculos** con funciones deterministas en Python.
3. **Da recomendaciones adaptadas** a esa familia.

Se expone como una **API HTTP** que consume un cliente externo, fuera de este repo.

Requisitos fijos:
- **Coste cero**: solo servicios con plan gratuito y sin tarjeta.
- No se entrena ni se hace fine-tuning de ningún modelo. Se usa un modelo general con herramientas y datos.

## Stack

| Capa | Tecnología | Notas |
|---|---|---|
| Lenguaje | Python 3.11+ | Tipado completo |
| API | FastAPI | Endpoint de chat con streaming |
| Agente | `pydantic-ai-slim[openai,google,groq,mcp]` | Herramientas, dependencias (`deps`) e historial |
| LLM principal | LLM de la hackathon de Sherpas (`gpt-5-mini`, API compatible con OpenAI) | `sherpy/agent/llm.py`, clave `TEAM_KEY` |
| LLM respaldo | Gemini Flash (AI Studio) y Groq, free tier, opcionales | `FallbackModel` si el principal devuelve 429 u otro error |
| Datos | **MCP de Sherpas** (streamable HTTP + Bearer token) y copia local `cases/` | El agente elige la fuente en cada consulta (ver "Elegir fuente") |
| Tests | pytest | Obligatorios para todas las funciones `calc_*` |

```
POST /chat  ──► FastAPI ──► Agent (Pydantic AI)
                              ├── LLM: Sherpas gpt-5-mini → fallback Gemini / Groq
                              ├── Tools de contexto: MCP de Sherpas (list_cases / get_case)
                              └── Tools de cálculo: calc_*  (Python puro)
```

### Estructura del código

```
sherpy/
  config.py          # Settings desde el entorno / .env
  calcs/             # calc_* puros (sin LLM) + modelos de resultado
  data/source.py     # CaseSource: MCPBackend (principal) + LocalBackend (cases/) + caché
  agent/             # build_agent(): FallbackModel Gemini→Groq, tools de contexto y de cálculo
  api/main.py        # FastAPI: lifespan abre el MCP una vez
tests/               # pytest; los calc_* se validan contra los key_metrics de Sherpas
```

### Arranque

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e .
cp .env.example .env            # rellenar claves
.venv/bin/pytest
.venv/bin/uvicorn sherpy.api.main:app --reload
```

Sin `MCP_URL`/`MCP_TOKEN` (o si el MCP falla) se usan los datos locales de `cases/`. Sin ninguna clave de LLM, `/chat` devuelve 503.

> TODO: hosting.

### Contrato de la API

- `GET /health` → `{status, data_source: "mcp"|"local", llm_configured}`
- `GET /cases` → lista de casos (como `list_cases`)
- `POST /chat` con `{case_id, message, history?}` → `text/event-stream` con eventos:
  - `delta` `{text}`: trozo de respuesta
  - `tool` `{name, args}` / `tool_result` `{name}`: llamadas a herramientas
  - `done` `{output, history}`: respuesta completa e historial; el cliente reenvía `history` tal cual en el siguiente turno (API sin estado)
  - `error` `{status, message}`: 429 si todos los proveedores tienen límite de uso, 502/503 otros fallos del proveedor
- Errores antes del stream: 404 caso inexistente, 422 petición/historial inválido, 503 sin LLM configurado.

## Variables de entorno

```env
TEAM_KEY=           # clave del equipo para el LLM de Sherpas (principal)
GOOGLE_API_KEY=     # aistudio.google.com (respaldo opcional)
GROQ_API_KEY=       # console.groq.com (respaldo)
MCP_URL=            # endpoint del MCP de Sherpas (nos lo da la organización)
MCP_TOKEN=          # Bearer token del MCP de Sherpas
```

Nunca hagas commit de `.env`. Las claves y el token solo se leen desde el entorno.

## Conexión al MCP de Sherpas

El MCP es la **fuente de datos**, no un modelo. Usa transporte streamable HTTP con cabecera `Authorization: Bearer <MCP_TOKEN>`.

Se usa un cliente MCP directo con el SDK oficial `mcp` (`ClientSession` + `streamable_http_client`, en `sherpy/data/source.py`). El LLM no ve el MCP crudo: ve nuestras herramientas `list_cases` / `get_case(part, section)`, fijadas al caso activo, con caché y respaldo local. (En Pydantic AI V2 `MCPServerStreamableHTTP` ya no existe; su sustituto es `MCPToolset`.) Formato de la respuesta de `get_case`:

```python
r = await session.call_tool("get_case", {"caseId": ..., "part": ..., "section": ...})
data = json.loads(r.content[0].text)["value"]   # con part (JSON o Markdown)
```

`section` admite el prefijo del título en Markdown (p. ej. `"D5"` → `"D5: Decision-making style"`).

- Cachea en memoria las respuestas de `get_case` por `(caseId, part, section)`: los datos son estáticos.
- **Elegir fuente:** en cada pregunta el agente decide con `get_case(source="local"|"mcp", ...)`. `local` (1–13 ms, sin red) para preguntas generales, cabeceras, varias secciones o repreguntas; `mcp` (60–600 ms) para un dato puntual y concreto o si `local` no lo tiene. Si la fuente elegida falla, se usa la otra. Las `calc_*` leen local primero. El evento `tool_result` informa del origen real (`cache` | `local` | `mcp`).
- Abre la sesión MCP una vez (en el `lifespan` de FastAPI) y reutilízala; no la abras en cada herramienta.

## Datos de Sherpas

Son 4 casos **ficticios y sintéticos**. Todas las cantidades están en **USD** y el contexto fiscal es **EE. UU.** (IRS/SSA).

| caseId | Hogar | Ciudad | Score | Banda |
|---|---|---|---|---|
| `daniel-emily-johnson` | Pareja 44/42, hijos 11 y 9, MFJ | Denver, CO | 52 | vulnerable |
| `michael-carter` | Soltero 37, sin hijos | Austin, TX | 26 | critical |
| `samantha-lewis` | Madre soltera 47, hijos 18 y 16 | Miami, FL | 82 | strong |
| `timothy-melanie-weah` | Pareja 40/38, hijos 6 y 3, MFJ | San Francisco, CA | 60 | stable |

### Partes de cada caso (iguales en el MCP y en la copia local `cases/`)

```
cases/
  <caseId>/
    legal-values.json               # valores fiscales oficiales (idéntico en todos los casos)
    financial-health.json           # ~20k tokens
    investment-analysis.json        # ~23k tokens
    persona.md                      # ~2k tokens, Markdown con secciones "## "
    signals.json                    # ~1.3k tokens
```

### Partes y secciones clave

**financial-health.json**
- `case`: hogar (`household[]` con `first_name`, `role` client/co-client/child, `age`, `retirement_age`, `risk_profile`), `tax_filing_status` (`SINGLE` | `MARRIED_JOINTLY`), ciudad y estado.
- `score`: `published_score`, `band`, `pillars[]` (Savings and cash flow, Debt, Investments, Liquidity and emergency fund, Insurance and protection), `caps_applied[]`, **`top_moves[]`** (acción, esfuerzo, `score_before` / `score_after`), `combined_moves[]` y `explainer` (Markdown).
- `profile`:
  - `incomes[]`: `type` (`SALARY_AND_WAGES`, `SOCIAL_SECURITY`, `ANNUITY_INCOME`...), `yearly`, `owner`, `starts`, `ends`.
  - `expenses[]`: `yearly` y `monthly`.
  - `accounts[]`: `account_type`, `tax_treatment` (`TAXABLE` | `TAX_DEFERRED` | `TAX_FREE`), `balance`, `yearly_contribution_*`, `yearly_employer_match`.
  - `other_assets[]`.
  - `liabilities[]`: `balance`, `interest_rate` (decimal, 0.18 = 18 %), `monthly_payment`.
  - `insurance[]`: `coverage` puede ser un número o un texto como "in place, amount not recorded".
  - `goals[]`: `yearly_costs[]`; las compras llevan `planned_purchase` y `planned_loan`.
- `goals`: `goalsSummary[]` y `chartGoals[]` con fechas.
- `key_metrics` (grande) y `projections`: métricas y proyecciones ya calculadas. Úsalas antes de recalcular.
- `narrative`: texto explicativo.

**investment-analysis.json**: `positions`, `key_metrics` (concentración, sectores, `performanceAnalysis.perEventDrawdowns`, `marketEnvironments` de la cartera actual y de la recomendada) y `analysis.events`.

**persona.md**: D0 nivel de patrimonio · D1 etapa vital · D2 complejidad · D3 gravedad de los huecos · D4 orientación al riesgo · D5 estilo de decisión · D6 motivación · Transition alerts · Subjective/Objective concerns · Tensions. **Es la fuente para personalizar el consejo.**

**signals.json**: eventos que afectan a la familia, cada uno con `moment` (`market` | `rule` | `life` | `decision`), `touches_this_family[]` y `where_in_the_data[]`. Es el mejor contexto inicial porque es pequeño.

**legal-values.json**: `StandardDeductionByYear`, `FederalIncomeTaxBracketsByYear` (tramos con `upTo` / `rate`; el último tramo tiene `upTo: null`), `ChildTaxCreditByYear` (`perChild`, `childUnderAge` 17, `phaseOutStart` por estado civil, `reductionPer1000` 50), `SSWageBaseByYear`, `FICATaxSS`, `FICATaxMedicare` y `FICATaxAdditionalMedicare`. Hay valores de 2026 y 2027 (publicados por el IRS el 5 de noviembre de 2026).

### Reglas de carga

- Las partes grandes **nunca se mandan enteras al LLM**. Se cargan por sección: `get_case(caseId, part, section)`.
- Orden recomendado de contexto: `signals` → `financial-health.score` → `persona` → lo que haga falta.
- Herramientas del MCP: `list_cases()` y `get_case(caseId, part?, section?)`. Sin `part`, `get_case` devuelve la cabecera con las secciones y su tamaño en tokens; úsalo para decidir qué pedir.

## Comportamiento del agente

### Herramientas

**Contexto**
- `list_cases()`
- `get_case(part, section?)`: trabaja sobre el caso activo (`deps.case_id`).

**Cálculo**: Python puro, deterministas y con tests:
- `calc_federal_tax(year)`: salarios − aportaciones pre-tax (401k tradicional + HSA) − deducción estándar → tramos → menos el crédito por hijo (con phase-out). `tax_before_credits` cuadra con el "Federal Income Tax" de Sherpas, que no descuenta el CTC.
- `calc_fica(year)`: Seguridad Social hasta el wage base + Medicare + Additional Medicare.
- `calc_emergency_fund(target_months)`: efectivo accesible (checking + savings + money market de `key_metrics`) / salidas mensuales (gastos + cuotas de deuda). Cuadra con `facts.cash.monthsCovered`.
- `calc_debt_payoff(debt_name, extra_monthly)`: meses e intereses totales.
- `calc_goal_gap(goal)`: hueco de universidad, casa o jubilación.
- `calc_market_shock(drop_pct)`: impacto en dólares de una caída sobre la cartera invertible.

### Reglas obligatorias

1. **El LLM nunca hace cuentas.** Todo número nuevo sale de una herramienta `calc_*` o de los datos de Sherpas.
2. **No inventes datos.** Si algo no está en los datos, dilo. Cita el dato concreto que respalda cada recomendación.
3. **Prioridades por defecto:** fondo de emergencia → deuda de interés alto (tarjetas) → protección (vida, incapacidad, umbrella) → jubilación → universidad y otros objetivos.
4. **Apóyate en `score.top_moves` y `combined_moves`.** Son las acciones que más suben el score; explica el porqué con los pilares.
5. **Personaliza con `persona`:**
   - Respeta el `risk_profile` de cada miembro. Si la pareja tiene perfiles distintos, explica el plan para que lo entiendan los dos.
   - Adapta la forma de explicar al estilo de decisión (D5: validator, collaborator, dual...).
   - Habla de su motivación (D6).
   - Banda `critical` o `vulnerable`: acciones concretas y pocas, sin recomendar más riesgo.
6. **Tono:** cercano, claro y sin jerga innecesaria. Respuestas cortas; máximo 3 acciones priorizadas salvo que pidan más.
7. **Aviso:** es información educativa y no asesoramiento financiero regulado. Dilo una vez y no lo repitas en cada mensaje.
8. Responde en el idioma del usuario.

## Convenciones de código

- Tipado completo, Pydantic para los modelos de entrada y salida de la API y de las herramientas.
- Una función `calc_*` = una herramienta. Docstring clara, porque el LLM la lee para decidir cuándo usarla.
- Los cálculos van en un módulo separado del agente (por ejemplo `calcs/`), sin dependencias del LLM, para poder testearlos de forma aislada.
- Todo importe en USD como `float` y redondeado solo al presentarlo. Las tasas en decimal.
- El historial de conversación se gestiona con `message_history` de Pydantic AI.
- Captura los 429 y otros errores del proveedor y devuelve errores HTTP claros.
- No añadir dependencias de pago ni servicios con coste.
- **Tests siempre en la rama `test`**: escribir, modificar y ejecutar los tests (`pytest`) en la rama `test`, nunca en `development` ni en `main`.

## Qué NO hacer

- No hacer fine-tuning ni entrenar modelos.
- No usar una base de datos vectorial ni RAG: los datos son estructurados y se acceden con herramientas.
- No mandar `financial-health` ni `investment-analysis` completos en un solo prompt.
- No hardcodear claves.
- No presentar los datos como reales: son sintéticos.