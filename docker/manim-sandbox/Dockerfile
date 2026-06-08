FROM python:3.11-slim

# System deps for Manim CE
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libcairo2-dev \
    libpango1.0-dev \
    pkg-config \
    texlive-latex-base \
    texlive-fonts-recommended \
    texlive-latex-extra \
    dvipng \
    && rm -rf /var/lib/apt/lists/*

# Manim CE — pinned for reproducibility
RUN pip install --no-cache-dir manim==0.18.1

# Non-root user — runs all scene code as appuser
RUN useradd -m -u 1000 appuser
RUN mkdir -p /workspace/input /workspace/output && chown -R appuser /workspace

USER appuser
WORKDIR /workspace

COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
