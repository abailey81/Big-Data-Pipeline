CW2 Multi-Factor L/S Equity Backtest Engine
=============================================

Team Kolmogorov — IFTE0003 Big Data in Quantitative Finance · UCL MSc Banking and Digital Finance.

Sector-neutral, dollar-neutral monthly long/short equity backtest on the
678-stock CW1 universe.  The implemented composite combines two factors —
momentum (12-1) and value (B/P + E/P + CF/P) — at equal 50/50 weights,
after Coursework 1's four-factor proposal was reduced based on
out-of-sample information-coefficient evidence.  Methodology, results,
and limitations are documented in the accompanying report.

.. toctree::
   :maxdepth: 2
   :caption: Guide

   installation
   architecture
   usage

.. toctree::
   :maxdepth: 2
   :caption: API reference

   api_engine
   api_analytics
