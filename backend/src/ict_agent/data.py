"""DuckDB 数据导入和只读查询边界。"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
class SourceSnapshot:
    """一张已导入来源文件的内容身份与数值摘要。"""

    table: str
    filename: str
    size_bytes: int
    sha256: str
    rows: int
    min_date: str | None
    max_date: str | None


@dataclass(frozen=True)
class DataSnapshot:
    """当前业务数据库对应的一组固定来源身份。"""

    snapshot_id: str
    imported_at: str
    schema_fingerprint: str
    sources: tuple[SourceSnapshot, ...]


@dataclass(frozen=True)
class QueryResult:
    """DuckDB 查询的 JSON 友好结果。"""

    columns: tuple[str, ...]
    rows: tuple[tuple[DatabaseScalar, ...], ...]


@dataclass(frozen=True)
class CaseWrite:
    """统一信号入口写入调查案件库的案件记录。"""

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
    discovery_source: str = "RULE"
    business_type: str | None = None
    source_snapshot_id: str = ""
    data_quality_status: str = "UNKNOWN"
    data_quality_warnings: Sequence[str] = ()


@dataclass(frozen=True)
class RuleHitWrite:
    """统一入口写入案件库的一条风险信号。"""

    rule_hit_id: str
    case_id: str
    rule_id: str
    rule_name: str
    rule_version: str
    severity: str
    exposure_amount: float
    reason: str
    metrics: Mapping[str, object]
    threshold_source: str
    sources: Sequence[str]
    period: str
    threshold_version: str = ""


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
    source_snapshot_id: str = ""


@dataclass(frozen=True)
class PreTransactionWrite:
    """一笔与业务事实库隔离的模拟新交易。"""

    simulation_id: str
    case_id: str
    customer_id: str
    customer_name: str
    business_type: str
    amount_yuan: float
    proposed_term_days: int
    expected_margin_rate: float | None
    scenario: str
    seed: int
    historical_order_count: int
    distribution_json: str
    source_snapshot_id: str
    data_quality_status: str
    data_quality_warnings_json: str
    generated_at: str


@dataclass(frozen=True)
class InvestigationWrite:
    """一次 Agent 调查的持久化记录。"""

    investigation_id: str
    case_id: str
    report_json: str
    evidence_json: str
    protocol_json: str
    created_at: str


@dataclass(frozen=True)
class ReviewWrite:
    """一次人工审核的持久化记录。"""

    review_id: str
    case_id: str
    decision: str
    reviewer: str
    reason: str
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_fingerprint() -> str:
    contract = {
        table: {
            "filename": spec.filename,
            "required_columns": sorted(spec.required_columns),
            "type_overrides": dict(sorted(spec.type_overrides.items())),
            "date_column": spec.date_column,
        }
        for table, spec in sorted(TABLE_SPECS.items())
    }
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


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
    source_identities: dict[str, tuple[int, str]] = {}
    for table, spec in TABLE_SPECS.items():
        source = data_dir / spec.filename
        sources[table] = (source, _validate_source(source, spec))
        source_identities[table] = (source.stat().st_size, _file_sha256(source))

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
        schema_fingerprint = _schema_fingerprint()
        source_snapshots = []
        for summary in summaries:
            source = sources[summary.table][0]
            expected_size, expected_sha256 = source_identities[summary.table]
            if source.stat().st_size != expected_size or _file_sha256(source) != expected_sha256:
                raise DataAccessError(
                    f"{source.name} 在导入期间发生变化，请停止数据写入后重新导入。"
                )
            source_snapshots.append(
                SourceSnapshot(
                    table=summary.table,
                    filename=TABLE_SPECS[summary.table].filename,
                    size_bytes=expected_size,
                    sha256=expected_sha256,
                    rows=summary.rows,
                    min_date=summary.min_date,
                    max_date=summary.max_date,
                )
            )
        snapshot_material = {
            "schema_fingerprint": schema_fingerprint,
            "sources": [{"table": item.table, "sha256": item.sha256} for item in source_snapshots],
        }
        snapshot_id = hashlib.sha256(
            json.dumps(snapshot_material, sort_keys=True).encode()
        ).hexdigest()[:24]
        imported_at = datetime.now(UTC).isoformat()
        connection.execute(
            """
            CREATE TABLE import_manifest (
                snapshot_id VARCHAR PRIMARY KEY,
                imported_at TIMESTAMP NOT NULL,
                schema_fingerprint VARCHAR NOT NULL,
                sources_json VARCHAR NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO import_manifest VALUES (?, ?, ?, ?)",
            [
                snapshot_id,
                imported_at,
                schema_fingerprint,
                json.dumps(
                    [item.__dict__ for item in source_snapshots],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ],
        )
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
        self._ready = False

    def _connect(self) -> duckdb.DuckDBPyConnection:
        connection = duckdb.connect(
            str(self.database_path),
            read_only=True,
            config={
                "enable_external_access": "false",
                "allow_community_extensions": "false",
                "allow_unsigned_extensions": "false",
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
                "threads": "4",
                "memory_limit": "1GB",
                "max_temp_directory_size": "0B",
            },
        )
        connection.execute("SET lock_configuration = true")
        return connection

    def ensure_ready(self) -> None:
        """确认数据库存在且包含全部业务表。"""

        if self._ready:
            return
        if not self.database_path.is_file():
            raise DataAccessError(
                "分析数据库尚未生成，请先运行 python backend/scripts/import_data.py。"
            )
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
                ).fetchall()
        except duckdb.Error as exc:
            raise DataAccessError("分析数据库无法打开，请重新执行数据导入。") from exc
        tables = {str(row[0]) for row in rows}
        missing = sorted((TABLE_SPECS.keys() | {"import_manifest"}) - tables)
        if missing:
            raise DataAccessError(f"分析数据库缺少表：{', '.join(missing)}，请重新导入。")
        self._ready = True

    def fetch(self, sql: str, parameters: SqlParameters = ()) -> QueryResult:
        """执行由业务工具提供的参数化只读查询。"""

        self.ensure_ready()
        try:
            with self._connect() as connection:
                cursor = connection.execute(sql, list(parameters))
                description = cursor.description or []
                columns = tuple(str(item[0]) for item in description)
                raw_rows = cursor.fetchmany(10_001)
                if len(raw_rows) > 10_000:
                    raise DataAccessError("数据查询结果超过 10000 行，请缩小受控查询范围。")
                rows = tuple(tuple(_normalize_value(value) for value in row) for row in raw_rows)
        except DataAccessError:
            raise
        except duckdb.Error as exc:
            raise DataAccessError("数据查询失败，请检查数据库是否需要重新导入。") from exc
        return QueryResult(columns=columns, rows=rows)

    def get_snapshot(self) -> DataSnapshot:
        """读取当前导入快照身份，不暴露本机原始路径。"""

        result = self.fetch(
            """
            SELECT snapshot_id, imported_at, schema_fingerprint, sources_json
            FROM import_manifest LIMIT 1
            """
        )
        if not result.rows:
            raise DataAccessError("分析数据库缺少导入快照身份，请重新导入。")
        row = result.rows[0]
        sources = tuple(SourceSnapshot(**item) for item in json.loads(str(row[3])))
        return DataSnapshot(
            snapshot_id=str(row[0]),
            imported_at=str(row[1]),
            schema_fingerprint=str(row[2]),
            sources=sources,
        )


