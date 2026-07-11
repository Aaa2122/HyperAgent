FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md llm_schemas.py llm_checks.py prompt_strategist.md prompt_trader.md prompt_risk_review.md ./
COPY agent ./agent
RUN pip install --no-cache-dir .

ENV AGENT_MODE=paper
EXPOSE 8000
CMD ["uvicorn", "agent.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
