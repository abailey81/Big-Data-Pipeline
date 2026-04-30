Installation Guide
==================

Prerequisites
-------------

Before installing the pipeline, ensure the following are available:

* **Python 3.10+** -- required by all dependencies
* **Poetry** -- Python package manager (``pip install poetry``)
* **Docker** -- for PostgreSQL, MinIO, MongoDB, Kafka, Zookeeper, and seed containers
* **Git** -- for cloning the repository and ``ift_global`` dependency

Step 1: Clone the Repository
-----------------------------

.. code-block:: bash

   git clone https://github.com/abailey81/Big-Data-Pipeline.git
   cd Big-Data-Pipeline

Step 2: Start Infrastructure
-----------------------------

From the repository root, launch the Docker containers:

.. code-block:: bash

   docker compose up -d postgres-db minio mongodb zookeeper kafka
   docker compose up postgres-seed minio-seed mongo-seed

This starts:

* **PostgreSQL** on port ``5438`` (dev) or ``5432`` (Docker network)
* **MinIO** on port ``9000`` with console on ``9001``
* **MongoDB** on port ``27017`` (document store for ESG, API caches)
* **Zookeeper** on port ``2181`` (Kafka coordination)
* **Kafka** on port ``9092`` (event streaming)
* **postgres-seed** -- seeds the ``systematic_equity.company_static`` table with 678 companies
* **minio-seed** -- creates the ``iftbigdata`` bucket in MinIO
* **mongo-seed** -- creates MongoDB collections with indexes

Step 3: Install Python Dependencies
-------------------------------------

.. code-block:: bash

   cd coursework_one
   poetry install

This installs all core and development dependencies defined in ``pyproject.toml``,
including ``ift_global`` from the Kolmogorov's team GitHub repository.

Step 4: Initialise the Database Schema
----------------------------------------

.. code-block:: bash

   poetry run python Main.py --env_type dev --init_schema

This creates the ``systematic_equity`` schema and all eleven tables
(``company_static``, ``daily_prices``, ``fundamentals``, ``fx_rates``,
``vix_data``, ``risk_free_rate``, ``benchmark_index``, ``company_ratios``,
``esg_scores``, ``ingestion_log``, ``pipeline_metadata``).

Step 5: Verify the Installation
---------------------------------

Run a dry-run to confirm configuration is valid:

.. code-block:: bash

   poetry run python Main.py --env_type dev --dry_run

Run the unit test suite:

.. code-block:: bash

   poetry run pytest -m "not integration"

Environment Variables
-----------------------

The pipeline reads environment variables from ``.env.dev``. Key variables:

.. list-table::
   :header-rows: 1

   * - Variable
     - Description
     - Default
   * - ``POSTGRES_HOST_DEV``
     - PostgreSQL host for dev
     - ``localhost``
   * - ``POSTGRES_PORT_DEV``
     - PostgreSQL port for dev
     - ``5438``
   * - ``POSTGRES_DATABASE``
     - Database name
     - ``fift``
   * - ``MINIO_URL``
     - MinIO endpoint
     - ``http://localhost:9000``
   * - ``MONGO_HOST``
     - MongoDB host
     - ``localhost``
   * - ``MONGO_PORT``
     - MongoDB port
     - ``27017``
   * - ``KAFKA_BOOTSTRAP_SERVERS``
     - Kafka bootstrap servers
     - ``localhost:9092``
   * - ``NEWSAPI_KEY``
     - NewsAPI key (optional, secondary news source)
     - (none)
