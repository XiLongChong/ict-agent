"""DuckDB 数据导入和只读查询边界。"""

from __future__ import annotations

import csv
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import duckdb

type DatabaseScalar = str | int | float | bool | None
type SqlParameters = Sequence[object]


class DataAccessError(RuntimeError):
    """数据文件、数据库或查询不可用。"""


@dataclass(frozen=True)
class TableSpec:
    """一张固定比赛 CSV 的导入契约。"""

    filename: str
    required_columns: frozenset[str]
    type_overrides: Mapping[str, str]
    date_column: str | None


@dataclass(frozen=True)
class ImportSummary:
    """一张表完成导入后的校验摘要。"""

    table: str
    rows: int
    min_date: str | None
    max_date: str | None


@dataclass(frozen=True)
class QueryResult:
    """DuckDB 查询的 JSON 友好结果。"""

    columns: tuple[str, ...]
    rows: tuple[tuple[DatabaseScalar, ...], ...]


@dataclass(frozen=True)
class CaseWrite:
    """规则扫描写入案件库的案件记录。"""

    case_id: str
    case_type: str
    entity_type: str
    entity_id: str
    entity_label: str
    entity_context: Mapping[str, DatabaseScalar]
    observation_date: str
    priority: str
    exposure_amount: float
    summary: str
    rule_hit_count: int
    rule_set_version: str
    created_at: str


@dataclass(frozen=True)
class RuleHitWrite:
    """规则扫描写入案件库的命中记录。"""

    rule_hit_id: str
    case_id: str
    rule_id: str
    rule_name: str
    rule_version: str
    severity: str
    exposure_amount: float
    reason: str
    metrics: Mapping[str, DatabaseScalar]
    threshold_source: str
    sources: Sequence[str]
    period: str


@dataclass(frozen=True)
class RuleRunWrite:
    """一次规则扫描的摘要。"""

    run_id: str
    rule_set_version: str
    observation_date: str
    cases_detected: int
    rule_hits: int
    receivable_cases: int
    inventory_cases: int
    created_at: str


@dataclass(frozen=True)
class InvestigationWrite:
    """一次 Agent 调查的持久化记录。"""

    investigation_id: str
    case_id: str
    report_json: str
    evidence_json: str
    created_at: str


@dataclass(frozen=True)
class ReviewWrite:
    """一次人工审核的持久化记录。"""

    review_id: str
    case_id: str
    decision: str
    reviewer: str
    reason: str
    action: str | None
    next_review_at: str | None
    created_at: str


