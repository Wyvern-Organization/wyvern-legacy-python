# Partitioned ID Migration Runbook

This migration must be performed on ASPC first. Do not run the initial re-indexing independently on nubu; nubu must receive the migrated ASPC database/state so both nodes share the same old-to-new ID mapping.

## Safety Sequence

1. Stop normal traffic or put the deployment behind maintenance routing.
2. Set `WYVERN_INDEXING=true` and `WYVERN_NODE_ID=aspc` on ASPC.
3. Restart ASPC and confirm `/health` returns:

   ```json
   {"ok": true, "mode": "indexing", "message": "Indexing..."}
   ```

4. From `wyvern-backend`, run:

   ```bash
   python scripts/migrate_partitioned_ids.py
   ```

   The script creates a `pg_dump` backup before running Alembic. Backups are written under `private/backups/`.

5. Confirm the script prints a validation report with no `orphans` and no `soft_reference_orphans`.
6. Confirm the exported old-to-new mapping exists under `private/migrations/` with `.sha256` checksum files.
7. Set `WYVERN_INDEXING=false` and restart ASPC.
8. Verify login, DMs, messages, users, servers, channels, roles, invites, uploads, reactions, workspaces, webhooks, and sessions.
9. Confirm newly created ASPC records use `aspc_*` IDs.
10. Copy the migrated/verified ASPC database or state to nubu.
11. Start nubu with `WYVERN_NODE_ID=nubu` and confirm newly created nubu records use `nubu_*` IDs.

## Useful Commands

Validate and export the mapping without rerunning Alembic:

```bash
WYVERN_INDEXING=true WYVERN_NODE_ID=aspc python scripts/migrate_partitioned_ids.py --validate-only
```

If the script is interrupted before Alembic commits, rerun it after confirming the backup exists. PostgreSQL DDL is transactional for this migration path, and the mapping inserts are deterministic.

Keep `private/backups/` and `private/migrations/` private. They include restore material and legacy ID mapping data.
