# Team Context MCP Server

> Contexto compartido y persistente para equipos que trabajan con LLMs.

---

## El problema

Cuando un equipo de devs trabaja con LLMs, el contexto está fragmentado en tres niveles:

**Dentro de una sesión:** cada sesión arranca desde cero. Las mismas decisiones de arquitectura se explican una y otra vez. Las skills del equipo se cargan todas siempre, aunque solo 2 sean relevantes para el task actual.

**Entre proyectos:** cuando alguien resuelve un bug crítico en el backend, ese conocimiento no llega al equipo de frontend aunque el patrón sea idéntico. Cada proyecto aprende las mismas lecciones por separado.

**Cuando alguien se va:** el dev que resolvió la race condition del worker de pagos se fue hace seis meses. Nadie recuerda cómo lo hizo. El próximo que lo encuentre empieza de cero.

Tokens desperdiciados, tiempo desperdiciado, y conocimiento institucional que se evapora.

## La solución

Un servidor MCP que inyecta silenciosamente el contexto relevante antes de cada llamada al LLM.

El dev escribe su prompt normalmente. El servidor encuentra qué es relevante — skills, decisiones de arquitectura, PRs pasados, bugs históricos — y lo agrega al contexto. El LLM responde como si conociera el proyecto y la historia del equipo.

Resuelve los tres problemas:
- **Sesión:** indexa skills y decisiones del repo, disponibles en cualquier sesión
- **Cross-proyecto:** debug memory compartida entre todos los repos del equipo
- **Rotación de equipo:** el conocimiento queda en la DB, no en la cabeza de una persona

### Sistema híbrido: DB local + LLM de turno

Este proyecto **no llama a ninguna API de LLM**. No tiene `OPENAI_API_KEY`, no hace requests a Anthropic, no genera texto.

Lo que hace es distinto: mantiene una base de conocimiento local (SQLite + embeddings) y la expone vía protocolo MCP. El LLM que estés usando — Claude, GPT-4, Gemini, el modelo local que corre en Ollama — lee ese contexto a través del protocolo estándar antes de generar su respuesta.

```
Tu LLM favorito
      │
      │ "necesito contexto para este prompt"
      ▼
  MCP Server  ←── lee ──→  ~/.team-mcp/proyecto.db      (contexto del proyecto)
      │          └──────→  ~/.team-mcp/debug-memory.db  (bugs históricos cross-proyecto)
      │                     (embeddings locales, sin cloud, sin API key)
      │
      │ "acá tenés los fragmentos más relevantes"
      ▼
Tu LLM favorito genera la respuesta
```

**El sistema es agnóstico al LLM.** Funciona igual con Claude Code, Cursor, GitHub Copilot o cualquier cliente que soporte MCP. Cambiar de proveedor de LLM no requiere ningún cambio en el servidor ni en el índice.

**¿Por qué esta separación?** Porque el conocimiento del equipo es tuyo — no debería vivir en la nube ni quedar atado a un proveedor. Si mañana cambiás de Claude a GPT-5, o a un modelo local en Ollama, el índice y toda la base de conocimiento siguen intactos. Zero lock-in.

---

## Arquitectura: dos componentes

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI LOCAL                            │
│                    (team-mcp <cmd>)                         │
│                                                             │
│  • Indexa el repo (skills, team memory, docs, git log)      │
│  • Scrapea bug fixes de GitHub → debug memory               │
│  • Guarda memorias de sesión                                │
│  • Busca en el índice desde la terminal                     │
│  • Arranca el servidor MCP                                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ escribe / lee
                           ▼
            ┌──────────────────────────────┐
            │  ~/.team-mcp/proyecto.db     │  ← por proyecto
            │  ~/.team-mcp/debug-memory.db │  ← cross-proyecto
            └──────────────┬───────────────┘
                           │ lee
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      MCP SERVER                             │
│                  (stdio transport)                          │
│                                                             │
│  • get_context(prompt)        → contexto rankeado           │
│                                 + debug history si es error │
│  • query_debug_history(query) → bugs históricos similares   │
│  • list_skills()              → lista lo indexado           │
│  • add_memory(content)        → guarda memoria desde el LLM │
└──────────────────────────┬──────────────────────────────────┘
                           │ MCP protocol
                           ▼
              Claude / Cursor / Copilot / cualquier cliente MCP
