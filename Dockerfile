FROM python:3.12-slim-bookworm

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/home/legal/.cache/huggingface

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# torch 单独成层：wheel 有数百 MB，requirements.txt 变更不应触发重新下载。
RUN python -m pip install --upgrade pip \
    && python -m pip install --index-url "${TORCH_INDEX_URL}" "torch>=2.2,<3"

COPY requirements.txt ./
RUN python -m pip install -r requirements.txt

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin legal \
    && mkdir -p /home/legal/.cache/huggingface \
    && chown -R legal:legal /home/legal

COPY --chown=legal:legal . .
COPY docker/entrypoint.sh /usr/local/bin/legal-entrypoint
RUN chmod 0755 /usr/local/bin/legal-entrypoint

USER legal

EXPOSE 8000

ENTRYPOINT ["legal-entrypoint"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
