FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MPLBACKEND=Agg

WORKDIR /opt/scalabel/scalable_json_to_tilewise_annotation

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY scalable_json_to_tilewise_annotation/pyproject.toml ./pyproject.toml
RUN uv sync --no-dev

COPY scalable_json_to_tilewise_annotation/src ./src
RUN mkdir -p run logs

EXPOSE 8787

CMD ["uv", "run", "--no-sync", "python", "src/service.py"]
