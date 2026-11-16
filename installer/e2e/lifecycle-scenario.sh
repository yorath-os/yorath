#!/bin/sh
# End-to-end check of update-and-rollback on a v0.2.0 install (docs/operate/update-and-rollback.md).
# The bundle with tags v0.2.0, v0.2.1, v0.2.2 is built by lifecycle-bundle.sh. Run as root on the node:
#   sh installer/e2e/lifecycle-scenario.sh /root/e2e.bundle
# shellcheck disable=SC2034  # variables are read by eval in check
set -u
B="${1:?path to the bundle: sh lifecycle-scenario.sh /path/e2e.bundle}"
api=http://127.0.0.1:8000
C="docker compose --project-directory /opt/yorath/installer -f /opt/yorath/installer/compose.yaml"
sql() { $C exec -T db psql -U yorath -d yorath -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }
fails=0
base_schema=$(sed -n 's/^  schema_version: "\([^"]*\)".*/\1/p' /opt/yorath/distribution.yaml)  # v0.2.0 schema
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fails=$((fails+1)); fi; }
step() { echo; echo "=== $*"; t0=$(date +%s); }
took() { echo "--- $1: $(( $(date +%s) - t0 )) s"; }
ver() { curl -fsS $api/version | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["distribution"]["version"], d["schema_head"], d["schema_matches_manifest"])'; }
snapshot() { echo "users=$(sql 'select count(*) from users') docs=$(sql 'select count(*) from documents') files=$($C exec -T core sh -c 'find /data/documents -type f | wc -l' | tr -d '[:space:]')"; }
upload() {  # name text
  printf '%s\n' "$2" > "/tmp/$1"
  curl -fsS -o /dev/null -b /tmp/j -F "file=@/tmp/$1;type=text/markdown" "$api/data/assets/$asset/documents"
}

step "data v0.2.0"
code=$(sed -n 's/^YORATH_SETUP_CODE=//p' /opt/yorath/installer/.env.local)
curl -fsS -o /dev/null -c /tmp/j -H 'content-type: application/json' \
  -d "{\"login\":\"alex\",\"password\":\"owner-pass-1\",\"display_name\":\"S\",\"code\":\"$code\"}" $api/auth/setup
