# Team Context MCP Server

> Contexto compartido y persistente para equipos que trabajan con LLMs.

---

## El problema

Cuando un equipo de 5 devs trabaja con LLMs, el contexto está fragmentado.

Cada sesión arranca desde cero. Las mismas decisiones de arquitectura se explican una y otra vez. Nadie recuerda qué se intentó y falló el sprint pasado. Todas las tools se cargan siempre, aunque solo 2 sean relevantes.

Tokens desperdiciados, tiempo desperdiciado, y respuestas del LLM que ignoran cómo trabaja tu equipo.

## La solución

Un servidor MCP que inyecta silenciosamente el contexto relevante antes de cada llamada al LLM.

El dev escribe su prompt normalmente. El servidor encuentra qué es relevante — skills, decisiones de arquitectura, PRs pasados — y lo agrega al contexto. El LLM responde como si conociera el proyecto.

```
Prompt del dev
    │
    ▼
MCP Server
    ├─ similarity search → skills relevantes
    ├─ similarity search → conocimiento del equipo
    ├─ similarity search → historial de PRs
    │
    ▼
LLM (Claude / GPT / Gemini — el que uses)
    │
    ▼
Respuesta que sigue las convenciones reales del equipo
```

## Cómo funciona

### Skills

Las herramientas y patrones del equipo, guardadas como archivos markdown e indexadas con embeddings.

```
skills/
   create-endpoint.md
   create-migration.md
   add-event-handler.md
```

En vez de cargar 40 tools en cada contexto, el servidor carga las 3-5 que son realmente relevantes para el prompt actual.

### Team Knowledge Memory

Memoria técnica compartida del proyecto. Principios de arquitectura, convenciones, experimentos fallidos, patrones adoptados. Indexada con prioridad alta para que siempre aparezca cuando es relevante.

### Historial de PRs

El servidor indexa automáticamente tus Pull Requests — título, descripción, diff, comentarios de review.

Preguntás:

```
¿Por qué sacamos Redis del service layer?
```

Obtenés:

```
PR #47 — Redis removido por race conditions en writes concurrentes
Decisión tomada por: @dev1, @dev2
Merged: 2026-02-14
```

Documentación que se escribe sola, del trabajo que el equipo ya está haciendo.

## Soporte multi-proyecto

Una sola vector DB, namespace por proyecto. El servidor lee el nombre del proyecto desde `git remote` al hacer init. Sin configuración manual.

También soporta queries cross-project — útil para encontrar cómo se resolvieron problemas similares en otros repos.

## Ranking del contexto

Los resultados no se ordenan solo por similitud. Cada resultado se puntúa por:

```
score = semantic_similarity + architectural_priority + recency
```

Los archivos prioritarios se definen en `mcp.config.json`:

```json
{
  "priority_files": [
    "docs/architecture.md",
    "team/context.md",
    "src/domain/"
  ]
}
```

## Instalación

```bash
# Clonar el repo
git clone https://github.com/tu-usuario/Team-Context-MCP-Server
cd Team-Context-MCP-Server

# Instalar (CPU-only, funciona en cualquier máquina sin importar la GPU)
./install.sh

# Activar el entorno
source .venv/bin/activate

# Inicializar en tu proyecto
cd tu-proyecto
team-mcp init

# Indexar historial de commits/PRs
team-mcp index-prs
```

## Demo real

El dev escribe:

```
> create a new REST endpoint for user profiles
```

Lo que el MCP busca y rankea:

