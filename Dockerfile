FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir -e .

COPY . .

CMD ["python", "-m", "atlas.control_plane.main"]
