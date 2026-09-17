#!/bin/sh
# In-cluster hourly export of the home PostgreSQL: pg_dump over the cluster network as the
# owner role, age-encrypted in a pipe, single PUTs with the write-only Roles Anywhere backup
# leaf (credential_process in /config/aws-config). Plaintext never touches a disk; nothing
# printed can identify data. The owner role is not a superuser, so roles/verifiers are NOT
# exported here: the daily credential bundle under recovery/ carries what a restore needs.
set -eu
: "${BUCKET:?}" "${RECIPIENT:?}" "${PGHOST:?}" "${PGPORT:?}" "${PGDATABASE:?}" "${PGUSER:?}" "${PGPASSWORD:?}" "${DAILY_HOUR_UTC:?}"
export AWS_CONFIG_FILE=/config/aws-config AWS_PROFILE=home-server-backup AWS_EC2_METADATA_DISABLED=true
stamp=$(date -u +%Y%m%dT%H%M%SZ)
tier=hourly
[ "$(date -u +%H)" = "$DAILY_HOUR_UTC" ] && tier=daily
work=$(mktemp -d /work/export.XXXXXX)
trap 'rm -rf "$work"' EXIT
umask 077

pg_dump --format=custom --no-password | age --encrypt -r "$RECIPIENT" > "$work/postgres.dump.age"
size=$(stat -c %s "$work/postgres.dump.age")
digest=$(sha256sum "$work/postgres.dump.age" | cut -d ' ' -f 1)
printf '{"kind":"driftplain-home-server-postgres-export","version":2,"origin":"cluster","tier":"%s","created_at":"%s","database":"%s","recipient":"%s","objects":{"postgres.dump.age":{"size":%s,"sha256":"%s"}},"limitations":["no roles export: the owner role is not a superuser; restore roles from the daily credential bundle under recovery/"]}\n' \
  "$tier" "$stamp" "$PGDATABASE" "$RECIPIENT" "$size" "$digest" > "$work/manifest.json"

prefix="postgres/$tier/cluster-$stamp"
for name in postgres.dump.age manifest.json; do
  aws s3api put-object --bucket "$BUCKET" --key "$prefix/$name" --body "$work/$name" \
    --checksum-algorithm SHA256 --content-type application/octet-stream > /dev/null
done
echo "uploaded $prefix ($tier, $size bytes)"
