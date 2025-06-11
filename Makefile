appname = example
package = example

# Default goal
.DEFAULT_GOAL := help

# Help
.PHONY: help
help:
	@echo ""
	@echo "$(appname_verbose) Makefile"
	@echo ""
	@echo "Usage:"
	@echo "  make [command]"
	@echo ""
	@echo "Commands:"
	@echo "  build_test              Build the package"
	@echo "  coverage                Run tests and create a coverage report"
	@echo "  graph_models            Create a graph of the models"
	@echo "  pre-commit-checks       Run pre-commit checks"
	@echo "  tox_tests               Run tests with tox"
	@echo "  translations            Create or update translation files"
	@echo "  compile_translations    Compile translation files"
	@echo ""

# Translation files
.PHONY: translations
translations:
	@echo "Creating or updating translation files"
	@django-admin makemessages \
		-l cs_CZ \
		-l de \
		-l es \
		-l fr_FR \
		-l it_IT \
		-l ja \
		-l ko_KR \
		-l nl_NL \
		-l pl_PL \
		-l ru \
		-l sk \
		-l uk \
		-l zh_Hans \
		--keep-pot \
		--ignore 'build/*'

# Compile translation files
.PHONY: compile_translations
compile_translations:
	@echo "Compiling translation files"
	@django-admin compilemessages \
		-l cs_CZ \
		-l de \
		-l es \
		-l fr_FR \
		-l it_IT \
		-l ja \
		-l ko_KR \
		-l nl_NL \
		-l pl_PL \
		-l ru \
		-l sk \
		-l uk \
		-l zh_Hans

# Graph models
.PHONY: graph_models
graph_models:
	@echo "Creating a graph of the models"
	@python ../myauth/manage.py \
		graph_models \
		$(package) \
		--arrow-shape normal \
		-o $(appname)-models.png

# Coverage
.PHONY: coverage
coverage:
	@echo "Running tests and creating a coverage report"
	@rm -rf htmlcov
	@coverage run ../myauth/manage.py \
		test \
		$(package) \
		--keepdb \
		--failfast; \
	coverage html; \
	coverage report -m

# Build test
.PHONY: build_test
build_test:
	@echo "Building the package"
	@rm -rf dist
	@python3 -m build

# Tox tests
.PHONY: tox_tests
tox_tests:
	@echo "Running tests with tox"
	@export USE_MYSQL=False; \
	tox -v -e allianceauth-latest; \
	rm -rf .tox/

# Pre-commit checks
.PHONY: pre-commit-checks
pre-commit-checks:
	@echo "Running pre-commit checks"
	@pre-commit run --all-files