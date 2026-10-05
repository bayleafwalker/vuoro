"""Documentation resolution and provenance over Git-authored knowledge.

Git owns authored documents. This package reads them, builds a rebuildable
catalog, and answers three operations:

* ``search``  - discover sources by text and metadata, with match explanations;
* ``get``     - read an exact revision or bounded section with its notices;
* ``resolve_context`` - the applicable source set for a work context, with
  conflicts, missing authority and unevidenced transitions made explicit.

It never writes to the documents it reads and holds no work or publication
state. A context manifest is bound to work through the existing evidence path.
"""

__version__ = "0.1.0"
