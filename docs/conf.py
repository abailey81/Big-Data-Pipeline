"""
Sphinx documentation configuration for the CW1 pipeline.

Usage:
    cd docs/
    sphinx-apidoc -o source/ ../modules/
    make html
"""
import os
import sys

# -- Path setup ---------------------------------------------------------------
sys.path.insert(0, os.path.abspath('..'))

# -- Project information ------------------------------------------------------
project = 'CW1 Systematic Equity Pipeline'
copyright = '2026, Team XX - UCL IFT'
author = 'Team XX'
release = '2.1.0'

# -- General configuration ----------------------------------------------------
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
]

templates_path = ['_templates']
exclude_patterns = ['_build']

# -- Options for HTML output --------------------------------------------------
html_theme = 'pydata_sphinx_theme'
html_static_path = ['_static']

# -- Napoleon settings for Google/NumPy style docstrings ----------------------
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True

# -- Intersphinx links to standard Python docs --------------------------------
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'pandas': ('https://pandas.pydata.org/docs/', None),
    'sqlalchemy': ('https://docs.sqlalchemy.org/en/20/', None),
}

# -- Autodoc settings ---------------------------------------------------------
autodoc_default_options = {
    'members': True,
    'undoc-members': True,
    'show-inheritance': True,
}
autodoc_member_order = 'bysource'
