#!/bin/bash
# Instalación de Team Context MCP
# Instala torch CPU-only primero para evitar problems de compatibilidad de GPU.

set -e

echo "→ Creando entorno virtual..."
python3 -m venv .venv

echo "→ Instalando PyTorch (CPU only, ~200MB en vez de 2GB)..."
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu --quiet

echo "→ Instalando dependencias..."
.venv/bin/pip install "mcp[cli]" sentence-transformers sqlite-vec click gitpython rich pydantic --quiet

echo "→ Instalando paquete..."
.venv/bin/pip install -e . --quiet

echo ""
echo "✓ Instalación completa."
echo ""
echo "Próximos pasos:"
echo "  source .venv/bin/activate"
echo "  team-mcp init"
echo "  team-mcp serve"
