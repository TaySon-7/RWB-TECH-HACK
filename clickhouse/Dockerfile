FROM clickhouse/clickhouse-server:25.3@sha256:b627d7a9bc0e0c1bac26cdbe9d2fc6316faa29c5d8a174f28f5abd57d0fa6ba2
# The upstream entrypoint owns format_schema_path. Bake read-only contracts
# with the proper owner instead of chowning a read-only host bind mount.
COPY --chown=clickhouse:clickhouse schemas /etc/clickhouse-server/format_schemas
