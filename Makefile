VENV ?= .venv
PYTHON ?= $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
PYTEST ?= $(if $(wildcard $(VENV)/bin/pytest),$(VENV)/bin/pytest,pytest)

.PHONY: setup run test install

setup:
	@if [ ! -d "$(VENV)" ]; then python3 -m venv $(VENV); fi
	@$(VENV)/bin/pip install -r requirements.txt

run:
	$(PYTHON) -m src.main --issue "$(ISSUE)"

test:
	$(PYTEST)

install:
	pip install -r requirements.txt