```

**La CLI** es la herramienta del dev: indexa el repo, scrapea bug history, guarda decisiones, inspecciona el índice.

**El servidor MCP** es lo que el LLM consume: recibe prompts, busca en ambas DBs y devuelve los fragmentos más relevantes.

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

### Debug Memory — historia cross-proyecto de bugs

Los repos del equipo acumulan años de bug fixes documentados en PRs que nadie recuerda. El scraper los indexa en una DB separada, disponible desde cualquier proyecto:

```
~/.team-mcp/
├── mi-api.db          ← contexto específico del proyecto
├── otro-proyecto.db   ← contexto específico del proyecto
└── debug-memory.db    ← bug fixes históricos, cross-proyecto
```

La `debug-memory.db` **no depende del proyecto activo**. Sin importar desde qué repo estés trabajando, la historia de bugs del equipo siempre está disponible.

#### Cómo funciona

**1. Scraping** — el scraper llama a la API de GitHub y busca PRs cerrados/mergeados con labels de bug (`bug`, `fix`, `hotfix`, `regression`, etc.). De cada PR extrae título, descripción del problema, solución aplicada, archivos modificados, autor y fecha. El token se resuelve automáticamente desde `gh` CLI.

**2. Embeddings** — el texto combinado de título + problema + solución se convierte en un vector de 384 dimensiones usando el mismo modelo `all-MiniLM-L6-v2` del resto del sistema. Se guarda en `debug-memory.db` junto con los metadatos.

**3. Consulta por similitud** — cuando llega una query, se genera su embedding y se buscan los N vectores más cercanos usando `sqlite-vec`. El resultado incluye el bug histórico, la solución aplicada y el link al PR original.

**4. Inyección proactiva** — `get_context` detecta si el prompt contiene patrones de error (`exception`, `traceback`, `race condition`, `timeout`, etc.) y busca automáticamente en debug memory antes de responder. El LLM recibe el contexto del proyecto *y* los bugs históricos similares en una sola llamada.

```
Dev: "tenemos una race condition en el worker de pagos"

LLM recibe via get_context:
  → contexto del proyecto actual (skills, arquitectura)
  → [debug memory] "Hace 8 meses, mismo patrón en el worker de notificaciones.
     Solución: distributed locks con Redis. PR: github.com/org/repo/pull/234"
```

Ese conocimiento habría desaparecido cuando el dev original se fue. Ahora está indexado.

**Setup para el equipo:**

```bash
# Apuntar a los repos propios (el token se toma de gh CLI automáticamente)
team-mcp debug-scrape --repo mi-empresa/backend --repo mi-empresa/payments-api --max-prs 200
team-mcp debug-embed
```

---

## Continuidad entre sesiones

Cada sesión con el LLM arranca desde cero. Las decisiones tomadas durante la sesión se pierden si no se guardan.

Usá `add-memory` para cosas que **no quedaron en ningún commit ni PR**: decisiones tomadas en una call, en Slack, o durante la sesión misma.

```bash
# Útil: decisión que no tiene commit asociado
team-mcp add-memory "Descartamos migrar a microservicios — discutido en call del 2024-03-15, no escala el equipo"

# No hace falta: si hay un commit con buen mensaje, index-prs ya lo levanta
# team-mcp add-memory "Movimos auth a middleware — ver PR #52"  ← innecesario
```

En la próxima sesión, el servidor inyecta esos fragmentos automáticamente cuando el prompt es relevante.

**Tip:** al final de la sesión, pedile al LLM `"qué decisiones tomamos hoy que no están en ningún commit"` y usá eso como input para `add-memory`.

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

**Ejemplo real:** el equipo tiene tres convenciones de logging acumuladas a lo largo del tiempo.

```
skills/logging-v1.md   → status: deprecated  → score: ~0.08
skills/logging-v2.md   → status: deprecated  → score: ~0.09
skills/logging-v3.md   → (activo)            → score: 0.87
```

Cuando el LLM recibe `"add logging to this service"`, el servidor devuelve `logging-v3.md` con score dominante. El equipo no tuvo que borrar ni migrar nada — solo marcar el frontmatter.

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

Una sola instalación, múltiples proyectos. Cada proyecto tiene su propia DB detectada automáticamente desde `git remote origin`. La debug memory es compartida entre todos.

```
~/.team-mcp/
   mi-api.db          ← índice del proyecto mi-api
   otro-repo.db       ← índice del proyecto otro-repo
   frontend.db        ← índice del proyecto frontend
   debug-memory.db    ← bugs históricos, disponible en todos los proyectos
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
# ── Contexto del proyecto ─────────────────────────────────────────────────────
team-mcp init                        # Indexa skills, team memory y docs del repo
team-mcp init --reset                # Borra el índice existente y re-indexa desde cero
team-mcp index-prs                   # Indexa historial de commits como contexto de PRs
team-mcp index-prs --limit 100       # Limita la cantidad de commits a indexar

# ── Memorias de sesión ────────────────────────────────────────────────────────
team-mcp add-memory "texto"          # Guarda una decisión o contexto en la DB
team-mcp add-memory "texto" -p repo  # Especifica el proyecto manualmente

# ── Inspección ────────────────────────────────────────────────────────────────
team-mcp search "query"              # Busca en el índice desde la terminal
team-mcp search "query" --type skill # Filtra por tipo: skill | memory | pr | doc
team-mcp status                      # Muestra cuántos documentos hay indexados por tipo