```
Batches: 100%|████████████████████| 1/1 [00:00<00:00, 110.93it/s]

┏━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Type     ┃ Score  ┃ Source                         ┃ Preview                                                      ┃
┡━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ skill    │ 0.8743 │ skills/create-endpoint.md      │ # Skill: Create REST Endpoint  ## Cuándo usar Cuando se pide │
│          │        │                                │ crear un nuevo endp…                                         │
│ skill    │ 0.7252 │ skills/create-endpoint.md      │ # Skill: Create REST Endpoint  ## Cuándo usar Cuando se pide │
│          │        │                                │ crear un nuevo endp…                                         │
│ skill    │ 0.4781 │ skills/create-migration.md     │ # Skill: Create Database Migration  ## Cuándo usar Cuando se │
│          │        │                                │ necesita modificar …                                         │
│ memory   │ 0.3871 │ team/context.md                │ # Team Knowledge — Context  ## Stack actual - Backend:       │
│          │        │                                │ FastAPI (Python 3.10+) - …                                   │
│ skill    │ 0.3289 │ skills/create-migration.md     │ # Skill: Create Database Migration  ## Cuándo usar Cuando se │
│          │        │                                │ necesita modificar …                                         │
└──────────┴────────┴────────────────────────────────┴──────────────────────────────────────────────────────────────┘
```

El LLM recibe silenciosamente esos 5 fragmentos de contexto y responde siguiendo las convenciones reales del equipo. El dev no tuvo que explicar nada.

## Comandos disponibles

```bash
team-mcp init           # Escanea e indexa el repo (skills, team memory, docs)
team-mcp init --reset   # Re-indexa desde cero
team-mcp index-prs      # Indexa historial de commits como contexto de PRs
team-mcp search "query" # Busca en el índice desde la terminal
team-mcp status         # Muestra qué está indexado
team-mcp serve          # Arranca el servidor MCP (lo usa el cliente LLM)
```

## Integración con Claude Desktop / Claude Code

Copiá el contenido de `claude_mcp_config.json` a tu config de Claude:
- Linux/Mac: `~/.config/claude/claude_desktop_config.json`

## Stack

| Componente      | Tecnología                                                   |
| --------------- | ------------------------------------------------------------ |
| Embeddings      | `sentence-transformers` / `all-MiniLM-L6-v2` — corre en CPU |
| Vector DB       | SQLite + `sqlite-vec`                                        |
| Protocolo       | MCP estándar — funciona con cualquier cliente compatible     |
| Integración Git | git log / hooks                                              |

## Compatibilidad

- Claude Code / Claude Desktop
- Cursor
- GitHub Copilot (agent mode)
- Cualquier cliente compatible con MCP

## Cómo probarlo

### Sin Claude Desktop — inspector MCP en el browser

El SDK incluye un inspector visual. Arrancalo con:

```bash
source .venv/bin/activate
mcp dev src/team_context_mcp/server.py
```

Abre una UI en `http://localhost:5173` donde podés llamar a las tools manualmente:

- `get_context` → pasale un prompt y ves qué contexto devuelve rankeado
- `list_skills` → lista lo que hay indexado
- `add_memory` → agrega una memoria desde la UI

No necesitás ningún cliente LLM ni cuenta extra.

### Probar el indexado de PRs sin múltiples cuentas

El indexer usa `git log` local — cualquier commit ya es contexto válido. Podés agregar commits de prueba:

```bash
git commit --allow-empty -m "fix: removimos Redis del service layer por race conditions en writes concurrentes"
git commit --allow-empty -m "feat: migración a outbox pattern para eventos internos, descartamos Kafka"

team-mcp index-prs
team-mcp search "por qué sacamos Redis"
```

### Con Claude Desktop (el caso real)

```bash
mkdir -p ~/.config/claude
cp claude_mcp_config.json ~/.config/claude/claude_desktop_config.json
# Reiniciar Claude Desktop
```

Claude va a llamar `get_context` automáticamente cada vez que trabajes en el proyecto.

---

## Lo que NO hace

El servidor no modifica tu prompt. Sin optimización, sin resumen, sin traducción. Esas estrategias introducen errores semánticos silenciosos que el usuario no ve.

El sistema solo clasifica y routea. La generación queda a cargo de tu LLM.

---

## Status

Work in progress. Built as a portfolio project to demonstrate practical use of embeddings, MCP, and developer tooling for AI workflows.

Related project: [SkillAudit](https://github.com/matiasdev/skillaudit) — behavioral analysis of MCP servers. Same domain, different angle.
