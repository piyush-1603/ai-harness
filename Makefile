.PHONY: run test install

run:
	python -m src.main --issue "$(ISSUE)"

test:
	pytest

install:
	pip install -r requirements.txt
