import os
import re
from copy import deepcopy

import jinja2
from nbconvert.exporters import HTMLExporter, MarkdownExporter
from nbconvert.filters.highlight import _pygments_highlight
from nbconvert.filters.markdown_mistune import IPythonRenderer
from nbconvert.nbconvertapp import NbConvertApp
from nbconvert.preprocessors import Preprocessor
from pygments.formatters import HtmlFormatter
from traitlets import Integer
from traitlets.config import Config

from mkdocs_jupyter.utils import slugify
from mkdocs_jupyter.templates import GENERATED_MD, LATEX_CUSTOM_SCRIPT


# We monkeypatch nbconvert.filters.markdown_mistune.IPythonRenderer.header
# to use a version that makes the id all lowercase
# We do this because mkdocs uses all lowercase TOC titles to make it url friendly
# so this makes the links from the TOC work


def add_anchor_lower_id(html, anchor_link_text="¶"):
    from ipython_genutils import py3compat
    from defusedxml import cElementTree as ElementTree
    from xml.etree.cElementTree import Element
    from nbconvert.filters.strings import _convert_header_id, html2text

    try:
        h = ElementTree.fromstring(py3compat.cast_bytes_py2(html, encoding="utf-8"))
    except Exception:
        # failed to parse, just return it unmodified
        return html
    link = _convert_header_id(html2text(h))
    h.set("id", slugify(link))
    a = Element("a", {"class": "anchor-link", "href": "#" + link})
    try:
        # Test if the anchor link text is HTML (e.g. an image)
        a.append(ElementTree.fromstring(anchor_link_text))
    except Exception:
        # If we fail to parse, assume we've just got regular text
        a.text = anchor_link_text
    h.append(a)

    # Known issue of Python3.x, ElementTree.tostring() returns a byte string
    # instead of a text string.  See issue http://bugs.python.org/issue10942
    # Workaround is to make sure the bytes are casted to a string.
    return py3compat.decode(ElementTree.tostring(h), "utf-8")


def new_header(self, text, level, raw=None):
    html = super(IPythonRenderer, self).header(text, level, raw=raw)
    anchor_link_text = self.options.get("anchor_link_text", "¶")
    return add_anchor_lower_id(html, anchor_link_text=anchor_link_text)


IPythonRenderer.header = new_header

# End monkeypatch --------------------------------------------------------------


THIS_DIR = os.path.dirname(os.path.realpath(__file__))


def nb2md(nb_path):
    """Convert a notebook to markdown

    We use a template that removed all code cells because if the body
    is to big (javascript and stuff) it takes to long to read and parse
    """
    config = Config()
    template_file = "mkdocs_md/md-no-codecell.md.j2"

    exporter = MarkdownExporter(
        # config=config,
        template_file=template_file,
        # uncomment this line when new nbconvert is released
        # https://github.com/jupyter/nbconvert/pull/1429
        # extra_template_paths=[os.path.join(THIS_DIR, "templates")],
    )
    ## Delete this block when nbconvert is released
    from jupyter_core.paths import jupyter_path

    defaults = jupyter_path("nbconvert", "templates")
    exporter.template_paths.append(os.path.join(THIS_DIR, "templates"))
    print(exporter.template_paths)
    ## End block
    body, resources = exporter.from_filename(nb_path)
    return body


def nb2html(nb_path, start=0, end=None, execute=False):
    """Convert a notebook and return html"""

    # Load the user's nbconvert configuration
    app = NbConvertApp()
    app.load_config_file()

    app.config.update(
        {
            # This Preprocessor changes the pygments css prefixes
            # from .highlight to .highlight-ipynb
            "CSSHTMLHeaderPreprocessor": {
                "enabled": True,
                "highlight_class": ".highlight-ipynb",
            },
            "SubCell": {"enabled": True, "start": start, "end": end},
            "ExecutePreprocessor": {"enabled": execute, "store_widget_state": True},
        }
    )

    preprocessors_ = [SubCell]

    filters = {
        "highlight_code": custom_highlight_code,
    }

    template_file = "mkdocs_html/notebook.html.j2"
    # template_file = "lab/index.html.j2"

    exporter = HTMLExporter(
        config=app.config,
        template_file=template_file,
        # uncomment this line when new nbconvert is released
        # https://github.com/jupyter/nbconvert/pull/1429
        # extra_template_paths=[os.path.join(THIS_DIR, "templates")],
        preprocessors=preprocessors_,
        filters=filters,
    )
    ## Delete this block when nbconvert is released
    # https://github.com/jupyter/nbconvert/pull/1429
    from jupyter_core.paths import jupyter_path

    defaults = jupyter_path("nbconvert", "templates")
    exporter.template_paths.append(os.path.join(THIS_DIR, "templates"))
    print(exporter.template_paths)
    ## End block

    html, info = exporter.from_filename(nb_path)

    # HTML and CSS fixes
    # html = html_fixes(html, info, fix_css=True, ignore_css=False)
    return GENERATED_MD.format(html=html)


class SliceIndex(Integer):
    """An integer trait that accepts None"""

    default_value = None

    def validate(self, obj, value):
        if value is None:
            return value
        else:
            return super(SliceIndex, self).validate(obj, value)


class SubCell(Preprocessor):
    """A preprocessor to select a slice of the cells of a notebook"""

    start = SliceIndex(0, config=True, help="First cell of notebook")
    end = SliceIndex(None, config=True, help="Last cell of notebook")

    def preprocess(self, nb, resources):
        nbc = deepcopy(nb)
        nbc.cells = nbc.cells[self.start : self.end]
        return nbc, resources


def custom_highlight_code(source, language="python", metadata=None):
    """
    Makes the syntax highlighting from pygments in the Notebook output
    have the prefix(`highlight-ipynb`).
    So it doesn't break the theme pygments
    This modifies only html content, not css
    """
    if not language:
        language = "ipython3"

    formatter = HtmlFormatter(cssclass=" highlight highlight-ipynb hl-" + language)
    output = _pygments_highlight(source, formatter, language, metadata)
    return output


def html_fixes(content, info, fix_css=True, ignore_css=False):
    """
    General fixes for the notebook generated html
    fix_css does basic filter to remove extra CSS from the core Jupyter CSS
    ignore_css is to not include at all the Jupyter CSS
    """

    def style_tag(styles):
        return '<style type="text/css">{0}</style>'.format(styles)

    def filter_css(style):
        """
        This is a little bit of a Hack.
        Jupyter returns a lot of CSS including its own bootstrap.
        We try to get only the core Jupyter Notebook CSS
        """
        index = style.find("/*!\n*\n* IPython notebook\n*\n*/")
        if index > 0:
            style = style[index:]
        index = style.find("/*!\n*\n* IPython notebook webapp\n*\n*/")
        if index > 0:
            style = style[:index]

        style = re.sub(r"color\:\#0+(;)?", "", style)
        style = re.sub(
            r"\.rendered_html[a-z0-9,._ ]*\{[a-z0-9:;%.#\-\s\n]+\}", "", style
        )
        return style_tag(style)

    if not ignore_css:
        jupyter_css = "\n".join(style_tag(style) for style in info["inlining"]["css"])
        if fix_css:
            jupyter_css = "\n".join(
                filter_css(style) for style in info["inlining"]["css"]
            )
        print(info["inlining"]["css"])
        content = jupyter_css + content
    content = content + LATEX_CUSTOM_SCRIPT
    return content


if __name__ == "__main__":
    nb2html("tests/mkdocs/docs/demo.ipynb")
