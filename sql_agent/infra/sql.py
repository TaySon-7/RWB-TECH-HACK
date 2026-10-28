"""SQL structure checks. Database permissions remain the final boundary."""
from sqlglot import exp, parse
from sqlglot.optimizer.scope import traverse_scope

ALLOWED_FUNCTIONS = {
    'SUM', 'AVG', 'COUNT', 'MIN', 'MAX', 'COALESCE', 'NULLIF', 'ROUND',
    'CAST', 'TRY_CAST', 'EXTRACT', 'TIMESTAMP_TRUNC', 'DATE_TRUNC',
    'TIME_TO_STR', 'TO_CHAR', 'GENERATE_SERIES', 'GENERATE_TIMESTAMP_ARRAY',
    'EXPLODING_GENERATE_SERIES',
    'LOWER', 'UPPER', 'TRIM', 'LENGTH', 'ABS', 'CONCAT', 'CONCAT_WS',
    'ROW_NUMBER', 'RANK', 'DENSE_RANK', 'LAG', 'LEAD', 'FIRST_VALUE',
    'LAST_VALUE', 'ARRAY_AGG', 'BOOL_AND', 'BOOL_OR', 'PERCENTILE_CONT',
    'PERCENTILE_DISC', 'CURRENT_DATE', 'CURRENT_TIMESTAMP', 'DATE',
    'CEIL', 'FLOOR', 'GREATEST', 'LEAST', 'STDDEV', 'VARIANCE',
    'TOSTARTOFMONTH', 'TOSTARTOFYEAR', 'TODATE', 'TODATETIME', 'TODATETIME64',
    'TOFLOAT64', 'TODECIMAL64', 'FORMATDATETIME', 'COUNTIF', 'SUMIF', 'UNIQEXACT',
    'TOYEAR', 'TOMONTH', 'TODAYOFMONTH', 'TODAYOFWEEK', 'IFNULL', 'MULTIIF',
    'COUNT_IF', 'SUM_IF', 'TOSTRING', 'QUANTILE', 'MEDIAN', 'STDDEV_POP', 'STDDEV_SAMP',
    'CASE', 'IF', 'AND', 'OR',  # SQL syntax also represented as Func nodes.
}


def validate_sql(sql: str, tables: set[str], dialect: str = 'postgres') -> str:
    statements = [s for s in parse(sql, read=dialect) if s is not None]
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ValueError('Only one SELECT/CTE query is allowed')
    tree = statements[0]
    forbidden = ('Insert', 'Update', 'Delete', 'Create', 'Drop', 'Alter', 'Command', 'Copy', 'Into', 'Lock', 'Transaction')
    for node in tree.walk():
        if type(node).__name__ in forbidden:
            raise ValueError(f'Forbidden SQL operation: {type(node).__name__}')
        if isinstance(node, exp.Func):
            anonymous = isinstance(node, (exp.Anonymous, exp.AnonymousAggFunc, exp.CombinedAggFunc))
            name = node.name.upper() if anonymous else node.sql_name().upper()
            if name not in ALLOWED_FUNCTIONS:
                raise ValueError(f'Function is not allowed: {name}')
        # Do not permit schema-qualified function calls (including user functions).
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise ValueError('Qualified function calls are not allowed')
    # Scope analysis distinguishes actual tables from CTE aliases.
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                if source.catalog or source.db not in {'core', 'mart'} or (source.name not in tables and source.db + '.' + source.name not in tables):
                    raise ValueError(f'Only known core tables are allowed: {source.sql()}')
    return sql.strip().rstrip(';') + ';'


def referenced_tables(sql: str, dialect: str = 'postgres') -> list[str]:
    tree = parse(sql, read=dialect)[0]
    names = set()
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table) and source.db:
                names.add(source.db + '.' + source.name)
    return sorted(names)

