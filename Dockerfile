# Deliberately minimal: the orchestrator is stdlib-only Python, so the image
# is just the interpreter plus the code. No pip install step exists at all.
FROM python:3.12-slim

WORKDIR /app
COPY orchestrator/ orchestrator/
COPY scripts/ scripts/
COPY tests/ tests/

# State (state.json, dashboard.html, logs) lives on a mounted volume so it
# survives container restarts and is inspectable from the host.
VOLUME ["/app/state"]

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "orchestrator"]
CMD ["run"]