asset=$(curl -fsS -b /tmp/j -H 'content-type: application/json' -d '{"name":"e2e","data_class":"internal"}' $api/data/assets | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
upload before.md "marker before the update"
S0=$(snapshot); echo "S0: $S0"; echo "version: $(ver)"
check "yorath status knows the version" '[ -n "$(yorath status | grep "version:      0.2.0")" ]'

step "yorath backup"
yorath backup; took backup
check "backup with checksums" 'cd /var/lib/yorath/backups/*-0.2.0 && sha256sum -c --quiet SHA256SUMS'

step "yorath update 0.2.1 --from bundle"
yorath update 0.2.1 --from $B; rc=$?; took update
check "update rc=0" '[ $rc -eq 0 ]'
check "version 0.2.1 and the probe schema" '[ "$(ver)" = "0.2.1 0099_e2e_probe True" ]'
check "probe column exists" '[ "$(sql "select count(*) from information_schema.columns where column_name='"'"'e2e_probe'"'"'")" = 1 ]'
check "data unchanged after the update" '[ "$(snapshot)" = "$S0" ]'

step "data after the update"
curl -fsS -o /dev/null -c /tmp/j -H 'content-type: application/json' -d '{"login":"alex","password":"owner-pass-1"}' $api/auth/login
upload after.md "marker after the update"
echo "S1: $(snapshot)"

step "yorath rollback --yes"
yorath rollback --yes; rc=$?; took rollback
check "rollback rc=0" '[ $rc -eq 0 ]'
check "version 0.2.0 and its schema" '[ "$(ver)" = "0.2.0 $base_schema True" ]'
check "probe column is gone" '[ "$(sql "select count(*) from information_schema.columns where column_name='"'"'e2e_probe'"'"'")" = 0 ]'
check "data as before the update" '[ "$(snapshot)" = "$S0" ]'
safety=$(ls -1d /var/lib/yorath/backups/*-0.2.1-safety | tail -1)
check "safety backup holds the document added after the update" '$C exec -T db pg_restore -f - --data-only -t documents < "$safety/db.dump" | grep -q after.md'
ev=$(sql "select details from audit_events where event_type='installation.restored' order by occurred_at desc limit 1")
echo "installation.restored: $ev"
check "event with the number of replaced documents" 'echo "$ev" | grep -q "\"documents\":1"'

step "refusals"
yorath rollback --yes; rc=$?
check "second rollback refused" '[ $rc -ne 0 ]'
yorath restore "$(basename "$safety")" --yes; rc=$?
check "restore of a 0.2.1 backup on 0.2.0 refused" '[ $rc -ne 0 ]'
yorath update 0.2.0 --from $B; rc=$?
check "update to the same version refused" '[ $rc -ne 0 ]'

step "yorath update 0.2.2 (core does not start)"
YORATH_READY_TIMEOUT=90 yorath update 0.2.2 --from $B; rc=$?; took failed-update
check "update rc!=0" '[ $rc -ne 0 ]'
check "back on 0.2.0" '[ "$(ver)" = "0.2.0 $base_schema True" ]'
check "data in place" '[ "$(snapshot)" = "$S0" ]'
check "log of the failed version saved" 'grep -q "broken migration" /var/lib/yorath/failed-update-0.2.2.log'
check "failed-update event" '[ "$(sql "select count(*) from audit_events where event_type='"'"'installation.restored'"'"' and reason='"'"'failed-update'"'"'")" = 1 ]'

step "yorath restore of a regular 0.2.0 backup"
regular=$(ls -1d /var/lib/yorath/backups/*-0.2.0 | head -1)
yorath restore "$(basename "$regular")" --yes; rc=$?; took restore
check "restore rc=0" '[ $rc -eq 0 ]'
check "data as in the backup" '[ "$(snapshot)" = "$S0" ]'

step "yorath doctor"
yorath doctor; rc=$?
check "doctor ok on a working installation" '[ $rc -eq 0 ]'
$C stop core >/dev/null 2>&1
yorath doctor; rc=$?
check "doctor sees the stopped core" '[ $rc -ne 0 ]'
$C start core >/dev/null 2>&1
i=0; until curl -fsS $api/health >/dev/null 2>&1; do i=$((i+1)); [ $i -lt 60 ] || break; sleep 2; done

step "yorath diag"
docker exec "$($C ps -q core)" sh -c 'echo "probe yorath_key_TESTSECRET0123456789 Bearer abc.def.ghi" > /proc/1/fd/1'
yorath diag; rc=$?
arch=$(ls -1 /var/lib/yorath/diag/*.tar.gz | tail -1)
rm -rf /tmp/diag && mkdir /tmp/diag && tar -C /tmp/diag -xzf "$arch"
pw=$(sed -n 's/^YORATH_DB_PASSWORD=//p' /opt/yorath/installer/.env.local)
sk=$(sed -n 's/^YORATH_SECRET_KEY=//p' /opt/yorath/installer/.env.local)
check "diag rc=0" '[ $rc -eq 0 ]'
check "no API key in the archive" '! grep -rq TESTSECRET0123456789 /tmp/diag'
check "no Bearer token in the archive" '! grep -rq abc.def.ghi /tmp/diag'
check "no database password in the archive" '! grep -rqF "$pw" /tmp/diag'
check "no secrets key in the archive" '! grep -rqF "$sk" /tmp/diag'
check "password variable name is present" 'grep -rq "^YORATH_DB_PASSWORD$" /tmp/diag'
check "core log in the archive" 'grep -q probe /tmp/diag/*/log-core.txt'

check "all backups intact after diag" '( for b in /var/lib/yorath/backups/*/; do (cd "$b" && sha256sum -c --quiet SHA256SUMS) || exit 1; done )'

echo; echo "RESULT: $fails failures"
