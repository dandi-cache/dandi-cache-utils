"""Sphinx configuration for the dandi-cache-utils documentation."""

import datetime

project = "dandi-cache-utils"
author = "DANDI Cache"
copyright = f"{datetime.datetime.now(tz=datetime.UTC).year}, {author}"

extensions = [
    "myst_parser",
    "sphinx_copybutton",
]

# `colon_fence` lets an admonition be written as ::: fenced blocks, which stay readable as plain
# Markdown on GitHub as well as rendering here.
myst_enable_extensions = ["colon_fence"]

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "README.md", "requirements.txt"]

html_theme = "pydata_sphinx_theme"
html_show_sourcelink = False
html_theme_options = {
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/dandi-cache/dandi-cache-utils",
            "icon": "fa-brands fa-github",
            "type": "fontawesome",
        },
    ],
}