TABLE_SPECS: dict[str, TableSpec] = {
    "sales": TableSpec(
        filename="销售流水.csv",
        required_columns=frozenset(
            {
                "出库日期",
                "客户编号",
                "合同号",
                "销售订单号",
                "库存组织名称",
                "物料编码",
                "数量",
                "出库类型",
                "事务处理类型名称",
                "销售金额_折扣后_含税",
                "出库成本金额",
            }
        ),
        type_overrides={
            "出库日期": "TIMESTAMP",
            "订单创建日期": "TIMESTAMP",
            "客户编号": "VARCHAR",
            "项目编号": "VARCHAR",
            "合同号": "VARCHAR",
            "销售订单号": "VARCHAR",
            "出库单号": "VARCHAR",
            "物料编码": "VARCHAR",
            "数量": "DOUBLE",
            "销售金额_折扣后_含税": "DOUBLE",
            "出库成本金额": "DOUBLE",
            "价保": "DOUBLE",
            "厂商返利": "DOUBLE",
            "现金折扣": "DOUBLE",
        },
        date_column="出库日期",
    ),
    "payments": TableSpec(
        filename="业务回款明细.csv",
        required_columns=frozenset(
            {
                "回款日期",
                "客户编号",
                "合同号",
                "销售订单号",
                "回款金额",
                "超期利息金额",
                "最终承诺还款日期",
                "是否超期",
                "超期天数",
                "物料编码",
            }
        ),
        type_overrides={
            "合同号": "VARCHAR",
            "项目编号": "VARCHAR",
            "客户编号": "VARCHAR",
            "销售订单号": "VARCHAR",
            "出库单号": "VARCHAR",
            "发票号": "VARCHAR",
            "收款编号": "VARCHAR",
            "物料编码": "VARCHAR",
            "出库日期": "TIMESTAMP",
            "开票日期": "TIMESTAMP",
            "回款日期": "TIMESTAMP",
            "首次承诺还款日期": "TIMESTAMP",
            "最终承诺还款日期": "TIMESTAMP",
            "超期天数": "INTEGER",
            "回款金额": "DOUBLE",
            "超期利息金额": "DOUBLE",
        },
        date_column="回款日期",
    ),
    "contracts": TableSpec(
        filename="增值合同签约明细.csv",
        required_columns=frozenset(
            {
                "申请日期",
                "合同编号",
                "合同状态",
                "销售金额",
                "实际净毛利率_不含税",
                "开票金额1",
            }
        ),
        type_overrides={
            "申请日期": "TIMESTAMP",
            "合同编号": "VARCHAR",
            "销售金额": "DOUBLE",
            "实估毛利_不含税": "DOUBLE",
            "实际净毛利_不含税": "DOUBLE",
            "开票金额1": "DOUBLE",
            "实际净毛利率_不含税": "DOUBLE",
        },
        date_column="申请日期",
    ),
    "ar_snapshots": TableSpec(
        filename="应收快照_月末24期.csv",
        required_columns=frozenset(
            {
                "快照时间",
                "合同号",
                "客户编号",
                "客户名称",
                "销售订单号",
                "应收金额",
                "超期应收金额",
                "超期30天以上金额",
                "超期60天以上金额",
                "最终承诺还款日期",
                "是否展期",
                "超期天数",
                "物料编码",
            }
        ),
        type_overrides={
            "快照时间": "TIMESTAMP",
            "合同号": "VARCHAR",
            "客户编号": "VARCHAR",
            "销售订单号": "VARCHAR",
            "物料编码": "VARCHAR",
            "出库日期": "TIMESTAMP",
            "账期起算日期": "TIMESTAMP",
            "最终承诺还款日期": "TIMESTAMP",
            "超期天数": "INTEGER",
            "应收金额": "DOUBLE",
            "超期应收金额": "DOUBLE",
            "超期30天以上金额": "DOUBLE",
            "超期60天以上金额": "DOUBLE",
        },
        date_column="快照时间",
    ),
    "inventory_snapshots": TableSpec(
        filename="库龄快照_季末8期.csv",
        required_columns=frozenset(
            {
                "快照日期",
                "物料编码",
                "库存组织名称",
                "数量",
                "库龄",
                "含税总价",
                "是否超期",
            }
        ),
        type_overrides={
            "快照日期": "TIMESTAMP",
            "物料编码": "VARCHAR",
            "批次": "VARCHAR",
            "项目编号": "VARCHAR",
            "采购订单编号": "VARCHAR",
            "实际采购日期": "TIMESTAMP",
            "数量": "DOUBLE",
            "库龄": "INTEGER",
            "含税单价": "DOUBLE",
            "含税总价": "DOUBLE",
            "超期天数": "INTEGER",
        },
        date_column="快照日期",
    ),
    "extensions": TableSpec(
        filename="展期记录.csv",
        required_columns=frozenset(
            {
                "快照时间",
                "合同号",
                "客户编号",
                "销售订单号",
                "物料编码",
                "最终承诺还款日期",
                "是否展期",
                "超期天数",
                "gkey",
            }
        ),
        type_overrides={
            "快照时间": "TIMESTAMP",
            "合同号": "VARCHAR",
            "客户编号": "VARCHAR",
            "销售订单号": "VARCHAR",
            "物料编码": "VARCHAR",
            "gkey": "VARCHAR",
            "账期起算日期": "TIMESTAMP",
            "最终承诺还款日期": "TIMESTAMP",
            "超期天数": "INTEGER",
            "应收金额": "DOUBLE",
            "超期应收金额": "DOUBLE",
        },
        date_column="快照时间",
    ),
    "customer_credit": TableSpec(
        filename="客户授信.csv",
        required_columns=frozenset(
            {
                "客户编号_中台",
                "客户名称",
                "授信额度",
                "黑白名单状态",
                "黑白名单原因",
                "黑白名单创建时间",
                "失信分级",
                "净资产",
                "净利润",
                "信用保险",
            }
        ),
        type_overrides={
            "客户编号_中台": "VARCHAR",
            "授信额度": "DOUBLE",
            "冻结金额": "DOUBLE",
            "赊销阈值": "DOUBLE",
            "临时额度": "DOUBLE",
            "分区信控审批额度": "DOUBLE",
            "黑白名单状态": "INTEGER",
            "黑白名单创建时间": "TIMESTAMP",
            "失信分级": "VARCHAR",
            "信用保险": "VARCHAR",
            "净资产": "DOUBLE",
            "净利润": "DOUBLE",
        },
        date_column=None,
    ),
}


