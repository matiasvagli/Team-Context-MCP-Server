# Team Context MCP Server

> Contexto compartido y persistente para equipos que trabajan con LLMs.

---

## El problema

Cuando un equipo de devs trabaja con LLMs, el contexto está fragmentado.

Cada sesión arranca desde cero. Las mismas decisiones de arquitectura se explican una y otra vez. Nadie recuerda qué se intentó y falló el sprint pasado. Todas las tools se cargan siempre, aunque solo 2 sean relevantes.

Tokens desperdiciados, tiempo desperdiciado, y respuestas del LLM que ignoran cómo trabaja tu equipo.

## La solución

Un servidor MCP que inyecta silenciosamente el contexto relevante antes de cada llamada al LLM.

El dev escribe su prompt normalmente. El servidor encuentra qué es relevante — skills, decisiones de arquitectura, PRs pasados — y lo agrega al contexto. El LLM responde como si conociera el proyecto.

---

## Arquitectura: dos componentes

El sistema tiene dos partes que se complementan:

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI LOCAL                            │
│                    (team-mcp <cmd>)                         │
│                                                             │
│  • Indexa el repo (skills, team memory, docs, git log)      │
│  • Guarda memorias de sesión                                │
│  • Busca en el índice desde la terminal                     │
│  • Arranca el servidor MCP                                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ escribe / lee
                           ▼
                  ~/.team-mcp/{proyecto}.db
                  (SQLite + sqlite-vec, local)
                           │
                           │ lee
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      MCP SERVER                             │
│                  (stdio transport)                          │
│                                                             │
│  • get_context(prompt) → devuelve contexto rankeado         │
│  • list_skills()       → lista lo indexado                  │
│  • add_memory(content) → guarda memoria desde el LLM        │
└──────────────────────────┬──────────────────────────────────┘
                           │ MCP protocol
                           ▼
              Claude / Cursor / Copilot / cualquier cliente MCP
```

**La CLI** es la herramienta del dev: indexa el repo, guarda decisiones, inspecciona el índice.

**El servidor MCP** es lo que el LLM consume: recibe prompts, busca en la DB y devuelve los fragmentos de contexto más relevantes.

Ambos comparten la misma DB local. No hay servidor externo, no hay cloud, no hay red.

---

## Qué se indexa

### Skills

Las herramientas y patrones del equipo, guardadas como archivos markdown.

```
skills/
   create-endpoint.md
   create-migration.md
   add-event-handler.md
```

En vez de cargar 40 tools en cada contexto, el servidor carga las 3–5 que son realmente relevantes para el prompt actual.

### Team Knowledge Memory

Memoria técnica compartida del proyecto. Principios de arquitectura, convenciones, experimentos fallidos, patrones adoptados. Indexada con prioridad alta para que siempre aparezca cuando es relevante.

### Historial de PRs / commits

El indexer extrae automáticamente el historial de git — mensaje del commit y archivos modificados. Sin API de GitHub, sin configuración extra.

```
¿Por qué sacamos Redis del service layer?
→ Commit abc1234 — "fix: removimos Redis por race conditions en writes concurrentes"
   Archivos: src/cache.py, src/service/user.py
```

---

## Continuidad entre sesiones

Cada sesión con el LLM arranca desde cero. Las decisiones tomadas durante la sesión se pierden si no se guardan.

Para evitar esto, usá `add-memory` antes de cerrar:

```bash
team-mcp add-memory "Decidimos mover auth a un middleware dedicado — ver PR #52"
team-mcp add-memory "Descartamos JWT stateless por problemas con revocación de tokens"
```

En la próxima sesión, el servidor inyecta esos fragmentos automáticamente cuando el prompt es relevante.

**Tip:** al final de la sesión, pedile al LLM `"dame un resumen de las decisiones que tomamos hoy"` y usá eso como input para `add-memory`.

---

## Invalidación de contexto (deprecation)

Si una decisión de arquitectura quedó obsoleta, marcala como deprecated en el archivo correspondiente con frontmatter:

```markdown
---
status: deprecated
---

# Usar Redis para caché

