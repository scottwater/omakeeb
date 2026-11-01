PYTHON ?= python3

.PHONY: run demo test check ui-check install uninstall

run:
	$(PYTHON) -m omakeeb

demo:
	$(PYTHON) -m omakeeb --demo

test:
	$(PYTHON) -m unittest discover -s tests -v

check: test
	$(PYTHON) -m compileall -q omakeeb scripts

ui-check:
	$(PYTHON) scripts/smoke_ui.py

install:
	$(PYTHON) scripts/install.py install

uninstall:
	$(PYTHON) scripts/install.py uninstall
