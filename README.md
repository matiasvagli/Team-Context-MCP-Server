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

### Sistema híbrido: DB local + LLM de turno

Este proyecto **no llama a ninguna API de LLM**. No tiene `OPENAI_API_KEY`, no hace requests a Anthropic, no genera texto.

Lo que hace es distinto: mantiene una base de conocimiento local (SQLite + embeddings) y la expone vía protocolo MCP. El LLM que estés usando — Claude, GPT-4, Gemini, el modelo local que corre en Ollama — lee ese contexto a través del protocolo estándar antes de generar su respuesta.

```
Tu LLM favorito
      │
      │ "necesito contexto para este prompt"
      ▼
  MCP Server  ←── lee ──→  ~/.team-mcp/proyecto.db
      │                     (embeddings locales,
      │                      sin cloud, sin API key)
      │
      │ "acá tenés los 5 fragmentos más relevantes"
      ▼
Tu LLM favorito genera la respuesta
```

**El sistema es agnóstico al LLM.** Funciona igual con Claude Code, Cursor, GitHub Copilot o cualquier cliente que soporte MCP. Cambiar de proveedor de LLM no requiere ningún cambio en el servidor ni en el índice.

**¿Por qué esta separación?** Porque el conocimiento del equipo es tuyo — no debería vivir en la nube ni quedar atado a un proveedor. El MCP Server indexa, rankea y filtra localmente usando el modelo de embeddings CPU-only. El LLM externo solo recibe el texto ya procesado: fragmentos limpios y relevantes. Si mañana cambiás de Claude a GPT-5, o a un modelo local en Ollama, el índice y toda la base de conocimiento siguen intactos. Zero lock-in.

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

### Flujo de datos

```
ESCRITURA (CLI)                        LECTURA (MCP Server)

 archivo .md / git log                  prompt del dev
        │                                      │
        ▼                                      ▼
  .mcpignore check               Embedder.embed(prompt)
  (¿ignorar?)                            │
        │                                ▼
        ▼                        sqlite-vec MATCH
  sanitizer.redact()              top_k × 10 candidatos
  (tokens, keys…)                        │
        │                                ▼
        ▼                     score = semantic×0.6
  Embedder.embed()               + priority×0.25
  (all-MiniLM-L6-v2)             + recency×0.15
        │                        - deprecated×0.9
        ▼                                │
  VectorDB.insert()                      ▼
  (documents + embeddings)   score < threshold → vacío
                                         │
                                         ▼
                                  top_k resultados
                                  → LLM context
```

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

El path es configurable en `mcp.config.json` con `skills_dir`. Si tus skills están en otra ubicación (por ejemplo `.agents/skills/` o `docs/prompts/`), actualizá ese campo antes de correr `team-mcp init`:

```json
{
  "skills_dir": ".agents/skills"
}
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

## Privacidad y filtros de seguridad

El indexer aplica dos capas de protección antes de guardar cualquier contenido en la DB.

### `.mcpignore` — excluir archivos y directorios

Creá un `.mcpignore` en la raíz del proyecto con la misma sintaxis que `.gitignore`:

```
# .mcpignore
.env
.env.*
*.pem
*.key
secrets/
credentials/
node_modules/
```

Cualquier archivo que coincida con un patrón es ignorado completamente durante el indexado. El repo incluye un `.mcpignore` de ejemplo con defaults razonables.

### Redacción automática de datos sensibles

Antes de embeber cualquier contenido (archivos, commits, docs), el indexer aplica una lista de regex sobre el texto. Si detecta un patrón sensible, lo reemplaza con `[REDACTED]` antes de guardar en la DB.

Patrones cubiertos:

| Tipo | Ejemplo detectado |
|------|-------------------|
| Anthropic API key | `sk-ant-api03-...` |
| OpenAI API key | `sk-...` |
| GitHub token | `ghp_...`, `ghs_...` |
| AWS access key | `AKIA...` |
| Bearer token | `Bearer eyJ...` |
| Private key block | `-----BEGIN PRIVATE KEY-----` |
| Database URL con credenciales | `postgres://user:pass@host/db` |
| Asignación genérica de secretos | `password = "abc123longvalue"` |

