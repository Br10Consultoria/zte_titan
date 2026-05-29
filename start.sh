#!/bin/bash
# ============================================================
# ZTE Titan Manager - Script de Inicialização (sem Docker)
# ============================================================

set -e

echo "=============================================="
echo "  ZTE Titan Manager - Inicializando..."
echo "=============================================="

# Verifica Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 não encontrado. Instale Python 3.11+"
    exit 1
fi

# Verifica Redis
if ! command -v redis-server &> /dev/null && ! systemctl is-active --quiet redis 2>/dev/null; then
    echo "⚠️  Redis não encontrado ou não está rodando."
    echo "   O sistema funcionará sem cache. Para instalar:"
    echo "   Ubuntu/Debian: sudo apt install redis-server && sudo systemctl start redis"
    echo "   CentOS/RHEL:   sudo yum install redis && sudo systemctl start redis"
fi

# Cria e ativa ambiente virtual
if [ ! -d "venv" ]; then
    echo "📦 Criando ambiente virtual Python..."
    python3 -m venv venv
fi

source venv/bin/activate

# Instala dependências
echo "📦 Instalando dependências..."
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt

# Cria arquivo .env se não existir
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✅ Arquivo .env criado a partir do .env.example"
    echo "⚠️  IMPORTANTE: Edite o .env e altere o SECRET_KEY!"
fi

# Cria diretório de dados
mkdir -p data

# Inicia o servidor
echo ""
echo "=============================================="
echo "  ✅ Iniciando ZTE Titan Manager..."
echo "  🌐 Acesse: http://localhost:8000"
echo "  👤 Login padrão: admin / Admin@2024!"
echo "  📖 API Docs: http://localhost:8000/api/docs"
echo "=============================================="
echo ""

cd backend
DATABASE_URL="sqlite:///$(pwd)/../data/zte_titan.db" \
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
