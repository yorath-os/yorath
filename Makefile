# Yorath developer commands. Compose reads installer/.env, which is generated from distribution.yaml.
COMPOSE = docker compose --project-directory installer -f installer/compose.yaml
COMPOSE_DEV = $(COMPOSE) -f installer/compose.dev.yaml

.PHONY: render-env up up-dev down down-v logs ps test test-unit test-groups test-web test-installer coverage-map lint check-manifest check-name check-lang check-readme test-scripts build

render-env:
	cd core && uv run python ../installer/render-env.py

up: render-env            ## prod profile: built images
	$(COMPOSE) up -d --build

up-dev: render-env        ## dev profile: sources mounted, auto-reload
	$(COMPOSE_DEV) up -d --build

down:
	$(COMPOSE) down

down-v:                   ## including data (db, Ollama models)
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

build: render-env
	$(COMPOSE) build

DB_PORT := $(or $(shell grep -s '^YORATH_DB_PORT=' installer/.env.local | cut -d= -f2),5432)
TEST_DATABASE_URL ?= postgresql+asyncpg://yorath:yorath@127.0.0.1:$(DB_PORT)/yorath

test: test-unit test-groups test-web test-installer test-scripts

test-unit:                ## pure functions, no database needed
	cd core && uv run pytest -q tests/unit

test-groups:              ## the whole core on a live Postgres: fails without a database, never skips
	cd core && YORATH_DATABASE_URL=$(TEST_DATABASE_URL) uv run pytest -q tests/groups

test-scripts:             ## tests of scripts/ (check-readme)
	cd core && uv run pytest -q -p no:cacheprovider ../scripts/tests

test-installer:           ## install.sh: version, stream run, root check (no root, network or Docker needed)
	sh installer/tests/install-script.sh

test-web:
	cd web && npm test --if-present

coverage-map:             ## which invariants are covered by tests and mutations
	cd core && uv run python -m tests.coverage_map

lint:
	cd core && uv run ruff format --check . && uv run ruff check . && uv run mypy src
	cd web && npm run lint --if-present && npx tsc -b  # tsc -b, as in the image build (erasableSyntaxOnly)

check-manifest:
	cd core && uv run yorath check-manifest
	$(MAKE) render-env
	$(COMPOSE) config --quiet && echo "compose config: ok"

check-name:               ## the old name has not come back (T11 rename-yorath)
	./scripts/check-old-name.sh $(CHECK_NAME_EXCLUDES)

check-readme:             ## README.md matches docs/facts.yaml, docs/advantages.yaml and docs/ (public-readme)
	cd core && uv run python ../scripts/check-readme.py $(CHECK_README_ARGS)
check-lang:               ## no Cyrillic in product files (translate-english)
	./scripts/check-lang.sh $(CHECK_LANG_EXCLUDES)

# Targets of the working repository (site, launch gate, release export); absent in the public tree.
-include private.mk