El contenido almacenado en la DB nunca contiene el valor original — solo el marcador `[REDACTED]`. El embedding se genera sobre el texto ya redactado.

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

**Ejemplo real:** el equipo tiene tres convenciones de logging acumuladas a lo largo del tiempo.

```
skills/logging-v1.md   → status: deprecated  → score: ~0.08
skills/logging-v2.md   → status: deprecated  → score: ~0.09
skills/logging-v3.md   → (activo)            → score: 0.87
```

Cuando el LLM recibe `"add logging to this service"`, el servidor devuelve `logging-v3.md` con score dominante. Las versiones anteriores existen en la DB pero nunca superan el threshold. El equipo no tuvo que borrar ni migrar nada — solo marcar el frontmatter.

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

# Instalar dependencias (CPU-only, funciona en cualquier máquina)
./install.sh
```

`install.sh` instala PyTorch CPU-only y el paquete en modo editable. Después de esto, el comando `team-mcp` queda disponible **globalmente en tu terminal** — no necesitás activar ningún entorno ni estar en el directorio del repo.

```bash
# Usalo desde cualquier proyecto, en cualquier terminal
cd ~/trabajo/mi-api
team-mcp init
team-mcp index-prs
```

> Si preferís un entorno aislado: `pipx install -e /ruta/a/Team-Context-MCP-Server` después de correr `install.sh` para torch.

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

# Gestión de proyectos
team-mcp projects                    # Lista todos los proyectos indexados en ~/.team-mcp/
team-mcp delete-project <nombre>     # Borra el índice de un proyecto (pide confirmación)
team-mcp delete-project <nombre> -y  # Borra sin confirmación

# Servidor
team-mcp serve                       # Arranca el servidor MCP (para el cliente LLM)
```

`init` es idempotente: si ya existe un documento con el mismo `source_path`, lo reemplaza. Podés correrlo en cada sesión sin generar duplicados.

---

## Integración con clientes MCP

El repositorio incluye un `.mcp.json` listo para usar. Cualquier cliente compatible (Claude Code, Cursor, Windsurf, Antigravity, etc.) que abra este workspace lo detecta automáticamente.

```json
{
  "mcpServers": {
    "team-context": {
      "command": "team-mcp",
      "args": ["serve"],
      "type": "stdio"
    }
  }
}
```

> **Importante:** El `.mcp.json` asume que `team-mcp` está en tu `$PATH` (instalado con `pipx`). Si el cliente no lo encuentra, usá la ruta absoluta:
> ```json
> "command": "/home/tu-usuario/.local/bin/team-mcp"
> ```
> Para saber la ruta exacta en tu máquina: `which team-mcp`

Cada cliente MCP tiene su propio archivo de configuración global:

| Cliente | Config global |
|---------|--------------|
| Claude Code | `~/.claude.json` |
| Claude Desktop | `~/.config/claude/claude_desktop_config.json` |
| Cursor / Windsurf | Settings UI del editor |

El servidor arranca automáticamente en background cuando el cliente lee el `.mcp.json` — no es necesario correr `team-mcp serve` a mano.

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

## Decisiones de diseño

**SQLite + sqlite-vec** — La alternativa obvia era Chroma o Qdrant. Se descartaron porque requieren un proceso servidor separado, añaden latencia de red y complican el setup en CI. SQLite es un archivo local: latencia cero, zero-config, portable entre máquinas con un `cp`.

**all-MiniLM-L6-v2** — Modelos más grandes (e5-large, bge-large) tienen mejor recall pero requieren GPU o 3–4× más tiempo de CPU. `all-MiniLM-L6-v2` corre en 50–80ms por batch en cualquier laptop, produce vectores de 384 dimensiones con precisión suficiente para contexto técnico, y no levanta el ventilador. El tradeoff es correcto para este dominio.

