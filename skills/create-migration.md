# Skill: Create Database Migration

## Cuándo usar
Cuando se necesita modificar el esquema de la DB: agregar tabla, columna, índice, o hacer cambios de datos.

## Convención del equipo
- Migraciones en `migrations/` con nombre `YYYYMMDD_descripcion.sql` o usando Alembic
- Siempre incluir `up` y `down` (reversible)
- Las migraciones nunca deben eliminar datos sin respaldo confirmado
- Revisar con el equipo antes de hacer DROP en producción

## Ejemplo (Alembic)

```python
def upgrade():
    op.add_column('users', sa.Column('avatar_url', sa.String(500), nullable=True))
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

def downgrade():
    op.drop_index('ix_users_email', 'users')
    op.drop_column('users', 'avatar_url')
```

## Reglas
- No hardcodear IDs en migraciones de datos
- Probar la migración en staging antes de producción
- Documentar en el PR qué cambio de negocio requirió este cambio de esquema
