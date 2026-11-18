# Precisa de Node (o Agent SDK executa o CLI do Claude Code) e de Python.
# Base Node porque instalar Python no Debian é mais simples que o contrário.
FROM node:20-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

# O Claude Code recusa rodar como root e precisa de HOME gravável.
RUN useradd -m -u 10001 mesa
WORKDIR /app

COPY requirements.txt .
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH="/venv/bin:${PATH}"

COPY app/ ./app/
COPY static/ ./static/
COPY scripts/ ./scripts/

# Pula o wizard de onboarding do Claude Code no primeiro uso.
RUN mkdir -p /home/mesa/.claude /app/campanha && echo '{"hasCompletedOnboarding":true}' > /home/mesa/.claude.json \
    && chown -R mesa:mesa /home/mesa /app

USER mesa
ENV HOME=/home/mesa \
    CAMPAIGN_DIR=/app/campanha \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
