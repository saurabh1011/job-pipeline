FROM python:3.12-slim

# Install system deps for Playwright + Chromium + PDF export
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation \
    pandoc \
    wget \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Install typst (pandoc PDF engine)
RUN wget -qO /tmp/typst.tar.xz \
    "https://github.com/typst/typst/releases/download/v0.13.1/typst-x86_64-unknown-linux-musl.tar.xz" \
    && tar -xJf /tmp/typst.tar.xz -C /tmp \
    && mv /tmp/typst-x86_64-unknown-linux-musl/typst /usr/local/bin/typst \
    && rm -rf /tmp/typst*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (uses bundled Chromium)
RUN playwright install chromium --with-deps

COPY . .

# Data directory for persistent SQLite DB
RUN mkdir -p /data
ENV DB_PATH=/data/jobs.db

EXPOSE 8000

CMD ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8000"]
