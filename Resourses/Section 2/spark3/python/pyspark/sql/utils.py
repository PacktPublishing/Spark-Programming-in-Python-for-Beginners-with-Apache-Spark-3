#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import py4j
import sys

if sys.version_info.major >= 3:
    unicode = str


class CapturedException(Exception):
    def __init__(self, desc, stackTrace, cause=None):
        self.desc = desc
        self.stackTrace = stackTrace
        self.cause = convert_exception(cause) if cause is not None else None

    def __str__(self):
        desc = self.desc
        # encode unicode instance for python2 for human readable description
        if sys.version_info.major < 3 and isinstance(desc, unicode):
            return str(desc.encode('utf-8'))
        else:
            return str(desc)


class AnalysisException(CapturedException):
    """
    Failed to analyze a SQL query plan.
    """


class ParseException(CapturedException):
    """
    Failed to parse a SQL command.
    """


class IllegalArgumentException(CapturedException):
    """
    Passed an illegal or inappropriate argument.
    """


class StreamingQueryException(CapturedException):
    """
    Exception that stopped a :class:`StreamingQuery`.
    """


class QueryExecutionException(CapturedException):
    """
    Failed to execute a query.
    """


class UnknownException(CapturedException):
    """
    None of the above exceptions.
    """


def convert_exception(e):
    s = e.toString()
    stackTrace = '\n\t at '.join(map(lambda x: x.toString(), e.getStackTrace()))
    c = e.getCause()
    if s.startswith('org.apache.spark.sql.AnalysisException: '):
        return AnalysisException(s.split(': ', 1)[1], stackTrace, c)
    if s.startswith('org.apache.spark.sql.catalyst.analysis'):
        return AnalysisException(s.split(': ', 1)[1], stackTrace, c)
    if s.startswith('org.apache.spark.sql.catalyst.parser.ParseException: '):
        return ParseException(s.split(': ', 1)[1], stackTrace, c)
    if s.startswith('org.apache.spark.sql.streaming.StreamingQueryException: '):
        return StreamingQueryException(s.split(': ', 1)[1], stackTrace, c)
    if s.startswith('org.apache.spark.sql.execution.QueryExecutionException: '):
        return QueryExecutionException(s.split(': ', 1)[1], stackTrace, c)
    if s.startswith('java.lang.IllegalArgumentException: '):
        return IllegalArgumentException(s.split(': ', 1)[1], stackTrace, c)
    return UnknownException(s, stackTrace, c)


def capture_sql_exception(f):
    def deco(*a, **kw):
        try:
            return f(*a, **kw)
        except py4j.protocol.Py4JJavaError as e:
            converted = convert_exception(e.java_exception)
            if not isinstance(converted, UnknownException):
                raise converted
            else:
                raise
    return deco


def install_exception_handler():
    """
    Hook an exception handler into Py4j, which could capture some SQL exceptions in Java.

    When calling Java API, it will call `get_return_value` to parse the returned object.
    If any exception happened in JVM, the result will be Java exception object, it raise
    py4j.protocol.Py4JJavaError. We replace the original `get_return_value` with one that
    could capture the Java exception and throw a Python one (with the same error message).

    It's idempotent, could be called multiple times.
    """
    original = py4j.protocol.get_return_value
    # The original `get_return_value` is not patched, it's idempotent.
    patched = capture_sql_exception(original)
    # only patch the one used in py4j.java_gateway (call Java API)
    py4j.java_gateway.get_return_value = patched


def toJArray(gateway, jtype, arr):
    """
    Convert python list to java type array
    :param gateway: Py4j Gateway
    :param jtype: java type of element in array
    :param arr: python type list
    """
    jarr = gateway.new_array(jtype, len(arr))
    for i in range(0, len(arr)):
        jarr[i] = arr[i]
    return jarr


def require_minimum_pandas_version():
    """ Raise ImportError if minimum version of Pandas is not installed
    """
    # TODO(HyukjinKwon): Relocate and deduplicate the version specification.
    minimum_pandas_version = "0.23.2"

    from distutils.version import LooseVersion
    try:
        import pandas
        have_pandas = True
    except ImportError:
        have_pandas = False
    if not have_pandas:
        raise ImportError("Pandas >= %s must be installed; however, "
                          "it was not found." % minimum_pandas_version)
    if LooseVersion(pandas.__version__) < LooseVersion(minimum_pandas_version):
        raise ImportError("Pandas >= %s must be installed; however, "
                          "your version was %s." % (minimum_pandas_version, pandas.__version__))


def require_minimum_pyarrow_version():
    """ Raise ImportError if minimum version of pyarrow is not installed
    """
    # TODO(HyukjinKwon): Relocate and deduplicate the version specification.
    minimum_pyarrow_version = "0.15.1"

    from distutils.version import LooseVersion
    import os
    try:
        import pyarrow
        have_arrow = True
    except ImportError:
        have_arrow = False
    if not have_arrow:
        raise ImportError("PyArrow >= %s must be installed; however, "
                          "it was not found." % minimum_pyarrow_version)
    if LooseVersion(pyarrow.__version__) < LooseVersion(minimum_pyarrow_version):
        raise ImportError("PyArrow >= %s must be installed; however, "
                          "your version was %s." % (minimum_pyarrow_version, pyarrow.__version__))
    if os.environ.get("ARROW_PRE_0_15_IPC_FORMAT", "0") == "1":
        raise RuntimeError("Arrow legacy IPC format is not supported in PySpark, "
                           "please unset ARROW_PRE_0_15_IPC_FORMAT")


def require_test_compiled():
    """ Raise Exception if test classes are not compiled
    """
    import os
    import glob
    try:
        spark_home = os.environ['SPARK_HOME']
    except KeyError:
        raise RuntimeError('SPARK_HOME is not defined in environment')

    test_class_path = os.path.join(
        spark_home, 'sql', 'core', 'target', '*', 'test-classes')
    paths = glob.glob(test_class_path)

    if len(paths) == 0:
        raise RuntimeError(
            "%s doesn't exist. Spark sql test classes are not compiled." % test_class_path)


class ForeachBatchFunction(object):
    """
    This is the Python implementation of Java interface 'ForeachBatchFunction'. This wraps
    the user-defined 'foreachBatch' function such that it can be called from the JVM when
    the query is active.
    """

    def __init__(self, sql_ctx, func):
        self.sql_ctx = sql_ctx
        self.func = func

    def call(self, jdf, batch_id):
        from pyspark.sql.dataframe import DataFrame
        try:
            self.func(DataFrame(jdf, self.sql_ctx), batch_id)
        except Exception as e:
            self.error = e
            raise e

    class Java:
        implements = ['org.apache.spark.sql.execution.streaming.sources.PythonForeachBatchFunction']


def to_str(value):
    """
    A wrapper over str(), but converts bool values to lower case strings.
    If None is given, just returns None, instead of converting it to string "None".
    """
    if isinstance(value, bool):
        return str(value).lower()
    elif value is None:
        return value
    else:
        return str(value)