**Ranking híbrido en vez de solo similitud vectorial** — La similitud coseno sola tiene dos problemas conocidos: no distingue documentos populares de documentos relevantes, y trata igual a un doc de hace 3 años que uno de la semana pasada. El componente `recency` evita que decisiones obsoletas dominen el ranking. El componente `priority` permite que `docs/architecture.md` siempre aparezca aunque la similitud semántica no sea la más alta. Sin esto, el LLM recibiría contexto técnicamente correcto pero desactualizado.

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

## Debug Memory — historia cross-proyecto de bugs (rama en desarrollo)

> **Branch activo:** `feature/debug-memory` — feature en progreso, no mergeada a `main` todavía.

La rama agrega un sistema de memoria histórica de bugs que **vive separado de los proyectos**. El problema que resuelve: cuando un dev se va del equipo, el conocimiento de cómo se resolvieron bugs críticos se pierde. Esta feature lo preserva y lo hace queryable.

### Cómo funciona

Se scrapeaan PRs cerrados/mergeados con labels de bug de repos de GitHub y se guardan en una DB separada:

```
~/.team-mcp/
├── mi-api.db          ← contexto del proyecto (como antes)
├── otro-proyecto.db   ← contexto del proyecto (como antes)
└── debug-memory.db    ← cross-proyecto, siempre disponible ← NUEVO
```

La `debug-memory.db` **no depende del proyecto activo**. Sin importar desde qué repo estés trabajando, `debug-query` siempre accede a la misma base de conocimiento histórica.

### Flujo de uso — equipo real

El caso de uso real es apuntar a los repos propios del equipo. El token se resuelve automáticamente desde `gh` CLI si está autenticado:

```bash
# Indexar los repos del equipo (privados o públicos)
team-mcp debug-scrape --repo mi-empresa/backend --repo mi-empresa/payments-api --max-prs 200

# Generar embeddings
team-mcp debug-embed

# Consultar
team-mcp debug-query "race condition en worker de pagos"

# Ver cobertura
team-mcp debug-stats
```

Con `gh auth login` hecho una vez, no hace falta configurar ningún token — el scraper lo toma solo.

### Demo / testing con repos públicos

Para probar sin acceso a repos privados, usar repos open source con buena cultura de PRs:

```bash
team-mcp debug-scrape --repo tiangolo/fastapi --repo pallets/flask --max-prs 20
team-mcp debug-embed
team-mcp debug-query "dependency injection"
```

### Nueva tool MCP: `query_debug_history`

Una vez que la DB tiene datos, el LLM puede consultarla directamente:

```
query_debug_history("race condition en payment worker")
```

Devuelve los bugs históricos más similares con problema, solución y link al PR original. El LLM recibe contexto concreto en vez de tener que googlear o preguntar al equipo.

### Inyección proactiva — integrada en `get_context`

Cuando el LLM llama `get_context` con un prompt que parece un bug o error (contiene palabras como `exception`, `race condition`, `timeout`, etc.), el servidor **busca automáticamente en debug history** y adjunta los resultados relevantes — sin que el LLM tenga que decidir llamar `query_debug_history` por separado.

```
Dev: "tengo este traceback: ..."
LLM llama get_context (como siempre)
  → servidor detecta patrón de error
  → busca en debug-memory automáticamente
  → respuesta incluye contexto del proyecto + bugs históricos similares
```

`query_debug_history` sigue disponible para búsquedas manuales explícitas.

### Demo

```
Dev: "tenemos un race condition en el worker de pagos"

LLM (via query_debug_history): "Hace 8 meses tuvimos algo similar en el worker
de notificaciones. Lo resolvimos agregando distributed locks con Redis.
Acá está el PR con la implementación completa: github.com/org/repo/pull/234"
```

Ese conocimiento habría desaparecido cuando el dev original se fue. Ahora está indexado.

---

## Status

Proyecto en desarrollo. Construido como portfolio para demostrar el uso práctico de embeddings, MCP y tooling para flujos de trabajo de IA.
