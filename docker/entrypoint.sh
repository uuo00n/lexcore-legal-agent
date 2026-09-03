#!/bin/sh
set -eu

embedding_model="${EMBEDDING_MODEL:-}"
if [ -d /app/models/bge-small-zh-v1.5 ]; then
    case "$embedding_model" in
        ""|models/bge-small-zh-v1.5|/app/models/bge-small-zh-v1.5)
            export EMBEDDING_MODEL=/app/models/bge-small-zh-v1.5
            ;;
    esac
elif [ -z "$embedding_model" ] \
    || [ "$embedding_model" = "models/bge-small-zh-v1.5" ] \
    || [ "$embedding_model" = "/app/models/bge-small-zh-v1.5" ]; then
    export EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
fi

reranker_model="${RERANKER_MODEL:-}"
if [ -d /app/models/bge-reranker-base ]; then
    case "$reranker_model" in
        ""|models/bge-reranker-base|/app/models/bge-reranker-base)
            export RERANKER_MODEL=/app/models/bge-reranker-base
            ;;
    esac
elif [ -z "$reranker_model" ] \
    || [ "$reranker_model" = "models/bge-reranker-base" ] \
    || [ "$reranker_model" = "/app/models/bge-reranker-base" ]; then
    export RERANKER_MODEL=BAAI/bge-reranker-base
fi

exec "$@"
