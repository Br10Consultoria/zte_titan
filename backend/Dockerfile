FROM python:3.11-slim

WORKDIR /app

# Instala dependências do sistema, timezone e ferramentas SNMP
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    tzdata \
    snmp \
    snmp-mibs-downloader \
    && ln -sf /usr/share/zoneinfo/America/Sao_Paulo /etc/localtime \
    && echo "America/Sao_Paulo" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Habilita MIBs SNMP
RUN echo 'mibs +ALL' >> /etc/snmp/snmp.conf 2>/dev/null || true

ENV TZ=America/Sao_Paulo

# Copia e instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código do backend
COPY . .

# O frontend agora é mapeado via volume no docker-compose.yml
# COPY ../frontend /app/frontend

# Cria diretório para o banco de dados
RUN mkdir -p /app/data

# Porta da aplicação
EXPOSE 8000

# Comando de inicialização
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