def _normalize_value(value: object) -> DatabaseScalar:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _read_header(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return next(csv.reader(handle))
    except (OSError, StopIteration, UnicodeError, csv.Error) as exc:
        raise DataAccessError(f"无法读取 CSV 表头：{path.name}") from exc


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _types_literal(overrides: Mapping[str, str]) -> str:
    items = ", ".join(
        f"{_sql_string(column)}: {_sql_string(data_type)}"
        for column, data_type in overrides.items()
    )
    return "{" + items + "}"


def _validate_source(path: Path, spec: TableSpec) -> dict[str, str]:
    if not path.is_file():
        raise DataAccessError(f"缺少比赛数据文件：{path}")
    header = _read_header(path)
    missing = sorted(spec.required_columns - set(header))
    if missing:
        raise DataAccessError(f"{path.name} 缺少必需列：{', '.join(missing)}")
    return {name: data_type for name, data_type in spec.type_overrides.items() if name in header}


def _validate_table(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    spec: TableSpec,
) -> ImportSummary:
    row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    row_count = int(row[0]) if row is not None else 0
    if row_count == 0:
        raise DataAccessError(f"{spec.filename} 没有数据行，已取消重建。")

    if spec.date_column is None:
        return ImportSummary(table=table, rows=row_count, min_date=None, max_date=None)

    date_row = connection.execute(
        f'SELECT MIN("{spec.date_column}"), MAX("{spec.date_column}") FROM "{table}"'
    ).fetchone()
    if date_row is None or date_row[0] is None or date_row[1] is None:
        raise DataAccessError(f"{spec.filename} 的 {spec.date_column} 没有有效日期。")
    return ImportSummary(
        table=table,
        rows=row_count,
        min_date=str(_normalize_value(date_row[0])),
        max_date=str(_normalize_value(date_row[1])),
    )


def _remove_database_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}.wal")):
        if candidate.exists():
            candidate.unlink()


def rebuild_database(data_dir: Path, database_path: Path) -> list[ImportSummary]:
    """从固定 7 张 CSV 原子重建 DuckDB。"""

    sources: dict[str, tuple[Path, dict[str, str]]] = {}
    for table, spec in TABLE_SPECS.items():
        source = data_dir / spec.filename
        sources[table] = (source, _validate_source(source, spec))

    database_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = database_path.parent / f".{database_path.name}.{uuid4().hex}.tmp"
    connection: duckdb.DuckDBPyConnection | None = None
    summaries: list[ImportSummary] = []

    try:
        connection = duckdb.connect(str(temporary_path))
        connection.execute("SET preserve_insertion_order = false")
        connection.execute("SET threads = 4")
        for table, spec in TABLE_SPECS.items():
            source, overrides = sources[table]
            if not re.fullmatch(r"[a-z_]+", table):
                raise DataAccessError(f"非法内部表名：{table}")
            connection.execute(
                f'CREATE TABLE "{table}" AS '
                f"SELECT * FROM read_csv(?, header = true, sample_size = 200000, "
                f"types = {_types_literal(overrides)})",
                [str(source)],
            )
            summaries.append(_validate_table(connection, table, spec))
        connection.execute("CHECKPOINT")
        connection.close()
        connection = None
        target_wal = Path(f"{database_path}.wal")
        if target_wal.exists():
            target_wal.unlink()
        os.replace(temporary_path, database_path)
        return summaries
    except DataAccessError:
        raise
    except (duckdb.Error, OSError) as exc:
        raise DataAccessError(f"数据重建失败：{exc}") from exc
    finally:
        if connection is not None:
            connection.close()
        _remove_database_files(temporary_path)


