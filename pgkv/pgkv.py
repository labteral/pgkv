#!/usr/bin/env python
# -*- coding: utf-8 -*-

import psycopg2
import psycopg2.sql
import json
from datetime import datetime
from threading import Lock


def thread_safe(method):
    def _method(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return _method


def rollback_on_error(method):
    def _method(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as error:
            try:
                self.rollback()
            except Exception:
                pass
            raise error
    return _method


class Store:
    DEFAULT_COLUMN_FAMILY = 'value'

    def __init__(
        self,
        host=None,
        port=None,
        namespace=None,
        username=None,
        password=None,
    ):
        self._lock = Lock()

        if host is None:
            host = '127.0.0.1'
        self._host = host

        if port is None:
            port = 5432
        self._port = port

        if namespace is None:
            raise ValueError
        self._namespace = namespace

        if username is None:
            username = 'postgres'
        self._username = username

        if password is None:
            password = ''
        self._password = password

        self._known_tables = {}

        self._cursor = None
        self._setup_database()

    @property
    def host(self):
        return self._host

    @property
    def port(self):
        return self._port

    @property
    def namespace(self):
        return self._namespace

    @property
    def username(self):
        return self._username

    @property
    def password(self):
        return self._password

    @property
    def cursor(self):
        return self._cursor

    @property
    def connection(self):
        return self._connnection

    def begin(self):
        self._connection = self._get_connection()
        if self.cursor is not None:
            raise RuntimeError('transaction already started')
        self._cursor = self._connection.cursor()

    def commit(self):
        if self.cursor is None:
            return
        self._connection.commit()
        self._cursor.close()
        self._cursor = None
        self._connection.close()

    def rollback(self):
        self._connection.rollback()
        self._cursor.close()
        self._cursor = None
        self._connection.close()

    @thread_safe
    @rollback_on_error
    def put(
        self,
        table,
        key,
        value,
        column_family=None
    ):
        table = table.lower()

        if column_family is None:
            column_family = self.DEFAULT_COLUMN_FAMILY
        column_family = column_family.lower()

        if table not in self._known_tables:
            self._known_tables[table] = set()
            self._create_table(table)
            self._configure_distributed_table(table)

        if column_family not in self._known_tables[table]:
            self._create_column_family(
                table,
                column_family,
                value
            )
            self._known_tables[table].add(column_family)

        autocommit = True if self._cursor is None else False
        if autocommit:
            self.begin()

        query = psycopg2.sql.SQL(
            """
            INSERT INTO {table}
            (
                key,
                {column_family}
            )
            VALUES (
                %s,
                %s
            )
            ON CONFLICT (key) DO UPDATE
            SET {column_family} = %s;
        """
        ).format(
            table=psycopg2.sql.Identifier(table),
            column_family=psycopg2.sql.Identifier(column_family),
        )

        if isinstance(value, dict):
            value = json.dumps(
                value,
                ensure_ascii=False,
                separators=(',', ':')
            )

        self._cursor.execute(
            query,
            (
                key,
                value,
                value
            )
        )

        if autocommit:
            self.commit()

    @thread_safe
    @rollback_on_error
    def get(
        self,
        table,
        key,
        column_family=None
    ):
        table = table.lower()

        if column_family is None:
            column_family = self.DEFAULT_COLUMN_FAMILY
        column_family = column_family.lower()

        query = psycopg2.sql.SQL(
            """
                SELECT {column_family}
                FROM {table}
                WHERE key = %s
                LIMIT 1;
            """
        ).format(
            table=psycopg2.sql.Identifier(table),
            column_family=psycopg2.sql.Identifier(column_family)
        )

        autocommit = True if self._cursor is None else False
        if autocommit:
            self.begin()

        try:
            self._cursor.execute(query, (key,))
        except (
            psycopg2.errors.UndefinedTable,
            psycopg2.errors.UndefinedColumn
        ):
            if autocommit:
                self.commit()
            return None

        row = self._cursor.fetchone()
        result = row[0]

        if isinstance(result, memoryview):
            result = result.tobytes()

        if autocommit:
            self.commit()

        if not row:
            return None

        return result

    @thread_safe
    @rollback_on_error
    def exists(
        self,
        table,
        key
    ):
        return True if self.get(table, key) else False

    @thread_safe
    @rollback_on_error
    def delete(
        self,
        table,
        key
    ):
        table = table.lower()

        query = psycopg2.sql.SQL(
            """
                DELETE FROM {table}
                WHERE key = %s;
            """
        ).format(
            table=psycopg2.sql.Identifier(table)
        )

        autocommit = True if self._cursor is None else False
        if autocommit:
            self.begin()

        try:
            self._cursor.execute(query, (key,))
        except (
            psycopg2.errors.UndefinedTable,
            psycopg2.errors.UndefinedColumn
        ):
            pass

    @thread_safe
    @rollback_on_error
    def scan(
        self,
        table,
        column_family=None,
        start_key=None,
        stop_key=None,
        order_by=None,
        order_by_timestamp=None,
        order=None,
        limit=None
    ):
        table = table.lower()

        if order is not None:
            if isinstance(order, int):
                if order == 1:
                    order = 'ASC'
                elif order == -1:
                    order = 'DESC'
                else:
                    raise ValueError('order must be 1 or -1 if type is int')
            elif not isinstance(order, str):
                raise TypeError('order must be of type int or string')
        order = order.upper() if order else 'ASC'
        if order and order not in ('ASC', 'DESC'):
            raise ValueError('order must be ASC or DESC')

        if order_by_timestamp is True:
            order_by = 'created_at'
        if order_by is None:
            order_by = 'key'
        order_line = 'ORDER BY {order_by}' + f'{order}'

        if limit and not isinstance(limit, int):
            raise TypeError
        limit = limit or 'ALL'
        limit_line = f'LIMIT {limit};'

        if column_family is None:
            column_family = self.DEFAULT_COLUMN_FAMILY
        column_family = column_family.lower()

        query = """
            SELECT key, {column_family}
            FROM {table}
        """

        if start_key is not None and stop_key is not None:
            query += 'WHERE key >= %s AND key <= %s'
            query_variables = (start_key, stop_key)
        elif start_key is not None:
            query += 'WHERE key >= %s'
            query_variables = (start_key, )
        elif stop_key is not None:
            query += 'WHERE key <= %s'
            query_variables = (stop_key, )
        else:
            query_variables = None

        query += order_line + ' ' + limit_line
        query = psycopg2.sql.SQL(query).format(
            table=psycopg2.sql.Identifier(table),
            column_family=psycopg2.sql.Identifier(column_family),
            order_by=psycopg2.sql.Identifier(order_by)
        )

        autocommit = True if self._cursor is None else False
        if autocommit:
            self.begin()

        try:
            self._cursor.execute(query, query_variables)
        except (
            psycopg2.errors.UndefinedTable,
            psycopg2.errors.UndefinedColumn
        ):
            if autocommit:
                self.commit()
            return None

        rows = self._cursor.fetchall()

        if autocommit:
            self.commit()

        for row in rows:
            result = row[1]
            if isinstance(result, memoryview):
                result = result.tobytes()
            yield (row[0], result)

    def _configure_distributed_table(
        self,
        table
    ):
        connection = self._get_connection()
        connection.autocommit = True
        cursor = connection.cursor()

        try:
            query = """
                SELECT create_distributed_table(
                    %s,
                    'key',
                    colocate_with => 'none'
                );
            """
            cursor.execute(query, (table,))

        except psycopg2.errors.InvalidTableDefinition:
            # Already distributed table
            return
        except psycopg2.errors.UndefinedFunction:
            return
        finally:
            cursor.close()
            connection.close()

    def _create_table(
        self,
        table
    ):
        connection = self._get_connection()
        connection.autocommit = True
        cursor = connection.cursor()

        query = psycopg2.sql.SQL(
            """
                CREATE TABLE IF NOT EXISTS {table}
                (
                    key TEXT NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    PRIMARY KEY (key)
                );
            """
        ).format(
            table=psycopg2.sql.Identifier(table)
        )
        cursor.execute(query)

        query = psycopg2.sql.SQL(
            """
                CREATE INDEX IF NOT EXISTS key_hash_idx
                ON {table} USING HASH (key);

                CREATE INDEX IF NOT EXISTS key_created_at_idx
                ON {table} (created_at);
            """
        ).format(
            table=psycopg2.sql.Identifier(table)
        )
        cursor.execute(query)

        cursor.close()
        connection.close()

    def _create_column_family(
        self,
        table,
        column_family,
        sample_value
    ):
        if isinstance(sample_value, str):
            column_type = 'TEXT'
        elif isinstance(sample_value, dict):
            column_type = 'JSONB'
        elif isinstance(sample_value, bool):
            column_type = 'BOOLEAN'
        elif isinstance(sample_value, int):
            column_type = 'BIGINT'
        elif isinstance(sample_value, float):
            column_type = 'DECIMAL'
        elif isinstance(sample_value, bytes):
            column_type = 'BYTEA'
        elif isinstance(sample_value, datetime):
            column_type = 'TIMESTAMP WITHOUT TIME ZONE'
        else:
            raise ValueError

        connection = self._get_connection()
        connection.autocommit = True
        cursor = connection.cursor()

        # With citus enabled ADD COLUMN IF NOT EXISTS fails if it exists
        query = psycopg2.sql.SQL(
            'SELECT {column_family} FROM {table} LIMIT 1;'
        ).format(
            table=psycopg2.sql.Identifier(table),
            column_family=psycopg2.sql.Identifier(column_family),
        )

        try:
            cursor.execute(query)
            columnExists = True
        except psycopg2.errors.UndefinedColumn:
            columnExists = False

        if not columnExists:
            query = psycopg2.sql.SQL(
                """
                    ALTER TABLE {table}
                    ADD COLUMN IF NOT EXISTS {column_family} {column_type};
                """
            ).format(
                table=psycopg2.sql.Identifier(table),
                column_family=psycopg2.sql.Identifier(column_family),
                column_type=psycopg2.sql.SQL(column_type),
            )
            cursor.execute(query)

        cursor.close()
        connection.close()

    def _setup_database(self):
        connection = self._get_connection('postgres')
        connection.autocommit = True
        connection.set_isolation_level(
            psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT
        )
        cursor = connection.cursor()

        try:
            query = psycopg2.sql.SQL(
                'CREATE DATABASE {database}'
            ).format(
                database=psycopg2.sql.Identifier(self._namespace),
            )
            cursor.execute(query)
        except psycopg2.errors.DuplicateDatabase:
            pass

        # Enable the extension for this namespace
        try:
            query = 'CREATE EXTENSION IF NOT EXISTS citus;'
            cursor.execute(query)
        except psycopg2.errors.UndefinedFile:
            pass

        connection.close()

    def _get_connection(self, namespace=None):
        if namespace is None:
            namespace = self._namespace
        connection = psycopg2.connect(
            host=self._host,
            port=self._port,
            database=namespace,
            user=self._username,
            password=self._password
        )
        return connection
