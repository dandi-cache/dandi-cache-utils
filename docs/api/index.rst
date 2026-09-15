API reference
=============

A cache's update code imports the top-level package and reaches everything through it:

.. code-block:: python

   import dandi_cache_utils as dandi_cache

   dandi_cache.open_dataset()
   dandi_cache.run_incremental_update()
   dandi_cache.nwb.walk_structure()

That flat namespace is the API. The modules behind it are private, so there is one page to read
rather than nine, and moving a function between them is not a breaking change.

The exceptions are the three archive modules, which a cache names directly because their
dependencies are optional: ``api`` needs the DANDI client, ``nwb`` the remote NWB stack, and
``s3`` boto3. Each keeps those imports inside the functions that use them, so importing the
package costs nothing a cache has not installed.

The package
-----------

.. automodule:: dandi_cache_utils
   :members:
   :imported-members:
   :undoc-members:
   :show-inheritance:

DANDI archive operations
------------------------

.. autosummary::
   :toctree: generated
   :recursive:

   api
   nwb
   s3