...contenido...
```

Al correr `team-mcp init`, el skill se re-indexa con una penalización fuerte de score (`× 0.1`). No desaparece — el LLM puede verlo si lo busca explícitamente — pero nunca va a ganarle a un resultado activo en el ranking normal.

Esto resuelve el problema de que convivían contextos contradictorios (ej. "usar Redis" vs. "usar Valkey") sin que el LLM supiera cuál era vigente.

---

## Ranking del contexto

Los resultados no se ordenan solo por similitud vectorial. Cada resultado se puntúa por tres componentes:

```
score = (semantic_similarity × 0.6) + (priority × 0.25) + (recency × 0.15)
```

- **Semantic similarity**: similitud coseno entre el prompt y el documento
- **Priority**: peso configurable por archivo en `mcp.config.json`
- **Recency**: los documentos más recientes tienen ventaja

Si el score máximo está por debajo del umbral configurado (`similarity_threshold`), el servidor devuelve vacío. Mejor no dar contexto que dar contexto irrelevante.

### Configuración (`mcp.config.json`)

```json
{
  "priority_files": [
    "docs/architecture.md",
    "team/context.md",
    "src/domain/"
  ],
  "skills_dir": "skills",
  "team_dir": "team",
  "top_k": 5,
  "similarity_threshold": 0.35
}
```

Los archivos en `priority_files` reciben `priority = 0.95`. El resto usa el default por tipo (`skill: 0.9`, `memory: 0.85`, `pr: 0.7`, `doc: 0.6`).

---

## Soporte multi-proyecto

Una sola DB local (`~/.team-mcp/`), con un archivo por proyecto. El nombre se detecta automáticamente desde `git remote origin`. Sin configuración manual.

```
~/.team-mcp/
   mi-api.db
   otro-repo.db
   frontend.db
```

---

## Instalación

```bash
# Clonar el repo
git clone https://github.com/tu-usuario/Team-Context-MCP-Server
cd Team-Context-MCP-Server

# Instalar (CPU-only, funciona en cualquier máquina)
./install.sh

# Activar el entorno
source .venv/bin/activate

# Ir a tu proyecto e indexar
cd tu-proyecto
team-mcp init
team-mcp index-prs
```

---

## Comandos CLI

```bash
# Indexado
team-mcp init                        # Indexa skills, team memory y docs del repo
team-mcp init --reset                # Borra el índice existente y re-indexa desde cero
team-mcp index-prs                   # Indexa historial de commits como contexto de PRs
team-mcp index-prs --limit 100       # Limita la cantidad de commits a indexar

# Memorias de sesión
team-mcp add-memory "texto"          # Guarda una decisión o contexto en la DB
team-mcp add-memory "texto" -p repo  # Especifica el proyecto manualmente

# Inspección
team-mcp search "query"              # Busca en el índice desde la terminal
team-mcp search "query" --type skill # Filtra por tipo: skill | memory | pr | doc
team-mcp status                      # Muestra cuántos documentos hay indexados por tipo

# Servidor
team-mcp serve                       # Arranca el servidor MCP (para el cliente LLM)
```

`init` es idempotente: si ya existe un documento con el mismo `source_path`, lo reemplaza. Podés correrlo en cada sesión sin generar duplicados.

---

## Integración con Claude Desktop / Claude Code

Copiá el contenido de `claude_mcp_config.json` a tu config de Claude:

```bash
# Linux / Mac
cp claude_mcp_config.json ~/.config/claude/claude_desktop_config.json
# Reiniciar Claude Desktop
```

Claude va a llamar `get_context` automáticamente cada vez que trabajes en el proyecto.

---

## Cómo probarlo sin cliente LLM

El SDK incluye un inspector visual. Arrancalo con:

```bash
source .venv/bin/activate
mcp dev src/team_context_mcp/server.py
```

Abre una UI en `http://localhost:5173` donde podés llamar a las tools manualmente:

- `get_context` → pasale un prompt y ves qué contexto devuelve rankeado
- `list_skills` → lista lo que hay indexado
- `add_memory` → agrega una memoria desde la UI

### Probar el indexado de PRs

El indexer usa `git log` local — cualquier commit ya es contexto válido:

```bash
git commit --allow-empty -m "fix: removimos Redis del service layer por race conditions en writes concurrentes"
git commit --allow-empty -m "feat: migración a outbox pattern para eventos internos, descartamos Kafka"

team-mcp index-prs
team-mcp search "por qué sacamos Redis"
```

---

## Stack

| Componente   | Tecnología                                               |
| ------------ | -------------------------------------------------------- |
| Embeddings   | `sentence-transformers` / `all-MiniLM-L6-v2` — CPU only |
| Vector DB    | SQLite + `sqlite-vec` — local, sin servidor externo      |
| CLI          | Click + Rich                                             |
| Protocolo    | MCP estándar (FastMCP) — cualquier cliente compatible    |
| Git          | GitPython — detección de proyecto y lectura de log       |

## Compatibilidad

- Claude Code / Claude Desktop
- Cursor
- GitHub Copilot (agent mode)
- Cualquier cliente compatible con MCP

---

## Lo que NO hace

El servidor no modifica tu prompt. Sin optimización, sin resumen, sin traducción. Esas estrategias introducen errores semánticos silenciosos.

El sistema solo clasifica y routea. La generación queda a cargo de tu LLM.

---

## Status

Work in progress. Built as a portfolio project to demonstrate practical use of embeddings, MCP, and developer tooling for AI workflows.
