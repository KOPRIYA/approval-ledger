FROM python:3.11-slim
WORKDIR /app
COPY approval_ledger ./approval_ledger
COPY tests ./tests
RUN python -m unittest discover -s tests -v
RUN useradd --create-home appuser && mkdir /data && chown appuser /data
USER appuser
CMD ["python", "-m", "approval_ledger", "demo", "--out", "/data/demo"]