class DuckDBStore:
    """每次查询使用独立只读连接的数据访问对象。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def ensure_ready(self) -> None:
        """确认数据库存在且包含全部业务表。"""

        if not self.database_path.is_file():
            raise DataAccessError(
                "分析数据库尚未生成，请先运行 python backend/scripts/import_data.py。"
            )
        try:
            with duckdb.connect(str(self.database_path), read_only=True) as connection:
                rows = connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
                ).fetchall()
        except duckdb.Error as exc:
            raise DataAccessError("分析数据库无法打开，请重新执行数据导入。") from exc
        tables = {str(row[0]) for row in rows}
        missing = sorted(TABLE_SPECS.keys() - tables)
        if missing:
            raise DataAccessError(f"分析数据库缺少表：{', '.join(missing)}，请重新导入。")

    def fetch(self, sql: str, parameters: SqlParameters = ()) -> QueryResult:
        """执行由业务工具提供的参数化只读查询。"""

        self.ensure_ready()
        try:
            with duckdb.connect(str(self.database_path), read_only=True) as connection:
                cursor = connection.execute(sql, list(parameters))
                description = cursor.description or []
                columns = tuple(str(item[0]) for item in description)
                rows = tuple(
                    tuple(_normalize_value(value) for value in row) for row in cursor.fetchall()
                )
        except duckdb.Error as exc:
            raise DataAccessError("数据查询失败，请检查数据库是否需要重新导入。") from exc
        return QueryResult(columns=columns, rows=rows)


class CaseStore:
    """独立案件库，避免七表全量重建覆盖调查和人工审核记录。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @staticmethod
    def _create_schema(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rule_runs (
                run_id VARCHAR PRIMARY KEY,
                rule_set_version VARCHAR NOT NULL,
                observation_date DATE NOT NULL,
                cases_detected INTEGER NOT NULL,
                cases_created INTEGER NOT NULL,
                rule_hits INTEGER NOT NULL,
                receivable_cases INTEGER NOT NULL,
                inventory_cases INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_cases (
                case_id VARCHAR PRIMARY KEY,
                case_type VARCHAR NOT NULL,
                entity_type VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                entity_label VARCHAR NOT NULL,
                entity_context_json VARCHAR NOT NULL,
                observation_date DATE NOT NULL,
                status VARCHAR NOT NULL,
                priority VARCHAR NOT NULL,
                exposure_amount DOUBLE NOT NULL,
                summary VARCHAR NOT NULL,
                rule_hit_count INTEGER NOT NULL,
                rule_set_version VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                next_review_at DATE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rule_hits (
                rule_hit_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                rule_id VARCHAR NOT NULL,
                rule_name VARCHAR NOT NULL,
                rule_version VARCHAR NOT NULL,
                severity VARCHAR NOT NULL,
                exposure_amount DOUBLE NOT NULL,
                reason VARCHAR NOT NULL,
                metrics_json VARCHAR NOT NULL,
                threshold_source VARCHAR NOT NULL,
                sources_json VARCHAR NOT NULL,
                period VARCHAR NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS investigations (
                investigation_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                report_json VARCHAR NOT NULL,
                evidence_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reviews (
                review_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                decision VARCHAR NOT NULL,
                reviewer VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                action VARCHAR,
                next_review_at DATE,
                created_at TIMESTAMP NOT NULL
            )
            """
        )

    def ensure_ready(self) -> None:
        """创建案件库及固定表。"""

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                self._create_schema(connection)
        except (duckdb.Error, OSError) as exc:
            raise DataAccessError("案件数据库无法初始化。") from exc

    def save_rule_scan(
        self,
        run: RuleRunWrite,
        cases: Sequence[CaseWrite],
        hits: Sequence[RuleHitWrite],
    ) -> int:
        """幂等保存一次规则扫描，保留已有案件状态和人工流程。"""

        self.ensure_ready()
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                self._create_schema(connection)
                connection.begin()
                existing = 0
                for case in cases:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM risk_cases WHERE case_id = ?", [case.case_id]
                    ).fetchone()
                    existing += int(row[0]) if row is not None else 0
                    connection.execute(
                        """
                        INSERT INTO risk_cases VALUES (
                            ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, NULL
                        )
                        ON CONFLICT (case_id) DO UPDATE SET
                            priority = excluded.priority,
                            exposure_amount = excluded.exposure_amount,
                            summary = excluded.summary,
                            rule_hit_count = excluded.rule_hit_count,
                            updated_at = excluded.updated_at
                        """,
                        [
                            case.case_id,
                            case.case_type,
                            case.entity_type,
                            case.entity_id,
                            case.entity_label,
                            json.dumps(case.entity_context, ensure_ascii=False),
                            case.observation_date,
                            case.priority,
                            case.exposure_amount,
                            case.summary,
                            case.rule_hit_count,
                            case.rule_set_version,
                            case.created_at,
                            case.created_at,
                        ],
                    )
                    connection.execute("DELETE FROM rule_hits WHERE case_id = ?", [case.case_id])

                for hit in hits:
                    connection.execute(
                        """
                        INSERT INTO rule_hits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            hit.rule_hit_id,
                            hit.case_id,
                            hit.rule_id,
                            hit.rule_name,
                            hit.rule_version,
                            hit.severity,
                            hit.exposure_amount,
                            hit.reason,
                            json.dumps(hit.metrics, ensure_ascii=False),
                            hit.threshold_source,
                            json.dumps(list(hit.sources), ensure_ascii=False),
                            hit.period,
                        ],
                    )

                created = len(cases) - existing
                connection.execute(
                    """
                    INSERT INTO rule_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        run.run_id,
                        run.rule_set_version,
                        run.observation_date,
                        run.cases_detected,
                        created,
                        run.rule_hits,
                        run.receivable_cases,
                        run.inventory_cases,
                        run.created_at,
                    ],
                )
                connection.commit()
                return created
        except duckdb.Error as exc:
            raise DataAccessError("规则扫描结果无法写入案件数据库。") from exc

    def fetch_latest_run(self) -> QueryResult:
        """返回最近一次规则扫描。"""

        return self._fetch(
            """
            SELECT run_id, rule_set_version, observation_date, cases_detected, cases_created,
                   rule_hits, receivable_cases, inventory_cases, created_at
            FROM rule_runs ORDER BY created_at DESC LIMIT 1
            """
        )

    def fetch_cases(
        self,
        *,
        status: str | None = None,
        case_type: str | None = None,
        limit: int = 200,
    ) -> QueryResult:
        """返回案件队列。"""

        clauses = [
            "rule_set_version = (SELECT rule_set_version FROM rule_runs "
            "ORDER BY created_at DESC LIMIT 1)"
        ]
        parameters: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        if case_type is not None:
            clauses.append("case_type = ?")
            parameters.append(case_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        return self._fetch(
            f"""
            SELECT case_id, case_type, entity_type, entity_id, entity_label,
                   observation_date, status, priority, exposure_amount, summary,
                   rule_hit_count, rule_set_version, updated_at, next_review_at
            FROM risk_cases
            {where}
            ORDER BY
                CASE priority WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                              WHEN 'MEDIUM' THEN 3 ELSE 4 END,
                exposure_amount DESC, updated_at DESC
            LIMIT ?
            """,
            parameters,
        )

    def fetch_case(self, case_id: str) -> QueryResult:
        """返回一个案件的主体和内部实体上下文。"""

        return self._fetch(
            """
            SELECT case_id, case_type, entity_type, entity_id, entity_label,
                   entity_context_json, observation_date, status, priority,
                   exposure_amount, summary, rule_hit_count, rule_set_version,
                   updated_at, next_review_at
            FROM risk_cases WHERE case_id = ?
            """,
            [case_id],
        )

    def fetch_rule_hits(self, case_id: str) -> QueryResult:
        """返回案件的全部规则命中。"""

        return self._fetch(
            """
            SELECT rule_hit_id, rule_id, rule_name, rule_version, severity,
                   exposure_amount, reason, metrics_json, threshold_source,
                   sources_json, period
            FROM rule_hits WHERE case_id = ?
            ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                                   WHEN 'MEDIUM' THEN 3 ELSE 4 END, rule_id
            """,
            [case_id],
        )

    def fetch_latest_investigation(self, case_id: str) -> QueryResult:
        """返回案件最近一次 Agent 调查。"""

        return self._fetch(
            """
            SELECT investigation_id, case_id, report_json, evidence_json, created_at
            FROM investigations WHERE case_id = ? ORDER BY created_at DESC LIMIT 1
            """,
            [case_id],
        )

    def fetch_reviews(self, case_id: str) -> QueryResult:
        """返回案件人工审核历史。"""

        return self._fetch(
            """
            SELECT review_id, case_id, decision, reviewer, reason, action,
                   next_review_at, created_at
            FROM reviews WHERE case_id = ? ORDER BY created_at DESC
            """,
            [case_id],
        )

    def fetch_overview(self) -> QueryResult:
        """返回风险首页案件聚合。"""

        return self._fetch(
            """
            SELECT
                COUNT(*) AS total_cases,
                COUNT(*) FILTER (WHERE status IN ('OPEN', 'INVESTIGATING')) AS open_cases,
                COUNT(*) FILTER (WHERE status = 'PENDING_REVIEW') AS pending_review_cases,
                COUNT(*) FILTER (WHERE status = 'MONITORING') AS monitoring_cases,
                COUNT(*) FILTER (WHERE status = 'ACTION_REQUIRED') AS action_required_cases,
                COUNT(*) FILTER (WHERE priority = 'CRITICAL') AS critical_cases,
                COALESCE(SUM(exposure_amount) FILTER (
                    WHERE status NOT IN ('CLOSED_FALSE_POSITIVE', 'CLOSED_RESOLVED')
                ), 0) AS exposure_amount,
                COUNT(*) FILTER (WHERE case_type = 'ACCOUNTS_RECEIVABLE') AS ar_cases,
                COUNT(*) FILTER (WHERE case_type = 'INVENTORY') AS inventory_cases
            FROM risk_cases
            WHERE rule_set_version = (
                SELECT rule_set_version FROM rule_runs ORDER BY created_at DESC LIMIT 1
            )
            """
        )

    def save_investigation(self, record: InvestigationWrite) -> None:
        """保存调查并将案件推进到待审核。"""

        self.ensure_ready()
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                connection.begin()
                connection.execute(
                    "INSERT INTO investigations VALUES (?, ?, ?, ?, ?)",
                    [
                        record.investigation_id,
                        record.case_id,
                        record.report_json,
                        record.evidence_json,
                        record.created_at,
                    ],
                )
                connection.execute(
                    """
                    UPDATE risk_cases
                    SET status = 'PENDING_REVIEW', updated_at = ?
                    WHERE case_id = ?
                    """,
                    [record.created_at, record.case_id],
                )
                connection.commit()
        except duckdb.Error as exc:
            raise DataAccessError("调查结果无法写入案件数据库。") from exc

    def save_review(self, record: ReviewWrite, new_status: str) -> None:
        """保存人工审核并更新案件状态。"""

        self.ensure_ready()
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                connection.begin()
                connection.execute(
                    "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        record.review_id,
                        record.case_id,
                        record.decision,
                        record.reviewer,
                        record.reason,
                        record.action,
                        record.next_review_at,
                        record.created_at,
                    ],
                )
                connection.execute(
                    """
                    UPDATE risk_cases
                    SET status = ?, next_review_at = ?, updated_at = ?
                    WHERE case_id = ?
                    """,
                    [new_status, record.next_review_at, record.created_at, record.case_id],
                )
                connection.commit()
        except duckdb.Error as exc:
            raise DataAccessError("人工审核无法写入案件数据库。") from exc

    def _fetch(self, sql: str, parameters: SqlParameters = ()) -> QueryResult:
        self.ensure_ready()
        try:
            with duckdb.connect(str(self.database_path), read_only=True) as connection:
                cursor = connection.execute(sql, list(parameters))
                description = cursor.description or []
                columns = tuple(str(item[0]) for item in description)
                rows = tuple(
                    tuple(_normalize_value(value) for value in row) for row in cursor.fetchall()
                )
        except duckdb.Error as exc:
            raise DataAccessError("案件查询失败。") from exc
        return QueryResult(columns=columns, rows=rows)
