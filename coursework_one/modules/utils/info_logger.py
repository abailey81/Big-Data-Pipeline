"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : info_logger utils
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

"""

import uuid

from ift_global.utils.logger import IFTLogger

pipeline_logger = IFTLogger(app_name="big_data", service_name="systematic_equity", log_level="info")


def generate_run_id() -> str:
    """Generates a unique run identifier for pipeline traceability.

    :return: UUID string
    :rtype: str
    """
    return str(uuid.uuid4())