# ── Debug Memory (cross-proyecto) ─────────────────────────────────────────────
team-mcp debug-scrape --repo org/repo --max-prs 200  # Scrapea bug fixes de GitHub
team-mcp debug-embed                 # Genera embeddings para los eventos scrapeados
team-mcp debug-query "race condition async worker"   # Busca bugs históricos similares
team-mcp debug-stats                 # Muestra cobertura: repos indexados y total de eventos

# ── Gestión de proyectos ──────────────────────────────────────────────────────
team-mcp projects                    # Lista todos los proyectos indexados en ~/.team-mcp/
team-mcp delete-project <nombre>     # Borra el índice de un proyecto (pide confirmación)
team-mcp delete-project <nombre> -y  # Borra sin confirmación

# ── Servidor ──────────────────────────────────────────────────────────────────
team-mcp serve                       # Arranca el servidor MCP (para el cliente LLM)
```

`init` es idempotente: si ya existe un documento con el mismo `source_path`, lo reemplaza. Podés correrlo en cada sesión sin generar duplicados.

Para `debug-scrape`, el token de GitHub se resuelve automáticamente desde `gh` CLI si está autenticado (`gh auth login`). Sin token funciona con límite de 60 requests/hora.

---

## Integración con clientes MCP

El repositorio incluye un `.mcp.json` listo para usar. Cualquier cliente compatible (Claude Code, Cursor, Windsurf, etc.) que abra este workspace lo detecta automáticamente.

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

---

## Cómo probarlo sin cliente LLM

El SDK incluye un inspector visual. Arrancalo con:

```bash
source .venv/bin/activate
mcp dev src/team_context_mcp/server.py
```

Abre una UI en `http://localhost:5173` donde podés llamar a las tools manualmente:

- `get_context` → pasale un prompt con un error y ves que devuelve contexto del proyecto + bugs históricos
- `query_debug_history` → búsqueda manual en debug memory
- `list_skills` → lista lo que hay indexado
- `add_memory` → agrega una memoria desde la UI

### Probar el flujo completo

```bash
# 1. Indexar el repo actual
team-mcp init && team-mcp index-prs

# 2. Scrapear bug history (usa gh CLI para el token)
team-mcp debug-scrape --repo tiangolo/fastapi --max-prs 20
team-mcp debug-embed

# 3. Probar búsqueda
team-mcp debug-query "race condition async"
team-mcp search "por qué sacamos Redis"
```

---

## Decisiones de diseño

**SQLite + sqlite-vec** — La alternativa obvia era Chroma o Qdrant. Se descartaron porque requieren un proceso servidor separado, añaden latencia de red y complican el setup en CI. SQLite es un archivo local: latencia cero, zero-config, portable entre máquinas con un `cp`.

**DB separada para debug memory** — Los bug patterns (race condition en async worker, deadlock en transacciones) son relevantes *entre* proyectos, no *dentro* de uno. Mezclarlos con el contexto del proyecto contaminaría el ranking. Una DB global elimina ese problema y permite que el conocimiento fluya entre repos sin configuración.

**all-MiniLM-L6-v2** — Modelos más grandes (e5-large, bge-large) tienen mejor recall pero requieren GPU o 3–4× más tiempo de CPU. `all-MiniLM-L6-v2` corre en 50–80ms por batch en cualquier laptop, produce vectores de 384 dimensiones con precisión suficiente para contexto técnico, y no levanta el ventilador. El tradeoff es correcto para este dominio.

**Ranking híbrido en vez de solo similitud vectorial** — La similitud coseno sola trata igual a un doc de hace 3 años que uno de la semana pasada. El componente `recency` evita que decisiones obsoletas dominen el ranking. El componente `priority` permite que `docs/architecture.md` siempre aparezca aunque la similitud semántica no sea la más alta.

**Token de GitHub desde gh CLI** — En vez de requerir que el usuario configure un `.env`, el scraper detecta automáticamente el token del `gh` CLI si está autenticado. El 100% de los devs que usan GitHub ya tienen `gh` instalado y autenticado.

---

## Stack

| Componente   | Tecnología                                               |
| ------------ | -------------------------------------------------------- |
| Embeddings   | `sentence-transformers` / `all-MiniLM-L6-v2` — CPU only |
| Vector DB    | SQLite + `sqlite-vec` — local, sin servidor externo      |
| CLI          | Click + Rich                                             |
| Protocolo    | MCP estándar (FastMCP) — cualquier cliente compatible    |
| Git          | GitPython — detección de proyecto y lectura de log       |
| GitHub API   | urllib (stdlib) — sin dependencias extra para el scraper |

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

Proyecto en desarrollo activo. Construido como portfolio para demostrar el uso práctico de embeddings, RAG, MCP y tooling para flujos de trabajo de IA en equipos reales.
