API Reference
==============

This section provides auto-generated documentation for all modules in the pipeline.

Data Models
-----------

.. automodule:: modules.data_models.models
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.data_models.table_models
   :members:
   :undoc-members:
   :show-inheritance:

Database Operations
--------------------

.. automodule:: modules.db_ops.sql_conn
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.db_ops.postgres_config
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.db_ops.extract_from_query
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.db_ops.minio_store
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.db_ops.mongo_conn
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.db_ops.kafka_ops
   :members:
   :undoc-members:
   :show-inheritance:

Input / Downloaders
--------------------

.. automodule:: modules.input.get_company_static
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.base_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.price_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.fundamentals_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.edgar_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.finnhub_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.fx_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.vix_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.risk_free_rate_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.esg_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.news_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.newsapi_downloader
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.input.gdelt_downloader
   :members:
   :undoc-members:
   :show-inheritance:

Orchestration
--------------

.. automodule:: modules.orchestration.state
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.orchestration.stage_prices
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.orchestration.stage_fundamentals
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.orchestration.stage_macro
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.orchestration.stage_ratios
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.orchestration.stage_esg
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.orchestration.stage_sentiment
   :members:
   :undoc-members:
   :show-inheritance:

Processing
-----------

.. automodule:: modules.processing.ticker_utils
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.processing.data_cleaner
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.processing.data_quality
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.processing.sentiment_scorer
   :members:
   :undoc-members:
   :show-inheritance:

Output
-------

.. automodule:: modules.output.data_exporter
   :members:
   :undoc-members:
   :show-inheritance:

Utilities
----------

.. automodule:: modules.utils.args_parser
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.utils.info_logger
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.utils.scheduler
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.utils.circuit_breaker
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.utils.rate_limiter
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.utils.retry
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.utils.health_check
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.utils.pipeline_metrics
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.utils.concurrent_executor
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.utils.progress_tracker
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: modules.utils.exceptions
   :members:
   :undoc-members:
   :show-inheritance:
