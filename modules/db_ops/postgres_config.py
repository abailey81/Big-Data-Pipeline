"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Postgres configuration model
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

"""

import os

from pydantic import BaseModel, Field, field_validator


class PostgresConfig(BaseModel):
    """Pydantic model for PostgreSQL connection configuration.

    Falls back to environment variables when values are not provided directly.

    :param username: Database username
    :type username: str or None
    :param password: Database password
    :type password: str or None
    :param host: Database host address
    :type host: str or None
    :param port: Database port
    :type port: str or None
    :param database: Database name
    :type database: str or None
    """

    username: str | None = Field(None, description="Postgres username", validate_default=True)
    password: str | None = Field(None, description="Postgres password", validate_default=True)
    host: str | None = Field(None, description="Postgres host", validate_default=True)
    port: str | None = Field(None, description="Postgres port", validate_default=True)
    database: str | None = Field(None, description="Postgres database name", validate_default=True)

    @field_validator("username", mode="after")
    @classmethod
    def get_username(cls, v) -> str:
        if not v:
            return os.environ.get("POSTGRES_USERNAME")
        return v

    @field_validator("password", mode="after")
    @classmethod
    def get_password(cls, v) -> str:
        if not v:
            return os.environ.get("POSTGRES_PASSWORD")
        return v

    @field_validator("host", mode="after")
    @classmethod
    def get_host(cls, v) -> str:
        if not v:
            return os.environ.get("POSTGRES_HOST_DEV", os.environ.get("POSTGRES_HOST"))
        return v

    @field_validator("port", mode="after")
    @classmethod
    def get_port(cls, v) -> str:
        if not v:
            return os.environ.get("POSTGRES_PORT_DEV", os.environ.get("POSTGRES_PORT"))
        return v

    @field_validator("database", mode="after")
    @classmethod
    def get_db(cls, v) -> str:
        if not v:
            return os.environ.get("POSTGRES_DATABASE", "fift")
        return v
