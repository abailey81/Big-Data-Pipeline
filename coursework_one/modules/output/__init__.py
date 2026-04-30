"""
Output module for the Systematic Equity Pipeline.

Provides utilities for exporting processed data from PostgreSQL
to various formats (CSV, JSON) for downstream analysis in CW2.
"""

from modules.output.data_exporter import DataExporter

__all__ = ["DataExporter"]
