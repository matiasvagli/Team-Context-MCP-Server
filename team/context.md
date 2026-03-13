# Team Knowledge — Context

## Stack actual
- Backend: FastAPI (Python 3.10+)
- DB: PostgreSQL 15 con Alembic para migraciones
- Frontend: Next.js 14 (App Router)
- Infra: Docker Compose en dev, AWS ECS en prod
- CI: GitHub Actions

## Decisiones arquitectónicas

### 2026-01 — Removimos Redis del service layer
Redis introducía complejidad de invalidación de cache que generaba bugs intermitentes.
Ahora el caching se hace a nivel de CDN para assets estáticos y en la DB con índices.
Ver PR #47 para el detalle.

### 2025-11 — Migramos de REST a un híbrido REST + eventos internos
Los eventos internos usan una tabla `events` en PostgreSQL (outbox pattern).
No usamos Kafka ni RabbitMQ — la carga actual no lo justifica.

### 2025-09 — Decisión de no usar ORM completo
Usamos `asyncpg` + queries SQL escritas a mano para queries críticas.
SQLAlchemy Core para queries dinámicas de filtrado.
Evitar SQLAlchemy ORM (demasiado mágico, difícil de debuggear en async).

## Convenciones

- Variables de entorno en `.env` + `config.py` con Pydantic Settings
- Logs estructurados JSON en producción, human-readable en dev
- Tests: pytest + httpx AsyncClient. Coverage mínimo: 80%
- PRs requieren al menos 1 aprobación antes de merge

## Experimentos fallidos

- **GraphQL (2025-06)**: descartado por overhead de implementación vs beneficio real
- **Event sourcing completo**: descartado, complejidad innecesaria para el tamaño del equipo
- **Microservicios tempranos**: nos quemamos, volvimos a monolito modular
