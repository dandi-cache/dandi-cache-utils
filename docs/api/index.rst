API reference
=============

The modules below are the library. A cache's update code imports the top-level package and reaches
everything through it:

.. code-block:: python

   import dandi_cache_utils as dandi_cache

   dandi_cache.open_dataset()           # re-exported from dandi_cache_utils.cli
   dandi_cache.run_incremental_update() # re-exported from dandi_cache_utils.runner
   dandi_cache.nwb.walk_structure()     # dandi_cache_utils.dandi.nwb, imported on first use

The three DANDI modules are bound lazily, so a cache that only reads S3 never pays for ``pynwb``.

Core
----

.. autosummary::
   :toctree: generated
   :recursive:

   dandi_cache_utils.cli
   dandi_cache_utils.config
   dandi_cache_utils.dataset
   dandi_cache_utils.jsonl
   dandi_cache_utils.logs
   dandi_cache_utils.runner

DANDI archive operations
------------------------

.. autosummary::
   :toctree: generated
   :recursive:

   dandi_cache_utils.dandi.api
   dandi_cache_utils.dandi.nwb
   dandi_cache_utils.dandi.s3
