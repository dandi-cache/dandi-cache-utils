"""Sphinx configuration for the dandi-cache-utils documentation."""

import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

project = "dandi-cache-utils"
author = "DANDI Cache"
copyright = f"{datetime.datetime.now(tz=datetime.UTC).year}, {author}"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinx_copybutton",
]

# `colon_fence` lets an admonition be written as ::: fenced blocks, which stay readable as plain
# Markdown on GitHub as well as rendering here.
myst_enable_extensions = ["colon_fence"]

# `healthstatus-review/` is dated audits of the whole organization, plain GitHub Markdown meant to
# be read in the repository rather than pages of this site. Excluding the directory keeps each new
# review from warning about missing from a toctree, which would fail the build under `-W`.
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "README.md",
    "healthstatus-review/**",
    "requirements.txt",
]

autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}
autodoc_typehints = "description"
python_maximum_signature_line_length = 88

html_theme = "pydata_sphinx_theme"
html_show_sourcelink = False
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_theme_options = {
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/dandi-cache/dandi-cache-utils",
            "icon": "fa-brands fa-github",
            "type": "fontawesome",
        },
        {
            "name": "Container images",
            "url": "https://github.com/orgs/dandi-cache/packages/container/package/dandi-cache-utils",
            "icon": "fa-brands fa-docker",
            "type": "fontawesome",
        },
    ],
}
