PYTHON ?= python3

.PHONY: run test install

run:
	$(PYTHON) -m src.main --issue "$(ISSUE)"

test:
	pytest

install:
	pip install -r requirements.txt
