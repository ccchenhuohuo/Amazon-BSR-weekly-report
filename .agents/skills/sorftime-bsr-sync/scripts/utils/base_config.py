#!/usr/bin/env python3

import os
from dataclasses import dataclass


class ConfigError(ValueError):
    pass


@dataclass
class APIConfig:
    api_key: str
    base_url: str = 'https://standardapi.sorftime.com/api/CategoryRequest'
    domain: int = 1
    timeout: int = 120

    @classmethod
    def from_env(cls, prefix='SORFTIME_'):
        api_key = os.environ.get(prefix + 'API_KEY', '')
        base_url = os.environ.get(prefix + 'API_URL', 'https://standardapi.sorftime.com/api/CategoryRequest')
        domain_str = os.environ.get(prefix + 'DOMAIN', '1')
        timeout_str = os.environ.get(prefix + 'TIMEOUT', '120')

        if not api_key:
            raise ConfigError(prefix + 'API_KEY is required (please set via environment variable)')

        try:
            domain = int(domain_str)
        except ValueError:
            raise ConfigError(prefix + 'DOMAIN must be an integer, got %r' % domain_str)

        if domain < 1:
            raise ConfigError(prefix + 'DOMAIN must be >= 1, got %s' % domain)

        try:
            timeout = int(timeout_str)
        except ValueError:
            raise ConfigError(prefix + 'TIMEOUT must be an integer, got %r' % timeout_str)

        if not 1 <= timeout <= 600:
            raise ConfigError(prefix + 'TIMEOUT must be between 1 and 600 seconds, got %s' % timeout)

        return cls(
            api_key=api_key,
            base_url=base_url,
            domain=domain,
            timeout=timeout
        )


@dataclass
class DatabaseConfig:
    host: str
    user: str
    password: str
    port: int = 33060
    mysql_port: int = 30930
    stream_load_host: str = ''
    stream_load_port: int = 33060
    stream_load_fallback_host: str = ''
    stream_load_fallback_port: int = 0
    database: str = ''
    table: str = ''

    def __post_init__(self):
        if not self.stream_load_host:
            self.stream_load_host = self.host

    @classmethod
    def from_env(cls, prefix='DORIS_'):
        host = os.environ.get(prefix + 'HOST', '')
        user = os.environ.get(prefix + 'USER', '')
        password = os.environ.get(prefix + 'PASSWORD', '')
        port_str = os.environ.get(prefix + 'PORT', '33060')
        mysql_port_str = os.environ.get(prefix + 'MYSQL_PORT', '30930')
        stream_load_host = os.environ.get(prefix + 'STREAM_LOAD_HOST', host)
        stream_load_port_str = os.environ.get(prefix + 'STREAM_LOAD_PORT', port_str)
        stream_load_fallback_host = os.environ.get(prefix + 'STREAM_LOAD_FALLBACK_HOST', '')
        stream_load_fallback_port_str = os.environ.get(prefix + 'STREAM_LOAD_FALLBACK_PORT', '0')
        database = os.environ.get(prefix + 'DATABASE', '')
        table = os.environ.get(prefix + 'TABLE', '')

        if not host:
            raise ConfigError(prefix + 'HOST is required (please set via environment variable)')
        if not user:
            raise ConfigError(prefix + 'USER is required (please set via environment variable)')
        if not password:
            raise ConfigError(prefix + 'PASSWORD is required (please set via environment variable)')
        if not stream_load_host:
            raise ConfigError(prefix + 'STREAM_LOAD_HOST is required (or set ' + prefix + 'HOST as fallback)')
        if not database:
            raise ConfigError(prefix + 'DATABASE is required (please set via environment variable)')
        if not table:
            raise ConfigError(prefix + 'TABLE is required (please set via environment variable)')

        try:
            port = int(port_str)
        except ValueError:
            raise ConfigError(prefix + 'PORT must be an integer, got %r' % port_str)

        if not 1 <= port <= 65535:
            raise ConfigError(prefix + 'PORT must be between 1 and 65535, got %s' % port)

        try:
            mysql_port = int(mysql_port_str)
        except ValueError:
            raise ConfigError(prefix + 'MYSQL_PORT must be an integer, got %r' % mysql_port_str)

        if not 1 <= mysql_port <= 65535:
            raise ConfigError(prefix + 'MYSQL_PORT must be between 1 and 65535, got %s' % mysql_port)

        try:
            stream_load_port = int(stream_load_port_str)
        except ValueError:
            raise ConfigError(prefix + 'STREAM_LOAD_PORT must be an integer, got %r' % stream_load_port_str)

        if not 1 <= stream_load_port <= 65535:
            raise ConfigError(prefix + 'STREAM_LOAD_PORT must be between 1 and 65535, got %s' % stream_load_port)

        try:
            stream_load_fallback_port = int(stream_load_fallback_port_str)
        except ValueError:
            raise ConfigError(prefix + 'STREAM_LOAD_FALLBACK_PORT must be an integer, got %r' % stream_load_fallback_port_str)

        if stream_load_fallback_host and not 1 <= stream_load_fallback_port <= 65535:
            raise ConfigError(
                prefix + 'STREAM_LOAD_FALLBACK_PORT must be between 1 and 65535, got %s' % stream_load_fallback_port
            )

        return cls(
            host=host,
            user=user,
            password=password,
            port=port,
            mysql_port=mysql_port,
            stream_load_host=stream_load_host,
            stream_load_port=stream_load_port,
            stream_load_fallback_host=stream_load_fallback_host,
            stream_load_fallback_port=stream_load_fallback_port,
            database=database,
            table=table
        )


@dataclass
class BackfillConfig:
    max_workers: int = 4
    start_date: str = '2026-01-01'
    target_weekday: int = 4  # FRIDAY (0=Monday, 4=Friday, 6=Sunday)

    @classmethod
    def from_env(cls):
        max_workers_str = os.environ.get('MAX_WORKERS', '4')
        start_date = os.environ.get('START_DATE', '2026-01-01')
        target_weekday_str = os.environ.get('TARGET_WEEKDAY', 'friday')

        try:
            max_workers = int(max_workers_str)
        except ValueError:
            raise ConfigError('MAX_WORKERS must be an integer, got %r' % max_workers_str)

        if not 1 <= max_workers <= 32:
            raise ConfigError('MAX_WORKERS must be between 1 and 32, got %s' % max_workers)

        from utils.date_utils import validate_date, parse_weekday_param
        if not validate_date(start_date):
            raise ConfigError('START_DATE format invalid, expected YYYY-MM-DD, got %r' % start_date)

        try:
            target_weekday = parse_weekday_param(target_weekday_str)
        except ValueError as e:
            raise ConfigError('TARGET_WEEKDAY invalid: %s' % e)

        return cls(
            max_workers=max_workers,
            start_date=start_date,
            target_weekday=target_weekday
        )


__all__ = [
    'ConfigError',
    'APIConfig',
    'DatabaseConfig',
    'BackfillConfig'
]