class CaseStore:
    """独立案件库，避免七表全量重建覆盖调查和人工审核记录。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @staticmethod
    def _create_schema(connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_runs (
                run_id VARCHAR PRIMARY KEY,
                discovery_source VARCHAR NOT NULL,
                source_set_version VARCHAR NOT NULL,
                source_snapshot_id VARCHAR NOT NULL,
                observation_date DATE NOT NULL,
                cases_detected INTEGER NOT NULL,
                cases_created INTEGER NOT NULL,
                signal_count INTEGER NOT NULL,
                receivable_cases INTEGER NOT NULL,
                inventory_cases INTEGER NOT NULL,
                pre_transaction_cases INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS investigation_cases (
                case_id VARCHAR PRIMARY KEY,
                discovery_source VARCHAR NOT NULL,
                case_type VARCHAR NOT NULL,
                entity_type VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                entity_label VARCHAR NOT NULL,
                business_type VARCHAR,
                entity_context_json VARCHAR NOT NULL,
                observation_date DATE NOT NULL,
                status VARCHAR NOT NULL,
                priority VARCHAR NOT NULL,
                exposure_amount DOUBLE NOT NULL,
                summary VARCHAR NOT NULL,
                signal_count INTEGER NOT NULL,
                source_set_version VARCHAR NOT NULL,
                source_snapshot_id VARCHAR NOT NULL,
                data_quality_status VARCHAR NOT NULL,
                data_quality_warnings_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_signals (
                signal_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                signal_code VARCHAR NOT NULL,
                signal_name VARCHAR NOT NULL,
                source_version VARCHAR NOT NULL,
                severity VARCHAR NOT NULL,
                exposure_amount DOUBLE NOT NULL,
                reason VARCHAR NOT NULL,
                metrics_json VARCHAR NOT NULL,
                threshold_source VARCHAR NOT NULL,
                threshold_version VARCHAR NOT NULL,
                sources_json VARCHAR NOT NULL,
                period VARCHAR NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS case_investigations (
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
            CREATE TABLE IF NOT EXISTS investigation_protocols (
                investigation_id VARCHAR PRIMARY KEY,
                protocol_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS case_reviews (
                review_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                decision VARCHAR NOT NULL,
                reviewer VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pre_transaction_simulations (
                simulation_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                customer_id VARCHAR NOT NULL,
                customer_name VARCHAR NOT NULL,
                business_type VARCHAR NOT NULL,
                amount_yuan DOUBLE NOT NULL,
                proposed_term_days INTEGER NOT NULL,
                expected_margin_rate DOUBLE,
                scenario VARCHAR NOT NULL,
                seed BIGINT NOT NULL,
                historical_order_count INTEGER NOT NULL,
                distribution_json VARCHAR NOT NULL,
                source_snapshot_id VARCHAR NOT NULL,
                data_quality_status VARCHAR NOT NULL,
                data_quality_warnings_json VARCHAR NOT NULL,
                generated_at TIMESTAMP NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feishu_notifications (
                notification_id VARCHAR PRIMARY KEY,
                event_type VARCHAR NOT NULL,
                case_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                message_id VARCHAR NOT NULL,
                error_message VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS integration_settings (
                setting_key VARCHAR PRIMARY KEY,
                setting_value VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL
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
    ) -> list[str]:
        """把确定性规则作为一个可替换信号源写入统一案件库。"""

        self.ensure_ready()
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                self._create_schema(connection)
                connection.begin()
                created_case_ids: list[str] = []
                for case in cases:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM investigation_cases WHERE case_id = ?",
                        [case.case_id],
                    ).fetchone()
                    if row is None or int(row[0]) == 0:
                        created_case_ids.append(case.case_id)
                    connection.execute(
                        """
                        INSERT INTO investigation_cases (
                            case_id, discovery_source, case_type, entity_type, entity_id,
                            entity_label, business_type, entity_context_json, observation_date,
                            status, priority, exposure_amount, summary, signal_count,
                            source_set_version, source_snapshot_id, data_quality_status,
                            data_quality_warnings_json, created_at, updated_at
                        ) VALUES (
                            ?, 'RULE', ?, ?, ?, ?, ?, ?, ?, 'PENDING_AGENT_REVIEW',
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT (case_id) DO UPDATE SET
                            priority = excluded.priority,
                            exposure_amount = excluded.exposure_amount,
                            summary = excluded.summary,
                            signal_count = excluded.signal_count,
                            business_type = excluded.business_type,
                            entity_context_json = excluded.entity_context_json,
                            source_snapshot_id = excluded.source_snapshot_id,
                            data_quality_status = excluded.data_quality_status,
                            data_quality_warnings_json = excluded.data_quality_warnings_json,
                            updated_at = excluded.updated_at
                        """,
                        [
                            case.case_id,
                            case.case_type,
                            case.entity_type,
                            case.entity_id,
                            case.entity_label,
                            case.business_type,
                            json.dumps(case.entity_context, ensure_ascii=False),
                            case.observation_date,
                            case.priority,
                            case.exposure_amount,
                            case.summary,
                            case.rule_hit_count,
                            case.rule_set_version,
                            case.source_snapshot_id or run.source_snapshot_id,
                            case.data_quality_status,
                            json.dumps(list(case.data_quality_warnings), ensure_ascii=False),
                            case.created_at,
                            case.created_at,
                        ],
                    )
                    connection.execute("DELETE FROM risk_signals WHERE case_id = ?", [case.case_id])

                for hit in hits:
                    connection.execute(
                        """
                        INSERT INTO risk_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            hit.threshold_version or hit.rule_version,
                            json.dumps(list(hit.sources), ensure_ascii=False),
                            hit.period,
                        ],
                    )

                connection.execute(
                    """
                    INSERT INTO detection_runs VALUES (
                        ?, 'RULE', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?
                    )
                    """,
                    [
                        run.run_id,
                        run.rule_set_version,
                        run.source_snapshot_id,
                        run.observation_date,
                        run.cases_detected,
                        len(created_case_ids),
                        run.rule_hits,
                        run.receivable_cases,
                        run.inventory_cases,
                        run.created_at,
                    ],
                )
                connection.commit()
                return created_case_ids
        except duckdb.Error as exc:
            raise DataAccessError("规则扫描结果无法写入案件数据库。") from exc

    def fetch_latest_run(self) -> QueryResult:
        """返回最近一次规则扫描。"""

        return self._fetch(
            """
            SELECT run_id, source_set_version, observation_date, cases_detected, cases_created,
                   signal_count, receivable_cases, inventory_cases, created_at
            FROM detection_runs
            WHERE discovery_source = 'RULE'
            ORDER BY created_at DESC LIMIT 1
            """
        )

    def save_pre_transaction_case(
        self,
        case: CaseWrite,
        signal: RuleHitWrite,
        simulation: PreTransactionWrite,
    ) -> bool:
        """原子保存模拟交易、统一风险信号和待调查案件。"""

        self.ensure_ready()
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                self._create_schema(connection)
                connection.begin()
                exists = connection.execute(
                    "SELECT COUNT(*) FROM investigation_cases WHERE case_id = ?",
                    [case.case_id],
                ).fetchone()
                created = exists is None or int(exists[0]) == 0
                connection.execute(
                    """
                    INSERT INTO investigation_cases (
                        case_id, discovery_source, case_type, entity_type, entity_id,
                        entity_label, business_type, entity_context_json, observation_date,
                        status, priority, exposure_amount, summary, signal_count,
                        source_set_version, source_snapshot_id, data_quality_status,
                        data_quality_warnings_json, created_at, updated_at
                    ) VALUES (
                        ?, 'PRE_TRANSACTION', 'PRE_TRANSACTION', ?, ?, ?, ?, ?, ?,
                        'PENDING_AGENT_REVIEW', ?, ?, ?, 1, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT (case_id) DO NOTHING
                    """,
                    [
                        case.case_id,
                        case.entity_type,
                        case.entity_id,
                        case.entity_label,
                        case.business_type,
                        json.dumps(case.entity_context, ensure_ascii=False),
                        case.observation_date,
                        case.priority,
                        case.exposure_amount,
                        case.summary,
                        case.rule_set_version,
                        case.source_snapshot_id,
                        case.data_quality_status,
                        json.dumps(list(case.data_quality_warnings), ensure_ascii=False),
                        case.created_at,
                        case.created_at,
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO risk_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (signal_id) DO NOTHING
                    """,
                    [
                        signal.rule_hit_id,
                        signal.case_id,
                        signal.rule_id,
                        signal.rule_name,
                        signal.rule_version,
                        signal.severity,
                        signal.exposure_amount,
                        signal.reason,
                        json.dumps(signal.metrics, ensure_ascii=False),
                        signal.threshold_source,
                        signal.threshold_version or signal.rule_version,
                        json.dumps(list(signal.sources), ensure_ascii=False),
                        signal.period,
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO pre_transaction_simulations VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    ) ON CONFLICT (simulation_id) DO NOTHING
                    """,
                    [
                        simulation.simulation_id,
                        simulation.case_id,
                        simulation.customer_id,
                        simulation.customer_name,
                        simulation.business_type,
                        simulation.amount_yuan,
                        simulation.proposed_term_days,
                        simulation.expected_margin_rate,
                        simulation.scenario,
                        simulation.seed,
                        simulation.historical_order_count,
                        simulation.distribution_json,
                        simulation.source_snapshot_id,
                        simulation.data_quality_status,
                        simulation.data_quality_warnings_json,
                        simulation.generated_at,
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO detection_runs VALUES (
                        ?, 'PRE_TRANSACTION', ?, ?, ?, 1, ?, 1, 0, 0, 1, ?
                    ) ON CONFLICT (run_id) DO NOTHING
                    """,
                    [
                        simulation.simulation_id,
                        case.rule_set_version,
                        simulation.source_snapshot_id,
                        case.observation_date,
                        1 if created else 0,
                        simulation.generated_at,
                    ],
                )
                connection.commit()
                return created
        except duckdb.Error as exc:
            raise DataAccessError("模拟交易案件无法写入案件数据库。") from exc

    def fetch_pre_transaction_simulations(self, *, limit: int = 50) -> QueryResult:
        """返回最近生成的模拟交易，不读取或改写真实销售表。"""

        return self._fetch(
            """
            SELECT simulation_id, case_id, customer_id, customer_name, business_type,
                   amount_yuan, proposed_term_days, expected_margin_rate, scenario, seed,
                   historical_order_count, distribution_json, source_snapshot_id,
                   data_quality_status, data_quality_warnings_json, generated_at
            FROM pre_transaction_simulations
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            [limit],
        )

    def save_feishu_notification(
        self,
        *,
        notification_id: str,
        event_type: str,
        case_id: str,
        status: str,
        message_id: str,
        error_message: str,
        created_at: str,
    ) -> bool:
        """幂等保存一次飞书案件通知结果。"""

        self.ensure_ready()
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                row = connection.execute(
                    """
                    INSERT INTO feishu_notifications VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (notification_id) DO NOTHING
                    RETURNING notification_id
                    """,
                    [
                        notification_id,
                        event_type,
                        case_id,
                        status,
                        message_id,
                        error_message,
                        created_at,
                    ],
                ).fetchone()
                return row is not None
        except duckdb.Error as exc:
            raise DataAccessError("飞书通知结果无法写入案件数据库。") from exc

    def fetch_feishu_notifications(self, case_id: str) -> QueryResult:
        """返回案件的飞书通知审计记录。"""

        return self._fetch(
            """
            SELECT notification_id, event_type, status, message_id, error_message, created_at
            FROM feishu_notifications WHERE case_id = ? ORDER BY created_at DESC
            """,
            [case_id],
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
            "(discovery_source <> 'RULE' OR source_set_version = ("
            "SELECT source_set_version FROM detection_runs "
            "WHERE discovery_source = 'RULE' ORDER BY created_at DESC LIMIT 1))"
        ]
        parameters: list[object] = []
        if status is not None:
            if status == "PENDING_AGENT_REVIEW":
                clauses.append("status IN ('PENDING_AGENT_REVIEW', 'AGENT_REVIEWING')")
            else:
                clauses.append("status = ?")
                parameters.append(status)
        if case_type is not None:
            clauses.append("case_type = ?")
            parameters.append(case_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        return self._fetch(
            f"""
            SELECT case_id, discovery_source, case_type, entity_type, entity_id, entity_label,
                   business_type, observation_date, status, priority, exposure_amount, summary,
                   signal_count, source_set_version, source_snapshot_id, data_quality_status,
                   data_quality_warnings_json, updated_at,
                   COALESCE((
                       SELECT signal_name
                       FROM risk_signals
                       WHERE case_id = investigation_cases.case_id
                       ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                                              WHEN 'MEDIUM' THEN 3 ELSE 4 END, signal_code
                       LIMIT 1
                   ), summary) AS signal_overview
            FROM investigation_cases
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
            SELECT case_id, discovery_source, case_type, entity_type, entity_id, entity_label,
                   business_type, entity_context_json, observation_date, status, priority,
                   exposure_amount, summary, signal_count, source_set_version, source_snapshot_id,
                   data_quality_status, data_quality_warnings_json, updated_at,
                   COALESCE((
                       SELECT signal_name
                       FROM risk_signals
                       WHERE case_id = investigation_cases.case_id
                       ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                                              WHEN 'MEDIUM' THEN 3 ELSE 4 END, signal_code
                       LIMIT 1
                   ), summary) AS signal_overview
            FROM investigation_cases WHERE case_id = ?
            """,
            [case_id],
        )

    def fetch_signals(self, case_id: str) -> QueryResult:
        """返回案件的全部风险信号。"""

        return self._fetch(
            """
            SELECT signal_id, signal_code, signal_name, source_version, severity,
                   exposure_amount, reason, metrics_json, threshold_source, threshold_version,
                   sources_json, period
            FROM risk_signals WHERE case_id = ?
            ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                                   WHEN 'MEDIUM' THEN 3 ELSE 4 END, signal_code
            """,
            [case_id],
        )

    def fetch_latest_investigation(self, case_id: str) -> QueryResult:
        """返回案件最近一次 Agent 调查。"""

        return self._fetch(
            """
            SELECT i.investigation_id, i.case_id, i.report_json, i.evidence_json,
                   i.created_at,
                   json_extract_string(p.protocol_json, '$.schema_version') AS schema_version,
                   json_extract_string(p.protocol_json, '$.api_format') AS api_format
            FROM case_investigations AS i
            LEFT JOIN investigation_protocols AS p
              ON p.investigation_id = i.investigation_id
            WHERE i.case_id = ? ORDER BY i.created_at DESC LIMIT 1
            """,
            [case_id],
        )

    def fetch_investigation_protocol(self, investigation_id: str) -> QueryResult:
        """按调查编号返回完整模型协议，仅供按需调试和下载。"""

        return self._fetch(
            """
            SELECT protocol_json
            FROM investigation_protocols
            WHERE investigation_id = ?
            """,
            [investigation_id],
        )

    def fetch_reviews(self, case_id: str) -> QueryResult:
        """返回案件人工审核历史。"""

        return self._fetch(
            """
            SELECT review_id, case_id, decision, reviewer, reason, created_at
            FROM case_reviews WHERE case_id = ? ORDER BY created_at DESC
            """,
            [case_id],
        )

    def fetch_overview(self) -> QueryResult:
        """返回风险首页案件聚合。"""

        return self._fetch(
            """
            SELECT
                COUNT(*) AS total_cases,
                COUNT(*) FILTER (
                    WHERE status IN ('PENDING_AGENT_REVIEW', 'AGENT_REVIEWING')
                ) AS pending_agent_cases,
                COUNT(*) FILTER (
                    WHERE status = 'PENDING_HUMAN_REVIEW'
                ) AS pending_human_review_cases,
                COUNT(*) FILTER (WHERE status = 'ACTION_IN_PROGRESS') AS action_in_progress_cases,
                COUNT(*) FILTER (WHERE status = 'CLOSED') AS closed_cases,
                COUNT(*) FILTER (
                    WHERE priority IN ('HIGH', 'CRITICAL')
                ) AS high_priority_cases,
                COALESCE(SUM(exposure_amount) FILTER (
                    WHERE status != 'CLOSED'
                ), 0) AS exposure_amount,
                COUNT(*) FILTER (WHERE case_type = 'ACCOUNTS_RECEIVABLE') AS ar_cases,
                COUNT(*) FILTER (WHERE case_type = 'INVENTORY') AS inventory_cases,
                COUNT(*) FILTER (WHERE case_type = 'PRE_TRANSACTION') AS pre_transaction_cases
            FROM investigation_cases
            WHERE discovery_source <> 'RULE' OR source_set_version = (
                SELECT source_set_version FROM detection_runs
                WHERE discovery_source = 'RULE' ORDER BY created_at DESC LIMIT 1
            )
            """
        )

    # ------------------------------------------------------------------
    def get_integration_setting(self, setting_key: str) -> str | None:
        """读取单个外部集成配置。"""

        result = self._fetch(
            "SELECT setting_value FROM integration_settings WHERE setting_key = ?",
            [setting_key],
        )
        return str(result.rows[0][0]) if result.rows else None

    def save_integration_setting(
        self, setting_key: str, setting_value: str, updated_at: str
    ) -> None:
        """保存或替换单个外部集成配置。"""

        self.ensure_ready()
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                connection.execute(
                    "INSERT INTO integration_settings VALUES (?, ?, ?) "
                    "ON CONFLICT (setting_key) DO UPDATE SET "
                    "setting_value = excluded.setting_value, "
                    "updated_at = excluded.updated_at",
                    [setting_key, setting_value, updated_at],
                )
        except duckdb.Error as exc:
            raise DataAccessError("外部集成配置无法写入案件数据库。") from exc

    def save_investigation(self, record: InvestigationWrite) -> None:
        """保存调查并将案件推进到待审核。"""

        self.ensure_ready()
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                connection.begin()
                connection.execute(
                    "INSERT INTO case_investigations VALUES (?, ?, ?, ?, ?)",
                    [
                        record.investigation_id,
                        record.case_id,
                        record.report_json,
                        record.evidence_json,
                        record.created_at,
                    ],
                )
                connection.execute(
                    "INSERT INTO investigation_protocols VALUES (?, ?, ?)",
                    [record.investigation_id, record.protocol_json, record.created_at],
                )
                updated = connection.execute(
                    """
                    UPDATE investigation_cases
                    SET status = 'PENDING_HUMAN_REVIEW', updated_at = ?
                    WHERE case_id = ? AND status = 'AGENT_REVIEWING'
                    RETURNING case_id
                    """,
                    [record.created_at, record.case_id],
                ).fetchone()
                if updated is None:
                    connection.rollback()
                    raise DataAccessError("当前案件状态不允许保存调查报告。")
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
                    "INSERT INTO case_reviews VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        record.review_id,
                        record.case_id,
                        record.decision,
                        record.reviewer,
                        record.reason,
                        record.created_at,
                    ],
                )
                updated = connection.execute(
                    """
                    UPDATE investigation_cases
                    SET status = ?, updated_at = ?
                    WHERE case_id = ? AND status = 'PENDING_HUMAN_REVIEW'
                    RETURNING case_id
                    """,
                    [new_status, record.created_at, record.case_id],
                ).fetchone()
                if updated is None:
                    connection.rollback()
                    raise DataAccessError("当前案件状态不允许保存人工复核。")
                connection.commit()
        except duckdb.Error as exc:
            raise DataAccessError("人工审核无法写入案件数据库。") from exc

    def transition_case(self, case_id: str, expected_status: str, new_status: str) -> bool:
        """仅在案件处于预期状态时推进，避免重复调查或越级审核。"""

        self.ensure_ready()
        now = datetime.now(UTC).isoformat()
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                row = connection.execute(
                    """
                    UPDATE investigation_cases
                    SET status = ?, updated_at = ?
                    WHERE case_id = ? AND status = ?
                    RETURNING case_id
                    """,
                    [new_status, now, case_id, expected_status],
                ).fetchone()
                return row is not None
        except duckdb.Error as exc:
            raise DataAccessError("案件状态无法更新。") from exc

    def recover_interrupted_investigations(self) -> int:
        """服务启动时释放上一个进程遗留的 Agent 临时运行状态。"""

        self.ensure_ready()
        now = datetime.now(UTC).isoformat()
        try:
            with duckdb.connect(str(self.database_path)) as connection:
                rows = connection.execute(
                    """
                    UPDATE investigation_cases
                    SET status = 'PENDING_AGENT_REVIEW', updated_at = ?
                    WHERE status = 'AGENT_REVIEWING'
                    RETURNING case_id
                    """,
                    [now],
                ).fetchall()
                return len(rows)
        except duckdb.Error as exc:
            raise DataAccessError("中断的调查状态无法恢复。") from exc

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
