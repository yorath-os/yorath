#!/bin/sh
# test repository: v0.2.0 = current main, v0.2.1 = + a probe migration, v0.2.2 = a broken migration
set -eu
sed_i() { sed "$1" "$2" > "$2.tmp" && mv "$2.tmp" "$2"; }  # same on GNU and BSD sed
# Run on the developer machine: sh installer/e2e/lifecycle-bundle.sh [directory]; copy the bundle to the node.
src="$(cd "$(dirname "$0")/../.." && pwd)"
out="${1:-$(mktemp -d)}"; mkdir -p "$out"; cd "$out"; rm -rf repo e2e.bundle
git clone -q "$src" repo; cd repo
git tag -l | xargs -r git tag -d >/dev/null  # real release tags get in the way of the test v0.2.x
git config user.email e2e@local; git config user.name e2e; git checkout -q -b lifecycle-test
sed_i 's/^  version: .*/  version: 0.2.0/' distribution.yaml; git commit -qam "e2e 0.2.0"; git tag v0.2.0
head=$(sed -n 's/^  schema_version: "\([^"]*\)".*/\1/p' distribution.yaml)
printf '"""e2e probe"""\n\nimport sqlalchemy as sa\n\nfrom alembic import op\n\nrevision = "0099_e2e_probe"\ndown_revision = "%s"\nbranch_labels = None\ndepends_on = None\n\n\ndef upgrade() -> None:\n    op.add_column("nodes", sa.Column("e2e_probe", sa.Integer(), nullable=True))\n\n\ndef downgrade() -> None:\n    op.drop_column("nodes", "e2e_probe")\n' "$head" > core/alembic/versions/0099_e2e_probe.py
sed_i "s/^  version: .*/  version: 0.2.1/; s/schema_version: \"$head\"/schema_version: \"0099_e2e_probe\"/" distribution.yaml
git add -A; git commit -qm "e2e 0.2.1"; git tag v0.2.1
printf '"""e2e broken"""\n\nrevision = "0100_e2e_broken"\ndown_revision = "0099_e2e_probe"\nbranch_labels = None\ndepends_on = None\n\n\ndef upgrade() -> None:\n    raise RuntimeError("e2e: broken migration")\n\n\ndef downgrade() -> None:\n    pass\n' > core/alembic/versions/0100_e2e_broken.py
sed_i 's/^  version: .*/  version: 0.2.2/; s/schema_version: "0099_e2e_probe"/schema_version: "0100_e2e_broken"/' distribution.yaml
git add -A; git commit -qm "e2e 0.2.2"; git tag v0.2.2
git checkout -q v0.2.0; git bundle create -q ../e2e.bundle --all
echo "bundle: $out/e2e.bundle ($(git tag | tr "\n" " "))"
