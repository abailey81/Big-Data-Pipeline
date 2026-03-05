CW1 Systematic Equity Pipeline
================================

**IFTE0003: Big Data in Quantitative Finance -- UCL Institute of Finance & Technology**

This pipeline implements the Coursework 1 data extraction, processing, and storage
system for a flow-based multi-factor equity investment strategy. It downloads daily
OHLCV prices, quarterly fundamentals, FX rates, and VIX data for 678 companies in
the investable universe, storing raw files in MinIO and validated records in PostgreSQL.

The theoretical framework follows Vayanos and Woolley (2012) with three alpha signals
-- momentum, value, and quality -- to be computed in Coursework Two.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   installation
   usage
   architecture
   api


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
