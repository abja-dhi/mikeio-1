
build: test
	python setup.py sdist bdist_wheel

test:
	pytest --disable-warnings

doctest:
	pytest mikeio/dfs*.py mikeio/eum.py --doctest-modules
	rm *.dfs* # remove temporary files, created from doctests

coverage: 
	pytest --cov-report html --cov=mikeio tests/

docs: FORCE
	cd docs; make html ;cd -

FORCE:
