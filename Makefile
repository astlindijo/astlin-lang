.PHONY: test test-native demo build doctor package release clean

test:
	python3 -m unittest discover -s tests -v

test-native:
	python3 -m astlin test --sanitize

demo:
	python3 -m astlin run

build:
	python3 -m astlin build

doctor:
	python3 -m astlin doctor

package:
	python3 tools/build_backend.py

release:
	python3 tools/prepare_release.py

clean:
	rm -rf build dist __pycache__ tests/__pycache__ astlin/__pycache__ tools/__pycache__
