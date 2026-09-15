The documentation is built with [Sphinx](https://www.sphinx-doc.org/) using the [PyData Sphinx Theme](https://pydata-sphinx-theme.readthedocs.io/), and published by [Read the Docs](https://readthedocs.org/) from [`.readthedocs.yaml`](.readthedocs.yaml) in this directory rather than at the repository root.

### Building locally

```bash
pip install -r docs/requirements.txt
python -m sphinx -b html docs docs/_build/html
```

Open `docs/_build/html/index.html` to preview the result.
Add `-W` to fail on the same warnings Read the Docs fails on.
