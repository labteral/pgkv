#!/usr/bin/env python
# -*- coding: utf-8 -*-

import psycopg2
import psycopg2.sql
from psycopg2.extras import execute_values
import json
from datetime import datetime
from threading import Lock
from typing import List


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
    DEFAULT_column = 'value'

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
        self._setup_namespace()

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
        key=None,
        value=None,
        column=None,
        kv_pairs: List = None,
        upsert=True,
    ):
        table = table.lower()

        if (not key or not value) and not kv_pairs:
            raise ValueError

        if not kv_pairs:
            kv_pairs = [(key, value)]
        sample_value = kv_pairs[0][1]

        if column is None:
            column = self.DEFAULT_column
        column = column.lower()

        if isinstance(sample_value, dict):
            kv_pairs = [
                (
                    key,
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(',', ':'),
                    ),
                ) for key, value in kv_pairs
            ]

        if table not in self._known_tables:
            self._known_tables[table] = set()
            self._create_table(table)
            self._configure_distributed_table(table)

        if column not in self._known_tables[table]:
            self._create_column(table, column, sample_value)
            self._known_tables[table].add(column)

        autocommit = True if self._cursor is None else False
        if autocommit:
            self.begin()

        query = """
            INSERT INTO {table}
            (
                key,
                {column}
            )
            VALUES %s
        """
        if upsert is True:
            query += """
                ON CONFLICT (key) DO UPDATE
                SET {column} = EXCLUDED.{column}
            """

        query = psycopg2.sql.SQL(query).format(
            table=psycopg2.sql.Identifier(table),
            column=psycopg2.sql.Identifier(column),
        )

        execute_values(self._cursor, query, kv_pairs)

        if autocommit:
            self.commit()

    @thread_safe
    @rollback_on_error
    def get(
        self,
        table,
        key,
        column=None,
        full_row=False,
    ):
        table = table.lower()

        if column is None:
            column = self.DEFAULT_column
        column = column.lower()

        query = 'SELECT '
        if full_row is True:
            query += '*'
        else:
            query += '{column}'
        query += """
            FROM {table}
            WHERE key = %s
            LIMIT 1;
        """

        query = psycopg2.sql.SQL(query).format(
            table=psycopg2.sql.Identifier(table),
            column=psycopg2.sql.Identifier(column)
        )

        autocommit = True if self._cursor is None else False
        if autocommit:
            self.begin()

        try:
            self._cursor.execute(query, (key, ))
        except (psycopg2.errors.UndefinedTable,
                psycopg2.errors.UndefinedColumn):
            if autocommit:
                self.commit()
            return None

        row = self._cursor.fetchone()
        if row is None:
            result = None
        else:
            result = self._get_results([row], remove_keys=True)[0]

        if autocommit:
            self.commit()

        return result

    @thread_safe
    @rollback_on_error
    def exists(
        self,
        table,
        key,
    ):
        return True if self.get(table, key) else False

    @thread_safe
    @rollback_on_error
    def delete(
        self,
        table,
        key,
    ):
        table = table.lower()

        query = psycopg2.sql.SQL(
            """
                DELETE FROM {table}
                WHERE key = %s;
            """
        ).format(table=psycopg2.sql.Identifier(table))

        autocommit = True if self._cursor is None else False
        if autocommit:
            self.begin()

        try:
            self._cursor.execute(query, (key, ))
        except (psycopg2.errors.UndefinedTable,
                psycopg2.errors.UndefinedColumn):
            pass

    @thread_safe
    @rollback_on_error
    def scan(
        self,
        table,
        column=None,
        start_key=None,
        stop_key=None,
        order_by=None,
        order_by_timestamp=False,
        order=None,
        limit=None,
        full_row=False,
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

        if column is None:
            column = self.DEFAULT_column
        column = column.lower()

        query = 'SELECT '
        if full_row is True:
            query += '*'
        else:
            query += 'key, {column}'
        query += 'FROM {table}'

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
            column=psycopg2.sql.Identifier(column),
            order_by=psycopg2.sql.Identifier(order_by)
        )

        autocommit = True if self._cursor is None else False
        if autocommit:
            self.begin()

        try:
            self._cursor.execute(query, query_variables)
        except (psycopg2.errors.UndefinedTable,
                psycopg2.errors.UndefinedColumn):
            if autocommit:
                self.commit()
            return None

        rows = self._cursor.fetchall()
        results = self._get_results(rows, remove_keys=False)

        if autocommit:
            self.commit()

        return results

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

    def _configure_distributed_table(self, table):
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
            cursor.execute(query, (table, ))

        except psycopg2.errors.InvalidTableDefinition:
            # Already distributed table
            return
        except psycopg2.errors.UndefinedFunction:
            return
        finally:
            cursor.close()
            connection.close()

    def _create_table(self, table):
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
        ).format(table=psycopg2.sql.Identifier(table))
        cursor.execute(query)

        query = psycopg2.sql.SQL(
            """
                CREATE INDEX IF NOT EXISTS key_hash_idx
                ON {table} USING HASH (key);

                CREATE INDEX IF NOT EXISTS key_created_at_idx
                ON {table} (created_at);
            """
        ).format(table=psycopg2.sql.Identifier(table))
        cursor.execute(query)

        cursor.close()
        connection.close()

    def _create_column(
        self,
        table,
        column,
        sample_value,
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
        query = psycopg2.sql.SQL('SELECT {column} FROM {table} LIMIT 1;'
                                 ).format(
                                     table=psycopg2.sql.Identifier(table),
                                     column=psycopg2.sql.Identifier(column),
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
                    ADD COLUMN IF NOT EXISTS {column} {column_type};
                """
            ).format(
                table=psycopg2.sql.Identifier(table),
                column=psycopg2.sql.Identifier(column),
                column_type=psycopg2.sql.SQL(column_type),
            )
            cursor.execute(query)

        cursor.close()
        connection.close()

    def _setup_namespace(self):
        # Connect to the default database: postgres
        connection = self._get_connection(namespace='postgres')
        connection.autocommit = True
        connection.set_isolation_level(
            psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT
        )
        cursor = connection.cursor()

        try:
            query = psycopg2.sql.SQL('CREATE DATABASE {database}').format(
                database=psycopg2.sql.Identifier(self._namespace),
            )
            cursor.execute(query)
        except psycopg2.errors.DuplicateDatabase:
            pass

        cursor.close()
        connection.close()

        connection = self._get_connection()
        connection.autocommit = True
        connection.set_isolation_level(
            psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT
        )
        cursor = connection.cursor()

        # Enable the extension for this namespace
        try:
            query = 'CREATE EXTENSION IF NOT EXISTS citus;'
            cursor.execute(query)
        except psycopg2.errors.UndefinedFile:
            pass

        cursor.close()
        connection.close()

    def _get_results(
        self,
        rows,
        remove_keys=False,
    ):
        columns = [description[0] for description in self._cursor.description]

        results = []
        for row in rows:
            result = {}

            for index, column in enumerate(columns):
                value = row[index]
                if remove_keys is True and column == 'key':
                    continue
                if isinstance(value, memoryview):
                    value = result.tobytes()
                result[column] = value

            results.append(result)

        return results
