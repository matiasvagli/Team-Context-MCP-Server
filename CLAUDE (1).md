# Project Handoff — Team Context MCP Server

## Qué es esto

Un servidor MCP (Model Context Protocol) que da a equipos de desarrollo contexto compartido y persistente cuando trabajan con LLMs (Claude, GPT, Gemini o cualquier cliente compatible con MCP).

El desarrollador trabaja normalmente con su LLM.
El MCP inyecta silenciosamente el contexto correcto y las tools necesarias basándose en el conocimiento del proyecto y del equipo.

## Problema que resuelve

Hoy un equipo de 5 devs trabajando con LLMs tiene el contexto fragmentado:

- Cada sesión del LLM arranca desde cero
- Se repite el mismo contexto una y otra vez
- No hay memoria compartida de decisiones arquitectónicas
- Nadie recuerda qué se intentó y falló semanas atrás
- Las tools/skills se cargan siempre completas aunque no sean relevantes

Resultado: fricción en el flujo de trabajo, decisiones técnicas que se pierden, tokens y tiempo desperdiciados.

## Solución

Un MCP Server local que agrega contexto relevante antes de enviar el prompt al LLM.

El sistema consulta tres fuentes de conocimiento del proyecto:

- Skills del equipo
- Memoria técnica del proyecto
- Historial de Pull Requests

El LLM recibe solo el contexto relevante para la tarea actual.

## Arquitectura

```
Dev prompt
    │
    ▼
MCP Server
    ├─ embedding del prompt
    ├─ similarity search → skills relevantes
    ├─ similarity search → memoria técnica del equipo
    ├─ similarity search → PRs / code reviews
    │
    ▼
LLM (Claude / GPT / Gemini)
    │
    ▼
Respuesta con contexto real del proyecto
```

## Componentes del sistema

### 1. Skills DB

Base de conocimiento con herramientas y patrones del equipo.

```
skills/
   create-endpoint.md
   create-migration.md
   add-event-handler.md
```

Cada skill incluye cuándo usarla, cómo usarla, y ejemplos reales.

Las skills se indexan con embeddings. Cuando llega un prompt:

```
prompt → embedding → similarity search → tools relevantes
```

En vez de cargar 40 tools, el sistema carga solo 3-5 relevantes.

### 2. Team Knowledge Memory

Memoria técnica compartida del proyecto. Contiene decisiones arquitectónicas, convenciones del equipo, experimentos fallidos, patrones adoptados.

Este archivo (`team/context.md`) se indexa con prioridad alta en el sistema.

### 3. Embeddings de PRs y Code Reviews

El sistema indexa automáticamente los Pull Requests. Se almacenan embeddings de título, descripción, diff relevante y comentarios de review.

Esto permite preguntas como:

> "Why did we remove Redis from the service layer?"

Y recuperar directamente el PR donde se tomó esa decisión.

La documentación emerge automáticamente del trabajo real del equipo.

## Multi-proyecto: namespace en la DB

Todos los proyectos comparten una sola vector DB. Cada registro tiene metadata con el proyecto al que pertenece.

```json
{
  "embedding": [...],
  "content": "...",
  "metadata": {
    "project": "mi-repo",
    "type": "pr",
    "date": "2026-03-10"
  }
}
```

La query filtra por proyecto antes del similarity search:

```sql
WHERE metadata.project = 'mi-repo'
ORDER BY similarity DESC
LIMIT 5
```

El `mcp init` toma el nombre del proyecto automáticamente del `git remote`. Sin configuración manual.

Ventaja adicional: queries cross-project. "¿Algún otro proyecto del equipo resolvió este problema antes?" Solo cambiando el WHERE.

## Ranking del contexto

El MCP no devuelve simplemente los resultados más similares. Cada resultado se pondera por:

```
score = semantic_similarity + architectural_priority + recency
```

- **semantic_similarity**: similitud semántica del embedding
- **architectural_priority**: peso definido en `mcp.config.json` donde el equipo marca los archivos clave del sistema
- **recency**: cambios más recientes tienen mayor peso

Ejemplo de config:

```json
{
  "priority_files": [
    "docs/architecture.md",
    "team/context.md",
    "src/domain/"
  ]
}
```

## Bootstrapping

Cuando el MCP se instala por primera vez:

```bash
mcp init
```

Se escanea el repo y se indexan automáticamente: README, `docs/`, archivos de arquitectura, skills del equipo, partes relevantes del código.

Luego el sistema se mantiene actualizado con eventos del repositorio (PR merged, release created).

## Stack técnico

| Componente      | Tecnología                                           |
| --------------- | ---------------------------------------------------- |
| Embeddings      | `sentence-transformers` / `all-MiniLM-L6-v2`         |
| Vector DB       | SQLite + `sqlite-vec` (ChromaDB si escala)           |
| Modelo local    | Phi-3 mini o Qwen 1.5B — solo routing, no generación |
| Protocolo       | MCP estándar — agnóstico al cliente LLM              |
| Integración Git | git hooks / webhooks / CLI post-merge                |

## Lo que NO hace

El sistema no modifica el prompt del usuario. No optimiza, no resume, no traduce. Estas estrategias introducen errores silenciosos que el usuario no ve.

El modelo local solo clasifica y routea. La generación queda a cargo del LLM cloud.

## Demo: qué se ve cuando funciona

```bash
mcp init        # escanea el repo, crea la DB
mcp index-prs   # indexa el historial de PRs
```

Query de un dev:

```
> create a new REST endpoint for user profiles
```

Lo que el MCP inyecta silenciosamente al LLM:

```
[skill]  create-endpoint.md        relevance: 0.94
[memory] Architecture principles   relevance: 0.87
[pr]     PR #89: /orders endpoint  relevance: 0.81
```

El LLM responde siguiendo las convenciones reales del equipo, sin que el dev haya explicado nada.

## Por dónde arrancar

1. Servidor MCP básico que responde a un cliente
2. Skills DB con embeddings y similarity search funcionando
3. Integración con Claude Code
4. Namespace multi-proyecto en la DB
5. Indexado de PRs via Git hooks
6. Ranking con architectural_priority

## Conexión con SkillAudit

SkillAudit analiza MCP servers de terceros para detectar comportamiento malicioso. Este proyecto construye un MCP server propio. Son el mismo dominio desde ángulos distintos — juntos muestran que entendés el ecosistema MCP end to end.

## Decisiones tomadas

| Decisión                       | Alternativa descartada            | Por qué                                          |
| ------------------------------ | --------------------------------- | ------------------------------------------------ |
| Modelo local solo para routing | Modelo local para resumir prompts | Resumir introduce errores silenciosos            |
| MCP estándar                   | Tool propietaria                  | Agnóstico al cliente, funciona con cualquier LLM |
| Embeddings de PRs              | Documentación manual              | La doc emerge sola del trabajo real              |
| Namespace en DB única          | Una DB por proyecto               | Menos overhead, permite queries cross-project    |
| sqlite-vec para empezar        | ChromaDB directo                  | Menos dependencias, más fácil de clonar y probar |
