FROM python:3.13-slim

RUN pip install --no-cache-dir workspace-mcp

EXPOSE 8000

CMD ["workspace-mcp", "--transport", "streamable-http", "--tool-tier", "complete", "--tools", "gmail", "calendar"]
